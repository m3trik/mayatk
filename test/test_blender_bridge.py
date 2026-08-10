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

import math
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
        from pythontk.core_utils import script_template

        self.assertEqual(
            set(BlenderBridge.template_modes_allowed),
            {
                script_template.SEND_TO,
                script_template.SAVE_AS,
                script_template.ROUND_TRIP,
            },
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
        default (the specs read the dict, so a missing key is a loud import error), but
        DEFAULTS may carry MORE: API-only parameters (the bake's resolution / samples /
        denoise / device overrides) substitute into templates without being panel
        widgets. For the shared keys the two must agree, or the panel would show one
        default and a headless run use another.
        """
        from mayatk.env_utils.blender_bridge._blender_bridge import DEFAULTS

        spec_defaults = params.Parameters.defaults()
        self.assertLessEqual(set(spec_defaults), set(DEFAULTS))
        for key, value in spec_defaults.items():
            self.assertEqual(DEFAULTS[key], value, key)
        # The API-only extras exist and are exactly the quality overrides.
        self.assertEqual(
            set(DEFAULTS) - set(spec_defaults),
            {
                "LIGHTMAP_RESOLUTION",
                "LIGHTMAP_SAMPLES",
                "LIGHTMAP_DENOISE",
                "LIGHTMAP_DEVICE",
            },
        )

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
        self.assertIn("LIGHTMAP_QUALITY = 'quest'", script)
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
            self.bridge.bake_lightmaps(os.path.join(self.tmp, "t.lightmaps.json"), [cube])

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
        self.assertEqual(
            os.path.normcase(os.path.dirname(out)),
            os.path.normcase(tempfile.gettempdir()),
            out,
        )
        project = BlenderBridge._default_lightmap_dir()
        if project:  # a workspace is always set in Maya, but do not assume it here
            self.assertNotEqual(
                os.path.normcase(os.path.dirname(out)), os.path.normcase(project)
            )
        # The maps themselves are the opposite case -- a real project artifact, resolved
        # through the workspace's own texture rule.
        self.assertTrue(not project or project.lower().endswith("sourceimages"), project)

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

        A production texture set lives in its OWN subfolder (``sourceimages/OFFICE_ENV/``),
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
            self._textured_cube("lm_dir_one", os.path.join(one, "SET_ONE_Base_color.png")),
            self._textured_cube("lm_dir_m1", os.path.join(many, "SET_MANY_Base_color.png")),
            self._textured_cube("lm_dir_m2", os.path.join(many, "SET_MANY_Normal.png")),
        ]
        self.assertEqual(
            os.path.normcase(BlenderBridge._default_lightmap_dir(cubes)),
            os.path.normcase(many),
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
        with mock.patch.object(
            BlenderBridge, "round_trip", return_value=None
        ) as round_trip, mock.patch.object(
            BlenderBridge, "_scene_objects", return_value=["|grp|pCube1"]
        ) as scene_objects:
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
            import pythontk as ptk

            Path(artifact).write_text("{}", encoding="utf-8")
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
        with mock.patch.object(bridge_base.HandoffBridge, "_preflight", return_value=True):
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

        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

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
                            "bb_inst_copy": {"map": exr, "mesh": "Mesh", "rect": rect_b},
                        },
                    }
                ),
                encoding="utf-8",
            )
            wired = BlenderBridge().reassemble_lightmaps(manifest, [src, copy])
        self.assertEqual(set(wired), {src, copy})
        baker = LightmapBaker()
        self.assertEqual(baker._marker_info(src)["scaleOffset"], rect_a)
        self.assertEqual(baker._marker_info(copy)["scaleOffset"], rect_b)
        # The layout landed on the SHARED shape (once), as the lightmap set.
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertIn("lightmap", sets)
        # And the publisher carries one record per instance.
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_export_string(LightmapBaker.LIGHTMAP_METADATA)
        recs = {o["name"]: o for o in json.loads(raw)["objects"]}
        self.assertEqual(set(recs), {"bb_inst_src", "bb_inst_copy"})
        self.assertEqual(recs["bb_inst_src"]["scaleOffset"], rect_a)
        self.assertEqual(recs["bb_inst_copy"]["scaleOffset"], rect_b)

    def test_v1_manifest_still_reassembles(self):
        """Legacy inline-layout manifests keep working (identity rects)."""
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

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
        info = LightmapBaker()._marker_info(solo)
        self.assertEqual(info["scaleOffset"], [1.0, 1.0, 0.0, 0.0])

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
            with export, load, self._run_patch(), mock.patch.object(
                BlenderBridge, "reassemble_lightmaps", return_value={solo: "m.exr"}
            ) as m_re, mock.patch(
                "mayatk.env_utils.webxr_preview.WebXrPreview"
            ) as m_prev:
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
            with export, load, self._run_patch(), mock.patch.object(
                BlenderBridge, "reassemble_lightmaps"
            ) as m_re3:
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
            r["name"]: r
            for r in BlenderBridge()._manifest_lights([sun_xf, point_xf])
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
        shipping lights with no row to turn it off.
        """
        for spec_key in ("INCLUDE_LIGHTS", "SCENE_LIGHT_STRENGTH"):
            self.assertIn(spec_key, params.PARAMS)
            self.assertIn(spec_key, BlenderBridge().params_defaults())
        bake = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "bake_lightmaps.py").read_text(encoding="utf-8")
        )
        self.assertIn("INCLUDE_LIGHTS", bake)
        self.assertIn("SCENE_LIGHT_STRENGTH", bake)
        imported = params.Parameters.referenced_keys(
            (_TEMPLATE_DIR / "import.py").read_text(encoding="utf-8")
        )
        self.assertIn("INCLUDE_LIGHTS", imported)

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
        only worth anything if the manifest reaches a consumer. ``lightmap_metadata``
        rides ``data_export``, which is NOT under the selected roots, so a selection
        push exported the meshes alone and the preview came back unlit with nothing
        in the log to explain it. Pinned at the export set: whatever the caller asks
        for, the carrier goes too.
        """
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        mesh = cmds.ls(cmds.polyCube(name="bb_prev_mesh")[0], long=True)[0]
        DataNodes.set_export_string(LightmapBaker.LIGHTMAP_METADATA, '{"version": 1}')
        carrier = cmds.ls(DataNodes.EXPORT, long=True)[0]

        with mock.patch.object(
            handoff_export.FbxUtils, "export"
        ) as m_export, mock.patch.object(handoff_export.FbxUtils, "load_plugin"):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {})
        exported = m_export.call_args.kwargs["objects"]
        self.assertIn(mesh, exported)
        self.assertIn(carrier, exported)

        # The strip-materials path must ship it too -- and must NOT duplicate it
        # (a locked, shapeless node has no materials to strip).
        with mock.patch.object(
            handoff_export.FbxUtils, "export"
        ) as m_strip, mock.patch.object(handoff_export.FbxUtils, "load_plugin"):
            WebXrPreview()._export_fbx([mesh], "x.fbx", {"INCLUDE_MATERIALS": False})
        stripped = m_strip.call_args.kwargs["objects"]
        self.assertIn(carrier, stripped)
        self.assertNotIn(mesh, stripped)  # the mesh went as a shader-less copy

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
