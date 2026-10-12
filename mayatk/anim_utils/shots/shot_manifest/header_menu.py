# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest's header menu: display options (Long Names, Hide Missing
Shots), Local Edits' View Original Sheet and Reset, the Build options (Rebuild
Edited Shots, a Key / Marker / Off choice per behavior), the actions (Expand,
Colors, the sibling panels) and the panel's help.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

from .behaviors import Behaviors
from .manifest_data import BEHAVIOR_STATUS_COLORS, PASTEL_STATUS, ManifestData
from .manifest_host import ManifestHost


class HeaderMenuMixin:
    """The header menu, mixed into ``ShotManifestController``.

    Uses the controller's ``ui`` (``header``), ``sb``, ``_settings``,
    ``_editor``, ``_steps``, ``_last_results``, ``assess()``, ``_redraw()``,
    ``_shown_steps()``, ``_update_build_button()`` and ``_set_footer()``.
    """

    _COLOR_SETTINGS_NS = "ShotManifest/colors"
    #: What Build does with a behavior type (the header menu's choice per
    #: type): key it, mark it on the timeline instead, or leave it alone.
    BEHAVIOR_MODES = (("Key", "key"), ("Marker", "marker"), ("Off", "off"))

    def _setup_header_menu(self) -> None:
        """Configure the header option menu.

        Generation settings (threshold, mode) now live in the shared
        ``shots.ui`` panel, opened via the Settings button.
        """
        menu = self.ui.header.menu
        menu.setTitle("Shot Manifest:")

        chk_long = menu.add(
            "QCheckBox",
            setText="Long Names",
            setChecked=bool(self._settings.value("long_names", False)),
            setToolTip="Show full DAG paths instead of leaf node names.",
        )
        chk_long.toggled.connect(self._on_long_names_toggled)
        self._chk_hide_missing = chk_hide = menu.add(
            "QCheckBox",
            setText="Hide Missing Shots",
            setObjectName="chk_hide_missing",
            setChecked=self._hide_missing,
            setToolTip=(
                "Leave out the steps that have no shot -- a sheet's steps that\n"
                "need no animation, or are not animated yet. Assess and Build\n"
                "skip them too: Build creates no shot for a step it does not\n"
                "show, and keeps updating the shots there are. A step you add\n"
                "(Local Edits) stays shown, and so does every step while none\n"
                "has a shot yet."
            ),
        )
        chk_hide.toggled.connect(self._on_hide_missing_toggled)
        self._editor.add_header_options(menu)
        self._add_build_options(menu)

        menu.add("Separator", setTitle="Actions")
        menu.add(
            "QPushButton",
            setText="Expand All Missing",
            setObjectName="btn_expand_missing",
            setToolTip="Expand every step row that has missing objects or behaviors.",
        )
        menu.add(
            "QPushButton",
            setText="Expand All Extra",
            setObjectName="btn_expand_extra",
            setToolTip="Expand every step row that has scene-discovered objects not in the CSV.",
        )
        menu.add(
            "QPushButton",
            setText="Colors\u2026",
            setObjectName="btn_manifest_colors",
            setToolTip="Edit manifest status colors.",
        ).released.connect(self._open_color_editor)
        menu.add(
            "QPushButton",
            setText="Audio Clips\u2026",
            setObjectName="btn_audio_clips",
            setToolTip="Open the Audio Clips editor to load, key, and\nmanage audio tracks used by this manifest.",
        ).released.connect(self._open_audio_clips)
        menu.add(
            "QPushButton",
            setText="Render Effects\u2026",
            setObjectName="btn_render_effects",
            setToolTip="Open Render Effects to key or revise the opacity and highlight\nchannels this manifest's fade and highlight behaviors key.",
        ).released.connect(self._open_render_effects)
        menu.add(
            "QPushButton",
            setText="Shots\u2026",
            setObjectName="btn_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )

        self.ui.header.set_help_text(
            self.sb.tooltip.fmt(
                title="Shot Manifest",
                body="Check the scene's shots against a build sheet, or review the shots the scene already has.",
                sections=[
                    (
                        "Quick Start \u2014 Build Sheet",
                        [
                            "Browse to a CSV file, or paste a web address (a Google "
                            "Sheets share link works) and press Enter.",
                            "Review parsed steps in the table; edit ranges as needed.",
                            "Click <b>Build</b> to create shots with behaviors applied.",
                            "Click <b>Assess</b> to verify completeness.",
                            "The template's options (under its picker in the header "
                            "menu) adapt it to the sheet: step-ID style (Auto reads "
                            "<code>A01.)</code> and <code>A01</code> / "
                            "<code>IN_01</code> sheets alike), audio, and "
                            "<b>Auto-fill Missing Assets</b> -- then <b>Export</b> "
                            "hands them, with the rest, to the sheet's owner.",
                        ],
                    ),
                    (
                        "No Build Sheet",
                        [
                            "Leave the field empty (or clear it) \u2014 the table shows the "
                            "scene's own shots with their descriptions; <b>Assess</b> checks "
                            "them for missing objects and behaviors.",
                            "A scene with no shots yet is generated from animation using "
                            "the settings in Shot Settings; refine ranges, then <b>Build</b>.",
                            "A scene built from a sheet re-opens checked against that sheet.",
                        ],
                    ),
                    (
                        "Table Columns",
                        [
                            "<b>Step</b> \u2014 Step ID (e.g. A01), or the name Local Edits gave it.",
                            "<b>Section</b> \u2014 Read-only grouping label from the sheet.",
                            "<b>Description</b> \u2014 Audio narration or step notes.",
                            "<b>Behaviors</b> \u2014 Per-object actions; click the child row label to toggle.",
                            "<b>Start / End</b> \u2014 Frame range. Solid text = user-entered; dim italic = auto-filled.",
                        ],
                    ),
                    (
                        "Editing &amp; Actions",
                        [
                            "Double-click Start or End to type a frame. Downstream steps re-flow.",
                            "Right-click a range cell: Set Start to Current Frame, Auto-fill from Gaps, Clear Range.",
                            "<b>Assess</b> \u2014 Read-only comparison; red tint = missing, grey = locked or edited, normal = valid. While it shows it stays current: a shot edit made anywhere, a Local Edit, a hide or a sheet read again runs it again.",
                            "<b>Build</b> \u2014 Create or update shots from loaded steps. Locked shots are never modified, "
                            "and neither is a shot edited by hand since its last Build (renamed, resized, its members "
                            "or description changed) unless <b>Rebuild Edited Shots</b> is on in the header menu; "
                            "Assess shows such a step as edited. "
                            "Fades and highlights are keyed from the scene's effect recipe (Render Effects); "
                            "Build re-keys those an older recipe made and removes the keys of behaviors the "
                            "doc dropped, so it stays enabled while either is pending. A behavior type set "
                            "to <b>Marker</b> is marked on the Shot Sequencer's timeline instead.",
                            "<b>Export</b> \u2014 a live preview of the export: "
                            "the rows the table shows, or the selected ones, as CSV, "
                            "JSON or Markdown -- what the table says, Local Edits, "
                            "frames and status included. Set the format, rows, object "
                            "layout and columns beside the text and it updates as you "
                            "go; then <b>Copy</b> it or <b>Save As</b> a file. It never "
                            "writes the loaded sheet.",
                            "Right-click a step row: Open in Shot Sequencer or Shots (once built). "
                            "An object row adds Show in "
                            "Outliner, Copy, Apply its behaviors, and Fade / Highlight '...' -- Render "
                            "Effects on that object alone, where Key re-applies its behaviors; an audio "
                            "row opens its clip in Audio Clips.",
                        ],
                    ),
                    (
                        "When the Scene Moves On",
                        [
                            "Every shot lists its members: a step's shot shows those the "
                            "step does not list (tinted), with no Assess needed.",
                            "A shot no step pairs with -- split off or added in the Shot "
                            "Sequencer -- is a 'not in doc' row where the timeline has it. "
                            "Right-click it: open it, reveal its members, <b>Add to the "
                            "Manifest</b> (a step of its own, a Local Edit), <b>Pair with "
                            "Step</b> (the shot of a step that has none), or Remove Shot "
                            "-- its keys stay in the scene.",
                            "<b>Lock</b> a step, a selection or a whole section "
                            "(right-click): final -- Build never changes its shot and its "
                            "row takes no Local Edits. Unlock to revise, then lock again.",
                            "<b>Hide Missing Shots</b> (header menu) leaves out the steps "
                            "with no shot once any step has one; Assess and Build skip them, "
                            "so Build only keeps the shots there are up to date.",
                        ],
                    ),
                    (
                        "Build Options (header menu)",
                        [
                            "<b>Rebuild Edited Shots</b> \u2014 off: a shot edited by hand "
                            "since its last Build is kept as it is, its behaviors not re-keyed. "
                            "On: Build rebuilds it from the doc like any other.",
                            "<b>Behaviors</b> \u2014 a Key / Marker / Off choice per "
                            "behavior type. <b>Key</b>: Build keys it. <b>Marker</b>: Build "
                            "keys nothing -- it puts a marker on the Shot Sequencer's timeline "
                            "where the keys would go, naming the objects, for keying by hand; "
                            "Assess checks the marker is there, and its labels show "
                            f"{ManifestData.MARKED_GLYPH}. <b>Off</b>: neither keyed nor "
                            "marked, and not reported by Assess; its labels show it struck "
                            "through. Marker and Off leave the keys a type already has.",
                            "Both are kept per user, for every scene.",
                        ],
                    ),
                    (
                        "Local Edits",
                        [
                            "Revise the manifest without touching the sheet: edits are "
                            "kept with the scene, always applied, and Build follows them. "
                            "A locked step takes none until it is unlocked.",
                            "<b>View Original Sheet</b> (header menu) shows the sheet as "
                            "delivered, beside your edits, to compare -- read-only, and "
                            "Build is off while it shows.  Turn it off to return; nothing "
                            "is lost.",
                            "Double-click a step's name or description, or an object's "
                            "name, to edit it.  An object's name says which scene object "
                            "the step means -- the object itself is never renamed.",
                            "Right-click: Add Step, Hide (a flyout: the step, the "
                            "selected steps, its section, or every step without a shot) "
                            "/ Show Hidden (a built step stays: Build never removes a "
                            "shot), Move an object to another step, Rename All of one "
                            "object, Revert.  Behavior ticks are edits too.",
                            "Renaming a built step renames its shot on the next Build -- "
                            "and the clip it exports as.  Edited cells are italic; their "
                            "tooltip shows the sheet's value.  <b>Reset Local Edits</b> "
                            "drops them all.",
                        ],
                    ),
                ],
            )
        )

    @property
    def _rebuild_edited(self) -> bool:
        """Header menu > Rebuild Edited Shots (off by default, kept per user):
        whether a build rebuilds a shot edited by hand since its last build
        (``ShotManifest.edited_by_hand``) instead of keeping it."""
        return bool(self._settings.value("rebuild_edited", False))

    def _behavior_set(self, key: str) -> frozenset:
        """A behavior-type list the header menu keeps per user
        (``disabled_behaviors``, ``marked_behaviors``).

        Read once and kept in step by :meth:`_set_behavior_mode`: every
        behavior label asks, on every table rebuild.
        """
        names = self._behavior_sets.get(key)
        if names is None:
            raw = self._settings.value(key, [])
            names = frozenset(raw if isinstance(raw, list) else ())
            self._behavior_sets[key] = names
        return names

    def _disabled_behaviors(self) -> frozenset:
        """The behavior types set to Off in the header menu."""
        return self._behavior_set("disabled_behaviors")

    def _marked_behaviors(self) -> frozenset:
        """The behavior types set to Marker in the header menu: Build marks
        them on the timeline instead of keying them."""
        return self._behavior_set("marked_behaviors") - self._disabled_behaviors()

    def _behavior_mode(self, name: str) -> str:
        """``"key"``, ``"marker"`` or ``"off"``: what Build does with *name*."""
        if name in self._disabled_behaviors():
            return "off"
        return "marker" if name in self._marked_behaviors() else "key"

    def _add_build_options(self, menu) -> None:
        """The header menu's Build options: Rebuild Edited Shots, then a
        Key / Marker / Off choice per behavior template.

        Global, not per scene: how a build treats a hand's edits and what it
        does with each behavior are how this user works (2026-10-07: "make
        sure that the manifest does not override edited shots on re-build by
        default. this can be a separate global option"; "ability to
        disable/enable certain behaviors"; 2026-10-11: "a third option ... of
        adding a marker in the sequencer instead of keying").
        """
        menu.add("Separator", setTitle="Build")
        self._chk_rebuild_edited = menu.add(
            "QCheckBox",
            setText="Rebuild Edited Shots",
            setChecked=self._rebuild_edited,
            setToolTip=(
                "Off: a shot edited by hand since its last Build -- renamed,\n"
                "resized, its members or description changed -- is kept as it is\n"
                "and its behaviors are not re-keyed.\n"
                "On: Build rebuilds it from the doc like any other shot."
            ),
        )
        self._chk_rebuild_edited.toggled.connect(self._on_rebuild_edited_toggled)
        menu.add("Separator", setTitle="Behaviors")
        self._behavior_modes = {}
        for name in Behaviors.list_behaviors():
            label = ManifestData.fmt_behavior(name)
            cmb = menu.add(
                "QComboBox",
                setCurrentTextPrefix=f"{label}:  ",
                setToolTip=(
                    f"Key: Build keys {label} wherever the doc asks for it.\n"
                    "Marker: Build keys nothing -- it puts a marker on the Shot\n"
                    "Sequencer's timeline where the keys would go, naming the\n"
                    "objects, and Assess checks the marker is there.\n"
                    "Off: Build neither keys nor marks it, and Assess does not\n"
                    "report it.\n"
                    "Marker and Off leave the keys it already has."
                ),
            )
            for text, mode in self.BEHAVIOR_MODES:
                cmb.addItem(text, mode)
            cmb.setCurrentIndex(cmb.findData(self._behavior_mode(name)))
            cmb.currentIndexChanged.connect(
                lambda _i, n=name, c=cmb: self._set_behavior_mode(n, c.currentData())
            )
            self._behavior_modes[name] = cmb

    def _on_rebuild_edited_toggled(self, checked: bool) -> None:
        """Persist Rebuild Edited Shots; what Build would do changed."""
        self._settings.setValue("rebuild_edited", bool(checked))
        self._build_options_changed()

    def _set_behavior_mode(self, name: str, mode: str) -> None:
        """Set what Build does with the behavior type *name*: ``"key"``,
        ``"marker"`` or ``"off"`` (Assess follows)."""
        for key, member in (
            ("disabled_behaviors", mode == "off"),
            ("marked_behaviors", mode == "marker"),
        ):
            names = set(self._behavior_set(key))
            if member:
                names.add(name)
            else:
                names.discard(name)
            self._settings.setValue(key, sorted(names))
            self._behavior_sets[key] = frozenset(names)
        self._build_options_changed()

    def _build_options_changed(self) -> None:
        """A showing assessment is judged again under the new options (Build
        enabled with it); otherwise the table redraws its behavior labels."""
        if self._last_results:
            self.assess(skip_key_check=True)
            return
        if self._steps:
            self._redraw()
        else:
            self._update_build_button()

    def _on_long_names_toggled(self, checked: bool) -> None:
        """Persist and apply the long-names display preference."""
        self._settings.setValue("long_names", checked)
        self._redraw()

    def _set_hide_missing(self, on: bool) -> None:
        """Turn Hide Missing Shots on or off from elsewhere (a step row's Hide
        flyout): through the header menu's box, which then says so too."""
        chk = getattr(self, "_chk_hide_missing", None)
        if chk is None:
            self._on_hide_missing_toggled(on)
        elif chk.isChecked() != bool(on):
            chk.setChecked(bool(on))

    def _on_hide_missing_toggled(self, checked: bool) -> None:
        """Persist Hide Missing Shots; a showing assessment is judged again
        over the steps now shown (Build follows them)."""
        self._settings.setValue("hide_missing", bool(checked))
        if not self._steps:
            return
        if self._last_results:
            self.assess(skip_key_check=True)  # its footer counts the hidden
        else:
            self._redraw()
            self._set_footer(self._shown_note())

    def _shown_note(self) -> str:
        """What the table leaves out: ``"37 steps without a shot hidden."``,
        or the steps and shots it shows."""
        shown = len(self._shown_steps())
        hidden = len(self._steps) - shown
        if hidden:
            return (
                f"{hidden} step(s) without a shot hidden (Hide Missing Shots): "
                "Build does not create them."
            )
        return f"{shown} steps shown."

    def _open_audio_clips(self) -> None:
        """Open the Audio Clips editor."""
        self.sb.handlers.marking_menu.show("audio_clips")

    def _open_render_effects(self) -> None:
        """Open the Render Effects panel."""
        self.sb.handlers.marking_menu.show("render_effects")

    def _open_color_editor(self) -> None:
        """Launch the status-color editor dialog."""
        from uitk.widgets.editors.color_mapping_editor import ColorMappingDialog
        from uitk.managers.settings_manager import SettingsManager

        # Keys with actual (fg, bg) colours — skip 'valid'/'csv_object' (None, None)
        editable_keys = [
            k for k, v in PASTEL_STATUS.items() if v[0] is not None or v[1] is not None
        ]

        # Build defaults dict: {key: (fg_hex, bg_hex)}
        defaults = {}
        for k in editable_keys:
            fg, bg = PASTEL_STATUS[k]
            defaults[k] = (str(fg) if fg else "#808080", str(bg) if bg else "#2A2A2A")

        sections = [("Status Colors", editable_keys)]

        color_settings = SettingsManager(namespace=self._COLOR_SETTINGS_NS)
        dlg = ColorMappingDialog(
            defaults=defaults,
            sections=sections,
            settings=color_settings,
            title="Manifest Colors",
            preset_dir=ManifestHost.COLOR_PRESET_DIR,
            parent=self.ui,
        )

        def _apply(cmap):
            # Write changed colours back into the live PASTEL_STATUS palette
            for key, val in cmap.items():
                if key in PASTEL_STATUS:
                    PASTEL_STATUS[key] = val
            # Update derived constants
            BEHAVIOR_STATUS_COLORS["missing"] = PASTEL_STATUS["missing_behavior"][0]
            BEHAVIOR_STATUS_COLORS["error"] = PASTEL_STATUS["missing_object"][0]
            self._redraw()  # in the new colours

        dlg.colors_changed.connect(_apply)
        dlg.exec_()

    def _restore_color_overrides(self) -> None:
        """Apply any persisted color overrides to the live palette."""
        from uitk.managers.settings_manager import SettingsManager

        settings = SettingsManager(namespace=self._COLOR_SETTINGS_NS)
        changed = False
        for key in list(PASTEL_STATUS):
            fg_val = settings.value(f"{key}/fg")
            bg_val = settings.value(f"{key}/bg")
            if fg_val or bg_val:
                orig_fg, orig_bg = PASTEL_STATUS[key]
                PASTEL_STATUS[key] = (
                    fg_val or (str(orig_fg) if orig_fg else None),
                    bg_val or (str(orig_bg) if orig_bg else None),
                )
                changed = True
        if changed:
            BEHAVIOR_STATUS_COLORS["missing"] = PASTEL_STATUS["missing_behavior"][0]
            BEHAVIOR_STATUS_COLORS["error"] = PASTEL_STATUS["missing_object"][0]
