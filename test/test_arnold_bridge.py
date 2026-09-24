# !/usr/bin/python
# coding=utf-8
"""Tests for ArnoldBridge — add/remove/rebuild/idempotency and scope handling.

Requires a live Maya runtime with the MtoA (Arnold) plugin available; the whole
case skips cleanly when ``mtoa`` cannot be loaded (e.g. CI without Arnold).

Run headless (from the workspace root)::

    & "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        mayatk/test/test_arnold_bridge.py
"""

import sys
import unittest
from unittest import mock

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import mayatk as mtk
from mayatk.mat_utils._mat_utils import MatUtils
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

    @staticmethod
    def _add_group(shader, name):
        """Another shading group fed by *shader*, holding a cube -- the
        production shape: a material consolidated across objects keeps one
        group per object (an FBX import mints one per mesh)."""
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cmds.polyCube(name=f"{name}_geo")[0], edit=True, forceElement=sg)
        return sg

    @staticmethod
    def _slot(sg):
        src = cmds.listConnections(
            f"{sg}.aiSurfaceShader", source=True, destination=False
        )
        return src[0] if src else None

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
        # second add — should be a no-op, and report nothing done
        self.assertEqual(self.bridge.add(materials=shader), [])
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

    # ------------------------------------------------ a material on many groups
    def test_add_bridges_every_group_the_material_drives(self):
        """A material on several shading groups renders in Arnold through ALL
        of them. The bridge rode the material's first group only, so every
        other group rendered error magenta -- and in a lightmap bake that
        magenta bounced onto the floor (production soldering room, 2026-09-23:
        the bench's legs sat on a second group of the top's material)."""
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        legs = self._add_group(shader, "legs")
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        self.assertIsNotNone(ai)
        self.assertEqual((self._slot(top), self._slot(legs)), (ai, ai))
        self.assertEqual(_ai_count(), 1, "one network, shared by the groups")

    def test_add_extends_a_bridge_into_the_groups_it_misses(self):
        """A scene bridged before that holds a bridge on one group only: a
        second add completes it instead of skipping the bridged material."""
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        legs = self._add_group(shader, "legs")
        # Reported as touched, so the panel's count says what the add did.
        self.assertEqual(self.bridge.add(materials=shader), [ai])
        self.assertEqual((self._slot(top), self._slot(legs)), (ai, ai))
        self.assertEqual(_ai_count(), 1)

    def test_add_never_replaces_a_groups_own_override(self):
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        legs = self._add_group(shader, "legs")
        own = cmds.shadingNode("standardSurface", asShader=True, name="legs_own")
        cmds.connectAttr(f"{own}.outColor", f"{legs}.aiSurfaceShader", force=True)
        self.bridge.add(materials=shader)
        self.assertEqual(self._slot(legs), own)
        self.assertIsNotNone(self._slot(top), "the open group was left magenta")

    def test_remove_clears_every_group(self):
        """Remove leaves no Arnold override on any of the material's groups,
        not only on the first one's."""
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        legs = self._add_group(shader, "legs")
        for sg in (top, legs):
            ai = cmds.shadingNode("aiStandardSurface", asShader=True, name=f"{sg}_ai")
            cmds.connectAttr(f"{ai}.outColor", f"{sg}.aiSurfaceShader", force=True)
        self.bridge.remove(materials=shader)
        self.assertEqual((self._slot(top), self._slot(legs)), (None, None))
        self.assertFalse(self.bridge.has_bridge(shader))

    def _lookdev_override(self, sg):
        """An assigned material of its own (a cube renders it), wired as *sg*'s
        Arnold override -- a look shared from elsewhere in the scene."""
        look, look_sg, look_files = self._make_base_material(
            "lookdev", ["look_BaseColor.png"]
        )
        cmds.sets(cmds.polyCube(name="lookdev_geo")[0], edit=True, forceElement=look_sg)
        cmds.connectAttr(f"{look}.outColor", f"{sg}.aiSurfaceShader", force=True)
        return look, look_sg, look_files

    def test_remove_never_deletes_a_material_assigned_in_its_own_right(self):
        """An override that renders objects of its own is unwired, never
        deleted: Remove used to take the material and its textures with it."""
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        look, _look_sg, look_files = self._lookdev_override(sg)
        self.assertEqual(self.bridge.remove(materials=shader), [shader])
        self.assertIsNone(self._slot(sg))
        self.assertTrue(cmds.objExists(look))
        self.assertTrue(all(cmds.objExists(f) for f in look_files))

    def test_a_material_overriding_another_group_still_gets_its_own_bridge(self):
        """A group a material only overrides is another material's: it is
        neither the override's bridge nor one of its own groups."""
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        look, look_sg, _ = self._lookdev_override(sg)
        self.bridge.add(materials=look)
        ai = self._slot(look_sg)
        self.assertIsNotNone(ai, "the override material got no bridge")
        self.assertEqual(cmds.nodeType(ai), "aiStandardSurface")
        self.assertEqual(self._slot(sg), look, "matA's override was touched")

    # ------------------------------------------------------------- temporary
    def test_temporary_bridges_for_the_block_and_leaves_nothing(self):
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        legs = self._add_group(shader, "legs")
        files = _file_count()
        with self.bridge.temporary(shader) as filled:
            self.assertEqual(sorted(filled), sorted([top, legs]))
            self.assertIsNotNone(self._slot(top))
        self.assertEqual((self._slot(top), self._slot(legs)), (None, None))
        self.assertEqual((_ai_count(), _file_count()), (0, files))

    def test_temporary_lends_an_authored_bridge_and_takes_it_back(self):
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        authored = cmds.shadingNode("aiStandardSurface", asShader=True, name="own_ai")
        cmds.connectAttr(f"{authored}.outColor", f"{top}.aiSurfaceShader", force=True)
        legs = self._add_group(shader, "legs")
        with self.bridge.temporary([shader]) as filled:
            self.assertEqual((filled, self._slot(legs)), ([legs], authored))
        self.assertEqual((self._slot(top), self._slot(legs)), (authored, None))
        self.assertEqual(_ai_count(), 1)

    def test_temporary_puts_the_slots_back_when_the_block_raises(self):
        shader, top, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        with self.assertRaises(RuntimeError):
            with self.bridge.temporary(shader):
                raise RuntimeError("render failed")
        self.assertIsNone(self._slot(top))
        self.assertEqual(_ai_count(), 0)
        self.assertEqual(
            cmds.listConnections(
                f"{top}.surfaceShader", source=True, destination=False
            ),
            [shader],
        )

    def test_temporary_takes_back_a_bridge_left_half_built(self):
        """An add that fails part-way raises out of the block's entry with its
        own error, and nothing it built stays: neither the material bridged
        before the failure nor the network it failed on, textures included."""
        first, first_sg, _ = self._make_base_material("matA", ["a_BaseColor.png"])
        second, second_sg, _ = self._make_base_material("matB", ["b_BaseColor.png"])
        files = _file_count()
        real = ArnoldBridge._connect_texture
        calls = []

        def fail_second(bridge, *args, **kwargs):
            calls.append(args[0])
            if len(calls) > 1:
                raise RuntimeError("texture would not wire")
            return real(bridge, *args, **kwargs)

        with mock.patch.object(ArnoldBridge, "_connect_texture", fail_second):
            with self.assertRaises(RuntimeError) as caught:
                with self.bridge.temporary([first, second]):
                    self.fail("the block ran on a half-built bridge")
        self.assertIn("would not wire", str(caught.exception))
        self.assertEqual((self._slot(first_sg), self._slot(second_sg)), (None, None))
        self.assertEqual((_ai_count(), _file_count()), (0, files))

    def test_helpers_are_utility_nodes_not_materials(self):
        """The aiMultiply / bump2d helpers must not register as shaders.

        Creating them with ``shadingNode -asShader`` parked them in
        ``defaultShaderList1``, so ``cmds.ls(materials=True)`` (and every
        materials list built on it, including tentacle's materials combo)
        listed 'aiMultiply1' / 'bump2d1' alongside real shaders.
        """
        maps = ["model_BaseColor.png", "model_Normal_OpenGL.png"]
        shader, _, _ = self._make_base_material("matA", maps)
        self.bridge.add(materials=shader)

        ai = self.bridge.get_bridge(shader)
        helpers = (
            cmds.ls(cmds.listHistory(ai) or [], type=["aiMultiply", "bump2d"]) or []
        )
        self.assertTrue(helpers, "expected aiMultiply + bump2d helpers")

        mats = set(cmds.ls(materials=True) or [])
        leaked = mats.intersection(helpers)
        self.assertFalse(leaked, f"bridge helpers registered as shaders: {leaked}")
        # The bridge shader itself IS a material and must stay listed.
        self.assertIn(ai, mats)

    def test_bridge_file_nodes_hidden_by_exc_classification(self):
        """The Texture Path Editor's "Exclude Arnold Nodes" toggle must hide these.

        The bridge owns a dedicated file node per texture, so each bridged
        material doubles the rows in the panel. Requires the bridge shader to
        be discoverable: it drives the SG's ``aiSurfaceShader`` slot, not
        ``surfaceShader``.
        """
        maps = ["model_BaseColor.png", "model_Roughness.png"]
        shader, _, base_files = self._make_base_material("matA", maps)
        self.bridge.add(materials=shader)
        bridge_files = self._bridge_file_nodes(shader)
        self.assertTrue(bridge_files, "expected dedicated bridge file nodes")

        kept = MatUtils.get_file_nodes(
            return_type="fileNodeName", exc_classification="rendernode/arnold*"
        )
        for fn in bridge_files:
            self.assertNotIn(fn, kept, f"Arnold-only file node still listed: {fn}")
        for fn in base_files:
            self.assertIn(fn, kept, f"base material's texture was hidden: {fn}")

        # Unfiltered, the bridge's textures report the bridge shader — they
        # used to come back with an empty Shader column (the SG's Arnold slot
        # wasn't read), which also made them look like unowned orphans.
        ai = self.bridge.get_bridge(shader)
        owners = dict(MatUtils.get_file_nodes(return_type="fileNodeName|shaderName"))
        for fn in bridge_files:
            self.assertEqual(owners.get(fn), ai)
        for fn in base_files:
            self.assertEqual(owners.get(fn), shader)

    def test_bridge_discoverable_via_arnold_sg_slot(self):
        """The bridge is found through the base SG's ``aiSurfaceShader`` slot alone.

        The bridge shader has no shading group of its own. It once got one from
        ``create_render_node`` -- member-less, exactly what "Delete All Unused
        Materials" removes, and one more per bake through the texture baker's
        translation guard -- so the only link from a shading group to the bridge
        is that slot, and shader discovery has to read it.
        """
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        bridge_files = self._bridge_file_nodes(shader)

        own_sgs = [
            s for s in (cmds.listConnections(ai, type="shadingEngine") or []) if s != sg
        ]
        self.assertEqual(own_sgs, [], "the bridge shader must not mint its own SG")
        self.assertEqual(
            cmds.listConnections(
                f"{sg}.aiSurfaceShader", source=True, destination=False
            ),
            [ai],
            "bridge must still drive the base SG's Arnold slot",
        )

        kept = MatUtils.get_file_nodes(
            return_type="fileNodeName", exc_classification="rendernode/arnold*"
        )
        for fn in bridge_files:
            self.assertNotIn(fn, kept, f"Arnold-only file node still listed: {fn}")

    # ----------------------------------------------------- robustness (scope)
    def test_get_shading_engines_of_a_vanished_node_is_empty(self):
        """A vanished node must not raise (regression: ValueError
        'No object matches name: aiMultiply1' from cmds.listConnections)."""
        self.assertEqual(self.bridge._get_shading_engines("aiMultiply1"), [])
        self.assertIsNone(self.bridge.get_bridge("aiMultiply1"))
        self.assertFalse(self.bridge.has_bridge("aiMultiply1"))

    def test_add_force_with_helper_in_scope_skips_not_crashes(self):
        """A force-rebuild that deletes a bridge helper still listed later in
        scope must skip the vanished node, not crash on listConnections."""
        shader, _, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)  # creates the aiMultiply helper
        helper = (
            cmds.ls(
                cmds.listHistory(self.bridge.get_bridge(shader)) or [],
                type="aiMultiply",
            )
            or [None]
        )[0]
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

    def test_a_bridge_mints_no_shading_group_and_leaves_none(self):
        """REGRESSION (2026-09-22): the bridge shader came with a shading group
        of its own that nothing was assigned to and remove() never deleted --
        the texture baker's translation guard adds and removes bridges on every
        bake, and a production room held three generations of empty
        ``<mat>_aiSG``. The bridge rides the base material's group only."""
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        before = set(cmds.ls(type="shadingEngine"))
        self.bridge.add(materials=shader)
        self.assertEqual(set(cmds.ls(type="shadingEngine")), before)
        self.bridge.remove(materials=shader)
        self.assertEqual(set(cmds.ls(type="shadingEngine")), before)

    def test_remove_clears_the_empty_group_an_older_bridge_left(self):
        """A bridge made before the fix still has its empty ``_aiSG``; removing
        it takes that group too -- but never the base material's own group,
        which the bridge feeds through aiSurfaceShader and which has members."""
        shader, sg, _ = self._make_base_material("matA", ["model_BaseColor.png"])
        cube = cmds.polyCube(name="bridgeMember")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        legacy = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{ai}SG"
        )
        cmds.connectAttr(f"{ai}.outColor", f"{legacy}.surfaceShader")
        self.bridge.remove(materials=shader)
        self.assertFalse(cmds.objExists(legacy))
        self.assertTrue(cmds.objExists(sg))
        self.assertIn(
            cmds.listRelatives(cube, shapes=True, fullPath=True)[0],
            cmds.ls(cmds.sets(sg, query=True) or [], long=True),
        )

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
        self.assertNotEqual(
            cmds.ls(self.bridge.get_bridge(shader), uuid=True)[0], first_uuid
        )
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
        # AO feeds the aiMultiply blended into base color — and it must be the
        # GREEN channel broadcast to input2R/G/B, NOT the whole packed outColor.
        # Wiring the full (metallic, AO, detail) color into the multiply zeroes
        # red on a non-metal and renders every object green (issue 3 regression).
        mult = cmds.listConnections(f"{ai}.baseColor", type="aiMultiply")
        self.assertTrue(mult, "aiMultiply not feeding baseColor")
        ao_src = (
            cmds.listConnections(
                f"{mult[0]}.input2R", source=True, destination=False, plugs=True
            )
            or []
        )
        self.assertTrue(
            ao_src, "MSAO AO must broadcast into the baseColor multiply (input2R)"
        )
        self.assertTrue(
            ao_src[0].endswith(".outColorG"),
            f"MSAO AO must come from the GREEN channel (was the green-render bug "
            f"when the whole outColor fed the multiply); got {ao_src[0]}",
        )
        # Belt-and-suspenders: the whole packed outColor must not drive the
        # multiply (the green bug wired ``file.outColor`` into the input2 compound).
        in2_srcs = (
            cmds.listConnections(
                f"{mult[0]}.input2", source=True, destination=False, plugs=True
            )
            or []
        )
        self.assertFalse(
            any(p.endswith(".outColor") for p in in2_srcs),
            "MSAO must broadcast a single channel, not the whole packed outColor",
        )
        # Smoothness lives in the packed ALPHA channel, so the file feeding
        # roughness must read the real alpha (alphaIsLuminance=0). With aIL=1
        # Maya synthesizes outAlpha from RGB luminance and silently drops
        # smoothness, driving roughness from luminance(metallic, AO, detail).
        # Walk specularRoughness ← reverse ← file to assert on the right node.
        rev = (
            cmds.listConnections(
                f"{ai}.specularRoughness",
                source=True,
                destination=False,
                type="reverse",
            )
            or []
        )
        self.assertTrue(rev, "smoothness-invert reverse not feeding roughness")
        rough_file = (
            cmds.listConnections(
                f"{rev[0]}.inputX", source=True, destination=False, type="file"
            )
            or []
        )
        self.assertTrue(rough_file, "reverse not fed by an MSAO file node")
        self.assertEqual(
            cmds.getAttr(f"{rough_file[0]}.alphaIsLuminance"),
            0,
            "MSAO smoothness must read the real alpha (aIL=0), not luminance",
        )

    def test_base_color_read_as_srgb(self):
        # Albedo is sRGB-authored, so the bridge must tag its file node sRGB.
        # Raw renders the Arnold preview too dark and breaks parity with the
        # game material (whose base color stays at the sRGB default).
        shader, _, _ = self._make_base_material("matBC", ["model_BaseColor.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        mult = cmds.listConnections(f"{ai}.baseColor", type="aiMultiply")
        self.assertTrue(mult, "aiMultiply not feeding baseColor")
        base_file = (
            cmds.listConnections(
                f"{mult[0]}.input1", source=True, destination=False, type="file"
            )
            or []
        )
        self.assertTrue(base_file, "base color file feeding the multiply missing")
        self.assertEqual(
            cmds.getAttr(f"{base_file[0]}.colorSpace"),
            "sRGB",
            "base color must be read as sRGB, not Raw",
        )

    def test_mrao_channel_routing(self):
        # MRAO: R=Metallic, G=Roughness, B=AO. Metalness + roughness (NOT
        # inverted) + an AO multiply; no reverse node since roughness is direct.
        shader, _, _ = self._make_base_material("matMRAO", ["model_MRAO.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)

        self.assertTrue(
            cmds.listConnections(f"{ai}.metalness"), "MRAO->metalness missing"
        )
        self.assertTrue(
            cmds.listConnections(f"{ai}.specularRoughness"), "MRAO->roughness missing"
        )
        hist = cmds.listHistory(ai) or []
        self.assertFalse(
            cmds.ls(hist, type="reverse"),
            "MRAO roughness is direct — it must not insert a reverse node",
        )
        # AO is broadcast to the multiply's per-channel inputs (input2R/G/B),
        # mirroring ORM; the parent compound plug reports no aggregate connection.
        mult = cmds.listConnections(f"{ai}.baseColor", type="aiMultiply")
        self.assertTrue(mult, "aiMultiply not feeding baseColor")
        self.assertTrue(
            cmds.listConnections(f"{mult[0]}.input2R"), "MRAO AO->multiply missing"
        )

    def test_specular_drives_metalness(self):
        # Specular has no aiStandardSurface analogue → used as a metalness proxy.
        shader, _, _ = self._make_base_material("matSpec", ["model_Specular.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        self.assertTrue(
            cmds.listConnections(f"{ai}.metalness"), "Specular->metalness missing"
        )

    def test_glossiness_inverts_to_roughness(self):
        shader, _, _ = self._make_base_material("matGloss", ["model_Glossiness.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        self.assertTrue(
            cmds.listConnections(f"{ai}.specularRoughness"),
            "Glossiness->roughness missing",
        )
        hist = cmds.listHistory(ai) or []
        self.assertTrue(
            cmds.ls(hist, type="reverse"),
            "Glossiness must invert to roughness via a reverse node",
        )

    def test_smoothness_inverts_to_roughness(self):
        shader, _, _ = self._make_base_material("matSmooth", ["model_Smoothness.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        self.assertTrue(
            cmds.listConnections(f"{ai}.specularRoughness"),
            "Smoothness->roughness missing",
        )
        hist = cmds.listHistory(ai) or []
        self.assertTrue(
            cmds.ls(hist, type="reverse"),
            "Smoothness must invert to roughness via a reverse node",
        )

    def test_bump_and_height_drive_object_space_bump(self):
        # Bump / Height feed the bump2d in bump mode (bumpInterp 0), unlike a
        # tangent-space normal (bumpInterp 1).
        for name, mapfile in (
            ("matBump", "model_Bump.png"),
            ("matHeight", "model_Height.png"),
        ):
            shader, _, _ = self._make_base_material(name, [mapfile])
            self.bridge.add(materials=shader)
            ai = self.bridge.get_bridge(shader)
            bump = cmds.listConnections(f"{ai}.normalCamera", type="bump2d")
            self.assertTrue(bump, f"{name}: bump2d missing")
            self.assertTrue(
                cmds.listConnections(f"{bump[0]}.bumpValue"),
                f"{name}: bumpValue not driven",
            )
            self.assertEqual(
                cmds.getAttr(f"{bump[0]}.bumpInterp"),
                0,
                f"{name}: bump/height must use bump interpretation (0)",
            )

    def test_normal_uses_tangent_space_bump(self):
        # A normal map keeps the tangent-space interpretation (bumpInterp 1).
        shader, _, _ = self._make_base_material("matN", ["model_Normal_OpenGL.png"])
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        bump = cmds.listConnections(f"{ai}.normalCamera", type="bump2d")
        self.assertTrue(bump, "bump2d missing")
        self.assertEqual(
            cmds.getAttr(f"{bump[0]}.bumpInterp"),
            1,
            "normal map must use tangent-space interpretation (1)",
        )

    # --------------------------------------------------- primary > fallback
    def test_normal_supersedes_bump_and_height(self):
        # A tangent normal wins the bump2d slot over the bump/height fallbacks
        # (which would otherwise overwrite it with bump interpretation).
        shader, _, _ = self._make_base_material(
            "matNB", ["model_Normal_OpenGL.png", "model_Height.png"]
        )
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        bump = cmds.listConnections(f"{ai}.normalCamera", type="bump2d")
        self.assertTrue(bump, "bump2d missing")
        self.assertEqual(
            cmds.getAttr(f"{bump[0]}.bumpInterp"),
            1,
            "normal must win the bump slot over height (tangent interpretation)",
        )

    def test_roughness_supersedes_glossiness(self):
        # Roughness (direct) wins over Glossiness (which would invert), so no
        # smoothness-invert reverse node is created.
        shader, _, _ = self._make_base_material(
            "matRG", ["model_Roughness.png", "model_Glossiness.png"]
        )
        self.bridge.add(materials=shader)
        ai = self.bridge.get_bridge(shader)
        self.assertTrue(cmds.listConnections(f"{ai}.specularRoughness"))
        hist = cmds.listHistory(ai) or []
        self.assertFalse(
            cmds.ls(hist, type="reverse"),
            "Roughness must win over Glossiness (no smoothness-invert reverse)",
        )

    def test_packed_mask_supersedes_specular_and_standalone_ao(self):
        # A packed mask drives both metalness and AO. A co-present Specular (a
        # metalness *proxy*) and a standalone Ambient_Occlusion would each wire
        # the same Arnold slot a second time, so the packed mask supersedes both
        # — leaving exactly one driver per property (no force=True last-wins
        # fight). The drop is conditional: Specular alone still drives metalness
        # (test_specular_drives_metalness).
        shader, _, _ = self._make_base_material(
            "matMaskSpecAO",
            ["model_MRAO.png", "model_Specular.png", "model_AO.png"],
        )
        types = {t for _, t in self.bridge._iter_base_textures(shader)}
        self.assertIn("MRAO", types)
        self.assertNotIn(
            "Specular",
            types,
            "packed metalness must supersede the Specular metalness proxy",
        )
        self.assertNotIn(
            "Ambient_Occlusion",
            types,
            "packed AO must supersede a standalone Ambient_Occlusion map",
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


def _stingray_loadable() -> bool:
    """True if the ShaderFX plugin loads (so StingrayPBS exists)."""
    try:
        if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin", quiet=True)
        return bool(cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True))
    except Exception:
        return False


class UnrenderableMaterialsTest(unittest.TestCase):
    """What Arnold would render as error magenta as the scene stands: the game
    shaders on a group with members and an empty bridge slot, each named once."""

    @classmethod
    def setUpClass(cls):
        if not (_mtoa_available() and _stingray_loadable()):
            raise unittest.SkipTest("mtoa + shaderFXPlugin required")

    def setUp(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _group(shader, name, member=True):
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        if member:
            cmds.sets(cmds.polyCube(name=f"{name}_geo")[0], edit=True, forceElement=sg)
        return sg

    @staticmethod
    def _override(sg):
        ai = cmds.shadingNode("aiStandardSurface", asShader=True, name=f"{sg}_ai")
        cmds.connectAttr(f"{ai}.outColor", f"{sg}.aiSurfaceShader", force=True)

    def test_names_each_magenta_game_shader_once_and_nothing_else(self):
        rack = cmds.shadingNode("StingrayPBS", asShader=True, name="rack_srp")
        self._group(rack, "rackTop")
        self._group(rack, "rackLegs")  # a second group: still one name
        half = cmds.shadingNode("StingrayPBS", asShader=True, name="half_srp")
        self._override(self._group(half, "halfTop"))
        self._group(half, "halfLegs")  # the group its bridge misses
        done = cmds.shadingNode("StingrayPBS", asShader=True, name="done_srp")
        self._override(self._group(done, "done"))  # renders its override
        idle = cmds.shadingNode("StingrayPBS", asShader=True, name="idle_srp")
        self._group(idle, "idle", member=False)  # renders nothing
        plain = cmds.shadingNode("lambert", asShader=True, name="plain_lam")
        self._group(plain, "plain")  # Arnold renders a lambert as it is
        self.assertEqual(
            sorted(ArnoldBridge.unrenderable_materials()), sorted([rack, half])
        )

    def test_a_scene_bridged_everywhere_names_none(self):
        """So a render or a bake of it calls no ``add`` at all -- no undo step,
        no "bridge exists" line per material on every click."""
        rack = cmds.shadingNode("StingrayPBS", asShader=True, name="rack_srp")
        self._group(rack, "rackTop")
        self._group(rack, "rackLegs")
        ArnoldBridge().add(materials=rack)
        self.assertEqual(ArnoldBridge.unrenderable_materials(), [])


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
