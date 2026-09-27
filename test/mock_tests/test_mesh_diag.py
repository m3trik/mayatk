# coding=utf-8
"""Mock tests for the non-manifold trio on ``MeshDiagnostics``.

The live bowtie-mesh behavior is pinned by tentacle's ``test_uv.py``
(``TestTb004NonManifoldStrategy``, which drives these through the Unfold slot);
these pin the routing: vertices before UVs, the repair summary, and that a
failing repair step is survived.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

_REAL_MAYA_LOADED = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

from mayatk.core_utils.diagnostics import mesh_diag  # noqa: E402
from mayatk.core_utils.diagnostics.mesh_diag import MeshDiagnostics  # noqa: E402
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics  # noqa: E402


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest("Mock-based suite -- skipped when real Maya is loaded.")


class _DiagCase(unittest.TestCase):
    def patch(self, target, attr, value):
        patcher = patch.object(target, attr, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value


class TestFindNonManifoldVertices(_DiagCase):
    def test_maps_only_the_meshes_that_have_any(self):
        self.patch(
            mesh_diag.cmds,
            "ls",
            MagicMock(side_effect=lambda x, **kw: ["s1", "s2"] if kw.get("dag") else x),
        )
        self.patch(
            mesh_diag.cmds,
            "polyInfo",
            MagicMock(
                side_effect=lambda s, **_kw: ["s1.vtx[3]"] if s == "s1" else None
            ),
        )
        self.assertEqual(
            MeshDiagnostics.find_non_manifold_vertices("mesh"), {"s1": ["s1.vtx[3]"]}
        )

    def test_nothing_given_is_nothing_found(self):
        self.assertEqual(MeshDiagnostics.find_non_manifold_vertices([]), {})


class TestSelectNonManifold(_DiagCase):
    def setUp(self):
        self.select = self.patch(mesh_diag.cmds, "select", MagicMock())
        self.select_type = self.patch(mesh_diag.cmds, "selectType", MagicMock())
        self.patch(mesh_diag.cmds, "selectMode", MagicMock())

    def _found(self, verts, uvs):
        self.patch(
            MeshDiagnostics, "find_non_manifold_vertices", MagicMock(return_value=verts)
        )
        self.patch(UvDiagnostics, "find_non_manifold_uvs", MagicMock(return_value=uvs))

    def test_vertices_win_over_uvs(self):
        self._found({"s": ["s.vtx[1]"]}, {"s": ["s.map[2]"]})
        self.assertEqual(
            MeshDiagnostics.select_non_manifold(["m"]), ("vertices", ["s.vtx[1]"])
        )
        self.select.assert_called_once_with(["s.vtx[1]"], replace=True)
        self.select_type.assert_called_once_with(vertex=True)

    def test_uvs_are_the_fallback(self):
        self._found({}, {"s": ["s.map[2]", "s.map[5]"]})
        self.assertEqual(
            MeshDiagnostics.select_non_manifold(["m"]),
            ("uvs", ["s.map[2]", "s.map[5]"]),
        )
        self.select_type.assert_called_once_with(polymeshUV=True)

    def test_a_clean_mesh_leaves_the_selection_alone(self):
        self._found({}, {})
        self.assertEqual(MeshDiagnostics.select_non_manifold(["m"]), (None, []))
        self.select.assert_not_called()


class TestRepairNonManifold(_DiagCase):
    def setUp(self):
        # before, after: two verts + one UV found, then only the UV remains.
        self.patch(
            MeshDiagnostics,
            "find_non_manifold_vertices",
            MagicMock(side_effect=[{"s": ["v1", "v2"]}, {}]),
        )
        self.patch(
            UvDiagnostics,
            "find_non_manifold_uvs",
            MagicMock(side_effect=[{"s": ["u1"]}, {"s": ["u1"]}]),
        )

    def test_reports_what_was_fixed_and_what_remains(self):
        clean = self.patch(MeshDiagnostics, "clean_geometry", MagicMock())
        uv_fix = self.patch(UvDiagnostics, "repair_non_manifold_uvs", MagicMock())
        summary = MeshDiagnostics.repair_non_manifold(["m"], quiet=True)
        self.assertEqual(summary, {"total": 3, "fixed": 2, "remaining": 1})
        clean.assert_called_once_with(["m"], repair=True, nonmanifold=True)
        uv_fix.assert_called_once_with(["m"])

    def test_a_failing_step_is_survived(self):
        self.patch(
            MeshDiagnostics,
            "clean_geometry",
            MagicMock(side_effect=RuntimeError("polyCleanup failed")),
        )
        self.patch(
            UvDiagnostics,
            "repair_non_manifold_uvs",
            MagicMock(side_effect=ValueError("no faces")),
        )
        summary = MeshDiagnostics.repair_non_manifold(["m"], quiet=True)
        self.assertEqual(summary["remaining"], 1)


if __name__ == "__main__":
    unittest.main()
