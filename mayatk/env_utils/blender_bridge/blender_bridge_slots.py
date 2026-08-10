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

from pythontk.core_utils.script_template import ROUND_TRIP

from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

# From this package:
from mayatk.env_utils.blender_bridge._blender_bridge import BlenderBridge, _TEMPLATE_DIR
from mayatk.env_utils.blender_bridge import parameters as _params

_PRESETS_ROOT = Path("mayatk/blender_bridge")

#: Modes the panel runs BLOCKING + headless, as opposed to launching a Blender. Read off
#: the spec that serves them rather than listed here: they are exactly the bridge's
#: blocking route, and a mode added there but missed here would fall through to
#: ``send()`` and launch a GUI Blender on a script with no ``__OUT_FILE__``. What the
#: two have in common is the wait; they differ only in what becomes of the artifact
#: (``save_as`` hands it to the artist, ``round_trip`` feeds the bridge's return leg).
_BLOCKING_MODES = tuple(BlenderBridge.run_spec.modes)


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
                "bake_lightmaps (round_trip) — send to bake",
                [
                    "Bakes Cycles lightmaps in a <b>headless</b> Blender and brings them "
                    "<b>back into this scene</b>. One click, no save dialog; Maya blocks "
                    "until it finishes (a real bake takes minutes).",
                    "Blender does the whole lightmap job — UVs, bake, denoise, atlas — and "
                    "on return the layout is written onto your meshes and the maps wired in "
                    "<b>alongside your existing materials</b>: material and UV0 untouched, "
                    "lightmap on UV1. Undo with <b>Revert to Source</b> in the Lightmap "
                    "Baker panel.",
                    "<b>The bake targets no platform.</b> Once committed, every export "
                    "ships it on its own: FBX → Unity (native lightmap binding) and "
                    "GLB → web viewer — neither needs to know the bake happened.",
                    "<b>The Maya viewport will not show it</b>, by design: the commit is "
                    "non-destructive and builds no file node, so your material is exactly "
                    "as you left it. What lands in the scene is the lightmap UV set on "
                    "channel 1 plus a marker on each transform. To see it lit, click "
                    "<b>WebXR Preview</b> in the <b>Rendering</b> panel — it reads the "
                    "committed bake on its own, no bake-time setting needed — or open "
                    "the EXR in <b>Lightmap Folder</b>.",
                    "<b>Instances are fully supported</b> — and preserved. Every copy is "
                    "baked separately (each stands in different light) and gets its own "
                    "patch of the atlas via a per-instance rect, exactly Unity's native "
                    "<i>lightmapScaleOffset</i> model; the shared mesh and your scene's "
                    "instancing are untouched.",
                    "<b>Quality</b> is a preset tier (Preview / Quest / Desktop / Hero); "
                    "<b>Lightmap Folder</b> defaults to the project's sourceimages.",
                    "<b>Your scene's lighting is what gets baked.</b> <b>Include Lights</b> "
                    "(on) exports the Maya lights and bakes with them; <b>Scene Light "
                    "Strength</b> rebalances their power, since Maya intensity and Cycles "
                    "watts are different units and the FBX crossing translates through "
                    "both — the bake log prints each light's final wattage, so tune from "
                    "that rather than guessing.",
                    "Your lights ride <i>beside</i> the FBX rather than inside it and are "
                    "rebuilt in Blender — an FBX light crashes Blender 5.1's importer, and "
                    "FBX can't represent Arnold lights at any version, so this route is "
                    "the one that carries <i>aiAreaLight</i> too. Ambient and volume lights "
                    "have no Cycles equivalent and are skipped.",
                    "A <b>StingrayPBS IBL</b> is not a light object at all, so it can't come "
                    "across — use <b>Environment HDRI</b> and/or real scene lights. The two "
                    "sources compose. With neither the bake comes out black — and says so in "
                    "the log rather than leaving you to wonder.",
                    "Need lights for a scene that has none? <b>Lights From Geometry</b> in the "
                    "<b>Lighting</b> panel builds real, artist-owned area lights from your "
                    "light-fixture meshes (or their selected lens faces) — they then bake here "
                    "like any other light. An emissive <i>map</i> is only an appearance — it is "
                    "not the room's light source.",
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

        # A blocking template writes a FILE instead of launching a Blender, so it needs a
        # destination that send() has no way to supply -- then takes the blocking route.
        # Without this branch the run launches and fails on an empty __OUT_FILE__.
        # ``auto`` templates derive the path (their artifact is pipeline plumbing, like
        # the bake's return manifest); only artist-named artifacts get the save dialog.
        template_file = BlenderBridge.template_path(template)
        auto_output = False
        if mode in _BLOCKING_MODES:
            auto_output = BlenderBridge.template_output_mode(template_file) == "auto"
            out_path = (
                self.bridge.default_output_path(template)
                if auto_output
                else self._prompt_save_path(template)
            )
            if not out_path:
                self.bridge.logger.info("Cancelled.")
                return

        self.bridge.logger.info(
            f"--- {template} ({mode}) on {len(selection)} object(s) ---"
        )
        try:
            if mode in _BLOCKING_MODES:
                # Blocking and headless: the artist waits on a bake, so say so, and report
                # the artifact rather than leaving them to guess whether it worked. The
                # timeout comes from the template — the shared 600s default is shorter than
                # a real bake, and a timeout kill leaves no artifact, so it would present
                # as a silent bake failure.
                #
                # Same run either way but for the entry point: ``round_trip`` puts what
                # comes back through the bridge's return leg (_ingest), ``save_as`` stops
                # at the written file. Dispatching on the mode keeps that the template's
                # declaration to make rather than a name match here.
                common = dict(
                    objects=selection,
                    template=template,
                    params=params,
                    timeout=BlenderBridge.template_timeout(template_file),
                )
                with self.sb.progress(
                    text=f"Working: {template} (this can take minutes)"
                ):
                    result = (
                        self.bridge.round_trip(out=out_path, **common)
                        if mode == ROUND_TRIP
                        else self.bridge.save_as(out_path, **common)
                    )
                if result and result.get("output"):
                    if auto_output:
                        # A derived artifact is plumbing in tracked temp storage; announcing
                        # its path invites the artist to go looking for a deliverable that
                        # isn't one. What the run produced is reported by the step below.
                        self.bridge.logger.info(f"Artifact: {result['output']}")
                    else:
                        self.bridge.logger.success(f"Wrote {result['output']}")
                    # The return leg (reassembly onto the scene) already ran inside the
                    # bridge's _ingest hook -- the panel only reports it.
                    wired = result.get("reassembled")
                    if wired:
                        self.bridge.logger.success(
                            f"{len(wired)} lightmap binding(s) wired into the scene."
                        )
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
        preferred = BlenderBridge.template_output_ext(
            BlenderBridge.template_path(template)
        )
        # A declared artifact name may carry a compound suffix (".lightmaps.json"), which
        # the bridge validates on its FINAL extension -- so compare on that, or a valid
        # declaration would read as a bug and get warned about below.
        final = Path(preferred).suffix or preferred
        if final in extensions:  # show the likely one first
            extensions.remove(final)
            extensions.insert(0, final)
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
