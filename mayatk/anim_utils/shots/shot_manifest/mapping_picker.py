# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest's mapping template: the picker in the header menu (built-in
and user templates, Refresh and Open Folder on its option box), the template's
options as rows under it, and applying them to the loaded sheet.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

from typing import Dict, Optional

import pythontk as ptk

from .manifest_data import ERROR_COLOR


class MappingPickerMixin:
    """The mapping template, mixed into ``ShotManifestController``.

    Uses the controller's ``ui.header.menu``, ``ui.txt_csv_path``,
    ``_settings``, ``_csv_path``, ``_mapping_*`` / ``_active_mapping`` /
    ``_option_rows`` state, ``logger``, ``_load_csv()`` and ``_set_footer()``.
    """

    def _setup_mapping_combo(self) -> None:
        """Add the mapping selector to the header menu as an option-box template.

        A titled divider introduces the control, then a
        :class:`~uitk.widgets.comboBox.ComboBox` whose ``option_box`` carries two
        icon buttons to its right — *Refresh* (rescan the folder) and *Open
        folder*.  Mapping files aren't edited in-place from the UI; the workflow
        is to open the folder, manage the files externally, then refresh.
        """
        from uitk.widgets.comboBox import ComboBox

        menu = self.ui.header.menu
        menu.add("Separator", setTitle="CSV Mapping")
        cmb = menu.add(
            ComboBox,
            setObjectName="cmb_csv_mapping",
            setToolTip=(
                "Select a CSV mapping. Built-in mappings ship with the tool;\n"
                "your own live in the mappings folder — open it to add or edit\n"
                "files, then click Refresh."
            ),
        )
        self._cmb_mapping = cmb
        self._refresh_mapping_list()
        cmb.currentIndexChanged.connect(self._on_mapping_changed)
        # Pin the combo (and thus its square icon buttons) to the header menu's
        # row height so the template row lines up with the sibling buttons.
        self._wire_mapping_option_box(
            cmb, target_h=getattr(menu, "fixed_item_height", None)
        )

    def _wire_mapping_option_box(self, cmb, target_h=None) -> None:
        """Attach the option-box toolbar to *cmb*: *Refresh* then *Open folder*.

        Factored out so the construction is unit-testable on a real ComboBox.
        A no-op when *cmb* isn't a real widget — the controller's logic tests
        run against a mocked UI where the toolbar is irrelevant.

        *target_h* pins the combo to the header row height; the option-box sizes
        its square icon buttons to the combo's height, so both end up matching
        the sibling buttons.  Falls back to the combo's natural height hint.
        """
        from qtpy import QtWidgets

        if not isinstance(cmb, QtWidgets.QWidget):
            return

        cmb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        h = (
            target_h
            if isinstance(target_h, int) and target_h > 0
            else cmb.sizeHint().height()
        )
        cmb.setFixedHeight(h)
        # Give the combo its row height *now*, before the synchronous option-box
        # wrap below: the option-box sizes its icon buttons from the wrapped
        # widget's current height, which is otherwise 0 until the still-hidden
        # header menu is first laid out.
        cmb.resize(cmb.width() or cmb.sizeHint().width(), h)

        cmb.option_box.add_action(
            callback=self._refresh_mapping_list,
            icon="refresh",
            tooltip="Rescan the mappings folder for files you've added, removed, or edited.",
        )
        cmb.option_box.add_action(
            callback=self._open_mappings_folder,
            icon="folder",
            tooltip="Open the mappings folder to add or edit files (manage them here, then Refresh).",
        )

    def _refresh_mapping_list(self, select: Optional[str] = None) -> None:
        """Rebuild the mapping combo, tagging each item by source.

        The item *text* carries a built-in/user tag for the user; the real
        mapping name is stored as item *data* so selection stays robust.
        Selection priority: *select* arg > last-used > ``default`` > ``(none)``.
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        cmb = self._cmb_mapping
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItem("(none)", None)
        if self._mapping_dir:
            names = list(Mapping.discover(self._mapping_dir))
            for name in names:
                cmb.addItem(name, name)
        else:
            ts = Mapping.templates()
            names = ts.names()
            for name in names:
                tag = "user" if ts.source(name) == "user" else "built-in"
                cmb.addItem(f"{name}  ·  {tag}", name)

        target = select
        if target is None and not self._mapping_dir:
            target = Mapping.templates().active
            retired = Mapping.retired(target) if target else None
            if retired is not None:
                target = self._migrate_retired_mapping(target, *retired)
        idx = self._combo_data_index(target) if target else -1
        if idx < 0 and "default" in names:
            idx = self._combo_data_index("default")
        cmb.setCurrentIndex(idx if idx >= 0 else 0)
        cmb.blockSignals(False)
        # Programmatic refresh: apply but don't persist — rebuilding the list
        # must not overwrite the user's last-used pointer.
        self._apply_mapping(cmb.currentData(), persist=False)

    def _migrate_retired_mapping(
        self, name: str, replacement: str, values: dict
    ) -> str:
        """Point a saved selection of retired template *name* at its
        replacement, carrying the option *values* it stood for into the
        replacement's saved ones -- over a saved value of the same option, as
        the retired template was the one last chosen.  Returns the
        replacement."""
        import json
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        options = {**self._option_values(replacement), **values}
        self._settings.setValue(f"mapping_options/{replacement}", json.dumps(options))
        try:
            Mapping.templates().active = replacement
        except Exception:
            pass
        self.logger.info(
            "Mapping %r is retired; using %r with %s.", name, replacement, options
        )
        return replacement

    def _combo_data_index(self, name) -> int:
        """Index of the combo item whose data == *name*, or -1."""
        cmb = self._cmb_mapping
        for i in range(cmb.count()):
            if cmb.itemData(i) == name:
                return i
        return -1

    def _on_mapping_changed(self, arg=None) -> None:
        """Handle a mapping selection.

        Connected to ``currentIndexChanged`` (passes an int) — the real mapping
        name is read from the current item's data. A ``str`` *arg* is also
        accepted (legacy/tests) and used directly as the name.
        """
        if isinstance(arg, str):
            name = None if (not arg or arg == "(none)") else arg
        else:
            name = self._cmb_mapping.currentData()
        self._apply_mapping(name, persist=True)

    def _apply_mapping(self, name, persist: bool) -> None:
        """Load *name* into ``_active_mapping`` and re-parse the current CSV.

        *persist* records the choice as last-used (skipped in directory-override
        mode, whose pointer belongs to a different store).
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        self._mapping_name = name or None
        self._mapping_template = None
        if name:
            try:
                self._mapping_template = Mapping.load_mapping(
                    name, self._mapping_dir or None
                )
            except Exception as exc:
                self.logger.error("Failed to load mapping '%s': %s", name, exc)
                self._set_footer(f"Mapping error: {exc}", color=ERROR_COLOR)
                self._active_mapping = None
                self._build_option_rows()
                return
            if persist and not self._mapping_dir:
                try:
                    Mapping.templates().active = (
                        name  # remember last-used across sessions
                    )
                except Exception:
                    pass
        self._build_option_rows()
        self._reapply_mapping()

    def _option_values(self, name: Optional[str] = None) -> Dict[str, object]:
        """The saved option values of template *name*, the current one by
        default (``{}`` if none)."""
        import json

        name = name or self._mapping_name
        if not name:
            return {}
        raw = self._settings.value(f"mapping_options/{name}", "")
        try:
            values = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return {}
        return values if isinstance(values, dict) else {}

    def _reapply_mapping(self) -> None:
        """Rebuild the effective template from its options, then re-load the sheet."""
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        self._active_mapping = (
            Mapping.apply_options(self._mapping_template, self._option_values())
            if self._mapping_template is not None
            else None
        )
        path = self._csv_path or self.ui.txt_csv_path.text().strip()
        if path:
            self._load_csv(path)

    def _on_option_changed(self, key: str, value) -> None:
        """Save one option of the current template and re-apply it."""
        import json

        values = self._option_values()
        values[key] = value
        self._settings.setValue(
            f"mapping_options/{self._mapping_name}", json.dumps(values)
        )
        self._reapply_mapping()

    def _build_option_rows(self) -> None:
        """Show the current template's options under its picker in the header menu.

        One row per option (``Mapping.option_specs``), built by uitk's widget
        factory from an ``AttributeSpec`` -- the template declares its own
        settings, so a new option needs no code here.  Values persist per
        template; the template, not the widget state, owns them.
        """
        from qtpy import QtCore, QtWidgets
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping
        from uitk.bridge.spec import AttributeSpec, KindFactory
        from uitk.bridge.tooltip import Tooltip

        menu = self.ui.header.menu
        for row in self._option_rows:
            menu.remove_widget(row)
            row.deleteLater()
        self._option_rows = []
        if not isinstance(menu, QtWidgets.QWidget):
            return  # mocked UI (logic tests)
        values = self._option_values()
        for opt in Mapping.option_specs(self._mapping_template):
            spec = AttributeSpec(
                key=opt["key"],
                label=opt["label"],
                kind=opt["kind"],
                default=values.get(opt["key"], opt["default"]),
                choices=tuple(opt.get("choices", ())),
                tooltip=opt["tooltip"],
            )
            row = QtWidgets.QWidget()
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(2)
            label = QtWidgets.QLabel(f"{spec.display_label}:", row)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            widget = KindFactory.make_widget(spec, row)
            widget.setObjectName(f"opt_{spec.key}")
            widget.restore_state = False  # the template owns the value
            tip = Tooltip.format_param_tooltip(spec)
            label.setToolTip(tip)
            widget.setToolTip(tip)
            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            KindFactory.connect_changed(
                widget, lambda value, key=spec.key: self._on_option_changed(key, value)
            )
            menu.add(row)
            self._option_rows.append(row)

    def _open_mappings_folder(self) -> None:
        """Open the writable folder where user mapping files live.

        Mapping files are managed externally, not edited in-place from the UI:
        the user opens this folder, adds/edits/removes files, then clicks
        *Refresh*.  On first use (empty folder) it's seeded with a documented
        example and the format reference, so there's a model to copy and a spec
        to read.
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        ts = Mapping.templates()
        d = ts.user_dir
        d.mkdir(parents=True, exist_ok=True)
        self._seed_mappings_folder(ts)
        ptk.FileUtils.open_explorer(str(d), logger=self.logger)

    @staticmethod
    def _seed_mappings_folder(ts) -> None:
        """Seed *ts*'s empty user folder with an example mapping + format
        reference (``Mapping.seed_user_folder``: a no-op once it holds anything).
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        Mapping.seed_user_folder(ts)
