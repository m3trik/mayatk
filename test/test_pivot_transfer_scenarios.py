import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    from base_test import MayaTkTestCase, skipIfBatch
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase, skipIfBatch
from mayatk.xform_utils._xform_utils import XformUtils


class TestPivotTransferScenarios(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.thresh = 0.001

    # ---------------------------------------------------------------- helpers

    def _pivot_frame(self, obj):
        """The frame the Object-mode manipulator aligns to — *obj*'s world
        orientation as a pure rotation matrix.

        Deliberately NOT ``matchTransform(piv=True, rot=True)``: that drives
        the ROTATE channel and ignores ``rotateAxis`` on both ends, so it
        reports a pivot as source-aligned while the object's real frame is
        untouched.  A transfer that cancels itself out to identity reads as a
        pass through that lens, which is how the no-op transfer shipped.

        Scale is decomposed out so a scaled object still reports a pure frame.
        """
        m = om.MMatrix(cmds.xform(obj, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation().asMatrix()

    def _assert_frames_close(self, got, want, msg=""):
        for i in range(16):
            if abs(got[i] - want[i]) > self.thresh:
                self.fail(
                    f"Pivot frames differ at index {i}: {got[i]} vs {want[i]}. {msg}"
                )

    def _frame_from_euler(self, x, y, z):
        """Reference frame built from world XYZ euler degrees."""
        loc = cmds.spaceLocator()[0]
        cmds.rotate(x, y, z, loc)
        frame = self._pivot_frame(loc)
        cmds.delete(loc)
        return frame

    def _world_points(self, transform):
        """Flat world-space point/CV coordinates of *transform*'s first shape.

        Queried through ``cmds`` rather than ``MFn*.…(kWorld)`` on purpose:
        the API read composes the dag path's inclusive matrix, which can still
        be the PRE-move matrix when nothing has forced the DG to evaluate.
        That snapshots a "before" of an object sitting at the origin and fails
        the comparison intermittently — measured, not theoretical.
        """
        shape = cmds.listRelatives(
            transform, shapes=True, noIntermediate=True, fullPath=True
        )[0]
        component = "cv[*][*]" if cmds.nodeType(shape) != "mesh" else "vtx[*]"
        return cmds.xform(f"{shape}.{component}", q=True, ws=True, t=True)

    def _assert_points_close(self, before, after, msg=""):
        self.assertEqual(len(before), len(after), f"point count changed. {msg}")
        self.assertGreater(len(before), 0, f"no points were read. {msg}")
        for i, (b, a) in enumerate(zip(before, after)):
            if abs(b - a) > self.thresh:
                self.fail(
                    f"Geometry moved at coordinate {i}: {b} vs {a}. {msg}"
                )

    # ------------------------------------------------------- original scenarios

    def test_transfer_from_rotated_source(self):
        """Source is rotated. Target is identity."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_transfer_to_frozen_target(self):
        """Source is rotated. Target is frozen (geometry rotated, transform identity)."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 0, 0, s)

        cmds.rotate(0, 90, 0, t)
        cmds.makeIdentity(t, apply=True, r=True)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_transfer_from_source_with_baked_custom_pivot(self):
        """Source's orientation lives in rotateAxis, not in the rotate channel.

        This is what an object looks like after Maya's Bake Pivot: rotate is
        zero and the custom pivot frame is carried by rotateAxis.  Reading the
        source through its ROTATE channel (``matchTransform -rot``) sees an
        unrotated object and transfers nothing.

        Built with ``setAttr`` on purpose — ``xform -ra`` compensates the
        rotate channel to hold the world orientation, so it cannot produce a
        custom pivot frame at all.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.setAttr(f"{s}.rotateAxis", 0, 45, 0, type="double3")
        # Guard the fixture itself: the source must really be re-framed.
        self._assert_frames_close(
            self._pivot_frame(s),
            self._frame_from_euler(0, 45, 0),
            "fixture did not produce a custom pivot frame",
        )

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_mirror_translate_reflects_position(self):
        """mirror='x' reflects the transferred translate pivot across the world YZ plane."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.xform(s, ws=True, rp=[3, 4, 5])

        XformUtils.transfer_pivot([s, t], translate=True, world_space=True, mirror="x")

        rp = cmds.xform(t, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], -3, delta=self.thresh)
        self.assertAlmostEqual(rp[1], 4, delta=self.thresh)
        self.assertAlmostEqual(rp[2], 5, delta=self.thresh)

    def test_mirror_rotate_reflects_orientation(self):
        """mirror='x' of a Z-rotated source pivot yields the negated Z rotation."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(0, 0, 30, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True, mirror="x")

        self._assert_frames_close(
            self._pivot_frame(t), self._frame_from_euler(0, 0, -30)
        )

    def test_mirror_objectspace_negates_rotateaxis(self):
        """world_space=False mirror='x' reflects rotateAxis: X preserved, Y/Z negated."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.setAttr(f"{s}.rotateAxis", 20, 35, 50, type="double3")

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=False, mirror="x")

        ra = cmds.xform(t, q=True, ra=True)
        self.assertAlmostEqual(ra[0], 20, delta=self.thresh)
        self.assertAlmostEqual(ra[1], -35, delta=self.thresh)
        self.assertAlmostEqual(ra[2], -50, delta=self.thresh)

    def test_transfer_preserve_target_pos(self):
        """Rotation-only transfer must not move the pivot position."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.move(10, 0, 0, s)
        cmds.rotate(0, 45, 0, s)
        cmds.move(-10, 0, 0, t)

        orig_pos = cmds.xform(t, q=True, ws=True, rp=True)

        XformUtils.transfer_pivot([s, t], rotate=True, translate=False, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))
        self.assertAlmostEqual(
            cmds.xform(t, q=True, ws=True, rp=True)[0], orig_pos[0], delta=self.thresh
        )

    # --------------------------------------------------- regression: no-op bug

    def test_world_space_rotate_actually_reorients_the_pivot(self):
        """The core regression: the transferred frame must land ON the target.

        The rotate pass used to zero the rotate channel and then write the
        INVERSE orientation into rotateAxis.  ``xform -ra`` compensates the
        rotate channel to hold the world orientation, so the two cancelled and
        the target's frame came out identity — the operation did nothing.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        frame = self._pivot_frame(t)
        self._assert_frames_close(frame, self._frame_from_euler(45, 45, 0))
        # Explicitly reject the identity that the cancelling writes produced.
        identity = om.MMatrix()
        self.assertFalse(
            all(abs(frame[i] - identity[i]) < self.thresh for i in range(16)),
            "target pivot frame is identity — the transfer was a no-op",
        )

    def test_world_space_rotate_parks_frame_on_the_pivot(self):
        """A permanent transfer leaves rotate clean and the frame on rotateAxis."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True, bake=True)

        for v in cmds.xform(t, q=True, ro=True):
            self.assertAlmostEqual(v, 0, delta=self.thresh)
        ra = cmds.xform(t, q=True, ra=True)
        self.assertAlmostEqual(ra[0], 45, delta=self.thresh)
        self.assertAlmostEqual(ra[1], 45, delta=self.thresh)
        self.assertAlmostEqual(ra[2], 0, delta=self.thresh)

    def test_world_space_rotate_pins_mesh_geometry(self):
        """A pivot transfer re-frames the pivot; it must not move the object."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)
        cmds.move(-4, 1, 2, t)
        before = self._world_points(t)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_points_close(before, self._world_points(t))

    def test_world_space_rotate_pins_nurbs_geometry(self):
        """NURBS targets are pinned too.

        The old per-vertex restore only handled ``mesh`` shapes, so a NURBS
        target was re-oriented and never compensated — its CVs swung with the
        transform.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.sphere(n="target")[0]

        cmds.rotate(45, 45, 0, s)
        cmds.move(-4, 1, 2, t)
        before = self._world_points(t)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_points_close(before, self._world_points(t))

    def test_transfer_onto_target_with_existing_pivot_orientation(self):
        """A target that already carries a rotateAxis must land ON the source.

        The write drives the rotate channel, so a leftover rotateAxis composes
        on top of it and the result lands on ``ra * source``.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)
        cmds.setAttr(f"{t}.rotateAxis", 15, 25, 35, type="double3")

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_transfer_is_idempotent(self):
        """Re-running a transfer must not drift the frame."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)
        first = self._pivot_frame(t)
        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), first)
        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_transfer_respects_target_rotate_order(self):
        """``xform -ws -ro`` reads euler in the TARGET's rotate order.

        Feeding it raw XYZ values lands a non-XYZ target on a different
        orientation entirely.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 30, 60, s)
        cmds.xform(t, roo="zyx")

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_transfer_to_parented_target(self):
        """A target under a rotated parent still lands on the source's WORLD frame."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)
        grp = cmds.group(t, n="parent_grp")
        cmds.rotate(20, 33, 47, grp)
        t = cmds.ls(t, long=True)[0]

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))

    def test_scaled_source_transfers_a_pure_frame(self):
        """A scaled source must not leak its scale into the transferred frame."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 45, 0, s)
        cmds.scale(2, 3, 4, s)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        self._assert_frames_close(
            self._pivot_frame(t), self._frame_from_euler(45, 45, 0)
        )
        for v in cmds.xform(t, q=True, s=True, r=True):
            self.assertAlmostEqual(v, 1.0, delta=self.thresh)

    def test_object_space_rotate_preserves_target_rotate_channel(self):
        """Object-space transfer copies rotateAxis without eating rotate.

        ``xform -ra`` silently compensates the rotate channel to hold the world
        orientation, which both mangles a channel the user never touched and
        cancels the transfer out to nothing.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.setAttr(f"{s}.rotateAxis", 20, 35, 50, type="double3")
        cmds.rotate(0, 60, 0, t)

        XformUtils.transfer_pivot([s, t], rotate=True, world_space=False)

        ro = cmds.xform(t, q=True, ro=True)
        self.assertAlmostEqual(ro[0], 0, delta=self.thresh)
        self.assertAlmostEqual(ro[1], 60, delta=self.thresh)
        self.assertAlmostEqual(ro[2], 0, delta=self.thresh)

        ra = cmds.xform(t, q=True, ra=True)
        for got, want in zip(ra, (20, 35, 50)):
            self.assertAlmostEqual(got, want, delta=self.thresh)

    # ------------------------------------------------- bake: permanent vs temp

    def test_bake_writes_a_permanent_pivot(self):
        """bake=True writes the target's OWN pivot attributes."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 30, 60, s)
        cmds.xform(s, ws=True, rp=[3, 4, 5])

        XformUtils.transfer_pivot(
            [s, t], translate=True, rotate=True, world_space=True, bake=True
        )

        self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))
        rp = cmds.xform(t, q=True, ws=True, rp=True)
        for got, want in zip(rp, (3, 4, 5)):
            self.assertAlmostEqual(got, want, delta=self.thresh)

    def test_bake_off_leaves_target_attributes_untouched(self):
        """bake=False is a manipulator override — no target attribute moves.

        The previous behavior had it backwards: bake=True ran a
        ``freeze_transforms`` that folded the transferred frame into the
        vertices and reset the channels, so the whole transfer was discarded.
        """
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 30, 60, s)
        cmds.xform(s, ws=True, rp=[3, 4, 5])

        before = {
            "ro": cmds.xform(t, q=True, ro=True),
            "ra": cmds.xform(t, q=True, ra=True),
            "rp": cmds.xform(t, q=True, ws=True, rp=True),
            "sp": cmds.xform(t, q=True, ws=True, sp=True),
        }
        before_points = self._world_points(t)

        XformUtils.transfer_pivot(
            [s, t], translate=True, rotate=True, world_space=True, bake=False
        )

        for key, want in before.items():
            got = {
                "ro": cmds.xform(t, q=True, ro=True),
                "ra": cmds.xform(t, q=True, ra=True),
                "rp": cmds.xform(t, q=True, ws=True, rp=True),
                "sp": cmds.xform(t, q=True, ws=True, sp=True),
            }[key]
            for g, w in zip(got, want):
                self.assertAlmostEqual(
                    g, w, delta=self.thresh, msg=f"{key} changed under bake=False"
                )
        self._assert_points_close(before_points, self._world_points(t))

    def test_bake_off_selects_the_targets(self):
        """The manipulator addresses the selection, so the targets become it."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.rotate(45, 30, 60, s)
        cmds.select(clear=True)

        XformUtils.transfer_pivot([s, t], translate=True, bake=False)

        self.assertEqual(cmds.ls(sl=True), [t])

    @skipIfBatch("cmds.manipPivot needs an interactive manipulator")
    def test_bake_off_sets_the_manip_pivot(self):
        """The manipulator lands on the source's pivot position."""
        s = cmds.polyCube(n="source")[0]
        t = cmds.polyCube(n="target")[0]

        cmds.xform(s, ws=True, rp=[3, 4, 5])
        cmds.setToolTo("moveSuperContext")

        XformUtils.transfer_pivot([s, t], translate=True, bake=False)

        pos = cmds.manipPivot(q=True, p=True)
        pos = list(pos[0]) if pos and isinstance(pos[0], (list, tuple)) else list(pos)
        for got, want in zip(pos, (3, 4, 5)):
            self.assertAlmostEqual(got, want, delta=self.thresh)

    def test_multiple_targets_all_receive_the_frame(self):
        """Every target in the selection gets the source's frame."""
        s = cmds.polyCube(n="source")[0]
        t1 = cmds.polyCube(n="target1")[0]
        t2 = cmds.polyCube(n="target2")[0]

        cmds.rotate(45, 45, 0, s)
        cmds.move(5, 0, 0, t2)

        XformUtils.transfer_pivot([s, t1, t2], rotate=True, world_space=True)

        for t in (t1, t2):
            self._assert_frames_close(self._pivot_frame(t), self._pivot_frame(s))
