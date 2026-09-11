# !/usr/bin/python
# coding=utf-8
"""Live check: does the export path preserve a real tube rig, and put it back?

MANUAL, GATED: needs a Maya license (run under ``mayapy``) and a production
scene containing tube rigs. Deliberately NOT named ``test_*`` so ``run_tests.py``
never picks it up -- the synthetic cover lives in
``test_tube_rig.py::TestTubeRigOpmShearContract``; this exercises the real
pipeline against a real asset.

WHY IT EXISTS. A tube rig's bind joints follow their tweak through a CONNECTED
``offsetParentMatrix``, and a connected OPM never reaches FBX -- the export folds
``TRS x OPM`` onto the plugs, which is TRS-representable only while the OPM is a
similarity. The stretch system makes it one that is not, so essentially every
posed tube rig routes into ``flatten_sheared_chains``: a world-fitted bake that
REPARENTS joints and neutralises OPM / segmentScaleCompensate / joint orients to
write TRS keys, then restores all of it. That restore is the thing to distrust,
and this measures both halves:

  A. after the flatten, do the bind joints still occupy the same WORLD matrices?
     (that product is what ships)
  B. after ``run_deferred_restores``, is the rig BACK -- parents, OPM connection
     AND source plug, segmentScaleCompensate, joint orients, worlds?

Baseline on the reference production assembly, 2026-09-10: 210 transforms
flattened with world-fitted keys at 4202 frames, world preservation 5.68e-14,
restore deviation exactly 0, wiring differences 0. A rewrite of the twist system
must reproduce those three numbers.

SAFETY. The scene is opened from its real path so references resolve, then the
IN-MEMORY scene is IMMEDIATELY renamed to a scratch guard path, so no save --
ours, a plugin's, a crash handler's -- can reach the source file. Nothing here
saves. The scene path comes from the environment, never from source, so no
client path is committed.

Run:
    $env:TUBE_RIG_LIVE_SCENE = "<path to a scene with tube rigs>"
    & "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" mayatk\\test\\tube_rig_export_live_check.py

Exit code 0 only when all three verdicts pass.
"""

import json
import logging
import os
import tempfile

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds  # noqa: E402

WORLD_TOLERANCE = 1e-3  # cm; the flatten is exact to ~1e-14 in practice


def open_guarded(path, scratch):
    """Open *path*, then make it unreachable by any subsequent save."""
    cmds.file(path, open=True, force=True, prompt=False, ignoreVersion=True)
    guard = os.path.join(scratch, "_tube_rig_live_check_GUARD.ma")
    cmds.file(rename=guard)
    print(f"opened, then renamed in-memory scene to: {guard}")
    print("  (the source scene is now unreachable by any save)\n")


def opm_driven_joints():
    """Joints whose offsetParentMatrix is CONNECTED -- what the export scan sees."""
    out = []
    for j in cmds.ls(type="joint", long=True) or []:
        try:
            if cmds.connectionInfo(f"{j}.offsetParentMatrix", isDestination=True):
                out.append(j)
        except RuntimeError:
            continue
    return out


def worlds(nodes, frames):
    out, restore = {}, cmds.currentTime(query=True)
    try:
        for t in frames:
            cmds.currentTime(t, edit=True)
            for n in nodes:
                if cmds.objExists(n):
                    out[(n, t)] = cmds.xform(
                        n, query=True, worldSpace=True, matrix=True
                    )
    finally:
        cmds.currentTime(restore, edit=True)
    return out


def rig_state(nodes):
    """Exactly what the flatten neutralises -- an incomplete restore shows here."""
    state = {}
    for n in nodes:
        if not cmds.objExists(n):
            state[n] = None
            continue
        state[n] = {
            "parent": (cmds.listRelatives(n, parent=True, fullPath=True) or [None])[0],
            "opm_connected": bool(
                cmds.connectionInfo(f"{n}.offsetParentMatrix", isDestination=True)
            ),
            "opm_source": (
                cmds.listConnections(
                    f"{n}.offsetParentMatrix",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or [None]
            )[0],
            "ssc": cmds.getAttr(f"{n}.segmentScaleCompensate"),
            "jo": [round(v, 9) for v in cmds.getAttr(f"{n}.jointOrient")[0]],
        }
    return state


def max_deviation(before, after):
    worst, where = 0.0, None
    for key, matrix in before.items():
        if key not in after:
            continue
        dev = max(abs(a - b) for a, b in zip(matrix, after[key]))
        if dev > worst:
            worst, where = dev, key
    return worst, where


def main():
    scene = os.environ.get("TUBE_RIG_LIVE_SCENE")
    if not scene or not os.path.exists(scene):
        print("Set TUBE_RIG_LIVE_SCENE to a scene containing tube rigs.")
        return 2
    scratch = os.environ.get("TUBE_RIG_LIVE_SCRATCH") or tempfile.gettempdir()

    open_guarded(scene, scratch)

    from mayatk.env_utils.scene_exporter.task_manager import TaskManager

    joints = opm_driven_joints()
    if not joints:
        print("No OPM-driven joints in this scene -- nothing for the flatten to do.")
        return 2
    roots = sorted({"|" + j.split("|")[1] for j in joints if j.count("|") > 1})
    print(f"OPM-driven joints: {len(joints)}   export roots: {len(roots)}")

    lo = cmds.playbackOptions(query=True, min=True)
    hi = cmds.playbackOptions(query=True, max=True)
    frames = sorted({lo, lo + 1, (lo + hi) / 2.0, hi - 1, hi})
    print(f"sampling frames: {frames}\n")

    # Construct properly. `TaskManager.__new__` (the shortcut some unit fixtures
    # use) skips __init__, so `_deferred_restores` never exists and the flatten
    # dies the moment it stages its restore -- which is the half being tested.
    tm = TaskManager(logging.getLogger("tube_rig_live_check"))
    tm.objects = roots
    tm._live_objects = lambda: tm.objects

    before_worlds = worlds(joints, frames)
    before_state = rig_state(joints)

    print("=== A. flatten_sheared_chains() ===")
    ok, messages = tm.flatten_sheared_chains()
    print(f"  ok={ok}")
    for line in (messages or [])[:4]:
        print(f"    {line}")

    flat_dev, flat_where = max_deviation(before_worlds, worlds(joints, frames))
    print(f"\n  world preservation: {flat_dev:.6g}")
    if flat_where:
        print(f"    worst: {flat_where[0].rsplit('|', 1)[-1]} @ frame {flat_where[1]}")

    print("\n=== B. run_deferred_restores() ===")
    tm.run_deferred_restores()
    rest_dev, rest_where = max_deviation(before_worlds, worlds(joints, frames))
    print(f"  world restoration: {rest_dev:.6g}")
    if rest_where:
        print(f"    worst: {rest_where[0].rsplit('|', 1)[-1]} @ frame {rest_where[1]}")

    after_state = rig_state(joints)
    diffs = []
    for node, before in before_state.items():
        after = after_state.get(node)
        if before is None or after is None:
            diffs.append((node, "node missing after restore"))
            continue
        for key in ("parent", "opm_connected", "opm_source", "ssc", "jo"):
            if after[key] != before[key]:
                diffs.append((node, f"{key}: {before[key]!r} -> {after[key]!r}"))
    print(f"  wiring restored: {len(diffs)} difference(s)")
    for node, diff in diffs[:10]:
        print(f"    {node.rsplit('|', 1)[-1]}: {diff}")

    exports_ok = flat_dev < WORLD_TOLERANCE
    worlds_ok = rest_dev < WORLD_TOLERANCE
    wiring_ok = not diffs
    print("\n=== VERDICT ===")
    print(
        f"  exports correctly (worlds survive flatten): "
        f"{'PASS' if exports_ok else 'FAIL'}  ({flat_dev:.3g})"
    )
    print(
        f"  no corruption (worlds restored)           : "
        f"{'PASS' if worlds_ok else 'FAIL'}  ({rest_dev:.3g})"
    )
    print(
        f"  no corruption (wiring restored)           : "
        f"{'PASS' if wiring_ok else 'FAIL'}  ({len(diffs)} diffs)"
    )

    report = os.path.join(scratch, "_tube_rig_live_check.json")
    with open(report, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "joints": len(joints),
                "flatten_ok": bool(ok),
                "world_dev_after_flatten": flat_dev,
                "world_dev_after_restore": rest_dev,
                "wiring_diffs": [[n, d] for n, d in diffs],
            },
            handle,
            indent=2,
        )
    print(f"\nwrote {report}")
    return 0 if (exports_ok and worlds_ok and wiring_ok) else 1


if __name__ == "__main__":
    code = main()
    from pythontk import ProcessExit

    ProcessExit.hard_exit(code)
