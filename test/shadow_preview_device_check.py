# !/usr/bin/python
# coding=utf-8
"""The horizon preview on BOTH Viewport 2.0 devices, against the reference.

For each device this launches a **fresh** GUI Maya (never an existing
session) with ``MAYA_VP2_DEVICE_OVERRIDE`` set for that process alone -- the
user's ``vp2RenderingEngine`` preference is never touched -- builds a
table-shaped horizon rig, attaches the preview, checks that the effect
compiled and its uniforms track the light, playblasts the plane from straight
above, and compares every pixel against ``HorizonMap.alpha`` decoding the very
PNG the rig baked. Then it detaches and checks the real material came back and
the export record never changed.

Its non-``test_`` name keeps it out of ``run_tests.py``, whose single launched
Maya cannot switch device mid-run; ``test_shadow_preview.py`` covers the
device-free half headlessly. Run it after touching
``pythontk/geo_utils/shadow_horizon.glsl``, ``shadow_preview.py`` or the
rig's material code::

    python mayatk/test/shadow_preview_device_check.py            # both devices
    python mayatk/test/shadow_preview_device_check.py dx11       # one
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import pythontk as ptk  # noqa: E402
from mayatk.rig_utils.shadow_preview import ShadowPreview  # noqa: E402

DEVICES = {
    "dx11": "VirtualDeviceDx11",
    "glcore": "VirtualDeviceGLCoreProfile",
}
TEMP = os.path.join(HERE, "temp_tests")
#: Playblast size and the ortho camera's width in scene units.
SHOT = 256
ORTHO = 9.0
LIGHT_A = (6.0, 5.0, 1.5)
LIGHT_B = (-2.0, 4.0, 5.5)
#: Tile height; ``--tile-h N`` overrides it (the map is 8 tile rows tall, so
#: 64 makes a 512-pixel texture and 32 a 256-pixel one).
TILE_H = 64

IN_MAYA = r"""
import json, os, sys
sys.path[:0] = [r"__REPO__", r"__PTK__"]
import maya.cmds as cmds
import numpy as np
from mayatk.rig_utils.shadow_rig import ShadowRig
from mayatk.rig_utils.shadow_preview import ShadowPreview

out = {"device_info": [str(l) for l in cmds.ogs(q=True, deviceInformation=True)]}
out["device"] = ShadowPreview.device()
out["language"], out["refusal"] = ShadowPreview.language()

cmds.file(new=True, force=True)
cmds.workspace(r"__PROJ__", openWorkspace=True)
cmds.colorManagementPrefs(edit=True, cmEnabled=False)

# A table: a slab on four legs -- two layers in the map, and legs that share
# a bin from many texels (the grounded second-run path).
slab = cmds.polyCube(name="Slab", width=2.0, height=0.2, depth=1.2)[0]
cmds.setAttr(slab + ".translate", 1.0, 1.1, 0.5, type="double3")
legs = []
for i, (dx, dz) in enumerate(((-0.85, -0.45), (0.85, -0.45), (-0.85, 0.45), (0.85, 0.45))):
    leg = cmds.polyCube(name=f"Leg{i}", width=0.14, height=1.0, depth=0.14)[0]
    cmds.setAttr(leg + ".translate", 1.0 + dx, 0.5, 0.5 + dz, type="double3")
    legs.append(leg)
rig = ShadowRig.create([slab] + legs, source_name="keyLight", rig_type="horizon",
                       light_pos=__LIGHT_A__, texture_res=128,
                       horizon_bins=32, horizon_size=(128, __TILE_H__))
plane = rig.shadow_plane
out["plane"] = plane
out["horizon_png"] = rig.horizon_path
out["contact"] = cmds.xform(rig.contact_locator, q=True, ws=True, t=True)
out["record_before"] = ShadowRig.export_record(plane)
sg_before = cmds.listConnections(cmds.listRelatives(plane, shapes=True, fullPath=True)[0], type="shadingEngine")
out["sg_before"] = sorted(set(sg_before or []))

try:
    fx = ShadowPreview.attach(plane)
except Exception as e:
    out["attach_error"] = f"{type(e).__name__}: {e}"
    fx = None
out["fx"] = fx
if fx:
    out["node_type"] = cmds.nodeType(fx)
    out["techniques"] = cmds.getAttr(fx + ".techniques") or []
    out["uniforms"] = sorted(a for a in (cmds.listAttr(fx) or []) if a.startswith("g") and a[1:2].isupper())
    cmds.refresh(force=True)
    out["source_uniform_A"] = list(cmds.getAttr(fx + ".gSource")[0])
    out["origin_uniform"] = list(cmds.getAttr(fx + ".gOrigin")[0])
    out["ground_uniform"] = cmds.getAttr(fx + ".gGround")
    out["constants"] = {n: cmds.getAttr(f"{fx}.{n}") for n in ("gBins", "gCols", "gLayers", "gTileW", "gTileH", "gRMin", "gRMax", "gMaxStretch")}
    out["record_attached"] = ShadowRig.export_record(plane)
    out["texture_node_attached"] = ShadowRig._plane_texture_node(plane)
    out["shading_attached"] = list(ShadowRig._plane_shading(plane))
    map_node = (cmds.listConnections(fx + ".gHorizonTex", source=True) or [""])[0]
    out["preview_texture"] = cmds.getAttr(map_node + ".fileTextureName") if map_node else ""

    # -- the shot: the plane alone, from straight above, over white ---------
    for node in [slab] + legs + [rig.light, rig.contact_locator]:
        cmds.hide(node)
    cam = cmds.camera(name="hzCheckCam", orthographic=True, orthographicWidth=__ORTHO__)[0]
    px, py, pz = cmds.xform(plane, q=True, ws=True, t=True)
    cmds.setAttr(cam + ".translate", px, 20.0, pz, type="double3")
    cmds.setAttr(cam + ".rotate", -90.0, 0.0, 0.0, type="double3")
    cmds.hide(cam)
    out["camera_centre"] = [px, pz]
    out["plane_corners"] = [cmds.xform(f"{plane}.vtx[{i}]", q=True, ws=True, t=True) for i in range(4)]
    out["plane_opacity"] = cmds.getAttr(f"{plane}.{ShadowRig.OPACITY_ATTR}")
    out["plane_intensity"] = cmds.getAttr(plane + ".shadowIntensity")
    # The shot's ALPHA channel is the signal: an offscreen playblast writes
    # the background at alpha 0 and a blended surface at its own alpha, so the
    # plane's pixels carry a * opacity * intensity exactly, whatever the
    # background looks like. The gradient is turned off only so the RGB is
    # readable when someone opens the PNG.
    cmds.displayPref(displayGradient=False)
    cmds.displayRGBColor("background", 1.0, 1.0, 1.0)
    # Deterministic blending: VP2's transparency algorithm decides whether a
    # blended surface lands as its alpha (Object Sorting) or as a per-pixel
    # coverage dither that only AVERAGES to it. The comparison is per pixel.
    cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 1)
    out["transparency_algorithm"] = cmds.getAttr("hardwareRenderingGlobals.transparencyAlgorithm")
    out["multisample"] = cmds.getAttr("hardwareRenderingGlobals.multiSampleEnable")
    panel = cmds.getPanel(withFocus=True)
    if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
        panel = [p for p in cmds.getPanel(type="modelPanel")][0]
    cmds.modelEditor(panel, edit=True, camera=cam, grid=False, displayAppearance="smoothShaded",
                     displayLights="default", displayTextures=True, headsUpDisplay=False,
                     nurbsCurves=False, locators=False, lights=False, cameras=False)
    cmds.refresh(force=True)
    shot = r"__SHOT__"
    cmds.playblast(frame=[1], format="image", compression="png", completeFilename=shot,
                   viewer=False, showOrnaments=False, offScreen=True, forceOverwrite=True,
                   width=__SHOT_SIZE__, height=__SHOT_SIZE__, percent=100, quality=100)
    out["shot"] = shot
    out["shot_exists"] = os.path.exists(shot)

    # -- liveness: the uniform follows the light with no Python in between ---
    cmds.setAttr(rig.light + ".translate", *__LIGHT_B__, type="double3")
    cmds.refresh(force=True)
    out["source_uniform_B"] = list(cmds.getAttr(fx + ".gSource")[0])
    out["light_B_world"] = cmds.xform(rig.light, q=True, ws=True, t=True)

    # -- detach: the real material is back, nothing of the preview remains --
    out["detached"] = ShadowPreview.detach(plane)
    sg_after = cmds.listConnections(cmds.listRelatives(plane, shapes=True, fullPath=True)[0], type="shadingEngine")
    out["sg_after"] = sorted(set(sg_after or []))
    out["leftovers"] = cmds.ls(f"*{ShadowPreview.INFIX}*") or []
    out["record_after"] = ShadowRig.export_record(plane)
    out["attrs_after"] = [a for a in (ShadowPreview.SHADER_ATTR, ShadowPreview.RESTORE_ATTR)
                          if cmds.attributeQuery(a, node=plane, exists=True)]

with open(r"__RESULT__", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, default=str)
print("DEVICE CHECK WROTE", r"__RESULT__")
"""


def _expected_alpha(res):
    """``(alpha, inside, scale)``: ``HorizonMap.alpha`` over the shot's pixel
    grid in the plane's frame, the mask of pixels the plane covers, and the
    plane's opacity x intensity the shader multiplies the alpha by."""
    rec = res["record_before"]
    hz = rec["horizon"]
    png = np.asarray(Image.open(res["horizon_png"]).convert("RGBA"))
    cx, cy, cz = res["contact"]
    hmap = ptk.HorizonMap.from_rgba(
        png,
        bins=hz["bins"],
        size=hz["tile"],
        r_min=hz["r_min"],
        r_max=hz["r_max"],
        ground=rec["ground"] - cy,
        max_stretch=hz["max_stretch"],
    )
    ccx, ccz = res["camera_centre"]
    n = SHOT
    idx = (np.arange(n) + 0.5) / n - 0.5
    xs = ccx + idx[None, :] * ORTHO
    zs = ccz + idx[:, None] * ORTHO
    xs, zs = np.broadcast_arrays(xs, zs)
    # The map's frame is the contact's (unrotated here): a translation.
    pts = np.column_stack([xs.ravel() - cx, np.zeros(n * n), zs.ravel() - cz])
    light = np.asarray(LIGHT_A, dtype=float) - np.array([cx, cy, cz])
    alpha = hmap.alpha(pts, light=light, source_size=rec["source_size"])
    # Only inside the plane's quad: the rest of the frame is the background.
    corners = np.asarray(res["plane_corners"], dtype=float)[:, [0, 2]]
    centre = corners.mean(axis=0)
    a_axis = corners[1] - corners[0]
    b_axis = corners[2] - corners[0]
    la, lb = np.linalg.norm(a_axis), np.linalg.norm(b_axis)
    a_axis, b_axis = a_axis / la, b_axis / lb
    rel = np.column_stack([xs.ravel(), zs.ravel()]) - centre
    inside = (np.abs(rel @ a_axis) <= 0.5 * la) & (np.abs(rel @ b_axis) <= 0.5 * lb)
    scale = float(res["plane_opacity"]) * float(res["plane_intensity"])
    return alpha.reshape(n, n), inside.reshape(n, n), scale


def run_device(device: str) -> bool:
    from mayatk.env_utils.maya_connection import MayaConnection

    os.makedirs(TEMP, exist_ok=True)
    project = os.path.join(TEMP, f"shadow_preview_{device}").replace("\\", "/")
    os.makedirs(os.path.join(project, "sourceimages"), exist_ok=True)
    result = os.path.join(project, "result.json").replace("\\", "/")
    shot = os.path.join(project, "shot.png").replace("\\", "/")
    if os.path.exists(result):
        os.remove(result)
    code = (
        IN_MAYA.replace("__REPO__", REPO)
        .replace("__PTK__", os.path.join(MONO, "pythontk"))
        .replace("__PROJ__", project)
        .replace("__RESULT__", result)
        .replace("__SHOT__", shot)
        .replace("__SHOT_SIZE__", str(SHOT))
        .replace("__ORTHO__", str(ORTHO))
        .replace("__LIGHT_A__", repr(LIGHT_A))
        .replace("__LIGHT_B__", repr(LIGHT_B))
        .replace("__TILE_H__", str(TILE_H))
    )

    os.environ["MAYA_VP2_DEVICE_OVERRIDE"] = DEVICES[device]
    print(f"\n=== {device}: launching a fresh Maya ({DEVICES[device]}) ===")
    conn = MayaConnection()
    conn.connect()
    try:
        _, editor = conn.execute_and_capture_editor_output(code, timeout=600)
    finally:
        conn.shutdown(force=True)
    if not os.path.exists(result):
        print("NO RESULT FILE. Script Editor said:\n", editor)
        return False
    with open(result, "r", encoding="utf-8") as fh:
        res = json.load(fh)

    checks = []

    def check(name, cond, detail=""):
        checks.append((bool(cond), name, detail))

    check(
        "device classified",
        res["device"] == device,
        f"{res['device']!r} from {res['device_info']}",
    )
    check(
        "language chosen",
        res["language"] == {"dx11": "hlsl", "glcore": "glsl"}[device],
        res["language"],
    )
    check(
        "attached",
        res.get("fx") and not res.get("attach_error"),
        res.get("attach_error", ""),
    )
    if res.get("fx"):
        check("effect compiled", res["techniques"] == ["Main"], str(res["techniques"]))
        for u in (
            "gOrigin",
            "gAxisA",
            "gAxisB",
            "gAxisUp",
            "gSource",
            "gGround",
            "gHorizonTex",
            "gBins",
            "gOpacity",
        ):
            check(f"uniform {u} exposed", u in res["uniforms"], "")
        origin = res["origin_uniform"]
        check(
            "gOrigin tracks the contact",
            max(abs(a - b) for a, b in zip(origin, res["contact"])) < 1e-4,
            f"{origin} vs {res['contact']}",
        )
        check(
            "gGround is the ground in the frame",
            abs(
                res["ground_uniform"]
                - (res["record_before"]["ground"] - res["contact"][1])
            )
            < 1e-4,
            str(res["ground_uniform"]),
        )
        check(
            "gSource tracks the light (A)",
            max(abs(a - b) for a, b in zip(res["source_uniform_A"], LIGHT_A)) < 1e-4,
            str(res["source_uniform_A"]),
        )
        check(
            "gSource tracks the light (B, moved)",
            max(
                abs(a - b)
                for a, b in zip(res["source_uniform_B"], res["light_B_world"])
            )
            < 1e-4,
            f"{res['source_uniform_B']} vs {res['light_B_world']}",
        )
        hz = res["record_before"]["horizon"]
        want = {
            "gBins": hz["bins"],
            "gCols": hz["layout"][0],
            "gLayers": hz["layers"],
            "gTileW": hz["tile"][0],
            "gTileH": hz["tile"][1],
            "gRMin": hz["r_min"],
            "gRMax": hz["r_max"],
            "gMaxStretch": hz["max_stretch"],
        }
        got = res.get("constants") or {}
        bad = {
            k: (got.get(k), v)
            for k, v in want.items()
            if abs(float(got.get(k, -1)) - float(v)) > 1e-4
        }
        check("layout constants read back as set", not bad, str(bad))
        check(
            "record unchanged while attached",
            res["record_attached"] == res["record_before"],
            ""
            if res["record_attached"] == res["record_before"]
            else json.dumps(res["record_attached"])[:200],
        )
        check(
            "silhouette file node resolves while attached",
            bool(res["texture_node_attached"]),
            str(res["texture_node_attached"]),
        )
        check(
            "opacity chain resolves while attached",
            all(res["shading_attached"]),
            str(res["shading_attached"]),
        )
        texture = str(res.get("preview_texture") or "")
        check(
            "the bound texture is the map's 16-bit promotion",
            texture.endswith(ShadowPreview.TEXTURE_SUFFIX) and os.path.exists(texture),
            texture,
        )
        check("detached", res["detached"], "")
        check(
            "real material restored",
            res["sg_after"] == res["sg_before"],
            f"{res['sg_after']} vs {res['sg_before']}",
        )
        check("no preview nodes left", not res["leftovers"], str(res["leftovers"]))
        check("no preview attrs left", not res["attrs_after"], str(res["attrs_after"]))
        check(
            "record unchanged after detach",
            res["record_after"] == res["record_before"],
            "",
        )

        # -- the pixels: the shot's alpha IS a * opacity * intensity ---------
        if res.get("shot_exists"):
            got = np.asarray(Image.open(res["shot"]).convert("RGBA"))[..., 3] / 255.0
            alpha, inside, scale = _expected_alpha(res)
            expected = alpha * scale
            got_in, exp_in, alpha_in = got[inside], expected[inside], alpha[inside]
            diff = np.abs(got_in - exp_in)
            n_in = int(inside.sum())
            in_shadow = int((alpha_in > 0.5).sum())
            umbra = got_in[alpha_in > 0.5].mean() / scale if in_shadow else float("nan")
            print(
                f"  pixels: {n_in} in plane, {in_shadow} in shadow (reference), "
                f"fade {scale:.3f}, mean|d| {diff.mean():.4f}, "
                f"p98 {np.percentile(diff, 98):.3f}, "
                f">0.05: {100.0 * (diff > 0.05).mean():.2f}%, umbra/fade {umbra:.3f}"
            )
            check("the plane covers pixels", n_in > 500, str(n_in))
            check("the fixture casts a shadow", in_shadow > 100, str(in_shadow))
            check("mean pixel error < 0.02", diff.mean() < 0.02, f"{diff.mean():.4f}")
            check(
                "< 6% of pixels off by > 0.05",
                (diff > 0.05).mean() < 0.06,
                f"{100.0 * (diff > 0.05).mean():.2f}%",
            )
            if in_shadow:
                check("the umbra is dark", umbra > 0.8, f"{umbra:.3f}")
            check(
                "nothing drawn outside the plane",
                got[~inside].max() < 0.05,
                f"{got[~inside].max():.3f}",
            )
        else:
            check("playblast written", False, "no shot")

    passed = sum(1 for ok, _, _ in checks if ok)
    for ok, name, detail in checks:
        print(
            f"  {'OK  ' if ok else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
        )
    verdict = "PASS" if passed == len(checks) else "FAIL"
    print(f"===RESULT {device}: {verdict}=== ({passed}/{len(checks)})")
    return verdict == "PASS"


def main(argv):
    global TILE_H
    if "--tile-h" in argv:
        i = argv.index("--tile-h")
        TILE_H = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]
    devices = argv or list(DEVICES)
    results = {d: run_device(d) for d in devices}
    print(
        "\n" + " ".join(f"{d}={'PASS' if ok else 'FAIL'}" for d, ok in results.items())
    )
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
