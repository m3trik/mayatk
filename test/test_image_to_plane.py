# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.image_to_plane module

Tests for ImageToPlane class and shared MatUtils helpers:
- create_file_node: file node + place2dTexture wiring
- create_shading_group: SG creation and object assignment
- create_stingray_shader: StingrayPBS shader creation
- ImageToPlane.create: batch plane creation with correct aspect ratios
- ImageToPlane.remove: cleanup of planes and upstream material graph
- ImageToPlane._get_image_dimensions: native Maya image query
- ImageToPlane._create_shader: shader dispatch (stingray / standard)
- ImageToPlane._connect_texture: texture wiring for both shader types
"""
import os
import sys
import tempfile
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import pymel.core as pm
    from maya import cmds
except ImportError:
    pm = None
    cmds = None

import mayatk as mtk
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.image_to_plane._image_to_plane import ImageToPlane

from base_test import MayaTkTestCase
import maya.cmds as cmds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_image(width, height, name="test_img", directory=None):
    """Write a minimal BMP file with the given pixel dimensions.

    Returns the absolute path to the temporary image.  The caller is
    responsible for cleanup.
    """
    if directory is None:
        directory = tempfile.mkdtemp(prefix="itp_test_")

    path = os.path.join(directory, f"{name}.bmp")

    # Minimal 24-bit BMP (no compression, no palette)
    row_bytes = (width * 3 + 3) & ~3  # rows padded to 4-byte boundary
    pixel_data_size = row_bytes * height
    file_size = 54 + pixel_data_size  # 14 header + 40 DIB header + pixels

    import struct

    with open(path, "wb") as f:
        # -- BMP header (14 bytes) --
        f.write(b"BM")
        f.write(struct.pack("<I", file_size))
        f.write(struct.pack("<HH", 0, 0))  # reserved
        f.write(struct.pack("<I", 54))  # pixel data offset

        # -- DIB header (BITMAPINFOHEADER, 40 bytes) --
        f.write(struct.pack("<I", 40))  # header size
        f.write(struct.pack("<i", width))
        f.write(struct.pack("<i", height))
        f.write(struct.pack("<HH", 1, 24))  # planes, bpp
        f.write(struct.pack("<I", 0))  # no compression
        f.write(struct.pack("<I", pixel_data_size))
        f.write(struct.pack("<ii", 2835, 2835))  # 72 DPI
        f.write(struct.pack("<II", 0, 0))  # colours

        # -- Pixel data (blue fill) --
        row = (b"\xff\x00\x00" * width).ljust(row_bytes, b"\x00")
        for _ in range(height):
            f.write(row)

    return path


# ===========================================================================
# Shared MatUtils Helpers
# ===========================================================================


class TestCreateFileNode(MayaTkTestCase):
    """Tests for MatUtils.create_file_node."""

    def setUp(self):
        super().setUp()
        self._tmp_dir = tempfile.mkdtemp(prefix="itp_test_")
        self._img = _create_test_image(64, 64, "tile", self._tmp_dir)

    def tearDown(self):
        super().tearDown()
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_creates_file_and_place2d(self):
        """create_file_node returns a file node and a place2dTexture."""
        fn, p2d = MatUtils.create_file_node(self._img, name="myTex")
        self.assertEqual(cmds.nodeType(fn), "file")
        self.assertEqual(cmds.nodeType(p2d), "place2dTexture")

    def test_texture_path_set(self):
        """File node stores the image path."""
        fn, _ = MatUtils.create_file_node(self._img)
        stored = cmds.getAttr(f"{fn}.fileTextureName")
        self.assertEqual(os.path.normpath(stored), os.path.normpath(self._img))

    def test_place2d_connected_to_file(self):
        """place2dTexture.outUV is connected to file.uvCoord."""
        fn, p2d = MatUtils.create_file_node(self._img, name="wiring")
        conns = cmds.listConnections(f"{p2d}.outUV", plugs=True) or []
        dest_names = [c for c in conns]
        self.assertTrue(
            any("uvCoord" in d for d in dest_names),
            f"outUV not connected to uvCoord — destinations: {dest_names}",
        )

    def test_default_name_from_stem(self):
        """When no name is given, the node name derives from the file stem."""
        fn, p2d = MatUtils.create_file_node(self._img)
        self.assertIn("tile", fn)
        self.assertIn("tile", p2d)

    def test_color_space_override(self):
        """Explicit color_space kwarg sets the attribute."""
        fn, _ = MatUtils.create_file_node(self._img, color_space="Raw")
        self.assertEqual(cmds.getAttr(f"{fn}.colorSpace"), "Raw")


class TestCreateShadingGroup(MayaTkTestCase):
    """Tests for MatUtils.create_shading_group."""

    def test_creates_sg_connected_to_shader(self):
        """SG is created and surfaceShader is connected."""
        shader = cmds.shadingNode("lambert", asShader=True, name="test_lam")
        sg = MatUtils.create_shading_group(shader)
        self.assertEqual(cmds.nodeType(sg), "shadingEngine")
        conns = cmds.listConnections(f"{sg}.surfaceShader") or []
        self.assertIn(str(shader), [str(c) for c in conns])

    def test_custom_sg_name(self):
        """Custom name is respected."""
        shader = cmds.shadingNode("lambert", asShader=True, name="sg_test")
        sg = MatUtils.create_shading_group(shader, name="mySG")
        self.assertEqual(sg, "mySG")

    def test_assign_to_object(self):
        """Objects passed via assign_to are members of the new SG."""
        shader = cmds.shadingNode("lambert", asShader=True, name="assign_mat")
        cube = cmds.polyCube(name="assign_cube")[0]
        sg = MatUtils.create_shading_group(shader, assign_to=cube)
        members = cmds.sets(sg, query=True) or []
        # polyPlane shapes get added
        self.assertTrue(len(members) > 0)

    def test_assign_to_list(self):
        """Assign multiple objects at once."""
        shader = cmds.shadingNode("lambert", asShader=True, name="multi_mat")
        a = cmds.polyCube(name="a")[0]
        b = (
            cmds.polySphere(name="b")[0]
            if hasattr(pm, "poleSphere")
            else cmds.polySphere(name="b")[0]
        )
        sg = MatUtils.create_shading_group(shader, assign_to=[a, b])
        members = cmds.sets(sg, query=True) or []
        self.assertTrue(len(members) >= 2)


def _ensure_stingray_loadable():
    """Ensure shaderFXPlugin / Stingray support is loaded.

    StingrayPBS is provided by shaderFXPlugin in Maya 2024+; both stock
    Maya and mayapy auto-load it, but test runners that mock/clear plugin
    state can leave it unloaded. Loading it explicitly is harmless.
    """
    for p in ("shaderFXPlugin", "stingray_pbs"):
        try:
            if not cmds.pluginInfo(p, query=True, loaded=True):
                cmds.loadPlugin(p, quiet=True)
        except RuntimeError:
            pass


class TestCreateStingrayShader(MayaTkTestCase):
    """Tests for MatUtils.create_stingray_shader."""

    def test_creates_stingray_node(self):
        """Stingray PBS node is created."""
        _ensure_stingray_loadable()
        shader = MatUtils.create_stingray_shader("test_sr")
        self.assertTrue(cmds.objExists(shader))
        self.assertIn("Stingray", cmds.nodeType(shader))


# ===========================================================================
# ImageToPlane Core Logic
# ===========================================================================


class TestImageToPlane(MayaTkTestCase):
    """Tests for ImageToPlane.create / remove."""

    def setUp(self):
        super().setUp()
        self._tmp_dir = tempfile.mkdtemp(prefix="itp_test_")

    def tearDown(self):
        super().tearDown()
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # -- Dimensions --------------------------------------------------------

    def test_get_image_dimensions_landscape(self):
        """A 200×100 image reports (200, 100)."""
        img = _create_test_image(200, 100, "landscape", self._tmp_dir)
        w, h = ImageToPlane._get_image_dimensions(img)
        self.assertEqual(w, 200)
        self.assertEqual(h, 100)

    def test_get_image_dimensions_portrait(self):
        """A 100×200 image reports (100, 200)."""
        img = _create_test_image(100, 200, "portrait", self._tmp_dir)
        w, h = ImageToPlane._get_image_dimensions(img)
        self.assertEqual(w, 100)
        self.assertEqual(h, 200)

    def test_get_image_dimensions_square(self):
        """A 128×128 image reports (128, 128)."""
        img = _create_test_image(128, 128, "square", self._tmp_dir)
        w, h = ImageToPlane._get_image_dimensions(img)
        self.assertEqual(w, 128)
        self.assertEqual(h, 128)

    def test_get_image_dimensions_missing_file(self):
        """Missing image falls back to (1, 1)."""
        w, h = ImageToPlane._get_image_dimensions("/nonexistent/image.png")
        self.assertEqual((w, h), (1, 1))

    # -- Single plane creation (standard shader) ---------------------------

    def test_create_single_standard(self):
        """Create one plane with standard material."""
        img = _create_test_image(200, 100, "wide", self._tmp_dir)
        results = ImageToPlane.create([img], mat_type="standard", suffix="_MAT")
        self.assertIn("wide", results)
        plane = results["wide"]
        self.assertTrue(cmds.objExists(plane))

    def test_plane_aspect_ratio_landscape(self):
        """Plane width/height matches a 2:1 landscape image."""
        img = _create_test_image(200, 100, "ratio_test", self._tmp_dir)
        results = ImageToPlane.create(
            [img],
            mat_type="standard",
            plane_height=10.0,
        )
        plane = results["ratio_test"]
        bb = cmds.exactWorldBoundingBox(plane)
        # bb = [xmin, ymin, zmin, xmax, ymax, zmax]
        # With axis=[0,0,1] the plane lies in XY
        plane_w = bb[3] - bb[0]  # x extent
        plane_h = bb[4] - bb[1]  # y extent
        # One of these should be ~20 (width) and ~10 (height),
        # or the other pair depending on axis.  Check the ratio.
        larger = max(plane_w, plane_h)
        smaller = min(plane_w, plane_h)
        # For non-degenerate dimensions only
        if smaller > 0.01:
            ratio = larger / smaller
            self.assertAlmostEqual(ratio, 2.0, places=1)

    def test_plane_aspect_ratio_portrait(self):
        """Plane width/height matches a 1:2 portrait image."""
        img = _create_test_image(100, 200, "portrait_ratio", self._tmp_dir)
        results = ImageToPlane.create(
            [img],
            mat_type="standard",
            plane_height=10.0,
        )
        plane = results["portrait_ratio"]
        bb = cmds.exactWorldBoundingBox(plane)
        plane_w = bb[3] - bb[0]
        plane_h = bb[4] - bb[1]
        larger = max(plane_w, plane_h)
        smaller = min(plane_w, plane_h)
        if smaller > 0.01:
            ratio = larger / smaller
            self.assertAlmostEqual(ratio, 2.0, places=1)

    def test_plane_aspect_ratio_square(self):
        """Square image produces a square plane."""
        img = _create_test_image(128, 128, "sq", self._tmp_dir)
        results = ImageToPlane.create(
            [img],
            mat_type="standard",
            plane_height=10.0,
        )
        plane = results["sq"]
        bb = cmds.exactWorldBoundingBox(plane)
        plane_w = bb[3] - bb[0]
        plane_h = bb[4] - bb[1]
        larger = max(plane_w, plane_h)
        smaller = min(plane_w, plane_h)
        if smaller > 0.01:
            self.assertAlmostEqual(larger / smaller, 1.0, places=1)

    # -- Material naming & suffix ------------------------------------------

    def test_material_suffix_default(self):
        """Default suffix is _MAT."""
        img = _create_test_image(64, 64, "brick", self._tmp_dir)
        ImageToPlane.create([img], mat_type="standard")
        self.assertTrue(cmds.objExists("brick_MAT"))

    def test_material_suffix_custom(self):
        """Custom suffix is applied."""
        img = _create_test_image(64, 64, "stone", self._tmp_dir)
        ImageToPlane.create([img], mat_type="standard", suffix="_proxy")
        self.assertTrue(cmds.objExists("stone_proxy"))

    def test_material_is_assigned(self):
        """The created material is assigned to the plane's shading group."""
        img = _create_test_image(64, 64, "assigned", self._tmp_dir)
        results = ImageToPlane.create([img], mat_type="standard")
        plane = results["assigned"]
        shapes = (cmds.listRelatives(str(plane), shapes=True, ni=True) or [])
        self.assertTrue(len(shapes) > 0)
        sgs = cmds.listConnections(shapes[0], type="shadingEngine") or []
        self.assertTrue(len(sgs) > 0)
        # SG should be connected to our shader
        shaders = cmds.listConnections(f"{sgs[0]}.surfaceShader") or []
        self.assertTrue(
            any("assigned_MAT" in n for n in shaders),
            f"Expected shader 'assigned_MAT' in {shaders}",
        )

    def test_file_node_connected(self):
        """A file node with the image path feeds the shader."""
        img = _create_test_image(64, 64, "filecheck", self._tmp_dir)
        ImageToPlane.create([img], mat_type="standard")
        shader = "filecheck_MAT"
        # Walk upstream from baseColor or color
        color_attr = "baseColor" if cmds.attributeQuery("baseColor", node=shader, exists=True) else "color"
        conns = cmds.listConnections(f"{shader}.{color_attr}", type="file") or []
        self.assertTrue(len(conns) > 0, "No file node connected to shader color")
        fn = conns[0]
        stored = cmds.getAttr(f"{fn}.fileTextureName")
        self.assertEqual(
            os.path.normpath(stored),
            os.path.normpath(img),
        )

    # -- Batch creation ----------------------------------------------------

    def test_batch_creates_multiple_planes(self):
        """Multiple images produce one plane each."""
        imgs = [
            _create_test_image(64, 64, f"batch_{i}", self._tmp_dir) for i in range(4)
        ]
        results = ImageToPlane.create(imgs, mat_type="standard")
        self.assertEqual(len(results), 4)
        for i in range(4):
            self.assertIn(f"batch_{i}", results)

    def test_batch_skips_missing_images(self):
        """Non-existent paths are skipped without raising."""
        good = _create_test_image(64, 64, "good", self._tmp_dir)
        results = ImageToPlane.create(
            [good, "/nonexistent/fake.png"],
            mat_type="standard",
        )
        self.assertEqual(len(results), 1)
        self.assertIn("good", results)

    # -- Stingray shader ---------------------------------------------------

    def test_create_stingray_plane(self):
        """Create a plane with Stingray PBS material."""
        _ensure_stingray_loadable()
        img = _create_test_image(64, 64, "sr_test", self._tmp_dir)
        results = ImageToPlane.create([img], mat_type="stingray")
        self.assertIn("sr_test", results)
        self.assertTrue(cmds.objExists("sr_test_MAT"))

    def test_stingray_use_color_map_enabled(self):
        """Verify use_color_map is set to 1 so the texture is visible.

        Regression: StingrayPBS planes appeared white because use_color_map
        was never enabled after connecting the file node to TEX_color_map.
        """
        _ensure_stingray_loadable()
        img = _create_test_image(64, 64, "sr_clr", self._tmp_dir)
        ImageToPlane.create([img], mat_type="stingray")
        shader = "sr_clr_MAT"
        self.assertTrue(cmds.objExists(shader))
        self.assertTrue(
            cmds.attributeQuery("use_color_map", node=shader, exists=True),
            "StingrayPBS shader missing use_color_map attr",
        )
        self.assertEqual(
            cmds.getAttr(f"{shader}.use_color_map"),
            1.0,
            "use_color_map not enabled — texture will appear white",
        )

    # -- Remove ------------------------------------------------------------

    def test_remove_deletes_plane_and_material(self):
        """remove() cleans up the plane, shader, file node, and SG."""
        img = _create_test_image(64, 64, "removable", self._tmp_dir)
        results = ImageToPlane.create([img], mat_type="standard")
        plane = results["removable"]
        # Grab names before removal
        plane_name = plane
        mat_name = "removable_MAT"
        sg_name = f"{mat_name}_SG"

        removed = ImageToPlane.remove([plane])
        self.assertEqual(removed, 1)
        self.assertFalse(cmds.objExists(plane_name))
        self.assertFalse(cmds.objExists(mat_name))

    def test_remove_uses_selection_when_no_args(self):
        """remove() with no args uses current selection."""
        img = _create_test_image(64, 64, "sel_rm", self._tmp_dir)
        results = ImageToPlane.create([img], mat_type="standard")
        plane = results["sel_rm"]
        cmds.select(plane, replace=True)

        removed = ImageToPlane.remove()
        self.assertEqual(removed, 1)
        self.assertFalse(cmds.objExists("sel_rm"))

    def test_remove_returns_zero_on_empty(self):
        """remove() with nothing selected returns 0."""
        cmds.select(clear=True)
        self.assertEqual(ImageToPlane.remove(), 0)

    # -- Plane height parameter --------------------------------------------

    def test_custom_plane_height(self):
        """plane_height parameter scales the plane correctly."""
        img = _create_test_image(100, 100, "sized", self._tmp_dir)
        results = ImageToPlane.create(
            [img],
            mat_type="standard",
            plane_height=5.0,
        )
        plane = results["sized"]
        bb = cmds.exactWorldBoundingBox(plane)
        # Square image → both extents should be ~5.0
        plane_w = bb[3] - bb[0]
        plane_h = bb[4] - bb[1]
        extent = max(plane_w, plane_h)
        self.assertAlmostEqual(extent, 5.0, places=1)


# ===========================================================================
# Slots (UI layer) — Pure Mock
# ===========================================================================


class TestImageToPlaneSlots(unittest.TestCase):
    """Test the slots class with a mocked switchboard (no Maya required)."""

    def setUp(self):
        from unittest.mock import MagicMock

        self.sb = MagicMock()
        self.ui = MagicMock()
        self.sb.loaded_ui.image_to_plane = self.ui

        from mayatk.mat_utils.image_to_plane.image_to_plane_slots import (
            ImageToPlaneSlots,
        )

        self.slots = ImageToPlaneSlots(self.sb)

    def test_buttons_connected(self):
        """All four buttons are wired to their slots."""
        self.ui.b000.clicked.connect.assert_called_once()
        self.ui.b001.clicked.connect.assert_called_once()
        self.ui.b002.clicked.connect.assert_called_once()
        self.ui.b004.clicked.connect.assert_called_once()

    def test_clear_list(self):
        """_clear_list clears the QListWidget."""
        self.slots._clear_list()
        self.ui.lst_files.clear.assert_called_once()
        self.ui.footer.setText.assert_called()


if __name__ == "__main__":
    unittest.main()
