# !/usr/bin/python
# coding=utf-8
"""Pure-Python tests for HierarchySidecar.

These tests don't require Maya — the sidecar is a path/JSON helper that
sits below the cmds/mel layer.  Run with the workspace venv:

    & "o:\\Cloud\\Code\\_scripts\\.venv\\Scripts\\python.exe" -m pytest \
        o:\\Cloud\\Code\\_scripts\\mayatk\\test\\test_hierarchy_sidecar.py -v
"""
import json
import os
import sys
import tempfile
import unittest

# Allow running directly without installing mayatk.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
for p in (
    os.path.join(_REPO_ROOT, "mayatk"),
    os.path.join(_REPO_ROOT, "pythontk"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

from mayatk.env_utils.hierarchy_sync.hierarchy_sidecar import HierarchySidecar


class BaseStemTest(unittest.TestCase):
    """VERSION_SUFFIX_RE + base_stem behaviour."""

    def test_plain_name_unchanged(self):
        self.assertEqual(HierarchySidecar.base_stem("shot.fbx"), "shot")

    def test_strips_trailing_padded_version(self):
        self.assertEqual(HierarchySidecar.base_stem("shot_v003.fbx"), "shot")

    def test_strips_trailing_unpadded_version(self):
        self.assertEqual(HierarchySidecar.base_stem("shot_v3.fbx"), "shot")

    def test_strips_uppercase_version(self):
        self.assertEqual(HierarchySidecar.base_stem("shot_V12.fbx"), "shot")

    def test_does_not_strip_mid_name_version(self):
        # `_v\d+` only matches at end-of-stem.
        self.assertEqual(
            HierarchySidecar.base_stem("arch_v2_proxy.fbx"), "arch_v2_proxy"
        )

    def test_multiple_extension_handled(self):
        # splitext strips only the final extension, so '.tar' becomes part of stem.
        self.assertEqual(
            HierarchySidecar.base_stem("shot_v003.tar.gz"), "shot_v003.tar"
        )

    def test_directory_in_path(self):
        self.assertEqual(
            HierarchySidecar.base_stem(os.path.join("C:", "exports", "shot_v8.fbx")),
            "shot",
        )


class ManifestPathRoutingTest(unittest.TestCase):
    """manifest_path_for / diff_report_path_for route through base_stem flag."""

    def test_plain_mode_keeps_version_in_name(self):
        path = HierarchySidecar.manifest_path_for("C:/x/shot_v003.fbx")
        self.assertEqual(os.path.basename(path), ".shot_v003.hierarchy.json")

    def test_base_stem_mode_strips_version(self):
        path = HierarchySidecar.manifest_path_for(
            "C:/x/shot_v003.fbx", base_stem=True
        )
        self.assertEqual(os.path.basename(path), ".shot.hierarchy.json")

    def test_diff_report_routes_through_base_stem_flag(self):
        plain = HierarchySidecar.diff_report_path_for("C:/x/shot_v003.fbx")
        versioned = HierarchySidecar.diff_report_path_for(
            "C:/x/shot_v003.fbx", base_stem=True
        )
        self.assertEqual(os.path.basename(plain), ".shot_v003.hierarchy_diff.txt")
        self.assertEqual(os.path.basename(versioned), ".shot.hierarchy_diff.txt")

    def test_unversioned_file_unchanged_by_base_stem_flag(self):
        # If the stem doesn't end in _v\d+, base_stem mode is a no-op.
        plain = HierarchySidecar.manifest_path_for("C:/x/shot.fbx")
        versioned = HierarchySidecar.manifest_path_for(
            "C:/x/shot.fbx", base_stem=True
        )
        self.assertEqual(plain, versioned)


class FindLegacyManifestTest(unittest.TestCase):
    """find_legacy_manifest picks the highest version by integer, not lex."""

    def test_empty_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                HierarchySidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            )

    def test_no_legacy_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            # Other JSON files in dir shouldn't be picked up.
            open(os.path.join(d, ".other.hierarchy.json"), "w").close()
            open(os.path.join(d, ".shot.hierarchy.json"), "w").close()
            self.assertIsNone(
                HierarchySidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            )

    def test_picks_highest_padded_version(self):
        with tempfile.TemporaryDirectory() as d:
            for n in (1, 3, 5):
                open(os.path.join(d, f".shot_v{n:03d}.hierarchy.json"), "w").close()
            result = HierarchySidecar.find_legacy_manifest(
                os.path.join(d, "shot.fbx")
            )
            self.assertEqual(os.path.basename(result), ".shot_v005.hierarchy.json")

    def test_picks_highest_unpadded_version_by_int(self):
        # The lex-vs-int bug: max('_v2', '_v10') is '_v2' lexically.
        with tempfile.TemporaryDirectory() as d:
            for n in (2, 9, 10, 11):
                open(os.path.join(d, f".shot_v{n}.hierarchy.json"), "w").close()
            result = HierarchySidecar.find_legacy_manifest(
                os.path.join(d, "shot.fbx")
            )
            self.assertEqual(os.path.basename(result), ".shot_v11.hierarchy.json")

    def test_only_matches_own_base_stem(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".shot_v003.hierarchy.json"), "w").close()
            open(os.path.join(d, ".other_v005.hierarchy.json"), "w").close()
            result = HierarchySidecar.find_legacy_manifest(
                os.path.join(d, "shot.fbx")
            )
            self.assertEqual(os.path.basename(result), ".shot_v003.hierarchy.json")


class EnsureBaseNameTest(unittest.TestCase):
    """ensure_base_name migrates legacy sidecars idempotently."""

    def test_no_legacy_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                HierarchySidecar.ensure_base_name(os.path.join(d, "shot.fbx"))
            )

    def test_already_base_name_returns_path(self):
        with tempfile.TemporaryDirectory() as d:
            existing = os.path.join(d, ".shot.hierarchy.json")
            open(existing, "w").close()
            result = HierarchySidecar.ensure_base_name(
                os.path.join(d, "shot_v003.fbx")
            )
            self.assertEqual(result, existing)
            # No migration should have occurred — file count is 1.
            self.assertEqual(len(os.listdir(d)), 1)

    def test_migrates_latest_legacy_to_base(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            with open(legacy, "w") as f:
                json.dump({"paths": ["root"]}, f)
            # An older legacy that should NOT be promoted.
            other = os.path.join(d, ".shot_v003.hierarchy.json")
            open(other, "w").close()

            result = HierarchySidecar.ensure_base_name(
                os.path.join(d, "shot_v007.fbx")
            )
            self.assertEqual(os.path.basename(result), ".shot.hierarchy.json")
            self.assertTrue(os.path.exists(result))
            self.assertFalse(os.path.exists(legacy))
            # The older legacy is left intact (not our job to clean up).
            self.assertTrue(os.path.exists(other))

    def test_idempotent(self):
        # Running twice should be safe — second call finds the migrated file.
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            open(legacy, "w").close()
            export_path = os.path.join(d, "shot_v007.fbx")

            first = HierarchySidecar.ensure_base_name(export_path)
            second = HierarchySidecar.ensure_base_name(export_path)
            self.assertEqual(first, second)


class WriteReadManifestRoutingTest(unittest.TestCase):
    """write_manifest / read_manifest propagate base_stem correctly."""

    def test_roundtrip_with_base_stem(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot_v003.fbx")
            paths = {"root|child", "root|other"}
            written = HierarchySidecar.write_manifest(export, paths, base_stem=True)
            self.assertIsNotNone(written)
            self.assertEqual(os.path.basename(written), ".shot.hierarchy.json")

            read_back = HierarchySidecar.read_manifest(export, base_stem=True)
            self.assertEqual(read_back, paths)

    def test_base_stem_shares_manifest_across_versions(self):
        with tempfile.TemporaryDirectory() as d:
            v3 = os.path.join(d, "shot_v003.fbx")
            v4 = os.path.join(d, "shot_v004.fbx")
            HierarchySidecar.write_manifest(v3, {"a"}, base_stem=True)
            # Reading from a different version path should find the same data.
            self.assertEqual(
                HierarchySidecar.read_manifest(v4, base_stem=True), {"a"}
            )

    def test_plain_mode_does_not_share(self):
        with tempfile.TemporaryDirectory() as d:
            v3 = os.path.join(d, "shot_v003.fbx")
            v4 = os.path.join(d, "shot_v004.fbx")
            HierarchySidecar.write_manifest(v3, {"a"})
            self.assertIsNone(HierarchySidecar.read_manifest(v4))


class PrevFallbackTest(unittest.TestCase):
    """compare/read_manifest fall back to the .prev backup when the manifest is gone.

    Guards against a deleted or corrupted manifest silently passing the
    hierarchy check when the last-known-good baseline is still on disk.
    """

    def _write_with_prev(self, d):
        """Write twice with differing content so a .prev exists; return export path."""
        export = os.path.join(d, "shot.fbx")
        HierarchySidecar.write_manifest(export, {"A", "A|B"})
        HierarchySidecar.write_manifest(export, {"A", "A|B", "A|C"})
        return export

    def test_compare_uses_prev_after_manifest_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(HierarchySidecar.manifest_path_for(export))
            # .prev holds the older baseline {A, A|B}.
            match, missing, extra = HierarchySidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_compare_detects_drift_via_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(HierarchySidecar.manifest_path_for(export))
            match, missing, extra = HierarchySidecar.compare(export, {"A"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|B"])
            self.assertEqual(extra, [])

    def test_compare_uses_prev_when_manifest_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            with open(
                HierarchySidecar.manifest_path_for(export), "w", encoding="utf-8"
            ) as f:
                f.write("not json{")
            match, _, _ = HierarchySidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_read_manifest_falls_back_to_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(HierarchySidecar.manifest_path_for(export))
            self.assertEqual(
                HierarchySidecar.read_manifest(export), {"A", "A|B"}
            )

    def test_no_manifest_no_prev_passes(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "never.fbx")
            self.assertEqual(
                HierarchySidecar.compare(export, {"X"}), (True, [], [])
            )
            self.assertIsNone(HierarchySidecar.read_manifest(export))

    def test_intact_manifest_wins_over_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            # Manifest {A, A|B, A|C} present — .prev must NOT shadow it.
            match, _, _ = HierarchySidecar.compare(export, {"A", "A|B", "A|C"})
            self.assertTrue(match)
            match, missing, _ = HierarchySidecar.compare(export, {"A", "A|B"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|C"])


if __name__ == "__main__":
    unittest.main(exit=False)
