# coding=utf-8
"""Shot Manifest mapping schema + loader tests.

Runs under mayapy: the ``shot_manifest`` import chain pulls ``maya.cmds`` at
module load (via ``_shots``), though none of these tests need a live scene.

    & $MAYAPY mayatk\\test\\test_shot_manifest_mapping.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from mayatk.anim_utils.shots.shot_manifest import mapping as M
from mayatk.anim_utils.shots.shot_manifest.mapping._spec import AUDIO_METHODS
from mayatk.anim_utils.shots.shot_manifest.mapping._mapping import (
    _AUDIO_BUILDERS,
    DEFAULT_DIR,
)
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ColumnMap, parse_csv
from pythontk.core_utils.schema_spec import SchemaError


class MappingSpecTest(unittest.TestCase):
    def test_audio_builder_registry_matches_descriptor_registry(self):
        # OCP guard: the resolver builders and the validate/docs descriptors
        # must enumerate the same methods.
        self.assertEqual(set(_AUDIO_BUILDERS), set(AUDIO_METHODS))

    def test_skeleton_is_valid_against_its_own_schema(self):
        self.assertTrue(M.MappingSpec.validate(M.MappingSpec.skeleton()).ok)

    def test_shipped_mappings_validate_clean(self):
        for name in ("default", "speedrun"):
            data = json.loads((DEFAULT_DIR / f"{name}.json").read_text(encoding="utf-8"))
            res = M.MappingSpec.validate(data)
            self.assertTrue(res.ok, f"{name}: errors={res.errors}")

    def test_unknown_audio_method_is_error(self):
        res = M.MappingSpec.validate({"audio_resolve": {"method": "bogus"}})
        self.assertFalse(res.ok)
        self.assertTrue(any("audio_resolve" in e for e in res.errors))

    def test_regex_method_requires_pattern(self):
        res = M.MappingSpec.validate({"audio_resolve": {"method": "regex"}})
        self.assertFalse(res.ok)
        self.assertTrue(any("pattern" in e for e in res.errors))

    def test_unknown_top_level_key_is_warning_not_error(self):
        res = M.MappingSpec.validate({"bogus": 1})
        self.assertTrue(res.ok)  # tolerated — does not reject the file
        self.assertTrue(res.warnings)

    def test_load_mapping_raises_schema_error_on_bad_method(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text(json.dumps({"audio_resolve": {"method": "nope"}}), encoding="utf-8")
            with self.assertRaises(SchemaError):
                M.load_mapping(str(p))

    def test_discover_includes_shipped_builtins(self):
        names = M.discover()
        self.assertIn("default", names)
        self.assertIn("speedrun", names)


class MappingResolveRoundTripTest(unittest.TestCase):
    """The refactor must be behavior-preserving: a CSV parsed through the
    ``default`` mapping yields exactly what the underlying ColumnMap path does."""

    CSV = (
        "SECTION A: INTRO\n"
        "Step,Step Contents,Asset Names,Voice Support\n"
        "A01.),Aileron fades in,wing_L,Welcome to the course\n"
        "A02.),Rudder appears,rudder,N/A\n"
    )

    def _csv(self, d):
        p = Path(d) / "m.csv"
        p.write_text(self.CSV, encoding="utf-8")
        return str(p)

    def test_resolve_matches_direct_columnmap_path(self):
        with tempfile.TemporaryDirectory() as d:
            csv = self._csv(d)
            data = M.load_mapping("default")
            via_resolve = M.resolve(csv, mapping=data)
            via_direct = parse_csv(csv, columns=ColumnMap.from_dict(data["columns"]))
            self.assertTrue(via_resolve)  # non-empty
            self.assertEqual(
                [s.step_id for s in via_resolve],
                [s.step_id for s in via_direct],
            )
            self.assertEqual(
                [s.description for s in via_resolve],
                [s.description for s in via_direct],
            )

    def test_speedrun_mapping_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            csv = self._csv(d)
            steps = M.resolve(csv, name="speedrun", directory=str(DEFAULT_DIR))
            self.assertTrue(steps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
