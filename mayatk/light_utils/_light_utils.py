# !/usr/bin/python
# coding=utf-8
"""Light utilities — building real scene lights from the geometry that represents them."""

from typing import Any, Dict, List, Optional, Sequence

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk

# From this package:
from mayatk.node_utils._node_utils import NodeUtils


class _LightUtilsInternal:
    """Internal helpers for :class:`LightUtils`."""

    @staticmethod
    def _scene_center():
        """World bbox centre of the scene's renderable meshes, or the origin.

        The reference an ``auto`` aim points at, so a ceiling plate faces down
        and a wall plate faces inward with no per-object setup.

        ``exactWorldBoundingBox`` rather than either bbox helper next door:
        ``XformUtils.get_center_point`` averages component POSITIONS (so it
        cannot be handed shapes), and ``CoreUtils.get_bounding_box`` takes a
        single node. This wants the combined box of a whole set.

        ``allPaths=True`` is load-bearing: ``ls`` otherwise reports ONE dag path
        per shape NODE, so in a room assembled from instanced modules -- the
        normal way a ceiling grid or a corridor is built -- every instance past
        the first is invisible here and the reference collapses onto the
        original. Measured on a six-module instanced corridor: the centre came
        back at x=-2.5 instead of x=998, which is enough to aim a wall plate
        out of the room rather than into it.
        """
        meshes = (
            cmds.ls(type="mesh", long=True, noIntermediate=True, allPaths=True) or []
        )
        if not meshes:
            return (0.0, 0.0, 0.0)
        bounds = cmds.exactWorldBoundingBox(meshes)
        return tuple((bounds[i] + bounds[i + 3]) / 2.0 for i in range(3))

    @staticmethod
    def _group_by_shape(objects: Optional[Sequence[Any]]) -> Dict[str, List[str]]:
        """``{shape: [members]}`` — one emitter per shape, faces kept as components.

        A face selection is the point of the whole exercise: a troffer's mesh is
        housing plus lens, and sizing an emitter to the housing makes a light
        wider than the thing that glows.

        Two normalisations, both load-bearing:

        * The node half is always resolved to its **shape**. A viewport face
          selection is rooted at the transform (``pCube1.f[1]``) while an
          API-built one is usually rooted at the shape, and left alone those two
          spellings of the same faces would group separately and build two
          lights on top of each other.
        * Members are stored as **full paths**, because they outlive this call
          on the light's source property and a leaf name stops being unique the
          moment a module is duplicated or instanced.
        """
        if objects is None:
            objects = cmds.ls(selection=True, long=True, flatten=False) or []

        grouped: Dict[str, List[str]] = {}
        for item in ptk.make_iterable(objects):
            node, _, component = str(item).partition(".")
            resolved = cmds.ls(node, long=True) or []
            if not resolved:
                continue
            node = resolved[0]
            if cmds.objectType(node, isAType="transform"):
                # ``descend=True``: a GROUP is what a fixture module IS once it
                # has been instanced across a ceiling -- the artist picks the
                # module, not the mesh buried inside it. The gate (descend only
                # when the node carries no shape of its own) and the measured
                # ``allDescendents`` caveats live in ``get_shapes``, shared with
                # ``MatUtils.get_mats``.
                shapes = NodeUtils.get_shapes(node, descend=True)
            else:
                shapes = [node]
            # Filter on the SHAPE, not the node: a light or camera reaches here
            # as an ordinary transform, and its bounding box is a manipulator
            # rather than a surface -- so re-running over a selection that
            # already contains generated lights would build emitters from them.
            shapes = [
                s for s in shapes if cmds.objectType(s, isAType="surfaceShape")
            ]
            if not shapes:
                continue
            if component:
                # Component indices belong to ONE shape; a multi-shape transform
                # is pathological and Maya's own expression is ambiguous there.
                grouped.setdefault(shapes[0], []).append(f"{shapes[0]}.{component}")
            else:
                for shape in shapes:
                    grouped.setdefault(shape, []).append(shape)
        # One selection can name the same geometry twice -- a group AND a mesh
        # inside it, or the two spellings of one face set (transform-rooted and
        # shape-rooted). The grouping already lands them on one emitter, but the
        # members ride on the light's source property, so a repeat would be
        # stored and re-read on every sync. Deduped once at the end rather than
        # per append, which is O(n^2) over a face selection.
        return {
            shape: ptk.remove_duplicates(members)
            for shape, members in grouped.items()
        }

    @classmethod
    def _clusters(cls, shape, members, cluster):
        """Split one shape's members into the emitters they should become.

        ``"shell"`` (default) groups the selected faces into connected islands,
        because a merged environment mesh is the normal case and a single
        bounding volume over four separate troffers is one enormous light
        spanning the whole ceiling. ``"object"`` keeps the historical
        one-light-per-shape behaviour; ``"face"`` gives every face its own.
        """
        faces = [m for m in members if "." in m]
        if not faces or cluster == "object":
            return [members]
        if cluster == "face":
            return [[face] for face in cmds.ls(faces, flatten=True) or []]

        from mayatk.core_utils.components import Components

        islands = Components.get_contiguous_islands(faces) or []
        # Map back to the ORIGINAL members by component index rather than
        # re-resolving the islander's names: those are Maya's own component
        # strings, not full paths, and resolving them by name is ambiguous the
        # moment two shapes share a leaf name. All faces here belong to one
        # shape, so the index is a unique key.
        by_index = {
            member.rsplit("[", 1)[-1].rstrip("]"): member
            for member in cmds.ls(faces, flatten=True, long=True) or []
        }
        clustered = []
        for island in islands:
            mapped = sorted(
                by_index[key]
                for key in (
                    str(face).rsplit("[", 1)[-1].rstrip("]") for face in island
                )
                if key in by_index
            )
            if mapped:
                clustered.append(mapped)
        return clustered or [members]

    @staticmethod
    def _face_normal(members):
        """Area-agnostic average of the members' world face normals, or ``None``.

        The emission direction a *selection* actually has. Averaged rather than
        taken from one face so a slightly domed or bevelled lens still yields
        the direction it faces overall; ``None`` for a whole-object member,
        which has no single normal and falls back to the bounding-box solve.
        """
        from mayatk.core_utils.components import Components

        total = [0.0, 0.0, 0.0]
        count = 0
        for face in cmds.ls([m for m in members if "." in m], flatten=True) or []:
            try:
                normal = Components.get_normal(face)
            except (TypeError, RuntimeError):
                continue
            for i in range(3):
                total[i] += normal[i]
            count += 1
        if not count:
            return None
        length = sum(c * c for c in total) ** 0.5
        if length < 1e-9:  # a closed shell: the normals cancel, so it has no facing
            return None
        return [c / length for c in total]

    @staticmethod
    def _world_points(members):
        """World-space vertex positions of *members*.

        Queried straight off the faces. Going via
        ``polyListComponentConversion`` would be the obvious route and is the
        wrong one: it does not preserve full paths (measured, and a documented
        trap in this repo -- ``|B|dup|dupShape.f[0]`` converts to
        ``B|dup.vtx[0:3]``), so re-resolving the result by name can pick up a
        different mesh's vertices the moment two shapes share a leaf name.
        ``xform`` on a face already returns its vertex positions.
        """
        flat = cmds.xform(members, query=True, translation=True, worldSpace=True) or []
        return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]

    @staticmethod
    def _emissive_color(members: Sequence[str]) -> Optional[List[float]]:
        """The assigned material's emission colour, or ``None``.

        Read through :class:`ShaderAttributeMap` -- the registry that already
        knows which attribute IS emission per shader type -- so this does not
        become yet another copy of that table. A textured emission has no single
        colour to read, so it falls through to the caller's parameter rather than
        guessing an average.

        Gated on ``SceneState.emission_weight`` for the reason that reader
        documents: ``standardSurface.emissionColor`` DEFAULTS to white while its
        ``emission`` weight defaults to 0, so colour alone reports a bright
        emissive on Maya's own default material -- which would hand every plain
        fixture a white light and silently override the caller's *kelvin*.
        """
        from mayatk.env_utils.scene_state import SceneState
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        for mat in MatUtils.get_mats(list(members), as_strings=True) or []:
            mapping = ShaderAttributeMap.get_attr(cmds.nodeType(mat), "emission")
            if not mapping or SceneState.emission_weight(mat) == 0.0:
                continue
            plug = f"{mat}.{mapping[0]}"
            if not cmds.objExists(plug) or cmds.listConnections(
                plug, source=True, destination=False
            ):
                continue  # textured: no single colour
            try:
                color = list(cmds.getAttr(plug)[0])[:3]
            except (RuntimeError, ValueError, TypeError, IndexError):
                continue
            if any(c > 0.0 for c in color):
                peak = max(color)
                return [c / peak for c in color]
        return None


class LightUtils(_LightUtilsInternal, ptk.HelpMixin):
    """Scene-light authoring (mirror of ``btk.LightUtils``)."""

    #: Attribute stamped on a generated light naming the geometry it came from.
    #: The link is a PROPERTY on the light, not a registry some other tool has to
    #: consult: the light is a complete, ordinary scene light the moment it
    #: exists, and nothing downstream -- exporter, bridge, renderer -- needs to
    #: know it was generated. :meth:`sync_lights_from_geometry` reads it back.
    SOURCE_ATTR = "geoLightSource"

    #: Maya's area light is a 2x2 square in its transform's LOCAL space, so the
    #: transform's scale is what sets the emitter's real size.
    AREA_LOCAL_SIZE = 2.0

    #: Arnold's DAG light node types. An Arnold light inherits
    #: ``THlocatorShape``, **not** Maya's ``light``, so ``objectType(isAType=
    #: "light")`` is False for one and ``cmds.ls(lights=True)`` never reports it
    #: (probed on Maya 2025 + MtoA 5.4.5). Any "does this scene have light"
    #: question that asks only Maya is therefore blind to an Arnold-only scene
    #: -- which is the normal case for this package's own bake path.
    #:
    #: Only the five types that are real DAG lights: ``aiLightBlocker`` and
    #: ``aiLightDecay`` are light FILTERS and ``aiImagerLightMixer`` is an
    #: imager. The same five back ``edit_utils.primitives``' "arnold" creation
    #: registry, which is keyed by friendly name rather than node type.
    ARNOLD_LIGHT_TYPES = (
        "aiAreaLight",
        "aiSkyDomeLight",
        "aiMeshLight",
        "aiPhotometricLight",
        "aiLightPortal",
    )

    @classmethod
    def all_lights(cls) -> List[str]:
        """Every light SHAPE in the scene -- Maya's and Arnold's.

        The population :meth:`contributing_lights` filters. Callers that need
        both ("there are lights, but none contribute") must take them from here
        rather than running their own ``ls``: two enumerations that disagree is
        precisely how a scene lit only by Arnold came to be read as having
        lights of which none work.

        Returns:
            list: Light shape full paths, de-duplicated, native ones first.
        """
        from mayatk.light_utils.hdr_manager import HdrManager

        shapes = list(cmds.ls(lights=True, long=True) or [])
        # Skipped when mtoa is unloaded: no Arnold light node can exist then,
        # and the query warns "Unknown object type" once per unregistered type
        # (measured) -- noise on a path a panel may call on every sync.
        if HdrManager.arnold_loaded():
            shapes.extend(cmds.ls(type=cls.ARNOLD_LIGHT_TYPES, long=True) or [])
        return list(dict.fromkeys(shapes))

    @classmethod
    def contributing_lights(cls) -> List[str]:
        """The scene's light SHAPES that can actually light a render.

        "Is there light in this scene" is not answered by ``ls(lights=True)``:
        that query is blind to Arnold entirely (see :attr:`ARNOLD_LIGHT_TYPES`,
        which is why this walks :meth:`all_lights`), and a light contributes
        only when it is visible -- **inherited**, so a parent group's flag
        counts -- and its intensity is non-zero. Measured
        on OFFICE_ENV 2026-08-12: four area lights at intensity 110 with
        ``aiNormalize`` correctly off, every one of them hidden at the
        transform, which baked an atlas 147x dimmer than the same room's
        previous bake.

        Visibility routes through :meth:`mayatk.DisplayUtils.is_visible` rather
        than a local walk, so this cannot drift from what the geometry side
        calls visible -- except for templating, which is passed as VISIBLE
        here. Templating is a viewport display/selection state and its effect
        on a light's render contribution is unverified; this result gates a
        refusal, so the unverified case fails OPEN rather than blocking a bake
        that would have worked.

        Deliberately NOT a "will the bake be lit" oracle: emissive materials
        light an Arnold bake with no light in the scene at all (see
        ``TextureBaker.arnold_translation_guard``), and ``aiExposure`` can dim
        a visible light to nothing. It answers exactly one question -- which
        lights are switched on -- and callers decide what that means.

        Returns:
            list: Light shape full paths, empty when nothing can contribute.
        """
        from mayatk.display_utils._display_utils import DisplayUtils

        contributing = []
        for shape in cls.all_lights():
            try:
                if not DisplayUtils.is_visible(
                    shape, consider_templated_visible=True
                ):
                    continue
                if float(cmds.getAttr(f"{shape}.intensity")) == 0.0:
                    continue
            except (RuntimeError, ValueError, TypeError):
                # An unreadable light is assumed to contribute: this gates a
                # refusal, and a false NEGATIVE would block a bake that works.
                pass
            contributing.append(shape)
        return contributing

    @classmethod
    def lights_from_geometry(
        cls,
        objects: Optional[Sequence[Any]] = None,
        intensity: float = 100.0,
        kelvin: Optional[float] = None,
        color: Optional[Sequence[float]] = None,
        offset: float = 1.0,
        toward: Optional[Sequence[float]] = None,
        prefix: str = "",
        emit_specular: bool = False,
        cluster: str = "shell",
    ) -> List[str]:
        """Create a real area light matched to each selected fixture, or face set.

        The geometry that *represents* a luminaire already has the position, size
        and facing of the real thing, so a light can be derived from it rather
        than hand-placed. What this deliberately does NOT do is derive that light
        at render or bake time: it authors an ordinary Maya light, once, which the
        artist then owns -- re-aimable, re-coloured, keyable, and carried by every
        exporter and bridge through the same path as any other light. A bake
        parameter standing in for a fixture can do none of that, and forces the
        baker to know what a fixture is.

        **Select faces, not just objects.** A troffer mesh is housing plus lens;
        an emitter sized to the whole mesh is wider and taller than the part that
        glows. Selecting the lens faces gives the real emitter. Each shape
        contributes one light (its selected faces, or its whole bounds when the
        shape itself is selected), and each INSTANCE of a shape is its own
        fixture: a ceiling grid built by instancing one module gets a light per
        module, named after the instance rather than after the shared shape.

        Colour is **property-driven**: an assigned material with a constant
        emission colour supplies it, so the fixture's own look-dev decides the
        light's hue with nothing to keep in sync by hand. *kelvin* / *color* are
        the fallback for geometry that carries no emission, and *color* overrides.

        Example::

            # lens faces selected in the viewport
            mtk.LightUtils.lights_from_geometry(kelvin=4000, intensity=50)

        Parameters:
            objects: Shapes, transforms or face components. A transform with no
                shape of its own (a group -- how an instanced fixture module is
                picked) contributes its mesh descendants. ``None`` -> selection.
            intensity: Maya light intensity. Unitless, and it is the SCENE's
                value; the Blender bridge converts it (and reports the resulting
                wattage) rather than this deciding a renderer's units. The
                created lights emit PER-AREA (``aiNormalize`` off): Arnold's
                normalized default spreads the intensity over the emitter, so a
                fixture-sized plate baked ~100x dimmer than the number
                suggested (measured, licensed solo bake: far wall 0.009
                normalized vs 1.03 per-area at the same intensities) --
                "created lights, baked, got darkness" was the default
                experience. Requires mtoa loaded at creation time (warned
                otherwise).
            kelvin: Colour temperature used when the source material carries no
                constant emission. Office troffers are 3500-4100K.
            color: Explicit linear RGB. Overrides both *kelvin* and the material.
            offset: Scene units of clearance between the plate and its light.
            toward: World point the plates should face. ``None`` -> the centre of
                the scene's geometry, which is what makes a ceiling plate aim
                down and a wall plate aim inward without per-object setup.
            prefix: Name prefix; default names after the source shape.
            emit_specular: Leave the light's specular contribution on. Off by
                default because a baked highlight is locked to the baking
                viewpoint and reads as a smudge from every other angle.
            cluster: How a face selection becomes emitters. ``"shell"``
                (default) groups connected faces into islands and gives each
                one its own light -- the right answer for a merged environment
                mesh, where a single volume over four separate troffers would
                be one enormous light spanning the ceiling. ``"object"`` keeps
                one light per shape; ``"face"`` gives every face its own.

        Returns:
            (list) the created light transforms.
        """
        grouped = cls._group_by_shape(objects)
        if not grouped:
            return []

        if toward is None:
            toward = cls._scene_center()

        created: List[str] = []
        for shape, shape_members in grouped.items():
            for members in cls._clusters(shape, shape_members, cluster):
                plate = cls._solve_plate(members, toward, offset)
                if plate is None:
                    continue
                created.append(
                    cls._build_light(
                        shape,
                        members,
                        plate,
                        intensity=intensity,
                        kelvin=kelvin,
                        color=color,
                        prefix=prefix,
                        emit_specular=emit_specular,
                    )
                )
        if created:
            # Per-area emission rides mtoa's aiNormalize extension attr, which
            # only exists while the plugin is loaded. Created without it, the
            # lights fall back to Arnold's normalized default and bake ~100x
            # dimmer than their intensity suggests -- say so ONCE now, at
            # authoring time, not per light and not at bake time.
            probe = cmds.listRelatives(created[0], shapes=True, fullPath=True)[0]
            if not cmds.attributeQuery("aiNormalize", node=probe, exists=True):
                cmds.warning(
                    "lights_from_geometry: mtoa is not loaded, so the created "
                    "lights could not be set to per-area emission (aiNormalize "
                    "off). Loaded later, they will bake ~100x dimmer than "
                    "their intensity suggests -- turn Normalize off on them."
                )
        return created

    @classmethod
    def _solve_plate(cls, members, toward, offset):
        """The emitter for one cluster: oriented from its points when it has them.

        Components carry both the vertices and the facing, so the oriented solve
        is exact and needs no ``toward`` guess. A whole-object member has
        neither, so it falls back to the axis-aligned box.
        """
        normal = cls._face_normal(members)
        points = cls._world_points(members) if normal else []
        if normal and len(points) >= 3:
            return ptk.PlateEmitter.from_points(
                points, normal=normal, offset=offset, up_axis=1
            )
        bounds = cmds.exactWorldBoundingBox(members)
        # Maya is Y-up, so an ambiguous (coplanar) plate resolves to "down"
        # about axis 1 rather than Blender's 2.
        return ptk.PlateEmitter.from_bounds(
            bounds[:3], bounds[3:], toward=toward, offset=offset, up_axis=1
        )

    @classmethod
    def _build_light(
        cls,
        shape,
        members,
        plate,
        intensity,
        kelvin,
        color,
        prefix,
        emit_specular,
    ):
        """Create and place one area light for a solved *plate*."""
        # Named after the shape's PARENT, not the shape: instances share one
        # shape node, so the shape leaf is the same string for every fixture in
        # an instanced ceiling grid and Maya would number the lights after the
        # original ("lensShape_areaLight1/2/3") with nothing tying each back to
        # the module it lights. The instance path's transform leaf is the name
        # the artist actually sees in the outliner.
        parts = shape.split("|")
        leaf = parts[-2] if len(parts) > 1 and parts[-2] else parts[-1]

        transform = cmds.shadingNode("areaLight", asLight=True)
        # shadingNode hands back the transform for a light, not the shape, and
        # as a SHORT name -- resolved to a full path so what this returns
        # matches what the queries find, and so a duplicate leaf name
        # downstream is unambiguous.
        if cmds.nodeType(transform) != "transform":
            transform = cmds.listRelatives(transform, parent=True, fullPath=True)[0]
        transform = cmds.ls(transform, long=True)[0]
        # Renamed rather than named at creation: shadingNode applies *name* to
        # the light NODE and lets Maya derive the transform's, which only lands
        # on the asked-for name when the string happens to contain "Shape"
        # ("lensShape_areaLight" -> "lens_areaLight", but "lens_areaLight" ->
        # "areaLight1"). Measured, and it is the transform the artist sees.
        transform = cmds.ls(
            cmds.rename(transform, f"{prefix}{leaf}_areaLight"), long=True
        )[0]
        light = cmds.listRelatives(
            transform, shapes=True, fullPath=True, noIntermediate=True
        )[0]
        light = cmds.ls(
            cmds.rename(light, f"{transform.rsplit('|', 1)[-1]}Shape"), long=True
        )[0]

        cls._place(transform, plate)

        # Explicit colour wins, then the fixture's own emission, then the
        # colour temperature. Spelled out rather than chained: the nested
        # ternary read as though kelvin could override the material.
        if color is not None:
            rgb = list(color)[:3]
        else:
            rgb = cls._emissive_color(members)
            if rgb is None and kelvin:
                rgb = ptk.ImgUtils.kelvin_to_linear_rgb(kelvin)
        if rgb:
            cmds.setAttr(f"{light}.color", *rgb, type="double3")
        cmds.setAttr(f"{light}.intensity", float(intensity))
        if cmds.attributeQuery("emitSpecular", node=light, exists=True):
            cmds.setAttr(f"{light}.emitSpecular", bool(emit_specular))
        # Per-AREA emission, not normalized total: Arnold's normalize spreads
        # the intensity over the emitter, so a fixture-sized plate (the whole
        # point of this tool) bakes ~100x dimmer than the same intensity on a
        # small light -- measured on a production room, a licensed solo bake:
        # far wall 0.009 normalized vs 1.03 normalize-off at the same
        # intensities. Per-area is also the physical reading of a luminous
        # panel: a bigger fixture emits MORE total light, and it matches how
        # Blender treats emissive strength. mtoa-only attr, so guarded.
        if cmds.attributeQuery("aiNormalize", node=light, exists=True):
            cmds.setAttr(f"{light}.aiNormalize", 0)

        cmds.addAttr(transform, longName=cls.SOURCE_ATTR, dataType="string")
        cmds.setAttr(f"{transform}.{cls.SOURCE_ATTR}", ",".join(members), type="string")
        return transform

    @classmethod
    def _place(cls, transform, plate):
        """Position, orient and size *transform* to a solved *plate*.

        Written as a single world matrix rather than translate + aimConstraint +
        scale, because an oriented plate needs its ROLL set too: an aim
        constraint fixes only where the light points and leaves a rotated
        rectangle's long edge wherever the world-up vector happens to put it.

        A Maya area light emits along its local -Z and is 2x2 in local space, so
        the basis rows carry the emitter's real size: local X along the plate's
        long edge, local Z against the emission normal.
        """
        x_axis = plate.tangent
        z_axis = tuple(-c for c in plate.normal)
        y_axis = (
            z_axis[1] * x_axis[2] - z_axis[2] * x_axis[1],
            z_axis[2] * x_axis[0] - z_axis[0] * x_axis[2],
            z_axis[0] * x_axis[1] - z_axis[1] * x_axis[0],
        )
        scale_x = plate.size[0] / cls.AREA_LOCAL_SIZE
        scale_y = plate.size[1] / cls.AREA_LOCAL_SIZE
        matrix = (
            [x_axis[i] * scale_x for i in range(3)] + [0.0]
            + [y_axis[i] * scale_y for i in range(3)] + [0.0]
            + list(z_axis) + [0.0]
            + list(plate.position) + [1.0]
        )
        cmds.xform(transform, matrix=matrix, worldSpace=True)

    @classmethod
    def generated_lights(cls) -> List[str]:
        """Every light this tool generated.

        Found by the marker attribute rather than by name or by a set, so the
        answer survives renaming, reparenting, saving and reloading -- which is
        the whole reason the marker is a property on the light.

        Queried as a PLUG pattern (``*.geoLightSource``) rather than by walking
        every transform and asking whether it carries the attribute: the scan is
        O(scene) and this runs on every sync and every teardown.
        """
        # recursive=True searches namespaces too. Without it the pattern matches
        # only the current namespace, so every light in a referenced or
        # namespaced scene silently escapes teardown -- measured: 2 of 3 found.
        return (
            cmds.ls(
                f"*.{cls.SOURCE_ATTR}", objectsOnly=True, long=True, recursive=True
            )
            or []
        )

    @classmethod
    def upgrade_authored_lights(cls) -> List[str]:
        """Bring generated lights up to per-area emission; return those changed.

        :meth:`lights_from_geometry` authors per-area lights (``aiNormalize``
        off) -- but it hasn't always, and a saved scene keeps the marker attr,
        not the semantics of the session that created it. A scene saved with
        older-authored lights reopens NORMALIZED: intensity spread over the
        emitter, fixture-scale plates baking ~100x dim (measured: a production
        room re-baked at 0.006 mean because the manual Normalize fix
        evaporated with the reopen). Run before every bake -- idempotent, and
        strictly scoped to lights carrying :attr:`SOURCE_ATTR`, so a
        hand-authored light (whose normalize state is the artist's) is never
        touched.

        Loads mtoa when there is something to upgrade: the attr only exists
        with the plugin, and an Arnold bake is about to need it anyway.
        """
        lights = cls.generated_lights()
        if not lights:
            return []
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except RuntimeError:
            pass
        upgraded: List[str] = []
        for transform in lights:
            # The marker lives on the transform, but tolerate a tagged shape.
            shape = (
                transform
                if cmds.ls(transform, lights=True)
                else (
                    cmds.listRelatives(
                        transform, shapes=True, fullPath=True, noIntermediate=True
                    )
                    or [None]
                )[0]
            )
            if not shape or not cmds.attributeQuery(
                "aiNormalize", node=shape, exists=True
            ):
                continue
            if cmds.getAttr(f"{shape}.aiNormalize"):
                cmds.setAttr(f"{shape}.aiNormalize", 0)
                upgraded.append(transform)
        return upgraded

    @classmethod
    def remove_lights(cls) -> List[str]:
        """Delete this tool's generated lights; return the names removed.

        The teardown half of the marker, and the mirror of blendertk's
        ``remove_lights``.

        Only lights this tool generated are touched -- a hand-authored light
        that happens to sit in the same place carries no marker and is left
        alone, which is what makes this safe to run on somebody else's scene.
        """
        removed = cls.generated_lights()
        if removed:
            cmds.delete(removed)
        return removed

    @classmethod
    def sync_lights_from_geometry(
        cls,
        lights: Optional[Sequence[Any]] = None,
        offset: float = 1.0,
    ) -> List[str]:
        """Re-fit generated lights to their source geometry; return those updated.

        The counterpart to the source property: geometry moves, and a light
        derived from it should be able to follow without being rebuilt (which
        would discard every intensity and colour tweak made since). Only
        *placement* is re-solved -- size, position, orientation -- because those
        are the geometry's to own and everything else is the artist's.

        Solved and placed through the same helpers creation uses, so a sync
        cannot disagree with a rebuild: re-fitting an oriented emitter with the
        bounding-box solver would quietly flatten it back to axis-aligned.

        *lights* defaults to every marked light in the scene, so a scene-wide
        re-fit is one call with no selection.
        """
        if lights is None:
            lights = cls.generated_lights()

        toward = cls._scene_center()

        updated: List[str] = []
        for transform in ptk.make_iterable(lights):
            transform = str(transform)
            plug = f"{transform}.{cls.SOURCE_ATTR}"
            if not cmds.objExists(plug):
                continue
            members = [m for m in (cmds.getAttr(plug) or "").split(",") if m]
            # A source that no longer exists is skipped, not guessed at: the
            # light stays exactly where the artist last saw it.
            members = [m for m in members if cmds.objExists(m.split(".", 1)[0])]
            if not members:
                continue

            plate = cls._solve_plate(members, toward, offset)
            if plate is None:
                continue
            cls._place(transform, plate)
            updated.append(transform)
        return updated
