# !/usr/bin/python
# coding=utf-8
"""Mock-based tests for ``mayatk.mat_utils.marmoset_bridge._toolbag_helpers``.

These helpers run inside Marmoset Toolbag's bundled Python (``mset``),
not inside Maya. We stub ``mset`` here and exercise the pure-Python
control flow directly -- no Maya, no Toolbag required.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock


# --------------------------------------------------------------------------
# Stub ``mset`` BEFORE the helper module is imported. The helper module
# captures ``import mset`` into a module-global at top level, so we have to
# inject our fake first.
# --------------------------------------------------------------------------
class _FakeMaterial:
    """Stand-in for ``mset.Material``."""


class _FakeSkyBoxObject:
    """Stand-in for ``mset.SkyBoxObject`` -- enables isinstance()."""


class _FakeMeshObject:
    """Stand-in for ``mset.MeshObject`` -- enables isinstance() in
    :func:`helpers.collect_mesh_objects` and supports ``.parent``
    re-assignment so the bake-pairing tests can assert on resulting
    parent links."""

    def __init__(self, name=""):
        self.name = name
        self.parent = None


_fake_mset = MagicMock()
_fake_mset.Material = _FakeMaterial
_fake_mset.SkyBoxObject = _FakeSkyBoxObject
_fake_mset.MeshObject = _FakeMeshObject
sys.modules["mset"] = _fake_mset

# Make the helper importable from this test (helpers live next to the
# bridge package, not under templates/).
_PKG_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "mayatk",
        "mat_utils",
        "marmoset_bridge",
    )
)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import _toolbag_helpers as helpers  # noqa: E402


class TestFindMaterial(unittest.TestCase):
    """Exact match must always win over substring fallback."""

    def test_exact_match_preferred_over_substring(self):
        exact = _FakeMaterial()
        exact.name = "MAT_Body"
        suffixed = _FakeMaterial()
        suffixed.name = "MAT_Body_ncl1_1"  # FBX-imported variant

        # Substring candidate first in the list -- exact match must still win.
        result = helpers.find_material("MAT_Body", [suffixed, exact])
        self.assertIs(result, exact)

    def test_substring_fallback_when_no_exact(self):
        m = _FakeMaterial()
        m.name = "MAT_Body_ncl1_1"
        self.assertIs(helpers.find_material("MAT_Body", [m]), m)

    def test_no_match_returns_none(self):
        m = _FakeMaterial()
        m.name = "Unrelated"
        self.assertIsNone(helpers.find_material("MAT_Body", [m]))


class TestLoadManifest(unittest.TestCase):
    def test_missing_path_returns_empty(self):
        self.assertEqual(helpers.load_manifest("/nonexistent.json"), {})

    def test_blank_path_returns_empty(self):
        self.assertEqual(helpers.load_manifest(""), {})

    def test_malformed_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("{not valid json")
            path = fh.name
        try:
            self.assertEqual(helpers.load_manifest(path), {})
        finally:
            os.unlink(path)

    def test_returns_materials_dict(self):
        payload = {"materials": {"M_A": {"baseColor": "a.png"}}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(payload, fh)
            path = fh.name
        try:
            result = helpers.load_manifest(path)
            self.assertEqual(result["M_A"]["baseColor"], "a.png")
        finally:
            os.unlink(path)


class TestWireMaterialsFromManifest(unittest.TestCase):
    def setUp(self):
        # Real on-disk texture files: wire_materials_from_manifest now
        # checks os.path.isfile() before calling setField, so tests must
        # use paths that actually exist.
        self._tmpdir = tempfile.mkdtemp(prefix="wire_test_")
        self.base_png = os.path.join(self._tmpdir, "base.png")
        self.normal_png = os.path.join(self._tmpdir, "normal.png")
        for p in (self.base_png, self.normal_png):
            with open(p, "wb") as fh:
                fh.write(b"")

        self._manifest_path = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(
            {
                "materials": {
                    "MAT_Test": {
                        "baseColor": self.base_png,
                        "normal": self.normal_png,
                    }
                }
            },
            self._manifest_path,
        )
        self._manifest_path.close()

    def tearDown(self):
        import shutil
        os.unlink(self._manifest_path.name)
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        _fake_mset.getAllMaterials.reset_mock(return_value=True, side_effect=True)

    def _make_subroutine(self, *field_names):
        """Build a fake subroutine that reports *field_names* from getFieldNames."""
        sub = MagicMock()
        sub.getFieldNames.return_value = list(field_names)
        return sub

    def _make_material(self, name, roughness_field="Roughness Map"):
        """Build a FakeMaterial with subroutines that report realistic field names.

        Each subroutine's getFieldNames() returns the field the helper
        should pick. ``roughness_field`` is configurable so tests can
        simulate both 'Gloss Map' and 'Roughness Map' variants.
        """
        m = _FakeMaterial()
        m.name = name
        m.albedo = self._make_subroutine("Albedo Map")
        m.surface = self._make_subroutine("Normal Map")
        m.microsurface = self._make_subroutine(roughness_field)
        m.reflectivity = self._make_subroutine("Metalness Map")
        m.occlusion = self._make_subroutine("Occlusion Map")
        m.emissive = self._make_subroutine("Emissive Map")
        m.transparency = self._make_subroutine("Transparency Map")
        return m

    def test_wires_each_slot_and_returns_count(self):
        mat = self._make_material("MAT_Test")
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )

        self.assertEqual(wired, 2)
        # Toolbag API: subroutine.setField(name, path)
        mat.albedo.setField.assert_called_with("Albedo Map", self.base_png)
        mat.surface.setField.assert_called_with("Normal Map", self.normal_png)

    def test_color_slots_tagged_srgb_data_slots_left_linear(self):
        """After wiring, colour maps must be flagged sRGB and data maps
        Linear. Toolbag's setField loads every texture sRGB=False (Linear),
        which washes out albedo/emissive; the helper reads the field's
        Texture back and corrects the colour-space per slot."""
        mat = self._make_material("MAT_Test")
        _fake_mset.getAllMaterials.return_value = [mat]

        helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )

        # baseColor -> albedo: colour map, must be sRGB.
        self.assertIs(mat.albedo.getField.return_value.sRGB, True)
        # normal -> surface: data map, must stay Linear.
        self.assertIs(mat.surface.getField.return_value.sRGB, False)

    def test_returns_zero_when_no_matching_material(self):
        unrelated = self._make_material("Other")
        _fake_mset.getAllMaterials.return_value = [unrelated]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 0)

    def test_per_slot_exception_does_not_abort_pass(self):
        """A single bad slot must not skip the remaining slots."""
        mat = self._make_material("MAT_Test")
        mat.albedo.setField.side_effect = RuntimeError("simulated Toolbag fail")
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 1)
        mat.surface.setField.assert_called_with("Normal Map", self.normal_png)

    def test_missing_texture_file_is_skipped_not_wired(self):
        """If the texture path doesn't exist on disk, skip rather than
        passing it to Toolbag (which would 'wire' it but display nothing)."""
        # Rewrite manifest to point at a non-existent file.
        bogus = os.path.join(self._tmpdir, "does_not_exist.png")
        with open(self._manifest_path.name, "w", encoding="utf-8") as fh:
            json.dump(
                {"materials": {"MAT_Test": {"baseColor": bogus}}}, fh
            )
        mat = self._make_material("MAT_Test")
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 0)
        mat.albedo.setField.assert_not_called()

    def test_roughness_picks_gloss_field_when_subroutine_is_gloss(self):
        """The microsurface module is variant-driven; if the active
        variant exposes 'Gloss Map' (not 'Roughness Map'), use that."""
        with open(self._manifest_path.name, "w", encoding="utf-8") as fh:
            json.dump(
                {"materials": {"MAT_Test": {"roughness": self.base_png}}}, fh
            )
        mat = self._make_material("MAT_Test", roughness_field="Gloss Map")
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 1)
        mat.microsurface.setField.assert_called_with("Gloss Map", self.base_png)

    def test_roughness_picks_roughness_field_when_subroutine_is_roughness(self):
        """And conversely, picks 'Roughness Map' when that's the variant."""
        with open(self._manifest_path.name, "w", encoding="utf-8") as fh:
            json.dump(
                {"materials": {"MAT_Test": {"roughness": self.base_png}}}, fh
            )
        mat = self._make_material("MAT_Test", roughness_field="Roughness Map")
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 1)
        mat.microsurface.setField.assert_called_with("Roughness Map", self.base_png)

    def test_subroutine_with_no_fields_is_skipped(self):
        """If the variant is disabled (empty field list), don't crash."""
        with open(self._manifest_path.name, "w", encoding="utf-8") as fh:
            json.dump(
                {"materials": {"MAT_Test": {"roughness": self.base_png}}}, fh
            )
        mat = self._make_material("MAT_Test")
        # Override to no fields available.
        mat.microsurface.getFieldNames.return_value = []
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 0)
        mat.microsurface.setField.assert_not_called()

    def test_field_discovery_falls_back_to_first_available(self):
        """If none of the candidate names matches, use whatever field
        the subroutine *does* expose (most subroutines have exactly one)."""
        with open(self._manifest_path.name, "w", encoding="utf-8") as fh:
            json.dump(
                {"materials": {"MAT_Test": {"roughness": self.base_png}}}, fh
            )
        mat = self._make_material("MAT_Test")
        mat.microsurface.getFieldNames.return_value = ["Unknown Variant Map"]
        _fake_mset.getAllMaterials.return_value = [mat]

        wired = helpers.wire_materials_from_manifest(
            self._manifest_path.name, verbose=False
        )
        self.assertEqual(wired, 1)
        mat.microsurface.setField.assert_called_with(
            "Unknown Variant Map", self.base_png
        )


class TestSplitHighLow(unittest.TestCase):
    """The 4 suffix-config rows + edge cases for ``.001`` strip and
    meshes that happen to end in both suffixes."""

    @staticmethod
    def _obj(name, parent=None):
        # ``spec=["name", "parent"]`` so MagicMock can't auto-create stray
        # attributes that the parent-chain walk in split_high_low would
        # otherwise follow indefinitely into fake-MagicMock-land.
        m = MagicMock(spec=["name", "parent"])
        m.name = name
        m.parent = parent
        return m

    def _names(self, group):
        return [o.name for o in group]

    # ---- Truth-table: both suffixes set ---------------------------------

    def test_both_suffixes_set_explicit_match_only(self):
        objs = [
            self._obj("body_high"),
            self._obj("body_low"),
            self._obj("decoration"),    # neither -> others
        ]
        h, lo, ot = helpers.split_high_low(objs, "_high", "_low")
        self.assertEqual(self._names(h), ["body_high"])
        self.assertEqual(self._names(lo), ["body_low"])
        self.assertEqual(self._names(ot), ["decoration"])

    # ---- Truth-table: only HIGH set (the user's preferred workflow) -----

    def test_only_high_suffix_set_rest_becomes_low(self):
        objs = [
            self._obj("body_high"),
            self._obj("retopo_a"),
            self._obj("retopo_b"),
        ]
        h, lo, ot = helpers.split_high_low(objs, "_high", "")
        self.assertEqual(self._names(h), ["body_high"])
        self.assertEqual(self._names(lo), ["retopo_a", "retopo_b"])
        self.assertEqual(ot, [])

    # ---- Truth-table: only LOW set --------------------------------------

    def test_only_low_suffix_set_rest_becomes_high(self):
        objs = [
            self._obj("retopo_low"),
            self._obj("sculpt_a"),
            self._obj("sculpt_b"),
        ]
        h, lo, ot = helpers.split_high_low(objs, "", "_low")
        self.assertEqual(self._names(h), ["sculpt_a", "sculpt_b"])
        self.assertEqual(self._names(lo), ["retopo_low"])
        self.assertEqual(ot, [])

    # ---- Truth-table: neither set ---------------------------------------

    def test_neither_suffix_set_all_become_others(self):
        objs = [self._obj("a"), self._obj("b")]
        h, lo, ot = helpers.split_high_low(objs, "", "")
        self.assertEqual(h, [])
        self.assertEqual(lo, [])
        self.assertEqual(self._names(ot), ["a", "b"])

    # ---- Edge cases -----------------------------------------------------

    def test_fbx_dot_001_duplicate_suffix_is_stripped(self):
        """FBX importers sometimes append ``.001`` to duplicate transforms;
        the suffix match must still work."""
        objs = [
            self._obj("body_high.001"),
            self._obj("body_low.001"),
        ]
        h, lo, ot = helpers.split_high_low(objs, "_high", "_low")
        self.assertEqual(self._names(h), ["body_high.001"])
        self.assertEqual(self._names(lo), ["body_low.001"])
        self.assertEqual(ot, [])

    def test_mesh_ending_in_both_suffixes_goes_to_high(self):
        """HIGH is checked first; a name ending in both becomes high."""
        objs = [self._obj("cube_low_high")]
        h, lo, _ot = helpers.split_high_low(objs, "_high", "_low")
        self.assertEqual(self._names(h), ["cube_low_high"])
        self.assertEqual(lo, [])

    def test_objects_without_name_attr_default_to_empty(self):
        """getattr fallback: an object with no .name doesn't crash; it
        just won't match any suffix."""
        obj = MagicMock(spec=[])  # no attributes set
        h, lo, ot = helpers.split_high_low([obj], "_high", "_low")
        self.assertEqual(h, [])
        self.assertEqual(lo, [])
        # With both suffixes set, unmatched -> others.
        self.assertEqual(len(ot), 1)

    def test_preserves_input_order_within_buckets(self):
        """Bake output should be deterministic; the helper must not
        reorder objects within each bucket."""
        objs = [
            self._obj("b_high"),
            self._obj("a_high"),
            self._obj("z_high"),
        ]
        h, _, _ = helpers.split_high_low(objs, "_high", "_low")
        self.assertEqual(self._names(h), ["b_high", "a_high", "z_high"])

    # ---- Parent-chain classification: tag a group, not every mesh ------

    def test_parent_group_suffix_classifies_children(self):
        """A child mesh with no suffix should inherit its parent group's
        suffix -- so the user can tag ``engine_high`` once instead of
        renaming every mesh inside it."""
        engine_high = self._obj("engine_high")
        engine_low = self._obj("engine_low")
        children = [
            self._obj("block", parent=engine_high),
            self._obj("pipes", parent=engine_high),
            self._obj("retopo_block", parent=engine_low),
        ]
        h, lo, ot = helpers.split_high_low(children, "_high", "_low")
        self.assertEqual(self._names(h), ["block", "pipes"])
        self.assertEqual(self._names(lo), ["retopo_block"])
        self.assertEqual(ot, [])

    def test_own_suffix_wins_over_parent_suffix(self):
        """An explicit per-mesh suffix overrides whatever the parent
        group says -- the closest level (self) decides classification."""
        group_high = self._obj("group_high")
        mesh = self._obj("override_low", parent=group_high)
        h, lo, _ot = helpers.split_high_low([mesh], "_high", "_low")
        self.assertEqual(self._names(h), [])
        self.assertEqual(self._names(lo), ["override_low"])

    def test_walks_grandparent_when_immediate_parent_unsuffixed(self):
        """Hierarchy: ``vehicle_high > engine > block_mesh``. The walk
        must keep going up past the unsuffixed engine group."""
        vehicle_high = self._obj("vehicle_high")
        engine = self._obj("engine", parent=vehicle_high)
        mesh = self._obj("block", parent=engine)
        h, lo, _ot = helpers.split_high_low([mesh], "_high", "_low")
        self.assertEqual(self._names(h), ["block"])
        self.assertEqual(lo, [])

    def test_unmatched_chain_falls_through_to_rest_is_X(self):
        """No suffix anywhere in the chain -> normal fallback rules apply
        (with only HIGH set, anything unsuffixed becomes low)."""
        bare = self._obj("decoration", parent=self._obj("group"))
        _h, lo, ot = helpers.split_high_low([bare], "_high", "")
        self.assertEqual(self._names(lo), ["decoration"])
        self.assertEqual(ot, [])

    # ---- Pre-classified sidecar (Maya-side bake-pairs manifest) --------

    def test_pre_classified_dict_overrides_chain(self):
        """The pre-classified dict (written by the Maya bridge before
        FBX export) is the authoritative source -- it must win even when
        an object's own name or ancestor name says otherwise."""
        objs = [
            self._obj("body_high"),    # own name says high
            self._obj("body_low"),     # own name says low
            self._obj("unsuffixed"),   # neither
        ]
        # Force the opposite classification via the sidecar.
        pre = {
            "body_high": "low",
            "body_low": "high",
            "unsuffixed": "high",
        }
        h, lo, ot = helpers.split_high_low(
            objs, "_high", "_low", pre_classified=pre
        )
        self.assertEqual(self._names(h), ["body_low", "unsuffixed"])
        self.assertEqual(self._names(lo), ["body_high"])
        self.assertEqual(ot, [])

    def test_pre_classified_misses_fall_through_to_chain(self):
        """When the dict doesn't cover an object, the normal chain walk
        still runs -- so the new code path is purely additive."""
        objs = [
            self._obj("body_high"),  # not in dict; chain says high
            self._obj("retopo"),     # in dict, force low
        ]
        pre = {"retopo": "low"}
        h, lo, ot = helpers.split_high_low(
            objs, "_high", "_low", pre_classified=pre
        )
        self.assertEqual(self._names(h), ["body_high"])
        self.assertEqual(self._names(lo), ["retopo"])
        self.assertEqual(ot, [])

    def test_pre_classified_none_or_empty_is_no_op(self):
        """``None`` and ``{}`` must be equivalent and not break the chain
        walker's existing behaviour."""
        objs = [self._obj("body_high"), self._obj("body_low")]
        h1, l1, o1 = helpers.split_high_low(objs, "_high", "_low", pre_classified=None)
        h2, l2, o2 = helpers.split_high_low(objs, "_high", "_low", pre_classified={})
        self.assertEqual(self._names(h1), self._names(h2))
        self.assertEqual(self._names(l1), self._names(l2))
        self.assertEqual(o1, o2)

    def test_cycle_guard_does_not_infinite_loop(self):
        """A self-referential parent (malformed scene) must not hang the
        classifier. The 64-deep guard caps the walk."""
        a = self._obj("a")
        a.parent = a  # cycle
        h, lo, ot = helpers.split_high_low([a], "_high", "_low")
        # No suffix anywhere -> falls through to others.
        self.assertEqual(self._names(ot), ["a"])


class TestCollectMeshObjects(unittest.TestCase):
    """Walking an ``mset.ExternalObject``'s tree is the only way to get
    actual mesh transforms back from ``mset.importModel()`` in Toolbag 5+.
    These tests pin the recursion + isinstance filter so a regression
    here can't reintroduce the ``len()`` crash that brought us here."""

    @staticmethod
    def _xform(children):
        """Stand-in for a non-mesh transform node with children."""
        node = MagicMock()
        node.getChildren.return_value = children
        return node

    def test_none_root_returns_empty(self):
        self.assertEqual(helpers.collect_mesh_objects(None), [])

    def test_flat_external_object_returns_mesh_children(self):
        """ExternalObject is the typical Toolbag 5 importModel return."""
        a, b = _FakeMeshObject("a_low"), _FakeMeshObject("b_high")
        ext = self._xform([a, b])
        self.assertEqual(helpers.collect_mesh_objects(ext), [a, b])

    def test_recurses_into_group_children(self):
        """Nested FBX hierarchy (group -> meshes) must flatten."""
        a, b = _FakeMeshObject("a"), _FakeMeshObject("b")
        group = self._xform([a, b])
        ext = self._xform([group])
        self.assertEqual(helpers.collect_mesh_objects(ext), [a, b])

    def test_filters_non_mesh_children(self):
        """Cameras, lights, sky, etc. must not enter the bake group."""
        mesh = _FakeMeshObject("mesh")
        camera = MagicMock()  # NOT a _FakeMeshObject
        camera.getChildren.return_value = []
        ext = self._xform([mesh, camera])
        self.assertEqual(helpers.collect_mesh_objects(ext), [mesh])

    def test_getchildren_exception_is_swallowed(self):
        """Toolbag occasionally raises opaque errors; helper must keep going."""
        bad = MagicMock()
        bad.getChildren.side_effect = RuntimeError("Toolbag boom")
        self.assertEqual(helpers.collect_mesh_objects(bad), [])

    def test_already_a_list(self):
        """Defensive: caller may pre-flatten. Accept lists too."""
        a = _FakeMeshObject("a")
        self.assertEqual(helpers.collect_mesh_objects([a, "noise"]), [a])

    def test_already_a_single_mesh(self):
        a = _FakeMeshObject("a")
        self.assertEqual(helpers.collect_mesh_objects(a), [a])


class TestDerivePerRunLogPath(unittest.TestCase):
    """The single source of truth shared by bridge + helper. If this drifts,
    the bridge logs a link to one path and the helper writes to another."""

    def test_replaces_materials_suffix_and_swaps_ext(self):
        self.assertEqual(
            helpers.derive_per_run_log_path("/tmp/scene.materials.json"),
            "/tmp/scene.toolbag.log",
        )

    def test_handles_path_without_materials_suffix(self):
        self.assertEqual(
            helpers.derive_per_run_log_path("/tmp/data.json"),
            "/tmp/data.toolbag.log",
        )

    def test_empty_path_returns_empty(self):
        self.assertEqual(helpers.derive_per_run_log_path(""), "")
        self.assertEqual(helpers.derive_per_run_log_path(None), "")


class TestApplySkyPreset(unittest.TestCase):
    def tearDown(self):
        _fake_mset.getAllObjects.reset_mock(return_value=True, side_effect=True)

    def test_blank_preset_returns_false(self):
        self.assertFalse(helpers.apply_sky_preset(""))

    def test_no_skies_in_scene_returns_false(self):
        _fake_mset.getAllObjects.return_value = []
        self.assertFalse(helpers.apply_sky_preset("any.tbsky"))

    def test_loads_sky_on_first_skybox(self):
        """Toolbag 5 API: SkyBoxObject.loadSky(path) -- NOT loadPreset."""
        sky = _FakeSkyBoxObject()
        sky.loadSky = MagicMock()
        _fake_mset.getAllObjects.return_value = [sky]

        self.assertTrue(helpers.apply_sky_preset("X.tbsky"))
        sky.loadSky.assert_called_once_with("X.tbsky")


# --------------------------------------------------------------------------
# End-to-end: render a template, exec it in a sandbox with mocked mset,
# and prove that the wire calls actually fire. This catches regressions
# in template substitution, helper imports, and the order of operations
# inside main() -- which the per-function unit tests above cannot.
# --------------------------------------------------------------------------
class TestRenderedTemplateExecutes(unittest.TestCase):
    """Pretend to be Toolbag: exec the rendered script, verify wire calls."""

    @classmethod
    def setUpClass(cls):
        # The engine is DCC-free; Maya is mocked by the mock_tests conftest
        # only so the marmoset_bridge package __init__ imports cleanly.
        from mayatk.mat_utils.marmoset_bridge._marmoset_engine import (
            MarmosetEngine,
            SEND_TO,
        )
        cls.MarmosetEngine = MarmosetEngine
        cls.SEND_TO = SEND_TO

    def setUp(self):
        # Sibling suites (test_marmoset_rpc) pop sys.modules["mset"] on cleanup
        # to simulate "not hosted by Toolbag", clobbering the module-level fake
        # installed at import. These tests exec rendered templates that do
        # ``import mset``, so re-establish the fake here to stay order-
        # independent (otherwise ``import mset`` falls back to None and the
        # template hits ``'NoneType' has no attribute 'importModel'``).
        sys.modules["mset"] = _fake_mset

        self._tmpdir = tempfile.mkdtemp(prefix="toolbag_helpers_exec_")
        # Real textures on disk -- the new missing-file guard skips paths
        # that don't exist, so the manifest must point at actual files.
        self.bc_png = os.path.join(self._tmpdir, "body_BC.png")
        self.n_png = os.path.join(self._tmpdir, "body_N.png")
        for p in (self.bc_png, self.n_png):
            with open(p, "wb") as fh:
                fh.write(b"")

        manifest = {
            "materials": {
                "MAT_Body": {
                    "baseColor": self.bc_png,
                    "normal": self.n_png,
                }
            }
        }
        self.manifest_path = os.path.join(self._tmpdir, "scene.materials.json")
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)

        # Toolbag won't see this file; the rendered script just checks the
        # path exists before importing, so an empty placeholder is fine.
        self.fbx_path = os.path.join(self._tmpdir, "scene.fbx")
        with open(self.fbx_path, "wb") as fh:
            fh.write(b"")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_subroutine(self, field_name):
        sub = MagicMock()
        sub.getFieldNames.return_value = [field_name]
        return sub

    def _fake_scene(self, mat_names=("MAT_Body",), include_sky=True):
        """Build a fake Toolbag scene with named materials + optional sky.

        Each material's subroutines report a realistic single-field list
        from getFieldNames(), matching what Toolbag 5 actually does.
        """
        materials = []
        scene_objects = []
        mats = {}
        for n in mat_names:
            m = _FakeMaterial()
            m.name = n
            m.albedo = self._make_subroutine("Albedo Map")
            m.surface = self._make_subroutine("Normal Map")
            m.microsurface = self._make_subroutine("Roughness Map")
            m.reflectivity = self._make_subroutine("Metalness Map")
            m.occlusion = self._make_subroutine("Occlusion Map")
            m.emissive = self._make_subroutine("Emissive Map")
            m.transparency = self._make_subroutine("Transparency Map")
            materials.append(m)
            mats[n] = m
        if include_sky:
            sky = _FakeSkyBoxObject()
            sky.loadSky = MagicMock()
            scene_objects.append(sky)
            mats["__sky__"] = sky
        _fake_mset.getAllMaterials.return_value = materials
        _fake_mset.getAllObjects.return_value = scene_objects
        return mats

    def _render_and_exec(self, template):
        """Render *template* and exec it with our mocked mset in scope."""
        bridge = self.MarmosetEngine()
        rendered = bridge.render_template(
            template=template,
            mode=self.SEND_TO,
            model_path=self.fbx_path,
            manifest_path=self.manifest_path,
            output_dir=self._tmpdir,
            headless=False,
        )
        self.assertIsNotNone(rendered, f"{template} did not render.")
        self.assertNotIn(
            "__TOOLBAG_HELPERS_DIR__", rendered, "helpers dir not substituted"
        )

        # Strip the ``if __name__ == "__main__": main()`` guard's effect by
        # exec'ing under a synthetic module name AND then calling main()
        # directly. Many templates only run main() under __main__, so we
        # call it ourselves to make the test deterministic.
        ns = {"__name__": "__toolbag_template__"}
        exec(compile(rendered, f"<{template}>", "exec"), ns)
        self.assertIn("main", ns, "rendered script must define main()")
        ns["main"]()
        return ns

    def test_lookdev_template_wires_materials(self):
        """The rendered lookdev script must call setField on each manifest slot."""
        mats = self._fake_scene(mat_names=("MAT_Body",), include_sky=True)
        self._render_and_exec("lookdev")

        body = mats["MAT_Body"]
        body.albedo.setField.assert_called_with("Albedo Map", self.bc_png)
        body.surface.setField.assert_called_with("Normal Map", self.n_png)

    def test_lookdev_loads_sky_before_wiring(self):
        """Sky preset must be applied before the wiring pass."""
        mats = self._fake_scene(mat_names=("MAT_Body",), include_sky=True)
        sky = mats["__sky__"]
        self._render_and_exec("lookdev")

        # Both must have happened. Toolbag 5 API is loadSky, not loadPreset.
        sky.loadSky.assert_called_once()
        mats["MAT_Body"].albedo.setField.assert_called()

    def test_import_template_wires_materials(self):
        """The rendered import script must also call setField for each slot."""
        mats = self._fake_scene(mat_names=("MAT_Body",), include_sky=False)
        self._render_and_exec("import")

        body = mats["MAT_Body"]
        body.albedo.setField.assert_called_with("Albedo Map", self.bc_png)
        body.surface.setField.assert_called_with("Normal Map", self.n_png)

    def test_lookdev_handles_fbx_suffixed_material_name(self):
        """FBX-imported names like 'MAT_Body_ncl1_1' must still wire."""
        mats = self._fake_scene(
            mat_names=("MAT_Body_ncl1_1",), include_sky=True
        )
        self._render_and_exec("lookdev")

        suffixed = mats["MAT_Body_ncl1_1"]
        suffixed.albedo.setField.assert_called_with("Albedo Map", self.bc_png)

    def test_lookdev_writes_log_file_alongside_manifest(self):
        """The send_to log file must exist and contain wiring lines."""
        self._fake_scene(mat_names=("MAT_Body",), include_sky=True)
        self._render_and_exec("lookdev")

        log_path = os.path.join(self._tmpdir, "scene.toolbag.log")
        self.assertTrue(os.path.isfile(log_path), f"Log not written: {log_path}")
        content = open(log_path, "r", encoding="utf-8").read()
        self.assertIn("MAT_Body", content)
        self.assertIn("Wired", content)

    def _stage_bake_scene(self, mesh_names):
        """Configure _fake_mset to return *mesh_names* from importModel,
        and a BakerObject whose addGroup yields a TransformObject-shaped
        group with the Toolbag 5 child layout: a "High" container and a
        "Low" container that meshes get re-parented into.

        ``importModel`` is mocked to return an ``ExternalObject``-shaped
        wrapper (an object whose ``getChildren()`` returns the mesh list).
        The mesh instances are ``_FakeMeshObject`` so the helper's
        ``isinstance(c, mset.MeshObject)`` filter accepts them.

        Returns ``(baker, group, imported, high_parent, low_parent)``.
        Tests assert pairing by inspecting each mesh's resulting
        ``.parent`` attribute.
        """
        imported = [_FakeMeshObject(name=n) for n in mesh_names]
        external = MagicMock()
        external.getChildren.return_value = imported
        _fake_mset.importModel.return_value = external

        high_parent = MagicMock(name="HighParent")
        high_parent.name = "High"
        low_parent = MagicMock(name="LowParent")
        low_parent.name = "Low"
        group = MagicMock(name="BakeGroup")
        group.getChildren.return_value = [high_parent, low_parent]

        baker = MagicMock()
        baker.addGroup.return_value = group
        _fake_mset.BakerObject.return_value = baker
        return baker, group, imported, high_parent, low_parent

    def test_bake_template_pairs_high_low_via_explicit_suffixes(self):
        """The rendered bake script must re-parent the matching meshes
        into the group's "High" and "Low" container children."""
        baker, group, imported, high_p, low_p = self._stage_bake_scene(
            ["body_high", "body_low", "decoration"]
        )
        # Explicit both-suffix config (the test name): HIGH=_high, LOW=_low,
        # so an unmatched mesh ('decoration') lands in neither bucket. The
        # registry default for LOW_SUFFIX is "" ("rest is low"), which would
        # sweep 'decoration' into low -- this test exercises the explicit case.
        bridge = self.MarmosetEngine()
        rendered = bridge.render_template(
            template="bake",
            mode=self.SEND_TO,
            model_path=self.fbx_path,
            manifest_path=self.manifest_path,
            output_dir=self._tmpdir,
            params={"HIGH_SUFFIX": "_high", "LOW_SUFFIX": "_low"},
        )
        ns = {"__name__": "__toolbag_template__"}
        exec(compile(rendered, "<bake>", "exec"), ns)
        ns["main"]()

        # By-name lookup so failures point at the actual mesh that drifted.
        by_name = {m.name: m for m in imported}
        self.assertIs(by_name["body_high"].parent, high_p)
        self.assertIs(by_name["body_low"].parent, low_p)
        # 'decoration' belongs to neither bucket -- parent must NOT have
        # been touched by the bake group wiring.
        self.assertIsNot(by_name["decoration"].parent, high_p)
        self.assertIsNot(by_name["decoration"].parent, low_p)

    def test_bake_template_only_high_suffix_rest_becomes_low(self):
        """LOW='(none)' (empty string): unsuffixed meshes are wired as low."""
        baker, group, imported, high_p, low_p = self._stage_bake_scene(
            ["body_high", "retopo_a", "retopo_b"]
        )
        bridge = self.MarmosetEngine()
        rendered = bridge.render_template(
            template="bake",
            mode=self.SEND_TO,
            model_path=self.fbx_path,
            manifest_path=self.manifest_path,
            output_dir=self._tmpdir,
            params={"LOW_SUFFIX": ""},
        )
        ns = {"__name__": "__toolbag_template__"}
        exec(compile(rendered, "<bake>", "exec"), ns)
        ns["main"]()

        by_name = {m.name: m for m in imported}
        self.assertIs(by_name["body_high"].parent, high_p)
        self.assertIs(by_name["retopo_a"].parent, low_p)
        self.assertIs(by_name["retopo_b"].parent, low_p)

    def test_bake_template_output_path_is_psd(self):
        """Toolbag's BakerObject takes ``outputPath`` as the bake-project
        filename and the per-map writer derives output extensions from
        it. Toolbag only accepts ``.psd`` here; ``.tga``/``.tif`` cause
        ``Bake failed - check output path to see if it's valid``.

        Regression coverage: an earlier version of the template encoded
        a BAKE_BITS-driven ``.tga``/``.tif`` switch that broke headless
        bakes. If someone re-introduces that, this test fails fast
        instead of producing zero output files at runtime.
        """
        baker, group, imported, high_p, low_p = self._stage_bake_scene(
            ["body_high", "body_low"]
        )
        bridge = self.MarmosetEngine()
        rendered = bridge.render_template(
            template="bake",
            mode=self.SEND_TO,
            model_path=self.fbx_path,
            manifest_path=self.manifest_path,
            output_dir=self._tmpdir,
        )

        ns = {"__name__": "__toolbag_template__"}
        exec(compile(rendered, "<bake>", "exec"), ns)
        ns["main"]()

        # Search the rendered template source -- the output path lives in
        # the ``_output_path()`` helper and is consumed via ``setattr`` on
        # a MagicMock, so checking attribute-call history is fiddlier than
        # just asserting the literal in the rendered script.
        self.assertIn("bake.psd", rendered)
        for forbidden in ('"bake.tga"', "'bake.tga'", '"bake.tif"', "'bake.tif'"):
            self.assertNotIn(forbidden, rendered)

    def test_bake_template_setter_failure_does_not_abort(self):
        """One picky baker setter (e.g. a renamed attribute or wrong-type
        value in a future Toolbag) must NOT take down the rest of the
        bake setup. ``_set`` swallows + logs; subsequent attributes and
        the pairing step still run.

        Sets ``outputBits`` to raise TypeError on assignment and verifies
        (a) ``outputPath`` still got set, (b) pairing still completed.
        Regression coverage for the cascading-failure bug we hit while
        debugging Toolbag 5 API drift live.
        """
        baker, group, imported, high_p, low_p = self._stage_bake_scene(
            ["body_high", "body_low"]
        )

        # Make outputBits assignment fail. PropertyMock on the MagicMock
        # is the simplest way: any assignment triggers the side_effect.
        def _explode_on_assign(value):
            raise TypeError("outputBits is read-only in this fictional Toolbag")
        type(baker).outputBits = unittest.mock.PropertyMock(
            side_effect=_explode_on_assign
        )

        bridge = self.MarmosetEngine()
        rendered = bridge.render_template(
            template="bake",
            mode=self.SEND_TO,
            model_path=self.fbx_path,
            manifest_path=self.manifest_path,
            output_dir=self._tmpdir,
        )
        ns = {"__name__": "__toolbag_template__"}
        exec(compile(rendered, "<bake>", "exec"), ns)
        # Must not raise -- the setter exception is caught by _set.
        ns["main"]()

        # Subsequent attributes still got assigned.
        self.assertIsNotNone(baker.outputPath, "outputPath was not set")
        # And pairing still completed -- the heart of the bake setup.
        by_name = {m.name: m for m in imported}
        self.assertIs(by_name["body_high"].parent, high_p)
        self.assertIs(by_name["body_low"].parent, low_p)


if __name__ == "__main__":
    unittest.main()
