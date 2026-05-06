# !/usr/bin/python
# coding=utf-8
"""
Workflow / integration tests for mayatk.mat_utils.mat_updater

Covers gaps not exercised by test_material_updater.py:
- aiStandardSurface and multi-material flows
- texture_cache reuse across materials
- IGNORED_ENV_SETS filtering
- common-root subset merge across processed sets
- run_factory=False (skip factory) path
- empty / unsupported materials early return
- prepare_maps error paths (batch and per-material)
- transfer_mode = copy / move / none
- globally_moved_files dedup across materials sharing textures
- MAYA_LOCATION system-file filter
- move_file failure -> files_to_keep fallback
- copy_file shutil fallback
- EnvUtils.get_env_info raising
- disconnect_associated_attributes (standardSurface, StingrayPBS, dry-run, indirect driver)
- update_network filter_redundant_maps + connection-error swallow
- end-to-end integration with real MapFactory + on-disk PNGs
"""
import os
import shutil
import unittest
from unittest.mock import MagicMock, patch

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase
from mayatk.mat_utils.mat_updater import MatUpdater


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(path: str, size: int = 4) -> str:
    """Write a tiny valid PNG so PIL/MapFactory can open it."""
    try:
        from PIL import Image

        Image.new("RGB", (size, size), (128, 128, 128)).save(path)
    except Exception:
        # Fallback: a minimal 1x1 PNG byte sequence
        with open(path, "wb") as f:
            f.write(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90"
                b"wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00"
                b"\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
            )
    return path


def _short(node) -> str:
    return str(node).split("|")[-1].split(":")[-1]


# ---------------------------------------------------------------------------
# Core workflow gaps
# ---------------------------------------------------------------------------


class TestRunFactorySkip(MayaTkTestCase):
    """run_factory=False branch: no MapFactory invocation, files passed through."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_runfactory")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "color.png")
        with open(self.tex, "w") as f:
            f.write("dummy")
        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="rf_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="rf_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    def test_all_flags_false_skips_factory(self, mock_prepare):
        """All processing flags False: prepare_maps must NOT be called."""
        config = {
            "convert": False,
            "optimize": False,
            "convert_format": False,
            "convert_type": False,
            "resize": False,
            "pack": False,
        }
        results = MatUpdater.update_materials(
            materials=[self.mat], config=config, verbose=False
        )
        mock_prepare.assert_not_called()
        # The original file should still be in the result
        key = _short(self.mat)
        self.assertIn(key, results)
        self.assertIn(self.tex, [os.path.normpath(p) for p in results[key]["textures"]])


class TestEmptyMaterials(MayaTkTestCase):
    """Empty / unsupported material list returns an empty dict."""

    def test_no_supported_materials(self):
        # Scene has nothing supported -> early return
        results = MatUpdater.update_materials(materials=[], verbose=False)
        self.assertEqual(results, {})

    def test_materials_none_no_supported_in_scene(self):
        # Only an unsupported lambert exists
        cmds.shadingNode("lambert", asShader=True, name="lam_only")
        results = MatUpdater.update_materials(materials=None, verbose=False)
        self.assertEqual(results, {})


class TestPrepareMapsErrorPaths(MayaTkTestCase):
    """Errors from MapFactory.prepare_maps must not crash update_materials."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_err")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "BaseColor.png")
        with open(self.tex, "w") as f:
            f.write("dummy")
        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="err_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="err_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps", side_effect=RuntimeError("boom"))
    def test_batch_and_per_material_error_swallowed(self, _mock):
        # Should not raise; results should still be a dict (empty body is fine)
        results = MatUpdater.update_materials(materials=[self.mat], verbose=False)
        self.assertIsInstance(results, dict)


class TestEnvInfoRaises(MayaTkTestCase):
    """EnvUtils.get_env_info raising during relative path resolve must not crash."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_envraise")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "tex.png")
        with open(self.tex, "w") as f:
            f.write("dummy")
        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="env_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="env_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch(
        "mayatk.mat_utils.mat_updater.EnvUtils.get_env_info",
        side_effect=RuntimeError("no project"),
    )
    def test_env_info_raise_does_not_crash(self, _env, mock_prepare):
        mock_prepare.return_value = [self.tex]
        # Relative path triggers the env_info lookup, which raises
        results = MatUpdater.update_materials(
            materials=[self.mat],
            config={"move_to_folder": "RelativeOnly", "dry_run": True},
            verbose=False,
        )
        self.assertIsInstance(results, dict)


# ---------------------------------------------------------------------------
# Texture cache & multi-set logic
# ---------------------------------------------------------------------------


class TestTextureCacheReuse(MayaTkTestCase):
    """Two materials with the same textures should share the cache after first call."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_cache")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "shared_BaseColor.png")
        with open(self.tex, "w") as f:
            f.write("dummy")

        self.mat_a = cmds.shadingNode("standardSurface", asShader=True, name="cache_a")
        self.mat_b = cmds.shadingNode("standardSurface", asShader=True, name="cache_b")
        for m in (self.mat_a, self.mat_b):
            fn = cmds.shadingNode(
                "file", asTexture=True, name=f"file_{_short(m)}"
            )
            cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{m}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.group_textures_by_set")
    @patch("pythontk.MapFactory.prepare_maps")
    def test_cache_reused_when_set_unknown(self, mock_prepare, mock_group):
        """If batch lookup misses (no matching set), per-material call caches.

        Second material with same files should hit cache, not re-call prepare_maps.
        """
        # Batch call returns dict keyed by NAME_A; local-set lookup returns NAME_B
        # so batch_success stays False and the per-material reprocess path runs.
        # On the second material with the same files, the cache must short-circuit.
        mock_group.return_value = {"local_name_b": [self.tex]}
        mock_prepare.side_effect = [
            {"batch_name_a": [self.tex]},  # batch call (keys won't match local set)
            [self.tex],  # first per-material reprocess
            # if cache fails, a 3rd call would happen and StopIteration would raise
        ]
        MatUpdater.update_materials(
            materials=[self.mat_a, self.mat_b], config={"convert": True}, verbose=False
        )
        # 1 batch + 1 per-material reprocess for first material; second hits cache
        self.assertEqual(mock_prepare.call_count, 2)


class TestIgnoredEnvSets(MayaTkTestCase):
    """Environment cube/LUT sets should be filtered out before set-count logic."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_envset")
        os.makedirs(self.tmp, exist_ok=True)
        self.color = os.path.join(self.tmp, "asset_BaseColor.png")
        self.ibl = os.path.join(self.tmp, "ibl_brdf_lut.png")
        for p in (self.color, self.ibl):
            with open(p, "w") as f:
                f.write("dummy")

        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="env_mat")
        # Both files connected as file nodes
        for path, attr in ((self.color, "baseColor"), (self.ibl, "specularColor")):
            fn = cmds.shadingNode(
                "file", asTexture=True, name=f"f_{os.path.basename(path)}"
            )
            cmds.setAttr(f"{fn}.fileTextureName", path, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.{attr}")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.group_textures_by_set")
    @patch("pythontk.MapFactory.prepare_maps")
    def test_env_set_filtered_from_local_sets(self, mock_prepare, mock_group):
        """ibl_brdf_lut should be filtered, leaving 1 set so batch lookup succeeds."""
        # Batch processing returns processed set keyed by base name "asset"
        mock_prepare.return_value = {"asset": [self.color]}
        # Group returns 2 sets: "asset" and "ibl_brdf_lut"
        mock_group.return_value = {
            "asset": [self.color],
            "ibl_brdf_lut": [self.ibl],
        }

        results = MatUpdater.update_materials(
            materials=[self.mat], config={"convert": True}, verbose=False
        )
        # Should only call prepare_maps once (batch); env-set filtered means
        # local_sets has length 1, batch_success=True, no per-material reprocess.
        self.assertEqual(mock_prepare.call_count, 1)
        self.assertIn(_short(self.mat), results)


class TestSubsetRootMerge(MayaTkTestCase):
    """When all set names share a common root, merge into root set."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_subset")
        os.makedirs(self.tmp, exist_ok=True)
        # Two files where set names will be e.g. "asset" and "asset_curvature"
        self.f1 = os.path.join(self.tmp, "asset_BaseColor.png")
        self.f2 = os.path.join(self.tmp, "asset_curvature_Normal.png")
        for p in (self.f1, self.f2):
            with open(p, "w") as f:
                f.write("dummy")

        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="sub_mat")
        for p, attr in ((self.f1, "baseColor"), (self.f2, "specularColor")):
            fn = cmds.shadingNode("file", asTexture=True, name=f"f_{os.path.basename(p)}")
            cmds.setAttr(f"{fn}.fileTextureName", p, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.{attr}")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.group_textures_by_set")
    @patch("pythontk.MapFactory.prepare_maps")
    def test_common_root_merge(self, mock_prepare, mock_group):
        # processed_sets keyed by root only
        mock_prepare.return_value = {"asset": [self.f1, self.f2]}
        # group returns two non-env sets where the longer starts with the shorter
        mock_group.return_value = {
            "asset": [self.f1],
            "asset_curvature": [self.f2],
        }
        results = MatUpdater.update_materials(
            materials=[self.mat], config={"convert": True}, verbose=False
        )
        # Should batch successfully via subset merge -> only 1 prepare_maps call
        self.assertEqual(mock_prepare.call_count, 1)
        self.assertIn(_short(self.mat), results)


# ---------------------------------------------------------------------------
# Transfer-mode logic
# ---------------------------------------------------------------------------


class TestTransferModes(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.src_dir = os.path.join(os.environ["TEMP"], "mat_upd_xfer_src")
        self.out_dir = os.path.join(os.environ["TEMP"], "mat_upd_xfer_out")
        for d in (self.src_dir, self.out_dir):
            os.makedirs(d, exist_ok=True)
        self.src = os.path.join(self.src_dir, "color.png")
        self.gen = os.path.join(self.src_dir, "color_GENERATED.png")
        for p in (self.src, self.gen):
            with open(p, "w") as f:
                f.write("dummy")

        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="xfer_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="xfer_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.src, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.copy_file")
    @patch("pythontk.FileUtils.move_file")
    def test_copy_mode_splits_source_and_generated(
        self, mock_move, mock_copy, mock_prepare
    ):
        """transfer_mode='copy': source -> copy_file; generated -> move_file."""
        # Returned files include the source AND a new generated file
        mock_prepare.return_value = [self.src, self.gen]
        mock_move.return_value = [os.path.join(self.out_dir, "color_GENERATED.png")]
        mock_copy.return_value = os.path.join(self.out_dir, "color.png")

        MatUpdater.update_materials(
            materials=[self.mat],
            config={"move_to_folder": self.out_dir, "transfer_mode": "copy"},
            verbose=False,
        )

        # Source file should have been COPIED, not moved
        self.assertTrue(mock_copy.called, "Source file should be copied in copy mode")
        copy_args = [c.args[0] for c in mock_copy.call_args_list]
        self.assertIn(self.src, copy_args)

        # Generated file should have been MOVED
        self.assertTrue(mock_move.called)
        moved_files = mock_move.call_args.args[0]
        self.assertIn(self.gen, moved_files)
        self.assertNotIn(self.src, moved_files)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file")
    def test_move_mode_moves_everything(self, mock_move, mock_prepare):
        mock_prepare.return_value = [self.src, self.gen]
        mock_move.return_value = [
            os.path.join(self.out_dir, "color.png"),
            os.path.join(self.out_dir, "color_GENERATED.png"),
        ]

        MatUpdater.update_materials(
            materials=[self.mat],
            config={"move_to_folder": self.out_dir, "transfer_mode": "move"},
            verbose=False,
        )

        moved_files = mock_move.call_args.args[0]
        self.assertIn(self.src, moved_files)
        self.assertIn(self.gen, moved_files)


class TestGloballyMovedDedup(MayaTkTestCase):
    """Two materials sharing a texture: move_file invoked once for that texture."""

    def setUp(self):
        super().setUp()
        self.src_dir = os.path.join(os.environ["TEMP"], "mat_upd_dedup_src")
        self.out_dir = os.path.join(os.environ["TEMP"], "mat_upd_dedup_out")
        for d in (self.src_dir, self.out_dir):
            os.makedirs(d, exist_ok=True)
        self.shared = os.path.join(self.src_dir, "shared.png")
        with open(self.shared, "w") as f:
            f.write("dummy")

        self.mat_a = cmds.shadingNode("standardSurface", asShader=True, name="da")
        self.mat_b = cmds.shadingNode("standardSurface", asShader=True, name="db")
        for m in (self.mat_a, self.mat_b):
            fn = cmds.shadingNode("file", asTexture=True, name=f"f_{_short(m)}")
            cmds.setAttr(f"{fn}.fileTextureName", self.shared, type="string")
            cmds.connectAttr(f"{fn}.outColor", f"{m}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file")
    def test_shared_texture_moved_once(self, mock_move, mock_prepare):
        mock_prepare.return_value = [self.shared]
        mock_move.return_value = [os.path.join(self.out_dir, "shared.png")]

        MatUpdater.update_materials(
            materials=[self.mat_a, self.mat_b],
            config={"move_to_folder": self.out_dir, "transfer_mode": "move"},
            verbose=False,
        )

        # Aggregate every file that move_file was asked to move
        all_moved = []
        for c in mock_move.call_args_list:
            arg = c.args[0]
            if isinstance(arg, list):
                all_moved.extend(arg)
            else:
                all_moved.append(arg)
        self.assertEqual(
            all_moved.count(self.shared),
            1,
            f"Shared texture should be moved exactly once, got {all_moved}",
        )


class TestMayaLocationFilter(MayaTkTestCase):
    """Files under MAYA_LOCATION must be excluded from move."""

    def setUp(self):
        super().setUp()
        self.src_dir = os.path.join(os.environ["TEMP"], "mat_upd_mloc_src")
        self.out_dir = os.path.join(os.environ["TEMP"], "mat_upd_mloc_out")
        for d in (self.src_dir, self.out_dir):
            os.makedirs(d, exist_ok=True)
        self.user_tex = os.path.join(self.src_dir, "user.png")
        with open(self.user_tex, "w") as f:
            f.write("dummy")

        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="mloc_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="mloc_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.user_tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file")
    def test_maya_location_file_filtered(self, mock_move, mock_prepare):
        """A file under $MAYA_LOCATION must NOT appear in the move list."""
        maya_loc = os.environ.get("MAYA_LOCATION", "")
        if not maya_loc:
            self.skipTest("MAYA_LOCATION is not set")
        sys_tex = os.path.join(maya_loc, "presets", "fake_sys_tex.png")
        # Don't actually create it under Maya — just hand the path to prepare_maps.
        mock_prepare.return_value = [self.user_tex, sys_tex]
        mock_move.return_value = [os.path.join(self.out_dir, "user.png")]

        MatUpdater.update_materials(
            materials=[self.mat],
            config={"move_to_folder": self.out_dir, "transfer_mode": "move"},
            verbose=False,
        )

        # The system file should never be passed to move_file
        if mock_move.called:
            moved = mock_move.call_args.args[0]
            self.assertNotIn(sys_tex, moved)


class TestMoveFailureFallback(MayaTkTestCase):
    """If move_file raises, the workflow keeps going (no propagation)."""

    def setUp(self):
        super().setUp()
        self.src_dir = os.path.join(os.environ["TEMP"], "mat_upd_movefail")
        self.out_dir = os.path.join(os.environ["TEMP"], "mat_upd_movefail_out")
        for d in (self.src_dir, self.out_dir):
            os.makedirs(d, exist_ok=True)
        self.tex = os.path.join(self.src_dir, "tex.png")
        with open(self.tex, "w") as f:
            f.write("dummy")
        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="mf_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="mf_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file", side_effect=OSError("permission denied"))
    def test_move_failure_does_not_raise(self, _mock_move, mock_prepare):
        mock_prepare.return_value = [self.tex]
        results = MatUpdater.update_materials(
            materials=[self.mat],
            config={"move_to_folder": self.out_dir, "transfer_mode": "move"},
            verbose=False,
        )
        self.assertIsInstance(results, dict)
        self.assertIn(_short(self.mat), results)


# ---------------------------------------------------------------------------
# disconnect_associated_attributes
# ---------------------------------------------------------------------------


class TestDisconnectAssociatedAttributes(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_disc")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "tex.png").replace("\\", "/")
        with open(self.tex, "w") as f:
            f.write("dummy")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_skips_disconnect(self):
        mat = cmds.shadingNode("standardSurface", asShader=True, name="dr_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="dr_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.baseColor")
        # Should be a no-op; connection survives
        MatUpdater.disconnect_associated_attributes(
            mat, [self.tex], config={"dry_run": True}
        )
        conns = cmds.listConnections(f"{mat}.baseColor", source=True) or []
        self.assertIn(fn, conns)

    def test_disconnects_standard_surface_basecolor(self):
        mat = cmds.shadingNode("standardSurface", asShader=True, name="ss_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="ss_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.baseColor")

        MatUpdater.disconnect_associated_attributes(mat, [self.tex])
        conns = cmds.listConnections(f"{mat}.baseColor", source=True) or []
        self.assertEqual(conns, [], "baseColor should be disconnected")

    def test_disconnects_stingray_color_map(self):
        try:
            if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                cmds.loadPlugin("shaderFXPlugin")
        except Exception:
            self.skipTest("shaderFXPlugin not available")

        mat = cmds.shadingNode("StingrayPBS", asShader=True, name="sr_mat")
        try:
            cmds.setAttr(f"{mat}.initgraph", True)
        except Exception:
            pass

        if not cmds.attributeQuery("TEX_color_map", node=mat, exists=True):
            self.skipTest("StingrayPBS lacks TEX_color_map on this Maya build")

        fn = cmds.shadingNode("file", asTexture=True, name="sr_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        try:
            cmds.connectAttr(f"{fn}.outColor", f"{mat}.TEX_color_map", force=True)
        except Exception:
            self.skipTest("Could not connect to TEX_color_map directly")

        MatUpdater.disconnect_associated_attributes(mat, [self.tex])
        conns = cmds.listConnections(f"{mat}.TEX_color_map", source=True) or []
        self.assertEqual(conns, [], "TEX_color_map should be disconnected")

    def test_unrelated_file_does_not_disconnect(self):
        mat = cmds.shadingNode("standardSurface", asShader=True, name="unr_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="unr_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.baseColor")

        # Pass a path that does NOT match the file node's path
        unrelated = os.path.join(self.tmp, "OTHER.png").replace("\\", "/")
        MatUpdater.disconnect_associated_attributes(mat, [unrelated])
        conns = cmds.listConnections(f"{mat}.baseColor", source=True) or []
        self.assertIn(fn, conns, "Unrelated path must not trigger disconnection")


# ---------------------------------------------------------------------------
# update_network
# ---------------------------------------------------------------------------


class TestUpdateNetwork(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_un")
        os.makedirs(self.tmp, exist_ok=True)
        self.color = os.path.join(self.tmp, "thing_BaseColor.png")
        with open(self.color, "w") as f:
            f.write("dummy")
        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="un_mat")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("mayatk.mat_utils.mat_updater.GameShader")
    @patch("pythontk.MapFactory.filter_redundant_maps")
    def test_filter_redundant_maps_called(self, mock_filter, _mock_gs):
        MatUpdater.update_network(self.mat, [self.color], {"dry_run": True})
        self.assertTrue(
            mock_filter.called,
            "filter_redundant_maps should be invoked on the inventory",
        )

    @patch("mayatk.mat_utils.mat_updater.GameShader")
    def test_connection_error_swallowed(self, mock_gs_cls):
        instance = mock_gs_cls.return_value
        instance.connect_standard_surface_nodes.side_effect = RuntimeError("nope")
        # Should not raise
        result = MatUpdater.update_network(self.mat, [self.color], {})
        self.assertIsInstance(result, dict)

    def test_dry_run_returns_inventory_no_connections(self):
        result = MatUpdater.update_network(self.mat, [self.color], {"dry_run": True})
        # Inventory should be a dict of map_type -> path
        self.assertIsInstance(result, dict)
        # No real connection should have been made
        conns = cmds.listConnections(f"{self.mat}.baseColor", source=True) or []
        self.assertEqual(conns, [], "Dry run must not create connections")


# ---------------------------------------------------------------------------
# aiStandardSurface support
# ---------------------------------------------------------------------------


class TestAiStandardSurface(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        try:
            if not cmds.pluginInfo("mtoa", query=True, loaded=True):
                cmds.loadPlugin("mtoa")
        except Exception:
            self.skipTest("mtoa (Arnold) not available")

        self.tmp = os.path.join(os.environ["TEMP"], "mat_upd_ai")
        os.makedirs(self.tmp, exist_ok=True)
        self.tex = os.path.join(self.tmp, "ai_BaseColor.png")
        with open(self.tex, "w") as f:
            f.write("dummy")
        try:
            self.mat = cmds.shadingNode(
                "aiStandardSurface", asShader=True, name="ai_mat"
            )
        except Exception:
            self.skipTest("Could not create aiStandardSurface")

        fn = cmds.shadingNode("file", asTexture=True, name="ai_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("pythontk.MapFactory.prepare_maps")
    def test_ai_picked_up_by_materials_none(self, mock_prepare):
        mock_prepare.return_value = [self.tex]
        results = MatUpdater.update_materials(materials=None, verbose=False)
        self.assertIn(_short(self.mat), results)


# ---------------------------------------------------------------------------
# End-to-end integration: real MapFactory, real on-disk PNGs
# ---------------------------------------------------------------------------


class TestEndToEndIntegration(MayaTkTestCase):
    """Run the workflow without mocking MapFactory — catches integration breakage."""

    def setUp(self):
        super().setUp()
        # Real PNGs so MapFactory can do its thing without throwing
        try:
            from PIL import Image  # noqa
        except ImportError:
            self.skipTest("PIL/Pillow required for integration test")

        self.src_dir = os.path.join(os.environ["TEMP"], "mat_upd_e2e_src")
        self.out_dir = os.path.join(os.environ["TEMP"], "mat_upd_e2e_out")
        for d in (self.src_dir, self.out_dir):
            os.makedirs(d, exist_ok=True)

        self.color = _make_png(os.path.join(self.src_dir, "asset_BaseColor.png"))

        self.mat = cmds.shadingNode("standardSurface", asShader=True, name="e2e_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="e2e_file")
        cmds.setAttr(f"{fn}.fileTextureName", self.color, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{self.mat}.baseColor")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.out_dir, ignore_errors=True)

    def test_dry_run_real_factory(self):
        """Dry run with real MapFactory: returns inventory, makes no FS changes."""
        results = MatUpdater.update_materials(
            materials=[self.mat],
            config={"dry_run": True, "convert": False, "optimize": False},
            verbose=False,
        )
        key = _short(self.mat)
        self.assertIn(key, results)
        # Connection inventory exists
        self.assertIsInstance(results[key]["connected"], dict)


if __name__ == "__main__":
    unittest.main()
