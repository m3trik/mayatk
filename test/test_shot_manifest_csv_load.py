#!/usr/bin/python
# coding=utf-8
"""Regression tests for ShotManifestController._load_csv failure handling.

Covers the bug where a failed CSV load (missing file, unreadable cloud
placeholder, or malformed CSV) returned early *before* enabling the CSV
path widgets -- leaving the field disabled so the user could neither
inspect nor correct the bad path.

Runs WITHOUT Maya: maya.cmds is mocked by conftest, and ``_load_csv`` is
exercised as an unbound method against a stub ``self`` so no Qt window /
switchboard construction is required.
"""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Importing conftest injects the shared maya.cmds mocks into sys.modules
# before any ``import mayatk`` below.
from conftest import mock_cmds  # noqa: E402,F401  (side effect: maya mocks)

_WORKSPACE = Path(__file__).parent.parent.parent.absolute()
for _subdir in ("pythontk", "uitk", "mayatk"):
    _p = str(_WORKSPACE / _subdir)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ColumnMap  # noqa: E402
from mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots import (  # noqa: E402
    ShotManifestController,
)

_SLOTS = "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots"


def _make_stub_controller():
    """A stub carrying only the collaborators ``_load_csv`` touches."""
    ctrl = types.SimpleNamespace()
    ctrl._sync_csv_widgets = MagicMock()
    ctrl._set_footer = MagicMock()
    ctrl._load_data = MagicMock()
    ctrl._refresh_ranges = MagicMock()
    ctrl._recent_csv_option = MagicMock()
    ctrl._active_mapping = None
    ctrl._column_map = ColumnMap()
    ctrl._csv_path = ""
    ctrl.logger = MagicMock()
    ctrl.ui = MagicMock()
    # _load_csv delegates its OSError message to this static method; bind the
    # real one so the stub exercises the genuine diagnosis.
    ctrl._describe_read_failure = ShotManifestController._describe_read_failure
    return ctrl


class LoadCsvFailureTest(unittest.TestCase):
    """Every _load_csv failure path must leave the field usable."""

    def test_missing_file_enables_widgets(self):
        """A non-existent path still enables the field (so it can be fixed)."""
        ctrl = _make_stub_controller()
        ShotManifestController._load_csv(ctrl, "X:/no/such/file.csv")

        ctrl._sync_csv_widgets.assert_called_once_with(True)
        ctrl._load_data.assert_not_called()
        ctrl.ui.txt_csv_path.set_action_color.assert_called_with("invalid")

    def test_unreadable_oserror_enables_widgets(self):
        """An OSError on read (e.g. errno 22) enables the field, not load."""
        ctrl = _make_stub_controller()
        with patch("os.path.isfile", return_value=True), patch(
            "pythontk.FileUtils.free_space", return_value=None
        ), patch(
            f"{_SLOTS}.parse_csv", side_effect=OSError(22, "Invalid argument")
        ):
            ShotManifestController._load_csv(ctrl, "X:/cloud/only.csv")

        ctrl._sync_csv_widgets.assert_called_once_with(True)
        ctrl._load_data.assert_not_called()
        ctrl.ui.txt_csv_path.set_action_color.assert_called_with("invalid")

    def test_malformed_csv_still_enables_widgets(self):
        """A non-OSError parse failure also enables the field and reports it."""
        ctrl = _make_stub_controller()
        with patch("os.path.isfile", return_value=True), patch(
            f"{_SLOTS}.parse_csv", side_effect=ValueError("bad header")
        ):
            ShotManifestController._load_csv(ctrl, "X:/data/bad.csv")

        ctrl._sync_csv_widgets.assert_called_once_with(True)
        ctrl._load_data.assert_not_called()


class DescribeReadFailureTest(unittest.TestCase):
    """The read-failure message names a checkable cause, never guesses one.

    Regression for a misdiagnosis: the old message asserted "online-only
    cloud file, make it available offline", which is usually wrong (cloud
    files hydrate on demand) and useless when the real cause is a full disk.
    """

    @staticmethod
    def _describe(path, *, free, placeholder):
        exc = OSError(22, "Invalid argument")
        with patch("pythontk.FileUtils.free_space", return_value=free), patch(
            "pythontk.FileUtils.is_cloud_placeholder", return_value=placeholder
        ):
            return ShotManifestController._describe_read_failure(path, exc).lower()

    def test_low_disk_surfaces_free_space_figure(self):
        """Low free space appends the actual figure (a fact, not an assertion)."""
        msg = self._describe("X:/seq/m.csv", free=786 * 1024 * 1024, placeholder=True)
        # Pin the converted figure + drive, not just the literal "MB free" (that
        # is in the format string regardless, so it wouldn't catch a unit bug).
        self.assertIn("786 mb free", msg)
        self.assertIn("x:", msg)
        self.assertIn("disk may be full", msg)  # still enumerated, not asserted
        # Must NOT revert to the old "make available offline" misdiagnosis.
        self.assertNotIn("available offline", msg)

    def test_cloud_file_includes_sync_client_cause(self):
        """A cloud file lists the sync client among the causes, not 'offline'."""
        msg = self._describe("X:/seq/m.csv", free=10 * 1024**3, placeholder=True)
        self.assertIn("cloud", msg)
        self.assertIn("sync", msg)
        self.assertNotIn("available offline", msg)
        self.assertNotIn("mb free", msg)  # ample space -> no figure appended

    def test_local_file_omits_cloud_cause(self):
        """A local file with ample space enumerates causes without the cloud one."""
        msg = self._describe("C:/local/m.csv", free=10 * 1024**3, placeholder=False)
        self.assertIn("disk may be full", msg)
        self.assertNotIn("cloud", msg)
        self.assertNotIn("mb free", msg)

    def test_unknown_free_space_still_explains(self):
        """When free space can't be queried (None), it still returns a message."""
        msg = self._describe("C:/local/m.csv", free=None, placeholder=False)
        self.assertIn("disk", msg)
        self.assertNotIn("mb free", msg)


class CsvPathEditingTest(unittest.TestCase):
    """Committing a typed/pasted path loads it (editable-field support)."""

    @staticmethod
    def _ctrl(*, text, csv_path=""):
        ctrl = types.SimpleNamespace()
        ctrl._load_csv = MagicMock()
        ctrl._on_csv_browsed = MagicMock()
        ctrl._csv_path = csv_path
        ctrl.ui = MagicMock()
        ctrl.ui.txt_csv_path.text.return_value = text
        return ctrl

    def test_valid_changed_path_loads(self):
        ctrl = self._ctrl(text="X:/seq/new.csv", csv_path="X:/old.csv")
        ShotManifestController._on_csv_path_edited(ctrl)
        ctrl._load_csv.assert_called_once_with("X:/seq/new.csv")

    def test_unchanged_path_does_not_reload(self):
        ctrl = self._ctrl(text="X:/same.csv", csv_path="X:/same.csv")
        ShotManifestController._on_csv_path_edited(ctrl)
        ctrl._load_csv.assert_not_called()

    def test_empty_path_does_nothing(self):
        ctrl = self._ctrl(text="   ", csv_path="")
        ShotManifestController._on_csv_path_edited(ctrl)
        ctrl._load_csv.assert_not_called()

    def test_changed_path_delegates_to_load(self):
        """Any changed path is handed to _load_csv (the authority on validity),
        which reports a missing file / cloud error itself."""
        ctrl = self._ctrl(text="X:/missing.csv", csv_path="")
        ShotManifestController._on_csv_path_edited(ctrl)
        ctrl._load_csv.assert_called_once_with("X:/missing.csv")

    def test_pasted_path_is_stripped(self):
        """Surrounding whitespace (common when pasting) is stripped before load."""
        ctrl = self._ctrl(text="  X:/seq/new.csv  ", csv_path="")
        ShotManifestController._on_csv_path_edited(ctrl)
        ctrl._load_csv.assert_called_once_with("X:/seq/new.csv")

    def test_recent_selection_loads(self):
        """Picking a path from the recent list loads it via _on_csv_browsed."""
        ctrl = self._ctrl(text="X:/recent.csv", csv_path="")
        ShotManifestController._on_csv_recent_selected(ctrl, "X:/recent.csv")
        ctrl._on_csv_browsed.assert_called_once_with("X:/recent.csv")

if __name__ == "__main__":
    unittest.main(exit=False)
