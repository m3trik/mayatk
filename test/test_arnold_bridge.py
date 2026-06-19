# !/usr/bin/python
# coding=utf-8
"""Tests for ArnoldBridge — add/remove/rebuild/idempotency and scope handling.

Requires a live Maya runtime with the MtoA (Arnold) plugin available; the whole
case skips cleanly when ``mtoa`` cannot be loaded (e.g. CI without Arnold).

Run headless::

    & "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        o:/Cloud/Code/_scripts/mayatk/test/test_arnold_bridge.py
"""
import os
import sys
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
mayatk_dir = os.path.join(scripts_dir, "mayatk")
if mayatk_dir not in sys.path:
    sys.path.insert(0, mayatk_dir)

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import mayatk as mtk
from mayatk.mat_utils.arnold_bridge import ArnoldBridgeSlots

ArnoldBridge = mtk.ArnoldBridge


# --- Stubs to drive ArnoldBridgeSlots headlessly ---------------------------
# The offscreen QPA platform can't load the real Switchboard panel, so the slot
# logic is exercised against real Maya geometry through minimal widget stubs.
class _StubFooter:
    def __init__(self):
        self.text = ""

    def setText(self, t):
        self.text = t

    def progress(self, text=""):
        import contextlib

        self.text = text
        return contextlib.nullcontext()


class _StubCombo:
    def __init__(self, text):
        self._text = text

    def currentText(self):
        return self._text


class _StubCheck:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _StubUi:
    def __init__(self, scope, force):
        self.cmb000 = _StubCombo(scope)
        self.chk000 = _StubCheck(force)
        self.footer = _StubFooter()


class _StubSb:
    def __init__(self, ui):
        self.loaded_ui = type("LoadedUi", (), {"arnold_bridge": ui})()


def _mtoa_available() -> bool:
    """True if the Arnold plugin can be loaded (so aiStandardSurface exists)."""
    try:
        if not cmds.pluginInfo("mtoa", query=True, loaded=True):
            cmds.loadPlugin("mtoa", quiet=True)
        return bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
    except Exception:
        return False


def _file_count() -> int:
    return len(cmds.ls(type="file") or [])


def _ai_count() -> int:
    return len(cmds.ls(type="aiStandardSurface") or [])


class ArnoldBridgeTest(unittest.TestCase):
    # color3 attrs on standardSurface to park base file nodes on so they land
    # in the material's upstream history (which attr is irrelevant to the
    # bridge — it resolves map type from the file name, not the slot).
    PARK_ATTRS = ["baseColor", "coatColor", "emissionColor", "specularColor"]

    @classmethod
    def setUpClass(cls):
        # Deferred until after standalone init (decoration-time cmds calls would
        # run before maya.standalone.initialize() and falsely skip everything).
        if not _mtoa_available():
            raise unittest.SkipTest("mtoa (Arnold) plugin not available")

    def setUp(self):
        cmds.file(new=True, force=True)
        self.bridge = ArnoldBridge()

    def _make_base_material(self, name, map_names):
        """Create a standardSurface + SG with a `file` node per map name."""
        shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)

        file_nodes = []
        for i, mname in enumerate(map_names):
            fn = cmds.shadingNode("file", asTexture=True, name=f"{name}_file{i}")
            cmds.setAttr(f"{fn}.fileTextureName", mname, type="string")
            cmds.connectAttr(
                f"{fn}.outColor",
                f"{shader}.{self.PARK_ATTRS[i % len(self.PARK_ATTRS)]}",
                force=True,
            )
            file_nodes.append(fn)
        return shader, sg, file_nodes

    def _bridge_file_nodes(self, material):
        ai = self.bridge.get_bridge(material)
        hist = cmds.listHistory(ai) or []
        return cmds.ls(hist, type="file") or []

    # ------------------------------------------------------------------ add
    def test_no_bridge_initially(self):
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.assertFalse(self.bridge.has_bridge(shader))
        self.assertIsNone(self.bridge.get_bridge(shader))

    def test_add_creates_dedicated_bridge(self):
        maps = ["model_BaseColor.png", "model_Roughness.png", "model_Normal_OpenGL.png"]
        shader, sg, base_files = self._make_base_material("matA", maps)
        base_file_set = set(base_files)
        base_file_count = _file_count()

        result = self.bridge.add(materials=shader)
        self.assertEqual(len(result), 1)

        # Bridge exists and drives the SG's aiSurfaceShader slot.
        ai = self.bridge.get_bridge(shader)
        self.assertIsNotNone(ai)
        self.assertEqual(cmds.nodeType(ai), "aiStandardSurface")
        driver = cmds.listConnections(
            f"{sg}.aiSurfaceShader", source=True, destination=False
        )
        self.assertEqual(driver, [ai])

        # Base material still drives surfaceShader (untouched).
        self.assertEqual(
            cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False),
            [shader],
        )

        # Dedicated file nodes: bridge made its own, none shared with the base.
        bridge_files = self._bridge_file_nodes(shader)
        expected = self.bridge._iter_base_textures(shader)
        self.assertEqual(len(bridge_files), len(expected))
        self.assertTrue(set(bridge_files).isdisjoint(base_file_set))
        self.assertGreater(_file_count(), base_file_count)

    def test_add_idempotent(self):
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        first = self.bridge.get_bridge(shader)
        self.bridge.add(materials=shader)  # second add — should be a no-op
        self.assertEqual(self.bridge.get_bridge(shader), first)
        self.assertEqual(_ai_count(), 1)

    def test_add_force_rebuilds(self):
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        first_uuid = cmds.ls(self.bridge.get_bridge(shader), uuid=True)[0]
        self.bridge.add(materials=shader, force=True)
        second_uuid = cmds.ls(self.bridge.get_bridge(shader), uuid=True)[0]
        # New DG node (Maya may recycle the freed name, so compare by UUID).
        self.assertNotEqual(first_uuid, second_uuid)
        self.assertEqual(_ai_count(), 1)  # old bridge fully replaced

    # ----------------------------------------------------- robustness (scope)
    def test_get_shading_engine_nonexistent_returns_none(self):
        """A vanished node must not raise (regression: ValueError
        'No object matches name: aiMultiply1' from cmds.listConnections)."""
        self.assertIsNone(self.bridge._get_shading_engine("aiMultiply1"))
        self.assertIsNone(self.bridge.get_bridge("aiMultiply1"))
        self.assertFalse(self.bridge.has_bridge("aiMultiply1"))

    def test_add_force_with_helper_in_scope_skips_not_crashes(self):
        """A force-rebuild that deletes a bridge helper still listed later in
        scope must skip the vanished node, not crash on listConnections."""
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)  # creates the aiMultiply helper
        helper = (cmds.ls(cmds.listHistory(self.bridge.get_bridge(shader)) or [],
                          type="aiMultiply") or [None])[0]
        self.assertIsNotNone(helper, "expected an aiMultiply helper in the bridge")
        # Both the material and its own helper in scope: processing the material
        # removes the bridge (deleting `helper`); the later `helper` target is
        # then a vanished node — it must be skipped cleanly, not raise.
        result = self.bridge.add(materials=[shader, helper], force=True)
        self.assertIn(self.bridge.get_bridge(shader), result)  # rebuilt
        self.assertEqual(_ai_count(), 1)  # only the material's bridge, helper skipped

    # --------------------------------------------------------------- remove
    def test_remove_restores_base(self):
        maps = ["model_BaseColor.png", "model_Roughness.png"]
        shader, sg, base_files = self._make_base_material("matA", maps)
        base_file_count = _file_count()

        self.bridge.add(materials=shader)
        self.assertTrue(self.bridge.has_bridge(shader))

        self.bridge.remove(materials=shader)

        # Bridge gone; SG.aiSurfaceShader cleared; no Arnold shaders linger.
        self.assertFalse(self.bridge.has_bridge(shader))
        self.assertFalse(
            cmds.listConnections(
                f"{sg}.aiSurfaceShader", source=True, destination=False
            )
        )
        self.assertEqual(_ai_count(), 0)

        # Base material + its file nodes intact; file count back to baseline.
        self.assertTrue(cmds.objExists(shader))
        for fn in base_files:
            self.assertTrue(cmds.objExists(fn))
        self.assertEqual(_file_count(), base_file_count)

    def test_remove_without_bridge_is_noop(self):
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.assertEqual(self.bridge.remove(materials=shader), [])

    def test_rebuild(self):
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        first_uuid = cmds.ls(self.bridge.get_bridge(shader), uuid=True)[0]
        self.bridge.rebuild(materials=shader)
        self.assertTrue(self.bridge.has_bridge(shader))
        # Genuinely re-created (compare by UUID, not the recyclable name).
        self.assertNotEqual(cmds.ls(self.bridge.get_bridge(shader), uuid=True)[0], first_uuid)
        self.assertEqual(_ai_count(), 1)

    # ------------------------------------------------------------- wiring
    def test_msao_channel_routing(self):
        # A Unity HDRP mask (R=Metallic, G=AO, B=Detail, A=Smoothness) must
        # drive metalness, an inverted-smoothness roughness, and an AO multiply.
        shader, _, _ = self._make_base_material("matMSAO", ["model_MaskMap.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)

        self.assertTrue(
            cmds.listConnections(f"{ai}.metalness"), "MSAO->metalness missing"
        )
        self.assertTrue(
            cmds.listConnections(f"{ai}.specularRoughness"),
            "MSAO->roughness missing",
        )
        # Smoothness is inverted to roughness via a reverse node.
        hist = cmds.listHistory(ai) or []
        self.assertTrue(
            cmds.ls(hist, type="reverse"), "smoothness-invert reverse node missing"
        )
        # AO feeds the aiMultiply blended into base color.
        mult = cmds.listConnections(f"{ai}.baseColor", type="aiMultiply")
        self.assertTrue(mult, "aiMultiply not feeding baseColor")
        self.assertTrue(
            cmds.listConnections(f"{mult[0]}.input2"), "MSAO AO->multiply missing"
        )

    # ---------------------------------------------------------------- scope
    def test_scope_by_object(self):
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        cube = cmds.polyCube(name="pCubeBridge")[0]
        cmds.sets(cube, edit=True, forceElement=sg)

        self.bridge.add(objects=[cube])
        self.assertTrue(self.bridge.has_bridge(shader))

    def test_scope_all_textured_materials(self):
        shader_a, _, _ = self._make_base_material("matA", ["a_BaseColor.png"])
        shader_b, _, _ = self._make_base_material("matB", ["b_Roughness.png"])
        cmds.select(clear=True)  # force whole-scene fallback

        self.bridge.add()
        self.assertTrue(self.bridge.has_bridge(shader_a))
        self.assertTrue(self.bridge.has_bridge(shader_b))

    def test_scene_fallback_skips_textureless(self):
        # A material with no texture file nodes must not get a bridge from a
        # bare add() (protects default shaders like lambert1).
        bare = cmds.shadingNode("standardSurface", asShader=True, name="bareMat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="bareMatSG"
        )
        cmds.connectAttr(f"{bare}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.select(clear=True)

        self.bridge.add()
        self.assertFalse(self.bridge.has_bridge(bare))

    def test_explicit_textureless_material_is_bridged(self):
        # Explicit targeting bridges even a solid-color material.
        bare = cmds.shadingNode("standardSurface", asShader=True, name="bareMat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="bareMatSG"
        )
        cmds.connectAttr(f"{bare}.outColor", f"{sg}.surfaceShader", force=True)

        self.bridge.add(materials=bare)
        self.assertTrue(self.bridge.has_bridge(bare))


class ArnoldBridgeSlotsTest(unittest.TestCase):
    """ArnoldBridgeSlots driven through stubbed widgets against real geometry."""

    @classmethod
    def setUpClass(cls):
        if not _mtoa_available():
            raise unittest.SkipTest("mtoa (Arnold) plugin not available")

    def setUp(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _slots(scope="Selected Objects", force=False):
        return ArnoldBridgeSlots(_StubSb(_StubUi(scope, force)))

    def _textured_cube(self, name):
        """A standardSurface (with one texture) assigned to a new cube."""
        shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{fn}.fileTextureName", f"{name}_BaseColor.png", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{shader}.baseColor", force=True)
        cube = cmds.polyCube(name=f"{name}_cube")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        return shader, cube

    def test_add_remove_selected_scope(self):
        shader, cube = self._textured_cube("matSel")
        slots = self._slots(scope="Selected Objects")
        cmds.select(cube, replace=True)

        slots.b000()  # Add
        self.assertTrue(slots._bridge.has_bridge(shader))
        self.assertIn("Added 1", slots.ui.footer.text)

        slots.b001()  # Remove
        self.assertFalse(slots._bridge.has_bridge(shader))
        self.assertIn("Removed 1", slots.ui.footer.text)

    def test_add_empty_selection_warns_no_bridge(self):
        shader, _ = self._textured_cube("matSel")
        slots = self._slots(scope="Selected Objects")
        cmds.select(clear=True)

        slots.b000()
        self.assertFalse(slots._bridge.has_bridge(shader))
        self.assertIn("Select object", slots.ui.footer.text)

    def test_add_all_scene_scope(self):
        shader_a, _ = self._textured_cube("matA")
        shader_b, _ = self._textured_cube("matB")
        slots = self._slots(scope="All Scene Materials")
        # Selection is irrelevant for the All Scene Materials scope.

        slots.b000()
        self.assertTrue(slots._bridge.has_bridge(shader_a))
        self.assertTrue(slots._bridge.has_bridge(shader_b))

    def test_force_rebuilds(self):
        shader, cube = self._textured_cube("matSel")
        cmds.select(cube, replace=True)
        self._slots().b000()  # initial add (no force)
        first_uuid = cmds.ls(ArnoldBridge().get_bridge(shader), uuid=True)[0]

        self._slots(force=True).b000()  # force → rebuild
        second_uuid = cmds.ls(ArnoldBridge().get_bridge(shader), uuid=True)[0]
        self.assertNotEqual(first_uuid, second_uuid)

    def test_rebuild_button(self):
        shader, cube = self._textured_cube("matSel")
        cmds.select(cube, replace=True)
        slots = self._slots()
        slots.b000()
        first_uuid = cmds.ls(slots._bridge.get_bridge(shader), uuid=True)[0]
        slots.b002()  # Rebuild
        self.assertTrue(slots._bridge.has_bridge(shader))
        self.assertNotEqual(
            cmds.ls(slots._bridge.get_bridge(shader), uuid=True)[0], first_uuid
        )

    def test_select_bridged(self):
        shader, cube = self._textured_cube("matSel")
        cmds.select(cube, replace=True)
        slots = self._slots()
        slots.b000()
        bridge_shader = ArnoldBridge().get_bridge(shader)
        cmds.select(clear=True)

        slots.select_bridged()
        sel = cmds.ls(selection=True) or []
        self.assertIn(shader, sel)
        # The aiStandardSurface bridge shader itself must not be selected.
        self.assertNotIn(bridge_shader, sel)


if __name__ == "__main__":
    import maya.standalone

    try:
        cmds.about(version=True)
    except Exception:
        maya.standalone.initialize(name="python")

    unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2)

    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
