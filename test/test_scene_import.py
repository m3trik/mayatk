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
        self.assertNotIn(
            "_import_scene", {p.stem for p in bb.BlenderBridge.list_templates()}
        )

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
        for flag in (
            "use_mesh_modifiers",
            "use_tspace",
            "use_custom_props",
            "add_leaf_bones",
            "bake_anim",
        ):
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

    def test_tiled_images_resolve_to_a_real_tile(self):
        # A <UDIM>/<UVTILE> token is not an on-disk file: it must resolve to the
        # set's first existing tile (flattened, logged) instead of producing a
        # misleading "packed or needs relinking" file-less entry.
        self.assertIn("_TILE_TOKENS", self.txt)
        self.assertIn("<UDIM>", self.txt)
        self.assertIn("<UVTILE>", self.txt)
        self.assertIn("glob.escape", self.txt)  # paths may hold glob-special chars

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
            r"C:\scenes\test scene.blend",
            r"C:\tmp\out.fbx",
            via="fbx",
            embed_textures=False,
            include_animation=True,
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
        self.assertEqual(
            BlenderSceneImport._fbx_safe_name("dotted.001"), "dottedFBXASC046001"
        )
        self.assertEqual(
            BlenderSceneImport._fbx_safe_name("spa ced"), "spaFBXASC032ced"
        )
        self.assertEqual(BlenderSceneImport._fbx_safe_name("dash-y"), "dashFBXASC045y")
        self.assertEqual(BlenderSceneImport._fbx_safe_name("1digit"), "FBXASC049digit")
        self.assertEqual(BlenderSceneImport._fbx_safe_name("Clean_Name"), "Clean_Name")

    def test_matches_with_clash_suffix(self):
        self.assertTrue(BlenderSceneImport._matches_fbx_name("M_test", "M_test"))
        # Maya's rename-on-clash appends digits.
        self.assertTrue(BlenderSceneImport._matches_fbx_name("M_test1", "M_test"))
        self.assertFalse(BlenderSceneImport._matches_fbx_name("M_test_extra", "M_test"))
        self.assertFalse(BlenderSceneImport._matches_fbx_name("Other", "M_test"))


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
    def _rebuild_material(files, name, slots=None, shader_type="stingray"):
        calls = _StubbedImport.calls
        # ``slots`` and ``shader_type`` are recorded in the SAME tuple, not
        # parallel lists: the manifest's authoritative shader slots are what let
        # a texture whose filename carries no map-type token be rebuilt at all,
        # and the shader type is what the panel's Rebuild Shader choice actually
        # controls. A signature change at this seam fails only at RUNTIME (the
        # applier's per-entry except swallows it), so the call must be asserted
        # whole.
        calls.setdefault("created", []).append(
            (tuple(files), name, slots, shader_type)
        )
        if name == "M_unclass":
            return None  # "nothing classified" -- keep the FBX material
        # Cheap stand-in for the GameShader network: shader + SG, no textures.
        shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        return sg


class TestRestoreEmptyGroups(MayaTkTestCase):
    """Imported parent Empties (FBX nulls -> locators) become plain groups.

    Regression: every Blender Empty arrived as a locator, so a sent/pulled group
    hierarchy read as locators all the way down (live production report). Parent
    Empties are groups; childless ones stay locators (point markers).
    """

    def _locator(self, name, parent=None):
        transform = cmds.spaceLocator(name=name)[0]
        if parent:
            transform = cmds.parent(transform, parent)[0]
        return transform

    def test_parent_locators_become_groups_leaves_stay(self):
        grp = self._locator("grp")
        sub = self._locator("sub", grp)
        leaf = self._locator("leaf_marker", grp)
        cube = cmds.polyCube(name="cubeA")[0]
        cube = cmds.parent(cube, sub)[0]
        new_nodes = cmds.ls("grp", "sub", "leaf_marker", cube, dag=True, long=True)

        stripped = BlenderSceneImport._restore_empty_groups(new_nodes)

        self.assertEqual(stripped, 2)
        self.assertEqual(cmds.listRelatives("grp", shapes=True), None)
        self.assertEqual(cmds.listRelatives("sub", shapes=True), None)
        self.assertEqual(
            cmds.nodeType((cmds.listRelatives("leaf_marker", shapes=True) or [""])[0]),
            "locator",
        )

    def test_out_of_scope_and_multi_shape_locators_untouched(self):
        # A pre-existing user locator outside new_nodes must never be touched...
        keep = self._locator("user_locator")
        cmds.parent(cmds.polyCube(name="kid")[0], keep)
        # ...nor a transform whose locator is not its only shape.
        multi = self._locator("multi")
        cmds.createNode("locator", name="multiShape2", parent=multi)
        cmds.parent(cmds.polyCube(name="kid2")[0], multi)

        stripped = BlenderSceneImport._restore_empty_groups(
            cmds.ls("multi", dag=True, long=True)
        )

        self.assertEqual(stripped, 0)
        self.assertEqual(len(cmds.listRelatives(keep, shapes=True) or []), 1)
        self.assertEqual(len(cmds.listRelatives(multi, shapes=True) or []), 2)


class TestRebuildMaterialShaderType(MayaTkTestCase):
    """_rebuild_material — the panel's shader choice, and its degradation path.

    The fallback fires only where the requested shader CANNOT be built (openPBR
    needs a recent Maya 2025+, Stingray needs the ShaderFX plugin) — i.e. exactly
    the installs this suite never runs on — so a stubbed engine is the only way
    to pin it. Without that, a silent regression here would surface as "the send
    ignored my shader choice" on someone else's machine.
    """

    class _Engine:
        """Stands in for GameShader: records requests, refuses non-default ones."""

        def __init__(self, refuse=("open_pbr", "stingray"), **_):
            self.refuse = refuse
            self.requested = []

        def create_network(self, files, name=None, **kwargs):
            wanted = kwargs.get("shader_type")
            self.requested.append(wanted)
            if wanted in self.refuse:
                raise RuntimeError(f"{wanted} node type unavailable")
            return cmds.shadingNode("standardSurface", asShader=True, name=name)

    def _run(self, engine, **kwargs):
        """Rebuild against the stubbed engine; *kwargs* omitted exercises the default."""
        from unittest import mock

        with mock.patch(
            "mayatk.mat_utils.game_shader.GameShader", return_value=engine
        ):
            si.BlenderSceneImport._rebuild_material(
                ["nonexistent_Base_Color.png"], "M_x", None, **kwargs
            )

    def test_requested_shader_type_reaches_the_engine(self):
        engine = self._Engine(refuse=())
        self._run(engine, shader_type="open_pbr")
        self.assertEqual(engine.requested, ["open_pbr"])

    def test_unavailable_shader_type_retries_as_standard_surface(self):
        engine = self._Engine()
        self._run(engine, shader_type="open_pbr")
        self.assertEqual(engine.requested, ["open_pbr", "standard_surface"])

    def test_standard_surface_failure_is_not_swallowed(self):
        """Only a SHADER-TYPE fallback is tolerated; the default failing is real."""
        engine = self._Engine(refuse=("standard_surface",))
        with self.assertRaises(RuntimeError):
            self._run(engine, shader_type="standard_surface")

    def test_default_is_the_game_shader(self):
        """Stingray by default: these hand-offs feed a game engine, and it is
        the only family that declares its texture slots, so its maps survive
        the trip back out instead of being re-guessed from filenames."""
        engine = self._Engine(refuse=())
        self._run(engine)
        self.assertEqual(engine.requested, ["stingray"])

    def test_unavailable_default_still_degrades_to_standard_surface(self):
        """A Maya without the ShaderFX plugin must not lose the material."""
        engine = self._Engine(refuse=("stingray",))
        self._run(engine)
        self.assertEqual(engine.requested, ["stingray", "standard_surface"])


class TestSceneImportOrchestration(MayaTkTestCase):
    """convert -> import -> manifest rebuild -> cleanup, against real nodes."""

    def setUp(self):
        super().setUp()
        _StubbedImport.calls = {}
        self.src = os.path.join(tempfile.gettempdir(), "mtk_scene_import_src.blend")
        with open(self.src, "wb") as f:
            f.write(b"BLENDER-v500")
        self.tex = os.path.join(tempfile.gettempdir(), "mtk_scene_import_BaseColor.png")
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
        sg_a = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="M_testSG"
        )
        cmds.connectAttr(f"{mat_a}.outColor", f"{sg_a}.surfaceShader", force=True)
        mat_b = cmds.shadingNode("phong", asShader=True, name="M_keep")
        sg_b = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="M_keepSG"
        )
        cmds.connectAttr(f"{mat_b}.outColor", f"{sg_b}.surfaceShader", force=True)
        cmds.sets(f"{cube}.f[0:2]", forceElement=sg_a)
        cmds.sets(f"{cube}.f[3:5]", forceElement=sg_b)

        obj_b = cmds.polyCube(name="objB")[0]
        mat_r = cmds.shadingNode("phong", asShader=True, name="M_renamed_by_importer")
        sg_r = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="M_renamed_by_importerSG",
        )
        cmds.connectAttr(f"{mat_r}.outColor", f"{sg_r}.surfaceShader", force=True)
        cmds.sets(obj_b, forceElement=sg_r)

        obj_c = cmds.polyCube(name="objC")[0]
        return [cube, obj_b, obj_c, mat_a, sg_a, mat_b, sg_b, mat_r, sg_r]

    def test_full_orchestration(self):
        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                # Primary path: SG-level member transfer (per-face preserved).
                {
                    "name": "M_test",
                    "fbx_material": "M_test",
                    "objects": ["objA"],
                    "files": [self.tex],
                    # Authoritative shader slots -- must reach _rebuild_material.
                    "slots": {"baseColor": self.tex},
                },
                # Fallback path: importer renamed the material -> object-level.
                {
                    "name": "M_fb",
                    "fbx_material": "M_nowhere",
                    "objects": ["objB"],
                    "files": [self.tex],
                },
                # All files gone -> named warning, nothing touched.
                {
                    "name": "M_gone",
                    "fbx_material": "M_gone",
                    "objects": ["objC"],
                    "files": ["X:/missing.png"],
                },
            ],
        }
        _StubbedImport.calls["import_result"] = self._build_imported_scene

        imported = _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)

        # Returns the transform subset (behavior parity with blendertk).
        self.assertEqual(sorted(imported), ["objA", "objB", "objC"])

        # Rebuilt from the on-disk file only for entries whose files exist.
        # Asserted WHOLE (files, name, slots, shader_type): the manifest's shader
        # slots must reach the rebuilder, or the one thing that lets an
        # unclassifiably-named texture be rebuilt is silently dropped at the call
        # site; the shader type must too, or the panel's choice is ignored.
        self.assertEqual(
            _StubbedImport.calls["created"],
            [
                ((self.tex,), "M_test", {"baseColor": self.tex}, "stingray"),
                ((self.tex,), "M_fb", None, "stingray"),
            ],
        )

        # Primary swap: faces 0-2 moved to the rebuilt SG; faces 3-5 untouched.
        sg_new = "M_testSG1" if cmds.objExists("M_testSG1") else "M_testSG"
        members = cmds.sets(sg_new, query=True) or []
        self.assertTrue(any("f[0:2]" in m for m in members), members)
        keep_members = cmds.sets("M_keepSG", query=True) or []
        self.assertTrue(any("f[3:5]" in m for m in keep_members), keep_members)
        # The replaced phong (and its emptied SG) purged; the keeper stays.
        self.assertFalse(
            cmds.objExists("M_test") and cmds.nodeType("M_test") == "phong"
        )
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
        self.assertFalse(os.path.exists(_StubbedImport.calls["fbx"] + ".manifest.json"))

    def test_shader_type_choice_reaches_the_rebuilder(self):
        """The panel's Rebuild Shader choice must survive to GameShader.

        The default is asserted by ``test_full_orchestration``; this pins the
        NON-default, which is the half that silently no-ops if the applier drops
        the argument (the per-entry except would hide a signature mismatch).
        """
        nodes = self._build_imported_scene()
        artifacts = ptk.TempArtifacts("mtk_shader_choice", policy="scoped")
        manifest_path = artifacts.path(extension=".json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": [
                        {
                            "name": "M_test",
                            "fbx_material": "M_test",
                            "objects": ["objA"],
                            "files": [self.tex],
                        }
                    ],
                },
                fh,
            )
        try:
            _StubbedImport()._apply_texture_manifest(
                manifest_path, nodes, shader_type="open_pbr"
            )
        finally:
            artifacts.cleanup()

        self.assertEqual(
            [call[3] for call in _StubbedImport.calls.get("created", [])],
            ["open_pbr"],
        )

    def test_rebuilt_material_reclaims_the_source_name(self):
        """The rebuild must not leave the material renamed.

        The network is built while the FBX-carried material still OWNS the
        name, so Maya hands the rebuild "M_test1"; the FBX one is purged a
        moment later and the name is free again. Nothing reclaimed it, so every
        sent material landed suffixed ("MAT_VDATS_instruments1" -- live
        production report). For a Unity-bound asset the material name IS the
        binding, which makes a silent rename a destructive transfer, and the
        digit compounds on every re-send.
        """
        nodes = self._build_imported_scene()
        artifacts = ptk.TempArtifacts("mtk_rebuild_name", policy="scoped")
        manifest_path = artifacts.path(extension=".json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": [
                        {
                            "name": "M_test",
                            "fbx_material": "M_test",
                            "objects": ["objA"],
                            "files": [self.tex],
                        }
                    ],
                },
                fh,
            )
        try:
            _StubbedImport()._apply_texture_manifest(manifest_path, nodes)
        finally:
            artifacts.cleanup()

        self.assertTrue(cmds.objExists("M_test"), cmds.ls(materials=True))
        # The REBUILT network under the original name, not the purged phong.
        self.assertEqual(cmds.nodeType("M_test"), "standardSurface")
        self.assertFalse(cmds.objExists("M_test1"), "clash suffix left behind")
        # The shading group follows its shader so the pair stays legible.
        self.assertTrue(cmds.objExists("M_testSG"), cmds.ls(type="shadingEngine"))
        self.assertFalse(cmds.objExists("M_testSG1"))

    def test_name_reclaim_never_steals_a_live_name(self):
        """A still-assigned FBX material keeps its name; the rebuild yields.

        The object-level fallback runs precisely when the FBX material was NOT
        matched, so it may still be assigned elsewhere -- reclaiming its name
        would either fail or (worse) rename the wrong node.
        """
        nodes = self._build_imported_scene()
        artifacts = ptk.TempArtifacts("mtk_rebuild_live", policy="scoped")
        manifest_path = artifacts.path(extension=".json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": [
                        {
                            # Names the material that objA's OTHER slot still uses.
                            "name": "M_keep",
                            "fbx_material": "no_such_material",
                            "objects": ["objA"],
                            "files": [self.tex],
                        }
                    ],
                },
                fh,
            )
        try:
            _StubbedImport()._apply_texture_manifest(manifest_path, nodes)
        finally:
            artifacts.cleanup()

        # The live phong still owns the name; the rebuild kept its suffix.
        self.assertEqual(cmds.nodeType("M_keep"), "phong")
        self.assertTrue(cmds.objExists("M_keep1"))

    def test_unclassified_entry_keeps_fbx_material(self):
        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                {
                    "name": "M_unclass",
                    "fbx_material": "M_test",
                    "objects": ["objA"],
                    "files": [self.tex],
                },
            ],
        }
        _StubbedImport.calls["import_result"] = self._build_imported_scene
        _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)
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
                sg = cmds.sets(
                    renderable=True,
                    noSurfaceShader=True,
                    empty=True,
                    name=f"{mat_name}SG",
                )
                cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
                cmds.sets(obj, forceElement=sg)
                nodes += [obj, mat, sg]
            return nodes

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                # No exact SG match ("M_test1" only) -> suffix path, which must
                # skip "M_test2" (a sibling entry's exact target).
                {
                    "name": "M_test",
                    "fbx_material": "M_test",
                    "objects": ["objA"],
                    "files": [self.tex],
                },
                {
                    "name": "M_two",
                    "fbx_material": "M_test2",
                    "objects": ["objD"],
                    "files": [self.tex],
                },
            ],
        }
        _StubbedImport.calls["import_result"] = build
        eng = _StubbedImport()
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        eng.logger.addHandler(_Capture())
        eng.import_scene(self.src, via="fbx", use_cache=False)

        def shape_of(sg_members):
            return {m.split("|")[-1] for m in sg_members}

        self.assertTrue(
            any(
                s.startswith("objA")
                for s in shape_of(cmds.sets("M_testSG", query=True) or [])
            ),
            "entry M_test should claim the clash-renamed M_test1",
        )
        self.assertTrue(
            any(
                s.startswith("objD")
                for s in shape_of(cmds.sets("M_twoSG", query=True) or [])
            ),
            "entry M_test2 keeps its own SG",
        )
        # The load-bearing assertion: without the sibling guard, M_test's
        # suffix match empties M_test2SG first and M_two only lands via the
        # object-level RESCUE -- same end state, wrong path. Pin the path.
        self.assertTrue(
            any(
                "Rebuilt material M_two" in m and "shading group(s)" in m
                for m in records
            ),
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
                sg = cmds.sets(
                    renderable=True,
                    noSurfaceShader=True,
                    empty=True,
                    name=f"{mat_name}SG",
                )
                cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
                cmds.sets(obj, forceElement=sg)
                nodes += [obj, mat, sg]
            return nodes

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [
                {
                    "name": "M_test",
                    "fbx_material": "M_test",
                    "objects": ["objA"],
                    "files": [self.tex],
                },
            ],
            # Untextured materials get no entry, but they ARE listed here.
            "scene_materials": ["M_test", "M_test2"],
        }
        _StubbedImport.calls["import_result"] = build
        _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)

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

        _StubbedImport().import_scene(self.src, via="fbx")
        self.assertEqual(_StubbedImport.calls["runs"], 1)
        _StubbedImport().import_scene(self.src, via="fbx")
        self.assertEqual(
            _StubbedImport.calls["runs"],
            1,
            "second identical import must NOT relaunch Blender",
        )
        _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)
        self.assertEqual(
            _StubbedImport.calls["runs"],
            2,
            "use_cache=False must force a fresh conversion",
        )

    def test_failure_keeps_intermediate_fbx(self):
        _StubbedImport.calls["manifest"] = {"version": 1, "materials": []}

        def boom():
            raise RuntimeError("import boom")

        _StubbedImport.calls["import_result"] = boom
        with self.assertRaises(RuntimeError):
            _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)
        kept = _StubbedImport.calls["fbx"]
        self.assertTrue(os.path.exists(kept), "intermediate FBX kept on failure")
        os.remove(kept)
        os.remove(kept + ".manifest.json")

    def test_malformed_manifest_never_aborts(self):
        _StubbedImport.calls["manifest"] = ["not", "a", "dict"]
        _StubbedImport.calls["import_result"] = self._build_imported_scene
        imported = _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)
        self.assertEqual(sorted(imported), ["objA", "objB", "objC"])

    def test_import_fbx_resets_sticky_plugin_state(self):
        """The FBX plugin's import options are global + sticky: whatever the
        user's last interactive import set (verified live: a poisoned mode
        persists across calls) silently shapes cmds.file imports. _import_fbx
        must reset and pin mode to "add" — the factory default "merge" can
        retarget animation onto same-named pre-existing scene nodes."""
        import maya.mel as mel

        cube = cmds.polyCube(name="fbx_state_probe")[0]
        fbx = os.path.join(
            tempfile.gettempdir(), "mtk_scene_import_state_probe.fbx"
        ).replace("\\", "/")
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        cmds.select(cube)
        mel.eval(f'FBXExport -f "{fbx}" -s')
        try:
            mel.eval("FBXImportMode -v exmerge")  # poison: "update animation"
            new_nodes = BlenderSceneImport()._import_fbx(fbx)
            self.assertEqual(mel.eval("FBXImportMode -q"), "add")
            self.assertTrue(new_nodes, "the import must ADD nodes")
        finally:
            os.remove(fbx)


class TestConversionRoutes(unittest.TestCase):
    """FBX is the default pull route; USD stays one kwarg away (via="usd").

    Mirror of blendertk's checks. FBX instancing is carried by the format itself
    on both sides, so nothing stands between a Blender linked duplicate and a real
    Maya instance. The USD route matches it by replaying a recorded grouping --
    guaranteed-or-fail since the v2 sidecar (a failed replay fails the conversion
    atomically; see TestUsdInstanceReplayStrict). FBX stays the default for its
    format-native instancing; the bake template accepts either intermediate
    (dispatch on extension).
    """

    def test_via_defaults_are_fbx(self):
        import inspect

        for fn in (
            BlenderSceneImport.import_scene,
            BlenderSceneImport.bake_scene,
            BlenderSceneImport.render_script,
            BlenderSceneImport.convert,
        ):
            p = inspect.signature(fn).parameters.get("via")
            self.assertIsNotNone(p, fn.__name__)
            self.assertEqual(p.default, "fbx", fn.__name__)

    def test_default_render_is_fbx_template(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        script = eng.render_script(r"C:\s.blend", r"C:\o.fbx")
        self.assertIn("export_scene.fbx", script)
        self.assertNotIn("usd_export", script)
        compile(script, "_import_scene_rendered.py", "exec")

    def test_usd_route_still_renders_the_usd_template(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        script = eng.render_script(r"C:\s.blend", r"C:\o.usd", via="usd")
        self.assertIn("usd_export", script)
        self.assertNotIn("export_scene.fbx", script)
        compile(script, "_import_scene_usd_rendered.py", "exec")

    def test_routes_are_separate_cache_identities(self):
        self.assertNotEqual(
            BlenderSceneImport._cache_key(__file__, {}, "usd"),
            BlenderSceneImport._cache_key(__file__, {}, "fbx"),
        )

    def test_bake_template_is_source_generalized(self):
        txt = si._BAKE_TEMPLATE.read_text()
        self.assertIn("__" + "SRC_FILE" + "__", txt)
        self.assertNotIn("SRC_FBX", txt)
        # USD branch: native mayaUsd translator, no manifest needed.
        self.assertIn("USD Import", txt)
        self.assertIn("mayaUsdPlugin", txt)

    def test_usd_export_frame_range_gated_on_real_animation(self):
        """USD has no animation curves -- export_animation writes a time sample per
        frame per prim, so the range multiplies export cost. The Maya-side mirror of
        this gate measured 234s -> 1.8s on a 755-object static module."""
        txt = (si._TEMPLATE_DIR / "_import_scene_usd.py").read_text()
        self.assertIn("def _narrow_frame_range", txt)
        self.assertIn("INCLUDE_ANIMATION and _narrow_frame_range(bpy)", txt)
        # Constraints/NLA/drivers move things without keys -- range can't be derived.
        self.assertIn("ob.constraints", txt)
        self.assertIn("nla_tracks", txt)

    def test_convert_scrubs_the_ocio_handoff(self):
        """The conversion Blender is launched FROM Maya and inherits its env; an
        OCIO pointing inside Maya's install would override Blender's color
        management. The send path strips it via the spec helper -- so must the
        pull path, through the SAME helper rather than a second copy."""
        import inspect

        src = inspect.getsource(BlenderSceneImport.convert)
        self.assertIn("_SPEC.launch_env()", src)

    def test_usd_route_rebuilds_instances_from_the_sidecar(self):
        """Blender linked duplicates must arrive as real Maya instances.

        The USD export is flat (USD's instancing gives read-only prototypes, not
        Maya's shared-shape model), so the relationship travels in the sidecar
        and is replayed. The FBX route preserves sharing natively, so USD has to
        match it to be a usable alternative -- measured before the fix: 6
        transforms -> 6 independent shapes via USD vs 2 via FBX.
        """
        import inspect

        txt = (si._TEMPLATE_DIR / "_import_scene_usd.py").read_text()
        self.assertIn("def collect_instance_groups", txt)
        self.assertIn('"instances": groups', txt)
        self.assertIn('"use_instancing": False', txt)

        self.assertTrue(hasattr(BlenderSceneImport, "_apply_instance_manifest"))
        src = inspect.getsource(BlenderSceneImport._apply_instance_manifest)
        # Instance the master's shape, drop the follower's own geometry, then
        # re-assign shading (instancing routes it through instObjGroups).
        self.assertIn("add=True, shape=True", src)
        self.assertIn("forceElement", src)
        self.assertIn("_apply_instance_manifest", inspect.getsource(
            BlenderSceneImport.import_scene
        ))
        bake = (si._TEMPLATE_DIR / "_bake_scene.py").read_text()
        self.assertIn("def apply_instances", bake)

    def test_usd_default_export_flattens_instances(self):
        """exportInstances collapses mayaUsd material export (measured: def Material
        3 -> 0, material:binding 4 -> 0), so the interchange default flattens."""
        from mayatk.env_utils.usd import UsdUtils

        self.assertIs(UsdUtils._DEFAULT_EXPORT_OPTIONS["exportInstances"], False)

    def test_bake_render_substitutes_usd_source(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        script = eng.render_bake_script(r"C:\cache\conv.usd", r"C:\cache\conv.ma")
        self.assertIn("C:/cache/conv.usd", script)
        compile(script, "_bake_scene_rendered.py", "exec")


class TestUsdInstanceReplayStrict(MayaTkTestCase):
    """The USD route's instance replay is guaranteed-or-fail (v2 sidecar).

    A silently flattened scene looks correct and only misbehaves when an artist
    edits one "instance" and its siblings don't follow -- the one outcome a
    non-destructive transfer forbids. So the replay either fully rebuilds the
    recorded sharing or the conversion FAILS, atomically: the import happens in
    an isolation namespace (clash-proof name matching) that is deleted wholesale
    on failure and merged to the root on success.
    """

    USD_KW = dict(
        exportInstances=False,
        mergeTransformAndShape=True,
        defaultMeshScheme="none",
        shadingMode="none",
    )

    def _manifest(self, path, groups, version=2, fmt="names"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "format": fmt, "instances": groups}, fh)

    def _stub_engine(self, usd_path):
        """An engine whose Blender conversion is replaced by a prepared payload."""
        from types import SimpleNamespace

        class Stub(BlenderSceneImport):
            def _cached_conversion(self, src, **kw):
                return SimpleNamespace(path=usd_path, scratch=None)

        return Stub()

    def _export_chairs(self, usd_path, names=("Chair_001", "Chair_002")):
        """Author the conversion payload honestly: real cubes through mayaUSDExport."""
        if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        for name in names:
            cmds.polyCube(name=name)
        if os.path.exists(usd_path):
            os.remove(usd_path)
        cmds.mayaUSDExport(file=usd_path, **self.USD_KW)
        cmds.file(new=True, force=True)

    # ---- the replay itself -------------------------------------------------
    def test_replay_rebuilds_shared_shapes_and_per_instance_shading(self):
        for name in ("Chair_001", "Chair_002", "Chair_003"):
            cmds.polyCube(name=name)
        red = cmds.shadingNode("standardSurface", asShader=True, name="strict_red")
        red_sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="strict_redSG"
        )
        cmds.connectAttr(red + ".outColor", red_sg + ".surfaceShader")
        cmds.sets("Chair_002", edit=True, forceElement=red_sg)

        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_replay.manifest.json")
        self._manifest(mpath, [["Chair_001", "Chair_002", "Chair_003"]])
        try:
            nodes = cmds.ls("Chair_00*", long=True) + cmds.ls(
                "Chair_00*", dag=True, shapes=True, long=True
            )
            rebuilt = BlenderSceneImport()._apply_instance_manifest(mpath, nodes)
        finally:
            os.remove(mpath)

        self.assertEqual(rebuilt, 2)
        shapes = cmds.listRelatives("Chair_001", shapes=True, fullPath=True)
        parents = cmds.listRelatives(shapes[0], allParents=True)
        self.assertEqual(len(parents), 3, "one shape shared by all three transforms")
        # The follower's own shader must survive the re-instance (instObjGroups).
        members = [str(m) for m in (cmds.sets(red_sg, query=True) or [])]
        self.assertTrue(
            any("Chair_002" in m for m in members),
            f"per-instance shading lost: {members}",
        )

    def test_replay_raises_on_unmatched_member(self):
        cmds.polyCube(name="Chair_001")
        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_ghost.manifest.json")
        self._manifest(mpath, [["Chair_001", "Ghost_777"]])
        try:
            with self.assertRaises(RuntimeError) as ctx:
                BlenderSceneImport()._apply_instance_manifest(
                    mpath, cmds.ls("Chair_001", long=True)
                )
        finally:
            os.remove(mpath)
        self.assertIn("Ghost_777", str(ctx.exception))

    def test_replay_rejects_stale_manifest_version(self):
        # A v1 sidecar predates sanitized names -- replaying it can silently
        # mismatch, which is exactly what v2 exists to prevent.
        cmds.polyCube(name="Chair_001")
        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_v1.manifest.json")
        self._manifest(mpath, [["Chair_001"]], version=1)
        try:
            with self.assertRaises(RuntimeError):
                BlenderSceneImport()._apply_instance_manifest(
                    mpath, cmds.ls("Chair_001", long=True)
                )
        finally:
            os.remove(mpath)

    # ---- the import leg ----------------------------------------------------
    def test_usd_leg_requires_manifest(self):
        usd = os.path.join(tempfile.gettempdir(), "mtk_strict_nomanifest.usda")
        with open(usd, "w", encoding="utf-8") as fh:
            fh.write("#usda 1.0\n")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._stub_engine(usd).import_scene(
                    "X:/nope/scene.blend", via="usd", use_cache=False
                )
        finally:
            os.remove(usd)
        self.assertIn("manifest", str(ctx.exception).lower())

    def test_usd_leg_rolls_back_on_replay_failure(self):
        usd = os.path.join(tempfile.gettempdir(), "mtk_strict_rollback.usda")
        self._export_chairs(usd)
        mpath = usd + ".manifest.json"
        self._manifest(mpath, [["Chair_001", "Ghost_777"]])
        namespaces_before = set(cmds.namespaceInfo(listOnlyNamespaces=True))
        try:
            with self.assertRaises(RuntimeError):
                self._stub_engine(usd).import_scene(
                    "X:/nope/scene.blend", via="usd", use_cache=False, cleanup=False
                )
            self.assertFalse(
                cmds.ls("Chair_00*", type="transform"),
                "failed replay must remove everything it imported",
            )
            self.assertEqual(
                set(cmds.namespaceInfo(listOnlyNamespaces=True)),
                namespaces_before,
                "the isolation namespace must not survive a failed import",
            )
        finally:
            for p in (usd, mpath):
                if os.path.exists(p):
                    os.remove(p)

    def test_usd_leg_cleans_namespace_when_import_itself_fails(self):
        # Not just the replay: a corrupt payload makes the IMPORT raise after
        # the isolation namespace already exists -- that too must leave the
        # scene exactly as it was.
        usd = os.path.join(tempfile.gettempdir(), "mtk_strict_corrupt.usda")
        with open(usd, "w", encoding="utf-8") as fh:
            fh.write("#usda 1.0\ndef Foo (\n")  # unparsable: unclosed prim spec
        self._manifest(usd + ".manifest.json", [])
        namespaces_before = set(cmds.namespaceInfo(listOnlyNamespaces=True))
        try:
            with self.assertRaises(RuntimeError):
                self._stub_engine(usd).import_scene(
                    "X:/nope/scene.blend", via="usd", use_cache=False, cleanup=False
                )
            self.assertEqual(
                set(cmds.namespaceInfo(listOnlyNamespaces=True)),
                namespaces_before,
                "a failed USD import must not leak the isolation namespace",
            )
        finally:
            for p in (usd, usd + ".manifest.json"):
                if os.path.exists(p):
                    os.remove(p)

    def test_usd_leg_survives_name_clash_and_merges_to_root(self):
        usd = os.path.join(tempfile.gettempdir(), "mtk_strict_clash.usda")
        self._export_chairs(usd)
        mpath = usd + ".manifest.json"
        self._manifest(mpath, [["Chair_001", "Chair_002"]])
        # The scene already holds a node with an incoming name: without namespace
        # isolation Maya renames the incoming one and the replay can't find it.
        clash = cmds.polyCube(name="Chair_001")[0]
        clash_shape = cmds.listRelatives(clash, shapes=True, fullPath=True)[0]
        try:
            imported = self._stub_engine(usd).import_scene(
                "X:/nope/scene.blend", via="usd", use_cache=False, cleanup=False
            )
        finally:
            for p in (usd, mpath):
                if os.path.exists(p):
                    os.remove(p)

        self.assertEqual(len(imported), 2)
        self.assertTrue(
            all(":" not in node for node in imported),
            f"imported nodes must merge back to the root namespace: {imported}",
        )
        # Compare NODE identity, not DAG paths: a properly shared shape shows
        # one path PER instance parent, so paths always count 2 -- the UUID is
        # per node and counts 1 only when the shape is truly shared.
        shared = set()
        for node in imported:
            for shape in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
                shared.update(cmds.ls(shape, uuid=True) or [])
        self.assertEqual(
            len(shared), 1, f"both imported transforms must share ONE shape: {shared}"
        )
        self.assertEqual(
            len(cmds.listRelatives(clash_shape, allParents=True)),
            1,
            "the pre-existing clash node must not be pulled into the instance set",
        )

    # ---- the templates -----------------------------------------------------
    def test_export_template_sidecar_contract(self):
        txt = (si._TEMPLATE_DIR / "_import_scene_usd.py").read_text(encoding="utf-8")
        # The manifest is ALWAYS written (empty groups included) so the Maya side
        # can tell "no instances" from "sidecar lost"...
        self.assertIn('"version": 2', txt)
        self.assertIn("def _sanitize_prim_name", txt)
        # ...and a failed sidecar write withholds the USD artifact -- success is
        # judged by the artifact, so leaving it would report a clean conversion.
        self.assertIn("os.remove(OUT_USD)", txt)

    def test_export_template_sanitizer_matches_blender(self):
        """Pinned against a live Blender 5.1 probe: '.'/' '/':' -> '_', and a
        LEADING DIGIT IS PREFIXED (Blender), not replaced (TfMakeValidIdentifier)."""
        import ast

        txt = (si._TEMPLATE_DIR / "_import_scene_usd.py").read_text(encoding="utf-8")
        tree = ast.parse(txt)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_sanitize_prim_name"
        )
        ns = {"re": __import__("re")}
        exec(  # noqa: S102 -- the template is repo-owned source
            compile(ast.Module(body=[fn], type_ignores=[]), "<template>", "exec"), ns
        )
        sanitize = ns["_sanitize_prim_name"]
        self.assertEqual(sanitize("Chair.001"), "Chair_001")
        self.assertEqual(sanitize("weird name:ok.001"), "weird_name_ok_001")
        self.assertEqual(sanitize("1digit"), "_1digit")
        self.assertEqual(sanitize("a.b"), "a_b")
        self.assertEqual(sanitize(""), "_")

    def test_bake_template_usd_branch_is_loud(self):
        bake = si._BAKE_TEMPLATE.read_text(encoding="utf-8")
        # A USD source whose sidecar cannot be replayed must fail the bake (no
        # artifact -> the parent raises) instead of saving a flattened .ma.
        self.assertIn("raise RuntimeError", bake)
        self.assertNotIn("Instance rebuild failed; shapes stay independent", bake)

    def test_bake_scene_usd_requires_conversion_manifest(self):
        from types import SimpleNamespace

        usd = os.path.join(tempfile.gettempdir(), "mtk_strict_bake.usda")
        with open(usd, "w", encoding="utf-8") as fh:
            fh.write("#usda 1.0\n")
        src = os.path.join(tempfile.gettempdir(), "mtk_strict_bake_src.blend")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("BLENDER")

        baked = {}

        class Stub(BlenderSceneImport):
            def _cached_conversion(self, s, **kw):
                return SimpleNamespace(path=usd, scratch=None)

            @staticmethod
            def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
                baked["ran"] = True
                with open(artifact, "w", encoding="utf-8") as fh:
                    fh.write("//Maya ASCII")
                return ptk.ScriptRunResult(artifact, 0, "stub", 0.1, "stub.py")

            def require_mayapy(self):
                return "stub_mayapy"

        try:
            with self.assertRaises(RuntimeError) as ctx:
                Stub().bake_scene(src, via="usd", use_cache=False)
            self.assertIn("manifest", str(ctx.exception).lower())
            self.assertNotIn("ran", baked, "the bake must not run without the sidecar")

            with open(usd + ".manifest.json", "w", encoding="utf-8") as fh:
                json.dump({"version": 2, "format": "names", "instances": []}, fh)
            out = Stub().bake_scene(src, via="usd", use_cache=False)
            self.assertTrue(baked.get("ran"))
            self.assertTrue(out.endswith(".ma"))
            for p in (out, out + si.BAKE_SOURCE_SUFFIX):
                if os.path.exists(p):
                    os.remove(p)
        finally:
            for p in (usd, usd + ".manifest.json", src):
                if os.path.exists(p):
                    os.remove(p)


class TestSceneImportSurface(unittest.TestCase):
    """Public registration on the mtk root."""

    def test_registered(self):
        import mayatk as mtk

        self.assertIs(mtk.BlenderSceneImport, BlenderSceneImport)


if __name__ == "__main__":
    unittest.main(verbosity=2)
