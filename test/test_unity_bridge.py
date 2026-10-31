# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.unity_bridge.

``UnityBridge`` is the Maya half of the Unity Workflow app (extapps' ``unity_workflow``,
opened by tentacle with this class as its engine): it resolves the app's Scope, exports the
FBX with the scene records (the shared MayaExportMixin) and hands the payload to the
delivery the app attaches (``deliverer=``, unitytk's ``CopyToAssetsDeliverer``). The host
half names no unitytk; the sends below attach the real deliverer, where unitytk is on the
path, to drive the production chain into a temp project (a folder with ``Assets/``, no
Unity), and a hand-back deliverer to pin what the app receives.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py unity_bridge``).
"""

import ast
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import maya.cmds as cmds

import pythontk as ptk
from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge

from base_test import MayaTkTestCase

#: The delivery the app attaches (unitytk), on the runner's pinned path.
UNITYTK = importlib.util.find_spec("unitytk") is not None


def _unity_deliverer():
    """The app's delivery: unitytk's copy into Assets/ (+ optional launch)."""
    from unitytk import CopyToAssetsDeliverer

    return CopyToAssetsDeliverer()


class _HandBack(ptk.Deliverer):
    """What the app receives from the host half: the payload, as produced."""

    def __init__(self, preflight=True):
        self.ok = preflight
        self.payloads = []

    def preflight(self, bridge, request):
        return self.ok

    def deliver(self, bridge, payload, request):
        self.payloads.append(payload)
        return {"fbx": payload.primary, "project": bridge.project_path}


def _imported_roots(path):
    """The top-level package of every import in the module at *path*."""
    roots = []
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots.append(node.module.split(".")[0])
    return roots


class TestUnityBridgeUnit(unittest.TestCase):
    """Pure (no Maya geometry) -- composition, and what is the app's."""

    def test_the_host_half_imports_no_unitytk(self):
        """The delivery is the Unity Workflow app's: no module of the host half
        imports unitytk, and mayatk declares no ``unity`` extra."""
        import mayatk.env_utils.unity_bridge as package

        folder = Path(package.__file__).resolve().parent
        for path in sorted(folder.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertNotIn("unitytk", _imported_roots(path))
        pyproject = folder.parents[2] / "pyproject.toml"
        self.assertNotIn("unitytk", pyproject.read_text(encoding="utf-8"))

    def test_its_delivery_is_the_callers(self):
        self.assertIsNone(UnityBridge().deliverer)
        deliverer = _HandBack()
        bridge = UnityBridge(project_path="C:/proj", deliverer=deliverer)
        self.assertIs(bridge.deliverer, deliverer)
        self.assertEqual(bridge.project_path, "C:/proj")
        for gone in ("list_delivery_modes", "list_template_modes", "_deliverer_cls"):
            self.assertFalse(hasattr(UnityBridge, gone), gone)

    def test_an_optional_package_installs_where_maya_imports(self):
        """The app, opened inside Maya, installs unitytk through its engine."""
        from mayatk.env_utils.handoff_export import BridgeHost

        with mock.patch.object(BridgeHost, "install_package") as install:
            UnityBridge.install_package("unitytk>=0.1.0")
        install.assert_called_once_with("unitytk>=0.1.0")

    def test_ships_the_metadata_carrier(self):
        """This FBX lands in Assets/ as the deliverable, not as an intermediate.

        unitytk's ``LightmapMetadataController`` binds Unity's native lightmap slots
        by reading ``lightmaps`` off ``data_export``. A selection export does
        not include that node, so without the opt-in the import is silently unlit --
        on the platform the bake targets first.
        """
        self.assertIs(UnityBridge.include_data_export, True)

    def test_its_takes_are_exact_slices(self):
        """Unity builds the clips from the takes, so this hand-off resamples
        its split; a DCC hand-off keeps the authored keys (the mixin default)."""
        from mayatk.env_utils.handoff_export import MayaExportMixin

        self.assertIs(UnityBridge.resample_takes, True)
        self.assertIs(MayaExportMixin.resample_takes, False)

    def test_it_animates_unless_a_request_says_otherwise(self):
        """Unity builds the clips from the takes, and this panel offers no
        animation knob; a DCC hand-off keeps its own (the mixin default)."""
        from mayatk.env_utils.handoff_export import MayaExportMixin

        self.assertIs(getattr(UnityBridge, "include_animation", None), True)
        self.assertIs(getattr(MayaExportMixin, "include_animation", None), False)

    def test_params_defaults(self):
        """The export rows only: the delivery's rows are the app's."""
        d = UnityBridge().params_defaults()
        self.assertEqual(
            set(d), {"SCOPE", "INCLUDE_MATERIALS", "EMBED_TEXTURES", "TRIANGULATE"}
        )
        self.assertTrue(d["INCLUDE_MATERIALS"])
        self.assertEqual(d["SCOPE"], "selected")

    def test_the_app_reads_its_rows_off_the_engine(self):
        """The Unity Workflow app builds its Current Scene rows from
        ``UnityBridge.params_registry()``; the engine's defaults read the same."""
        from mayatk.env_utils.unity_bridge import parameters as P

        self.assertIs(UnityBridge.params_registry(), P.Parameters)
        self.assertEqual(UnityBridge().params_defaults(), P.Parameters.defaults())
        self.assertEqual({spec.section for spec in P.PARAMS.values()}, {"Export"})


class TestUnityBridgeSend(MayaTkTestCase):
    """End-to-end: real Maya export -> the app's delivery (unitytk) into a temp
    Unity project's Assets/."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="unity_bridge_test_"))
        self.project = self.tmp / "UnityProj"
        (self.project / "Assets").mkdir(parents=True)
        if not UNITYTK:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.skipTest("unitytk (the app's delivery) is not on the path")
        self.bridge = UnityBridge(
            project_path=str(self.project), deliverer=_unity_deliverer()
        )

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

    def test_an_animated_send_ships_every_channel_in_every_take(self):
        """``late`` is keyed only inside ShotA. Maya's split used to leave
        ShotB with no channel for it, and Unity played it at its rest pose for
        the whole shot.
        Added: 2026-10-04
        """
        import pythontk as ptk
        from mayatk.anim_utils.shots._shots import ShotStore

        cube = cmds.polyCube(name="UnityAnim")[0]
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=40, value=10)
        late = cmds.polyCube(name="UnityEarly")[0]
        cmds.setKeyframe(late, attribute="translateY", time=5, value=0)
        cmds.setKeyframe(late, attribute="translateY", time=15, value=4)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 20, objects=[cube])
        store.define_shot("ShotB", 21, 40, objects=[cube])
        store.publish_export_view()

        result = self.bridge.send(
            [cube, late],
            template="copy_to_assets",
            mode="send_to",
            params={"INCLUDE_ANIMATION": True, "ASSET_NAME": "anim_takes"},
        )

        self.assertIsNotNone(result, "send returned None (delivery failed)")
        curves = ptk.FbxFile.load(
            result["asset"], span_arrays=("KeyTime",), raw_payloads=False
        ).take_curves()
        self.assertIn("ShotB", curves)
        whole = set(curves["Take 001"])
        self.assertTrue(any(key[0] == "UnityEarly" for key in whole))
        tick = ptk.FbxFile.TICKS_PER_SECOND / 24.0
        for take, (start, end) in (("ShotA", (1, 20)), ("ShotB", (21, 40))):
            self.assertEqual(sorted(whole - set(curves[take])), [], take)
            for key, (first, last, _count) in curves[take].items():
                self.assertEqual(
                    (round(first / tick), round(last / tick)), (start, end), key
                )

    def test_a_send_with_the_panels_params_ships_one_take_per_shot(self):
        """The Unity panel offers no animation knob, so INCLUDE_ANIMATION never
        reached the hand-off: no take was split, ``resample_takes`` never ran,
        and Unity built a single ``Take 001`` clip for a scene that declares its
        shots. The bridge animates unless a request says otherwise.
        Added: 2026-10-05
        """
        import pythontk as ptk
        from mayatk.anim_utils.shots._shots import ShotStore

        cube = cmds.polyCube(name="UnityPanelAnim")[0]
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=40, value=10)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 20, objects=[cube])
        store.define_shot("ShotB", 21, 40, objects=[cube])
        store.publish_export_view()

        panel = {"ASSETS_SUBDIR": "Bridge", "ASSET_NAME": "panel_takes"}
        result = self.bridge.send(
            [cube], template="copy_to_assets", mode="send_to", params=panel
        )
        self.assertIsNotNone(result, "send returned None (delivery failed)")
        takes = ptk.FbxFile.load(result["asset"], raw_payloads=False).take_names()
        self.assertIn("ShotA", takes)
        self.assertIn("ShotB", takes)

        still = self.bridge.send(
            [cube],
            template="copy_to_assets",
            mode="send_to",
            params=dict(panel, ASSET_NAME="panel_still", INCLUDE_ANIMATION=False),
        )
        self.assertNotIn(
            "ShotA", ptk.FbxFile.load(still["asset"], raw_payloads=False).take_names()
        )

    def test_a_send_ships_the_audio_its_scene_plays_now(self):
        """The ``audio`` record describes the tracks as they stand at the
        send -- a track added since the last one included -- and each track's
        file rides inside the FBX under its event's name, which Unity extracts
        beside the model: no clip is placed by hand. Measured before: no
        manifest at all, then the previous export's, and never the audio."""
        import wave

        import pythontk as ptk
        from mayatk.audio_utils._audio_utils import AudioUtils

        def wav(name):
            path = (self.tmp / "sound" / name).as_posix()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with wave.open(path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(22050)
                out.writeframes(name.encode() * 400)
            return path

        cube = cmds.polyCube(name="UnityAudio")[0]
        step = wav("footstep_take3.wav")
        AudioUtils.add_clip(step, 1, name="footstep")
        self.bridge.send(
            [cube],
            template="copy_to_assets",
            mode="send_to",
            params={"ASSETS_SUBDIR": "First"},
        )
        punch = wav("punch.wav")
        AudioUtils.add_clip(punch, 10, name="punch")
        result = self.bridge.send(
            [cube],
            template="copy_to_assets",
            mode="send_to",
            params={"ASSETS_SUBDIR": "Second"},
        )
        fbx = result["asset"]
        self.assertEqual(sorted(ptk.FbxMedia.audio_events(fbx)), ["footstep", "punch"])
        embedded = {row["name"]: row["bytes"] for row in ptk.FbxMedia.embedded(fbx)}
        self.assertEqual(embedded.get("footstep.wav"), Path(step).stat().st_size)
        self.assertEqual(embedded.get("punch.wav"), Path(punch).stat().st_size)

    def test_send_aborts_when_no_project(self):
        cube = cmds.polyCube(name="UnityNoProj")[0]
        self.bridge.project_path = str(self.tmp / "not_a_project")
        result = self.bridge.send([cube], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)

    def test_send_aborts_with_empty_selection(self):
        result = self.bridge.send([], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)


class TestUnityBridgeHandsTheAppItsFiles(MayaTkTestCase):
    """The host half's contract with the app, with no unitytk: the scope's FBX
    and its asset name go to the delivery the app attached, checked first."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="unity_handback_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_app_receives_the_fbx_and_the_asset_name(self):
        cube = cmds.polyCube(name="UnityHandBack")[0]
        deliverer = _HandBack()
        bridge = UnityBridge(project_path=str(self.tmp), deliverer=deliverer)
        result = bridge.send([cube], template="copy_to_assets", mode="")
        self.assertIsNotNone(result)
        (payload,) = deliverer.payloads
        self.assertTrue(ptk.FbxFile.is_fbx(payload.primary), payload.primary)
        self.assertEqual(payload.extras["default_asset_name"], "UnityHandBack")
        self.assertEqual(result["project"], str(self.tmp))

    def test_a_refused_delivery_exports_nothing(self):
        cube = cmds.polyCube(name="UnityRefused")[0]
        bridge = UnityBridge(deliverer=_HandBack(preflight=False))
        with mock.patch.object(UnityBridge, "_produce") as produce:
            self.assertIsNone(bridge.send([cube], template="copy_to_assets", mode=""))
        produce.assert_not_called()

    def test_a_send_without_delivery_exports_nothing(self):
        """Library use without the app: refused, saying what is missing,
        before the export is paid for."""
        cube = cmds.polyCube(name="UnityNoDelivery")[0]
        bridge = UnityBridge()
        with mock.patch.object(UnityBridge, "_produce") as produce:
            with mock.patch.object(bridge.logger, "error") as error:
                self.assertIsNone(
                    bridge.send([cube], template="copy_to_assets", mode="")
                )
        produce.assert_not_called()
        self.assertIn("no deliverer", error.call_args[0][0])


_MISSING = object()  # "not passed" -- distinct from an explicit bridge=None


class TestUnityScopeResolution(MayaTkTestCase):
    """Scope resolution for the shared ``SCOPE`` param: :meth:`BridgeHost.resolve_scope`,
    which tentacle's bridge panels call (``mtk.BridgeHost``) and the Maya
    bridge-slots base delegates to, so every Maya bridge resolves scope
    identically. The Unity engine is the whole-scene hook; no Qt panel needed."""

    def _resolve(self, scope, bridge=_MISSING):
        from mayatk.env_utils.handoff_export import BridgeHost

        # Default: the real engine, i.e. the production path. Pass ``bridge=None``
        # for the no-hook fallback.
        return BridgeHost.resolve_scope(
            scope, UnityBridge() if bridge is _MISSING else bridge
        )

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
        """Documented fallback: no engine (or one with no whole-scene hook) still
        resolves "all" to the renderable geometry."""
        cmds.polyCube()
        cmds.select(clear=True)
        resolved = self._resolve("all", bridge=None)
        self.assertTrue(resolved)
        self.assertTrue(all(cmds.objectType(o) == "mesh" for o in resolved), resolved)

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

    def test_the_engine_resolves_the_apps_scope(self):
        """What the app asks its engine (``scope_objects``): the selection,
        the DAG roots for Entire Scene, the visible, and no widening."""
        cube = cmds.polyCube(name="scope_app_cube")[0]
        grp = cmds.group(cube, name="scope_app_group")
        hidden = cmds.polyCube(name="scope_app_hidden")[0]
        cmds.setAttr(f"{hidden}.visibility", 0)
        cmds.select(cube, replace=True)
        bridge = UnityBridge()

        def leaves(scope):
            return [str(o).split("|")[-1] for o in bridge.scope_objects(scope)]

        self.assertEqual(leaves("selected"), [cube])
        self.assertEqual(leaves("bogus"), [cube])
        self.assertIn(grp, leaves("all"))
        self.assertIn(cube, leaves("visible"))
        self.assertNotIn(hidden, leaves("visible"))

    def test_a_maya_scope_ships_as_resolved(self):
        """Maya's export-selection writes a group's subtree: no closure."""
        from mayatk.env_utils.handoff_export import BridgeHost

        grp = cmds.group(cmds.polyCube()[0], name="closure_group")
        resolved = cmds.ls(grp, long=True)
        self.assertEqual(BridgeHost.scope_closure(resolved, {"SCOPE": "all"}), resolved)


if __name__ == "__main__":
    unittest.main()
