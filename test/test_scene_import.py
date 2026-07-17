# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.blender_bridge._scene_import.

Maya-side coverage for the pull-direction engine (``mtk.import_blender_scene`` --
the mirror of blendertk's ``btk.import_maya_scene``): template hygiene, script
rendering, discovery, input validation, FBX-name matching, and the convert ->
import -> rebuild -> cleanup orchestration with the Blender run, the FBX import,
and the GameShader build stubbed (a real conversion needs a Blender install; the
gated ``scene_import_live_e2e.py`` covers it end to end).

The manifest APPLY logic runs against REAL Maya nodes: shading-group member
transfer (the Maya analogue of blendertk's slot-level swap) must preserve
per-face assignments on multi-material meshes, and orphan purge must remove the
replaced material without touching anything still assigned.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py scene_import``).
"""
import glob
import json
import logging
import os
import tempfile
import unittest

import maya.cmds as cmds

import pythontk as ptk
from mayatk.env_utils.blender_bridge import _blender_bridge as bb
from mayatk.env_utils.blender_bridge import _scene_import as si
from mayatk.env_utils.blender_bridge._scene_import import (
    BlenderSceneImport,
    import_blender_scene,
    _fbx_safe_name,
    _matches_fbx_name,
    _IMPORT_TEMPLATE,
)

from base_test import MayaTkTestCase


class TestSceneImportTemplate(unittest.TestCase):
    """Template hygiene -- text-level pins on the Blender-side conversion script."""

    @classmethod
    def setUpClass(cls):
        cls.txt = _IMPORT_TEMPLATE.read_text(encoding="utf-8")

    def test_template_exists_and_is_hidden(self):
        self.assertTrue(_IMPORT_TEMPLATE.is_file())
        # Underscore-prefixed: never a user-pickable send recipe in the panel.
        self.assertNotIn("_import_scene", {p.stem for p in bb.list_templates()})

    def test_judged_by_artifact_contract(self):
        # os._exit makes the exit code honest (blender --background exits 0
        # even after a --python script raises).
        self.assertIn("os._exit(0)", self.txt)
        self.assertIn("os._exit(1)", self.txt)
        self.assertIn("export_scene.fbx", self.txt)

    def test_absolute_texture_paths(self):
        # The FBX lands in the temp dir: relative texture paths would be
        # unresolvable in Maya (the mirror of the pink-materials fix).
        self.assertIn('"ABSOLUTE"', self.txt)

    def test_per_kwarg_tolerance(self):
        # A renamed/removed exporter parameter must be dropped and retried,
        # not kill the conversion (bpy.ops rejects the whole call on one).
        self.assertIn("FBX kwarg skipped", self.txt)
        self.assertIn("TypeError", self.txt)

    def test_full_fidelity_flags(self):
        for flag in ("use_mesh_modifiers", "use_tspace", "use_custom_props",
                     "add_leaf_bones", "bake_anim"):
            self.assertIn(flag, self.txt)

    def test_manifest_written_with_fileless_entries(self):
        # File-less entries are written too -- a packed/broken-link material
        # must surface as a NAMED warning Maya-side, not silently gray.
        self.assertIn("write_texture_manifest", self.txt)
        self.assertIn(".manifest.json", self.txt)
        self.assertIn('"materials": entries', self.txt)
        # The sidecar also lists EVERY scene material (textured or not) so the
        # importer's rename-suffix match can never claim a real sibling's name.
        self.assertIn('"scene_materials": scene_materials', self.txt)

    def test_node_group_recursion(self):
        # Textures nested in node groups must reach the manifest.
        self.assertIn("ShaderNodeGroup", self.txt)
        self.assertIn("ShaderNodeTexImage", self.txt)

    def test_manifest_scopes_to_the_active_scene(self):
        # The FBX exporter writes the ACTIVE scene's objects; bpy.data.objects
        # would drag in other scenes / unlinked objects and produce manifest
        # entries nothing Maya-side can ever match.
        self.assertIn("for obj in bpy.context.scene.objects", self.txt)
        self.assertNotIn("for obj in bpy.data.objects", self.txt)


class TestSceneImportRendering(unittest.TestCase):
    """render_script substitution -- pure."""

    def test_render(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        script = eng.render_script(
            r"C:\scenes\test scene.blend", r"C:\tmp\out.fbx",
            embed_textures=False, include_animation=True,
        )
        self.assertNotIn("__" + "SRC_PATH" + "__", script)
        self.assertIn('r"C:/scenes/test scene.blend"', script)
        self.assertIn("C:/tmp/out.fbx", script)
        self.assertIn("EMBED_TEXTURES = False", script)
        self.assertIn("INCLUDE_ANIMATION = True", script)
        compile(script, "_import_scene_rendered.py", "exec")  # valid Python

    def test_launch_args_are_headless_factory(self):
        # The conversion Blender must be headless AND factory-startup (skips
        # the user's addons/config -- including any tentacle autostart).
        self.assertEqual(
            si._LAUNCH_ARGS, ("--background", "--factory-startup", "--python")
        )


class TestSceneImportDiscovery(unittest.TestCase):
    """Executable discovery -- pure."""

    def test_blender_path_no_raise(self):
        eng = BlenderSceneImport()
        self.assertTrue(eng.blender_path is None or isinstance(eng.blender_path, str))

    def test_explicit_path_wins(self):
        self.assertEqual(
            BlenderSceneImport("Y:/blender.exe").blender_path, "Y:/blender.exe"
        )


class TestSceneImportValidation(unittest.TestCase):
    """convert() input validation -- runs before any executable is required."""

    def test_missing_scene_raises(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        with self.assertRaises(FileNotFoundError):
            eng.convert("no_such_scene.blend", "out.fbx")

    def test_wrong_extension_raises(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        bad = os.path.join(tempfile.gettempdir(), "mtk_scene_import_bad.ma")
        open(bad, "w").close()
        try:
            with self.assertRaises(ValueError):
                eng.convert(bad, "out.fbx")
        finally:
            os.remove(bad)


class TestFbxNameMatching(unittest.TestCase):
    """Blender datablock name -> Maya FBX-importer spelling.

    The FBXASC encoding is pinned against a LIVE probe (Maya 2025 FBX import
    of a Blender export): ``dotted.001`` -> ``dottedFBXASC046001``,
    ``spa ced`` -> ``spaFBXASC032ced``, ``dash-y`` -> ``dashFBXASC045y``,
    ``1digit`` -> ``FBXASC049digit`` (leading digit encoded, later digits kept).
    """

    def test_fbx_safe_name(self):
        self.assertEqual(_fbx_safe_name("dotted.001"), "dottedFBXASC046001")
        self.assertEqual(_fbx_safe_name("spa ced"), "spaFBXASC032ced")
        self.assertEqual(_fbx_safe_name("dash-y"), "dashFBXASC045y")
        self.assertEqual(_fbx_safe_name("1digit"), "FBXASC049digit")
        self.assertEqual(_fbx_safe_name("Clean_Name"), "Clean_Name")

    def test_matches_with_clash_suffix(self):
        self.assertTrue(_matches_fbx_name("M_test", "M_test"))
        # Maya's rename-on-clash appends digits.
        self.assertTrue(_matches_fbx_name("M_test1", "M_test"))
        self.assertFalse(_matches_fbx_name("M_test_extra", "M_test"))
        self.assertFalse(_matches_fbx_name("Other", "M_test"))


class _StubbedImport(BlenderSceneImport):
    """Blender run + FBX import + GameShader stubbed; manifest apply is REAL."""

    calls = {}

    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        calls = _StubbedImport.calls
        calls["runs"] = calls.get("runs", 0) + 1
        with open(artifact, "wb") as fh:  # the Blender side "produces" the FBX
            fh.write(b"fbx-bytes")
        with open(artifact + ".manifest.json", "w") as mf:
            json.dump(calls["manifest"], mf)
        return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

    def require_blender(self):
        return "stub_blender"

    def _import_fbx(self, fbx_path, fbx_options=None):
        calls = _StubbedImport.calls
        calls["fbx"] = fbx_path
        calls["fbx_options"] = fbx_options
        return calls["import_result"]()

    @staticmethod
    def _rebuild_material(files, name):
        calls = _StubbedImport.calls
        calls.setdefault("created", []).append((tuple(files), name))
        if name == "M_unclass":
            return None  # "nothing classified" -- keep the FBX material
        # Cheap stand-in for the GameShader network: shader + SG, no textures.
        shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                       name=f"{name}SG")
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        return sg


class TestSceneImportOrchestration(MayaTkTestCase):
    """convert -> import -> manifest rebuild -> cleanup, against real nodes."""

    def setUp(self):
        super().setUp()
        _StubbedImport.calls = {}
        self.src = os.path.join(tempfile.gettempdir(), "mtk_scene_import_src.blend")
        with open(self.src, "wb") as f:
            f.write(b"BLENDER-v500")
        self.tex = os.path.join(
            tempfile.gettempdir(), "mtk_scene_import_BaseColor.png"
        )
        with open(self.tex, "wb") as f:
            f.write(b"png-bytes")

    def tearDown(self):
        for path in (self.src, self.tex):
            if os.path.exists(path):
                os.remove(path)
        for stale in glob.glob(
            os.path.join(tempfile.gettempdir(), "blender_to_mtk_cache_*")
        ):
            os.remove(stale)
        super().tearDown()

    def _build_imported_scene(self):
        """Real nodes mimicking what the FBX importer creates: a two-material
        cube (per-face split), a fallback object whose material the importer
        renamed, and an untouched bystander."""
        cube = cmds.polyCube(name="objA")[0]
        mat_a = cmds.shadingNode("phong", asShader=True, name="M_test")
        sg_a = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                         name="M_testSG")
        cmds.connectAttr(f"{mat_a}.outColor", f"{sg_a}.surfaceShader", force=True)
        mat_b = cmds.shadingNode("phong", asShader=True, name="M_keep")
        sg_b = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                         name="M_keepSG")
        cmds.connectAttr(f"{mat_b}.outColor", f"{sg_b}.surfaceShader", force=True)
        cmds.sets(f"{cube}.f[0:2]", forceElement=sg_a)
        cmds.sets(f"{cube}.f[3:5]", forceElement=sg_b)

        obj_b = cmds.polyCube(name="objB")[0]
        mat_r = cmds.shadingNode("phong", asShader=True, name="M_renamed_by_importer")
        sg_r = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                         name="M_renamed_by_importerSG")
        cmds.connectAttr(f"{mat_r}.outColor", f"{sg_r}.surfaceShader", force=True)
        cmds.sets(obj_b, forceElement=sg_r)

        obj_c = cmds.polyCube(name="objC")[0]
        return [cube, obj_b, obj_c, mat_a, sg_a, mat_b, sg_b, mat_r, sg_r]

    def test_full_orchestration(self):
        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                # Primary path: SG-level member transfer (per-face preserved).
                {"name": "M_test", "fbx_material": "M_test",
                 "objects": ["objA"], "files": [self.tex]},
                # Fallback path: importer renamed the material -> object-level.
                {"name": "M_fb", "fbx_material": "M_nowhere",
                 "objects": ["objB"], "files": [self.tex]},
                # All files gone -> named warning, nothing touched.
                {"name": "M_gone", "fbx_material": "M_gone",
                 "objects": ["objC"], "files": ["X:/missing.png"]},
            ],
        }
        _StubbedImport.calls["import_result"] = self._build_imported_scene

        imported = _StubbedImport().import_scene(self.src, use_cache=False)

        # Returns the transform subset (behavior parity with blendertk).
        self.assertEqual(sorted(imported), ["objA", "objB", "objC"])

        # Rebuilt from the on-disk file only for entries whose files exist.
        self.assertEqual(
            _StubbedImport.calls["created"],
            [((self.tex,), "M_test"), ((self.tex,), "M_fb")],
        )

        # Primary swap: faces 0-2 moved to the rebuilt SG; faces 3-5 untouched.
        sg_new = "M_testSG1" if cmds.objExists("M_testSG1") else "M_testSG"
        members = cmds.sets(sg_new, query=True) or []
        self.assertTrue(any("f[0:2]" in m for m in members), members)
        keep_members = cmds.sets("M_keepSG", query=True) or []
        self.assertTrue(any("f[3:5]" in m for m in keep_members), keep_members)
        # The replaced phong (and its emptied SG) purged; the keeper stays.
        self.assertFalse(cmds.objExists("M_test") and
                         cmds.nodeType("M_test") == "phong")
        self.assertTrue(cmds.objExists("M_keep"))

        # Fallback: objB force-assigned to the rebuilt M_fb network (Maya
        # records renderable-set membership by SHAPE, not transform).
        fb_members = cmds.sets("M_fbSG", query=True) or []
        self.assertTrue(
            any(m.split("|")[-1].startswith("objB") for m in fb_members),
            fb_members,
        )

        # Intermediate payload removed on success.
        self.assertFalse(os.path.exists(_StubbedImport.calls["fbx"]))
        self.assertFalse(
            os.path.exists(_StubbedImport.calls["fbx"] + ".manifest.json")
        )

    def test_unclassified_entry_keeps_fbx_material(self):
        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                {"name": "M_unclass", "fbx_material": "M_test",
                 "objects": ["objA"], "files": [self.tex]},
            ],
        }
        _StubbedImport.calls["import_result"] = self._build_imported_scene
        _StubbedImport().import_scene(self.src, use_cache=False)
        # _rebuild_material returned None -> the FBX-carried phong survives.
        self.assertTrue(cmds.objExists("M_test"))
        self.assertEqual(cmds.nodeType("M_test"), "phong")

    def test_suffix_match_never_steals_a_sibling_entry(self):
        """A clash-renamed match ("M_test" -> importer's "M_test1") must not
        also claim "M_test2" -- that name is ANOTHER entry's exact target."""

        def build():
            nodes = []
            for obj_name, mat_name in (("objA", "M_test1"), ("objD", "M_test2")):
                obj = cmds.polyCube(name=obj_name)[0]
                mat = cmds.shadingNode("phong", asShader=True, name=mat_name)
                sg = cmds.sets(renderable=True, noSurfaceShader=True,
                               empty=True, name=f"{mat_name}SG")
                cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader",
                                 force=True)
                cmds.sets(obj, forceElement=sg)
                nodes += [obj, mat, sg]
            return nodes

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                # No exact SG match ("M_test1" only) -> suffix path, which must
                # skip "M_test2" (a sibling entry's exact target).
                {"name": "M_test", "fbx_material": "M_test",
                 "objects": ["objA"], "files": [self.tex]},
                {"name": "M_two", "fbx_material": "M_test2",
                 "objects": ["objD"], "files": [self.tex]},
            ],
        }
        _StubbedImport.calls["import_result"] = build
        eng = _StubbedImport()
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        eng.logger.addHandler(_Capture())
        eng.import_scene(self.src, use_cache=False)

        def shape_of(sg_members):
            return {m.split("|")[-1] for m in sg_members}

        self.assertTrue(
            any(s.startswith("objA") for s in
                shape_of(cmds.sets("M_testSG", query=True) or [])),
            "entry M_test should claim the clash-renamed M_test1",
        )
        self.assertTrue(
            any(s.startswith("objD") for s in
                shape_of(cmds.sets("M_twoSG", query=True) or [])),
            "entry M_test2 keeps its own SG",
        )
        # The load-bearing assertion: without the sibling guard, M_test's
        # suffix match empties M_test2SG first and M_two only lands via the
        # object-level RESCUE -- same end state, wrong path. Pin the path.
        self.assertTrue(
            any("Rebuilt material M_two" in m and "shading group(s)" in m
                for m in records),
            f"M_two must swap via the PRIMARY (SG) path, got: {records}",
        )

    def test_suffix_match_never_steals_an_untextured_scene_material(self):
        """An UNTEXTURED .blend sibling ("M_test2", no manifest entry) must not
        be claimed by "M_test"'s clash-rename suffix match: the manifest's
        ``scene_materials`` list marks it as its own real material."""

        def build():
            nodes = []
            # M_test1 = the importer's clash-rename of textured "M_test";
            # M_test2 = an untextured sibling imported under its OWN name.
            for obj_name, mat_name in (("objA", "M_test1"), ("objD", "M_test2")):
                obj = cmds.polyCube(name=obj_name)[0]
                mat = cmds.shadingNode("phong", asShader=True, name=mat_name)
                sg = cmds.sets(renderable=True, noSurfaceShader=True,
                               empty=True, name=f"{mat_name}SG")
                cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader",
                                 force=True)
                cmds.sets(obj, forceElement=sg)
                nodes += [obj, mat, sg]
            return nodes

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                {"name": "M_test", "fbx_material": "M_test",
                 "objects": ["objA"], "files": [self.tex]},
            ],
            # Untextured materials get no entry, but they ARE listed here.
            "scene_materials": ["M_test", "M_test2"],
        }
        _StubbedImport.calls["import_result"] = build
        _StubbedImport().import_scene(self.src, use_cache=False)

        def shapes(sg):
            return {m.split("|")[-1] for m in (cmds.sets(sg, query=True) or [])}

        self.assertTrue(
            any(s.startswith("objA") for s in shapes("M_testSG")),
            "entry M_test should still claim the clash-renamed M_test1",
        )
        self.assertTrue(
            any(s.startswith("objD") for s in shapes("M_test2SG")),
            "untextured M_test2 must keep its own members — not be repainted "
            "with M_test's rebuilt textures",
        )
        self.assertTrue(
            cmds.objExists("M_test2") and cmds.nodeType("M_test2") == "phong",
            "the untextured sibling's FBX-carried material must survive",
        )

    def test_conversion_cache(self):
        _StubbedImport.calls["manifest"] = {"version": 1, "materials": []}
        _StubbedImport.calls["import_result"] = lambda: []

        _StubbedImport().import_scene(self.src)
        self.assertEqual(_StubbedImport.calls["runs"], 1)
        _StubbedImport().import_scene(self.src)
        self.assertEqual(
            _StubbedImport.calls["runs"], 1,
            "second identical import must NOT relaunch Blender",
        )
        _StubbedImport().import_scene(self.src, use_cache=False)
        self.assertEqual(
            _StubbedImport.calls["runs"], 2,
            "use_cache=False must force a fresh conversion",
        )

    def test_failure_keeps_intermediate_fbx(self):
        _StubbedImport.calls["manifest"] = {"version": 1, "materials": []}

        def boom():
            raise RuntimeError("import boom")

        _StubbedImport.calls["import_result"] = boom
        with self.assertRaises(RuntimeError):
            _StubbedImport().import_scene(self.src, use_cache=False)
        kept = _StubbedImport.calls["fbx"]
        self.assertTrue(os.path.exists(kept), "intermediate FBX kept on failure")
        os.remove(kept)
        os.remove(kept + ".manifest.json")

    def test_malformed_manifest_never_aborts(self):
        _StubbedImport.calls["manifest"] = ["not", "a", "dict"]
        _StubbedImport.calls["import_result"] = self._build_imported_scene
        imported = _StubbedImport().import_scene(self.src, use_cache=False)
        self.assertEqual(sorted(imported), ["objA", "objB", "objC"])


class TestSceneImportSurface(unittest.TestCase):
    """Public registration on the mtk root."""

    def test_registered(self):
        import mayatk as mtk

        self.assertIs(mtk.import_blender_scene, import_blender_scene)
        self.assertIs(mtk.BlenderSceneImport, BlenderSceneImport)


if __name__ == "__main__":
    unittest.main(verbosity=2)
