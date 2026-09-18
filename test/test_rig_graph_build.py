# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.rig_utils.rig_graph_build (RigGraphBuilder) -- Maya as the TARGET.

Run with mayapy:
    & $MAYAPY mayatk\\test\\run_tests.py rig_graph_build

The mirror of blendertk's builder: a RigGraph (the Blender extractor's output
shape) planned against Maya's capability and built from Maya's own constraint
commands. Every op grades ``approximate`` until a conformance fixture vouches
for it.
"""

import maya.cmds as cmds
from base_test import MayaTkTestCase

from pythontk import RigCapability


def _fixture():
    cmds.file(new=True, force=True)
    grp = cmds.group(empty=True, name="rig")
    made = {}
    for name in ("ctrl_a", "ctrl_b", "driven", "aimed", "up_obj"):
        made[name] = cmds.spaceLocator(name=name)[0]
        cmds.parent(made[name], grp)
    cmds.setAttr(made["ctrl_a"] + ".translate", 1, 0, 0)
    cmds.setAttr(made["ctrl_b"] + ".translate", 0, 2, 0)
    cmds.addAttr(
        made["ctrl_a"], longName="stretch", attributeType="double", keyable=True
    )
    cmds.select(clear=True)
    j1 = cmds.joint(p=(0, 0, 0), name="j1")
    cmds.joint(p=(0, 2, 1), name="j2")
    cmds.joint(p=(0, 4, 0), name="j3")
    cmds.parent(j1, grp)
    curve = cmds.curve(p=[(0, 0, 0), (0, 2, 1), (0, 4, 0)], degree=2, name="ik_curve")
    cmds.parent(curve, grp)
    return made


NODE_IDS = (
    "/rig",
    "/rig/ctrl_a",
    "/rig/ctrl_b",
    "/rig/driven",
    "/rig/aimed",
    "/rig/up_obj",
    "/rig/j1",
    "/rig/j1/j2",
    "/rig/j1/j2/j3",
    "/rig/ik_curve",
    "/rig/ghost",
)
GRAPH = {
    "version": 1,
    "source": {"app": "blender", "linear_unit": "m", "up_axis": "z", "census": {}},
    "policy": {"fallback": "bake", "verify": {"tolerance": 0.1, "frames": [1]}},
    "nodes": [{"id": i} for i in NODE_IDS],
    "records": [
        {
            "id": "r_blend",
            "shape": "transform",
            "op": "blend",
            "target": {"id": "/rig/driven", "channels": ["translate", "rotate"]},
            "sources": [
                {"id": "/rig/ctrl_a", "role": "space", "weight": 1.0},
                {"id": "/rig/ctrl_b", "role": "space", "weight": 0.25},
            ],
            "params": {"compose": "matrix", "maintain_offset": True},
        },
        {
            "id": "r_aim",
            "shape": "transform",
            "op": "aim",
            "target": {"id": "/rig/aimed", "channels": ["rotate"]},
            "sources": [
                {"id": "/rig/ctrl_a", "role": "target", "weight": 1.0},
                {"id": "/rig/up_obj", "role": "up"},
            ],
            "params": {
                "aim_axis": [1.0, 0, 0],
                "up_axis": [0, 1.0, 0],
                "up_ref": {"kind": "object", "role": "up"},
            },
        },
        {
            "id": "r_lin",
            "shape": "channel",
            "op": "linear",
            "target": "/rig/aimed.scale.y",
            "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
            "params": {"scale": 2.0, "offset": 0.5},
        },
        {
            "id": "r_curve",
            "shape": "channel",
            "op": "curve",
            "target": "/rig/driven.scale.x",
            "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
            "params": {"points": [[0, 0, 0, 0], [1, 5, 0, 0]], "interp": "bezier"},
        },
        {
            "id": "r_spline",
            "shape": "transform",
            "op": "spline_ik",
            "target": {
                "chain": ["/rig/j1", "/rig/j1/j2", "/rig/j1/j2/j3"],
                "channels": ["rotate"],
            },
            "sources": [{"id": "/rig/ik_curve", "role": "curve"}],
            "params": {"twist": {"distribution": "linear", "start": 0.0, "end": 0.0}},
        },
        {
            "id": "r_opaque",
            "shape": "opaque",
            "op": "opaque",
            "target": {"ids": ["/rig/ghost"]},
            "sources": [],
            "params": {
                "origin": {"app": "blender", "node_type": "driver", "node": "x"}
            },
        },
    ],
}


class TestRigGraphBuilderMaya(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        from mayatk.rig_utils.rig_graph_build import RigGraphBuilder

        self.made = _fixture()
        self.builder = RigGraphBuilder()
        nodes = cmds.ls(long=True, type="transform")
        self.result = self.builder.build(GRAPH, nodes, is_usd=False)

    def test_capability_is_honest_data(self):
        cap = self.builder.capability()
        RigCapability.from_dict(cap)
        self.assertEqual(cap["target"], "maya")
        self.assertTrue(
            {
                "transform/blend",
                "transform/aim",
                "transform/spline_ik",
                "channel/linear",
                "channel/curve",
            }
            <= set(cap["ops"])
        )
        self.assertTrue(
            all(o["fidelity"] == "approximate" for o in cap["ops"].values())
        )

    def test_builds_the_describable_records_and_bakes_the_opaque(self):
        failed = [e for e in self.result["report"] if e.get("kind") == "failed"]
        self.assertTrue(
            {"r_blend", "r_aim", "r_lin", "r_curve", "r_spline"}
            <= set(self.result["built"]),
            failed,
        )
        self.assertIn("/rig/ghost", self.result["baked"])

    def test_blend_is_a_parent_constraint_with_weights(self):
        pcs = cmds.listRelatives(self.made["driven"], type="parentConstraint") or []
        self.assertEqual(len(pcs), 1)
        aliases = cmds.parentConstraint(pcs[0], query=True, weightAliasList=True)
        self.assertEqual(
            sorted(round(cmds.getAttr(f"{pcs[0]}.{a}"), 3) for a in aliases),
            [0.25, 1.0],
        )

    def test_aim_is_an_aim_constraint_with_the_up_object(self):
        acs = cmds.listRelatives(self.made["aimed"], type="aimConstraint") or []
        self.assertEqual(len(acs), 1)
        self.assertEqual(cmds.getAttr(f"{acs[0]}.worldUpType"), 1)
        self.assertEqual(
            cmds.listConnections(f"{acs[0]}.worldUpMatrix", source=True),
            [self.made["up_obj"]],
        )

    def test_linear_and_curve_drive_their_plugs(self):
        cmds.setAttr(self.made["ctrl_a"] + ".stretch", 1.0)
        self.assertAlmostEqual(
            cmds.getAttr(self.made["aimed"] + ".scaleY"), 2.5, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(self.made["driven"] + ".scaleX"), 5.0, places=3
        )

    def test_spline_ik_spans_the_chain(self):
        handles = cmds.ls(type="ikHandle")
        self.assertEqual(len(handles), 1)
        solver = cmds.listConnections(handles[0] + ".ikSolver")
        self.assertEqual(cmds.nodeType(solver[0]), "ikSplineSolver")

    def test_remove_takes_a_record_back(self):
        self.assertGreater(self.builder.remove("r_blend"), 0)
        self.assertEqual(
            cmds.listRelatives(self.made["driven"], type="parentConstraint") or [], []
        )

    def test_fbx_spelled_names_resolve_by_decoded_id(self):
        # Maya's FBX importer spells "dotted.001" as "dottedFBXASC046001"; the
        # payload's ids are prim-sanitised Blender names ("dotted_001").
        grp = cmds.createNode("transform", name="dottedFBXASC046001")
        leaf = cmds.createNode("transform", name="childFBXASC046space", parent=grp)
        self.builder._index(cmds.ls(long=True, type="transform"))
        self.assertEqual(self.builder._node("/dotted_001"), "|" + grp)
        self.assertEqual(
            self.builder._node("/dotted_001/child_space"), "|dottedFBXASC046001|" + leaf
        )
        self.assertEqual(
            self.builder._node("/elsewhere/child_space"), "|dottedFBXASC046001|" + leaf
        )

    def test_a_skin_binding_is_checked_not_built_and_a_missing_one_fails(self):
        # points/skin: the carrier ships a MESH skin, so the builder only checks
        # the binding arrived; a mesh without one FAILS (its component bakes),
        # and a curve skin is refused by the capability before anything runs.
        from mayatk.rig_utils.rig_graph_build import RigGraphBuilder

        cube = cmds.polyCube(name="skin_mesh", constructionHistory=False)[0]
        bare = cmds.polyCube(name="bare_mesh", constructionHistory=False)[0]
        cmds.parent(cube, bare, "rig")
        cmds.skinCluster("j1", "j2", cube, name="skin1", toSelectedBones=True)

        def skin(rid, target, joint, geometry="mesh"):
            return {
                "id": rid,
                "shape": "points",
                "op": "skin",
                "target": {"id": target, "points": "all"},
                "sources": [{"id": joint, "role": "influence"}],
                "params": {"geometry": geometry},
            }

        # Three separate components (one joint each), so the component rule
        # does not fold one outcome into the others.
        graph = {
            "version": 1,
            "source": {
                "app": "blender",
                "linear_unit": "m",
                "up_axis": "z",
                "census": {},
            },
            "policy": {"fallback": "bake"},
            "nodes": [
                {"id": i}
                for i in (
                    "/rig",
                    "/rig/j1",
                    "/rig/j1/j2",
                    "/rig/j1/j2/j3",
                    "/rig/skin_mesh",
                    "/rig/bare_mesh",
                    "/rig/ik_curve",
                )
            ],
            "records": [
                skin("r_ok", "/rig/skin_mesh", "/rig/j1"),
                skin("r_bare", "/rig/bare_mesh", "/rig/j1/j2"),
                skin("r_curve", "/rig/ik_curve", "/rig/j1/j2/j3", "curve"),
            ],
        }
        result = RigGraphBuilder().build(graph, cmds.ls(long=True, type="transform"))
        kinds = {e["record"]: (e["kind"], e.get("reason")) for e in result["report"]}
        self.assertEqual(kinds["r_ok"], ("native", "built"))
        self.assertEqual(kinds["r_bare"], ("failed", "builder_raised"))
        self.assertEqual(kinds["r_curve"], ("baked", "unsupported_param"))
        self.assertEqual(result["built"], ["r_ok"])
