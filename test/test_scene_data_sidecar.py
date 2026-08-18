# !/usr/bin/python
# coding=utf-8
"""Pure-Python tests for SceneDataSidecar.

These tests don't require Maya — the sidecar is a path/JSON helper that
sits below the cmds/mel layer.  Run with the workspace venv:

    & .venv\\Scripts\\python.exe -m pytest mayatk\\test\\test_scene_data_sidecar.py -v
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
    """Format v3 structure, data snapshot round-trip, v1 read compat."""

    def test_v3_structure_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"A", "A|B"})
            with open(
                SceneDataSidecar.manifest_path_for(export), encoding="utf-8"
            ) as f:
                raw = json.load(f)
            self.assertEqual(raw["format"], 3)
            self.assertEqual(raw["hierarchy"]["paths"], ["A", "A|B"])
            self.assertEqual(raw["hierarchy"]["object_count"], 2)
            self.assertTrue(raw["hierarchy"]["hash"])
            # Empty data is omitted; no flat v1 keys at top level; no diff
            # section when none was recorded.
            self.assertNotIn("data_export", raw)
            self.assertNotIn("paths", raw)
            self.assertNotIn("last_diff", raw["hierarchy"])

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

    def test_dropping_data_drops_the_record(self):
        # Carrier cleared between exports: the payload is rebuilt whole, so
        # the new manifest simply has no data section (and no shadow copy
        # of the old one survives anywhere).
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A"}
            SceneDataSidecar.write_manifest(export, paths, data={"k": 1})
            SceneDataSidecar.write_manifest(export, paths)
            self.assertIsNone(SceneDataSidecar.read_data(export))


class SingleFileContractTest(unittest.TestCase):
    """v3 contract: one sidecar per stem — no companions ever written,
    leftover v2-era companions swept on write."""

    def test_write_never_creates_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"A"}, data={"k": 1})
            SceneDataSidecar.write_manifest(export, {"A", "A|B"}, data={"k": 2})
            manifest = SceneDataSidecar.manifest_path_for(export)
            self.assertFalse(os.path.exists(manifest + ".prev"))
            # The manifest is the only sidecar in the folder.
            self.assertEqual(os.listdir(d), [os.path.basename(manifest)])

    def test_write_sweeps_v2_companions(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            manifest = SceneDataSidecar.manifest_path_for(export)
            leftovers = [
                manifest + ".prev",
                os.path.join(d, ".shot.hierarchy.json.prev"),
                os.path.join(d, ".shot.hierarchy_diff.txt"),
            ]
            for p in leftovers:
                with open(p, "w") as f:
                    f.write("{}")
            SceneDataSidecar.write_manifest(export, {"A"})
            for p in leftovers:
                self.assertFalse(os.path.exists(p), p)
            self.assertTrue(os.path.exists(manifest))

    def test_write_sweeps_companions_under_base_stem(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot_v003.fbx")
            manifest = SceneDataSidecar.manifest_path_for(export, base_stem=True)
            leftovers = [
                manifest + ".prev",
                os.path.join(d, ".shot.hierarchy_diff.txt"),
            ]
            for p in leftovers:
                with open(p, "w") as f:
                    f.write("{}")
            SceneDataSidecar.write_manifest(export, {"A"}, base_stem=True)
            for p in leftovers:
                self.assertFalse(os.path.exists(p), p)

    def test_failed_write_leaves_no_tmp(self):
        # A write into a nonexistent directory fails without littering.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "missing_subdir", "shot.fbx")
            result = SceneDataSidecar.write_manifest(export, {"A"})
            self.assertIsNone(result)


class LastDiffTest(unittest.TestCase):
    """hierarchy.last_diff records the accepted diff; clean writes drop it."""

    LAST_DIFF = {
        "missing": ["OLD|node"],
        "extra": ["NEW|node"],
        "reparented": [["OLD", "NEW", 1]],
    }

    def _raw(self, export):
        with open(
            SceneDataSidecar.manifest_path_for(export), encoding="utf-8"
        ) as f:
            return json.load(f)

    def test_last_diff_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(
                export, {"A"}, last_diff=self.LAST_DIFF
            )
            self.assertEqual(
                self._raw(export)["hierarchy"]["last_diff"], self.LAST_DIFF
            )

    def test_clean_write_drops_last_diff(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(
                export, {"A"}, last_diff=self.LAST_DIFF
            )
            SceneDataSidecar.write_manifest(export, {"A"})
            self.assertNotIn("last_diff", self._raw(export)["hierarchy"])

    def test_last_diff_does_not_affect_check_or_reads(self):
        # The hash covers only the paths; readers ignore the diff record.
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            paths = {"A", "A|B"}
            SceneDataSidecar.write_manifest(
                export, paths, data={"k": 1}, last_diff=self.LAST_DIFF
            )
            self.assertEqual(
                SceneDataSidecar.compare(export, paths), (True, [], [])
            )
            self.assertEqual(SceneDataSidecar.read_manifest(export), paths)
            self.assertEqual(SceneDataSidecar.read_data(export), {"k": 1})


class HiddenAttributeTest(unittest.TestCase):
    """On Windows the manifest carries FILE_ATTRIBUTE_HIDDEN through rewrites.

    os.replace hands the result the tmp file's attributes (measured), so the
    flag must be re-applied after every write — and a hidden target rejects
    open('w'), so the rewrite path itself proves the tmp+replace contract.
    """

    @staticmethod
    def _is_hidden(path):
        import stat

        return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)

    @unittest.skipUnless(os.name == "nt", "Windows-only attribute")
    def test_manifest_hidden_after_write_and_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"A"})
            manifest = SceneDataSidecar.manifest_path_for(export)
            self.assertTrue(self._is_hidden(manifest))
            # Rewriting a hidden manifest must succeed and stay hidden.
            result = SceneDataSidecar.write_manifest(export, {"A", "A|B"})
            self.assertIsNotNone(result)
            self.assertTrue(self._is_hidden(manifest))
            self.assertEqual(
                SceneDataSidecar.read_manifest(export), {"A", "A|B"}
            )


class PrevFallbackTest(unittest.TestCase):
    """Reads fall back to a surviving v2-era .prev until a write sweeps it.

    v3 never writes .prev files, but ones left by v2 writers exist in the
    wild — fixtures place them by hand, exactly as a v2 writer left them.
    """

    def _fixture_with_prev(self, d):
        """Manifest {A, A|B, A|C} plus a hand-placed v2-era .prev {A, A|B}."""
        export = os.path.join(d, "shot.fbx")
        SceneDataSidecar.write_manifest(export, {"A", "A|B", "A|C"})
        old = ["A", "A|B"]
        prev = SceneDataSidecar.manifest_path_for(export) + ".prev"
        with open(prev, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "format": 2,
                    "hierarchy": {
                        "paths": old,
                        "object_count": len(old),
                        "hash": SceneDataSidecar._paths_hash(old),
                    },
                },
                f,
            )
        return export

    def test_compare_uses_prev_after_manifest_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._fixture_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            # .prev holds the older baseline {A, A|B}.
            match, missing, extra = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_compare_detects_drift_via_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._fixture_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            match, missing, extra = SceneDataSidecar.compare(export, {"A"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|B"])
            self.assertEqual(extra, [])

    def test_compare_uses_prev_when_manifest_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._fixture_with_prev(d)
            # Corrupt in place (remove first: the manifest may be hidden,
            # and a hidden file rejects open('w')).
            manifest = SceneDataSidecar.manifest_path_for(export)
            os.remove(manifest)
            with open(manifest, "w", encoding="utf-8") as f:
                f.write("not json{")
            match, _, _ = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertTrue(match)

    def test_read_manifest_falls_back_to_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._fixture_with_prev(d)
            os.remove(SceneDataSidecar.manifest_path_for(export))
            self.assertEqual(SceneDataSidecar.read_manifest(export), {"A", "A|B"})

    def test_no_manifest_no_prev_passes(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "never.fbx")
            self.assertEqual(SceneDataSidecar.compare(export, {"X"}), (True, [], []))
            self.assertIsNone(SceneDataSidecar.read_manifest(export))

    def test_intact_manifest_wins_over_prev(self):
        with tempfile.TemporaryDirectory() as d:
            export = self._fixture_with_prev(d)
            # Manifest {A, A|B, A|C} present — .prev must NOT shadow it.
            match, _, _ = SceneDataSidecar.compare(export, {"A", "A|B", "A|C"})
            self.assertTrue(match)
            match, missing, _ = SceneDataSidecar.compare(export, {"A", "A|B"})
            self.assertFalse(match)
            self.assertEqual(missing, ["A|C"])


class AncestorScopeTest(unittest.TestCase):
    """The baseline records what SHIPS, not how the export set was scoped.

    Maya's ``exportSelected`` (FBX and mayaAscii alike, probe-verified
    2026-08-17) writes the parent chain of every selected DAG node, so a
    group ships whether the group itself or only its leaves are in the
    export set.  A path set built from the set alone read those two as
    different hierarchies -- the exporter's check reported ``- INTERACTIVE``
    (the group) after a leaves-only export against a manifest written from a
    group-selected export of the SAME file.  Path sets are therefore closed
    under ancestors, on both sides of the comparison (manifests written
    before this rule carry no ancestor entries).
    """

    def test_build_clean_path_set_includes_ancestors(self):
        paths = SceneDataSidecar.build_clean_path_set(
            ["|INTERACTIVE|part_HOOK|part_HOOKShape"]
        )
        self.assertEqual(
            paths,
            {
                "INTERACTIVE",
                "INTERACTIVE|part_HOOK",
                "INTERACTIVE|part_HOOK|part_HOOKShape",
            },
        )

    def test_build_clean_path_set_strips_namespaces_before_closing(self):
        paths = SceneDataSidecar.build_clean_path_set(["|ns:G|ns:SUB|ns:C"])
        self.assertEqual(paths, {"G", "G|SUB", "G|SUB|C"})

    def test_with_ancestors_is_idempotent(self):
        closed = SceneDataSidecar.with_ancestors({"A|B|C", "X"})
        self.assertEqual(closed, {"A", "A|B", "A|B|C", "X"})
        self.assertEqual(SceneDataSidecar.with_ancestors(closed), closed)

    def test_compare_group_selected_vs_leaves_selected_match(self):
        """The user-visible case: manifest from a group-selected export,
        current export set = the leaves only (or the reverse)."""
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "HOOKS_PINS.fbx")
            group_selected = {"INTERACTIVE", "INTERACTIVE|part", "INTERACTIVE|part|partShape"}
            leaves_only = {"INTERACTIVE|part", "INTERACTIVE|part|partShape"}

            SceneDataSidecar.write_manifest(export, group_selected)
            self.assertEqual(
                SceneDataSidecar.compare(export, leaves_only), (True, [], [])
            )
            SceneDataSidecar.write_manifest(export, leaves_only)
            self.assertEqual(
                SceneDataSidecar.compare(export, group_selected), (True, [], [])
            )

    def test_compare_closes_legacy_manifest_without_ancestors(self):
        """A manifest written before the rule (leaves + shapes only, as every
        'visible'-mode export recorded) still compares equal to a closed set."""
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            legacy = ["G|C", "G|C|CShape"]
            with open(SceneDataSidecar.manifest_path_for(export), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "format": 3,
                        "hierarchy": {
                            "paths": legacy,
                            "object_count": len(legacy),
                            "hash": SceneDataSidecar._paths_hash(legacy),
                        },
                    },
                    f,
                )
            current = SceneDataSidecar.build_clean_path_set(["|G|C", "|G|C|CShape"])
            self.assertIn("G", current)
            self.assertEqual(SceneDataSidecar.compare(export, current), (True, [], []))

    def test_real_structural_change_still_detected(self):
        """Closure must not mask a real difference: a hidden sibling that ships
        only when the group itself is exported."""
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"G|C", "G|C|CShape"})
            match, missing, extra = SceneDataSidecar.compare(
                export, {"G", "G|C", "G|C|CShape", "G|D", "G|D|DShape"}
            )
            self.assertFalse(match)
            self.assertEqual(missing, [])
            self.assertEqual(extra, ["G|D", "G|D|DShape"])

    def test_reparenting_still_detected_under_closure(self):
        """A subtree moved under a new parent reads as reparenting, and the
        bare new-parent entry the closure adds is what the exporter's check
        already treats as explained."""
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "shot.fbx")
            SceneDataSidecar.write_manifest(export, {"GRP|c", "GRP|c|cShape"})
            match, missing, extra = SceneDataSidecar.compare(
                export, {"NEW|GRP|c", "NEW|GRP|c|cShape"}
            )
            self.assertFalse(match)
            self.assertEqual(
                SceneDataSidecar.detect_reparenting(missing, extra),
                [("GRP", "NEW", 3)],
            )
            self.assertIn("NEW", extra)


class FormatDiffReportTest(unittest.TestCase):
    """format_diff_report returns the full text; nothing touches disk."""

    def test_report_sections(self):
        report = SceneDataSidecar.format_diff_report(
            ["OLD|a", "OLD|a|aShape"], ["NEW|a", "NEW|a|aShape"]
        )
        self.assertIn("Hierarchy Diff Report", report)
        self.assertIn("Missing:  2", report)
        self.assertIn("Extra:    2", report)
        self.assertIn("All missing (2):", report)
        self.assertIn("  - OLD|a", report)
        self.assertIn("  + NEW|a", report)

    def test_report_calls_out_reparenting(self):
        missing = ["grp|child"]
        extra = ["root|grp|child", "root"]
        report = SceneDataSidecar.format_diff_report(missing, extra)
        self.assertIn("Reparented: 'grp' moved under 'root' (1 nodes)", report)


class CompatShimTest(unittest.TestCase):
    """The deprecated hierarchy_sidecar module aliases the new class."""

    def test_hierarchy_sidecar_alias(self):
        from mayatk.env_utils.hierarchy_sync.hierarchy_sidecar import (
            HierarchySidecar,
        )

        self.assertIs(HierarchySidecar, SceneDataSidecar)


if __name__ == "__main__":
    unittest.main(exit=False)
