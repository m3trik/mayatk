# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.unity_bridge.

Maya-side coverage for the Unity hand-off: the engine resolves the selection, exports an FBX
(shared MayaExportMixin), and copies it into a Unity project's ``Assets/`` (shared
unitytk.CopyToAssetsDeliverer Strategy). No real Unity is needed -- the project is just a folder
with an ``Assets/`` directory, and ``LAUNCH_MODE`` defaults to no-launch.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py unity_bridge``).
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import maya.cmds as cmds

from pythontk.core_utils.app_handoff import HandoffRequest
from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge

from base_test import MayaTkTestCase


def _copy_req():
    return HandoffRequest(template="copy_to_assets", mode="send_to")


class TestUnityBridgeUnit(unittest.TestCase):
    """Pure (no Maya geometry) -- composition + delivery modes."""

    def test_delivery_modes(self):
        self.assertEqual(UnityBridge.list_delivery_modes(), [("copy_to_assets", "")])

    def test_params_defaults(self):
        d = UnityBridge().params_defaults()
        self.assertEqual(d["ASSETS_SUBDIR"], "Imported")
        self.assertEqual(d["LAUNCH_MODE"], "")  # no-launch default
        self.assertTrue(d["INCLUDE_MATERIALS"])
        self.assertEqual(d["SCOPE"], "selected")
        self.assertEqual(d["UNITY_VERSION"], "")  # auto/newest

    def test_slot_delivery_and_manage_modes_no_studio(self):
        """The slot exposes copy-to-Assets plus the Manage Unity Scripts flow;
        the 'Unity Studio' mode is gone.

        Unity Studio is a separate paid, browser-based product (Unity Cloud Asset
        Manager), not this desktop FBX hand-off, so it was removed. Script
        management is panel-native (its own SCRIPTS checklist + SCRIPTS_ACTION
        parameters), not a buried menu button.
        """
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import (
            UnityBridgeSlots as S,
        )

        self.assertEqual(S.MODE_COPY, "copy_to_assets")
        self.assertEqual(S.MODE_MANAGE, "manage_scripts")
        self.assertEqual(
            S.MODE_LABELS,
            {S.MODE_COPY: "Copy to Project", S.MODE_MANAGE: "Manage Unity Scripts"},
        )
        self.assertEqual(
            S.list_template_modes(S), [(S.MODE_COPY, ""), (S.MODE_MANAGE, "")]
        )
        # The management flow is a template, not a field-menu button.
        self.assertTrue(hasattr(S, "_manage_unity_scripts"))
        self.assertFalse(hasattr(S, "_deploy_unity_scripts"))
        # No leftover Unity Studio surface.
        self.assertFalse(hasattr(S, "MODE_STUDIO"))
        self.assertFalse(hasattr(S, "MODE_EXISTING"))
        self.assertFalse(hasattr(S, "MODE_PARAM_KEYS"))

    def test_script_params_are_a_runtime_populated_checklist(self):
        """The Manage Unity Scripts mode owns a checkable script list + the
        action combo; the list's entries are filled from the installed unitytk
        release at panel init, so the registry hard-codes no C# filenames."""
        from mayatk.env_utils.unity_bridge import parameters as P
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import (
            UnityBridgeSlots as S,
        )

        spec = P.PARAMS["SCRIPTS"]
        self.assertEqual(spec.kind, "check_list")
        self.assertEqual(list(spec.choices), [])  # runtime-populated
        self.assertEqual(spec.default, [])
        self.assertEqual(S.SCRIPT_PARAM_KEYS, frozenset({"SCRIPTS", "SCRIPTS_ACTION"}))
        # Both are gated off the export mode (they only apply to manage mode).
        self.assertTrue(S.SCRIPT_PARAM_KEYS.issubset(set(P.PARAMS)))
        self.assertTrue(hasattr(S, "_populate_script_components"))
        self.assertTrue(hasattr(S, "_script_selection"))

    def test_deployable_components_back_the_checklist(self):
        """The checklist rows are unitytk's deployable components -- optional
        controllers only; the core files ride along with every install."""
        from unitytk import TemplateDeployer

        comps = TemplateDeployer.components()
        self.assertTrue(comps[0].required and comps[0].key == "core")
        optional = [c for c in comps if not c.required]
        self.assertIn("audio_event", [c.key for c in optional])
        self.assertTrue(all(c.label and c.files for c in optional))

    def test_preflight_requires_project(self):
        br = UnityBridge()
        self.assertFalse(br.deliverer.preflight(br, _copy_req()))  # no project set

    def test_preflight_rejects_non_project_dir(self):
        tmp = tempfile.mkdtemp()
        try:
            br = UnityBridge(project_path=tmp)  # no Assets/ subdir
            self.assertFalse(br.deliverer.preflight(br, _copy_req()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestUnityBridgeSend(MayaTkTestCase):
    """End-to-end: real Maya export -> copy into a temp Unity project's Assets/."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="unity_bridge_test_"))
        self.project = self.tmp / "UnityProj"
        (self.project / "Assets").mkdir(parents=True)
        self.bridge = UnityBridge(project_path=str(self.project))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_send_copies_named_fbx_into_assets_subdir(self):
        cube = cmds.polyCube(name="UnityHero")[0]
        result = self.bridge.send(
            [cube],
            template="copy_to_assets",
            mode="send_to",
            params={"ASSETS_SUBDIR": "Models", "ASSET_NAME": ""},
        )
        self.assertIsNotNone(result, "send returned None (delivery failed)")
        dest = Path(result["asset"])
        # Asset named after the selected object, under Assets/Models.
        self.assertEqual(dest, self.project / "Assets" / "Models" / "UnityHero.fbx")
        self.assertTrue(dest.is_file(), f"FBX not copied to {dest}")
        self.assertGreater(dest.stat().st_size, 0, "copied FBX is empty")
        self.assertFalse(result["launched"])

    def test_send_default_subdir_and_explicit_name(self):
        cube = cmds.polyCube(name="UnityCube")[0]
        result = self.bridge.send(
            [cube],
            template="copy_to_assets",
            mode="send_to",
            params={"ASSET_NAME": "Custom/Name"},
        )
        dest = Path(result["asset"])
        # Default subdir 'Imported'; name sanitized.
        self.assertEqual(dest, self.project / "Assets" / "Imported" / "Custom_Name.fbx")
        self.assertTrue(dest.is_file())

    def test_send_aborts_when_no_project(self):
        cube = cmds.polyCube(name="UnityNoProj")[0]
        self.bridge.project_path = str(self.tmp / "not_a_project")
        result = self.bridge.send([cube], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)

    def test_send_aborts_with_empty_selection(self):
        result = self.bridge.send([], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)


_MISSING = object()  # "not passed" -- distinct from an explicit bridge=None


class _ScopeSelf:
    """The only part of a slots instance ``resolve_scope_objects`` reads."""

    def __init__(self, bridge):
        if bridge is not None:
            self.bridge = bridge  # absent entirely when None -- the no-hook case


class TestUnityScopeResolution(MayaTkTestCase):
    """Scope resolution for the shared ``SCOPE`` param.

    Exercised through ``UnityBridgeSlots`` but IMPLEMENTED once on
    :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase`, so every
    Maya bridge (Blender / Unity / Marmoset / Substance / Rizom) resolves scope
    identically. It reads only ``self.bridge`` (for the whole-scene hook), so it
    runs against a stub without building the Qt panel."""

    def _resolve(self, scope, bridge=_MISSING):
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots

        # ``self`` is only read for ``.bridge`` (the whole-scene hook), so a stub
        # carrying one is enough -- no Qt panel needed. Default: the real bridge,
        # i.e. the production path. Pass ``bridge=None`` for the no-hook fallback.
        stub = _ScopeSelf(UnityBridge() if bridge is _MISSING else bridge)
        return UnityBridgeSlots.resolve_scope_objects(stub, scope)

    def test_resolver_is_shared_not_per_bridge(self):
        """Every Maya bridge slot must inherit the ONE resolver, not carry a copy."""
        from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots
        from mayatk.env_utils.blender_bridge.blender_bridge_slots import (
            BlenderBridgeSlots,
        )
        from mayatk.mat_utils.marmoset_bridge.marmoset_bridge_slots import (
            MarmosetBridgeSlots,
        )
        from mayatk.mat_utils.substance_bridge.substance_bridge_slots import (
            SubstanceBridgeSlots,
        )
        from mayatk.uv_utils.rizom_bridge.rizom_bridge_slots import RizomBridgeSlots

        shared = MayaBridgeSlotsBase.resolve_scope_objects
        for slots in (
            UnityBridgeSlots,
            BlenderBridgeSlots,
            MarmosetBridgeSlots,
            SubstanceBridgeSlots,
            RizomBridgeSlots,
        ):
            self.assertIs(slots.resolve_scope_objects, shared, slots.__name__)

    def test_unknown_scope_falls_back_to_selection(self):
        """An unrecognised scope must never silently widen the send."""
        cmds.polyCube()
        cmds.select(clear=True)
        self.assertEqual(self._resolve("bogus"), [])

    def _mesh_count(self, objs):
        """Meshes REACHABLE from *objs* -- the resolver may return roots or shapes."""
        found = set()
        for o in objs:
            o = str(o)
            if cmds.objectType(o) == "mesh":
                found.add(o)
            found.update(
                cmds.listRelatives(o, allDescendents=True, type="mesh", fullPath=True)
                or []
            )
        return len(cmds.ls(list(found), noIntermediate=True, long=True) or [])

    def test_scope_all_covers_every_scene_mesh(self):
        cmds.polyCube()
        cmds.polySphere()
        cmds.select(clear=True)  # 'all' ignores the selection
        self.assertGreaterEqual(self._mesh_count(self._resolve("all")), 2)

    def test_scope_all_returns_roots_so_the_hierarchy_travels(self):
        """A mesh-only "all" drops group transforms, and the exporter then re-roots
        every child on the far side -- so the whole-scene hook returns DAG roots."""
        cube = cmds.polyCube(name="scope_grouped")[0]
        grp = cmds.group(cube, name="scope_group")
        cmds.select(clear=True)
        resolved = [str(o) for o in self._resolve("all")]
        self.assertTrue(any(o.split("|")[-1] == grp for o in resolved), resolved)

    def test_scope_all_without_the_hook_falls_back_to_meshes(self):
        """Documented fallback: a slots instance with no bridge (or a bridge with no
        whole-scene hook) still resolves "all" to the renderable geometry."""
        cmds.polyCube()
        cmds.select(clear=True)
        resolved = self._resolve("all", bridge=None)
        self.assertTrue(resolved)
        self.assertTrue(
            all(cmds.objectType(o) == "mesh" for o in resolved), resolved
        )

    def test_scope_selected_uses_selection_only(self):
        cube = cmds.polyCube()[0]
        cmds.polySphere()
        cmds.select(cube, replace=True)
        resolved = self._resolve("selected")
        self.assertTrue(any(cube in str(o) for o in resolved))
        # The unselected sphere transform must not be in the selected scope.
        self.assertFalse(any("Sphere" in str(o) for o in resolved))

    def test_scope_visible_excludes_hidden(self):
        cmds.polyCube(name="VisibleCube")[0]
        hidden = cmds.polyCube(name="HiddenCube")[0]
        cmds.setAttr(f"{hidden}.visibility", 0)
        cmds.select(clear=True)
        resolved = [str(o) for o in self._resolve("visible")]
        self.assertTrue(any("VisibleCube" in o for o in resolved))
        self.assertFalse(any("HiddenCube" in o for o in resolved))


if __name__ == "__main__":
    unittest.main()
