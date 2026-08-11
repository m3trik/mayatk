"""Tests for TextureBaker's Arnold backend (Phase 1 latent-bug fixes).

Two regressions the Phase 0b spike surfaced in Maya 2025:
  1. arnold_available() probed cmds.listCommands() -- which does NOT exist in
     Maya 2025 -- so it raised AttributeError once mtoa was loaded.
  2. arnoldRenderToTexture writes <shapeName>.<ext>, but bake() looked for the
     <transform>.<ext> name, so the file was "missing" and the object was
     dropped from the result dict (the Arnold path never actually worked).
"""
import contextlib
import sys
import os
import shutil
import tempfile
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import maya.cmds as cmds
from base_test import MayaTkTestCase
from mayatk.mat_utils.texture_baker import TextureBaker


def _arnold_loadable():
    try:
        if not cmds.pluginInfo("mtoa", q=True, loaded=True):
            cmds.loadPlugin("mtoa")
        return hasattr(cmds, "arnoldRenderToTexture")
    except Exception:
        return False


class TestArnoldAvailable(MayaTkTestCase):
    def test_returns_bool_without_raising(self):
        # Regression #1: must not raise even when mtoa is loaded.
        try:
            cmds.loadPlugin("mtoa")
        except Exception:
            pass
        self.assertIsInstance(TextureBaker.arnold_available(), bool)


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestArnoldBakeOutputNaming(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bake_lighting_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_bake_returns_existing_prefixed_path(self):
        # Regression #2: RTT names the file after the shape, not the transform.
        cube = cmds.polyCube(name="bakeCube")[0]
        long_name = cmds.ls(cube, long=True)[0]
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube], output_dir=self.tmp, prefix="bake_", backend="arnold"
        )
        self.assertIn(long_name, result)
        path = result[long_name]
        self.assertTrue(os.path.exists(path), f"missing: {path}")
        self.assertEqual(os.path.basename(path), "bake_bakeCube.exr")

    def test_bake_applies_suffix(self):
        # The <base><suffix> convention (e.g. "<object>_Lightmap").
        cube = cmds.polyCube(name="suffixCube")[0]
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube], output_dir=self.tmp, prefix="", suffix="_Lightmap",
            backend="arnold",
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "suffixCube_Lightmap.exr")


class TestBakeUvSetTargeting(MayaTkTestCase):
    """uv_set= must decide the layout the bake actually renders.

    Arnold ignores the scene's current UV set (probe-measured), so its paths
    pass RTT's own uv_set flag; the current-set switch remains as
    convertSolidTx's targeting plus the missing-set warning, and is restored
    after the bake. The content-position tests are the real contract: where
    the EXR's nonzero texels land names the layout that rendered.
    """

    @staticmethod
    def _current(shape):
        return (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]

    def _cube_with_lightmap_set(self):
        cube = cmds.polyCube(name="uvTargetCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="lightmap")
        # map1 stays current after a create.
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="map1")
        return cube, shape

    def test_set_makes_current_and_returns_prior(self):
        cube, shape = self._cube_with_lightmap_set()
        baker = TextureBaker()
        prev = baker._set_current_uv_set(cube, "lightmap")
        self.assertEqual(self._current(shape), "lightmap")
        self.assertEqual(list(prev.values()), ["map1"])
        baker._restore_uv_sets(prev)
        self.assertEqual(self._current(shape), "map1")

    def test_missing_set_returns_empty_and_leaves_current(self):
        cube, shape = self._cube_with_lightmap_set()
        prev = TextureBaker()._set_current_uv_set(cube, "nope")
        self.assertEqual(prev, {})
        self.assertEqual(self._current(shape), "map1")

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_bake_into_uv_set_restores_current(self):
        cube, shape = self._cube_with_lightmap_set()
        tmp = tempfile.mkdtemp(prefix="bake_uvset_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=64, samples=2, file_format="exr").bake(
            [cube], output_dir=tmp, backend="arnold", uv_set="lightmap"
        )
        self.assertTrue(result)  # produced a file
        self.assertEqual(self._current(shape), "map1")  # scene left as found

    def _quadrant_plane(self, name):
        """Plane whose map1 fills 0-1 and whose lightmap set sits in the
        upper-right quadrant -- where the EXR's content lands names the layout
        that actually rendered."""
        plane = cmds.polyPlane(name=name, sx=1, sy=1)[0]
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="lightmap")
        cmds.polyEditUV(
            f"{shape}.map[*]", pivotU=0.0, pivotV=0.0, scaleU=0.5, scaleV=0.5
        )
        cmds.polyEditUV(f"{shape}.map[*]", uValue=0.5, vValue=0.5, relative=True)
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="map1")
        return plane

    def _content_is_quadrant(self, path):
        """(nonzero_coverage, u_min_frac, v_row_max_frac) of *path*'s content."""
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("cv2 unavailable")
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
        self.assertIsNotNone(img, f"unreadable bake output: {path}")
        nz = img[..., :3].max(axis=2) > 1e-6
        self.assertTrue(nz.any(), f"empty bake output: {path}")
        cols = np.where(nz.any(axis=0))[0]
        rows = np.where(nz.any(axis=1))[0]
        return (
            float(nz.mean()),
            cols.min() / nz.shape[1],
            rows.max() / nz.shape[0],
        )

    def _white_flat(self):
        flat = cmds.shadingNode("aiFlat", asShader=True)
        cmds.setAttr(f"{flat}.color", 1, 1, 1, type="double3")
        return flat

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_bake_renders_the_requested_set_not_the_current_one(self):
        # arnoldRenderToTexture IGNORES the scene's current UV set
        # (probe-measured: with "lightmap" current and no flag, content still
        # covered map1's full 0-1) -- the target must ride the command's own
        # uv_set flag. This is the OFFICE_ENV black-room bug: every wall's
        # bake landed on map1's layout while the committed atlas rect sampled
        # the lightmap layout -- bright bake, black walls.
        plane = self._quadrant_plane("uvFlagPlane")
        tmp = tempfile.mkdtemp(prefix="bake_uvflag_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # extend_edges off: this test reads which layout rendered from WHERE the
        # content lands, and edge extension deliberately fills the background,
        # which erases exactly that signal. The flag's own default is covered by
        # test_rtt_kwargs_extend_edges_by_default.
        result = TextureBaker(
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            [plane],
            output_dir=tmp,
            backend="arnold",
            uv_set="lightmap",
            shader=self._white_flat(),
        )
        self.assertTrue(result)
        cover, u_min, v_row_max = self._content_is_quadrant(
            next(iter(result.values()))
        )
        self.assertLess(cover, 0.5, "full-map content: the bake rendered map1")
        self.assertGreater(u_min, 0.3)  # right half...
        self.assertLess(v_row_max, 0.7)  # ...top rows (EXR row 0 == v 1)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_batch_bake_carries_the_uv_set(self):
        # The batch path is one RTT call -- the one uv_set flag must reach it
        # too, and a batch whose objects agree on the target must stay a batch.
        # Planes are separated: two coincident surfaces bake to zeros (RTT
        # surface sampling cannot disambiguate coplanar twins).
        planes = [self._quadrant_plane(f"uvBatchPlane{i}") for i in range(2)]
        for i, p in enumerate(planes):
            cmds.move(i * 5.0, 0, 0, p)
        tmp = tempfile.mkdtemp(prefix="bake_uvbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(  # extend_edges off -- see the note above
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            planes,
            output_dir=tmp,
            backend="arnold",
            batch=True,
            uv_set="lightmap",
            shader=self._white_flat(),
        )
        self.assertEqual(len(result), 2)
        for path in result.values():
            cover, u_min, v_row_max = self._content_is_quadrant(path)
            self.assertLess(cover, 0.5)
            self.assertGreater(u_min, 0.3)
            self.assertLess(v_row_max, 0.7)

    @unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
    def test_batch_with_mixed_targets_falls_back_and_stays_correct(self):
        # One RTT call takes ONE uv_set -- mixed per-object targets cannot
        # batch. The observable that matters: each object still bakes its OWN
        # target layout (via the per-object fallback), not one another's.
        quad = self._quadrant_plane("uvMixQuad")
        full = cmds.polyPlane(name="uvMixFull", sx=1, sy=1)[0]
        cmds.move(5.0, 0, 0, full)  # coplanar twins bake to zeros
        tmp = tempfile.mkdtemp(prefix="bake_uvmix_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        long_quad = cmds.ls(quad, long=True)[0]
        long_full = cmds.ls(full, long=True)[0]
        result = TextureBaker(  # extend_edges off -- see the note above
            resolution=64, samples=1, file_format="exr", extend_edges=False
        ).bake(
            [quad, full],
            output_dir=tmp,
            backend="arnold",
            batch=True,
            uv_set={long_quad: "lightmap", long_full: "map1"},
            shader=self._white_flat(),
        )
        self.assertEqual(len(result), 2)
        cover_q, u_min_q, _ = self._content_is_quadrant(result[long_quad])
        self.assertLess(cover_q, 0.5)
        self.assertGreater(u_min_q, 0.3)
        cover_f, _, _ = self._content_is_quadrant(result[long_full])
        self.assertGreater(cover_f, 0.9)  # map1 fills the whole map

    def test_rtt_kwargs_extend_edges_by_default(self):
        """Bake past the island border unless a caller opts out.

        Without it Arnold writes partial-coverage edge texels whose RGB is
        premultiplied by that coverage -- a dark ring around every island, and a
        dark seam wherever two tiles meet, since both put their dark border on
        the same line. Measured on a lit cube at 128px: island edges 83.7%
        darker than the interior and 7.40% of the map partially covered; with
        the flag the partial texels go to 0.00% and the interior is unchanged.
        """
        self.assertIs(TextureBaker()._rtt_kwargs("/tmp", None).get("extend_edges"), True)
        off = TextureBaker(extend_edges=False)
        self.assertIs(off._rtt_kwargs("/tmp", None).get("extend_edges"), False)


class TestBakeProgressCallback(MayaTkTestCase):
    """on_progress fires per object and can cancel -- no renderer needed.

    on_progress is invoked at the top of each object's iteration (before the
    render), so cancelling on the first call stops the bake before any render
    happens -- the wiring + cancel path are testable without Arnold.
    """

    def test_on_progress_called_and_cancel_stops_bake(self):
        cubes = [cmds.polyCube(name=f"progCube{i}")[0] for i in range(3)]
        tmp = tempfile.mkdtemp(prefix="bake_prog_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        seen = []

        def cb(done, total, name):
            seen.append((done, total))
            return False  # cancel immediately, before the first render

        result = TextureBaker(resolution=8).bake(
            cubes, output_dir=tmp, backend="convertSolidTx", on_progress=cb
        )
        self.assertEqual(seen, [(0, 3)])  # called once for object 0, then stopped
        self.assertEqual(result, {})  # cancelled before any file was produced


class TestBakeNaming(unittest.TestCase):
    """Output-name resolution (stem + collision) — pure logic, no render."""

    def test_resolve_stem_prefers_resolver(self):
        b = TextureBaker()
        self.assertEqual(
            b._resolve_stem(lambda o: "Mat_Base", "|grp|obj", "obj"), "Mat_Base"
        )
        self.assertEqual(
            b._resolve_stem({"|grp|obj": "Mat_Base"}, "|grp|obj", "obj"), "Mat_Base"
        )

    def test_resolve_stem_falls_back_to_leaf(self):
        b = TextureBaker()
        self.assertEqual(b._resolve_stem(None, "|obj", "obj"), "obj")  # no resolver
        self.assertEqual(b._resolve_stem(lambda o: None, "|obj", "obj"), "obj")  # empty
        self.assertEqual(b._resolve_stem({}, "|obj", "obj"), "obj")  # missing key

        def boom(_o):
            raise RuntimeError("nope")

        self.assertEqual(b._resolve_stem(boom, "|obj", "obj"), "obj")  # raised

    def test_unique_path_disambiguates_collisions(self):
        b = TextureBaker(file_format="exr")
        used = set()
        p1 = b._unique_path("/out", "Shared_Lightmap", used)
        p2 = b._unique_path("/out", "Shared_Lightmap", used)
        p3 = b._unique_path("/out", "Shared_Lightmap", used)
        self.assertEqual(os.path.basename(p1), "Shared_Lightmap.exr")
        self.assertEqual(os.path.basename(p2), "Shared_Lightmap_1.exr")
        self.assertEqual(os.path.basename(p3), "Shared_Lightmap_2.exr")

    def test_unique_path_honors_effective_format(self):
        # bake() overrides the requested format with the backend's effective
        # one (Arnold RTT has no format flag; it always writes EXR).
        b = TextureBaker(file_format="png")
        path = b._unique_path("/out", "Card", set(), "exr")
        self.assertEqual(os.path.basename(path), "Card.exr")


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestBakeStemEndToEnd(MayaTkTestCase):
    """End-to-end: the stem resolver names the actual file + progress reaches 100%."""

    def test_stem_names_output_and_progress_completes(self):
        cube = cmds.polyCube(name="longNodeName")[0]
        tmp = tempfile.mkdtemp(prefix="bake_stem_e2e_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        seen = []
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [cube], output_dir=tmp, prefix="", suffix="_Lightmap", backend="arnold",
            stem=lambda o: "Plants_Metal_Base_01",
            on_progress=lambda d, t, n: seen.append((d, t)) or True,
        )
        path = next(iter(result.values()))
        self.assertEqual(os.path.basename(path), "Plants_Metal_Base_01_Lightmap.exr")
        self.assertEqual(seen[0], (0, 1))   # per-object start tick
        self.assertEqual(seen[-1], (1, 1))  # final completion tick → 100%


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestPinnedRenderSettings(MayaTkTestCase):
    """render_settings are pinned on defaultArnoldRenderOptions, then restored."""

    def test_settings_set_during_bake_and_restored_after(self):
        from mtoa.core import createOptions

        createOptions()
        node = "defaultArnoldRenderOptions"
        cmds.setAttr(f"{node}.GIDiffuseDepth", 1)  # a known pre-bake state

        b = TextureBaker(render_settings={"GIDiffuseDepth": 4})
        with b._pinned_render_settings("arnold"):
            self.assertEqual(cmds.getAttr(f"{node}.GIDiffuseDepth"), 4)
        self.assertEqual(cmds.getAttr(f"{node}.GIDiffuseDepth"), 1)  # restored

    def test_unknown_attr_is_skipped_not_fatal(self):
        b = TextureBaker(render_settings={"NoSuchArnoldAttr": 7, "GIDiffuseDepth": 2})
        with b._pinned_render_settings("arnold"):
            self.assertEqual(
                cmds.getAttr("defaultArnoldRenderOptions.GIDiffuseDepth"), 2
            )

    def test_non_arnold_backend_is_a_noop(self):
        b = TextureBaker(render_settings={"GIDiffuseDepth": 4})
        with b._pinned_render_settings("convertSolidTx"):
            pass  # must not require or touch the Arnold options node

    def test_batch_bakes_all_objects_in_one_call(self):
        # Batch mode: one RTT call for the whole selection (7.45x measured);
        # per-shape files must map back to the right objects with the same
        # naming convention as the per-object loop.
        a = cmds.polyCube(name="batchA")[0]
        b = cmds.polyCube(name="batchB")[0]
        tmp = tempfile.mkdtemp(prefix="bake_batch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ticks = []
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [a, b], output_dir=tmp, prefix="", suffix="_LM", backend="arnold",
            batch=True,
            on_progress=lambda d, t, n: ticks.append((d, t)) or True,
        )
        self.assertEqual(len(result), 2)
        names = sorted(os.path.basename(p) for p in result.values())
        self.assertEqual(names, ["batchA_LM.exr", "batchB_LM.exr"])
        for p in result.values():
            self.assertTrue(os.path.exists(p))
        # One cancellable start tick + the final completion tick.
        self.assertEqual(ticks[0], (0, 2))
        self.assertEqual(ticks[-1], (2, 2))

    def test_batch_duplicate_shape_leaves_fall_back_to_loop(self):
        # RTT names batch output by shape leaf -- duplicates would overwrite
        # each other, so the batch must detect them and fall back per-object
        # (which dir-diffs between calls and stays collision-free).
        a = cmds.polyCube(name="dupBatch")[0]
        cmds.group(a, name="dupBatchGrp")
        la = cmds.ls("dupBatchGrp|dupBatch", long=True)[0]
        lb = cmds.ls(cmds.polyCube(name="dupBatch")[0], long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_dupbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [la, lb], output_dir=tmp, prefix="", suffix="", backend="arnold",
            batch=True,
        )
        self.assertEqual(len(result), 2)  # both baked despite the collision
        self.assertNotEqual(result[la], result[lb])  # distinct files
        for p in result.values():
            self.assertTrue(os.path.exists(p))

    def test_instances_of_one_shape_batch_together(self):
        # THE production shape: 24 wall tiles on one mesh. They share a shape
        # leaf, but RTT writes each as "<transform>_<shapeLeaf>.exr", so they
        # do NOT collide -- the old leaf-only test rejected them anyway and
        # forced one full scene translation per object (measured: 275.4s for
        # 4 objects against 12.9s batched, because each call re-exports the
        # whole scene). They must batch, and map back to distinct files.
        a = cmds.polyCube(name="instBatchOne")[0]
        b = cmds.instance(a, name="instBatchTwo")[0]
        c = cmds.instance(a, name="instBatchThree")[0]
        cmds.move(3, 0, 0, b)
        cmds.move(6, 0, 0, c)
        longs = [cmds.ls(o, long=True)[0] for o in (a, b, c)]
        tmp = tempfile.mkdtemp(prefix="bake_instgroup_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            longs, output_dir=tmp, prefix="", suffix="_LM", backend="arnold",
            batch=True,
        )
        self.assertEqual(sorted(result), sorted(longs))
        paths = [result[o] for o in longs]
        self.assertEqual(len(set(paths)), 3, "instances collided onto one map")
        for p in paths:
            self.assertTrue(os.path.exists(p))

    def test_rtt_stem_qualifies_instances_only(self):
        # The collision test is only as good as this prediction.
        solo = cmds.polyCube(name="stemSolo")[0]
        solo_shape = cmds.listRelatives(solo, shapes=True, fullPath=True)[0]
        self.assertEqual(
            TextureBaker._rtt_stem(cmds.ls(solo, long=True)[0], solo_shape),
            "stemSoloShape",
        )
        inst = cmds.polyCube(name="stemInst")[0]
        cmds.instance(inst, name="stemInstTwin")
        inst_shape = cmds.listRelatives(inst, shapes=True, fullPath=True)[0]
        self.assertEqual(
            TextureBaker._rtt_stem(cmds.ls(inst, long=True)[0], inst_shape),
            "stemInst_stemInstShape",
        )

    def test_batch_single_instanced_object_maps_qualified_stem(self):
        # An instanced shape gets a path-qualified RTT filename
        # ("<transform>_<shapeLeaf>.exr") even when it is the ONLY object in
        # the call -- sibling instances elsewhere in the scene are enough to
        # force qualified Arnold node names. The batch result mapping must
        # match that spelling too (regression: it looked for the bare shape
        # leaf, found nothing, and warned "produced no output" while the
        # rendered file sat in the output dir).
        a = cmds.polyCube(name="instBatchA")[0]
        cmds.instance(a, name="instBatchB")  # sibling stays OUT of the bake
        la = cmds.ls(a, long=True)[0]
        tmp = tempfile.mkdtemp(prefix="bake_instbatch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [la], output_dir=tmp, prefix="", suffix="_LM", backend="arnold",
            batch=True,
        )
        self.assertEqual(list(result), [la])
        self.assertTrue(os.path.exists(result[la]))
        self.assertEqual(os.path.basename(result[la]), "instBatchA_LM.exr")

    def test_arnold_format_request_is_pinned_to_exr(self):
        # A png request with the Arnold backend must yield real .exr output
        # (RTT has no format flag), not EXR bytes behind a .png name.
        cube = cmds.polyCube(name="fmtCube")[0]
        tmp = tempfile.mkdtemp(prefix="bake_fmt_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = TextureBaker(resolution=16, samples=1, file_format="png").bake(
            [cube], output_dir=tmp, backend="arnold"
        )
        self.assertTrue(result)
        path = next(iter(result.values()))
        self.assertTrue(path.endswith(".exr"), path)
        self.assertTrue(os.path.exists(path))


class TestResolveMeshes(MayaTkTestCase):
    """Only mesh transforms reach a renderer.

    A production selection routinely holds more than geometry -- the room's
    lights (the Blender-bridge bake needs those selected) and an export
    locator. Arnold RTT does not degrade on those: it raises per object
    ("quad_light nodes are not supported types", "not exported to Arnold
    world") and then reports success while writing no file.
    """

    def test_drops_lights_locators_and_empty_groups(self):
        cube = cmds.ls(cmds.polyCube(name="bakeMesh")[0], long=True)[0]
        light = cmds.ls(
            cmds.listRelatives(
                cmds.shadingNode("areaLight", asLight=True), parent=True, fullPath=True
            )
            or [cmds.shadingNode("areaLight", asLight=True)],
            long=True,
        )[0]
        locator = cmds.ls(cmds.spaceLocator(name="data_export")[0], long=True)[0]
        group = cmds.ls(cmds.group(empty=True, name="emptyGrp"), long=True)[0]

        self.assertEqual(
            TextureBaker.resolve_meshes([cube, light, locator, group]), [cube]
        )

    def test_shapes_and_components_resolve_to_their_transform(self):
        cube = cmds.ls(cmds.polyCube(name="compMesh")[0], long=True)[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        # Same transform reached three ways -> one entry, not three.
        self.assertEqual(
            TextureBaker.resolve_meshes([cube, shape, f"{cube}.f[0]"]), [cube]
        )

    def test_empty_input_does_not_fall_back_to_the_selection(self):
        # [] means "nothing was in scope", NOT "use the selection" -- otherwise
        # an empty scope silently escalates into baking whatever is selected.
        cmds.select(cmds.polyCube(name="selectedMesh")[0], replace=True)
        self.assertEqual(TextureBaker.resolve_meshes([]), [])

    def test_bake_refuses_a_selection_with_no_mesh(self):
        light = cmds.shadingNode("areaLight", asLight=True)
        parent = cmds.listRelatives(light, parent=True, fullPath=True)
        result = TextureBaker(resolution=16, samples=1).bake(
            [parent[0] if parent else light], output_dir=self.tmp_dir()
        )
        self.assertEqual(result, {})

    def tmp_dir(self):
        path = tempfile.mkdtemp(prefix="bake_nonmesh_")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestBakeDetectionSurvivesStrayOutputs(MayaTkTestCase):
    """A stray raw-named output from a failed placement must not blind the bake.

    Measured in production: a placement failure leaves the raw RTT-named file
    beside the atlas; the NEXT bake overwrites that same path, the name-set
    dir-diff sees no new file, and the object drops again -- the same meshes
    went black in consecutive pushes (deterministic, not the sync race).
    Detection has to be overwrite-aware: an mtime change is a new output.
    """

    def test_overwritten_stray_is_still_detected(self):
        plane = cmds.polyPlane(name="strayPlane", sx=1, sy=1)[0]
        flat = cmds.shadingNode("aiFlat", asShader=True)
        cmds.setAttr(f"{flat}.color", 1, 1, 1, type="double3")
        tmp = tempfile.mkdtemp(prefix="bake_stray_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # The stray a prior failed placement left behind, under the exact
        # name RTT will write again.
        with open(os.path.join(tmp, "strayPlaneShape.exr"), "wb") as fh:
            fh.write(b"stale")
        result = TextureBaker(resolution=16, samples=1, file_format="exr").bake(
            [plane], output_dir=tmp, backend="arnold", shader=flat
        )
        self.assertEqual(len(result), 1, "the overwritten output went undetected")
        out = next(iter(result.values()))
        self.assertGreater(
            os.path.getsize(out), 1000, "fresh render, not the stale stray"
        )


@unittest.skipUnless(_arnold_loadable(), "mtoa/arnoldRenderToTexture unavailable")
class TestForcedShaderReachesInstancedTargets(MayaTkTestCase):
    """``shader=`` must decide what the target renders -- instances included.

    Arnold's ``-shader`` flag is silently lost on the one instance that owns a
    shared mesh's shading-group membership: it renders its assigned material,
    so a lighting-only bake comes back as albedo x lighting for that instance
    alone. Measured on a 24-instance wall (OFFICE_ENV): the owning tile baked
    16% hot with a 10-17% step at every shared edge, against 25 boundaries
    continuous to 3% -- one bright rectangle with hard edges in the preview.
    """

    @staticmethod
    def _groups(obj):
        shape = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        )[0]
        return sorted(cmds.listSets(object=shape, type=1) or [])

    def test_shared_mesh_owner_bakes_with_the_override_not_its_material(self):
        import unittest.mock as mock

        from mayatk.mat_utils._mat_utils import MatUtils

        base = cmds.polyPlane(name="tile", sx=1, sy=1)[0]
        twin = cmds.instance(base, name="tileTwin")[0]
        cmds.move(3, 0, 0, twin)
        wall = cmds.shadingNode("lambert", asShader=True, name="wallMat")
        MatUtils.assign_mat([base, twin], wall)
        wall_sg = cmds.listConnections(wall, type="shadingEngine")[0]
        card = cmds.shadingNode("lambert", asShader=True, name="whiteCard")
        # Give the card its group up front so a target that never picked it up
        # reads as a plain assertion rather than a missing-node error.
        card_sg = MatUtils.create_shading_group(card)

        tmp = tempfile.mkdtemp(prefix="bake_forced_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        during = {}

        def record(_self, long_name, output_dir, shader, uv_set=None):
            during[long_name.rsplit("|", 1)[-1]] = self._groups(long_name)
            path = os.path.join(output_dir, f"{long_name.rsplit('|', 1)[-1]}.exr")
            with open(path, "wb") as fh:
                fh.write(b"x" * 2048)
            return path

        with mock.patch.object(TextureBaker, "_bake_with_arnold", record):
            TextureBaker(resolution=16, samples=1, file_format="exr").bake(
                [base, twin], output_dir=tmp, backend="arnold", shader=card
            )

        for leaf in ("tile", "tileTwin"):
            self.assertIn(
                card_sg, during[leaf], f"{leaf} did not render with the bake shader"
            )
            self.assertNotIn(
                wall_sg, during[leaf], f"{leaf} still carried its own material"
            )
        # ... and the scene is handed back exactly as it was found.
        for obj in (base, twin):
            self.assertEqual(
                self._groups(obj), [wall_sg], "the bake shader outlived the bake"
            )

    def test_an_instanced_target_with_an_override_declines_to_batch(self):
        """Batch cannot carry this fix, so an instanced target must not batch.

        Arnold drops ``-shader`` on the instance carrying a shared mesh's
        shading assignment (measured on the production wall: 5.948 batched vs
        5.142 per-object for the same tile), and carding a whole batch would
        kill the neighbour colour bleed the override exists for (see
        TestLightmapBakerArnold's GI bleed test). Splitting just the owners
        out is not available either: ``instObjGroups`` connections are
        reported relative to the DAG path they are queried through, so every
        instance claims ownership (probed on the production room). Refusing
        the batch is expensive -- each per-object call re-exports the whole
        scene, 275.4s vs 12.9s for 4 objects -- and still correct, which is
        the trade until the owner can be identified.
        """
        import unittest.mock as mock

        from mayatk.mat_utils._mat_utils import MatUtils

        base = cmds.polyPlane(name="batchTile", sx=1, sy=1)[0]
        sibling = cmds.instance(base, name="batchTileTwin")[0]
        cmds.move(3, 0, 0, sibling)
        other = cmds.polyPlane(name="batchOther", sx=1, sy=1)[0]
        cmds.move(0, 0, 3, other)
        # A second non-owner, so the batchable remainder is worth a batch call
        # (one leftover object is the same single RTT either way).
        other2 = cmds.polyPlane(name="batchOtherTwo", sx=1, sy=1)[0]
        cmds.move(0, 0, 6, other2)
        wall = cmds.shadingNode("lambert", asShader=True, name="batchWallMat")
        MatUtils.assign_mat([base, sibling, other, other2], wall)
        wall_sg = cmds.listConnections(wall, type="shadingEngine")[0]
        card = cmds.shadingNode("lambert", asShader=True, name="batchCard")
        card_sg = MatUtils.create_shading_group(card)

        tmp = tempfile.mkdtemp(prefix="bake_batch_forced_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        batched = []
        during = {}

        def no_batch(_self, objects, *args, **kwargs):
            batched.append(list(objects))
            return {}

        def per_object(_self, long_name, output_dir, shader, uv_set=None):
            leaf = long_name.rsplit("|", 1)[-1]
            during[leaf] = self._groups(long_name)
            during["sibling"] = self._groups(sibling)
            path = os.path.join(output_dir, f"{leaf}.exr")
            with open(path, "wb") as fh:
                fh.write(b"x" * 2048)
            return path

        with mock.patch.object(
            TextureBaker, "_bake_with_arnold_batch", no_batch
        ), mock.patch.object(TextureBaker, "_bake_with_arnold", per_object):
            TextureBaker(resolution=16, samples=1, file_format="exr").bake(
                [base, other, other2], output_dir=tmp, backend="arnold",
                shader=card, batch=True,
            )

        self.assertFalse(batched, "an instanced target was batched with an override")
        for leaf in ("batchTile", "batchOther", "batchOtherTwo"):
            self.assertIn(card_sg, during[leaf], f"{leaf} missed the bake shader")
        self.assertEqual(
            during["sibling"],
            [wall_sg],
            "an unselected instance of the same mesh was dragged into the bake",
        )
        for obj in (base, sibling, other, other2):
            self.assertEqual(
                self._groups(obj), [wall_sg], "the bake shader outlived the bake"
            )

    def test_an_unassigned_target_is_left_unassigned(self):
        """Restoring must not invent a material the object never had."""
        import unittest.mock as mock

        from mayatk.mat_utils._mat_utils import MatUtils

        plane = cmds.polyPlane(name="bareTile", sx=1, sy=1)[0]
        shape = cmds.listRelatives(
            plane, shapes=True, noIntermediate=True, fullPath=True
        )[0]
        for sg in cmds.listSets(object=shape, type=1) or []:
            cmds.sets(shape, edit=True, remove=sg)
        self.assertEqual(self._groups(plane), [], "test setup: expected no material")

        card = cmds.shadingNode("lambert", asShader=True, name="bareCard")
        MatUtils.create_shading_group(card)
        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        with baker._forced_shader(plane, card):
            pass
        self.assertEqual(
            self._groups(plane), [], "the bake shader was left behind on the object"
        )


class TestPlaceOutputSurvivesLockedDestination(MayaTkTestCase):
    """A locked destination must not cost the artist a finished bake.

    Measured in production: the project's ``sourceimages`` lives on a synced
    Dropbox share, so ``os.replace`` onto a previous map raised
    ``[WinError 32] The process cannot access the file because it is being
    used by another process`` — and the caller logged "Bake failed" and
    dropped the object, discarding a render that had already been paid for.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bake_lock_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _src(self, name="raw.exr"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(b"x")
        return path

    def test_locked_destination_falls_back_to_an_adjacent_name(self):
        import unittest.mock as mock

        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "OFFICE_ENV_Lightmap_9.exr")
        open(dst, "wb").close()  # the "previous" map, held open elsewhere

        real_replace = os.replace
        calls = []

        def flaky_replace(a, b):
            calls.append(b)
            if os.path.abspath(b) == os.path.abspath(dst):
                raise PermissionError(32, "used by another process")
            return real_replace(a, b)

        with mock.patch("os.replace", flaky_replace):
            out = baker._place_output(src, dst, set())

        self.assertNotEqual(os.path.abspath(out), os.path.abspath(dst))
        self.assertTrue(os.path.exists(out), out)
        self.assertFalse(os.path.exists(src), "the bake should have been moved")
        self.assertEqual(len(calls), 2, "should retry exactly once under a new name")

    def test_locked_source_still_places_via_copy(self):
        # The sync client holds the SOURCE too: it indexes each freshly
        # written RTT output, and renaming a file held without delete-share
        # raises the same WinError 32 whatever destination is tried -- so the
        # adjacent-name retry cannot help. Measured in production: 4 of a
        # room's 46 maps stayed under their raw RTT names, dropped out of the
        # atlas, and rendered as BLACK objects in the preview. A read-share
        # lock still permits copying; the finished bake must land at the
        # recorded path either way.
        import unittest.mock as mock

        baker = TextureBaker(resolution=16, samples=1)
        src = self._src("DOOR_A_DOOR_AShape.exr")
        dst = os.path.join(self.tmp, "DOOR_A_Lightmap.exr")

        def source_locked(a, b):
            raise PermissionError(32, "used by another process")

        with mock.patch("os.replace", source_locked):
            out = baker._place_output(src, dst, set())

        self.assertEqual(os.path.abspath(out), os.path.abspath(dst))
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), b"x")

    def test_unlocked_destination_is_replaced_in_place(self):
        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "final.exr")
        out = baker._place_output(src, dst, set())
        self.assertEqual(os.path.abspath(out), os.path.abspath(dst))
        self.assertTrue(os.path.exists(dst))

    def test_a_permanently_locked_directory_raises_instead_of_looping(self):
        # A lock on the DIRECTORY refuses every candidate name equally --
        # renames AND the copy fallback alike (a rename-only refusal is the
        # locked-source case, rescued by the copy). It must terminate and
        # report: an unbounded retry loop here would hang Maya with no error.
        import unittest.mock as mock

        baker = TextureBaker(resolution=16, samples=1)
        src = self._src()
        dst = os.path.join(self.tmp, "locked.exr")
        calls = []

        def always_locked(*_a, **_kw):
            calls.append(_a)
            raise PermissionError(32, "used by another process")

        with mock.patch("os.replace", always_locked), mock.patch(
            "shutil.copy2", always_locked
        ):
            with self.assertRaises(PermissionError):
                baker._place_output(src, dst, set())
        self.assertLessEqual(len(calls), TextureBaker._PLACE_ATTEMPTS + 2)

    def test_a_missing_source_still_raises(self):
        # The fallback is for a locked DESTINATION only -- a genuinely broken
        # move must not spin through adjacent names forever.
        baker = TextureBaker(resolution=16, samples=1)
        with self.assertRaises(OSError):
            baker._place_output(
                os.path.join(self.tmp, "nope.exr"),
                os.path.join(self.tmp, "out.exr"),
                set(),
            )


def _stingray_loadable():
    try:
        if not cmds.pluginInfo("shaderFXPlugin", q=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin")
        return True
    except Exception:
        return False


@unittest.skipUnless(
    _arnold_loadable() and _stingray_loadable(),
    "mtoa + shaderFXPlugin required (aiSurfaceShader attr / StingrayPBS node)",
)
class TestArnoldTranslationGuard(MayaTkTestCase):
    """Game (ShaderFX) materials must not bake as Arnold's error magenta.

    MtoA renders untranslatable shaders bright magenta, and with GI on that
    magenta BOUNCES: measured on a production room, the floor around
    StingrayPBS racks baked magenta-tinted shadows (dark-texel chroma
    3.00/0.21/2.89 -- ~85% pure (1,0,1)) while props away from the racks
    stayed neutral. The guard stands in an albedo-matched standardSurface on
    each affected shading group's aiSurfaceShader slot for the bake, exactly
    the manual workaround the room's walls already carried.
    """

    def _stingray_sg(self, name="rack"):
        shader = cmds.shadingNode("StingrayPBS", asShader=True, name=f"{name}_srp")
        # A fresh StingrayPBS node carries NO graph attributes (base_color,
        # TEX_color_map...) until its ShaderFX graph initializes -- the very
        # graph-dependence the guard probes around.
        try:
            cmds.shaderfx(sfxnode=shader, initShaderAttributes=True)
        except Exception:
            pass
        if not cmds.attributeQuery("base_color", node=shader, exists=True):
            self.skipTest("StingrayPBS default graph attrs unavailable")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        return shader, sg, cube

    @staticmethod
    def _override_source(sg):
        src = cmds.listConnections(
            f"{sg}.aiSurfaceShader", source=True, destination=False
        )
        return src[0] if src else None

    def test_guard_bridges_and_restores(self):
        shader, sg, _cube = self._stingray_sg()

        baker = TextureBaker(resolution=16, samples=1)
        with baker.arnold_translation_guard():
            standin = self._override_source(sg)
            self.assertIsNotNone(standin, "guard must wire aiSurfaceShader")
            # The stand-in IS the existing ArnoldBridge tool, not a bespoke
            # shader -- one implementation of Stingray->Arnold parity.
            self.assertEqual(cmds.nodeType(standin), "aiStandardSurface")
        self.assertIsNone(
            self._override_source(sg), "guard must remove its bridge on exit"
        )
        self.assertFalse(
            cmds.objExists(standin), "guard must delete its bridge on exit"
        )
        # The exported material and its group are untouched.
        self.assertTrue(cmds.objExists(shader))
        self.assertTrue(cmds.objExists(sg))

    def test_guard_respects_an_authored_override(self):
        _shader, sg, _cube = self._stingray_sg("authored")
        authored = cmds.shadingNode(
            "standardSurface", asShader=True, name="authored_ai"
        )
        cmds.connectAttr(
            f"{authored}.outColor", f"{sg}.aiSurfaceShader", force=True
        )
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            self.assertEqual(self._override_source(sg), authored)
        self.assertEqual(self._override_source(sg), authored)

    def test_guard_ignores_arnold_native_shaders(self):
        lam = cmds.shadingNode("lambert", asShader=True, name="native_lam")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="nativeSG"
        )
        cmds.connectAttr(f"{lam}.outColor", f"{sg}.surfaceShader", force=True)
        cube = cmds.polyCube(name="native_geo")[0]
        cmds.sets(cube, edit=True, forceElement=sg)
        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            self.assertIsNone(self._override_source(sg))

    def test_guard_bridge_carries_color_and_emissive_maps(self):
        shader, sg, _cube = self._stingray_sg("mapped")
        cfile = cmds.shadingNode("file", asTexture=True, name="mapped_color_file")
        efile = cmds.shadingNode("file", asTexture=True, name="mapped_emis_file")
        # ArnoldBridge resolves map types from FILE NAMES (MapFactory), not
        # from the graph-dependent Stingray plugs -- give it real names.
        cmds.setAttr(
            f"{cfile}.fileTextureName", "C:/tex/rack_Base_Color.png", type="string"
        )
        cmds.setAttr(
            f"{efile}.fileTextureName", "C:/tex/rack_Emissive.png", type="string"
        )
        cmds.connectAttr(
            f"{cfile}.outColor", f"{shader}.TEX_color_map", force=True
        )
        cmds.setAttr(f"{shader}.use_color_map", 1)
        cmds.connectAttr(
            f"{efile}.outColor", f"{shader}.TEX_emissive_map", force=True
        )
        cmds.setAttr(f"{shader}.use_emissive_map", 1)

        def _file_path_feeding(plug):
            src = cmds.listConnections(plug, source=True, destination=False) or []
            for node in src:
                if cmds.nodeType(node) == "file":
                    return cmds.getAttr(f"{node}.fileTextureName"), node
            return None, None

        with TextureBaker(resolution=16, samples=1).arnold_translation_guard():
            standin = self._override_source(sg)
            self.assertIsNotNone(standin)
            # Base color routes through the bridge's aiMultiply into a
            # DEDICATED file node carrying the same path -- never the game
            # material's own node (Arnold and Stingray need conflicting
            # colorSpace/alphaIsLuminance on the same map).
            mult = (
                cmds.listConnections(
                    f"{standin}.baseColor", source=True, destination=False
                )
                or [None]
            )[0]
            self.assertIsNotNone(mult, "bridge baseColor must be driven")
            color_path, color_node = _file_path_feeding(f"{mult}.input1")
            self.assertEqual(color_path, "C:/tex/rack_Base_Color.png")
            self.assertNotEqual(color_node, cfile, "bridge must not share nodes")
            emis_path, emis_node = _file_path_feeding(f"{standin}.emissionColor")
            self.assertEqual(emis_path, "C:/tex/rack_Emissive.png")
            self.assertNotEqual(emis_node, efile, "bridge must not share nodes")
        # The game material's own file nodes belong to the scene, not the guard.
        self.assertTrue(cmds.objExists(cfile))
        self.assertTrue(cmds.objExists(efile))

    def test_bake_enters_the_guard_by_default(self):
        import unittest.mock as mock

        baker = TextureBaker(resolution=16, samples=1, file_format="exr")
        entered = []

        @contextlib.contextmanager
        def spy():
            entered.append(True)
            yield

        tmp = tempfile.mkdtemp(prefix="guard_bake_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cube = cmds.polyCube(name="guardBakeCube")[0]
        with mock.patch.object(baker, "arnold_translation_guard", spy):
            baker.bake([cube], output_dir=tmp, backend="arnold")
        self.assertEqual(len(entered), 1)

    def test_translation_guard_false_opts_out(self):
        import unittest.mock as mock

        baker = TextureBaker(
            resolution=16, samples=1, file_format="exr", translation_guard=False
        )
        entered = []

        @contextlib.contextmanager
        def spy():
            entered.append(True)
            yield

        tmp = tempfile.mkdtemp(prefix="guard_optout_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cube = cmds.polyCube(name="guardOptOutCube")[0]
        with mock.patch.object(baker, "arnold_translation_guard", spy):
            baker.bake([cube], output_dir=tmp, backend="arnold")
        self.assertEqual(entered, [])


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestResolveMeshes))
    suite.addTests(
        loader.loadTestsFromTestCase(TestPlaceOutputSurvivesLockedDestination)
    )
    suite.addTests(loader.loadTestsFromTestCase(TestArnoldAvailable))
    suite.addTests(loader.loadTestsFromTestCase(TestArnoldBakeOutputNaming))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeUvSetTargeting))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeProgressCallback))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeNaming))
    suite.addTests(loader.loadTestsFromTestCase(TestBakeStemEndToEnd))
    suite.addTests(loader.loadTestsFromTestCase(TestPinnedRenderSettings))
    return unittest.TextTestRunner(verbosity=2).run(suite)


if __name__ == "__main__":
    run_tests()
