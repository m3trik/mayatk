# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.rig_utils.rig_graph_extract (RigGraphExtractor).

Run with mayapy:
    & $MAYAPY mayatk\test\run_tests.py rig_graph_extract

The extractor reads a Maya rig into a RigGraph. The contract that matters most
is the schema's central rule: every driver the scene holds is ACCOUNTED for --
described, or emitted as ``opaque`` -- so ``coverage()`` reports nothing
unaccounted. A first-cut extractor once read 43% of a production rig and the
planner called it clean; that number, not the plan, is the acceptance test.
"""

import maya.cmds as cmds
from base_test import MayaTkTestCase

from pythontk import RigGraph


def _rig():
    """One of every driver shape the production module carries, tiny."""
    cmds.file(new=True, force=True)
    grp = cmds.group(empty=True, name="rig")
    a = cmds.spaceLocator(name="ctrl_a")[0]
    b = cmds.spaceLocator(name="ctrl_b")[0]
    driven = cmds.spaceLocator(name="driven")[0]
    aimed = cmds.spaceLocator(name="aimed")[0]
    up = cmds.spaceLocator(name="up_obj")[0]
    cmds.parent(a, b, driven, aimed, up, grp)
    cmds.setAttr(a + ".translate", 1, 0, 0)
    cmds.setAttr(b + ".translate", 0, 2, 0)
    pc = cmds.parentConstraint(a, b, driven, maintainOffset=True)[0]
    cmds.setAttr(
        pc + "." + cmds.parentConstraint(pc, q=True, weightAliasList=True)[1], 0.25
    )
    ac = cmds.aimConstraint(
        a,
        aimed,
        worldUpType="object",
        worldUpObject=up,
        aimVector=(1, 0, 0),
        upVector=(0, 1, 0),
    )[0]
    cmds.select(clear=True)
    j1 = cmds.joint(p=(0, 0, 0), name="j1")
    j2 = cmds.joint(p=(0, 2, 1), name="j2")
    j3 = cmds.joint(p=(0, 4, 0), name="j3")
    cmds.parent(j1, grp)
    ik = cmds.ikHandle(startJoint=j1, endEffector=j3, solver="ikRPsolver", name="ik1")[
        0
    ]
    cmds.parent(ik, grp)  # ikHandle creates at the root; the rig owns it
    pole = cmds.spaceLocator(name="pole")[0]
    cmds.parent(pole, grp)
    cmds.poleVectorConstraint(pole, ik)
    cmds.addAttr(
        a, longName="stretch", attributeType="double", keyable=True, min=0, max=1
    )
    # On a channel the parentConstraint leaves alone: a key on a constrained
    # plug makes Maya insert a pairBlend -- a SHARED plug, not a curve.
    cmds.setDrivenKeyframe(driven + ".scaleX", currentDriver=a + ".stretch", dv=0, v=0)
    cmds.setDrivenKeyframe(driven + ".scaleX", currentDriver=a + ".stretch", dv=1, v=5)
    md = cmds.createNode("multiplyDivide", name="md1")
    cmds.connectAttr(a + ".stretch", md + ".input1X")
    cmds.setAttr(md + ".input2X", 2.0)
    cmds.connectAttr(md + ".outputX", aimed + ".scaleY")
    cmds.expression(string="{}.scaleZ = {}.stretch * 3;".format(aimed, a), name="expr1")
    # Chained math: md2 feeds a condition that feeds the rig. On the production
    # module 35 such nodes read as "inert" because the walk stopped at the
    # condition -- a driver called free, the silent-omission bug class.
    md2 = cmds.createNode("multiplyDivide", name="md2")
    cmds.connectAttr(a + ".stretch", md2 + ".input1X")
    cmds.setAttr(md2 + ".input2X", 4.0)
    # ...through a blendColors, the spline-IK stretch idiom: 21 production
    # multiplyDivide nodes feed one, and it was neither censused nor walked.
    bc = cmds.createNode("blendColors", name="bc1")
    cmds.connectAttr(md2 + ".outputX", bc + ".color1R")
    cond = cmds.createNode("condition", name="cond1")
    cmds.connectAttr(bc + ".outputR", cond + ".firstTerm")
    cmds.connectAttr(cond + ".outColorR", driven + ".scaleZ")  # a free channel
    mm = cmds.createNode("multMatrix", name="mm1")
    cmds.connectAttr(a + ".worldMatrix[0]", mm + ".matrixIn[0]")
    cmds.connectAttr(mm + ".matrixSum", up + ".offsetParentMatrix")
    cube = cmds.polyCube(name="skin_mesh", constructionHistory=False)[0]
    cmds.parent(cube, grp)
    cmds.skinCluster(j1, j2, j3, cube, name="skin1", toSelectedBones=True)
    return dict(
        grp=grp,
        a=a,
        b=b,
        driven=driven,
        aimed=aimed,
        up=up,
        ik=ik,
        joints=[j1, j2, j3],
        pc=pc,
        ac=ac,
        md=md,
        mm=mm,
    )


class TestRigGraphExtractor(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        from mayatk.rig_utils.rig_graph_extract import RigGraphExtractor

        self.rig = _rig()
        self.data = RigGraphExtractor().extract()
        self.graph = RigGraph.from_dict(self.data)

    def _records(self, shape, op):
        return [r for r in self.graph.records if r.shape == shape and r.op == op]

    def test_the_graph_is_well_formed(self):
        self.assertEqual(self.graph.validate(), [])
        self.assertEqual(self.data["source"]["app"], "maya")

    def test_every_driver_is_accounted_for(self):
        cov = self.graph.coverage()
        self.assertGreater(cov["seen"], 0)
        self.assertEqual(cov["unaccounted"], 0, cov)
        self.assertEqual(cov["uncensused"], {}, cov)

    def test_ids_are_sanitised_full_paths(self):
        ids = {n.id for n in self.graph.nodes}
        self.assertIn("/rig/driven", ids)
        self.assertIn("/rig/j1/j2/j3", ids)
        self.assertTrue(
            all(i.startswith("/") and "|" not in i and ":" not in i for i in ids)
        )

    def test_parent_constraint_is_a_blend_with_weighted_spaces(self):
        (rec,) = self._records("transform", "blend")
        self.assertEqual(rec.target["id"], "/rig/driven")
        weights = {s["id"]: s["weight"] for s in rec.sources}
        self.assertEqual(weights, {"/rig/ctrl_a": 1.0, "/rig/ctrl_b": 0.25})
        self.assertTrue(rec.params["maintain_offset"])
        self.assertTrue(any(p.startswith("parentConstraint:") for p in rec.provenance))

    def test_aim_constraint_names_its_up_by_role(self):
        (rec,) = self._records("transform", "aim")
        roles = {s["role"]: s["id"] for s in rec.sources}
        self.assertEqual(roles, {"target": "/rig/ctrl_a", "up": "/rig/up_obj"})
        self.assertEqual(rec.params["up_ref"], {"kind": "object", "role": "up"})
        self.assertEqual(rec.params["aim_axis"], [1.0, 0.0, 0.0])

    def test_ik_targets_the_chain_and_carries_its_pole(self):
        (rec,) = self._records("transform", "ik")
        self.assertEqual(
            rec.target["chain"], ["/rig/j1", "/rig/j1/j2", "/rig/j1/j2/j3"]
        )
        roles = {s["role"]: s["id"] for s in rec.sources}
        self.assertEqual(roles["goal"], "/rig/ik1")
        self.assertEqual(roles["pole"], "/rig/pole")
        self.assertEqual(rec.params["solver"], "rotate_plane")
        self.assertTrue(
            any(p.startswith("poleVectorConstraint:") for p in rec.provenance)
        )

    def test_spline_ik_names_its_curve_and_carries_no_goal(self):
        # A spline handle's own position is inert (the curve drives), and no
        # consumer's `spline_ik` has a `goal` role: emitting one had every
        # production chain planned straight to the bake (`unsupported_role`).
        from mayatk.rig_utils.rig_graph_extract import RigGraphExtractor

        cmds.select(clear=True)
        s1 = cmds.joint(p=(5, 0, 0), name="s1")
        cmds.joint(p=(5, 2, 0), name="s2")
        s3 = cmds.joint(p=(5, 4, 0), name="s3")
        cmds.parent(s1, self.rig["grp"])
        handle, _effector, curve = cmds.ikHandle(
            startJoint=s1,
            endEffector=s3,
            solver="ikSplineSolver",
            createCurve=True,
            parentCurve=False,
            name="sik1",
        )
        cmds.parent(handle, curve, self.rig["grp"])
        # The production idiom: the IK curve is skinned to a DRIVER joint --
        # the deformer edge that makes the driver's controls part of the rig.
        cmds.select(clear=True)
        drv = cmds.joint(p=(5, 2, 1), name="drv")
        cmds.parent(drv, self.rig["grp"])
        cmds.skinCluster(drv, curve, name="curve_skin", toSelectedBones=True)
        graph = RigGraph.from_dict(RigGraphExtractor().extract())
        (skin,) = [
            r
            for r in graph.records
            if r.op == "skin" and r.params.get("geometry") == "curve"
        ]
        self.assertEqual(skin.target["id"], "/rig/" + curve.split("|")[-1])
        self.assertEqual([s["id"] for s in skin.sources], ["/rig/drv"])
        (rec,) = [r for r in graph.records if r.op == "spline_ik"]
        roles = {src["role"]: src["id"] for src in rec.sources}
        self.assertEqual(set(roles), {"curve"})
        self.assertEqual(roles["curve"], "/rig/" + curve.split("|")[-1])
        self.assertEqual(
            rec.target["chain"], ["/rig/s1", "/rig/s1/s2", "/rig/s1/s2/s3"]
        )
        self.assertEqual(graph.validate(), [])

    def test_a_skin_binding_is_a_points_record_naming_its_influences(self):
        # The BINDING, not the weights: which joints deform which geometry, so
        # the deformer edge is a graph edge the component rule can see.
        (rec,) = self._records("points", "skin")
        self.assertEqual(rec.target, {"id": "/rig/skin_mesh", "points": "all"})
        self.assertEqual(
            [s["id"] for s in rec.sources], ["/rig/j1", "/rig/j1/j2", "/rig/j1/j2/j3"]
        )
        self.assertEqual({s["role"] for s in rec.sources}, {"influence"})
        self.assertEqual(rec.params, {"geometry": "mesh"})
        self.assertIn("skinCluster:skin1", rec.provenance)

    def test_set_driven_key_is_a_curve_and_math_is_a_linear(self):
        (curve,) = self._records("channel", "curve")
        self.assertEqual(curve.target, "/rig/driven.scale.x")
        self.assertEqual(curve.sources[0]["plug"], "/rig/ctrl_a.stretch")
        self.assertEqual(
            [p[:2] for p in curve.params["points"]], [[0.0, 0.0], [1.0, 5.0]]
        )
        (linear,) = self._records("channel", "linear")
        self.assertEqual(linear.target, "/rig/aimed.scale.y")
        self.assertEqual(linear.params, {"scale": 2.0, "offset": 0.0})

    def test_what_cannot_be_described_is_opaque_never_silent(self):
        origins = {
            r.params["origin"]["node_type"] for r in self._records("opaque", "opaque")
        }
        self.assertEqual(
            origins,
            {"expression", "multMatrix", "condition", "multiplyDivide", "blendColors"},
        )

    def test_chained_math_is_accounted_for_not_called_inert(self):
        # md2 -> cond1 -> driven.rotateZ: BOTH drive the rig through the chain.
        self.assertEqual(self.data["source"]["inert"], {})
        frozen = {
            p: r.target["ids"]
            for r in self._records("opaque", "opaque")
            for p in r.provenance
        }
        self.assertEqual(frozen.get("multiplyDivide:md2"), ["/rig/driven"])
        self.assertEqual(frozen.get("condition:cond1"), ["/rig/driven"])
        self.assertEqual(frozen.get("blendColors:bc1"), ["/rig/driven"])


def _humanik(cmds):
    """A stock HumanIK character: skeleton + control rig + the control rig set
    as the character's SOURCE, which is what wires the solver into the joints.

    Maya's OWN bundled character system, generated headless in under a second --
    real-world rig complexity with no asset to download, no licence to carry and
    no drift from a vendored binary. Returns the character node, or None when
    this Maya cannot provide HumanIK.
    """
    import maya.mel as mel

    try:
        for plug in ("mayaHIK", "mayaCharacterization", "OneClick"):
            cmds.loadPlugin(plug, quiet=True)
        for module in (
            "hikGlobalUtils",
            "hikCharacterControlsUI",
            "hikDefinitionOperations",
        ):
            mel.eval('source "{}.mel";'.format(module))
        from maya.app.quickRig import quickRigUI
    except Exception:
        return None
    # No scene wipe here: `MayaTkTestCase.setUp` already did the ONE per-test
    # reset the harness budgets for.
    character = mel.eval('hikCreateCharacter("probeChar")')
    quickRigUI.createHikSkeleton(
        character,
        {
            "NeckCount": 1,
            "ShoulderCount": 1,
            "SpineCount": 3,
            "WantIndexFinger": 1,
            "WantMiddleFinger": 1,
            "WantRingFinger": 1,
            "WantPinkyFinger": 1,
            "WantThumb": 1,
            "WantHipsTranslation": 1,
        },
    )
    quickRigUI.hikCreateControlRig(character)
    # Unsourced, HIK connects to nothing; sourcing it is the production state.
    mel.eval(
        'hikSetCurrentCharacter("{0}"); hikUpdateCharacterList(); '
        'hikSetCharacterInput("{0}", "{0}");'.format(character)
    )
    return character


class TestHumanIKRig(MayaTkTestCase):
    """A real, third-party-authored rig: Maya's bundled HumanIK.

    The procedural fixture above is ours, so it can only ever test what we
    already thought of. HumanIK is not ours and drives a skeleton the one way
    the extractor did not look -- a solver network, no constraint anywhere.
    Measured when this test was written: 63 joints, 567 driven joint plugs, and
    the extractor read ZERO records while ``coverage()`` answered ``0
    unaccounted`` -- a graph claiming to be complete with the whole rig missing.
    """

    def setUp(self):
        super().setUp()
        from mayatk.rig_utils.rig_graph_extract import RigGraphExtractor

        self.character = _humanik(cmds)
        if not self.character:
            self.skipTest("HumanIK is unavailable in this Maya")
        self.data = RigGraphExtractor().extract()
        self.graph = RigGraph.from_dict(self.data)

    def test_the_skeleton_is_driven_at_all(self):
        # Guards the fixture itself: unsourced, HIK wires nothing, and every
        # assertion below would pass against a rig that does not exist.
        # HIK wires the COMPONENT plugs (`HIKState2SK1.HipsTx -> Hips.translateX`),
        # never the compound, so a check on `.translate` reads zero on a fully
        # driven skeleton -- which is exactly what it did when this was written.
        driven = [
            j
            for j in cmds.ls(type="joint") or []
            if any(
                cmds.listConnections(
                    "{}.{}{}".format(j, channel, axis),
                    source=True,
                    destination=False,
                )
                for channel in ("translate", "rotate")
                for axis in "XYZ"
            )
        ]
        self.assertGreater(len(cmds.ls(type="joint") or []), 20)
        self.assertGreater(len(driven), 20)

    def test_a_solver_driven_skeleton_is_accounted_for_never_silently_empty(self):
        self.assertEqual(self.graph.validate(), [])
        self.assertGreater(len(self.graph.records), 0, "the whole rig read as nothing")
        cov = self.graph.coverage()
        self.assertGreater(cov["seen"], 0, "nothing censused: coverage cannot promise")
        self.assertEqual(cov["unaccounted"], 0, cov)
        self.assertEqual(cov["uncensused"], {}, cov)

    def test_the_joints_the_solver_moves_are_named_by_a_record(self):
        moved = {i for r in self.graph.records for i in r.target_ids()}
        self.assertTrue(
            any(i.endswith("_Hips") for i in moved),
            "the solver's own targets are missing from every record: {}".format(
                sorted(moved)[:6]
            ),
        )
        solver = [
            r
            for r in self.graph.records
            if any(p.startswith("HIKState2SK:") for p in r.provenance)
        ]
        self.assertTrue(solver, "HIKState2SK drove the skeleton and shipped no record")
        self.assertEqual({r.shape for r in solver}, {"opaque"})

    def test_the_plan_bakes_it_and_says_so_rather_than_dropping_it(self):
        from mayatk.rig_utils.rig_graph_build import RigGraphBuilder
        from pythontk import RigCapability, RigPlanner

        plan = RigPlanner.plan(
            self.graph, RigCapability.from_dict(RigGraphBuilder.capability())
        )
        self.assertTrue(plan.bake, "a rig nobody can build must still be baked")
        reasons = {e.reason for e in plan.report}
        self.assertIn("unsupported_shape", reasons)
