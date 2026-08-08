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

import os
import re
import tempfile
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
from pythontk.core_utils import app_handoff as bridge_base

from base_test import MayaTkTestCase


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
        # that forgets save_as relabels this as send_to -- the panel then routes it through
        # send(), which never populates __OUT_FILE__, and it launches and fails minutes in.
        self.assertEqual(modes["bake_lightmaps"], "save_as")

    def test_template_modes_parsed(self):
        self.assertEqual(
            BlenderBridge.template_modes(_TEMPLATE_DIR / "import.py"), ("send_to",)
        )
        self.assertEqual(
            BlenderBridge.template_modes(_TEMPLATE_DIR / "bake_lightmaps.py"),
            ("save_as",),
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

    def test_defaults_registry_matches_the_widget_specs(self):
        """The engine's Qt-free ``DEFAULTS`` is the same registry the panel shows.

        ``save_as`` can run where Qt cannot be imported (a headless DCC), so the engine
        answers ``params_defaults()`` from its own dict. The specs read that dict, so a
        MISSING key is already a loud import error; this pins the other direction -- a
        stale key nobody shows -- and that the two describe the same parameter set.
        """
        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        self.assertEqual(set(DEFAULTS), set(params.PARAMS))
        self.assertEqual(DEFAULTS, params.Parameters.defaults())

    def test_import_exposes_scene_and_frame_options(self):
        # The unified template exposes both scene-behavior knobs so the panel shows them.
        used = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "import.py").read_text()
        )
        self.assertIn("FRAME_VIEW", used)
        self.assertIn("CLEAR_SCENE", used)


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
            mock.patch.object(bridge_base.AppLauncher, "launch", return_value=object()),
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
        self.assertFalse(opts["FBXExportBakeComplexAnimation"])
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
            any(os.path.normcase(self.tex) == os.path.normcase(f) for f in entry["files"]),
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
        import pythontk as ptk

        self.assertTrue(
            any(
                ptk.MapRegistry.resolve_type_from_channel(c) is not None
                for c in slots
            ),
            f"no slot channel resolves to a map type: {sorted(slots)}",
        )

    def test_untextured_material_writes_no_sidecar(self):
        """A flat color rides the FBX fine -- no sidecar, no spurious warning."""
        cube = cmds.polyCube(name="mfx_plain")[0]
        fbx = os.path.join(self.tmp, "plain.fbx")
        BlenderBridge()._write_manifest([cube], fbx)
        self.assertFalse(os.path.isfile(fbx + ".manifest.json"))

    def test_produce_skips_the_sidecar_when_materials_are_off(self):
        """INCLUDE_MATERIALS=False is a geometry-only hand-off by contract."""
        cube = self._textured_cube()
        fbx = os.path.join(self.tmp, "nomat.fbx")
        bridge = BlenderBridge()
        with mock.patch.object(handoff_export.FbxUtils, "export"), mock.patch.object(
            handoff_export.FbxUtils, "load_plugin"
        ), mock.patch.object(bridge, "_make_payload_path", return_value=fbx):
            request = mock.Mock()
            request.params = {"INCLUDE_MATERIALS": False}
            bridge._produce([cube], request)
        self.assertFalse(os.path.isfile(fbx + ".manifest.json"))

    def test_template_replays_the_sidecar_through_the_shared_applier(self):
        """One applier, not a second copy of the rebuild logic."""
        text = (_TEMPLATE_DIR / "import.py").read_text(encoding="utf-8")
        self.assertIn("apply_texture_manifest", text)
        self.assertIn("_apply_texture_manifest", text)
        self.assertIn("MayaSceneImport", text)
        # Replayed regardless of the viewport-framing option.
        self.assertLess(
            text.index("apply_texture_manifest(new)"), text.index("if not FRAME_VIEW")
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
            import pythontk as ptk

            return ptk.ScriptRunResult(
                artifact=artifact,
                returncode=0,
                output="",
                duration=0.1,
                script_path="S.py",
            )

        return mock.patch.object(
            bridge_base.ScriptRunDeliverer, "run", staticmethod(fake_run)
        )

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

        staged = bridge_base.ScriptRunDeliverer._staging_path(self.out)
        self.assertIn("save_as_mainfile", run["script"])
        self.assertIn(f'OUT_FILE = r"{staged.replace(os.sep, "/")}"', run["script"])
        self.assertEqual(run["artifact"], staged)
        self.assertTrue(os.path.isfile(self.out))  # promoted
        self.assertFalse(os.path.exists(staged))
        self.assertFalse(re.findall(r"__[A-Z][A-Z0-9_]*__", run["script"]))

    def test_bake_lightmaps_targets_the_bake_template_and_keeps_glb(self):
        """``bake_lightmaps`` writes a .glb through the bake recipe, not the save one.

        Three wiring points that each fail silently: the wrong template renders a
        ``save_as_mainfile`` script (a .blend named .glb), a ``.glb`` missing from
        ``save_extensions`` gets rewritten to ``.blend`` by ``resolve_save_path``, and a
        kwarg that never reaches the render context leaves the bake on its default.
        """
        cube = cmds.polyCube(name="bb_bake")[0]
        out = os.path.join(self.tmp, "room.glb")
        export, load = self._export_patches()
        with export, load, self._run_patch():
            result = self.bridge.bake_lightmaps(
                out, [cube], environment_hdr="C:/hdri/room.hdr", samples=64
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["output"], out)  # .glb survived resolve_save_path
        self.assertEqual(len(self.runs), 1)
        script = self.runs[0]["script"]
        # The bake recipe, not _save_scene.
        self.assertIn("LightmapWebExport", script)
        self.assertNotIn("save_as_mainfile", script)
        # Named kwargs reach the template as Python literals.
        self.assertIn("LIGHTMAP_SAMPLES = 64", script)
        self.assertIn("ENVIRONMENT_HDR = 'C:/hdri/room.hdr'", script)
        # A registered STRING param is rendered as a Python literal by the formatter, so
        # the template must reference the token BARE; quoting it would assign the
        # doubly-quoted "'separated'" and silently break every mode comparison. Asserted
        # against the assignment itself -- the file's own comment explains the trap and
        # would satisfy a naive substring search for the broken form.
        self.assertIn("LIGHTMAP_MODE = 'separated'", script)
        self.assertNotIn('LIGHTMAP_MODE = "', script)
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
            self.bridge.bake_lightmaps(os.path.join(self.tmp, "t.glb"), [cube])

        used = self.runs[0]["timeout"]
        self.assertIsNotNone(used)
        self.assertGreater(used, 600, "bake inherited the too-short spec default")
        # Sourced from the template, not hardcoded at the call site, so the panel route
        # (which calls save_as directly) gets the same budget.
        self.assertEqual(
            used, BlenderBridge.template_timeout(_TEMPLATE_DIR / "bake_lightmaps.py")
        )

    def test_template_declares_its_artifact_format(self):
        """The save dialog's default format comes from the template, not its name."""
        self.assertEqual(
            BlenderBridge.template_output_ext(_TEMPLATE_DIR / "bake_lightmaps.py"), ".glb"
        )
        # An unannotated template falls back to the bridge's own default.
        self.assertEqual(
            BlenderBridge.template_output_ext(_TEMPLATE_DIR / "import.py"),
            BlenderBridge.save_extensions[0],
        )
        self.assertIsNone(BlenderBridge.template_timeout(_TEMPLATE_DIR / "import.py"))

    def test_bare_path_gets_the_blend_extension(self):
        cube = cmds.polyCube(name="bb_saveas_ext")[0]
        export, load = self._export_patches()
        with export, load, self._run_patch():
            result = self.bridge.save_as(os.path.join(self.tmp, "asset"), [cube])
        self.assertTrue(result["output"].endswith(".blend"))

    def test_defaults_to_the_whole_scene(self):
        """"Save the scene as ..." is about the scene -- selection state is irrelevant."""
        cube = cmds.polyCube(name="bb_saveas_scene")[0]
        cmds.select(clear=True)
        export, load = self._export_patches()
        with export, load, self._run_patch(), mock.patch.object(
            self.bridge, "_export_fbx", return_value=None
        ) as m_export:
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
        self.assertNotIn("_save_scene", [p.stem for p in BlenderBridge.list_templates()])
        self.assertTrue((_TEMPLATE_DIR / "_save_scene.py").is_file())

    def test_save_template_declares_only_save_as(self):
        """Read through the strict parser the blocking deliverer uses."""
        from pythontk.core_utils.script_template import ScriptTemplate

        self.assertEqual(
            ScriptTemplate.declared_modes(_TEMPLATE_DIR / "_save_scene.py"),
            ("save_as",),
        )

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
        with export, load, self._run_patch(), mock.patch.object(
            self.bridge, "_make_payload_path", return_value=fbx
        ), mock.patch.object(self.bridge, "_write_manifest") as m_manifest:
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

    def test_scope_specs_are_distinct_objects(self):
        """A shared mutable spec would let one bridge's tweak leak into all."""
        import importlib

        specs = [
            importlib.import_module(n).PARAMS["SCOPE"] for n in self._BRIDGES
        ]
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

    def test_ignores_scene_objects_the_run_did_not_export(self):
        """Resolution is scoped to this run, so a same-named stranger can't be picked up."""
        resolved, _amb, unmatched = BlenderBridge._resolve_returned_objects(
            ["stranger"], ["|grp|pCube1"]
        )
        self.assertEqual((resolved, unmatched), ({}, ["stranger"]))

    def test_missing_sidecar_is_reported_and_commits_nothing(self):
        """A GLB with no sidecar is still a valid deliverable -- it just isn't a round trip."""
        with tempfile.TemporaryDirectory() as tmp:
            glb = os.path.join(tmp, "nope.glb")
            Path(glb).write_bytes(b"")
            self.assertEqual(BlenderBridge().reassemble_lightmaps(glb, []), {})

    def test_unreadable_sidecar_does_not_raise(self):
        """The bake already cost minutes; a bad sidecar must not throw its result away."""
        with tempfile.TemporaryDirectory() as tmp:
            glb = os.path.join(tmp, "bad.glb")
            Path(glb).write_bytes(b"")
            Path(glb + BlenderBridge.RETURN_MANIFEST_SUFFIX).write_text("{not json")
            self.assertEqual(BlenderBridge().reassemble_lightmaps(glb, []), {})

    def test_lightmap_dir_is_a_registered_parameter(self):
        """It must reach the panel, or the EXRs silently land beside the .glb."""
        self.assertIn("LIGHTMAP_DIR", params.PARAMS)
        template = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        self.assertIn("__LIGHTMAP_DIR__", template)

    def test_template_leaves_registered_string_tokens_bare(self):
        """Registered params arrive pre-rendered as literals; quoting yields "'x'"."""
        template = (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        self.assertIn("LIGHTMAP_DIR = __LIGHTMAP_DIR__", template)
        self.assertNotIn('LIGHTMAP_DIR = r"__LIGHTMAP_DIR__"', template)

    def test_bake_lightmaps_indexes_the_same_set_it_exported(self):
        """save_as defaults to the whole SCENE, not the selection.

        Left unresolved, the bake would export the scene and then reassemble against an
        empty list: every object "unmatched", nothing wired back, and no error -- the GLB
        still lands and the run still reports success.
        """
        bridge = BlenderBridge()
        with mock.patch.object(
            BlenderBridge, "save_as", return_value=None
        ) as save_as, mock.patch.object(
            BlenderBridge, "_scene_objects", return_value=["|grp|pCube1"]
        ) as scene_objects:
            bridge.bake_lightmaps(os.path.join(tempfile.gettempdir(), "x.glb"))
        scene_objects.assert_called_once()
        self.assertEqual(save_as.call_args.args[1], ["|grp|pCube1"])

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
            self._template_constant("RETURN_MANIFEST_SUFFIX"),
            BlenderBridge.RETURN_MANIFEST_SUFFIX,
        )
        self.assertEqual(
            self._template_constant("RETURN_MANIFEST_VERSION"),
            BlenderBridge.RETURN_MANIFEST_VERSION,
        )

    def test_refuses_a_sidecar_from_a_newer_schema(self):
        """Misreading a newer payload writes WRONG UVs -- worse than doing nothing."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            glb = os.path.join(tmp, "future.glb")
            Path(glb).write_bytes(b"")
            Path(glb + BlenderBridge.RETURN_MANIFEST_SUFFIX).write_text(
                json.dumps(
                    {
                        "version": BlenderBridge.RETURN_MANIFEST_VERSION + 1,
                        "objects": {"pCube1": {"map": "x.exr", "uv_layout": {}}},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                BlenderBridge().reassemble_lightmaps(glb, ["|grp|pCube1"]), {}
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
