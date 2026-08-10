# !/usr/bin/python
# coding=utf-8
"""Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.

The Maya half of the Maya<->Blender object hand-off (``mtk.BlenderBridge`` <-> ``btk.MayaBridge``).
A thin :class:`pythontk.ScriptLaunchBridge` subclass: the shared ``send()`` skeleton (resolve ->
preflight -> produce payload -> deliver), the template discovery / ``BRIDGE_MODES`` / ``__KEY__``
substitution machinery, and the render-script-then-launch-a-fresh-app deliverer all live upstream in
:mod:`pythontk.core_utils.app_handoff`. The Maya-side selection + FBX export come from
:class:`mayatk.env_utils.handoff_export.MayaExportMixin` (shared with the Unity bridge). This file
owns only the Blender-specific bits, declared as a :class:`pythontk.ScriptLaunchSpec` dataclass
(executable discovery + the ``--python`` launch args) plus the parameter bindings.

Picking a different template is the "dynamic script selection". Two user-visible recipes ship --
``import`` (a single options-driven script whose ``CLEAR_SCENE`` / ``FRAME_VIEW`` booleans cover
what used to be three near-identical templates) and ``bake_lightmaps`` -- plus any extra
``templates/*.py`` the user drops in, discovered the same way.

Three delivery *modes* ride the one export pipeline (:attr:`spec` / :attr:`run_spec`, dispatched by
``HandoffBridge.deliverers``, named by ``pythontk.core_utils.script_template``'s ecosystem-wide
constants): ``send_to`` launches an interactive Blender on the ``import`` template, while
``save_as`` and ``round_trip`` both run Blender headlessly and wait for an artifact. Those two
share a spec and a deliverer because the mechanics are identical -- what differs is where the
artifact goes. ``save_as`` (:meth:`~pythontk.ScriptLaunchBridge.save_as`) hands the user a written
``.blend`` (``templates/_save_scene.py``); ``round_trip``
(:meth:`~pythontk.ScriptLaunchBridge.round_trip`, wrapped as :meth:`BlenderBridge.bake_lightmaps`)
hands ``templates/bake_lightmaps.py``'s manifest to :meth:`~BlenderBridge._ingest`, which folds it
into this scene and leaves no deliverable at all. Same FBX, same material sidecar, no second export
path.

**The bake is a round trip, and it stops at the scene.** It sends the selection, bakes in a
headless Blender through blendertk's ``LightmapBaker``, and brings the maps + their UV layouts
home -- committing them alongside the existing materials. It produces no deliverable and targets
no platform: FBX -> Unity and GLB -> web (``ptk.MeshConvert.apply_glb_lightmaps``) each read that
committed state on their own, with no knowledge of the bake; the Maya viewport deliberately shows
nothing (the commit builds no file node). Instances are first-class -- each copy is baked
separately and its atlas rect rides per TRANSFORM as the engine's ``lightmapScaleOffset`` binding,
so the scene's instancing survives untouched. Co-located with its panel
(``blender_bridge_slots.BlenderBridgeSlots`` + ``blender_bridge.ui``) under ``env_utils``;
discovered by :class:`mayatk.ui_utils.MayaUiHandler`. ``import maya.cmds`` is deferred so resolving
the package surface never needs a running Maya.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils import script_template as _templates
from pythontk.core_utils.script_template import ROUND_TRIP, SAVE_AS, SEND_TO

from mayatk.env_utils.handoff_export import MayaExportMixin


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


# Parameter defaults, declared HERE (Qt-free) rather than inside the widget specs:
# ``parameters.py`` reads them for its ``AttributeSpec`` defaults, so there is one source
# of truth, and the engine can still answer ``params_defaults()`` where the panel's Qt
# stack is unavailable -- a headless ``blender --background`` / ``mayapy`` calling
# ``save_as`` must not need a UI toolkit to know that materials default to on.
DEFAULTS: Dict[str, Any] = {
    "SCOPE": "selected",
    "INCLUDE_MATERIALS": True,
    "EMBED_TEXTURES": True,
    "APPLY_UNIT_SCALE": True,
    "INCLUDE_ANIMATION": False,
    "INCLUDE_LIGHTS": True,
    "TRIANGULATE": False,
    "CLEAR_SCENE": False,
    "FRAME_VIEW": False,
    # --- lightmap bake (templates/bake_lightmaps.py) ------------------------------
    # Only shown by the panel when the selected template references them, so they cost
    # the plain import recipe nothing. Quality is a named preset (blendertk's
    # LightmapBaker.preset_store); RESOLUTION/SAMPLES are API-level overrides on top of
    # it -- 0 means "use the preset", and neither is a panel widget.
    "LIGHTMAP_QUALITY": "quest",
    "LIGHTMAP_RESOLUTION": 0,
    "LIGHTMAP_SAMPLES": 0,
    "LIGHTMAP_DENOISE": True,
    "LIGHTMAP_DEVICE": "GPU",
    "ENVIRONMENT_HDR": "",
    "WORLD_STRENGTH": 0.35,
    "EMISSION_STRENGTH": 2.0,
    "SCENE_LIGHT_STRENGTH": 1.0,
    "LIGHTMAP_DIR": "",
}


# Declarative Blender hand-off config (target discovery + the ``--python`` launch args). Blender
# runs the rendered template on startup, detached, as an interactive GUI (NOT ``--background``): it
# opens for the artist and Maya returns control immediately. A FRESH instance every time
# (session-safety rule).
_SPEC = ptk.ScriptLaunchSpec(
    # ``$BLENDER_EXE`` / ``$BLENDER`` -> ``AppLauncher.find_app`` -> a scan of
    # ``Program Files\\Blender Foundation\\Blender *`` (highest version wins).
    app=ptk.AppSpec(
        name="Blender",
        env_vars=("BLENDER_EXE", "BLENDER"),
        app_names=("blender",),
        scan_globs=(r"{program_files}\Blender Foundation\Blender *\blender.exe",),
        not_found_msg=(
            "Blender executable not found. Install Blender or set $BLENDER_EXE / "
            "BlenderBridge.blender_path."
        ),
    ),
    template_dir=_TEMPLATE_DIR,
    launch_args=lambda script_path: ["--python", script_path],
    # The spec default, stated because it is load-bearing rather than incidental:
    # ``template_modes_allowed`` derives from it, and its FIRST entry is what an
    # unrecognized declaration falls back to.
    modes=(SEND_TO,),
    payload_prefix="mtk_to_blender",
    # The launched Blender inherits Maya's whole environment; an OCIO var pointing
    # inside Maya's own install would override Blender's color management with a
    # config authored for Maya. Strip exactly that case (mirror of MayaBridge's).
    launch_env=lambda: ptk.AppLauncher.handoff_env(os.environ.get("MAYA_LOCATION")),
)


# The BLOCKING route (``save_as``): the same Blender binary, run headlessly on the same
# rendered-template contract, with the caller waiting for the artifact. ``--factory-startup``
# keeps the user's addons/config out of a file they will open later -- the same reasoning
# the pull-direction conversion uses (``_scene_import._LAUNCH_ARGS``).
_RUN_SPEC = ptk.ScriptLaunchSpec(
    app=_SPEC.app,
    template_dir=_TEMPLATE_DIR,
    launch_args=lambda script_path: [
        "--background",
        "--factory-startup",
        "--python",
        script_path,
    ],
    # BOTH blocking modes, because they are mechanically identical -- run headlessly,
    # write ONE artifact, wait for it. They differ only in where that artifact goes:
    # ``save_as`` hands the user a ``.blend``; ``round_trip`` hands the bake's manifest to
    # ``_ingest``, which folds it into this scene and leaves no deliverable behind. One
    # spec, one deliverer; declaring a second would only duplicate the launch args.
    modes=(SAVE_AS, ROUND_TRIP),
    launch_env=_SPEC.launch_env,
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.


class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge):
    """Export the Maya selection and run a chosen Blender import template.

    Named after its target app (``BlenderBridge``), mirroring ``MarmosetBridge``; the Blender-side
    counterpart is ``blendertk.MayaBridge``. All Blender-specific config is the :data:`_SPEC` /
    :data:`_RUN_SPEC` dataclasses; this class adds only the Maya parameter bindings.

    Three ways out::

        bridge.send(objects)                     # -> a fresh interactive Blender
        bridge.save_as("C:/out/asset.blend")     # -> a .blend on disk (blocking, headless)
        bridge.bake_lightmaps()                  # -> this scene, lit (blocking, headless)

    ``save_as`` is Maya's "export to Blender's native format": no Blender window, no manual
    import step, and ``objects=None`` means the whole scene rather than the selection.
    :meth:`bake_lightmaps` is the ``round_trip`` mode -- the same headless run, but its
    artifact is an intermediate :meth:`_ingest` folds back into the Maya scene, so what the
    artist gets is committed scene state rather than a file.
    """

    spec = _SPEC
    run_spec = _RUN_SPEC
    # ``save_as`` writes Blender's native scene format (a bare path gets ".blend");
    # ``.json`` is the bake round trip's return manifest. Its artifact is scene
    # state, not a mesh file -- GLB and every other deliverable come from the
    # exporters, which read the committed bake on their own.
    save_extensions = (".blend", ".json")

    def __init__(self, blender_path: Optional[str] = None):
        super().__init__(app_path=blender_path)

    # Back-compat alias: existing callers / tests use ``.blender_path``.
    @property
    def blender_path(self) -> Optional[str]:
        return self.app_path

    @blender_path.setter
    def blender_path(self, value: Optional[str]) -> None:
        self.app_path = value

    # ------------------------------------------------------------------ parameter bindings
    def params_defaults(self) -> Dict[str, Any]:
        # DEFAULTS is the render-context SSoT; the widget specs are the PANEL SUBSET of
        # it. API-only parameters (the bake's resolution/samples/denoise/device
        # overrides, which the Quality preset covers for artists) live in DEFAULTS with
        # no widget, so they still substitute into every template. The overlay keeps a
        # spec-side default authoritative for keys that have a widget.
        try:
            from mayatk.env_utils.blender_bridge import parameters as _params
        except ImportError:  # no Qt -- see DEFAULTS
            return dict(DEFAULTS)
        return {**DEFAULTS, **_params.Parameters.defaults()}

    def render_context(self, params: Dict[str, Any]) -> Dict[str, str]:
        try:
            from mayatk.env_utils.blender_bridge import parameters as _params

            context = _params.Parameters.render_context(params)
        except ImportError:  # no Qt -- pythontk's plain Python-literal formatting
            context = super().render_context(params)
        # Blender ignores PYTHONPATH, so the launched child cannot import blendertk
        # on its own and the template's material rebuild would silently fall through
        # to "blendertk unavailable" -- i.e. exactly the unshaded-mesh bug the
        # manifest exists to fix. Thread in just the two roots it needs (never the
        # parent's whole sys.path: Maya's 3.11 site-packages would shadow Blender's
        # 3.13 stdlib). Not a user-facing PARAM, so it rides the render context
        # alongside FBX_PATH.
        context["EXTRA_SYS_PATH"] = repr(self.import_roots("blendertk", "pythontk"))
        return context

    @staticmethod
    def _instanced_shapes(objects) -> Dict[str, List[str]]:
        """``{shape: [instance transform, ...]}`` for shapes in *objects* worn more than once.

        Deduped by the shape's instance GROUP, not by its path. An instanced shape has one
        full DAG path *per instance* (``|wall_a|wallShape`` and ``|wall_b|wallShape`` are
        the same node), so keying on the path counts one shared wall 24 times -- the same
        trap ``NodeUtils.filter_duplicate_instances`` sidesteps, and the sorted parent
        tuple is stable whichever path it is reached through.
        """
        import maya.cmds as cmds

        from mayatk.node_utils._node_utils import NodeUtils

        out: Dict[str, List[str]] = {}
        seen: set = set()
        for obj in objects or []:
            for shape in NodeUtils.get_instanced_shapes(str(obj)) or []:
                parents = (
                    cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                )
                key = tuple(sorted(parents))
                if key in seen:
                    continue
                seen.add(key)
                out[shape] = parents
        return out

    # ------------------------------------------------------------------ payload
    def _produce(self, objects, request):
        """Export the FBX (via the mixin), then sidecar the texture manifest.

        The FBX is NOT lossy here. Probed end-to-end (Maya 2025 -> Blender 5.1):
        Maya writes StingrayPBS texture bindings as ``Maya|TEX_color_map`` /
        ``Maya|TEX_normal_map`` via ``FbxImplementation``/``FbxBindingTable``, and
        Blender's importer READS them and then discards them by design -- it logs
        ``WARNING: material link b'Maya|TEX_color_map' ignored`` and wires only the
        native Lambert/Phong slots (a plain lambert's ``DiffuseColor`` does arrive).
        So the bindings reach Blender and are dropped before Python can see them:
        the imported material carries no custom properties and no image nodes,
        which is why a real production selection lands unshaded. Recovering them
        would mean parsing the FBX independently. The manifest carries
        each textured material's ORIGINAL image files; the Blender-side
        ``import`` template replays it through blendertk's existing
        ``MayaSceneImport._apply_texture_manifest`` -- the same sidecar contract
        the pull direction has always used, and the exact mirror of
        ``btk.MayaBridge._produce``. Best-effort: a manifest failure must never
        cost the user the send itself.
        """
        payload = super()._produce(objects, request)
        try:
            self._write_manifest(
                objects,
                payload.primary,
                include_materials=bool(request.params.get("INCLUDE_MATERIALS", True)),
                include_lights=bool(
                    request.params.get("INCLUDE_LIGHTS", DEFAULTS["INCLUDE_LIGHTS"])
                ),
            )
        except Exception:  # noqa: BLE001
            self.logger.warning(
                "Manifest sidecar failed; Blender keeps the FBX-carried "
                "materials.",
                exc_info=True,
            )
        return payload

    def _write_manifest(
        self,
        objects,
        fbx_path: str,
        include_materials: bool = True,
        include_lights: bool = True,
    ) -> None:
        """Write ``<fbx>.manifest.json`` for *objects* (no-op when there is nothing to say).

        Same schema as the pull direction's collector in
        ``blendertk/env_utils/maya_bridge/templates/_import_scene.py`` (kept in
        step by hand -- that copy is dependency-free by template contract and
        runs the whole scene; this one is in-process and scoped to the exported
        set): one entry per material with its resolved image files, plus
        ``scene_materials`` naming EVERY material on the set so the Blender
        side's rename-on-clash matching can't claim an untextured sibling, plus
        ``transforms`` -- ``{leaf name: "locator" | "group"}`` for the shapeless
        and locator transforms in the set. Maya's exporter writes both as
        identical FBX nulls; the Blender side stamps the type as a
        ``maya_node_type`` custom property on the empties it creates, so a
        send BACK restores each one as the correct Maya node type instead of
        guessing from the children heuristic.

        The walk covers each seed's whole SUBTREE: Maya's export-selection
        ships descendants, so the manifest must describe the same set -- a
        group send must carry its descendant meshes' materials and its nested
        groups/locators.

        Texture resolution reuses :meth:`MatManifest.build`, which reads each
        shader's DECLARED slots through the ``ShaderAttributeMap`` SSoT instead
        of walking history -- history drags a Stingray network's env/BRDF
        plumbing in alongside the real maps. A material that resolves no
        textures is dropped: a flat color rides the FBX fine and needs no
        rebuild.
        """
        import maya.cmds as cmds

        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.mat_manifest import MatManifest

        seeds = cmds.ls([str(o) for o in objects], long=True) or []
        if not seeds:
            return
        transforms = list(seeds)
        seen = set(seeds)
        for seed in seeds:
            for descendant in (
                cmds.listRelatives(
                    seed, allDescendents=True, type="transform", fullPath=True
                )
                or []
            ):
                if descendant not in seen:
                    seen.add(descendant)
                    transforms.append(descendant)

        node_types = self._manifest_node_types(transforms)
        lights = self._manifest_lights(transforms) if include_lights else []
        if not include_materials:
            self._dump_manifest(fbx_path, [], [], node_types, lights)
            return

        slots_by_mat = MatManifest.build(transforms).get("materials", {})

        def _leaf(name: str) -> str:
            """Short, namespace-free name -- what FBX writes and Blender sees."""
            return str(name).split("|")[-1].split(":")[-1]

        scene_materials: List[str] = []
        objects_by_mat: Dict[str, List[str]] = {}
        for transform in transforms:
            for mat in MatUtils.get_mats([transform], as_strings=True) or []:
                name = _leaf(mat)
                if name not in scene_materials:
                    scene_materials.append(name)
                members = objects_by_mat.setdefault(name, [])
                if _leaf(transform) not in members:
                    members.append(_leaf(transform))

        entries: List[Dict[str, Any]] = []
        for mat_name, slots in slots_by_mat.items():
            name = _leaf(mat_name)
            # dict.fromkeys de-dupes while preserving slot order (one file can
            # feed several slots, e.g. a packed ORM).
            files = [p for p in dict.fromkeys((slots or {}).values()) if p]
            entries.append(
                {
                    "name": name,
                    "shader_type": "maya",
                    "fbx_material": name,
                    "objects": objects_by_mat.get(name, []),
                    "files": files,
                    # The logical channel each file was read from, kept ALONGSIDE
                    # (not instead of) the flat list. The Blender side classifies
                    # by filename first -- only a filename reveals packing like
                    # MSAO/ORM -- and consults this for files that classify to
                    # nothing, e.g. a plain color map named after a product
                    # ("Agilent_PNA.png"), which would otherwise be unrebuildable
                    # even though Maya knew it was the baseColor.
                    "slots": {ch: p for ch, p in (slots or {}).items() if p},
                }
            )
        self._dump_manifest(fbx_path, entries, scene_materials, node_types, lights)

    @staticmethod
    def _manifest_node_types(transforms: List[str]) -> Dict[str, str]:
        """``{leaf name: "locator" | "group"}`` for the node-type sidecar.

        A shapeless transform is a group; one whose shapes are all locators is
        a locator. Meshes (and anything else with real shapes) describe
        themselves in the FBX and are skipped. A leaf name claimed by BOTH
        kinds is dropped -- a wrong tag is worse than the children heuristic.
        """
        import maya.cmds as cmds

        out: Dict[str, str] = {}
        ambiguous = set()
        for transform in transforms:
            if cmds.nodeType(transform) != "transform":
                continue
            shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
            if not shapes:
                node_type = "group"
            elif all(cmds.nodeType(s) == "locator" for s in shapes):
                node_type = "locator"
            else:
                continue
            leaf = transform.split("|")[-1].split(":")[-1]
            if out.get(leaf, node_type) != node_type:
                ambiguous.add(leaf)
            out[leaf] = node_type
        for leaf in ambiguous:
            del out[leaf]
        return out

    #: Maya light node type -> Blender light type. Ambient and volume lights have no
    #: Cycles equivalent and are reported rather than silently approximated.
    LIGHT_TYPES: Dict[str, str] = {
        "pointLight": "POINT",
        "spotLight": "SPOT",
        "directionalLight": "SUN",
        "areaLight": "AREA",
        "aiAreaLight": "AREA",
    }
    #: Watts per unit of Maya light intensity for the local light types.
    #:
    #: An anchoring CONVENTION, not a derivation -- Maya intensity is unitless, so no
    #: exact conversion exists. 1000 W per unit puts a default (intensity 1.0) Maya
    #: light in the same range as Blender's own default point light, which makes the
    #: first bake land somewhere usable; ``SCENE_LIGHT_STRENGTH`` is the dial from
    #: there, and the bake log prints the resulting wattage so it is tuned from a
    #: number. A SUN is exempt: Maya's directional intensity and Blender's sun
    #: irradiance are both "1.0 = full", so that one maps 1:1.
    WATTS_PER_INTENSITY: float = 1000.0

    def _manifest_lights(self, transforms: List[str]) -> List[Dict[str, Any]]:
        """Light parameters for the sidecar -- ``[{name, type, color, energy, ...}]``.

        **Why the sidecar and not the FBX.** The light OBJECT deliberately never
        travels: Blender 5.1's bundled importer sets ``lamp.cycles.cast_shadow``,
        which Cycles 5.x removed, so a single light in the FBX raises inside
        ``IMPORT_SCENE_OT_fbx.execute`` and aborts the ENTIRE import -- geometry and
        all (measured on 5.1.2). Its *transform* does still ship as a null, though
        (probe-measured), so the FBX keeps doing the placement it is good at --
        including the Y-up/cm to Z-up/m conversion -- and this carries only what FBX
        drops. That is the same division of labour the material section already uses.

        It also buys what FBX could never do: ``aiAreaLight`` is a plugin node type
        FBX cannot represent at all, and it is read here like any other.

        Keyed by leaf name, matching the ``transforms`` section, because that is what
        Blender sees on the empty it creates.

        Selection-scoped BY DESIGN: only lights under the sent roots are recorded,
        exactly like meshes -- a bake's lighting is part of what the artist sends,
        so lights that should ride a module belong parented inside it.
        """
        import maya.cmds as cmds

        lights: List[Dict[str, Any]] = []
        for transform in transforms:
            for shape in cmds.listRelatives(transform, shapes=True, fullPath=True) or []:
                blender_type = self.LIGHT_TYPES.get(cmds.nodeType(shape))
                if blender_type is None:
                    continue
                intensity = cmds.getAttr(f"{shape}.intensity")
                # Arnold lights carry a separate EXPOSURE in stops, multiplying
                # intensity by 2**exposure -- it is where Arnold users put most of
                # their range, so reading intensity alone can be off by orders of
                # magnitude (exposure 5 is 32x). Attribute-probed rather than keyed
                # off the node type, so any other light that adopts the convention
                # is picked up too.
                if cmds.attributeQuery("exposure", node=shape, exists=True):
                    intensity = float(intensity) * (
                        2.0 ** float(cmds.getAttr(f"{shape}.exposure"))
                    )
                record: Dict[str, Any] = {
                    "name": transform.split("|")[-1].split(":")[-1],
                    "type": blender_type,
                    "color": list(cmds.getAttr(f"{shape}.color")[0]),
                    "energy": float(intensity)
                    * (1.0 if blender_type == "SUN" else self.WATTS_PER_INTENSITY),
                }
                # AIM travels here even though POSITION does not need to: measured,
                # the light transform's rotation does NOT survive the crossing. Maya's
                # exporter reconciles light nodes against FBX's own light-axis
                # convention, so the null that arrives carries only the importer's
                # Y-up -> Z-up rotation -- identical to a mesh that was never rotated
                # at all (a spot aimed straight down came out aiming sideways, and the
                # bake was black with nothing to say why). Translation is unaffected,
                # so the empty is still the source of position.
                #
                # A Maya light aims down its local -Z: that is world-space row 2 of
                # the transform's matrix, negated. Sent in MAYA axes and converted on
                # arrival, so the axis convention is stated once, on the side that
                # knows both.
                matrix = cmds.xform(
                    transform, query=True, matrix=True, worldSpace=True
                )
                record["aim"] = [-matrix[8], -matrix[9], -matrix[10]]
                record["axis_up"] = "Y"
                if blender_type == "SPOT":
                    # Maya cone angle is the FULL angle in degrees, and Blender's
                    # spot_size is the full angle in radians -- so this is a unit
                    # change, not a half-angle conversion.
                    record["spot_size"] = math.radians(
                        float(cmds.getAttr(f"{shape}.coneAngle"))
                    )
                    penumbra = float(cmds.getAttr(f"{shape}.penumbraAngle"))
                    cone = float(cmds.getAttr(f"{shape}.coneAngle")) or 1.0
                    # Blender blends INWARD from the edge as a 0-1 fraction of the
                    # cone; Maya's penumbra is an angle outside it (and may be
                    # negative, which softens inward). Magnitude over the cone is the
                    # closest honest mapping.
                    record["spot_blend"] = max(0.0, min(1.0, abs(penumbra) / cone))
                elif blender_type == "AREA":
                    # Maya's area light is a 2x2 square in the transform's LOCAL space;
                    # Blender sizes the light datablock in world units instead. Ship
                    # the local extent and let the Blender side scale it by the empty
                    # the FBX placed -- that empty already carries whatever scale and
                    # cm-to-m conversion the import applied to the geometry, so the
                    # light cannot end up in different units from the room it lights.
                    record["shape"] = "RECTANGLE"
                    record["local_size"] = [2.0, 2.0]
                lights.append(record)
        return lights

    def _dump_manifest(
        self,
        fbx_path: str,
        entries: List[Dict[str, Any]],
        scene_materials: List[str],
        node_types: Dict[str, str],
        lights: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Write the sidecar (shared by the materials-on and materials-off paths)."""
        import json

        lights = lights or []
        if not entries and not node_types and not lights:
            return
        with open(fbx_path + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": entries,
                    "scene_materials": scene_materials,
                    "transforms": node_types,
                    "lights": lights,
                },
                fh,
                indent=1,
            )
        self.logger.info(
            f"Manifest: {len(entries)} textured material(s), "
            f"{len(node_types)} group/locator transform(s), "
            f"{len(lights)} light(s) sidecarred."
        )

    # ------------------------------------------------------------------ lightmap bake
    def bake_lightmaps(
        self,
        out: Optional[str] = None,
        objects: Optional[List[Any]] = None,
        *,
        environment_hdr: Optional[str] = None,
        quality: Optional[str] = None,
        resolution: Optional[int] = None,
        samples: Optional[int] = None,
        scene_lights: Optional[bool] = None,
        light_strength: Optional[float] = None,
        timeout: Optional[float] = None,
        reassemble: bool = True,
        **params: Any,
    ) -> Optional[Dict[str, Any]]:
        """Bake the selection's lightmaps in a headless Blender; the scene comes back lit.

        The send-to-bake operation: named convenience over
        :meth:`~pythontk.ScriptLaunchBridge.round_trip` with the ``bake_lightmaps``
        template -- the same FBX export pipeline every other hand-off uses. Blocking;
        judged by the written return manifest (the maps + the UV layouts they were baked
        through), which is then consumed rather than kept.

        **It returns scene state, not a deliverable.** Blender owns the lightmap job end
        to end (UV generation, packing, baking, atlasing); on return *reassemble* writes
        the layout onto the Maya meshes and records the maps **alongside the existing
        materials** -- full PBR material and UV0 untouched, lightmap on UV1, undone by the
        same ``revert_lightmap`` the Maya-native bakes use. After that, the bake targets no
        platform: FBX -> Unity and GLB -> web each read the committed state on their own
        (see ``LightmapBaker.commit_lightmap`` / ``ptk.MeshConvert.apply_glb_lightmaps``).
        The Maya **viewport shows nothing** -- deliberately, since the commit builds no
        file node and leaves the material untouched; the scene gains a lightmap UV set on
        channel 1 and a marker per transform, and the map is inspected on disk or in a
        viewer. To see it lit, click **WebXR Preview** (Rendering panel): a fresh export carries the
        just-committed ``lightmap_metadata``, so that button self-feeds (FBX -> GLB ->
        ``apply_glb_lightmaps`` -> the viewer binds the maps) with no coupling back to
        this operation.

        **Instances are first-class**: every copy is baked separately and its atlas
        rect is committed per TRANSFORM as the engine's ``lightmapScaleOffset``
        binding, so the shared shape keeps one shared unwrap and the scene's
        instancing survives untouched.

        Cycles rather than mayatk's Arnold ``LightmapBaker``: it bakes white-card
        irradiance natively, needs no licence, denoises, and runs on the GPU. Pick one
        per project -- the two will not match visually.

        **Lighting: the sent selection's lights are used by default.** *scene_lights*
        (on) brings the Maya lights inside the sent hierarchy across and bakes with
        them, rebalanced by *light_strength* because Maya intensity is unitless --
        the returned summary reports each light's final wattage so that dial is
        tuned from a number. A light grouped outside the sent hierarchy does not
        travel, exactly like a mesh outside it. The lights that cross
        ride the manifest rather than the FBX (see :meth:`_manifest_lights`), which is
        also what lets an ``aiAreaLight`` come across at all. Compose with
        *environment_hdr* freely. Ambient/volume lights and a StingrayPBS IBL cannot
        come across -- a scene lit only by those needs an HDRI or real lights, and
        says so in the log rather than baking silently black. Turning light-fixture
        GEOMETRY into lights is an authoring step, not a bake option:
        :meth:`mayatk.LightUtils.lights_from_geometry` (tentacle Lighting panel)
        builds ordinary scene lights from it, which then cross here like any other.
        See the template's docstring for why an emissive map is deliberately not
        treated as a light source.

        Example::

            mtk.BlenderBridge().bake_lightmaps(
                environment_hdr="O:/hdri/unfinished_office.hdr",
                quality="desktop",
            )

        Parameters:
            out: Where the return manifest is written. ``None`` derives it
                (:meth:`default_output_path`) -- the manifest is pipeline plumbing, not a
                deliverable anyone names.
            quality: Preset tier (blendertk's ``LightmapBaker.preset_store``:
                ``preview`` / ``quest`` / ``desktop`` / ``hero``). *resolution* /
                *samples* override the preset when given.
            scene_lights: Bring the sent selection's Maya lights across and bake
                with them (default on; selection-scoped like every other node --
                they ride the manifest, not the FBX, see ``_manifest_lights``).
                ``False`` for a bake lit purely by *environment_hdr*.
            light_strength: Multiplier on the imported lights' power (default 1.0).
                The units do not survive the crossing intact; this is the one dial.

        Returns the deliverer result (``output`` / ``duration`` / ``returncode``), with
        ``reassembled`` (``{object: map}``) added when the return leg ran, or ``None`` on
        a handled failure.
        """
        for key, value in (
            ("ENVIRONMENT_HDR", environment_hdr),
            ("LIGHTMAP_QUALITY", quality),
            ("LIGHTMAP_RESOLUTION", resolution),
            ("LIGHTMAP_SAMPLES", samples),
            ("INCLUDE_LIGHTS", scene_lights),
            ("SCENE_LIGHT_STRENGTH", light_strength),
        ):
            if value is not None:
                params.setdefault(key, value)
        # Resolve the export set ONCE and hand the same list to both legs. save_as
        # defaults to the WHOLE SCENE rather than the selection, so leaving this None
        # would export one set and then reassemble against an empty one: every baked
        # object would come back "unmatched" and nothing would be wired in -- silently,
        # since the artifact still lands and the run still reports success. Normalized
        # the same way the export will be, so the return leg indexes the node names FBX
        # actually carried (shapes would index shape leaf names and match nothing).
        objects = (
            self._scene_objects() if objects is None else self._resolve_objects(objects)
        )

        params.setdefault("LIGHTMAP_DIR", self._default_lightmap_dir(objects))
        template = "bake_lightmaps"
        if out is None:
            out = self.default_output_path(template)
        if timeout is None:
            # The spec's 600s default would kill a real bake around the 10-minute mark
            # with no artifact written; the template declares its own budget.
            timeout = self.template_timeout(self.template_path(template))
        # The return leg lives in _ingest (the HandoffBridge hook _run calls after
        # deliver), so the panel and this API share one implementation; ``reassemble``
        # rides the request extras to opt out.
        #
        # ``round_trip``, not ``save_as``: mechanically the same blocking headless run
        # (same spec, same deliverer, same ``out``), but the mode is what the artist is
        # told this operation IS -- the manifest is plumbing that _ingest consumes, and
        # naming the mechanism would send them looking for a deliverable that is
        # deliberately never produced.
        return self.round_trip(
            objects,
            template=template,
            params=params,
            timeout=timeout,
            out=out,
            reassemble=reassemble,
        )

    def _ingest(self, result, objects, payload, request):
        """The bake's return leg: reassemble the manifest onto the Maya scene.

        :class:`pythontk.HandoffBridge`'s designed hook -- the one step that touches
        host state after the target app has run; every other hand-off passes through
        untouched. Fires only for a blocking run whose artifact is a return manifest
        (the same ``BRIDGE_OUTPUT_EXT`` contract the preflight keys off), so the panel,
        :meth:`bake_lightmaps` and any future caller share ONE return leg instead of
        each sniffing the artifact suffix. ``request.extras['reassemble']`` opts out
        (re-running a bake purely for its maps).

        Gated on the artifact's suffix, with the mode only as a cheap pre-filter --
        ``save_as`` is admitted next to ``round_trip`` so a custom template declaring the
        other blocking mode still gets its return leg. The shipped ``bake_lightmaps``
        declares ``round_trip`` alone, so ``save_as`` with THAT template is refused in
        preflight (``ScriptRunDeliverer.strict_modes``) and never reaches here.

        It stops at committed scene state -- deliberately. Viewing the result is the
        WebXR Preview button's job, and that button already self-feeds off the commit
        (a fresh export carries ``lightmap_metadata``, which ``apply_glb_lightmaps``
        binds during FBX -> GLB). Chaining a browser push from here would couple a
        scene operation to a network service for no gain: the same click works before
        the bake, after it, and after a manual edit, with one code path.
        """
        if (
            not result
            or request.mode not in (ROUND_TRIP, SAVE_AS)
            or not result.get("output")
            or self.template_output_ext(self.template_path(request.template))
            != self.RETURN_MANIFEST_SUFFIX
        ):
            return result
        if not request.get("reassemble", True):
            return result
        result["reassembled"] = self.reassemble_lightmaps(result["output"], objects)
        return result

    #: Filename convention for the bake's return manifest (each object's HDR lightmap +
    #: its per-instance atlas rect + one UV layout per unique mesh). ``_ingest`` keys
    #: the reassembly step off this ending, so it stays template-agnostic.
    RETURN_MANIFEST_SUFFIX = ".lightmaps.json"
    #: Highest return-manifest schema this reader accepts. Duplicated in the template
    #: (which runs in Blender and cannot import this) -- a test pins the two equal.
    #: v2: per-instance ``rect`` bindings + layouts deduped per unique mesh; v1 (inline
    #: ``uv_layout`` per object, identity rects) is still read.
    RETURN_MANIFEST_VERSION = 2

    @classmethod
    def default_output_path(cls, template: str) -> str:
        """Derive where a ``BRIDGE_OUTPUT = ("auto",)`` template's artifact goes.

        Tracked temp storage, named after the scene: an ``auto`` artifact is pipeline
        plumbing -- the bake's return manifest is read once, on the way back into the
        scene, and never opened again -- so the project is the wrong home for it. Derived
        beside the maps it would drop a machine-readable JSON into ``sourceimages`` on
        every bake with nothing to ever clean it up; ``ptk.TempArtifacts`` keeps the run
        identifiable in a log line and sweeps the namespace by age.

        Entirely driven by the template's own ``BRIDGE_OUTPUT_EXT`` (a compound suffix
        like ``.lightmaps.json`` is honored verbatim): no template is named here, so the
        next ``auto`` recipe works without touching this.
        """
        import maya.cmds as cmds

        ext = cls.template_output_ext(cls.template_path(template))
        stem = Path(cmds.file(query=True, sceneName=True) or "").stem or "untitled"
        return ptk.TempArtifacts("blender_bridge").path(extension=ext, name=stem)

    @staticmethod
    def _default_lightmap_dir(objects: Optional[List[Any]] = None) -> str:
        """Where the returned HDR lightmaps land: **beside the textures they join**.

        A lightmap is one more map of the set an object already wears, so it belongs in
        the folder the rest of that set lives in -- ``sourceimages/OFFICE_ENV/`` next to
        ``OFFICE_ENV_Base_color.png``, not loose in ``sourceimages`` where it reads as
        belonging to no set. The directory holding the most of *objects*' textures wins;
        a selection spanning several sets has no single right answer, and the majority is
        the least surprising of them.

        Falls back to the project's ``sourceimages`` when nothing textured is in scope,
        then to the saved scene's own folder (the rare no-workspace case; a scene that was
        never saved gets ``""``). Never temp: these become textures **this Maya scene
        references**, and an age sweep would delete them out from under a committed bake.
        """
        import maya.cmds as cmds

        # Deferred, and reaching the module rather than the subpackage: mayatk's
        # subpackage __init__ files are docstring-only (the root registers the surface),
        # so ``from mayatk.mat_utils import MatUtils`` does not resolve. Matches the
        # existing deferred import of the same class in this file.
        from mayatk.mat_utils._mat_utils import MatUtils

        if objects:
            try:
                paths = MatUtils.get_texture_paths(
                    objects=objects, absolute=True, exclude_bundled=True
                )
            except Exception:
                # A texture scan must never block a bake, and every failure mode lands on
                # the same documented answer as "nothing textured is selected" -- the
                # project's sourceimages, which is a correct place for the maps, not a
                # guess. Degrading here loses a nicety, not correctness.
                paths = []
            counts: Dict[str, int] = {}
            for path in paths or []:
                folder = os.path.dirname(str(path))
                if folder and os.path.isdir(folder):
                    counts[folder] = counts.get(folder, 0) + 1
            if counts:
                # Sorted first so a tie breaks on the path, not on scan order.
                return max(sorted(counts), key=counts.get)

        try:
            root = cmds.workspace(query=True, rootDirectory=True) or ""
            rule = cmds.workspace(fileRuleEntry="sourceImages") or "sourceimages"
        except Exception:
            root = rule = ""
        if root:
            return os.path.join(root, rule)
        scene = cmds.file(query=True, sceneName=True) or ""
        return str(Path(scene).parent) if scene else ""

    @staticmethod
    def _resolve_returned_objects(
        names: Any, objects: Optional[List[Any]]
    ) -> Tuple[Dict[str, str], List[str], List[str]]:
        """Map the return manifest's Blender names back onto the exported Maya nodes.

        Blender holds the FBX short name, possibly with a ``.001`` collision suffix --
        the convention every other replay in this bridge matches on. Resolution is scoped
        to the nodes THIS run exported, so an unrelated same-named node elsewhere in the
        scene can never be picked up.

        A leaf name shared by several exported nodes is genuinely ambiguous (Maya allows
        ``|a|wheel`` and ``|b|wheel``). Guessing would wire one object's lightmap onto
        another -- wrong in a way that reads as a bad bake -- so those are returned for
        the caller to report and skipped.

        Returns ``(resolved, ambiguous, unmatched)``.
        """
        index: Dict[str, List[str]] = {}
        for obj in objects or []:
            leaf = str(obj).rsplit("|", 1)[-1]
            # Namespaces do not survive FBX intact; accept the bare and flattened forms.
            for key in {leaf, leaf.rsplit(":", 1)[-1], leaf.replace(":", "_")}:
                index.setdefault(key, []).append(str(obj))

        resolved: Dict[str, str] = {}
        ambiguous: List[str] = []
        unmatched: List[str] = []
        for name in names:
            base = re.sub(r"\.\d{3}$", "", str(name))
            hits = list(dict.fromkeys(index.get(base) or []))
            if len(hits) == 1:
                resolved[str(name)] = hits[0]
            elif hits:
                ambiguous.append(str(name))
            else:
                unmatched.append(str(name))

        # The reverse collision: two baked objects landing on ONE Maya node. The caller
        # keys its mapping by the Maya node, so they would collapse silently with an
        # arbitrary winner -- one object wearing another's lightmap. Reject both.
        per_node: Dict[str, int] = {}
        for node in resolved.values():
            per_node[node] = per_node.get(node, 0) + 1
        for name, node in list(resolved.items()):
            if per_node[node] > 1:
                del resolved[name]
                ambiguous.append(name)
        return resolved, ambiguous, unmatched

    def _report_bake_lighting(self, lighting: Dict[str, Any]) -> None:
        """Say what actually lit the bake, and warn when the answer is 'nothing'.

        The bake runs in a headless Blender whose stdout the bridge only surfaces when the
        run *fails*. A scene with no lights bakes perfectly successfully to black, so
        without this the most common way this operation goes wrong is also its quietest:
        the artist gets a finished, committed, entirely black lightmap and no reason why.
        Maya's default viewport lighting is the trap -- it lights the viewport, is not a
        scene light, and exports nothing.
        """
        if not lighting:
            return  # older manifest; nothing to report rather than a fabricated summary
        sources = []
        if lighting.get("hdri"):
            sources.append(f"HDRI {lighting['hdri']}")
        if lighting.get("imported_lights"):
            sources.append(f"{lighting['imported_lights']} imported light(s)")
        self.logger.info(
            "Bake lighting: " + (", ".join(sources) if sources else "NONE")
        )
        for text in lighting.get("warnings") or []:
            self.logger.warning(text)

    def reassemble_lightmaps(
        self, manifest_path: str, objects: Optional[List[Any]] = None
    ) -> Dict[str, str]:
        """Wire a finished Blender bake back into this Maya scene.

        The return leg of :meth:`bake_lightmaps`: Blender owned the whole lightmap job, so
        this writes the layout it produced onto the Maya meshes
        (:meth:`~mayatk.UvUtils.apply_uv_layout`) and then records the maps
        (:meth:`~mayatk.LightmapBaker.commit_lightmap`) so they sit **alongside the
        existing maps** -- full PBR material and UV0 untouched, lightmap on UV1, undone by
        the same ``revert_lightmap`` the Maya-native bakes use.

        UVs are written first and only objects whose layout actually applied are
        committed: a map recorded against UVs that were rejected (a mesh edited since the
        hand-off) would be sampled through the wrong layout.

        Instances are first-class: the (shared) layout is applied once per shared shape,
        and each instance transform is committed with its own atlas rect
        (``scale_offsets`` -> the engine's ``lightmapScaleOffset`` binding). Reads
        manifest v1 (inline layouts, identity rects) and v2 (deduped layouts + rects).

        Returns ``{object: lightmap_path}`` for what was committed.
        """
        import json

        manifest = str(manifest_path)
        if not os.path.isfile(manifest):
            self.logger.warning(
                f"No return manifest at {manifest}; nothing wired back into Maya."
            )
            return {}
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            self.logger.error(f"Unreadable return manifest {manifest}: {e}")
            return {}

        try:
            version = int(data.get("version", self.RETURN_MANIFEST_VERSION))
        except (TypeError, ValueError):
            version = -1
        if not 0 < version <= self.RETURN_MANIFEST_VERSION:
            # Refuse rather than warn-and-continue: a newer schema is free to change how
            # the UV payload is encoded, and misreading that writes WRONG UVs onto the
            # meshes -- strictly worse than doing nothing, and it looks like a bad bake.
            self.logger.error(
                f"{manifest} declares schema v{data.get('version')!r}; this reads up to "
                f"v{self.RETURN_MANIFEST_VERSION}. Nothing wired back -- the baked maps "
                "are still on disk. Update mayatk, or bake with a matching blendertk."
            )
            return {}

        self._report_bake_lighting(data.get("lighting") or {})

        entries = data.get("objects") or {}
        if not entries:
            self.logger.warning(f"{manifest} names no objects; nothing to wire back.")
            return {}

        # v1 carried the layout inline per object (and predates rects); v2 dedupes the
        # layout per unique mesh (instances share it) and adds the per-instance rect.
        mesh_layouts = data.get("meshes") or {}

        def _layout(entry):
            if version >= 2:
                return mesh_layouts.get(entry.get("mesh"))
            return entry.get("uv_layout")

        import maya.cmds as cmds

        from mayatk.node_utils._node_utils import NodeUtils

        # The manifest names MESH transforms; a whole-scene save_as hands DAG roots.
        # Expand to descendants so the index holds the leaf names FBX actually carried.
        pool: List[str] = []
        for obj in objects or []:
            for node in [str(obj)] + (
                cmds.listRelatives(
                    str(obj), allDescendents=True, type="transform", fullPath=True
                )
                or []
            ):
                if node not in pool:
                    pool.append(node)

        instanced = self._instanced_shapes(pool)
        if instanced:
            copies = sum(len(v) for v in instanced.values())
            self.logger.info(
                f"{copies} instanced transform(s) share {len(instanced)} shape(s): "
                "the layout is applied once per shared shape, and each instance "
                "carries its own atlas rect."
            )

        resolved, ambiguous, unmatched = self._resolve_returned_objects(entries, pool)
        for label, names in (("ambiguous", ambiguous), ("unmatched", unmatched)):
            if names:
                self.logger.warning(
                    f"{len(names)} baked object(s) {label} against the exported "
                    f"selection; not wired: {', '.join(sorted(names)[:5])}"
                )

        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
        from mayatk.uv_utils._uv_utils import UvUtils

        # Vet each entry BEFORE touching a mesh: an entry that cannot be committed must
        # not leave rewritten UVs behind. A missing map would otherwise give the material
        # a dead texture reference plus a marker claiming the object is lit -- worse than
        # no lightmap, because nothing downstream reports it.
        usable: Dict[str, Tuple[str, Any, Optional[List[float]]]] = {}
        for blender, maya in resolved.items():
            entry = entries[blender]
            path, layout = entry.get("map") or "", _layout(entry)
            if not layout:
                self.logger.warning(f"{maya}: no UV layout in the sidecar; skipped.")
            elif not os.path.isfile(path):
                self.logger.warning(f"{maya}: lightmap missing at {path!r}; skipped.")
            else:
                usable[maya] = (path, layout, entry.get("rect"))

        # One UV write per shared shape: instance siblings wear the same shape, so the
        # (identical) layout is applied through one representative and the result fans
        # out to the whole group -- a topology rejection disqualifies every sibling,
        # never a silent subset.
        groups: Dict[str, List[str]] = {}
        for maya in usable:
            shape = NodeUtils.get_shape(maya)
            uid = (cmds.ls(shape, uuid=True) or [maya])[0] if shape else maya
            groups.setdefault(uid, []).append(maya)

        reps = {members[0]: members for members in groups.values()}
        applied = UvUtils.apply_uv_layout(
            {rep: usable[rep][1] for rep in reps}, quiet=True
        )
        wired = [m for rep, members in reps.items() if rep in applied for m in members]
        mapping = {m: usable[m][0] for m in wired}
        if not mapping:
            # Could be a vetting rejection or a UV failure; both are already logged per
            # object, so point there rather than naming one cause and being wrong.
            self.logger.error(
                "Nothing could be wired back -- see the per-object warnings above. "
                "The baked maps are still on disk; the scene is unchanged."
            )
            return {}

        baker = LightmapBaker()
        rects = {m: usable[m][2] for m in wired if usable[m][2]}
        recorded = baker.commit_lightmap(mapping, scale_offsets=rects)
        maps_dir = os.path.dirname(next(iter(mapping.values()), ""))
        self.logger.info(
            f"Wired {len(recorded)} lightmap(s) from {maps_dir} into the scene "
            "alongside the existing maps -- Lightmap Baker's Revert to Source undoes it."
        )
        return recorded

    @staticmethod
    def list_templates() -> List[Path]:
        """User-visible templates in ``templates/`` (skips underscore-prefixed)."""
        return _templates.ScriptTemplate.list_templates(_TEMPLATE_DIR, ".py")

    #: Modes a user-visible template may declare — DERIVED from the specs that serve
    #: them, never restated. The helpers filter declarations against this and silently
    #: fall back to the first entry for anything outside it, so a mode a spec delivers
    #: but this list forgot mislabels that template as an interactive send, which then
    #: launches a GUI Blender on a script with no ``__OUT_FILE__``. Deriving it makes
    #: that whole class of bug unreachable; ``_SPEC`` stays first so the fallback is
    #: still ``send_to``. (The classmethods below need this without an instance, which
    #: is why it is not ``HandoffBridge.modes``.)
    template_modes_allowed: Tuple[str, ...] = tuple(_SPEC.modes) + tuple(_RUN_SPEC.modes)

    @classmethod
    def template_modes(cls, template_path: Path) -> Tuple[str, ...]:
        """Modes a template declares via ``BRIDGE_MODES``; ``("send_to",)`` fallback."""
        return _templates.ScriptTemplate.template_modes(
            template_path, cls.template_modes_allowed
        )

    @classmethod
    def list_template_modes(cls) -> List[Tuple[str, str]]:
        """``[(stem, mode), ...]`` for every (template, mode) pairing."""
        return _templates.ScriptTemplate.list_template_modes(
            _TEMPLATE_DIR, ".py", cls.template_modes_allowed
        )

    @staticmethod
    def template_path(stem: str) -> Path:
        """The template file for a combo entry's *stem*.

        One place builds this: a stem that resolves to a missing file reads as "declares
        nothing" and silently falls back to the bridge defaults -- which for
        ``BRIDGE_TIMEOUT`` means quietly reinstating the 600s cap a bake cannot finish in.
        """
        return _TEMPLATE_DIR / f"{stem}.py"

    @staticmethod
    def _declared_value(template_path, field: str) -> Optional[str]:
        """First value a template declares via ``<field> = (...)``, or ``None``.

        The declarations ride the same reader as ``BRIDGE_MODES`` (which takes the field
        name as a parameter), so a template stays the one place its own contract is
        written down -- no per-template branching in the bridge or the panel. The RAW
        reader, deliberately: the mode-flavoured one folds legacy spellings, which has
        no business touching an extension or a timeout.
        """
        declared = _templates.ScriptTemplate.declared_values(template_path, field)
        return declared[0] if declared else None

    @classmethod
    def template_output_ext(cls, template_path) -> str:
        """Artifact extension a template declares via ``BRIDGE_OUTPUT_EXT``.

        Falls back to the bridge's default (:attr:`save_extensions` [0]) so an
        unannotated template still gets a sensible save dialog.
        """
        ext = cls._declared_value(template_path, "BRIDGE_OUTPUT_EXT")
        default = cls.save_extensions[0] if cls.save_extensions else ".blend"
        return ext or default

    @classmethod
    def template_output_mode(cls, template_path) -> str:
        """``"auto"`` or ``"prompt"`` -- how a ``save_as`` template's output path is chosen.

        Declared via ``BRIDGE_OUTPUT``. ``auto`` means the artifact is pipeline plumbing
        (the bake's return manifest) and the panel derives its path
        (:meth:`default_output_path`); the default ``prompt`` keeps the save dialog for
        artifacts an artist genuinely names (a ``.blend``).
        """
        return cls._declared_value(template_path, "BRIDGE_OUTPUT") or "prompt"

    @classmethod
    def template_timeout(cls, template_path) -> Optional[float]:
        """Seconds a template declares via ``BRIDGE_TIMEOUT``, else ``None`` (spec default).

        The spec's 600s suits a scene save and is far too short for a lightmap bake, which
        is unbounded in samples -- and a timeout kill leaves no artifact, so it presents as
        a silent bake failure rather than as a timeout.
        """
        raw = cls._declared_value(template_path, "BRIDGE_TIMEOUT")
        try:
            return float(raw) if raw else None
        except (TypeError, ValueError):
            return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = BlenderBridge()
    try:
        import maya.cmds as cmds

        sel = cmds.ls(selection=True, long=True) or []
    except ModuleNotFoundError:
        sel = []
    # bridge.send(sel)                                       # additive import
    # bridge.send(sel, params={"CLEAR_SCENE": True})         # clean-slate / replace scene
    # bridge.send(sel, params={"FRAME_VIEW": True})          # import + frame in view
    # bridge.send(sel, params={"INCLUDE_MATERIALS": False})  # geometry only
    # bridge.save_as("C:/out/asset.blend")                   # whole scene -> .blend (blocking)
    # bridge.save_as("C:/out/asset.blend", sel)              # just the selection
    bridge.send(sel)
