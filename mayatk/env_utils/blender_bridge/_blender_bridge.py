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

Picking a different template is the "dynamic script selection". One ``import`` recipe ships -- a
single options-driven script whose ``CLEAR_SCENE`` / ``FRAME_VIEW`` booleans cover what used to be
three near-identical templates -- and any extra ``templates/*.py`` the user drops in is discovered
the same way.

Two delivery *modes* ride the one export pipeline (:attr:`spec` / :attr:`run_spec`, dispatched by
``HandoffBridge.deliverers``): ``send_to`` launches an interactive Blender on the ``import``
template, and ``save_as`` (:meth:`~pythontk.ScriptLaunchBridge.save_as`) runs Blender headlessly on
``templates/_save_scene.py`` and returns a written ``.blend`` -- same FBX, same material sidecar,
no second export path. Co-located with its panel
(``blender_bridge_slots.BlenderBridgeSlots`` + ``blender_bridge.ui``) under ``env_utils``;
discovered by :class:`mayatk.ui_utils.MayaUiHandler`. ``import maya.cmds`` is deferred so resolving
the package surface never needs a running Maya.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils import script_template as _templates
from pythontk.core_utils.script_template import SAVE_AS, SEND_TO

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
    "TRIANGULATE": False,
    "CLEAR_SCENE": False,
    "FRAME_VIEW": False,
    # --- lightmap bake (templates/bake_lightmaps.py) ------------------------------
    # Only shown by the panel when the selected template references them, so they cost
    # the plain import recipe nothing.
    "LIGHTMAP_RESOLUTION": 1024,
    "LIGHTMAP_SAMPLES": 256,
    "LIGHTMAP_MODE": "separated",
    "LIGHTMAP_DENOISE": True,
    "LIGHTMAP_DEVICE": "GPU",
    "ENVIRONMENT_HDR": "",
    "WORLD_STRENGTH": 0.35,
    "FIXTURE_LIGHTS": True,
    "FIXTURE_PATTERN": "LIGHT_",
    "FIXTURE_WATTS": 200.0,
    "EMISSION_STRENGTH": 2.0,
    "TEXTURE_MAX_SIZE": 2048,
    "IMAGE_FORMAT": "WEBP",
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
    modes=(SAVE_AS,),
    launch_env=_SPEC.launch_env,
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.


class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge):
    """Export the Maya selection and run a chosen Blender import template.

    Named after its target app (``BlenderBridge``), mirroring ``MarmosetBridge``; the Blender-side
    counterpart is ``blendertk.MayaBridge``. All Blender-specific config is the :data:`_SPEC` /
    :data:`_RUN_SPEC` dataclasses; this class adds only the Maya parameter bindings.

    Two ways out::

        bridge.send(objects)                     # -> a fresh interactive Blender
        bridge.save_as("C:/out/asset.blend")     # -> a .blend on disk (blocking, headless)

    ``save_as`` is Maya's "export to Blender's native format": no Blender window, no manual
    import step, and ``objects=None`` means the whole scene rather than the selection.
    """

    spec = _SPEC
    run_spec = _RUN_SPEC
    # ``save_as`` writes Blender's native scene format; a bare path gets ".blend".
    # ``.glb`` rides the same headless route via the ``bake_lightmaps`` template
    # (:meth:`bake_lightmaps`), which writes a web deliverable rather than a scene.
    save_extensions = (".blend", ".glb")

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
        try:
            from mayatk.env_utils.blender_bridge import parameters as _params
        except ImportError:  # no Qt -- see DEFAULTS
            return dict(DEFAULTS)
        return _params.Parameters.defaults()

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
            )
        except Exception:  # noqa: BLE001
            self.logger.warning(
                "Manifest sidecar failed; Blender keeps the FBX-carried "
                "materials.",
                exc_info=True,
            )
        return payload

    def _write_manifest(
        self, objects, fbx_path: str, include_materials: bool = True
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
        if not include_materials:
            self._dump_manifest(fbx_path, [], [], node_types)
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
        self._dump_manifest(fbx_path, entries, scene_materials, node_types)

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

    def _dump_manifest(
        self,
        fbx_path: str,
        entries: List[Dict[str, Any]],
        scene_materials: List[str],
        node_types: Dict[str, str],
    ) -> None:
        """Write the sidecar (shared by the materials-on and materials-off paths)."""
        import json

        if not entries and not node_types:
            return
        with open(fbx_path + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": entries,
                    "scene_materials": scene_materials,
                    "transforms": node_types,
                },
                fh,
                indent=1,
            )
        self.logger.info(
            f"Manifest: {len(entries)} textured material(s), "
            f"{len(node_types)} group/locator transform(s) sidecarred."
        )

    # ------------------------------------------------------------------ lightmap bake
    def bake_lightmaps(
        self,
        out_glb: str,
        objects: Optional[List[Any]] = None,
        *,
        environment_hdr: Optional[str] = None,
        resolution: Optional[int] = None,
        samples: Optional[int] = None,
        mode: Optional[str] = None,
        fixture_lights: Optional[bool] = None,
        fixture_watts: Optional[float] = None,
        timeout: Optional[float] = None,
        reassemble: bool = True,
        **params: Any,
    ) -> Optional[Dict[str, Any]]:
        """Bake the selection's lightmaps in a headless Blender and wire them back in.

        Named convenience over :meth:`~pythontk.ScriptLaunchBridge.save_as` with the
        ``bake_lightmaps`` template -- the same export pipeline every other hand-off uses,
        so there is no second FBX path to maintain. Blocking; judged by the written GLB.

        **A round trip, not a one-way export.** Blender owns the lightmap job end to end
        (UV generation, packing, baking, atlasing); on return *reassemble* writes the
        layout it produced onto the Maya meshes and records the maps **alongside the
        existing ones** -- full PBR material and UV0 untouched, lightmap on UV1, undone by
        the same ``revert_lightmap`` the Maya-native bakes use. Pass ``reassemble=False``
        for a pure web deliverable that leaves the Maya scene alone.

        Cycles rather than mayatk's Arnold ``LightmapBaker``: it bakes white-card irradiance
        natively, needs no licence, denoises, and runs on the GPU. Pick one as *the* WebXR
        path -- the two will not match visually.

        **Lighting is not optional.** A scene lit by StingrayPBS IBL exports no lights at
        all, so pass *environment_hdr*, leave *fixture_lights* on, or both -- otherwise the
        bake is black. See the template's docstring for why an emissive map is deliberately
        not treated as a light source.

        Example::

            mtk.BlenderBridge().bake_lightmaps(
                "O:/deliver/office.glb",
                environment_hdr="O:/hdri/unfinished_office.hdr",
                samples=512,
            )

        Returns the deliverer result (``output`` / ``duration`` / ``returncode``), or
        ``None`` on a handled failure.
        """
        for key, value in (
            ("ENVIRONMENT_HDR", environment_hdr),
            ("LIGHTMAP_RESOLUTION", resolution),
            ("LIGHTMAP_SAMPLES", samples),
            ("LIGHTMAP_MODE", mode),
            ("FIXTURE_LIGHTS", fixture_lights),
            ("FIXTURE_WATTS", fixture_watts),
        ):
            if value is not None:
                params.setdefault(key, value)
        # Resolve the export set ONCE and hand the same list to both legs. save_as
        # defaults to the WHOLE SCENE rather than the selection, so leaving this None
        # would export one set and then reassemble against an empty one: every baked
        # object would come back "unmatched" and nothing would be wired in -- silently,
        # since the GLB still lands and the run still reports success.
        # ...and normalize it the same way the export will, so the return leg indexes the
        # node names FBX actually carried. A caller passing shapes would otherwise build
        # the index from shape leaf names and match nothing.
        objects = (
            self._scene_objects() if objects is None else self._resolve_objects(objects)
        )

        params.setdefault("LIGHTMAP_DIR", self._default_lightmap_dir())
        template = "bake_lightmaps"
        if timeout is None:
            # The spec's 600s default would kill a real bake around the 10-minute mark
            # with no artifact written; the template declares its own budget.
            timeout = self.template_timeout(self.template_path(template))
        result = self.save_as(
            out_glb,
            objects,
            template=template,
            params=params,
            timeout=timeout,
        )
        if result and result.get("output") and reassemble:
            result["reassembled"] = self.reassemble_lightmaps(
                result["output"], objects
            )
        return result

    #: Sidecar the bake template writes beside its GLB, carrying each object's HDR
    #: lightmap and the UV layout it was baked through. Duplicated in the template
    #: (which runs in Blender and cannot import this) -- a test pins the two equal,
    #: since a drift would just stop the panel finding the sidecar and quietly turn
    #: the round trip back into a one-way export.
    RETURN_MANIFEST_SUFFIX = ".lightmaps.json"
    #: Highest sidecar schema this knows how to read.
    RETURN_MANIFEST_VERSION = 1

    @staticmethod
    def _default_lightmap_dir() -> str:
        """The project's ``sourceimages``, or ``""`` outside a project.

        Where the returned HDR lightmaps land. They become textures **this Maya scene
        references**, so they belong in the project's texture folder -- not beside
        whatever path the artist happened to pick for the GLB, which may be a delivery
        folder or a desktop.
        """
        import maya.cmds as cmds

        try:
            root = cmds.workspace(query=True, rootDirectory=True) or ""
            rule = cmds.workspace(fileRuleEntry="sourceImages") or "sourceimages"
        except Exception:
            return ""
        return os.path.join(root, rule) if root else ""

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

    def reassemble_lightmaps(
        self, out_glb: str, objects: Optional[List[Any]] = None
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

        Returns ``{object: lightmap_path}`` for what was committed.
        """
        import json

        manifest = str(out_glb) + self.RETURN_MANIFEST_SUFFIX
        if not os.path.isfile(manifest):
            self.logger.warning(
                f"No lightmap sidecar beside {out_glb}; the GLB is still valid but "
                "nothing was wired back into Maya."
            )
            return {}
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            self.logger.error(f"Unreadable lightmap sidecar {manifest}: {e}")
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
                f"v{self.RETURN_MANIFEST_VERSION}. Nothing wired back -- the .glb is "
                "still valid. Update mayatk, or bake with a matching blendertk."
            )
            return {}

        entries = data.get("objects") or {}
        if not entries:
            self.logger.warning(f"{manifest} names no objects; nothing to wire back.")
            return {}

        resolved, ambiguous, unmatched = self._resolve_returned_objects(
            entries, objects
        )
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
        usable: Dict[str, Tuple[str, Any]] = {}
        for blender, maya in resolved.items():
            entry = entries[blender]
            path, layout = entry.get("map") or "", entry.get("uv_layout")
            if not layout:
                self.logger.warning(f"{maya}: no UV layout in the sidecar; skipped.")
            elif not os.path.isfile(path):
                self.logger.warning(f"{maya}: lightmap missing at {path!r}; skipped.")
            else:
                usable[maya] = (path, layout)

        applied = UvUtils.apply_uv_layout(
            {maya: layout for maya, (_p, layout) in usable.items()}, quiet=True
        )
        mapping = {maya: usable[maya][0] for maya in applied}
        if not mapping:
            # Could be a vetting rejection or a UV failure; both are already logged per
            # object, so point there rather than naming one cause and being wrong.
            self.logger.error(
                "Nothing could be wired back -- see the per-object warnings above. "
                "The .glb is still valid."
            )
            return {}

        baker = LightmapBaker()
        fused = str(data.get("mode")) == "fused"
        if fused:
            # A fused bake IS the material's appearance, so it REPLACES the shading
            # rather than joining it -- the other commit path, same revert.
            recorded = baker.commit_unlit(mapping)
        else:
            recorded = baker.commit_lightmap(mapping)
        self.logger.info(
            f"Wired {len(recorded)} lightmap(s) into the scene "
            + (
                "as an unlit material (the bake replaces the shading)"
                if fused
                else "alongside the existing maps"
            )
            + " -- Lightmap Baker's Revert to Source undoes it."
        )
        return recorded

    @staticmethod
    def list_templates() -> List[Path]:
        """User-visible templates in ``templates/`` (skips underscore-prefixed)."""
        return _templates.ScriptTemplate.list_templates(_TEMPLATE_DIR, ".py")

    #: Modes a user-visible template may declare. Both are real routes off the one export
    #: pipeline, so both must be *allowed* here — the helpers filter declarations against
    #: this and silently fall back to the first entry for anything outside it, which is how
    #: a ``save_as`` template ends up mislabelled as an interactive send.
    template_modes_allowed: Tuple[str, ...] = (SEND_TO, SAVE_AS)

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
        written down -- no per-template branching in the bridge or the panel.
        """
        declared = _templates.ScriptTemplate.declared_modes(template_path, field)
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
