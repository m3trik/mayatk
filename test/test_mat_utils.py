# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils module

Tests for MatUtils class functionality including:
- Material querying and assignment
- Scene material management
- Material creation
- Material ID operations
- Shading group operations
- File node and texture path operations
"""
import os
import unittest
import maya.cmds as cmds
import mayatk as mtk
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils

from base_test import MayaTkTestCase


class TestMatUtils(MayaTkTestCase):
    """Comprehensive tests for MatUtils class."""

    def setUp(self):
        """Set up test scene with geometries and materials."""
        super().setUp()
        # Create test geometries
        self.sphere = cmds.polySphere(name="test_sphere")[0]
        self.cube = cmds.polyCube(name="test_cube")[0]

        # Create test materials
        self.lambert1 = cmds.shadingNode("lambert", asShader=True, name="test_lambert1")
        self.lambert2 = cmds.shadingNode("lambert", asShader=True, name="test_lambert2")

        # Create shading groups
        self.sg1 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_sg1"
        )
        cmds.connectAttr(f"{self.lambert1}.outColor", f"{self.sg1}.surfaceShader")

        self.sg2 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_sg2"
        )
        cmds.connectAttr(f"{self.lambert2}.outColor", f"{self.sg2}.surfaceShader")

    def tearDown(self):
        """Clean up test materials and geometry."""
        super().tearDown()

    # -------------------------------------------------------------------------
    # Shading-assignment snapshot / copy (used by Preview's geometry restore)
    # -------------------------------------------------------------------------

    def test_get_shading_assignments_whole_object(self):
        """A single (object-level) material reads back as {sg: None}."""
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)
        self.assertEqual(
            MatUtils.get_shading_assignments(self.cube), {self.sg1: None}
        )

    def test_get_shading_assignments_per_face(self):
        """A per-face (multi-material) mesh reads back as {sg: [indices]}."""
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)
        cmds.sets(f"{self.cube}.f[1]", edit=True, forceElement=self.sg2)
        data = MatUtils.get_shading_assignments(self.cube)
        self.assertEqual(data.get(self.sg2), [1])
        self.assertCountEqual(data.get(self.sg1), [0, 2, 3, 4, 5])

    def test_get_apply_round_trip(self):
        """Per-face assignments survive a get -> apply onto an identical mesh."""
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)
        cmds.sets(f"{self.cube}.f[1]", edit=True, forceElement=self.sg2)
        snap = MatUtils.get_shading_assignments(self.cube)
        target = cmds.polyCube(name="copy_target")[0]
        MatUtils.apply_shading_assignments(target, snap)
        self.assertEqual(MatUtils.get_shading_assignments(target), snap)

    def test_apply_base_coats_uncovered_faces(self):
        """Faces beyond the snapshot (e.g. a bevel's new faces) are base-coated
        with the dominant material rather than left unshaded."""
        # Snapshot from a 6-face cube, applied to a 12-face target: the extra
        # faces must still land on a shading group (no unshaded/green faces).
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)
        cmds.sets(f"{self.cube}.f[1]", edit=True, forceElement=self.sg2)
        snap = MatUtils.get_shading_assignments(self.cube)

        target = cmds.polyCube(name="base_target", sx=2, sy=2)[0]  # >6 faces
        total = cmds.polyEvaluate(target, face=True)
        MatUtils.apply_shading_assignments(target, snap)

        tshape = cmds.listRelatives(target, shapes=True, noIntermediate=True)[0]
        owners = {target, tshape} | set(cmds.ls(target, long=True) or []) | set(
            cmds.ls(tshape, long=True) or []
        )
        covered = set()
        for sg in cmds.ls(type="shadingEngine"):
            for m in cmds.ls(cmds.sets(sg, q=True) or [], long=True, flatten=True) or []:
                if m.split(".f[")[0] in owners:
                    if ".f[" in m:
                        covered.add(int(m.split(".f[")[1].rstrip("]")))
                    else:
                        covered.update(range(total))
        self.assertEqual(len(covered), total, "apply left faces unshaded")

    # -------------------------------------------------------------------------
    # Material Query Tests
    # -------------------------------------------------------------------------

    def test_get_mats_from_object(self):
        """Test getting materials assigned to an object."""
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        mats = MatUtils.get_mats(self.sphere)
        self.assertIn(self.lambert1, mats)

    def test_get_mats_from_face(self):
        """Test getting materials from a face component."""
        # Assign to face explicitly to ensure component-level assignment is tested
        cmds.sets(f"{self.sphere}.f[0]", edit=True, forceElement=self.sg1)
        face = f"{self.sphere}.f[0]"
        face_mats = MatUtils.get_mats(face)
        self.assertIn(self.lambert1, face_mats)

    def test_get_mats_with_no_assignment(self):
        """Test getting materials from object with only default material."""
        # Cube has initialShadingGroup by default
        mats = MatUtils.get_mats(self.cube)
        self.assertTrue(len(mats) > 0)
        # Production returns strings; verify it is a shader-graph node.
        self.assertTrue(cmds.objExists(mats[0]))

    def test_get_scene_mats(self):
        """Test getting all materials in the scene."""
        scene_mats = MatUtils.get_scene_mats()
        self.assertIn(self.lambert1, scene_mats)
        self.assertIn(self.lambert2, scene_mats)

        # Test filtering
        filtered_mats = MatUtils.get_scene_mats(inc=["*lambert1*"])
        self.assertIn(self.lambert1, filtered_mats)
        self.assertNotIn(self.lambert2, filtered_mats)

    def test_get_scene_mats_excludes_defaults_by_default(self):
        """Maya defaults (lambert1, standardSurface1, ...) are dropped unless opted in."""
        default_names = {"lambert1", "particleCloud1", "shaderGlow1", "standardSurface1"}

        scene_mats = MatUtils.get_scene_mats()
        leaked = default_names.intersection({str(m).split("|")[-1] for m in scene_mats})
        self.assertFalse(leaked, f"Default materials leaked into result: {leaked}")

        # Opt-in: defaults should reappear.
        all_mats = MatUtils.get_scene_mats(exclude_defaults=False)
        all_short = {str(m).split("|")[-1] for m in all_mats}
        # lambert1 exists in every Maya scene; the others may not, depending on plugins.
        self.assertIn("lambert1", all_short)

    def test_get_scene_mats_excludes_utility_nodes(self):
        """Utility nodes parked in the shader list are not materials.

        ``shadingNode -asShader`` registers ANY node type in
        ``defaultShaderList1``, which is precisely what ``cmds.ls(materials=True)``
        reports — so a ``bump2d`` (classified ``utility/general/bump``) created
        that way showed up as a material. Regression: the Arnold bridge's
        ``aiMultiply`` / ``bump2d`` helpers leaking into the materials combo.
        """
        bump = cmds.shadingNode("bump2d", asShader=True, name="test_util_bump")
        # Maya itself considers it a material — the filter is ours to apply.
        self.assertIn(bump, cmds.ls(materials=True) or [])

        # A shader whose classification embeds "utility" deeper in the path
        # (surfaceShader and StingrayPBS are both 'shader/surface/utility')
        # must survive — the shader role is tested first.
        surf = cmds.shadingNode("surfaceShader", asShader=True, name="test_surf_shader")

        names = {str(m).split("|")[-1] for m in MatUtils.get_scene_mats()}
        self.assertNotIn(bump, names)
        self.assertIn(self.lambert1, names)  # real shaders unaffected
        self.assertIn(surf, names)

        # Opt-out: Maya's raw view is still reachable.
        raw = {
            str(m).split("|")[-1]
            for m in MatUtils.get_scene_mats(exclude_utility_nodes=False)
        }
        self.assertIn(bump, raw)

    def test_get_scene_mats_includes_shaders_outside_the_shader_list(self):
        """A shader wired to a shading engine is a scene material either way.

        ``cmds.ls(materials=True)`` reports ``defaultShaderList1``, and only
        ``shadingNode -asShader`` registers a node there — a shader built with
        ``createNode``, or wired up by an importer/plugin, is assigned to
        geometry yet invisible to Maya's query. It was therefore missing from
        the materials combo, and "Get Material" on the object using it reported
        the material as hidden by a list filter that wasn't even enabled.
        """
        raw = cmds.createNode("lambert", name="test_raw_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_raw_sg"
        )
        cmds.connectAttr(f"{raw}.outColor", f"{sg}.surfaceShader")
        cmds.sets(self.cube, edit=True, forceElement=sg)

        # Maya itself doesn't report it — that's the whole point.
        self.assertNotIn(raw, cmds.ls(materials=True) or [])

        self.assertIn(raw, MatUtils.get_scene_mats())
        self.assertIn(raw, MatUtils.get_scene_mats(as_dict=True).values())
        # The object's material must be resolvable in the scene list — the exact
        # condition the materials panel's "Get Material" checks.
        self.assertIn(
            MatUtils.get_mats(self.cube)[0],
            [str(m) for m in MatUtils.get_scene_mats(exclude_defaults=False)],
        )

    def test_get_scene_mats_keeps_both_sides_of_a_name_collision(self):
        """Materials sharing a short name across namespaces both survive.

        The name filter used to run over a ``{short_name: material}`` dict, so
        ``nsA:mat`` and ``nsB:mat`` collapsed to whichever came last — the other
        vanished from every return path and could never be made current.
        """
        cmds.namespace(add="nsA")
        cmds.namespace(add="nsB")
        a = cmds.shadingNode("lambert", asShader=True, name="nsA:dup_mat")
        b = cmds.shadingNode("lambert", asShader=True, name="nsB:dup_mat")

        mats = [str(m) for m in MatUtils.get_scene_mats()]
        self.assertIn(a, mats)
        self.assertIn(b, mats)

        # as_dict keys the colliding group on the qualified name so neither is
        # dropped and the two rows stay tellable apart.
        dct = MatUtils.get_scene_mats(as_dict=True)
        self.assertEqual({k for k, v in dct.items() if v in (a, b)}, {a, b})
        self.assertNotIn("dup_mat", dct)

        # Non-colliding materials keep their plain short-name key.
        self.assertIn("test_lambert1", MatUtils.get_scene_mats(as_dict=True))

    def test_get_scene_mats_name_filter_matches_the_short_name(self):
        """inc/exc still match on the short name, namespace or not."""
        cmds.namespace(add="nsC")
        c = cmds.shadingNode("lambert", asShader=True, name="nsC:filter_mat")

        self.assertIn(c, MatUtils.get_scene_mats(inc="filter_mat"))
        self.assertNotIn(c, MatUtils.get_scene_mats(exc="filter_mat"))

    def test_get_scene_mats_exc_classification(self):
        """``exc_classification`` drops materials whose type matches a pattern."""
        kept = {
            str(m).split("|")[-1]
            for m in MatUtils.get_scene_mats(exc_classification="shader/surface*")
        }
        self.assertNotIn(self.lambert1, kept)
        self.assertNotIn(self.lambert2, kept)

        # A non-matching pattern leaves the list intact.
        kept = {
            str(m).split("|")[-1]
            for m in MatUtils.get_scene_mats(exc_classification="rendernode/nosuch*")
        }
        self.assertIn(self.lambert1, kept)

    def test_get_file_nodes_exc_classification(self):
        """``exc_classification`` drops file nodes used only by matching shaders.

        Backs the Texture Path Editor's "Exclude Arnold Nodes" toggle: an
        Arnold preview shader owns a dedicated file node per texture, so every
        bridged material contributes a duplicate row. Uses ``surfaceShader``
        (``shader/surface/utility``) vs ``lambert`` (``shader/surface``) so the
        mechanism is testable without the Arnold plugin.
        """
        sur = cmds.shadingNode("surfaceShader", asShader=True, name="tpe_surface")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="tpe_surfaceSG"
        )
        cmds.connectAttr(f"{sur}.outColor", f"{sg}.surfaceShader")

        def _file(name):
            return cmds.shadingNode("file", asTexture=True, name=name)

        own_lambert = _file("tpe_fileL")
        cmds.connectAttr(f"{own_lambert}.outColor", f"{self.lambert1}.color")
        own_surface = _file("tpe_fileS")
        cmds.connectAttr(f"{own_surface}.outColor", f"{sur}.outColor")
        shared = _file("tpe_fileShared")
        cmds.connectAttr(f"{shared}.outColor", f"{self.lambert1}.incandescence")
        cmds.connectAttr(f"{shared}.outColor", f"{sur}.outTransparency")

        nodes = MatUtils.get_file_nodes(
            return_type="fileNodeName", exc_classification="shader/surface/utility*"
        )
        self.assertIn(own_lambert, nodes)
        self.assertNotIn(own_surface, nodes)
        # Shared with a non-excluded shader — hiding a renderer must not hide a
        # texture something else still uses.
        self.assertIn(shared, nodes)

        # Unfiltered, all three are present.
        nodes = MatUtils.get_file_nodes(return_type="fileNodeName")
        for n in (own_lambert, own_surface, shared):
            self.assertIn(n, nodes)

    def test_get_fav_mats(self):
        """Test getting favorite materials."""
        try:
            fav_mats = MatUtils.get_fav_mats()
            self.assertIsInstance(fav_mats, (list, tuple))
        except (AttributeError, NotImplementedError, ImportError):
            self.skipTest("get_fav_mats not implemented or unavailable")

    # -------------------------------------------------------------------------
    # Material Creation & Assignment Tests
    # -------------------------------------------------------------------------

    def test_create_mat_random(self):
        """Test creating a random material type."""
        random_mat = MatUtils.create_mat(mat_type="random", name="random_mat")
        self.assertTrue(cmds.objExists(random_mat))
        # Handle both string and PyNode return types
        mat_name = random_mat if hasattr(random_mat, "name") else random_mat
        self.assertTrue(mat_name.startswith("random_mat"))

    def test_create_mat_specific(self):
        """Test creating specific material types."""
        blinn = MatUtils.create_mat("blinn", name="test_blinn")
        self.assertEqual(cmds.nodeType(blinn), "blinn")

        # Test standardSurface if available (Maya 2020+)
        try:
            std = MatUtils.create_mat("standardSurface", name="test_std")
            self.assertEqual(cmds.nodeType(std), "standardSurface")
        except RuntimeError:
            pass  # standardSurface might not be available in older Maya versions

    def test_assign_mat(self):
        """Test assigning material to objects."""
        # Assign existing material
        MatUtils.assign_mat(self.cube, "test_lambert1")
        mats = MatUtils.get_mats(self.cube)
        self.assertIn(self.lambert1, mats)

        # Assign new material (should be created)
        MatUtils.assign_mat(self.cube, "new_created_mat")
        self.assertTrue(cmds.objExists("new_created_mat"))
        mats = MatUtils.get_mats(self.cube)
        self.assertEqual(mats[0], "new_created_mat")

    def test_is_mat_assigned(self):
        """``is_mat_assigned`` reports True only when an SG has DAG members."""
        # lambert1 from setUp has a shading group but no geometry yet.
        self.assertFalse(MatUtils.is_mat_assigned(self.lambert1))

        # Wire the sphere into lambert1's shading group.
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        self.assertTrue(MatUtils.is_mat_assigned(self.lambert1))
        # lambert2's SG is still empty.
        self.assertFalse(MatUtils.is_mat_assigned(self.lambert2))

        # Material with no shading engine at all is also unassigned.
        orphan = cmds.shadingNode("blinn", asShader=True, name="orphan_mat")
        self.assertFalse(MatUtils.is_mat_assigned(orphan))

    def test_is_mat_assigned_displacement_shader(self):
        """Non-surface shaders (displacement) wire through other attrs but still count."""
        # Build a displacementShader and route it into sg1 alongside lambert1.
        disp = cmds.shadingNode(
            "displacementShader", asShader=True, name="test_disp"
        )
        cmds.connectAttr(
            f"{disp}.displacement", f"{self.sg1}.displacementShader", force=True
        )

        # SG is still empty — both shaders should report unassigned.
        self.assertFalse(MatUtils.is_mat_assigned(disp))
        # Wire geometry into the SG; now the surface AND the displacement
        # shader connected to it both count as assigned.
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)
        self.assertTrue(MatUtils.is_mat_assigned(disp))

    def test_get_mat_info_filter_flags(self):
        """``get_mat_info`` honors exclude_defaults / exclude_unassigned / field toggles."""
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        # lambert2 stays unassigned (empty SG from setUp).

        # Baseline: all materials, full detail.
        all_records = MatUtils.get_mat_info(
            include_image_metadata=False  # avoid PIL on textureless mats
        )
        all_names = {r["material"] for r in all_records}
        self.assertIn(self.lambert1, all_names)
        self.assertIn(self.lambert2, all_names)
        self.assertIn("lambert1", all_names)  # default included by default

        # exclude_defaults drops lambert1/standardSurface1/etc.
        no_defaults = MatUtils.get_mat_info(
            exclude_defaults=True, include_image_metadata=False
        )
        no_default_names = {r["material"] for r in no_defaults}
        self.assertNotIn("lambert1", no_default_names)
        self.assertIn(self.lambert1, no_default_names)  # test_lambert1 != default

        # exclude_unassigned drops materials with empty shading engines.
        only_assigned = MatUtils.get_mat_info(
            exclude_unassigned=True, include_image_metadata=False
        )
        assigned_names = {r["material"] for r in only_assigned}
        self.assertIn(self.lambert1, assigned_names)
        self.assertNotIn(self.lambert2, assigned_names)

        # include_textures=False yields empty texture lists regardless of wiring.
        no_tex = MatUtils.get_mat_info(
            materials=[self.lambert1], include_textures=False
        )
        self.assertEqual(no_tex[0]["textures"], [])

    def test_get_mat_info_skip_image_metadata(self):
        """``include_image_metadata=False`` omits PIL-derived fields per texture."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("PIL not available")

        import shutil
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="matinfo_tex_")
        tex_path = os.path.join(tmp_dir, "tiny.png")
        Image.new("RGB", (8, 8), color=(255, 0, 0)).save(tex_path)
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

        file_node = cmds.shadingNode("file", asTexture=True, name="meta_test_file")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        cmds.connectAttr(
            f"{file_node}.outColor", f"{self.lambert1}.color", force=True
        )

        records = MatUtils.get_mat_info(
            materials=[self.lambert1],
            include_image_metadata=False,
            optimize_check=False,
        )
        textures = records[0]["textures"]
        self.assertEqual(len(textures), 1)
        t = textures[0]
        self.assertIn("path", t)
        self.assertNotIn("width", t)
        self.assertNotIn("mode", t)
        self.assertNotIn("bit_depth", t)

        # Formatter must not emit "Res: NonexNone" lines when metadata is absent.
        text = MatUtils.format_mat_info_text(records)
        self.assertNotIn("Res:", text)

    def test_is_connected(self):
        """Test checking if material is connected to shading group."""
        # lambert1 is connected in setUp
        # Note: is_connected returns True if the material is NOT connected (unused)
        self.assertFalse(MatUtils.is_connected(self.lambert1))

        # Create unconnected material
        unconnected = cmds.shadingNode("blinn", asShader=True, name="unconnected_mat")
        self.assertTrue(MatUtils.is_connected(unconnected))

        # Test delete option
        self.assertTrue(
            MatUtils.is_connected(unconnected, delete=True)
        )  # Returns True if deleted
        self.assertFalse(cmds.objExists("unconnected_mat"))

    # -------------------------------------------------------------------------
    # Texture & File Node Tests
    # -------------------------------------------------------------------------

    def test_get_connected_shaders(self):
        """Test retrieving shaders connected to file nodes."""
        file_node = cmds.shadingNode("file", asTexture=True, name="test_file")
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)

        shaders = MatUtils.get_connected_shaders(file_node)
        self.assertIn(self.lambert1, shaders)

    def test_get_file_nodes(self):
        """Test retrieving file nodes from materials."""
        file_node = cmds.shadingNode("file", asTexture=True, name="test_file_node")
        cmds.setAttr(f"{file_node}.fileTextureName", "c:/test/texture.jpg", type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)

        # Test basic retrieval
        nodes = MatUtils.get_file_nodes(materials=[self.lambert1])
        # Default return type is 'fileNode' (object)
        self.assertIn(file_node, nodes)

        # Test return types
        info = MatUtils.get_file_nodes(
            materials=[self.lambert1], return_type="shaderName|fileNodeName"
        )
        self.assertTrue(len(info) > 0)
        self.assertEqual(info[0], (self.lambert1, file_node))

    def test_collect_material_paths(self):
        """Test collecting file paths from materials."""
        file_node = cmds.shadingNode("file", asTexture=True, name="path_test_file")
        test_path = "c:/textures/test.jpg"
        cmds.setAttr(f"{file_node}.fileTextureName", test_path, type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)

        # Test collection
        paths = MatUtils.collect_material_paths(materials=[self.lambert1])
        # Note: Paths might be normalized/resolved, so check for substring or basename
        # collect_material_paths returns a list of tuples
        self.assertTrue(any("test.jpg" in p[0] for p in paths))

    def test_get_texture_paths_from_object(self):
        """Resolves texture paths via object → material → file-node chain
        without opening the image files (path-only fast path)."""
        file_node = cmds.shadingNode("file", asTexture=True, name="tp_test_file")
        cmds.setAttr(
            f"{file_node}.fileTextureName", "c:/textures/albedo.png", type="string"
        )
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)

        paths = MatUtils.get_texture_paths(objects=[self.sphere])
        self.assertTrue(any("albedo.png" in p for p in paths))

    def test_get_texture_paths_traverses_bump2d(self):
        """File nodes wired through utility nodes (bump2d) must still resolve."""
        bump = cmds.shadingNode("bump2d", asUtility=True, name="tp_bump")
        file_node = cmds.shadingNode("file", asTexture=True, name="tp_height_file")
        cmds.setAttr(
            f"{file_node}.fileTextureName", "c:/textures/height.png", type="string"
        )
        cmds.connectAttr(f"{file_node}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(
            f"{bump}.outNormal", f"{self.lambert1}.normalCamera", force=True
        )
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)

        paths = MatUtils.get_texture_paths(objects=[self.sphere])
        self.assertTrue(any("height.png" in p for p in paths))

    def test_get_texture_paths_no_scene_fallback_when_scoped(self):
        """Regression: any explicit scope (even an empty list) must NOT
        fall back to scanning every file node in the scene. That caused
        the optimize-selection hang on a polygon whose material had no
        textures wired in (or whose scope query was empty)."""
        # Scene has unrelated textures (simulates "real" scene)
        unrelated = cmds.shadingNode("file", asTexture=True, name="unrelated_file")
        cmds.setAttr(
            f"{unrelated}.fileTextureName", "c:/textures/unrelated.png", type="string"
        )

        # Case 1: scoped object whose material has no textures → []
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        self.assertEqual(MatUtils.get_texture_paths(objects=[self.sphere]), [])

        # Case 2: explicit empty list (e.g. caller's selection was empty)
        # must also be treated as "scoped to nothing", NOT "no scope"
        self.assertEqual(MatUtils.get_texture_paths(objects=[]), [])
        self.assertEqual(MatUtils.get_texture_paths(materials=[]), [])
        self.assertEqual(MatUtils.get_texture_paths(file_nodes=[]), [])

        # Case 3: no-arg call still scans the whole scene (legitimate fallback)
        scene_paths = MatUtils.get_texture_paths()
        self.assertTrue(any("unrelated.png" in p for p in scene_paths))

    def test_is_bundled_texture_detects_maya_install_images(self):
        """Textures under MAYA_LOCATION are Maya's own, not project assets."""
        install = mtk.EnvUtils.get_env_info("install_path")
        self.assertTrue(install, "MAYA_LOCATION must be set inside Maya")

        bundled = os.path.join(
            install, "presets", "ShaderFX", "Images", "PBS", "midday",
            "specular_cube.dds",
        )
        self.assertTrue(MatUtils.is_bundled_texture(bundled), bundled)
        # Case/separator insensitivity — stored paths are forward-slashed.
        self.assertTrue(
            MatUtils.is_bundled_texture(bundled.replace("\\", "/").upper())
        )
        self.assertFalse(MatUtils.is_bundled_texture("C:/proj/sourceimages/a.png"))
        self.assertFalse(MatUtils.is_bundled_texture(""))
        # A sibling directory that merely *starts* with the install path must
        # not match — that's what the separator in the prefix test is for.
        self.assertFalse(MatUtils.is_bundled_texture(install.rstrip("/\\") + "_bak/x.png"))

    def test_get_texture_paths_can_exclude_bundled_textures(self):
        """Regression (2026-08-05): a StingrayPBS selection fed Maya's own
        read-only preset cube maps to the Map Converter, which then died with
        PermissionError trying to rewrite files under Program Files."""
        install = mtk.EnvUtils.get_env_info("install_path")
        bundled = os.path.join(
            install, "presets", "ShaderFX", "Images", "PBS", "midday",
            "specular_cube.dds",
        ).replace("\\", "/")

        f_user = cmds.shadingNode("file", asTexture=True, name="tp_user_file")
        f_bundled = cmds.shadingNode("file", asTexture=True, name="tp_bundled_file")
        cmds.setAttr(
            f"{f_user}.fileTextureName", "c:/proj/sourceimages/rock.png", type="string"
        )
        cmds.setAttr(f"{f_bundled}.fileTextureName", bundled, type="string")
        cmds.connectAttr(f"{f_user}.outColor", f"{self.lambert1}.color", force=True)
        cmds.connectAttr(
            f"{f_bundled}.outColor", f"{self.lambert1}.ambientColor", force=True
        )

        default_paths = MatUtils.get_texture_paths(materials=[self.lambert1])
        self.assertTrue(
            any("specular_cube" in p for p in default_paths),
            "default must stay inclusive — inventory callers want every map",
        )

        filtered = MatUtils.get_texture_paths(
            materials=[self.lambert1], exclude_bundled=True
        )
        self.assertFalse([p for p in filtered if "specular_cube" in p], filtered)
        self.assertTrue(any("rock.png" in p for p in filtered), filtered)

    def test_get_texture_paths_workspace_relative_not_doubled(self):
        """A project-relative ``fileTextureName`` resolves against the project ROOT.

        Regression (2026-08-05): relative values were joined onto the
        *sourceimages* directory, so the usual stored form
        ``sourceimages/tex.png`` came back as
        ``<root>/sourceimages/sourceimages/tex.png`` — a path that exists
        nowhere. Every consumer then dropped the texture: the Map Converter's
        Maya scopes reported "Skipping (file not found)", MatManifest handed
        the Marmoset/Substance bridges dead paths, and
        copy_textures_to_sourceimages skipped files that were right there.
        """
        import shutil
        import tempfile

        ws_root = tempfile.mkdtemp(prefix="tex_paths_ws_")
        si = os.path.join(ws_root, "sourceimages")
        os.makedirs(si, exist_ok=True)
        real = os.path.join(si, "rel_tex.png")
        with open(real, "w") as f:
            f.write("dummy")

        file_node = cmds.shadingNode("file", asTexture=True, name="tp_rel_file")
        cmds.setAttr(
            f"{file_node}.fileTextureName", "sourceimages/rel_tex.png", type="string"
        )
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)

        original_ws = cmds.workspace(q=True, rd=True)
        try:
            cmds.workspace(ws_root, openWorkspace=True)
            paths = MatUtils.get_texture_paths(materials=[self.lambert1])
            self.assertEqual(len(paths), 1, paths)
            resolved = paths[0]
            self.assertNotIn(
                "sourceimages" + os.sep + "sourceimages",
                os.path.normpath(resolved),
                f"rule folder doubled: {resolved}",
            )
            self.assertTrue(
                os.path.isfile(resolved),
                f"resolved path must exist on disk: {resolved}",
            )
            self.assertEqual(os.path.normcase(resolved), os.path.normcase(real))

            # The relative form must round-trip back to what Maya stored.
            rel = MatUtils.get_texture_paths(
                materials=[self.lambert1], absolute=False
            )
            self.assertEqual(rel, ["sourceimages/rel_tex.png"])
        finally:
            try:
                if original_ws and os.path.isdir(original_ws):
                    cmds.workspace(original_ws, openWorkspace=True)
            except Exception:
                pass
            shutil.rmtree(ws_root, ignore_errors=True)

    def test_get_texture_paths_sourceimages_relative_still_resolves(self):
        """A value stored relative to sourceimages keeps working (fallback).

        The fix makes project-root expansion primary; the old
        sourceimages-relative join stays as a fallback so pipelines that
        stored bare ``tex.png`` don't regress.
        """
        import shutil
        import tempfile

        ws_root = tempfile.mkdtemp(prefix="tex_paths_bare_")
        si = os.path.join(ws_root, "sourceimages")
        os.makedirs(si, exist_ok=True)
        real = os.path.join(si, "bare_tex.png")
        with open(real, "w") as f:
            f.write("dummy")

        file_node = cmds.shadingNode("file", asTexture=True, name="tp_bare_file")
        cmds.setAttr(f"{file_node}.fileTextureName", "bare_tex.png", type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{self.lambert1}.color", force=True)

        original_ws = cmds.workspace(q=True, rd=True)
        try:
            cmds.workspace(ws_root, openWorkspace=True)
            paths = MatUtils.get_texture_paths(materials=[self.lambert1])
            self.assertEqual(len(paths), 1, paths)
            self.assertTrue(os.path.isfile(paths[0]), paths[0])
            self.assertEqual(os.path.normcase(paths[0]), os.path.normcase(real))
        finally:
            try:
                if original_ws and os.path.isdir(original_ws):
                    cmds.workspace(original_ws, openWorkspace=True)
            except Exception:
                pass
            shutil.rmtree(ws_root, ignore_errors=True)

    def test_get_texture_paths_dedup_and_order(self):
        """Duplicates removed, first-seen order preserved."""
        f1 = cmds.shadingNode("file", asTexture=True, name="tp_f1")
        f2 = cmds.shadingNode("file", asTexture=True, name="tp_f2")
        cmds.setAttr(f"{f1}.fileTextureName", "c:/textures/a.png", type="string")
        cmds.setAttr(f"{f2}.fileTextureName", "c:/textures/b.png", type="string")
        cmds.connectAttr(f"{f1}.outColor", f"{self.lambert1}.color", force=True)
        cmds.connectAttr(f"{f2}.outColor", f"{self.lambert1}.ambientColor", force=True)

        paths = MatUtils.get_texture_paths(
            materials=[self.lambert1],
            texture_names=["c:/textures/a.png"],  # duplicate of f1
        )
        # No duplicates
        self.assertEqual(len(paths), len(set(paths)))
        # Both unique entries present
        self.assertTrue(any("a.png" in p for p in paths))
        self.assertTrue(any("b.png" in p for p in paths))

    # -------------------------------------------------------------------------
    # Material ID Tests
    # -------------------------------------------------------------------------

    def test_find_by_mat_id(self):
        """Test finding objects by material assignment."""
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        cmds.sets(self.cube, edit=True, forceElement=self.sg2)

        # Find sphere by lambert1
        found = MatUtils.find_by_mat_id(self.lambert1)
        # Result might be faces or transforms depending on assignment
        # Since we assigned to whole object, it might return the transform or shape
        transforms = [NodeUtils.get_transform_node(x) for x in found]
        self.assertIn(self.sphere, transforms)

        # Test shell=True (should return transforms)
        found_shell = MatUtils.find_by_mat_id(self.lambert1, shell=True)
        # Normalize to short leaf names: production may return long DAG paths.
        leaves = [str(x).split("|")[-1] for x in found_shell]
        self.assertIn(str(self.sphere).split("|")[-1], leaves)

        # Test face assignment
        cmds.sets(f"{self.sphere}.f[0]", edit=True, forceElement=self.sg2)
        found_faces = MatUtils.find_by_mat_id(
            self.lambert2, objects=[self.sphere], shell=False
        )
        self.assertTrue(len(found_faces) > 0)
        # Production returns strings like "node.f[N]".
        self.assertIn(".f[", str(found_faces[0]))

    def test_find_unassigned_reports_default_shaded_and_orphans(self):
        """"No material" in Maya is two states, and both must be found.

        New geometry joins ``initialShadingGroup``, so it reports the default
        shader rather than nothing; geometry pulled out of every shading engine
        reports nothing at all. Both read as "unshaded" to a user.
        """
        default_shaded = cmds.polyCube(name="unassigned_default")[0]
        orphan = cmds.polyCube(name="unassigned_orphan")[0]
        orphan_shape = cmds.listRelatives(orphan, shapes=True, fullPath=True)[0]
        for sg in cmds.listConnections(orphan_shape, type="shadingEngine") or []:
            cmds.sets(orphan_shape, remove=sg)
        cmds.sets(self.cube, edit=True, forceElement=self.sg1)  # a real material

        leaves = [str(o).split("|")[-1] for o in MatUtils.find_unassigned()]
        self.assertIn(default_shaded, leaves)
        self.assertIn(orphan, leaves)
        self.assertNotIn(self.cube, leaves)

    def test_find_unassigned_include_default_false_is_orphans_only(self):
        """Opting out of the default-shaded case leaves only truly orphaned shapes."""
        default_shaded = cmds.polyCube(name="unassigned_default_only")[0]
        orphan = cmds.polyCube(name="unassigned_orphan_only")[0]
        orphan_shape = cmds.listRelatives(orphan, shapes=True, fullPath=True)[0]
        for sg in cmds.listConnections(orphan_shape, type="shadingEngine") or []:
            cmds.sets(orphan_shape, remove=sg)

        leaves = [
            str(o).split("|")[-1]
            for o in MatUtils.find_unassigned(include_default=False)
        ]
        self.assertIn(orphan, leaves)
        self.assertNotIn(default_shaded, leaves)

    def test_find_unassigned_scopes_to_given_objects(self):
        """A pool restricts the search; transforms and shapes both resolve."""
        inside = cmds.polyCube(name="unassigned_inside")[0]
        outside = cmds.polyCube(name="unassigned_outside")[0]

        leaves = [str(o).split("|")[-1] for o in MatUtils.find_unassigned([inside])]
        self.assertEqual(leaves, [inside])

        shape = cmds.listRelatives(inside, shapes=True, fullPath=True)[0]
        by_shape = [str(o).split("|")[-1] for o in MatUtils.find_unassigned([shape])]
        self.assertEqual(by_shape, [inside])
        self.assertNotIn(outside, leaves)

    def test_find_unassigned_accepts_components_like_find_by_mat_id(self):
        """A face selection must resolve to its object, not silently scope to nothing."""
        cube = cmds.polyCube(name="unassigned_component")[0]
        found = [str(o).split("|")[-1] for o in MatUtils.find_unassigned([f"{cube}.f[0]"])]
        self.assertEqual(found, [cube])

    def test_find_unassigned_scoped_to_nothing_is_not_a_scene_scan(self):
        """An input that resolves to no object must return [] — an argless
        ``cmds.ls`` would list the whole scene instead."""
        cmds.polyCube(name="unassigned_bystander")
        self.assertEqual(MatUtils.find_unassigned(["does_not_exist"]), [])

    def test_find_unassigned_skips_partially_assigned_objects(self):
        """Partially shaded geometry is assigned — its unshaded faces are a
        different question than "which objects did I forget to shade"."""
        partial = cmds.polyCube(name="unassigned_partial")[0]
        cmds.sets(f"{partial}.f[0:2]", edit=True, forceElement=self.sg1)

        leaves = [str(o).split("|")[-1] for o in MatUtils.find_unassigned()]
        self.assertNotIn(partial, leaves)

    def test_find_unassigned_is_the_complement_of_find_by_mat_id(self):
        """No object can be both unassigned and a user of a real material."""
        cmds.sets(self.sphere, edit=True, forceElement=self.sg1)
        users = {
            str(o).split("|")[-1]
            for o in MatUtils.find_by_mat_id(self.lambert1, shell=True)
        }
        unassigned = {str(o).split("|")[-1] for o in MatUtils.find_unassigned()}
        self.assertEqual(users & unassigned, set())

    def test_module_exposure(self):
        """Test that MatUtils methods are exposed at the module level."""
        # Test assign_mat exposure
        mtk.assign_mat(self.cube, "exposed_mat")
        self.assertTrue(cmds.objExists("exposed_mat"))

        # Test create_mat exposure
        mat = mtk.create_mat("blinn", name="exposed_blinn")
        self.assertTrue(cmds.objExists("exposed_blinn"))

        # Test get_mats exposure
        mats = mtk.get_mats(self.cube)
        self.assertTrue(mats)
        self.assertEqual(mats[0], "exposed_mat")

        # Test find_by_mat_id exposure
        # Ensure we pass the name string, as find_by_mat_id expects a string name or we need to verify PyNode support
        found = mtk.find_by_mat_id(mats[0], [self.cube])
        self.assertTrue(found)

    def test_assign_mat_with_pynode(self):
        """Test assign_mat with PyNode input for material."""
        # Create a material
        mat = MatUtils.create_mat("blinn", name="pynode_mat")
        # Assign using the PyNode object
        MatUtils.assign_mat(self.cube, mat)

        mats = MatUtils.get_mats(self.cube)
        self.assertEqual(mats[0], mat)

    def test_find_by_mat_id_with_pynode(self):
        """Test find_by_mat_id with PyNode input for material."""
        # Assign material
        MatUtils.assign_mat(self.cube, self.lambert1)

        # Search using PyNode
        # This is expected to fail if find_by_mat_id doesn't handle PyNodes,
        # but we want to know if it does or if we need to fix it.
        try:
            found = MatUtils.find_by_mat_id(self.lambert1, [self.cube])
            self.assertTrue(found)
        except TypeError:
            self.fail("find_by_mat_id failed with PyNode input")

        # Test get_scene_mats exposure
        scene_mats = mtk.get_scene_mats()
        self.assertTrue(len(scene_mats) > 0)

        # Test get_mat_swatch_icon exposure
        try:
            mtk.get_mat_swatch_icon(scene_mats[0])
        except Exception as e:
            self.fail(f"get_mat_swatch_icon raised exception: {e}")

        # Test reload_textures exposure
        try:
            mtk.reload_textures()
        except Exception as e:
            self.fail(f"reload_textures raised exception: {e}")

    # -------------------------------------------------------------------------
    # Regression: _resolve_texture_targets traverses utility nodes
    # -------------------------------------------------------------------------

    def test_resolve_texture_targets_finds_file_nodes_behind_utility_nodes(self):
        """Verify _resolve_texture_targets finds file nodes connected through
        intermediate utility nodes (bump2d, colorCorrect, etc.).

        Bug: listConnections(material, type='file') only finds directly
        connected file nodes. File nodes behind bump2d, colorCorrect,
        aiNormalMap, etc. were silently missed, causing find_texture_files
        and related helpers to skip one or two textures.
        Fixed: 2026-02-23
        """
        from maya import cmds

        # Create material with a directly-connected diffuse file node
        mat = cmds.shadingNode("lambert", asShader=True, name="resolve_test_mat")
        diffuse_file = cmds.shadingNode("file", asTexture=True, name="diffuse_file")
        cmds.setAttr(f"{diffuse_file}.fileTextureName", "diffuse.png", type="string")
        cmds.connectAttr(f"{diffuse_file}.outColor", f"{mat}.color", force=True)

        # Create a bump map behind a bump2d node (indirect connection)
        bump_file = cmds.shadingNode("file", asTexture=True, name="bump_file")
        cmds.setAttr(f"{bump_file}.fileTextureName", "bump.png", type="string")
        bump2d = cmds.shadingNode("bump2d", asUtility=True, name="test_bump2d")
        cmds.connectAttr(f"{bump_file}.outAlpha", f"{bump2d}.bumpValue", force=True)
        cmds.connectAttr(f"{bump2d}.outNormal", f"{mat}.normalCamera", force=True)

        # Resolve — both file nodes should be found
        result = MatUtils._resolve_texture_targets(materials=[mat], as_strings=True)
        file_node_names = result["file_nodes"]

        self.assertIn(
            "diffuse_file",
            file_node_names,
            "Directly connected file node should be found",
        )
        self.assertIn(
            "bump_file",
            file_node_names,
            "File node behind bump2d should be found (was missed by listConnections)",
        )

    def test_resolve_texture_targets_finds_file_behind_color_correct(self):
        """Verify file nodes behind colorCorrect utility nodes are found.

        Bug: Same as above — listConnections missed any file node not
        directly connected to the material.
        Fixed: 2026-02-23
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="cc_test_mat")

        # File -> gammaCorrect -> material.color
        # (gammaCorrect is universally available; colorCorrect may lack
        # the expected attribute name across Maya versions)
        cc_file = cmds.shadingNode("file", asTexture=True, name="cc_file")
        cmds.setAttr(f"{cc_file}.fileTextureName", "diffuse_cc.png", type="string")
        gc = cmds.shadingNode("gammaCorrect", asUtility=True, name="test_gc")
        cmds.connectAttr(f"{cc_file}.outColor", f"{gc}.value", force=True)
        cmds.connectAttr(f"{gc}.outValue", f"{mat}.color", force=True)

        result = MatUtils._resolve_texture_targets(materials=[mat], as_strings=True)
        self.assertIn(
            "cc_file",
            result["file_nodes"],
            "File node behind colorCorrect should be found",
        )

    # -------------------------------------------------------------------------
    # Regression: get_file_nodes shader deduplication optimization
    # -------------------------------------------------------------------------

    def test_get_file_nodes_shared_shader_across_shading_engines(self):
        """Verify get_file_nodes returns correct results when a shader is
        connected to multiple shading engines.

        Bug: get_file_nodes called listHistory for every shading engine
        connection, causing redundant work when the same shader appeared
        in multiple SGs. With the deduplication fix, each unique shader
        is traversed only once.
        Fixed: 2026-02-27
        """
        from maya import cmds

        # Create a shader with a file node
        shared_mat = cmds.shadingNode("lambert", asShader=True, name="shared_mat")
        shared_file = cmds.shadingNode("file", asTexture=True, name="shared_file")
        cmds.setAttr(f"{shared_file}.fileTextureName", "shared_tex.png", type="string")
        cmds.connectAttr(f"{shared_file}.outColor", f"{shared_mat}.color", force=True)

        # Connect the same shader to TWO shading engines
        sg1 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg1"
        )
        cmds.connectAttr(f"{shared_mat}.outColor", f"{sg1}.surfaceShader", force=True)
        sg2 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg2"
        )
        cmds.connectAttr(f"{shared_mat}.outColor", f"{sg2}.surfaceShader", force=True)

        # Query file nodes — shared_file should appear exactly once
        result = MatUtils.get_file_nodes(
            materials=[shared_mat], return_type="shaderName|fileNodeName"
        )
        file_node_names = [row[1] for row in result]
        self.assertIn(
            "shared_file",
            file_node_names,
            "File node connected to shared shader must be found",
        )
        self.assertEqual(
            file_node_names.count("shared_file"),
            1,
            "File node should appear exactly once despite shader in multiple SGs",
        )

    def test_get_file_nodes_batch_type_filter(self):
        """Verify get_file_nodes correctly filters file nodes using batch
        cmds.ls(type='file') instead of per-node cmds.nodeType(cmds) calls.

        Bug: Per-node nodeType calls were O(N) in the shader history size
        and added massive overhead in heavy scenes. Replaced with batch
        cmds.ls(history, type='file').
        Fixed: 2026-02-27
        """
        from maya import cmds

        # Create a shader with a file node AND a non-file utility node
        mat = cmds.shadingNode("lambert", asShader=True, name="batch_mat")
        file_node = cmds.shadingNode("file", asTexture=True, name="batch_file")
        cmds.setAttr(f"{file_node}.fileTextureName", "batch_tex.png", type="string")
        # Insert a bump2d between file and material
        bump = cmds.shadingNode("bump2d", asUtility=True, name="batch_bump")
        cmds.connectAttr(f"{file_node}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(f"{bump}.outNormal", f"{mat}.normalCamera", force=True)

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="batch_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        # get_file_nodes should find the file node through utility chain
        result = MatUtils.get_file_nodes(
            materials=[mat], return_type="shaderName|fileNodeName"
        )
        file_node_names = [row[1] for row in result]
        self.assertIn(
            "batch_file",
            file_node_names,
            "File node behind utility node must be found via batch ls filter",
        )

    def test_get_file_nodes_multiple_files_per_shader(self):
        """Verify get_file_nodes returns all file nodes when a single shader
        has multiple file textures (e.g. diffuse + bump).

        Ensures the deduplication optimization doesn't accidentally skip
        file nodes that share the same parent shader.
        Fixed: 2026-02-27
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="multi_mat")

        # Diffuse file node
        diff_file = cmds.shadingNode("file", asTexture=True, name="multi_diffuse")
        cmds.setAttr(f"{diff_file}.fileTextureName", "diffuse.png", type="string")
        cmds.connectAttr(f"{diff_file}.outColor", f"{mat}.color", force=True)

        # Bump file node (through bump2d)
        bump_file = cmds.shadingNode("file", asTexture=True, name="multi_bump")
        cmds.setAttr(f"{bump_file}.fileTextureName", "bump.png", type="string")
        bump = cmds.shadingNode("bump2d", asUtility=True, name="multi_bump2d")
        cmds.connectAttr(f"{bump_file}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(f"{bump}.outNormal", f"{mat}.normalCamera", force=True)

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="multi_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        result = MatUtils.get_file_nodes(materials=[mat], return_type="fileNodeName")
        self.assertIn("multi_diffuse", result, "Diffuse file node must be found")
        self.assertIn("multi_bump", result, "Bump file node must be found")
        self.assertEqual(len(result), 2, "Exactly two file nodes expected")

    def test_get_file_nodes_no_duplicates_in_scene_scan(self):
        """Verify a full scene scan (no material filter) returns each file
        node exactly once, even when the same file is reachable through
        multiple shading engines.

        Fixed: 2026-02-27
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="dedup_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="dedup_file")
        cmds.setAttr(f"{fn}.fileTextureName", "dedup.png", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)

        for i in range(3):
            sg = cmds.sets(
                renderable=True,
                noSurfaceShader=True,
                empty=True,
                name=f"dedup_sg{i}",
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        # Full scene scan
        result = MatUtils.get_file_nodes(return_type="shaderName|fileNodeName")
        dedup_rows = [r for r in result if r[1] == "dedup_file"]
        self.assertEqual(
            len(dedup_rows),
            1,
            "File node connected via 3 SGs should appear exactly once",
        )


class TestConnectToChannels(MayaTkTestCase):
    """connect_to_channels — drive a compound slot from a single-channel source.

    The shared primitive behind the GameShader wiring, the viewport-opacity path
    and the bridge's manifest rebuild. Its contract is a bool, so a slot that
    refuses must REPORT rather than raise — and must not leave the compound
    half-driven, which is worse than not connecting at all.
    """

    def setUp(self):
        super().setUp()
        self.shader = cmds.shadingNode("standardSurface", asShader=True, name="ctc_ss")
        self.file_node = cmds.shadingNode("file", asTexture=True, name="ctc_file")

    def _sources(self, attr):
        return (
            cmds.listConnections(
                f"{self.shader}.{attr}", source=True, destination=False, plugs=True
            )
            or []
        )

    def test_scalar_source_drives_every_child_of_a_compound(self):
        self.assertTrue(
            MatUtils.connect_to_channels(
                f"{self.file_node}.outAlpha", self.shader, "opacity"
            )
        )
        for child in ("opacityR", "opacityG", "opacityB"):
            self.assertEqual(
                [p.split(".")[-1] for p in self._sources(child)], ["outAlpha"]
            )

    def test_scalar_slot_connects_directly(self):
        self.assertTrue(
            MatUtils.connect_to_channels(
                f"{self.file_node}.outAlpha", self.shader, "specularRoughness"
            )
        )
        self.assertEqual(
            [p.split(".")[-1] for p in self._sources("specularRoughness")], ["outAlpha"]
        )

    def test_missing_attribute_returns_false(self):
        self.assertFalse(
            MatUtils.connect_to_channels(
                f"{self.file_node}.outAlpha", self.shader, "notAnAttribute"
            )
        )

    def test_refused_child_rolls_back_instead_of_raising(self):
        """A locked child must yield False, not a half-driven compound."""
        cmds.setAttr(f"{self.shader}.opacityB", lock=True)
        try:
            self.assertFalse(
                MatUtils.connect_to_channels(
                    f"{self.file_node}.outAlpha", self.shader, "opacity"
                )
            )
            for child in ("opacityR", "opacityG", "opacityB"):
                self.assertEqual(self._sources(child), [], f"{child} left connected")
        finally:
            cmds.setAttr(f"{self.shader}.opacityB", lock=False)

    def test_failed_broadcast_restores_the_parent_it_broke(self):
        """The parent is disconnected up front to avoid two textures driving one
        slot; if the children then refuse, that disconnect must be undone."""
        prior = cmds.shadingNode("file", asTexture=True, name="ctc_prior")
        cmds.connectAttr(f"{prior}.outColor", f"{self.shader}.opacity", force=True)
        cmds.setAttr(f"{self.shader}.opacityB", lock=True)
        try:
            self.assertFalse(
                MatUtils.connect_to_channels(
                    f"{self.file_node}.outAlpha", self.shader, "opacity"
                )
            )
            self.assertEqual(
                [p.split(".")[-1] for p in self._sources("opacity")],
                ["outColor"],
                "the pre-existing parent connection was not restored",
            )
        finally:
            cmds.setAttr(f"{self.shader}.opacityB", lock=False)


class TestViewportOpacity(MayaTkTestCase):
    """Wiring an opacity map into the slot the viewport honours."""

    def setUp(self):
        super().setUp()
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="vpo_")
        self.cube = cmds.polyCube(name="vpo_cube")[0]

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _png(self, name):
        from PIL import Image

        path = os.path.join(self.tmp, name)
        Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(path)
        return path

    def _file_node(self, path, name):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    def _mat(self, node_type, name):
        mat = cmds.shadingNode(node_type, asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(self.cube, edit=True, forceElement=sg)
        return mat

    def test_find_opacity_source_in_network(self):
        """An Opacity map already in the network is the source."""
        mat = self._mat("standardSurface", "vpo_std")
        color = self._file_node(self._png("set_Base_color.png"), "vpo_color")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_opacity")
        cmds.connectAttr(f"{color}.outColor", f"{mat}.baseColor", force=True)
        cmds.connectAttr(f"{opacity}.outColor", f"{mat}.coatColor", force=True)

        self.assertEqual(MatUtils.find_opacity_source(mat), opacity)

    def test_find_opacity_source_none_without_opacity_map(self):
        """A material with no opacity map reports none."""
        mat = self._mat("standardSurface", "vpo_std_plain")
        color = self._file_node(self._png("set_Base_color.png"), "vpo_color_only")
        cmds.connectAttr(f"{color}.outColor", f"{mat}.baseColor", force=True)

        self.assertIsNone(MatUtils.find_opacity_source(mat))

    def test_enable_viewport_opacity_standard_surface(self):
        """standardSurface: alpha drives every opacity channel."""
        mat = self._mat("standardSurface", "vpo_std_wire")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_op_wire")
        cmds.connectAttr(f"{opacity}.outColor", f"{mat}.coatColor", force=True)

        results = MatUtils.enable_viewport_opacity([mat])

        self.assertEqual(results, {"vpo_std_wire": "enabled"})
        for channel in ("opacityR", "opacityG", "opacityB"):
            self.assertEqual(
                cmds.listConnections(
                    f"{mat}.{channel}", source=True, destination=False, plugs=True
                ),
                [f"{opacity}.outAlpha"],
                f"{channel} not driven by the opacity map",
            )

    def test_enable_viewport_opacity_inverts_for_classic_shader(self):
        """lambert has no opacity slot — drive transparency with 1-alpha."""
        mat = self._mat("lambert", "vpo_lambert")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_op_lam")
        cmds.connectAttr(f"{opacity}.outColor", f"{mat}.color", force=True)

        results = MatUtils.enable_viewport_opacity([mat])

        self.assertEqual(results, {"vpo_lambert": "enabled"})
        rev = cmds.listConnections(
            f"{mat}.transparencyR", source=True, destination=False, type="reverse"
        )
        self.assertTrue(rev, "transparency should be driven through a reverse node")

    def test_enable_viewport_opacity_stingray_loads_transparent_graph(self):
        """StingrayPBS gains its opacity slots and keeps its other textures."""
        shader = mtk.GameShader()
        sr_node = shader.setup_stringray_node("vpo_sr", opacity=False)
        color = self._file_node(self._png("set_Base_color.png"), "vpo_sr_color")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_sr_opacity")
        cmds.connectAttr(f"{color}.outColor", f"{sr_node}.TEX_color_map", force=True)
        cmds.setAttr(f"{sr_node}.use_color_map", 1)
        # The opacity map is in the network but has nowhere to go on this graph.
        cmds.connectAttr(
            f"{opacity}.outColor", f"{sr_node}.TEX_emissive_map", force=True
        )

        results = MatUtils.enable_viewport_opacity([sr_node])

        self.assertEqual(results, {"vpo_sr": "enabled"})
        self.assertTrue(cmds.attributeQuery("opacity", node=sr_node, exists=True))
        self.assertEqual(cmds.getAttr(f"{sr_node}.use_opacity_map"), 1)
        self.assertTrue(
            cmds.listConnections(
                f"{sr_node}.opacity", source=True, destination=False
            ),
            "opacity slot left unconnected",
        )
        # The base color survived the graph swap.
        self.assertTrue(
            cmds.listConnections(f"{sr_node}.TEX_color_map", source=True),
            "base color lost when the transparent graph was loaded",
        )

    def test_enable_viewport_opacity_finds_map_on_disk(self):
        """An opacity map beside the material's textures is imported."""
        mat = self._mat("standardSurface", "vpo_disk")
        color_path = self._png("set_Base_color.png")
        self._png("set_Opacity.png")  # on disk only — not in the network
        color = self._file_node(color_path, "vpo_disk_color")
        cmds.connectAttr(f"{color}.outColor", f"{mat}.baseColor", force=True)

        results = MatUtils.enable_viewport_opacity([mat])

        self.assertEqual(results, {"vpo_disk": "enabled"})
        source = cmds.listConnections(
            f"{mat}.opacityR", source=True, destination=False
        )
        self.assertTrue(source)
        self.assertTrue(
            cmds.getAttr(f"{source[0]}.fileTextureName").endswith("set_Opacity.png")
        )

    def test_enable_viewport_opacity_alpha_source_per_map_type(self):
        """Grayscale Opacity reads luminance; packed albedo reads real alpha."""
        gray = self._mat("standardSurface", "vpo_gray")
        gray_map = self._file_node(self._png("set_Opacity.png"), "vpo_gray_op")
        cmds.connectAttr(f"{gray_map}.outColor", f"{gray}.coatColor", force=True)
        cmds.setAttr(f"{gray_map}.alphaIsLuminance", 0)

        packed = self._mat("standardSurface", "vpo_packed")
        packed_map = self._file_node(
            self._png("other_Albedo_Transparency.png"), "vpo_packed_at"
        )
        cmds.connectAttr(f"{packed_map}.outColor", f"{packed}.baseColor", force=True)
        cmds.setAttr(f"{packed_map}.alphaIsLuminance", 1)

        MatUtils.enable_viewport_opacity([gray, packed])

        self.assertEqual(
            cmds.getAttr(f"{gray_map}.alphaIsLuminance"),
            1,
            "a grayscale Opacity map must be read as luminance",
        )
        self.assertEqual(
            cmds.getAttr(f"{packed_map}.alphaIsLuminance"),
            0,
            "a packed Albedo_Transparency map must read its real alpha",
        )

    def test_enable_viewport_opacity_is_idempotent(self):
        """A second run rewires nothing and builds no duplicate reverse node."""
        mat = self._mat("lambert", "vpo_twice")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_twice_op")
        cmds.connectAttr(f"{opacity}.outColor", f"{mat}.color", force=True)

        MatUtils.enable_viewport_opacity([mat])
        before = len(cmds.ls(type="reverse") or [])
        results = MatUtils.enable_viewport_opacity([mat])

        self.assertEqual(results, {"vpo_twice": "already enabled"})
        self.assertEqual(len(cmds.ls(type="reverse") or []), before)

    def test_enable_viewport_opacity_skips_materials_without_a_map(self):
        """No opacity map anywhere — the material is left untouched."""
        mat = self._mat("standardSurface", "vpo_skip")
        color = self._file_node(self._png("set_Base_color.png"), "vpo_skip_color")
        cmds.connectAttr(f"{color}.outColor", f"{mat}.baseColor", force=True)

        results = MatUtils.enable_viewport_opacity([mat])

        self.assertEqual(results, {"vpo_skip": "no opacity map"})
        self.assertFalse(
            cmds.listConnections(f"{mat}.opacityR", source=True, destination=False)
        )

    def test_enable_viewport_opacity_accepts_objects(self):
        """Objects resolve to their materials (same call, either input)."""
        mat = self._mat("standardSurface", "vpo_obj")
        opacity = self._file_node(self._png("set_Opacity.png"), "vpo_obj_op")
        cmds.connectAttr(f"{opacity}.outColor", f"{mat}.coatColor", force=True)

        results = MatUtils.enable_viewport_opacity([self.cube])

        self.assertEqual(results.get("vpo_obj"), "enabled")

    def test_get_mats_by_scope(self):
        """Scope resolves selection / scene consistently."""
        mat = self._mat("standardSurface", "vpo_scope")

        cmds.select(self.cube, replace=True)
        self.assertIn(mat, MatUtils.get_mats_by_scope("selected"))

        cmds.select(clear=True)
        self.assertEqual(MatUtils.get_mats_by_scope("selected"), [])
        self.assertIn(mat, MatUtils.get_mats_by_scope("scene"))

    def test_set_transparency_algorithm(self):
        """Named viewport transparency modes map to their index."""
        self.assertTrue(MatUtils.set_transparency_algorithm("depth_peeling"))
        self.assertEqual(
            cmds.getAttr("hardwareRenderingGlobals.transparencyAlgorithm"), 3
        )
        self.assertFalse(MatUtils.set_transparency_algorithm("nonsense"))


if __name__ == "__main__":
    unittest.main()
