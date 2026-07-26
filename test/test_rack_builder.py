# !/usr/bin/python
# coding=utf-8
"""Tests for edit_utils/rack_builder.py — the parametric EIA-310 generator.

Two layers:

* :class:`RackSpecTest` — the declarative schema (pure Python, no Maya): the
  standard's constants, list-of-nested occupant/bay validation, and the
  skeleton→validate round trip.
* :class:`RackBuilderGeometryTest` — the builder against a live Maya scene:
  part counts, group containment, metric scale, and that nothing pre-existing
  is disturbed.
"""
import unittest

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase

import maya.cmds as cmds
from mayatk.edit_utils.rack_builder import (
    EIA310,
    OccupantSpec,
    BaySpec,
    RackSpec,
    RackBuilder,
)


class RackSpecTest(unittest.TestCase):
    """Schema layer — no Maya runtime required."""

    def test_standard_constants(self):
        # Anchor the published EIA-310 values so a refactor can't silently drift.
        self.assertAlmostEqual(EIA310.U_MM, 44.45, places=2)
        self.assertAlmostEqual(EIA310.PANEL_WIDTH_MM, 482.6, places=1)
        self.assertAlmostEqual(EIA310.u_to_mm(3), 133.35, places=2)

    def test_skeleton_is_self_valid(self):
        self.assertTrue(RackSpec.validate(RackSpec.skeleton()).ok)

    def test_default_spec_has_two_bays(self):
        spec = RackSpec()
        self.assertEqual(len(spec.bays), 2)
        self.assertTrue(all(isinstance(b, BaySpec) for b in spec.bays))

    def test_from_dict_builds_nested_occupants(self):
        spec = RackSpec.from_dict(
            {
                "name": "DA_2",
                "bays": [
                    {
                        "ru": 24,
                        "occupants": [
                            {"label": "Oscilloscope", "height_u": 8},
                            {"label": "Signal Generator", "height_u": 4},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(spec.name, "DA_2")
        self.assertEqual(len(spec.bays), 1)
        self.assertEqual(len(spec.bays[0].occupants), 2)
        self.assertIsInstance(spec.bays[0].occupants[0], OccupantSpec)
        self.assertEqual(spec.bays[0].occupants[0].height_u, 8)

    def test_occupant_missing_required_label_is_error(self):
        res = RackSpec.validate({"bays": [{"occupants": [{"height_u": 2}]}]})
        self.assertFalse(res.ok)
        self.assertTrue(
            any("label" in e and "bays[0].occupants[0]." in e for e in res.errors)
        )

    def test_bad_occupant_style_is_error(self):
        res = RackSpec.validate(
            {"bays": [{"occupants": [{"label": "X", "style": "chrome"}]}]}
        )
        self.assertFalse(res.ok)

    def test_round_trip_dict(self):
        src = {
            "name": "r",
            "bays": [{"ru": 12, "occupants": [{"label": "DMM", "height_u": 1}]}],
        }
        out = RackSpec.from_dict(src).to_dict()
        self.assertEqual(out["bays"][0]["occupants"][0]["label"], "DMM")


class RackBuilderGeometryTest(MayaTkTestCase):
    """Builder against a live scene."""

    def _spec(self, **kw):
        base = {
            "name": "testRack",
            "bays": [
                {
                    "ru": 6,
                    "occupants": [
                        {"label": "Scope", "height_u": 3, "style": "instrument"},
                        {"label": "Blank", "height_u": 1, "style": "blank"},
                    ],
                }
            ],
        }
        base.update(kw)
        return RackSpec.from_dict(base)

    def test_build_returns_single_group(self):
        root = RackBuilder(self._spec()).build()
        self.assertTrue(cmds.objExists(root))
        self.assertEqual(cmds.nodeType(root), "transform")
        self.assertTrue(root.endswith("_grp"))

    def test_every_part_is_under_the_group(self):
        root = RackBuilder(self._spec()).build()
        meshes = cmds.listRelatives(root, allDescendents=True, type="mesh") or []
        self.assertGreater(len(meshes), 0)
        # rails (2) + scope faceplate + scope body + blank faceplate = 5 bay
        # parts, plus the enclosure: plinth + header + 2 side panels (spec
        # default side_panels=True) = 9.
        transforms = cmds.listRelatives(root, children=True) or []
        self.assertEqual(len(transforms), 9)

    def test_blank_occupant_has_no_chassis_body(self):
        root = RackBuilder(self._spec()).build()
        kids = cmds.listRelatives(root, allDescendents=True, type="transform") or []
        # A blank contributes only its faceplate — no "*_body".
        self.assertFalse(any(k.endswith("Blank_body") for k in kids))
        self.assertTrue(any("Scope_body" in k for k in kids))

    def test_metric_scale_panel_width(self):
        # A faceplate must be 19 in (482.6 mm) wide at the default cm scale,
        # i.e. 48.26 Maya units.
        root = RackBuilder(self._spec()).build()
        faceplate = next(
            k
            for k in (cmds.listRelatives(root, allDescendents=True, type="transform") or [])
            if "Scope" in k and "body" not in k
        )
        bbox = cmds.exactWorldBoundingBox(faceplate)
        width = bbox[3] - bbox[0]
        self.assertAlmostEqual(width, EIA310.PANEL_WIDTH_MM / 10.0, places=1)

    def test_two_bays_are_offset_horizontally(self):
        spec = self._spec(
            bays=[
                {"ru": 6, "occupants": [{"label": "A", "height_u": 1}]},
                {"ru": 6, "occupants": [{"label": "B", "height_u": 1}]},
            ]
        )
        root = RackBuilder(spec).build()
        kids = cmds.listRelatives(root, allDescendents=True, type="transform") or []
        a = next(k for k in kids if k.endswith("_A") or "_A" in k and "body" not in k)
        b = next(k for k in kids if "_B" in k and "body" not in k)
        xa = cmds.xform(a, query=True, worldSpace=True, translation=True)[0]
        xb = cmds.xform(b, query=True, worldSpace=True, translation=True)[0]
        self.assertAlmostEqual(xb - xa, EIA310.PANEL_WIDTH_MM / 10.0, places=1)

    def test_build_does_not_disturb_existing_geometry(self):
        pre = cmds.polyCube(name="preExisting")[0]
        RackBuilder(self._spec()).build()
        self.assertTrue(cmds.objExists(pre), "pre-existing node must survive")

    def test_punch_holes_groups_the_boolean_result(self):
        # punch_holes replaces each rail with a polyCBoolOp result ({rail}_punched);
        # ch=False CONSUMES the input rail, so the builder must group that result,
        # not the now-deleted input. Regression guard for the ignored-return-value
        # bug in _build_rails (build() would otherwise fail grouping a stale node).
        spec = self._spec(
            side_panels=False,
            bays=[{"ru": 2, "punch_holes": True, "occupants": []}],
        )
        root = RackBuilder(spec).build()
        children = cmds.listRelatives(root, children=True) or []
        self.assertFalse(
            [c for c in children if not cmds.objExists(c)],
            "every grouped node must exist (bug appended the consumed rail)",
        )
        punched = [c for c in children if c.endswith("_punched")]
        self.assertEqual(
            len(punched), 2, f"both punched rails must be grouped: {children}"
        )
        # The boolean must have actually cut holes (a plain box is 6 faces); this
        # also guards the hole column being positioned across the rail, not off it.
        for r in punched:
            self.assertGreater(
                cmds.polyEvaluate(r, face=True), 6, f"{r} has no cut holes"
            )

    def test_from_dict_classmethod_validates(self):
        with self.assertRaises(Exception):
            RackBuilder.from_dict({"bays": [{"occupants": [{"height_u": 1}]}]})


if __name__ == "__main__":
    unittest.main()
