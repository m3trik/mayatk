# coding=utf-8
"""Mock tests for ``mayatk.uv_utils._uv_pack``'s native u3dLayout engine.

The live behavior (placement, grid dealing, pin weights) is pinned in
``test/test_uv_utils.py::TestPackUvsU3d``; these pin the plumbing a live run
cannot see at a glance: which ``u3dLayout`` flags a request emits, the UDIM-row
clamp, and the per-mesh failure isolation.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

_REAL_MAYA_LOADED = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

from mayatk.uv_utils import _uv_pack  # noqa: E402
from mayatk.uv_utils._uv_pack import _U3dPackInternal  # noqa: E402
from mayatk.uv_utils._uv_utils import UvUtils  # noqa: E402


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest("Mock-based suite -- skipped when real Maya is loaded.")


class _U3dCase(unittest.TestCase):
    """Stubs the scene: two meshes, u3dLayout recorded (or made to fail)."""

    meshes = ["a", "b"]

    def setUp(self):
        self.calls = []
        self.fail_on = set()  # mesh names whose presence in a call raises

        def layout(uvs, **kwargs):
            self.calls.append((list(uvs), kwargs))
            names = {str(u).split(".", 1)[0] for u in uvs}
            if names & self.fail_on:
                raise RuntimeError("u3dLayout: overlapping UVs in shell")

        uvs = [f"{m}.map[0:3]" for m in self.meshes]
        for target, attr, value in (
            (_U3dPackInternal, "_resolve", MagicMock(return_value=(self.meshes, uvs))),
            (_U3dPackInternal, "distribute_to_grid", MagicMock()),
            (_uv_pack.cmds, "u3dLayout", MagicMock(side_effect=layout)),
            (_uv_pack.cmds, "loadPlugin", MagicMock()),
            (
                _uv_pack.cmds,
                "polyListComponentConversion",
                MagicMock(side_effect=lambda mesh, **_kw: [f"{mesh}.map[0:3]"]),
            ),
        ):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pack(self, **kwargs):
        return UvUtils.pack_uvs(self.meshes, engine="u3d", **kwargs)


class TestU3dFlags(_U3dCase):
    def test_default_request_fills_the_first_tile(self):
        result = self.pack(map_size=1024)
        ((uvs, kwargs),) = self.calls
        self.assertEqual(uvs, ["a.map[0:3]", "b.map[0:3]"])
        spacing = UvUtils.calculate_uv_padding(1024, normalize=True)
        self.assertEqual(
            kwargs,
            dict(
                resolution=1024,
                shellSpacing=spacing,
                tileMargin=spacing / 2,
                preScaleMode=1,
                preRotateMode=0,
                packBox=[0, 1.0, 0, 1.0],
                multiObject=True,
            ),
        )
        self.assertEqual(result.engine, "u3d")
        self.assertEqual(result.succeeded, ["a", "b"])
        self.assertEqual(result.targets, ["a", "b"])
        self.assertEqual(result.tiles, (1, 1))
        _U3dPackInternal.distribute_to_grid.assert_not_called()

    def test_udim_and_coverage_anchor_the_pack_box(self):
        self.pack(udim=1012, coverage=(0.5, 1.0), preserve_3d=False)
        kwargs = self.calls[0][1]
        self.assertEqual(kwargs["packBox"], [1, 1.5, 1, 2.0])
        self.assertEqual(kwargs["preScaleMode"], 0)

    def test_raster_caps_at_4096_and_padding_is_in_pixels(self):
        self.pack(map_size=16384, padding=32)
        kwargs = self.calls[0][1]
        self.assertEqual(kwargs["resolution"], 4096)
        self.assertEqual(kwargs["shellSpacing"], 32 / 16384)

    def test_rotation_search_is_emitted_only_when_max_exceeds_min(self):
        self.pack(rotate_step=90, rotate_min=0, rotate_max=0)
        self.assertNotIn("rotateStep", self.calls[-1][1])
        self.pack(rotate_step=45, rotate_min=0, rotate_max=180)
        kwargs = self.calls[-1][1]
        self.assertEqual(
            (kwargs["rotateStep"], kwargs["rotateMin"], kwargs["rotateMax"]),
            (45, 0, 180),
        )

    def test_overrides_are_emitted_only_off_their_defaults(self):
        self.pack(mutations=1, scale_mode=2)
        kwargs = self.calls[-1][1]
        self.assertNotIn("mutations", kwargs)
        self.assertNotIn("layoutScaleMode", kwargs)
        self.pack(mutations=4, scale_mode=3, pre_rotate=2)
        kwargs = self.calls[-1][1]
        self.assertEqual(kwargs["mutations"], 4)
        self.assertEqual(kwargs["layoutScaleMode"], 3)
        self.assertEqual(kwargs["preRotateMode"], 2)


class TestU3dGrid(_U3dCase):
    def test_a_grid_is_dealt_here_then_packed_per_tile(self):
        result = self.pack(tiles=(3, 2), coverage=(0.5, 0.5))
        kwargs = self.calls[0][1]
        self.assertEqual((kwargs["tileU"], kwargs["tileV"]), (3, 2))
        self.assertEqual(kwargs["tileAssignMode"], 1)
        # Coverage is the grid's cell template: forced Full.
        self.assertEqual(kwargs["packBox"], [0, 1.0, 0, 1.0])
        _U3dPackInternal.distribute_to_grid.assert_called_once_with(
            UvUtils, ["a.map[0:3]", "b.map[0:3]"], 0, 0, 3, 2
        )
        self.assertEqual(result.tiles, (3, 2))

    def test_scale_mode_off_keeps_the_grid_but_deals_nothing(self):
        self.pack(tiles=(2, 2), scale_mode=1)
        kwargs = self.calls[0][1]
        self.assertNotIn("tileAssignMode", kwargs)
        _U3dPackInternal.distribute_to_grid.assert_not_called()

    def test_the_grid_is_clamped_to_the_udim_row(self):
        result = self.pack(udim=1009, tiles=(4, 1))  # u=8: two columns left
        self.assertEqual(result.tiles, (2, 1))
        self.assertEqual(self.calls[0][1]["tileU"], 2)


class TestU3dFailureIsolation(_U3dCase):
    def test_a_failed_batch_probes_each_mesh_and_repacks_the_survivors(self):
        self.fail_on = {"a"}
        result = self.pack()
        packed = [uvs for uvs, _ in self.calls]
        self.assertEqual(
            packed,
            [
                ["a.map[0:3]", "b.map[0:3]"],  # the batch
                ["a.map[0:3]"],  # probe: fails
                ["b.map[0:3]"],  # probe: packs
                ["b.map[0:3]"],  # survivors, together
            ],
        )
        self.assertEqual(result.succeeded, ["b"])
        self.assertEqual(result.failed, [("a", "overlapping UVs")])
        self.assertEqual(result.targets, ["b"])

    def test_a_lone_mesh_reports_without_a_probe_pass(self):
        self.meshes = ["a"]
        self.setUp()
        self.fail_on = {"a"}
        result = self.pack()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.failed, [("a", "overlapping UVs")])
        self.assertFalse(result)

    def test_no_uvs_raises_before_the_scene_is_touched(self):
        _U3dPackInternal._resolve.return_value = (["a"], [])
        with self.assertRaises(ValueError):
            self.pack()
        self.assertEqual(self.calls, [])


class TestEngineDispatch(unittest.TestCase):
    def test_unknown_engine_is_refused(self):
        with self.assertRaises(ValueError):
            UvUtils.pack_uvs(["a"], engine="bogus")

    def test_classify_unfold3d_error(self):
        cases = {
            "Mesh has non-manifold vertices. Clean up the mesh first.": (
                "non-manifold vertices"
            ),
            "u3dLayout: overlapping UVs detected in the shell": "overlapping UVs",
            "Some unexpected failure\nwith trailing detail lines": (
                "Some unexpected failure"
            ),
        }
        for message, reason in cases.items():
            with self.subTest(message=message):
                self.assertEqual(
                    UvUtils.classify_unfold3d_error(RuntimeError(message)), reason
                )
        self.assertEqual(
            len(UvUtils.classify_unfold3d_error(RuntimeError("x" * 90))), 50
        )


if __name__ == "__main__":
    unittest.main()
