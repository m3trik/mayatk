# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.edit_utils.bridge

Issue: the interactive Bridge tool surfaced ``polyBridgeEdge``'s multi-line
error wall (plus help URLs) straight into the Preview popup, with no guidance
on *why* the bridge failed. These tests pin:

- a valid two-equal-loop bridge on a combined mesh still succeeds,
- each real failure mode (unequal counts / one loop / non-border) raises an
  ``OperationError`` with a specific, readable message and the original Maya
  error chained for the console,
- ``_format_op_error`` collapses a raw multi-line driver error to a single
  clean line (no URLs) and renders an ``OperationError`` as titled rich text.
"""
import unittest

from base_test import MayaTkTestCase
import maya.cmds as cmds

from mayatk.edit_utils.bridge import Bridge, BridgeSlots
from mayatk.core_utils.preview import Preview, OperationError, _format_op_error

# Matches what BridgeSlots.perform_operation sends.
BRIDGE_KW = dict(
    curveType=0, divisions=0, smoothingAngle=30, bridgeOffset=0, taper=1.0, twist=0
)


class TestBridge(MayaTkTestCase):
    """Bridge failure diagnostics + clean popup message."""

    @staticmethod
    def _open_cyl(name, ty=0, sx=12):
        """Cylinder with both caps removed -> two open border loops."""
        c = cmds.polyCylinder(name=name, r=1, h=2, sx=sx, sy=1, ch=False)[0]
        cmds.delete(f"{c}.f[{sx}:]")
        cmds.move(0, ty, 0, c)
        return c

    @staticmethod
    def _loops_by_y(mesh):
        """Group the mesh's border edges into loops keyed by rounded Y."""
        cmds.select(mesh, r=True)
        cmds.polySelectConstraint(mode=3, type=0x8000, where=1)  # border edges
        border = cmds.ls(sl=True, fl=True) or []
        cmds.polySelectConstraint(disable=True)
        cmds.select(cl=True)
        groups = {}
        for e in border:
            vs = cmds.ls(
                cmds.polyListComponentConversion(e, fromEdge=True, toVertex=True),
                fl=True,
            )
            y = round(sum(cmds.pointPosition(v, w=True)[1] for v in vs) / len(vs), 2)
            groups.setdefault(y, []).append(e)
        return groups

    def _combined(self, sx_a=12, sx_b=12):
        """Two open cylinders combined into one mesh (two shells)."""
        a = self._open_cyl("brg_a", 0, sx=sx_a)
        b = self._open_cyl("brg_b", 4, sx=sx_b)
        return cmds.rename(
            cmds.polyUnite(a, b, ch=False, mergeUVSets=True)[0], "brg"
        )

    def _facing_loops(self, comb):
        g = self._loops_by_y(comb)
        ys = sorted(g)  # -1, +1, +3, +5  -> facing pair is +1 and +3
        return g[ys[1]] + g[ys[2]]

    # -- valid bridge still succeeds ------------------------------------------

    def test_valid_equal_loops_bridge(self):
        comb = self._combined()
        before = cmds.polyEvaluate(comb, face=True)
        Bridge.bridge(self._facing_loops(comb), **BRIDGE_KW)
        self.assertGreater(cmds.polyEvaluate(comb, face=True), before)

    # -- specific failure diagnostics -----------------------------------------

    def test_unequal_loops_message(self):
        comb = self._combined(sx_a=12, sx_b=8)
        with self.assertRaises(OperationError) as ctx:
            Bridge.bridge(self._facing_loops(comb), **BRIDGE_KW)
        msg = str(ctx.exception)
        self.assertIn("8", msg)
        self.assertIn("12", msg)
        self.assertIn("match", msg.lower())
        # original Maya error chained -> console still gets the full traceback
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_single_loop_message(self):
        c = self._open_cyl("brg_one", 0)
        g = self._loops_by_y(c)
        ys = sorted(g)
        with self.assertRaises(OperationError) as ctx:
            Bridge.bridge(g[ys[0]], **BRIDGE_KW)
        self.assertIn("two", str(ctx.exception).lower())

    def test_non_border_message(self):
        c = cmds.polyCylinder(name="brg_closed", r=1, h=4, sx=12, sy=4, ch=False)[0]
        ring = cmds.ls(f"{c}.e[24:35]", fl=True)  # interior ring, not a border
        with self.assertRaises(OperationError) as ctx:
            Bridge.bridge(ring, **BRIDGE_KW)
        self.assertIn("border", str(ctx.exception).lower())

    # -- popup formatting ------------------------------------------------------

    def test_format_collapses_driver_wall(self):
        raw = RuntimeError(
            "Maya cannot process the selected edges because\n"
            "the meshes being bridged must be combined into a single mesh, or\n"
            "see https://www.autodesk.com/maya-polygon-bridge-error"
        )
        out = _format_op_error(raw)
        self.assertNotIn("autodesk.com", out)
        self.assertNotIn("http", out)
        self.assertIn("Maya cannot process the selected edges", out)

    def test_format_renders_operation_error(self):
        err = OperationError("Boom.", causes=["do X", "do Y"], title="Bridge failed")
        out = _format_op_error(err)
        self.assertIn("Bridge failed", out)
        self.assertIn("Boom.", out)
        self.assertIn("do X", out)

    def test_format_escapes_untrusted_markup(self):
        # Untrusted exception text with a stray '<' must be escaped, not
        # swallowed as an HTML tag when rendered as rich text.
        err = RuntimeError("'<' not supported between 'int' and 'str'")
        out = _format_op_error(err)
        self.assertIn("&lt;", out)
        self.assertNotIn("'<'", out)


class _MockSignal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class _MockWidget:
    """Mock checkbox / button exposing only what Preview touches."""

    def __init__(self):
        self.toggled = _MockSignal()
        self.clicked = _MockSignal()
        self._checked = False
        self._enabled = True
        self.exclude_from_reset = False
        self.restore_state = True

    def setChecked(self, v):
        self._checked = bool(v)

    def isChecked(self):
        return self._checked

    def setEnabled(self, v):
        self._enabled = bool(v)

    def isEnabled(self):
        return self._enabled

    def blockSignals(self, v):
        return False

    def window(self):
        return None


class _BridgePreviewOp:
    """Stand-in for BridgeSlots: mutable params (like the UI spinboxes) that
    forward to Bridge.bridge. Mirrors the real slots' PRESERVE_GEOMETRY opt-in."""

    PRESERVE_GEOMETRY = True

    def __init__(self, **params):
        self.kwargs = dict(BRIDGE_KW)
        self.kwargs.update(params)

    def perform_operation(self, objects, contract):
        Bridge.bridge(objects, **self.kwargs)


class TestBridgePreviewRollback(MayaTkTestCase):
    """Regression: changing a value (e.g. Divisions) in the interactive Bridge
    must roll back the previous preview and re-bridge cleanly, even on a mesh
    with no upstream history (a freshly combined cylinder).

    Bug: polyBridgeEdge(ch=True) on a historyless mesh creates an intermediate
    orig-shape; the hermetic preview's node-diff rollback deleted it along with
    the bridge node, BAKING the bridge in and closing the border. The next
    refresh/commit then failed with "edges aren't on an open border". Fixed by
    BridgeSlots.PRESERVE_GEOMETRY=True plus Preview resolving the captured edge
    components to their owning transform for the snapshot. Verified in Maya.
    """

    @staticmethod
    def _counts(node):
        return (
            cmds.polyEvaluate(node, vertex=True),
            cmds.polyEvaluate(node, edge=True),
            cmds.polyEvaluate(node, face=True),
        )

    @staticmethod
    def _facing_loops(comb):
        cmds.select(comb, r=True)
        cmds.polySelectConstraint(mode=3, type=0x8000, where=1)
        border = cmds.ls(sl=True, fl=True) or []
        cmds.polySelectConstraint(disable=True)
        cmds.select(cl=True)
        g = {}
        for e in border:
            vs = cmds.ls(
                cmds.polyListComponentConversion(e, fromEdge=True, toVertex=True),
                fl=True,
            )
            y = round(sum(cmds.pointPosition(v, w=True)[1] for v in vs) / len(vs), 2)
            g.setdefault(y, []).append(e)
        ys = sorted(g)
        return g[ys[1]] + g[ys[2]]

    def _historyless_bridge_mesh(self, name="brg_hl"):
        a = cmds.polyCylinder(name=f"{name}_a", r=1, h=2, sx=12, sy=1, ch=False)[0]
        cmds.delete(f"{a}.f[12:]")
        b = cmds.polyCylinder(name=f"{name}_b", r=1, h=2, sx=12, sy=1, ch=False)[0]
        cmds.delete(f"{b}.f[12:]")
        cmds.move(0, 4, 0, b)
        comb = cmds.rename(cmds.polyUnite(a, b, ch=False, mergeUVSets=True)[0], name)
        cmds.delete(comb, constructionHistory=True)  # drop upstream history
        return comb

    def _make_preview(self, op):
        pv = Preview(op, _MockWidget(), _MockWidget(), message_func=lambda *a: None)
        self._previews.append(pv)
        return pv

    def _clean_bridge_counts(self, divisions, name):
        """Counts from a single fresh bridge -- the reference a refresh must match."""
        ref = self._historyless_bridge_mesh(name)
        op = _BridgePreviewOp(divisions=divisions)
        pv = self._make_preview(op)
        cmds.select(self._facing_loops(ref))
        pv.enable()
        result = self._counts(ref)
        pv.disable()
        return result

    def setUp(self):
        super().setUp()
        self._previews = []

    def tearDown(self):
        for pv in self._previews:
            try:
                pv.cleanup()
            except Exception:
                pass
        Preview.cleanup_all_instances()
        super().tearDown()

    def test_slots_class_opts_into_geometry_preservation(self):
        self.assertTrue(
            getattr(BridgeSlots, "PRESERVE_GEOMETRY", False),
            "BridgeSlots must set PRESERVE_GEOMETRY = True",
        )

    def test_refresh_does_not_break_bridge_on_historyless_mesh(self):
        comb = self._historyless_bridge_mesh()
        original = self._counts(comb)
        clean_two = self._clean_bridge_counts(2, "brg_hl_ref")

        op = _BridgePreviewOp(divisions=0)
        pv = self._make_preview(op)
        cmds.select(self._facing_loops(comb))
        pv.enable()
        self.assertNotEqual(self._counts(comb), original, "enable did not bridge")

        # "Change the Divisions value" -> refresh. Must roll back + re-bridge,
        # not bake/fail. Matches a clean single 2-division bridge.
        op.kwargs["divisions"] = 2
        pv.refresh()
        self.assertEqual(
            self._counts(comb),
            clean_two,
            "refresh baked/failed instead of re-bridging cleanly",
        )

        pv.disable()
        self.assertEqual(
            self._counts(comb), original, "disable did not restore the mesh"
        )


if __name__ == "__main__":
    unittest.main()
