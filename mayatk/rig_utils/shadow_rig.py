# !/usr/bin/python
# coding=utf-8
import os
import math

import numpy as np

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk import NodeUtils
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.core_utils.preview import Preview, OperationError


class ShadowRig(ptk.LoggingMixin):
    """Projected shadow for engine export (Unity, WebXR).

    A single quad plane on the ground carrying the target's shadow as a PNG
    texture. The shadow is the target's geometry projected onto the ground
    through the source — the real thing, not a side view: an overhead source
    draws the footprint, a low sun the long stretched shape, a near lamp the
    perspective-grown head, and a source with a size (an area light, the
    sun's half-degree disc) a penumbra that widens away from the contact.
    The projection lives in ``pythontk.ShadowProjection``; the DCC supplies
    the geometry and the source, and writes the PNG.

    Between rasterizations the plane follows the projection's *model* — the
    shadow of the target's bounding cylinder, a handful of ratios an
    expression evaluates per frame — so its direction, reach, perspective
    growth and slide (a target leaving the ground) track the source and the
    target live. The canvas the PNG was drawn into is stamped on the plane as
    fractions of that model, which is what re-places it at any light
    position with a grounded target's feet kept under the silhouette's;
    **Follow Source** (:meth:`auto_recalculate`) re-renders the PNG as soon
    as the silhouette itself has gone stale -- the source, or the target
    under it, moved past :attr:`AUTO_RECALCULATE_DEG` -- and **Recalculate
    Silhouette** / **Reproject** do it by hand (a rig is reported stale at
    export past :attr:`_STALE_BEARING_DEG`). The plane's transform channels
    and its ``opacity`` fade bake to keyframes for FBX.

    Sources: any transform (the rig reads its world position) — a locator, a
    point/spot/area light — or a ``directionalLight``, whose *direction*
    (world -Z) is projected along instead of a position, the way a sun works.

    Opacity: the source-size penumbra is in the PNG; the runtime fade on the
    plane's keyable ``opacity`` (the RenderOpacity preset) lightens a shadow
    as it elongates (``falloffPower``), kills it when the source drops below
    the target, and fades a target rising off the ground (``fadeHeight``).

    Material: ``standardSurface`` with the silhouette in ``baseColor`` (black
    RGB, silhouette in alpha) and ``file alpha x opacity`` into the shader's
    opacity — the one wiring VP2 renders per pixel (the StingrayPBS graphs
    can't preview the fade; measured 2026-09-02).

    Workflow for Unity:
    1. Create shadow with ShadowRig.create()
    2. Export through the Scene Exporter — its smart-bake task bakes the
       expression non-destructively, and the rig publishes a
       ``shadow_metadata`` channel on the ``data_export`` carrier
       (refreshed at export time via FbxUtils.run_export_preparers). For
       File > Export, the Game Exporter and the bridges, bake first with
       ShadowRig.bake() (or the panel's Bake to Keyframes button).
    3. In Unity: with unitytk's ShadowPlaneController.cs deployed, the
       import is automatic (unlit-transparent material bound to the
       silhouette, shadow casting/receiving + probes off). The silhouette
       PNG is an ordinary material texture, so Embed Textures ships it.
    4. WebXR / GLB: the sidecar's base-colour and alpha-mode sections carry
       the silhouette and the blend mode; the baked ``opacity`` ramp rides
       the visibility-tracks channel.

    Re-attaching: :meth:`for_node` / :meth:`from_plane` rebuild an instance
    from the stamps on an existing plane (the plane, its group, a target, the
    source, or any support node resolves it), for :meth:`set_source`,
    :meth:`rebuild`, :meth:`unbake_planes`, :meth:`refresh_silhouette` and
    :meth:`delete_rigs` — the panel's Utility section.
    """

    MODES = ("orbit",)
    # Retired modes accepted for one release, mapped to their replacement.
    _DEPRECATED_MODES = {"stretch": "orbit"}
    DEFAULT_SOURCE_NAME = "shadow_source"
    # Lift above the ground plane to avoid z-fighting (build + expression).
    GROUND_OFFSET = 0.01
    # The RenderOpacity preset's attr: the same name Unity's importer and the
    # visibility-tracks producer key on, so the fade needs no channel of its own.
    OPACITY_ATTR = "opacity"
    # Channels the expression drives (and bake() keys).
    BAKE_CHANNELS = (
        "translateX",
        "translateY",
        "translateZ",
        "rotateY",
        "scaleX",
        "scaleZ",
        OPACITY_ATTR,
    )
    # data_export carrier channel (see refresh_export_metadata).
    SHADOW_METADATA = "shadow_metadata"
    # Multi message attr on the plane linking every support node this rig
    # created — delete_rigs' rename-proof teardown manifest.
    _MEMBER_ATTR = "shadowRigMembers"
    # Message links from the plane to what the rig was built FROM — the
    # rename-proof handles refresh_silhouette needs once the Python instance
    # (and, after a bake, the decomposeMatrix connections) are gone.
    _TARGETS_ATTR = "shadowRigTargets"
    _SOURCE_ATTR = "shadowRigSource"
    # The unit 3D direction (source -> contact; a directional light's own
    # direction) the silhouette was rasterized from, in the contact's own
    # frame, so a moved source -- or a target turned or carried under it --
    # reads as stale (at export, and to Follow Source).
    _BEARING_ATTRS = ("silhouetteBearingX", "silhouetteBearingY", "silhouetteBearingZ")
    # The canvas the PNG covers (pythontk ShadowProjection.fractions): its
    # far edge in projected-head radii from where the head lands, its near
    # edge as a fraction of that far edge, its sides as fractions of the
    # width — what the expression re-places the plane from at any light
    # position, the anchor (a grounded target's feet) keeping its place in
    # the texture.
    _CANVAS_ATTRS = ("canvasU0", "canvasU1", "canvasW0", "canvasW1")
    _RECURSIVE_ATTR = "silhouetteRecursive"
    _STALE_BEARING_DEG = 10.0
    # The stale test's second yardstick: how far the source sat from the
    # contact when the silhouette was drawn (0 for a directional source). A
    # positional source moved in or out along the same bearing changes the
    # drawn shape too (perspective growth), and the bearing alone misses it.
    _DISTANCE_ATTR = "silhouetteDistance"
    #: Follow Source (:meth:`auto_recalculate`) re-renders a silhouette once
    #: its source has moved this far: degrees of bearing, or this fraction of
    #: its distance -- tight enough to read as live, loose enough that a
    #: nudge does not rewrite the PNG.
    AUTO_RECALCULATE_DEG = 2.0
    AUTO_RECALCULATE_DISTANCE = 0.1
    #: Softness, on a SOURCE transform (the panel's Softness control): the
    #: diameter the shadow gives the source, in world units -- for a
    #: directional light its angular diameter in DEGREES -- overriding the
    #: light's own physical size (:meth:`source_size`). Absent = physical.
    SOFTNESS_ATTR = "shadowSoftness"
    # Follow Source's state: the ScriptJobManager owner its callbacks hang
    # off, the transforms already watched (long names), and whether a
    # deferred re-render is queued (one per idle, however many attribute
    # sets a drag produces).
    _AUTO_OWNER = "ShadowRig.auto_recalculate"
    # The per-node callbacks under their own owner, so a re-arm (a scene
    # opened) drops the ones on nodes that no longer exist instead of
    # piling new ones on top.
    _AUTO_NODES_OWNER = "ShadowRig.auto_recalculate.nodes"
    _auto_on = False
    _auto_pending = False
    _auto_watched: set = set()
    # The plane mesh is built at unit size, so a baked scale reads as the
    # canvas extent in world units.
    PLANE_SIZE = 1.0

    #: Rig types, in the order the panel lists them. ``projected`` draws one
    #: silhouette the expression re-places; ``horizon`` adds a coverage-aware
    #: horizon map (``pythontk.ShadowHorizon``) the engine samples per frame
    #: so the outline follows a runtime light — see
    #: ``mayatk/docs/shadow_rig_morphing.md``.
    RIG_TYPES = ("projected", "horizon")
    _TYPE_ATTR = "shadowRigType"
    #: The engine places the quad from the source node at runtime while this
    #: is on; off leaves the imported keys alone.
    FOLLOW_ATTR = "followSource"
    #: Silhouette atlas stamps: the atlas PNG's basename and the plane's inset
    #: rect in it (``scaleX, scaleY, offsetX, offsetY``, bottom-left origin).
    _ATLAS_TEX_ATTR = "atlasTexture"
    _ATLAS_RECT_ATTRS = ("atlasScaleX", "atlasScaleY", "atlasOffsetX", "atlasOffsetY")
    #: Horizon map stamps (the record's ``horizon`` block).
    _HORIZON_TEX_ATTR = "horizonTexture"
    _HORIZON_INT_ATTRS = ("horizonSize", "horizonSpans", "horizonLevels")
    # The footprint the map covers, in the contact's frame, and the height a
    # 16-bit channel value of 65535 stands for: the map's own scale, apart
    # from the plane's live ``maxStretch`` (a placement cap the user can
    # retune afterwards without a re-bake).
    _HORIZON_FLOAT_ATTRS = (
        "horizonBoundsA0",
        "horizonBoundsA1",
        "horizonBoundsB0",
        "horizonBoundsB1",
        "horizonHeightScale",
    )
    _HORIZON_RECT_ATTRS = (
        "horizonScaleX",
        "horizonScaleY",
        "horizonOffsetX",
        "horizonOffsetY",
    )
    _HORIZON_HASH_ATTR = "horizonHash"
    #: The atlas the horizon block was packed into (the record's
    #: ``horizon.texture`` while packed) and the per-plane silhouette PNG a
    #: packed plane's file node no longer names.
    _HORIZON_ATLAS_ATTR = "horizonAtlas"
    _SILHOUETTE_ATTR = "silhouetteTexture"
    #: Each packed tile's pixel rect (row0, row1, col0, col1): Recalculate
    #: rewrites the texels in place without a repack.
    _ATLAS_PIXEL_ATTRS = ("atlasRow0", "atlasRow1", "atlasCol0", "atlasCol1")
    _HORIZON_PIXEL_ATTRS = ("horizonRow0", "horizonRow1", "horizonCol0", "horizonCol1")
    #: The map's bearing frame in FBX / glTF axes: Maya's contact-local +X
    #: and +Z (the contract's ``frame_a`` / ``frame_b``).
    HORIZON_FRAME = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    #: The ``shadow_metadata`` schema this producer writes.
    METADATA_VERSION = 2
    #: Atlas PNG per rig type, beside the silhouettes in ``sourceimages``.
    ATLAS_BASENAMES = {
        "projected": "shadow_atlas_projected.png",
        "horizon": "shadow_atlas_horizon.png",
    }

    def __init__(
        self,
        targets=None,
        light=None,
        ground_height=0.0,
        mode="orbit",
        source_name=None,
        name_base=None,
    ):
        # Accept single target or list of targets
        if targets is None:
            self.targets = []
        elif isinstance(targets, (list, tuple)):
            self.targets = [str(t) for t in targets]
        else:
            self.targets = [str(targets)]

        self.light = str(light) if light else None
        self.shadow_plane = None
        self.contact_locator = None
        self.ground_height = float(ground_height)
        self.shader = None
        self.opacity_mult = None
        self.texture_path = None
        self.group = None
        self.plane_size = self.PLANE_SIZE
        self.object_height = 0.0
        self.footprint_radius = 0.0
        self.canvas = None  # (u0, u1, w0, w1) fractions, once rasterized
        self.mode = self._resolve_mode(mode)
        self.rig_type = self.RIG_TYPES[0]
        self.horizon_path = None  # the horizon map PNG, once baked

        if name_base is not None:
            # Re-attaching to an existing rig (refresh_silhouette): the base is
            # the plane's own and must not be uniquified against itself.
            self._name_base = str(name_base)
            return

        # For naming, use first target or "combined" — uniquified against
        # existing rigs: every rig node/texture is named off this base, and a
        # collision (two multi-target "combined" rigs, or re-creating a
        # target's rig) would delete the older rig's decomposeMatrix nodes
        # out from under its expression and overwrite its silhouette PNG.
        # short_name strips DAG path + namespace: '|' is illegal in the name
        # flag (duplicate leaf names force path-qualified targets) and ':' in
        # the texture filename silently writes an NTFS alternate data stream.
        base = (
            CoreUtils.short_name(self.targets[0])
            if len(self.targets) == 1
            else "combined"
        )
        # One plane per source: a non-default source joins the base so two
        # sources on one target get distinct nodes and PNGs, while the
        # default keeps every existing name (Box_shadow, Box_shadow.png).
        source_leaf = CoreUtils.short_name(source_name) if source_name else ""
        if source_leaf and source_leaf != self.DEFAULT_SOURCE_NAME:
            base = f"{base}_{source_leaf}"
        i, unique = 0, base
        while cmds.objExists(f"{unique}_shadow_grp"):
            i += 1
            unique = f"{base}{i}"
        self._name_base = unique

    @classmethod
    def _resolve_mode(cls, mode):
        """The live mode for *mode*, warning once per build on a retired alias."""
        mode = str(mode or "orbit").lower()
        if mode in cls._DEPRECATED_MODES:
            live = cls._DEPRECATED_MODES[mode]
            cls.logger.warning(
                f"ShadowRig mode '{mode}' is retired and builds as '{live}': the "
                "axis-aligned plane placed the silhouette upside down for any "
                "light on the +Z side."
            )
            return live
        return mode if mode in cls.MODES else "orbit"

    # ------------------------------------------------------------- measure
    def _world_bbox(self):
        """``exactWorldBoundingBox`` over the targets' MESH geometry only.

        Helper shapes parented under a target — this rig's own contact
        locator, most notably — must not pollute the measurement (the
        locator sits at min-Y, so including it inflates ``objectHeight``
        by its display size). Mirrors blendertk's empty-skipping bounds.
        """
        shapes = []
        for t in self.targets:
            shapes += cmds.ls(t, type="mesh") or []  # target may BE a shape
            shapes += cmds.listRelatives(t, ad=True, type="mesh", fullPath=True) or []
        # Intermediate (Orig) shapes hold pre-deformation geometry — a skinned
        # target posed away from bind pose would union its bind pose into the
        # measurement, skewing objectHeight / plane size / center.
        shapes = cmds.ls(shapes, noIntermediate=True) or []
        if not shapes:
            return cmds.exactWorldBoundingBox(self.targets)
        return cmds.exactWorldBoundingBox(shapes)

    @staticmethod
    def has_mesh_geometry(node, recursive=True):
        """Does *node* carry (or, with *recursive*, contain) mesh geometry a
        shadow can be cast from? False for lights, locators, empty groups."""
        node = str(node)
        if not cmds.objExists(node):
            return False
        if cmds.ls(node, type="mesh", noIntermediate=True):
            return True
        if cmds.listRelatives(node, shapes=True, type="mesh", noIntermediate=True):
            return True
        if recursive and cmds.listRelatives(node, ad=True, type="mesh"):
            return True
        return False

    def create_contact_locator(self):
        """Create a locator at the lowest point of the combined objects to act as the shadow anchor."""
        bbox = self._world_bbox()
        # BBox is [xmin, ymin, zmin, xmax, ymax, zmax]
        center_x = (bbox[0] + bbox[3]) / 2.0
        min_y = bbox[1]
        center_z = (bbox[2] + bbox[5]) / 2.0

        self.contact_locator = cmds.spaceLocator(name=f"{self._name_base}_contact_loc")[
            0
        ]
        cmds.setAttr(
            f"{self.contact_locator}.translate",
            center_x,
            min_y,
            center_z,
            type="double3",
        )
        cmds.setAttr(
            f"{self.contact_locator}.localScale", 0.2, 0.2, 0.2, type="double3"
        )

        # Parent to first target so it moves/animates with it
        self.contact_locator = cmds.parent(self.contact_locator, self.targets[0])[0]

        return self.contact_locator

    # -------------------------------------------------------------- source
    @classmethod
    def ensure_source(cls, source_name=DEFAULT_SOURCE_NAME, position=(5, 10, 5)):
        """The transform named *source_name*, created as a locator if absent.

        Any transform is a valid source — the rig reads its world position —
        so a light (e.g. one built by ``LightUtils.lights_from_geometry``)
        resolves here as-is; a ``directionalLight`` is projected along its
        direction instead. A path-qualified or namespaced name resolves the
        same way, and a light SHAPE's name resolves to its transform. The
        panel calls this at preview enable, OUTSIDE the preview contract, so
        a source the user then positions survives every refresh and the
        commit replay (built inside the contract it was deleted and
        recreated at the default position on each).

        Raises:
            ValueError: the name is taken by a non-transform (a shader, a
                set...) — the build would fail deep in the expression.
        """
        source_name = str(source_name or cls.DEFAULT_SOURCE_NAME)
        if cmds.objExists(source_name):
            existing = cmds.ls(source_name, transforms=True, long=True)
            if not existing:
                shapes = cmds.ls(source_name, shapes=True, long=True) or []
                parents = (
                    cmds.listRelatives(shapes[0], parent=True, fullPath=True)
                    if shapes
                    else None
                )
                if parents:
                    return parents[0]
                raise ValueError(
                    f"Existing node '{source_name}' is not a transform — "
                    "choose a different shadow-source name."
                )
            if len(existing) > 1:
                cls.logger.warning(
                    f"Ambiguous shadow source '{source_name}' matched "
                    f"{len(existing)} transforms; using {existing[0]}."
                )
            return existing[0]

        light = cmds.spaceLocator(name=source_name)[0]
        cmds.setAttr(
            f"{light}.translate",
            position[0],
            position[1],
            position[2],
            type="double3",
        )
        cmds.setAttr(f"{light}.localScale", 1, 1, 1, type="double3")
        # Yellow color
        shapes = NodeUtils.get_shapes(light, no_intermediate=False)
        if shapes:
            shape = shapes[0]
            cmds.setAttr(f"{shape}.overrideEnabled", True)
            cmds.setAttr(f"{shape}.overrideColor", 17)
        # Created OUTSIDE any Preview contract (see above), so the Preview's
        # own isolation pass never sees it: a user in "view selected" would
        # get an invisible source to position. No-op unless a panel isolates.
        DisplayUtils.add_to_isolation_set(light)
        return light

    def get_or_create_shadow_source(
        self, position=(5, 10, 5), source_name=DEFAULT_SOURCE_NAME
    ):
        """Bind this rig to :meth:`ensure_source`'s transform for *source_name*.

        Args:
            position: Initial position if creating new.
            source_name: Name for the shadow source locator.
        """
        existed = cmds.objExists(str(source_name or self.DEFAULT_SOURCE_NAME))
        self.light = self.ensure_source(source_name, position)
        if existed:
            self.logger.info(f"Using existing shadow source: {self.light}")
        return self.light

    @staticmethod
    def _source_shape(source):
        """The source's light shape (long name) or None for a plain transform."""
        if not source or not cmds.objExists(source):
            return None
        shapes = cmds.listRelatives(
            source, shapes=True, fullPath=True, noIntermediate=True
        )
        for shape in shapes or []:
            if "light" in cmds.nodeType(shape, inherited=True):
                return shape
        return None

    @classmethod
    def source_is_directional(cls, source):
        """Is *source* projected along its direction (a ``directionalLight``)
        rather than from its position?"""
        shape = cls._source_shape(source)
        return bool(shape) and cmds.nodeType(shape) == "directionalLight"

    def _source_ray(self):
        """``(position, direction)`` of the source in world space — one of the
        two is None: a directional light shines along its world -Z axis, any
        other transform casts from where it sits."""
        if not self.light or not cmds.objExists(self.light):
            raise ValueError("The shadow source is missing.")
        if self.source_is_directional(self.light):
            m = cmds.xform(self.light, q=True, ws=True, matrix=True)
            d = np.array([-m[8], -m[9], -m[10]], dtype=float)
            n = np.linalg.norm(d)
            return None, tuple(d / n if n > 1e-12 else (0.0, -1.0, 0.0))
        return tuple(cmds.xform(self.light, q=True, ws=True, t=True)), None

    @classmethod
    def source_size(cls, source):
        """The size the shadow gives *source*, the penumbra's cause: its
        Softness when set (:attr:`SOFTNESS_ATTR`; degrees become radians for
        a directional light), else its physical size -- an area light's world
        diameter (its 2x2 local plate x world scale), a point/spot light's
        Arnold radius x2, a directional light's Arnold angle in radians; 0
        (sharp) for a locator or a light without one."""
        if not source or not cmds.objExists(source):
            return 0.0
        directional = cls.source_is_directional(source)
        softness = cls.source_softness(source)
        if softness is not None:
            return math.radians(softness) if directional else softness
        shape = cls._source_shape(source)
        if not shape:
            return 0.0
        kind = cmds.nodeType(shape)
        if kind == "areaLight":
            from mayatk.light_utils._light_utils import LightUtils

            m = cmds.xform(source, q=True, ws=True, matrix=True)
            sx = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2)
            sy = math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2)
            return LightUtils.AREA_LOCAL_SIZE * 0.5 * (sx + sy)
        if kind == "directionalLight":
            if cmds.attributeQuery("aiAngle", node=shape, exists=True):
                return math.radians(cmds.getAttr(f"{shape}.aiAngle"))
            return 0.0
        if cmds.attributeQuery("aiRadius", node=shape, exists=True):
            return 2.0 * cmds.getAttr(f"{shape}.aiRadius")
        return 0.0

    def _source_size(self):
        return self.source_size(self.light)

    @classmethod
    def source_softness(cls, source):
        """The Softness set on *source* -- world units, degrees for a
        directional light -- or None when it carries none (its physical
        size applies)."""
        if not source or not cmds.objExists(source):
            return None
        if not cmds.attributeQuery(cls.SOFTNESS_ATTR, node=source, exists=True):
            return None
        return max(float(cmds.getAttr(f"{source}.{cls.SOFTNESS_ATTR}")), 0.0)

    @classmethod
    def set_source_softness(cls, source, value):
        """Give *source* a Softness (added on first use; 0 = sharp) and
        return the shadow planes it lights, in no order -- the ones to
        Recalculate so their penumbra and stamps follow.

        Raises:
            ValueError: *source* is not a transform in the scene.
        """
        if not source or not cmds.objExists(source):
            raise ValueError(f"Shadow source not found: {source!r}")
        if not cmds.attributeQuery(cls.SOFTNESS_ATTR, node=source, exists=True):
            cmds.addAttr(source, ln=cls.SOFTNESS_ATTR, at="double", min=0.0, dv=0.0)
        cmds.setAttr(f"{source}.{cls.SOFTNESS_ATTR}", max(float(value), 0.0))
        return cls.planes_lit_by(source)

    @classmethod
    def planes_lit_by(cls, source):
        """The shadow planes whose stamped source is *source*."""
        wanted = cmds.ls(source, long=True)
        if not wanted:
            return []
        planes = []
        for plane in cls.find_shadow_planes():
            _, linked = cls._rig_links(plane)
            if linked and cmds.ls(linked, long=True) == wanted:
                planes.append(plane)
        return planes

    # --------------------------------------------------------------- plane
    def _ensure_plane_attr(self, ln, dv, min_val=None, max_val=None, keyable=True):
        """Add a double attr to the shadow plane if it doesn't exist yet
        (double: the canvas fractions and measured constants must round-trip
        exactly between the stamps and the rig instance)."""
        if not cmds.attributeQuery(ln, node=self.shadow_plane, exists=True):
            kwargs = {"ln": ln, "at": "double", "dv": dv, "k": keyable}
            if min_val is not None:
                kwargs["min"] = min_val
            if max_val is not None:
                kwargs["max"] = max_val
            cmds.addAttr(self.shadow_plane, **kwargs)

    def _measure_targets(self):
        """Stamp ``object_height`` / ``footprint_radius`` from the targets'
        world bounds (the bounding cylinder the model projects)."""
        bbox = self._world_bbox()
        self.object_height = max(bbox[4] - bbox[1], 0.001)
        self.footprint_radius = max(
            0.5 * math.hypot(bbox[3] - bbox[0], bbox[5] - bbox[2]), 0.001
        )
        return bbox

    def create_shadow_plane(self):
        """Create the unit quad for the shadow with the keyable shadow attrs
        and the measured constants the expression reads."""
        if not self.targets:
            raise ValueError("Target object(s) required")

        bbox = self._measure_targets()
        self.plane_size = self.PLANE_SIZE
        self.shadow_plane = cmds.polyPlane(
            name=f"{self._name_base}_shadow",
            width=self.plane_size,
            height=self.plane_size,
            sx=1,
            sy=1,
            axis=(0, 1, 0),
        )[0]

        # Art-direction attrs (keyable) + measured constants the expression reads.
        self._ensure_plane_attr("shadowIntensity", 1.0, 0.0, 1.0)
        self._ensure_plane_attr("falloffPower", 1.2, 0.0, 5.0)
        # Cap on the shadow's reach, in object heights (a sun at 9.5 deg).
        self._ensure_plane_attr(
            "maxStretch", ptk.ShadowProjection.DEFAULT_MAX_STRETCH, 0.0, 20.0
        )
        # Rise above the ground at which the shadow has fully faded out.
        self._ensure_plane_attr("fadeHeight", max(2.0 * self.object_height, 0.001), 0.0)
        # World Y of the ground the shadow lies on — an attr rather than a
        # literal in the expression, so a raised floor is a post-create edit.
        self._ensure_plane_attr("groundHeight", self.ground_height, keyable=False)
        self._ensure_plane_attr("basePlaneSize", self.plane_size, keyable=False)
        self._ensure_plane_attr("objectHeight", self.object_height, keyable=False)
        self._ensure_plane_attr("footprintRadius", self.footprint_radius, keyable=False)
        # Canvas fractions (stamped by the rasterizer) and the raster bearing.
        for attr, dv in zip(self._CANVAS_ATTRS, (-1.0, 1.0, -0.5, 0.5)):
            self._ensure_plane_attr(attr, dv, keyable=False)
        for attr in self._BEARING_ATTRS:
            self._ensure_plane_attr(attr, 0.0, keyable=False)
        self._ensure_plane_attr(self._DISTANCE_ATTR, 0.0, keyable=False)
        self._ensure_plane_attr("sourceSize", 0.0, keyable=False)
        if not cmds.attributeQuery(
            self._RECURSIVE_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.addAttr(self.shadow_plane, ln=self._RECURSIVE_ATTR, at="bool", dv=True)
        # The rig type (the record's ``type``) and the runtime-placement flag.
        if not cmds.attributeQuery(
            self._TYPE_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.addAttr(self.shadow_plane, ln=self._TYPE_ATTR, dt="string")
        cmds.setAttr(
            f"{self.shadow_plane}.{self._TYPE_ATTR}", self.rig_type, type="string"
        )
        if not cmds.attributeQuery(
            self.FOLLOW_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.addAttr(
                self.shadow_plane, ln=self.FOLLOW_ATTR, at="bool", dv=True, k=True
            )
        # The fade channel: the RenderOpacity preset (0-1, keyable) so the
        # name and range match what Unity's importer and the visibility
        # producer already read.
        Attributes.apply_preset(self.OPACITY_ATTR, [self.shadow_plane])
        # Measured constants are always restamped to this build's values.
        cmds.setAttr(f"{self.shadow_plane}.groundHeight", self.ground_height)
        cmds.setAttr(f"{self.shadow_plane}.basePlaneSize", self.plane_size)
        cmds.setAttr(f"{self.shadow_plane}.objectHeight", self.object_height)
        cmds.setAttr(f"{self.shadow_plane}.footprintRadius", self.footprint_radius)

        # Keep plane centered - pivot at center, vertices centered around origin
        # The expression handles positioning based on light direction
        cmds.xform(self.shadow_plane, pivots=[0, 0, 0], objectSpace=True)

        # Position at combined targets center
        center_x = (bbox[0] + bbox[3]) / 2.0
        center_z = (bbox[2] + bbox[5]) / 2.0
        cmds.setAttr(
            f"{self.shadow_plane}.translate",
            center_x,
            self.ground_height + self.GROUND_OFFSET,
            center_z,
            type="double3",
        )

        return self.shadow_plane

    def _gather_world_meshes(self, recursive=True):
        """``[(points, tris)]`` world-space arrays for every target mesh shape.

        Walks transforms first so instanced shapes yield one path per parent
        (otherwise listRelatives dedupes by node and we miss instance copies).
        """
        shapes = []
        for target in self.targets:
            if recursive:
                transforms = [target] + (
                    cmds.listRelatives(target, ad=True, type="transform", fullPath=True)
                    or []
                )
            else:
                transforms = [target]

            target_shapes = []
            for tx in transforms:
                # noIntermediate: an Orig shape holds pre-deformation geometry
                # — rasterizing it draws the bind pose into the silhouette of
                # a posed/skinned target (mirrors _world_bbox's filter).
                tx_shapes = (
                    cmds.listRelatives(
                        tx,
                        shapes=True,
                        type="mesh",
                        fullPath=True,
                        noIntermediate=True,
                    )
                    or []
                )
                target_shapes.extend(tx_shapes)

            if not target_shapes:
                direct_shapes = NodeUtils.get_shapes(target, no_intermediate=True)
                if direct_shapes:
                    target_shapes = direct_shapes

            shapes.extend(target_shapes)

        meshes = []
        for shape in shapes:
            try:
                sel_list = om2.MSelectionList()
                sel_list.add(str(shape))
                fn_mesh = om2.MFnMesh(sel_list.getDagPath(0))
                points = fn_mesh.getPoints(om2.MSpace.kWorld)
                _, tri_verts = fn_mesh.getTriangles()
                pts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
                tris = np.array(tri_verts, dtype=np.int64).reshape(-1, 3)
                if len(pts) and len(tris):
                    meshes.append((pts, tris))
            except Exception as e:
                self.logger.warning(f"Could not process shape {shape}: {e}")
        return meshes

    # ---------------------------------------------------------- silhouette
    def _source_distance(self):
        """The source's distance from the contact; 0 for a directional one
        (parallel rays: distance means nothing to the drawn shape)."""
        position, direction = self._source_ray()
        if direction is not None:
            return 0.0
        c = np.array(self._contact_point(), dtype=float)
        return float(np.linalg.norm(c - np.array(position, dtype=float)))

    def _stamp_bearing(self, bearing, recursive):
        """Record the unit 3D direction the silhouette was rasterized from
        (source -> contact; a directional light's own direction; in the
        contact's frame), the source's distance, and the descendant flag."""
        if not self.shadow_plane or not cmds.objExists(self.shadow_plane):
            return
        for attr, value in zip(self._BEARING_ATTRS, bearing):
            if cmds.attributeQuery(attr, node=self.shadow_plane, exists=True):
                cmds.setAttr(f"{self.shadow_plane}.{attr}", float(value))
        if cmds.attributeQuery(
            self._DISTANCE_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.setAttr(
                f"{self.shadow_plane}.{self._DISTANCE_ATTR}", self._source_distance()
            )
        if cmds.attributeQuery(
            self._RECURSIVE_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.setAttr(f"{self.shadow_plane}.{self._RECURSIVE_ATTR}", bool(recursive))

    def _stamp_canvas(self, fractions, source_size):
        """Record the canvas fractions the PNG covers (the expression re-places
        the plane from them) and the source size drawn into it."""
        self.canvas = tuple(float(f) for f in fractions)
        if not self.shadow_plane or not cmds.objExists(self.shadow_plane):
            return
        for attr, value in zip(self._CANVAS_ATTRS, self.canvas):
            if cmds.attributeQuery(attr, node=self.shadow_plane, exists=True):
                cmds.setAttr(f"{self.shadow_plane}.{attr}", value)
        if cmds.attributeQuery("sourceSize", node=self.shadow_plane, exists=True):
            cmds.setAttr(f"{self.shadow_plane}.sourceSize", float(source_size))

    def _contact_point(self):
        """The contact locator's world position (the model's base centre), or
        the measured footprint centre on the targets' underside before the
        locator exists."""
        if self.contact_locator and cmds.objExists(self.contact_locator):
            return tuple(cmds.xform(self.contact_locator, q=True, ws=True, t=True))
        bbox = self._world_bbox()
        return ((bbox[0] + bbox[3]) / 2.0, bbox[1], (bbox[2] + bbox[5]) / 2.0)

    def _max_stretch(self):
        """The plane's ``maxStretch`` (the reach cap, in object heights)."""
        if self.shadow_plane and cmds.attributeQuery(
            "maxStretch", node=self.shadow_plane, exists=True
        ):
            return cmds.getAttr(f"{self.shadow_plane}.maxStretch")
        return ptk.ShadowProjection.DEFAULT_MAX_STRETCH

    def current_model(self):
        """The projection model at the source's CURRENT position, for the
        stamped measurements — what the expression evaluates right now."""
        position, direction = self._source_ray()
        return ptk.ShadowProjection.model(
            self._contact_point(),
            position,
            self.ground_height,
            self.footprint_radius,
            self.object_height,
            up=1,
            direction=direction,
            max_stretch=self._max_stretch(),
        )

    def _current_bearing(self):
        """Unit 3D direction from the source to the contact (a directional
        light: its direction), in the contact's own frame — the stale
        check's yardstick. The contact rides under the target, so a target
        turned or carried to another bearing under the source reads as a
        moved source: the silhouette is one direction's projection of the
        target, whichever end moved."""
        position, direction = self._source_ray()
        if direction is not None:
            world = np.array(direction, dtype=float)
        else:
            c = np.array(self._contact_point(), dtype=float)
            world = c - np.array(position, dtype=float)
        n = float(np.linalg.norm(world))
        if n <= 1e-9:
            return (0.0, -1.0, 0.0)
        # Row-vector convention: world = local @ R, so local = R @ world.
        local = self._rigid_contact_frame()[:3, :3] @ (world / n)
        return tuple(float(v) for v in local)

    def create_silhouette_texture(
        self,
        size=512,
        axis="auto",
        recursive=True,
        *,
        uniform_alpha=True,
        falloff_power=0.8,
        vertical_weight=0.3,
        blur_amount=1.0,
        path=None,
        refit=True,
        source_size=None,
    ):
        """Rasterize the targets' shadow — their geometry projected onto the
        ground through the source — via ``pythontk.ImgUtils.rasterize_shadow``.

        Args:
            size: Texture resolution.
            axis: Retired. The silhouette is always the projection through
                the source (an overhead source draws the footprint, a low one
                the stretched shape); any other value warns and is ignored.
            recursive: If True, include descendant meshes (e.g. for groups/locators).
            uniform_alpha: Physically flat shadow (default). False adds the
                stylised contact falloff — alpha fading from the footprint to
                the tip — shaped by ``falloff_power`` / ``vertical_weight``.
            blur_amount: Edge anti-aliasing (pixels). The penumbra a sized
                source draws is separate and physical (see ``source_size``).
            path: Write here instead of ``<workspace>/sourceimages/<base>_shadow.png``
                — :meth:`refresh_silhouette` overwrites a rig's existing file
                in place so the file node and the engine join key stay valid.
            refit: Fit the canvas to the projected shadow and restamp the
                plane's canvas fractions (a live rig). False draws into the
                canvas the stamped fractions denote at the source's current
                position — a baked plane, whose keys already place it there.
            source_size: The source's diameter (world units; a directional
                light's angular diameter in radians) for the penumbra; None
                reads it off the source (:meth:`_source_size`).
        """
        from PIL import Image

        if str(axis).lower() not in ("auto", "light"):
            self.logger.warning(
                f"ShadowRig axis={axis!r} is retired and ignored: the silhouette "
                "is the target's projection through the source."
            )
        if path:
            self.texture_path = str(path)
            os.makedirs(os.path.dirname(self.texture_path) or ".", exist_ok=True)
        else:
            workspace = cmds.workspace(q=True, rd=True)
            output_dir = os.path.join(workspace, "sourceimages")
            os.makedirs(output_dir, exist_ok=True)
            self.texture_path = os.path.join(
                output_dir, f"{self._name_base}_shadow.png"
            )

        # Forward slashes: the form Maya writes back to a file node, so the
        # rig's own path compares equal to the node's and the exporter's
        # path tasks see one spelling.
        self.texture_path = self.texture_path.replace("\\", "/")

        meshes = self._gather_world_meshes(recursive)
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")
        if not self.object_height or not self.footprint_radius:
            self._measure_targets()

        position, direction = self._source_ray()
        if source_size is None:
            source_size = self._source_size()
        canvas = None
        if not refit and self.canvas is not None:
            canvas = self.current_model().rect(self.canvas)

        # The canvas is measured in the frame the EXPRESSION places the plane
        # in — the contact locator and the stamped constants — not one the
        # raster would re-derive from the meshes (a rotated target moves the
        # two apart).
        rgba, raster = ptk.ImgUtils.rasterize_shadow(
            meshes,
            position,
            self.ground_height,
            size=size,
            up=1,
            direction=direction,
            source_size=source_size,
            max_stretch=self._max_stretch(),
            canvas=canvas,
            contact=self._contact_point(),
            radius=self.footprint_radius,
            height=self.object_height,
            uniform_alpha=uniform_alpha,
            falloff_power=falloff_power,
            vertical_weight=vertical_weight,
            blur_amount=blur_amount,
        )
        Image.fromarray(rgba, "RGBA").save(self.texture_path)
        # A refresh that does not refit drew into the canvas the plane's
        # stamp denotes; keep that stamp rather than the raster's round trip
        # of it, which a collapsed canvas (a source below the head) cannot
        # re-derive.
        keep = self.canvas if (not refit and self.canvas is not None) else None
        self._stamp_canvas(keep or raster.fractions, source_size)
        self._stamp_bearing(self._current_bearing(), recursive)

        self.logger.info(
            f"Created silhouette texture: {self.texture_path} "
            f"(reach {raster.model.reach:.3f}, penumbra {raster.penumbra:.3f})"
        )
        return self.texture_path

    # ------------------------------------------------------------ material
    def create_material(
        self, shader_type="standard", stingray_opacity_mode="transparent"
    ):
        """Create material with the silhouette texture.

        Parameters:
            shader_type: ``"standard"`` (standardSurface — the default: VP2
                renders the silhouette AND the fade per pixel, the FBX carries
                the PNG as a real texture, Unity rewires it on import) or
                ``"stingray"`` (StingrayPBS; retired — its transparent graph
                shows the silhouette only through the colour map's alpha and
                cannot preview the fade at all; kept for one release).
            stingray_opacity_mode: When ``shader_type="stingray"``:
                ``"transparent"`` (alpha blend) or ``"masked"`` (alpha test).

        Material properties:
        - Base color: the silhouette file (black RGB; alpha = silhouette)
        - Opacity: file alpha x the plane's expression-driven ``opacity``
        - No specular / metalness (a shadow reflects nothing)
        """
        if not self.texture_path:
            raise ValueError("Texture not created yet")

        # Shared file/place2d setup
        file_node = cmds.shadingNode(
            "file", asTexture=True, name=f"{self.shadow_plane}_tex"
        )
        cmds.setAttr(f"{file_node}.fileTextureName", self.texture_path, type="string")
        place2d = cmds.shadingNode(
            "place2dTexture", asUtility=True, name=f"{self.shadow_plane}_place2d"
        )
        cmds.connectAttr(f"{place2d}.outUV", f"{file_node}.uv")
        cmds.connectAttr(f"{place2d}.outUvFilterSize", f"{file_node}.uvFilterSize")

        # file alpha (per pixel) x the plane's fade (the expression writes
        # plane.opacity) -> the shader's opacity.
        self.opacity_mult = cmds.shadingNode(
            "multiplyDivide",
            asUtility=True,
            name=f"{self.shadow_plane}_opacity_mult",
        )
        fade_plug = f"{self.shadow_plane}.{self.OPACITY_ATTR}"
        for chan in ("X", "Y", "Z"):
            cmds.connectAttr(
                f"{file_node}.outAlpha", f"{self.opacity_mult}.input1{chan}"
            )
            cmds.connectAttr(fade_plug, f"{self.opacity_mult}.input2{chan}")

        if shader_type == "stingray":
            self.logger.warning(
                "shader_type='stingray' is retired for the shadow rig: the "
                "transparent graph ignores its scalar opacity while the colour "
                "map's alpha is in use, so the fade never previews. Building it "
                "with the silhouette as the colour map; prefer 'standard'."
            )
            # Always load a graph — a bare StingrayPBS node exposes none of
            # the attrs (base_color/use_opacity_map/etc.).
            self.shader = MatUtils.create_stingray_shader(
                f"{self.shadow_plane}_mat",
                opacity_mode=stingray_opacity_mode,
            )
            cmds.setAttr(f"{self.shader}.base_color", 0, 0, 0, type="double3")
            if cmds.attributeQuery("metallic", node=self.shader, exists=True):
                cmds.setAttr(f"{self.shader}.metallic", 0)
            if cmds.attributeQuery("roughness", node=self.shader, exists=True):
                cmds.setAttr(f"{self.shader}.roughness", 1)
            # The only per-pixel opacity either graph has is the colour map's
            # alpha: ``use_opacity_map`` is a SOURCE SELECTOR (1 = colour
            # alpha) on both, so the silhouette reads through it on the
            # masked graph too — a per-child ``TEX_mask_map`` wire would
            # leave that sampler unbound and cut every fragment.
            cmds.connectAttr(
                f"{file_node}.outColor", f"{self.shader}.TEX_color_map", force=True
            )
            cmds.setAttr(f"{self.shader}.use_color_map", 1)
            cmds.setAttr(f"{self.shader}.use_opacity_map", 1)
            if cmds.attributeQuery("opacity", node=self.shader, exists=True):
                # Inert while the colour alpha is selected (measured), kept
                # so the fade multiplier stays wired the way the standard
                # path is.
                cmds.connectAttr(
                    f"{self.opacity_mult}.outputX",
                    f"{self.shader}.opacity",
                    force=True,
                )
        else:
            self.shader = cmds.shadingNode(
                "standardSurface", asShader=True, name=f"{self.shadow_plane}_mat"
            )
            # The silhouette IS the base colour (black RGB, alpha silhouette):
            # a real texture slot, so the FBX exporter writes it as a Texture
            # object (measured: 1 texture object, no stock IBL cubes) and the
            # scene sidecar's base-colour reader finds it by its slot.
            cmds.connectAttr(
                f"{file_node}.outColor", f"{self.shader}.baseColor", force=True
            )
            cmds.setAttr(f"{self.shader}.specular", 0)
            cmds.setAttr(f"{self.shader}.metalness", 0)
            cmds.setAttr(f"{self.shader}.specularRoughness", 1)
            cmds.connectAttr(f"{self.opacity_mult}.output", f"{self.shader}.opacity")
            self.logger.info("Created standardSurface material")

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{self.shader}_SG"
        )
        cmds.connectAttr(f"{self.shader}.outColor", f"{sg}.surfaceShader")
        cmds.sets(self.shadow_plane, fe=sg)

        return self.shader

    # ---------------------------------------------------------- expression
    def setup_expression(self):
        """(Re)create the expression that places the plane from the source
        and the contact — the projection model, evaluated per frame."""
        contact = self.contact_locator if self.contact_locator else self.targets[0]
        contact_dm = self._make_world_decompose(contact, "contact")
        if self.source_is_directional(self.light):
            stale = f"{self._name_base}_light_dm"
            if cmds.objExists(stale):
                cmds.delete(stale)
            light_vp = self._make_world_direction(self.light, "light")
            prologue = self._expr_directional(light_vp, contact_dm)
        else:
            stale = f"{self._name_base}_light_vp"
            if cmds.objExists(stale):
                cmds.delete(stale)
            light_dm = self._make_world_decompose(self.light, "light")
            prologue = self._expr_positional(light_dm, contact_dm)
        self._build_expression(prologue + self._expr_model() + self._expr_opacity())

    def _make_world_decompose(self, node, suffix):
        """A ``decomposeMatrix`` on ``node.worldMatrix`` — world position even
        when the node is parented/grouped (raw ``.translate`` is local)."""
        name = f"{self._name_base}_{suffix}_dm"
        if cmds.objExists(name):
            cmds.delete(name)
        dm = cmds.createNode("decomposeMatrix", name=name)
        cmds.connectAttr(f"{node}.worldMatrix[0]", f"{dm}.inputMatrix")
        return dm

    def _make_world_direction(self, node, suffix):
        """A ``vectorProduct`` giving *node*'s world -Z axis (a light's
        emission direction), normalized — the ray a directional source
        projects along."""
        name = f"{self._name_base}_{suffix}_vp"
        if cmds.objExists(name):
            cmds.delete(name)
        vp = cmds.createNode("vectorProduct", name=name)
        cmds.setAttr(f"{vp}.operation", 3)  # vector x matrix
        cmds.setAttr(f"{vp}.input1", 0, 0, -1, type="double3")
        cmds.setAttr(f"{vp}.normalizeOutput", 1)
        cmds.connectAttr(f"{node}.worldMatrix[0]", f"{vp}.matrix")
        return vp

    def _expr_contact(self, contact_dm):
        return f"""
// Contact (the footprint centre on the target's underside) and the constants
// stamped on the plane — pythontk ShadowProjection.model, evaluated live.
float $Cx = {contact_dm}.outputTranslateX;
float $Cy = {contact_dm}.outputTranslateY;
float $Cz = {contact_dm}.outputTranslateZ;
float $Gy = {self.shadow_plane}.groundHeight;
float $objH = {self.shadow_plane}.objectHeight;
float $r = {self.shadow_plane}.footprintRadius;
float $lim = {self.shadow_plane}.maxStretch;
float $size = {self.shadow_plane}.basePlaneSize;
"""

    def _expr_positional(self, light_dm, contact_dm):
        """Prologue for a positional source: its world position is the ray origin."""
        return (
            self._expr_contact(contact_dm)
            + f"""
// A positional source: the rays fan out from where it sits.
float $Lx = {light_dm}.outputTranslateX;
float $Ly = {light_dm}.outputTranslateY;
float $Lz = {light_dm}.outputTranslateZ;
"""
        )

    def _expr_directional(self, light_vp, contact_dm):
        """Prologue for a directional source: written as a point a long way
        back along its direction, so the one model body serves both."""
        return (
            self._expr_contact(contact_dm)
            + f"""
// A directional source (world -Z of the light): a point far back along the
// ray through the contact — parallel rays, the same body as a positional one.
float $far = {ptk.ShadowProjection.FAR_FACTOR:.1f} * max($objH, 2.0 * $r);
float $Lx = $Cx - {light_vp}.outputX * $far;
float $Ly = $Cy - {light_vp}.outputY * $far;
float $Lz = $Cz - {light_vp}.outputZ * $far;
"""
        )

    def _expr_model(self):
        """The model body: bearing, the base/top projection factors, the
        anchor, and the canvas placed from the stamped fractions (far edge
        on the projected head, the anchor at its stamped fraction)."""
        u0, u1, w0, w1 = self._CANVAS_ATTRS
        return f"""
// Bearing u: horizontal, away from the light (+Z when directly overhead);
// w across it (the plane's local +X).
float $dx = $Cx - $Lx;
float $dz = $Cz - $Lz;
float $dist = sqrt($dx * $dx + $dz * $dz);
float $ux = ($dist > 1e-6) ? $dx / $dist : 0.0;
float $uz = ($dist > 1e-6) ? $dz / $dist : 1.0;
float $wx = $uz;
float $wz = -$ux;

// Projection factors of the bounding cylinder's base and top disks,
// k = (L - G) / (L - disk height); the reach is capped at maxStretch heights.
float $kmax = 1.0 + $lim;
float $kb = clamp(0.0, $kmax, ($Ly - $Gy) / max(1e-4, $Ly - $Cy));
float $ktCap = min($kmax, $kb + $lim * $objH / max($dist, 1e-6));
float $kt = clamp(0.0, $ktCap, ($Ly - $Gy) / max(1e-4, $Ly - $Cy - $objH));
float $reach = max(0.0, $dist * ($kt - $kb));
float $base = $r * $kb;
float $top = $r * $kt;
float $len = $reach + $base + $top;
float $wid = 2.0 * $r * max($kt, $kb);

// Anchor: where the base centre lands on the ground (slides as the target rises).
float $Sx = $Lx + $dx * $kb;
float $Sz = $Lz + $dz * $kb;

// The canvas the PNG covers (stamped at raster time): its far edge in
// projected-head radii from where the head lands, its near edge as a
// fraction of that far edge — so the anchor keeps its place in the texture
// and a grounded target's feet stay under the silhouette's feet.
float $u0 = {self.shadow_plane}.{u0};
float $u1 = {self.shadow_plane}.{u1};
float $w0 = {self.shadow_plane}.{w0};
float $w1 = {self.shadow_plane}.{w1};
float $uHi = max(0.0, $reach + $u1 * $top);
float $uLo = $u0 * $uHi;
float $cu = 0.5 * ($uLo + $uHi);
float $cw = 0.5 * ($w0 + $w1) * $wid;

{self.shadow_plane}.translateX = $Sx + $ux * $cu + $wx * $cw;
{self.shadow_plane}.translateZ = $Sz + $uz * $cu + $wz * $cw;
{self.shadow_plane}.translateY = $Gy + {self.GROUND_OFFSET};
{self.shadow_plane}.rotateY = rad_to_deg(atan2($ux, $uz));
{self.shadow_plane}.scaleZ = max(1e-4, ($uHi - $uLo) / $size);
{self.shadow_plane}.scaleX = max(1e-4, ($w1 - $w0) * $wid / $size);
"""

    def _expr_opacity(self):
        """Expression epilogue: opacity = elongation falloff x light-height
        fade x rise fade (target leaving the ground), written to the plane's
        keyable ``opacity`` — the channel the material multiplies the file
        alpha by, the bake keys, and the engines read."""
        return f"""
// Opacity: a shadow lightens as it elongates past its footprint (more
// penumbra, more fill light), vanishes when the source drops below the
// target, and fades as the target rises off the ground.
float $intensity = {self.shadow_plane}.shadowIntensity;
float $power = {self.shadow_plane}.falloffPower;
float $fadeH = {self.shadow_plane}.fadeHeight;

float $stretch = max(1.0, $len / max(1e-4, 2.0 * $r));
float $distOpacity = $intensity / max(0.001, pow($stretch, $power));
float $heightFade = clamp(0.0, 1.0, $Ly - $Cy);
float $riseFade = clamp(0.0, 1.0, 1.0 - max(0.0, $Cy - $Gy) / max(0.001, $fadeH));

{self.shadow_plane}.{self.OPACITY_ATTR} = clamp(0.0, 1.0, $distOpacity * $heightFade * $riseFade);
"""

    def _build_expression(self, body):
        """(Re)create the plane's expression node from the mode body."""
        expr_name = f"{self.shadow_plane}_expr"
        if cmds.objExists(expr_name):
            cmds.delete(expr_name)
        cmds.expression(name=expr_name, string=body, alwaysEvaluate=True)

    # ------------------------------------------------------------------ bake
    def bake(self, start=None, end=None):
        """Bake this rig's driven channels to keyframes and remove the live
        expression (FBX-ready). See :meth:`bake_planes`."""
        return self.bake_planes([self.shadow_plane], start=start, end=end)

    @staticmethod
    def _live_rig_nodes(plane):
        """``(expressions, driver nodes)`` currently driving ``plane`` — the
        decomposeMatrix / vectorProduct nodes feeding its expression.

        Resolved via connections, not by name — robust to path-qualified
        plane names and suffixed expr nodes. Both sets are empty once baked.
        """
        exprs = set(
            cmds.listConnections(
                plane, source=True, destination=False, type="expression"
            )
            or []
        )
        drivers = set()
        for expr in exprs:
            for kind in ("decomposeMatrix", "vectorProduct"):
                drivers.update(
                    cmds.listConnections(
                        expr, source=True, destination=False, type=kind
                    )
                    or []
                )
        return exprs, drivers

    @classmethod
    def plane_is_live(cls, plane):
        """Does *plane* still carry its expression (not baked)?"""
        return bool(cls._live_rig_nodes(plane)[0])

    @classmethod
    def plane_is_baked(cls, plane):
        """Does *plane* carry baked keys on its driven channels?"""
        return any(
            cmds.listConnections(
                f"{plane}.{ch}", source=True, destination=False, type="animCurve"
            )
            for ch in cls.BAKE_CHANNELS
            if cmds.attributeQuery(ch, node=plane, exists=True)
        )

    @staticmethod
    def _unroll_rotation(plane):
        """Euler-filter the baked ``rotateY`` so a light crossing behind the
        target leaves a continuous curve, not a -180 -> +180 jump that key
        optimisation would smear into a spin (``atan2`` wraps there)."""
        plug = f"{plane}.rotateY"
        if not cmds.listConnections(
            plug, source=True, destination=False, type="animCurve"
        ):
            return
        try:
            cmds.filterCurve(plug, filter="euler")
        except RuntimeError:
            pass  # a flat curve has nothing to unroll

    @classmethod
    def _mirror_fade_to_visibility(cls, plane):
        """Sparse visibility keys around the baked fade's zero runs — the pairs
        Unity's opacity importer rebuilds a fade from, and the keyed
        visibility the GLB route needs before it will carry the ramp."""
        if not cmds.attributeQuery(cls.OPACITY_ATTR, node=plane, exists=True):
            return
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        OpacityAttributeMode.sync_visibility_from_opacity([plane], windows=True)

    # ------------------------------------------------------------------ export metadata
    @staticmethod
    def _plane_shading_groups(plane):
        """The shading groups of the plane's REAL material.

        The live membership, unless a horizon preview stands in for it
        (:class:`~mayatk.rig_utils.shadow_preview.ShadowPreview` swaps only
        the membership and snapshots the real one) -- then the snapshot's.
        Every accessor that walks the network goes through here, so a plane
        wearing a preview still reports its silhouette, its opacity chain and
        its shader type: without this the export record published
        ``"texture": ""`` and the R6 fallback silently vanished.
        """
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        snapshot = ShadowPreview.restore_snapshot(plane)
        if snapshot:
            return [sg for sg in snapshot if cmds.objExists(sg)]
        groups = []
        for shape in cmds.listRelatives(plane, shapes=True, fullPath=True) or []:
            for sg in cmds.listConnections(shape, type="shadingEngine") or []:
                if sg not in groups:
                    groups.append(sg)
        return groups

    @classmethod
    def _plane_texture_node(cls, plane):
        """The file node holding the plane's silhouette (SSoT; survives
        retexturing).

        Walks the opacity plugs first (standardSurface ``.opacity`` via the
        ``*_opacity_mult`` multiplyDivide, or the retired stingray
        ``.opacity``), then the colour slots (``baseColor`` /
        ``TEX_color_map``) that carry the same file — never the whole
        network: ``listHistory`` doesn't traverse a ShaderFX (StingrayPBS)
        node's inputs, and its loaded graph carries three stock IBL preset
        file nodes a material-wide search would wrongly match.
        """
        for sg in cls._plane_shading_groups(plane):
            shaders = cmds.listConnections(f"{sg}.surfaceShader", source=True) or []
            for shader in shaders:
                queue = [
                    src
                    for attr in ("opacity", "baseColor", "TEX_color_map")
                    if cmds.attributeQuery(attr, node=shader, exists=True)
                    for src in (
                        cmds.listConnections(
                            f"{shader}.{attr}", source=True, destination=False
                        )
                        or []
                    )
                ]
                seen = set()
                while queue:
                    node = queue.pop(0)
                    if node in seen:
                        continue
                    seen.add(node)
                    if cmds.nodeType(node) == "file":
                        return node
                    # The fade attr feeds the multiplier from the plane's
                    # transform: never walk back through the plane itself.
                    if cmds.nodeType(node) == "transform":
                        continue
                    queue += (
                        cmds.listConnections(node, source=True, destination=False) or []
                    )
        return None

    @classmethod
    def _plane_texture_path(cls, plane):
        """Full path of the plane's OWN silhouette PNG (see
        :meth:`_plane_texture_node`). A packed plane's file node names the
        atlas; its tile stays beside it under the stamped ``silhouetteTexture``
        name, which is what Recalculate rewrites and the record carries."""
        node = cls._plane_texture_node(plane)
        bound = (cmds.getAttr(f"{node}.fileTextureName") if node else None) or None
        own = cls._plane_attr(plane, cls._SILHOUETTE_ATTR, "")
        if own and cls.plane_is_atlased(plane):
            folder = (
                os.path.dirname(bound)
                if bound
                else os.path.join(cmds.workspace(q=True, rd=True), "sourceimages")
            )
            return os.path.join(folder, own).replace("\\", "/")
        return bound

    @classmethod
    def _plane_shading(cls, plane):
        """``(shader, opacity multiplyDivide)`` of the plane's REAL material
        (see :meth:`_plane_shading_groups`), or Nones."""
        for sg in cls._plane_shading_groups(plane):
            shaders = cmds.listConnections(f"{sg}.surfaceShader", source=True) or []
            for shader in shaders:
                mult = None
                if cmds.attributeQuery("opacity", node=shader, exists=True):
                    sources = (
                        cmds.listConnections(
                            f"{shader}.opacity", source=True, destination=False
                        )
                        or []
                    )
                    mult = next(
                        (n for n in sources if cmds.nodeType(n) == "multiplyDivide"),
                        None,
                    )
                return shader, mult
        return None, None

    @classmethod
    def _rig_links(cls, plane):
        """``(targets, source)`` the plane was built from, via the stamped
        message links; ``([], None)`` for rigs built before the stamps."""
        targets = []
        if cmds.attributeQuery(cls._TARGETS_ATTR, node=plane, exists=True):
            targets = (
                cmds.listConnections(
                    f"{plane}.{cls._TARGETS_ATTR}", source=True, destination=False
                )
                or []
            )
            targets = cmds.ls(targets, long=True) or []
        source = None
        if cmds.attributeQuery(cls._SOURCE_ATTR, node=plane, exists=True):
            found = (
                cmds.listConnections(
                    f"{plane}.{cls._SOURCE_ATTR}", source=True, destination=False
                )
                or []
            )
            source = (cmds.ls(found, long=True) or [None])[0]
        return targets, source

    def _stamp_rig_links(self):
        """Link the plane to its targets and source by message — the handles
        :meth:`refresh_silhouette` and the stale-bearing check need after the
        Python instance is gone (and, once baked, after the decomposeMatrix
        connections are). Message links survive renames and reparenting."""
        plane = self.shadow_plane
        if not cmds.attributeQuery(self._TARGETS_ATTR, node=plane, exists=True):
            cmds.addAttr(
                plane,
                ln=self._TARGETS_ATTR,
                at="message",
                multi=True,
                indexMatters=False,
            )
        if not cmds.attributeQuery(self._SOURCE_ATTR, node=plane, exists=True):
            cmds.addAttr(plane, ln=self._SOURCE_ATTR, at="message")
        linked = set(
            cmds.ls(
                cmds.listConnections(
                    f"{plane}.{self._TARGETS_ATTR}", source=True, destination=False
                )
                or [],
                long=True,
            )
            or []
        )
        for target in self.targets:
            if not cmds.objExists(target):
                continue
            if (cmds.ls(target, long=True) or [target])[0] in linked:
                continue
            cmds.connectAttr(
                f"{target}.message",
                f"{plane}.{self._TARGETS_ATTR}",
                nextAvailable=True,
                force=True,
            )
        if self.light and cmds.objExists(self.light):
            cmds.connectAttr(
                f"{self.light}.message", f"{plane}.{self._SOURCE_ATTR}", force=True
            )

    # ------------------------------------------------------------ re-attach
    @classmethod
    def from_plane(cls, plane):
        """A rig instance re-attached to an existing *plane* via its stamps,
        or None when the plane predates the target/source links (nothing to
        recompute from).

        Resolves what the panel's Utility actions need: the targets and the
        source, the contact locator and group, the shading nodes, the texture
        path, and the measured constants / canvas fractions the expression
        reads — so :meth:`set_source`, :meth:`setup_expression`,
        :meth:`refresh_silhouette` and :meth:`rebuild` work on a rig built in
        an earlier session, baked or live.
        """
        targets, source = cls._rig_links(plane)
        if not targets or not source:
            return None
        leaf = CoreUtils.leaf_name(plane)
        base = leaf[: -len("_shadow")] if leaf.endswith("_shadow") else leaf
        rig = cls(targets, light=source, name_base=base)
        rig.shadow_plane = plane
        for attr, field in (
            ("groundHeight", "ground_height"),
            ("objectHeight", "object_height"),
            ("footprintRadius", "footprint_radius"),
            ("basePlaneSize", "plane_size"),
        ):
            if cmds.attributeQuery(attr, node=plane, exists=True):
                setattr(rig, field, cmds.getAttr(f"{plane}.{attr}"))
        if not rig.footprint_radius:
            rig._measure_targets()  # a plane stamped before the model attrs
        if all(
            cmds.attributeQuery(a, node=plane, exists=True) for a in cls._CANVAS_ATTRS
        ):
            rig.canvas = tuple(cmds.getAttr(f"{plane}.{a}") for a in cls._CANVAS_ATTRS)
        parent = cmds.listRelatives(plane, parent=True, fullPath=True)
        if parent and CoreUtils.leaf_name(parent[0]).endswith("_shadow_grp"):
            rig.group = parent[0]
        rig.contact_locator = cls._plane_contact(plane)
        rig.shader, rig.opacity_mult = cls._plane_shading(plane)
        rig.texture_path = cls._plane_texture_path(plane)
        rig.rig_type = cls.plane_type(plane)
        rig.horizon_path = cls._plane_horizon_path(plane)
        return rig

    # Retained for one release: the pre-public spelling.
    _from_plane = from_plane

    @classmethod
    def planes_for_nodes(cls, nodes):
        """Shadow planes the given nodes touch: the planes themselves (or
        their ``*_shadow_grp``), plus every plane whose stamped links lead
        back to a node — a target, the source (its transform or light shape),
        the contact locator, a shading node."""
        planes = cls.find_shadow_planes(nodes)
        seen = set(cmds.ls(planes, long=True) or [])
        link_attrs = (cls._TARGETS_ATTR, cls._SOURCE_ATTR, cls._MEMBER_ATTR)
        pool = []
        for node in cmds.ls([str(n) for n in nodes or []], long=True) or []:
            pool.append(node)
            if cmds.ls(node, shapes=True):
                pool += cmds.listRelatives(node, parent=True, fullPath=True) or []
        for node in dict.fromkeys(pool):
            plugs = (
                cmds.listConnections(
                    f"{node}.message", source=False, destination=True, plugs=True
                )
                or []
            )
            for plug in plugs:
                owner, _, attr = plug.partition(".")
                if attr.split("[")[0] not in link_attrs:
                    continue
                long = (cmds.ls(owner, long=True) or [owner])[0]
                if long in seen:
                    continue
                if cmds.attributeQuery("basePlaneSize", node=long, exists=True):
                    seen.add(long)
                    planes.append(owner)
        return planes

    @classmethod
    def for_node(cls, node):
        """The rig *node* belongs to (a plane, its group, a target, the
        source, a support node), re-attached via :meth:`from_plane`; None
        when it belongs to none (or the rig predates the stamps)."""
        for plane in cls.planes_for_nodes([node]):
            rig = cls.from_plane(plane)
            if rig is not None:
                return rig
        return None

    @classmethod
    def for_nodes(cls, nodes):
        """Distinct rigs the *nodes* touch (see :meth:`for_node`)."""
        rigs = []
        for plane in cls.planes_for_nodes(nodes):
            rig = cls.from_plane(plane)
            if rig is not None:
                rigs.append(rig)
        return rigs

    def _relink_drivers(self, build):
        """Run *build* (which creates driver/expression nodes) and stamp what
        it created onto the teardown manifest."""
        pre = set(cmds.ls(long=True))
        build()
        self._link_members(set(cmds.ls(long=True)) - pre)

    def set_source(self, source_name, position=(5, 10, 5), size=None):
        """Re-point this rig at another source — an existing transform or
        light, or a locator to create at *position* — and re-render its
        silhouette from there. A baked plane has its expression restored
        first (its keys described the old source).

        Returns:
            The source transform now linked.
        """
        self.light = self.ensure_source(source_name, position)
        if self.shadow_plane and self.plane_is_baked(self.shadow_plane):
            self._clear_baked_keys(self.shadow_plane)
        self._stamp_rig_links()
        self._relink_drivers(self.setup_expression)
        self.refresh_silhouette([self.shadow_plane], size=size, refit=True)
        self._watch_nodes([self.light])
        return self.light

    @classmethod
    def _clear_baked_keys(cls, plane):
        """Delete the baked keys on the driven channels (and the visibility
        mirror) so an expression can drive the plane again."""
        for ch in cls.BAKE_CHANNELS + ("visibility",):
            if not cmds.attributeQuery(ch, node=plane, exists=True):
                continue
            curves = cmds.listConnections(
                f"{plane}.{ch}", source=True, destination=False, type="animCurve"
            )
            if curves:
                cmds.delete(curves)
        if cmds.attributeQuery("visibility", node=plane, exists=True):
            cmds.setAttr(f"{plane}.visibility", True)

    @classmethod
    def unbake_planes(cls, planes=None):
        """Restore the live expression on baked shadow planes — the reverse
        of :meth:`bake_planes`, so a rig exported earlier can be edited again
        (move the source, change the ground, re-bake). Works off the stamped
        links; a plane that predates them is skipped with a warning.

        Args:
            planes: Shadow plane transform(s); None restores every baked
                plane in the scene.

        Returns:
            The list of planes whose expression was restored.
        """
        restored = []
        for plane in cls.find_shadow_planes(planes):
            if cls.plane_is_live(plane):
                continue
            rig = cls.from_plane(plane)
            if rig is None:
                cls.logger.warning(
                    f"{plane}: built before the target/source stamps; re-create "
                    "the rig to restore its expression."
                )
                continue
            if not (rig.light and cmds.objExists(rig.light)):
                cls.logger.warning(f"{plane}: its source is gone; set a new one first.")
                continue
            cls._clear_baked_keys(plane)
            rig._relink_drivers(rig.setup_expression)
            restored.append(plane)
        if restored:
            cls.refresh_export_metadata()
        return restored

    @classmethod
    def rebuild(cls, plane, texture_res=None, recursive=None, shader_type=None):
        """Tear a rig down and build it again from its own stamps — the same
        targets, source and ground — with the target's CURRENT geometry and,
        optionally, a new resolution / descendant rule / shader. The name
        base is kept, so the engine join key (``<name>_shadow``) survives.

        Returns:
            The new :class:`ShadowRig`, or None when *plane* predates the
            stamps or its targets / source are gone.
        """
        rig = cls.from_plane(plane)
        if rig is None:
            cls.logger.warning(
                f"{plane}: built before the target/source stamps; cannot rebuild."
            )
            return None
        if recursive is None:
            recursive = True
            if cmds.attributeQuery(cls._RECURSIVE_ATTR, node=plane, exists=True):
                recursive = bool(cmds.getAttr(f"{plane}.{cls._RECURSIVE_ATTR}"))
        if texture_res is None:
            texture_res = cls._texture_size(rig.texture_path) or 512
        if shader_type is None:
            shader_type = (
                "stingray"
                if rig.shader and cmds.nodeType(rig.shader) == "StingrayPBS"
                else "standard"
            )
        targets = [t for t in rig.targets if cmds.objExists(t)]
        source = rig.light
        if not targets or not (source and cmds.objExists(source)):
            cls.logger.warning(
                f"{plane}: its targets or source are gone; cannot rebuild."
            )
            return None
        ground = rig.ground_height
        rig_type = rig.rig_type
        horizon = cls._horizon_params(plane)
        atlased = cls.plane_is_atlased(plane)
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        previewed = ShadowPreview.is_attached(plane)
        cls.delete_rigs([plane])
        rebuilt = cls.create(
            targets,
            texture_res=texture_res,
            source_name=source,
            recursive=recursive,
            ground_height=ground,
            shader_type=shader_type,
            rig_type=rig_type,
            horizon_size=horizon.get("size"),
            horizon_spans=horizon.get("spans"),
        )
        if atlased:
            # The rig left its atlas on delete; a rebuilt rig rejoins it.
            cls.pack_atlas([rebuilt.shadow_plane])
        if previewed and rebuilt.rig_type == "horizon":
            # Display state follows the rig: the delete stood the preview
            # down, the rebuilt plane gets it back.
            cls._reattach_preview(rebuilt.shadow_plane)
        return rebuilt

    @staticmethod
    def _texture_size(path):
        """Pixel width of the PNG at *path*, or None."""
        if not path or not os.path.exists(path):
            return None
        try:
            from PIL import Image

            with Image.open(path) as img:
                return img.size[0]
        except OSError:
            return None

    @classmethod
    def _stamped_bearing(cls, plane):
        """The unit direction stamped at raster time, or None for a pre-stamp rig."""
        if not all(
            cmds.attributeQuery(a, node=plane, exists=True) for a in cls._BEARING_ATTRS
        ):
            return None
        return tuple(cmds.getAttr(f"{plane}.{a}") for a in cls._BEARING_ATTRS)

    @classmethod
    def silhouette_is_stale(cls, plane, *, degrees=None, distance=None):
        """Has the source moved past *degrees* (default
        :attr:`_STALE_BEARING_DEG`) from the direction the plane's silhouette
        was rasterized from -- a target turned or carried under the source
        counts the same, the direction being measured in the contact's frame
        -- or, with *distance*, in or out by more than that fraction of the
        distance it was drawn at (a positional source only)? False when
        unknowable (a rig built before the stamps, or a missing source)."""
        rig = cls.from_plane(plane)
        stamped = cls._stamped_bearing(plane)
        if rig is None or stamped is None:
            return False
        if not (rig.light and cmds.objExists(rig.light)):
            return False
        s = np.array(stamped, dtype=float)
        norm = float(np.linalg.norm(s))
        if norm < 1e-6:
            return True  # never rasterized against a source
        current = np.array(rig._current_bearing(), dtype=float)
        dot = float(np.clip(np.dot(current, s) / norm, -1.0, 1.0))
        limit = cls._STALE_BEARING_DEG if degrees is None else float(degrees)
        if math.degrees(math.acos(dot)) > limit:
            return True
        if distance is None:
            return False
        was = float(cls._plane_attr(plane, cls._DISTANCE_ATTR, 0.0) or 0.0)
        if was <= 1e-9:
            return False  # directional, or stamped before the distance was
        return abs(rig._source_distance() - was) / was > float(distance)

    # ------------------------------------------------------- follow source
    @classmethod
    def auto_recalculate(cls, on=True):
        """Follow Source: re-render a silhouette as soon as its source -- or
        its target -- has moved past :attr:`AUTO_RECALCULATE_DEG` /
        :attr:`AUTO_RECALCULATE_DISTANCE` (the panel's Follow Source box).

        The expression already re-places the plane live; what goes stale is
        the drawn shape, which is one direction's projection of the target,
        so either end moving counts. An attribute-changed callback on every
        rig's source and targets (and their ancestors: a light under a
        moving group moves too) queues ONE deferred pass per idle, which
        Recalculates the stale planes -- 0.1 s at 512 px, so a dragged light
        or prop re-projects as the drag settles. Scene-wide and scene-open
        aware; :meth:`create` and :meth:`set_source` watch their nodes. Off
        unsubscribes everything.
        """
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        mgr.unsubscribe_all(cls._AUTO_OWNER)
        mgr.unsubscribe_all(cls._AUTO_NODES_OWNER)
        cls._auto_watched = set()
        cls._auto_on = bool(on)
        if not cls._auto_on:
            return
        for event in ("SceneOpened", "NewSceneOpened"):
            mgr.subscribe(event, cls._rearm_auto_recalculate, owner=cls._AUTO_OWNER)
        cls._rearm_auto_recalculate()

    @classmethod
    def auto_recalculate_enabled(cls):
        """Is Follow Source on?"""
        return bool(cls._auto_on)

    @classmethod
    def _rearm_auto_recalculate(cls):
        """Watch the source and the targets of every rig in the scene (a
        scene just opened brings its own; a new one none)."""
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(cls._AUTO_NODES_OWNER)
        cls._auto_watched = set()
        if not cls._auto_on:
            return
        for plane in cls.find_shadow_planes():
            targets, source = cls._rig_links(plane)
            cls._watch_nodes([source, *targets])

    @classmethod
    def _watch_nodes(cls, nodes):
        """An attribute-changed callback on each of *nodes* -- a rig's
        source and its targets: either end of the projection moving goes
        stale -- and on their ancestor transforms (a light under a moving
        group moves too), once per node, while Follow Source is on."""
        if not cls._auto_on:
            return
        import maya.api.OpenMaya as om
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for node in nodes:
            found = cmds.ls(node, long=True) if node else []
            if not found:
                continue
            path = found[0]
            chain = [path]
            while path.count("|") > 1:
                path = path.rsplit("|", 1)[0]
                chain.append(path)
            for name in chain:
                if name in cls._auto_watched:
                    continue
                sel = om.MSelectionList()
                sel.add(name)
                token = mgr.add_om_callback(
                    om.MNodeMessage.addAttributeChangedCallback,
                    sel.getDependNode(0),
                    cls._on_node_changed,
                    owner=cls._AUTO_NODES_OWNER,
                )
                if token is not None:
                    cls._auto_watched.add(name)

    @classmethod
    def _on_node_changed(cls, msg, plug, other_plug, client_data):
        """The callback: an attribute SET on a watched node (a source, a
        target, or an ancestor of either) queues one deferred
        :meth:`recalculate_stale` (the check itself decides whether
        anything moved far enough)."""
        import maya.api.OpenMaya as om

        if not (msg & om.MNodeMessage.kAttributeSet):
            return
        if cls._auto_pending or not cls._auto_on:
            return
        cls._auto_pending = True
        # Plain idle priority: the pending flag already folds a drag's sets
        # into one pass, and a lowest-priority idle can starve while a
        # command port or a scrub keeps Maya busy (measured: never ran).
        cmds.evalDeferred(cls._auto_fire)

    @classmethod
    def _auto_fire(cls):
        cls._auto_pending = False
        if cls._auto_on:
            cls.recalculate_stale()

    @classmethod
    def recalculate_stale(cls, planes=None):
        """Recalculate the silhouettes (of *planes*, default all) whose
        source or target moved past the Follow Source thresholds; the rest
        are left alone. Returns the planes re-rendered."""
        stale = [
            p
            for p in cls.find_shadow_planes(planes)
            if cls.silhouette_is_stale(
                p,
                degrees=cls.AUTO_RECALCULATE_DEG,
                distance=cls.AUTO_RECALCULATE_DISTANCE,
            )
        ]
        if not stale:
            return []
        cmds.undoInfo(openChunk=True, chunkName="Shadow Rig: Follow Source")
        try:
            return cls.refresh_silhouette(stale)
        finally:
            cmds.undoInfo(closeChunk=True)

    @classmethod
    def _reattach_preview(cls, plane):
        """Put a standing horizon preview back on *plane* after its map or
        the plane itself was rebuilt. A refusal (no viewport here, or the
        map gone) is a warning, never an error: the rig's own operation
        already succeeded and display state must not undo it."""
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        try:
            ShadowPreview.attach(plane)
        except ValueError as error:
            cls.logger.warning(
                f"{plane}: the horizon preview was not restored: {error}"
            )

    @classmethod
    def refresh_silhouette(cls, planes=None, size=None, refit=None):
        """Re-rasterize shadow planes' silhouettes from their source's CURRENT
        position, overwriting each plane's PNG in place.

        The Recalculate action: a source that was moved after the build (or
        one created at the default position and positioned later) leaves the
        silhouette rendered from a position that no longer holds. Works off
        the stamped target/source links, so it needs no Python instance.

        Args:
            planes: Shadow plane transform(s); None refreshes every plane in
                the scene that carries the stamps.
            size: Texture resolution; None keeps each plane's current size.
            refit: Fit the canvas to the new projection and restamp the plane
                (its expression re-places it). None = yes for a live rig, no
                for a baked one — its keys already place the plane, so the
                PNG is drawn into the canvas those keys describe.

        Returns:
            The list of planes whose silhouette was rewritten.
        """
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        refreshed = []
        for plane in cls.find_shadow_planes(planes):
            rig = cls.from_plane(plane)
            if rig is None:
                cls.logger.warning(
                    f"{plane}: built before the target/source stamps; re-create "
                    "the rig to recalculate its silhouette."
                )
                continue
            node = cls._plane_texture_node(plane)
            # The plane's OWN tile, never the atlas its file node names while
            # packed: the raster overwrites this file and the tile is then
            # copied into the atlas in place.
            path = rig.texture_path
            res = size or cls._texture_size(path) or 512
            recursive = True
            if cmds.attributeQuery(cls._RECURSIVE_ATTR, node=plane, exists=True):
                recursive = bool(cmds.getAttr(f"{plane}.{cls._RECURSIVE_ATTR}"))
            fit = cls.plane_is_live(plane) if refit is None else bool(refit)
            rig.create_silhouette_texture(
                size=res, recursive=recursive, path=path, refit=fit
            )
            bound = rig.texture_path
            if cls.plane_is_atlased(plane):
                # The tile is rewritten in place; the plane keeps sampling
                # the atlas, so nothing else repacks.
                bound = cls._write_atlas_tile(plane, "projected")
            if rig.rig_type == "horizon":
                # The map depends on the geometry, not the source: re-bake only
                # when the target changed since the last bake.
                params = cls._horizon_params(plane)
                rig.bake_horizon(
                    size=params.get("size"),
                    spans=params.get("spans"),
                    only_if_changed=True,
                )
                if cls._plane_attr(plane, cls._HORIZON_ATLAS_ATTR, ""):
                    cls._write_atlas_tile(plane, "horizon")
                if ShadowPreview.is_attached(plane):
                    # The preview samples the map through its own texture
                    # node: rebind it (attach is idempotent) or it keeps
                    # showing the map the re-bake just replaced.
                    cls._reattach_preview(plane)
            if node and bound:
                # VP2 caches by path and ignores a set to the same value:
                # clear, then set, so the rewritten file is actually reloaded.
                cmds.setAttr(f"{node}.fileTextureName", "", type="string")
                cmds.setAttr(f"{node}.fileTextureName", bound, type="string")
            refreshed.append(plane)
        if refreshed:
            cls.refresh_export_metadata()
        return refreshed

    @classmethod
    def refresh_export_metadata(cls):
        """Republish the ``shadow_metadata`` channel on the ``data_export``
        carrier from the scene's shadow planes.

        The canonical, no-arg pre-export refresh for the shadow rig — wired
        into ``FbxUtils._KNOWN_PRODUCERS`` so the Scene Exporter (and any
        ``run_export_preparers`` caller) ships a current channel. The payload
        joins Unity-side by GameObject name (unitytk's
        ``ShadowPlaneController.cs``):

        ``{"version": 1, "planes": [{"name", "texture", "intensity"}]}``

        Clears the channel when the scene has no shadow planes (no empty
        carrier left behind). Warns about planes whose silhouette was
        rasterized from a bearing the source has since left (Recalculate
        fixes it; nothing is rewritten here — an export must not write the
        project's textures behind the user).

        Returns:
            The published JSON string, or None when cleared.
        """
        import json

        from mayatk.node_utils.data_nodes import DataNodes

        planes = cls.find_shadow_planes()
        if not planes:
            DataNodes.set_export_string(cls.SHADOW_METADATA, "")
            return None
        records = []
        stale = []
        for plane in planes:
            records.append(cls.export_record(plane))
            if cls.silhouette_is_stale(plane):
                stale.append(CoreUtils.leaf_name(plane))
        if stale:
            cls.logger.warning(
                "Shadow silhouette rasterized from a bearing the source has since "
                f"left: {', '.join(stale)}. Press Recalculate Silhouette (or "
                "ShadowRig.refresh_silhouette) before exporting."
            )
        payload = json.dumps(
            {
                "version": cls.METADATA_VERSION,
                "unit_scale": cls.unit_scale(),
                "planes": records,
            }
        )
        DataNodes.set_export_string(cls.SHADOW_METADATA, payload)
        return payload

    @staticmethod
    def unit_scale():
        """Metres per scene linear unit (the record's ``unit_scale``): an
        engine that imported in metres multiplies the record's lengths."""
        unit = cmds.currentUnit(query=True, linear=True)
        return {
            "mm": 0.001,
            "cm": 0.01,
            "m": 1.0,
            "km": 1000.0,
            "in": 0.0254,
            "ft": 0.3048,
            "yd": 0.9144,
            "mi": 1609.344,
        }.get(unit, 1.0)

    @classmethod
    def _plane_attr(cls, plane, name, default=None):
        """A stamped attr's value, or *default* when the plane lacks it."""
        if cmds.attributeQuery(name, node=plane, exists=True):
            return cmds.getAttr(f"{plane}.{name}")
        return default

    @classmethod
    def export_record(cls, plane):
        """One plane's ``shadow_metadata`` v2 record (the engine contract in
        ``mayatk/docs/shadow_rig_morphing.md``): the join key, the type, the
        textures, the source and contact nodes the engine reads at runtime,
        the projection model's inputs, and the atlas / horizon blocks when
        the rig carries them. Works off the stamps, so it needs no Python
        instance and survives a rig built in an earlier session."""
        attr = cls._plane_attr
        tex = cls._plane_texture_path(plane)
        _, source = cls._rig_links(plane)
        contact = cls._plane_contact(plane)
        directional = bool(source) and cls.source_is_directional(source)
        size = float(attr(plane, "sourceSize", 0.0) or 0.0)
        record = {
            "name": CoreUtils.leaf_name(plane),
            "type": cls.plane_type(plane),
            "texture": os.path.basename(tex) if tex else "",
            "intensity": round(float(attr(plane, "shadowIntensity", 1.0)), 4),
            "source": CoreUtils.leaf_name(source) if source else "",
            "source_type": "directional" if directional else "point",
            "source_size": 0.0 if directional else round(size, 6),
            "source_angle": round(size, 6) if directional else 0.0,
            "follow_source": bool(attr(plane, cls.FOLLOW_ATTR, True)),
            "contact": CoreUtils.leaf_name(contact) if contact else "",
            "ground": round(float(attr(plane, "groundHeight", 0.0)), 6),
            "radius": round(float(attr(plane, "footprintRadius", 0.0)), 6),
            "height": round(float(attr(plane, "objectHeight", 0.0)), 6),
            "max_stretch": round(
                float(
                    attr(plane, "maxStretch", ptk.ShadowProjection.DEFAULT_MAX_STRETCH)
                ),
                6,
            ),
            "canvas": [
                round(float(attr(plane, a, d)), 6)
                for a, d in zip(cls._CANVAS_ATTRS, (-1.0, 1.0, -0.5, 0.5))
            ],
        }
        atlas = attr(plane, cls._ATLAS_TEX_ATTR, "")
        if atlas:
            record["atlas"] = {
                "texture": atlas,
                "rect": cls._read_rect(plane, cls._ATLAS_RECT_ATTRS),
            }
        horizon = cls._horizon_params(plane)
        if horizon:
            record["horizon"] = horizon
        return record

    # ---------------------------------------------------------------- rig type
    @classmethod
    def plane_type(cls, plane):
        """The rig type stamped on *plane* (``projected`` for a rig built
        before the stamp existed)."""
        value = cls._plane_attr(plane, cls._TYPE_ATTR, "") or cls.RIG_TYPES[0]
        return value if value in cls.RIG_TYPES else cls.RIG_TYPES[0]

    @classmethod
    def _plane_contact(cls, plane):
        """The rig's contact locator — from the members manifest (rename-
        proof) or, for a rig stamped before it, by name."""
        if cmds.attributeQuery(cls._MEMBER_ATTR, node=plane, exists=True):
            for node in (
                cmds.listConnections(
                    f"{plane}.{cls._MEMBER_ATTR}", source=True, destination=False
                )
                or []
            ):
                if cmds.nodeType(node) == "transform" and CoreUtils.leaf_name(
                    node
                ).endswith("_contact_loc"):
                    return (cmds.ls(node, long=True) or [node])[0]
        leaf = CoreUtils.leaf_name(plane)
        base = leaf[: -len("_shadow")] if leaf.endswith("_shadow") else leaf
        candidate = f"{base}_contact_loc"
        if cmds.objExists(candidate):
            return (cmds.ls(candidate, long=True) or [candidate])[0]
        return None

    @classmethod
    def _ensure_string_attr(cls, plane, name, value=None):
        if not cmds.attributeQuery(name, node=plane, exists=True):
            cmds.addAttr(plane, ln=name, dt="string")
        if value is not None:
            cmds.setAttr(f"{plane}.{name}", str(value), type="string")

    @classmethod
    def _ensure_int_attr(cls, plane, name, value):
        if not cmds.attributeQuery(name, node=plane, exists=True):
            cmds.addAttr(plane, ln=name, at="long", dv=int(value))
        cmds.setAttr(f"{plane}.{name}", int(value))

    @classmethod
    def _read_rect(cls, plane, attrs):
        """A stamped ``(scaleX, scaleY, offsetX, offsetY)`` rect, identity
        when unstamped."""
        return [
            round(float(cls._plane_attr(plane, a, d)), 6)
            for a, d in zip(attrs, (1.0, 1.0, 0.0, 0.0))
        ]

    @classmethod
    def _stamp_rect(cls, plane, attrs, rect):
        for name, value in zip(attrs, rect):
            if not cmds.attributeQuery(name, node=plane, exists=True):
                cmds.addAttr(plane, ln=name, at="double", dv=float(value))
            cmds.setAttr(f"{plane}.{name}", float(value))

    @classmethod
    def _stamp_pixel_rect(cls, plane, attrs, rect):
        for name, value in zip(attrs, rect):
            cls._ensure_int_attr(plane, name, value)

    # ------------------------------------------------------------- horizon map
    def _recursive_flag(self):
        if not self.shadow_plane:
            return True
        return bool(self._plane_attr(self.shadow_plane, self._RECURSIVE_ATTR, True))

    def _contact_frame(self):
        """The contact locator's world matrix (row-vector convention) — the
        horizon map's frame: origin at the contact, axes the target's own."""
        node = self.contact_locator or self.targets[0]
        matrix = cmds.xform(node, query=True, matrix=True, worldSpace=True)
        return np.array(matrix, dtype=float).reshape(4, 4)

    def _rigid_contact_frame(self):
        """:meth:`_contact_frame` with the scale taken out: the axes (the
        matrix's rows) normalised, the origin kept. The contact locator is
        parented under the target and inherits its scale, while every
        consumer of the map -- the previews, Unity, the viewer -- normalises
        the frame's axes; a map baked in the scaled frame read heights and
        distances divided by that scale (measured: a target scaled x2 in Y
        baked a 1 m map of a 2 m cube)."""
        frame = self._contact_frame()
        rigid = frame.copy()
        for i in range(3):
            length = float(np.linalg.norm(frame[i, :3]))
            if length > 1e-12:
                rigid[i, :3] = frame[i, :3] / length
        return rigid

    def horizon_output_path(self):
        """``<base>_horizon.png`` beside the silhouette."""
        if self.texture_path:
            folder = os.path.dirname(self.texture_path)
        else:
            folder = os.path.join(cmds.workspace(q=True, rd=True), "sourceimages")
        return os.path.join(folder, f"{self._name_base}_horizon.png").replace("\\", "/")

    @staticmethod
    def _geometry_hash(meshes, salt):
        """A digest of the meshes' points and triangles (millimetre-rounded)
        and *salt* -- the map's size and spans -- so Recalculate re-bakes the
        map when the target changed or the map was asked for at another
        resolution."""
        import hashlib

        digest = hashlib.sha1()
        digest.update(repr(salt).encode())
        for pts, tris in meshes:
            digest.update(np.round(np.asarray(pts, dtype=float), 3).tobytes())
            digest.update(np.asarray(tris, dtype=np.int64).tobytes())
        return digest.hexdigest()[:16]

    def bake_horizon(self, size=None, spans=None, path=None, *, only_if_changed=False):
        """Bake the target's height-field shadow map
        (``pythontk.ShadowHorizon``) in the contact locator's frame and write
        it beside the silhouette as ``<base>_horizon.png``; stamps the
        record's ``horizon`` block and turns the rig into the ``horizon``
        type. The engine marches the map per frame from the source node, so
        the outline follows a runtime light; the silhouette stays as the
        fallback and the DCC preview.

        The frame is the contact's, which is what lets a prop rotated (or
        moved) at runtime carry its shadow — the engine reads the same node's
        matrix. Its up axis is taken to be the ground's normal, exactly as
        the projection model takes the world's: a target tilted off the
        vertical is out of scope for both, since the plane itself is placed
        on the world ground.

        Parameters:
            size, spans: Footprint pixels per side and solid spans per
                column; ``ShadowHorizon``'s measured defaults when None.
            path: Write here instead of beside the silhouette.
            only_if_changed: Skip the bake when the target's geometry hash
                (and the map's size and spans) matches the stamped one
                (Recalculate).

        Returns:
            The PNG path.
        """
        from PIL import Image

        meshes = self._gather_world_meshes(self._recursive_flag())
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")
        size = int(size or ptk.ShadowHorizon.DEFAULT_SIZE)
        spans = int(spans or ptk.ShadowHorizon.DEFAULT_SPANS)
        digest = self._geometry_hash(meshes, f"{size}x{spans}")
        plane = self.shadow_plane
        current = self._plane_attr(plane, self._HORIZON_HASH_ATTR, "")
        if (
            only_if_changed
            and current == digest
            and self.horizon_path
            and os.path.exists(self.horizon_path)
        ):
            return self.horizon_path
        inverse = np.linalg.inv(self._rigid_contact_frame())
        local = []
        for pts, tris in meshes:
            hom = np.hstack([pts, np.ones((len(pts), 1))]) @ inverse
            local.append((hom[:, :3], tris))
        contact = self._contact_point()
        ground_pt = (
            np.array([contact[0], self.ground_height, contact[2], 1.0]) @ inverse
        )
        hmap = ptk.ShadowHorizon.bake(
            local, ground=float(ground_pt[1]), up=1, size=size, spans=spans
        )
        self.horizon_path = str(path) if path else self.horizon_output_path()
        self.horizon_path = self.horizon_path.replace("\\", "/")
        os.makedirs(os.path.dirname(self.horizon_path) or ".", exist_ok=True)
        Image.fromarray(hmap.to_rgba(), "RGBA").save(self.horizon_path)
        self.rig_type = "horizon"
        self._ensure_string_attr(plane, self._TYPE_ATTR, "horizon")
        self._ensure_string_attr(
            plane, self._HORIZON_TEX_ATTR, os.path.basename(self.horizon_path)
        )
        for name, value in zip(
            self._HORIZON_INT_ATTRS, (hmap.size, hmap.spans, hmap.levels)
        ):
            self._ensure_int_attr(plane, name, value)
        for name, value in zip(
            self._HORIZON_FLOAT_ATTRS, (*hmap.bounds, hmap.height_scale)
        ):
            self._ensure_plane_attr(name, float(value), keyable=False)
            cmds.setAttr(f"{plane}.{name}", float(value))
        if not cmds.attributeQuery(
            self._HORIZON_RECT_ATTRS[0], node=plane, exists=True
        ):
            self._stamp_rect(plane, self._HORIZON_RECT_ATTRS, (1.0, 1.0, 0.0, 0.0))
        self._ensure_string_attr(plane, self._HORIZON_HASH_ATTR, digest)
        self.logger.info(
            f"Baked horizon map: {self.horizon_path} ({hmap.size} px footprint, "
            f"{hmap.spans} spans)"
        )
        return self.horizon_path

    @classmethod
    def _plane_horizon_path(cls, plane):
        """The plane's own horizon PNG (beside its silhouette), or None."""
        name = cls._plane_attr(plane, cls._HORIZON_TEX_ATTR, "")
        if not name:
            return None
        tex = cls._plane_texture_path(plane)
        folder = (
            os.path.dirname(tex)
            if tex
            else os.path.join(cmds.workspace(q=True, rd=True), "sourceimages")
        )
        return os.path.join(folder, name).replace("\\", "/")

    @classmethod
    def _horizon_params(cls, plane):
        """The record's ``horizon`` block from the stamps; ``{}`` for a rig
        without a map."""
        if cls.plane_type(plane) != "horizon":
            return {}
        if not cls._plane_attr(plane, cls._HORIZON_TEX_ATTR, ""):
            return {}
        ints = [int(cls._plane_attr(plane, a, 0) or 0) for a in cls._HORIZON_INT_ATTRS]
        floats = [
            float(cls._plane_attr(plane, a, 0.0) or 0.0)
            for a in cls._HORIZON_FLOAT_ATTRS
        ]
        atlas = cls._plane_attr(plane, cls._HORIZON_ATLAS_ATTR, "")
        return ptk.ShadowHorizon.record(
            texture=atlas or cls._plane_attr(plane, cls._HORIZON_TEX_ATTR, ""),
            size=ints[0],
            spans=ints[1],
            levels=ints[2],
            bounds=floats[:4],
            height_scale=floats[4],
            frame_a=cls.HORIZON_FRAME[0],
            frame_b=cls.HORIZON_FRAME[1],
            rect=cls._read_rect(plane, cls._HORIZON_RECT_ATTRS),
        )

    # ------------------------------------------------------------------- atlas
    @classmethod
    def plane_is_atlased(cls, plane):
        """True while the plane samples the shared silhouette atlas."""
        return bool(cls._plane_attr(plane, cls._ATLAS_TEX_ATTR, ""))

    @classmethod
    def _atlased_planes(cls):
        """Every plane still sampling an atlas (silhouette or horizon)."""
        return [
            p
            for p in cls.find_shadow_planes()
            if any(cls._packed_in(p, kind) for kind in cls.RIG_TYPES)
        ]

    @classmethod
    def _packed_in(cls, plane, kind):
        """Is *plane* currently sampling the *kind* atlas?"""
        attr = cls._ATLAS_TEX_ATTR if kind == "projected" else cls._HORIZON_ATLAS_ATTR
        return bool(cls._plane_attr(plane, attr, ""))

    @classmethod
    def _clear_atlas_stamps(cls, plane, kind):
        """Take *plane* out of the *kind* atlas: identity rect, and for the
        silhouette its own UVs and file node back."""
        if kind == "projected":
            own = cls._plane_texture_path(plane)
            cls._set_plane_uvs(plane, (1.0, 1.0, 0.0, 0.0))
            cls._ensure_string_attr(plane, cls._ATLAS_TEX_ATTR, "")
            cls._stamp_rect(plane, cls._ATLAS_RECT_ATTRS, (1.0, 1.0, 0.0, 0.0))
            cls._rebind_file_node(plane, own)
        else:
            cls._ensure_string_attr(plane, cls._HORIZON_ATLAS_ATTR, "")
            cls._stamp_rect(plane, cls._HORIZON_RECT_ATTRS, (1.0, 1.0, 0.0, 0.0))

    @classmethod
    def _repack_atlased(cls):
        """Rewrite the atlases from the planes that are still packed — after a
        rig is deleted or unpacked. Removes an atlas nothing samples."""
        atlased = cls._atlased_planes()
        if atlased:
            return cls.pack_atlas(atlased)
        for kind in cls.RIG_TYPES:
            path = cls._atlas_path(kind)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return {}

    @classmethod
    def _atlas_path(cls, kind, folder=None):
        folder = folder or os.path.join(cmds.workspace(q=True, rd=True), "sourceimages")
        return os.path.join(folder, cls.ATLAS_BASENAMES[kind]).replace("\\", "/")

    @classmethod
    def _set_plane_uvs(cls, plane, rect):
        """Remap the quad's unit UVs into *rect* (undoing the rect they were
        last remapped into), so a fallback viewer samples the tile with no
        transform at all."""
        prev = (
            cls._read_rect(plane, cls._ATLAS_RECT_ATTRS)
            if cls.plane_is_atlased(plane)
            else [1.0, 1.0, 0.0, 0.0]
        )
        count = cmds.polyEvaluate(plane, uvcoord=True)
        for i in range(int(count)):
            u, v = cmds.polyEditUV(f"{plane}.map[{i}]", query=True)
            unit_u = round((u - prev[2]) / prev[0]) if prev[0] else 0
            unit_v = round((v - prev[3]) / prev[1]) if prev[1] else 0
            cmds.polyEditUV(
                f"{plane}.map[{i}]",
                relative=False,
                uValue=rect[2] + unit_u * rect[0],
                vValue=rect[3] + unit_v * rect[1],
            )

    @classmethod
    def _rebind_file_node(cls, plane, path):
        node = cls._plane_texture_node(plane)
        if node and path:
            # VP2 caches by path and ignores a set to the same value.
            cmds.setAttr(f"{node}.fileTextureName", "", type="string")
            cmds.setAttr(f"{node}.fileTextureName", path, type="string")

    @classmethod
    def pack_atlas(cls, planes=None, *, gutter=None):
        """Pack the scene's shadow tiles into one atlas per kind — every
        plane's silhouette into ``shadow_atlas_projected.png`` and every
        horizon rig's map block into ``shadow_atlas_horizon.png`` — beside
        the tiles in ``sourceimages``.

        A packed plane keeps its own PNG (Recalculate rewrites the tile in
        place through :meth:`_write_atlas_tile`), has its quad UVs remapped
        into its inset rect and its file node pointed at the atlas, and
        carries the rect in its record (``atlas`` / ``horizon.rect``) so the
        engines batch and instance planes that share a type. Any plane
        already packed is repacked with *planes* — the atlas is one file, and
        a partial repack would move rects out from under the others.

        Parameters:
            planes: The planes to pack (all when None).
            gutter: Texels inset on every side of a published rect
                (``ShadowAtlas.GUTTER`` when None).

        Returns:
            ``{kind: atlas path}`` for the kinds that packed anything.
        """
        from PIL import Image

        gutter = ptk.ShadowAtlas.GUTTER if gutter is None else int(gutter)
        # None = every plane; an explicit empty sequence = none of them (the
        # repack paths pass exactly the planes that must stay packed).
        wanted = set(cls.find_shadow_planes(planes) if planes or planes is None else [])
        wanted |= set(cls._atlased_planes())
        planes = sorted(wanted)
        out = {}
        for kind in cls.RIG_TYPES:
            members, orphans = [], []
            for plane in planes:
                if kind == "projected":
                    tex = cls._plane_texture_path(plane)
                elif cls.plane_type(plane) == "horizon":
                    tex = cls._plane_horizon_path(plane)
                else:
                    tex = None
                if tex and os.path.exists(tex):
                    members.append((plane, tex))
                elif cls._packed_in(plane, kind):
                    # Its tile is gone (deleted, or the rig came from another
                    # project): leaving the stamps would aim its UVs at a rect
                    # the repack hands to a different plane — a shadow wearing
                    # someone else's shape. Drop it out of the atlas instead.
                    orphans.append(plane)
            for plane in orphans:
                cls._clear_atlas_stamps(plane, kind)
                cls.logger.warning(
                    f"{CoreUtils.leaf_name(plane)}: its "
                    f"{'silhouette' if kind == 'projected' else 'horizon map'} PNG is "
                    "missing, so it was dropped from the atlas — Recalculate "
                    "Silhouette rewrites it, then Pack Atlas re-joins it."
                )
            atlas_path = cls._atlas_path(
                kind, os.path.dirname(members[0][1]) if members else None
            )
            if not members:
                if os.path.exists(atlas_path):
                    try:
                        os.remove(atlas_path)
                    except OSError:
                        pass
                continue
            tiles = {
                CoreUtils.leaf_name(p): np.asarray(Image.open(t).convert("RGBA"))
                for p, t in members
            }
            # A horizon map is addressed by TEXEL (the shader fetches, never
            # filters), so its published rect is the block's exact rect: the
            # gutter inset that protects a silhouette's bilinear taps would
            # shift every texel address by the inset.
            atlas, rects, pixel_rects = ptk.ShadowAtlas.pack(
                tiles, gutter=0 if kind == "horizon" else gutter
            )
            Image.fromarray(atlas, "RGBA").save(atlas_path)
            base = os.path.basename(atlas_path)
            for plane, tex in members:
                name = CoreUtils.leaf_name(plane)
                if kind == "projected":
                    cls._set_plane_uvs(plane, rects[name])
                    cls._ensure_string_attr(
                        plane, cls._SILHOUETTE_ATTR, os.path.basename(tex)
                    )
                    cls._ensure_string_attr(plane, cls._ATLAS_TEX_ATTR, base)
                    cls._stamp_rect(plane, cls._ATLAS_RECT_ATTRS, rects[name])
                    cls._stamp_pixel_rect(
                        plane, cls._ATLAS_PIXEL_ATTRS, pixel_rects[name]
                    )
                    cls._rebind_file_node(plane, atlas_path)
                else:
                    cls._ensure_string_attr(plane, cls._HORIZON_ATLAS_ATTR, base)
                    cls._stamp_rect(plane, cls._HORIZON_RECT_ATTRS, rects[name])
                    cls._stamp_pixel_rect(
                        plane, cls._HORIZON_PIXEL_ATTRS, pixel_rects[name]
                    )
            out[kind] = atlas_path
        if planes:
            cls.refresh_export_metadata()
        return out

    @classmethod
    def unpack_atlas(cls, planes=None):
        """Undo :meth:`pack_atlas` for *planes* (all when None): unit UVs,
        the file node back on the plane's own PNG, the rect stamps cleared.
        Returns the planes that were unpacked."""
        done = []
        for plane in cls.find_shadow_planes(planes):
            touched = False
            for kind in cls.RIG_TYPES:
                if cls._packed_in(plane, kind):
                    cls._clear_atlas_stamps(plane, kind)
                    touched = True
            if touched:
                done.append(plane)
        if done:
            # The survivors' atlas is rewritten without the leavers.
            cls._repack_atlased()
            cls.refresh_export_metadata()
        return done

    @classmethod
    def _write_atlas_tile(cls, plane, kind):
        """Rewrite one packed tile in place from the plane's own PNG and
        return the atlas path (no repack: the rect is the stamped one)."""
        from PIL import Image

        if kind == "projected":
            tex, attrs, atlas_name = (
                cls._plane_texture_path(plane),
                cls._ATLAS_PIXEL_ATTRS,
                cls._plane_attr(plane, cls._ATLAS_TEX_ATTR, ""),
            )
        else:
            tex, attrs, atlas_name = (
                cls._plane_horizon_path(plane),
                cls._HORIZON_PIXEL_ATTRS,
                cls._plane_attr(plane, cls._HORIZON_ATLAS_ATTR, ""),
            )
        if not (tex and atlas_name and os.path.exists(tex)):
            return None
        atlas_path = os.path.join(os.path.dirname(tex), atlas_name).replace("\\", "/")
        if not os.path.exists(atlas_path):
            return None
        rect = tuple(int(cls._plane_attr(plane, a, 0) or 0) for a in attrs)
        atlas = np.asarray(Image.open(atlas_path).convert("RGBA")).copy()
        tile = np.asarray(Image.open(tex).convert("RGBA"))
        ptk.ShadowAtlas.write_tile(atlas, rect, tile)
        Image.fromarray(atlas, "RGBA").save(atlas_path)
        return atlas_path

    @classmethod
    def find_shadow_planes(cls, nodes=None):
        """Shadow planes = transforms carrying the stamped ``basePlaneSize``
        attr. ``nodes`` limits the search (their descendants included, so a
        selected ``*_shadow_grp`` finds its plane); None scans the scene."""
        if nodes:
            # Selections can carry non-DAG nodes (shaders, sets) — filter to
            # transforms before walking descendants.
            pool = cmds.ls([str(n) for n in nodes], transforms=True) or []
            if pool:
                # fullPath: bare leaf names are ambiguous under duplicate
                # transform names and crash attributeQuery; ls() normalizes
                # back to shortest-unique (matching the scene-scan branch).
                kids = (
                    cmds.listRelatives(pool, ad=True, type="transform", fullPath=True)
                    or []
                )
                pool = cmds.ls(pool + kids) or []
        else:
            pool = cmds.ls(type="transform") or []
        return [
            n
            for n in dict.fromkeys(pool)
            if cmds.attributeQuery("basePlaneSize", node=n, exists=True)
        ]

    @classmethod
    def bake_planes(cls, planes=None, start=None, end=None):
        """Bake shadow planes' expression-driven channels to keyframes and
        delete the live rig nodes (expression + driver nodes) so the result
        exports cleanly to FBX.

        Keys the transform channels AND the ``opacity`` fade, euler-unrolls
        the baked ``rotateY``, and mirrors the fade's zero runs to sparse
        visibility keys (the pairs Unity's opacity importer rebuilds a fade
        from; the keyed visibility the GLB route needs to carry the ramp).

        Args:
            planes: Shadow plane transform(s); None bakes every shadow plane
                in the scene that still has a live expression.
            start/end: Frame range; defaults to the playback range.

        Returns:
            The list of planes that were baked.
        """
        planes = cls.find_shadow_planes(planes)
        if start is None:
            start = cmds.playbackOptions(q=True, min=True)
        if end is None:
            end = cmds.playbackOptions(q=True, max=True)

        live = []  # (plane, rig nodes to delete after the bake)
        for plane in planes:
            if cmds.referenceQuery(plane, isNodeReferenced=True):
                # A referenced rig's expression can't be deleted from here —
                # bake in the source file instead of half-baking this one.
                cls.logger.warning(f"Skipping referenced shadow plane: {plane}")
                continue
            exprs, drivers = cls._live_rig_nodes(plane)
            if not exprs:
                continue  # already baked / hand-keyed
            live.append((plane, exprs | drivers))
        if not live:
            return []

        # ONE bakeResults over every live plane's plugs — simulation=True
        # replays the whole timeline, so baking per-plane would cost one
        # full playback per rig. Rigs built before the fade channel existed
        # have no opacity attr to key.
        plugs = [
            f"{plane}.{ch}"
            for plane, _ in live
            for ch in cls.BAKE_CHANNELS
            if cmds.attributeQuery(ch, node=plane, exists=True)
        ]
        cmds.bakeResults(
            plugs,
            time=(start, end),
            simulation=True,
            sampleBy=1,
            disableImplicitControl=True,
            preserveOutsideKeys=False,
        )
        for _, nodes in live:
            for node in nodes:
                if cmds.objExists(node):
                    cmds.delete(node)
        baked = [plane for plane, _ in live]
        for plane in baked:
            cls._unroll_rotation(plane)
            cls._mirror_fade_to_visibility(plane)
        cls.refresh_export_metadata()
        return baked

    # ------------------------------------------------------------------ delete
    def delete(self, delete_textures=False):
        """Delete this rig completely. See :meth:`delete_rigs`."""
        return self.delete_rigs([self.shadow_plane], delete_textures=delete_textures)

    @classmethod
    def delete_rigs(cls, planes=None, delete_textures=False):
        """Tear down shadow rig(s) completely — live or baked.

        Removes, per plane: the plane and its enclosing ``*_shadow_grp``
        (when it holds nothing else), the expression and driver nodes, the
        whole shading network (shader, SG, file/place2d/opacity nodes — via
        the ``shadowRigMembers`` manifest stamped at create), and the contact
        locator parented under the target. The targets and the shared
        shadow-source locator are left untouched; the ``shadow_metadata``
        channel is republished afterwards.

        Rigs built before the manifest existed still lose the plane, group,
        and any live expression/driver nodes (resolved via connections);
        their shading network is left assigned-but-orphaned.

        Args:
            planes: Shadow plane transform(s); None deletes every shadow
                rig in the scene.
            delete_textures: Also remove the silhouette PNG from disk.

        Returns:
            The list of planes that were deleted.
        """
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        planes = cls.find_shadow_planes(planes)
        deleted = []
        repack = False
        for plane in planes:
            if cmds.referenceQuery(plane, isNodeReferenced=True):
                # Referenced nodes can't be deleted from here — tear the rig
                # down in its source file.
                cls.logger.warning(f"Skipping referenced shadow plane: {plane}")
                continue
            # A live preview's nodes are not in the manifest (display state,
            # never rig content): stand it down first or they outlive the rig.
            if ShadowPreview.is_attached(plane):
                ShadowPreview.detach(plane)
            doomed = set()
            # The stamped manifest (rename-proof; includes the shading
            # network and the ShaderFX graph's stock file nodes).
            if cmds.attributeQuery(cls._MEMBER_ATTR, node=plane, exists=True):
                doomed.update(
                    cmds.listConnections(
                        f"{plane}.{cls._MEMBER_ATTR}",
                        source=True,
                        destination=False,
                    )
                    or []
                )
            # Live rig nodes via connections (covers pre-manifest rigs).
            exprs, drivers = cls._live_rig_nodes(plane)
            doomed |= exprs | drivers

            if delete_textures:
                for tex in (
                    cls._plane_texture_path(plane),
                    cls._plane_horizon_path(plane),
                ):
                    if tex and os.path.exists(tex):
                        try:
                            os.remove(tex)
                        except OSError:
                            pass  # locked/read-only — node teardown still proceeds
            # An atlased rig leaves its tile behind; the survivors repack below.
            atlased = cls.plane_is_atlased(plane)
            # The enclosing group — only when named like ours and holding
            # nothing but this plane (never a user's own parent group).
            root = plane
            parent = cmds.listRelatives(plane, parent=True, fullPath=True)
            if parent and CoreUtils.leaf_name(parent[0]).endswith("_shadow_grp"):
                kids = cmds.listRelatives(parent[0], children=True, fullPath=True)
                if len(kids or []) == 1:
                    root = parent[0]
            for node in [root] + sorted(doomed):
                if node and cmds.objExists(node):
                    cmds.delete(node)
            deleted.append(plane)
            repack = repack or atlased
        if repack:
            cls._repack_atlased()
        if deleted:
            cls.refresh_export_metadata()
        return deleted

    def _link_members(self, new_nodes):
        """Stamp the build's support nodes onto the plane as a multi message
        attr — :meth:`delete_rigs`' rename-proof teardown manifest.

        ``new_nodes`` is the created-node set diff captured around the build
        (long names). The plane's and source's subtrees are excluded: the
        plane/group are the deletion roots, and the source locator is shared
        across rigs by design. Everything else — contact locator, expression,
        driver nodes, the whole shading network (including the ShaderFX
        graph's stock file nodes) — is linked.
        """
        if not cmds.attributeQuery(
            self._MEMBER_ATTR, node=self.shadow_plane, exists=True
        ):
            cmds.addAttr(
                self.shadow_plane,
                ln=self._MEMBER_ATTR,
                at="message",
                multi=True,
                indexMatters=False,
            )
        roots = cmds.ls(
            [n for n in (self.shadow_plane, self.group, self.light) if n],
            long=True,
        )
        subtree_prefixes = tuple(f"{r}|" for r in roots)
        for node in sorted(new_nodes):
            if not cmds.objExists(node):
                continue
            if node in roots or node.startswith(subtree_prefixes):
                continue
            cmds.connectAttr(
                f"{node}.message",
                f"{self.shadow_plane}.{self._MEMBER_ATTR}",
                nextAvailable=True,
                force=True,
            )

    @classmethod
    def create(
        cls,
        targets,
        light_pos=(5, 10, 5),
        texture_res=512,
        axis="auto",
        source_name=DEFAULT_SOURCE_NAME,
        recursive=True,
        mode="orbit",
        ground_height=0.0,
        shader_type="standard",
        rig_type="projected",
        horizon_size=None,
        horizon_spans=None,
    ):
        """Create a projected shadow for engine export.

        Args:
            targets: Object(s) to cast shadow from. Can be a single object
                     or a list of objects for a combined shadow.
            rig_type: ``"projected"`` (default) or ``"horizon"`` — the latter
                also bakes the target's horizon map (:meth:`bake_horizon`)
                so the engine can follow a runtime light; the silhouette
                stays as the fallback and the DCC preview.
            horizon_size, horizon_spans: The horizon map's footprint pixels
                per side and solid spans per column;
                ``pythontk.ShadowHorizon``'s measured defaults when None.
            light_pos: Initial position for a source locator this call creates.
            texture_res: Resolution of silhouette texture
            axis: Retired — the silhouette is always the projection through
                  the source; any other value warns and is ignored.
            source_name: The shadow source — any existing transform's name
                         (a light included; a ``directionalLight`` projects
                         along its direction), or the name of a locator to
                         create at ``light_pos``. Reuse a name to share one
                         source; one plane is built per source, so call
                         :meth:`create_for_sources` for several.
            recursive: If True, include descendant meshes in shadow.
            mode: ``"orbit"`` (the plane rotates to face away from the light).
                  ``"stretch"`` is retired and builds as orbit with a warning.
            ground_height: World Y of the ground plane the shadow lies on
                (editable afterwards on the plane's ``groundHeight`` attr).
            shader_type: ``"standard"`` (default) or the retired ``"stingray"``
                (see :meth:`create_material`).

        Returns:
            ShadowRig instance

        Note: a failed build rolls itself back — every node created up to
        the failure (including a source locator this call created) and any
        half-written texture are removed before the exception re-raises.
        """
        if rig_type not in cls.RIG_TYPES:
            raise ValueError(f"rig_type {rig_type!r} is not one of {cls.RIG_TYPES}.")
        shadow = cls(
            targets=targets,
            mode=mode,
            ground_height=ground_height,
            source_name=source_name,
        )
        pre = set(cmds.ls(long=True))
        try:
            shadow.get_or_create_shadow_source(
                position=light_pos, source_name=source_name
            )
            shadow.create_contact_locator()
            shadow.create_shadow_plane()
            shadow.create_silhouette_texture(
                size=texture_res, axis=axis, recursive=recursive
            )
            shadow.create_material(shader_type=shader_type)
            shadow.setup_expression()
            # Follow Source, when it is on: either end of the projection.
            shadow._watch_nodes([shadow.light, *shadow.targets])
            if rig_type == "horizon":
                shadow.bake_horizon(size=horizon_size, spans=horizon_spans)

            shadow.group = cmds.group(
                empty=True, name=f"{shadow._name_base}_shadow_grp"
            )
            # Re-capture: parenting can path-qualify the name under a collision
            # (same lesson as Controls.create — the stale ref stops resolving).
            shadow.shadow_plane = cmds.parent(shadow.shadow_plane, shadow.group)[0]
            # contact_locator stays on first target

            # Stamp the rig's support nodes onto the plane (delete_rigs'
            # teardown manifest) BEFORE the metadata refresh — the data_export
            # carrier is shared and must never enter the manifest.
            shadow._link_members(set(cmds.ls(long=True)) - pre)
            shadow._stamp_rig_links()
        except Exception:
            # Roll back the partial build — a failed create() must not leave
            # orphan nodes (or a half-written texture) behind.
            for node in set(cmds.ls(long=True)) - pre:
                if cmds.objExists(node):  # cascade deletions invalidate paths
                    try:
                        cmds.delete(node)
                    except (RuntimeError, ValueError):
                        pass
            for stale in (shadow.texture_path, shadow.horizon_path):
                if stale and os.path.exists(stale):
                    try:
                        os.remove(stale)
                    except OSError:
                        pass
            raise

        # A rig built while a viewport is isolated must land visible: a
        # Utility Rebuild or a script runs outside the Preview, whose own
        # isolation pass covers only the passes it drives. Idempotent, and a
        # no-op unless a panel has Isolate Select on.
        DisplayUtils.add_to_isolation_set(
            [shadow.group, shadow.shadow_plane, shadow.contact_locator]
        )

        # Publish the engine hand-off record onto the data_export carrier (the
        # Scene Exporter re-refreshes it at export time via run_export_preparers).
        cls.refresh_export_metadata()

        target_names = ", ".join(str(t) for t in shadow.targets)
        shadow.logger.success(
            f"Shadow rig for {target_names} ({shadow.mode}) — plane "
            f"{shadow.shadow_plane}, source {shadow.light}, "
            f"texture {shadow.texture_path}"
        )
        return shadow

    @classmethod
    def create_for_sources(cls, targets, sources, **kwargs):
        """One shadow rig per source — N lights cast N shadows.

        Each source gets its own plane, expression, and silhouette PNG (the
        projection differs per source), named ``<target>_<source>_shadow``
        for any source but the default ``shadow_source`` (which keeps the
        plain ``<target>_shadow``). ``kwargs`` pass through to :meth:`create`.

        Args:
            targets: Object(s) to cast shadow from.
            sources: Source transform names (existing transforms, lights
                included, or locator names to create).

        Returns:
            The list of ShadowRig instances, in ``sources`` order.
        """
        names = [str(s) for s in ptk.make_iterable(sources) if str(s).strip()]
        if not names:
            names = [cls.DEFAULT_SOURCE_NAME]
        kwargs.pop("source_name", None)
        return [cls.create(targets, source_name=name, **kwargs) for name in names]

    @classmethod
    def create_horizon_for_sources(cls, targets, sources, **kwargs):
        """:meth:`create_for_sources` for the ``horizon`` rig type."""
        kwargs["rig_type"] = "horizon"
        return cls.create_for_sources(targets, sources, **kwargs)

    @classmethod
    def create_per_object(cls, targets, sources, **kwargs):
        """One rig per target per source — the panel's *Per object* planes.

        Each transform gets its own contact, quad, tile and record (a table
        with props on it is a *Combined* rig instead); ``kwargs`` pass through
        to :meth:`create`. Returns the rigs, targets-major.
        """
        rigs = []
        for target in ptk.make_iterable(targets):
            rigs.extend(cls.create_for_sources([target], sources, **kwargs))
        return rigs


class ShadowRigSlots:
    #: Rig types the panel offers, keyed by the ``Rig:`` combo's label ->
    #: the engine builder ``(targets, source_names, **options) -> [rigs]``.
    #: The strategy seam a new rig type lands in: one row here, one combo
    #: item, no branching in :meth:`perform_operation`.
    RIG_BUILDERS = {
        "Projected": ShadowRig.create_for_sources,
        "Horizon": ShadowRig.create_horizon_for_sources,
    }
    #: A combo item carrying this suffix is a rig type only planned for —
    #: listed so the panel shows the direction, disabled until it lands.
    PLANNED_SUFFIX = "(planned)"
    #: ``Atlas:`` combo → whether to pack the built rigs' tiles: Auto packs
    #: once two rigs of a kind exist in the scene.
    ATLAS_MODES = ("Auto", "Off", "On")

    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (shadow_rig.ui)
        self.ui = self.sb.loaded_ui.shadow_rig

        # Preview wraps perform_operation in an undo chunk so toggling the
        # checkbox builds the rig, tweaking any option refreshes it, and
        # clicking b000 (Create Shadow) commits.
        self.preview = Preview(
            self, self.ui.chk_preview, self.ui.b000, message_func=self.sb.message_box
        )

        # Any option change should re-bake the previewed rig.
        self.ui.cmb_type.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb_planes.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb_atlas.currentIndexChanged.connect(self.preview.refresh)
        self.ui.chk_combine.toggled.connect(self.preview.refresh)
        self.ui.s000.currentIndexChanged.connect(self.preview.refresh)
        # A renamed source must exist BEFORE the refresh rebuilds against it,
        # and outside the preview contract (see prepare_operation).
        self._built_sources = None  # the names the live preview was built from
        self.ui.txt_source.editingFinished.connect(self._on_sources_edited)
        # b000-b003 and b009-b010 are auto-wired by the switchboard (method
        # name == objectName); a raw connect here on one of those stacked a
        # second connection → double-fire. The deeper Utility actions (Apply
        # Source, Rebuild Rig, Restore Expression) hang off b003's / b002's
        # option boxes — see b003_init / b002_init — and the actions about
        # the source (Source From Selection, Reproject) off Source Name's —
        # see txt_source_init.

        self._init_tooltips()

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly "
                "for game engines (Unity, WebXR). The plane carries the "
                "target's shadow as a PNG — its geometry projected onto the "
                "ground through the source, the way a real shadow forms: an "
                "overhead source draws the footprint, a low one the long "
                "stretched shape, an area light a penumbra that softens away "
                "from the contact. An expression keeps the plane's direction, "
                "reach and fade tracking the source and the target live.",
                steps=[
                    "Select one or more target meshes.",
                    "Pick the <b>Rig</b> type — <b>Projected</b> (one silhouette "
                    "the model re-places) or <b>Horizon</b> (also bakes a "
                    "horizon map the engine samples per frame, so the outline "
                    "follows a runtime light).",
                    "Pick <b>Planes</b> — <b>Combined</b> builds one plane for "
                    "the whole selection, <b>Per object</b> one per object — and "
                    "<b>Atlas</b>: <b>Auto</b> packs the planes' tiles into one "
                    "texture per kind once two rigs exist, so the engines batch "
                    "and instance them.",
                    "Enable <b>Preview</b> to build the rig live. The source "
                    "locator is created once and survives every refresh — "
                    "move it to place the light, or pick a real light with "
                    "<b>Source From Selection</b> (the pick icon beside "
                    "Source Name).",
                    "Tweak <b>Resolution</b> and <b>Include Children</b>; the "
                    "preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview "
                    "to discard.",
                    "Move the source, or the target under it: <b>Follow "
                    "Source</b> (on by default) re-renders every silhouette as "
                    "soon as either has moved -- the plane already follows "
                    "both, but the drawn shape is one direction's projection. "
                    "Off, or after a geometry edit (which it does not watch), "
                    "press <b>Reproject</b> (the refresh icon beside Source "
                    "Name) or <b>Recalculate Silhouette</b>.",
                    "<b>Softness</b> is the diameter the shadow gives the "
                    "source (world units; a directional light: degrees). It "
                    "lives on the source, so every rig it lights, Unity and "
                    "the viewer share one penumbra; 0 is sharp.",
                    "A committed <b>Horizon</b> rig shows its live preview at "
                    "once, so its outline morphs as the light moves; the "
                    "<b>Live Horizon Preview</b> box mirrors what stands.",
                    "Export through the <b>Scene Exporter</b> — its smart bake "
                    "bakes the expression and the rig's <i>shadow_metadata</i> "
                    "rides the data_export carrier. For File > Export or a "
                    "bridge, press <b>Bake to Keyframes</b> first.",
                ],
                sections=[
                    (
                        "Sources",
                        [
                            "<b>Source Name</b> — one or more transform names, "
                            "comma-separated; one shadow plane is built per source.",
                            "<b>Source From Selection</b> — use the selected "
                            "transform(s), lights included, as the source(s). A "
                            "directional light projects along its direction, like "
                            "the sun. Selected <b>faces</b> are a fixture: a real "
                            "area light is "
                            "built per shape, the way the Lighting panel does, and "
                            "becomes the source.",
                            "<b>Reproject</b> — re-render the silhouette of every "
                            "rig the named source(s) light, from where the source "
                            "and the target are now: the manual form of Follow "
                            "Source, and the one to press after a geometry edit. "
                            "A Horizon rig's live map follows the light on its "
                            "own; its fallback silhouette is redrawn like the rest.",
                        ],
                    ),
                    (
                        "Utility",
                        [
                            "Every Utility button acts on the rig(s) the "
                            "selection touches — the plane, its group, a target, "
                            "the source, or any of the rig's nodes — also on rigs "
                            "built in an earlier session.",
                            "<b>Recalculate Silhouette</b> re-renders the PNG; "
                            "its option box holds the deeper updates: <b>Apply "
                            "Source</b> (re-point the rig at the Source Name "
                            "field) and <b>Rebuild Rig</b> (re-create it from the "
                            "target's current geometry with the panel's options). "
                            "<b>Bake to Keyframes</b>' option box holds <b>Restore "
                            "Expression</b>, its inverse; <b>Delete Rig</b> tears "
                            "the rig down.",
                        ],
                    ),
                    (
                        "Plane attributes",
                        [
                            "<b>shadowIntensity</b> / <b>falloffPower</b> — overall "
                            "strength and how fast an elongated shadow lightens.",
                            "<b>maxStretch</b> — cap on the shadow's reach, in "
                            "object heights.",
                            "<b>fadeHeight</b> — rise off the ground at which the "
                            "shadow has fully faded.",
                            "<b>groundHeight</b> — world Y of the ground the "
                            "shadow lies on.",
                        ],
                    ),
                ],
                notes=[
                    "Unity plug-and-play: deploy unitytk's C# templates once "
                    "(<i>unitytk.TemplateDeployer.deploy_package</i>) and export "
                    "via the Scene Exporter with Embed Textures on — the import "
                    "sets up the unlit-transparent material and shadow flags "
                    "automatically. Other engines: assign an unlit/transparent "
                    "shader with the PNG by hand.",
                    "The fade is the plane's keyable <i>opacity</i>: it bakes "
                    "with the transform and rides the FBX as an animated "
                    "custom attribute.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        ui = self.ui

        ui.cmb_type.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Type",
                body="Which shadow rig to build.",
                sections=[
                    (
                        "Types",
                        [
                            "<b>Projected</b> — one silhouette, the target's "
                            "projection through the source, re-placed live by the "
                            "projection model; <b>Recalculate Silhouette</b> "
                            "re-renders it when the source has moved.",
                            "<b>Horizon</b> — the projected rig plus a "
                            "coverage-aware horizon map (<i>&lt;name&gt;_horizon.png</i>) "
                            "baked in the target's own frame: the engine samples "
                            "it per frame from the source node, so the outline "
                            "follows a moving light — and a moved prop carries its "
                            "shadow — without a re-render. The silhouette stays as "
                            "the fallback and the viewport preview. Design: "
                            "<i>mayatk/docs/shadow_rig_morphing.md</i>.",
                        ],
                    )
                ],
            )
        )
        ui.cmb_planes.setToolTip(
            self.sb.tooltip.fmt(
                title="Planes",
                body="How many shadow planes the selection builds.",
                sections=[
                    (
                        "Modes",
                        [
                            "<b>Combined</b> — one plane for the whole selection "
                            "(a table with the props on it casts one shadow).",
                            "<b>Per object</b> — one plane per selected object, "
                            "each with its own contact, tile and record; the "
                            "planes share an atlas and the engines instance them.",
                        ],
                    )
                ],
                notes=["Either way, one plane is built per source."],
            )
        )
        ui.cmb_atlas.setToolTip(
            self.sb.tooltip.fmt(
                title="Atlas",
                body="Pack the planes' tiles into one texture per kind — the "
                "silhouettes into <i>shadow_atlas_projected.png</i>, the horizon "
                "maps into <i>shadow_atlas_horizon.png</i> — so the engines draw "
                "every plane of a kind with one material and instance them.",
                sections=[
                    (
                        "Modes",
                        [
                            "<b>Auto</b> — pack once the scene holds two rigs.",
                            "<b>Off</b> — every plane keeps its own texture.",
                            "<b>On</b> — always pack.",
                        ],
                    )
                ],
                notes=[
                    "Each plane keeps its own PNG: Recalculate rewrites its "
                    "tile in place, and a fallback viewer samples the atlas "
                    "through the plane's own UVs with no transform at all.",
                    "<b>Pack Atlas</b> in the Utility section packs or "
                    "repacks every rig in the scene.",
                ],
            )
        )
        ui.b010.setToolTip(
            self.sb.tooltip.fmt(
                title="Pack Atlas",
                body="Pack (or repack) every shadow rig's tiles into the "
                "scene's atlases — see the <b>Atlas</b> option.",
                notes=[
                    "Acts on the whole scene: the atlas is one file, so a "
                    "partial repack would move rects out from under the "
                    "other planes.",
                ],
            )
        )
        ui.chk_combine.setToolTip(
            self.sb.tooltip.fmt(
                title="Include Children",
                body="Include the selected objects' descendant meshes in the "
                "baked silhouette.",
                notes=[
                    "The selection always shares a single combined shadow plane.",
                    "Off — only the selected meshes themselves are rasterized.",
                ],
            )
        )
        ui.txt_source.setToolTip(
            self.sb.tooltip.fmt(
                title="Source Name",
                body="The shadow source(s) the projection is cast from — any "
                "transform name (a light included), comma-separated for "
                "several. A missing name is created as a locator when the "
                "preview starts.",
                notes=[
                    "Reuse a name to share one source across rigs; one shadow "
                    "plane is built per source.",
                    "A directional light projects along its direction (the sun); "
                    "anything else casts from where it sits.",
                    "Move the source in the viewport — the preview keeps it.",
                    "Its option box holds <b>Source From Selection</b> (the "
                    "pick icon) and <b>Reproject</b> (the refresh icon).",
                ],
            )
        )
        ui.s000.setToolTip(
            self.sb.tooltip.fmt(
                title="Texture Resolution",
                body="Pixel resolution of the baked silhouette PNG carried by "
                "the shadow plane.",
                notes=[
                    "Higher = crisper shadow edge, but a larger texture on disk.",
                ],
            )
        )
        ui.chk_horizon_preview.setToolTip(
            self.sb.tooltip.fmt(
                title="Live Horizon Preview",
                body="Shows a Horizon rig's shadow the way the engines will: "
                "the baked map evaluated in the viewport from the live source, "
                "so the outline morphs as you move the light.",
                notes=[
                    "Acts on the Horizon rig(s) the selection touches, or all "
                    "when nothing is selected.",
                    "Display only: nothing about the rig or its export changes, "
                    "and the preview is stood down before any FBX export.",
                    "Needs a hardware viewport (Viewport 2.0 on DirectX 11 or OpenGL Core Profile).",
                ],
            )
        )
        ui.chk_preview.setToolTip(
            self.sb.tooltip.fmt(
                title="Preview",
                body="Builds the shadow rig live so you can judge it before "
                "committing.",
                notes=[
                    "Tweaking any option refreshes the preview; the source "
                    "keeps its position.",
                    "<b>Create Shadow</b> commits it; disabling Preview discards it.",
                ],
            )
        )
        ui.b000.setToolTip(
            self.sb.tooltip.fmt(
                title="Create Shadow",
                body="Commits the previewed shadow rig for the selected target(s), "
                "or builds one straight from the selection.",
                steps=[
                    "Select one or more target meshes.",
                    "Enable <b>Preview</b> and dial in the options.",
                    "Press <b>Create Shadow</b>.",
                ],
            )
        )
        ui.b001.setToolTip(
            self.sb.tooltip.fmt(
                title="Reset to Defaults",
                body="Restores every option on this panel to its default value.",
            )
        )
        ui.b002.setToolTip(
            self.sb.tooltip.fmt(
                title="Bake to Keyframes",
                body="Bakes the shadow plane's driven motion and fade to "
                "keyframes over the playback range and removes the live rig — "
                "leaving an FBX-ready plane.",
                notes=[
                    "Applies to the rig(s) the selection touches, or all planes "
                    "if nothing is selected.",
                    "The Scene Exporter's smart bake does this for you; bake "
                    "here before File > Export, the Game Exporter, or a bridge.",
                    "Its option box holds <b>Restore Expression</b>, which "
                    "reverses it.",
                ],
            )
        )
        ui.b003.setToolTip(
            self.sb.tooltip.fmt(
                title="Recalculate Silhouette",
                body="Re-renders the silhouette PNG from the source's current "
                "position and the target's current geometry, overwriting the "
                "plane's texture in place.",
                notes=[
                    "Applies to the rig(s) the selection touches, or all planes "
                    "if nothing is selected.",
                    "Works on a baked rig — the PNG is drawn into the canvas "
                    "its keys describe.",
                    "Its option box holds the deeper updates: <b>Apply "
                    "Source</b> and <b>Rebuild Rig</b>.",
                ],
            )
        )
        ui.b009.setToolTip(
            self.sb.tooltip.fmt(
                title="Delete Rig",
                body="Tears down the rig(s) the selection touches — plane, "
                "group, expression, material and contact locator. The targets "
                "and the source are kept.",
            )
        )

    # -------------------------------------------------------- option boxes
    def cmb_type_init(self, widget):
        """A rig type the panel only plans for is listed but not selectable."""
        model = widget.model()
        for i in range(widget.count()):
            if widget.itemText(i).strip().endswith(self.PLANNED_SUFFIX):
                model.item(i).setEnabled(False)

    def txt_source_init(self, widget):
        """Source Name's option box: the two actions about the source --
        Source From Selection (the pick icon) and Reproject (the refresh
        icon). Idempotent: the switchboard runs an ``_init`` once per
        widget, but a test may run it by hand."""
        if getattr(widget, "_source_actions", None):
            return
        box = widget.option_box
        pick = box.add_action(
            callback=self.source_from_selection,
            icon="select",
            tooltip=self.sb.tooltip.fmt(
                title="Source From Selection",
                body="Uses the selected transform(s) — lights included — as the "
                "shadow source(s), writing their names into Source Name.",
                notes=[
                    "Several selected transforms build one shadow plane each.",
                    "Selected <b>faces</b> are a fixture: a real area light is "
                    "built per shape — the Lighting panel's <i>Lights From "
                    "Geometry</i> — and becomes the source; its size draws the "
                    "shadow's penumbra.",
                    "With the preview running, the previewed targets are "
                    "rebuilt against the new source(s) at once.",
                ],
            ),
        )
        reproject = box.add_action(
            callback=self.reproject_sources,
            icon="refresh",
            tooltip=self.sb.tooltip.fmt(
                title="Reproject",
                body="Re-renders the silhouette of every rig the named "
                "source(s) light, from where the source and the target are "
                "now — the manual form of Follow Source.",
                notes=[
                    "Press it with Follow Source off, or after editing the "
                    "target's geometry, which Follow Source does not watch.",
                    "A Horizon rig's live map follows the light on its own; "
                    "its fallback silhouette is redrawn like the rest.",
                    "<b>Recalculate Silhouette</b> in Utility does the same "
                    "for the rigs the selection touches.",
                ],
            ),
        )
        pick.widget.setObjectName("btn_source_from_selection")
        reproject.widget.setObjectName("btn_reproject")
        widget._source_actions = (pick, reproject)

    def b003_init(self, widget):
        """Recalculate Silhouette's option box: the deeper updates of an
        existing rig — Apply Source and Rebuild Rig."""
        self._add_option_actions(
            widget,
            "Update Rig",
            [
                (
                    "btn_apply_source",
                    "Apply Source",
                    self.apply_source,
                    self.sb.tooltip.fmt(
                        title="Apply Source",
                        body="Re-points the rig(s) the selection touches at the "
                        "first Source Name, re-rendering their silhouettes from "
                        "there.",
                        notes=[
                            "A baked rig has its expression restored first.",
                            "Select the plane, its group, a target, the old "
                            "source, or any of the rig's nodes.",
                        ],
                    ),
                ),
                (
                    "btn_rebuild",
                    "Rebuild Rig",
                    self.rebuild_rig,
                    self.sb.tooltip.fmt(
                        title="Rebuild Rig",
                        body="Re-creates the rig(s) the selection touches from "
                        "the target's current geometry, keeping their targets, "
                        "source and ground, with this panel's Resolution and "
                        "Include Children.",
                        notes=[
                            "The plane keeps its name, so an engine-side join "
                            "survives.",
                        ],
                    ),
                ),
            ],
        )

    def b002_init(self, widget):
        """Bake to Keyframes' option box: Restore Expression, its inverse."""
        self._add_option_actions(
            widget,
            "Bake",
            [
                (
                    "btn_restore",
                    "Restore Expression",
                    self.restore_expression,
                    self.sb.tooltip.fmt(
                        title="Restore Expression",
                        body="Un-bakes the rig(s) the selection touches: removes the "
                        "baked keys and rebuilds the live expression from the rig's "
                        "stamped source and targets.",
                    ),
                ),
            ],
        )

    @staticmethod
    def _add_option_actions(widget, title, actions):
        """Fill *widget*'s option box with push-button *actions* — the rarer,
        deeper operations behind a Utility button — from ``(objectName, text,
        handler, tooltip)`` rows. Idempotent — the menu exposes its items as
        attributes by objectName, so a built menu is detectable: the switchboard
        runs an ``_init`` once per widget, but a test may run it by hand."""
        menu = widget.option_box.menu
        if getattr(menu, actions[0][0], None) is not None:
            return
        menu.setTitle(title)
        for name, text, handler, tooltip in actions:
            button = menu.add(
                "QPushButton", setText=text, setObjectName=name, setToolTip=tooltip
            )
            button.clicked.connect(handler)

    # ------------------------------------------------------------- sources
    def _source_names(self):
        """The source names typed into the panel (comma-separated), or the default."""
        text = self.ui.txt_source.text() or ""
        names = [n.strip() for n in text.replace(";", ",").split(",")]
        names = list(dict.fromkeys(n for n in names if n))
        return names or [ShadowRig.DEFAULT_SOURCE_NAME]

    def _set_source_names(self, names):
        self.ui.txt_source.setText(", ".join(names))

    def _ensure_sources(self):
        """Every named source exists as a transform (missing ones become
        locators at the default position). Runs OUTSIDE the preview contract
        — see :meth:`prepare_operation`."""
        for name in self._source_names():
            ShadowRig.ensure_source(name)

    def _on_sources_edited(self):
        """Source Name edited: create any new name first (outside the
        contract), then refresh the preview against it. ``editingFinished``
        also fires on focus loss, so an unchanged field rebuilds nothing."""
        if self.preview.enabled:
            if self._source_names() == self._built_sources:
                self._sync_softness_box()
                return
            try:
                self._ensure_sources()
            except ValueError as e:
                self.sb.message_box(str(e))
                return
        self._sync_softness_box()
        self.preview.refresh()

    def prepare_operation(self, objects):
        """Preview's one-shot precondition, run at enable outside any
        contract: the source locator(s) exist before the rig is built.

        Built inside ``perform_operation`` the source was a created node of
        every preview pass — each refresh and the commit replay rolled it
        back and recreated it at the default position, discarding wherever
        the user had placed it.
        """
        self._ensure_sources()

    def _rig_builder(self):
        """The engine builder for the ``Rig:`` combo's type.

        Raises:
            OperationError: the type is only planned for (its item is disabled,
                so this is a programmatic selection) — the Preview reports it
                and turns itself off.
        """
        label = self.ui.cmb_type.currentText().split(":", 1)[-1].strip()
        key = label.replace(self.PLANNED_SUFFIX, "").strip()
        builder = self.RIG_BUILDERS.get(key)
        if builder is None:
            raise OperationError(
                f"The {key} rig is not available yet.",
                causes=[
                    "It is a planned rig type — see mayatk/docs/shadow_rig_morphing.md."
                ],
                title="Rig type",
            )
        return builder

    def _resolution(self):
        """The Resolution combo's value (``"Resolution: 512"`` -> 512)."""
        res_text = self.ui.s000.currentText()
        try:
            return int(res_text.replace("Resolution: ", "").strip())
        except (ValueError, AttributeError):
            return 512

    def _per_object(self):
        """True when the ``Planes:`` combo says one rig per selected object."""
        return "per object" in self.ui.cmb_planes.currentText().lower()

    def _atlas_mode(self):
        """The ``Atlas:`` combo's mode (``Auto`` / ``Off`` / ``On``)."""
        label = self.ui.cmb_atlas.currentText().split(":", 1)[-1].strip()
        return label if label in self.ATLAS_MODES else self.ATLAS_MODES[0]

    def _pack_if_wanted(self, rigs):
        """Pack the scene's tiles per the ``Atlas:`` combo: ``On`` always,
        ``Auto`` once the scene holds two rigs (a lone plane gains nothing
        from an atlas), ``Off`` never. Returns the atlas paths. Every rig
        carries a silhouette, so two rigs always fill the projected atlas;
        the horizon atlas takes the horizon rigs among them.

        The whole scene, not just the rigs this build made: there is one atlas
        per rig type, so a second rig has to join the first rather than start
        an atlas of its own — and repacking is what gives every plane a rect
        that agrees with the file.

        Committed rigs only — an atlas is a file the rigs already in the scene
        sample, so packing during the hermetic preview would rewrite theirs
        (and cancelling would take it away with the rehearsal's own files).
        The preview shows the same shadow either way: the tile is reached
        through the plane's own UVs.
        """
        mode = self._atlas_mode()
        planes = ShadowRig.find_shadow_planes()
        if mode == "Off" or not rigs or not planes:
            return {}
        if mode == "Auto" and len(planes) < 2:
            return {}
        return ShadowRig.pack_atlas(planes)

    def _shadow_targets(self, objects, recursive):
        """The selection's shadow casters: mesh-bearing transforms that are
        not one of the named sources — a light or locator in the selection
        is the source, never a target.

        Raises:
            OperationError: nothing in the selection can cast a shadow.
        """
        sources = set()
        for name in self._source_names():
            if cmds.objExists(name):
                sources.update(cmds.ls(name, long=True) or [])
        nodes = [
            str(obj).split(".")[0] for obj in objects or []
        ]  # a component picks its object
        targets, rejected = [], []
        for node in nodes:
            long = (cmds.ls(node, long=True) or [node])[0]
            if long in sources or ShadowRig.source_is_directional(node):
                continue
            if ShadowRig.has_mesh_geometry(node, recursive):
                if long not in targets:
                    targets.append(long)
            else:
                rejected.append(CoreUtils.leaf_name(node))
        if targets:
            return targets
        causes = []
        if rejected:
            causes.append(
                "Not mesh geometry: "
                + ", ".join(rejected[:6])
                + (" …" if len(rejected) > 6 else "")
            )
        if nodes and all(
            (cmds.ls(n, long=True) or [None])[0] in sources for n in nodes
        ):
            causes.append(
                "The selection is the shadow source itself — select the "
                "mesh(es) that cast the shadow."
            )
        if not recursive:
            causes.append(
                "Include Children is off: a group only counts when its "
                "descendant meshes are included."
            )
        raise OperationError(
            "Select the mesh(es) to cast a shadow from.",
            causes=causes,
            title="No shadow targets",
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    # ------------------------------------------------------------- utility
    def _selected_planes(self, action, allow_all=False):
        """The shadow planes the selection touches for a Utility *action*;
        with *allow_all*, every plane when nothing is selected. Reports and
        returns an empty list when there is nothing to act on."""
        sel = cmds.ls(selection=True, long=True) or []
        if not sel:
            if allow_all:
                planes = ShadowRig.find_shadow_planes()
                if not planes:
                    self.sb.message_box("No shadow planes in the scene.")
                return planes
            self.sb.message_box(
                f"Select the shadow plane(s) to {action} — or the rig's group, a "
                "target, or its source."
            )
            return []
        planes = ShadowRig.planes_for_nodes(sel)
        if not planes:
            self.sb.message_box(
                "The selection touches no shadow rig. Select the plane(s) to "
                f"{action}, the rig's group, a target, or its source"
                + (", or clear the selection to act on all." if allow_all else ".")
            )
        return planes

    def chk_follow_init(self, widget):
        """Follow Source arms the engine's watcher from the box's (saved)
        state on every show, so a reopened panel and a restored setting
        agree with what the scene does."""
        widget.refresh_on_show = True
        ShadowRig.auto_recalculate(widget.isChecked())

    def chk_follow(self, checked):
        """Follow Source: re-render a silhouette as soon as its source -- or
        its target -- has moved: the drawn shape is one direction's
        projection, and only the plane's placement followed them before.
        Off leaves Reproject and Recalculate Silhouette as the manual ways."""
        ShadowRig.auto_recalculate(checked)
        if checked:
            done = ShadowRig.recalculate_stale()
            if done:
                self.sb.logger.info(f"Follow Source recalculated {len(done)} plane(s).")

    def s001_init(self, widget):
        """Softness shows the scene's value for the first Source Name -- its
        Softness when set, else the light's physical size -- never a saved
        setting; re-read on every show and whenever the names change."""
        widget.restore_state = False
        widget.refresh_on_show = True
        self._softness_tip = widget.toolTip()
        self._sync_softness_box()

    @CoreUtils.undoable(name="Shadow Rig: Softness", suspend_refresh=True)
    def s001(self, value):
        """Softness: the diameter the shadow gives the source(s) named in
        Source Name (world units; a directional light: degrees of angular
        diameter), set on the source itself so every rig it lights shares
        it, in Unity and the viewer too. A missing source is created, as
        Preview would; the planes it lights are Recalculated at once."""
        planes = set()
        try:
            for name in self._source_names():
                source = ShadowRig.ensure_source(name)
                planes.update(ShadowRig.set_source_softness(source, value))
        except ValueError as e:
            self.sb.message_box(str(e))
            return
        if planes:
            ShadowRig.refresh_silhouette(sorted(planes))

    def _sync_softness_box(self):
        """Put the first named source's effective size in the box, in the
        box's units, with the source and the units on the tooltip."""
        box = self.ui.s001
        name = self._source_names()[0]
        value, units, origin = 0.0, "world units", "no source yet"
        if cmds.objExists(name):
            directional = ShadowRig.source_is_directional(name)
            units = "degrees" if directional else "world units"
            softness = ShadowRig.source_softness(name)
            if softness is not None:
                value, origin = softness, "its Softness"
            else:
                value = ShadowRig.source_size(name)
                if directional:
                    value = math.degrees(value)
                origin = "its physical size"
        box.blockSignals(True)
        try:
            box.setValue(float(value))
        finally:
            box.blockSignals(False)
        box.setToolTip(
            self.sb.tooltip.fmt(
                title="Softness",
                body=f"{getattr(self, '_softness_tip', '')}<br>"
                f"<b>{name}</b>: {value:.3g} {units} ({origin}).",
            )
        )

    def chk_horizon_preview_init(self, widget):
        """The box mirrors the SCENE, never a saved setting: checked while a
        preview stands, enabled only where one can stand -- a Horizon rig in
        the scene and a Viewport 2.0 device that compiles the effect. A
        restored "checked" with nothing attached is what made the box need
        a second toggle before it did anything."""
        widget.restore_state = False  # never read back from QSettings
        widget.refresh_on_show = True  # re-synced every time the panel shows
        self._preview_tip = widget.toolTip()
        self._install_scene_sync()
        self._sync_preview_box()

    def _install_scene_sync(self):
        """Re-sync the box after a scene open / new (a fresh scene has no
        previews; a saved one may carry them). Once per panel instance; the
        subscriptions die with the panel widget."""
        if getattr(self, "_scene_sync_installed", False):
            return
        self._scene_sync_installed = True
        try:
            from mayatk.core_utils.script_job_manager import ScriptJobManager

            mgr = ScriptJobManager.instance()
            for event in ("SceneOpened", "NewSceneOpened"):
                mgr.subscribe(event, self._sync_preview_box, owner=self)
            mgr.connect_cleanup(self.ui, owner=self)
        except RuntimeError:
            pass  # no script jobs here (batch): the show-time sync still runs

    def _sync_preview_box(self):
        """Checked = a preview stands on some horizon plane; enabled = the
        scene has a Horizon rig and this session can compile the effect,
        with the reason on the tooltip when it cannot."""
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        box = self.ui.chk_horizon_preview
        horizon = [
            p
            for p in ShadowRig.find_shadow_planes()
            if ShadowRig.plane_type(p) == "horizon"
        ]
        language, refusal = ShadowPreview.language()
        attached = ShadowPreview.attached_planes() if horizon else []
        if not horizon:
            reason = "No Horizon rig in the scene to preview."
        elif language is None:
            reason = refusal
        else:
            reason = ""
        box.blockSignals(True)
        try:
            box.setChecked(bool(attached))
            box.setEnabled(not reason)
        finally:
            box.blockSignals(False)
        tip = getattr(self, "_preview_tip", box.toolTip())
        box.setToolTip(
            tip
            if not reason
            else self.sb.tooltip.fmt(title="Live Horizon Preview", body=reason)
        )

    def chk_horizon_preview(self, checked):
        """Live Horizon Preview: a Viewport 2.0 shader on the horizon plane(s)
        the selection touches (or all) that evaluates the baked map from the
        live source, so the outline morphs as the light moves -- what Unity
        and the WebXR viewer will show. Display state only: the real material
        stays wired and comes back when the box is cleared, and every preview
        is detached before an FBX export."""
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        planes = self._selected_planes("preview", allow_all=True)
        horizon = [p for p in planes if ShadowRig.plane_type(p) == "horizon"]
        if not horizon:
            if planes:
                self.sb.message_box(
                    "The selection touches no <b>Horizon</b> rig. The live "
                    "preview evaluates a baked horizon map; a Projected rig's "
                    "silhouette already is its preview."
                )
            self._sync_preview_box()
            return
        if checked:
            language, refusal = ShadowPreview.language()
            if language is None:
                self.sb.message_box(refusal)
                self._sync_preview_box()
                return
        done, failed = ShadowPreview.toggle(horizon, on=checked)
        if failed:
            self.sb.message_box(
                self._summary(
                    "Horizon preview " + ("on" if checked else "off"), done, failed
                )
            )
        # The box shows what stands, not what was asked for.
        self._sync_preview_box()

    def b002(self):
        """Bake to Keyframes: bake the rig(s) the selection touches (or all)
        to keys over the playback range and remove the live rig."""
        planes = self._selected_planes("bake", allow_all=True)
        if not planes:
            return
        baked = ShadowRig.bake_planes(planes)
        if baked:
            self.sb.message_box(f"Baked {len(baked)} shadow plane(s) to keyframes.")
        else:
            self.sb.message_box("No shadow planes with a live expression found.")

    def b003(self):
        """Recalculate Silhouette: re-render the rig(s) the selection touches
        (or all) from their source's current position."""
        planes = self._selected_planes("recalculate", allow_all=True)
        if not planes:
            return
        refreshed = ShadowRig.refresh_silhouette(planes)
        if refreshed:
            self.sb.message_box(f"Recalculated {len(refreshed)} silhouette(s).")
        else:
            self.sb.message_box(
                "No shadow planes to recalculate (rigs built before the "
                "target/source stamps must be re-created)."
            )

    def source_from_selection(self):
        """Source From Selection (Source Name's option box): the selected
        transform(s) — lights included
        — become the source(s). Selected FACES are a fixture: a real area
        light is built per shape (``LightUtils.lights_from_geometry``, the
        Lighting panel's Lights From Geometry) and those lights become the
        sources."""
        sel = cmds.ls(selection=True, long=True) or []
        faces = (
            cmds.filterExpand(*sel, selectionMask=34, expand=True) if sel else None
        ) or []
        if faces:
            from mayatk.light_utils._light_utils import LightUtils

            created = LightUtils.lights_from_geometry(faces)
            if not created:
                self.sb.message_box(
                    "No area light could be built from the selected faces."
                )
                return
            self._set_source_names(created)
            self.sb.message_box(
                f"Built {len(created)} area light(s) from the selected faces; "
                "they are now the shadow source(s). Select the target(s) and "
                "enable Preview."
            )
            self._on_sources_edited()
            return
        transforms = cmds.ls(sel, transforms=True, long=True) or []
        # A selected light/shape resolves to its transform.
        for shape in cmds.ls(sel, shapes=True, long=True) or []:
            transforms += cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transforms = list(dict.fromkeys(transforms))
        if not transforms:
            self.sb.message_box(
                "Select the transform(s) to use as shadow source(s) — or a "
                "fixture's faces to build area lights from."
            )
            return
        self._set_source_names(transforms)
        self._on_sources_edited()

    @CoreUtils.undoable(name="Shadow Rig: Reproject", suspend_refresh=True)
    def reproject_sources(self):
        """Reproject (Source Name's option box): re-render the silhouette of
        every plane the named source(s) light, from where the source and the
        target are now -- the manual form of Follow Source, and the one to
        press after a geometry edit it does not watch. A Horizon rig's live
        map follows the light on its own; its fallback silhouette is redrawn
        like the rest."""
        names = self._source_names()
        planes, missing = [], []
        for name in names:
            if not cmds.objExists(name):
                missing.append(name)
                continue
            for plane in ShadowRig.planes_lit_by(name):
                if plane not in planes:
                    planes.append(plane)
        if not planes:
            self.sb.message_box(
                f"No shadow rig is lit by {', '.join(names)}."
                + (f" Missing: {', '.join(missing)}." if missing else "")
                + " Build one with Preview and Create Shadow, or name a "
                "rig's source."
            )
            return
        refreshed = ShadowRig.refresh_silhouette(planes)
        self.sb.message_box(
            f"Reprojected {len(refreshed)} silhouette(s) from {', '.join(names)}."
        )

    @CoreUtils.undoable(name="Shadow Rig: Apply Source", suspend_refresh=True)
    def apply_source(self):
        """Apply Source (Recalculate's option box): re-point the rig(s) the
        selection touches at the first Source Name and re-render their
        silhouettes."""
        planes = self._selected_planes("re-source")
        if not planes:
            return
        name = self._source_names()[0]
        try:
            source = ShadowRig.ensure_source(name)
        except ValueError as e:
            self.sb.message_box(str(e))
            return
        done, failed = [], []
        for plane in planes:
            rig = ShadowRig.from_plane(plane)
            if rig is None:
                failed.append(f"{CoreUtils.leaf_name(plane)}: built before the stamps")
                continue
            try:
                rig.set_source(source, size=self._resolution())
                done.append(CoreUtils.leaf_name(plane))
            except Exception as e:
                failed.append(f"{CoreUtils.leaf_name(plane)}: {e}")
                self.sb.logger.error(f"Apply Source ({plane}): {e}", exc_info=True)
        self.sb.message_box(
            self._summary(f"Source now {CoreUtils.leaf_name(source)}", done, failed)
        )

    @CoreUtils.undoable(name="Shadow Rig: Rebuild", suspend_refresh=True)
    def rebuild_rig(self):
        """Rebuild Rig (Recalculate's option box): re-create the rig(s) the
        selection touches from the target's current geometry with this
        panel's options."""
        planes = self._selected_planes("rebuild")
        if not planes:
            return
        done, failed = [], []
        for plane in planes:
            leaf = CoreUtils.leaf_name(plane)
            try:
                rig = ShadowRig.rebuild(
                    plane,
                    texture_res=self._resolution(),
                    recursive=self.ui.chk_combine.isChecked(),
                )
            except Exception as e:
                failed.append(f"{leaf}: {e}")
                self.sb.logger.error(f"Rebuild ({plane}): {e}", exc_info=True)
                continue
            if rig is None:
                failed.append(f"{leaf}: built before the stamps, or its nodes are gone")
            else:
                done.append(leaf)
        self._sync_preview_box()
        self.sb.message_box(self._summary("Rebuilt", done, failed))

    @CoreUtils.undoable(name="Shadow Rig: Restore Expression", suspend_refresh=True)
    def restore_expression(self):
        """Restore Expression (Bake's option box): un-bake the rig(s) the
        selection touches."""
        planes = self._selected_planes("restore")
        if not planes:
            return
        restored = ShadowRig.unbake_planes(planes)
        if restored:
            self.sb.message_box(f"Restored the expression on {len(restored)} plane(s).")
        else:
            self.sb.message_box(
                "Nothing to restore: the selected rig(s) are live already, or "
                "predate the target/source stamps."
            )

    @CoreUtils.undoable(name="Shadow Rig: Delete", suspend_refresh=True)
    def b009(self):
        """Delete Rig: tear down the rig(s) the selection touches."""
        planes = self._selected_planes("delete")
        if not planes:
            return
        deleted = ShadowRig.delete_rigs(planes)
        self._sync_preview_box()
        self.sb.message_box(f"Deleted {len(deleted)} shadow rig(s).")

    @CoreUtils.undoable(name="Shadow Rig: Pack Atlas", suspend_refresh=True)
    def b010(self):
        """Pack Atlas: pack or repack every shadow rig's tiles into the
        scene's atlases (one per kind)."""
        planes = ShadowRig.find_shadow_planes()
        if not planes:
            self.sb.message_box("No shadow planes in the scene.")
            return
        packed = ShadowRig.pack_atlas(planes)
        if packed:
            names = ", ".join(os.path.basename(p) for p in packed.values())
            self.sb.message_box(f"Packed {len(planes)} plane(s) into {names}.")
        else:
            self.sb.message_box("No tiles to pack — the planes' PNGs are missing.")

    @staticmethod
    def _summary(what, done, failed):
        lines = []
        if done:
            lines.append(f"{what}: {', '.join(done)}")
        if failed:
            lines.append("Failed:\n  " + "\n  ".join(failed))
        return "\n".join(lines) or "Nothing done."

    def _preview_new_horizon(self, rigs):
        """A committed Horizon rig shows its live preview at once, where the
        device can compile it: the morphing outline IS the rig, and a
        horizon plane without its preview is indistinguishable from a
        Projected one. A refusal only informs (the box's tooltip carries
        the reason); the box mirrors what stands either way."""
        from mayatk.rig_utils.shadow_preview import ShadowPreview

        planes = [
            rig.shadow_plane
            for rig in rigs
            if rig.rig_type == "horizon" and rig.shadow_plane
        ]
        if not planes:
            return
        language, refusal = ShadowPreview.language()
        if language is None:
            self.sb.logger.info(f"Horizon preview not shown: {refusal}")
            return
        done, failed = ShadowPreview.toggle(planes, on=True)
        if failed:
            self.sb.logger.warning(self._summary("Horizon preview on", done, failed))

    def perform_operation(self, objects, contract):
        """Build one shadow rig per source for the given targets.

        Called by Preview during the hermetic preview phase (contract is a
        CleanupContract) and again during commit (contract is None).
        """
        recursive = self.ui.chk_combine.isChecked()
        targets = self._shadow_targets(objects, recursive)

        if contract is not None:
            # create() republishes shadow_metadata on the data_export carrier.
            # A brand-new carrier is rolled back as a created node, but a
            # PRE-EXISTING one (other producers' channels) only gets its attr
            # mutated — snapshot it so canceling the preview can't leave a
            # stale channel behind.
            from mayatk.node_utils.data_nodes import DataNodes

            if cmds.objExists(DataNodes.EXPORT):
                contract.record_modification(
                    DataNodes.EXPORT, ShadowRig.SHADOW_METADATA
                )

        names = self._source_names()
        builder = self._rig_builder()
        groups = [[t] for t in targets] if self._per_object() else [targets]
        rigs = []
        for group in groups:
            rigs.extend(
                builder(
                    group, names, texture_res=self._resolution(), recursive=recursive
                )
            )
        self._built_sources = names
        if contract is None:
            self._pack_if_wanted(rigs)
            self._preview_new_horizon(rigs)
            # A committed Horizon rig is something the preview box can act on.
            self._sync_preview_box()
        else:
            for rig in rigs:
                for path in (rig.texture_path, rig.horizon_path):
                    if path:
                        contract.add_file(path)


if __name__ == "__main__":
    sel = cmds.ls(selection=True) or []
    if not sel:
        print("Select object(s) first.")
    else:
        ShadowRig.create(sel)
