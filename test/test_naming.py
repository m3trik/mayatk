# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.edit_utils.naming module
"""
import unittest
from mayatk.edit_utils.naming._naming import Naming
from base_test import MayaTkTestCase
import maya.cmds as cmds


def _uuid(node):
    """Capture a node's UUID so we can find it again after rename."""
    return cmds.ls(node, uuid=True)[0]


def _name(uid):
    """Resolve a UUID back to the node's leaf short-name."""
    n = cmds.ls(uid, long=False)
    return n[0].split("|")[-1].split(":")[-1] if n else ""


class TestNaming(MayaTkTestCase):
    """Tests for Naming class functionality."""

    def setUp(self):
        super().setUp()
        self.grp1 = cmds.group(n="TestGroup1", em=True)
        self.grp2 = cmds.group(n="TestGroup2", em=True)

    def test_rename_duplicates_in_hierarchy(self):
        """Test renaming multiple objects with same short name in different hierarchies."""
        cube1 = cmds.polyCube(n="Cube")[0]
        u1 = _uuid(cube1)
        cmds.parent(cube1, self.grp1)

        cube2 = cmds.polyCube(n="Cube")[0]
        u2 = _uuid(cube2)
        cmds.parent(cube2, self.grp2)

        # Resolve the (possibly mangled) names back through their UUIDs so we
        # can pass current paths to Naming.rename.
        cube1, cube2 = cmds.ls(u1, long=False)[0], cmds.ls(u2, long=False)[0]

        # Verify setup — both cubes leaf-named "Cube" under different parents.
        self.assertEqual(_name(u1), "Cube")
        self.assertEqual(_name(u2), "Cube")

        # Rename
        Naming.rename([cube1, cube2], "RenamedCube")

        # Verify — both can be "RenamedCube" because they're in different groups.
        self.assertEqual(_name(u1), "RenamedCube")
        self.assertEqual(_name(u2), "RenamedCube")

    def test_rename_unique_names(self):
        """Test simple rename of unique objects."""
        cube = cmds.polyCube(n="UniqueCube")[0]
        u = _uuid(cube)
        Naming.rename([cube], "NewName")
        self.assertEqual(_name(u), "NewName")

    def test_rename_pattern(self):
        """Test renaming with pattern replacement."""
        cube = cmds.polyCube(n="My_Cube_GEO")[0]
        u = _uuid(cube)
        Naming.rename([cube], "Sphere", "*Cube*")
        self.assertIn("Sphere", _name(u))

    def test_rename_suffix_retention(self):
        """Test suffix retention."""
        cube = cmds.polyCube(n="MyObject_GEO")[0]
        u = _uuid(cube)
        Naming.rename([cube], "NewObject", retain_suffix=True)
        self.assertEqual(_name(u), "NewObject_GEO")

    def test_rename_suffix_retention_replaces_new_suffix(self):
        """Verify retain_suffix replaces newName's suffix with each object's original suffix."""
        grp = cmds.group(n="S00B6_TAG_GRP", em=True)
        loc = cmds.spaceLocator(n="S00B6_TAG_LOC")[0]
        geo = cmds.polyCube(n="S00B6_TAG_GEO")[0]
        ug, ul, uo = _uuid(grp), _uuid(loc), _uuid(geo)

        valid_suffixes = ["_GRP", "_LOC", "_GEO"]
        Naming.rename([grp, loc, geo],
            "S00B8_TAG_LOC",
            retain_suffix=True,
            valid_suffixes=valid_suffixes,
        )

        self.assertEqual(_name(ug), "S00B8_TAG_GRP")
        self.assertEqual(_name(ul), "S00B8_TAG_LOC")
        self.assertEqual(_name(uo), "S00B8_TAG_GEO")

    def test_rename_suffix_retention_no_valid_suffixes(self):
        """Verify retain_suffix with valid_suffixes=None treats any _XXX as a suffix."""
        grp = cmds.group(n="Foo_GRP", em=True)
        geo = cmds.polyCube(n="Foo_GEO")[0]
        ug, uo = _uuid(grp), _uuid(geo)

        Naming.rename([grp, geo],
            "Bar_GEO",
            retain_suffix=True,
            valid_suffixes=None,
        )

        self.assertEqual(_name(ug), "Bar_GRP")
        self.assertEqual(_name(uo), "Bar_GEO")

    def test_rename_suffix_retention_strips_trailing_digits(self):
        """Verify trailing digits are stripped when matching suffixes."""
        grp1 = cmds.group(n="Asset_GRP1", em=True)
        grp2 = cmds.group(n="Asset_GRP2", em=True)
        loc = cmds.spaceLocator(n="Asset_LOC3")[0]
        u1, u2, ul = _uuid(grp1), _uuid(grp2), _uuid(loc)

        valid_suffixes = ["_GRP", "_LOC", "_GEO"]
        Naming.rename([grp1, grp2, loc],
            "NewAsset_LOC",
            retain_suffix=True,
            valid_suffixes=valid_suffixes,
        )

        self.assertEqual(_name(u1), "NewAsset_GRP")
        self.assertEqual(_name(u2), "NewAsset_GRP1")
        self.assertEqual(_name(ul), "NewAsset_LOC")

    def test_rename_suffix_retention_unknown_new_suffix_not_stripped(self):
        """Verify newName's suffix is NOT stripped if not in valid_suffixes."""
        geo = cmds.polyCube(n="Part_GEO")[0]
        u = _uuid(geo)

        Naming.rename([geo],
            "Detail_HIGH",
            retain_suffix=True,
            valid_suffixes=["_GRP", "_LOC", "_GEO"],
        )

        # _HIGH is not in valid_suffixes so it should NOT be stripped.
        # _GEO from oldName gets appended instead.
        self.assertEqual(_name(u), "Detail_HIGH_GEO")

    def test_append_location_based_suffix_basic(self):
        """Test append_location_based_suffix basic functionality."""
        c1 = cmds.polyCube(n="BoxA")[0]
        cmds.move(0, 0, 0, c1)
        c2 = cmds.polyCube(n="BoxA")[0]
        cmds.move(10, 0, 0, c2)
        c3 = cmds.polyCube(n="BoxA")[0]
        cmds.move(5, 0, 0, c3)
        u1, u2, u3 = _uuid(c1), _uuid(c2), _uuid(c3)

        Naming.append_location_based_suffix([c1, c2, c3], strip_trailing_ints=True)

        self.assertTrue(_name(u1).endswith("_01"))
        self.assertTrue(_name(u3).endswith("_02"))
        self.assertTrue(_name(u2).endswith("_03"))

    def test_append_location_based_suffix_independent_groups(self):
        """Test independent groups renaming."""
        b1 = cmds.polyCube(n="Box")[0]
        cmds.move(0, 0, 0, b1)
        b2 = cmds.polyCube(n="Box")[0]
        cmds.move(10, 0, 0, b2)

        s1 = cmds.polySphere(n="Sphere")[0]
        cmds.move(0, 0, 0, s1)
        s2 = cmds.polySphere(n="Sphere")[0]
        cmds.move(10, 0, 0, s2)
        ub1, ub2, us1, us2 = _uuid(b1), _uuid(b2), _uuid(s1), _uuid(s2)

        Naming.append_location_based_suffix(
            [b1, b2, s1, s2], independent_groups=True, strip_trailing_ints=True
        )

        self.assertTrue(_name(ub1).endswith("_01"), f"Box1 is {_name(ub1)}")
        self.assertTrue(_name(ub2).endswith("_02"), f"Box2 is {_name(ub2)}")
        self.assertTrue(_name(us1).endswith("_01"), f"Sphere1 is {_name(us1)}")
        self.assertTrue(_name(us2).endswith("_02"), f"Sphere2 is {_name(us2)}")

    def test_append_location_based_suffix_stripping(self):
        """Test stripping defined suffixes."""
        c1 = cmds.polyCube(n="Name_GRP_01")[0]
        u = _uuid(c1)

        Naming.append_location_based_suffix(
            [c1],
            strip_trailing_ints=True,
            strip_defined_suffixes=True,
            valid_suffixes=["_GRP"],
        )
        self.assertTrue(_name(u).endswith("Name_01"), f"Got {_name(u)}")

    def test_independent_groups_formatting(self):
        """Test suffix placement behavior in independent groups mode."""
        g1 = cmds.group(n="Container_GRP", em=True)
        cmds.move(0, 0, 0, g1)
        g2 = cmds.group(n="Container_GRP", em=True)
        cmds.move(10, 0, 0, g2)
        u1, u2 = _uuid(g1), _uuid(g2)

        Naming.append_location_based_suffix(
            [g1, g2],
            independent_groups=True,
            strip_defined_suffixes=False,
            valid_suffixes=["_GRP"],
        )
        self.assertEqual(_name(u1), "Container_01_GRP")
        self.assertEqual(_name(u2), "Container_02_GRP")

        # Reset (resolve current names from UUIDs since the originals are stale).
        cmds.rename(cmds.ls(u1)[0], "Container_GRP")
        cmds.rename(cmds.ls(u2)[0], "Container_GRP1")

        Naming.append_location_based_suffix(
            [cmds.ls(u1)[0], cmds.ls(u2)[0]],
            independent_groups=True,
            strip_defined_suffixes=True,
            valid_suffixes=["_GRP"],
        )
        self.assertEqual(_name(u1), "Container_01")
        self.assertEqual(_name(u2), "Container_02")

    # ------------------------------------------------------------------
    # suffix_by_type: strip_trailing_padding
    # ------------------------------------------------------------------

    def test_suffix_by_type_padding_preserves_underscore_number(self):
        """Verify strip_trailing_padding=True keeps intentional '_02' numbering."""
        cube = cmds.polyCube(n="Cube_02")[0]
        u = _uuid(cube)
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(_name(u), "Cube_02_GEO")

    def test_suffix_by_type_padding_strips_orphan_underscores(self):
        """Verify strip_trailing_padding cleans up bare trailing underscores."""
        cube = cmds.polyCube(n="Cube_")[0]
        u = _uuid(cube)
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(_name(u), "Cube_GEO")

    def test_suffix_by_type_padding_strips_orphan_underscore_digits(self):
        """Verify strip_trailing_padding cleans orphaned '_' + digits left
        after removing a wrong suffix (e.g. 'Foo_01_' -> 'Foo')."""
        cube = cmds.polyCube(n="Cube_01_")[0]
        u = _uuid(cube)
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(_name(u), "Cube_GEO")

    def test_suffix_by_type_padding_no_trailing_artifact(self):
        """Verify strip_trailing_padding is a no-op when name is clean."""
        cube = cmds.polyCube(n="CleanName")[0]
        u = _uuid(cube)
        Naming.suffix_by_type(
            [cube],
            strip_trailing_ints=False,
            strip_trailing_underscores=False,
            strip_trailing_padding=True,
        )
        self.assertEqual(_name(u), "CleanName_GEO")

    # ------------------------------------------------------------------
    # rename: strip-residue collapse
    # ------------------------------------------------------------------

    def test_rename_strip_collapses_underscore_residue(self):
        """Stripping a token collapses the leftover separator runs.

        Regression: stripping accumulated '__uninst_tmp' scratch tokens
        (VDATS_module.ma) left runs of orphaned underscores behind
        ('vdat__uninst_tmp__uninst_tmpShape702' -> 'vdat____Shape702').
        """
        cube = cmds.polyCube(n="vdat__uninst_tmp__uninst_tmpShape702")[0]
        u = _uuid(cube)
        Naming.rename([cube], "", "*uninst_tmp*")
        self.assertEqual(_name(u), "vdat_Shape702")

    def test_rename_strip_trailing_residue(self):
        """Stripping a trailing token leaves no orphaned trailing underscores."""
        cube = cmds.polyCube(n="Crate__RZTMP")[0]
        u = _uuid(cube)
        Naming.rename([cube], "", "*RZTMP*")
        self.assertEqual(_name(u), "Crate")

    def test_rename_explicit_double_underscore_preserved(self):
        """An explicit '__' in the requested name is honored, not collapsed."""
        cube = cmds.polyCube(n="PlainCube")[0]
        u = _uuid(cube)
        Naming.rename([cube], "foo__bar")
        self.assertEqual(_name(u), "foo__bar")

    def test_rename_collapse_padding_off_preserves_residue(self):
        """collapse_padding=False keeps every underscore run untouched."""
        cube = cmds.polyCube(n="vdat__uninst_tmpShape7")[0]
        u = _uuid(cube)
        Naming.rename([cube], "", "*uninst_tmp*", collapse_padding=False)
        self.assertEqual(_name(u), "vdat__Shape7")


class TestConformShapeNames(MayaTkTestCase):
    """Tests for Naming.conform_shape_names."""

    def test_conform_mangled_shape(self):
        """A mangled shape is renamed to '<transform>Shape'."""
        cube = cmds.polyCube(n="vdat1")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.rename(shape, "vdatShape1__uninst_tmpShape380")
        pairs = Naming.conform_shape_names([cube])
        self.assertEqual(len(pairs), 1)
        leaf = cmds.listRelatives(cube, shapes=True)[0].split("|")[-1]
        self.assertEqual(leaf, "vdatShape1")

    def test_conform_skips_already_conforming(self):
        """A conventionally named shape is left untouched."""
        cube = cmds.polyCube(n="Crate")[0]
        pairs = Naming.conform_shape_names([cube])
        self.assertEqual(pairs, [])

    def test_conform_empty_objects_is_noop(self):
        """An empty (non-None) object list must not fall back to selection/scene."""
        cube = cmds.polyCube(n="untouched")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        mangled = cmds.rename(shape, "untouched____badShape")
        cmds.select(cube)  # a selection fallback would wrongly repair it
        self.assertEqual(Naming.conform_shape_names([]), [])
        self.assertTrue(cmds.objExists(mangled))

    def test_conform_instanced_shape_renamed_once(self):
        """A shared (instanced) shape is renamed exactly once, fixing every path."""
        cube = cmds.polyCube(n="proto")[0]
        inst = cmds.instance(cube)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.rename(shape, "vdat____Shape770__uninst_tmp____Shape")
        pairs = Naming.conform_shape_names([cube, inst])
        self.assertEqual(len(pairs), 1)
        for t in (cube, inst):
            leafs = [
                s.split("|")[-1]
                for s in cmds.listRelatives(t, shapes=True, fullPath=True)
            ]
            self.assertEqual(leafs, ["protoShape"])

    def test_conform_intermediate_gets_orig_suffix(self):
        """A mangled intermediate (orig) shape conforms to '<transform>ShapeOrig'."""
        cube = cmds.polyCube(n="smoothy")[0]
        cmds.cluster(cube)  # deformer → intermediate orig shape
        all_shapes = (
            cmds.listRelatives(cube, shapes=True, fullPath=True, noIntermediate=False)
            or []
        )
        orig = [s for s in all_shapes if cmds.getAttr(f"{s}.intermediateObject")]
        self.assertTrue(orig, "polySmooth should have produced an orig shape")
        cmds.rename(orig[0], "junk__uninst_tmpShapeOrig999")
        Naming.conform_shape_names([cube])
        all_shapes = (
            cmds.listRelatives(cube, shapes=True, fullPath=True, noIntermediate=False)
            or []
        )
        leafs = sorted(s.split("|")[-1] for s in all_shapes)
        self.assertIn("smoothyShapeOrig", leafs)


if __name__ == "__main__":
    unittest.main()
