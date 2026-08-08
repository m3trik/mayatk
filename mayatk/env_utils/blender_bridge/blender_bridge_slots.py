# !/usr/bin/python
# coding=utf-8
"""Slots for the Blender bridge panel.

Thin subclass of :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase` (which subclasses
uitk's :class:`BridgeSlotsBase`) -- the panel machinery (template combo, dynamic parameter widgets,
user presets, log routing, per-template description) lives upstream. This file owns only the
Blender-specific bits: the bridge factory, the ``(template, mode)`` listing, the header menu, and
the ``b000`` send action. Mirrors ``marmoset_bridge_slots`` / the blendertk ``maya_bridge`` slots.

``REQUIRE_OUTPUT_DIR = False`` -- the bridge round-trips through a temp FBX it manages internally;
there's no user-visible artifact to point at, so the base skips the Output Dir row.
"""

import traceback
from pathlib import Path

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from pythontk.core_utils.script_template import SAVE_AS

from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

# From this package:
from mayatk.env_utils.blender_bridge._blender_bridge import BlenderBridge, _TEMPLATE_DIR
from mayatk.env_utils.blender_bridge import parameters as _params


_PRESETS_ROOT = Path("mayatk/blender_bridge")


class BlenderBridgeSlots(MayaBridgeSlotsBase):
    """Slots wired to ``blender_bridge.ui`` via :class:`MayaBridgeSlotsBase`.

    Discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler` so
    ``marking_menu.show("blender_bridge")`` works from anywhere with no explicit registration.
    """

    UI_NAME = "blender_bridge"
    PRESETS_ROOT = _PRESETS_ROOT
    LOG_TAG = "blender_bridge"
    REQUIRE_OUTPUT_DIR = False

    # Uses the base's default header menu (Open Templates / Refresh / Clear
    # Log); only the help differs, so it's declared as data.
    HELP_SPEC = {
        "title": "Blender Bridge",
        "body": "Send the selected objects to a fresh Blender. Maya exports the selection as "
        "FBX; Blender runs the chosen import template with your parameter values "
        "substituted in.",
        "steps": [
            "Select one or more objects.",
            "Toggle the import options (clear scene, frame in view, materials, …).",
            "Click <b>Send to Blender</b>.",
        ],
        "sections": [
            (
                "import (send_to) — options",
                [
                    "<b>Clear Scene First</b> — delete the current scene's objects before importing "
                    "(clean slate). Off imports additively.",
                    "<b>Frame in View</b> — after import, select &amp; frame the new objects with "
                    "material-preview shading.",
                ],
            ),
            (
                "bake_lightmaps (save_as) — WebXR deliverable",
                [
                    "Bakes Cycles lightmaps in a <b>headless</b> Blender and writes a "
                    "browser-ready <b>.glb</b>. You are asked where to save it; Maya blocks "
                    "until it finishes (a real bake takes minutes).",
                    "<b>It comes back.</b> Blender does the whole lightmap job — UVs, "
                    "bake, atlas — and on return the layout it produced is written onto "
                    "your meshes and the maps are wired in <b>alongside your existing "
                    "maps</b>: material and UV0 untouched, lightmap on UV1. Undo it with "
                    "<b>Revert to Source</b> in the Lightmap Baker panel.",
                    "<b>Lightmap Folder</b> is where the .exr lightmaps land. They become "
                    "textures this scene references, so they default to the project's "
                    "sourceimages rather than sitting beside the .glb.",
                    "<b>Lighting is required.</b> A scene lit by StingrayPBS IBL exports no "
                    "lights at all — its cubemaps don't travel through FBX — so set an "
                    "<b>Environment HDRI</b>, leave <b>Lights From Fixtures</b> on, or both. "
                    "With neither, the bake comes out black.",
                    "<b>Lights From Fixtures</b> builds real area lights from your light-fixture "
                    "meshes (matched by <b>Fixture Name Contains</b>). An emissive <i>map</i> is "
                    "only an appearance — it is not the room's light source.",
                    "<b>Max Texture Size</b> / <b>Image Format</b> are the file budget: texture "
                    "bytes are the deliverable's size (a 4K PBR set measured 96 MB vs 1.7 MB at "
                    "2048 + WebP). Your lightmaps are exempt — they ship at the resolution you set.",
                ],
            ),
        ],
        "notes": [
            "Each template exposes only its own options, so the panel changes when you switch "
            "entries. The dropdown also picks up custom templates dropped into the templates "
            "folder (use <code>__KEY__</code> tokens from <i>parameters.py</i>), then click "
            "<b>Refresh Templates</b>.",
            "A fresh Blender is launched every time; your running Blender is never touched.",
        ],
    }

    # ------------------------------------------------------------------ base-class hooks
    @property
    def params_module(self):
        return _params.Parameters

    @property
    def template_dir(self) -> Path:
        return _TEMPLATE_DIR

    def make_bridge(self) -> BlenderBridge:
        return BlenderBridge()

    def list_template_modes(self):
        return BlenderBridge.list_template_modes()

    # ------------------------------------------------------------------ b000 -- send
    def b000(self):
        """Send the selected objects to Blender with the chosen template."""
        if cmds is None:
            self.bridge.logger.error(
                "Maya is not available; cannot run the Blender bridge."
            )
            return

        # Scope (Selected / Entire Scene / Visible Only) resolves via the shared
        # bridge-slots base; it logs the scope-aware reason when empty.
        params = self.collect_param_values()
        selection = self.scoped_objects(params)
        if not selection:
            return

        pair = self._selected_template_mode()
        if not pair:
            self.bridge.logger.warning(
                "No template chosen. Pick one from the dropdown above."
            )
            return
        template, mode = pair

        if not self.bridge.blender_path:
            self.bridge.logger.error(
                "Blender not found. Install Blender or set $BLENDER_EXE / "
                "BlenderBridge.blender_path."
            )
            return

        # A save_as template writes a FILE instead of launching a Blender, so it needs a
        # destination that send() has no way to supply -- ask, then take the blocking
        # route. Without this branch the run launches and fails on an empty __OUT_FILE__.
        if mode == SAVE_AS:
            out_path = self._prompt_save_path(template)
            if not out_path:
                self.bridge.logger.info("Cancelled.")
                return

        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )
        try:
            if mode == SAVE_AS:
                # Blocking and headless: the artist waits on a bake, so say so, and report
                # the artifact rather than leaving them to guess whether it worked. The
                # timeout comes from the template — the shared 600s default is shorter than
                # a real bake, and a timeout kill leaves no artifact, so it would present
                # as a silent bake failure.
                with self.sb.progress(text=f"Working: {template} (this can take minutes)"):
                    result = self.bridge.save_as(
                        out_path,
                        objects=selection,
                        template=template,
                        params=params,
                        timeout=BlenderBridge.template_timeout(
                            BlenderBridge.template_path(template)
                        ),
                    )
                if result and result.get("output"):
                    self.bridge.logger.success(f"Wrote {result['output']}")
                    # A run that left a lightmap sidecar is a ROUND TRIP, not just an
                    # export -- wire the bake back into this scene. Keyed off the
                    # artifact rather than the template's name, so it stays
                    # template-agnostic and any future template that returns lightmaps
                    # gets the reassembly for free.
                    sidecar = Path(
                        result["output"] + BlenderBridge.RETURN_MANIFEST_SUFFIX
                    )
                    if sidecar.is_file():
                        self.bridge.reassemble_lightmaps(result["output"], selection)
                else:
                    self.bridge.logger.error(
                        f"{template} produced no file -- see the log above."
                    )
                return

            with self.sb.progress(text=f"Working: Send to Blender ({template})"):
                self.bridge.send(
                    objects=selection,
                    template=template,
                    mode=mode,
                    params=params,
                )
        except Exception:
            self.bridge.logger.error("Bridge raised:\n" + traceback.format_exc())

    def _prompt_save_path(self, template: str) -> str:
        """Ask where a ``save_as`` template should write, seeded from the scene name.

        The template declares its own artifact format (``BRIDGE_OUTPUT_EXT``) — matching a
        name substring here would quietly offer the wrong one to the next template added.
        The bridge's accepted extensions drive the filter, so the dialog cannot offer a
        format ``resolve_save_path`` would then rewrite.
        """
        extensions = list(BlenderBridge.save_extensions) or [".blend"]
        preferred = BlenderBridge.template_output_ext(BlenderBridge.template_path(template))
        if preferred in extensions:  # show the likely one first
            extensions.remove(preferred)
            extensions.insert(0, preferred)
        else:
            # Offering it anyway would be worse than ignoring it: resolve_save_path
            # APPENDS save_extensions[0] to anything it does not recognise, so the artist
            # would pick "room.usd" and silently get "room.usd.blend". A declaration the
            # bridge cannot accept is a bug in the template, so say so and fall back.
            self.bridge.logger.warning(
                f"{template} declares BRIDGE_OUTPUT_EXT {preferred!r}, which is not in "
                f"BlenderBridge.save_extensions {BlenderBridge.save_extensions}; "
                f"offering {extensions[0]} instead."
            )
            preferred = extensions[0]

        scene = cmds.file(query=True, sceneName=True) or ""
        stem = Path(scene).stem or "untitled"
        start_dir = self.resolved_output_dir() or (
            str(Path(scene).parent) if scene else str(Path.home())
        )
        return (
            self.sb.save_file_dialog(
                title=f"Save {template} output",
                start_dir=str(Path(start_dir) / f"{stem}{preferred}"),
                file_types=[f"*{ext}" for ext in extensions],
                filter_description="Blender bridge output",
            )
            or ""
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("blender_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
