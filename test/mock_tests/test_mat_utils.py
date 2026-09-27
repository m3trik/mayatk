# coding=utf-8
"""Mock tests for ``MatUtils`` -- the plumbing a live Maya run cannot isolate.

The live behavior (Maya's own ``uvTilingMode`` labels on real ``file`` nodes)
is pinned in ``test/test_mat_utils.py``; these pin which mode
:meth:`MatUtils.apply_uv_tiling` picks for each tile spelling, placeholders
included, against real tile files on disk and a recorded ``cmds``.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pythontk as ptk

_REAL_MAYA_LOADED = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

from mayatk.mat_utils import _duplicates, _texture_paths  # noqa: E402
from mayatk.mat_utils._mat_utils import MatUtils  # noqa: E402


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest("Mock-based suite -- skipped when real Maya is loaded.")


class _FileNodes:
    """The three ``cmds`` calls ``apply_uv_tiling`` makes, over plain dicts."""

    LABELS = "Off:0-based (ZBrush):1-based (Mudbox):UDIM (Mari):Explicit (Tiles)"

    def __init__(self, paths):
        self.paths = dict(paths)  # node -> fileTextureName
        self.modes = {node: 0 for node in self.paths}  # node -> uvTilingMode

    def attributeQuery(self, attr, node=None, exists=False, listEnum=False):  # noqa: N802
        if exists:
            return node in self.paths
        if listEnum:
            return [self.LABELS]
        raise AssertionError(f"unexpected attributeQuery({attr!r})")

    def getAttr(self, plug):  # noqa: N802
        node, attr = plug.split(".", 1)
        return self.modes[node] if attr == "uvTilingMode" else self.paths[node]

    def setAttr(self, plug, value):  # noqa: N802
        self.modes[plug.split(".", 1)[0]] = value

    def label(self, node):
        return self.LABELS.split(":")[self.modes[node]]


class TestApplyUvTiling(unittest.TestCase):
    """The mode each tile spelling names -- routed through ``ptk.TiledPath``."""

    def setUp(self):
        store = ptk.TempArtifacts("mtk_uv_tiling_mock", policy="scoped")
        self.addCleanup(store.cleanup, True)
        self.root = store.dir_path()

    def _run(self, cases):
        """``{stored name: (tile files on disk, expected mode)}`` -> assert modes."""
        paths = {}
        for index, (stored, (tiles, _mode)) in enumerate(cases.items()):
            for tile in tiles:
                with open(os.path.join(self.root, tile), "wb") as f:
                    f.write(b"tile")
            paths[f"file{index}"] = os.path.join(self.root, stored).replace("\\", "/")
        nodes = _FileNodes(paths)
        with patch.object(_texture_paths, "cmds", nodes):
            changed = MatUtils.apply_uv_tiling(list(paths))
        by_node = dict(zip(paths, cases))
        for node, stored in by_node.items():
            self.assertEqual(nodes.label(node), cases[stored][1], stored)
        self.assertEqual(
            sorted(changed),
            sorted(n for n, s in by_node.items() if cases[s][1] != "Off"),
        )

    def test_a_concrete_tile_names_its_scheme(self):
        self._run(
            {
                "rock_BaseColor.1001.png": (
                    ["rock_BaseColor.1001.png", "rock_BaseColor.1002.png"],
                    "UDIM (Mari)",
                ),
                "mud_BaseColor.u1_v1.png": (
                    ["mud_BaseColor.u1_v1.png", "mud_BaseColor.u2_v1.png"],
                    "1-based (Mudbox)",
                ),
                "zb_BaseColor.u0_v0.png": (
                    ["zb_BaseColor.u0_v0.png", "zb_BaseColor.u1_v0.png"],
                    "0-based (ZBrush)",
                ),
                "wall_BaseColor.1024.png": (["wall_BaseColor.1024.png"], "Off"),
                "plain_BaseColor.png": (["plain_BaseColor.png"], "Off"),
            }
        )

    def test_a_placeholder_tiles_in_its_own_vocabulary(self):
        """``<u>_<v>`` is a UV-tile placeholder, not a UDIM one.

        Its set is ``u#_v#`` files (``MapFactory.get_tile_paths`` and the
        existence probe both read it through ``ptk.TiledPath``, whose stand-in
        for it is ``u1_v1``). Before this change the placeholder was not read
        and the node was left untiled (``Off``); now it tiles 1-based, like
        ``<UVTILE>``.
        """
        self._run(
            {
                "pair_BaseColor.<u>_<v>.png": (
                    ["pair_BaseColor.u1_v1.png", "pair_BaseColor.u2_v1.png"],
                    "1-based (Mudbox)",
                ),
                "mb_BaseColor.<UVTILE>.png": (
                    ["mb_BaseColor.u1_v1.png", "mb_BaseColor.u2_v1.png"],
                    "1-based (Mudbox)",
                ),
                "mari_BaseColor.<UDIM>.png": (
                    ["mari_BaseColor.1001.png", "mari_BaseColor.1002.png"],
                    "UDIM (Mari)",
                ),
            }
        )


def _case_rule(fold):
    """``os.path.normcase`` for a disk that folds case (Windows) or keeps it
    (Linux), on whichever host runs the test: separators normalize as the
    host's own ``normcase`` does; only the case handling differs."""

    def normcase(path):
        path = os.fspath(path).replace("/", os.sep)
        return path.lower() if fold else path

    return normcase


class _Disk:
    """``MatUtils.texture_tiles`` over a set of files, telling paths apart by
    the simulated disk's case rule."""

    def __init__(self, files, normcase):
        self.normcase = normcase
        self.files = {normcase(os.path.normpath(f)): f for f in files}

    def texture_tiles(self, path):
        key = self.normcase(os.path.normpath(path))
        return [self.files[key]] if key in self.files else []


class _MaterialGraph:
    """The ``cmds`` calls the duplicate-texture finder makes, over plain dicts:
    one ``file`` node per material slot, wired straight into it."""

    def __init__(self, materials):
        self.materials = materials  # material -> {slot attr: texture path}
        self.files = {  # file node -> (material, slot attr, texture path)
            f"{mat}_{attr}_file": (mat, attr, path)
            for mat, slots in materials.items()
            for attr, path in slots.items()
        }

    def ls(self, nodes, mat=False, type=None):
        if mat:
            return [n for n in nodes if n in self.materials]
        if type == "file":
            return [n for n in nodes if n in self.files]
        raise AssertionError(f"unexpected ls(mat={mat}, type={type!r})")

    def nodeType(self, node):  # noqa: N802
        return "standardSurface"

    def listHistory(self, node, pruneDagObjects=False):  # noqa: N802,N803
        return [node] + [f for f, (mat, _a, _p) in self.files.items() if mat == node]

    def objExists(self, plug):  # noqa: N802
        return plug.split(".", 1)[0] in self.files

    def getAttr(self, plug):  # noqa: N802
        return self.files[plug.split(".", 1)[0]][2]

    def listConnections(self, node, source=True, destination=True, plugs=False):  # noqa: N802
        mat, attr, _path = self.files[node]
        return [f"{mat}.{attr}"]


class TestPathIdentityFollowsTheDiskCaseRule(unittest.TestCase):
    """Two paths name one file where ``os.path.normcase`` says so -- never by a
    blanket ``.lower()``: on Linux ``Rock.png`` and ``rock.png`` are two files,
    and folding case there binds a texture to the wrong one. Each case runs
    under both rules, simulated on whichever host runs the suite."""

    ROOT = os.path.abspath(os.sep).replace("\\", "/")

    def _relativize(self, fold, path, workspace, sourceimages, files):
        normcase = _case_rule(fold)
        disk = _Disk(files, normcase)
        with (
            patch.object(os.path, "normcase", normcase),
            patch.object(MatUtils, "texture_tiles", staticmethod(disk.texture_tiles)),
        ):
            return MatUtils.to_project_relative(path, workspace, sourceimages)

    def test_the_round_trip_tells_a_rule_spelled_in_another_case_apart(self):
        """The texture is not on disk yet; a file of its relative name sits
        only under a sourceImages rule that differs from the root by case
        alone. Where case is kept that is ANOTHER file the relative form would
        bind to, so the path stays absolute; where it folds, it is the same
        file and the root-relative form stands."""
        ws, rule = self.ROOT + "proj", self.ROOT + "Proj"
        path = ws + "/tex/Foo.png"
        files = [rule + "/tex/Foo.png"]
        self.assertEqual(self._relativize(False, path, ws, rule, files), path)
        self.assertEqual(self._relativize(True, path, ws, rule, files), "tex/Foo.png")

    def test_the_shadow_guard_tells_differently_cased_folders_apart(self):
        """A texture under an out-of-root rule, with a same-named file at the
        project root in a folder that differs by case alone: where case is
        kept that root file is a DIFFERENT file that would shadow the
        rule-relative form, so the path stays absolute."""
        ws, rule = self.ROOT + "proj", self.ROOT + "Proj"
        path = rule + "/Foo.png"
        self.assertEqual(
            self._relativize(False, path, ws, rule, [path, ws + "/Foo.png"]), path
        )
        # Control: with nothing at the root, the rule-relative form stands.
        self.assertEqual(self._relativize(False, path, ws, rule, [path]), "Foo.png")

    def test_strict_duplicates_fold_case_only_where_the_disk_does(self):
        graph = _MaterialGraph(
            {
                "rockA_MAT": {"baseColor": self.ROOT + "tex/Rock.png"},
                "rockB_MAT": {"baseColor": self.ROOT + "tex/rock.png"},
            }
        )

        def find(fold, strict=True):
            with (
                patch.object(os.path, "normcase", _case_rule(fold)),
                patch.object(_duplicates, "cmds", graph),
            ):
                return MatUtils.find_materials_with_duplicate_textures(
                    list(graph.materials), strict=strict, verify=False
                )

        self.assertEqual(find(fold=False), {}, "two files where case is kept")
        self.assertEqual(find(fold=True), {"rockA_MAT": ["rockB_MAT"]})
        # The loose mode keys on the basename stem, case-folded on every OS.
        self.assertEqual(find(fold=False, strict=False), {"rockA_MAT": ["rockB_MAT"]})


if __name__ == "__main__":
    unittest.main()
