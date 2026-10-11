# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest's Export button (beside Assess) and its option menu: the
manifest's rows -- those the table shows, or the selected ones -- as CSV, JSON
or Markdown (``pythontk.ManifestExport``), saved to a file or put on the
clipboard.  What a row says is what the table says: Local Edits applied, the
names read from a step's text or filled from the scene, the frames, and the
status an Assess gave.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

import os
from typing import Any, Dict, List

from pythontk import BuilderStep, ManifestExport

from .manifest_data import ERROR_COLOR
from .manifest_host import ManifestHost


class ExportMenuMixin:
    """The Export button, mixed into ``ShotManifestController``.

    Uses the controller's ``ui`` (``b004``, ``tbl_steps``), ``sb``,
    ``_settings``, ``_steps``, ``_csv_path``, ``_last_results``,
    ``_last_resolved``, ``_use_short_names``, ``_rows()`` and
    ``_set_footer()``.
    """

    #: Each option's choices: ``(label, value, tooltip)``.
    EXPORT_CHOICES = {
        "format": (
            ("CSV", "csv", "A spreadsheet: Excel, Google Sheets."),
            ("JSON", "json", "Each row whole, for a script or a pipeline."),
            ("Markdown", "markdown", "A table for a review, a ticket or a chat."),
        ),
        "to": (
            ("File…", "file", "Save it -- you pick where."),
            ("Clipboard", "clipboard", "Copy it, to paste where you like."),
        ),
        "rows": (
            ("Shown", "shown", "Every row the table shows."),
            ("Selected", "selected", "The selected rows (an object row: its step)."),
        ),
        "objects": (
            (
                "A Row Each",
                "rows",
                "Each object on a row of its own, its step's cells on the first "
                "-- as a sheet lists them.",
            ),
            ("One Cell", "cell", "One row per step, its objects a line each."),
        ),
    }
    #: What each option is, for its tooltip.
    EXPORT_HINTS = {
        "format": "What the export is written as.",
        "to": "Where it goes.",
        "rows": "Which rows go.",
        "objects": "How a step's objects are laid out in CSV and Markdown.",
        "columns": "Which columns go, in this order.",
    }
    #: The options as a new user finds them.
    EXPORT_DEFAULTS = {
        "format": "csv",
        "to": "file",
        "rows": "shown",
        "objects": "rows",
        "columns": [k for k, _h in ManifestExport.COLUMNS if k != "voice"],
    }

    def _setup_export_menu(self) -> None:
        """Give the Export button its option menu -- Format, To, Rows, Objects
        and Columns -- built by uitk's widget factory, kept per user."""
        from qtpy import QtWidgets

        btn = getattr(self.ui, "b004", None)
        if not isinstance(btn, QtWidgets.QWidget):
            return  # mocked UI (logic tests)
        from uitk.bridge.spec import AttributeSpec, KindFactory
        from uitk.widgets.form_rows import FormRows

        values = self._export_options()
        choices = dict(self.EXPORT_CHOICES)
        choices["columns"] = tuple((h, k) for k, h in ManifestExport.COLUMNS)
        form = FormRows()
        self._export_widgets = {}
        for key, label in (
            ("format", "Format"),
            ("to", "To"),
            ("rows", "Rows"),
            ("objects", "Objects"),
            ("columns", "Columns"),
        ):
            spec = AttributeSpec(
                key=f"export_{key}",
                label=label,
                kind="check_list" if key == "columns" else "choice",
                default=values[key],
                choices=choices[key],
            )
            widget = KindFactory.make_widget(spec, form)
            widget.restore_state = False  # the export_options setting owns it
            form.add(widget, label=label, hint=self.EXPORT_HINTS[key])
            KindFactory.connect_changed(
                widget, lambda value, key=key: self._set_export_option(key, value)
            )
            self._export_widgets[key] = widget
        btn.option_box.menu.add(form)
        self._sync_export_options()

    def _export_options(self) -> Dict[str, Any]:
        """The Export options, a saved value that is no longer a choice
        replaced by its default."""
        saved = self._settings.value("export_options", {})
        if not isinstance(saved, dict):
            saved = {}
        options = dict(
            self.EXPORT_DEFAULTS, columns=list(self.EXPORT_DEFAULTS["columns"])
        )
        for key, entries in self.EXPORT_CHOICES.items():
            if saved.get(key) in {value for _l, value, _t in entries}:
                options[key] = saved[key]
        if isinstance(saved.get("columns"), list):
            options["columns"] = ManifestExport.columns(saved["columns"])
        return options

    def _set_export_option(self, key: str, value) -> None:
        """Keep one Export option (per user, for every scene)."""
        options = self._export_options()
        options[key] = list(value) if key == "columns" else value
        self._settings.setValue("export_options", options)  # stored as JSON
        self._sync_export_options()

    def _sync_export_options(self) -> None:
        """Say on the button what a click exports; JSON lays no objects out."""
        options = self._export_options()
        widget = getattr(self, "_export_widgets", {}).get("objects")
        if widget is not None:
            widget.setEnabled(options["format"] != "json")
        btn = getattr(self.ui, "b004", None)
        if btn is not None:
            btn.setToolTip(
                f"Export the {options['rows']} rows as "
                f"{self._export_label(options['format'])} to "
                f"{'the clipboard' if options['to'] == 'clipboard' else 'a file'}.\n"
                "Its menu button sets the format, where it goes, the rows,\n"
                "how objects are laid out and the columns."
            )

    @classmethod
    def _export_label(cls, fmt: str) -> str:
        """``"csv"`` -> ``"CSV"``."""
        return next(label for label, v, _t in cls.EXPORT_CHOICES["format"] if v == fmt)

    def _export_rows(self, which: str) -> list:
        """The table's ``(step, shot)`` rows, all of them or the selected ones
        (an object row selects its step's row)."""
        from qtpy.QtCore import Qt

        rows = self._rows()
        if which != "selected":
            return rows
        picked = set()
        for item in self.ui.tbl_steps.selectedItems():
            row = item.parent() if item.parent() is not None else item
            data = row.data(0, Qt.UserRole)
            if isinstance(data, BuilderStep):
                picked.add(("step", data.step_id))
            elif getattr(data, "shot_id", None) is not None:
                picked.add(("shot", data.shot_id))
        return [
            (step, shot)
            for step, shot in rows
            if (("step", step.step_id) if step is not None else ("shot", shot.shot_id))
            in picked
        ]

    def _export_records(self, rows: list) -> List[Dict[str, Any]]:
        """*rows* as the table shows them: the frames it shows a step with no
        shot at, the showing assessment's statuses, its object names."""
        return ManifestExport.records(
            rows,
            ranges={sid: (s, e) for sid, s, e, _user in self._last_resolved},
            statuses={r.step_id: r.status for r in self._last_results},
            object_name=ManifestHost.leaf_name if self._use_short_names else None,
        )

    def export(self) -> None:
        """Export the manifest's rows the way the option menu says."""
        options = self._export_options()
        fmt, columns, layout = options["format"], options["columns"], options["objects"]
        if not self._steps:
            self._set_footer(
                "Nothing to export: load a sheet, or the scene's shots.",
                color=ERROR_COLOR,
            )
            return
        rows = self._export_rows(options["rows"])
        if not rows:
            self._set_footer(
                "Nothing to export: select the rows to export, or set Rows to "
                "Shown (Export's menu button).",
                color=ERROR_COLOR,
            )
            return
        if not columns:
            self._set_footer(
                "Nothing to export: tick a column (Export's menu button).",
                color=ERROR_COLOR,
            )
            return
        records = self._export_records(rows)
        label = self._export_label(fmt)
        if options["to"] == "clipboard":
            self._export_to_clipboard(records, fmt, columns, layout)
            paste = " -- paste it into a spreadsheet" if fmt == "csv" else ""
            self._set_footer(f"Copied {len(records)} rows as {label}{paste}.")
            return
        path = self._export_path(fmt)
        if not path:
            return
        try:
            ManifestExport.write(path, records, columns, layout)
        except (OSError, ValueError) as exc:
            self._set_footer(f"Couldn't export: {exc}", color=ERROR_COLOR)
            return
        self._settings.setValue("export_dir", os.path.dirname(path))
        self._set_footer(f"Exported {len(records)} rows to {os.path.basename(path)}.")

    def _export_to_clipboard(self, records, fmt, columns, layout) -> None:
        """Put *records* on the clipboard: CSV as a table a spreadsheet pastes
        (HTML, with tab-separated text), the others as their text."""
        from qtpy.QtCore import QMimeData
        from qtpy.QtWidgets import QApplication

        if fmt != "csv":
            text = ManifestExport.render(records, fmt, columns, layout)
            QApplication.clipboard().setText(text)
            return
        tsv, html = ManifestExport.clipboard(
            ManifestExport.table(records, columns, layout)
        )
        mime = QMimeData()
        mime.setText(tsv)
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)

    def _export_path(self, fmt: str) -> str:
        """Ask where to save a *fmt* export ("" if cancelled): beside the last
        export, else the sheet, named after the sheet.  The loaded sheet
        itself is refused -- the manifest never writes it."""
        ext = ManifestExport.FORMATS[fmt]
        sheet = self._csv_path if os.path.isfile(self._csv_path or "") else ""
        stem = os.path.splitext(os.path.basename(sheet))[0] if sheet else "shot"
        folder = self._settings.value("export_dir", "") or os.path.dirname(sheet)
        path = self.sb.save_file_dialog(
            file_types=[f"*{ext}"],
            title="Export the Shot Manifest",
            start_dir=os.path.join(folder, f"{stem}_manifest{ext}"),
            filter_description=f"{self._export_label(fmt)} Files",
        )
        if not path:
            return ""
        if ManifestExport.format_of(path) != fmt:
            path += ext
        if sheet and os.path.normcase(os.path.abspath(path)) == os.path.normcase(
            os.path.abspath(sheet)
        ):
            self._set_footer(
                "That is the loaded sheet: the manifest never writes it -- "
                "export under another name.",
                color=ERROR_COLOR,
            )
            return ""
        return path
