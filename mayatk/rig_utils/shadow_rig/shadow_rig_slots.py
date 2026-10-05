# !/usr/bin/python
# coding=utf-8
"""Shadow Rig panel — the Switchboard slots for ``shadow_rig.ui``.

A thin driver over :class:`ShadowRig`; the rig types the panel offers are
the ``RIG_TYPES`` strategy table on :class:`ShadowRigSlots`.
"""

import os
import math


try:
    import maya.cmds as cmds
except ImportError:
    pass

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.preview import Preview, OperationError
from mayatk.rig_utils.shadow_rig._shadow_rig import ShadowRig


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

    def b001_init(self, widget):
        """Reset to Defaults, on uitk's shared reset grammar (Shift+Click saves the
        current values as the defaults, Ctrl+Shift+Click forgets them)."""
        from uitk.managers.reset_gesture import ResetGesture

        self._reset_gesture = ResetGesture(widget)

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
        from mayatk.rig_utils.shadow_rig.shadow_preview import ShadowPreview

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
        from mayatk.rig_utils.shadow_rig.shadow_preview import ShadowPreview

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
        from mayatk.rig_utils.shadow_rig.shadow_preview import ShadowPreview

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
