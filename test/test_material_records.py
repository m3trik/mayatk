# !/usr/bin/python
# coding=utf-8
"""Tests for ``env_utils.material_records`` -- the ``materials`` record's Maya
producer.

The producer only gathers each shader's values in its family's vocabulary
(``ptk.MaterialFamilies`` decides what they mean, pinned by pythontk's
``test_material_record``), so these pin the gathering against real shaders,
and -- the proof that the gathering is right -- that a file Maya's own FBX
exporter writes, read back by pythontk's reader, states every material as the
producer did. Added: 2026-10-20
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
        from mayatk.env_utils.material_records import MaterialRecords

        return {e["name"]: e for e in MaterialRecords.entries(objects)}


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
        stated = self._entries(objects)
        read = {
            e["name"]: e
            for e in ptk.MeshConvert.read_fbx_materials(self._write(objects))["materials"]
        }
        for name in ("LAMP", "LENS", "SURF", "FLAT"):
            with self.subTest(material=name):
                self.assertIn(name, read)
                for field in (
                    "baseColorTexture",
                    "metallicTexture",
                    "roughnessTexture",
                    "alphaMode",
                ):
                    self.assertEqual(stated[name].get(field), read[name].get(field), field)
                for field in ("baseColorFactor", "emissiveFactor"):
                    self.assertEqual(
                        [round(c, 4) for c in stated[name][field]],
                        [round(c, 4) for c in read[name][field]],
                        field,
                    )
                for field in ("metallicFactor", "roughnessFactor"):
                    self.assertAlmostEqual(stated[name][field], read[name][field], 4, field)

    def test_blinn_and_phong_e_read_back_as_they_were_stated(self):
        """The legacy models Maya's exporter writes as a Phong: whatever it
        writes as their shininess, the producer must state the same."""
        cubes = []
        for node_type in ("blinn", "phongE"):
            mat = cmds.shadingNode(node_type, asShader=True, name=node_type.upper())
            cubes.append(self._assign(mat, node_type.upper()))
        stated = self._entries(cubes)
        read = {
            e["name"]: e
            for e in ptk.MeshConvert.read_fbx_materials(self._write(cubes, "legacy.fbx"))[
                "materials"
            ]
        }
        for name in ("BLINN", "PHONGE"):
            with self.subTest(material=name):
                for field in ("metallicFactor", "roughnessFactor"):
                    self.assertAlmostEqual(stated[name][field], read[name][field], 4, field)


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


if __name__ == "__main__":
    unittest.main()
