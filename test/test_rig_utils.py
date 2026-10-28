# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.rig_utils module

Tests for RigUtils class functionality including:
- Locator creation and management
- Attribute locking
- Group creation
- Rigging utilities (telescope, switch attributes)
- Joint chain operations
- Skin cluster operations
"""

import unittest
from mayatk.node_utils.attributes._attributes import Attributes
import maya.cmds as cmds

# Handle lazy loading of RigUtils
try:
    from mayatk.rig_utils._rig_utils import RigUtils
    from mayatk.rig_utils.telescope_rig import TelescopeRig
    from mayatk.xform_utils._xform_utils import XformUtils
except ImportError:
    import sys
    import os

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mayatk.rig_utils._rig_utils import RigUtils
    from mayatk.rig_utils.telescope_rig import TelescopeRig
    from mayatk.xform_utils._xform_utils import XformUtils

from base_test import MayaTkTestCase


class TestRigUtils(MayaTkTestCase):
    """Tests for RigUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        # Common test objects
        self.cube = self.create_test_cube("test_cube")
        self.sphere = self.create_test_sphere("test_sphere")

    def tearDown(self):
        """Clean up."""
        super().tearDown()

    def test_ik_handle_query_answers_for_a_node_that_is_gone(self):
        """A query must not abort its caller on a stale name.

        ``cmds.nodeType`` RAISES on an unresolvable path, so this reported a
        missing node by killing whatever was scanning: measured in a production
        export, ``smart_bake`` walked the export set node by node and died here
        at task 11 of 17 on a shape an earlier task had renamed — two minutes
        of texture work lost and no deliverable written.
        """
        self.assertEqual(RigUtils.get_ik_handles_for_joint("|no_such_node"), [])
        # A live non-joint is still simply "not a joint".
        self.assertEqual(RigUtils.get_ik_handles_for_joint(self.cube), [])

    def test_create_helper(self):
        """Test create_helper method."""
        # Test 1: Create locator
        loc = RigUtils.create_helper("helper_loc", helper_type="locator")
        self.assertNodeExists("helper_loc")
        self.assertTrue(cmds.objectType(str(loc)) == "transform")

        # Test 2: Create joint
        jnt = RigUtils.create_helper("helper_jnt", helper_type="joint")
        self.assertNodeExists("helper_jnt")
        self.assertEqual(cmds.nodeType(jnt), "joint")

        # Test 3: Create with parent
        child = RigUtils.create_helper("child_helper", parent=self.cube)
        self.assertEqual(
            (cmds.listRelatives(str(child), parent=True) or [None])[0], self.cube
        )

        # Test 4: Cleanup existing
        RigUtils.create_helper("cleanup_me")
        result = RigUtils.create_helper("cleanup_me", cleanup=True)
        self.assertFalse(cmds.objExists("cleanup_me"))
        self.assertIsNone(result)

    def test_create_group(self):
        """Test create_group method."""
        # Test 1: Empty group
        grp = RigUtils.create_group(name="empty_grp")
        self.assertNodeExists("empty_grp")
        self.assertEqual(len((cmds.listRelatives(str(grp), children=True) or [])), 0)

        # Test 2: Group with objects
        grp2 = RigUtils.create_group(objects=[self.cube, self.sphere], name="obj_grp")
        self.assertNodeExists("obj_grp")
        self.assertIn(self.cube, (cmds.listRelatives(str(grp2), children=True) or []))
        self.assertIn(self.sphere, (cmds.listRelatives(str(grp2), children=True) or []))

        # Test 3: Zero transforms
        # Move cube first
        cmds.move(10, 10, 10, self.cube)
        grp3 = RigUtils.create_group(
            objects=[self.cube], name="zero_grp", zero_translation=True
        )
        self.assertEqual(cmds.getAttr(f"{grp3}.tx"), 0)
        self.assertEqual(cmds.getAttr(f"{grp3}.ty"), 0)
        self.assertEqual(cmds.getAttr(f"{grp3}.tz"), 0)

    def test_create_locator(self):
        """Test create_locator method."""
        # Test 1: Basic creation
        RigUtils.create_locator(name="basic_loc")
        self.assertNodeExists("basic_loc")

        # Test 2: Scale
        loc_scaled = RigUtils.create_locator(name="scaled_loc", scale=5.0)
        self.assertEqual(cmds.getAttr(f"{loc_scaled}.scaleX"), 5.0)

        # Test 3: Position from object
        cmds.move(5, 5, 5, self.cube)
        loc_pos = RigUtils.create_locator(name="pos_loc", position=self.cube)
        pos = cmds.xform(loc_pos, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 5.0)

    def test_create_locator_size_sizes_the_shape_not_the_transform(self):
        """``size`` is the locator's DISPLAY: its shape's localScale.

        ``scale`` sizes the transform, which everything parented under the
        locator inherits; ``size`` draws the cross bigger and leaves the
        channels unit -- blendertk's ``create_locator(size=)``, the Empty's
        display size.
        """
        loc = RigUtils.create_locator(name="sized_loc", size=2.5)
        shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{shape}.localScale")[0]], [2.5] * 3
        )
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{loc}.scale")[0]], [1.0] * 3
        )

    def test_create_locator_at_object(self):
        """Test create_locator_at_object method."""
        # Setup
        cmds.move(10, 0, 0, self.cube)

        # Test 1: Basic rig
        RigUtils.create_locator_at_object(
            self.cube, grp_suffix="_GRP", loc_suffix="_LOC", obj_suffix="_GEO"
        )

        # Verify hierarchy: GRP -> LOC -> GEO
        grp_name = "test_cube_GRP"
        loc_name = "test_cube_LOC"
        geo_name = "test_cube_GEO"

        self.assertNodeExists(grp_name)
        self.assertNodeExists(loc_name)
        self.assertNodeExists(geo_name)

        grp = grp_name
        loc = loc_name
        geo = geo_name

        self.assertEqual((cmds.listRelatives(str(loc), parent=True) or [None])[0], grp)
        self.assertEqual((cmds.listRelatives(str(geo), parent=True) or [None])[0], loc)

        # Verify positions match
        self.assertAlmostEqual(
            cmds.xform(loc, query=True, worldSpace=True, translation=True)[0], 10.0
        )

    def test_create_locator_at_object_loc_scale_sizes_the_locator(self):
        """``loc_scale`` sizes the locator's DISPLAY; its channels stay zeroed.

        Bug: the locator was built scaled as a TRANSFORM, and the rig then wrote
        the object's world matrix over that and froze it -- so every rig drew a
        unit cross whatever the panel's Locator Scale said (measured in a fresh
        mayapy: localScale 1.0 for loc_scale=2.5). The cube is scaled, so the
        size also has to come through the freeze, which takes that scale off
        the locator.
        Fixed: 2026-10-10
        """
        cmds.xform(self.cube, scale=(2, 3, 1))
        RigUtils.create_locator_at_object(
            self.cube,
            loc_scale=2.5,
            grp_suffix="_GRP",
            loc_suffix="_LOC",
            obj_suffix="_GEO",
        )
        shape = cmds.listRelatives("test_cube_LOC", shapes=True, fullPath=True)[0]
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{shape}.localScale")[0]], [2.5] * 3
        )
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr("test_cube_LOC.scale")[0]], [1.0] * 3
        )

    def test_create_locator_at_object_selects_and_returns_every_locator(self):
        """Every locator built is selected and returned, not just the last.

        The select sat inside the per-object loop with ``replace=True``, so a
        multi-object rig left only the final locator selected, and the method
        returned nothing -- blendertk's twin returns its locators.
        Fixed: 2026-10-10
        """
        locs = RigUtils.create_locator_at_object(
            [self.cube, self.sphere],
            grp_suffix="_GRP",
            loc_suffix="_LOC",
            obj_suffix="_GEO",
        )
        expected = sorted(cmds.ls(["test_cube_LOC", "test_sphere_LOC"], long=True))
        self.assertIsInstance(locs, list)
        self.assertEqual(sorted(cmds.ls(locs, long=True)), expected)
        self.assertEqual(sorted(cmds.ls(selection=True, long=True)), expected)

    def test_create_locator_at_object_rotate_order_keeps_spin_on_one_channel(self):
        """A prop picked up and then spun about its own axis keys ONE channel.

        Bug (SOL_REPLACE_ASSEMBLY, SOCKET_WRENCH): every locator was built in
        Maya's default "xyz", where only an object-space X spin stays on one
        channel. The wrench was keyed to rx=98 to lift it, then spun about its
        own Z -- and rx/ry/rz all moved. ``rotate_order`` lets the rig put the
        spin axis first, so the same spin writes rz alone.
        """
        RigUtils.create_locator_at_object(self.cube, rotate_order="zyx")
        loc = "test_cube_LOC"
        self.assertEqual(cmds.getAttr(f"{loc}.rotateOrder", asString=True), "zyx")
        cmds.setAttr(f"{loc}.rotate", 98.3, -6.4, -0.15)
        before = cmds.getAttr(f"{loc}.rotate")[0]
        cmds.rotate(0, 0, 15, loc, relative=True, objectSpace=True, forceOrderXYZ=True)
        after = cmds.getAttr(f"{loc}.rotate")[0]
        self.assertAlmostEqual(after[0], before[0], places=4)
        self.assertAlmostEqual(after[1], before[1], places=4)
        self.assertAlmostEqual(after[2], before[2] + 15, places=4)

        with self.assertRaises(ValueError):
            RigUtils.create_locator_at_object(self.sphere, rotate_order="abc")

    def test_create_locator_at_group_preserves_position(self):
        """Verify locator is placed at the group's content center, not scene root.

        Bug: When create_locator_at_object was called on a group node whose own
        transforms were at origin (but whose children were offset), the locator
        was placed at scene root (0,0,0) instead of at the center of the group's
        children. This happened because bake_pivot was skipped for groups and
        get_manip_pivot_matrix returned the group's identity transform.
        Fixed: 2026-02-23
        """
        # Create a group at origin with a child mesh offset to (10, 0, 0)
        child = self.create_test_cube("child_mesh")
        cmds.move(10, 0, 0, child)
        grp = cmds.group(em=True, n="org_group")
        cmds.parent(child, grp)

        # Group is at origin, child is at (10, 0, 0) world
        grp_pos = cmds.xform(grp, q=True, ws=True, t=True)
        self.assertAlmostEqual(grp_pos[0], 0.0, places=3)

        # Run create_locator_at_object on the group
        RigUtils.create_locator_at_object(grp)

        # The locator should be at the child's center (~10, 0, 0), NOT at scene root
        loc = "org_group_LOC"
        loc_pos = cmds.xform(loc, q=True, ws=True, t=True)
        self.assertAlmostEqual(
            loc_pos[0],
            10.0,
            places=1,
            msg=f"Locator X should be ~10 (children center), got {loc_pos[0]}",
        )

    def test_create_locator_at_group_with_transforms(self):
        """Verify locator matches position for a group that has non-zero transforms."""
        # Create a group at (5, 10, 0) with a child at (0,0,0) relative
        child = self.create_test_cube("offset_child")
        grp = cmds.group(child, n="offset_group")
        cmds.move(5, 10, 0, grp)

        RigUtils.create_locator_at_object(grp)

        loc = "offset_group_LOC"
        loc_pos = cmds.xform(loc, q=True, ws=True, t=True)
        self.assertAlmostEqual(loc_pos[0], 5.0, places=1)
        self.assertAlmostEqual(loc_pos[1], 10.0, places=1)

    def test_create_locator_at_group_preserves_orientation(self):
        """Verify locator orientation matches the group's manip pivot orientation.

        Bug: For groups, bake_pivot was skipped (correct) but the code only
        read the group's world-transform matrix for orientation, missing any
        custom manipulator-pivot orientation.  The locator ended up with
        identity rotation even when the group had a visible orientation.
        Fixed: 2026-02-23
        """
        # Create a rotated group with a child
        child = self.create_test_cube("rot_child")
        grp = cmds.group(child, n="rotated_group")
        cmds.rotate(0, 45, 0, grp, ws=True)

        # Before calling create_locator_at_object, the group is rotated 45 Y
        grp_rot = cmds.xform(grp, q=True, ws=True, ro=True)
        self.assertAlmostEqual(grp_rot[1], 45.0, places=1)

        RigUtils.create_locator_at_object(grp)

        # The locator (or its parent GRP) should reflect the 45° Y rotation
        grp_node = "rotated_group_GRP"
        grp_rot_result = cmds.xform(grp_node, q=True, ws=True, ro=True)
        self.assertAlmostEqual(
            grp_rot_result[1],
            45.0,
            places=1,
            msg=f"Locator rig Y rotation should be ~45°, got {grp_rot_result[1]}",
        )

    # -- A driven object's motion survives its rig (BACKLOG 2026-10-10) -----

    _RIG_AFFIXES = dict(grp_suffix="_GRP", loc_suffix="_LOC", obj_suffix="_GEO")

    @staticmethod
    def _world_samples(uuid, frames=(1, 4, 7, 10)):
        """``{frame: world matrix}`` of the node *uuid* names, wherever it is."""
        out = {}
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            node = cmds.ls(uuid, long=True)[0]
            out[frame] = cmds.xform(node, query=True, worldSpace=True, matrix=True)
        cmds.currentTime(frames[0], edit=True)
        return out

    def assertWorldMotionKept(self, uuid, before, places=4):
        after = self._world_samples(uuid, tuple(before))
        for frame, matrix in before.items():
            for a, b in zip(matrix, after[frame]):
                self.assertAlmostEqual(
                    a,
                    b,
                    places=places,
                    msg=f"world moved at frame {frame}: {matrix} -> {after[frame]}",
                )

    def _keyed_cube(self, name="keyed_cube"):
        """A cube keyed tx 5 (f1) / 15 (f10) -- the entry's repro."""
        cube = self.create_test_cube(name)
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=5)
        cmds.setKeyframe(cube, attribute="translateX", time=10, value=15)
        cmds.currentTime(1, edit=True)
        return cube

    def test_create_locator_at_object_keeps_a_keyed_objects_motion(self):
        """A keyed object plays where it played before it was rigged.

        Bug: the rig froze the object while its curves still drove its
        channels, under a GRP holding the frame-1 pose -- measured in a fresh
        mayapy, a cube keyed tx 5 (f1) / 15 (f10) played at world x 10 / 20.
        Its channels are its animation, so they are kept, and its
        offsetParentMatrix cancels the pose the rig took -- blendertk's twin
        cancels it in the parent-inverse.
        Fixed: 2026-10-17
        """
        cube = self._keyed_cube()
        uuid = cmds.ls(cube, uuid=True)[0]
        before = self._world_samples(uuid)

        RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)

        self.assertWorldMotionKept(uuid, before)
        geo = cmds.ls(uuid, long=True)[0]
        self.assertEqual(geo, "|keyed_cube_GRP|keyed_cube_LOC|keyed_cube_GEO")
        # The curve still drives the channel it was keyed on.
        self.assertAlmostEqual(cmds.getAttr(f"{geo}.translateX", time=10), 15.0)
        # The rig still rigs: the locator sits zeroed on the object and
        # carries it.
        loc = "keyed_cube_LOC"
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{loc}.translate")[0]], [0.0] * 3
        )
        self.assertAlmostEqual(
            cmds.xform(loc, query=True, worldSpace=True, translation=True)[0], 5.0
        )
        cmds.setAttr(f"{loc}.translateY", 3)
        self.assertAlmostEqual(
            cmds.xform(geo, query=True, worldSpace=True, translation=True)[1], 3.0
        )

    def test_create_locator_at_object_keeps_a_keyed_rotation_inside_a_parent(self):
        """Rotate keys, a pivot off the origin and a static scale, under a
        turned, scaled parent: every frame's world matrix is unchanged, the
        GRP takes the object's place under that parent, and the locator sits
        on the pivot. ``bake_pivot`` used to write the keyed channels too (and
        move the points under them): the pivot alone shifted the cube 0.5.
        Fixed: 2026-10-17
        """
        cube = self.create_test_cube("spun_cube")
        cmds.setAttr(f"{cube}.rotatePivot", 0.5, 0.5, 0.5)
        cmds.setAttr(f"{cube}.scalePivot", 0.5, 0.5, 0.5)
        cmds.setAttr(f"{cube}.scale", 2, 1, 1)
        cmds.setAttr(f"{cube}.translate", 3, 2, 1)
        cmds.setKeyframe(cube, attribute="rotateY", time=1, value=30)
        cmds.setKeyframe(cube, attribute="rotateY", time=10, value=120)
        outer = cmds.group(cube, name="outer")
        cmds.setAttr(f"{outer}.translate", 0, 4, 0)
        cmds.setAttr(f"{outer}.rotate", 0, 45, 20)
        cmds.setAttr(f"{outer}.scale", 2, 2, 2)
        cmds.currentTime(1, edit=True)
        uuid = cmds.ls(cube, uuid=True)[0]
        before = self._world_samples(uuid)
        pivot = cmds.xform(
            cmds.ls(uuid, long=True)[0], query=True, worldSpace=True, rotatePivot=True
        )

        RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)

        self.assertWorldMotionKept(uuid, before)
        self.assertEqual(
            cmds.ls(uuid, long=True)[0],
            "|outer|spun_cube_GRP|spun_cube_LOC|spun_cube_GEO",
        )
        loc = cmds.xform("spun_cube_LOC", query=True, worldSpace=True, translation=True)
        for a, b in zip(loc, pivot):
            self.assertAlmostEqual(a, b, places=4)

    def test_create_locator_at_object_places_the_locator_in_a_metre_scene(self):
        """In a scene working in metres a keyed object's locator sits on its
        pivot, and a group's on its contents' centre: the pivot and the box
        are read in the working unit, the matrix placing the locator is in
        the internal centimetre -- mixed, the locator landed at 1/100 of the
        distance. Fixed: 2026-10-19
        """
        cmds.currentUnit(linear="m")
        self.addCleanup(cmds.currentUnit, linear="cm")
        cube = self._keyed_cube("metre_cube")
        cmds.setAttr(f"{cube}.rotatePivot", 0.5, 0.5, 0.5)
        uuid = cmds.ls(cube, uuid=True)[0]
        before = self._world_samples(uuid)
        pivot = cmds.xform(cube, query=True, worldSpace=True, rotatePivot=True)

        RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)

        self.assertWorldMotionKept(uuid, before)
        loc = cmds.xform(
            "metre_cube_LOC", query=True, worldSpace=True, translation=True
        )
        for a, b in zip(loc, pivot):
            self.assertAlmostEqual(a, b, places=4)

        child = self.create_test_cube("metre_child")
        cmds.setAttr(f"{child}.translate", 2, 1, 0)
        grp = cmds.group(child, name="metre_group")
        bb = cmds.exactWorldBoundingBox(child)
        centre = [(bb[i] + bb[i + 3]) / 2.0 for i in range(3)]

        RigUtils.create_locator_at_object(grp)

        loc = cmds.xform(
            "metre_group_LOC", query=True, worldSpace=True, translation=True
        )
        for a, b in zip(loc, centre):
            self.assertAlmostEqual(a, b, places=4)

    def _restore_frozen_rig(self, name, unit, pivot_source):
        """A rig built at (10, 5, 0) cm in a container 300 cm off the origin,
        frozen (the container not), its anchor restored twice with the working
        unit set to *unit*. Read back in centimetres: the GRP's and LOC's world
        rotate pivots, how far any geo vertex moved, what each restore returned.
        """
        container = cmds.group(empty=True, name=f"{name}_container", world=True)
        cmds.setAttr(f"{container}.translate", 0, 0, 300, type="double3")
        cube = cmds.polyCube(name=name)[0]
        cmds.move(10, 5, 0, cube, absolute=True)
        RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)
        cmds.parent(f"{name}_GRP", container)
        grp = f"|{container}|{name}_GRP"
        loc = f"{grp}|{name}_LOC"
        mesh = cmds.listRelatives(
            f"{loc}|{name}_GEO", shapes=True, type="mesh", fullPath=True
        )[0]
        before = cmds.xform(f"{mesh}.vtx[*]", query=True, worldSpace=True, t=True)
        XformUtils.freeze_transforms(grp, freeze_children=True)
        cmds.currentUnit(linear=unit)
        try:
            restored = [
                RigUtils.restore_rig_anchors(container, pivot_source=pivot_source)
                for _ in range(2)
            ]
        finally:
            cmds.currentUnit(linear="cm")
        after = cmds.xform(f"{mesh}.vtx[*]", query=True, worldSpace=True, t=True)
        return {
            "restored": restored,
            "grp_rp": cmds.xform(grp, query=True, worldSpace=True, rotatePivot=True),
            "loc_rp": cmds.xform(loc, query=True, worldSpace=True, rotatePivot=True),
            "moved": max(abs(a - b) for a, b in zip(before, after)),
        }

    def test_restore_rig_anchors_restores_in_a_metre_scene(self):
        """In a scene working in metres a frozen rig's anchor comes back where
        it was, as it does in centimetres: the anchor and the pivots are read
        and written in the working unit, the GRP's world position off its
        matrix in the internal centimetre -- mixed, a rig in a container 3 m
        off the origin was thrown ~300 m, and a second restore moved it again.
        Fixed: 2026-10-19
        """
        for source in ("bbox", "rp"):
            with self.subTest(pivot_source=source):
                cm = self._restore_frozen_rig(f"cm_{source}", "cm", source)
                m = self._restore_frozen_rig(f"m_{source}", "m", source)
                self.assertEqual(cm["restored"], [[f"cm_{source}_GRP"], []])
                self.assertEqual(m["restored"], [[f"m_{source}_GRP"], []])
                for key in ("grp_rp", "loc_rp"):
                    for got, ref, want in zip(m[key], cm[key], (10.0, 5.0, 0.0)):
                        self.assertAlmostEqual(ref, want, places=3, msg=key)
                        self.assertAlmostEqual(got, ref, places=3, msg=key)
                self.assertLess(cm["moved"], 1e-3)
                self.assertLess(m["moved"], 1e-3)

    def test_create_locator_at_object_keeps_a_constrained_object_in_place(self):
        """A constraint re-solves under its new parent: pinned, it already did."""
        driver = cmds.spaceLocator(name="driver")[0]
        cmds.setKeyframe(driver, attribute="translateZ", time=1, value=2)
        cmds.setKeyframe(driver, attribute="translateZ", time=10, value=-8)
        cmds.setKeyframe(driver, attribute="rotateX", time=1, value=10)
        cmds.setKeyframe(driver, attribute="rotateX", time=10, value=80)
        cmds.setAttr(f"{self.cube}.translate", 3, 1, 0)
        cmds.parentConstraint(driver, self.cube, maintainOffset=True)
        cmds.currentTime(1, edit=True)
        uuid = cmds.ls(self.cube, uuid=True)[0]
        before = self._world_samples(uuid)

        RigUtils.create_locator_at_object(self.cube, **self._RIG_AFFIXES)

        self.assertWorldMotionKept(uuid, before)

    def test_create_locator_at_keyed_group_keeps_its_contents_motion(self):
        """A keyed GROUP keeps its channels, and its contents are not flattened.
        Fixed: 2026-10-17
        """
        child = self.create_test_cube("riding_cube")
        cmds.setAttr(f"{child}.translate", 2, 0, 0)
        grp = cmds.group(child, name="moving_group")
        cmds.setAttr(f"{grp}.rotateY", 30)
        cmds.setKeyframe(grp, attribute="translateY", time=1, value=1)
        cmds.setKeyframe(grp, attribute="translateY", time=10, value=6)
        cmds.currentTime(1, edit=True)
        uuids = [cmds.ls(n, uuid=True)[0] for n in (child, grp)]
        before = [self._world_samples(u) for u in uuids]

        RigUtils.create_locator_at_object(grp, **self._RIG_AFFIXES)

        for uuid, samples in zip(uuids, before):
            self.assertWorldMotionKept(uuid, samples)
        child_path = cmds.ls(uuids[0], long=True)[0]
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{child_path}.translate")[0]],
            [2.0, 0.0, 0.0],
        )

    def test_create_locator_at_group_flattens_as_before(self):
        """Pinned: what a group's rig flattens is unchanged. Undriven contents
        flatten -- the group's channels and its child's zero, the points
        taking their places; with a KEYED child inside, nothing flattens
        (makeIdentity refuses the subtree), nothing moves, and the curves
        still drive.
        """
        still = self.create_test_cube("still_cube")
        cmds.setAttr(f"{still}.translate", -3, 1, 0)
        grp = cmds.group(still, name="still_group")
        cmds.setAttr(f"{grp}.translate", 1, 2, 3)
        cmds.setAttr(f"{grp}.rotateY", 30)
        uuids = [cmds.ls(n, uuid=True)[0] for n in (still, grp)]
        bbox = cmds.exactWorldBoundingBox(still)

        RigUtils.create_locator_at_object(grp, **self._RIG_AFFIXES)

        # Flattened: the transforms zero and the points take their places.
        flat = cmds.exactWorldBoundingBox(cmds.ls(uuids[0], long=True)[0])
        for a, b in zip(bbox, flat):
            self.assertAlmostEqual(a, b, places=4)
        for uuid in uuids:
            node = cmds.ls(uuid, long=True)[0]
            for attr in ("translate", "rotate"):
                self.assertEqual(
                    [round(v, 6) for v in cmds.getAttr(f"{node}.{attr}")[0]],
                    [0.0] * 3,
                    f"{node}.{attr} was not flattened",
                )

        keyed = self._keyed_cube("rider")
        static = self.create_test_cube("sitter")
        cmds.setAttr(f"{static}.translate", -3, 1, 0)
        grp = cmds.group(keyed, static, name="mixed_group")
        cmds.setAttr(f"{grp}.translate", 1, 2, 3)
        uuids = [cmds.ls(n, uuid=True)[0] for n in (keyed, static, grp)]
        before = [self._world_samples(u) for u in uuids]

        RigUtils.create_locator_at_object(grp, **self._RIG_AFFIXES)

        for uuid, samples in zip(uuids, before):
            self.assertWorldMotionKept(uuid, samples)
        self.assertAlmostEqual(
            cmds.getAttr(f"{cmds.ls(uuids[0], long=True)[0]}.translateX", time=10),
            15.0,
        )

    def test_create_locator_at_object_still_freezes_a_static_object(self):
        """Pinned: an undriven object is frozen as before -- channels zeroed,
        no offsetParentMatrix, nothing moved."""
        cmds.setAttr(f"{self.cube}.translate", 4, 1, 2)
        cmds.setAttr(f"{self.cube}.rotate", 10, 20, 30)
        uuid = cmds.ls(self.cube, uuid=True)[0]
        bbox = cmds.exactWorldBoundingBox(self.cube)

        RigUtils.create_locator_at_object(self.cube, **self._RIG_AFFIXES)

        geo = cmds.ls(uuid, long=True)[0]
        for attr in ("translate", "rotate"):
            self.assertEqual(
                [round(v, 6) for v in cmds.getAttr(f"{geo}.{attr}")[0]], [0.0] * 3
            )
        identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{geo}.offsetParentMatrix")],
            identity,
        )
        for a, b in zip(bbox, cmds.exactWorldBoundingBox(geo)):
            self.assertAlmostEqual(a, b, places=4)

    def test_remove_locator_hands_a_keyed_object_back_unmoved(self):
        """Rig then un-rig a keyed object: it plays as authored, under its own
        parent again, its offsetParentMatrix back where it was."""
        cube = self._keyed_cube()
        outer = cmds.group(cube, name="outer")
        cmds.setAttr(f"{outer}.rotateZ", 25)
        cmds.currentTime(1, edit=True)
        uuid = cmds.ls(cube, uuid=True)[0]
        before = self._world_samples(uuid)
        opm = cmds.getAttr(f"{cmds.ls(uuid, long=True)[0]}.offsetParentMatrix")

        locs = RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)
        RigUtils.remove_locator(locs)

        self.assertWorldMotionKept(uuid, before)
        geo = cmds.ls(uuid, long=True)[0]
        self.assertEqual(geo, "|outer|keyed_cube_GEO")
        for a, b in zip(opm, cmds.getAttr(f"{geo}.offsetParentMatrix")):
            self.assertAlmostEqual(a, b, places=6)

    def test_create_locator_at_object_keyed_rig_ships_through_a_baking_fbx(self):
        """The deliverable carries the rigged motion: an FBX written with Bake
        Animation on -- the Scene Exporter's take path, the bridges' sends --
        folds the offsetParentMatrix into the baked channels (measured: exact,
        Resample All on or off; a raw-curve write drops it).
        Fixed: 2026-10-17
        """
        import os

        import maya.mel as mel

        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            self.skipTest("fbxmaya plugin not available")
        cube = self._keyed_cube()
        cmds.setKeyframe(cube, attribute="rotateY", time=1, value=30)
        cmds.setKeyframe(cube, attribute="rotateY", time=10, value=120)
        cmds.currentTime(1, edit=True)
        frames = (1, 4, 7, 10)
        authored = self._world_samples(cmds.ls(cube, uuid=True)[0], frames)

        locs = RigUtils.create_locator_at_object(cube, **self._RIG_AFFIXES)

        fbx = self.temp_path(f"loc_rig_keyed_{os.getpid()}.fbx")
        mel_path = fbx.replace("\\", "/")
        try:
            cmds.select(
                cmds.ls(locs[0], long=True)[0].rsplit("|", 1)[0], hierarchy=True
            )
            mel.eval("FBXResetExport")
            mel.eval("FBXExportBakeComplexAnimation -v true")
            mel.eval("FBXExportBakeComplexStart -v 1")
            mel.eval("FBXExportBakeComplexEnd -v 10")
            mel.eval(f'FBXExport -f "{mel_path}" -s')
            cmds.file(new=True, force=True)
            mel.eval("FBXResetImport")
            mel.eval("FBXImportMode -v add")
            mel.eval("FBXImportSetTake -ti -1")
            cmds.file(fbx, i=True, type="FBX", ignoreVersion=True)
        finally:
            if os.path.isfile(fbx):
                os.remove(fbx)
        imported = cmds.ls("keyed_cube_GEO", long=True)
        self.assertEqual(len(imported), 1)
        self.assertWorldMotionKept(cmds.ls(imported[0], uuid=True)[0], authored)

    def test_remove_locator(self):
        """Test remove_locator method."""
        # Setup hierarchy: GRP -> LOC -> CUBE
        grp = cmds.group(em=True, n="test_GRP")
        loc = cmds.spaceLocator(n="test_LOC")[0]
        cmds.parent(loc, grp)
        cmds.parent(self.cube, loc)

        # Test removal
        RigUtils.remove_locator(loc)

        self.assertFalse(cmds.objExists("test_LOC"))
        self.assertTrue(cmds.objExists("test_cube"))
        # Cube should be parented to GRP now
        self.assertEqual(
            (cmds.listRelatives(str(self.cube), parent=True) or [None])[0], grp
        )

    def test_remove_locator_with_ambiguously_named_children(self):
        """Children whose names are NOT unique scene-wide survive removal.

        Bug: children were captured as PARTIAL paths (Maya's shortest-unique
        name), unparented to the world, and only then reparented -- by which
        point `LOC|CHILD` no longer resolved and cmds.parent raised
        `ValueError: No object matches name`, stranding the child at the
        world root with the locator already deleted.
        Fixed: 2026-08-27
        """
        # A second node of the same name makes the child's name ambiguous, so
        # Maya hands back `LOC|WIRE_BASE` instead of a bare `WIRE_BASE`.
        decoy_grp = cmds.group(empty=True, name="decoy_GRP")
        cmds.group(empty=True, name="WIRE_BASE", parent=decoy_grp)

        grp = cmds.group(empty=True, name="amb_GRP")
        loc = cmds.spaceLocator(name="amb_LOC")[0]
        cmds.parent(loc, grp)
        child = cmds.group(empty=True, name="WIRE_BASE")
        child = cmds.ls(cmds.parent(child, loc)[0], long=True)[0]
        cmds.move(3, 4, 5, child)
        pos_before = cmds.xform(child, q=True, ws=True, t=True)

        RigUtils.remove_locator(loc)

        self.assertFalse(cmds.objExists("amb_LOC"))
        # The child moved onto the GRP (no grandparent), not to the world.
        moved = [n for n in cmds.ls("WIRE_BASE", long=True) if "decoy" not in n]
        self.assertEqual(len(moved), 1, f"child lost or duplicated: {moved}")
        self.assertEqual(moved[0], "|amb_GRP|WIRE_BASE")
        # cmds.parent is absolute -- the world position must not shift.
        pos_after = cmds.xform(moved[0], q=True, ws=True, t=True)
        for a, b in zip(pos_before, pos_after):
            self.assertAlmostEqual(a, b, places=4)

    def test_remove_locator_reparents_to_grandparent(self):
        """TOP -> GRP -> LOC -> child: the child lands on TOP, GRP is culled."""
        decoy_grp = cmds.group(empty=True, name="decoy_GRP")
        cmds.group(empty=True, name="WIRE_BASE", parent=decoy_grp)

        top = cmds.group(empty=True, name="top_GRP")
        cmds.group(empty=True, name="mid_GRP", parent=top)
        loc = cmds.spaceLocator(name="deep_LOC")[0]
        cmds.parent(loc, "|top_GRP|mid_GRP")
        child = cmds.group(empty=True, name="WIRE_BASE")
        cmds.parent(child, loc)

        RigUtils.remove_locator(loc)

        self.assertFalse(cmds.objExists("deep_LOC"))
        # The now-empty intermediate group is removed.
        self.assertFalse(cmds.objExists("mid_GRP"))
        self.assertIn("|top_GRP|WIRE_BASE", cmds.ls("WIRE_BASE", long=True))

    def test_remove_locator_at_world_root_keeps_children(self):
        """A root-level locator releases its children to the world."""
        loc = cmds.spaceLocator(name="root_LOC")[0]
        cmds.parent(self.cube, loc)

        RigUtils.remove_locator(loc)

        self.assertFalse(cmds.objExists("root_LOC"))
        self.assertNodeExists("test_cube")
        self.assertIsNone(cmds.listRelatives("test_cube", parent=True))

    def test_attr_lock_state(self):
        """Test get_lock_state and set_lock_state via Attributes."""
        # Setup
        cmds.setAttr(f"{self.cube}.tx", lock=True)
        cmds.setAttr(f"{self.cube}.ry", lock=True)

        # Test Get
        state = Attributes.get_lock_state([self.cube])
        cube_state = state[self.cube]
        self.assertTrue(cube_state["tx"])
        self.assertTrue(cube_state["ry"])
        self.assertFalse(cube_state["tz"])

        # Test Unlock via Get
        Attributes.get_lock_state([self.cube], unlock=True)
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))

        # Test Set Bulk
        Attributes.set_lock_state(self.cube, translate=True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        self.assertTrue(cmds.getAttr(f"{self.cube}.ty", lock=True))
        self.assertTrue(cmds.getAttr(f"{self.cube}.tz", lock=True))

    def test_attr_lock_state_namespaced_duplicates(self):
        """Round-trip lock state for two transforms sharing a leaf name across
        namespaces — mirrors the referenced-module scene structure where
        ``short_name`` keying silently collided.
        """
        cmds.namespace(add="nsA")
        cmds.namespace(add="nsB")
        a = cmds.polyCube(n="nsA:dup_xform")[0]
        b = cmds.polyCube(n="nsB:dup_xform")[0]

        # Distinct lock states: A locks tx, B locks ry — no overlap so a
        # collision-driven overwrite would leave one of them wrongly locked
        # or wrongly unlocked after restore.
        cmds.setAttr(f"{a}.tx", lock=True)
        cmds.setAttr(f"{b}.ry", lock=True)

        with Attributes.temporarily_unlock([a, b]):
            self.assertFalse(cmds.getAttr(f"{a}.tx", lock=True))
            self.assertFalse(cmds.getAttr(f"{b}.ry", lock=True))

        # Original state must be restored independently for each.
        self.assertTrue(cmds.getAttr(f"{a}.tx", lock=True))
        self.assertFalse(cmds.getAttr(f"{a}.ry", lock=True))
        self.assertFalse(cmds.getAttr(f"{b}.tx", lock=True))
        self.assertTrue(cmds.getAttr(f"{b}.ry", lock=True))

    def test_attr_lock_state_same_name_under_different_parents(self):
        """Two transforms sharing a leaf name in the SAME namespace, under
        different parents.

        The namespaced case above never collided — ``leaf_name`` keeps the
        namespace, so ``nsA:dup`` and ``nsB:dup`` are distinct keys. Plain
        duplicates are not: ``|A|DUP`` and ``|B|DUP`` both key as ``DUP``, and
        a rig scene is full of them — a component name reused verbatim under
        each of several sibling rigs. With ``unlock=True`` the
        second read lands after the first node was unlocked, so the surviving
        record said "unlocked" and the restore dropped real locks —
        ``temporarily_unlock`` round-trips one node at a time to prevent it.
        """
        pa = cmds.group(empty=True, name="LKDUP_A")
        pb = cmds.group(empty=True, name="LKDUP_B")
        a = cmds.parent(cmds.group(empty=True, name="DUPNODE"), pa)[0]
        b = cmds.parent(cmds.group(empty=True, name="DUPNODE"), pb)[0]
        a, b = "|LKDUP_A|DUPNODE", "|LKDUP_B|DUPNODE"
        self.assertEqual(a.rsplit("|", 1)[-1], b.rsplit("|", 1)[-1])  # same leaf

        cmds.setAttr(f"{a}.tx", lock=True)
        cmds.setAttr(f"{a}.ry", lock=True)  # b stays fully unlocked

        with Attributes.temporarily_unlock([a, b]):
            self.assertFalse(cmds.getAttr(f"{a}.tx", lock=True))
            self.assertFalse(cmds.getAttr(f"{a}.ry", lock=True))

        self.assertTrue(cmds.getAttr(f"{a}.tx", lock=True), "a.tx lock was lost")
        self.assertTrue(cmds.getAttr(f"{a}.ry", lock=True), "a.ry lock was lost")
        self.assertFalse(cmds.getAttr(f"{b}.tx", lock=True), "b was wrongly locked")

    def _build_telescope(self, segment_count=3, end_pos=(0, 10, 0), **kwargs):
        """Build a telescope rig test scene: base at origin, end at *end_pos*,
        cubes stacked along Y. Returns (base, end, segments, rig, bundle)."""
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.xform(end, ws=True, t=end_pos)
        segments = []
        for i in range(segment_count):
            seg = cmds.polyCube(n=f"seg{i + 1}")[0]
            cmds.xform(seg, ws=True, t=(0, 1 + i * 2, 0))
            segments.append(seg)
        rig = TelescopeRig()
        kwargs.setdefault("collapsed_distance", 2.0)
        bundle = rig.setup_telescope_rig(base, end, segments, **kwargs)
        return base, end, segments, rig, bundle

    def test_telescope_rig_verbose_report(self):
        """The INFO-level run report must render without raising.

        Every other telescope test builds at the default WARNING level, where
        the run banner / build group / summary box are gated OFF — so a typo in
        one of them (a bundle field that doesn't exist, say) would never
        execute under test while breaking every real panel run, which logs at
        INFO. The Blender twin shipped exactly that bug. Build, then tear down,
        with the report actually rendering.
        """
        base = cmds.spaceLocator(n="v_base")[0]
        end = cmds.spaceLocator(n="v_end")[0]
        cmds.xform(end, ws=True, t=(0, 12, 0))
        segments = []
        for i in range(3):
            seg = cmds.polyCube(n=f"vseg{i + 1}", h=4)[0]
            cmds.xform(seg, ws=True, t=(0, 2 + i * 4, 0))
            segments.append(seg)

        rig = TelescopeRig(log_level="INFO")
        bundle = rig.setup_telescope_rig(base, end, segments, collapsed_distance=2.0)
        self.assertIsNotNone(bundle)
        self.assertTrue(rig.teardown(bundle))

        # Auto-created handles at INFO exercise the _create_handle log_link.
        auto_segments = []
        for i in range(3):
            seg = cmds.polyCube(n=f"aseg{i + 1}", h=4)[0]
            cmds.xform(seg, ws=True, t=(0, 2 + i * 3, 0))
            auto_segments.append(seg)
        auto = TelescopeRig(log_level="INFO").setup_telescope_rig(
            segments=auto_segments
        )
        self.assertEqual(len(auto.created_locators), 2)

    def test_setup_telescope_rig(self):
        """Basic 3-segment build: bundle, constraints, driven keys, locks."""
        base, end, segs, rig, bundle = self._build_telescope()

        # Distance node exists and reads WORLD-space distance.
        self.assertTrue(cmds.objExists(bundle.distance_node))
        self.assertAlmostEqual(
            cmds.getAttr(f"{bundle.distance_node}.distance"), 10.0, places=4
        )

        # Constraints: mutual locator aims + end parents + interior point/aim.
        self.assertTrue(cmds.listConnections(base, type="aimConstraint"))
        self.assertTrue(cmds.listConnections(end, type="aimConstraint"))
        for node in bundle.constraints:
            self.assertTrue(cmds.objExists(node))

        # Driven key on the interior segment's along-axis scale only.
        self.assertEqual(bundle.driven_plugs, [f"{segs[1]}.scaleY"])
        self.assertTrue(cmds.listConnections(f"{segs[1]}.scaleY", type="animCurve"))

        # Off-axis scales locked, driven axis left free.
        self.assertTrue(cmds.getAttr(f"{segs[1]}.scaleX", lock=True))
        self.assertTrue(cmds.getAttr(f"{segs[1]}.scaleZ", lock=True))
        self.assertFalse(cmds.getAttr(f"{segs[1]}.scaleY", lock=True))

        # User hierarchy untouched: no re-parenting, no helper locators.
        for seg in segs:
            self.assertIsNone(cmds.listRelatives(seg, parent=True))

    def test_telescope_rig_five_segments_cycle_free(self):
        """4+ segments must build cycle-free and distribute interiors evenly.

        Regression: interiors were point-constrained to neighboring segments
        (which hung under the adjacent helper locators), producing DG cycles
        for any build beyond three segments.
        """
        base, end, segs, rig, bundle = self._build_telescope(segment_count=5)

        self.assertFalse(cmds.cycleCheck(all=True, list=True) or [])

        # Build pose preserved (maintained offsets — no pop on build): every
        # segment still sits exactly where _build_telescope placed it.
        for i, seg in enumerate(segs):
            got = cmds.xform(seg, q=True, ws=True, t=True)
            for got_c, want_c in zip(got, (0.0, 1 + i * 2, 0.0)):
                self.assertAlmostEqual(got_c, want_c, places=4)

        # Extend the strut: each interior shifts by its fraction of the delta.
        cmds.xform(end, ws=True, t=(0, 16, 0))
        for i, seg in enumerate(segs[1:-1], start=1):
            fraction = i / 4.0
            expected_y = (1 + i * 2) + fraction * 6.0
            self.assertAlmostEqual(
                cmds.xform(seg, q=True, ws=True, t=True)[1], expected_y, places=3
            )

    def test_telescope_rig_scale_tracks_distance(self):
        """Interior scale = distance/initial, clamped at the collapse ratio.

        Regression: default driven-key tangents/infinity stopped the scale at
        the key range, tearing the rig open when extended past build pose.
        """
        base, end, segs, rig, bundle = self._build_telescope(
            segment_count=3, collapsed_distance=2.0
        )
        plug = f"{segs[1]}.scaleY"

        cmds.xform(end, ws=True, t=(0, 16, 0))  # beyond build pose
        self.assertAlmostEqual(cmds.getAttr(plug), 1.6, places=3)

        cmds.xform(end, ws=True, t=(0, 5, 0))  # mid travel
        self.assertAlmostEqual(cmds.getAttr(plug), 0.5, places=3)

        cmds.xform(end, ws=True, t=(0, 1, 0))  # past full collapse: clamp
        self.assertAlmostEqual(cmds.getAttr(plug), 0.2, places=3)

    def test_telescope_rig_world_space_driver(self):
        """distanceBetween must read world positions, not local .translate.

        Regression: the driver read local translate, so a parented locator
        measured the wrong distance.
        """
        base = cmds.spaceLocator(n="base_loc")[0]
        grp = cmds.group(base, n="base_grp")
        cmds.xform(grp, ws=True, t=(10, 0, 0))
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.xform(end, ws=True, t=(0, 10, 0))
        segs = [cmds.polyCube(n=f"seg{i}")[0] for i in (1, 2, 3)]

        rig = TelescopeRig()
        bundle = rig.setup_telescope_rig(base, end, segs, collapsed_distance=2.0)
        self.assertAlmostEqual(
            cmds.getAttr(f"{bundle.distance_node}.distance"), 14.1421, places=3
        )

    def test_telescope_rig_axis_parameter(self):
        """aim_axis remaps the driven scale channel and the lock set."""
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.xform(end, ws=True, t=(10, 0, 0))
        segs = []
        for i in range(3):
            seg = cmds.polyCube(n=f"seg{i + 1}")[0]
            cmds.xform(seg, ws=True, t=(1 + i * 2, 0, 0))
            segs.append(seg)

        rig = TelescopeRig()
        bundle = rig.setup_telescope_rig(
            base, end, segs, collapsed_distance=2.0, aim_axis="x"
        )
        self.assertEqual(bundle.driven_plugs, [f"{segs[1]}.scaleX"])
        self.assertTrue(cmds.getAttr(f"{segs[1]}.scaleY", lock=True))
        self.assertTrue(cmds.getAttr(f"{segs[1]}.scaleZ", lock=True))
        self.assertFalse(cmds.getAttr(f"{segs[1]}.scaleX", lock=True))

        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(
                base, end, segs, collapsed_distance=2.0, aim_axis="w"
            )

    def test_telescope_rig_failed_validation_leaves_scene_clean(self):
        """A refused build must not create or modify anything.

        Regression: the zero-distance check ran after constraints and
        re-parenting, leaving debris in the scene on failure.
        """
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]  # coincident with base
        segs = [cmds.polyCube(n=f"seg{i}")[0] for i in (1, 2, 3)]
        before = set(cmds.ls(long=True))

        rig = TelescopeRig()
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(base, end, segs)
        self.assertEqual(set(cmds.ls(long=True)), before)

        # Out-of-range collapsed_distance refuses just as cleanly.
        cmds.xform(end, ws=True, t=(0, 10, 0))
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(base, end, segs, collapsed_distance=15.0)
        self.assertEqual(set(cmds.ls(long=True)), before)

        # Duplicate / nonexistent segments and overlapping roles refuse cleanly.
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(
                base, end, [segs[0], segs[0], segs[1]], collapsed_distance=2.0
            )
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(
                base, end, [segs[0], "does_not_exist", segs[1]], collapsed_distance=2.0
            )
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(base, base, segs, collapsed_distance=2.0)
        self.assertEqual(set(cmds.ls(long=True)), before)

        # A locked driven channel is caught pre-flight, not mid-build.
        cmds.setAttr(f"{segs[1]}.scaleY", lock=True)
        with self.assertRaises(ValueError):
            rig.setup_telescope_rig(base, end, segs, collapsed_distance=2.0)
        self.assertEqual(set(cmds.ls(long=True)), before)

    def test_telescope_rig_teardown(self):
        """teardown removes every created node and restores locks/scales."""
        base, end, segs, rig, bundle = self._build_telescope()
        cmds.xform(end, ws=True, t=(0, 16, 0))  # leave the rig posed

        self.assertTrue(rig.teardown())

        self.assertFalse(cmds.objExists(bundle.distance_node))
        for node in bundle.constraints:
            self.assertFalse(cmds.objExists(node))
        self.assertFalse(
            cmds.listConnections(f"{segs[1]}.scaleY", type="animCurve") or []
        )
        # Locks released, build-pose scale restored, geometry still present.
        self.assertFalse(cmds.getAttr(f"{segs[1]}.scaleX", lock=True))
        self.assertAlmostEqual(cmds.getAttr(f"{segs[1]}.scaleY"), 1.0, places=4)
        for node in (base, end, *segs):
            self.assertTrue(cmds.objExists(node))

        # Nothing left to tear down on a second call.
        self.assertFalse(rig.teardown())

    def test_telescope_rig_two_segment_strut(self):
        """Two segments build a sliding strut — no distance node, no keys.

        The common case: a hydraulic/strut pair that slides rather than
        stretches. Regression — this used to create a distanceBetween node
        that drove nothing and range-check a collapsed_distance it never used.
        """
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.xform(end, ws=True, t=(0, 10, 0))
        outer = cmds.polyCube(n="outer")[0]
        inner = cmds.polyCube(n="inner")[0]
        cmds.xform(inner, ws=True, t=(0, 4, 0))

        rig = TelescopeRig()
        bundle = rig.setup_telescope_rig(base, end, [outer, inner])

        self.assertIsNone(bundle.distance_node)
        self.assertEqual(bundle.driven_plugs, [])
        self.assertFalse(cmds.ls(type="distanceBetween") or [])
        self.assertFalse(cmds.cycleCheck(all=True, list=True) or [])

        # Both halves ride their locator and still carry the shear locks.
        self.assertTrue(cmds.getAttr(f"{outer}.scaleX", lock=True))
        self.assertFalse(cmds.getAttr(f"{outer}.scaleY", lock=True))

        # Extending the strut slides the inner half with the end locator.
        cmds.xform(end, ws=True, t=(0, 18, 0))
        self.assertAlmostEqual(
            cmds.xform(inner, q=True, ws=True, t=True)[1], 12.0, places=3
        )
        # ...and the outer half stays put on the base.
        self.assertAlmostEqual(
            cmds.xform(outer, q=True, ws=True, t=True)[1], 0.0, places=3
        )

    def test_telescope_rig_creates_missing_locators(self):
        """Omitted handles are built at the strut's outer ends.

        Segments alone are a valid selection — the rig no longer requires the
        artist to hand-author locators before it will build.
        """
        segments = []
        for i in range(3):
            seg = cmds.polyCube(n=f"seg{i + 1}", h=4)[0]
            cmds.xform(seg, ws=True, t=(0, 2 + i * 3, 0))
            segments.append(seg)

        rig = TelescopeRig()
        bundle = rig.setup_telescope_rig(segments=segments)

        self.assertEqual(len(bundle.created_locators), 2)
        for locator in bundle.created_locators:
            self.assertTrue(cmds.objExists(locator))
        # Base at the bottom of seg1 (y=0), end at the top of seg3 (y=10).
        self.assertAlmostEqual(
            cmds.xform(bundle.base_locator, q=True, ws=True, t=True)[1], 0.0, places=3
        )
        self.assertAlmostEqual(
            cmds.xform(bundle.end_locator, q=True, ws=True, t=True)[1], 10.0, places=3
        )
        self.assertAlmostEqual(bundle.initial_distance, 10.0, places=3)
        self.assertFalse(cmds.cycleCheck(all=True, list=True) or [])

        # Build pose preserved — nothing pops when the handles appear.
        for i, seg in enumerate(segments):
            self.assertAlmostEqual(
                cmds.xform(seg, q=True, ws=True, t=True)[1], 2 + i * 3, places=3
            )

        # Teardown takes the locators it made with it, and leaves the geometry.
        rig.teardown()
        for locator in bundle.created_locators:
            self.assertFalse(cmds.objExists(locator))
        for seg in segments:
            self.assertTrue(cmds.objExists(seg))

    def test_telescope_rig_auto_handles_measure_grouped_segments(self):
        """Auto placement measures a segment whose mesh is nested in a group.

        Regression: the geometry probe only looked at the node's DIRECT shapes,
        so an imported segment (group -> transform -> mesh) measured as
        shapeless — the handle fell back to the group's pivot and the collapse
        distance to an even split. `exactWorldBoundingBox` handles the group
        fine; only the guard in front of it was wrong.
        """
        groups = []
        for i in range(3):
            cube = cmds.polyCube(n=f"gseg{i + 1}", h=4)[0]
            cmds.xform(cube, ws=True, t=(0, 2 + i * 3, 0))
            # Pivot deliberately away from the geometry: if the probe falls
            # back to the pivot the placement is visibly wrong.
            grp = cmds.group(cube, n=f"gseg{i + 1}_grp")
            cmds.xform(grp, ws=True, piv=(0, 0, 0))
            groups.append(grp)

        bundle = TelescopeRig().setup_telescope_rig(segments=groups)

        self.assertAlmostEqual(
            cmds.xform(bundle.base_locator, q=True, ws=True, t=True)[1], 0.0, places=3
        )
        self.assertAlmostEqual(
            cmds.xform(bundle.end_locator, q=True, ws=True, t=True)[1], 10.0, places=3
        )
        self.assertAlmostEqual(bundle.initial_distance, 10.0, places=3)
        self.assertAlmostEqual(bundle.collapsed_distance, 4.0, places=3)

    def test_telescope_rig_refuses_to_rig_an_already_rigged_node(self):
        """A second build on the same nodes is refused, not stacked."""
        base, end, segs, rig, bundle = self._build_telescope()
        before = set(cmds.ls(long=True))

        with self.assertRaises(ValueError) as caught:
            TelescopeRig().setup_telescope_rig(base, end, segs, collapsed_distance=2.0)
        self.assertIn("already carry a telescope rig", str(caught.exception))
        self.assertEqual(set(cmds.ls(long=True)), before)

        # Overlapping on a single segment is enough to be refused.
        other = cmds.polyCube(n="other_seg")[0]
        cmds.xform(other, ws=True, t=(0, 20, 0))
        with self.assertRaises(ValueError):
            TelescopeRig().setup_telescope_rig(
                segments=[segs[0], other], collapsed_distance=2.0
            )

        # Once removed, the same nodes rig again cleanly.
        rig.teardown()
        self.assertIsNotNone(
            TelescopeRig().setup_telescope_rig(base, end, segs, collapsed_distance=2.0)
        )

    def test_telescope_rig_auto_collapsed_distance(self):
        """collapsed_distance=None measures the longest segment.

        Fully nested, a telescope is as long as its longest tube — so that
        length is the base-to-end distance at full retraction.
        """
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.xform(end, ws=True, t=(0, 12, 0))
        segments = []
        for i, height in enumerate((6.0, 4.0, 4.0)):
            seg = cmds.polyCube(n=f"seg{i + 1}", h=height)[0]
            cmds.xform(seg, ws=True, t=(0, 2 + i * 4, 0))
            segments.append(seg)

        bundle = TelescopeRig().setup_telescope_rig(base, end, segments)
        self.assertAlmostEqual(bundle.collapsed_distance, 6.0, places=3)

        # Collapsing to that distance drives the interior to its nested size.
        cmds.xform(end, ws=True, t=(0, 6, 0))
        self.assertAlmostEqual(
            cmds.getAttr(f"{segments[1]}.scaleY"), 6.0 / 12.0, places=3
        )

    def test_telescope_rig_bundle_recovers_from_the_scene(self):
        """The build stamps its record on the base locator.

        Without this the bundle only lives on the Python instance, so
        reopening the panel (or the scene) makes teardown unreachable.
        """
        base, end, segs, rig, bundle = self._build_telescope()

        # A fresh class — no instance state — still finds the rig.
        found = TelescopeRig.find_bundles([segs[1]])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0], bundle)
        self.assertEqual(TelescopeRig.find_bundles([base]), found)
        self.assertEqual(TelescopeRig.find_bundles(["test_cube"]), [])

        # ...and tears it down from that recovered record alone.
        self.assertTrue(TelescopeRig().teardown(found[0]))
        self.assertFalse(cmds.objExists(bundle.distance_node))
        self.assertEqual(TelescopeRig.scene_bundles(), [])

    def test_telescope_rig_partition_selection_reads_the_selection_order(self):
        """The roles tentacle's Telescope Rig panel builds from: a leading /
        trailing locator is the base / end, the rest are the segments."""
        base = cmds.spaceLocator(n="part_base")[0]
        end = cmds.spaceLocator(n="part_end")[0]
        segs = [cmds.polyCube(n=f"part_seg{i}")[0] for i in range(2)]
        grp = cmds.group(cmds.polyCube(n="part_inner")[0], n="part_grp")

        cmds.select(base, *segs, end)
        self.assertEqual(TelescopeRig.partition_selection(), (base, segs, end))
        cmds.select(segs[0], grp)  # a group whose mesh hangs below is a segment
        self.assertEqual(
            TelescopeRig.partition_selection(), (None, [segs[0], grp], None)
        )
        cmds.select(clear=True)
        self.assertEqual(TelescopeRig.partition_selection(), (None, [], None))

    def test_telescope_rig_teardown_of_two_rigs_is_one_undo_step(self):
        """A Remove over a selection spanning two rigs: one call, one Ctrl+Z."""
        rigs = []
        for k in range(2):
            segs = []
            for i in range(2):
                seg = cmds.polyCube(n=f"pair{k}_seg{i}", h=4)[0]
                cmds.xform(seg, ws=True, t=(k * 10, 2 + i * 3, 0))
                segs.append(seg)
            rigs.append(TelescopeRig().setup_telescope_rig(segments=segs, name=f"pair{k}"))

        self.assertTrue(TelescopeRig().teardown(rigs))
        self.assertEqual(TelescopeRig.scene_bundles(), [])
        self.assertFalse(cmds.ls("pair*_LOC"))

        cmds.undo()  # both rigs come back together
        self.assertEqual(
            sorted(b.name for b in TelescopeRig.scene_bundles()), ["pair0", "pair1"]
        )

    def test_create_switch(self):
        """Test create_switch method via Attributes."""
        # Test 1: Bool — production returns the plug string
        # ("<node>.<attr>"); query type via cmds.getAttr.
        attr = Attributes.create_switch(self.cube, "mySwitch")
        self.assertTrue(
            cmds.attributeQuery("mySwitch", node=str(self.cube), exists=True)
        )
        self.assertEqual(cmds.getAttr(str(attr), type=True), "bool")

        # Test 2: Weighted
        attr2 = Attributes.create_switch(self.cube, "myWeight", weighted=True)
        self.assertEqual(cmds.getAttr(str(attr2), type=True), "double")

    def test_connect_switch_to_constraint(self):
        """Test connect_switch_to_constraint method."""
        # Setup
        target1 = cmds.spaceLocator(n="t1")[0]
        target2 = cmds.spaceLocator(n="t2")[0]
        const = cmds.parentConstraint(target1, target2, self.cube)[0]

        # Test 1: Weighted blend (2 targets)
        res = RigUtils.connect_switch_to_constraint(
            const, attr_name="blend_switch", weighted=True
        )
        self.assertIn("reverse_node", res)
        self.assertTrue(
            cmds.attributeQuery("blend_switch", node=str(self.cube), exists=True)
        )

        # Test 2: Enum switch (add 3rd target)
        target3 = cmds.spaceLocator(n="t3")[0]
        const2 = cmds.parentConstraint(target1, target2, target3, self.sphere)[0]
        res2 = RigUtils.connect_switch_to_constraint(const2, attr_name="enum_switch")
        self.assertEqual(cmds.getAttr(f"{self.sphere}.enum_switch", type=True), "enum")
        self.assertIn("condition_node_0", res2)

    def test_joint_chain_ops(self):
        """Test get_joint_chain_from_root and invert_joint_chain."""
        # Setup chain: j1 -> j2 -> j3
        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, 0, 0), n="j1")
        j2 = cmds.joint(p=(0, 1, 0), n="j2")
        j3 = cmds.joint(p=(0, 2, 0), n="j3")

        # Test Get Chain. get_joint_chain_from_root returns full DAG paths so the
        # chain stays unambiguous when joint leaf names collide (e.g. mirrored
        # rigs); compare by leaf name since j1/j2/j3 are uniquely named here.
        chain = RigUtils.get_joint_chain_from_root(j1)
        self.assertEqual([c.split("|")[-1] for c in chain], [j1, j2, j3])

        # Test Invert Chain
        inv_chain = RigUtils.invert_joint_chain(j1, keep_original=True)
        self.assertEqual(len(inv_chain), 3)

        root = inv_chain[0]
        # Production returns plain strings — query world translation via cmds.
        root_t = cmds.xform(str(root), q=True, ws=True, t=True)
        j3_t = cmds.xform(str(j3), q=True, ws=True, t=True)
        for a, b in zip(root_t, j3_t):
            self.assertAlmostEqual(a, b, places=4)

    def test_rebind_skin_clusters(self):
        """Test rebind_skin_clusters method."""
        # Setup skinned mesh
        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, -1, 0))
        j2 = cmds.joint(p=(0, 1, 0))
        skin = cmds.skinCluster(j1, j2, self.cube)[0]
        # Compare via UUID — rebind preserves the cluster's *name* but
        # produces a new underlying node. PyNode equality on same-name
        # nodes can re-resolve and falsely pass the assertion.
        old_uuid = cmds.ls(str(skin), uuid=True)[0]

        # Test
        result = RigUtils.rebind_skin_clusters([self.cube])

        # Verify skinCluster still exists (it's a new one, but exists)
        new_skin = cmds.ls(cmds.listHistory(self.cube), type="skinCluster")
        self.assertTrue(new_skin)
        new_uuid = cmds.ls(str(new_skin[0]), uuid=True)[0]
        self.assertNotEqual(new_uuid, old_uuid)  # Should be a new node

        # Summary reports the success and nothing else.
        self.assertEqual(result["rebound"], [self.cube])
        self.assertEqual(result["no_skin_cluster"], [])
        self.assertEqual(result["wrong_type"], [])
        self.assertEqual(result["failed"], [])

    def test_rebind_skin_clusters_leaves_no_weight_files(self):
        """The weights a rebind round-trips through are scratch: they go to a
        scoped ``ptk.TempArtifacts`` folder removed when the rebind returns,
        never a fixed ``<maya tmp>/skin_rebind_weights`` folder that kept every
        mesh's XML forever. Added: 2026-10-13"""
        import os

        import pythontk as ptk

        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, -1, 0))
        j2 = cmds.joint(p=(0, 1, 0))
        cmds.skinCluster(j1, j2, self.cube)
        legacy = os.path.join(cmds.internalVar(userTmpDir=True), "skin_rebind_weights")
        before = set(os.listdir(legacy)) if os.path.isdir(legacy) else set()

        result = RigUtils.rebind_skin_clusters([self.cube])

        self.assertEqual(result["rebound"], [self.cube])
        after = set(os.listdir(legacy)) if os.path.isdir(legacy) else set()
        self.assertEqual(after - before, set(), "weights left in Maya's tmp dir")
        store = os.path.join(ptk.TempArtifacts.root(), "skin_rebind")
        left = [
            f
            for f in (os.listdir(store) if os.path.isdir(store) else [])
            if not f.startswith(".")
        ]
        self.assertEqual(left, [], "the scratch folder outlived the rebind")

    def test_rebind_skin_clusters_keeps_the_skinning_settings(self):
        """A dual-quaternion skin rebinds dual-quaternion, its non-rigid
        support and influence cap kept. Bug: the cluster was rebuilt with
        ``skinMethod=0`` and the defaults, so a DQS skin came back classic
        linear and every bent vertex moved. Fixed: 2026-10-19"""
        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, -1, 0))
        j2 = cmds.joint(p=(0, 1, 0))
        skin = cmds.skinCluster(j1, j2, self.cube, skinMethod=1)[0]
        cmds.setAttr(f"{skin}.dqsSupportNonRigid", True)
        cmds.setAttr(f"{skin}.maxInfluences", 2)
        cmds.setAttr(f"{skin}.maintainMaxInfluences", True)

        result = RigUtils.rebind_skin_clusters([self.cube])

        self.assertEqual(result["rebound"], [self.cube])
        new_skin = cmds.ls(cmds.listHistory(self.cube), type="skinCluster")[0]
        self.assertEqual(cmds.getAttr(f"{new_skin}.skinningMethod"), 1)
        self.assertTrue(cmds.getAttr(f"{new_skin}.dqsSupportNonRigid"))
        self.assertEqual(cmds.getAttr(f"{new_skin}.maxInfluences"), 2)
        self.assertTrue(cmds.getAttr(f"{new_skin}.maintainMaxInfluences"))

    def test_rebind_skin_clusters_no_skin_cluster(self):
        """A mesh with no skinCluster is reported under 'no_skin_cluster'."""
        result = RigUtils.rebind_skin_clusters([self.sphere])
        self.assertEqual(result["rebound"], [])
        self.assertEqual(result["no_skin_cluster"], [self.sphere])
        self.assertEqual(result["wrong_type"], [])
        self.assertEqual(result["failed"], [])

    def test_rebind_skin_clusters_wrong_type(self):
        """A non-mesh-transform input is reported under 'wrong_type'."""
        loc = cmds.spaceLocator(name="rebind_loc")[0]
        result = RigUtils.rebind_skin_clusters([loc])
        self.assertEqual(result["rebound"], [])
        self.assertEqual(result["no_skin_cluster"], [])
        self.assertEqual(result["wrong_type"], [loc])
        self.assertEqual(result["failed"], [])


class TestRebindKeepsBindPoses(MayaTkTestCase):
    """A rebind restores the cluster's ORIGINAL bindPreMatrix, so it must keep
    the dagPose that records that bind, and leave the poses of other skins on
    the joints alone. Recreated by ``cmds.skinCluster``, the cluster got a
    fresh pose at the CURRENT pose instead: 2.87 units off its restored
    bindPreMatrix with a joint posed, and the original pose left orphaned.
    Added: 2026-10-17"""

    def _rebind(self, how):
        from mayatk.rig_utils.skinning import SkinUtils
        from rig_metrics import BindPoseMetrics

        module = cmds.createNode("transform", name="rebind_GRP")
        cmds.select(clear=True)
        joints = [
            cmds.joint(p=p, name=f"rebindJnt{i + 1}")
            for i, p in enumerate([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        ]
        cmds.parent(joints[0], module)
        skins = {}
        for name, z in (("rebindA", 0), ("rebindB", 3)):
            mesh = cmds.polyCylinder(name=name, h=10, sx=8, sy=6, ax=(1, 0, 0))[0]
            cmds.move(0, 0, z, mesh)
            skins[name] = SkinUtils.bind(mesh, joints, name=f"{name}_skin")
        if how == "module":
            cmds.setAttr(f"{module}.translateY", 2)
            cmds.setAttr(f"{module}.rotateZ", 20)
        elif how == "joint":
            cmds.setAttr(f"{joints[1]}.rotateZ", 35)
        other = BindPoseMetrics.record("rebindB_skin_pose")

        result = RigUtils.rebind_skin_clusters(["rebindA"])

        self.assertEqual(result["rebound"], ["rebindA"])
        rebound = SkinUtils.get_skin_cluster("rebindA")
        self.assertEqual(BindPoseMetrics.pose(rebound), "rebindA_skin_pose")
        self.assertLess(BindPoseMetrics.error(rebound), 1e-6)
        self.assertEqual(BindPoseMetrics.pose(skins["rebindB"]), "rebindB_skin_pose")
        self.assertEqual(BindPoseMetrics.record("rebindB_skin_pose"), other)
        self.assertEqual(
            sorted(cmds.ls(type="dagPose")), ["rebindA_skin_pose", "rebindB_skin_pose"]
        )

    def test_a_posed_joint_rebinds_into_the_original_bind_pose(self):
        self._rebind("joint")

    def test_a_moved_module_rebinds_into_the_original_bind_pose(self):
        self._rebind("module")

    def test_at_rest_the_rebind_keeps_the_original_bind_pose(self):
        self._rebind("rest")


class TestRiggingPanelOperations(MayaTkTestCase):
    """The operations the Rigging panel (tentacle's ``RiggingSlots``) calls in
    both hosts: blendertk's twins take the same calls."""

    def test_add_locator_is_display_sized_ordered_and_selected(self):
        loc = RigUtils.add_locator(size=2.5, rotate_order="zyx")
        shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
        self.assertEqual(
            [round(v, 6) for v in cmds.getAttr(f"{shape}.localScale")[0]], [2.5] * 3
        )
        self.assertEqual([round(v, 6) for v in cmds.getAttr(f"{loc}.scale")[0]], [1.0] * 3)
        self.assertEqual(cmds.getAttr(f"{loc}.rotateOrder", asString=True), "zyx")
        self.assertEqual(cmds.ls(selection=True), cmds.ls(loc))

    def test_add_locator_refuses_a_bad_rotate_order(self):
        with self.assertRaises(ValueError):
            RigUtils.add_locator(rotate_order="xxy")

    def test_utility_nodes_are_sorted_and_unknown_ones_refused(self):
        self.assertEqual(list(RigUtils.UTILITY_NODES), sorted(RigUtils.UTILITY_NODES))
        self.assertEqual(
            set(RigUtils.UTILITY_NODES), {"Cluster", "IK Handle", "Joints", "Lattice"}
        )
        with self.assertRaises(ValueError):
            RigUtils.create_utility_node("Armature")

    def test_a_lattice_surrounds_the_selection(self):
        cmds.select(self.create_test_cube("lattice_cube"))
        nodes = RigUtils.create_utility_node("Lattice")
        self.assertTrue(cmds.ls(nodes, type="transform"))
        self.assertTrue(cmds.ls(type="ffd"))

    def test_auto_rig_names_the_maya_riggers(self):
        self.assertEqual(RigUtils.AUTO_RIGS, ("Quick Rig", "HumanIK"))
        with self.assertRaises(ValueError):
            RigUtils.auto_rig("Generate Rig")

    def test_remove_locator_returns_what_it_removed(self):
        loc = RigUtils.create_locator_at_object(self.create_test_cube("rigged_cube"))[0]
        other = self.create_test_cube("not_a_locator")
        self.assertEqual(RigUtils.remove_locator(other), [])  # not a locator
        self.assertEqual(RigUtils.remove_locator(loc), [loc])
        self.assertFalse(cmds.objExists(loc))
        cmds.select(clear=True)
        self.assertEqual(RigUtils.remove_locator(None), [])

    def test_remove_locator_takes_the_selection(self):
        loc = RigUtils.create_locator_at_object(self.create_test_cube("sel_cube"))[0]
        cmds.select(loc)
        self.assertEqual(RigUtils.remove_locator(), [loc])

    def test_create_locator_at_object_takes_blenders_group_display(self):
        cube = self.create_test_cube("display_cube")
        locs = RigUtils.create_locator_at_object(cube, group_display="ring")
        self.assertEqual(len(locs), 1)

    def test_lock_transforms_locks_the_scope_named(self):
        cube = cmds.ls(self.create_test_cube("lock_cube"), long=True)[0]
        nodes, attrs = RigUtils.lock_transforms(cube, lock=True, scope="rotate")
        self.assertEqual((nodes, attrs), ([cube], ["rx", "ry", "rz"]))
        self.assertTrue(cmds.getAttr(f"{cube}.ry", lock=True))
        self.assertFalse(cmds.getAttr(f"{cube}.tx", lock=True))
        RigUtils.lock_transforms(cube, lock=False, scope="all")
        self.assertFalse(cmds.getAttr(f"{cube}.ry", lock=True))

    def test_lock_transforms_takes_the_selection(self):
        cube = self.create_test_cube("sel_lock_cube")
        cmds.select(cube)
        nodes, attrs = RigUtils.lock_transforms(None, lock=True, scope="all")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(len(attrs), 9)
        self.assertTrue(cmds.getAttr(f"{cube}.sz", lock=True))
        cmds.select(clear=True)
        self.assertEqual(RigUtils.lock_transforms(None), ([], []))

    def test_lock_transforms_refuses_an_unknown_scope(self):
        with self.assertRaises(ValueError):
            RigUtils.lock_transforms(self.create_test_cube("scope_cube"), scope="xyz")


if __name__ == "__main__":
    unittest.main(verbosity=2)
