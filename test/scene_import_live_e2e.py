# !/usr/bin/python
# coding=utf-8
"""Live end-to-end check for ``mtk.import_blender_scene`` (Blender -> Maya pull).

MANUAL, GATED: requires a local Blender install AND a Maya license (run under
``mayapy``). Deliberately NOT named ``test_*`` so ``run_tests.py`` never picks it
up — the stubbed coverage lives in ``test_scene_import.py``; this exercises the
real thing end to end (the mirror of blendertk's ``scene_import_live_e2e.py``).

Run:
    & "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" mayatk\\test\\scene_import_live_e2e.py

Builds a real ``.blend`` in a fresh headless Blender with four trap objects:

- ``e2e_cube``    — TWO materials on one mesh (per-face split): ``e2e_matA.001``
                    (dotted Blender name + linked Base_Color + unlinked
                    Normal_OpenGL + PACKED Metallic_Smoothness) and ``e2e_flat``
                    (no textures — must ride the FBX untouched).
- ``e2e_sphere`` /
  ``e2e_cone``    — ONE shared textured material (``e2e_shared``) — must rebuild
                    exactly once, assigned to both.
- ``e2e_missing`` — material whose image points at a nonexistent file — must
                    surface as a NAMED warning, not silent gray.

Then imports it into an initialized ``maya.standalone`` via the production path
and asserts the rebuilt networks, per-face preservation, packed-map wiring,
warnings, conversion-cache behavior, and temp hygiene. All artifacts cleaned up.
"""
import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

MONO = r"O:\Cloud\Code\_scripts"
for p in (rf"{MONO}\mayatk", rf"{MONO}\pythontk", rf"{MONO}\uitk"):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


# Blender-side fixture builder (dependency-free bpy; rendered with the work dir).
BUILD_TEMPLATE = '''
import os
import bpy

OUT_DIR = r"__OUT_DIR__"
BLEND = r"__BLEND__"

bpy.ops.wm.read_homefile(use_empty=True)


def png(name, rgba):
    img = bpy.data.images.new(name, width=8, height=8, alpha=True)
    img.pixels = list(rgba) * 64
    img.filepath_raw = os.path.join(OUT_DIR, name + ".png")
    img.file_format = "PNG"
    img.save()
    return img.filepath_raw


def material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    return mat


def tex_node(mat, path, link_to=None):
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = bpy.data.images.load(path)
    if link_to:
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs[link_to])
    return node


# --- e2e_cube: two materials on one mesh (dotted name + packed map traps) ----
base_a = png("e2e_matA_Base_Color", (0.8, 0.2, 0.2, 1.0))
norm_a = png("e2e_matA_Normal_OpenGL", (0.5, 0.5, 1.0, 1.0))
ms_a = png("e2e_matA_Metallic_Smoothness", (1.0, 0.0, 0.0, 0.5))
mat_a = material("e2e_matA.001")
tex_node(mat_a, base_a, link_to="Base Color")
tex_node(mat_a, norm_a)  # unlinked: manifest transport, not FBX transport
tex_node(mat_a, ms_a)    # packed map: NO FBX slot exists -- manifest only
mat_flat = material("e2e_flat")  # untextured: no manifest entry, rides the FBX

bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
cube.name = "e2e_cube"
cube.data.materials.append(mat_a)
cube.data.materials.append(mat_flat)
for i, poly in enumerate(cube.data.polygons):
    poly.material_index = 0 if i < 3 else 1

# --- e2e_sphere + e2e_cone: one shared textured material ---------------------
base_s = png("e2e_shared_Base_Color", (0.2, 0.8, 0.2, 1.0))
mat_shared = material("e2e_shared")
tex_node(mat_shared, base_s, link_to="Base Color")
for maker, name in (
    (bpy.ops.mesh.primitive_uv_sphere_add, "e2e_sphere"),
    (bpy.ops.mesh.primitive_cone_add, "e2e_cone"),
):
    maker()
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(mat_shared)

# --- e2e_missing: image path that does not exist ------------------------------
mat_gone = material("e2e_gone")
node = mat_gone.node_tree.nodes.new("ShaderNodeTexImage")
img = bpy.data.images.new("gone", width=4, height=4)
img.filepath = os.path.join(OUT_DIR, "nowhere", "e2e_gone_Base_Color.png")
img.source = "FILE"
node.image = img
bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
missing = bpy.context.active_object
missing.name = "e2e_missing"
missing.data.materials.append(mat_gone)

bpy.ops.wm.save_as_mainfile(filepath=BLEND)
print("fixture saved:", BLEND)
'''


work = tempfile.mkdtemp(prefix="mtk_scene_import_e2e_")
cache_glob = os.path.join(tempfile.gettempdir(), "blender_to_mtk_cache_*")
ok = False
try:
    from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

    blender = BlenderSceneImport(log_level="WARNING").require_blender()

    # ---- phase 1: build the fixture .blend in a FRESH headless Blender ------
    blend = os.path.join(work, "e2e_scene.blend")
    build_path = os.path.join(work, "build_scene.py")
    with open(build_path, "w", encoding="utf-8") as fh:
        fh.write(
            BUILD_TEMPLATE
            .replace("__OUT_DIR__", work)
            .replace("__BLEND__", blend)
        )
    proc = subprocess.run(
        [blender, "--background", "--factory-startup", "--python", build_path],
        capture_output=True, text=True, timeout=300,
    )
    check(".blend fixture built", os.path.isfile(blend),
          (proc.stdout + proc.stderr)[-800:] if not os.path.isfile(blend) else "")

    # ---- phase 2: import via the production path under maya.standalone ------
    # Snapshot pre-existing conversion scratch (debris from unrelated crashed
    # runs is kept-for-debugging by design); the hygiene check below must flag
    # only what THIS run leaks.
    def scratch_files():
        return {
            p for p in glob.glob(
                os.path.join(tempfile.gettempdir(), "blender_to_mtk_*"))
            if "cache" not in os.path.basename(p)
        }

    pre_scratch = scratch_files()

    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    cmds.file(new=True, force=True)

    import mayatk as mtk

    eng = mtk.BlenderSceneImport()
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    eng.logger.addHandler(_Capture())

    t0 = time.time()
    imported = eng.import_scene(blend)
    first_duration = time.time() - t0

    def find(prefix, nodes):
        return [n for n in nodes if n.split("|")[-1].startswith(prefix)]

    check("all four objects imported",
          all(find(p, imported) for p in
              ("e2e_cube", "e2e_sphere", "e2e_cone", "e2e_missing")),
          imported)

    # shared material: rebuilt exactly ONCE, assigned to both meshes
    shared = [n for n in cmds.ls(type="standardSurface") or []
              if n.startswith("e2e_shared")]
    check("shared material rebuilt once as standardSurface",
          len(shared) == 1, shared)
    if shared:
        sgs = cmds.listConnections(shared[0], type="shadingEngine") or []
        members = [m for sg in set(sgs) for m in (cmds.sets(sg, query=True) or [])]
        check("shared SG covers sphere AND cone",
              any("e2e_sphere" in m for m in members)
              and any("e2e_cone" in m for m in members), members)
        srcs = cmds.listConnections(f"{shared[0]}.baseColor",
                                    source=True, destination=False) or []
        files = [cmds.getAttr(f"{s}.fileTextureName") for s in srcs
                 if cmds.nodeType(s) == "file"]
        check("shared baseColor fed by the original texture file",
              any(f.endswith("e2e_shared_Base_Color.png") for f in files), files)

    # matA (dotted Blender name): rebuilt + packed Metallic_Smoothness wired
    mat_a = [n for n in cmds.ls(type="standardSurface") or []
             if n.startswith("e2e_matA_001")]
    check("dotted-name material rebuilt (e2e_matA.001 -> e2e_matA_001)",
          len(mat_a) == 1, mat_a)
    if mat_a:
        base_srcs = cmds.listConnections(f"{mat_a[0]}.baseColor",
                                         source=True, destination=False) or []
        check("matA baseColor connected", bool(base_srcs), base_srcs)
        metal_srcs = cmds.listConnections(f"{mat_a[0]}.metalness",
                                          source=True, destination=False) or []
        rough_srcs = cmds.listConnections(f"{mat_a[0]}.specularRoughness",
                                          source=True, destination=False) or []
        check("packed Metallic_Smoothness wired (metalness + roughness fed)",
              bool(metal_srcs) and bool(rough_srcs),
              f"metal={metal_srcs} rough={rough_srcs}")
        normal_srcs = cmds.listConnections(f"{mat_a[0]}.normalCamera",
                                           source=True, destination=False) or []
        check("matA normal chain connected", bool(normal_srcs), normal_srcs)

        # per-face preservation on the two-material cube
        cube_tf = find("e2e_cube", imported)[0]
        shape = (cmds.listRelatives(cube_tf, shapes=True, fullPath=True) or [None])[0]
        cube_sgs = set(cmds.listConnections(shape, type="shadingEngine") or [])
        check("cube keeps TWO shading groups (multi-material preserved)",
              len(cube_sgs) >= 2, cube_sgs)
        a_sgs = set(cmds.listConnections(mat_a[0], type="shadingEngine") or [])
        face_members = [m for sg in a_sgs for m in (cmds.sets(sg, query=True) or [])
                        if ".f[" in m]
        check("rebuilt matA assigned per-FACE (not whole object)",
              bool(face_members), face_members)

    # e2e_gone: named warning, no rebuilt network
    check("missing-texture material warns BY NAME",
          any("e2e_gone" in m and "stays untextured" in m for m in records),
          [m for m in records if "e2e_gone" in m])
    check("no ghost network for the file-less material",
          not [n for n in cmds.ls(type="standardSurface") or []
               if n.startswith("e2e_gone")])

    # temp hygiene: conversion scratch gone; only the promoted cache remains
    leaked = scratch_files() - pre_scratch
    check("conversion scratch cleaned up", not leaked, sorted(leaked))

    # cache: an identical second import must skip the Blender launch
    cmds.file(new=True, force=True)
    records.clear()
    t0 = time.time()
    eng.import_scene(blend)
    second_duration = time.time() - t0
    check("second import hits the conversion cache",
          any("cache hit" in m for m in records),
          f"first={first_duration:.1f}s second={second_duration:.1f}s")
    check("cache hit is dramatically faster",
          second_duration < max(first_duration * 0.5, 5.0),
          f"first={first_duration:.1f}s second={second_duration:.1f}s")

    # ---- via="usd" route: the SAME .blend through the USD intermediate ------
    # A/B against the FBX legs above: LINKED textures must arrive natively
    # (UsdPreviewSurface import), with no manifest rebuild involved. The
    # UNLINKED/packed images (norm_a / ms_a) are manifest-only transport by
    # design — the USD exporter carries the actual node network, so they are
    # NOT expected here (that asymmetry is the documented route trade-off).
    def descendant_sgs(prefix, nodes):
        """Shading engines bound anywhere under *prefix* (any nesting depth)."""
        tfs = find(prefix, nodes)
        if not tfs:
            return set()
        shapes = cmds.listRelatives(
            tfs[0], allDescendents=True, fullPath=True, type="mesh"
        ) or []
        return {sg for shape in shapes
                for sg in (cmds.listConnections(shape, type="shadingEngine") or [])}

    def sg_history_file(prefix, nodes, tex_suffix):
        """True when *prefix*'s shading network reads a file ending *tex_suffix*."""
        for sg in descendant_sgs(prefix, nodes):
            for node in cmds.listHistory(sg) or []:
                if cmds.nodeType(node) == "file":
                    path = cmds.getAttr(f"{node}.fileTextureName") or ""
                    if path.replace("\\", "/").endswith(tex_suffix):
                        return True
        return False

    cmds.file(new=True, force=True)
    records.clear()
    imported_usd = eng.import_scene(blend, via="usd", use_cache=False)
    check("USD route: all four objects imported",
          all(find(p, imported_usd) for p in
              ("e2e_cube", "e2e_sphere", "e2e_cone", "e2e_missing")),
          imported_usd)
    check("USD route: shared Base_Color arrives NATIVELY (no manifest rebuild)",
          sg_history_file("e2e_sphere", imported_usd, "e2e_shared_Base_Color.png")
          and not any("Rebuilt material" in m for m in records))
    check("USD route: matA Base_Color arrives natively",
          sg_history_file("e2e_cube", imported_usd, "e2e_matA_Base_Color.png"))
    cube_u_sgs = descendant_sgs("e2e_cube", imported_usd)
    check("USD route: two-material cube keeps both bindings (GeomSubsets)",
          len(cube_u_sgs) >= 2, cube_u_sgs)

    ok = all(line.startswith("OK") for line in lines)
except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())
finally:
    shutil.rmtree(work, ignore_errors=True)
    for stale in glob.glob(cache_glob) + glob.glob(cache_glob + ".manifest.json"):
        try:
            os.remove(stale)
        except OSError:
            pass

for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
sys.stdout.flush()
os._exit(0 if ok else 1)
