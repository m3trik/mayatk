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

    # Channel layout for packed masks — which ``outColor`` channel carries which
    # property, so one routine wires them all. ``rough`` is ``(channel, invert)``
    # (invert routes through a ``reverse`` node, e.g. smoothness → roughness);
    # ``ao`` is the single grayscale channel ('R'/'G'/'B') broadcast into the
    # baseColor multiply. ``A`` (in ``rough``) means the alpha plug.
    #
    # AO is ALWAYS a single channel broadcast to RGB — never the whole outColor.
    # Feeding the full packed outColor into the baseColor multiply tints the
    # surface by (metallic, ao, detail): for the common non-metal case R≈0 zeroes
    # red and the AO green channel dominates, so every object renders green. The
    # AO occlusion lives in exactly one channel; broadcast only that.
    #
    # ``aIL`` is the file node's ``alphaIsLuminance``. It MUST be 0 whenever a
    # property is read from the real alpha plug ('A' in ``rough``): with aIL=1
    # Maya synthesizes outAlpha from RGB luminance and IGNORES the packed alpha,
    # so smoothness would silently become luminance(metallic, ao, detail). Layouts
    # that read everything from colour channels can leave aIL at either value.
    _PACKED_LAYOUTS = {
        # Unreal/glTF ORM: R=AO, G=Roughness, B=Metallic.
        "ORM": {"aIL": 0, "metal": "B", "rough": ("G", False), "ao": "R"},
        # Metallic-Roughness-AO: R=Metallic, G=Roughness, B=AO.
        "MRAO": {"aIL": 0, "metal": "R", "rough": ("G", False), "ao": "B"},
        # Unity HDRP mask: R=Metallic, G=AO, B=Detail, A=Smoothness.
        # aIL=0 so the smoothness in the real alpha channel is read, not luminance.
        "MSAO": {"aIL": 0, "metal": "R", "rough": ("A", True), "ao": "G"},
        # Unity URP: RGB=Metallic, A=Smoothness. aIL=0 to read the alpha smoothness
        # (RGB are all metallic, so luminance would just re-read metalness).
        "Metallic_Smoothness": {"aIL": 0, "metal": "R", "rough": ("A", True)},
    }

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
        """The shading engine fed by *material*'s ``outColor``.

        Returns None for a node that no longer exists — an earlier
        force-rebuild in the same ``add``/``remove`` pass can delete a bridge
        helper (e.g. ``aiMultiply1``) that's still referenced later in the
        resolved scope, and ``cmds.listConnections`` would otherwise raise
        ``ValueError: No object matches name`` instead of letting the caller
        skip it cleanly.
        """
        material = str(material)
        if not cmds.objExists(material):
            return None
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

        # When several packed masks resolve for one material, keep only the
        # highest-priority one (the order below) so a property isn't wired twice.
        present = [
            p
            for p in ("ORM", "MRAO", "MSAO", "Metallic_Smoothness")
            if p in {t for _, t in found}
        ]
        if len(present) > 1:
            drop = set(present[1:])
            found = [(p, t) for p, t in found if t not in drop]

        # A primary map supersedes the fallback maps it substitutes for, so the
        # two never fight over the same Arnold slot. (These fallbacks used to be
        # dropped outright; keep the primary deterministically winning now that
        # they're wired too.)
        types = {t for _, t in found}
        drop = set()
        if "Roughness" in types:  # vs. inverted-smoothness fallbacks
            drop |= {"Glossiness", "Smoothness"}
        # Any map that drives metalness — plain Metallic or a packed mask —
        # supersedes the Specular luminance proxy, so both never wire metalness.
        if types & {"Metallic", "ORM", "MRAO", "MSAO", "Metallic_Smoothness"}:
            drop |= {"Specular"}
        if any(t.startswith("Normal") for t in types):  # vs. bump/height
            drop |= {"Bump", "Height"}
        # A packed mask carrying AO (ORM/MRAO/MSAO broadcast a single channel
        # into the baseColor multiply) supersedes a standalone AO map (which
        # drives the whole multiply input2), so the two don't fight over it.
        if types & {"ORM", "MRAO", "MSAO"}:
            drop |= {"Ambient_Occlusion"}
        if drop:
            found = [(p, t) for p, t in found if t not in drop]
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

    # ----------------------------------------------------------- wiring helpers
    @staticmethod
    def _make_file(
        texture: str,
        *,
        alpha_is_luminance: Optional[int] = None,
        color_space: str = "Raw",
    ) -> str:
        """Create a dedicated ``file`` node for the bridge.

        The bridge never shares the base material's file nodes (Arnold and
        Stingray need conflicting ``colorSpace`` / ``alphaIsLuminance``), so
        every map gets its own node — created here so the per-map handlers don't
        each repeat the boilerplate.

        ``color_space`` defaults to ``Raw`` (correct for every data map —
        normals, packed masks, roughness, etc.). Colour maps that are authored
        sRGB (base colour, emissive) MUST pass ``color_space="sRGB"`` or the
        Arnold preview renders too dark and no longer matches the game material.
        """
        kwargs = dict(
            fileTextureName=texture,
            colorSpace=color_space,
            ignoreColorSpaceFileRules=1,
            name=ptk.format_path(texture, section="name"),
        )
        if alpha_is_luminance is not None:
            kwargs["alphaIsLuminance"] = alpha_is_luminance
        return NodeUtils.create_render_node("file", **kwargs)

    @staticmethod
    def _chan_plug(file_node: str, channel: str) -> str:
        """Output plug for a colour channel ('R'/'G'/'B') or 'A' (the alpha)."""
        if channel == "A":
            return f"{file_node}.outAlpha"
        return f"{file_node}.outColor{channel}"

    @staticmethod
    def _wire_roughness(ai_node: str, source_plug: str, *, invert: bool = False) -> None:
        """Drive ``specularRoughness`` (+ transmission blur) from a scalar plug.

        Inserts a ``reverse`` node when the source is smoothness / glossiness
        (the inverse of roughness).
        """
        if invert:
            reverse_node = NodeUtils.create_render_node(
                "reverse", name="invertSmoothness"
            )
            cmds.connectAttr(source_plug, f"{reverse_node}.inputX", force=True)
            source_plug = f"{reverse_node}.outputX"
        cmds.connectAttr(source_plug, f"{ai_node}.specularRoughness", force=True)
        cmds.connectAttr(
            source_plug, f"{ai_node}.transmissionExtraRoughness", force=True
        )

    @staticmethod
    def _wire_bump(bump_node: str, source_plug: str, *, tangent: bool) -> None:
        """Feed a scalar plug into the ``bump2d`` (tangent normals vs. bump/height)."""
        cmds.setAttr(f"{bump_node}.bumpInterp", 1 if tangent else 0)
        cmds.connectAttr(source_plug, f"{bump_node}.bumpValue", force=True)

    def _connect_packed(
        self, file_node: str, ai_node: str, aiMult_node: str, layout: dict
    ) -> None:
        """Route a packed mask (ORM / MRAO / MSAO / Metallic_Smoothness).

        ``layout`` (see :attr:`_PACKED_LAYOUTS`) names the channel for each
        property, so this one routine replaces four near-identical branches.
        """
        cmds.connectAttr(
            self._chan_plug(file_node, layout["metal"]),
            f"{ai_node}.metalness",
            force=True,
        )
        rough_ch, invert = layout["rough"]
        self._wire_roughness(
            ai_node, self._chan_plug(file_node, rough_ch), invert=invert
        )
        ao_ch = layout.get("ao")
        if ao_ch:
            # Broadcast the single AO channel uniformly into the baseColor
            # multiply (input2R/G/B) — never the whole packed outColor (see
            # _PACKED_LAYOUTS: that greens every non-metal surface).
            src = self._chan_plug(file_node, ao_ch)
            for c in "RGB":
                cmds.connectAttr(src, f"{aiMult_node}.input2{c}", force=True)

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
        # Normals (Normal / Normal_OpenGL / Normal_DirectX) → tangent-space bump.
        if texture_type.startswith("Normal"):
            f = self._make_file(texture, alpha_is_luminance=1)
            self._wire_bump(bump_node, f"{f}.outAlpha", tangent=True)
            return True

        # Packed masks (ORM / MRAO / MSAO / Metallic_Smoothness).
        layout = self._PACKED_LAYOUTS.get(texture_type)
        if layout:
            f = self._make_file(texture, alpha_is_luminance=layout["aIL"])
            self._connect_packed(f, ai_node, aiMult_node, layout)
            return True

        # Colour → base color (via the AO multiply's input1). sRGB-authored, so
        # read it as sRGB — Raw would render the albedo too dark and break parity
        # with the game material.
        if texture_type in ("Base_Color", "Diffuse", "Albedo_Transparency"):
            f = self._make_file(texture, color_space="sRGB")
            cmds.connectAttr(f"{f}.outColor", f"{aiMult_node}.input1", force=True)
            if texture_type == "Albedo_Transparency":
                # Alpha → standard-surface opacity (R/G/B).
                for c in "RGB":
                    cmds.connectAttr(
                        f"{f}.outAlpha", f"{ai_node}.opacity{c}", force=True
                    )
            return True

        if texture_type == "Roughness":
            f = self._make_file(texture, alpha_is_luminance=1)
            self._wire_roughness(ai_node, f"{f}.outAlpha")
            return True

        # Glossiness / Smoothness are the inverse of roughness.
        if texture_type in ("Glossiness", "Smoothness"):
            f = self._make_file(texture, alpha_is_luminance=1)
            self._wire_roughness(ai_node, f"{f}.outAlpha", invert=True)
            return True

        # Specular has no aiStandardSurface analogue — use its luminance as a
        # metalness proxy (the registry's own Metallic fallback).
        if texture_type in ("Metallic", "Specular"):
            f = self._make_file(texture, alpha_is_luminance=1)
            cmds.connectAttr(f"{f}.outAlpha", f"{ai_node}.metalness", force=True)
            return True

        if texture_type == "Emissive":
            # Emissive colour is sRGB-authored (see base colour).
            f = self._make_file(texture, color_space="sRGB")
            cmds.connectAttr(f"{f}.outAlpha", f"{ai_node}.emission", force=True)
            cmds.connectAttr(f"{f}.outColor", f"{ai_node}.emissionColor", force=True)
            return True

        # Bump / Height → object-space bump (not tangent normals).
        if texture_type in ("Bump", "Height"):
            f = self._make_file(texture, alpha_is_luminance=1)
            self._wire_bump(bump_node, f"{f}.outAlpha", tangent=False)
            return True

        if texture_type == "Ambient_Occlusion":
            f = self._make_file(texture)
            cmds.connectAttr(f"{f}.outColor", f"{aiMult_node}.input2", force=True)
            return True

        if texture_type == "Opacity":
            f = self._make_file(texture, alpha_is_luminance=1)
            cmds.connectAttr(f"{f}.outColor", f"{ai_node}.opacity", force=True)
            return True

        return False


class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``arnold_bridge.ui`` panel.

    A thin driver over :class:`ArnoldBridge` — no bridge logic lives here.
    Add / Remove operate on the scope picked in the combobox (selected objects'
    materials, or every scene material). Force makes Add rebuild a material that
    already has a network instead of skipping it.
    """

    # (label, op-kwarg-resolver-key). The combobox shows the labels; the action
    # methods map the current label to a scope via _scope_kwargs().
    _SCOPE_LABELS = ("Selected Objects", "All Scene Materials")
    _VERB = {"add": "Added", "remove": "Removed"}

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
                        "<b>Add Network</b> mirrors each material's textures onto a "
                        "new Arnold shader wired to the shading group's "
                        "<i>aiSurfaceShader</i> slot.",
                        "<b>Remove Network</b> deletes the Arnold network (the "
                        "exported material is untouched). Enable <b>Force</b> to "
                        "rebuild a material that already has a network.",
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
        """Add Network."""
        self._run("add", force=self.ui.chk000.isChecked())

    def b001(self) -> None:
        """Remove Network."""
        self._run("remove")

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
            self.ui.footer.setText("No materials have an Arnold network.")
            return
        cmds.select(bridged, replace=True)
        self.ui.footer.setText(
            f"Selected {len(bridged)} material(s) with an Arnold network."
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
            text=f"{op.capitalize()} Arnold network ({desc})…"
        ):
            result = getattr(self._bridge, op)(**kwargs)

        n = len(result)
        self.ui.footer.setText(
            f"{self._VERB[op]} {n} Arnold network{'' if n == 1 else 's'} ({desc})."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("arnold_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
