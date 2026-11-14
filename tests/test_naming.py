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

    def test_rename_duplicates_with_filter(self):
        """Duplicate short names must all rename when a filter is supplied.

        Regression: the filtered path deduplicated through find_str, so only the
        first of two same-named objects was renamed and the second silently kept
        its old name. The no-filter path was unaffected (it formats per name),
        which is why test_rename_duplicates_in_hierarchy never caught it.
        """
        cube1 = cmds.polyCube(n="Cube")[0]
        u1 = _uuid(cube1)
        cmds.parent(cube1, self.grp1)

        cube2 = cmds.polyCube(n="Cube")[0]
        u2 = _uuid(cube2)
        cmds.parent(cube2, self.grp2)

        cube1, cube2 = cmds.ls(u1, long=True)[0], cmds.ls(u2, long=True)[0]
        Naming.rename([cube1, cube2], "**_GEO", "*Cube*")

        self.assertEqual(_name(u1), "Cube_GEO")
        self.assertEqual(_name(u2), "Cube_GEO")

    def test_rename_paired_terms(self):
        """Pipe-separated 'to' terms pair positionally with the filter's terms."""
        left = cmds.polyCube(n="arm_L")[0]
        right = cmds.polyCube(n="arm_R")[0]
        ul, ur = _uuid(left), _uuid(right)
        Naming.rename([left, right], "*_lt|*_rt", "*_L|*_R")
        self.assertEqual(_name(ul), "arm_lt")
        self.assertEqual(_name(ur), "arm_rt")

    def test_rename_multi_term_filter_formats_each_match(self):
        """Each filter term supplies the 'from' text for the names it matched.

        Regression: a '|' filter collapsed into one literal, so matched objects
        came back unformatted.
        """
        cube = cmds.polyCube(n="pCube1")[0]
        sphere = cmds.polySphere(n="nurbsSphere1")[0]
        uc, us = _uuid(cube), _uuid(sphere)
        Naming.rename([cube, sphere], "*box*", "pCube*|nurbs*")
        self.assertEqual(_name(uc), "box1")
        self.assertEqual(_name(us), "boxSphere1")

    def test_rename_regex_backref(self):
        """Regex capture groups expand in the 'to' pattern."""
        cube = cmds.polyCube(n="pCube1")[0]
        u = _uuid(cube)
        Naming.rename([cube], r"*\1_GEO", r"Cube(\d+)", regex=True)
        self.assertEqual(_name(u), "p1_GEO")

    def test_rename_regex_replace_suffix_cuts_at_match(self):
        """Regex replace-suffix cuts at the match instead of blindly appending."""
        cube = cmds.polyCube(n="pCube1")[0]
        u = _uuid(cube)
        Naming.rename([cube], "*_GEO", r"Cube.*", regex=True)
        self.assertEqual(_name(u), "p_GEO")

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
        Naming.rename(
            [grp, loc, geo],
            "S00B8_TAG_LOC",
            retain_suffix=True,
            valid_suffixes=valid_suffixes,
        )

        self.assertEqual(_name(ug), "S00B8_TAG_GRP")
        self.assertEqual(_name(ul), "S00B8_TAG_LOC")
        self.assertEqual(_name(uo), "S00B8_TAG_GEO")

    def test_rename_suffix_retention_defaults_to_the_convention(self):
        """Verify valid_suffixes=None retains the shared convention's affixes."""
        grp = cmds.group(n="Foo_GRP", em=True)
        geo = cmds.polyCube(n="Foo_GEO")[0]
        ug, uo = _uuid(grp), _uuid(geo)

        Naming.rename(
            [grp, geo],
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
        Naming.rename(
            [grp1, grp2, loc],
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

        Naming.rename(
            [geo],
            "Detail_HIGH",
            retain_suffix=True,
            valid_suffixes=["_GRP", "_LOC", "_GEO"],
        )

        # _HIGH is not in valid_suffixes so it should NOT be stripped.
        # _GEO from oldName gets appended instead.
        self.assertEqual(_name(u), "Detail_HIGH_GEO")

    def test_rename_suffix_retention_only_defined_suffixes(self):
        """A trailing token outside the convention is numbering/description, not a type."""
        cube = cmds.polyCube(n="wall_low")[0]
        u = _uuid(cube)

        # Default valid_suffixes: the shared convention, which has no '_low'.
        Naming.rename([cube], "Prop", retain_suffix=True)

        self.assertEqual(_name(u), "Prop")

    def test_rename_suffix_retention_not_doubled_by_append(self):
        """An append pattern keeps the suffix, so there is nothing to retain."""
        geo = cmds.polyCube(n="Sphere_GEO")[0]
        u = _uuid(geo)

        Naming.rename([geo], "**_A", retain_suffix=True, valid_suffixes=["_GEO"])

        self.assertEqual(_name(u), "Sphere_GEO_A")

    def test_rename_suffix_retention_keeps_numbering(self):
        """A numbered suffix keeps its number -- collapsing it collides names."""
        geo = cmds.polyCube(n="pCube_GEO1")[0]
        u = _uuid(geo)

        Naming.rename([geo], "**_A", retain_suffix=True, valid_suffixes=["_GEO"])

        self.assertEqual(_name(u), "pCube_GEO1_A")

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
        (PROPS_module.ma) left runs of orphaned underscores behind
        ('prop__uninst_tmp__uninst_tmpShape702' -> 'prop____Shape702').
        """
        cube = cmds.polyCube(n="prop__uninst_tmp__uninst_tmpShape702")[0]
        u = _uuid(cube)
        Naming.rename([cube], "", "*uninst_tmp*")
        self.assertEqual(_name(u), "prop_Shape702")

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
        cube = cmds.polyCube(n="prop__uninst_tmpShape7")[0]
        u = _uuid(cube)
        Naming.rename([cube], "", "*uninst_tmp*", collapse_padding=False)
        self.assertEqual(_name(u), "prop__Shape7")

    # ------------------------------------------------------------------
    # dry_run: every operation plans + reports without touching the scene
    # ------------------------------------------------------------------

    def test_rename_dry_run(self):
        cube = cmds.polyCube(n="pCube1")[0]
        u = _uuid(cube)
        planned = Naming.rename([cube], "**_GEO", "*Cube*", dry_run=True)
        self.assertEqual(planned, ["pCube1_GEO"])
        self.assertEqual(_name(u), "pCube1")

    def test_set_case_dry_run_and_return(self):
        cube = cmds.polyCube(n="pCube1")[0]
        u = _uuid(cube)
        self.assertEqual(Naming.set_case([cube], "upper", dry_run=True), ["PCUBE1"])
        self.assertEqual(_name(u), "pCube1")
        self.assertEqual(Naming.set_case([cube], "upper"), ["PCUBE1"])
        self.assertEqual(_name(u), "PCUBE1")

    def test_strip_chars_dry_run(self):
        cube = cmds.polyCube(n="XXcube")[0]
        u = _uuid(cube)
        self.assertEqual(Naming.strip_chars([cube], 2, dry_run=True), ["cube"])
        self.assertEqual(_name(u), "XXcube")
        self.assertEqual(Naming.strip_chars([cube], 2), ["cube"])
        self.assertEqual(_name(u), "cube")

    def test_suffix_by_type_dry_run(self):
        cube = cmds.polyCube(n="Box")[0]
        u = _uuid(cube)
        self.assertEqual(Naming.suffix_by_type([cube], dry_run=True), ["Box_GEO"])
        self.assertEqual(_name(u), "Box")

    def test_location_suffix_keeps_already_correct_name(self):
        """An object whose name is already right must not be left on the placeholder.

        Regression: the two-pass rename parked EVERY node on 'p0000000000', but
        the plan never renames an unchanged entry back.
        """
        near = cmds.polyCube(n="l_01")[0]
        far = cmds.polyCube(n="r")[0]
        cmds.move(5, 0, 0, far)
        un, uf = _uuid(near), _uuid(far)
        Naming.append_location_based_suffix([far, near])
        self.assertEqual(_name(un), "l_01")
        self.assertEqual(_name(uf), "r_02")

    def test_location_suffix_dry_run(self):
        near = cmds.polyCube(n="l")[0]
        far = cmds.polyCube(n="r")[0]
        cmds.move(5, 0, 0, far)
        un, uf = _uuid(near), _uuid(far)
        planned = Naming.append_location_based_suffix([far, near], dry_run=True)
        self.assertEqual(planned, ["l_01", "r_02"])
        self.assertEqual((_name(un), _name(uf)), ("l", "r"))

    # ------------------------------------------------------------------
    # type resolution + expanded suffix set
    # ------------------------------------------------------------------

    def test_type_key_transforms_resolve_through_shapes(self):
        """A curve / camera / light TRANSFORM classifies like its shape.

        Regression: the old objectType-based lookup saw 'transform' for these
        and applied no suffix at all.
        """
        crv = cmds.circle(n="Path", ch=False)[0]
        cam = cmds.camera(n="Shot")[0]
        lgt = cmds.shadingNode("pointLight", asLight=True, n="Key")
        srf = cmds.nurbsPlane(n="Patch", ch=False)[0]
        grp = cmds.group(em=True, n="Root")
        loc = cmds.spaceLocator(n="Helper")[0]
        self.assertEqual(Naming.type_key(crv), "nurbsCurve")
        self.assertEqual(Naming.type_key(cam), "camera")
        self.assertEqual(Naming.type_key(lgt), "light")
        self.assertEqual(Naming.type_key(srf), "nurbsSurface")
        self.assertEqual(Naming.type_key(grp), "group")
        self.assertEqual(Naming.type_key(loc), "locator")
        self.assertEqual(Naming.type_key(cmds.polyCube(n="Box")[0]), "mesh")

    def test_type_key_rig_and_deformer_nodes(self):
        cube = cmds.polyCube(n="Skin")[0]
        other = cmds.polyCube(n="Follower")[0]
        j1 = cmds.joint(n="j1", p=(0, 0, 0))
        j2 = cmds.joint(n="j2", p=(0, 5, 0))
        ikh = cmds.ikHandle(sj=j1, ee=j2, n="arm_ik")[0]
        skin = cmds.skinCluster(j1, cube, n="skin1")[0]
        bs = cmds.blendShape(cube, n="bs1")[0]
        cls_node, cls_handle = cmds.cluster(cube, n="cls1")
        lat = cmds.lattice(cube, n="lat1")  # (ffd, lattice, base)
        con = cmds.pointConstraint(j1, other, n="con1")[0]
        self.assertEqual(Naming.type_key(j1), "joint")
        self.assertEqual(Naming.type_key(ikh), "ikHandle")
        self.assertEqual(Naming.type_key(skin), "skinCluster")
        self.assertEqual(Naming.type_key(bs), "blendShape")
        self.assertEqual(Naming.type_key(cls_node), "cluster")
        self.assertEqual(Naming.type_key(cls_handle), "cluster")
        self.assertEqual(Naming.type_key(lat[0]), "lattice")
        self.assertEqual(Naming.type_key(lat[1]), "lattice")
        self.assertEqual(Naming.type_key(lat[2]), "lattice")
        self.assertEqual(Naming.type_key(con), "constraint")

    def test_type_key_shading_and_scene_nodes(self):
        mat = cmds.shadingNode("lambert", asShader=True, n="brick")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, n="brickSG")
        tex = cmds.shadingNode("file", asTexture=True, n="brick_D")
        st = cmds.sets(empty=True, n="mySet")
        layer = cmds.createDisplayLayer(n="bgLayer", empty=True)
        self.assertEqual(Naming.type_key(mat), "material")
        self.assertEqual(Naming.type_key(sg), "shadingEngine")
        self.assertEqual(Naming.type_key(tex), "texture")
        self.assertEqual(Naming.type_key(st), "objectSet")
        self.assertEqual(Naming.type_key(layer), "displayLayer")

    def test_suffix_by_type_expanded_set(self):
        crv = cmds.circle(n="Path", ch=False)[0]
        cam = cmds.rename(cmds.camera()[0], "Shot")
        srf = cmds.nurbsPlane(n="Patch", ch=False)[0]
        mat = cmds.shadingNode("lambert", asShader=True, n="brick")
        uc, ua, us = _uuid(crv), _uuid(cam), _uuid(srf)
        Naming.suffix_by_type([crv, cam, srf, mat])
        self.assertEqual(_name(uc), "Path_CRV")
        self.assertEqual(_name(ua), "Shot_CAM")
        self.assertEqual(_name(us), "Patch_SRF")
        self.assertTrue(cmds.objExists("brick_MAT"))

    def test_suffix_by_type_empty_suffix_disables_type(self):
        cam = cmds.rename(cmds.camera()[0], "Shot")
        u = _uuid(cam)
        Naming.suffix_by_type([cam], camera_suffix="")
        self.assertEqual(_name(u), "Shot")

    def test_suffix_by_type_strips_wrong_expanded_suffix(self):
        cube = cmds.polyCube(n="Wall_SRF")[0]
        u = _uuid(cube)
        Naming.suffix_by_type([cube])
        self.assertEqual(_name(u), "Wall_GEO")

    def test_suffix_by_type_keeps_words_that_merely_spell_a_type_token(self):
        """A type vocabulary is fixed-case; the words a name is made of are not.

        The strip runs the WHOLE 19-entry convention table at every name. While
        it folded case, any ordinary word spelling a token was eaten from either
        end -- "security_cam" came back "security_GEO", "tile_set" as
        "tile_GEO", "con_rod" as "rod_GEO". These are ordinary asset names.
        """
        for typed, expected in (
            ("security_cam", "security_cam_GEO"),
            ("tile_set", "tile_set_GEO"),
            ("con_rod", "con_rod_GEO"),
            ("bolt_bs", "bolt_bs_GEO"),
        ):
            with self.subTest(name=typed):
                cube = cmds.polyCube(n=typed)[0]
                u = _uuid(cube)
                Naming.suffix_by_type([cube])
                self.assertEqual(_name(u), expected)

    def test_suffix_by_type_moves_a_legacy_affix_instead_of_doubling_it(self):
        """The convention flipping sides must CORRECT a name, not decorate it.

        The strip used to exclude the affix about to be applied, to spare an
        already-correct name. apply_affix is idempotent, so that bought nothing
        -- and once the convention moved to a prefix the vocabulary held only
        "GEO_", which was the one entry a legacy "body_GEO" matched. Excluding
        it left the name unstripped and applied a second affix.
        """
        cube = cmds.polyCube(n="body_GEO")[0]
        u = _uuid(cube)
        Naming.suffix_by_type([cube], mesh_suffix="GEO_")
        self.assertEqual(
            _name(u),
            "GEO_body",
            "a legacy suffix-spelled name gained a SECOND affix",
        )

    def test_suffix_by_type_leaves_an_already_correct_name_alone(self):
        """The other half of the same contract: no churn."""
        cube = cmds.polyCube(n="GEO_body")[0]
        u = _uuid(cube)
        Naming.suffix_by_type([cube], mesh_suffix="GEO_")
        self.assertEqual(_name(u), "GEO_body")

    def test_suffix_by_type_parent_before_child(self):
        """Renaming a parent first must not orphan the child's cached path."""
        grp = cmds.group(em=True, n="Root")
        cube = cmds.polyCube(n="Box")[0]
        cmds.parent(cube, grp)
        ug, uc = _uuid(grp), _uuid(cube)
        Naming.suffix_by_type([grp, cmds.ls(uc, long=True)[0]])
        self.assertEqual(_name(ug), "Root_GRP")
        self.assertEqual(_name(uc), "Box_GEO")

    def test_read_only_nodes_skipped_not_failed(self):
        """A read-only node is skipped (one tally line) and the batch result stays parallel."""
        import os

        ref = cmds.polyCube(n="RefCube")[0]
        path = os.path.join(
            cmds.internalVar(userTmpDir=True), "naming_readonly_ref.ma"
        ).replace("\\", "/")
        cmds.select(ref)
        cmds.file(path, force=True, exportSelected=True, type="mayaAscii")
        cmds.delete(ref)
        node = cmds.file(path, reference=True, namespace="ro", returnNewNodes=True)
        ro = next(n for n in cmds.ls(node, type="transform"))
        try:
            self.assertTrue(cmds.ls(ro, readOnly=True))
            local = cmds.polyCube(n="Local")[0]
            result = Naming.suffix_by_type([ro, local])
            self.assertEqual(len(result), 2)
            self.assertEqual(result[1].split("|")[-1], "Local_GEO")
            self.assertEqual(cmds.ls(ro)[0].split(":")[-1], "RefCube")
        finally:
            cmds.file(path, removeReference=True)
            os.remove(path)

    def test_scene_objects_excludes_defaults_and_shapes(self):
        cmds.polyCube(n="Box")
        names = {n.split("|")[-1] for n in Naming.scene_objects()}
        self.assertIn("Box", names)
        self.assertNotIn("BoxShape", names)
        for default in ("persp", "lambert1", "initialShadingGroup", "time1"):
            self.assertNotIn(default, names)


class TestConformShapeNames(MayaTkTestCase):
    """Tests for Naming.conform_shape_names."""

    def test_conform_mangled_shape(self):
        """A mangled shape is renamed to '<transform>Shape'."""
        cube = cmds.polyCube(n="prop1")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.rename(shape, "propShape1__uninst_tmpShape380")
        pairs = Naming.conform_shape_names([cube])
        self.assertEqual(len(pairs), 1)
        leaf = cmds.listRelatives(cube, shapes=True)[0].split("|")[-1]
        self.assertEqual(leaf, "propShape1")

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
        cmds.rename(shape, "prop____Shape770__uninst_tmp____Shape")
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


class TestNamingToolApi(MayaTkTestCase):
    """What the Naming panel (tentacle) asks of the engine."""

    def test_scope_objects_is_the_selection_or_the_scene(self):
        a = cmds.polyCube(n="scopeA")[0]
        cmds.polyCube(n="scopeB")
        cmds.select(a)
        self.assertEqual(Naming.scope_objects(), cmds.ls(a, uuid=True))  # frozen by UUID
        names = {
            n.split("|")[-1] for n in cmds.ls(Naming.scope_objects(scene=True), long=True)
        }
        self.assertLessEqual({"scopeA", "scopeB"}, names)
        cmds.select(clear=True)
        self.assertEqual(Naming.scope_objects(), [])

    def test_select_by_name_selects_the_matches(self):
        cube = cmds.polyCube(n="findMe")[0]
        loc = cmds.spaceLocator(n="findLoc")[0]
        cmds.group(em=True, n="other")  # searched, never matched
        found, searched = Naming.select_by_name("findMe|findLoc")  # exact terms
        self.assertEqual(sorted(found), sorted(cmds.ls([cube, loc], long=True)))
        self.assertEqual(sorted(cmds.ls(selection=True, long=True)), sorted(found))
        self.assertGreater(searched, len(found))
        found, searched = Naming.select_by_name("find*", locators_only=True)
        self.assertEqual((found, searched), (cmds.ls(loc, long=True), 1))

    def test_select_by_name_with_no_match_clears_the_selection(self):
        cmds.select(cmds.polyCube(n="keepMe")[0])
        self.assertEqual(Naming.select_by_name("noSuchName")[0], [])
        self.assertEqual(cmds.ls(selection=True), [])

    def test_select_link_labels_the_leaf_and_selects_the_path(self):
        path = cmds.ls(cmds.polyCube(n="linked")[0], long=True)[0]
        self.assertEqual(
            Naming.select_link(path), Naming.log_link("linked", "select", node=path)
        )

    def test_location_suffix_skips_what_has_no_location(self):
        near = cmds.polyCube(n="row")[0]
        far = cmds.polyCube(n="row")[0]  # Maya names it row1
        cmds.move(5, 0, 0, far)
        material = cmds.shadingNode("lambert", asShader=True, n="rowMat")
        Naming.append_location_based_suffix([far, near, material])
        self.assertTrue(cmds.objExists("row_01") and cmds.objExists("row_02"))
        self.assertTrue(cmds.objExists("rowMat"))

    def test_location_suffix_with_no_transform_renames_nothing(self):
        material = cmds.shadingNode("lambert", asShader=True, n="loneMat")
        self.assertEqual(Naming.append_location_based_suffix([material]), [])
        self.assertTrue(cmds.objExists("loneMat"))

    def test_every_convention_entry_applies_in_maya(self):
        self.assertEqual(Naming.UNSUPPORTED_TYPES, {})


class _Captured:
    """Collect what ``Naming.logger`` emits during a block (message texts)."""

    def __init__(self):
        import logging

        self.texts = []
        handler = logging.Handler()
        handler.emit = lambda record: self.texts.append(record.getMessage())
        self.handler = handler

    def __enter__(self):
        Naming.logger.addHandler(self.handler)
        return self

    def __exit__(self, *_exc):
        Naming.logger.removeHandler(self.handler)

    def lines(self, word):
        return [t for t in self.texts if word in t]


class TestNamingEdgeCases(MayaTkTestCase):
    """The Naming probe's findings (2026-10-31), one regression each."""

    def tearDown(self):
        cmds.namespace(setNamespace=":")
        super().tearDown()

    # -- S1-A: the node's own namespace is kept --------------------------------

    def test_every_operation_keeps_the_namespace(self):
        """Regression: each operation renamed ``ns:prop1`` into the CURRENT
        namespace (``|PROP1``, ``|prop1_GEO``, ...), dropping ``ns``."""
        cmds.namespace(add="ns")
        runs = (
            lambda n: Naming.set_case([n], "upper"),
            lambda n: Naming.suffix_by_type([n]),
            lambda n: Naming.strip_chars([n], 1, trailing=True),
            lambda n: Naming.rename([n], "**_v2"),
            lambda n: Naming.append_location_based_suffix([n]),
        )
        for run in runs:
            node = cmds.polyCube(n="ns:prop1")[0]
            uid = _uuid(node)
            run(node)
            path = cmds.ls(uid, long=True)[0]
            self.assertTrue(path.split("|")[-1].startswith("ns:"), path)
            cmds.delete(path)

    def test_a_root_node_stays_in_the_root_while_another_namespace_is_current(self):
        cmds.group(em=True, n="rootobj")
        uid = _uuid("rootobj")
        cmds.namespace(add="other")
        cmds.namespace(setNamespace=":other")
        Naming.rename(["|rootobj"], "moved")
        self.assertEqual(cmds.ls(uid, long=True), ["|moved"])

    # -- S1-B: the Scene scope is the user's objects ---------------------------

    def test_scene_scope_holds_the_users_objects_only(self):
        """Regression: the Scene scope was every DG node minus a few, so it
        renamed script nodes, renderer singletons and construction history."""
        cube = cmds.polyCube(n="crate")[0]  # also makes polyCube1 history
        mat = cmds.shadingNode("lambert", asShader=True, n="crateMat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, n="crateSG")
        layer = cmds.createDisplayLayer(empty=True, n="propsLayer")
        cmds.scriptNode(n="myScriptNode", beforeScript="pass", sourceType="python")
        cmds.scriptNode(n="uiConfigurationScriptNode", beforeScript="pass")
        cmds.createNode("unknown", n="pluginSingleton")
        names = {n.split("|")[-1] for n in Naming.scene_objects()}
        self.assertLessEqual({cube, mat, sg, layer}, names)
        for system in ("polyCube1", "myScriptNode", "uiConfigurationScriptNode",
                       "pluginSingleton", "time1", "lambert1", "persp"):
            self.assertNotIn(system, names)
        found, searched = Naming.select_by_name("*")
        self.assertEqual(searched, len(Naming._user_nodes()))
        self.assertNotIn("polyCube1", {f.split("|")[-1] for f in found})
        self.assertNotIn("time1", {f.split("|")[-1] for f in found})

    # -- S1-D / S2-C: two phases in every operation, nothing stranded ----------

    def test_a_locked_node_in_a_location_batch_strands_nothing(self):
        """Regression: the park ran before the skip filter and caught nothing:
        RuntimeError, and the others left as p0000000000, p0000000001."""
        a = cmds.group(em=True, n="a")
        lk = cmds.group(em=True, n="lk")
        b = cmds.group(em=True, n="b")
        for i, node in enumerate((a, lk, b)):
            cmds.move(i, 0, 0, node)
        cmds.lockNode(lk, lock=True)
        try:
            with _Captured() as log:
                Naming.append_location_based_suffix([a, lk, b])
            leafs = {n.split("|")[-1] for n in cmds.ls(type="transform")}
            self.assertFalse([n for n in leafs if "park" in n or n.startswith("p000")])
            self.assertIn("lk", leafs)
            self.assertLessEqual({"a_01", "b_03"}, leafs)
            self.assertEqual(len(log.lines("Skipped")), 1, log.texts)
        finally:
            cmds.lockNode(lk, lock=False)

    def test_a_swap_renames_both(self):
        """Regression: obj1 -> obj2 / obj2 -> obj1 gave obj3, obj1."""
        u1, u2 = _uuid(cmds.group(em=True, n="obj1")), _uuid(cmds.group(em=True, n="obj2"))
        Naming.rename(["obj1", "obj2"], "obj2|obj1", "obj1|obj2")
        self.assertEqual((_name(u1), _name(u2)), ("obj2", "obj1"))

    # -- E: what Maya will not rename is skipped, in one line ------------------

    def test_locked_and_default_nodes_are_skipped_in_one_line(self):
        ok = cmds.group(em=True, n="okCube")
        lk = cmds.group(em=True, n="lockedCube")
        cmds.lockNode(lk, lock=True)
        try:
            with _Captured() as log:
                result = Naming.rename(
                    ["|persp", "lambert1", "time1", lk, ok], "**_x"
                )
            self.assertEqual(log.lines("Could not rename"), [])
            self.assertEqual(len(log.lines("Skipped")), 1, log.texts)
            self.assertEqual(result[-1], "okCube_x")
            self.assertEqual(len(result), 5)
        finally:
            cmds.lockNode(lk, lock=False)

    # -- F: a shape follows its transform -------------------------------------

    def test_a_shape_selected_with_its_transform_follows_it(self):
        """Regression: select -hi, then Suffix By Type named the shape
        'seatShape_GEO'; Maya carries a shape along with its transform."""
        seat = cmds.polyCube(n="seat")[0]
        cmds.select(seat, hi=True)
        Naming.suffix_by_type(cmds.ls(selection=True, long=True))
        self.assertEqual(cmds.listRelatives("|seat_GEO", shapes=True), ["seat_GEOShape"])

    # -- G: a numbered duplicate is re-affixed, not decorated -----------------

    def test_suffix_by_type_re_affixes_a_numbered_duplicate(self):
        """Regression: Ctrl+D of crate_GEO (crate_GEO1) came out crate_GEO1_GEO."""
        dup = cmds.polyCube(n="crate_GEO1")[0]
        idx = cmds.polyCube(n="wall_GEO_01")[0]
        ud, ui = _uuid(dup), _uuid(idx)
        Naming.suffix_by_type([dup, idx])
        self.assertEqual((_name(ud), _name(ui)), ("crate1_GEO", "wall_01_GEO"))

    # -- H: alphabetical numbering is re-runnable -----------------------------

    def test_alphabetical_location_is_re_runnable(self):
        """Regression: a second alphabetical run gave tile_C_C."""
        tiles = []
        for x in range(4):
            tiles.append(_uuid(cmds.group(em=True, n="tile")))
            cmds.move(x, 0, 0, cmds.ls(tiles[-1])[0])
        Naming.append_location_based_suffix(cmds.ls(tiles), alphabetical=True)
        first = [_name(u) for u in tiles]
        self.assertEqual(first, ["tile_A", "tile_B", "tile_C", "tile_D"])
        Naming.append_location_based_suffix(cmds.ls(tiles), alphabetical=True)
        self.assertEqual([_name(u) for u in tiles], first)

    def test_independent_groups_re_run_is_stable(self):
        """A kept type suffix: the number before it is this tool's, so a re-run
        groups the objects again instead of numbering each alone (collision)."""
        u1 = _uuid(cmds.group(em=True, n="Container_GRP"))
        u2 = _uuid(cmds.group(em=True, n="Container_GRP"))
        cmds.move(10, 0, 0, cmds.ls(u2)[0])
        for alphabetical, want in ((False, ("Container_01_GRP", "Container_02_GRP")),
                                   (True, ("Container_A_GRP", "Container_B_GRP"))):
            for _run in range(2):
                Naming.append_location_based_suffix(
                    cmds.ls([u1, u2]),
                    independent_groups=True,
                    strip_defined_suffixes=False,
                    alphabetical=alphabetical,
                    valid_suffixes=["_GRP"],
                )
                self.assertEqual((_name(u1), _name(u2)), want)

    def test_alphabetical_keeps_a_side_marker(self):
        """A letter the tool never wrote ('_L', '_R') is name content."""
        left, right = cmds.group(em=True, n="arm_L"), cmds.group(em=True, n="arm_R")
        cmds.move(1, 0, 0, right)
        ul, ur = _uuid(left), _uuid(right)
        Naming.append_location_based_suffix([left, right], alphabetical=True)
        self.assertEqual((_name(ul), _name(ur)), ("arm_L_A", "arm_R_B"))

    # -- K / L: every planned name is legalized -------------------------------

    def test_strip_leading_keeps_the_digit(self):
        """Regression: stripping 'a' from 'a1box' gave '_box'."""
        u = _uuid(cmds.group(em=True, n="a1box"))
        Naming.strip_chars(cmds.ls(u), 1)
        self.assertEqual(_name(u), "_1box")

    def test_every_planned_name_is_legalized(self):
        """A leading digit gets '_' (Maya itself drops it: '1box' -> 'box');
        a name with nothing legal left is skipped, with a warning."""
        u = _uuid(cmds.group(em=True, n="c4"))
        Naming.rename(cmds.ls(u), "1box")
        self.assertEqual(_name(u), "_1box")
        u = _uuid(cmds.group(em=True, n="c7"))
        with _Captured() as log:
            Naming.rename(cmds.ls(u), "---")
        self.assertEqual(_name(u), "c7")
        self.assertTrue(log.lines("no letters or digits"), log.texts)
        u = _uuid(cmds.group(em=True, n="c6"))
        with _Captured() as log:
            Naming.rename(cmds.ls(u), "crate_#")
        self.assertTrue(log.lines("not a counter"), log.texts)

    # -- M: the dry run predicts Maya's numbering -----------------------------

    def test_a_dry_run_predicts_the_live_numbering(self):
        """Regression: the preview read 'box, box, box'; live gave box1, box2."""
        cmds.group(em=True, n="box")
        cubes = [cmds.group(em=True, n=f"pCube{i}") for i in (1, 2, 3)]
        mat = cmds.shadingNode("lambert", asShader=True, n="mat")
        g = cmds.group(em=True, n="g")
        kid = cmds.group(em=True, n="kid", parent=g)
        targets = cubes + [mat, kid]
        uids = [_uuid(t) for t in targets]
        dry = Naming.rename(targets, "box", dry_run=True)
        with _Captured() as log:
            live = Naming.rename(cmds.ls(uids, long=True), "box")
        live_leafs = [_name(u) for u in uids]
        # DAG siblings of 'box' number on; the DG node collides with every
        # node name; the child of 'g' has no sibling 'box'.
        self.assertEqual(live_leafs, ["box1", "box2", "box3", "box4", "box"])
        self.assertEqual(dry, live_leafs)
        self.assertEqual([n.split("|")[-1] for n in live], live_leafs)
        self.assertEqual(log.lines("instead of"), [])

    def test_a_frozen_scope_survives_a_parent_rename(self):
        """Regression: Apply after the user renamed a parent found nothing in
        scope -- the frozen long names had gone stale."""
        g = cmds.group(em=True, n="grp")
        kid = cmds.group(em=True, n="kid", parent=g)
        cmds.select(kid)
        frozen = Naming.scope_objects()
        cmds.rename(g, "group_renamed_by_user")
        Naming.rename(frozen, "**_v2")
        self.assertTrue(cmds.objExists("|group_renamed_by_user|kid_v2"))

    # -- S3: Find --------------------------------------------------------------

    def test_find_matches_a_namespace_the_pattern_names(self):
        cmds.namespace(add="ns")
        cmds.group(em=True, n="ns:box")
        cmds.group(em=True, n="box")
        found, _searched = Naming.select_by_name("ns:*")
        self.assertEqual(found, ["|ns:box"])
        found, _searched = Naming.select_by_name("box")
        self.assertEqual(sorted(found), ["|box", "|ns:box"])
        # A regex group's '(?:' is no namespace: the leaf is matched as ever.
        found, _searched = Naming.select_by_name("(?:box)", regex=True)
        self.assertEqual(sorted(found), ["|box", "|ns:box"])

    def test_locators_only_finds_every_instance(self):
        loc = cmds.spaceLocator(n="aim")[0]
        inst = cmds.instance(loc, n="aimInst")[0]
        found, searched = Naming.select_by_name("aim*", locators_only=True)
        self.assertEqual(sorted(found), sorted(cmds.ls([loc, inst], long=True)))
        self.assertEqual(searched, 2)

    # -- Suffix By Location's order --------------------------------------------

    def _row(self):
        """east, west, north, up, center, far_west, near_east -- the probe's L1."""
        spots = {
            "east": (3, 0, 0), "west": (-3, 0, 0), "north": (0, 0, -3),
            "up": (0, 3, 0), "center": (0, 0, 0), "far_west": (-6, 0, 0),
            "near_east": (1, 0, 0),
        }
        uids = {}
        for name, pos in spots.items():
            node = cmds.group(em=True, n=name)
            cmds.move(*pos, node)
            uids[name] = _uuid(node)
        return uids

    def test_location_orders_along_an_axis_by_signed_position(self):
        uids = self._row()
        Naming.append_location_based_suffix(cmds.ls(list(uids.values())), order="x")
        got = {name: _name(u) for name, u in uids.items()}
        # x: far_west -6, west -3, then the three at x=0 by y, z (north z=-3
        # first, center, up y=3), near_east 1, east 3.
        self.assertEqual(got["far_west"], "far_west_01")
        self.assertEqual(got["west"], "west_02")
        self.assertEqual(got["north"], "north_03")
        self.assertEqual(got["center"], "center_04")
        self.assertEqual(got["up"], "up_05")
        self.assertEqual(got["near_east"], "near_east_06")
        self.assertEqual(got["east"], "east_07")

    def test_location_auto_numbers_along_the_row(self):
        row = []
        for x in (4, -2, 9, 0):
            node = cmds.group(em=True, n="post")
            cmds.move(x, 0.001 * x, 0, node)  # a row along X, a hair of Y noise
            row.append(_uuid(node))
        Naming.append_location_based_suffix(cmds.ls(row), order="auto")
        self.assertEqual([_name(u) for u in row], ["post_03", "post_01", "post_04", "post_02"])
        Naming.append_location_based_suffix(cmds.ls(row), order="auto", reverse=True)
        self.assertEqual([_name(u) for u in row], ["post_02", "post_04", "post_01", "post_03"])

    def test_location_distance_ties_break_by_position_not_selection(self):
        """Regression: east and west at equal distance numbered in selection order."""
        uids = self._row()
        for names in (("east", "west"), ("west", "east")):
            nodes = cmds.ls([uids[n] for n in names])
            Naming.append_location_based_suffix(nodes, strip_trailing_ints=True)
            self.assertEqual(_name(uids["west"]), "west_01")
            self.assertEqual(_name(uids["east"]), "east_02")

    def test_location_reads_the_pivot_not_the_bounding_box(self):
        """Regression: an empty group's bounding box sits at the origin, so it
        numbered first wherever it stood."""
        null = cmds.group(em=True, n="nullA")
        cmds.move(10, 0, 0, null)
        box = cmds.polyCube(n="boxC")[0]
        cmds.move(2, 0, 0, box)
        un, ub = _uuid(null), _uuid(box)
        Naming.append_location_based_suffix([null, box])
        self.assertEqual((_name(ub), _name(un)), ("boxC_01", "nullA_02"))


if __name__ == "__main__":
    unittest.main()
