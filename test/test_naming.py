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
