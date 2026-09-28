# !/usr/bin/python
# coding=utf-8
"""Live check: an articulated rig on a real prop, through the real export.

MANUAL, GATED: needs a Maya license (run under ``mayapy``) and a production
scene holding a prop of rigid parts. Deliberately NOT named ``test_*`` so
``run_tests.py`` never picks it up -- the synthetic cover is
``test_articulated_rig.py``; this runs the whole chain on a real asset:

  A. the analysis proposes the joints (printed for the record);
  B. the build moves no part at rest;
  C. Maya poses the joints exactly as ``ptk.ArticulationModel`` does -- the
     numbers Unity and the WebXR runtime play;
  D. a grab lands the head where it is sent;
  E. the export (records published in the bracket, FBX written baked, the
     rig-helper sweep, FBX2glTF) ships every joint and every grabbed part and
     none of the controls, and ``extras.articulation_web`` binds them all;
  F. the teardown puts every part back exactly.

The GLB is left in a detached scratch directory and its path printed as
``LIVE_GLB=<path>``: ``pythontk/test/test_articulated_rig_web.py`` loads it in
the real page when ``ARTICULATED_RIG_LIVE_GLB`` names it (the grab there).

SAFETY. The scene is opened from its real path, then the IN-MEMORY scene is
immediately renamed to a scratch guard path, so no save -- ours, a plugin's, a
crash handler's -- can reach the source. Nothing here saves. The scene path
comes from the environment, never from source, so no client path is committed.

Run:
    $env:ARTICULATED_RIG_LIVE_SCENE = "<a scene with a prop of rigid parts>"
    $env:ARTICULATED_RIG_LIVE_NODE = "<the prop's group>"   # optional
    & "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" mayatk\\test\\articulated_rig_live_check.py

Exit code 0 only when every verdict passes.
"""

import json
import os
import random

import maya.standalone

maya.standalone.initialize(name="python")

import maya.api.OpenMaya as om  # noqa: E402
import maya.cmds as cmds  # noqa: E402
import maya.mel as mel  # noqa: E402

import pythontk as ptk  # noqa: E402

from mayatk.env_utils.fbx_utils import FbxUtils  # noqa: E402
from mayatk.node_utils.data_nodes import DataNodes  # noqa: E402
from mayatk.rig_utils.articulated_rig import ArticulatedRig  # noqa: E402

TOLERANCE = 1e-4  # scene units; the build and the model are exact to ~1e-6


def world(node):
    return om.MMatrix(cmds.getAttr(f"{node}.worldMatrix[0]"))


def uuid_of(node):
    return cmds.ls(node, uuid=True)[0]


def by_uuid(uuid):
    return cmds.ls(uuid, long=True)[0]


def mdiff(a, b):
    return max(abs(a[i] - b[i]) for i in range(16))


def verdict(name, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def prop_node():
    named = os.environ.get("ARTICULATED_RIG_LIVE_NODE")
    if named:
        return named
    tops = [
        n
        for n in cmds.ls(assemblies=True, long=True)
        if cmds.listRelatives(n, allDescendents=True, type="mesh")
    ]
    if len(tops) != 1:
        raise SystemExit(
            f"Name the prop with ARTICULATED_RIG_LIVE_NODE; top groups: {tops}"
        )
    return tops[0]


def main():
    scene = os.environ.get("ARTICULATED_RIG_LIVE_SCENE")
    if not scene or not os.path.isfile(scene):
        raise SystemExit("Set ARTICULATED_RIG_LIVE_SCENE to a scene file.")
    scratch = ptk.TempArtifacts("articulated_rig_live", policy="detached").dir_path()
    cmds.file(scene, open=True, force=True, prompt=False, ignoreVersion=True)
    cmds.file(rename=os.path.join(scratch, "_articulated_rig_live_GUARD.ma"))
    print("opened; the in-memory scene now points at a scratch guard path\n")

    ok = True
    node = prop_node()
    plan = ArticulatedRig.analyze(node)
    print("A. proposal:")
    for joint in plan["joints"]:
        part = plan["links"][joint["link"]][0].split("|")[-1]
        pos = ", ".join(f"{v:.2f}" for v in joint["position"])
        print(
            f"   {part:<12} {joint['type']:<10} ({pos})  limits {joint['limits']}  -- {joint['reason']}"
        )
    for part in plan["unreached"]:
        print(f"   UNREACHED: {part}")
    # By uuid: the build parents every moving part under its joint, which
    # changes its path.
    parts = [uuid_of(n) for link in plan["links"] for n in link]
    rest = {p: world(by_uuid(p)) for p in parts}

    rig = ArticulatedRig.create(plan["links"], plan["joints"])
    drift = max(mdiff(rest[p], world(by_uuid(p))) for p in parts)
    ok &= verdict(
        "B. build moves nothing", drift < TOLERANCE, f"worst part drift {drift:.2e}"
    )

    model = rig.model()
    group = world(rig.group)
    rng = random.Random(1)
    worst = 0.0
    for _ in range(12):
        state = []
        for slot in range(len(model.channels)):
            lo, hi = model.limits(slot)
            state.append(
                rng.uniform(-45 if lo is None else lo, 45 if hi is None else hi)
            )
        rig.set_state(state, key=False)
        for index, (p, q) in enumerate(model.world(state)):
            xf = om.MTransformationMatrix(om.MQuaternion(*q).asMatrix())
            xf.setTranslation(om.MVector(*p), om.MSpace.kTransform)
            got = world(rig.joint(rig.joint_ids()[index]))
            worst = max(worst, mdiff(xf.asMatrix() * group, got))
    ok &= verdict(
        "C. Maya == ArticulationModel",
        worst < TOLERANCE,
        f"worst joint difference {worst:.2e}",
    )

    rig.set_state(model.rest_state(), key=False)
    head = by_uuid(parts[-1])
    centre = cmds.exactWorldBoundingBox(head, calculateExactly=True)
    centre = [(centre[i] + centre[i + 3]) / 2 for i in range(3)]
    size = max(cmds.exactWorldBoundingBox(node)[3:]) - min(
        cmds.exactWorldBoundingBox(node)[:3]
    )
    target = [centre[0] - 0.08 * size, centre[1] + 0.06 * size, centre[2] + 0.05 * size]
    left = rig.pose_to(head, target, point=centre, key=False)
    ok &= verdict(
        "D. a grab lands the head",
        left < 0.01 * size,
        f"{left:.4f} left of {0.08 * size:.2f}",
    )

    # A clip for the export: three keys on every control.
    for frame in (1, 12, 24):
        cmds.currentTime(frame)
        state = []
        for slot in range(len(model.channels)):
            lo, hi = model.limits(slot)
            state.append(
                rng.uniform(
                    -20 if lo is None else 0.5 * lo, 20 if hi is None else 0.5 * hi
                )
            )
        rig.set_state(state, key=True)
    cmds.playbackOptions(
        minTime=1, maxTime=24, animationStartTime=1, animationEndTime=24
    )

    fbx = os.path.join(scratch, "articulated_live.fbx")
    glb = os.path.join(scratch, "articulated_live.glb")
    FbxUtils.reset_export()
    mel.eval("FBXExportBakeComplexAnimation -v true")
    FbxUtils.set_bake_range_from_scene()
    with FbxUtils.export_prepared(FbxUtils.export_context()):
        selection = [cmds.ls(node, long=True)[0]] + DataNodes.get_export_nodes()
        cmds.select(selection, replace=True)
        cmds.file(
            fbx, force=True, options="v=0;", type="FBX export", exportSelected=True
        )
        report = FbxUtils.drop_rig_apparatus(
            fbx, cmds.ls(selection=True, long=True) or []
        )
    print(
        f"\n   rig-helper sweep: {json.dumps(report, default=str)[:400] if report else report}"
    )
    ptk.MeshConvert.fbx_to_glb(fbx, glb)
    with ptk.MeshConvert.open_glb(glb) as edit:
        names = [n.get("name", "") for n in edit.gltf.get("nodes") or []]
        manifest = (edit.gltf.get("extras") or {}).get(
            ptk.MeshConvert.ARTICULATION_WEB_KEY
        )
    record = rig.record()
    joints = [j["name"] for j in record["joints"]]
    grabbed = [g["node"] for g in record["grab"]]
    affix = ptk.NamingConvention.affix("control")
    controls = [n for n in names if n.endswith(affix)]
    ok &= verdict(
        "E. the GLB ships the skeleton, not the controls",
        all(j in names for j in joints)
        and all(g in names for g in grabbed)
        and not controls,
        f"{sum(j in names for j in joints)}/{len(joints)} joints, "
        f"{sum(g in names for g in grabbed)}/{len(grabbed)} parts, {len(controls)} controls",
    )
    bound = manifest["rigs"][0] if manifest and manifest.get("rigs") else {}
    ok &= verdict(
        "E. extras.articulation_web binds them",
        len(bound.get("joints", [])) == len(joints)
        and len(bound.get("grab", [])) == len(grabbed),
        f"{len(bound.get('joints', []))} joints, {len(bound.get('grab', []))} grab parts bound",
    )

    cmds.currentTime(1)
    for curve in cmds.ls(type="animCurve") or []:
        cmds.delete(curve)
    rig.teardown()
    drift = max(mdiff(rest[p], world(by_uuid(p))) for p in parts)
    ok &= verdict(
        "F. teardown restores every part", drift < TOLERANCE, f"worst drift {drift:.2e}"
    )
    print(f"\nLIVE_GLB={glb}")
    return 0 if ok else 1


if __name__ == "__main__":
    import traceback

    code = 1
    try:
        code = main()
    except BaseException:  # noqa: BLE001 -- the hard exit below would swallow it unprinted
        traceback.print_exc()
    finally:
        maya.standalone.uninitialize()
    ptk.ProcessExit.hard_exit(code)
