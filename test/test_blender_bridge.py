# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.blender_bridge.

Maya-side regression coverage for the template-driven send to Blender. The actual Blender launch
and the FBX export are stubbed (launching Blender would open a GUI; export is covered by the
``FbxUtils`` suite), so these tests pin the bridge's own logic:

- executable discovery never raises and honors ``$BLENDER_EXE``,
- template discovery + mode parsing,
- the rendered Blender script substitutes the FBX path + parameter values,
- ``send`` derives FBX options from params, writes the script, and launches ``--python``,
- the strip-materials path exports shader-less duplicates and leaves the originals untouched.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py blender_bridge``).
"""

import json
import math
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import maya.cmds as cmds

from mayatk.env_utils.blender_bridge._blender_bridge import BlenderBridge, _TEMPLATE_DIR
from mayatk.env_utils.blender_bridge import parameters as params

# Shared engine internals moved upstream: the Maya FBX export lives on
# MayaExportMixin (handoff_export); the fresh-app launch lives on the pythontk
# ScriptLaunchDeliverer (app_handoff). Patch them where they're actually looked up.
from mayatk.env_utils import handoff_export

from base_test import MayaTkTestCase
import pythontk as ptk


class TestBlenderBridgeDiscovery(unittest.TestCase):
    """Executable discovery -- pure."""

    def test_blender_path_no_raise(self):
        self.assertTrue(
            BlenderBridge().blender_path is None
            or isinstance(BlenderBridge().blender_path, str)
        )

    def test_env_override(self):
        fd, path = tempfile.mkstemp(suffix=".exe", prefix="fake_blender_")
        os.close(fd)
        try:
            with mock.patch.dict(os.environ, {"BLENDER_EXE": path}):
                self.assertEqual(BlenderBridge().blender_path, path)
        finally:
            Path(path).unlink(missing_ok=True)


class TestBlenderBridgeTemplates(unittest.TestCase):
    """Template discovery + rendering -- pure (no Maya geometry)."""

    def test_list_template_modes(self):
        pairs = BlenderBridge.list_template_modes()
        modes = dict(pairs)
        # `import` is the interactive send recipe (the three near-identical ones collapsed
        # into it); `bake_lightmaps` writes an artifact instead of launching Blender.
        self.assertEqual(set(modes), {"import", "bake_lightmaps"})
        self.assertEqual(modes["import"], "send_to")
        # Pinned deliberately: the discovery helpers filter declarations against an
        # `allowed` tuple and silently fall back to its first entry, so an allowed-list
        # that forgets round_trip relabels this as send_to -- the panel then routes it
        # through send(), which never populates __OUT_FILE__, and it launches and fails
        # minutes in. It is also the artist-facing label, so the word has to be the one
        # that describes the operation (the scene comes back lit) rather than the
        # mechanics it shares with save_as.
        self.assertEqual(modes["bake_lightmaps"], "round_trip")

    def test_template_modes_parsed(self):
        self.assertEqual(
            BlenderBridge.template_modes(_TEMPLATE_DIR / "import.py"), ("send_to",)
        )
        self.assertEqual(
            BlenderBridge.template_modes(_TEMPLATE_DIR / "bake_lightmaps.py"),
            ("round_trip",),
        )

    def test_mode_vocabulary_comes_from_pythontk(self):
        """The bridge names no mode string of its own.

        The strings are an on-disk contract (``BRIDGE_MODES`` in every template), so a
        locally spelled copy is a second dialect of a file format -- which is how the
        Marmoset/Substance bridges' ``roundtrip`` drifted from the canon's ``round_trip``
        and made a mode look shared when it was not.
        """
        self.assertEqual(
            set(BlenderBridge.template_modes_allowed),
            {ptk.SEND_TO, ptk.SAVE_AS, ptk.ROUND_TRIP},
        )

    def test_render_substitutes_path_and_params(self):
        merged = params.Parameters.defaults()
        rendered = BlenderBridge().render_template("import", r"C:\t\x.fbx", merged)
        self.assertIn("bpy.ops.import_scene.fbx", rendered)
        self.assertIn(
            'FBX_PATH = r"C:/t/x.fbx"', rendered
        )  # forward-slashed, no __KEY__ left
        self.assertNotIn("__", rendered)  # every placeholder substituted
        self.assertIn("APPLY_UNIT_SCALE = True", rendered)
        self.assertIn("INCLUDE_ANIMATION = False", rendered)
        # The export-options comment was substituted too (panel-visibility echo).
        self.assertIn("materials=True", rendered)

    def test_defaults_registry_covers_the_widget_specs(self):
        """``DEFAULTS`` is the render-context SSoT; the widget specs are its panel subset.

        ``save_as`` can run where Qt cannot be imported (a headless DCC), so the engine
        answers ``params_defaults()`` from its own dict. Every widget must have a Qt-free
        default (the specs read the dict, so a missing key is a loud import error), and
        the values must agree, or the panel would show one default and a headless run
        use another.

        The two sets are now EQUAL. They were not: the bake's resolution / samples /
        denoise / device were API-only, substituted into the template with no row to
        set them from, on the argument that the Quality tier already named good values.
        That also hid Packing and the map affix, so every bridge bake was force-atlased
        and force-named ``_Lightmap`` -- and it left the recipe's panel disagreeing with
        the Lightmap Baker panel it drives. Equality is the invariant that keeps a knob
        from going quiet again: if the template substitutes it, the artist can see it.
        """
        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        spec_defaults = params.Parameters.defaults()
        self.assertEqual(set(spec_defaults), set(DEFAULTS))
        for key, value in spec_defaults.items():
            self.assertEqual(DEFAULTS[key], value, key)

    def test_the_bake_recipe_exposes_the_lightmap_bakers_own_dials(self):
        """The bridge drives blendertk's ``LightmapBaker``; it must show its settings.

        One row per dial and switch of that baker's panel -- Quality, Packing,
        Processor, Resolution + Denoise, Samples + Adaptive, Bounces, output folder
        + Beside Textures, name affix -- so an artist who has used the panel
        recognises the recipe. Pinned by KEY rather than by widget count so adding an
        unrelated row cannot quietly satisfy it (``TestBridgeMirrorsTheBlenderBaker``
        derives the same set from the baker itself).

        The lighting rows beside them (HDRI / world / scene-light / emission strength)
        are deliberately NOT in this list: Maya's baker renders the scene's own Arnold
        lights in place, while this one has to transport them into another renderer's
        units, so those belong to the crossing rather than to the baker.
        """
        template = os.path.join(_TEMPLATE_DIR, "bake_lightmaps.py")
        referenced = params.Parameters.referenced_keys(
            open(template, encoding="utf-8").read()
        )
        for key in (
            "LIGHTMAP_QUALITY",
            "LIGHTMAP_PACKING",
            "LIGHTMAP_DEVICE",
            "LIGHTMAP_RESOLUTION",
            "LIGHTMAP_DENOISE",
            "LIGHTMAP_SAMPLES",
            "LIGHTMAP_ADAPTIVE",
            "LIGHTMAP_BOUNCES",
            "LIGHTMAP_DIR",
            "LIGHTMAP_BESIDE_TEXTURES",
            "LIGHTMAP_AFFIX",
        ):
            self.assertIn(key, params.PARAMS, f"{key} has no panel row")
            self.assertIn(key, referenced, f"{key} is not used by the bake template")

    def test_each_switch_sits_beside_the_dial_it_qualifies(self):
        """As it rides that field's option box on the Lightmap Baker panel."""
        keys = list(params.PARAMS)
        for switch, dial in (
            ("LIGHTMAP_DENOISE", "LIGHTMAP_RESOLUTION"),
            ("LIGHTMAP_ADAPTIVE", "LIGHTMAP_SAMPLES"),
            ("LIGHTMAP_BESIDE_TEXTURES", "LIGHTMAP_DIR"),
        ):
            self.assertTrue(params.PARAMS[switch].inline, switch)
            self.assertEqual(keys.index(switch), keys.index(dial) + 1, switch)

    def test_a_dial_left_to_the_tier_says_so(self):
        """Samples and Bounces keep the Quality tier's value at their lowest
        setting (Bounces -1: 0 is a real depth), shown as words, not a number."""
        for key, unset in (("LIGHTMAP_SAMPLES", 0), ("LIGHTMAP_BOUNCES", -1)):
            spec = params.PARAMS[key]
            self.assertEqual((spec.minimum, spec.default), (unset, unset), key)
            self.assertTrue(spec.placeholder, key)

    def test_the_affix_mode_survives_into_the_template(self):
        """The ``affix`` kind substitutes the SPELLING alone -- the mode must be resolved.

        Deliberate in uitk (most templates only want the string), and silent here: a
        spelling like ``_Lightmap`` reads as a SUFFIX under Auto, so an artist who
        pinned Prefix would get a suffix and no error. The bridge therefore derives
        ``LIGHTMAP_PREFIX`` / ``LIGHTMAP_SUFFIX`` in ``render_context`` -- which the
        template consumes instead of the composite -- through the same
        ``split_affix`` the Lightmap Baker panel's own field resolves with.
        """
        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        cases = {
            ("_Lightmap", "suffix"): ("''", "'_Lightmap'"),
            ("LM_", "prefix"): ("'LM_'", "''"),
            # The case that has no other way to be expressed: the same spelling,
            # pinned to the other side. Text-only substitution cannot carry this.
            ("_Lightmap", "prefix"): ("'_Lightmap'", "''"),
        }
        for (text, mode), expected in cases.items():
            values = {**DEFAULTS, "LIGHTMAP_AFFIX": {"text": text, "mode": mode}}
            context = params.Parameters.render_context(values)
            self.assertEqual(
                (context["LIGHTMAP_PREFIX"], context["LIGHTMAP_SUFFIX"]),
                expected,
                f"{text!r} pinned {mode}",
            )

    def test_the_bake_template_renders_to_valid_python(self):
        """Every token substituted, and the result still parses.

        The template only becomes Python once rendered, so a stray token or a value
        that renders as a bare name (``GPU`` rather than ``'GPU'``) is a NameError
        minutes into a headless run, with no artifact and no useful log.
        """
        import ast

        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        source = open(
            os.path.join(_TEMPLATE_DIR, "bake_lightmaps.py"), encoding="utf-8"
        ).read()
        context = params.Parameters.render_context(DEFAULTS)
        context.update(
            {
                "FBX_PATH": "C:/t/x.fbx",
                "OUT_FILE": "C:/t/o.json",
                "EXTRA_SYS_PATH": "['a']",
            }
        )
        rendered = ptk.StrUtils.replace_delimited(source, context)
        self.assertEqual(re.findall(r"__[A-Z0-9_]+__", rendered), [])
        ast.parse(rendered)
        # The new dials substitute as the values the baker takes.
        self.assertIn("LIGHTMAP_BOUNCES = -1", rendered)
        self.assertIn("LIGHTMAP_ADAPTIVE = True", rendered)
        self.assertIn("LIGHTMAP_BESIDE_TEXTURES = False", rendered)

    def test_import_exposes_scene_and_frame_options(self):
        # The unified template exposes both scene-behavior knobs so the panel shows them.
        used = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "import.py").read_text()
        )
        self.assertIn("FRAME_VIEW", used)
        self.assertIn("CLEAR_SCENE", used)

    def test_import_hides_maya_groups_in_step_with_the_engine(self):
        """A Maya group draws nothing; its Empty must not land as a full-size axes
        cross. ``import.py`` hands the payload to blendertk's ONE consumer
        (``MayaSceneImport.import_payload``), whose node-type tagger shrinks a group
        to Blender's hard minimum -- so the template keeps no hand copy of the
        tagger or its constant to drift from the engine."""
        text = (_TEMPLATE_DIR / "import.py").read_text(encoding="utf-8")
        self.assertIn(".import_payload(", text)
        self.assertNotIn("GROUP_EMPTY_DISPLAY_SIZE", text)
        self.assertNotIn("def tag_node_types", text)
        try:
            import inspect

            from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport
        except Exception:  # blendertk is not on mayatk's test path by default
            return
        self.assertIn(
            "_tag_maya_node_types", inspect.getsource(MayaSceneImport.import_payload)
        )


class TestBlenderBridgeSend(MayaTkTestCase):
    """Send flow -- Blender launch + FBX export stubbed; strip path runs for real."""

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    def _patches(self, export_side_effect=None):
        return (
            mock.patch.object(
                handoff_export.FbxUtils,
                "export",
                side_effect=export_side_effect,
                return_value="x.fbx",
            ),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
            mock.patch.object(ptk.AppLauncher, "launch", return_value=object()),
        )

    def test_send_export_and_launch_args(self):
        cube = cmds.polyCube(name="bb_send")[0]
        export, load, launch = self._patches()
        with export as m_export, load, launch as m_launch:
            result = self.bridge.send(
                [cube],
                template="import",
                params={
                    "EMBED_TEXTURES": True,
                    "TRIANGULATE": True,
                    "INCLUDE_ANIMATION": False,
                },
            )
        opts = m_export.call_args.kwargs["options"]
        self.assertTrue(opts["FBXExportEmbeddedTextures"])
        self.assertTrue(opts["FBXExportTriangulate"])
        # The bake belongs to the write plan, applied flag-then-range -- never
        # to the option pins, whose flag write reset the range a split set.
        self.assertNotIn("FBXExportBakeComplexAnimation", opts)
        self.assertFalse(m_export.call_args.kwargs["plan"].bakes)
        args = m_launch.call_args.kwargs.get("args") or m_launch.call_args.args[1]
        self.assertEqual(args[0], "--python")
        script = result["script"]
        self.assertEqual(args[1], script)
        self.assertTrue(m_launch.call_args.kwargs.get("detached"))
        self.assertIn("import_scene.fbx", Path(script).read_text(encoding="utf-8"))
        Path(script).unlink(missing_ok=True)

    def test_export_restores_the_users_own_selection(self):
        """The export selects what it exports -- it must put the ARTIST's selection back.

        Restoring the exported set instead silently changed the live selection whenever
        it wasn't the selection: an explicit ``send(objects=...)``, and every
        ``save_as``, which defaults to the whole scene.
        """
        kept = cmds.polyCube(name="bb_sel_kept")[0]
        other = cmds.polyCube(name="bb_sel_other")[0]
        cmds.select(kept, replace=True)

        export, load, launch = self._patches()
        with export, load, launch:
            self.bridge.send([other], template="import")

        self.assertEqual(
            [n.split("|")[-1] for n in cmds.ls(selection=True, long=True) or []],
            [kept],
        )

    def test_export_with_nothing_selected_leaves_nothing_selected(self):
        cube = cmds.polyCube(name="bb_sel_none")[0]
        cmds.select(clear=True)
        export, load, launch = self._patches()
        with export, load, launch:
            self.bridge.send([cube], template="import")
        self.assertEqual(cmds.ls(selection=True) or [], [])

    def test_send_bad_template_returns_none(self):
        cube = cmds.polyCube(name="bb_badtpl")[0]
        export, load, launch = self._patches()
        with export, load, launch:
            self.assertIsNone(self.bridge.send([cube], template="does_not_exist"))

    def test_strip_materials_exports_shaderless_copies(self):
        cube = cmds.polyCube(name="bb_strip")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="bb_lam")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="bb_lamSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        captured = {}

        def capture(**kwargs):
            objs = kwargs["objects"]
            captured["objects"] = list(objs)
            sgs = set()
            for o in objs:
                shapes = cmds.listRelatives(o, shapes=True, fullPath=True) or []
                sgs.update(cmds.listConnections(shapes, type="shadingEngine") or [])
            captured["shading_engines"] = sgs
            return "x.fbx"

        export, load, launch = self._patches(export_side_effect=capture)
        with export, load, launch:
            self.bridge.send(
                [cube], template="import", params={"INCLUDE_MATERIALS": False}
            )

        self.assertTrue(captured["objects"])
        self.assertNotIn(cube, captured["objects"])
        self.assertIn("initialShadingGroup", captured["shading_engines"])
        self.assertNotIn(sg, captured["shading_engines"])
        for dup in captured["objects"]:
            self.assertFalse(cmds.objExists(dup), f"temp duplicate {dup} not deleted")
        orig_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        self.assertIn(sg, cmds.listConnections(orig_shape, type="shadingEngine") or [])

    def test_strip_materials_keeps_a_group_subtree_and_strips_every_mesh_in_it(self):
        """A GROUP in the export set ships its children — shaderless.

        The set is any transform (``save_as`` hands over DAG roots), so the
        temp copy must keep the subtree; and the strip must reach the child
        meshes, which previously kept their original materials because only
        the copied ROOTS were forced to ``initialShadingGroup``.
        """
        grp = cmds.group(empty=True, name="bb_grp")
        kids = []
        for i in range(2):
            cube = cmds.polyCube(name=f"bb_kid{i}")[0]
            kids.append(cmds.parent(cube, grp)[0])
        shader = cmds.shadingNode("lambert", asShader=True, name="bb_kidLam")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="bb_kidLamSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets([f"{grp}|{k}" for k in kids], edit=True, forceElement=sg)

        captured = {}

        def capture(**kwargs):
            objs = list(kwargs["objects"])
            meshes = (
                cmds.listRelatives(
                    objs, allDescendents=True, type="mesh", fullPath=True, ni=True
                )
                or []
            )
            captured["mesh_count"] = len(meshes)
            captured["shading_engines"] = set(
                cmds.listConnections(meshes, type="shadingEngine") or []
            )
            return "x.fbx"

        export, load, launch = self._patches(export_side_effect=capture)
        with export, load, launch:
            self.bridge.send(
                [grp], template="import", params={"INCLUDE_MATERIALS": False}
            )

        self.assertEqual(captured["mesh_count"], 2, "the group's children were dropped")
        self.assertEqual(captured["shading_engines"], {"initialShadingGroup"})
        # The originals keep their material.
        for k in kids:
            shape = cmds.listRelatives(f"{grp}|{k}", shapes=True, fullPath=True)[0]
            self.assertIn(sg, cmds.listConnections(shape, type="shadingEngine") or [])


class TestBlenderBridgeUsdCarrier(MayaTkTestCase):
    """The USD carrier: the same send, produced as a USD layer instead of an FBX.

    Opt-in beside FBX (``CARRIER`` param), produced through ``UsdUtils.export`` on
    the shared mixin, and LOUD where USD cannot carry what FBX carries: an instanced
    selection is refused before anything is exported, because a flat USD would
    arrive as N independent meshes and only betray itself hours later -- the
    silent structural loss that reverted a USD default on 2026-08-02.
    """

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    def _patches(self):
        return (
            mock.patch.object(handoff_export.UsdUtils, "export", return_value="x.usd"),
            mock.patch.object(handoff_export.FbxUtils, "export", return_value="x.fbx"),
            mock.patch.object(ptk.AppLauncher, "launch", return_value=object()),
        )

    def test_the_bridge_offers_both_carriers_and_defaults_to_fbx(self):
        self.assertEqual(self.bridge.carriers, ("fbx", "usd"))
        self.assertEqual(self.bridge.params_defaults()["CARRIER"], "fbx")
        # The panel renders the same choice the engine enforces.
        self.assertEqual(params.PARAMS["CARRIER"].default, "fbx")
        self.assertEqual(
            [v for _l, v in params.PARAMS["CARRIER"].choices], ["fbx", "usd"]
        )

    def test_usd_carrier_exports_a_usd_layer_through_usd_utils(self):
        cube = cmds.polyCube(name="bb_usd_send")[0]
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx as m_fbx, launch:
            result = self.bridge.send(
                [cube], template="import", params={"CARRIER": "usd"}
            )
        self.assertIsNotNone(result)
        self.assertTrue(result["payload"].lower().endswith(".usd"))
        m_fbx.assert_not_called()
        m_usd.assert_called_once()
        kwargs = m_usd.call_args.kwargs
        self.assertEqual(kwargs["file_path"], result["payload"])
        self.assertTrue(kwargs["selection_only"])
        self.assertIn(cube, [n.split("|")[-1] for n in kwargs["objects"]])
        opts = kwargs["options"]
        # The live-verified interchange set of the pull route, not a new guess.
        self.assertEqual(opts["shadingMode"], "useRegistry")
        self.assertEqual(opts["convertMaterialsTo"], ["UsdPreviewSurface"])
        self.assertEqual(opts["defaultMeshScheme"], "none")
        self.assertEqual(opts["exportRelativeTextures"], "absolute")
        self.assertIs(opts["exportInstances"], False)
        self.assertNotIn("frameRange", opts)  # static send samples no frames
        # The receiving template sees the payload under both token names and
        # routes a USD payload to the USD importer.
        body = Path(result["script"]).read_text(encoding="utf-8")
        self.assertIn(result["payload"].replace("\\", "/"), body)
        self.assertIn("wm.usd_import", body)
        Path(result["script"]).unlink(missing_ok=True)

    def test_fbx_stays_the_default_route(self):
        cube = cmds.polyCube(name="bb_usd_default")[0]
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx as m_fbx, launch:
            result = self.bridge.send([cube], template="import")
        self.assertTrue(result["payload"].lower().endswith(".fbx"))
        m_usd.assert_not_called()
        m_fbx.assert_called_once()

    def test_materials_off_strips_natively_on_usd(self):
        """USD has a no-shading export mode, so the strip needs no duplicates."""
        cube = cmds.polyCube(name="bb_usd_strip")[0]
        before = set(cmds.ls(long=True))
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx, launch:
            self.bridge.send(
                [cube],
                template="import",
                params={"CARRIER": "usd", "INCLUDE_MATERIALS": False},
            )
        self.assertEqual(m_usd.call_args.kwargs["options"]["shadingMode"], "none")
        self.assertIn(
            cube, [n.split("|")[-1] for n in m_usd.call_args.kwargs["objects"]]
        )
        self.assertEqual(set(cmds.ls(long=True)) - before, set())  # no residue

    def test_animation_send_samples_the_scene_range(self):
        cube = cmds.polyCube(name="bb_usd_anim")[0]
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=24, value=5)
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx, launch:
            self.bridge.send(
                [cube],
                template="import",
                params={"CARRIER": "usd", "INCLUDE_ANIMATION": True},
            )
        frame_range = m_usd.call_args.kwargs["options"]["frameRange"]
        self.assertEqual(tuple(frame_range), (1.0, 24.0))

    def test_an_instanced_selection_is_refused_on_usd_and_nothing_leaves(self):
        cube = cmds.polyCube(name="bb_usd_inst")[0]
        twin = cmds.instance(cube)[0]
        usd, fbx, launch = self._patches()
        with (
            usd as m_usd,
            fbx as m_fbx,
            launch as m_launch,
            self.assertLogs(self.bridge.logger, level="ERROR") as logs,
        ):
            result = self.bridge.send(
                [cube, twin], template="import", params={"CARRIER": "usd"}
            )
        self.assertIsNone(result)
        m_usd.assert_not_called()
        m_fbx.assert_not_called()
        m_launch.assert_not_called()
        self.assertTrue(
            any(
                "instanc" in line.lower() and "fbx" in line.lower()
                for line in logs.output
            ),
            logs.output,
        )

    def test_an_instance_whose_sibling_stays_behind_is_no_loss(self):
        """One copy leaves as one mesh -- nothing in the payload is duplicated."""
        cube = cmds.polyCube(name="bb_usd_half")[0]
        cmds.instance(cube)  # the twin is NOT sent
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx, launch:
            result = self.bridge.send(
                [cube], template="import", params={"CARRIER": "usd"}
            )
        self.assertIsNotNone(result)
        m_usd.assert_called_once()

    def test_the_same_instanced_selection_still_sends_via_fbx(self):
        cube = cmds.polyCube(name="bb_fbx_inst")[0]
        twin = cmds.instance(cube)[0]
        usd, fbx, launch = self._patches()
        with usd, fbx as m_fbx, launch:
            result = self.bridge.send([cube, twin], template="import")
        self.assertIsNotNone(result)
        m_fbx.assert_called_once()

    def test_a_carrier_the_bridge_does_not_offer_is_refused(self):
        cube = cmds.polyCube(name="bb_usd_obj")[0]
        usd, fbx, launch = self._patches()
        with usd as m_usd, fbx as m_fbx, launch:
            self.assertIsNone(
                self.bridge.send([cube], template="import", params={"CARRIER": "obj"})
            )
        m_usd.assert_not_called()
        m_fbx.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestBlenderBridgeTextureManifest(MayaTkTestCase):
    """The send writes a texture sidecar so Maya materials survive the FBX hop.

    The FBX carries the StingrayPBS bindings (``Maya|TEX_*`` via
    ``FbxImplementation``), but Blender's importer discards them by design
    (``material link ... ignored``) and wires only the native Lambert/Phong
    slots -- nothing reaches Python, so a real production selection landed in
    Blender unshaded. ``BlenderBridge`` sidecars each
    textured material's ORIGINAL image files and the Blender-side ``import``
    template replays them through blendertk's existing applier, mirroring what
    ``btk.MayaBridge`` already does in the other direction.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bb_manifest_")
        # A real file on disk: the Blender-side applier drops entries whose
        # paths don't resolve, so the collector must record resolvable ones.
        self.tex = os.path.join(self.tmp, "cube_BaseColor.png")
        with open(self.tex, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")

    def _textured_cube(self):
        cube = cmds.polyCube(name="mfx_cube")[0]
        shader = cmds.shadingNode("standardSurface", asShader=True, name="mfx_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="mfx_matSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        file_node = cmds.shadingNode("file", asTexture=True, name="mfx_file")
        cmds.setAttr(f"{file_node}.fileTextureName", self.tex, type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.baseColor", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube

    def test_manifest_records_the_material_texture(self):
        import json

        cube = self._textured_cube()
        fbx = os.path.join(self.tmp, "payload.fbx")
        BlenderBridge()._write_manifest([cube], fbx)

        manifest = fbx + ".manifest.json"
        self.assertTrue(os.path.isfile(manifest), "no sidecar written")
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)

        names = [e["name"] for e in data["materials"]]
        self.assertIn("mfx_mat", names)
        entry = next(e for e in data["materials"] if e["name"] == "mfx_mat")
        # Schema the Blender-side applier consumes.
        self.assertEqual(entry["fbx_material"], "mfx_mat")
        self.assertIn("mfx_cube", entry["objects"])
        self.assertTrue(
            any(
                os.path.normcase(self.tex) == os.path.normcase(f)
                for f in entry["files"]
            ),
            entry["files"],
        )
        self.assertIn("mfx_mat", data["scene_materials"])

    def test_manifest_keeps_the_authoritative_shader_slots(self):
        """``slots`` rides ALONGSIDE ``files`` so unclassifiable maps stay rebuildable.

        A texture named after a product (``Agilent_PNA.png``) carries no map-type
        token, so the Blender side cannot classify it by filename -- but Maya knew
        which shader input it came from. Dropping that on the floor is what left 6
        of 9 production materials unshaded.
        """
        import json

        cube = self._textured_cube()
        fbx = os.path.join(self.tmp, "slots.fbx")
        BlenderBridge()._write_manifest([cube], fbx)

        with open(fbx + ".manifest.json", encoding="utf-8") as fh:
            entry = next(
                e for e in json.load(fh)["materials"] if e["name"] == "mfx_mat"
            )

        slots = entry.get("slots")
        self.assertIsInstance(slots, dict, "manifest dropped the shader slots")
        self.assertTrue(slots, "slots present but empty")
        # Every slot path must also appear in files -- the two views agree.
        for channel, path in slots.items():
            self.assertIn(path, entry["files"], f"{channel} path missing from files")
        # And each channel must be one the shared registry can resolve.

        self.assertTrue(
            any(
                ptk.MapRegistry.resolve_type_from_channel(c) is not None for c in slots
            ),
            f"no slot channel resolves to a map type: {sorted(slots)}",
        )

    def test_untextured_material_writes_no_sidecar(self):
        """A flat color rides the FBX fine -- no sidecar, no spurious warning."""
        cube = cmds.polyCube(name="mfx_plain")[0]
        fbx = os.path.join(self.tmp, "plain.fbx")
        BlenderBridge()._write_manifest([cube], fbx)
        self.assertFalse(os.path.isfile(fbx + ".manifest.json"))

    def test_manifest_spells_namespaced_names_as_blender_will_see_them(self):
        """A referenced asset's nodes are namespaced; FBX keeps the colon, USD
        sanitizes it -- a manifest that strips the namespace matches nothing
        (live: the namespaced mesh arrived untextured)."""
        cmds.namespace(add="bbns")
        cube = cmds.polyCube(name="bbns:wall")[0]
        shader = cmds.shadingNode(
            "standardSurface", asShader=True, name="bbns:wall_mat"
        )
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="bbns:wall_matSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        tex = os.path.join(self.tmp, "wall_BaseColor.png")
        Path(tex).write_bytes(b"x")
        node = cmds.shadingNode("file", asTexture=True, name="bbns:wall_file")
        cmds.setAttr(f"{node}.fileTextureName", tex, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{shader}.baseColor", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        marker = cmds.spaceLocator(name="bbns:snap")[0]

        for carrier, want_obj, want_mat in (
            ("fbx", "bbns:wall", "bbns:wall_mat"),
            ("usd", "bbns_wall", "bbns_wall_mat"),
        ):
            with self.subTest(carrier=carrier):
                path = os.path.join(self.tmp, f"m.{carrier}")
                BlenderBridge()._write_manifest(
                    [cube, marker],
                    path,
                    spell=BlenderBridge._manifest_spelling(carrier),
                )
                import json

                with open(path + ".manifest.json", encoding="utf-8") as fh:
                    data = json.load(fh)
                entry = data["materials"][0]
                self.assertEqual(entry["name"], want_mat)
                self.assertEqual(entry["fbx_material"], want_mat)
                self.assertEqual(entry["objects"], [want_obj])
                self.assertIn(
                    BlenderBridge._manifest_spelling(carrier)(marker),
                    data["transforms"],
                )

    def test_produce_skips_the_sidecar_when_materials_are_off(self):
        """INCLUDE_MATERIALS=False is a geometry-only hand-off by contract."""
        cube = self._textured_cube()
        fbx = os.path.join(self.tmp, "nomat.fbx")
        bridge = BlenderBridge()
        with (
            mock.patch.object(handoff_export.FbxUtils, "export"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
            mock.patch.object(bridge, "_make_payload_path", return_value=fbx),
        ):
            request = mock.Mock()
            request.params = {"INCLUDE_MATERIALS": False}
            bridge._produce([cube], request)
        self.assertFalse(os.path.isfile(fbx + ".manifest.json"))

    def test_template_replays_the_sidecar_through_the_shared_applier(self):
        """One consumer, not a second sequence of the appliers: both receivers hand the
        payload to ``MayaSceneImport.import_payload``, the call blendertk's own pull
        makes too."""
        for name in ("import.py", "_save_scene.py"):
            text = (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("MayaSceneImport", text, name)
            self.assertIn(".import_payload(", text, name)
            for applier in (
                "_apply_texture_manifest(",
                "_tag_maya_node_types(",
                "_rebuild_lights(",
            ):
                self.assertNotIn(applier, text, f"{name} re-sequences {applier}")
        text = (_TEMPLATE_DIR / "import.py").read_text(encoding="utf-8")
        # Replayed regardless of the viewport-framing option.
        self.assertLess(
            text.index("new = import_payload()"), text.index("if not FRAME_VIEW")
        )


class TestBlenderBridgeShotManifest(MayaTkTestCase):
    """The send sidecars the scene's portable records so Blender shows the same
    shots and emissive groups.

    Neither carrier holds a shot or a group's membership; the ``shots`` section
    (``ShotStore.export_transfer`` over pythontk's ``ShotTransfer``) and the generic
    ``records`` one (``DataNodes.transfer_sections``) ride the same manifest the
    materials and lights do, scoped to what is sent and spelled the way the carrier
    spells names.
    """

    def setUp(self):
        super().setUp()
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        ShotStore._auto_export_disabled = False
        self.tmp = tempfile.mkdtemp(prefix="bb_shots_")

    def tearDown(self):
        import shutil
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.disable_auto_export()
        ShotStore._auto_export_disabled = False
        ShotStore.clear_active()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _scene_with_a_shot(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        cmds.namespace(add="ref")
        cube = cmds.polyCube(name="ref:sent_cube")[0]
        for f in (1, 24):
            cmds.setKeyframe(cube, attribute="translateX", t=f, v=f)
        stayed = cmds.polyCube(name="stayed")[0]
        store = ShotStore.active()
        shot = store.define_shot("Intro", 1, 24, objects=[cube, stayed])
        curve = cmds.listConnections(f"{cube}.translateX", type="animCurve")[0]
        store.edit_ledger.record_key(curve, 24.0, shot.shot_id, "end")
        return cube, shot

    def _manifest(self, **kwargs):
        fbx = os.path.join(self.tmp, "send.fbx")
        path = fbx + ".manifest.json"
        if os.path.exists(path):
            os.remove(path)
        BlenderBridge()._write_manifest(kwargs.pop("objects"), fbx, **kwargs)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_manifest_carries_the_shots_scoped_to_the_send(self):
        cube, shot = self._scene_with_a_shot()
        manifest = self._manifest(objects=[cube])
        section = manifest["shots"]
        self.assertEqual(section["store"]["shots"][0]["objects"], ["ref:sent_cube"])
        self.assertEqual(
            section["ledger"]["keys"],
            {"ref:sent_cube": {"translateX": [[24.0, shot.shot_id, "end"]]}},
        )
        # USD spells the prim; the section follows the carrier like every section.
        usd = self._manifest(
            objects=[cube], spell=BlenderBridge._manifest_spelling("usd")
        )
        self.assertEqual(
            usd["shots"]["store"]["shots"][0]["objects"], ["ref_sent_cube"]
        )

    def test_the_artist_can_opt_out(self):
        cube, _shot = self._scene_with_a_shot()
        self.assertIsNone(self._manifest(objects=[cube], include_scene_data=False))

    def test_the_retired_parameter_name_is_gone(self):
        """``INCLUDE_SHOTS`` became ``INCLUDE_SCENE_DATA`` and warned until 0.19.0,
        where it goes: the old spelling no longer resolves (drift guard)."""
        self.assertIsNone(BlenderBridge.param_aliases)
        merged = BlenderBridge().merge_params({"INCLUDE_SHOTS": False})
        self.assertIsNot(merged.get("INCLUDE_SCENE_DATA"), False)

    def test_every_portable_record_rides_the_records_section(self):
        """An emissive group crosses with its registry, its membership (scoped to
        the send, spelled by the carrier) and each member's face count."""
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        cube, _shot = self._scene_with_a_shot()
        EmissiveGroups.add_group("glow", [f"{cube}.f[1]", f"{cube}.f[3]"])
        EmissiveGroups.add_group("rim", ["stayed.f[0]"])  # not sent
        records = self._manifest(objects=[cube])["records"]
        payload = records[ptk.SceneRecords.EMISSIVE_REGISTRY.key]
        self.assertEqual(sorted(payload["registry"]["groups"]), ["glow", "rim"])
        self.assertEqual(payload["members"], {"glow": {"ref:sent_cube": [1, 3]}})
        self.assertEqual(payload["faces"], {"ref:sent_cube": 6})
        # A record bound to Maya's constructs never rides a hand-off.
        self.assertEqual(list(records), [ptk.SceneRecords.EMISSIVE_REGISTRY.key])

    def test_the_sidecar_is_written_atomically(self):
        """Through ``HandoffManifest.write``: a send that died mid-write must not
        leave a truncated sidecar the consumer would read as 'nothing to say'."""
        cube, _shot = self._scene_with_a_shot()
        with mock.patch.object(
            ptk.FileUtils, "write_json", wraps=ptk.FileUtils.write_json
        ) as write:
            self.assertIsNotNone(self._manifest(objects=[cube]))
        self.assertEqual(write.call_count, 1)


class TestSteppedKeysRideTheManifest(MayaTkTestCase):
    """A Maya stepped key holds in Blender (``HandoffManifest.STEPPED_KEYS``).

    FBX carries the key, but Blender's importer makes every key LINEAR, so a
    hold arrived as a ramp to the next key (SOL_REPLACE_ASSEMBLY's SCREWDRIVER
    steps at 12 and holds to 731; pulled, it moved the whole way).
    ``MayaExportMixin.stepped_keys`` lists the steps off the authored curves --
    for the pull's conversion template and for the send -- and blendertk's
    ``MayaSceneImport`` makes those keys CONSTANT again.
    """

    def setUp(self):
        super().setUp()
        tmp = ptk.TempArtifacts("mtk_bridge_stepped_keys", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.dir_path()

    @staticmethod
    def _held(name, attrs=("translateY",), step_at=12, also=()):
        cube = cmds.polyCube(name=name)[0]
        for attr in attrs:
            for f, v in ((1, 0.0), (step_at, 0.0), (731, -0.65), (760, -0.3)):
                cmds.setKeyframe(cube, attribute=attr, t=f, v=v)
            cmds.keyTangent(
                cube, attribute=attr, time=(step_at, step_at), outTangentType="step"
            )
        for attr in also:  # keyed in the family, never stepped
            for f in (1, 12, 731):
                cmds.setKeyframe(cube, attribute=attr, t=f, v=f * 0.01)
        return cube

    def test_the_collector_lists_a_step_per_transform_family(self):
        from mayatk.env_utils.handoff_export import MayaExportMixin

        held = self._held("held")
        both = self._held("both", attrs=("rotateX", "rotateZ"))
        mixed = self._held("mixed", also=("translateX",))
        cmds.polyCube(name="plain")
        cmds.setKeyframe("plain", attribute="translateX", t=1, v=0)
        cmds.setKeyframe("plain", attribute="translateX", t=9, v=1)
        found = MayaExportMixin.stepped_keys()
        self.assertEqual(found.get(held), {"translate": [12.0]})
        self.assertEqual(found.get(both), {"rotate": [12.0]})
        # translateX keys 12 linearly: a family-wide CONSTANT would freeze it.
        self.assertNotIn(mixed, found)
        self.assertNotIn("plain", found)
        # Scoped to the nodes asked for, spelled as the carrier spells them.
        scoped = MayaExportMixin.stepped_keys(
            [held], spell=lambda name: "x_" + name.split("|")[-1]
        )
        self.assertEqual(scoped, {"x_held": {"translate": [12.0]}})

    def test_a_family_frame_needs_every_animated_channel_stepping_there(self):
        """Bug: a frame was listed where a sibling channel of its family moves
        through it with no key. Blender's FBX importer keys every transform
        curve of an object at the union of their times, so the sibling got a
        key there, made CONSTANT: it held where Maya ramps.
        Fixed: 2026-10-12"""
        from mayatk.env_utils.handoff_export import MayaExportMixin

        moving = self._held("moving_sibling")  # translateY steps at 12
        for f, v in ((0, 0.0), (30, 3.0)):  # translateX ramps through 12
            cmds.setKeyframe(moving, attribute="translateX", t=f, v=v)
        static = self._held("static_sibling")
        cmds.setKeyframe(static, attribute="translateX", t=5, v=1.0)  # one key
        found = MayaExportMixin.stepped_keys()
        self.assertNotIn(moving, found)
        self.assertEqual(found.get(static), {"translate": [12.0]})

    def test_a_send_with_animation_sidecars_its_steps(self):
        cube = self._held("sent_cube")
        fbx = os.path.join(self.tmp, "send.fbx")
        path = fbx + ".manifest.json"
        BlenderBridge()._write_manifest(
            [cube], fbx, include_materials=False, include_animation=True
        )
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(
            manifest[ptk.HandoffManifest.STEPPED_KEYS],
            {"sent_cube": {"translate": [12.0]}},
        )
        # A send without animation ships no curves, so no steps either.
        os.remove(path)
        BlenderBridge()._write_manifest(
            [cube], fbx, include_materials=False, include_scene_data=False
        )
        self.assertFalse(os.path.exists(path))


class TestHandoffShipsOneWholeTake(MayaTkTestCase):
    """A DCC hand-off FBX carries the animation as ONE whole-timeline take.

    Every importer plays one take (Maya the last, Blender the first per
    object) and the receiver lands the shots from the manifest's ``shots``
    section, so a per-shot split is nothing a DCC reads -- and an unresampled
    split cuts each shot's channels at its window (a curve with no key inside
    a shot contributes nothing to it), while the bake range shrinks to the
    union of the shots, dropping every key outside them.
    """

    def setUp(self):
        super().setUp()
        tmp = ptk.TempArtifacts("mtk_bridge_whole_take", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.fbx = tmp.path(extension=".fbx")
        self.addCleanup(self._drop_carrier)

    @staticmethod
    def _drop_carrier():
        from mayatk.node_utils.data_nodes import DataNodes

        for node in cmds.ls(DataNodes.EXPORT, long=True) or []:
            cmds.lockNode(node, lock=False, lockName=False)
            cmds.delete(node)

    def test_a_scene_with_shots_hands_off_one_take_holding_everything(self):
        """Bug: the Blender bridge split the scene's declared shots into
        per-shot takes beside a whole take bounded by the shots' union, so
        keys outside every shot never shipped.
        Fixed: 2026-10-13"""
        from mayatk.node_utils.data_nodes import DataNodes

        cmds.currentUnit(time="film")
        cmds.playbackOptions(animationStartTime=1, animationEndTime=100)
        cube = cmds.ls(cmds.polyCube(name="whole_take_cube")[0], long=True)[0]
        for frame, value in ((1, 0.0), (40, 4.0), (100, 10.0)):
            cmds.setKeyframe(cube, attribute="translateX", t=frame, v=value)
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {
                "shots": [
                    {"clip": "Shot_A", "start": 10, "end": 30, "objects": []},
                    {"clip": "Shot_B", "start": 50, "end": 70, "objects": []},
                ]
            },
        )
        self.assertEqual(len(handoff_export.FbxUtils.declared_takes()), 2)

        BlenderBridge()._export_fbx(
            [cube], self.fbx, {"INCLUDE_ANIMATION": True, "INCLUDE_MATERIALS": True}
        )

        fbx = ptk.FbxFile.load(self.fbx, raw_payloads=False, span_arrays=("KeyTime",))
        takes = fbx.take_names()
        self.assertEqual(len(takes), 1, f"takes: {takes}")
        first, last = fbx.take_spans()[takes[0]]
        fps = 24.0  # film, set above
        self.assertAlmostEqual(first * fps, 1.0, places=3)
        self.assertAlmostEqual(last * fps, 100.0, places=3)

    def test_the_takes_a_consumer_plays_still_split(self):
        """The Unity bridge (clips built from the takes) and the WebXR
        preview (one clip per take) keep the split; only the DCC hand-offs
        drop it."""
        from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge
        from mayatk.env_utils.webxr_preview import WebXrPreview

        self.assertIs(BlenderBridge.split_takes, False)
        self.assertIs(UnityBridge.split_takes, True)
        self.assertIs(WebXrPreview.split_takes, True)


class TestBridgeRebuildDeclaredOpacity(MayaTkTestCase):
    """A manifest-declared opacity must reach the shader that has a slot for it.

    ``slots["opacity"]`` names a file the filename taxonomy cannot place (a
    product-named cutout), rescued through ``MatManifest.restore`` AFTER the
    network is built. StingrayPBS takes its slots from the ShaderFX graph
    loaded at creation, so the declaration has to reach that decision --
    ``MapFactory.prepare_maps`` drops the file itself before then, which is
    why the graph is named outright rather than implied by the texture set.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bb_rebuild_")

    def _img(self, name):
        from PIL import Image

        path = os.path.join(self.tmp, name)
        Image.new("RGB", (8, 8), (128, 128, 128)).save(path)
        return path

    def test_declared_opacity_survives_prepare_maps_dropping_its_file(self):

        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        files = [
            self._img("bb_Base_Color.png"),
            self._img("bb_Roughness.png"),
            self._img("bb_ProductShot.png"),  # classifies to nothing
        ]
        mystery = files[-1]
        self.assertIsNone(
            ptk.MapFactory.resolve_map_type(mystery),
            "premise: the manifest is the only thing that knows this is a cutout",
        )
        self.assertNotIn(
            mystery,
            ptk.MapFactory.prepare_maps(files, group_by_set=False),
            "premise: prepare_maps drops it, so the set cannot vouch for it",
        )

        sg = BlenderSceneImport._rebuild_material(
            files, "bb_declared", {"opacity": mystery}, shader_type="stingray"
        )
        shader = cmds.listConnections(f"{sg}.surfaceShader", source=True)[0]

        self.assertTrue(
            cmds.attributeQuery("opacity", node=shader, exists=True),
            "the transparent graph was not loaded -- the rescued cutout has "
            "no slot to land in and the material arrives fully opaque",
        )
        # ...and the declaration is what CHOSE that graph, which is the half
        # this test exists for. Driving the slot is a separate, tracked
        # problem: ``Standard_Transparent.sfx`` has no sampler for a separate
        # opacity map -- ``opacity`` is a scalar uniform, and
        # ``use_opacity_map`` selects the COLOUR MAP'S alpha instead. So
        # ``connect_channel`` now refuses the wire rather than making the
        # dead connection it used to report as a success. Landing the map
        # means packing it into the colour alpha first; until then the
        # channel is honestly missing, NOT silently wrong.
        # See .claude/BACKLOG.md 2026-08-25 | mayatk | S2 | "manifest replay
        # cannot restore a SEPARATE opacity texture onto a transparent
        # StingrayPBS" -- flip this back when that lands.
        self.assertFalse(
            cmds.listConnections(f"{shader}.opacity", source=True),
            "a separate opacity file is wired again -- that plug is a scalar "
            "uniform, so the connection renders as one flat value",
        )


class TestBlenderBridgeSaveAs(MayaTkTestCase):
    """``save_as``: the same send pipeline delivered to a HEADLESS Blender.

    The Blender run itself is stubbed (it would take ~10s and needs an install); what's
    pinned here is that Maya's half is reused rather than reimplemented -- one FBX
    export, one manifest, the ``_save_scene`` template, and the whole scene by default.
    """

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")
        self.tmp = tempfile.mkdtemp(prefix="bb_saveas_")
        self.out = os.path.join(self.tmp, "asset.blend")
        self.runs = []

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _run_patch(self):
        """Stub the blocking runner; record the call and create the promised artifact."""

        def fake_run(app_exe, script_text, *, artifact, launch_args, timeout, env=None):
            self.runs.append(
                {
                    "app": app_exe,
                    "script": script_text,
                    "artifact": artifact,
                    "args": list(launch_args("S.py")),
                    "timeout": timeout,
                }
            )
            Path(artifact).write_text("blend", encoding="utf-8")

            return ptk.ScriptRunResult(
                artifact=artifact,
                returncode=0,
                output="",
                duration=0.1,
                script_path="S.py",
            )

        return mock.patch.object(ptk.ScriptRunDeliverer, "run", staticmethod(fake_run))

    def _export_patches(self):
        return (
            mock.patch.object(handoff_export.FbxUtils, "export", return_value="x.fbx"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        )

    def test_save_as_runs_blender_headless_on_the_save_template(self):
        cube = cmds.polyCube(name="bb_saveas")[0]
        export, load = self._export_patches()
        with export, load, self._run_patch():
            result = self.bridge.save_as(self.out, [cube])

        self.assertIsNotNone(result)
        self.assertEqual(result["output"], self.out)
        self.assertEqual(result["mode"], "save_as")
        self.assertEqual(len(self.runs), 1)
        run = self.runs[0]
        # Headless argv -- never an interactive session (and never a reused one).
        self.assertEqual(
            run["args"], ["--background", "--factory-startup", "--python", "S.py"]
        )
        # The rendered script is the save recipe, pointed at both paths. Blender writes
        # a staging sibling; the caller's path is the promotion target, so a failed run
        # can never destroy an existing .blend.
        import re

        staged = ptk.ScriptRunDeliverer._staging_path(self.out)
        self.assertIn("save_as_mainfile", run["script"])
        self.assertIn(f'OUT_FILE = r"{staged.replace(os.sep, "/")}"', run["script"])
        self.assertEqual(run["artifact"], staged)
        self.assertTrue(os.path.isfile(self.out))  # promoted
        self.assertFalse(os.path.exists(staged))
        self.assertFalse(re.findall(r"__[A-Z][A-Z0-9_]*__", run["script"]))

    def test_bake_lightmaps_targets_the_bake_template_and_keeps_json(self):
        """``bake_lightmaps`` writes its return manifest through the bake recipe.

        Three wiring points that each fail silently: the wrong template renders a
        ``save_as_mainfile`` script (a .blend named .json), a ``.json`` missing from
        ``save_extensions`` gets rewritten to ``.blend`` by ``resolve_save_path``, and a
        kwarg that never reaches the render context leaves the bake on its default.
        """
        cube = cmds.polyCube(name="bb_bake")[0]
        out = os.path.join(self.tmp, "room.lightmaps.json")
        export, load = self._export_patches()
        with export, load, self._run_patch():
            result = self.bridge.bake_lightmaps(
                out, [cube], environment_hdr="C:/hdri/room.hdr", samples=64
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["output"], out)  # .json survived resolve_save_path
        self.assertEqual(len(self.runs), 1)
        script = self.runs[0]["script"]
        # The bake recipe (blendertk's platform-agnostic baker), not _save_scene --
        # and no web/GLB machinery: deliverable encodes belong to the exporters.
        self.assertIn("LightmapBaker", script)
        self.assertNotIn("save_as_mainfile", script)
        self.assertNotIn("LightmapWebExport", script)
        # The quality preset resolves in Blender, where the preset store lives.
        self.assertIn("LIGHTMAP_QUALITY = 'mobile'", script)
        # Named kwargs reach the template as Python literals (overriding the preset).
        self.assertIn("LIGHTMAP_SAMPLES = 64", script)
        self.assertIn("ENVIRONMENT_HDR = 'C:/hdri/room.hdr'", script)
        # The fused/unlit level was removed, so the template no longer takes a mode
        # token -- but the manifest still CARRIES ``"mode": "separated"``, which is the
        # wire format every downstream reader keys on.
        self.assertNotIn("LIGHTMAP_MODE", script)
        self.assertIn('"mode": "separated"', script)
        self.assertFalse(re.findall(r"__[A-Z][A-Z0-9_]*__", script))

    def test_bake_lightmaps_outlives_the_default_run_timeout(self):
        """A bake must not inherit the 600s spec default.

        Measured: 7 objects at 512 samples / 1024px took ~13 minutes, and samples scale
        without limit. A timeout kill writes NO artifact, so inheriting the default
        presents as a silent bake failure roughly ten minutes in -- the most expensive
        kind of bug to diagnose, because the bake itself was working.
        """
        cube = cmds.polyCube(name="bb_bake_timeout")[0]
        export, load = self._export_patches()
        with export, load, self._run_patch():
            self.bridge.bake_lightmaps(
                os.path.join(self.tmp, "t.lightmaps.json"), [cube]
            )

        used = self.runs[0]["timeout"]
        self.assertIsNotNone(used)
        self.assertGreater(used, 600, "bake inherited the too-short spec default")
        # Sourced from the template, not hardcoded at the call site, so the panel route
        # (which calls round_trip directly) gets the same budget.
        self.assertEqual(
            used, BlenderBridge.template_timeout(_TEMPLATE_DIR / "bake_lightmaps.py")
        )

    def test_template_declares_its_artifact_format(self):
        """The artifact contract comes from the template, not its name.

        The compound suffix is load-bearing: the panel's reassembly step triggers on an
        artifact NAMED this way, so a drift between the declaration and
        ``RETURN_MANIFEST_SUFFIX`` would leave the bake succeeding while the round trip
        silently degraded to a one-way export. Its FINAL extension must still be one the
        bridge accepts, or ``resolve_save_path`` would rewrite the derived name.
        """
        declared = BlenderBridge.template_output_ext(
            _TEMPLATE_DIR / "bake_lightmaps.py"
        )
        self.assertEqual(declared, BlenderBridge.RETURN_MANIFEST_SUFFIX)
        self.assertIn(os.path.splitext(declared)[1], BlenderBridge.save_extensions)
        # The bake's artifact is pipeline plumbing: the panel derives its path
        # instead of prompting. An unannotated template keeps the save dialog.
        self.assertEqual(
            BlenderBridge.template_output_mode(_TEMPLATE_DIR / "bake_lightmaps.py"),
            "auto",
        )
        self.assertEqual(
            BlenderBridge.template_output_mode(_TEMPLATE_DIR / "import.py"), "prompt"
        )
        # An unannotated template falls back to the bridge's own default.
        self.assertEqual(
            BlenderBridge.template_output_ext(_TEMPLATE_DIR / "import.py"),
            BlenderBridge.save_extensions[0],
        )
        self.assertIsNone(BlenderBridge.template_timeout(_TEMPLATE_DIR / "import.py"))

    def test_derived_artifact_lands_in_tracked_temp_not_the_project(self):
        """An ``auto`` artifact is plumbing, so it must not be written into the project.

        The return manifest is read once, on the way back into the scene, and never
        opened again. Deriving it beside the maps put a machine-readable JSON into
        ``sourceimages`` on every bake with nothing to ever collect it; ``TempArtifacts``
        owns an age-swept namespace instead. The scene stem stays in the name so a log
        line still identifies the run, and the template's declared compound suffix must
        survive intact -- the panel triggers reassembly on it.
        """
        out = BlenderBridge.default_output_path("bake_lightmaps")
        self.assertTrue(out.endswith(BlenderBridge.RETURN_MANIFEST_SUFFIX), out)
        # The store's own folder under the ecosystem temp root.
        self.assertEqual(
            os.path.normcase(os.path.dirname(out)),
            os.path.normcase(os.path.join(ptk.TempArtifacts.root(), "blender_bridge")),
            out,
        )
        project = BlenderBridge._default_lightmap_dir()
        if project:  # a workspace is always set in Maya, but do not assume it here
            self.assertNotEqual(
                os.path.normcase(os.path.dirname(out)), os.path.normcase(project)
            )
        # The maps themselves are the opposite case -- a real project artifact, resolved
        # through the workspace's own texture rule.
        self.assertTrue(
            not project or project.lower().endswith("sourceimages"), project
        )

    def _textured_cube(self, name, tex_path):
        """A cube wearing a lambert whose colour comes from a file node at *tex_path*."""
        os.makedirs(os.path.dirname(tex_path), exist_ok=True)
        with open(tex_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")  # header only; nothing decodes it
        mat = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_matSG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{node}.fileTextureName", tex_path, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{mat}.color", force=True)
        cube = cmds.polyCube(name=name)[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        return cmds.ls(cube, long=True)[0]

    def test_lightmaps_default_beside_the_textures_they_join(self):
        """The map is one more map of the set, so it lands in that set's own folder.

        A production texture set lives in its OWN subfolder (``sourceimages/ROOM_ENV/``),
        so defaulting to the ``sourceimages`` ROOT drops the lightmap a level above the
        maps it belongs to -- where it reads as belonging to no set, and where an artist
        looking beside the albedo will not find it.
        """
        tex_dir = os.path.join(self.tmp, "sourceimages", "SET_A")
        cube = self._textured_cube(
            "lm_dir_a", os.path.join(tex_dir, "SET_A_Base_color.png")
        )
        self.assertEqual(
            os.path.normcase(BlenderBridge._default_lightmap_dir([cube])),
            os.path.normcase(tex_dir),
        )

    def test_the_texture_folder_holding_the_most_maps_wins(self):
        """A selection spanning sets has no single right answer; the majority is picked."""
        one = os.path.join(self.tmp, "sourceimages", "SET_ONE")
        many = os.path.join(self.tmp, "sourceimages", "SET_MANY")
        cubes = [
            self._textured_cube(
                "lm_dir_one", os.path.join(one, "SET_ONE_Base_color.png")
            ),
            self._textured_cube(
                "lm_dir_m1", os.path.join(many, "SET_MANY_Base_color.png")
            ),
            self._textured_cube("lm_dir_m2", os.path.join(many, "SET_MANY_Normal.png")),
        ]
        self.assertEqual(
            os.path.normcase(BlenderBridge._default_lightmap_dir(cubes)),
            os.path.normcase(many),
        )

    def test_a_panel_bake_lands_its_maps_outside_tracked_temp(self):
        """The PANEL path must default LIGHTMAP_DIR too, or the maps are age-swept.

        ``bake_lightmaps`` (the API) defaults the directory, but the panel calls
        ``round_trip`` straight through with its widget params -- where the key is
        the spec default ``""``. The template then falls back to the artifact's own
        folder, which is ``TempArtifacts("blender_bridge")``: the finished lightmaps
        are committed into the scene from a directory whose whole contract is that
        it gets swept by age. ``_default_lightmap_dir``'s own docstring says
        "Never temp"; this is the path that ignored it.
        """
        cube = cmds.polyCube(name="bb_panel_bake")[0]
        out = BlenderBridge.default_output_path("bake_lightmaps")
        export, load = self._export_patches()
        with export, load, self._run_patch():
            self.bridge.round_trip(
                [cube],
                out=out,
                template="bake_lightmaps",
                params=dict(params.Parameters.defaults()),
            )

        script = self.runs[0]["script"]
        rendered = re.search(r"^LIGHTMAP_DIR = (.+)$", script, re.M)
        self.assertIsNotNone(rendered, script[:400])
        folder = rendered.group(1).strip().strip("'\"r")
        self.assertTrue(folder, "LIGHTMAP_DIR was left empty on the panel path")
        # The session folder standing in for it: the maps reach it when the
        # scene is saved, from a store this session owns -- never age-swept
        # while it runs (LightmapRecords._stage).
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        self.assertEqual(
            os.path.normcase(LightmapRecords._home(folder.replace("/", os.sep))),
            os.path.normcase(BlenderBridge._default_lightmap_dir([cube])),
        )

    def test_untextured_objects_fall_back_to_sourceimages(self):
        """No texture set to join -> the project's own texture folder, never temp."""
        cube = cmds.ls(cmds.polyCube(name="lm_dir_bare")[0], long=True)[0]
        fallback = BlenderBridge._default_lightmap_dir([cube])
        self.assertEqual(fallback, BlenderBridge._default_lightmap_dir())
        self.assertTrue(
            not fallback or fallback.lower().endswith("sourceimages"), fallback
        )

    def test_bare_path_gets_the_blend_extension(self):
        cube = cmds.polyCube(name="bb_saveas_ext")[0]
        export, load = self._export_patches()
        with export, load, self._run_patch():
            result = self.bridge.save_as(os.path.join(self.tmp, "asset"), [cube])
        self.assertTrue(result["output"].endswith(".blend"))

    def test_defaults_to_the_whole_scene(self):
        """ "Save the scene as ..." is about the scene -- selection state is irrelevant."""
        cube = cmds.polyCube(name="bb_saveas_scene")[0]
        cmds.select(clear=True)
        export, load = self._export_patches()
        with (
            export,
            load,
            self._run_patch(),
            mock.patch.object(
                self.bridge, "_export_fbx", return_value=None
            ) as m_export,
        ):
            # _export_fbx is stubbed, so no FBX lands -- the payload path still
            # threads through and the run stub writes the artifact.
            self.bridge.save_as(self.out)
        exported = m_export.call_args.args[0]
        self.assertIn(cube, [n.split("|")[-1] for n in exported])

    def test_startup_cameras_are_not_part_of_the_scene(self):
        """They are viewport furniture; FBX drops them anyway, so carrying them would
        only make the exported set lie about what is being saved."""
        cmds.polyCube(name="bb_saveas_cams")
        roots = self.bridge._scene_objects()
        leaves = {n.split("|")[-1] for n in roots}
        self.assertFalse(leaves & {"persp", "top", "front", "side"}, roots)

    def test_save_template_is_hidden_from_the_panel(self):
        """It is not a user-pickable send recipe -- it belongs to save_as."""
        self.assertNotIn(
            "_save_scene", [p.stem for p in BlenderBridge.list_templates()]
        )
        self.assertTrue((_TEMPLATE_DIR / "_save_scene.py").is_file())

    def test_save_template_declares_only_save_as(self):
        """Read through the strict parser the blocking deliverer uses."""
        from pythontk import ScriptTemplate

        self.assertEqual(
            ScriptTemplate.declared_modes(_TEMPLATE_DIR / "_save_scene.py"),
            ("save_as",),
        )

    def test_bake_template_is_rejected_for_save_as(self):
        """The bake declares ``round_trip`` alone, and the strict parser holds it to that.

        Not pedantry: ``save_as`` would run the identical bake and then hand the caller a
        manifest with nothing to read it -- the return leg (``_ingest``) is what makes the
        maps land in the scene. Failing in preflight beats succeeding at a no-op minutes
        later, which is indistinguishable from a bake that produced nothing.
        """
        cube = cmds.polyCube(name="bb_bake_saveas")[0]
        export, load = self._export_patches()
        with export as m_export, load, self._run_patch():
            result = self.bridge.save_as(
                os.path.join(self.tmp, "x.lightmaps.json"),
                [cube],
                template="bake_lightmaps",
            )
        self.assertIsNone(result)
        self.assertEqual(self.runs, [])
        m_export.assert_not_called()  # aborted before the export

    def test_interactive_template_is_rejected_for_save_as(self):
        """``import.py`` never writes a .blend -- catch it in preflight, not 10s later."""
        cube = cmds.polyCube(name="bb_saveas_badtpl")[0]
        export, load = self._export_patches()
        with export as m_export, load, self._run_patch():
            result = self.bridge.save_as(self.out, [cube], template="import")
        self.assertIsNone(result)
        self.assertEqual(self.runs, [])
        m_export.assert_not_called()  # aborted before the export

    def test_texture_manifest_rides_along(self):
        """save_as reuses ``_produce``, so the material sidecar is not a send-only fix."""
        cube = cmds.polyCube(name="bb_saveas_mat")[0]
        fbx = os.path.join(self.tmp, "payload.fbx")
        export, load = self._export_patches()
        with (
            export,
            load,
            self._run_patch(),
            mock.patch.object(self.bridge, "_make_payload_path", return_value=fbx),
            mock.patch.object(self.bridge, "_write_manifest") as m_manifest,
        ):
            self.bridge.save_as(self.out, [cube])
        m_manifest.assert_called_once()
        self.assertEqual(m_manifest.call_args.args[1], fbx)


class TestBridgeScopeParam(unittest.TestCase):
    """Every Maya hand-off bridge exposes the shared Scope combo."""

    _BRIDGES = (
        "mayatk.env_utils.blender_bridge.parameters",
        "mayatk.env_utils.unity_bridge.parameters",
        "mayatk.mat_utils.marmoset_bridge.parameters",
        "mayatk.mat_utils.substance_bridge.parameters",
        "mayatk.uv_utils.rizom_bridge.parameters",
    )

    def test_every_bridge_registers_scope(self):
        import importlib

        for name in self._BRIDGES:
            mod = importlib.import_module(name)
            with self.subTest(bridge=name):
                self.assertIn("SCOPE", mod.PARAMS)
                spec = mod.PARAMS["SCOPE"]
                self.assertEqual(spec.default, "selected")
                self.assertEqual(
                    [v for _label, v in spec.choices],
                    ["selected", "all", "visible"],
                )

    # Bridges whose target reads USD -> (engine class, the send templates whose
    # panel must show the Format combo). A bridge declaring only ("fbx",) must
    # NOT register the param: a choice the engine would refuse is not a choice.
    _CARRIER_SURFACES = (
        (
            "mayatk.env_utils.blender_bridge",
            "_blender_bridge",
            "BlenderBridge",
            ("import",),
        ),
        (
            "mayatk.mat_utils.marmoset_bridge",
            "_marmoset_bridge",
            "MarmosetBridge",
            ("bake", "import", "lookdev"),
        ),
        (
            "mayatk.mat_utils.substance_bridge",
            "_substance_bridge",
            "SubstanceBridge",
            ("import", "bake_lighting"),
        ),
    )

    def test_every_usd_capable_bridge_exposes_the_format_combo(self):
        """The panel renders a param only where the active template echoes its
        token, so the engine's ``carriers`` and the templates' ``__CARRIER__``
        echoes must agree -- on every send template, not just one."""
        import importlib

        for pkg, module, cls_name, templates in self._CARRIER_SURFACES:
            params = importlib.import_module(pkg + ".parameters")
            engine = importlib.import_module(f"{pkg}.{module}")
            with self.subTest(bridge=pkg):
                self.assertEqual(getattr(engine, cls_name).carriers, ("fbx", "usd"))
                spec = params.PARAMS["CARRIER"]
                self.assertEqual(spec.default, "fbx")
                self.assertEqual([v for _l, v in spec.choices], ["fbx", "usd"])
                template_dir = engine._TEMPLATE_DIR
                for stem in templates:
                    text = (template_dir / f"{stem}.py").read_text(encoding="utf-8")
                    self.assertIn(
                        "CARRIER",
                        params.Parameters.referenced_keys(text),
                        f"{stem}.py does not surface the Format combo",
                    )

    def test_fbx_only_bridges_offer_no_format_choice(self):
        import importlib

        for pkg in ("mayatk.env_utils.unity_bridge", "mayatk.uv_utils.rizom_bridge"):
            params = importlib.import_module(pkg + ".parameters")
            with self.subTest(bridge=pkg):
                self.assertNotIn("CARRIER", params.PARAMS)

    def test_scope_specs_are_distinct_objects(self):
        """A shared mutable spec would let one bridge's tweak leak into all."""
        import importlib

        specs = [importlib.import_module(n).PARAMS["SCOPE"] for n in self._BRIDGES]
        self.assertEqual(len({id(s) for s in specs}), len(specs))


class TestBridgeLightmapRoundTrip(unittest.TestCase):
    """The return leg: a Blender bake wired back into the Maya scene.

    Blender owns the lightmap job end to end and hands back the finished UV layout, so
    everything here is about receiving that faithfully -- above all, never wiring one
    object's lightmap onto another, which reads as a bad bake rather than a bug.
    """

    def test_resolves_blender_names_against_the_exported_selection(self):
        resolved, ambiguous, unmatched = BlenderBridge._resolve_returned_objects(
            ["pCube1", "wall"], ["|grp|pCube1", "|grp|wall", "|grp|floor"]
        )
        self.assertEqual(resolved, {"pCube1": "|grp|pCube1", "wall": "|grp|wall"})
        self.assertEqual((ambiguous, unmatched), ([], []))

    def test_strips_blenders_collision_suffix(self):
        """Blender renames an incoming duplicate to ``name.001``; that is still the node."""
        resolved, _amb, unmatched = BlenderBridge._resolve_returned_objects(
            ["pCube1.001"], ["|grp|pCube1"]
        )
        self.assertEqual(resolved, {"pCube1.001": "|grp|pCube1"})
        self.assertEqual(unmatched, [])

    def test_refuses_to_guess_between_duplicate_leaf_names(self):
        """Maya allows |a|wheel and |b|wheel; guessing would mis-assign a lightmap."""
        resolved, ambiguous, _un = BlenderBridge._resolve_returned_objects(
            ["wheel"], ["|a|wheel", "|b|wheel"]
        )
        self.assertEqual(resolved, {})
        self.assertEqual(ambiguous, ["wheel"])

    # -- position-based disambiguation -------------------------------------
    #
    # A duplicated group leaves two exported nodes sharing a leaf name, FBX
    # collapses the paths, and Blender hands back `PROPS_083` + `PROPS_083.001`.
    # Measured on a production room: 2 of 50 objects went unlit. The manifest now
    # carries each object's location, and matching is by SHAPE of the point set
    # rather than by coordinates, so no axis or unit convention is assumed
    # anywhere -- the Maya->Blender crossing is Y-up/cm to Z-up/m, and a fix that
    # hard-codes that mapping mis-wires silently the day either side changes it.

    #: Maya world translations for a scene with two same-named cabinets.
    _MAYA_POS = {
        "|DA1|PROPS_083": (100.0, 0.0, 0.0),
        "|DA2|PROPS_083": (900.0, 0.0, 400.0),
        "|grp|anchor_a": (0.0, 0.0, 0.0),
        "|grp|anchor_b": (500.0, 200.0, 0.0),
        "|grp|anchor_c": (0.0, 0.0, 800.0),
    }

    @staticmethod
    def _as_blender(point):
        """Y-up centimetres to Z-up metres -- what the FBX crossing actually does.

        The resolver is never told this. It is applied only to build the fixture,
        so a resolver that assumed any particular mapping would fail the test.
        """
        x, y, z = point
        return (x * 0.01, -z * 0.01, y * 0.01)

    def _round_trip(self, names, extra_maya=None):
        maya = dict(self._MAYA_POS)
        maya.update(extra_maya or {})
        locations = {}
        for name in names:
            node = names[name]
            locations[name] = self._as_blender(maya[node]) if node else None
        return maya, {k: v for k, v in locations.items() if v is not None}

    def test_position_disambiguates_two_same_named_nodes(self):
        names = {
            "PROPS_083": "|DA1|PROPS_083",
            "PROPS_083.001": "|DA2|PROPS_083",
            "anchor_a": "|grp|anchor_a",
            "anchor_b": "|grp|anchor_b",
            "anchor_c": "|grp|anchor_c",
        }
        maya, locations = self._round_trip(names)
        resolved, ambiguous, unmatched = BlenderBridge._resolve_returned_objects(
            list(names),
            list(maya),
            locations=locations,
            node_position=maya.get,
        )
        self.assertEqual((ambiguous, unmatched), ([], []))
        self.assertEqual(resolved["PROPS_083"], "|DA1|PROPS_083")
        self.assertEqual(resolved["PROPS_083.001"], "|DA2|PROPS_083")

    def test_a_manifest_without_locations_still_refuses_to_guess(self):
        """An older Blender side ships no location; behaviour must not change."""
        resolved, ambiguous, _un = BlenderBridge._resolve_returned_objects(
            ["wheel"],
            ["|a|wheel", "|b|wheel"],
            locations={},
            node_position=lambda n: None,
        )
        self.assertEqual(resolved, {})
        self.assertEqual(ambiguous, ["wheel"])

    def test_two_duplicates_at_the_SAME_position_stay_ambiguous(self):
        """A genuine tie is not resolvable, and guessing it is the original bug."""
        names = {
            "PROPS_083": "|DA1|PROPS_083",
            "PROPS_083.001": "|DA2|PROPS_083",
            "anchor_a": "|grp|anchor_a",
            "anchor_b": "|grp|anchor_b",
            "anchor_c": "|grp|anchor_c",
        }
        maya, locations = self._round_trip(names)
        maya["|DA2|PROPS_083"] = maya["|DA1|PROPS_083"]  # stacked duplicates
        resolved, ambiguous, _un = BlenderBridge._resolve_returned_objects(
            list(names), list(maya), locations=locations, node_position=maya.get
        )
        self.assertNotIn("PROPS_083", resolved)
        self.assertIn("PROPS_083", ambiguous)

    def test_two_duplicated_groups_in_one_run_do_not_steal_each_other(self):
        """A production room duplicates more than one thing.

        Every pending name is scored against every candidate, so the scoring
        has to be restricted to the nodes a name actually matched. Here a wheel
        sits at exactly the position of the OTHER group's cabinet -- legal, and
        the adversarial case: unscoped, that cabinet scores a perfect zero for
        `wheel` and competes for it, and however that resolves the wheel does
        not end up wired to its own node.
        """
        maya = dict(self._MAYA_POS)
        maya.update(
            {
                "|DA1|wheel": maya["|DA2|PROPS_083"],  # decoy for the other name
                "|DA2|wheel": (150.0, 0.0, 50.0),
            }
        )
        names = {
            "PROPS_083": "|DA1|PROPS_083",
            "PROPS_083.001": "|DA2|PROPS_083",
            "wheel": "|DA1|wheel",
            "wheel.001": "|DA2|wheel",
            "anchor_a": "|grp|anchor_a",
            "anchor_b": "|grp|anchor_b",
            "anchor_c": "|grp|anchor_c",
        }
        locations = {n: self._as_blender(maya[node]) for n, node in names.items()}
        resolved, ambiguous, unmatched = BlenderBridge._resolve_returned_objects(
            list(names), list(maya), locations=locations, node_position=maya.get
        )
        self.assertEqual((ambiguous, unmatched), ([], []))
        for name, node in names.items():
            self.assertEqual(resolved[name], node, f"{name} wired to the wrong node")

    def test_two_anchors_refuse_even_though_the_distances_would_separate_them(self):
        """Three anchors is the floor, and these duplicates are NOT the reason.

        Two anchors give exactly one pair, so the measured scale has nothing to
        agree with and the similarity check is vacuous. These duplicates sit at
        distinct distances from both anchors, so a two-anchor signature would
        happily separate them -- the refusal is the calibration floor doing its
        job, not a tie.
        """
        maya = {
            "|a|wheel": (100.0, 0.0, 0.0),
            "|b|wheel": (300.0, 0.0, 0.0),
            "|grp|anchor_a": (0.0, 0.0, 0.0),
            "|grp|anchor_b": (500.0, 0.0, 0.0),
        }
        locations = {
            "wheel": self._as_blender(maya["|a|wheel"]),
            "wheel.001": self._as_blender(maya["|b|wheel"]),
            "anchor_a": self._as_blender(maya["|grp|anchor_a"]),
            "anchor_b": self._as_blender(maya["|grp|anchor_b"]),
        }
        resolved, ambiguous, _un = BlenderBridge._resolve_returned_objects(
            ["wheel", "wheel.001", "anchor_a", "anchor_b"],
            list(maya),
            locations=locations,
            node_position=maya.get,
        )
        self.assertNotIn("wheel", resolved)
        self.assertEqual(sorted(ambiguous), ["wheel", "wheel.001"])

    def test_leaf_matches_names_every_node_a_returned_name_could_be(self):
        """The colliding nodes, ``.001`` suffix ignored -- what the warning must name."""
        pool = ["|a|wheel", "|b|wheel", "|c|hub", "|d|ns:wheel"]
        self.assertEqual(
            BlenderBridge._leaf_matches("wheel.001", pool),
            ["|a|wheel", "|b|wheel", "|d|ns:wheel"],
        )
        self.assertEqual(BlenderBridge._leaf_matches("hub", pool), ["|c|hub"])
        self.assertEqual(BlenderBridge._leaf_matches("axle", pool), [])

    def test_ignores_scene_objects_the_run_did_not_export(self):
        """Resolution is scoped to this run, so a same-named stranger can't be picked up."""
        resolved, _amb, unmatched = BlenderBridge._resolve_returned_objects(
            ["stranger"], ["|grp|pCube1"]
        )
        self.assertEqual((resolved, unmatched), ({}, ["stranger"]))

    def test_missing_manifest_is_reported_and_commits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            gone = os.path.join(tmp, "nope.lightmaps.json")
            self.assertEqual(BlenderBridge().reassemble_lightmaps(gone, []), {})

    def test_unreadable_manifest_does_not_raise(self):
        """The bake already cost minutes; a bad manifest must not throw its result away."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.lightmaps.json")
            Path(bad).write_text("{not json")
            self.assertEqual(BlenderBridge().reassemble_lightmaps(bad, []), {})

    def test_lightmap_dir_is_a_registered_parameter(self):
        """It must reach the panel, or the EXRs silently land beside the manifest."""
        self.assertIn("LIGHTMAP_DIR", params.PARAMS)
        template = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        self.assertIn("__LIGHTMAP_DIR__", template)

    def test_template_leaves_registered_string_tokens_bare(self):
        """Registered params arrive pre-rendered as literals; quoting yields "'x'"."""
        template = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        self.assertIn("LIGHTMAP_DIR = __LIGHTMAP_DIR__", template)
        self.assertNotIn('LIGHTMAP_DIR = r"__LIGHTMAP_DIR__"', template)

    def test_bake_lightmaps_indexes_the_same_set_it_exported(self):
        """The bake resolves its export set ONCE, rather than defaulting twice.

        Left unresolved, the bake would export the scene and then reassemble against an
        empty list: every object "unmatched", nothing wired back, and no error -- the
        artifact still lands and the run still reports success.
        """
        bridge = BlenderBridge()
        with (
            mock.patch.object(
                BlenderBridge, "round_trip", return_value=None
            ) as round_trip,
            mock.patch.object(
                BlenderBridge, "_scene_objects", return_value=["|grp|pCube1"]
            ) as scene_objects,
        ):
            bridge.bake_lightmaps()
        scene_objects.assert_called_once()
        self.assertEqual(round_trip.call_args.args[0], ["|grp|pCube1"])
        # No out= given: the derived path follows the manifest naming convention
        # (which is also what triggers the reassembly step) -- and it comes from the
        # template's declaration, with no template named in the deriver.
        derived = str(round_trip.call_args.kwargs["out"])
        self.assertTrue(derived.endswith(BlenderBridge.RETURN_MANIFEST_SUFFIX), derived)

    @staticmethod
    def _template_constant(name):
        """Read a module-level constant out of the (unrendered) bake template."""
        import ast

        src = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError(f"{name} is not a module-level constant in the template")

    def test_sidecar_contract_matches_the_template(self):
        """Both sides hardcode it -- the template runs in Blender and cannot import this.

        A drift wouldn't raise anywhere: the bake would succeed, the panel would simply
        not find the sidecar, and the round trip would quietly become a one-way export.
        """
        self.assertEqual(
            self._template_constant("RETURN_MANIFEST_VERSION"),
            BlenderBridge.RETURN_MANIFEST_VERSION,
        )

    def test_refuses_a_sidecar_from_a_newer_schema(self):
        """Misreading a newer payload writes WRONG UVs -- worse than doing nothing."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "future.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": BlenderBridge.RETURN_MANIFEST_VERSION + 1,
                        "objects": {"pCube1": {"map": "x.exr", "uv_layout": {}}},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                BlenderBridge().reassemble_lightmaps(manifest, ["|grp|pCube1"]), {}
            )

    def test_refuses_when_two_baked_objects_resolve_to_one_maya_node(self):
        """They would collapse in the caller's {maya: ...} mapping, winner arbitrary.

        The surviving object would wear the other's lightmap -- which reads as a bad
        bake, not as a name-resolution bug, so it must never be guessed at.
        """
        resolved, ambiguous, _un = BlenderBridge._resolve_returned_objects(
            ["wheel", "wheel.001"], ["|a|wheel"]
        )
        self.assertEqual(resolved, {})
        self.assertEqual(sorted(ambiguous), ["wheel", "wheel.001"])


class TestBridgePerInstanceLightmaps(MayaTkTestCase):
    """Instances are FIRST-CLASS lightmap citizens on the separated path.

    Each copy is baked separately (each stands in different light) and its atlas rect
    travels as a per-transform ``scaleOffset`` binding -- Unity's native
    ``Renderer.lightmapScaleOffset`` model -- over the ONE shared [0,1] unwrap, so the
    scene's instancing survives untouched. Instancing survives the crossing too
    (``FBXExportInstances`` pinned on; Blender links duplicates -- probe-measured).
    """

    def _instanced_pair(self):
        cube = cmds.polyCube(name="bb_inst_src")[0]
        copy = cmds.instance(cube, name="bb_inst_copy")[0]
        return cmds.ls(cube, long=True)[0], cmds.ls(copy, long=True)[0]

    def _run_patch(self):
        """Stub the blocking Blender run and create the artifact it promises."""

        def fake_run(app_exe, script_text, *, artifact, launch_args, timeout, env=None):

            Path(artifact).write_text("{}", encoding="utf-8")
            return ptk.ScriptRunResult(
                artifact=artifact,
                returncode=0,
                output="",
                duration=0.1,
                script_path="S.py",
            )

        return mock.patch.object(ptk.ScriptRunDeliverer, "run", staticmethod(fake_run))

    @staticmethod
    def _layout_from(shape):
        """A valid return-manifest UV layout, read from *shape*'s own current UVs.

        Loop-order float32 base64 -- the same wire format Blender's
        ``export_uv_layout`` produces -- so ``apply_uv_layout``'s topology
        fingerprint matches by construction.
        """
        import array
        import base64

        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        us, vs = fn.getUVs()
        counts, uv_ids = fn.getAssignedUVs()
        buf = array.array("f")
        for uv_id in uv_ids:
            buf.append(us[uv_id])
            buf.append(vs[uv_id])
        return {
            "uv_set": "lightmap",
            "poly_counts": list(counts),
            "num_verts": fn.numVertices,
            "uvs": base64.b64encode(buf.tobytes()).decode("ascii"),
        }

    def test_detects_a_shape_worn_by_several_transforms(self):
        src, copy = self._instanced_pair()
        found = BlenderBridge._instanced_shapes([src, copy])
        self.assertEqual(len(found), 1)
        self.assertEqual(len(next(iter(found.values()))), 2)
        # A plain (non-instanced) mesh is not flagged.
        solo = cmds.ls(cmds.polyCube(name="bb_solo")[0], long=True)[0]
        self.assertEqual(BlenderBridge._instanced_shapes([solo]), {})

    def test_preflight_accepts_instances_on_the_separated_path(self):
        src, copy = self._instanced_pair()
        request = mock.Mock(template="bake_lightmaps", params={})
        bridge = BlenderBridge()
        with mock.patch.object(ptk.HandoffBridge, "_preflight", return_value=True):
            self.assertTrue(bridge._preflight([src, copy], request))

    def test_the_real_bake_path_exports_instanced_geometry(self):
        """The acceptance has to hold on the route the PANEL takes, not just directly.

        The flip of the old refusal test: an instanced bake now reaches the FBX export
        and the (stubbed) Blender run.
        """
        src, copy = self._instanced_pair()
        bridge = BlenderBridge(blender_path="C:/fake/blender.exe")
        export, load = (
            mock.patch.object(handoff_export.FbxUtils, "export", return_value="x.fbx"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "asset.lightmaps.json")
            with export as m_export, load, self._run_patch():
                bridge.round_trip(
                    [src, copy], template="bake_lightmaps", params={}, out=out
                )
            m_export.assert_called()
            exported = {
                str(o).rsplit("|", 1)[-1]
                for o in m_export.call_args.kwargs.get("objects") or []
            }
            self.assertEqual(exported, {"bb_inst_src", "bb_inst_copy"})

    def test_v2_manifest_wires_instances_with_their_own_rects(self):
        """The shared shape gets the layout ONCE; each transform gets its own rect."""
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        src, copy = self._instanced_pair()
        shape = cmds.listRelatives(src, shapes=True, fullPath=True)[0]
        rect_a, rect_b = [0.5, 1.0, 0.0, 0.0], [0.5, 1.0, 0.5, 0.0]
        with tempfile.TemporaryDirectory() as tmp:
            exr = os.path.join(tmp, "atlas_Lightmap.exr")
            open(exr, "wb").close()
            manifest = os.path.join(tmp, "x.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "separated",
                        "lighting": {},
                        "meshes": {"Mesh": self._layout_from(shape)},
                        "objects": {
                            "bb_inst_src": {"map": exr, "mesh": "Mesh", "rect": rect_a},
                            "bb_inst_copy": {
                                "map": exr,
                                "mesh": "Mesh",
                                "rect": rect_b,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            wired = BlenderBridge().reassemble_lightmaps(manifest, [src, copy])
        self.assertEqual(set(wired), {src, copy})
        self.assertEqual(LightmapRecords._marker_info(src)["scaleOffset"], rect_a)
        self.assertEqual(LightmapRecords._marker_info(copy)["scaleOffset"], rect_b)
        # The layout landed on the SHARED shape (once), as the lightmap set.
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertIn("lightmap", sets)
        # And the publisher carries one record per instance.
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.read(ptk.Scope.DELIVERABLE, ptk.SceneRecords.LIGHTMAPS.key)
        recs = {o["name"]: o for o in json.loads(raw)["objects"]}
        self.assertEqual(set(recs), {"bb_inst_src", "bb_inst_copy"})
        self.assertEqual(recs["bb_inst_src"]["scaleOffset"], rect_a)
        self.assertEqual(recs["bb_inst_copy"]["scaleOffset"], rect_b)

    def test_v1_manifest_still_reassembles(self):
        """Legacy inline-layout manifests keep working (identity rects)."""
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        solo = cmds.ls(cmds.polyCube(name="bb_v1_solo")[0], long=True)[0]
        shape = cmds.listRelatives(solo, shapes=True, fullPath=True)[0]
        with tempfile.TemporaryDirectory() as tmp:
            exr = os.path.join(tmp, "v1_Lightmap.exr")
            open(exr, "wb").close()
            manifest = os.path.join(tmp, "x.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "mode": "separated",
                        "objects": {
                            "bb_v1_solo": {
                                "map": exr,
                                "uv_layout": self._layout_from(shape),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            wired = BlenderBridge().reassemble_lightmaps(manifest, [solo])
        self.assertEqual(set(wired), {solo})
        info = LightmapRecords._marker_info(solo)
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])

    def test_a_legacy_marker_is_settled_before_the_returned_layout_lands(self):
        """``commit`` carries a LEGACY marker's ``uvRect`` forward: the squeeze it
        records is still in the UVs until something restores it. The return leg
        REPLACES those UVs with Blender's layout, so the record is settled first
        (``LightmapRecords.migrate_legacy``, as every bake does). Carried onto the
        new layout, a later revert or migration would invert a squeeze that is no
        longer there, over the layout the map was baked on."""
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        solo = cmds.ls(cmds.polyCube(name="bb_legacy_solo")[0], long=True)[0]
        shape = cmds.listRelatives(solo, shapes=True, fullPath=True)[0]
        layout = self._layout_from(shape)  # what Blender hands back
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        rect = [0.5, 0.5, 0.25, 0.25]
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "old_atlas.exr")
            exr = os.path.join(tmp, "bb_legacy_solo_Lightmap.exr")
            for path in (old, exr):
                open(path, "wb").close()
            # A commit from before rect binding: the UVs squeezed into the
            # object's atlas cell, and the squeeze recorded as uvRect.
            LightmapRecords._transform_lightmap_uvs(shape, "lightmap", rect)
            LightmapRecords.commit({solo: old})
            legacy = LightmapRecords._marker_info(solo)
            legacy["uvRect"] = list(rect)
            LightmapRecords._write_marker(LightmapRecords._marker_node(solo), legacy)
            self.assertEqual(LightmapRecords._marker_info(solo)["uvRect"], rect)
            manifest = os.path.join(tmp, "x.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "separated",
                        "lighting": {},
                        "meshes": {"Mesh": layout},
                        "objects": {"bb_legacy_solo": {"map": exr, "mesh": "Mesh"}},
                    }
                ),
                encoding="utf-8",
            )
            wired = BlenderBridge().reassemble_lightmaps(manifest, [solo])
        self.assertEqual(set(wired), {solo})
        info = LightmapRecords._marker_info(solo)
        self.assertNotIn("uvRect", info)
        self.assertEqual(info["map"], "bb_legacy_solo_Lightmap.exr")
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(LightmapRecords.migrate_legacy([solo]), [])

    def test_ingest_owns_the_return_leg_and_never_the_browser(self):
        """One return leg for panel and API: _ingest reassembles, and stops there.

        The panel no longer sniffs the artifact suffix -- a round trip through the REAL
        route must reassemble exactly once and honor ``reassemble=False``. It must
        also leave the browser alone: viewing the bake is the WebXR Preview button's
        job, and a scene operation that reaches for a network service on its own has
        no way to fail quietly.
        """
        solo = cmds.ls(cmds.polyCube(name="bb_ingest")[0], long=True)[0]
        bridge = BlenderBridge(blender_path="C:/fake/blender.exe")
        export, load = (
            mock.patch.object(handoff_export.FbxUtils, "export", return_value="x.fbx"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.lightmaps.json")
            with (
                export,
                load,
                self._run_patch(),
                mock.patch.object(
                    BlenderBridge, "reassemble_lightmaps", return_value={solo: "m.exr"}
                ) as m_re,
                mock.patch("mayatk.env_utils.webxr_preview.WebXrPreview") as m_prev,
            ):
                result = bridge.round_trip(
                    [solo],
                    template="bake_lightmaps",
                    params={},
                    out=out,
                )
            m_re.assert_called_once()
            self.assertEqual(result.get("reassembled"), {solo: "m.exr"})
            m_prev.assert_not_called()

            # reassemble=False opts the return leg out entirely.
            with (
                export,
                load,
                self._run_patch(),
                mock.patch.object(BlenderBridge, "reassemble_lightmaps") as m_re3,
            ):
                bridge.round_trip(
                    [solo],
                    template="bake_lightmaps",
                    params={},
                    out=out,
                    reassemble=False,
                )
            m_re3.assert_not_called()

    def test_fbx_options_pin_instancing_on(self):
        """FBX options are sticky session state and another bridge sets this False."""
        options = BlenderBridge()._fbx_options({})
        self.assertIs(options.get("FBXExportInstances"), True)

    def test_the_handoff_write_starts_from_a_RESET_fbx_state(self):
        """Pinning is not enough: what is NOT pinned must not be inherited.

        ``_fbx_options`` pins nine flags; the plugin has many more, they are
        sticky for the life of the session, and the ones it does not name decide
        the deliverable's CONTENT --
        ``FBXExportReferencedAssetsContent`` most of all, since a referenced
        module (a whole office environment, on the assembly this was reported
        against) is either in the file or is not. Whoever ran last therefore
        decided part of the hand-off, so the same scene pushed twice in one
        session could ship different geometry. The Scene Exporter has always
        reset before pinning (``_apply_default_fbx_options``) and
        ``FbxUtils.reset_export``'s own docstring says to; this path did not,
        which is precisely the preview-vs-deliverable divergence the mixin
        exists to remove.

        The reset must NOT move into ``FbxUtils.export``: the Scene Exporter
        arms its bake range and take split BEFORE the write, and a reset there
        would wipe both.
        """
        import maya.mel as mel

        from mayatk.env_utils.webxr_preview import WebXrPreview

        flag = "FBXExportReferencedAssetsContent"  # sticky, and NOT pinned
        handoff_export.FbxUtils.reset_export()
        factory = mel.eval(f"{flag} -q")

        mesh = cmds.ls(cmds.polyCube(name="sticky_probe")[0], long=True)[0]
        # Another bridge's leftovers, which is all it takes.
        mel.eval(f"{flag} -v {'false' if factory else 'true'}")
        self.assertNotEqual(mel.eval(f"{flag} -q"), factory, "probe did not perturb")

        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_export,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {})

        self.assertEqual(
            mel.eval(f"{flag} -q"),
            factory,
            f"{flag} was inherited from the session instead of reset",
        )
        # The pins still land on top of the reset -- the reset must not undo them.
        self.assertIs(m_export.call_args.kwargs["options"]["FBXExportInstances"], True)

    def test_an_animated_handoff_with_no_takes_bakes_the_scenes_own_range(self):
        """The factory bake range is 1-48, which is not the scene's anything.

        ``apply_takes`` sets a union range whenever shots are DECLARED, so the
        uncovered case is animation with none -- where the export would ship 48
        frames of a timeline of any length. Measured on Maya 2025: a reset
        leaves ``FBXExportBakeComplexStart/End`` at 1 and 48 regardless of the
        scene.
        """
        import maya.mel as mel

        from mayatk.env_utils.webxr_preview import WebXrPreview

        cmds.playbackOptions(animationStartTime=20, animationEndTime=310)
        mesh = cmds.ls(cmds.polyCube(name="range_probe")[0], long=True)[0]
        cmds.setKeyframe(mesh, attribute="translateX", time=20, value=0)
        cmds.setKeyframe(mesh, attribute="translateX", time=310, value=5)

        with (
            mock.patch.object(handoff_export.FbxUtils, "export"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
            mock.patch.object(
                handoff_export.FbxUtils, "apply_takes_from_node", return_value=0
            ),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {"INCLUDE_ANIMATION": True})

        self.assertEqual(mel.eval("FBXExportBakeComplexStart -q"), 20)
        self.assertEqual(mel.eval("FBXExportBakeComplexEnd -q"), 310)

    def test_scene_lights_travel_as_manifest_data_not_fbx_lights(self):
        """The bake is of the artist's own lighting -- carried as DATA, not as FBX lights.

        The light OBJECT deliberately never enters the FBX: Blender 5.1's bundled
        importer sets ``lamp.cycles.cast_shadow`` (removed in Cycles 5.x), so ONE
        light aborts the entire import, geometry and all (measured on 5.1.2). Its
        transform still ships as a null the importer places correctly, so the
        manifest carries only the parameters -- the same division of labour the
        material section uses, and the only route that can carry an ``aiAreaLight``
        at all.
        """
        self.assertIs(BlenderBridge().params_defaults()["INCLUDE_LIGHTS"], True)
        self.assertIs(BlenderBridge()._fbx_options({})["FBXExportLights"], False)

        spot = cmds.spotLight(name="bb_key")
        transform = cmds.listRelatives(
            cmds.ls(spot, long=True)[0], parent=True, fullPath=True
        )[0]
        cmds.setAttr(f"{transform}.rotateX", -90)  # aim straight down
        cmds.setAttr(f"{cmds.ls(spot, long=True)[0]}.coneAngle", 60)

        records = BlenderBridge()._manifest_lights([transform])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["name"], "bb_key")
        self.assertEqual(record["type"], "SPOT")
        self.assertAlmostEqual(record["spot_size"], math.radians(60), places=6)
        # AIM is load-bearing and measured: Maya's exporter reconciles light nodes
        # against FBX's light-axis convention, so the arriving null's ROTATION is
        # just the importer's up-axis fix -- a spot aimed down would bake sideways.
        # Maya aims down local -Z, and rotateX=-90 turns that into world -Y.
        self.assertEqual([round(v, 6) for v in record["aim"]], [0.0, -1.0, 0.0])
        self.assertEqual(record["axis_up"], "Y")

    def test_light_type_map_and_energy_anchor(self):
        """Types Cycles cannot express are skipped, not silently approximated."""
        self.assertEqual(BlenderBridge.LIGHT_TYPES["aiAreaLight"], "AREA")
        for unsupported in ("ambientLight", "volumeLight"):
            self.assertNotIn(unsupported, BlenderBridge.LIGHT_TYPES)

        # A SUN is exempt from the wattage anchor: Maya directional intensity and
        # Blender sun irradiance are both "1.0 = full", so it maps 1:1. Everything
        # else converts, because Maya intensity is unitless.
        sun = cmds.directionalLight(name="bb_sun")
        sun_xf = cmds.listRelatives(
            cmds.ls(sun, long=True)[0], parent=True, fullPath=True
        )[0]
        cmds.setAttr(f"{cmds.ls(sun, long=True)[0]}.intensity", 2.0)
        point = cmds.pointLight(name="bb_pt")
        point_xf = cmds.listRelatives(
            cmds.ls(point, long=True)[0], parent=True, fullPath=True
        )[0]
        cmds.setAttr(f"{cmds.ls(point, long=True)[0]}.intensity", 2.0)

        by_name = {
            r["name"]: r for r in BlenderBridge()._manifest_lights([sun_xf, point_xf])
        }
        self.assertEqual(by_name["bb_sun"]["energy"], 2.0)
        self.assertEqual(
            by_name["bb_pt"]["energy"], 2.0 * BlenderBridge.WATTS_PER_INTENSITY
        )

    def test_exposure_stops_fold_into_energy(self):
        """Arnold puts most of its range in EXPOSURE, not intensity.

        Reading intensity alone is off by 2**exposure -- a factor of 32 at a routine
        exposure 5. Probed by attribute rather than node type so it is not tied to
        one plugin's naming; a Maya-native light simply has no such attr.
        """
        shape = cmds.ls(cmds.pointLight(name="bb_exp"), long=True)[0]
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.intensity", 1.0)
        base = BlenderBridge()._manifest_lights([transform])[0]["energy"]

        cmds.addAttr(shape, longName="exposure", attributeType="double")
        cmds.setAttr(f"{shape}.exposure", 5.0)
        lifted = BlenderBridge()._manifest_lights([transform])[0]["energy"]
        self.assertEqual(lifted, base * 32.0)

    def test_light_params_get_panel_rows(self):
        """A knob that changes what the FBX carries must be visible, not implicit.

        Row visibility is driven by the template referencing ``__KEY__``, so both
        templates echo INCLUDE_LIGHTS -- otherwise the import recipe would start
        shipping lights with no row to turn it off. INCLUDE_ENVIRONMENT is the
        bake's alone: only its template lights a world from the scene's sky dome.
        """
        for spec_key in (
            "INCLUDE_LIGHTS",
            "SCENE_LIGHT_STRENGTH",
            "INCLUDE_ENVIRONMENT",
        ):
            self.assertIn(spec_key, params.PARAMS)
            self.assertIn(spec_key, BlenderBridge().params_defaults())
        bake = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        self.assertIn("INCLUDE_LIGHTS", bake)
        self.assertIn("SCENE_LIGHT_STRENGTH", bake)
        self.assertIn("INCLUDE_ENVIRONMENT", bake)
        imported = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "import.py").read_text(encoding="utf-8")
        )
        self.assertIn("INCLUDE_LIGHTS", imported)
        self.assertNotIn("INCLUDE_ENVIRONMENT", imported)

    def test_the_bake_declares_no_preview_knob_at_all(self):
        """Bake and preview are decoupled: neither the pre-step nor a post-step.

        ``LIGHTMAP_UNINSTANCE`` died with the instanced refusal. A
        ``LIGHTMAP_PREVIEW`` post-step briefly took its gated row and is gone too --
        the preview button reads the committed scene on its own, so a bake-time
        toggle would only be a second way to spell "click it".
        """
        for key in ("LIGHTMAP_UNINSTANCE", "LIGHTMAP_PREVIEW"):
            self.assertNotIn(key, params.PARAMS)
            self.assertNotIn(key, BlenderBridge().params_defaults())

    def test_the_bridge_ships_no_metadata_carrier(self):
        """Only a bridge whose CONSUMER reads data_export opts in; Blender doesn't.

        The carrier would arrive in Blender as a stray empty and the bake template
        never looks at it -- the flag's default has to stay off for the round trip.
        """
        self.assertIs(BlenderBridge().include_data_export, False)
        self.assertEqual(BlenderBridge()._data_export_carrier(), [])

    def test_webxr_preview_export_carries_the_committed_manifest(self):
        """The last leg of the round trip: bake here, click Preview, see it lit.

        Lives with the bake tests because it is the same contract -- the commit is
        only worth anything if the manifest reaches a consumer. ``lightmaps``
        rides ``data_export``, which is NOT under the selected roots, so a selection
        push exported the meshes alone and the preview came back unlit with nothing
        in the log to explain it. Pinned at the export set: whatever the caller asks
        for, the carrier goes too.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        mesh = cmds.ls(cmds.polyCube(name="bb_prev_mesh")[0], long=True)[0]
        DataNodes.write(
            ptk.Scope.DELIVERABLE, ptk.SceneRecords.LIGHTMAPS.key, '{"version": 1}'
        )
        # Resolve the carrier the way the product does. The export set now folds
        # in EVERY carrier (a referenced module publishes onto its own namespaced
        # one), and that plural resolver returns unambiguous LONG paths.
        carrier = DataNodes.get_export_nodes()[0]

        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_export,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {})
        exported = m_export.call_args.kwargs["objects"]
        self.assertIn(mesh, exported)
        self.assertIn(carrier, exported)

        # The strip-materials path must ship it too -- and must NOT duplicate it
        # (a locked, shapeless node has no materials to strip).
        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_strip,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {"INCLUDE_MATERIALS": False})
        stripped = m_strip.call_args.kwargs["objects"]
        self.assertIn(carrier, stripped)
        self.assertNotIn(mesh, stripped)  # the mesh went as a shader-less copy

    def test_a_referenced_modules_namespaced_carrier_ships_too(self):
        """An assembly's lightmap manifest lives on the REFERENCE's carrier.

        ``cmds.ls("data_export")`` does not match ``NS:data_export``, so the
        single-carrier resolve was blind to it and a selection push of a
        referenced, fully-baked module shipped a GLB with no manifest -- it
        previewed unlit with the bake sitting in the scene. Measured on a
        production assembly whose 48-object manifest was on
        ``ROOM_ENV:data_export``.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        DataNodes.ensure_export()
        cmds.namespace(add="MODULE")
        namespaced = cmds.ls(
            cmds.createNode("transform", name="MODULE:data_export"), long=True
        )[0]
        mesh = cmds.ls(cmds.polyCube(name="ns_carrier_mesh")[0], long=True)[0]

        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_export,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {})

        exported = m_export.call_args.kwargs["objects"]
        self.assertIn(namespaced, exported)
        self.assertIn("|data_export", exported)

    def test_webxr_preview_ships_the_start_camera_it_was_not_given(self):
        """The page opens its views through the scene's ``user_pos`` camera and
        stands a headset under it -- so the preview lets cameras through its FBX
        (the hand-off default keeps them out), and a push joins the start camera
        to whatever its scope chose: a referenced set's namespaced one, unless
        the root namespace holds one. An empty push stays empty."""
        from mayatk.env_utils.webxr_preview import WebXrPreview

        bridge = WebXrPreview()
        self.assertIs(bridge._fbx_options({})["FBXExportCameras"], True)
        self.assertIsNone(bridge._start_node("user_pos"))

        mesh = cmds.ls(cmds.polyCube(name="start_cam_mesh")[0], long=True)[0]
        cmds.namespace(add="SET")
        cam = cmds.ls(cmds.rename(cmds.camera()[0], "SET:user_pos"), long=True)[0]
        self.assertEqual(bridge._start_node("user_pos"), cam)

        sent = []
        with mock.patch.object(
            WebXrPreview,
            "send",
            side_effect=lambda objects, **kwargs: sent.append(list(objects)),
        ):
            bridge.push(objects=[mesh])
            bridge.push(objects=[])
        self.assertEqual(sent, [[mesh, cam], []])

        root = cmds.ls(cmds.spaceLocator(name="user_pos")[0], long=True)[0]
        self.assertEqual(bridge._start_node("user_pos"), root)

    def test_a_materials_strip_ships_what_holds_no_shading_as_itself(self):
        """The strip copies what it takes materials off, and only that: a camera
        or a locator has none, and its copy would ship under Maya's uniquified
        name -- the preview's start camera as ``user_pos1``, found by nothing.
        A set with nothing shaded copies nothing at all. The ``data_export``
        carrier rides beside the copies: the export states the shaded
        set's materials on it, and the preview's GLB build reads that
        record off it (``ptk.MeshConvert._apply_fbx_materials``).
        Updated: 2026-10-20"""
        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.env_utils.webxr_preview import WebXrPreview

        mesh = cmds.ls(cmds.polyCube(name="strip_mesh")[0], long=True)[0]
        cam = cmds.ls(cmds.rename(cmds.camera()[0], "user_pos"), long=True)[0]
        loc = cmds.ls(cmds.spaceLocator(name="strip_marker")[0], long=True)[0]
        stripped = {"INCLUDE_MATERIALS": False}

        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_export,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh, cam, loc], "x.fbx", stripped)
            exported = list(m_export.call_args.kwargs["objects"])
            WebXrPreview()._export_fbx([cam, loc], "x.fbx", stripped)
            unshaded = list(m_export.call_args.kwargs["objects"])

        self.assertIn(cam, exported)
        self.assertIn(loc, exported)
        self.assertNotIn(mesh, exported)  # the mesh went as a shader-less copy
        carrier = f"|{DataNodes.EXPORT}"
        self.assertIn(carrier, exported)
        self.assertEqual(len([o for o in exported if o != carrier]), 3)
        # Nothing in that send is shaded, so its records state no material and
        # no carrier rides: the hand-off's context is scoped to what it sends.
        self.assertEqual(unshaded, [cam, loc])

    def test_a_subset_hand_off_states_only_the_materials_it_ships(self):
        """A hand-off's records state what it ships: its context is scoped to
        the roots it sends, as the Scene Exporter's is, so a send of one of
        two shaded meshes carries that mesh's material alone -- not every
        material in the scene -- and the scene keeps its own records: the
        materials record, the write's alone, is cleared after it.
        Added: 2026-10-20"""
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        def shaded(name):
            mesh = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
            mat = cmds.shadingNode("blinn", asShader=True, name=f"{name}_mat")
            sg = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
            cmds.sets(mesh, edit=True, forceElement=sg)
            return mesh, mat

        sent, sent_mat = shaded("sentMesh")
        _kept, kept_mat = shaded("keptMesh")
        stated = {}

        def capture(*_args, **_kwargs):
            stated["record"] = ptk.SceneRecords.MATERIALS.load(DataNodes)

        with (
            mock.patch.object(handoff_export.FbxUtils, "export", side_effect=capture),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([sent], "x.fbx", {})

        names = {m["name"] for m in (stated.get("record") or {}).get("materials", [])}
        self.assertIn(sent_mat, names)
        self.assertNotIn(kept_mat, names)
        self.assertIsNone(
            ptk.SceneRecords.MATERIALS.load(DataNodes),
            "the materials record is the write's alone",
        )

    def test_declared_takes_are_realized_for_an_animated_handoff(self):
        """Shots must survive the preview leg, not only the Scene Exporter's.

        The session hook that splits takes on File > Export is opt-in and
        nothing installs it headless, so the split reached only the exporter --
        which calls it explicitly. Measured on a 12-shot production assembly:
        the exporter's GLB carried 12 named clips and the preview's carried one
        whole-timeline ``Take 001``. Two writers of the same deliverable must
        not disagree about whether shots survive.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        # A scene that declares one take: the shot record's clip, with its range.
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {"shots": [{"clip": "Shot_1", "start": 1, "end": 10, "objects": []}]},
        )

        for animation, expected in ((True, 1), (False, 0)):
            with self.subTest(animation=animation):
                with (
                    mock.patch.object(handoff_export.FbxUtils, "export"),
                    mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
                    mock.patch.object(
                        handoff_export.FbxUtils, "apply_takes_from_node", return_value=1
                    ) as m_apply,
                    mock.patch.object(
                        handoff_export.FbxUtils, "reset_takes"
                    ) as m_reset,
                ):
                    WebXrPreview()._export_fbx(
                        [], "x.fbx", {"INCLUDE_ANIMATION": animation}
                    )
                self.assertEqual(m_apply.call_count, expected)
                # Take splits are sticky global exporter state: left armed they
                # leak into the user's own next File > Export.
                self.assertEqual(m_reset.call_count, expected)

    def test_metadata_carrier_is_never_invented(self):
        """No committed metadata -> no carrier -> nothing added to the export set.

        A scene that has never been baked must not gain a stray empty in its GLB
        just because the preview is willing to carry one.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        if cmds.objExists(DataNodes.EXPORT):
            cmds.lockNode(DataNodes.EXPORT, lock=False, lockName=False)
            cmds.delete(DataNodes.EXPORT)
        self.assertEqual(WebXrPreview()._data_export_carrier(), [])
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))

    def _drop_carrier(self):
        """Remove the LOCKED ``data_export`` node a test left behind.

        ``DataNodes`` locks the carrier, and the suite's scene reset cannot
        delete a locked node — so a test that stamps a channel leaks it into
        every test after it (measured: it broke an unrelated light-manifest
        test three classes later).
        """
        from mayatk.node_utils.data_nodes import DataNodes

        for node in cmds.ls(DataNodes.EXPORT, long=True) or []:
            cmds.lockNode(node, lock=False, lockName=False)
            cmds.delete(node)

    def test_a_bridge_that_ships_the_carrier_refreshes_it_first(self):
        """The preview must not ship a channel the exporter would rebuild.

        Most channels are persisted state, but some are DERIVED from the live
        scene each export — ``render_effects`` reads the visibility curves
        themselves — and those go stale the moment an artist re-keys. The Scene
        Exporter refreshes via its own task; without the same call here the same
        scene previewed one way and exported another, which is precisely the
        divergence the preview exists to rule out.

        Driven through ``_export_fbx``, because that is where the refresh now
        happens: the write runs inside ``FbxUtils.export_prepared`` with a
        HANDOFF context, which refreshes the DERIVED records and brackets the
        whole export. Reading the carrier on its own would prove nothing about
        what an export ships.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects
        from mayatk.node_utils.data_nodes import DataNodes

        self.addCleanup(self._drop_carrier)
        grp = cmds.group(cmds.polyCube()[0], name="PREVIEW_GATE")
        RenderEffects.key_fade([grp], start=5, end=20, direction="in")
        DataNodes.write(ptk.Scope.DELIVERABLE, RenderEffects.DATA_CHANNEL, "")
        self.assertFalse(
            DataNodes.read(ptk.Scope.DELIVERABLE, RenderEffects.DATA_CHANNEL)
        )

        with (
            mock.patch.object(handoff_export.FbxUtils, "export") as m_export,
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([grp], "x.fbx", {})

        shipped = m_export.call_args.kwargs["objects"]
        self.assertTrue(
            [n for n in shipped if DataNodes.EXPORT in n],
            f"the carrier itself must still ship: {shipped}",
        )
        published = DataNodes.read(ptk.Scope.DELIVERABLE, RenderEffects.DATA_CHANNEL)
        self.assertTrue(published, "the derived channel was not refreshed")
        self.assertIn("PREVIEW_GATE", published)

    def test_the_refresh_does_not_clear_a_channel_it_cannot_regenerate(self):
        """The narrow half of the rule above, and the reason it IS narrow.

        A producer with nothing to publish CLEARS its channel — that is right
        for an export pipeline, which is the authority on every channel, and
        wrong for a hand-off that merely SHIPS the carrier. Refreshing the whole
        set from here wiped a `lightmaps` whose markers the scene no
        longer carried, and the preview then shipped that asset unlit.

        Driven through ``_export_fbx`` for the reason the test above gives, and
        here it is load-bearing: asserting a channel is NOT cleared passes
        trivially against a path that runs no preparers at all, so this has to
        be the path that DOES run them.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes

        self.addCleanup(self._drop_carrier)
        mesh = cmds.polyCube(name="bb_keepchan")[0]
        DataNodes.write(ptk.Scope.DELIVERABLE, "lightmaps", '{"version": 1}')

        with (
            mock.patch.object(handoff_export.FbxUtils, "export"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
        ):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {})

        self.assertEqual(
            DataNodes.read(ptk.Scope.DELIVERABLE, "lightmaps"), '{"version": 1}'
        )

    def test_visible_scope_keeps_every_instance_sibling(self):
        """The shape->first-parent coercion silently dropped instance siblings."""
        from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

        src, copy = self._instanced_pair()
        slots = mock.Mock(spec=[])  # no .bridge -- the visible path needs none
        got = MayaBridgeSlotsBase.resolve_scope_objects(slots, "visible")
        self.assertIn(src, got)
        self.assertIn(copy, got)
        # A hidden sibling must not ride in on its visible twin's shape.
        cmds.setAttr(f"{copy}.visibility", 0)
        got = MayaBridgeSlotsBase.resolve_scope_objects(slots, "visible")
        self.assertIn(src, got)
        self.assertNotIn(copy, got)


class TestBridgeAtlasCells(MayaTkTestCase):
    """A bridge-baked atlas records its cells, so a partial re-bake goes in place.

    The template hands back where in its atlas each object was written
    (``bake_atlased``: blendertk's ``_bake_atlas``), and the return leg records
    it in the bake baseline (``LightmapRecords.commit(cells=)``) with the extent
    measured HERE -- what a later bake measures against before writing a cell
    back in place (Blender's is in its own scene's units). Before, the bridge
    passed no cells, and every partial re-bake of a bridge-baked atlas
    re-packed its whole group.
    """

    ATLAS = "Crate_Lightmap.exr"
    RES = 64
    CELLS = {
        "bbc_a": [0.25, 0.25, 0.0625, 0.0625],
        "bbc_b": [0.25, 0.25, 0.375, 0.0625],
        "bbc_c": [0.25, 0.25, 0.6875, 0.0625],
    }

    def setUp(self):
        super().setUp()
        self.store = self.enterContext(ptk.TempArtifacts("bb_cells", policy="scoped"))
        self.dir = self.store.dir_path(name="maps")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        mat = cmds.shadingNode("lambert", asShader=True, name="bbcCrateMat")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        fn = cmds.shadingNode("file", asTexture=True, name="bbcCrateFile")
        cmds.setAttr(
            f"{fn}.fileTextureName", "C:/tex/Crate_BaseColor.png", type="string"
        )
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)
        self.cubes = {}
        for i, name in enumerate(self.CELLS):
            cube = cmds.polyCube(name=name)[0]
            cmds.move(i * 10.0, 0, 0, cube)
            shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
            cmds.sets(shape, edit=True, forceElement=sg)
            self.cubes[name] = cmds.ls(cube, long=True)[0]

    def _cell_rows(self, name):
        return ptk.ImgUtils.atlas_pixel_rects([self.CELLS[name]], self.RES)[0]

    def _write_atlas(self):
        """The atlas a bridge bake returns: each cube's cell one flat value."""
        import cv2
        import numpy as np

        img = np.zeros((self.RES, self.RES, 3), np.float32)
        for value, name in enumerate(self.CELLS, start=1):
            row0, row1, col0, col1 = self._cell_rows(name)
            img[row0:row1, col0:col1] = float(value)
        path = os.path.join(self.dir, self.ATLAS)
        cv2.imwrite(path, img)
        return path

    def _manifest(self, cells=True):
        atlas = self._write_atlas()
        meshes, objects = {}, {}
        for name, node in self.cubes.items():
            shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
            meshes[name] = TestBridgePerInstanceLightmaps._layout_from(shape)
            entry = {"map": atlas, "mesh": name, "rect": list(self.CELLS[name])}
            if cells:
                # Blender's extent is in ITS units: deliberately not Maya's.
                entry.update(
                    cell=list(self.CELLS[name]),
                    extent=[999.0, 999.0],
                    size=self.RES,
                    packing="atlas",
                )
            objects[name] = entry
        path = os.path.join(self.store.dir_path(), "bb_cells.lightmaps.json")
        Path(path).write_text(
            json.dumps(
                {
                    "version": BlenderBridge.RETURN_MANIFEST_VERSION,
                    "mode": "separated",
                    "lighting": {},
                    "meshes": meshes,
                    "objects": objects,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_the_return_leg_records_each_cell_with_the_extent_measured_here(self):
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        wired = BlenderBridge().reassemble_lightmaps(
            self._manifest(), list(self.cubes.values())
        )
        self.assertEqual(set(wired), set(self.cubes.values()))
        state = LightmapRecords.bake_state()["objects"]
        for name, node in self.cubes.items():
            entry = state[node]
            self.assertEqual(entry["cell"], self.CELLS[name])
            self.assertEqual((entry["size"], entry["packing"]), (self.RES, "atlas"))
            self.assertEqual(entry["map"], self.ATLAS.lower())
            for got, want in zip(entry["extent"], LightmapBaker()._atlas_extent(node)):
                self.assertAlmostEqual(got, want, places=5)

    def test_a_partial_rebake_of_a_bridge_baked_atlas_goes_in_place(self):
        import cv2
        import numpy as np

        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        BlenderBridge().reassemble_lightmaps(
            self._manifest(), list(self.cubes.values())
        )
        atlas = os.path.join(self.dir, self.ATLAS)
        before = cv2.imread(atlas, cv2.IMREAD_UNCHANGED)
        a = self.cubes["bbc_a"]

        class _FlatBaker:
            """A bake backend rendering one flat 5.0 per object."""

            def bake(self, objects, output_dir=None, prefix="", suffix="", **_kw):
                out = {}
                for obj in objects:
                    long_name = cmds.ls(obj, long=True)[0]
                    leaf = long_name.rsplit("|", 1)[-1]
                    img = np.full((16, 16, 4), 5.0, np.float32)
                    path = os.path.join(output_dir, f"{prefix}{leaf}{suffix}.exr")
                    cv2.imwrite(path, img)
                    out[long_name] = path
                return out

        result = LightmapBaker(
            resolution=self.RES, baker=_FlatBaker(), reflection_probe=False
        ).bake([a], packing="atlas", output_dir=self.dir)
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        # The atlas as the scene reads it: in the session until the save.
        after = cv2.imread(LightmapRecords._stage(atlas), cv2.IMREAD_UNCHANGED)
        self.assertEqual(result.in_place, [a], result.repacked)
        self.assertEqual(
            sorted(f for f in os.listdir(self.dir) if f.endswith(".exr")),
            [self.ATLAS],
            "no _1 atlas",
        )
        for name in ("bbc_b", "bbc_c"):
            row0, row1, col0, col1 = self._cell_rows(name)
            self.assertTrue(
                np.array_equal(
                    after[row0:row1, col0:col1], before[row0:row1, col0:col1]
                ),
                f"{name}'s cell changed",
            )
        row0, row1, col0, col1 = self._cell_rows("bbc_a")
        self.assertTrue(np.allclose(after[row0:row1, col0:col1], 5.0, atol=1e-3))

    def test_an_older_blendertk_records_no_cells_and_says_what_that_costs(self):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        bridge = BlenderBridge()
        with mock.patch.object(bridge.logger, "info") as info:
            bridge.reassemble_lightmaps(
                self._manifest(cells=False), list(self.cubes.values())
            )
        state = LightmapRecords.bake_state()["objects"]
        self.assertTrue(all(state[n]["cell"] is None for n in self.cubes.values()))
        said = " ".join(str(c.args[0]) for c in info.call_args_list)
        self.assertIn("carry no atlas cell", said)
        self.assertIn("re-packs its whole group", said)

    def test_the_template_ships_the_cells_its_blendertk_returns(self):
        """The Blender half, on stand-ins for bpy and blendertk: a blendertk with
        ``_bake_atlas`` hands back the cells and each manifest entry carries
        them; an older one bakes through ``bake_atlas`` alone and says so."""
        import ast
        import io
        from contextlib import redirect_stdout

        source = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        nodes = [
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef)
            and n.name in ("bake_atlased", "write_return_manifest")
        ]
        code = compile(ast.Module(nodes, type_ignores=[]), "template", "exec")
        out_file = os.path.join(self.store.dir_path(), "t.lightmaps.json")
        obj = types.SimpleNamespace(
            data=types.SimpleNamespace(name="CrateMesh"),
            matrix_world=types.SimpleNamespace(translation=(1.0, 2.0, 3.0)),
        )
        bpy = types.SimpleNamespace(data=types.SimpleNamespace(objects={"crate": obj}))
        uv_module = types.ModuleType("_uv_utils")
        uv_module.UvUtils = types.SimpleNamespace(
            export_uv_layout=lambda objs: {"crate": {"uvs": ""}}
        )
        namespace = {
            "bpy": bpy,
            "os": os,
            "json": json,
            "OUT_FILE": out_file,
            "RETURN_MANIFEST_VERSION": BlenderBridge.RETURN_MANIFEST_VERSION,
        }
        exec(code, namespace)
        cell = {"cell": [0.5, 0.5, 0.0, 0.0], "extent": [2.0, 1.0]}
        cell.update(size=64, packing="atlas")
        packed = {"crate": ("C:/maps/Crate_Lightmap.exr", [0.5, 0.5, 0.0, 0.0])}

        class _NewBaker:
            def _bake_atlas(self, meshes, **kwargs):
                self.kwargs = kwargs
                return types.SimpleNamespace(packed=packed, cells={"crate": cell})

        class _OldBaker:
            def bake_atlas(self, meshes, **kwargs):
                self.kwargs = kwargs
                return packed

        new = _NewBaker()
        got_packed, got_cells = namespace["bake_atlased"](
            new, ["crate"], "C:/maps", "", "_Lightmap", {"x.exr": ["y"]}
        )
        self.assertEqual((got_packed, got_cells), (packed, {"crate": cell}))
        self.assertEqual(new.kwargs["claims"], {"x.exr": ["y"]})
        with (
            mock.patch.dict(sys.modules, {"blendertk.uv_utils._uv_utils": uv_module}),
            redirect_stdout(io.StringIO()),
        ):
            namespace["write_return_manifest"](got_packed, {}, None, got_cells)
        entry = json.loads(Path(out_file).read_text(encoding="utf-8"))["objects"][
            "crate"
        ]
        self.assertEqual({k: entry[k] for k in cell}, cell)

        printed = io.StringIO()
        with redirect_stdout(printed):
            old_packed, old_cells = namespace["bake_atlased"](
                _OldBaker(), ["crate"], "C:/maps", "", "_Lightmap", None
            )
        self.assertEqual((old_packed, old_cells), (packed, {}))
        self.assertIn("returns no atlas cells", printed.getvalue())


class TestBridgeLightManifest(MayaTkTestCase):
    """``_manifest_lights`` -- the only carrier of a light's PHYSICS across the bridge.

    The FBX ships the light's transform as a null and nothing else (a real light
    node aborts Blender 5.1's whole import), so every one of these values is either
    read correctly here or lost silently -- and a bake that comes back wrong reads
    as a baker bug, not a translation one.
    """

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    @staticmethod
    def _set_attr(shape, attr, value, attr_type="float"):
        """Set *attr* on *shape*, adding it first only if it is not already there.

        The ``ai*`` attributes these tests exercise are ADDED TO NATIVE MAYA
        LIGHTS by mtoa, so whether they pre-exist depends on whether the plugin
        happens to be loaded -- which in a chunked run depends on which other
        module shared the process. Adding unconditionally raises there, and
        assuming they are absent silently tests the wrong branch; this pins the
        attribute's VALUE either way, which is what every caller here means.
        """
        if not cmds.attributeQuery(attr, node=shape, exists=True):
            cmds.addAttr(shape, longName=attr, attributeType=attr_type)
        cmds.setAttr(f"{shape}.{attr}", value)

    @staticmethod
    def _light(kind, **attrs):
        """A light of *kind* with *attrs* set on its shape; returns (transform, shape)."""
        transform = cmds.shadingNode(kind, asLight=True)
        shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
        for attr, value in attrs.items():
            cmds.setAttr(f"{shape}.{attr}", value)
        return cmds.ls(transform, long=True)[0], shape

    def _record(self, transform):
        records = self.bridge._manifest_lights([transform])
        return records[0] if records else None

    def test_ai_exposure_on_a_native_maya_light_reaches_the_energy(self):
        """mtoa spells it ``aiExposure`` on a NATIVE light, not ``exposure``.

        Exposure is in STOPS, so probing only Arnold's own spelling reads a native
        rig lit at exposure 5 as 32x too dim -- and the bake comes back looking
        unlit with nothing in the log to say why.
        """
        transform, shape = self._light("pointLight", intensity=2.0)
        # The attribute IS mtoa's, so it may or may not already be on the node;
        # either way what this test needs is its value (see _set_attr).
        self._set_attr(shape, "aiExposure", 5.0)

        record = self._record(transform)
        self.assertAlmostEqual(
            record["energy"],
            2.0 * 32.0 * BlenderBridge.WATTS_PER_INTENSITY,
            places=3,
        )

    def test_spot_penumbra_widens_the_cone_rather_than_eating_it(self):
        """Maya's positive penumbra falls off OUTSIDE the cone; Blender blends inward.

        Publishing the bare cone as ``spot_size`` and adding a blend carves the
        penumbra out of the lit core instead of appending it, so every softened
        spot bakes a smaller hotspot than Maya renders.
        """
        transform, _shape = self._light("spotLight", coneAngle=40.0, penumbraAngle=10.0)
        record = self._record(transform)
        # 40 + 2*10 = 60 degrees lit, of which the outer 20 (2*10) is the blend.
        self.assertAlmostEqual(record["spot_size"], math.radians(60.0), places=5)
        self.assertAlmostEqual(record["spot_blend"], 20.0 / 60.0, places=5)

    def test_a_negative_penumbra_softens_inward_and_keeps_the_cone(self):
        """Blender's own model -- the cone is unchanged, only the falloff moves in."""
        transform, _shape = self._light(
            "spotLight", coneAngle=40.0, penumbraAngle=-10.0
        )
        record = self._record(transform)
        self.assertAlmostEqual(record["spot_size"], math.radians(40.0), places=5)
        self.assertAlmostEqual(record["spot_blend"], 20.0 / 40.0, places=5)

    def test_a_non_normalized_area_light_travels_as_radiance_not_watts(self):
        """Maya's areaLight emits PER UNIT AREA, and the area is NOT knowable here.

        Blender's ``energy`` is total watts, so the emitting area does have to ride
        the power -- but area is a SQUARED length, and this side only has a scale in
        Maya's working units. Multiplying here put a cm scene out by 1e4 (2500x after
        the 2x2 local square), so what travels is the unit-independent quantity:
        radiance, which the far side turns into watts against the lamp's real metres.

        No ``energy`` at all, deliberately -- the two are alternatives, and a record
        carrying both would let a reader pick the wrong one silently.
        """
        transform, shape = self._light("areaLight", intensity=1.0)
        cmds.setAttr(f"{transform}.scaleX", 5.0)
        cmds.setAttr(f"{transform}.scaleY", 4.0)
        # Pinned explicitly: with mtoa loaded a native areaLight ALSO carries
        # aiNormalize (default ON), and that is the other branch -- so leaving it
        # to the plugin's presence would silently test whichever one this process
        # happened to get.
        self._set_attr(shape, "aiNormalize", 0, attr_type="bool")

        record = self._record(transform)
        self.assertEqual(record["type"], "AREA")
        self.assertNotIn("energy", record)
        self.assertAlmostEqual(record["radiance"], 1.0, places=6)
        # The scale is what the far side sizes the lamp from; it must not have been
        # folded into the power on the way out.
        self.assertEqual(record["local_size"], [2.0, 2.0])

    def test_a_production_ceiling_fixture_does_not_bake_at_half_a_gigawatt(self):
        """Regression, pinned to the rig that produced a fully saturated lightmap.

        ROOM_ENV (2026-08-29): four ``areaLight`` fixtures, ``aiNormalize`` off,
        intensity 100, transform scale 179.15872 x 29.967792 in a CENTIMETRE scene --
        a 3.58 m x 0.60 m luminaire. The old path shipped
        ``100 * 1000 W * (179.15872 * 29.967792)`` = 5.37e8 W per fixture; the return
        manifest recorded 536899136.0 and every atlas came back pinned at 65504, the
        half-float ceiling. The lamp's real emitting area is 2.147 m2, so the honest
        figure is ``100 * pi * 2.147`` ~ 674 W -- and that conversion happens where
        the metres are, which is why this side must ship radiance and nothing else.
        """
        transform, shape = self._light("areaLight", intensity=100.0)
        cmds.setAttr(f"{transform}.scaleX", 179.15872)
        cmds.setAttr(f"{transform}.scaleY", 29.967792)
        self._set_attr(shape, "aiNormalize", 0, attr_type="bool")

        record = self._record(transform)
        self.assertAlmostEqual(record["radiance"], 100.0, places=6)
        # The specific number that shipped, named so the regression cannot come back
        # wearing a different constant.
        self.assertNotIn("energy", record)
        for value in record.values():
            if isinstance(value, (int, float)):
                self.assertLess(
                    abs(value),
                    1e6,
                    f"a light record carries {value!r}: no per-light quantity in "
                    "this schema is legitimately that large",
                )

    def test_a_normalized_area_light_keeps_its_energy_under_scale(self):
        """``aiNormalize`` on IS Blender's model -- total power, constant under resize.

        The complement of the tests above, and the reason radiance is conditional
        rather than unconditional: a normalized light's intensity is already a
        total-emission figure, so re-deriving it from an area would over-brighten it
        by exactly the factor the non-normalized case would otherwise be dim by.
        """
        transform, shape = self._light("areaLight", intensity=1.0)
        cmds.setAttr(f"{transform}.scaleX", 5.0)
        cmds.setAttr(f"{transform}.scaleY", 4.0)
        self._set_attr(shape, "aiNormalize", 1, attr_type="bool")

        record = self._record(transform)
        self.assertNotIn("radiance", record)
        self.assertAlmostEqual(
            record["energy"], 1.0 * BlenderBridge.WATTS_PER_INTENSITY, places=3
        )

    def test_a_hidden_light_is_recorded_as_hidden_not_dropped(self):
        """The reader honours the scope it is given; the BAKE owns the bake's rule.

        Visibility is INHERITED, so a light whose own flag is on is still off under a
        hidden group -- the exact shape that cost a production room a full bake. What
        changed 2026-09-17 is WHERE that is acted on: a scene transfer reads these
        same records and must keep the light (the artist may have hidden it only to
        clear the viewport), so the reader tags it and the bake drops it.
        """
        transform, _shape = self._light("pointLight", intensity=100.0)
        group = cmds.group(transform, name="bb_hidden_rig")
        cmds.setAttr(f"{group}.visibility", 0)
        # Re-resolve: grouping REPARENTED the light, so its old path is stale.
        transform = cmds.listRelatives(group, children=True, fullPath=True)[0]

        records = self.bridge._manifest_lights([transform])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["hidden"])
        # ...and the bake still refuses it, with the explanation that a room
        # which baked dark needs.
        self.assertEqual(self.bridge._contributing_lights(records), [])

    def test_an_area_light_carries_its_roll_as_right(self):
        """The FBX strips a light's rotation and ``aim`` fixes only where it
        points, so a rectangle's long side rides ``right`` (its world local X).
        Measured on the production office (2026-10-01): its fixture strips sit
        under a group turned 90 degrees about Y and crossed turned 90 from the
        fixtures they light, so every shadow smeared the wrong way.
        Added: 2026-10-01
        """
        transform, _shape = self._light("areaLight", intensity=10.0)
        cmds.setAttr(f"{transform}.rotateX", -90)  # facing down
        group = cmds.group(transform, name="bb_turned_rig")
        cmds.setAttr(f"{group}.rotateY", 90)
        transform = cmds.listRelatives(group, children=True, fullPath=True)[0]

        record = self.bridge._manifest_lights([transform])[0]

        for got, want in zip(record["aim"], (0.0, -1.0, 0.0)):
            self.assertAlmostEqual(got, want, places=5)
        # Local X, turned 90 about world Y, points along world -Z.
        for got, want in zip(record["right"], (0.0, 0.0, -1.0)):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_zero_intensity_light_is_recorded_as_hidden_not_dropped(self):
        transform, _shape = self._light("pointLight", intensity=0.0)
        records = self.bridge._manifest_lights([transform])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["hidden"])
        self.assertEqual(self.bridge._contributing_lights(records), [])

    def test_a_contributing_light_carries_no_hidden_key_at_all(self):
        """An absent key means "contributes", so a reader that predates the field
        is unaffected and the common case adds nothing to the document."""
        transform, _shape = self._light("pointLight", intensity=3.0)
        record = self.bridge._manifest_lights([transform])[0]
        self.assertNotIn("hidden", record)
        self.assertEqual(self.bridge._contributing_lights([record]), [record])

    def test_a_hidden_light_is_not_counted_in_the_decay_warning(self):
        """Recording a hidden light must not put it in the BAKE's diagnostics.

        The falloff warning is about a bake's fidelity -- Cycles lights are
        always inverse-square -- so a light the bake drops cannot get it wrong.
        It is also deliberately ONE line for the whole send, because Maya's
        default IS decayRate 0 and a per-light warning reads as noise; counting
        lights that never reach the bake puts that noise straight back.
        """
        transform, shape = self._light("pointLight", intensity=100.0)
        cmds.setAttr(f"{shape}.decayRate", 0)  # Maya's default: no decay
        group = cmds.group(transform, name="bb_decay_hidden")
        cmds.setAttr(f"{group}.visibility", 0)
        transform = cmds.listRelatives(group, children=True, fullPath=True)[0]

        with self.assertNoLogs(self.bridge.logger, level="WARNING"):
            records = self.bridge._manifest_lights([transform])
        self.assertTrue(records[0]["hidden"])

        # ...and a VISIBLE one with the same falloff still warns, so the guard
        # above is the hidden-ness and not a broken check.
        lit, lit_shape = self._light("pointLight", intensity=100.0)
        cmds.setAttr(f"{lit_shape}.decayRate", 0)
        with self.assertLogs(self.bridge.logger, level="WARNING") as caught:
            self.bridge._manifest_lights([lit])
        self.assertIn("decay", "\n".join(caught.output))

    def test_a_lit_visible_light_still_crosses(self):
        """The guard must never stand between a working rig and its bake."""
        transform, _shape = self._light("pointLight", intensity=3.0)
        record = self._record(transform)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(
            record["energy"], 3.0 * BlenderBridge.WATTS_PER_INTENSITY, places=3
        )

    def test_maya_shadow_flags_ride_the_record(self):
        """Maya lights default to shadows OFF; a Cycles light always casts unless told."""
        transform, shape = self._light("pointLight", intensity=1.0)
        cmds.setAttr(f"{shape}.useDepthMapShadows", 0)
        cmds.setAttr(f"{shape}.useRayTraceShadows", 0)
        self.assertIs(self._record(transform)["cast_shadow"], False)

        cmds.setAttr(f"{shape}.useRayTraceShadows", 1)
        self.assertIs(self._record(transform)["cast_shadow"], True)


class TestBridgeSceneLighting(MayaTkTestCase):
    """The bake's lights: the Lightmap Baker panels' three switches, same meaning.

    * ``INCLUDE_SCENE_LIGHTS`` (on): every scene light crosses, one outside the
      send on a null of its own at its world matrix -- exporting the light's own
      transform would ship its ancestors' geometry too (probed) -- gone again once
      the payload is written. Off: only the lights inside the send (the
      2026-08-09 behaviour).
    * ``INCLUDE_HIDDEN_LIGHTS`` (off): a hidden light crosses switched on, on a
      null of its own -- its own null arrives hidden, and Blender never evaluates
      a hidden object, so it would be placed at the origin (probed).

    The export is stubbed; the manifest is written for real and read back.
    """

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")
        self.tmp = self.enterContext(
            ptk.TempArtifacts("bb_scene_lights", policy="scoped")
        ).dir_path()

    @staticmethod
    def _light(name, parent=None, at=(0.0, 0.0, 0.0), intensity=2.0):
        transform = cmds.listRelatives(
            cmds.pointLight(name=name, intensity=intensity), parent=True, fullPath=True
        )[0]
        if parent:
            transform = cmds.parent(transform, parent)[0]
        transform = cmds.ls(transform, long=True)[0]
        cmds.xform(transform, worldSpace=True, translation=at)
        return transform

    def _send(self, objects, template=None, **params):
        """``_produce`` with the FBX export stubbed: ``(manifest lights, exported)``.

        *exported* maps each node the export was handed to its world translation
        AT export time (a null made for the send is gone afterwards)."""
        template = template or BlenderBridge._LIGHTMAP_TEMPLATE
        fbx = os.path.join(self.tmp, f"send{len(os.listdir(self.tmp))}.fbx")
        exported = {}

        def export(_self, objs, _request):
            for node in objs:
                exported[cmds.ls(node, long=True)[0]] = [
                    round(v, 4)
                    for v in cmds.xform(node, q=True, worldSpace=True, translation=True)
                ]
            return types.SimpleNamespace(primary=fbx)

        request = types.SimpleNamespace(
            template=template, params=dict(params, LIGHTMAP_DIR=self.tmp)
        )
        with mock.patch.object(
            handoff_export.MayaExportMixin,
            "_produce",
            autospec=True,
            side_effect=export,
        ):
            self.bridge._produce(objects, request)
        manifest = Path(fbx + ".manifest.json")
        lights = (
            json.loads(manifest.read_text(encoding="utf-8")).get("lights") or []
            if manifest.exists()
            else []
        )
        return {record["name"]: record for record in lights}, exported

    def _module(self):
        group = cmds.group(empty=True, name="bbsl_module")
        cmds.parent(cmds.polyCube(name="bbsl_floor")[0], group)
        return cmds.ls(group, long=True)[0]

    def test_the_switches_have_rows_and_defaults(self):
        self.assertIs(BlenderBridge().params_defaults()["INCLUDE_SCENE_LIGHTS"], True)
        self.assertIs(BlenderBridge().params_defaults()["INCLUDE_HIDDEN_LIGHTS"], False)
        bake = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        keys = list(params.PARAMS)
        for key in ("INCLUDE_SCENE_LIGHTS", "INCLUDE_HIDDEN_LIGHTS"):
            self.assertIn(key, bake, f"{key} is not shown with the bake")
        # In the panel's order: Include Scene Lighting, the HDRI, Hidden Lights.
        self.assertEqual(
            keys.index("INCLUDE_ENVIRONMENT") - 1, keys.index("INCLUDE_SCENE_LIGHTS")
        )
        self.assertEqual(
            keys.index("INCLUDE_ENVIRONMENT") + 1, keys.index("INCLUDE_HIDDEN_LIGHTS")
        )

    def test_every_scene_light_crosses_on_a_null_placed_where_it_is(self):
        module = self._module()
        rig = cmds.group(empty=True, name="bbsl_rig")
        cmds.setAttr(f"{rig}.rotateY", 30)
        outside = self._light("bbsl_out", parent=rig, at=(120.0, 50.0, -30.0))
        before = set(cmds.ls(assemblies=True, long=True))

        lights, exported = self._send([module])

        self.assertIn("bbsl_out", lights, "the light outside the send crossed")
        self.assertNotIn("hidden", lights["bbsl_out"])
        self.assertIn("|bbsl_out", exported, "its null rode the export")
        self.assertEqual(exported["|bbsl_out"], [120.0, 50.0, -30.0])
        self.assertNotIn(outside, exported, "never the light's own branch")
        self.assertNotIn(cmds.ls(rig, long=True)[0], exported)
        self.assertEqual(
            set(cmds.ls(assemblies=True, long=True)), before, "the null is gone"
        )

    def test_with_the_master_off_only_the_sends_lights_cross(self):
        module = self._module()
        self._light("bbsl_in", parent=module, at=(0.0, 100.0, 0.0))
        self._light("bbsl_away", at=(500.0, 100.0, 0.0))
        lights, exported = self._send([module], INCLUDE_SCENE_LIGHTS=False)
        self.assertEqual(set(lights), {"bbsl_in"})
        self.assertEqual(list(exported), [module])

    def test_a_hidden_light_crosses_switched_on_only_when_asked(self):
        module = self._module()
        hidden = self._light("bbsl_hid", parent=module, at=(10.0, 200.0, 5.0))
        cmds.setAttr(f"{hidden}.visibility", False)
        self._light("bbsl_zero", parent=module, intensity=0.0)

        lights, _exported = self._send([module])
        self.assertEqual(lights, {}, "hidden and zero lights stay out by default")

        lights, exported = self._send([module], INCLUDE_HIDDEN_LIGHTS=True)
        # Its own null arrives hidden (Blender would place it at the origin), so
        # it rides one of its own, named apart from it.
        name = "bbsl_hid" + BlenderBridge._LIGHT_PROXY_SUFFIX
        self.assertEqual(set(lights), {name}, "the zero-intensity one lights nothing")
        self.assertNotIn("hidden", lights[name], "it crosses switched ON")
        self.assertEqual(exported[f"|{name}"], [10.0, 200.0, 5.0])
        self.assertFalse(cmds.getAttr(f"{hidden}.visibility"), "Maya is untouched")
        self.assertFalse(cmds.objExists(f"|{name}"))

    def test_a_plain_send_carries_the_send_alone(self):
        """The switches are the bake's: a send to Blender is authored content."""
        module = self._module()
        self._light("bbsl_elsewhere", at=(0.0, 300.0, 0.0))
        lights, exported = self._send([module], template="import")
        self.assertEqual(lights, {})
        self.assertEqual(list(exported), [module])

    def test_the_blender_baker_renders_what_crossed(self):
        """The child's baker is pinned to every light, the world and no hidden one:
        the Maya side chose what crossed, and a saved preset of the same name must
        not switch off a lamp on a null of its own (it sits under no baked mesh)
        nor detach the world the template built."""
        import ast
        import sys

        source = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        node = next(
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name == "make_baker"
        )
        code = compile(ast.Module([node], type_ignores=[]), "template", "exec")
        asked = {}

        class _Baker:
            resolution = samples = bounces = 1
            denoise = adaptive = beside_textures = False
            device = "CPU"

            @classmethod
            def from_preset(cls, name, **kwargs):
                asked.update(kwargs)
                return cls()

        module = types.ModuleType("lightmap_baker")
        module.LightmapBaker = _Baker
        namespace = {
            "LIGHTMAP_RESOLUTION": 0,
            "LIGHTMAP_SAMPLES": 0,
            "LIGHTMAP_BOUNCES": -1,
            "LIGHTMAP_QUALITY": "mobile",
            "LIGHTMAP_DENOISE": True,
            "LIGHTMAP_ADAPTIVE": True,
            "LIGHTMAP_BESIDE_TEXTURES": False,
            "LIGHTMAP_REFLECTION_PROBE": False,
            "LIGHTMAP_DEVICE": "AUTO",
        }
        exec(code, namespace)
        with mock.patch.dict(
            sys.modules,
            {"blendertk.light_utils.lightmap_baker.lightmap_baker": module},
        ):
            namespace["make_baker"]()
        self.assertEqual(
            (
                asked.get("include_scene_lights"),
                asked.get("include_environment"),
                asked.get("include_hidden_lights"),
            ),
            (True, True, False),
        )
        # The row's own value reaches the baker's switch (Added: 2026-10-17).
        self.assertIs(asked.get("reflection_probe"), False)

    def test_the_nulls_never_reach_the_undo_queue(self):
        module = self._module()
        self._light("bbsl_undo", at=(0.0, 300.0, 0.0))
        cmds.flushUndo()
        cmds.undoInfo(state=True)
        cmds.polyCube(name="bbsl_last_edit")
        self._send([module])
        self.assertTrue(cmds.objExists("bbsl_last_edit"))
        cmds.undo()
        self.assertFalse(
            cmds.objExists("bbsl_last_edit"), "one undo takes the artist's own edit"
        )


class TestBridgeSkyDomeWorld(MayaTkTestCase):
    """``_manifest_world`` -- the scene's sky dome, sent as the bake's world.

    An Arnold scene lit by a dome (the common IBL setup) baked black unless
    Environment HDRI was set by hand: a dome is not a light OBJECT in Blender, it
    is the world, and nothing carried it.
    """

    _set_attr = staticmethod(TestBridgeLightManifest._set_attr)
    _light = staticmethod(TestBridgeLightManifest._light)

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    def _dome(self, **attrs):
        """A sky dome with *attrs* on its shape -- mtoa's node, so skipped without it."""
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except RuntimeError as error:
            self.skipTest(f"mtoa is not loadable here: {error}")
        return self._light("aiSkyDomeLight", **attrs)

    def _image(self):
        """A file that exists, for a dome to read (the send checks it is there)."""
        fd, path = tempfile.mkstemp(suffix=".exr", prefix="bb_sky_")
        os.close(fd)
        self.addCleanup(Path(path).unlink, missing_ok=True)
        return path

    def _textured(self, shape, path):
        texture = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{texture}.fileTextureName", path, type="string")
        cmds.connectAttr(f"{texture}.outColor", f"{shape}.color", force=True)

    def test_a_textured_dome_travels_as_the_world(self):
        """The image, the level Arnold renders it at, and where its centre faces.

        Strength is ``intensity * 2**aiExposure``: a world is radiance on both
        sides, so it maps 1:1 with no wattage anchor. Measured with kick against
        a u-ramp: a latlong dome's image centre (u = 0.5) faces its local +Z, so a
        30 degree turn faces it at (sin 30, 0, cos 30) -- sent in Maya axes, like
        a light's aim, for the side that knows Blender's panorama to convert.
        """
        transform, shape = self._dome(intensity=2.0)
        self._set_attr(shape, "aiExposure", 1.0)
        image = self._image()
        self._textured(shape, image)
        cmds.setAttr(f"{transform}.rotateY", 30.0)

        world = self.bridge._manifest_world()
        self.assertEqual(world["name"], transform.rsplit("|", 1)[-1])
        self.assertEqual(os.path.normcase(world["hdri"]), os.path.normcase(image))
        self.assertNotIn("color", world)
        self.assertAlmostEqual(world["strength"], 4.0, places=6)
        turn = math.radians(30.0)
        self.assertEqual(
            [round(v, 6) for v in world["center"]],
            [round(math.sin(turn), 6), 0.0, round(math.cos(turn), 6)],
        )
        self.assertEqual(world["axis_up"], "Y")

    def test_an_untextured_dome_travels_as_its_colour(self):
        _transform, shape = self._dome(intensity=0.5)
        cmds.setAttr(f"{shape}.color", 0.2, 0.3, 0.4, type="double3")
        world = self.bridge._manifest_world()
        self.assertNotIn("hdri", world)
        self.assertEqual([round(c, 6) for c in world["color"]], [0.2, 0.3, 0.4])
        self.assertAlmostEqual(world["strength"], 0.5, places=6)

    def test_a_dome_the_bake_cannot_reproduce_is_named_and_not_sent(self):
        """Switched off, not a latlong, or an image that is not there.

        A mirrored-ball or angular panorama has no equirect reading, and a missing
        image would raise on the far side minutes into the bake -- so each is
        said here, before the send, where the artist can act on it.
        """
        transform, shape = self._dome()
        cmds.setAttr(f"{transform}.visibility", 0)
        with self.assertLogs(self.bridge.logger, level="WARNING"):
            self.assertIsNone(self.bridge._manifest_world())
        cmds.setAttr(f"{transform}.visibility", 1)
        cmds.setAttr(f"{shape}.format", 0)  # mirrored_ball
        with self.assertLogs(self.bridge.logger, level="WARNING"):
            self.assertIsNone(self.bridge._manifest_world())
        cmds.setAttr(f"{shape}.format", 2)  # latlong
        self._textured(shape, "X:/nowhere/sky.exr")
        with self.assertLogs(self.bridge.logger, level="WARNING"):
            self.assertIsNone(self.bridge._manifest_world())

    def test_a_dome_tilted_off_its_up_axis_is_warned(self):
        """Only a turn about the up axis reaches the bake: the record says where
        the image's centre faces. A roll about that direction leaves it level --
        at rotateZ 30 the centre still faces (0, 0, 1) -- while Arnold renders
        the horizon tilted, so the tilt is read off the dome's UP axis: a roll,
        a pitch or an upside-down dome is warned, a turn is not (2026-09-15)."""
        transform, _shape = self._dome(intensity=1.0)
        for attr, angle in (("rotateZ", 30.0), ("rotateX", 30.0), ("rotateX", 180.0)):
            cmds.xform(transform, rotation=(0.0, 0.0, 0.0))
            cmds.setAttr(f"{transform}.{attr}", angle)
            with self.assertLogs(self.bridge.logger, level="WARNING") as caught:
                self.assertIsNotNone(self.bridge._manifest_world())
            self.assertIn("tilted", "\n".join(caught.output), (attr, angle))
        cmds.xform(transform, rotation=(0.0, 75.0, 0.0))
        with self.assertNoLogs(self.bridge.logger, level="WARNING"):
            self.assertIsNotNone(self.bridge._manifest_world())

    def test_the_manifest_carries_the_world_only_when_asked(self):
        """Only the bake's template reads a world, and its Include Environment
        row leaves one out -- the Arnold baker's own toggle, same meaning."""
        cube = cmds.polyCube(name="bb_world_cube")[0]
        fd, fbx = tempfile.mkstemp(suffix=".fbx", prefix="bb_world_")
        os.close(fd)
        manifest = Path(fbx + ".manifest.json")
        self.addCleanup(Path(fbx).unlink, missing_ok=True)
        self.addCleanup(manifest.unlink, missing_ok=True)

        self.bridge._write_manifest([cube], fbx, include_environment=True)
        self.assertFalse(manifest.exists(), "no dome, nothing to say")
        self._dome(intensity=1.0)
        self.bridge._write_manifest([cube], fbx, include_environment=True)
        world = json.loads(manifest.read_text(encoding="utf-8"))["world"]
        self.assertAlmostEqual(world["strength"], 1.0, places=6)
        manifest.unlink()
        self.bridge._write_manifest([cube], fbx)
        self.assertFalse(manifest.exists(), "a send without the world has none")

    def test_only_the_bake_asks_for_the_world(self):
        """A plain send lights no world, so it reads no dome (and warns about
        none); the bake does, unless its Include Environment row is off."""
        cube = cmds.polyCube(name="bb_world_send")[0]
        asked = []

        def include_environment(template, **params):
            request = types.SimpleNamespace(template=template, params=dict(params))
            with (
                mock.patch.object(
                    BlenderBridge,
                    "_write_manifest",
                    side_effect=lambda *_args, **kwargs: asked.append(kwargs),
                ),
                mock.patch.object(
                    handoff_export.MayaExportMixin,
                    "_produce",
                    return_value=types.SimpleNamespace(primary="payload.fbx"),
                ),
            ):
                self.bridge._produce([cube], request)
            return asked[-1]["include_environment"]

        bake = BlenderBridge._LIGHTMAP_TEMPLATE
        self.assertIs(include_environment("send"), False)
        self.assertIs(include_environment(bake), True)
        self.assertIs(include_environment(bake, INCLUDE_ENVIRONMENT=False), False)

    def test_an_emissive_map_lights_the_bake_at_arnolds_weight(self):
        """``ArnoldBridge`` wires an emissive map at emission 1, so Maya's own
        Lightmap Baker lights the room with the map as authored; the bridge's
        Cycles bake at 2.0 lit it with twice that.
        Added: 2026-10-01
        """
        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        self.assertEqual(DEFAULTS["EMISSION_STRENGTH"], 1.0)

    def test_the_bake_template_lights_its_world_through_the_applier(self):
        """The precedence -- explicit HDRI, the dome, ambient -- lives in ONE place,
        blendertk's ``MayaSceneImport.apply_world`` (tested there in Blender), so
        the template calls it rather than set the world itself. mayatk publishes
        before blendertk and the template runs against whichever blendertk the
        Blender side has, so one without the applier keeps the world the
        template set before the dome travelled -- the explicit HDRI, else flat
        ambient -- in the same report. The template only ever runs inside
        Blender, so ``light_scene`` runs here on stand-ins, both ways."""
        import ast
        import sys

        source = (_TEMPLATE_DIR / f"{BlenderBridge._LIGHTMAP_TEMPLATE}.py").read_text(
            encoding="utf-8"
        )
        light_scene = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "light_scene"
        )
        code = compile(ast.Module([light_scene], type_ignores=[]), "template", "exec")
        worlds = []

        def set_world_environment(hdri=None, strength=1.0, color=None):
            worlds.append((hdri, strength))
            return f"{hdri} @ {strength}"

        light_utils = types.ModuleType("light_utils")
        light_utils.LightUtils = types.SimpleNamespace(
            set_world_environment=set_world_environment,
            set_emission_strength=lambda strength: [],
        )

        def run(scene_import, hdri):
            namespace = {
                "os": os,
                "bpy": types.SimpleNamespace(data=types.SimpleNamespace(objects=[])),
                "FBX_PATH": "C:/bake/scene.fbx",
                "ENVIRONMENT_HDR": hdri,
                "WORLD_STRENGTH": 2.0,
                "EMISSION_STRENGTH": 1.0,
                "SCENE_LIGHT_STRENGTH": 1.0,
                "__INCLUDE_LIGHTS__": False,
                "INCLUDE_SCENE_LIGHTS": True,
                "rebuild_scene_lights": lambda: None,
                "emissive_material_count": lambda: 0,
            }
            exec(code, namespace)
            importer = types.ModuleType("scene_import")
            importer.MayaSceneImport = scene_import
            modules = {
                "blendertk.env_utils.maya_bridge._scene_import": importer,
                "blendertk.light_utils._light_utils": light_utils,
            }
            with mock.patch.dict(sys.modules, modules):
                return namespace["light_scene"]()

        class Applier:
            @staticmethod
            def apply_world(manifest_path, hdri="", strength=1.0):
                worlds.append((manifest_path, hdri, strength))
                return {"description": "dome", "hdri": "", "sky_dome": "Sky (sky.exr)"}

        report = run(Applier, "")
        self.assertEqual(worlds.pop(), ("C:/bake/scene.fbx.manifest.json", "", 2.0))
        self.assertEqual((report["hdri"], report["sky_dome"]), ("", "Sky (sky.exr)"))
        self.assertFalse(report["warnings"], "the dome lights the bake")

        class NoApplier:  # a blendertk from before the dome travelled
            pass

        report = run(NoApplier, "C:/maps/studio.hdr")
        self.assertEqual(worlds.pop(), ("C:/maps/studio.hdr", 2.0))
        self.assertEqual((report["hdri"], report["sky_dome"]), ("studio.hdr", ""))
        report = run(NoApplier, "")
        # Nothing lights the world, so it goes black -- as Arnold renders it --
        # rather than keeping the flat ambient Maya's own baker never sees.
        self.assertEqual(worlds[-2:], [(None, 2.0), (None, 0.0)])
        self.assertEqual((report["hdri"], report["sky_dome"]), ("", ""))
        self.assertIn("BLACK", " ".join(report["warnings"]))


class TestBridgeBakeableScope(MayaTkTestCase):
    """``_bakeable`` -- what the LIGHTMAP leg actually sends.

    A hidden mesh ships no geometry: probed Maya 2025 -> FBX -> glTF, a mesh
    hidden by its own flag or by an ancestor arrives in the GLB as a bare node
    with the mesh dropped. Baking one spends an export, a Blender import, a
    Cycles bake and a share of the atlas producing a map with nothing left to
    bind it to.
    """

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    @staticmethod
    def _cube(name, visible=True):
        transform = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
        cmds.setAttr(f"{transform}.visibility", visible)
        return transform

    def _names(self, kept):
        return sorted(str(o).rsplit("|", 1)[-1] for o in kept)

    def test_a_hidden_mesh_is_not_sent_to_the_bake(self):
        visible = self._cube("vis_cube")
        hidden = self._cube("hid_cube", visible=False)

        self.assertEqual(
            self._names(self.bridge._bakeable([visible, hidden])), ["vis_cube"]
        )

    def test_visibility_is_inherited_so_a_hidden_group_takes_its_children(self):
        """The production case: the room's spare geometry sits under a hidden group,
        each child's own visibility flag still reading True."""
        child = self._cube("inh_cube")
        group = cmds.ls(cmds.group(child, name="hidden_grp"), long=True)[0]
        cmds.setAttr(f"{group}.visibility", False)
        child = cmds.listRelatives(group, children=True, fullPath=True)[0]
        self.assertTrue(cmds.getAttr(f"{child}.visibility"))

        self.assertEqual(self.bridge._bakeable([child]), [])

    def test_a_mesh_hidden_only_by_its_display_layer_is_still_baked(self):
        """Maya's FBX exporter writes a layer-hidden mesh with full vertex data
        and no trace of the layer (measured), so it ships -- and has to come
        back lit. Arnold renders none of it, which is Maya's own baker's reason
        to leave it out (``DisplayUtils.is_visible``), not this gate's."""
        cube = self._cube("layer_cube")
        layer = cmds.createDisplayLayer([cube], name="bb_hidden_lyr", noRecurse=True)
        cmds.setAttr(f"{layer}.visibility", False)

        self.assertEqual(self._names(self.bridge._bakeable([cube])), ["layer_cube"])

    def test_a_non_mesh_passes_through_however_it_is_hidden(self):
        """Lights are gated separately and for a different reason (their own
        contribution), and a locator or empty group is not this gate's business."""
        light = cmds.ls(cmds.shadingNode("pointLight", asLight=True), long=True)[0]
        cmds.setAttr(f"{light}.visibility", False)
        locator = cmds.ls(cmds.spaceLocator(name="loc")[0], long=True)[0]
        cmds.setAttr(f"{locator}.visibility", False)

        self.assertEqual(
            self._names(self.bridge._bakeable([light, locator])),
            ["loc", light.rsplit("|", 1)[-1]],
        )

    def test_only_the_lightmap_leg_drops_hidden_geometry(self):
        """A plain send-to-Blender carries hidden meshes on purpose -- the artist
        may be going there to work on exactly that object."""
        hidden = self._cube("hid_cube", visible=False)
        sent = []

        request = types.SimpleNamespace(template="send", params={})
        with (
            mock.patch.object(BlenderBridge, "_write_manifest", return_value=None),
            mock.patch.object(
                handoff_export.MayaExportMixin,
                "_produce",
                side_effect=lambda objects, req: sent.append(list(objects)),
            ),
        ):
            self.bridge._produce([hidden], request)

        self.assertEqual(self._names(sent[0]), ["hid_cube"])


class TestBridgeAmbiguityReport(MayaTkTestCase):
    """The ambiguity warning names the colliding Maya nodes and the remedy."""

    def test_warning_names_the_colliding_dag_paths(self):
        """Blender's ``wheel`` / ``wheel.001`` alone cannot tell the artist what to rename.

        Measured on a production room: a duplicated cabinet group left two transforms
        both named ``PROPS_083``; 2 of 50 objects came back unlit behind a warning
        that named only the Blender-side names.
        """
        a = cmds.group(cmds.polyCube(name="wheel")[0], name="a")
        b = cmds.group(cmds.polyCube(name="wheel")[0], name="b")
        pool = cmds.listRelatives(a, children=True, fullPath=True) + cmds.listRelatives(
            b, children=True, fullPath=True
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "amb.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": 2,
                        "meshes": {},
                        "objects": {
                            "wheel": {"map": "missing.exr", "mesh": "m"},
                            "wheel.001": {"map": "missing.exr", "mesh": "m"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            bridge = (
                BlenderBridge()
            )  # its logger does not propagate: capture it directly
            with self.assertLogs(bridge.logger, level="WARNING") as captured:
                self.assertEqual(bridge.reassemble_lightmaps(manifest, pool), {})
        text = "\n".join(captured.output)
        self.assertIn("2 baked object(s) ambiguous", text)
        for path in pool:
            self.assertIn(path, text)
        self.assertIn("unique name", text)


class TestBridgeLightingReport(unittest.TestCase):
    """The lighting summary names every source that lit the bake, emissives included."""

    def test_emissive_materials_count_as_a_light_source(self):
        """A fixture-lit room sends no light object and no HDRI, yet bakes lit.

        Measured on a production office: four emissive fixture materials, no lights
        sent, WORLD_STRENGTH 0 -- lit maps, and a summary that said NONE.
        """
        bridge = BlenderBridge()
        with self.assertLogs(bridge.logger, level="INFO") as captured:
            bridge._report_bake_lighting({"emissive_materials": 4, "warnings": []})
        text = " ".join(captured.output)
        self.assertIn("4 emissive material(s)", text)
        self.assertNotIn("NONE", text)

    def test_nothing_lit_is_still_reported_as_none(self):
        bridge = BlenderBridge()
        with self.assertLogs(bridge.logger, level="INFO") as captured:
            bridge._report_bake_lighting(
                {"emissive_materials": 0, "imported_lights": 0}
            )
        self.assertIn("NONE", " ".join(captured.output))

    def test_a_sky_dome_that_lit_the_world_is_named(self):
        bridge = BlenderBridge()
        with self.assertLogs(bridge.logger, level="INFO") as captured:
            bridge._report_bake_lighting(
                {"sky_dome": "skydome (sky.exr)", "imported_lights": 0}
            )
        text = " ".join(captured.output)
        self.assertIn("sky dome skydome (sky.exr)", text)
        self.assertNotIn("NONE", text)


class TestPullTemplateCopiesMatchTheirSource(MayaTkTestCase):
    """blendertk's Maya-side conversion templates carry dependency-free COPIES of
    readers that live HERE, and this is the test that keeps them honest.

    The copies are structural, not laziness: those templates run in whatever mayapy
    the pull happens to find, which may have no mayatk (their headers state the
    contract, blendertk declares no mayatk dependency, and the one reader that does
    delegate -- lights -- is wrapped in a try/except that degrades to "not carried").
    CODE_STANDARD 6 sanctions exactly one kind of duplicate: a drift-GUARDED one.
    Until this test the docstrings instead said "kept in step by hand", and both
    copies had drifted -- one of them wrongly, on exactly the referenced-and-rigged
    scenes the bridge exists for.

    This lives in mayatk's suite because only here are BOTH halves present: real
    ``maya.cmds`` to execute them, and the sibling blendertk checkout to read.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = (
            _TEMPLATE_DIR.parents[4]
            / "blendertk"
            / "blendertk"
            / "env_utils"
            / "maya_bridge"
            / "templates"
        )

    def setUp(self):
        super().setUp()
        self._time_unit = cmds.currentUnit(query=True, time=True)

    def tearDown(self):
        cmds.currentUnit(time=self._time_unit)
        super().tearDown()  # restores the session-global FBX export hook

    def _template_function(self, filename, name):
        """The named top-level function of a blendertk template, compiled on its own.

        Text-level, because the module must NOT be imported: that is the contract
        under test, and a template is not even importable until it is rendered
        (``INCLUDE_ANIMATION = __INCLUDE_ANIMATION__`` is a bare name, not a value).

        The function's module-level dependencies come along -- the plain imports and
        every LITERAL top-level assignment (``_UNKEYED_DRIVERS`` and the like), which
        is what these functions actually close over. Unrendered placeholders are not
        literals, so they are skipped rather than crashing the prelude.
        """
        import ast

        path = self.templates / filename
        if not path.is_file():
            self.skipTest(f"sibling blendertk checkout missing: {path}")
        module = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(
            (
                n
                for n in module.body
                if isinstance(n, ast.FunctionDef) and n.name == name
            ),
            None,
        )
        self.assertIsNotNone(fn, f"{filename} lost {name}()")
        prelude = []
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                prelude.append(node)
            elif isinstance(node, ast.Assign):
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    continue  # a rendering placeholder, not a constant
                prelude.append(node)
        namespace = {}
        exec(
            compile(
                ast.Module(body=prelude + [fn], type_ignores=[]), str(path), "exec"
            ),
            namespace,
        )
        return namespace[name]

    def test_the_send_templates_heuristic_matches_the_engine_on_unmarked_empties(self):
        """blendertk's SEND templates (they run in Maya too) repair Empties through
        :meth:`BlenderSceneImport._restore_empty_groups` and fall back to the
        children heuristic when mayatk is missing. For an Empty no manifest marks,
        the fallback must decide exactly what the engine decides: a parent Empty
        loses its locator shape, a childless one keeps it, and a transform with a
        second shape is not an Empty at all. The two templates' loops are pinned
        token-identical by ``test_scene_import``; this runs one of them."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        fallback = self._template_function("_save_scene.py", "restore_empty_groups")

        def scene():
            cmds.file(new=True, force=True)
            nodes = []
            for name, parent in (
                ("grp", None),
                ("leaf", "grp"),
                ("point", None),
                ("mixed", None),
            ):
                transform = cmds.createNode("transform", name=name, parent=parent)
                cmds.createNode("locator", name=name + "Shape", parent=transform)
                nodes.append(transform)
            mesh = cmds.polyCube(constructionHistory=False)[0]
            cmds.parent(
                cmds.listRelatives(mesh, shapes=True)[0],
                "mixed",
                shape=True,
                relative=True,
            )
            cmds.delete(mesh)
            return cmds.ls(nodes, long=True, dag=True)

        def with_locators():
            return sorted(
                cmds.listRelatives(shape, parent=True, fullPath=True)[0]
                for shape in cmds.ls(type="locator", long=True)
            )

        fallback(cmds, None, scene())  # engine=None: the dependency-free branch
        by_template = with_locators()
        BlenderSceneImport._restore_empty_groups(scene())  # no manifest: heuristic
        by_engine = with_locators()
        self.assertEqual(by_template, by_engine)
        self.assertEqual(by_engine, ["|grp|leaf", "|mixed", "|point"])

    def test_scene_node_types_copy_matches_the_bridge_collector(self):
        """Four nodes, two of which the copy used to get wrong.

        A namespaced group and locator (a referenced scene is the production norm):
        the copy stripped the namespace to ``grp``, while Maya's exporter writes
        ``ref:grp`` literally and Blender keeps the colon -- so the Blender side
        looked up a key that was never there and every locator arrived a plain
        Empty. And a shapeless JOINT: ``ls(type="transform")`` returns joints too
        (derived type), so a rig's joints were all tagged ``group``.
        """
        cmds.namespace(add="ref")
        cmds.group(empty=True, name="ref:grp")
        cmds.select(clear=True)
        cmds.spaceLocator(name="ref:loc")
        cmds.select(clear=True)
        cmds.joint(name="ref:jnt")
        cmds.select(clear=True)
        cmds.polyCube(name="ref:cube")

        copy = self._template_function("_import_scene.py", "scene_node_types")
        got = copy(cmds)
        want = BlenderBridge._manifest_node_types(
            cmds.ls(type="transform", long=True) or []
        )
        self.assertEqual(got, want, "the template copy has drifted from the bridge")
        # Pin the behaviour itself, so a matching drift on BOTH sides still fails.
        self.assertEqual(got.get("ref:grp"), "group")
        self.assertEqual(got.get("ref:loc"), "locator")
        self.assertNotIn("ref:jnt", got)  # a joint is not a group
        self.assertNotIn("grp", got)  # the namespace survives
        self.assertNotIn("ref:cube", got)  # a mesh describes itself in the FBX

    def test_scene_settings_copy_matches_env_utils(self):
        """Both templates' clock reader, against the in-process one, on a scene whose
        every field differs from the default (a hardcoded fps or a swapped
        playback/animation range cannot pass by coincidence)."""
        from mayatk.env_utils._env_utils import EnvUtils

        cmds.currentUnit(time="ntsc")  # 30 fps, not the 24 default
        cmds.playbackOptions(
            animationStartTime=-5, animationEndTime=77, minTime=0, maxTime=48
        )
        cmds.currentTime(12)

        want = EnvUtils.scene_settings()
        self.assertEqual(want["fps"], 30.0)  # the reader itself is right
        for template in ("_import_scene.py", "_import_scene_usd.py"):
            with self.subTest(template=template):
                copy = self._template_function(template, "scene_settings")
                self.assertEqual(copy(cmds), want)

    def test_usd_export_flags_are_the_shared_interchange_set(self):
        """The USD template's ``mayaUSDExport`` kwargs ARE
        ``UsdUtils.INTERCHANGE_EXPORT_OPTIONS`` -- the hand-off set mayatk adopted
        FROM this route, reason by measured reason -- plus the destination.

        Compared as DATA (the literals, via ast), so the flags can be guarded
        without importing a template that must not be importable.
        """
        import ast

        from mayatk.env_utils.usd import UsdUtils

        path = self.templates / "_import_scene_usd.py"
        if not path.is_file():
            self.skipTest(f"sibling blendertk checkout missing: {path}")
        source = path.read_text(encoding="utf-8")
        function = next(
            (
                n
                for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == "export_usd"
            ),
            None,
        )
        self.assertIsNotNone(function, "USD template lost export_usd()")
        table = next(n for n in ast.walk(function) if isinstance(n, ast.Dict))
        flags = {}
        for key_node, value_node in zip(table.keys, table.values):
            key = ast.literal_eval(key_node)
            if key == "file":  # the destination, not a shared flag
                continue
            flags[key] = ast.literal_eval(value_node)
        self.assertEqual(flags, dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS))

    def test_animation_frame_range_copy_matches_sampling_frame_range(self):
        """The export-cost decision, across all three of its outcomes.

        Getting this wrong is expensive rather than wrong-looking (a full-range
        sample was measured at 234s vs 1.8s static on a 755-mesh module), so the
        copy is pinned against the engine on a static scene, a keyed one, and one
        with an unkeyed driver.
        """
        from mayatk.env_utils.usd import UsdUtils

        copy = self._template_function("_import_scene_usd.py", "_animation_frame_range")

        def _same(label):
            mine = copy(cmds)
            theirs = UsdUtils.sampling_frame_range()
            # Normalized: both are unpacked the same way by their callers, so a
            # list-vs-tuple difference is not the drift worth failing on.
            self.assertEqual(
                tuple(mine) if mine else mine,
                tuple(theirs) if theirs else theirs,
                f"frame-range copy drifted ({label})",
            )
            return tuple(mine) if mine else mine

        cmds.playbackOptions(
            animationStartTime=1, animationEndTime=50, minTime=1, maxTime=50
        )
        self.assertIsNone(_same("static scene"))

        cube = cmds.polyCube()[0]
        cmds.setKeyframe(cube, attribute="translateX", time=5, value=0.0)
        cmds.setKeyframe(cube, attribute="translateX", time=20, value=5.0)
        self.assertEqual(_same("plain keys"), (5.0, 20.0))  # the keys' own span

        follower = cmds.polyCube()[0]
        cmds.parentConstraint(cube, follower)
        self.assertEqual(_same("unkeyed driver"), (1.0, 50.0))  # the full range

    # ---- the USD export: ONE implementation, mayatk's -------------------------
    # The split export (blendshape gate, visibility resampled, static subtrees
    # left out of the sampled pass) lives in ``UsdUtils._maya_usd_export``, pinned
    # against mayaUsd by ``test_usd.TestSampledExport``. The template only hands
    # it the flag table -- and runs one plain pass where mayatk is missing.

    def _export_usd(self):
        export = self._template_function("_import_scene_usd.py", "export_usd")
        export.__globals__["OUT_USD"] = "X:/pull/out.usd"
        return export

    def test_usd_template_exports_through_usdutils(self):
        """The flag table plus the frame range, the whole scene, shading-group
        material names (this route's spelling), and ``prune_static``: its scene is
        a throwaway conversion copy."""
        import logging

        from mayatk.env_utils.usd import UsdUtils

        log = logging.getLogger("mayatk.env_utils.usd")
        level, handlers = log.level, list(log.handlers)
        self.addCleanup(setattr, log, "handlers", handlers)
        self.addCleanup(log.setLevel, level)
        fake = mock.MagicMock()
        with mock.patch.object(UsdUtils, "export") as spy:
            self._export_usd()(fake, (1.0, 30.0))
        spy.assert_called_once_with(
            "X:/pull/out.usd",
            options=dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, frameRange=(1.0, 30.0)),
            selection_only=False,
            material_names="shading_group",
            prune_static=True,
        )
        fake.mayaUSDExport.assert_not_called()

    def test_usd_template_runs_one_plain_pass_without_mayatk(self):
        """A mayapy without mayatk still converts: one ``mayaUSDExport``."""
        from mayatk.env_utils.usd import UsdUtils

        fake = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"mayatk.env_utils.usd": None}):
            self._export_usd()(fake, (1.0, 30.0))
        fake.mayaUSDExport.assert_called_once_with(
            file="X:/pull/out.usd",
            frameRange=(1.0, 30.0),
            **UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
        )


class TestBridgeMirrorsTheBlenderBaker(unittest.TestCase):
    """The bake recipe's rows are blendertk ``LightmapBaker``'s settings, read off it.

    Read from the sibling repo's SOURCE (the baker's preset keys) and shipped
    preset files, not imported: the mayatk suite does not put blendertk on its
    path, and a drift guard that only runs where nobody runs it guards nothing.
    Skipped outside the monorepo.
    """

    _BTK = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "blendertk",
        "blendertk",
        "light_utils",
        "lightmap_baker",
    )
    #: The baker's preset keys -> the bridge row carrying each. Include
    #: Environment keeps its shared row name: it is the crossing's too (the
    #: sky dome it lights the bake's world from).
    _ROWS = {
        "resolution": "LIGHTMAP_RESOLUTION",
        "samples": "LIGHTMAP_SAMPLES",
        "bounces": "LIGHTMAP_BOUNCES",
        "adaptive": "LIGHTMAP_ADAPTIVE",
        "include_scene_lights": "INCLUDE_SCENE_LIGHTS",
        "include_environment": "INCLUDE_ENVIRONMENT",
        "include_hidden_lights": "INCLUDE_HIDDEN_LIGHTS",
        "denoise": "LIGHTMAP_DENOISE",
        "beside_textures": "LIGHTMAP_BESIDE_TEXTURES",
        "reflection_probe": "LIGHTMAP_REFLECTION_PROBE",
    }

    def setUp(self):
        if not os.path.isdir(self._BTK):
            self.skipTest("blendertk is not beside mayatk (not the monorepo)")

    def _preset_keys(self):
        import ast

        source = open(
            os.path.join(self._BTK, "lightmap_baker.py"), encoding="utf-8"
        ).read()
        keys = {}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in ("PRESET_INT_KEYS", "PRESET_BOOL_KEYS"):
                    keys[node.target.id] = ast.literal_eval(node.value)
        self.assertEqual(len(keys), 2, "the baker's preset keys moved")
        return set(keys["PRESET_INT_KEYS"]) | set(keys["PRESET_BOOL_KEYS"])

    def test_every_dial_and_switch_of_the_baker_has_a_row_the_template_reads(self):
        self.assertEqual(set(self._ROWS), self._preset_keys())
        referenced = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        for key in self._ROWS.values():
            self.assertIn(key, params.PARAMS, f"{key} has no row")
            self.assertIn(key, referenced, f"{key} is not used by the bake template")

    def test_the_quality_rows_describe_the_shipped_tiers(self):
        """Each tier's row states the dials that tier's file ships -- the words the
        artist picks a tier by, and the ones that went stale when tiers were tuned."""
        for _label, tier, tip in params.PARAMS["LIGHTMAP_QUALITY"].choices:
            with open(
                os.path.join(self._BTK, "presets", f"{tier}.json"), encoding="utf-8"
            ) as fh:
                data = json.load(fh)
            self.assertIn(
                f"{data['resolution']} px / {data['samples']} samples / "
                f"{data['bounces']} bounces",
                tip,
                tier,
            )


class TestBridgeReflectionProbe(MayaTkTestCase):
    """A Blender bake's reflection probe comes home recorded in Maya's own space.

    blendertk's bake captures the room it lit (``LightmapBaker.bake_probe``) and
    the template ships its point and box in the Blender probe record's frame,
    with ANCHORS -- vertices of baked meshes in that frame. The return leg reads
    the same vertices here and measures the crossing (no axis or unit convention
    written down), then records the probe as Maya's own bake does. Added:
    2026-10-17
    """

    #: The crossing the fixture simulates: the Blender record is in metres, Maya
    #: in centimetres -- what the template's FBX import makes of a Maya scene.
    _SCALE = 0.01

    def setUp(self):
        super().setUp()
        self.tmp = self.enterContext(
            ptk.TempArtifacts("bridge_probe", policy="scoped")
        ).dir_path()
        self.cube = cmds.ls(
            cmds.polyCube(name="bbProbeRoom", width=600, height=300, depth=400)[0],
            long=True,
        )[0]
        cmds.move(50, 150, -20, self.cube)

    def _probe(self, name="untitled_Probe.exr", turn=False, anchors=None):
        """The template's ``probe`` entry for the fixture room, its file written."""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(b"probe")

        def record_frame(point):
            x, y, z = (c * self._SCALE for c in point)
            # A turned crossing: a quarter about the up axis.
            return [z, y, -x] if turn else [x, y, z]

        if anchors is None:
            anchors = [
                {
                    "object": "bbProbeRoom",
                    "vertex": i,
                    "point": record_frame(
                        cmds.pointPosition(f"{self.cube}.vtx[{i}]", world=True)
                    ),
                }
                for i in range(8)
            ]
        return {
            "map": path,
            "position": record_frame((50.0, 150.0, -20.0)),
            "box": [record_frame((-250, 0, -220)), record_frame((350, 300, 180))],
            "anchors": anchors,
        }

    def _commit(self, probe):
        from mayatk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        path = BlenderBridge()._commit_returned_probe(probe, {"bbProbeRoom": self.cube})
        return path, LightmapRecords.probe()

    def test_the_probe_is_recorded_in_this_scenes_space_and_name(self):
        path, record = self._commit(self._probe(name="other_Probe.exr"))
        self.assertEqual(os.path.basename(path), "untitled_Probe.exr")
        self.assertTrue(os.path.isfile(path), "renamed as Maya's own bake names it")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "other_Probe.exr")))
        self.assertEqual(record["map"], "untitled_Probe.exr")
        for got, want in zip(record["position"], (50.0, 150.0, -20.0)):
            self.assertAlmostEqual(got, want, delta=1e-3)
        for corner, wanted in zip(record["box"], ((-250, 0, -220), (350, 300, 180))):
            for got, want in zip(corner, wanted):
                self.assertAlmostEqual(got, want, delta=1e-3)

    def test_a_turned_crossing_records_no_probe(self):
        """The panorama is laid out in the record frame's axes: mapped through a
        turn, it would reflect the room on its side."""
        with self.assertLogs(BlenderBridge.logger, level="WARNING") as caught:
            path, record = self._commit(self._probe(turn=True))
        self.assertIsNone(path)
        self.assertIsNone(record)
        self.assertTrue(any("turned" in line for line in caught.output), caught.output)

    def test_too_few_anchors_record_no_probe(self):
        probe = self._probe()
        probe["anchors"] = probe["anchors"][:2]
        with self.assertLogs(BlenderBridge.logger, level="WARNING"):
            path, record = self._commit(probe)
        self.assertIsNone(path)
        self.assertIsNone(record)

    def test_an_anchor_off_the_crossing_records_no_probe(self):
        """A mesh edited since the hand-off: its vertices no longer describe one
        crossing, and a probe placed through them would stand somewhere else."""
        probe = self._probe()
        # Moved straight out from the room's middle: the crossing stays unturned,
        # so only the fit's residual can refuse it.
        points = [a["point"] for a in probe["anchors"]]
        middle = [sum(p[i] for p in points) / len(points) for i in range(3)]
        probe["anchors"][0]["point"] = [
            m + (c - m) * 1.05 for c, m in zip(points[0], middle)
        ]
        with self.assertLogs(BlenderBridge.logger, level="WARNING"):
            path, record = self._commit(probe)
        self.assertIsNone(path)
        self.assertIsNone(record)

    def test_a_return_manifest_without_a_probe_records_none(self):
        self.assertEqual(self._commit(None), (None, None))

    def test_the_template_ships_its_anchors_in_the_probe_records_frame(self):
        """The Blender half (``capture_probe``): the probe's point and box come
        from blendertk's record -- Blender's Z-up axes read as ``(x, z, -y)`` --
        so the anchors must be in that frame too, or the measured crossing turns
        and no probe is recorded. Run on stand-ins for bpy and blendertk."""
        import ast

        source = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        nodes = [
            n
            for n in ast.parse(source).body
            if (isinstance(n, ast.FunctionDef) and n.name == "capture_probe")
            or (
                isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "PROBE_ANCHOR_OBJECTS"
            )
        ]
        code = compile(ast.Module(nodes, type_ignores=[]), "template", "exec")

        class _Identity:
            def __matmul__(self, co):
                return co

        verts = [(1.0, 2.0, 9.0), (-1.0, 5.0, 0.5), (4.0, -2.0, 7.0)]
        obj = types.SimpleNamespace(
            type="MESH",
            matrix_world=_Identity(),
            data=types.SimpleNamespace(
                vertices=[types.SimpleNamespace(co=v) for v in verts]
            ),
        )
        bpy = types.SimpleNamespace(data=types.SimpleNamespace(objects={"room": obj}))
        record = {"position": [0.0, 1.5, -2.0], "box": None}
        records = types.ModuleType("lightmap_records")
        records.LightmapRecords = types.SimpleNamespace(probe=lambda: record)
        baker = types.SimpleNamespace(bake_probe=lambda maps: "C:/maps/p.exr")
        namespace = {"LIGHTMAP_REFLECTION_PROBE": True, "bpy": bpy, "os": os}
        exec(code, namespace)
        with mock.patch.dict(
            sys.modules,
            {"blendertk.light_utils.lightmap_baker.lightmap_records": records},
        ):
            shipped = namespace["capture_probe"](baker, {"room": ("m.exr", None)})
        self.assertEqual(shipped["position"], record["position"])
        points = {a["vertex"]: a["point"] for a in shipped["anchors"]}
        self.assertEqual(sorted(points), [0, 1, 2], "each axis' extremes")
        for index, (x, y, z) in enumerate(verts):
            self.assertEqual(points[index], [x, z, -y])
        namespace["LIGHTMAP_REFLECTION_PROBE"] = False
        self.assertIsNone(namespace["capture_probe"](baker, {"room": ("m", None)}))


class TestBridgeLightmapSceneRecords(MayaTkTestCase):
    """The scene's lightmap records cross with a bake send (``LIGHTMAP_SECTION``).

    Maya's own baker gives an Exclude-set member no map but keeps it in the
    render, and never writes over a map another object reads
    (``LightmapRecords.claims``). The bridge bakes in a factory Blender that
    holds neither record, so both ride the manifest, spelled the way Blender
    names what it imports -- and a name the bake cannot be sure of is never
    guessed at.
    """

    @staticmethod
    def _cube(name, parent=None):
        node = cmds.polyCube(name=name)[0]
        if parent:
            node = cmds.parent(node, parent)[0]
        return cmds.ls(node, long=True)[0]

    @staticmethod
    def _section(objects):
        bridge = BlenderBridge()
        return bridge._manifest_lightmap(
            bridge._subtree(objects), bridge._manifest_spelling()
        )

    def test_the_section_names_excluded_meshes_and_every_reader(self):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet

        keep = self._cube("lmx_keep")
        skip = self._cube("lmx_skip")
        outside = self._cube("lmx_outside")  # never sent
        LightmapExcludeSet.define([skip])
        LightmapRecords.commit(
            {
                keep: "C:/lm/Shared_Lightmap.exr",
                outside: "C:/lm/Shared_Lightmap.exr",
                skip: "C:/lm/Skip_Lightmap.exr",
            }
        )

        section = self._section([keep, skip])

        self.assertEqual(section["exclude"], ["lmx_skip"])
        # The re-baked reader by its Blender name; the one outside the send
        # carries a spelling no Blender object has, so the file stays its own.
        self.assertEqual(
            section["claims"]["shared_lightmap.exr"],
            sorted(["lmx_keep", f"{BlenderBridge._FOREIGN_READER}{outside}"]),
        )
        # An excluded reader is never the bake's either: its map is not replaced.
        self.assertEqual(
            section["claims"]["skip_lightmap.exr"],
            [f"{BlenderBridge._FOREIGN_READER}{skip}"],
        )

    def test_a_leaf_name_two_sent_meshes_share_is_never_guessed(self):
        """FBX keeps leaf names, so Blender sees ``twin`` and ``twin.001``;
        which is which cannot be known from here."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet

        grp_a = cmds.group(empty=True, name="lmx_grpA")
        grp_b = cmds.group(empty=True, name="lmx_grpB")
        a = self._cube("lmx_twin", grp_a)
        b = cmds.ls(cmds.rename(self._cube("lmx_other", grp_b), "lmx_twin"), long=True)[
            0
        ]
        LightmapExcludeSet.define([a])
        LightmapRecords.commit({b: "C:/lm/Twin_Lightmap.exr"})

        section = self._section([grp_a, grp_b])

        self.assertEqual(section["exclude"], [], "caught on the way back instead")
        self.assertEqual(
            section["claims"]["twin_lightmap.exr"],
            [f"{BlenderBridge._FOREIGN_READER}{b}"],
        )

    def test_a_mesh_sharing_its_leaf_name_with_a_group_is_never_guessed(self):
        """Blender names every imported OBJECT uniquely: a group and a mesh both
        called ``shelf`` arrive as ``shelf`` and ``shelf.001``, in the importer's
        order -- so the mesh's name is as unknowable as a twin mesh's."""
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet

        root = cmds.group(empty=True, name="lmx_root")
        mesh = self._cube("lmx_shelf", root)
        holder = cmds.group(empty=True, name="lmx_holder", parent=root)
        cmds.rename(cmds.group(empty=True, name="lmx_tmp", parent=holder), "lmx_shelf")
        LightmapExcludeSet.define([mesh])

        self.assertEqual(self._section([root])["exclude"], [])

    def test_the_template_rebuilds_materials_without_ambient_occlusion(self):
        """Cycles traces the occlusion an AO map approximates, so the bake
        rebuilds the sent materials without the AO multiply -- as the Arnold
        bake's game-shader stand-in does (production office: bounce-lit ceilings
        0.77x Arnold with it, 0.95x without).
        Added: 2026-10-01
        """
        import ast

        tree = ast.parse(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "_apply_texture_manifest"
        ]
        self.assertTrue(calls, "the template no longer replays the texture manifest")
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            self.assertIn("ambient_occlusion", kw)
            self.assertIs(kw["ambient_occlusion"].value, False)

    def test_the_template_keeps_hidden_meshes_out_of_render_and_targets(self):
        """A group send carries its hidden descendants -- ``_bakeable`` only
        filters the meshes it is handed -- and Blender's FBX importer maps the
        hidden flag to ``hide_viewport`` but leaves ``hide_render`` on, so Cycles
        rendered and baked what Arnold never renders. Measured on the production
        office (2026-10-01): the hidden ``|TEMP|PROPS`` figure and spare table
        were baked and in the render. Read off the template's source, the bridge
        suite's convention for template contracts: hiding is read per mesh up its
        parents, taken out of the render, and out of the targets.
        Added: 2026-10-01
        """
        import ast

        source = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        rule = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "hidden_in_maya"
        )
        body = ast.unparse(rule)
        self.assertIn("obj.hide_viewport", body)
        self.assertIn("obj = obj.parent", body)
        self.assertIn("hidden = [o for o in meshes if hidden_in_maya(o)]", source)
        self.assertIn("obj.hide_render = True", source)
        self.assertIn("meshes = [o for o in meshes if o not in hidden]", source)

    def test_the_template_reads_the_records_off_the_imported_meshes(self):
        """Not off every imported object: the light rebuild replaces the lights'
        FBX nulls, and the first read of a removed one raised ReferenceError --
        measured on a live round trip, the whole bake died before a ray."""
        import ast

        tree = ast.parse(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "lightmap_records"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual([ast.unparse(a) for a in calls[0].args], ["meshes"])

    def test_only_the_bake_send_writes_the_section(self):
        keep = self._cube("lmx_write")
        with tempfile.TemporaryDirectory() as tmp:
            fbx = os.path.join(tmp, "x.fbx")
            sidecar = fbx + ".manifest.json"
            quiet = dict(
                include_materials=False, include_lights=False, include_scene_data=False
            )
            BlenderBridge()._write_manifest([keep], fbx, include_lightmap=True, **quiet)
            with open(sidecar, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(
                data[BlenderBridge.LIGHTMAP_SECTION], {"exclude": [], "claims": {}}
            )
            os.remove(sidecar)
            BlenderBridge()._write_manifest([keep], fbx, **quiet)
            self.assertFalse(os.path.exists(sidecar), "a plain send has nothing to say")

    def test_a_send_whose_every_mesh_is_excluded_is_refused_before_the_export(self):
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet

        only = self._cube("lmx_only")
        LightmapExcludeSet.define([only])
        bridge = BlenderBridge()
        self.assertIn("Exclude set", bridge._all_excluded([only]))
        self.assertIsNone(bridge._all_excluded([only, self._cube("lmx_also")]))

        request = mock.Mock(
            template=BlenderBridge._LIGHTMAP_TEMPLATE, params={"LIGHTMAP_DIR": "C:/lm"}
        )
        with mock.patch.object(handoff_export.FbxUtils, "export") as exported:
            with self.assertLogs(bridge.logger, level="ERROR"):
                self.assertIsNone(bridge._produce([only], request))
        exported.assert_not_called()

    def test_a_bake_whose_manifest_fails_is_refused(self):
        """A send goes on without its sidecar; a bake cannot. The bake's
        manifest carries the claims that keep it off the maps other objects
        read, the Exclude set and the lights that cross: without it the bake
        would run unguarded and differently lit, and commit what it made. It
        stops instead, naming why."""
        cube = self._cube("lmx_unguarded")
        bridge = BlenderBridge()
        with (
            mock.patch.object(
                BlenderBridge,
                "_write_manifest",
                side_effect=RuntimeError("claims unreadable"),
            ),
            mock.patch.object(
                handoff_export.MayaExportMixin,
                "_produce",
                return_value=types.SimpleNamespace(primary="payload.fbx"),
            ),
        ):
            for template, refused in (
                ("send", False),
                (BlenderBridge._LIGHTMAP_TEMPLATE, True),
            ):
                request = types.SimpleNamespace(
                    template=template, params={"LIGHTMAP_DIR": "C:/lm"}
                )
                with self.assertLogs(bridge.logger, level="WARNING") as caught:
                    payload = bridge._produce([cube], request)
                self.assertEqual(payload is None, refused, template)
            self.assertIn("claims unreadable", caught.output[-1])

    def test_the_return_leg_never_wires_an_excluded_object(self):
        """The template leaves an excluded mesh out by name; one it could not
        name (a shared leaf name) comes back baked, and must still keep the map
        it had."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet

        kept = self._cube("lmx_back")
        LightmapRecords.commit({kept: "C:/lm/Old_Lightmap.exr"})
        LightmapExcludeSet.define([kept])
        shape = cmds.listRelatives(kept, shapes=True, fullPath=True)[0]
        with tempfile.TemporaryDirectory() as tmp:
            exr = os.path.join(tmp, "New_Lightmap.exr")
            open(exr, "wb").close()
            manifest = os.path.join(tmp, "x.lightmaps.json")
            Path(manifest).write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "separated",
                        "lighting": {},
                        "meshes": {
                            "Mesh": TestBridgePerInstanceLightmaps._layout_from(shape)
                        },
                        "objects": {"lmx_back": {"map": exr, "mesh": "Mesh"}},
                    }
                ),
                encoding="utf-8",
            )
            bridge = BlenderBridge()
            # A seed arrives spelled as its caller wrote it: long, or short.
            for seed in (kept, "lmx_back"):
                with self.assertLogs(bridge.logger, level="WARNING") as caught:
                    wired = bridge.reassemble_lightmaps(manifest, [seed])
                self.assertEqual(wired, {}, seed)
                self.assertTrue(
                    any("Exclude set" in line for line in caught.output), seed
                )
        self.assertEqual(LightmapRecords._marker_info(kept)["map"], "Old_Lightmap.exr")


class TestBridgeReturnsWaitForTheSave(MayaTkTestCase):
    """The bridge's returned maps wait in the session until the scene is saved,
    as every Maya bake's do (``LightmapRecords._stage``)."""

    def test_a_returned_map_keeps_off_another_scenes_file_of_its_name(self):
        """Blender names the maps in the session folder, which holds none of
        the home folder's files: one whose home name is another scene's -- not
        read here, not written by this scene -- moves to the next free name
        before it is wired, so the save cannot put it over that file. Added:
        2026-10-23"""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        home = self.enterContext(
            ptk.TempArtifacts("bb_strangers", policy="scoped")
        ).dir_path()
        theirs = os.path.join(home, "Room_Lightmap.exr")
        with open(theirs, "wb") as fh:
            fh.write(b"theirs")
        returned = os.path.join(LightmapRecords._pending().folder(home), "Room_Lightmap.exr")
        with open(returned, "wb") as fh:
            fh.write(b"ours")
        out = BlenderBridge._clear_of_strangers({"|a": returned, "|b": returned})
        self.assertEqual(len(set(out.values())), 1, "one atlas, renamed once")
        (path,) = set(out.values())
        self.assertEqual(os.path.basename(path), "Room_Lightmap_1.exr")
        self.assertEqual(os.path.dirname(path), os.path.dirname(returned))
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"theirs")
        # A map this scene reads is its own to replace: kept as named.
        LightmapRecords.commit({cmds.polyCube(name="bb_own")[0]: theirs}, written=False)
        again = os.path.join(os.path.dirname(returned), "Room_Lightmap.exr")
        with open(again, "wb") as fh:
            fh.write(b"ours")
        self.assertEqual(BlenderBridge._clear_of_strangers({"|a": again})["|a"], again)


class TestStrangersAreVettedBeforeTheUvWrite(TestBridgeAtlasCells):
    """``_clear_of_strangers`` renames files on disk and can raise; it ran
    after ``UvUtils.apply_uv_layout``, so a failure there left rewritten UVs
    with nothing committed -- against the return leg's own rule, "vet each
    entry BEFORE touching a mesh". Added: 2026-10-24"""

    def test_the_stranger_check_runs_before_any_uv_is_rewritten(self):
        from mayatk.uv_utils._uv_utils import UvUtils

        order = []
        real_clear = BlenderBridge._clear_of_strangers.__func__
        real_apply = UvUtils.apply_uv_layout

        def clear(cls, mapping):
            order.append("strangers")
            return real_clear(cls, mapping)

        def apply(*args, **kwargs):
            order.append("uvs")
            return real_apply(*args, **kwargs)

        with mock.patch.object(
            BlenderBridge, "_clear_of_strangers", classmethod(clear)
        ), mock.patch.object(UvUtils, "apply_uv_layout", side_effect=apply):
            wired = BlenderBridge().reassemble_lightmaps(
                self._manifest(), list(self.cubes.values())
            )
        self.assertEqual(set(wired), set(self.cubes.values()))
        self.assertEqual(order[:2], ["strangers", "uvs"], order)

    def test_a_failed_check_leaves_every_mesh_as_it_was(self):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        before = {
            node: cmds.polyUVSet(node, query=True, allUVSets=True)
            for node in self.cubes.values()
        }
        manifest = self._manifest()
        with mock.patch.object(
            BlenderBridge,
            "_clear_of_strangers",
            classmethod(lambda cls, mapping: (_ for _ in ()).throw(RuntimeError("x"))),
        ):
            with self.assertRaises(RuntimeError):
                BlenderBridge().reassemble_lightmaps(manifest, list(self.cubes.values()))
        after = {
            node: cmds.polyUVSet(node, query=True, allUVSets=True)
            for node in self.cubes.values()
        }
        self.assertEqual(after, before, "a UV set was written before the vetting")
        self.assertFalse(LightmapRecords.bake_state().get("objects"))


class TestAStrangerMoveThatFailsKeepsItsPath(TestBridgeAtlasCells):
    """``os.listdir`` / ``os.replace`` raised out of the return leg: the map
    keeps its path, warned, and the rest go on. Added: 2026-10-24"""

    def test_a_held_file_keeps_its_returned_path(self):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        home = self.enterContext(
            ptk.TempArtifacts("bb_strangers_held", policy="scoped")
        ).dir_path()
        with open(os.path.join(home, "Room_Lightmap.exr"), "wb") as fh:
            fh.write(b"theirs")
        returned = os.path.join(
            LightmapRecords._pending().folder(home), "Room_Lightmap.exr"
        )
        with open(returned, "wb") as fh:
            fh.write(b"ours")
        with mock.patch("os.replace", side_effect=PermissionError("held open")), (
            mock.patch.object(BlenderBridge.logger, "warning")
        ) as warned:
            out = BlenderBridge._clear_of_strangers({"|a": returned})
        self.assertEqual(out, {"|a": returned})
        self.assertTrue(warned.called)
        self.assertIn("Room_Lightmap.exr", str(warned.call_args))


class TestHandoffWritePlan(MayaTkTestCase):
    """A hand-off write ships every node where Maya plays it, and bakes over
    the range its plan says (``MayaExportMixin._write_plan``).

    A static hand-off -- the Blender bridge's and the WebXR preview's
    default -- does not bake, and the plug-in drops every offsetParentMatrix
    from such a write (measured on Maya 2025 / FBX 2020.3.6: a static
    ``freeze_to_opm`` cube 3.0 off), so each offset is folded into its node
    for the write; an animated one bakes, which folds it already. And the
    bake flag is written BEFORE the range: written after, as the hand-off's
    option pins did, it reset the shots' union to the whole animation range.
    Added: 2026-10-17
    """

    def setUp(self):
        super().setUp()
        from mayatk.env_utils.fbx_utils import FbxUtils

        tmp = ptk.TempArtifacts("mtk_handoff_write_plan", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.dir_path()
        self.addCleanup(FbxUtils.reset_export)
        self.addCleanup(TestHandoffShipsOneWholeTake._drop_carrier)
        cmds.playbackOptions(min=1, max=100, ast=1, aet=100)

    def _frozen(self):
        from mayatk.xform_utils._xform_utils import XformUtils

        cube = cmds.polyCube(name="handoff_frozen")[0]
        cmds.xform(cube, worldSpace=True, translation=(3, 0, 1), rotation=(0, 30, 0))
        XformUtils.freeze_to_opm([cube])
        return cmds.ls(cube, long=True)[0]

    def _shipped(self, path, leaf):
        cmds.file(new=True, force=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        (node,) = cmds.ls(leaf, long=True, type="transform")
        return (
            cmds.getAttr(f"{node}.worldMatrix[0]"),
            cmds.keyframe(node, query=True, keyframeCount=True),
        )

    def test_a_static_handoff_ships_each_offset_exactly_and_keyless(self):
        from mayatk.env_utils.webxr_preview import WebXrPreview

        for bridge in (BlenderBridge, WebXrPreview):
            with self.subTest(bridge=bridge.__name__):
                cmds.file(new=True, force=True)
                cube = self._frozen()
                world = cmds.getAttr(f"{cube}.worldMatrix[0]")
                offset = cmds.getAttr(f"{cube}.offsetParentMatrix")
                path = os.path.join(self.dir, f"{bridge.__name__}.fbx")

                bridge()._export_fbx(
                    [cube], path, {"INCLUDE_ANIMATION": False, "EMBED_TEXTURES": False}
                )

                self.assertEqual(cmds.getAttr(f"{cube}.offsetParentMatrix"), offset)
                got, keys = self._shipped(path, "handoff_frozen")
                self.assertLess(max(abs(a - b) for a, b in zip(world, got)), 1e-3)
                self.assertFalse(keys, "a static hand-off keyed a static node")

    def test_an_animated_unity_handoff_still_bakes(self):
        """The Unity bridge carries animation: its write bakes, which folds
        the offset itself -- nothing changes for it."""
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge

        cube = self._frozen()
        world = cmds.getAttr(f"{cube}.worldMatrix[0]")
        path = os.path.join(self.dir, "unity.fbx")
        seen = {}
        original = FbxUtils.offsets_folded.__func__

        def spy(cls, objects=None, plan=None, logger=None):
            seen["plan"] = plan
            return original(cls, objects, plan, logger)

        with mock.patch.object(FbxUtils, "offsets_folded", classmethod(spy)):
            UnityBridge()._export_fbx([cube], path, {"EMBED_TEXTURES": False})

        self.assertTrue(seen["plan"].bakes)
        got, _ = self._shipped(path, "handoff_frozen")
        self.assertLess(max(abs(a - b) for a, b in zip(world, got)), 1e-3)

    def test_a_pinned_node_ships_where_maya_draws_it_through_each_handoff(self):
        """A node that ignores its parent (``Matrices.pin_world_matrix``, every
        channel locked) under an animated module: the Unity hand-off bakes,
        the Blender one does not, and FBX has no "do not inherit" -- measured
        2026-10-17, 121.6 cm off baked and 98.7 plain. Each re-imports where
        Maya draws it, and the scene keeps its pin and locks."""
        from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge
        from mayatk.xform_utils.matrices import Matrices

        frames = (1, 30, 61, 100)
        locked = [f"{a}{x}" for a in ("translate", "rotate", "scale") for x in "XYZ"]
        for bridge, animated in ((UnityBridge, True), (BlenderBridge, False)):
            with self.subTest(bridge=bridge.__name__):
                cmds.file(new=True, force=True)
                cmds.currentTime(1)
                root = cmds.group(empty=True, name="handoff_module")
                cube = cmds.polyCube(name="handoff_pinned")[0]
                cube = cmds.ls(cmds.parent(cube, root)[0], long=True)[0]
                cmds.xform(cube, translation=(3, 2, 1), rotation=(0, 20, 0))
                self.assertTrue(Matrices.pin_world_matrix(cube))
                for attr, (first, last) in (("tx", (50, 150)), ("ry", (30, 75))):
                    cmds.setKeyframe(root, attribute=attr, time=1, value=first)
                    cmds.setKeyframe(root, attribute=attr, time=100, value=last)
                for attr in locked:
                    cmds.setAttr(f"{cube}.{attr}", lock=True)
                worlds = []
                for frame in frames:
                    cmds.currentTime(frame)
                    worlds.append(cmds.getAttr(f"{cube}.worldMatrix[0]"))
                path = os.path.join(self.dir, f"pinned_{bridge.__name__}.fbx")

                bridge()._export_fbx(
                    [root],
                    path,
                    {"INCLUDE_ANIMATION": animated, "EMBED_TEXTURES": False},
                )

                self.assertFalse(cmds.getAttr(f"{cube}.inheritsTransform"))
                self.assertTrue(
                    all(cmds.getAttr(f"{cube}.{a}", lock=True) for a in locked)
                )
                cmds.file(new=True, force=True)
                cmds.file(path, i=True, type="FBX", ignoreVersion=True)
                (shipped,) = cmds.ls("handoff_pinned", long=True, type="transform")
                for frame, world in zip(frames, worlds):
                    cmds.currentTime(frame)
                    got = cmds.getAttr(f"{shipped}.worldMatrix[0]")
                    self.assertLess(
                        max(abs(a - b) for a, b in zip(world, got)), 1e-3, frame
                    )

    def test_a_split_handoff_bakes_the_shots_union(self):
        """Bug: the hand-off pinned the bake flag after its take split set the
        shots' union, and the flag write reset the range -- the write baked
        the scene's animation range (1-100) instead of the union (10-70), and
        the whole take was resampled over all of it."""
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge
        from mayatk.node_utils.data_nodes import DataNodes

        cmds.currentUnit(time="film")
        cube = cmds.ls(cmds.polyCube(name="split_cube")[0], long=True)[0]
        for frame, value in ((1, 0.0), (40, 4.0), (100, 10.0)):
            cmds.setKeyframe(cube, attribute="translateX", t=frame, v=value)
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {
                "shots": [
                    {"clip": "Shot_A", "start": 10, "end": 30, "objects": []},
                    {"clip": "Shot_B", "start": 50, "end": 70, "objects": []},
                ]
            },
        )
        path = os.path.join(self.dir, "split.fbx")
        seen = {}
        real = cmds.file

        def writing(*args, **kwargs):
            if kwargs.get("exportSelected"):
                seen["range"] = FbxUtils.bake_range()
            return real(*args, **kwargs)

        with mock.patch.object(cmds, "file", side_effect=writing):
            UnityBridge()._export_fbx([cube], path, {"EMBED_TEXTURES": False})

        self.assertEqual(seen["range"], (10.0, 70.0))
        fbx = ptk.FbxFile.load(path, raw_payloads=False, span_arrays=("KeyTime",))
        curves = fbx.take_curves()["Take 001"]
        # Resampled over the union (61 frames) beside the keys outside it.
        self.assertEqual(curves[("split_cube", "Lcl Translation", "d|X")][2], 61 + 2)
