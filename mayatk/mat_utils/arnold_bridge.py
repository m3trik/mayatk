# !/usr/bin/python
# coding=utf-8
"""Arnold render-bridge management.

A "bridge" is an ``aiStandardSurface`` shader (plus its ``aiMultiply`` and
``bump2d`` helpers) wired into a shading engine's ``aiSurfaceShader`` slot,
parallel to the base game material on ``surfaceShader``. It lets the same asset
preview correctly under Arnold inside Maya while the Stingray / Standard Surface
material remains the single thing exported to FBX.

This module owns the bridge as a standalone, lifecycle-managed concern so it can
be added or removed *after* material creation, on any scope (given materials,
given objects, the current selection, or the whole scene). ``GameShader``
delegates its ``create_arnold`` option here.
"""
from functools import wraps
from typing import List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

try:  # UI-only helper; keep the headless ArnoldBridge import clean if uitk is absent
    from uitk.widgets.mixins.tooltip_mixin import fmt
except Exception:
    fmt = None

from mayatk.core_utils._core_utils import CoreUtils, short_name
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


def _selection_neutral(fn):
    """Restore the viewport selection after the wrapped op.

    Bridge ops create/delete shading nodes, which Maya would otherwise leave
    selected — clobbering the user's selection so a follow-up action (e.g.
    Remove after Add) reads the wrong scope. Keeps each op selection-neutral.
    """

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        selection = cmds.ls(selection=True, long=True) or []
        try:
            return fn(self, *args, **kwargs)
        finally:
            survivors = [s for s in selection if cmds.objExists(s)]
            if survivors:
                cmds.select(survivors, replace=True)
            else:
                cmds.select(clear=True)

    return wrapper


class ArnoldBridge(ptk.LoggingMixin):
    """Add, remove, query, and rebuild Arnold ``aiStandardSurface`` bridges.

    The bridge owns *dedicated* ``file`` nodes (it never shares the base
    material's texture nodes): Arnold and Stingray require conflicting
    ``colorSpace`` / ``alphaIsLuminance`` settings and read different output
    plugs on the same map, so a shared node cannot satisfy both. Dedicated
    nodes also make removal trivially correct — the whole ``aiSurfaceShader``
    island is deletable without touching the exported material.
    """

    # Shading-engine slot the Arnold shader drives (the MtoA render override
    # that sits alongside the standard ``surfaceShader``).
    BRIDGE_SLOT = "aiSurfaceShader"

    # ------------------------------------------------------------------ public
    @CoreUtils.undoable
    @_selection_neutral
    def add(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
        force: bool = False,
    ) -> List[str]:
        """Attach an Arnold bridge to every base material in scope.

        Textures are introspected from each base material's connected ``file``
        nodes, so no texture list is required — this is what lets a bridge be
        added long after the material was built.

        Parameters:
            materials: Material node(s) to bridge. Mutually combinable with
                ``objects``.
            objects: Object(s)/component(s) whose assigned materials are bridged.
            force: Rebuild the bridge if one already exists (default: skip).

        Returns:
            The created ``aiStandardSurface`` node(s).
        """
        targets = self._resolve_materials(materials, objects)
        if not targets:
            self.logger.warning("No materials in scope for Arnold bridge.")
            return []

        EnvUtils.load_plugin("mtoa")  # Load Arnold plugin
        results: List[str] = []
        for mat in targets:
            existing = self.get_bridge(mat)
            if existing:
                if not force:
                    self.logger.info(f"{short_name(mat)}: bridge exists — skipped.")
                    continue
                self.remove(materials=mat)

            sg = self._get_shading_engine(mat)
            if not sg:
                self.logger.warning(f"{short_name(mat)}: no shading engine — skipped.")
                continue

            name = short_name(mat)
            ai_node, aiMult_node, bump_node = self._setup_nodes(mat, sg, name)

            textures = self._iter_base_textures(mat)
            for path, map_type in textures:
                self._connect_texture(path, map_type, ai_node, aiMult_node, bump_node)

            results.append(ai_node)
            self.logger.success(
                f"{short_name(mat)}: Arnold bridge added ({len(textures)} maps)."
            )
        return results

    @CoreUtils.undoable
    @_selection_neutral
    def remove(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        """Delete the Arnold bridge from every base material in scope.

        Removes only the ``aiSurfaceShader`` island (Arnold shader + its helper
        and file nodes). The exported base material and its texture network are
        left untouched — even if, defensively, a node were shared it would be
        protected by the base material's own history.

        Parameters:
            materials: Material node(s) to clear.
            objects: Object(s)/component(s) whose assigned materials are cleared.

        Returns:
            The base materials whose bridge was removed.
        """
        targets = self._resolve_materials(materials, objects)
        removed: List[str] = []
        for mat in targets:
            ai_node = self.get_bridge(mat)
            if not ai_node:
                continue

            # Bridge island = everything upstream of the Arnold shader.
            island = set(cmds.listHistory(ai_node) or [])
            island.add(ai_node)
            # Protect anything the exported material also depends on.
            protected = set(cmds.listHistory(str(mat)) or [])
            protected.add(str(mat))

            to_delete = [
                n for n in island if n not in protected and cmds.objExists(n)
            ]
            if to_delete:
                cmds.delete(to_delete)
            removed.append(mat)
            self.logger.success(f"{short_name(mat)}: Arnold bridge removed.")

        if not removed:
            self.logger.info("No Arnold bridges found in scope.")
        return removed

    @CoreUtils.undoable
    def rebuild(
        self,
        materials: Optional[Union[str, List[str]]] = None,
        objects: Optional[Union[str, List[str]]] = None,
    ) -> List[str]:
        """Remove and re-add the bridge — resyncs it to the base material's
        current textures (the safe alternative to sharing file nodes).

        Atomic: the remove + re-add collapse into a single undo step.
        """
        targets = self._resolve_materials(materials, objects)
        if not targets:
            self.logger.warning("No materials in scope for Arnold bridge rebuild.")
            return []
        self.remove(materials=targets)
        return self.add(materials=targets, force=True)

    def get_bridge(self, material: str) -> Optional[str]:
        """Return the ``aiStandardSurface`` bridging *material*, or None."""
        sg = self._get_shading_engine(material)
        if not sg or not cmds.attributeQuery(
            self.BRIDGE_SLOT, node=str(sg), exists=True
        ):
            return None
        conns = (
            cmds.listConnections(
                f"{sg}.{self.BRIDGE_SLOT}", source=True, destination=False
            )
            or []
        )
        return conns[0] if conns else None

    def has_bridge(self, material: str) -> bool:
        """True if *material*'s shading engine already has an Arnold bridge."""
        return self.get_bridge(material) is not None

    # ----------------------------------------------------------------- scoping
    def _resolve_materials(
        self,
        materials: Optional[Union[str, List[str]]],
        objects: Optional[Union[str, List[str]]],
    ) -> List[str]:
        """Normalize materials/objects/selection/scene into base material nodes.

        Excludes ``aiStandardSurface`` nodes (the bridges themselves) so the
        bridge can never be built on top of another bridge.
        """
        mats: List[str] = []
        if materials is not None:
            if not isinstance(materials, (list, tuple, set)):
                materials = [materials]
            mats.extend(str(m) for m in materials)
        if objects is not None:
            mats.extend(MatUtils.get_mats(objects))

        if materials is None and objects is None:
            # Default scope: current selection, else the whole scene.
            sel = cmds.ls(selection=True, long=True) or []
            if sel:
                mats.extend(MatUtils.get_mats(sel))
            else:
                # Whole-scene fallback uses the canonical scene-materials getter
                # (drops Maya's default shaders) and keeps only assigned +
                # textured materials, so a bare add() never bridges an unused or
                # blank material. Explicit materials=/objects= bridge
                # unconditionally.
                mats.extend(
                    m
                    for m in MatUtils.get_scene_mats()
                    if self._get_shading_engine(m) and self._iter_base_textures(m)
                )

        seen, out = set(), []
        for m in mats:
            if not m or m in seen or not cmds.objExists(m):
                continue
            seen.add(m)
            if cmds.nodeType(m) == "aiStandardSurface":
                continue
            out.append(m)
        return out

    @staticmethod
    def _get_shading_engine(material: str) -> Optional[str]:
        """The shading engine fed by *material*'s ``outColor``."""
        return NodeUtils.get_connected_nodes(
            material,
            node_type="shadingEngine",
            direction="outgoing",
            first_match=True,
        )

    def _iter_base_textures(self, material: str) -> List[Tuple[str, str]]:
        """Resolve ``(path, map_type)`` for each unique map feeding *material*.

        Map type is resolved from the file name via ``MapFactory`` — the same
        single source of truth ``GameShader`` uses at creation. Packed-map
        precedence (ORM > MSAO > Metallic_Smoothness) mirrors creation so a
        hand-authored material with redundant packs wires cleanly.
        """
        history = cmds.listHistory(str(material), pruneDagObjects=True) or []
        file_nodes = cmds.ls(history, type="file") or []

        found: List[Tuple[str, str]] = []
        seen_types = set()
        for fn in file_nodes:
            if not cmds.objExists(f"{fn}.fileTextureName"):
                continue
            path = cmds.getAttr(f"{fn}.fileTextureName")
            if not path:
                continue
            map_type = ptk.MapFactory.resolve_map_type(path)
            if not map_type or map_type in seen_types:
                continue
            seen_types.add(map_type)
            found.append((path, map_type))

        types = {t for _, t in found}
        if "ORM" in types:
            found = [
                (p, t) for p, t in found if t not in ("MSAO", "Metallic_Smoothness")
            ]
        elif "MSAO" in types:
            found = [(p, t) for p, t in found if t != "Metallic_Smoothness"]
        return found

    # --------------------------------------------------------------- network
    def _setup_nodes(
        self, material: str, shading_engine: str, name: str
    ) -> Tuple[str, str, str]:
        """Create the Arnold shader trio and wire it to *shading_engine*.

        Creates an ``aiStandardSurface`` (→ ``aiSurfaceShader``), an
        ``aiMultiply`` feeding its ``baseColor``, and a tangent-space ``bump2d``
        feeding its ``normalCamera``.
        """
        ai_node = NodeUtils.create_render_node(
            "aiStandardSurface", name=name + "_ai" if name else ""
        )
        aiMult_node = cmds.shadingNode("aiMultiply", asShader=True)
        bump_node = cmds.shadingNode("bump2d", asShader=True)
        cmds.setAttr(f"{bump_node}.bumpInterp", 1)  # tangent-space normals

        Attributes.connect_multi(
            (f"{ai_node}.outColor", f"{shading_engine}.{self.BRIDGE_SLOT}"),
            (f"{aiMult_node}.outColor", f"{ai_node}.baseColor"),
            (f"{bump_node}.outNormal", f"{ai_node}.normalCamera"),
        )
        return ai_node, aiMult_node, bump_node

    def _connect_texture(
        self,
        texture: str,
        texture_type: str,
        ai_node: str,
        aiMult_node: str,
        bump_node: str,
    ) -> bool:
        """Connect one texture to the Arnold shader trio based on its map type.

        Parameters:
            texture: File path of the texture image.
            texture_type: Resolved map type (e.g. ``"Base_Color"``, ``"ORM"``).
            ai_node: The ``aiStandardSurface`` node.
            aiMult_node: The ``aiMultiply`` node blending base color / AO.
            bump_node: The ``bump2d`` node receiving normal maps.

        Returns:
            True if a connection was made, False for an unsupported type.
        """
        if texture_type in ["Base_Color", "Diffuse"]:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{aiMult_node}.input1", force=True
            )

        elif texture_type == "Albedo_Transparency":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Base color
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{aiMult_node}.input1", force=True
            )
            # Transparency: alpha -> standard-surface opacity
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.opacityR", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.opacityG", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.opacityB", force=True
            )
            return True

        elif texture_type == "Roughness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.specularRoughness", force=True
            )
            # Reuse roughness for refraction blurriness.
            cmds.connectAttr(
                f"{texture_node}.outAlpha",
                f"{ai_node}.transmissionExtraRoughness",
                force=True,
            )

        elif texture_type == "Metallic":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.metalness", force=True
            )

        elif texture_type == "Metallic_Smoothness":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            # Invert smoothness (alpha) -> roughness.
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{ai_node}.specularRoughness", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX",
                f"{ai_node}.transmissionExtraRoughness",
                force=True,
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{ai_node}.metalness", force=True
            )

        elif texture_type == "ORM":
            # Unreal/glTF ORM: R=AO, G=Roughness, B=Metallic
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=0,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColorB", f"{ai_node}.metalness", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorG", f"{ai_node}.specularRoughness", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorG",
                f"{ai_node}.transmissionExtraRoughness",
                force=True,
            )
            # AO (R) -> multiply uniformly with base color.
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{aiMult_node}.input2R", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{aiMult_node}.input2G", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{aiMult_node}.input2B", force=True
            )

        elif texture_type == "MSAO":
            # Unity HDRP mask: R=Metallic, G=AO, B=Detail, A=Smoothness
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColorR", f"{ai_node}.metalness", force=True
            )
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{reverse_node}.inputX", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX", f"{ai_node}.specularRoughness", force=True
            )
            cmds.connectAttr(
                f"{reverse_node}.outputX",
                f"{ai_node}.transmissionExtraRoughness",
                force=True,
            )
            # AO (G) -> multiply with base color.
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{aiMult_node}.input2", force=True
            )

        elif texture_type == "Emissive":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{ai_node}.emission", force=True
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{ai_node}.emissionColor", force=True
            )

        elif "Normal" in texture_type:
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outAlpha", f"{bump_node}.bumpValue", force=True
            )

        elif texture_type == "Ambient_Occlusion":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{aiMult_node}.input2", force=True
            )

        elif texture_type == "Opacity":
            texture_node = NodeUtils.create_render_node(
                "file",
                fileTextureName=texture,
                colorSpace="Raw",
                alphaIsLuminance=1,
                ignoreColorSpaceFileRules=1,
                name=ptk.format_path(texture, section="name"),
            )
            cmds.connectAttr(
                f"{texture_node}.outColor", f"{ai_node}.opacity", force=True
            )
        else:
            return False
        return True


class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``arnold_bridge.ui`` panel.

    A thin driver over :class:`ArnoldBridge` — no bridge logic lives here.
    Add / Remove / Rebuild operate on the scope picked in the combobox
    (selected objects' materials, or every scene material). Force makes Add
    rebuild a material that already has a bridge instead of skipping it.
    """

    # (label, op-kwarg-resolver-key). The combobox shows the labels; the action
    # methods map the current label to a scope via _scope_kwargs().
    _SCOPE_LABELS = ("Selected Objects", "All Scene Materials")
    _VERB = {"add": "Added", "remove": "Removed", "rebuild": "Rebuilt"}

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.arnold_bridge
        self._bridge = ArnoldBridge()

    # ------------------------------------------------------------------ header
    def header_init(self, widget) -> None:
        """Configure the header menu and help text."""
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Select Bridged Materials",
            setObjectName="select_bridged",
            setToolTip="Select every scene material that currently has an "
            "Arnold bridge.",
        )
        if fmt is not None:
            widget.set_help_text(
                fmt(
                    title="Arnold Render Bridge",
                    body="Attach (or remove) an Arnold <i>aiStandardSurface</i> "
                    "shader alongside a game material so the asset renders in "
                    "Arnold — without disturbing the Stingray / Standard Surface "
                    "material that exports to FBX.",
                    steps=[
                        "Pick a <b>Scope</b>: the selected objects' materials, or "
                        "every scene material.",
                        "<b>Add Bridge</b> mirrors each material's textures onto a "
                        "new Arnold shader wired to the shading group's "
                        "<i>aiSurfaceShader</i> slot.",
                        "<b>Remove Bridge</b> deletes the Arnold network (the "
                        "exported material is untouched); <b>Rebuild</b> re-syncs it "
                        "to the material's current textures.",
                    ],
                    sections=[
                        ("Scene-only by design", [
                            "The bridge drives <i>aiSurfaceShader</i>, which FBX "
                            "doesn't represent — only the Stingray / Standard "
                            "Surface material on <i>surfaceShader</i> exports. The "
                            "Arnold network never leaves the Maya scene.",
                            "It owns dedicated file nodes, so Remove cleanly deletes "
                            "the whole Arnold island and the base material's "
                            "textures stay put.",
                        ]),
                    ],
                )
            )

    # -------------------------------------------------------------------- combo
    def cmb000_init(self, widget) -> None:
        """Populate the Scope combobox (Selected Objects is the default)."""
        widget.clear()
        widget.addItems(self._SCOPE_LABELS)
        widget.setCurrentIndex(0)

    # ------------------------------------------------------------------ actions
    def b000(self) -> None:
        """Add Bridge."""
        self._run("add", force=self.ui.chk000.isChecked())

    def b001(self) -> None:
        """Remove Bridge."""
        self._run("remove")

    def b002(self) -> None:
        """Rebuild Bridge."""
        self._run("rebuild")

    def select_bridged(self) -> None:
        """Header action: select every base material that has a bridge.

        Excludes the ``aiStandardSurface`` bridge shaders themselves (they're
        scene materials too, and a bridge's ``outColor`` reaches its own SG's
        ``aiSurfaceShader``, so ``has_bridge`` would report them as bridged).
        """
        bridged = [
            m for m in self._scene_base_materials() if self._bridge.has_bridge(m)
        ]
        if not bridged:
            self.ui.footer.setText("No materials have an Arnold bridge.")
            return
        cmds.select(bridged, replace=True)
        self.ui.footer.setText(
            f"Selected {len(bridged)} bridged material(s)."
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _scene_base_materials() -> List[str]:
        """Scene materials minus the ``aiStandardSurface`` bridge shaders."""
        return [
            m
            for m in (MatUtils.get_scene_mats() or [])
            if cmds.nodeType(m) != "aiStandardSurface"
        ]

    def _scope_kwargs(self):
        """Resolve the Scope combobox to (kwargs, description) or (None, msg).

        Returns ``(None, message)`` when the scope yields nothing actionable so
        the caller can report it and bail.
        """
        label = self.ui.cmb000.currentText() or self._SCOPE_LABELS[0]
        if "All Scene" in label:
            materials = self._scene_base_materials()
            if not materials:
                return None, "No scene materials found."
            return {"materials": materials}, f"{len(materials)} scene material(s)"

        selection = cmds.ls(selection=True, long=True) or []
        if not selection:
            return None, (
                "Select object(s), or switch Scope to All Scene Materials."
            )
        return {"objects": selection}, "selection"

    def _run(self, op: str, force: bool = False) -> None:
        """Resolve scope, run ``op`` on the bridge, and report the count."""
        kwargs, desc = self._scope_kwargs()
        if kwargs is None:
            self.ui.footer.setText(desc)
            return
        if op == "add":
            kwargs["force"] = force

        with self.ui.footer.progress(
            text=f"{op.capitalize()} Arnold bridge ({desc})…"
        ):
            result = getattr(self._bridge, op)(**kwargs)

        n = len(result)
        self.ui.footer.setText(
            f"{self._VERB[op]} {n} Arnold bridge{'' if n == 1 else 's'} ({desc})."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("arnold_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
