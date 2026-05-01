# !/usr/bin/python
# coding=utf-8
"""Test Suite for remaining modules (node_icons, hdr_manager, controls, shadow_rig).

Covers headless-safe testable surfaces; UI slot classes are skipped.
"""
import unittest

import maya.cmds as cmds

from mayatk.ui_utils.node_icons import NodeIcons
from mayatk.light_utils.hdr_manager import HdrManager
from mayatk.rig_utils.controls import Controls, ControlNodes
from mayatk.rig_utils.shadow_rig import ShadowRig

from base_test import MayaTkTestCase, QuickTestCase


# ============================================================
# NodeIcons
# ============================================================


class TestNodeIcons(MayaTkTestCase):
    """NodeIcons — icon-name resolution + Qt loading."""

    def test_icon_name_for_type_format(self):
        self.assertEqual(NodeIcons.icon_name_for_type("mesh"), "out_mesh.png")
        self.assertEqual(NodeIcons.icon_name_for_type("camera"), "out_camera.png")

    def test_icon_name_for_nonexistent_node_returns_none(self):
        self.assertIsNone(NodeIcons.icon_name_for_node("does_not_exist"))

    def test_icon_name_for_polycube_resolves_to_mesh(self):
        cube = cmds.polyCube(name="ni_cube")[0]
        # Transform containing a mesh shape should resolve to mesh icon
        result = NodeIcons.icon_name_for_node(cube)
        self.assertEqual(result, "out_mesh.png")

    def test_icon_name_for_locator_resolves_to_locator(self):
        loc = cmds.spaceLocator(name="ni_loc")[0]
        result = NodeIcons.icon_name_for_node(loc)
        self.assertEqual(result, "out_locator.png")

    def test_icon_name_for_camera_resolves_to_camera(self):
        cam = cmds.camera(name="ni_cam")[0]
        result = NodeIcons.icon_name_for_node(cam)
        self.assertEqual(result, "out_camera.png")

    def test_get_icon_returns_none_for_nonexistent(self):
        self.assertIsNone(NodeIcons.get_icon("nonexistent_node"))

    def test_get_pixmap_returns_none_for_nonexistent(self):
        self.assertIsNone(NodeIcons.get_pixmap("nonexistent_node"))


# ============================================================
# HdrManager
# ============================================================


class TestHdrManagerProperty(MayaTkTestCase):
    """HdrManager.hdr_env property — getter returns None when no skydome exists."""

    def test_default_hdr_env_is_none(self):
        mgr = HdrManager()
        # Fresh scene has no aiSkyDomeLight named "aiSkyDomeLight_"
        self.assertIsNone(mgr.hdr_env)

    def test_class_constant(self):
        self.assertEqual(HdrManager.hdr_env_name, "aiSkyDomeLight_")

    def test_hdr_env_transform_returns_none_when_no_env(self):
        mgr = HdrManager()
        self.assertIsNone(mgr.hdr_env_transform)


# ============================================================
# Controls
# ============================================================


class TestControlsAxisToRotation(QuickTestCase):
    """Controls._axis_to_rotation — pure math."""

    def test_y_returns_zero_rotation(self):
        self.assertEqual(Controls._axis_to_rotation("y"), (0.0, 0.0, 0.0))

    def test_signed_axis(self):
        self.assertEqual(Controls._axis_to_rotation("+y"), (0.0, 0.0, 0.0))
        self.assertEqual(Controls._axis_to_rotation("-y"), (180.0, 0.0, 0.0))

    def test_x_axis(self):
        self.assertEqual(Controls._axis_to_rotation("x"), (0.0, 0.0, -90.0))
        self.assertEqual(Controls._axis_to_rotation("-x"), (0.0, 0.0, 90.0))

    def test_z_axis(self):
        self.assertEqual(Controls._axis_to_rotation("z"), (90.0, 0.0, 0.0))
        self.assertEqual(Controls._axis_to_rotation("-z"), (-90.0, 0.0, 0.0))

    def test_default_axis_is_y(self):
        # None / empty str defaults to y
        self.assertEqual(Controls._axis_to_rotation(""), (0.0, 0.0, 0.0))

    def test_invalid_axis_raises(self):
        with self.assertRaises(ValueError):
            Controls._axis_to_rotation("w")


class TestControlsPresets(MayaTkTestCase):
    """Controls preset registry + dynamic creation."""

    def test_register_preset_with_empty_name_raises(self):
        with self.assertRaises(ValueError):
            Controls.register_preset("", lambda **k: None)

    def test_register_preset_stores_in_lowercase(self):
        Controls.register_preset("MyPreset", lambda name, **k: cmds.spaceLocator(name=name)[0])
        try:
            self.assertIn("mypreset", Controls._PRESETS)
        finally:
            # Restore registry
            Controls._PRESETS.pop("mypreset", None)

    def test_create_diamond_preset(self):
        # Builtin presets register on first access
        ctrl = Controls.create("diamond", name="test_diamond_ctrl", offset_group=False)
        self.assertTrue(cmds.objExists(ctrl))

    def test_create_box_preset(self):
        ctrl = Controls.create("box", name="test_box_ctrl", offset_group=False)
        self.assertTrue(cmds.objExists(ctrl))


class TestControlNodesDataclass(QuickTestCase):
    """ControlNodes is a frozen dataclass — control required, group optional."""

    def test_default_group_is_none(self):
        cn = ControlNodes(control="ctl_x")
        self.assertEqual(cn.control, "ctl_x")
        self.assertIsNone(cn.group)

    def test_explicit_group(self):
        cn = ControlNodes(control="ctl_y", group="grp_y")
        self.assertEqual(cn.control, "ctl_y")
        self.assertEqual(cn.group, "grp_y")

    def test_frozen_immutable(self):
        cn = ControlNodes(control="ctl_z")
        with self.assertRaises(Exception):  # FrozenInstanceError
            cn.control = "other"


# ============================================================
# ShadowRig
# ============================================================


class TestShadowRigConstruction(QuickTestCase):
    """ShadowRig __init__ + state."""

    def test_modes_constant(self):
        self.assertEqual(ShadowRig.MODES, ("orbit", "stretch"))

    def test_default_targets_empty(self):
        rig = ShadowRig()
        self.assertEqual(rig.targets, [])

    def test_single_target_wrapped_in_list(self):
        rig = ShadowRig(targets="my_obj")
        self.assertEqual(rig.targets, ["my_obj"])

    def test_list_target_kept_as_list(self):
        rig = ShadowRig(targets=["a", "b"])
        self.assertEqual(rig.targets, ["a", "b"])

    def test_invalid_mode_falls_back_to_stretch(self):
        rig = ShadowRig(mode="unknown")
        self.assertEqual(rig.mode, "stretch")

    def test_orbit_mode_accepted(self):
        rig = ShadowRig(mode="orbit")
        self.assertEqual(rig.mode, "orbit")

    def test_name_base_single_target(self):
        rig = ShadowRig(targets="hero")
        self.assertEqual(rig._name_base, "hero")

    def test_name_base_multiple_targets(self):
        rig = ShadowRig(targets=["a", "b", "c"])
        self.assertEqual(rig._name_base, "combined")

    def test_initial_state_none(self):
        rig = ShadowRig()
        self.assertIsNone(rig.shadow_plane)
        self.assertIsNone(rig.contact_locator)
        self.assertIsNone(rig.shader)
        self.assertIsNone(rig.opacity_mult)
        self.assertIsNone(rig.texture_path)
        self.assertEqual(rig.ground_height, 0.0)


class TestShadowRigSourceCreation(MayaTkTestCase):
    """ShadowRig.get_or_create_shadow_source — locator factory."""

    def test_creates_new_shadow_source(self):
        cube = cmds.polyCube(name="sr_target")[0]
        rig = ShadowRig(targets=[cube])
        result = rig.get_or_create_shadow_source(
            position=(2, 5, 2), source_name="my_shadow_source"
        )
        self.assertTrue(cmds.objExists("my_shadow_source"))
        self.assertEqual(rig.light, "my_shadow_source")

    def test_reuses_existing_shadow_source(self):
        cube = cmds.polyCube(name="sr_existing")[0]
        # Pre-create the source
        existing = cmds.spaceLocator(name="preexisting_source")[0]

        rig = ShadowRig(targets=[cube])
        rig.get_or_create_shadow_source(source_name="preexisting_source")
        # Should reuse it, not create a duplicate
        self.assertEqual(rig.light, "preexisting_source")


if __name__ == "__main__":
    unittest.main()
