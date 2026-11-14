# !/usr/bin/python
# coding=utf-8
"""Tests for ``env_utils.material_records`` -- the ``materials`` record's Maya
producer.

The producer only gathers each shader's values in its family's vocabulary
(``ptk.MaterialFamilies`` decides what they mean, pinned by pythontk's
``test_material_record``), so these pin the gathering against real shaders,
and -- the proof that the gathering is right -- that a file Maya's own FBX
exporter writes, read back by pythontk's reader, states every material as the
producer did. Added: 2026-10-20. Record v2 (2026-11-05): the record is literal
glTF -- the producer packs metal and roughness, states emission above 1 as its
strength, unlit and the UV transform -- and a real export's FBX record and the
GLB built from it state the same materials (``TestTheRecordIsLiteralGltf``).
"""

import os
import struct
import unittest
import zlib

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase


def _png(path, grey=128):
    """A 4x4 grey PNG at *path*: a texture Maya's FBX exporter references."""

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + bytes([grey]) * 4 for _ in range(4))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class _Scene(MayaTkTestCase):
    """Shaders on cubes, their maps on disk."""

    def setUp(self):
        super().setUp()
        store = ptk.TempArtifacts("mtk_material_records", policy="scoped")
        self.dir = store.dir_path()
        self.addCleanup(store.cleanup)

    def _assign(self, mat, name):
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube

    def _file(self, name, grey=128):
        node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(
            f"{node}.fileTextureName",
            _png(os.path.join(self.dir, f"{name}.png"), grey),
            type="string",
        )
        return node

    def _stingray(self, name, **props):
        from mayatk.mat_utils._mat_utils import MatUtils

        cmds.loadPlugin("shaderFXPlugin", quiet=True)
        mat = MatUtils.create_stingray_shader(name)
        for slot, map_name in (
            ("TEX_color_map", f"{name}_BaseColor"),
            ("TEX_metallic_map", f"{name}_Metallic"),
            ("TEX_roughness_map", f"{name}_Roughness"),
        ):
            cmds.connectAttr(f"{self._file(map_name)}.outColor", f"{mat}.{slot}", force=True)
        for attr, value in {
            "use_color_map": 1.0,
            "use_metallic_map": 1.0,
            "use_roughness_map": 1.0,
            **props,
        }.items():
            if isinstance(value, tuple):
                cmds.setAttr(f"{mat}.{attr}", *value, type="double3")
            else:
                cmds.setAttr(f"{mat}.{attr}", value)
        return self._assign(mat, name), mat

    def _phong_lens(self, name="LENS"):
        mat = cmds.shadingNode("phong", asShader=True, name=name)
        cmds.setAttr(f"{mat}.color", 0.5, 0.5, 0.5, type="double3")
        cmds.setAttr(f"{mat}.diffuse", 0.8)
        cmds.setAttr(f"{mat}.transparency", 0.9, 0.9, 0.9, type="double3")
        cmds.setAttr(f"{mat}.cosinePower", 30.0)
        return self._assign(mat, name), mat

    def _standard_surface(self, name="SURF"):
        mat = cmds.shadingNode("standardSurface", asShader=True, name=name)
        cmds.setAttr(f"{mat}.base", 0.5)
        cmds.setAttr(f"{mat}.baseColor", 0.8, 0.4, 0.2, type="double3")
        cmds.setAttr(f"{mat}.metalness", 1.0)
        cmds.setAttr(f"{mat}.specularRoughness", 0.25)
        cmds.setAttr(f"{mat}.emission", 2.0)
        cmds.setAttr(f"{mat}.emissionColor", 1.0, 0.5, 0.0, type="double3")
        return self._assign(mat, name), mat

    @staticmethod
    def _entries(objects):
        """``{name: statement}``, each map by its file name (a statement
        keeps the path a packer reads)."""
        from mayatk.env_utils.material_records import MaterialRecords

        found = {}
        for entry in MaterialRecords.entries(objects):
            for field in ptk.MaterialFamilies.TEXTURE_FIELDS:
                if entry.get(field):
                    entry[field] = os.path.basename(entry[field])
            found[entry["name"]] = entry
        return found


class TestReadingEachFamily(_Scene):
    def test_a_stingray_is_read_off_its_graph(self):
        """Its own attribute names -- a map in use stands in for its value,
        the inert ``base_color`` stays out of a mapped albedo, the emission
        is the intensity."""
        cube, _ = self._stingray(
            "LAMP",
            base_color=(0.5, 0.5, 0.5),
            emissive=(1.0, 0.5, 0.0),
            emissive_intensity=0.75,
            use_emissive_map=0.0,
        )
        entry = self._entries([cube])["LAMP"]
        self.assertEqual(entry["baseColorFactor"], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(entry["baseColorTexture"], "LAMP_BaseColor.png")
        self.assertEqual(
            (entry["metallicFactor"], entry["metallicTexture"]), (1.0, "LAMP_Metallic.png")
        )
        self.assertEqual([round(c, 4) for c in entry["emissiveFactor"]], [0.75, 0.375, 0.0])
        self.assertEqual(entry["alphaMode"], "OPAQUE")

    def test_a_phong_lens_reads_as_its_fbx_writes_it_and_blends(self):
        cube, _ = self._phong_lens()
        entry = self._entries([cube])["LENS"]
        self.assertEqual([round(c, 4) for c in entry["baseColorFactor"]], [0.4, 0.4, 0.4, 0.1])
        self.assertAlmostEqual(entry["metallicFactor"], 0.4)
        self.assertAlmostEqual(entry["roughnessFactor"], (2.0 / 32.0) ** 0.5, places=6)
        self.assertEqual(entry["alphaMode"], "BLEND")

    def test_a_standard_surface_reads_its_weights(self):
        cube, _ = self._standard_surface()
        entry = self._entries([cube])["SURF"]
        self.assertEqual([round(c, 4) for c in entry["baseColorFactor"]], [0.4, 0.2, 0.1, 1.0])
        self.assertEqual((entry["metallicFactor"], entry["roughnessFactor"]), (1.0, 0.25))
        self.assertEqual([round(c, 4) for c in entry["emissiveFactor"]], [2.0, 1.0, 0.0])

    def test_a_surfaces_opacity_and_transmission_read_as_the_model_rules(self):
        """The opacity colour's one value and the transmission note are the
        model's (``ptk.MaterialFamilies``): a tinted opacity reads as its
        channels' mean, a glass says the record states no transmission.
        Added: 2026-10-31"""
        from mayatk.env_utils.material_records import MaterialRecords

        cube, mat = self._standard_surface("GLASS")
        cmds.setAttr(f"{mat}.opacity", 0.25, 0.5, 0.75, type="double3")
        cmds.setAttr(f"{mat}.transmission", 0.5)
        notes = []
        (entry,) = MaterialRecords.entries([cube], notes=notes)
        self.assertEqual(round(entry["baseColorFactor"][3], 4), 0.5)
        self.assertEqual(
            notes, [f"Material GLASS: {ptk.MaterialFamilies.TRANSMISSION_NOTE}"]
        )

    def test_a_surface_shader_is_unlit(self):
        mat = cmds.shadingNode("surfaceShader", asShader=True, name="FLAT")
        cmds.setAttr(f"{mat}.outColor", 0.2, 0.3, 0.4, type="double3")
        cube = self._assign(mat, "FLAT")
        entry = self._entries([cube])["FLAT"]
        self.assertTrue(entry["unlit"])
        self.assertEqual([round(c, 4) for c in entry["baseColorFactor"]], [0.2, 0.3, 0.4, 1.0])

    def test_a_shader_no_family_reads_is_left_to_the_file_and_said(self):
        from mayatk.env_utils.material_records import MaterialRecords

        mat = cmds.shadingNode("rampShader", asShader=True, name="RAMP")
        cube = self._assign(mat, "RAMP")
        notes = []
        self.assertEqual(MaterialRecords.entries([cube], notes=notes), [])
        self.assertTrue(any("rampShader" in note for note in notes), notes)

    def test_the_export_set_narrows_the_record(self):
        from mayatk.env_utils.material_records import MaterialRecords

        lens, _ = self._phong_lens()
        surf, _ = self._standard_surface()
        record = MaterialRecords.export_record(ptk.ExportContext(scope=(lens,)))
        self.assertEqual([e["name"] for e in record.payload["materials"]], ["LENS"])
        whole = MaterialRecords.export_record(ptk.ExportContext())
        self.assertEqual(
            {e["name"] for e in whole.payload["materials"]} >= {"LENS", "SURF"}, True
        )
        # An empty export set ships nothing, as the shadow record and the
        # hand-off read it -- not the whole scene (``if scope:``; blendertk's
        # twin already read ``is None``). Added: 2026-10-24
        self.assertIsNone(MaterialRecords.export_record(ptk.ExportContext(scope=())))

    def test_a_hierarchy_root_states_what_its_descendants_wear(self):
        """``get_mats`` reads direct shapes only: a group counts as its
        contents, a component as its object's faces, a name the scene does
        not hold as nothing -- one batched expansion (``_expand``), the maps
        read per material rather than by a second expansion. Added: 2026-10-24"""
        from mayatk.env_utils.material_records import MaterialRecords

        lens, _ = self._phong_lens()
        surf, _ = self._standard_surface()
        group = cmds.group(lens, surf, name="GRP")
        cmds.group(group, name="ROOT")
        self.assertEqual(
            sorted(e["name"] for e in MaterialRecords.entries(["ROOT"])),
            ["LENS", "SURF"],
        )
        record = MaterialRecords.export_record(
            ptk.ExportContext(scope=("ROOT", "NO_SUCH_NODE"))
        )
        self.assertEqual(
            sorted(e["name"] for e in record.payload["materials"]), ["LENS", "SURF"]
        )
        faces = f"{cmds.ls(lens, long=True)[0]}.f[0:5]"
        self.assertEqual([e["name"] for e in MaterialRecords.entries([faces])], ["LENS"])


class TestTheFileReadsAsTheSceneStatedIt(_Scene):
    """Maya's own FBX exporter writes each family's values, and pythontk's
    reader of a record-less file reads them into the same families: the two
    must state every material alike, or a file read back says other than its
    export did. The proof that the gathering is right."""

    def _write(self, objects, name="conformance.fbx"):
        from mayatk.env_utils.fbx_utils import FbxUtils

        path = os.path.join(self.dir, name)
        # A raw write: the bracket's producers would carry the record, and
        # this is about what the file says without one.
        with FbxUtils.scratch_export():
            FbxUtils.export(path, objects=list(objects))
        return path

    def test_every_family_reads_back_as_it_was_stated(self):
        lamp, _ = self._stingray(
            "LAMP", base_color=(0.5, 0.5, 0.5), emissive_intensity=0.75
        )
        lens, _ = self._phong_lens()
        surf, _ = self._standard_surface()
        flat = cmds.shadingNode("lambert", asShader=True, name="FLAT")
        cmds.setAttr(f"{flat}.color", 1.0, 0.0, 0.0, type="double3")
        plain = self._assign(flat, "FLAT")
        objects = [lamp, lens, surf, plain]
        stated = ptk.MaterialFamilies.payload(self._entries(objects).values())
        read = ptk.MeshConvert.read_fbx_materials(self._write(objects))
        mine = ptk.MaterialFamilies.materials_by_name(stated)
        theirs = ptk.MaterialFamilies.materials_by_name(read)
        for name in ("LAMP", "LENS", "SURF", "FLAT"):
            with self.subTest(material=name):
                self.assertIn(name, theirs)
                self.assertEqual(
                    ptk.MaterialFamilies.material_files(stated, mine[name]),
                    ptk.MaterialFamilies.material_files(read, theirs[name]),
                )
                self.assertEqual(mine[name]["alphaMode"], theirs[name]["alphaMode"])
                self.assertEqual(
                    [round(c, 4) for c in mine[name]["pbrMetallicRoughness"]["baseColorFactor"]],
                    [round(c, 4) for c in theirs[name]["pbrMetallicRoughness"]["baseColorFactor"]],
                )
                self.assertEqual(
                    [round(c, 4) for c in ptk.MaterialFamilies.emission(mine[name])],
                    [round(c, 4) for c in ptk.MaterialFamilies.emission(theirs[name])],
                )
                for key in ("metallicFactor", "roughnessFactor"):
                    self.assertAlmostEqual(
                        mine[name]["pbrMetallicRoughness"][key],
                        theirs[name]["pbrMetallicRoughness"][key],
                        4,
                        key,
                    )

    def test_blinn_and_phong_e_read_back_as_they_were_stated(self):
        """The legacy models Maya's exporter writes as a Phong: whatever it
        writes as their shininess, the producer must state the same."""
        cubes = []
        for node_type in ("blinn", "phongE"):
            mat = cmds.shadingNode(node_type, asShader=True, name=node_type.upper())
            cubes.append(self._assign(mat, node_type.upper()))
        stated = self._entries(cubes)
        read = ptk.MaterialFamilies.materials_by_name(
            ptk.MeshConvert.read_fbx_materials(self._write(cubes, "legacy.fbx"))
        )
        for name in ("BLINN", "PHONGE"):
            with self.subTest(material=name):
                for field in ("metallicFactor", "roughnessFactor"):
                    self.assertAlmostEqual(
                        stated[name][field], read[name]["pbrMetallicRoughness"][field], 4, field
                    )


class TestDiscardingAnEmptyCarrier(_Scene):
    """``DataNodes.discard_if_empty``: only a carrier that holds nothing goes."""

    def test_only_a_carrier_holding_nothing_goes(self):
        from mayatk.node_utils.data_nodes import DataNodes

        scope = ptk.Scope.DELIVERABLE
        DataNodes.write(scope, "materials", "{}")
        DataNodes.write(scope, "materials", "")
        cmds.addAttr(DataNodes.EXPORT, longName="emissiveGroup_Neon", attributeType="float")
        cmds.setAttr(f"{DataNodes.EXPORT}.emissiveGroup_Neon", 0.5)
        self.assertFalse(DataNodes.discard_if_empty(scope), "a weight is content")
        cmds.deleteAttr(f"{DataNodes.EXPORT}.emissiveGroup_Neon")
        self.assertFalse(DataNodes.discard_if_empty(ptk.Scope.PRIVATE))
        self.assertTrue(DataNodes.discard_if_empty(scope))
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))
        self.assertFalse(DataNodes.discard_if_empty(scope), "nothing left to remove")


class TestTheRecordRidesTheWriteAndLeavesTheScene(_Scene):
    """Transient: the export's bracket publishes it for the write, the file
    carries it, and the scene does not keep it once the bracket closes."""

    def test_a_bracketed_write_carries_it_and_the_scene_does_not_keep_it(self):
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        lens, _ = self._phong_lens()
        path = os.path.join(self.dir, "carried.fbx")
        ctx = FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF)
        with FbxUtils.export_prepared(ctx, only=[ptk.SceneRecords.MATERIALS]):
            self.assertIsNotNone(ptk.SceneRecords.MATERIALS.load(DataNodes))
            FbxUtils.export(path, objects=[lens, DataNodes.EXPORT])
        self.assertIsNone(
            ptk.SceneRecords.MATERIALS.load(DataNodes), "the scene never holds it"
        )
        self.assertFalse(
            cmds.objExists(DataNodes.EXPORT), "nor the carrier made for it alone"
        )
        carried = ptk.FbxMedia.record(path, "materials")
        self.assertEqual([e["name"] for e in carried["materials"]], ["LENS"])
        self.assertEqual(carried["materials"][0]["alphaMode"], "BLEND")


class TestLightmappedMaterialsLeaveTheirOcclusion(_Scene):
    """Lightmaps v3 (2026-11-07): the bake holds an object's occlusion, so a
    material only lightmapped objects wear ships without its occlusion map;
    one that lightmapped and unbaked objects share keeps it, and the export
    says so once, with the fix (``ptk.MaterialFamilies.export_record``).
    Decided against the ``lightmaps`` record the write ships, by mesh shape:
    a group root as the scope (as every real export passes it) counted its
    contents as unbaked too, so each baked material kept its occlusion with a
    false note (measured 2026-11-07)."""

    def _ao_stingray(self, name):
        cube, mat = self._stingray(name, use_ao_map=1.0)
        cmds.connectAttr(
            f"{self._file(f'{name}_AO')}.outColor", f"{mat}.TEX_ao_map", force=True
        )
        return cube

    @staticmethod
    def _bake(cube, slot=1):
        """*cube* baked as a committed bake leaves it: a lightmap set (at
        UV index *slot*) and the marker naming it."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
        from mayatk.uv_utils._uv_utils import UvUtils

        long = cmds.ls(cube, long=True)[0]
        shape = cmds.listRelatives(long, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="lightmap")
        if slot == 2:
            cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="filler")
            UvUtils.reorder_uv_sets(shape, ["map1", "filler", "lightmap"])
        LightmapRecords._write_marker(
            long, {"map": f"{cube}_Lightmap.exr", "uv_set": "lightmap"}
        )
        return long

    def _materials(self, ctx):
        """The materials a write of *ctx* ships, both producers run as an
        export runs them: the lightmaps first, the materials reading it."""
        from mayatk.env_utils.material_records import MaterialRecords
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        snapshot = ptk.ExportSnapshot.assemble(
            {
                ptk.SceneRecords.LIGHTMAPS: LightmapRecords.export_record,
                ptk.SceneRecords.MATERIALS: MaterialRecords.export_record,
            },
            ctx,
        )
        record = snapshot.record(ptk.SceneRecords.MATERIALS)
        return {m["name"]: m for m in record["materials"]}

    @staticmethod
    def _occlusion_notes(ctx):
        return [note for note in ctx.notes if "occlusion map" in note]

    def test_a_material_only_lightmapped_objects_wear_ships_no_occlusion(self):
        self._bake(self._ao_stingray("WALL"))
        self._bake(self._ao_stingray("SHARED"))
        unbaked = cmds.polyCube(name="SHARED_unbaked_geo")[0]
        cmds.sets(unbaked, edit=True, forceElement="SHAREDSG")
        ctx = ptk.ExportContext()
        materials = self._materials(ctx)
        self.assertNotIn("occlusionTexture", materials["WALL"], "the bake holds it")
        self.assertIn("metallicRoughnessTexture", materials["WALL"]["pbrMetallicRoughness"])
        self.assertIn("occlusionTexture", materials["SHARED"], "the unbaked cube needs it")
        said = [note for note in ctx.notes if "SHARED" in note]
        self.assertEqual(len(said), 1, ctx.notes)
        self.assertIn("give the lightmapped objects their own material", said[0])
        self.assertFalse(any("WALL" in note for note in ctx.notes), ctx.notes)

    def test_without_a_bake_every_material_keeps_its_occlusion(self):
        self._ao_stingray("WALL")
        ctx = ptk.ExportContext()
        self.assertIn("occlusionTexture", self._materials(ctx)["WALL"])
        self.assertFalse(self._occlusion_notes(ctx), ctx.notes)

    def test_a_group_root_scope_bakes_what_is_under_it(self):
        wall = self._bake(self._ao_stingray("WALL"))
        floor = self._ao_stingray("FLOOR")
        from mayatk.env_utils.fbx_utils import FbxUtils

        room = cmds.group(wall, floor, name="ROOM")
        ctx = FbxUtils.export_context(scope=(cmds.ls(room, long=True)[0],))
        materials = self._materials(ctx)
        self.assertNotIn("occlusionTexture", materials["WALL"], "the bake holds it")
        self.assertIn("occlusionTexture", materials["FLOOR"], "unbaked")
        self.assertFalse(self._occlusion_notes(ctx), ctx.notes)

    def test_a_shape_instanced_under_a_baked_transform_is_baked(self):
        cube = self._ao_stingray("CRATE")
        twin = cmds.instance(cube, name="CRATE_twin")[0]
        self._bake(cube)
        ctx = ptk.ExportContext()
        self.assertNotIn("occlusionTexture", self._materials(ctx)["CRATE"])
        self.assertFalse(self._occlusion_notes(ctx), ctx.notes)
        self.assertTrue(cmds.objExists(twin))

    def test_a_per_face_assignment_counts_each_material_on_its_shape(self):
        baked = self._bake(self._ao_stingray("PANEL"))
        self._ao_stingray("TRIM")  # its own cube stays unbaked
        cmds.sets(f"{baked}.f[0:1]", edit=True, forceElement="TRIMSG")
        ctx = ptk.ExportContext()
        materials = self._materials(ctx)
        self.assertNotIn("occlusionTexture", materials["PANEL"])
        self.assertIn("occlusionTexture", materials["TRIM"], "the unbaked cube wears it")
        said = [note for note in self._occlusion_notes(ctx) if "TRIM" in note]
        self.assertEqual(len(said), 1, ctx.notes)

    def test_a_baked_object_the_write_leaves_out_bakes_nothing_it_ships(self):
        self._bake(self._ao_stingray("SHARED"))
        kept = cmds.polyCube(name="SHARED_kept_geo")[0]
        cmds.sets(kept, edit=True, forceElement="SHAREDSG")
        from mayatk.env_utils.fbx_utils import FbxUtils

        ctx = FbxUtils.export_context(scope=(cmds.ls(kept, long=True)[0],))
        self.assertIn("occlusionTexture", self._materials(ctx)["SHARED"])
        self.assertFalse(self._occlusion_notes(ctx), ctx.notes)

    def test_a_refused_lightmaps_record_leaves_every_occlusion(self):
        """No lightmap ships, so none holds the occlusion. Added: 2026-11-07"""
        self._bake(self._ao_stingray("WALL"), slot=2)
        ctx = ptk.ExportContext()
        self.assertIn("occlusionTexture", self._materials(ctx)["WALL"])
        self.assertFalse(self._occlusion_notes(ctx), ctx.notes)
        self.assertTrue(any("Lightmaps not published" in n for n in ctx.notes), ctx.notes)

    def test_the_baker_is_named_by_no_row(self):
        """The materials read the record the write ships, so the row that
        named the baker's markers from a lower part retires (CODE_STANDARD
        section 15). Added: 2026-11-07"""
        from mayatk.env_utils.material_records import MaterialRecords

        self.assertFalse(hasattr(MaterialRecords, "LIGHTMAPPED"))
        self.assertEqual(ptk.SceneRecords.MATERIALS.after, ("lightmaps",))


class TestTheRecordIsLiteralGltf(_Scene):
    """Record v2 from the scene: metal and roughness packed into one image
    of the pack store, emission above 1 as the strength, Maya's unlit model as
    KHR_materials_unlit, a place2dTexture's repeat and offset as
    KHR_texture_transform -- and a real export: the record the FBX carries,
    and the GLB built from that FBX, whose materials are the record's.
    Added: 2026-11-05"""

    def _export_record(self, objects):
        from mayatk.env_utils.material_records import MaterialRecords

        record = MaterialRecords.export_record(ptk.ExportContext(scope=tuple(objects)))
        return record.payload

    def test_a_stingray_packs_its_metal_and_roughness_into_a_delivery_file(self):
        """The packed image is a delivery file of the pack store: nothing is
        written beside the maps it packs."""
        cube, _ = self._stingray("LAMP")
        before = sorted(os.listdir(self.dir))
        record = self._export_record([cube])
        self.assertEqual(ptk.SceneRecords.problems(ptk.SceneRecords.MATERIALS, record), [])
        (material,) = record["materials"]
        files = ptk.MaterialFamilies.material_files(record, material)
        self.assertEqual(files["metallicRoughnessTexture"], "LAMP_ORM.png")
        self.assertTrue(os.path.isfile(ptk.MaterialFamilies.packed_file("LAMP_ORM.png")))
        self.assertEqual(sorted(os.listdir(self.dir)), before, "nothing beside the maps")

    def test_emission_above_one_is_the_strength_and_a_surface_shader_unlit(self):
        surf, _ = self._standard_surface()
        mat = cmds.shadingNode("surfaceShader", asShader=True, name="FLAT")
        flat = self._assign(mat, "FLAT")
        by_name = ptk.MaterialFamilies.materials_by_name(self._export_record([surf, flat]))
        self.assertEqual(
            by_name["SURF"]["extensions"],
            {"KHR_materials_emissive_strength": {"emissiveStrength": 2.0}},
        )
        self.assertEqual(by_name["SURF"]["emissiveFactor"], [1.0, 0.5, 0.0])
        self.assertEqual(by_name["FLAT"]["extensions"], {"KHR_materials_unlit": {}})

    def test_a_place2d_repeat_and_offset_are_the_texture_transform(self):
        surf, mat = self._standard_surface("TILE")
        file_node = self._file("TILE_BaseColor")
        cmds.connectAttr(f"{file_node}.outColor", f"{mat}.baseColor", force=True)
        place = cmds.shadingNode("place2dTexture", asUtility=True)
        cmds.connectAttr(f"{place}.outUV", f"{file_node}.uvCoord", force=True)
        cmds.setAttr(f"{place}.repeatUV", 2.0, 3.0, type="double2")
        cmds.setAttr(f"{place}.offset", 0.25, 0.5, type="double2")
        (material,) = self._export_record([surf])["materials"]
        info = material["pbrMetallicRoughness"]["baseColorTexture"]
        self.assertEqual(
            info["extensions"]["KHR_texture_transform"],
            {"offset": [0.25, 1.0 - 3.0 - 0.5], "scale": [2.0, 3.0]},
        )

    def test_a_real_exports_glb_materials_are_its_fbx_record(self):
        """The FBX's record read back, and the GLB built from that FBX alone:
        each GLB material is the record's (names, factors, maps by file,
        extensions)."""
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        lamp, _ = self._stingray("LAMP", emissive=(1.0, 0.5, 0.0), emissive_intensity=3.0)
        surf, _ = self._standard_surface()
        mat = cmds.shadingNode("surfaceShader", asShader=True, name="FLAT")
        flat = self._assign(mat, "FLAT")
        path = os.path.join(self.dir, "real.fbx")
        ctx = FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF)
        with FbxUtils.export_prepared(ctx, only=[ptk.SceneRecords.MATERIALS]):
            FbxUtils.export(path, objects=[lamp, surf, flat, DataNodes.EXPORT])
        ptk.FbxMedia.embed_dependencies(path, search_dirs=[self.dir])
        record = ptk.FbxMedia.record(path, "materials")
        self.assertEqual(ptk.SceneRecords.problems(ptk.SceneRecords.MATERIALS, record), [])
        self.assertEqual(
            sorted(m["name"] for m in record["materials"]), ["FLAT", "LAMP", "SURF"]
        )
        built = ptk.GlbPipeline.build(path, os.path.join(self.dir, "real.glb"), downsize=False)
        with ptk.MeshConvert.open_glb(built["glb"]) as edit:
            assert_materials_are_the_records(self, edit, record)


def assert_materials_are_the_records(case, edit, record):
    """Every material *record* names is, in the open GLB, the record's own
    JSON, each map the GLB image of the file the record names (by stem: the
    build may re-encode the bytes)."""
    import copy

    def resolved(material, image_of):
        found = copy.deepcopy(material)
        found.pop("extras", None)
        for container, slot in ptk.MaterialFamilies.TEXTURE_SLOTS:
            holder = found.get(container) if container else found
            if isinstance(holder, dict) and isinstance(holder.get(slot), dict):
                holder[slot]["index"] = image_of(holder[slot]["index"])
        return found

    def glb_image(index):
        label = edit.image_label(edit.image_for_texture(index))
        return os.path.splitext(os.path.basename(label))[0].casefold()

    def record_image(index):
        name = ptk.MaterialFamilies.texture_file(record, index)
        return os.path.splitext(name)[0].casefold()

    by_name = {m.get("name"): m for m in edit.materials}
    for material in record["materials"]:
        with case.subTest(material=material["name"]):
            case.assertIn(material["name"], by_name)
            case.assertEqual(
                resolved(by_name[material["name"]], glb_image),
                resolved(material, record_image),
            )


if __name__ == "__main__":
    unittest.main()
