# !/usr/bin/python
# coding=utf-8
"""Pure-Python tests for SceneDataSidecar.

These tests don't require Maya — the sidecar is a path/JSON helper that
sits below the cmds/mel layer.  Run with the workspace venv:

    & "o:\\Cloud\\Code\\_scripts\\.venv\\Scripts\\python.exe" -m pytest \
        o:\\Cloud\\Code\\_scripts\\mayatk\\test\\test_scene_data_sidecar.py -v
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

from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class BaseStemTest(unittest.TestCase):
    """VERSION_SUFFIX_RE + base_stem behaviour."""

    def test_plain_name_unchanged(self):
        self.assertEqual(SceneDataSidecar.base_stem("shot.fbx"), "shot")

    def test_strips_trailing_padded_version(self):
        self.assertEqual(SceneDataSidecar.base_stem("shot_v003.fbx"), "shot")

    def test_strips_trailing_unpadded_version(self):
        self.assertEqual(SceneDataSidecar.base_stem("shot_v3.fbx"), "shot")

    def test_strips_uppercase_version(self):
        self.assertEqual(SceneDataSidecar.base_stem("shot_V12.fbx"), "shot")

    def test_does_not_strip_mid_name_version(self):
        # `_v\d+` only matches at end-of-stem.
        self.assertEqual(
            SceneDataSidecar.base_stem("arch_v2_proxy.fbx"), "arch_v2_proxy"
        )

    def test_multiple_extension_handled(self):
        # splitext strips only the final extension, so '.tar' becomes part of stem.
        self.assertEqual(
            SceneDataSidecar.base_stem("shot_v003.tar.gz"), "shot_v003.tar"
        )

    def test_directory_in_path(self):
        self.assertEqual(
            SceneDataSidecar.base_stem(os.path.join("C:", "exports", "shot_v8.fbx")),
            "shot",
        )


class ManifestPathRoutingTest(unittest.TestCase):
    """manifest_path_for / diff_report_path_for route through base_stem flag."""

    def test_plain_mode_keeps_version_in_name(self):
        path = SceneDataSidecar.manifest_path_for("C:/x/shot_v003.fbx")
        self.assertEqual(os.path.basename(path), ".shot_v003.scene_data.json")

    def test_base_stem_mode_strips_version(self):
        path = SceneDataSidecar.manifest_path_for("C:/x/shot_v003.fbx", base_stem=True)
        self.assertEqual(os.path.basename(path), ".shot.scene_data.json")

    def test_diff_report_routes_through_base_stem_flag(self):
        plain = SceneDataSidecar.diff_report_path_for("C:/x/shot_v003.fbx")
        versioned = SceneDataSidecar.diff_report_path_for(
            "C:/x/shot_v003.fbx", base_stem=True
        )
        self.assertEqual(os.path.basename(plain), ".shot_v003.hierarchy_diff.txt")
        self.assertEqual(os.path.basename(versioned), ".shot.hierarchy_diff.txt")

    def test_unversioned_file_unchanged_by_base_stem_flag(self):
        # If the stem doesn't end in _v\d+, base_stem mode is a no-op.
        plain = SceneDataSidecar.manifest_path_for("C:/x/shot.fbx")
        versioned = SceneDataSidecar.manifest_path_for("C:/x/shot.fbx", base_stem=True)
        self.assertEqual(plain, versioned)


class FindLegacyManifestTest(unittest.TestCase):
    """find_legacy_manifest picks the highest version by integer, not lex."""

    def test_empty_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            )

    def test_no_legacy_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            # Unversioned sidecars in dir shouldn't be picked up.
            open(os.path.join(d, ".other.hierarchy.json"), "w").close()
            open(os.path.join(d, ".shot.hierarchy.json"), "w").close()
            open(os.path.join(d, ".shot.scene_data.json"), "w").close()
            self.assertIsNone(
                SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            )

    def test_picks_highest_padded_version(self):
        with tempfile.TemporaryDirectory() as d:
            for n in (1, 3, 5):
                open(os.path.join(d, f".shot_v{n:03d}.hierarchy.json"), "w").close()
            result = SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            self.assertEqual(os.path.basename(result), ".shot_v005.hierarchy.json")

    def test_picks_highest_unpadded_version_by_int(self):
        # The lex-vs-int bug: max('_v2', '_v10') is '_v2' lexically.
        with tempfile.TemporaryDirectory() as d:
            for n in (2, 9, 10, 11):
                open(os.path.join(d, f".shot_v{n}.hierarchy.json"), "w").close()
            result = SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            self.assertEqual(os.path.basename(result), ".shot_v11.hierarchy.json")

    def test_only_matches_own_base_stem(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".shot_v003.hierarchy.json"), "w").close()
            open(os.path.join(d, ".other_v005.hierarchy.json"), "w").close()
            result = SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            self.assertEqual(os.path.basename(result), ".shot_v003.hierarchy.json")

    def test_highest_version_wins_across_namings(self):
        # A newer v1-named manifest outranks an older current-named one.
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".shot_v003.scene_data.json"), "w").close()
            open(os.path.join(d, ".shot_v010.hierarchy.json"), "w").close()
            result = SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            self.assertEqual(os.path.basename(result), ".shot_v010.hierarchy.json")

    def test_current_naming_wins_version_tie(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".shot_v005.hierarchy.json"), "w").close()
            open(os.path.join(d, ".shot_v005.scene_data.json"), "w").close()
            result = SceneDataSidecar.find_legacy_manifest(os.path.join(d, "shot.fbx"))
            self.assertEqual(os.path.basename(result), ".shot_v005.scene_data.json")


class EnsureBaseNameTest(unittest.TestCase):
    """ensure_base_name migrates legacy sidecars idempotently."""

    def test_no_legacy_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                SceneDataSidecar.ensure_base_name(os.path.join(d, "shot.fbx"))
            )

    def test_already_base_name_returns_path(self):
        with tempfile.TemporaryDirectory() as d:
            existing = os.path.join(d, ".shot.scene_data.json")
            open(existing, "w").close()
            result = SceneDataSidecar.ensure_base_name(os.path.join(d, "shot_v003.fbx"))
            self.assertEqual(result, existing)
            # No migration should have occurred — file count is 1.
            self.assertEqual(len(os.listdir(d)), 1)

    def test_v1_named_base_manifest_promoted(self):
        # A base-stem manifest under the old naming is renamed, not re-scanned.
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot.hierarchy.json")
            with open(legacy, "w") as f:
                json.dump({"paths": ["root"]}, f)
            result = SceneDataSidecar.ensure_base_name(os.path.join(d, "shot_v003.fbx"))
            self.assertEqual(os.path.basename(result), ".shot.scene_data.json")
            self.assertFalse(os.path.exists(legacy))
            self.assertEqual(len(os.listdir(d)), 1)

    def test_migrates_latest_legacy_to_base(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            with open(legacy, "w") as f:
                json.dump({"paths": ["root"]}, f)
            # An older legacy that should NOT be promoted.
            other = os.path.join(d, ".shot_v003.hierarchy.json")
            open(other, "w").close()

            result = SceneDataSidecar.ensure_base_name(os.path.join(d, "shot_v007.fbx"))
            self.assertEqual(os.path.basename(result), ".shot.scene_data.json")
            self.assertTrue(os.path.exists(result))
            self.assertFalse(os.path.exists(legacy))
            # The older legacy is left intact (not our job to clean up).
            self.assertTrue(os.path.exists(other))

    def test_prev_backup_carried_forward(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            open(legacy, "w").close()
            open(legacy + ".prev", "w").close()
            result = SceneDataSidecar.ensure_base_name(os.path.join(d, "shot_v007.fbx"))
            self.assertTrue(os.path.exists(result + ".prev"))
            self.assertFalse(os.path.exists(legacy + ".prev"))

    def test_idempotent(self):
        # Running twice should be safe — second call finds the migrated file.
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            open(legacy, "w").close()
            export_path = os.path.join(d, "shot_v007.fbx")

            first = SceneDataSidecar.ensure_base_name(export_path)
            second = SceneDataSidecar.ensure_base_name(export_path)
            self.assertEqual(first, second)


class MigrateLegacyTest(unittest.TestCase):
    """migrate_legacy is the single exporter-facing migration entry point."""

    def test_exact_stem_promotes_v1_name(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot.hierarchy.json")
            with open(legacy, "w") as f:
                json.dump({"paths": ["A"], "object_count": 1}, f)
            open(legacy + ".prev", "w").close()

            export = os.path.join(d, "shot.fbx")
            result = SceneDataSidecar.migrate_legacy(export)
            self.assertEqual(os.path.basename(result), ".shot.scene_data.json")
            self.assertFalse(os.path.exists(legacy))
            self.assertTrue(os.path.exists(result + ".prev"))
            # v1 content stays readable through the section shim.
            self.assertEqual(SceneDataSidecar.read_manifest(export), {"A"})

    def test_exact_stem_does_not_promote_versions(self):
        # Without base_stem, a per-version legacy of a DIFFERENT stem is not touched.
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            open(legacy, "w").close()
            self.assertIsNone(
                SceneDataSidecar.migrate_legacy(os.path.join(d, "shot.fbx"))
            )
            self.assertTrue(os.path.exists(legacy))

    def test_noop_when_current_exists(self):
        with tempfile.TemporaryDirectory() as d:
            current = os.path.join(d, ".shot.scene_data.json")
            open(current, "w").close()
            result = SceneDataSidecar.migrate_legacy(os.path.join(d, "shot.fbx"))
            self.assertEqual(result, current)

    def test_orphaned_prev_backup_migrated(self):
        # Manifest deleted but its v1-named .prev survives: the fallback
        # protection must survive the name migration too.
        with tempfile.TemporaryDirectory() as d:
            legacy_prev = os.path.join(d, ".shot.hierarchy.json.prev")
            with open(legacy_prev, "w") as f:
                json.dump({"paths": ["A"]}, f)
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.migrate_legacy(export)
            self.assertFalse(os.path.exists(legacy_prev))
            # compare() finds the baseline via the migrated .prev.
            match, missing, _ = SceneDataSidecar.compare(export, set())
            self.assertFalse(match)
            self.assertEqual(missing, ["A"])

    def test_base_stem_routes_to_ensure_base_name(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".shot_v005.hierarchy.json")
            open(legacy, "w").close()
            result = SceneDataSidecar.migrate_legacy(
                os.path.join(d, "shot_v007.fbx"), base_stem=True
            )
            self.assertEqual(os.path.basename(result), ".shot.scene_data.json")


class RenameTest(unittest.TestCase):
    """rename moves sidecars (promoting v1 names first) when an export is renamed."""

    def test_renames_current_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            old_export = os.path.join(d, "old.fbx")
            new_export = os.path.join(d, "new.fbx")
            SceneDataSidecar.write_manifest(old_export, {"A"})
            renamed = SceneDataSidecar.rename(old_export, new_export)
            self.assertTrue(renamed)
            self.assertEqual(SceneDataSidecar.read_manifest(new_export), {"A"})
            self.assertIsNone(SceneDataSidecar.read_manifest(old_export))

    def test_promotes_v1_name_before_renaming(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, ".old.hierarchy.json")
            with open(legacy, "w") as f:
                json.dump({"paths": ["A"]}, f)
            old_export = os.path.join(d, "old.fbx")
            new_export = os.path.join(d, "new.fbx")
            SceneDataSidecar.rename(old_export, new_export)
            self.assertEqual(SceneDataSidecar.read_manifest(new_export), {"A"})
            self.assertFalse(os.path.exists(legacy))


class WriteReadManifestRoutingTest(unittest.TestCase):
    """write_manifest / read_manifest propagate base_stem correctly."""

    def test_roundtrip_with_base_stem(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot_v003.fbx")
            paths = {"root|child", "root|other"}
            written = SceneDataSidecar.write_manifest(export, paths, base_stem=True)
            self.assertIsNotNone(written)
            self.assertEqual(os.path.basename(written), ".shot.scene_data.json")

            read_back = SceneDataSidecar.read_manifest(export, base_stem=True)
            self.assertEqual(read_back, paths)

    def test_base_stem_shares_manifest_across_versions(self):
        with tempfile.TemporaryDirectory() as d:
            v3 = os.path.join(d, "shot_v003.fbx")
            v4 = os.path.join(d, "shot_v004.fbx")
            SceneDataSidecar.write_manifest(v3, {"a"}, base_stem=True)
            # Reading from a different version path should find the same data.
            self.assertEqual(SceneDataSidecar.read_manifest(v4, base_stem=True), {"a"})

    def test_plain_mode_does_not_share(self):
        with tempfile.TemporaryDirectory() as d:
            v3 = os.path.join(d, "shot_v003.fbx")
            v4 = os.path.join(d, "shot_v004.fbx")
            SceneDataSidecar.write_manifest(v3, {"a"})
            self.assertIsNone(SceneDataSidecar.read_manifest(v4))


class ManifestFormatTest(unittest.TestCase):
    """Format v2 structure, data snapshot round-trip, v1 read compat."""

    def test_v2_structure_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"A", "A|B"})
            with open(
                SceneDataSidecar.manifest_path_for(export), encoding="utf-8"
            ) as f:
                raw = json.load(f)
            self.assertEqual(raw["format"], 2)
            self.assertEqual(raw["hierarchy"]["paths"], ["A", "A|B"])
            self.assertEqual(raw["hierarchy"]["object_count"], 2)
            self.assertTrue(raw["hierarchy"]["hash"])
            # Empty data is omitted; no flat v1 keys at top level.
            self.assertNotIn("data_export", raw)
            self.assertNotIn("paths", raw)

    def test_data_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            data = {"shot_metadata": {"shots": [1, 2]}, "fbx_takes": ["t1"]}
            SceneDataSidecar.write_manifest(export, {"A"}, data=data)
            self.assertEqual(SceneDataSidecar.read_data(export), data)
            # Hierarchy read is unaffected by the data section.
            self.assertEqual(SceneDataSidecar.read_manifest(export), {"A"})

    def test_authoring_locate_hint_is_not_recorded(self):
        """The sidecar ships beside the deliverable, so it carries no machine paths.

        The lightmap publisher stamps an absolute authoring directory into the
        manifest so the GLB converter can find the EXRs; the sidecar is a
        different consumer entirely ("a form of export", per this module) and a
        recipient can do nothing with a path on someone else's drive but read the
        folder names in it.  Measured on a client hand-off: the shipped sidecar
        named the client and the full Dropbox tree it was authored in.
        """
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            authored = r"O:\Dropbox (Client)\Team Folder\PROD\maya\sourceimages"
            data = {
                "lightmap_metadata": {
                    "version": 1,
                    "dir": authored,
                    "objects": [{"name": "room", "map": "room_Lightmap.exr"}],
                },
                "shot_metadata": {"shots": [1]},
            }
            SceneDataSidecar.write_manifest(export, {"A"}, data=data)
            with open(
                SceneDataSidecar.manifest_path_for(export), encoding="utf-8"
            ) as f:
                raw = f.read()
            self.assertNotIn("Dropbox (Client)", raw)
            written = json.loads(raw)["data_export"]
            self.assertNotIn("dir", written["lightmap_metadata"])
            # Scrubbed, not gutted: everything a consumer acts on survives, and
            # unrelated channels are untouched.
            self.assertEqual(
                written["lightmap_metadata"]["objects"],
                [{"name": "room", "map": "room_Lightmap.exr"}],
            )
            self.assertEqual(written["shot_metadata"], {"shots": [1]})

    def test_scrub_does_not_mutate_the_caller_snapshot(self):
        """The scrub is for the file; the in-memory snapshot is the caller's."""
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            data = {"lightmap_metadata": {"version": 1, "dir": r"O:\authored"}}
            SceneDataSidecar.write_manifest(export, {"A"}, data=data)
            self.assertEqual(data["lightmap_metadata"]["dir"], r"O:\authored")

    def test_read_data_none_without_section(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"A"})
            self.assertIsNone(SceneDataSidecar.read_data(export))

    def test_v1_flat_manifest_still_readable(self):
        # A v1 manifest (promoted to the current name by migrate_legacy)
        # reads as the hierarchy section.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = ["A", "A|B"]
            v1 = {
                "paths": paths,
                "object_count": 2,
                "hash": SceneDataSidecar._paths_hash(paths),
            }
            with open(
                SceneDataSidecar.manifest_path_for(export), "w", encoding="utf-8"
            ) as f:
                json.dump(v1, f)
            self.assertEqual(SceneDataSidecar.read_manifest(export), set(paths))
            self.assertIsNone(SceneDataSidecar.read_data(export))
            self.assertEqual(
                SceneDataSidecar.compare(export, set(paths)), (True, [], [])
            )
            match, missing, _ = SceneDataSidecar.compare(export, {"A"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|B"])

    def test_data_churn_does_not_affect_hierarchy_check(self):
        # The hash covers only the paths — a metadata-only change must not
        # trip the hierarchy diff.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A", "A|B"}
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            SceneDataSidecar.write_manifest(export, paths, data={"k": 2})
            self.assertEqual(
                SceneDataSidecar.compare(export, paths), (True, [], [])
            )

    def test_prev_preserves_previous_data_record(self):
        # A data-only change refreshes .prev so the previous export's full
        # record survives.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A"}
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            SceneDataSidecar.write_manifest(export, paths, data={"k": 2})
            prev = SceneDataSidecar.manifest_path_for(export) + ".prev"
            self.assertTrue(os.path.exists(prev))
            with open(prev, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["data_export"], {"k": 1})
            self.assertEqual(SceneDataSidecar.read_data(export), {"k": 2})

    def test_no_prev_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A"}
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            prev = SceneDataSidecar.manifest_path_for(export) + ".prev"
            self.assertFalse(os.path.exists(prev))

    def test_dropping_data_archives_old_record(self):
        # Carrier cleared between exports: the old record moves to .prev and
        # the new manifest has no data section.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A"}
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            SceneDataSidecar.write_manifest(export, paths)
            prev = SceneDataSidecar.manifest_path_for(export) + ".prev"
            self.assertTrue(os.path.exists(prev))
            self.assertIsNone(SceneDataSidecar.read_data(export))


class PrevFallbackTest(unittest.TestCase):
    """compare/read_manifest fall back to the .prev backup when the manifest is gone.

    Guards against a deleted or corrupted manifest silently passing the
    hierarchy check when the last-known-good baseline is still on disk.
    """

    def _write_with_prev(self, d):
        """Write twice with differing content so a .prev exists; return export path."""
        export = os.path.join(d, "shot.fbx")
        SceneDataSidecar.write_manifest(export, {"A", "A|B"})
        SceneDataSidecar.write_manifest(export, {"A", "A|B", "A|C"})
        return export

    def test_compare_uses_prev_after_manifest_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            # .prev holds the older baseline {A, A|B}.
            match, missing, extra = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_compare_detects_drift_via_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            match, missing, extra = SceneDataSidecar.compare(export, {"A"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|B"])
            self.assertEqual(extra, [])

    def test_compare_uses_prev_when_manifest_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            with open(
                SceneDataSidecar.manifest_path_for(export), "w", encoding="utf-8"
            ) as f:
                f.write("not json{")
            match, _, _ = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_read_manifest_falls_back_to_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            self.assertEqual(SceneDataSidecar.read_manifest(export), {"A", "A|B"})

    def test_no_manifest_no_prev_passes(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "never.fbx")
            self.assertEqual(SceneDataSidecar.compare(export, {"X"}), (True, [], []))
            self.assertIsNone(SceneDataSidecar.read_manifest(export))

    def test_intact_manifest_wins_over_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._write_with_prev(d)
            # Manifest {A, A|B, A|C} present — .prev must NOT shadow it.
            match, _, _ = SceneDataSidecar.compare(export, {"A", "A|B", "A|C"})
            self.assertTrue(match)
            match, missing, _ = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|C"])


class CompatShimTest(unittest.TestCase):
    """The deprecated hierarchy_sidecar module aliases the new class."""

    def test_hierarchy_sidecar_alias(self):
        from mayatk.env_utils.hierarchy_sync.hierarchy_sidecar import (
            HierarchySidecar,
        )

        self.assertIs(HierarchySidecar, SceneDataSidecar)


if __name__ == "__main__":
    unittest.main(exit=False)
