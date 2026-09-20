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

import ast
import re
import glob
import json
import logging
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

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
        # A hard exit makes the exit code honest (blender --background exits 0 even
        # after a --python script raises): ProcessExit when pythontk imports in the
        # child, os._exit otherwise.
        self.assertIn("_exit(0)", self.txt)
        self.assertIn("_exit(1)", self.txt)
        self.assertIn("ProcessExit.hard_exit(code)", self.txt)
        self.assertIn("os._exit(code)", self.txt)
        self.assertIn("export_scene.fbx", self.txt)

    def test_progress_markers_stream_to_the_parent(self):
        # ProgressRelay marker lines, flushed, so the Maya panel's footer follows the
        # conversion while it runs.
        self.assertIn(
            '"::progress:: {}/{} {}".format(done, total, text), flush=True', self.txt
        )
        self.assertIn('_progress(3, 5, "Writing the FBX")', self.txt)

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

    def test_scene_clock_recorded_and_absolute_timing(self):
        # The manifest's ``scene`` section (fps / ranges / current frame) is what
        # lets the Maya side adopt the source's clock; and the exporter's default
        # per-action stacks are start-ZEROED (a 10-90 clip landed at 0-80 --
        # measured), so both multi-stack modes are pinned off.
        self.assertIn("def scene_settings(bpy)", self.txt)
        self.assertIn('"scene": scene', self.txt)
        self.assertIn('"bake_anim_use_nla_strips": False', self.txt)
        self.assertIn('"bake_anim_use_all_actions": False', self.txt)

    def test_usd_template_records_the_clock_before_narrowing(self):
        # _narrow_frame_range rewrites the scene range to the sampled span before
        # the export; the record must be read BEFORE that, or the manifest carries
        # the narrowed range as the author's.
        txt = si._IMPORT_TEMPLATE_USD.read_text(encoding="utf-8")
        self.assertIn("def scene_settings(bpy)", txt)
        self.assertLess(
            txt.index("scene = scene_settings(bpy)"), txt.index("export_usd(bpy)\n")
        )
        self.assertIn(
            "bpy, scene, materials, scene_materials, scene_data=scene_data, rig=rig",
            txt,
        )

    def test_bake_template_adopts_the_clock_and_reads_usd_animation(self):
        txt = si._BAKE_TEMPLATE.read_text(encoding="utf-8")
        # The clock is adopted by the ONE payload consumer (a bake is a fresh scene).
        self.assertIn("adopt_scene=True", txt)
        self.assertIn("import_payload(", txt)
        # mayaUsd's translator defaults readAnimData OFF: every animated prim
        # baked static (measured). The options literal is pinned in
        # TestUsdPullRouteContracts alongside the other readers.
        self.assertIn("options=USD_IMPORT_OPTIONS,", txt)
        self.assertIn('USD_IMPORT_OPTIONS = "readAnimData=1;', txt)


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


class TestSendReceiversShareOneConsumerCall(unittest.TestCase):
    """The interactive send and the save_as receiver make the SAME consumer call.

    Both run in a Blender mayatk launches and hand the payload to blendertk's
    ``MayaSceneImport.import_payload``; the child can only import what EXTRA_SYS_PATH
    threads in, so the wrapper cannot be factored into a shared module -- it is a
    drift-GUARDED duplicate instead.
    """

    SHARED = ("SEND_FBX_OPTIONS", "_extend_sys_path", "import_payload")

    @staticmethod
    def _top_level(path):
        src = path.read_text(encoding="utf-8")
        out = {}
        for node in ast.parse(src).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                out[node.name] = ast.get_source_segment(src, node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = ast.get_source_segment(src, node)
        return out

    def test_the_consumer_call_is_identical(self):
        send = self._top_level(si._TEMPLATE_DIR / "import.py")
        save = self._top_level(si._TEMPLATE_DIR / "_save_scene.py")
        for name in self.SHARED:
            with self.subTest(name=name):
                self.assertIn(name, send)
                self.assertEqual(send[name], save.get(name))


class TestSceneImportProgress(unittest.TestCase):
    """bake_scene streams both children's progress markers into ONE bar, and a stop
    request ends the run (pure: both headless runs stubbed)."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="mtk_progress_")
        self.src = os.path.join(self.dir, "scene.blend")
        with open(self.src, "wb") as fh:
            fh.write(b"BLENDER")
        self.baked = []

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)
        for path in self.baked:
            for p in (path, path + si.BAKE_SOURCE_SUFFIX):
                if os.path.exists(p):
                    os.remove(p)

    @staticmethod
    def _stub():
        import pythontk as ptk

        class Stub(BlenderSceneImport):
            @staticmethod
            def _run_script(
                app_exe, script_text, *, artifact, timeout, env=None, on_output=None
            ):
                for line in (
                    "a line that is not a marker",
                    "::progress:: 1/2 Opening the scene",
                    "::progress:: 2/2 Writing the FBX",
                ):
                    if on_output is not None and on_output(line) is False:
                        raise ptk.OperationCancelled("stub child stopped")
                with open(artifact, "wb") as fh:
                    fh.write(b"fbx")
                return ptk.ScriptRunResult(artifact, 0, "", 0.1, "s.py")

            @staticmethod
            def _run_bake_script(
                app_exe, script_text, *, artifact, timeout, env=None, on_output=None
            ):
                if on_output is not None:
                    on_output("::progress:: 1/2 Importing the intermediate")
                with open(artifact, "w") as fh:
                    fh.write("//Maya ASCII\n")
                return ptk.ScriptRunResult(artifact, 0, "", 0.1, "s.py")

            def require_blender(self):
                return "stub_blender"

            def require_mayapy(self):
                return "stub_mayapy"

        return Stub()

    def test_both_stages_share_one_bar(self):
        reports = []
        self.baked.append(
            self._stub().bake_scene(
                self.src,
                use_cache=False,
                progress=lambda c, t, m: reports.append((c, t, m)),
            )
        )
        self.assertIn((25, 100, "Blender: Opening the scene"), reports)
        self.assertIn((50, 100, "Blender: Writing the FBX"), reports)
        self.assertIn((75, 100, "Maya: Importing the intermediate"), reports)
        values = [c for c, _, _ in reports if c is not None]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)

    def test_a_false_progress_stops_the_run(self):
        import pythontk as ptk

        with self.assertRaises(ptk.OperationCancelled):
            self._stub().bake_scene(
                self.src, use_cache=False, progress=lambda c, t, m: False
            )

    def test_no_default_timeout(self):
        # A production scene converts for minutes; a fixed budget killed one that was
        # still working. A caller with a UI stops a run through progress instead.
        import inspect

        for fn in (
            BlenderSceneImport.convert,
            BlenderSceneImport.bake,
            BlenderSceneImport.bake_scene,
            BlenderSceneImport.import_scene,
        ):
            with self.subTest(fn=fn.__name__):
                self.assertIsNone(inspect.signature(fn).parameters["timeout"].default)


class TestSceneImportGltfSource(unittest.TestCase):
    """glTF containers as a pull source -- the route Maya has no importer for.

    Maya ships no glTF importer at all, so the headless-Blender round-trip is the
    ONLY way a .glb reaches a Maya scene. What has to hold: the container is opened
    by IMPORT into an EMPTIED factory scene (``--factory-startup`` still loads the
    default cube/camera/light, which would otherwise ride the intermediate into the
    user's scene), its packed images are unpacked to a persistent SOURCE-keyed dir
    (not the conversion scratch, which the cache promotion discards), and the browse
    listing still answers "which files are Blender *scenes*" with .blend alone.
    """

    def setUp(self):
        self.eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _glb(self, name="mtk_scene_import_fixture.glb"):
        path = os.path.join(tempfile.gettempdir(), name)
        with open(path, "wb") as fh:
            fh.write(b"glTF")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    # ------------------------------------------------------------- constants
    def test_gltf_is_convertible_but_not_a_blender_scene(self):
        # SUPPORTED_EXTENSIONS drives find_scenes (the Reference Manager's
        # browse), which asks "is this a Blender SCENE" -- a glTF is a delivery
        # container, so widening that tuple would change an unrelated listing.
        self.assertEqual(si.SUPPORTED_EXTENSIONS, (".blend",))
        self.assertEqual(si.GLTF_EXTENSIONS, (".glb", ".gltf"))
        for ext in si.GLTF_EXTENSIONS:
            self.assertIn(ext, si.CONVERTIBLE_EXTENSIONS)
        self.assertIn(".blend", si.CONVERTIBLE_EXTENSIONS)

    def test_find_scenes_still_lists_blend_only(self):
        root = tempfile.mkdtemp(prefix="mtk_find_scenes_")
        self._dirs.append(root)
        for name in ("a.blend", "b.glb", "c.gltf"):
            open(os.path.join(root, name), "w").close()
        self.assertEqual(
            [os.path.basename(p) for p in self.eng.find_scenes(root)], ["a.blend"]
        )
        # ...but a caller that wants every convertible source can ask for one.
        self.assertEqual(
            sorted(
                os.path.basename(p)
                for p in self.eng.find_scenes(
                    root, extensions=si.CONVERTIBLE_EXTENSIONS
                )
            ),
            ["a.blend", "b.glb", "c.gltf"],
        )

    # ------------------------------------------------------------- validation
    def test_convert_accepts_a_gltf_source(self):
        # Past the extension guard: the failure must now come from the fake
        # Blender, not from a rejected format.
        src = self._glb()
        with self.assertRaises(Exception) as ctx:
            self.eng.convert(src, os.path.join(tempfile.gettempdir(), "o.fbx"))
        self.assertNotIsInstance(ctx.exception, ValueError)

    # ------------------------------------------------------------ texture dir
    def test_texture_dir_is_source_keyed_and_stable(self):
        a, b = self._glb("mtk_tex_key_a.glb"), self._glb("mtk_tex_key_b.glb")
        first = self.eng._texture_dir(a)
        self._dirs += [first]
        # Stable across calls: a cache HIT skips production, so the payload's
        # recorded texture paths must still resolve on the next pull.
        self.assertEqual(first, self.eng._texture_dir(a))
        second = self.eng._texture_dir(b)
        self._dirs.append(second)
        self.assertNotEqual(first, second)
        # Allocated through TempArtifacts (prefix-namespaced + age-swept), never raw.
        self.assertIn("blender_to_mtk_tex", os.path.basename(first))

    def test_is_gltf(self):
        self.assertTrue(self.eng._is_gltf("a.GLB"))
        self.assertTrue(self.eng._is_gltf("a.gltf"))
        self.assertFalse(self.eng._is_gltf("a.blend"))

    # -------------------------------------------------------------- templates
    def test_both_templates_branch_on_the_source_kind(self):
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            txt = template.read_text(encoding="utf-8")
            with self.subTest(template=template.name):
                # The open is routed through the branch, never called directly
                # in main() -- a direct open_mainfile would ignore a glTF.
                self.assertIn("def open_source(bpy):", txt)
                self.assertIn("open_source(bpy)", txt)
                self.assertIn("import_scene.gltf", txt)
                # The default cube/camera/light must not ride along.
                self.assertIn("read_factory_settings(use_empty=True)", txt)
                # A container's images are packed by definition.
                self.assertIn("def _unpack_images(bpy):", txt)
                # Files are named for the SOCKET they feed, not the datablock --
                # a glTF's "Image_0" classifies as nothing on the Maya side.
                self.assertIn("def _classified_names(bpy):", txt)
                self.assertIn("def _map_suffix(sockets):", txt)
                self.assertIn('TEX_DIR = r"__TEX_DIR__"', txt)

    def test_render_carries_the_texture_dir_on_both_routes(self):
        for via, out in (("fbx", "C:/tmp/o.fbx"), ("usd", "C:/tmp/o.usd")):
            with self.subTest(via=via):
                script = self.eng.render_script(
                    r"C:\deliverables\asset.glb",
                    out,
                    via=via,
                    texture_dir=r"C:\tmp\tex_ab12",
                )
                self.assertIn('TEX_DIR = r"C:/tmp/tex_ab12"', script)
                self.assertIn('r"C:/deliverables/asset.glb"', script)
                compile(script, "_import_scene_rendered.py", "exec")

    # --------------------------------------------------- socket -> map naming
    @staticmethod
    def _template_source(template, name):
        """The source text of one top-level definition in *template*."""
        src = template.read_text(encoding="utf-8")
        node = next(
            n
            for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        return ast.get_source_segment(src, node)

    @staticmethod
    def _template_func(template, name, needs=()):
        """Compile ONE function out of a conversion template.

        The templates are Blender-side scripts (they call ``main()`` at import and
        ``os._exit`` on failure), so they can never be imported here -- but the
        naming rules are pure and are the piece most likely to drift, so they are
        lifted out by AST and exercised directly. *needs* names module-level
        assignments the function closes over (e.g. ``_IMAGE_EXTENSIONS``).
        """
        tree = ast.parse(template.read_text(encoding="utf-8"))
        wanted = set(needs)
        body = [
            n
            for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name == name)
            or (
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in wanted for t in n.targets)
            )
        ]
        ns = {"os": os, "re": re}
        exec(compile(ast.Module(body, []), str(template), "exec"), ns)
        return ns[name]

    def test_map_suffix_survives_a_dotted_material_name(self):
        """ "Mat.001" must not have its map suffix eaten as a file extension.

        Blender names every duplicate datablock "<name>.001", so this is the common
        case, not an edge one. Measured before the fix: ``_image_filename`` ran the
        already-built stem "Dotted.001_Base_Color" through ``os.path.splitext``,
        which reads ".001_Base_Color" as the extension -- the file was written
        "Dotted.png", classified as None, and every PBR slot came back empty, which
        is exactly the failure the rename exists to prevent.
        """
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            filename_for = self._template_func(
                template, "_image_filename", needs=("_IMAGE_EXTENSIONS",)
            )
            image = SimpleNamespace(name="Image_0", file_format="PNG")
            with self.subTest(template=template.name):
                name = filename_for(image, set(), "Dotted.001_Base_Color")
                self.assertEqual(name, "Dotted.001_Base_Color.png")
                self.assertEqual(ptk.MapFactory.resolve_map_type(name), "Base_Color")
                # Unclassified images still take the datablock name, and a
                # datablock ".001" there IS the suffix-masquerading-as-extension
                # case the splitext was written for.
                self.assertEqual(filename_for(image, set(), None), "Image_0.png")

    def test_image_filenames_are_unique(self):
        # Two datablocks can sanitize to the same stem; the second must not
        # silently overwrite the first's pixels.
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            filename_for = self._template_func(
                template, "_image_filename", needs=("_IMAGE_EXTENSIONS",)
            )
            image = SimpleNamespace(name="Image_0", file_format="PNG")
            taken = set()
            first = filename_for(image, taken, "Mat_Base_Color")
            second = filename_for(image, taken, "Mat_Base_Color")
            with self.subTest(template=template.name):
                self.assertNotEqual(first, second)

    def test_socket_names_classify_under_the_shared_taxonomy(self):
        """Every name the template can produce must classify, or the rebuild is blind.

        This is the whole point of renaming on unpack: the Maya side picks a map type
        from the FILENAME (ptk.MapFactory's taxonomy), so a glTF's "Image_0" wires
        nothing and the material arrives untextured with no warning. Measured before
        the rename landed: TEX_color_map / TEX_metallic_map / TEX_roughness_map were
        all empty on a real .glb import. A taxonomy rename upstream must fail HERE.
        """
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            suffix_for = self._template_func(template, "_map_suffix")
            cases = {
                frozenset({"Base Color"}): "Base_Color",
                # ANY two of occlusion/roughness/metallic sharing one image is an
                # ORM: its canonical layout (R=AO, G=Roughness, B=Metallic) is
                # exactly glTF's packing, so every subset resolves to it.
                frozenset({"Metallic", "Roughness"}): "ORM",
                frozenset({"Occlusion", "Roughness"}): "ORM",
                frozenset({"Occlusion", "Metallic"}): "ORM",
                frozenset({"Occlusion", "Roughness", "Metallic"}): "ORM",
                frozenset({"Metallic"}): "Metallic",
                frozenset({"Roughness"}): "Roughness",
                # Occlusion alone: a standalone AO texture, which Substance / Maya
                # glTF exporters ship even though Blender's own merges it.
                frozenset({"Occlusion"}): "Ambient_Occlusion",
                frozenset({"Normal"}): "Normal_OpenGL",
                frozenset({"Emission Color"}): "Emissive",
                frozenset({"Alpha"}): "Opacity",
            }
            for sockets, expected in cases.items():
                with self.subTest(template=template.name, sockets=sorted(sockets)):
                    got = suffix_for(set(sockets))
                    self.assertEqual(got, expected)
                    self.assertEqual(
                        ptk.MapFactory.resolve_map_type("Mat_%s.png" % got),
                        expected,
                        "%r no longer classifies -- the template's naming and the "
                        "MapFactory taxonomy have drifted apart" % got,
                    )

    def test_occlusion_is_read_off_the_gltf_output_group(self):
        """A standalone AO texture must not be invisible to the classifier.

        Verified live on Blender 5.1: the glTF importer does NOT wire occlusion into
        the Principled BSDF -- it hangs off a ``glTF Material Output`` node group
        (``inputs=['Occlusion', 'Thickness']``). A walk of Principled inputs alone is
        blind to it, so a separate occlusion map would fall back to its datablock
        name and classify as nothing. Blender's own exporter merges occlusion into
        the roughness image, which is why the first end-to-end run did not catch it.
        """
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            txt = template.read_text(encoding="utf-8")
            with self.subTest(template=template.name):
                self.assertIn('_GLTF_OUTPUT_SOCKETS = ("Occlusion",)', txt)
                self.assertIn('.startswith("glTF")', txt)
                self.assertIn("ShaderNodeGroup", txt)

    def test_packed_textures_are_always_rewritten(self):
        # A conversion only reaches the unpack on a cache MISS, so skipping a write
        # that "looks done" saves nothing and would adopt a truncated file left by a
        # killed run. Pin the absence of that optimisation.
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            body = self._template_source(template, "_unpack_images")
            with self.subTest(template=template.name):
                self.assertIn("image.save()", body)
                self.assertNotIn("os.path.isfile(path)", body)

    def test_unrecognized_socket_falls_back_rather_than_failing(self):
        # An image on no known socket keeps its datablock name and degrades exactly
        # as an oddly-named .blend texture does -- never an aborted conversion.
        for template in (si._IMPORT_TEMPLATE, si._IMPORT_TEMPLATE_USD):
            suffix_for = self._template_func(template, "_map_suffix")
            self.assertIsNone(suffix_for({"Subsurface Weight"}))
            self.assertIsNone(suffix_for(set()))

    def test_render_leaves_the_texture_dir_empty_for_a_blend(self):
        # No unpack for a .blend -- its images already point at files on disk,
        # and _unpack_images no-ops on an empty TEX_DIR.
        script = self.eng.render_script(r"C:\s.blend", "C:/tmp/o.fbx", via="fbx")
        self.assertIn('TEX_DIR = r""', script)
        compile(script, "_import_scene_rendered.py", "exec")


class TestRigModeMayaSide(unittest.TestCase):
    """rig_mode on the Blender -> Maya pull: the mirror of blendertk's seams."""

    def setUp(self):
        self.eng = si.BlenderSceneImport()

    def test_render_emits_rig_mode_and_the_capability_on_both_routes(self):
        for via, ext in (("fbx", ".fbx"), ("usd", ".usd")):
            s = self.eng.render_script(
                "C:/s.blend", "C:/o" + ext, via=via, rig_mode="rig"
            )
            self.assertIn("RIG_MODE = 'rig'", s)
            self.assertIn("RIG_CAPABILITY = ", s)
            self.assertNotIn("RIG_CAPABILITY = ''", s)
            plain = self.eng.render_script(
                "C:/s.blend", "C:/o" + ext, via=via, rig_mode="auto"
            )
            self.assertIn("RIG_CAPABILITY = ''", plain)

    def test_cache_key_carries_rig_mode(self):
        self.assertNotEqual(
            si.BlenderSceneImport._cache_key(__file__, {"rig_mode": "rig"}, "usd"),
            si.BlenderSceneImport._cache_key(__file__, {"rig_mode": "auto"}, "usd"),
        )

    def test_import_scene_and_the_bake_template_share_one_payload_consumer(self):
        import inspect

        self.assertTrue(hasattr(si.BlenderSceneImport, "import_payload"))
        self.assertIn(
            "self.import_payload(",
            inspect.getsource(si.BlenderSceneImport.import_scene),
        )
        bake = (si._TEMPLATE_DIR / "_bake_scene.py").read_text(encoding="utf-8")
        self.assertIn("import_payload(", bake)

    def test_both_blender_side_templates_carry_the_rig_transfer(self):
        for name in ("_import_scene.py", "_import_scene_usd.py"):
            txt = (si._TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("RIG_MODE = __RIG_MODE__", txt, name)
            self.assertIn("RIG_CAPABILITY = __RIG_CAPABILITY__", txt, name)
            self.assertIn("def _transfer_rig", txt, name)

    def test_a_manifest_rig_section_is_built_through_the_builder(self):
        cmds.file(new=True, force=True)
        grp = cmds.group(empty=True, name="rig")
        a = cmds.spaceLocator(name="ctrl_a")[0]
        b = cmds.spaceLocator(name="driven")[0]
        cmds.parent(a, b, grp)
        cmds.addAttr(a, longName="stretch", attributeType="double", keyable=True)
        graph = {
            "version": 1,
            "source": {"app": "blender", "census": {}},
            "policy": {"fallback": "bake"},
            "nodes": [{"id": "/rig/ctrl_a"}, {"id": "/rig/driven"}],
            "records": [
                {
                    "id": "r1",
                    "shape": "channel",
                    "op": "linear",
                    "target": "/rig/driven.scale.y",
                    "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
                    "params": {"scale": 2.0, "offset": 0.0},
                }
            ],
        }
        res = self.eng._apply_rig_section(
            {"rig": {"graph": graph}},
            cmds.ls(long=True, type="transform"),
            is_usd=False,
        )
        self.assertEqual(res["built"], ["r1"])
        cmds.setAttr(a + ".stretch", 3.0)
        self.assertAlmostEqual(cmds.getAttr(b + ".scaleY"), 6.0, places=4)

    def test_a_failed_build_takes_its_whole_rig_component_back(self):
        # r2 targets a node the payload never made, so its build fails -- and
        # r1, a working driver sharing the control, goes with it (`cascaded`):
        # a rig component is all-or-nothing, with or without verify samples.
        cmds.file(new=True, force=True)
        grp = cmds.group(empty=True, name="rig")
        a = cmds.spaceLocator(name="ctrl_a")[0]
        b = cmds.spaceLocator(name="driven")[0]
        cmds.parent(a, b, grp)
        cmds.addAttr(a, longName="stretch", attributeType="double", keyable=True)

        def linear(rid, target):
            return {
                "id": rid,
                "shape": "channel",
                "op": "linear",
                "target": target,
                "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
                "params": {"scale": 2.0, "offset": 0.0},
            }

        graph = {
            "version": 1,
            "source": {"app": "blender", "census": {}},
            "policy": {"fallback": "bake"},
            "nodes": [
                {"id": "/rig/ctrl_a"},
                {"id": "/rig/driven"},
                {"id": "/rig/missing"},
            ],
            "records": [
                linear("r1", "/rig/driven.scale.y"),
                linear("r2", "/rig/missing.scale.x"),
            ],
        }
        res = self.eng._apply_rig_section(
            {"rig": {"graph": graph}},
            cmds.ls(long=True, type="transform"),
            is_usd=False,
        )
        self.assertEqual(res["built"], [])
        kinds = {
            e["record"]: e["kind"]
            for e in res["report"]
            if e["kind"] in ("failed", "cascaded")
        }
        self.assertEqual(kinds, {"r2": "failed", "r1": "cascaded"})
        cmds.setAttr(a + ".stretch", 3.0)
        self.assertNotAlmostEqual(
            cmds.getAttr(b + ".scaleY"), 6.0, places=4
        )  # driver gone


class TestConversionTemplateDrift(unittest.TestCase):
    """The two conversion templates share 18 top-level definitions VERBATIM.

    They are dependency-free Blender-side scripts (no mayatk/pythontk imports are
    available in the child process), so the shared halves -- the texture manifest,
    the scene clock, and the whole glTF open/unpack/naming path -- cannot be
    factored into a common module; the duplicate is structural, not laziness. What
    it must not be is UNGUARDED: a fix applied to one template and not the other
    means the FBX and USD routes silently disagree about the same scene, and the
    route is a per-call argument. This makes the copies a drift-GUARDED duplicate,
    which is the only kind this repo sanctions.

    Only ``collect_empties`` and ``main`` legitimately differ (route-specific), and
    they are named here so adding a third divergence is a deliberate act.
    """

    DIVERGENT = {"collect_empties", "main"}

    @staticmethod
    def _top_level(path):
        """``{name: source}`` for every top-level def/class/assignment."""
        src = path.read_text(encoding="utf-8")
        out = {}
        for node in ast.parse(src).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                out[node.name] = ast.get_source_segment(src, node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = ast.get_source_segment(src, node)
        return out

    def setUp(self):
        self.fbx = self._top_level(si._IMPORT_TEMPLATE)
        self.usd = self._top_level(si._IMPORT_TEMPLATE_USD)
        self.shared = set(self.fbx) & set(self.usd)

    def test_shared_definitions_are_identical(self):
        for name in sorted(self.shared - self.DIVERGENT):
            with self.subTest(name=name):
                self.assertEqual(
                    self.fbx[name],
                    self.usd[name],
                    f"{name!r} has drifted between the FBX and USD conversion "
                    "templates -- fix both, or add it to DIVERGENT with a reason",
                )

    def test_divergent_names_are_still_shared_and_still_differ(self):
        # Guards the guard: if one of these is unified or removed, the exemption
        # is stale and should go, not quietly cover a name it no longer describes.
        for name in sorted(self.DIVERGENT):
            with self.subTest(name=name):
                self.assertIn(name, self.shared)
                self.assertNotEqual(self.fbx[name], self.usd[name])

    def test_the_gltf_path_is_shared(self):
        # The whole point: a .glb must convert identically whichever route it takes.
        for name in (
            "_GLTF_EXTENSIONS",
            "_GLTF_OUTPUT_SOCKETS",
            "_IMAGE_EXTENSIONS",
            "_PBR_SOCKETS",
            "_classified_names",
            "_image_filename",
            "_images_feeding",
            "_import_gltf",
            "_map_suffix",
            "_unpack_images",
            "open_source",
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.shared)


class TestAFailedConversionWithholdsItsArtifact(unittest.TestCase):
    """Success is judged by the artifact, so a failed conversion must leave none.

    Only a failed SIDECAR used to withhold the payload, and only on the USD route.
    An exporter that failed after opening its file -- mayaUSDExport writes a layer
    before it refuses a root-level joint -- left a partial payload that passed as
    the conversion: the non-zero exit was tolerated as a teardown crash, and the
    caller reported a missing sidecar instead of the exporter's own message. On
    the FBX route a failed sidecar left an FBX that imported without it.
    """

    TEMPLATES = {"OUT_FBX": si._IMPORT_TEMPLATE, "OUT_USD": si._IMPORT_TEMPLATE_USD}

    def test_withhold_removes_the_payload_and_its_sidecar(self):
        for template in self.TEMPLATES.values():
            withhold, _ = TestUsdPullRouteContracts._template_function(
                template, "_withhold"
            )
            self.assertIsNotNone(withhold, f"{template.name} lost _withhold")
            withhold.__globals__["os"] = os
            tmp = tempfile.mkdtemp(prefix="mtk_withhold_")
            try:
                payload = os.path.join(tmp, "payload.usd")
                for path in (payload, payload + ".manifest.json"):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write("partial")
                withhold(payload)
                self.assertEqual(os.listdir(tmp), [], template.name)
                withhold(payload)  # nothing left to remove: never raises
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_the_run_handler_withholds_before_it_exits(self):
        """The module-level ``try: main()`` is each template's ONE failure exit:
        it withholds the payload, then exits non-zero."""
        for out, template in self.TEMPLATES.items():
            runs = [
                node
                for node in ast.parse(template.read_text(encoding="utf-8")).body
                if isinstance(node, ast.Try)
                and any(ast.unparse(stmt) == "main()" for stmt in node.body)
            ]
            self.assertEqual(len(runs), 1, template.name)
            calls = [ast.unparse(stmt) for stmt in runs[0].handlers[0].body]
            self.assertIn(f"_withhold({out})", calls, template.name)
            self.assertLess(calls.index(f"_withhold({out})"), calls.index("_exit(1)"))


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
    def _run_script(
        app_exe, script_text, *, artifact, timeout, env=None, on_output=None
    ):
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
        calls.setdefault("created", []).append((tuple(files), name, slots, shader_type))
        if name == "M_unclass":
            return None  # "nothing classified" -- keep the FBX material
        # Cheap stand-in for the GameShader network: shader + SG, no textures.
        shader = cmds.shadingNode("standardSurface", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        return sg


class TestApplySceneManifest(MayaTkTestCase):
    """``_apply_scene_manifest``: manifest record first, the USD stage as fallback."""

    def setUp(self):
        super().setUp()
        self._unit = cmds.currentUnit(q=True, time=True)
        self.tmp = tempfile.mkdtemp(prefix="mtk_scene_manifest_")

    def tearDown(self):
        cmds.currentUnit(time=self._unit)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_manifest_record_applied(self):
        manifest = os.path.join(self.tmp, "x.fbx.manifest.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "scene": {
                        "fps": 30.0,
                        "frame_start": 10,
                        "frame_end": 90,
                        "anim_start": 5,
                        "anim_end": 100,
                        "frame_current": 42,
                    },
                },
                fh,
            )
        got = BlenderSceneImport()._apply_scene_manifest(manifest, None)
        self.assertEqual(got["fps"], 30.0)
        self.assertEqual(cmds.currentUnit(q=True, time=True), "ntsc")
        q = lambda **k: cmds.playbackOptions(q=True, **k)  # noqa: E731
        self.assertEqual(
            (q(min=True), q(max=True), q(ast=True), q(aet=True)),
            (10.0, 90.0, 5.0, 100.0),
        )
        self.assertEqual(cmds.currentTime(q=True), 42.0)

    def test_missing_everything_is_a_silent_noop(self):
        self.assertEqual(BlenderSceneImport()._apply_scene_manifest(None, None), {})
        self.assertEqual(
            BlenderSceneImport()._apply_scene_manifest(
                os.path.join(self.tmp, "nope.json"), None
            ),
            {},
        )

    def test_usd_stage_fallback(self):
        from mayatk.env_utils.usd import UsdUtils

        UsdUtils.load_plugin()
        cmds.currentUnit(time="ntsc")
        cmds.playbackOptions(ast=10, aet=90, min=10, max=90)
        cube = cmds.polyCube(name="clock_cube")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=90, v=1)
        usd = os.path.join(self.tmp, "clock.usda")
        cmds.select(cube)
        cmds.mayaUSDExport(file=usd, selection=True, frameRange=(10, 90))
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="film")
        got = BlenderSceneImport()._apply_scene_manifest(None, usd)
        self.assertEqual(got.get("fps"), 30.0)
        self.assertEqual((got.get("anim_start"), got.get("anim_end")), (10.0, 90.0))
        self.assertEqual(cmds.currentUnit(q=True, time=True), "ntsc")
        self.assertEqual(cmds.playbackOptions(q=True, aet=True), 90.0)


class _ShotsCase(MayaTkTestCase):
    """Shot-store isolation for the tests that rebuild shots from a manifest."""

    def setUp(self):
        super().setUp()
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        ShotStore._auto_export_disabled = False
        self.tmp = tempfile.mkdtemp(prefix="mtk_shots_manifest_")

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.disable_auto_export()
        ShotStore._auto_export_disabled = False
        ShotStore.clear_active()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    @staticmethod
    def _section(objects, keys=None):
        """A ``shots`` section as blendertk's producer writes it (Blender names)."""
        return {
            "version": 1,
            "store": {
                "shots": [
                    {
                        "shot_id": 0,
                        "name": "Intro",
                        "start": 1.0,
                        "end": 24.0,
                        "objects": list(objects),
                        "metadata": {},
                        "locked": False,
                        "description": "",
                    }
                ],
                "hidden_objects": [],
                "pinned_objects": [],
                "markers": [],
                "gap": 0.0,
                "locked_gaps": [],
                "scene_fps": 24.0,
                "snap_whole_frames": True,
            },
            "ledger": {"steps": {}, "keys": keys or {}},
        }

    def _manifest(self, data):
        path = os.path.join(self.tmp, "x.fbx.manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path


class TestApplySceneData(_ShotsCase):
    """``_apply_scene_data``: names through the importer's spelling, scoped to
    the imported nodes, claims only where the receiving curve has a key -- and
    every other portable record through the same engine."""

    def test_names_resolve_through_the_fbx_importer_spelling(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        # "Cube.001" arrives FBXASC-encoded; "Lamp" arrives clash-renamed; a
        # bystander named exactly "Lamp" was already in the scene (not imported).
        dotted = cmds.polyCube(name="CubeFBXASC046001")[0]
        for f in (1, 24):
            cmds.setKeyframe(dotted, attribute="translateX", t=f, v=f)
        cmds.polyCube(name="Lamp")
        clash = cmds.polyCube(name="Lamp1")[0]
        path = self._manifest(
            {
                "version": 1,
                "shots": self._section(
                    ["Cube.001", "Lamp", "Ghost"],
                    keys={
                        "Cube.001": {"translateX": [[24.0, 0, "end"], [7.0, 0, "end"]]}
                    },
                ),
            }
        )
        BlenderSceneImport()._apply_scene_data(path, [dotted, clash])
        store = ShotStore.active()
        self.assertEqual(len(store.shots), 1)
        self.assertEqual(store.shots[0].objects, cmds.ls([dotted, clash], long=True))
        curve = cmds.listConnections(f"{dotted}.translateX", type="animCurve")[0]
        self.assertEqual(store.edit_ledger.key_times(curve), [24.0])

    def test_a_root_swaps_its_up_axis_channels_a_child_does_not(self):
        """Both exporters put ROOT objects through the Z-up -> Y-up crossing
        (measured on the FBX and USD pulls): a Blender root's ``location[2]``
        claim names Maya's translateY; a child's names translateZ."""
        from mayatk.anim_utils.shots._shots import ShotStore

        root = cmds.polyCube(name="Root")[0]
        cmds.setKeyframe(root, attribute="translateY", t=24, v=1)
        child = cmds.polyCube(name="Child")[0]
        cmds.parent(child, root)
        child = cmds.ls("Root|Child", long=True)[0]
        cmds.setKeyframe(child, attribute="translateZ", t=24, v=1)
        path = self._manifest(
            {
                "version": 1,
                "shots": self._section(
                    ["Root", "Child"],
                    keys={
                        "Root": {"translateZ": [[24.0, 0, "end"]]},
                        "Child": {"translateZ": [[24.0, 0, "end"]]},
                    },
                ),
            }
        )
        BlenderSceneImport()._apply_scene_data(path, [root, child])
        led = ShotStore.active().edit_ledger
        root_ty = cmds.listConnections(f"{root}.translateY", type="animCurve")[0]
        child_tz = cmds.listConnections(f"{child}.translateZ", type="animCurve")[0]
        self.assertEqual(led.key_times(root_ty), [24.0])
        self.assertEqual(led.key_times(child_tz), [24.0])
        self.assertEqual(led.curves, {root_ty, child_tz})

    def test_usd_names_resolve_through_the_prim_spelling(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        chair = cmds.polyCube(name="Chair_001")[0]
        path = self._manifest({"version": 2, "shots": self._section(["Chair.001"])})
        BlenderSceneImport()._apply_scene_data(path, [chair], carrier="usd")
        self.assertEqual(ShotStore.active().shots[0].objects, cmds.ls(chair, long=True))

    def test_an_emissive_group_lands_through_the_importer_spelling(self):
        """Every portable record rides the generic ``records`` section; its owner
        resolves members exactly as the shots do."""
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        dotted = cmds.polyCube(name="CubeFBXASC046001")[0]
        path = self._manifest(
            {
                "version": 2,
                "records": {
                    "emissive_groups": {
                        "registry": {
                            "schema": 1,
                            "groups": {"glow": {"slot": 0, "default": 1.0}},
                        },
                        "members": {"glow": {"Cube.001": [0, 2]}},
                        "faces": {"Cube.001": 6},
                    }
                },
            }
        )
        ctx = BlenderSceneImport()._apply_scene_data(path, [dotted])
        self.assertEqual(EmissiveGroups.list_groups()["glow"]["faces"], 2, ctx.notes)

    def test_the_earlier_name_still_lands_and_counts(self):
        """``_apply_shots_manifest`` stays for the templates of an earlier release
        that call it across the package boundary."""
        chair = cmds.polyCube(name="Chair_001")[0]
        path = self._manifest({"version": 2, "shots": self._section(["Chair.001"])})
        self.assertEqual(
            BlenderSceneImport()._apply_shots_manifest(path, [chair], carrier="usd"), 1
        )

    def test_nothing_to_apply_is_a_silent_zero(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        engine = BlenderSceneImport()
        self.assertEqual(engine._apply_shots_manifest(None, []), 0)
        self.assertEqual(
            engine._apply_shots_manifest(os.path.join(self.tmp, "nope.json"), []), 0
        )
        self.assertEqual(
            engine._apply_shots_manifest(self._manifest({"version": 1}), []), 0
        )
        self.assertEqual(ShotStore.active().shots, [])


class TestSceneImportShots(_ShotsCase):
    """import_scene rebuilds the conversion's shots, unless told not to."""

    def setUp(self):
        super().setUp()
        _StubbedImport.calls = {}
        self.src = os.path.join(self.tmp, "with_shots.blend")
        with open(self.src, "wb") as f:
            f.write(b"BLENDER-v500")

    def tearDown(self):
        for stale in glob.glob(
            os.path.join(tempfile.gettempdir(), "blender_to_mtk_cache_*")
        ):
            os.remove(stale)
        super().tearDown()

    def _keyed_import(self):
        cube = cmds.polyCube(name="objS")[0]
        for f in (1, 24):
            cmds.setKeyframe(cube, attribute="translateX", t=f, v=f)
        return [cube]

    def test_shots_are_rebuilt_from_the_sidecar(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [],
            "shots": self._section(
                ["objS"], keys={"objS": {"translateX": [[24.0, 0, "end"]]}}
            ),
        }
        _StubbedImport.calls["import_result"] = self._keyed_import
        imported = _StubbedImport().import_scene(self.src, via="fbx", use_cache=False)
        store = ShotStore.active()
        self.assertEqual(
            [(s.name, s.objects) for s in store.shots],
            [("Intro", cmds.ls(imported, long=True))],
        )
        curve = cmds.listConnections("objS.translateX", type="animCurve")[0]
        self.assertEqual(store.edit_ledger.key_times(curve), [24.0])

    def test_the_caller_can_decline_the_shots(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        _StubbedImport.calls["manifest"] = {
            "version": 1,
            "materials": [],
            "shots": self._section(["objS"]),
        }
        _StubbedImport.calls["import_result"] = self._keyed_import
        _StubbedImport().import_scene(self.src, via="fbx", use_cache=False, shots=False)
        self.assertEqual(ShotStore.active().shots, [])


class TestConversionTemplateShots(unittest.TestCase):
    """The Blender-side conversion templates carry the scene's records -- its
    shots, its emissive groups -- through blendertk."""

    def test_render_threads_the_toolkit_roots_for_the_scene_data_pass(self):
        eng = BlenderSceneImport(blender_path="X:/fake/blender.exe")
        for via in ("fbx", "usd"):
            with self.subTest(via=via):
                script = eng.render_script(r"C:\scenes\s.blend", r"C:\tmp\out", via=via)
                self.assertNotIn("__EXTRA_SYS_PATH__", script)
                roots = ptk.HandoffBridge.import_roots("blendertk", "pythontk")
                self.assertIn(f"EXTRA_SYS_PATH = {roots!r}", script)
                self.assertIn("def scene_data_sections(bpy, spell):", script)
                self.assertIn("scene_data=scene_data", script)
                compile(script, f"_import_scene_{via}_rendered.py", "exec")


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
        self._locator("leaf_marker", grp)
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

        with mock.patch("mayatk.mat_utils.game_shader.GameShader", return_value=engine):
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

    def test_usd_carrier_never_assigns_by_object_name(self):
        """Off a USD layer the bindings are exact per prim path; an entry whose
        shading group is not found is dropped, never rescued by short object
        name (ambiguous across hierarchies -- production, 2026-08-22)."""
        nodes = self._build_imported_scene()
        artifacts = ptk.TempArtifacts("mtk_usd_identity", policy="scoped")
        manifest_path = artifacts.path(extension=".json")
        entry = {
            "name": "M_orphan",
            "fbx_material": "M_not_imported",
            "objects": ["objB"],
            "files": [self.tex],
        }
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "materials": [entry]}, fh)
        try:
            _StubbedImport()._apply_texture_manifest(
                manifest_path, nodes, carrier="usd"
            )
            usd_sgs = set(cmds.listConnections("objBShape", type="shadingEngine") or [])
            _StubbedImport()._apply_texture_manifest(
                manifest_path, nodes, carrier="fbx"
            )
            fbx_sgs = set(cmds.listConnections("objBShape", type="shadingEngine") or [])
        finally:
            artifacts.cleanup()
        self.assertNotIn("M_orphanSG", usd_sgs, usd_sgs)
        self.assertIn("M_orphanSG", fbx_sgs, fbx_sgs)  # the FBX rescue still works

    def test_rebuilt_material_reclaims_the_source_name(self):
        """The rebuild must not leave the material renamed.

        The network is built while the FBX-carried material still OWNS the
        name, so Maya hands the rebuild "M_test1"; the FBX one is purged a
        moment later and the name is free again. Nothing reclaimed it, so every
        sent material landed suffixed ("MAT_PROPS_instruments1" -- live
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
        self.assertIn(
            "_apply_instance_manifest",
            inspect.getsource(BlenderSceneImport.import_payload),
        )
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
                # Stands in for ptk.CachedArtifact.Result: `hit` included because
                # import_scene reports whether the conversion was reused.
                return SimpleNamespace(path=usd_path, scratch=None, hit=False)

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

    def test_replay_rejects_a_foreign_spelling(self):
        # The gate is the SPELLING, not the version. A "paths" sidecar was
        # written by a MAYA producer, whose members are DAG paths; replaying it
        # here -- where the members are Blender names -- matches nothing, and a
        # silently flat scene only betrays itself when an artist edits one
        # "instance" and its siblings do not follow.
        cmds.polyCube(name="Chair_001")
        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_spelling.manifest.json")
        self._manifest(mpath, [["Chair_001"]], fmt="paths")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                BlenderSceneImport()._apply_instance_manifest(
                    mpath, cmds.ls("Chair_001", long=True)
                )
            self.assertIn("paths", str(ctx.exception))
        finally:
            os.remove(mpath)

    def test_replay_rejects_a_sidecar_that_spells_nothing(self):
        # No `format` at all: pre-2026-09-17 documents, and anything hand-rolled.
        cmds.polyCube(name="Chair_001")
        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_nofmt.manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "instances": [["Chair_001"]]}, fh)
        try:
            with self.assertRaises(RuntimeError):
                BlenderSceneImport()._apply_instance_manifest(
                    mpath, cmds.ls("Chair_001", long=True)
                )
        finally:
            os.remove(mpath)

    def test_the_version_alone_never_refuses_a_readable_sidecar(self):
        # The half of the 2026-09-17 decision that is easy to lose: `version`
        # names the SCHEMA now, so it must not quietly become a dialect gate
        # again. A document whose spelling this reader understands is replayed
        # whatever number it carries.
        for name in ("Chair_001", "Chair_002"):
            cmds.polyCube(name=name)
        mpath = os.path.join(tempfile.gettempdir(), "mtk_strict_oldver.manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "format": "names",
                    "instances": [["Chair_001", "Chair_002"]],
                },
                fh,
            )
        try:
            # A group needs two members to mean anything, so this is a real
            # replay that has to SUCCEED -- a refusal would be the old gate back.
            nodes = cmds.ls("Chair_00*", long=True) + cmds.ls(
                "Chair_00*", dag=True, shapes=True, long=True
            )
            BlenderSceneImport()._apply_instance_manifest(mpath, nodes)
        finally:
            os.remove(mpath)

    def test_every_producer_writes_the_one_document_version(self):
        """No route may reintroduce a per-carrier version number.

        Until 2026-09-17 the FBX route wrote 1 and the USD route 2 although the
        schema was identical, so `version` named the carrier and a real schema
        change had no number to turn. This fails the moment a producer invents
        its own again.
        """
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        producers = [
            os.path.join(here, "mayatk", "env_utils", "blender_bridge", p)
            for p in (
                os.path.join("templates", "_import_scene.py"),
                os.path.join("templates", "_import_scene_usd.py"),
                "_blender_bridge.py",
            )
        ]
        expected = str(ptk.HandoffManifest.VERSION)
        for path in producers:
            self.assertTrue(os.path.isfile(path), path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            found = re.findall(r'"version":\s*(\d+)', text)
            # A producer building through ``HandoffManifest`` writes the class's
            # own number (``VERSION_KEY: HandoffManifest.VERSION``) -- it cannot
            # drift, so the spelling counts as writing the version.
            symbolic = re.search(r"VERSION_KEY\s*:\s*[\w.]*\bVERSION\b", text)
            self.assertTrue(
                found or symbolic, f"{os.path.basename(path)} writes no version"
            )
            for value in found:
                self.assertEqual(
                    value,
                    expected,
                    f"{os.path.basename(path)} writes version {value}, not the "
                    f"one HandoffManifest.VERSION declares ({expected})",
                )

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
        # ...and a failed sidecar withholds the USD -- for any failure, on both
        # routes: see TestAFailedConversionWithholdsItsArtifact.

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
                # Stands in for ptk.CachedArtifact.Result: `hit` included because
                # bake_scene reports whether the conversion was reused.
                return SimpleNamespace(path=usd, scratch=None, hit=False)

            @staticmethod
            def _run_bake_script(
                app_exe, script_text, *, artifact, timeout, env=None, on_output=None
            ):
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


class TestUsdPullRouteContracts(unittest.TestCase):
    """The USD pull route's conversion template + engine branch, pinned as text
    and as behavior: animated meshes survive (fold), Maya reads the layer at
    Maya scale (centimeters), and Empties get their node types back."""

    TEMPLATE = si._TEMPLATE_DIR / "_import_scene_usd.py"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mtk_usd_pull_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_template_exports_y_up_cm_with_the_hidden_set_invisible(self):
        """Production pull 2026-08-22: the .blend landed in Maya rotated +90 X
        (Z-up stage; mayaUsd converts nothing on import) with its hidden
        bake-source set VISIBLE (Blender's exporter skips hidden objects, so
        they were never in the layer). The template converts to Y-up, reveals
        the hidden set for the exporter and stamps its prims invisible."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        for line in (
            '"convert_orientation": True',
            '"export_global_forward_selection": "NEGATIVE_Z"',
            '"export_global_up_selection": "Y"',
            '"convert_scene_units": "CENTIMETERS"',
            "hidden = hidden_objects(bpy)",
            "obj.hide_render = False",  # the exporter's RENDER evaluation skips these
            "mark_invisible(OUT_USD, hidden)",
        ):
            self.assertIn(line, text, line)
        # Every Maya-side reader of a Blender layer: Blender's render-active
        # ``st`` lands as ``map1`` (one literal, pinned in test_usd too).
        bake = si._BAKE_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            'USD_IMPORT_OPTIONS = "readAnimData=1;remapUVSetsTo=[[st,map1]]"', bake
        )
        self.assertIn("options=USD_IMPORT_OPTIONS,", bake)
        # ...and every Blender-side receiver of a Maya layer imports every prim,
        # hidden ones hidden, map1 render-active: through blendertk's consumer
        # (UsdUtils.import_scene inside it), its bare fallback still every prim.
        for name in ("import.py", "_save_scene.py"):
            receiver = (si._TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn(".import_payload(", receiver, name)
            self.assertIn("import_visible_only=False", receiver, name)
            self.assertNotIn("UsdUtils.import_usd(", receiver, name)

    def test_template_mark_invisible_stamps_the_exported_prims(self):
        """The copy of ``btk.UsdUtils.mark_invisible`` (with its path spelling
        helpers) finds each hidden object's prim by the exporter's path --
        the collision suffix sanitized -- and leaves a missing prim alone."""
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        from types import SimpleNamespace

        ns = {}
        for name in ("sanitize_prim_name", "export_prim_path", "mark_invisible"):
            fn, _ = self._template_function(self.TEMPLATE, name)
            self.assertIsNotNone(fn, f"template lost {name}")
            ns[name] = fn
        # the copies call each other by bare name: bind them into one namespace
        import re

        for fn in ns.values():
            fn.__globals__.update(ns, re=re)

        grp = SimpleNamespace(name="grp", parent=None)
        hidden = SimpleNamespace(name="part.001", parent=grp)
        stranger = SimpleNamespace(name="ghost", parent=None)
        self.assertEqual(ns["export_prim_path"](hidden), "/grp/part_001")
        self.assertEqual(ns["export_prim_path"](hidden, "/root"), "/root/grp/part_001")

        path = os.path.join(self.tmp, "mark.usda")
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.Xform.Define(stage, "/grp")
        UsdGeom.Mesh.Define(stage, "/grp/part_001")
        UsdGeom.Mesh.Define(stage, "/grp/visible")
        stage.GetRootLayer().Save()
        del stage

        self.assertEqual(ns["mark_invisible"](path, [hidden, stranger]), 1)
        stage = Usd.Stage.Open(path)

        def vis(p):
            return UsdGeom.Imageable(stage.GetPrimAtPath(p)).GetVisibilityAttr().Get()

        self.assertEqual(vis("/grp/part_001"), "invisible")
        self.assertEqual(vis("/grp/visible"), "inherited")

    def test_template_exports_unmerged_when_animated_and_folds_back(self):
        """Blender 5.1 drops an animated object's Mesh when merge_parent_xform and
        export_animation are both on -- the template must fold, not trust."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("def fold_single_mesh_xforms", text)
        self.assertIn("fold_single_mesh_xforms(OUT_USD)", text)
        self.assertIn('kwargs["merge_parent_xform"] = False', text)

    def test_template_writes_centimeters_for_maya(self):
        """mayaUsd 0.30 has no unit conversion on import (probed): a Maya-bound
        layer is written in cm, landing exactly as the FBX route does."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('"convert_scene_units": "CENTIMETERS"', text)

    def test_template_records_empties_for_the_locator_repair(self):
        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("def collect_empties", text)
        self.assertIn('"empties": collect_empties(bpy)', text)

    def test_template_ships_the_texture_manifest_and_maya_replays_it(self):
        """Blender's USD exporter writes only Principled-direct images (a packed
        ORM through SeparateColor exports as nothing) and Maya's pipeline wants
        the SHADER_TYPE rebuild, not usdPreviewSurface -- so the FBX route's
        manifest rides the USD and the USD branch replays it (live-verified:
        ORM split + bump normal identical to the FBX leg)."""
        import ast
        import inspect

        text = self.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "materials, scene_materials = collect_texture_manifest(bpy)", text
        )
        self.assertIn('"materials": materials or []', text)
        # the collectors are the FBX template's copies -- AST-identical
        fbx_text = (si._TEMPLATE_DIR / "_import_scene.py").read_text(encoding="utf-8")

        def dump(src, name):
            fn = self._template_function_node(src, name)
            self.assertIsNotNone(fn, name)
            fn.body = [n for n in fn.body if not isinstance(n, ast.Expr)]
            return ast.dump(fn)

        for name in (
            "_resolved_image_file",
            "_material_files",
            "collect_texture_manifest",
        ):
            self.assertEqual(dump(text, name), dump(fbx_text, name), name)
        src = inspect.getsource(BlenderSceneImport.import_payload)
        self.assertIn('carrier="usd"', src)
        self.assertIn("_convert_usd_preview_shaders(merged)", src)
        # The bake runs the SAME consumer, spelled by route.
        bake = (si._TEMPLATE_DIR / "_bake_scene.py").read_text(encoding="utf-8")
        self.assertIn('via="usd" if usd else "fbx"', bake)
        self.assertIn("import_payload(", bake)

    @staticmethod
    def _template_function_node(text, name):
        import ast

        return next(
            (
                n
                for n in ast.walk(ast.parse(text))
                if isinstance(n, ast.FunctionDef) and n.name == name
            ),
            None,
        )

    def test_engine_usd_branch_restores_locators(self):
        import inspect

        src = inspect.getsource(BlenderSceneImport.import_payload)
        self.assertIn("_restore_usd_locators(new_nodes, manifest_path)", src)

    # -- drift guards for the dependency-free copies -----------------------------
    # The conversion template cannot import blendertk (the target machine's
    # Blender may not have it), so it carries copies. CODE_STANDARD §6: a copy
    # is drift-guarded by a test, never just "kept in step by hand".

    @staticmethod
    def _template_function(path, name):
        """The named top-level function of a template, compiled on its own."""
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name
            ),
            None,
        )
        if fn is None:
            return None, None
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), ns)
        return ns[name], fn

    def test_template_fold_matches_the_engine_s_behavior(self):
        """The copy folds exactly what ``btk.UsdUtils.fold_single_mesh_xforms`` folds:
        an Xform whose only child is a Mesh (the mesh named like its object, under
        a parent) becomes one Mesh prim carrying the Xform's ops and time samples;
        a Mesh with ops of its own and a multi-child Xform are left alone."""
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        fold, _ = self._template_function(self.TEMPLATE, "fold_single_mesh_xforms")
        self.assertIsNotNone(fold, "template lost fold_single_mesh_xforms")

        path = os.path.join(self.tmp, "fold_probe.usda")
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.Xform.Define(stage, "/grp")
        mover = UsdGeom.Xform.Define(stage, "/grp/mover")
        op = mover.AddTranslateOp()
        op.Set((0.0, 0.0, 0.0), 1.0)
        op.Set((5.0, 0.0, 0.0), 10.0)
        UsdGeom.Mesh.Define(
            stage, "/grp/mover/mover"
        )  # datablock named like the object
        UsdGeom.Xform.Define(stage, "/grp/keep")  # two children: not a pair
        UsdGeom.Mesh.Define(stage, "/grp/keep/a")
        UsdGeom.Mesh.Define(stage, "/grp/keep/b")
        UsdGeom.Xform.Define(stage, "/grp/own")  # the mesh carries its own ops
        own_mesh = UsdGeom.Mesh.Define(stage, "/grp/own/m")
        own_mesh.AddScaleOp().Set((2.0, 2.0, 2.0))
        stage.GetRootLayer().Save()

        self.assertEqual(fold(path), 1)
        stage = Usd.Stage.Open(path)
        types = {str(p.GetPath()): p.GetTypeName() for p in stage.Traverse()}
        self.assertEqual(types.get("/grp/mover"), "Mesh")
        self.assertNotIn("/grp/mover/mover", types)
        self.assertEqual(types.get("/grp/keep"), "Xform")
        self.assertEqual(types.get("/grp/own/m"), "Mesh")
        translate = stage.GetPrimAtPath("/grp/mover").GetAttribute("xformOp:translate")
        self.assertEqual(translate.GetNumTimeSamples(), 2)
        self.assertEqual(tuple(translate.Get(10.0)), (5.0, 0.0, 0.0))
        self.assertEqual(fold(path), 0)  # idempotent

    def test_template_pin_primvar_indices_matches_the_engine_s_behavior(self):
        """The copy moves a lone time-sampled index array down to the DEFAULT, and
        leaves alone a primvar that already authors one.

        Blender 5.1 writes a skinned mesh's UVs values-at-default /
        indices-at-a-sample, and mayaUsd reads indices at the default -- so the
        mesh arrives with its UV coordinates and not one assigned face."""
        try:
            from pxr import Sdf, Usd, UsdGeom, Vt
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        pin, _ = self._template_function(self.TEMPLATE, "pin_primvar_indices")
        self.assertIsNotNone(pin, "template lost pin_primvar_indices")

        path = os.path.join(self.tmp, "pin_probe.usda")
        stage = Usd.Stage.CreateNew(path)
        mesh = UsdGeom.Mesh.Define(stage, "/split")
        api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
        split = api.CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying"
        )
        split.Set([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
        split.SetIndices(Vt.IntArray([0, 1, 2, 2, 1, 0]), 0.0)  # a SAMPLE, no default
        whole = UsdGeom.PrimvarsAPI(UsdGeom.Mesh.Define(stage, "/intact").GetPrim())
        keep = whole.CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying"
        )
        keep.Set([(0.0, 0.0), (1.0, 0.0)])
        keep.SetIndices([1, 0])  # already readable at the default
        # A primvar whose indices GENUINELY vary: pinning one sample would
        # publish that frame's mapping as the answer for every other frame.
        moving = UsdGeom.PrimvarsAPI(UsdGeom.Mesh.Define(stage, "/animated").GetPrim())
        vary = moving.CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying"
        )
        vary.Set([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
        vary.SetIndices(Vt.IntArray([0, 1, 2]), 0.0)
        vary.SetIndices(Vt.IntArray([2, 1, 0]), 10.0)
        stage.GetRootLayer().Save()

        self.assertEqual(pin(path), 1)  # only the split one
        stage = Usd.Stage.Open(path)
        fixed = UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/split")).GetPrimvar("st")
        self.assertEqual(list(fixed.GetIndices()), [0, 1, 2, 2, 1, 0])
        untouched = UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/intact")).GetPrimvar("st")
        self.assertEqual(list(untouched.GetIndices()), [1, 0])
        left = UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/animated")).GetPrimvar("st")
        self.assertIsNone(
            left.GetIndicesAttr().Get(),
            "a genuinely time-varying primvar was pinned to one frame's mapping",
        )
        self.assertEqual(left.GetIndicesAttr().GetNumTimeSamples(), 2, "samples lost")
        self.assertEqual(pin(path), 0)  # idempotent

    def test_template_mark_skinning_methods_matches_the_engine_s_behavior(self):
        """The copy stamps ``skinningMethod`` from Preserve Volume, both ways, and
        skips a mesh with no Armature modifier.

        mayaUsd writes this attribute but never reads it back, and Blender writes
        it for nothing -- so without the stamp a dual-quaternion skin returns
        deforming linearly."""
        try:
            from pxr import Usd, UsdGeom, UsdSkel
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        mark, _ = self._template_function(self.TEMPLATE, "mark_skinning_methods")
        self.assertIsNotNone(mark, "template lost mark_skinning_methods")
        # The copy calls its sibling path helpers. `_template_function` compiles
        # each in its OWN namespace, so every one has to be given what IT calls.
        helpers = {
            name: self._template_function(self.TEMPLATE, name)[0]
            for name in ("export_prim_path", "sanitize_prim_name")
        }
        for fn in (mark, *helpers.values()):
            fn.__globals__.update(helpers)
            fn.__globals__["re"] = __import__("re")

        path = os.path.join(self.tmp, "skin_probe.usda")
        stage = Usd.Stage.CreateNew(path)
        for name in ("dq", "lin", "plain"):
            UsdGeom.Mesh.Define(stage, "/" + name)
        stage.GetRootLayer().Save()

        class _Mod:
            def __init__(self, preserve):
                self.type = "ARMATURE"
                self.use_deform_preserve_volume = preserve

        class _Obj:
            def __init__(self, name, mods):
                self.name, self.type, self.parent, self.modifiers = (
                    name,
                    "MESH",
                    None,
                    mods,
                )

        class _Bpy:  # only `bpy.data.objects` is touched
            data = type("_D", (), {"objects": []})()

        bpy = _Bpy()
        bpy.data.objects = [
            _Obj("dq", [_Mod(True)]),
            _Obj("lin", [_Mod(False)]),
            _Obj("plain", []),  # no armature: not a skin, not stamped
        ]
        self.assertEqual(mark(bpy, path), 2)
        stage = Usd.Stage.Open(path)

        def method(prim_path):
            attr = UsdSkel.BindingAPI(
                stage.GetPrimAtPath(prim_path)
            ).GetSkinningMethodAttr()
            return str(attr.Get()) if attr and attr.HasAuthoredValue() else None

        self.assertEqual(method("/dq"), "dualQuaternion")
        self.assertEqual(method("/lin"), "classicLinear")
        self.assertIsNone(method("/plain"))

    def test_template_collect_empties_is_the_fbx_template_s_copy(self):
        """Two copies of one collector inside one package: identical by AST."""
        import ast

        usd_fn = self._template_function(self.TEMPLATE, "collect_empties")[1]
        fbx_fn = self._template_function(
            si._TEMPLATE_DIR / "_import_scene.py", "collect_empties"
        )[1]
        self.assertIsNotNone(usd_fn)
        self.assertIsNotNone(fbx_fn)
        for fn in (usd_fn, fbx_fn):
            fn.body = [
                n for n in fn.body if not isinstance(n, ast.Expr)
            ]  # drop docstring
        self.assertEqual(ast.dump(usd_fn), ast.dump(fbx_fn))

    def test_locator_fallback_loops_are_one_copy_across_the_three_templates(self):
        """The heuristic fallback of ``restore_usd_locators`` (a shapeless,
        childless plain transform gets a locator) is vendored into the Maya-side
        send templates too; the loop must be token-identical in all three."""
        import ast

        mono = si._TEMPLATE_DIR.parents[4]
        paths = [
            si._TEMPLATE_DIR / "_bake_scene.py",
            mono
            / "blendertk"
            / "blendertk"
            / "env_utils"
            / "maya_bridge"
            / "templates"
            / "import.py",
            mono
            / "blendertk"
            / "blendertk"
            / "env_utils"
            / "maya_bridge"
            / "templates"
            / "_save_scene.py",
        ]
        loops = []
        for path in paths:
            if not path.is_file():
                self.skipTest(f"sibling checkout missing: {path}")
            fn = self._template_function(path, "restore_usd_locators")[1]
            self.assertIsNotNone(fn, path)
            loop = next((n for n in fn.body if isinstance(n, ast.For)), None)
            self.assertIsNotNone(loop, path)
            loops.append(ast.dump(loop))
        self.assertEqual(len(set(loops)), 1, "locator fallback loops drifted apart")

    def test_empty_group_fallback_loops_are_one_copy_across_the_send_templates(self):
        """The FBX twin: both Maya-side send templates delegate the empty-group
        repair to :meth:`BlenderSceneImport._restore_empty_groups` and fall back
        to the children heuristic -- one loop, token-identical in both. The
        interactive template used to carry a hand-kept copy of the whole repair,
        manifest rules included, whose rename-on-clash match had already
        diverged from the engine's (2026-09-19). Executed against the engine by
        ``test_blender_bridge.TestPullTemplateCopiesMatchTheirSource``."""
        import ast

        templates = (
            si._TEMPLATE_DIR.parents[4]
            / "blendertk"
            / "blendertk"
            / "env_utils"
            / "maya_bridge"
            / "templates"
        )
        loops = []
        for name in ("import.py", "_save_scene.py"):
            path = templates / name
            if not path.is_file():
                self.skipTest(f"sibling checkout missing: {path}")
            fn = self._template_function_node(
                path.read_text(encoding="utf-8"), "restore_empty_groups"
            )
            self.assertIsNotNone(fn, path)
            source = ast.unparse(fn)
            self.assertIn("_restore_empty_groups(", source, f"{name}: no engine path")
            loop = next((n for n in fn.body if isinstance(n, ast.For)), None)
            self.assertIsNotNone(loop, path)
            loops.append(ast.dump(loop))
        self.assertEqual(len(set(loops)), 1, "empty-group fallback loops drifted apart")


class TestRestoreUsdLocators(MayaTkTestCase):
    """The USD inverse of the FBX Empty repair: CREATE locator shapes."""

    def _manifest(self, empties):
        import json

        path = os.path.join(self.tmp, "x.usd.manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"version": 2, "format": "names", "instances": [], "empties": empties},
                fh,
            )
        return path

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="mtk_usd_loc_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_childless_shapeless_transform_gets_a_locator_by_heuristic(self):
        marker = cmds.createNode("transform", name="marker")
        group = cmds.createNode("transform", name="grp")
        cmds.parent(cmds.polyCube(name="kid")[0], group)
        created = BlenderSceneImport._restore_usd_locators([marker, group], None)
        self.assertEqual(created, 1)
        self.assertEqual(
            cmds.nodeType(cmds.listRelatives(marker, shapes=True)[0]), "locator"
        )
        self.assertFalse(cmds.listRelatives(group, shapes=True))

    def test_manifest_rules_override_the_heuristic(self):
        parent_marker = cmds.createNode("transform", name="arrow")
        cmds.parent(cmds.polyCube(name="kid2")[0], parent_marker)
        plain_leaf = cmds.createNode("transform", name="leaf_group")
        manifest = self._manifest(
            [
                {
                    "name": "arrow",
                    "display_type": "ARROWS",
                },  # marked -> locator even as a parent
                {
                    "name": "leaf_group",
                    "maya_node_type": "group",
                },  # tagged group -> stays bare
            ]
        )
        created = BlenderSceneImport._restore_usd_locators(
            [parent_marker, plain_leaf], manifest
        )
        self.assertEqual(created, 1)
        self.assertTrue(cmds.listRelatives(parent_marker, shapes=True))
        self.assertFalse(cmds.listRelatives(plain_leaf, shapes=True))

    def test_flat_usd_preview_shaders_become_standard_surface(self):
        """mayaUsd imports a flat material as a usdPreviewSurface named after its
        Blender BSDF node; the pipeline wants a named standardSurface, SG spelled
        Maya's way. A textured one is the manifest replay's job and is left alone."""
        from mayatk.env_utils.usd import UsdUtils

        UsdUtils.load_plugin()
        cube = cmds.polyCube(name="flat_cube")[0]
        shader = cmds.shadingNode(
            "usdPreviewSurface", asShader=True, name="Principled_BSDF"
        )
        cmds.setAttr(f"{shader}.diffuseColor", 0.1, 0.2, 0.9, type="double3")
        cmds.setAttr(f"{shader}.roughness", 0.35)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="ball_mat"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        textured = cmds.shadingNode(
            "usdPreviewSurface", asShader=True, name="Principled_BSDF1"
        )
        tex = cmds.shadingNode("file", asTexture=True, name="crate_file")
        cmds.connectAttr(f"{tex}.outColor", f"{textured}.diffuseColor", force=True)
        sg2 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="crate_mat"
        )
        cmds.connectAttr(f"{textured}.outColor", f"{sg2}.surfaceShader", force=True)

        converted = BlenderSceneImport()._convert_usd_preview_shaders(
            [sg, sg2, shader, textured, cube]
        )
        self.assertEqual(converted, 1)
        self.assertFalse(cmds.objExists(shader))
        self.assertTrue(
            cmds.objExists("ball_mat")
            and cmds.nodeType("ball_mat") == "standardSurface"
        )
        self.assertTrue(cmds.objExists("ball_matSG"))
        self.assertEqual(
            [round(v, 2) for v in cmds.getAttr("ball_mat.baseColor")[0]],
            [0.1, 0.2, 0.9],
        )
        self.assertAlmostEqual(
            cmds.getAttr("ball_mat.specularRoughness"), 0.35, places=3
        )
        self.assertEqual(
            cmds.nodeType(textured), "usdPreviewSurface"
        )  # textured: untouched

    def test_a_leaf_joint_is_a_skeleton_tip_not_a_marker(self):
        """Joints derive from transform; a shapeless leaf joint must stay a joint."""
        root = cmds.joint(name="j_root")
        tip = cmds.joint(name="j_tip")
        marker = cmds.createNode("transform", name="marker2")
        created = BlenderSceneImport._restore_usd_locators([root, tip, marker], None)
        self.assertEqual(created, 1)
        self.assertFalse(cmds.listRelatives(tip, shapes=True))
        self.assertTrue(cmds.listRelatives(marker, shapes=True))

    def test_names_are_matched_usd_spelled(self):
        """The manifest holds Blender names; the import spelled them as prims."""
        node = cmds.createNode("transform", name="Chair_001")  # Blender "Chair.001"
        manifest = self._manifest([{"name": "Chair.001", "maya_node_type": "locator"}])
        cmds.parent(
            cmds.polyCube(name="kid3")[0], node
        )  # a parent: only the rule says locator
        self.assertEqual(BlenderSceneImport._restore_usd_locators([node], manifest), 1)


class TestUsdContainerSkeletons(MayaTkTestCase):
    """Blender writes an armature's DATA as a Skeleton prim nested under the
    object's Xform, and mayaUsd turns every Skeleton prim into a joint of its own
    -- unless it carries ``customData Maya:generated``, its own exporter's word
    for "a container, not a node". Unstamped, the container joint sat between
    the armature's transform and its root joint, at the armature's origin, and a
    bone id (``<armature>/<bone>``) matched it EXACTLY whenever the armature data
    is named like its root bone -- always, for a Maya joint that went to Blender
    and back. The rig transfer then built and verified against it: 2.4-2.6 m off
    on the production module, read as "the carrier drops the joint at the
    origin" (backlog 2026-09-17). The joint itself was never misplaced.

    Only a CONTAINER may be stamped (measured, mayaUsd 0.30): a Skeleton carrying
    its own transform -- a static export merges a leaf armature's object into it
    -- and a root-level Skeleton both arrived with NO joints at all once marked.
    """

    TEMPLATE = si._TEMPLATE_DIR / "_import_scene_usd.py"
    SOURCE = (
        si._TEMPLATE_DIR.parents[4] / "blendertk" / "blendertk" / "env_utils" / "usd.py"
    )

    def setUp(self):
        super().setUp()
        try:
            from pxr import Usd  # noqa: F401
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        self.tmp = tempfile.mkdtemp(prefix="mtk_usd_skel_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _mark(self):
        mark, _ = TestUsdPullRouteContracts._template_function(
            self.TEMPLATE, "mark_container_skeletons"
        )
        self.assertIsNotNone(mark, "template lost mark_container_skeletons")
        return mark

    def _blender_layout(self):
        """A layer shaped the way Blender 5.1's exporter writes armatures (probed):
        an animated export's object Xform holding its data's Skeleton; a static
        export's leaf armature MERGED into its Skeleton, transform and all; and a
        Skeleton at the root. Maya centimetres, Y-up, like the template's."""
        from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

        path = os.path.join(self.tmp, "armatures.usda")
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)

        def skeleton(prim_path, joint, rest):
            skel = UsdSkel.Skeleton.Define(stage, prim_path)
            UsdSkel.BindingAPI.Apply(skel.GetPrim())
            skel.CreateJointsAttr([joint])
            # Blender's rest pose is identity; the placement is all in the pose
            # (a joint nothing is bound to exports no bind -- see mayaUsd).
            skel.CreateBindTransformsAttr(Vt.Matrix4dArray([Gf.Matrix4d(1.0)]))
            skel.CreateRestTransformsAttr(
                Vt.Matrix4dArray([Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(*rest))])
            )
            return skel

        grp = UsdGeom.Xform.Define(stage, "/lone_grp")
        grp.AddTranslateOp().Set((120.0, 40.0, -60.0))
        arm = UsdGeom.Xform.Define(stage, "/lone_grp/lone_jnt")  # the OBJECT
        arm.AddTranslateOp().Set((0.0, 0.0, 0.0))
        skeleton("/lone_grp/lone_jnt/lone_jnt", "lone_jnt", (20.0, 10.0, 5.0))

        UsdGeom.Xform.Define(stage, "/merged_grp").AddTranslateOp().Set(
            (0.0, 30.0, 0.0)
        )
        merged = skeleton("/merged_grp/solo_arm", "solo_arm", (0.0, 0.0, 0.0))
        merged.AddTranslateOp().Set((-100.0, 20.0, -50.0))  # the object's own

        skeleton("/root_skel", "root_jnt", (5.0, 6.0, 7.0))
        stage.GetRootLayer().Save()
        with open(path + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "format": "names", "instances": []}, fh)
        return path

    @staticmethod
    def _stamped(path):
        from pxr import Usd

        stage = Usd.Stage.Open(path)
        return sorted(
            str(p.GetPath())
            for p in stage.Traverse()
            if p.GetCustomDataByKey("Maya:generated")
        )

    def test_only_a_container_skeleton_is_marked(self):
        from pxr import Usd

        mark = self._mark()
        path = self._blender_layout()
        self.assertEqual(mark(path), 1)
        self.assertEqual(self._stamped(path), ["/lone_grp/lone_jnt/lone_jnt"])
        self.assertEqual(mark(path), 0, "a marked layer must report nothing to do")
        # An author who already SAID something is not overruled.
        stage = Usd.Stage.Open(path)
        stage.GetPrimAtPath("/lone_grp/lone_jnt/lone_jnt").SetCustomDataByKey(
            "Maya:generated", False
        )
        stage.GetRootLayer().Save()
        self.assertEqual(mark(path), 0)
        self.assertEqual(self._stamped(path), [])

    def test_a_marked_container_imports_as_no_joint_and_the_bone_id_resolves(self):
        from mayatk.rig_utils.rig_graph_build import RigGraphBuilder

        path = self._blender_layout()
        self._mark()(path)
        imported = BlenderSceneImport().import_payload(path, via="usd")

        def at(node):
            return [round(v, 3) for v in cmds.xform(node, q=True, ws=True, t=True)]

        # The joint sits DIRECTLY under the armature's transform -- the FBX
        # route's shape -- and the container left no joint behind.
        joint = "|lone_grp|lone_jnt|lone_jnt"
        self.assertEqual(cmds.ls("lone_jnt", type="joint", long=True), [joint])
        self.assertEqual(at(joint), [140.0, 50.0, -55.0])
        builder = RigGraphBuilder()
        builder._index(imported)
        self.assertEqual(builder._node("/lone_grp/lone_jnt/lone_jnt"), joint)

        # Left unmarked, so nothing is lost: the merged leaf keeps its object
        # transform, the root-level skeleton its joints.
        self.assertIn(
            "|merged_grp|solo_arm|solo_arm",
            cmds.ls("solo_arm", type="joint", long=True),
        )
        self.assertEqual(at("|merged_grp|solo_arm|solo_arm"), [-100.0, 50.0, -50.0])
        roots = cmds.ls("root_jnt", type="joint", long=True)
        self.assertEqual(len(roots), 1)
        self.assertEqual(at(roots[0]), [5.0, 6.0, 7.0])

    def test_the_template_marks_what_it_exports(self):
        """``export_usd`` runs the pass on every layer it writes, after the fold
        (which renames prims)."""
        text = self.TEMPLATE.read_text(encoding="utf-8")
        body = text[text.index("def export_usd(") : text.index("def hidden_objects(")]
        self.assertIn("mark_container_skeletons(OUT_USD)", body)
        self.assertLess(
            body.index("fold_single_mesh_xforms(OUT_USD)"),
            body.index("mark_container_skeletons(OUT_USD)"),
        )

    def test_the_copy_is_blendertk_s_source_both_ways(self):
        """AST identity with ``btk.UsdUtils.mark_container_skeletons`` (docstrings
        aside): a change on EITHER side fails here, not only a drift in the copy."""
        if not self.SOURCE.is_file():
            self.skipTest(f"sibling blendertk checkout missing: {self.SOURCE}")
        _, copy = TestUsdPullRouteContracts._template_function(
            self.TEMPLATE, "mark_container_skeletons"
        )
        self.assertIsNotNone(copy, "template lost mark_container_skeletons")
        source = next(
            (
                fn
                for cls in ast.parse(self.SOURCE.read_text(encoding="utf-8")).body
                if isinstance(cls, ast.ClassDef) and cls.name == "UsdUtils"
                for fn in cls.body
                if isinstance(fn, ast.FunctionDef)
                and fn.name == "mark_container_skeletons"
            ),
            None,
        )
        self.assertIsNotNone(source, "btk.UsdUtils lost mark_container_skeletons")

        def code(fn):
            body = list(fn.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]  # the docstrings differ by design
            # Names and defaults, not annotations: the public method is typed,
            # the template's copies are not -- neither changes what runs.
            args = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
            defaults = [ast.dump(d) for d in fn.args.defaults]
            return ast.dump(ast.Module(body=body, type_ignores=[])), args, defaults

        self.assertEqual(code(copy), code(source))


class TestPayloadSectionPlan(MayaTkTestCase):
    """``import_payload``'s scene sections are a declared plan, not a hand-run chain.

    The carrier import is stubbed -- the point under test is which sections the
    plan admits, in what order, and how they are counted for progress -- so the
    steps run against an empty import and report without touching a real FBX.
    """

    def setUp(self):
        super().setUp()
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        self.tmp = tempfile.mkdtemp(prefix="mtk_payload_plan_")
        self.payload = os.path.join(self.tmp, "payload.fbx")
        with open(self.payload, "wb") as fh:
            fh.write(b"stub")
        self.engine = si.BlenderSceneImport()
        self.engine._import_fbx = lambda path, opts: []

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _sidecar(self, data, raw=None):
        with open(self.payload + ".manifest.json", "w", encoding="utf-8") as fh:
            fh.write(raw) if raw is not None else json.dump(data, fh)

    def _reports(self, **kwargs):
        seen = []
        self.engine.import_payload(
            self.payload, step=lambda d, t, m: seen.append((d, t, m)), **kwargs
        )
        return seen

    @staticmethod
    def _shots_section():
        return {"version": 2, "shots": [], "scene_fps": 24.0}

    def test_no_sidecar_runs_only_the_carrier_steps(self):
        labels = [m for _, _, m in self._reports()]
        self.assertEqual(
            labels, ["Importing the FBX", "Rebuilding instances and materials"]
        )

    def test_a_carried_section_is_admitted_and_counted(self):
        self._sidecar({"version": 1, "shots": self._shots_section()})
        seen = self._reports()
        # The shots section lands through the scene-data step (shots and every
        # other portable record, 2026-09-19).
        self.assertEqual([m for _, _, m in seen][-1], "Landing the scene data")
        self.assertTrue(
            all(t == 3 for _, t, _ in seen),
            "the progress total must count the steps that will really run",
        )

    def test_an_absent_section_is_not_counted(self):
        self._sidecar({"version": 1, "materials": []})
        seen = self._reports()
        self.assertTrue(all(t == 2 for _, t, _ in seen))

    def test_the_option_gate_drops_a_carried_section(self):
        self._sidecar({"version": 1, "shots": self._shots_section()})
        self.assertIn(
            "Landing the scene data", [m for _, _, m in self._reports()]
        )  # the label this gate must remove -- a vacuous check otherwise
        labels = [m for _, _, m in self._reports(scene_data=False)]
        self.assertNotIn("Landing the scene data", labels)

    def test_the_clock_is_not_section_gated(self):
        """With no ``scene`` section the applier falls back to the payload's own
        time setup, so *adopt_scene* alone must admit the step."""
        self._sidecar({"version": 1, "materials": []})
        labels = [m for _, _, m in self._reports(adopt_scene=True)]
        self.assertIn("Adopting the scene clock", labels)

    def test_sections_run_in_the_declared_order(self):
        self._sidecar(
            {
                "version": 1,
                "shots": self._shots_section(),
                "rig": {"graph": {}, "plan": {}},
            }
        )
        labels = [m for _, _, m in self._reports(adopt_scene=True)]
        self.assertEqual(
            labels[-3:],
            ["Adopting the scene clock", "Landing the scene data", "Building the rig"],
            "the shots name what every step rebuilt; the rig verifies after both",
        )

    def test_a_failed_section_is_logged_and_never_costs_the_import(self):
        self._sidecar({"version": 1, "shots": self._shots_section()})

        def boom(*a, **k):
            raise RuntimeError("section blew up")

        self.engine._apply_scene_data = boom
        with self.assertLogs(self.engine.logger, level=logging.WARNING) as caught:
            self.engine.import_payload(self.payload)
        self.assertTrue(
            any("Landing the scene data failed" in m for m in caught.output),
            caught.output,
        )

    def test_an_unreadable_sidecar_warns_once_and_imports_anyway(self):
        self._sidecar(None, raw="{not json")
        with self.assertLogs(self.engine.logger, level=logging.WARNING) as caught:
            self.engine.import_payload(self.payload)
        self.assertTrue(
            any("Unreadable manifest" in m for m in caught.output), caught.output
        )


class TestBlendRigLogicProbe(unittest.TestCase):
    """``scene_has_complex_animation`` reads a REAL ``.blend``'s block headers
    against its own DNA catalog -- the gate for the Reference Manager's
    Transfer-rig / Bake prompt, so a miss silently bakes a rig and a false hit
    asks a pointless question. Fixtures are written by the installed Blender
    (the bridge's own discovery), so every header layout and struct name is
    the real one; skipped where no Blender is installed."""

    _MAKE = r"""
import os, sys, bpy
out = sys.argv[sys.argv.index("--") + 1]
def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
def save(name, compress=False):
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, name), compress=compress)
fresh(); bpy.ops.mesh.primitive_cube_add(); c = bpy.context.object
c.keyframe_insert("location", frame=1); c.location.x = 5
c.keyframe_insert("location", frame=10)
save("keyed.blend")
fresh(); bpy.ops.mesh.primitive_cube_add(); c = bpy.context.object
bpy.ops.object.empty_add(); c.constraints.new("COPY_LOCATION").target = bpy.context.object
save("constraint.blend"); save("constraint_compressed.blend", compress=True)
fresh(); bpy.ops.mesh.primitive_cube_add()
bpy.context.object.driver_add("location", 0).driver.expression = "frame * 0.1"
save("driver.blend")
fresh(); bpy.ops.object.armature_add(); a = bpy.context.object
bpy.ops.object.mode_set(mode="EDIT")
b = a.data.edit_bones.new("b2"); b.head = (0, 0, 1); b.tail = (0, 0, 2)
b.parent = a.data.edit_bones[0]
bpy.ops.object.mode_set(mode="POSE")
a.pose.bones["b2"].constraints.new("IK").chain_count = 2
bpy.ops.object.mode_set(mode="OBJECT")
save("pose_ik.blend")
"""

    @classmethod
    def setUpClass(cls):
        import subprocess

        blender = BlenderSceneImport().blender_path
        if not blender or not os.path.isfile(blender):
            raise unittest.SkipTest("no Blender installed to write .blend fixtures")
        cls._store = ptk.TempArtifacts("blend_rig_probe", policy="scoped")
        cls.dir = cls._store.dir_path()
        script = os.path.join(cls.dir, "make.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(cls._MAKE)
        subprocess.run(
            [blender, "-b", "--factory-startup", "--python", script, "--", cls.dir],
            check=True,
            capture_output=True,
            timeout=300,
        )

    @classmethod
    def tearDownClass(cls):
        cls._store.cleanup()

    def _path(self, name):
        path = os.path.join(self.dir, name)
        self.assertTrue(os.path.isfile(path), f"Blender did not write {name}")
        return path

    def test_rig_logic_is_detected(self):
        for name in ("constraint.blend", "driver.blend", "pose_ik.blend"):
            self.assertTrue(
                BlenderSceneImport.scene_has_complex_animation(self._path(name)), name
            )

    def test_plain_keys_are_not_rig_logic(self):
        """Keys bake exactly: asking rig-vs-bake about them would be noise."""
        path = self._path("keyed.blend")
        self.assertFalse(BlenderSceneImport.scene_has_complex_animation(path))
        # ...and the False is a real read, not a parse failure passing as "none".
        structs = BlenderSceneImport._blend_block_structs(path)
        self.assertTrue({"Object", "Mesh", "FCurve"} <= structs, structs)

    def test_gzip_wrapped_blend_reads_like_the_raw_one(self):
        """Blender <= 2.9 compressed with gzip; the stdlib undoes it."""
        import gzip

        raw = self._path("constraint.blend")
        wrapped = os.path.join(self.dir, "gz.blend")
        with open(raw, "rb") as src, gzip.open(wrapped, "wb") as dst:
            shutil.copyfileobj(src, dst)
        self.assertEqual(
            BlenderSceneImport._blend_block_structs(wrapped),
            BlenderSceneImport._blend_block_structs(raw),
        )

    def test_unreadable_compression_asks_rather_than_guesses(self):
        """zstd needs Python 3.14's ``compression.zstd``; where it is missing
        the scene counts as rigged -- one extra question, never a silent bake."""
        path = self._path("constraint_compressed.blend")
        self.assertTrue(BlenderSceneImport.scene_has_complex_animation(path))

    def test_non_blend_and_missing_paths_are_not_probed(self):
        self.assertFalse(
            BlenderSceneImport.scene_has_complex_animation(
                os.path.join(self.dir, "missing.blend")
            )
        )
        self.assertFalse(BlenderSceneImport.scene_has_complex_animation(__file__))


if __name__ == "__main__":
    unittest.main(verbosity=2)
