# !/usr/bin/python
# coding=utf-8
"""Export baselines: fixed scenes exported through the Scene Exporter, pinned.

Each test builds one small scene, exports it as FBX + GLB through
``SceneExporter.perform_export`` -- the path the panel's Export button runs --
and compares both files with the fingerprints saved under
``test/baselines/export/`` (:class:`pythontk.ExportFingerprint`). A code move
must change no output, so any difference fails with its path named.

A change that alters the output on purpose rewrites the baselines with
``UPDATE_EXPORT_BASELINES=1``; the fingerprint diff in the commit is the review.
"""

import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase

BASELINES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines", "export")
UPDATE = os.environ.get("UPDATE_EXPORT_BASELINES") == "1"


class TestExportBaselines(MayaTkTestCase):
    """The Scene Exporter's FBX and GLB for three fixed scenes: a prop
    hierarchy with two materials, keyed transforms split into shots, and a
    skinned joint chain."""

    def setUp(self):
        super().setUp()
        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            self.skipTest("FBX plugin not available")
        artifacts = ptk.TempArtifacts("export_baselines", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        self.out = artifacts.dir_path()

    def _export(self, name, objects, **tasks):
        from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter

        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False
        written = exporter.perform_export(
            export_dir=self.out,
            objects=objects,
            output_name=name,
            tasks={"output_format": "fbx_glb", **tasks},
        )
        self.assertTrue(written, f"the {name} export wrote nothing")
        return name

    def assertMatchesBaseline(self, name):
        for extension in ("fbx", "glb"):
            path = os.path.join(self.out, f"{name}.{extension}")
            self.assertTrue(os.path.isfile(path), f"not written: {path}")
            baseline = os.path.join(BASELINES, f"{name}.{extension}.json")
            changes = ptk.ExportFingerprint.against(path, baseline, update=UPDATE)
            self.assertEqual(changes, [], f"{name}.{extension} differs from its baseline")

    @staticmethod
    def _material(name, kind, **attrs):
        shader = cmds.shadingNode(kind, asShader=True, name=name)
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
        cmds.connectAttr(f"{shader}.outColor", f"{group}.surfaceShader", force=True)
        for attr, value in attrs.items():
            if isinstance(value, tuple):
                cmds.setAttr(f"{shader}.{attr}", *value, type="double3")
            else:
                cmds.setAttr(f"{shader}.{attr}", value)
        return group

    def test_a_prop_hierarchy_with_materials(self):
        crate = cmds.polyCube(name="crate", width=1, height=1, depth=1)[0]
        ball = cmds.polySphere(name="ball", radius=0.5, subdivisionsX=8, subdivisionsY=6)[0]
        group = cmds.group(crate, ball, name="props_grp")
        cmds.xform(crate, translation=(1, 0.5, -2), rotation=(0, 45, 0), scale=(2, 1, 1))
        cmds.xform(ball, translation=(-1, 1, 0))
        cmds.sets(crate, edit=True, forceElement=self._material(
            "crate_mat", "lambert", color=(0.8, 0.2, 0.1)
        ))
        cmds.sets(ball, edit=True, forceElement=self._material(
            "ball_mat", "standardSurface", baseColor=(0.1, 0.3, 0.9), metalness=0.5
        ))
        self.assertMatchesBaseline(self._export("props", [group]))

    def test_keyed_transforms_split_into_shots(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        mover = cmds.polyCube(name="mover")[0]
        cmds.setKeyframe(mover, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(mover, attribute="translateX", time=40, value=10)
        cmds.setKeyframe(mover, attribute="rotateY", time=21, value=0)
        cmds.setKeyframe(mover, attribute="rotateY", time=40, value=90)
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("ShotA", 1, 20, objects=[mover])
        store.define_shot("ShotB", 21, 40, objects=[mover])
        self.assertMatchesBaseline(
            self._export(
                "shots", [mover], export_data_node=True, apply_declared_takes="both"
            )
        )

    def test_a_skinned_joint_chain(self):
        cmds.select(clear=True)
        joints = [
            cmds.joint(name=f"bone_{i}", position=(0, i * 2.0, 0)) for i in range(3)
        ]
        limb = cmds.polyCylinder(
            name="limb", radius=0.4, height=4, subdivisionsY=4, subdivisionsAxis=8
        )[0]
        cmds.xform(limb, translation=(0, 2, 0))
        cmds.skinCluster(joints[0], limb, toSelectedBones=False, maximumInfluences=2)
        cmds.setKeyframe(joints[1], attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(joints[1], attribute="rotateZ", time=20, value=60)
        rig = cmds.group(joints[0], limb, name="rig_grp")
        self.assertMatchesBaseline(self._export("skinned", [rig]))

    def test_a_keyed_child_joints_take_is_lost_in_the_glb_today(self):
        """Pins a defect (.claude/BACKLOG.md, 2026-10-27): the FBX carries
        bone_1's rotation take, but FBX2glTF reads its range as [4294967295 -
        4294967295] and the GLB ships no clip. A keyed mesh, or a lone root
        joint keyed on translate, converts fine; Segment Scale Compensate makes
        no difference. The FBX half must hold (anything else is not this
        defect); when the GLB half fails, the defect is fixed: flip it and
        delete the backlog entry."""
        cmds.select(clear=True)
        joints = [cmds.joint(name=f"bone_{i}", position=(0, i * 2.0, 0)) for i in range(3)]
        cmds.setKeyframe(joints[1], attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(joints[1], attribute="rotateZ", time=20, value=60)
        name = self._export("joint_take", [joints[0]])
        takes = ptk.ExportFingerprint.of(os.path.join(self.out, f"{name}.fbx"))["takes"]
        self.assertTrue(
            any(curve.startswith("bone_1|") for take in takes.values() for curve in take),
            f"the FBX lost bone_1's take too -- not the pinned defect: {takes}",
        )
        clips = ptk.ExportFingerprint.of(os.path.join(self.out, f"{name}.glb"))["animations"]
        self.assertFalse(
            any("bone_1" in channel for clip in clips.values() for channel in clip["channels"]),
            "bone_1's clip now reaches the GLB: the defect is fixed -- flip this "
            "assertion and delete the backlog entry",
        )


if __name__ == "__main__":
    unittest.main()
