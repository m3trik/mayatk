# !/usr/bin/python
# coding=utf-8
"""LIVE round-trip check: a scene must survive being converted, and converted AGAIN.

MANUAL, GATED: needs a local Blender install AND a Maya license, and takes minutes.
Deliberately NOT named ``test_*`` so ``run_tests.py`` never picks it up -- the
ungated coverage lives in ``test_scene_import.py`` / ``test_skinning.py``; this
exercises the whole two-DCC pipeline end to end.

Run::

    python mayatk/test/scene_roundtrip_live_e2e.py                 # generated fixture
    python mayatk/test/scene_roundtrip_live_e2e.py --scene X.ma    # a real module
    python mayatk/test/scene_roundtrip_live_e2e.py --hops 4 --keep

ONE home, not two. A round trip is a single object -- ``.ma -> .blend -> .ma ->
.blend`` exercises both engines in one run -- so there is no blendertk twin to keep
in step, and none is owed by the parity sweep (the per-DIRECTION checks, which do
mirror, are the two ``scene_import_live_e2e.py`` files).

WHAT IT ASSERTS, and why it is not "lossless"
---------------------------------------------
The first hop is lossy BY DESIGN: rig apparatus is classified and dropped
(``_classify_rig_machinery``), constraints are baked to curves, control curves go.
On the production module that is 469 nurbsCurves and 217 parentConstraints, so
``hop1 != source`` and always will be. Asserting losslessness would be asserting a
falsehood, and a check that cannot pass gets disabled.

The property that DOES hold is a **fixed point**: whatever the first pass decides,
the second pass must decide the same. So the contract is

    census(hop1) == census(hop3)      # .blend -> .blend
    census(hop2) == census(hop4)      # .ma    -> .ma
    clock(hop_n) == clock(source)     # for every n

That single invariant is what four of the five 2026-09-20 defects violated while
211 unit tests passed, because those tests assert that each STEP RAN, never that
the RESULT SETTLED. Measured then, before the fixes -- per round trip the module
gained 7 joints, 7 transforms, a shadingEngine and ~620 ``FBXASC`` name tokens,
shifted its clock +1 frame, and lost 3 of its 7 skinClusters.

Each hop runs INSIDE the DCC that owns it (a fresh ``blender --background`` /
``mayapy``), which is the session-safety rule and the production shape. It is no
longer load-bearing: the bake renderers used to hand the child the parent's whole
``sys.path``, so driving the Blender half from a plain venv shadowed Blender's
stdlib with the host's and the bake died on ``ModuleNotFoundError: _sha512``.
They now compose ``ptk.HandoffBridge.import_roots`` with ``child_sys_path``, and
a venv-driven bake of the production module completes (verified 2026-09-20).
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )
    return bool(cond)


# ---------------------------------------------------------------------------
# Child scripts. Dependency-free at module scope (the target interpreter only
# has its own DCC's modules); the toolkit roots ride in as __ROOTS__.
# ---------------------------------------------------------------------------

_ROOTS = [os.path.join(MONO, p) for p in ("pythontk", "mayatk", "blendertk", "uitk")]
# At module scope, like the sibling live check: this file lives inside the repo it
# tests, so the roots are always resolvable and a discovery helper should not carry
# an import side effect.
for _root in _ROOTS:
    if _root not in sys.path:
        sys.path.insert(0, _root)

import pythontk as ptk  # noqa: E402 -- after the roots above, by construction

# ``__ROOTS__`` substitution, not ``str.format``: these scripts are full of dict
# literals, and the bridges' own templates spell their one substitution the same
# way (``__EXTRA_SYS_PATH__``).
_PREAMBLE = """
import json, os, sys
for _p in __ROOTS__:
    if _p not in sys.path:
        sys.path.insert(0, _p)
"""

#: ``.ma``/``.mb`` -> ``.blend``, in a headless Blender (blendertk owns this leg).
_HOP_TO_BLEND = (
    _PREAMBLE
    + """
import shutil, traceback
SRC, DST = sys.argv[sys.argv.index("--") + 1:][:2]
from blendertk.env_utils.maya_bridge._scene_import import MayaSceneImport
try:
    got = MayaSceneImport(log_level="INFO").bake_scene(SRC, via=__VIA__, use_cache=False)
    shutil.copy2(got, DST)
    print("===HOP OK===", flush=True)
except Exception:
    traceback.print_exc()
    print("===HOP FAILED===", flush=True)
"""
)

#: ``.blend`` -> ``.ma``, in a headless mayapy (mayatk owns this leg).
_HOP_TO_MA = (
    _PREAMBLE
    + """
import shutil, traceback
import maya.standalone
maya.standalone.initialize(name="python")
SRC, DST = sys.argv[1], sys.argv[2]
from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport
try:
    got = BlenderSceneImport(log_level="INFO").bake_scene(SRC, via=__VIA__, use_cache=False)
    shutil.copy2(got, DST)
    print("===HOP OK===", flush=True)
except Exception:
    traceback.print_exc()
    print("===HOP FAILED===", flush=True)
# Not os._exit: on Windows it still runs every DLL's process-detach, where Maya
# faults in its own destructors -- each green hop then filed a crash dump and an
# untitled[Recovered-...].ma in %TEMP% (37 of them over two days of runs).
import pythontk as ptk
ptk.ProcessExit.hard_exit(0)
"""
)

#: The census both formats answer in the SAME shape, so hop1 compares to hop3.
#: Counts and totals only -- names drift legitimately (Maya uniquifies, Blender
#: suffixes) and a name diff would swamp the signal the check is after.
_CENSUS_BLEND = (
    _PREAMBLE
    + """
import bpy
SRC, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.open_mainfile(filepath=SRC)
bpy.context.view_layer.update()
d, sc = bpy.data, bpy.context.scene
from blendertk.anim_utils._anim_utils import AnimUtils

# Each object's OWN curves, through the engine's slot-aware reader. An assigned
# action is not animation: a 2026-09-21 USD run kept 83 objects' actions while
# emptying them, and a count of assigned actions read 109 == 109 across a pass
# that lost every show/hide.
curves = {o.name: AnimUtils.get_fcurves([o]) for o in d.objects}
by = {}
for o in d.objects:
    by[o.type] = by.get(o.type, 0) + 1
out = {
    "objects": len(d.objects),
    "mesh_data": len(d.meshes),
    "materials": len(d.materials),
    "images": len(d.images),
    "armatures": len(d.armatures),
    "bones": sum(len(a.bones) for a in d.armatures),
    "by_type": by,
    "deformed_meshes": sum(
        1 for o in d.objects if any(m.type == "ARMATURE" for m in o.modifiers)
    ),
    "animated_objects": sum(1 for fcs in curves.values() if fcs),
    # Show/hide on its own line: it is the channel every carrier drops.
    "visibility_animated": sum(
        1
        for fcs in curves.values()
        if any(fc.data_path in ("hide_viewport", "hide_render") for fc in fcs)
    ),
    "verts": sum(len(o.data.vertices) for o in d.objects if o.type == "MESH"),
    "faces": sum(len(o.data.polygons) for o in d.objects if o.type == "MESH"),
    "clock": [sc.frame_start, sc.frame_end, sc.render.fps],
    "mangled_names": sum(1 for o in d.objects if "FBXASC" in o.name),
}
json.dump(out, open(OUT, "w"), indent=1)
print("===CENSUS OK===", flush=True)
"""
)

_CENSUS_MA = (
    _PREAMBLE
    + """
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from mayatk.env_utils.blender_bridge._scene_import import (
    BlenderSceneImport as _BSI,
    _IMPORT_UTILITY_NODE_TYPES as _UTILITY_TYPES,
)
_is_wired = _BSI._is_wired
SRC, OUT = sys.argv[1], sys.argv[2]
cmds.file(SRC, open=True, force=True, ignoreVersion=True, prompt=False)
meshes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
verts = faces = 0
for m in meshes:
    try:
        verts += cmds.polyEvaluate(m, vertex=True) or 0
        faces += cmds.polyEvaluate(m, face=True) or 0
    except Exception:
        pass
by = {}
for t in ("transform", "joint", "locator", "camera", "mesh"):
    by[t] = len(cmds.ls(type=t, long=True) or [])
out = {
    "objects": by["transform"],
    "mesh_data": len(meshes),
    "materials": len(cmds.ls(type="shadingEngine") or []),
    "images": len(cmds.ls(type="file") or []),
    "bones": by["joint"],
    "by_type": by,
    "deformed_meshes": len(cmds.ls(type="skinCluster") or []),
    "anim_curves": len(cmds.ls(type="animCurve") or []),
    "visibility_curves": len(
        [
            c
            for c in (cmds.ls(type="animCurve") or [])
            if any(
                p.endswith((".visibility", ".v"))
                for p in cmds.listConnections(c, source=False, plugs=True) or []
            )
        ]
    ),
    "verts": verts,
    "faces": faces,
    "clock": [
        cmds.playbackOptions(q=True, ast=True),
        cmds.playbackOptions(q=True, aet=True),
        cmds.currentUnit(q=True, time=True),
    ],
    # By LEAF: a mangled PARENT would otherwise make every descendant's long path
    # count too, and the detail line has to name a real number.
    "mangled_names": len(
        [n for n in (cmds.ls(long=True) or []) if "FBXASC" in n.rsplit("|", 1)[-1]]
    ),
    # Through the ENGINE's own predicate, not a second copy of it: the harness must
    # not be able to disagree with production about what "drives nothing" means.
    "orphan_utility_nodes": len(
        [n for n in (cmds.ls(type=_UTILITY_TYPES) or []) if not _is_wired(n)]
    ),
}
json.dump(out, open(OUT, "w"), indent=1)
print("===CENSUS OK===", flush=True)
# Not os._exit: on Windows it still runs every DLL's process-detach, where Maya
# faults in its own destructors -- each green hop then filed a crash dump and an
# untitled[Recovered-...].ma in %TEMP% (37 of them over two days of runs).
import pythontk as ptk
ptk.ProcessExit.hard_exit(0)
"""
)

#: A .ma carrying the traps a round trip actually trips over. One skinned mesh
#: per side, and the two meshes SHARE A SHORT NAME under different parents --
#: legal in Maya, and the shape that seeded the 2026-09-20 cascade (the flatten
#: names its wrapper after the short name, ``cmds.group`` uniquified the clash,
#: and the twin lost its skin two hops later).
_FIXTURE = (
    _PREAMBLE
    + """
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
OUT = sys.argv[1]
cmds.file(new=True, force=True)
cmds.currentUnit(linear="centimeter", time="ntsc")
cmds.playbackOptions(animationStartTime=0, animationEndTime=40, minTime=1, maxTime=30)
shader = cmds.shadingNode("lambert", asShader=True, name="rt_mat")
sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="rt_matSG")
cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
for side in ("L", "R"):
    grp = cmds.group(empty=True, name="rt_" + side + "_GRP")
    cmds.setKeyframe(grp, attribute="translateX", t=1, v=0)
    cmds.setKeyframe(grp, attribute="translateX", t=30, v=25)
    tube = cmds.polyCylinder(name="rt_mesh", height=10, sx=10, sy=6, r=1,
                             axis=(1, 0, 0))[0]
    cmds.makeIdentity(tube, apply=True, t=True, r=True, s=True)
    tube = cmds.ls(cmds.parent(tube, grp, relative=True)[0], long=True)[0]
    cmds.sets(tube, edit=True, forceElement=sg)
    cmds.select(clear=True)
    chain = [cmds.joint(p=p, name="rt_" + side + "_jnt%d" % (i + 1))
             for i, p in enumerate([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])]
    cmds.parent(chain[0], grp, relative=True)
    cmds.setKeyframe(chain[1], attribute="rotateZ", t=1, v=0)
    cmds.setKeyframe(chain[1], attribute="rotateZ", t=30, v=35)
    # A SECOND joint root, so the skin spans two -- what the flatten exists for.
    plug = cmds.group(empty=True, name="rt_" + side + "_PLUG")
    cmds.select(clear=True)
    anchor = cmds.joint(p=(9, 0, 0), name="rt_" + side + "_anchor")
    cmds.parent(anchor, plug)
    cmds.skinCluster(cmds.ls(chain, long=True) + [anchor], tube,
                     toSelectedBones=True, maximumInfluences=2)
cmds.file(rename=OUT)
cmds.file(save=True, type="mayaAscii", force=True)
print("===FIXTURE OK===", flush=True)
# Not os._exit: on Windows it still runs every DLL's process-detach, where Maya
# faults in its own destructors -- each green hop then filed a crash dump and an
# untitled[Recovered-...].ma in %TEMP% (37 of them over two days of runs).
import pythontk as ptk
ptk.ProcessExit.hard_exit(0)
"""
)


def _write(text, work, name, via="fbx"):
    """Render a child script into *work*, substituting the tokens it declares.

    ``__ROOTS__`` and ``__VIA__``, by textual replacement rather than ``format``:
    these scripts are full of ``{}`` dict literals (see the module note above).
    """
    path = os.path.join(work, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("__ROOTS__", repr(_ROOTS)).replace("__VIA__", repr(via)))
    return path


#: Executable discovery, through the primitive the bridges themselves use
#: (``$ENV -> find_app -> install-dir scan``, newest install wins) rather than a
#: fourth hand-rolled copy of it. ``MAYATK_MAYAPY`` is honoured first so this
#: agrees with ``run_tests.py`` about which Maya the suite means.
_APPS = {
    "blender": dict(
        env_vars=("BLENDER_EXE",),
        app_names=("blender",),
        scan_globs=(r"{program_files}\Blender Foundation\Blender*lender.exe",),
    ),
    "mayapy": dict(
        env_vars=("MAYATK_MAYAPY", "MAYAPY_EXE"),
        location_env_vars=(("MAYA_LOCATION", ("bin", "mayapy.exe")),),
        app_names=("mayapy",),
        scan_globs=(r"{program_files}\Autodesk\Maya*in\mayapy.exe",),
    ),
}


def _find(app):
    """The Blender / mayapy executable, env override first."""
    return ptk.AppLauncher.resolve_app_path(**_APPS[app])


def _run(app, script, args, marker, log, timeout):
    """Run *script* under *app*; True when *marker* reached its stdout."""
    exe = _find(app)
    if not exe:
        return False, f"{app} not found"
    argv = (
        [exe, "--background", "--factory-startup", "--python", script, "--"] + args
        if app == "blender"
        else [exe, script] + args
    )
    t0 = time.time()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr or "")
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(out)
    return marker in out, f"{time.time() - t0:.0f}s, log {os.path.basename(log)}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scene", help="A .ma/.mb to round-trip (default: generated)")
    parser.add_argument("--hops", type=int, default=4)
    parser.add_argument(
        "--via",
        choices=("fbx", "usd"),
        default="fbx",
        help="The carrier BOTH legs use. The routes are independent engines with "
        "their own manifest sections, so a fixed point on one says nothing about "
        "the other.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the artifacts")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Per hop")
    opts = parser.parse_args(argv)

    # Through the primitive, not mkdtemp: a --keep run, or one whose DCC dies
    # mid-hop, leaves its folder behind, and only a prefix namespace gets swept.
    scratch = ptk.TempArtifacts("mtk_roundtrip", policy="detached")
    work = scratch.dir_path()
    ok = True
    try:
        scripts = {
            "to_blend": _write(_HOP_TO_BLEND, work, "_hop_to_blend.py", opts.via),
            "to_ma": _write(_HOP_TO_MA, work, "_hop_to_ma.py", opts.via),
            "census_blend": _write(_CENSUS_BLEND, work, "_census_blend.py"),
            "census_ma": _write(_CENSUS_MA, work, "_census_ma.py"),
            "fixture": _write(_FIXTURE, work, "_fixture.py"),
        }

        source = opts.scene
        if not source:
            source = os.path.join(work, "rt_fixture.ma")
            good, detail = _run(
                "mayapy",
                scripts["fixture"],
                [source],
                "===FIXTURE OK===",
                os.path.join(work, "fixture.log"),
                opts.timeout,
            )
            ok &= check("fixture scene built", good and os.path.isfile(source), detail)
            if not ok:
                return ok
        source = os.path.abspath(source)

        def census(path, tag):
            out = os.path.join(work, tag + ".json")
            blend = path.lower().endswith(".blend")
            good, detail = _run(
                "blender" if blend else "mayapy",
                scripts["census_blend"] if blend else scripts["census_ma"],
                [path, out],
                "===CENSUS OK===",
                os.path.join(work, tag + ".census.log"),
                opts.timeout,
            )
            if not (good and os.path.isfile(out)):
                check(f"census of {tag}", False, detail)
                return None
            with open(out, encoding="utf-8") as fh:
                return json.load(fh)

        src_census = census(source, "source")
        censuses, current = {}, source
        for hop in range(1, opts.hops + 1):
            to_blend = current.lower().endswith((".ma", ".mb"))
            dst = os.path.join(work, f"hop{hop}" + (".blend" if to_blend else ".ma"))
            good, detail = _run(
                "blender" if to_blend else "mayapy",
                scripts["to_blend"] if to_blend else scripts["to_ma"],
                [current, dst],
                "===HOP OK===",
                os.path.join(work, f"hop{hop}.log"),
                opts.timeout,
            )
            if not check(f"hop {hop}: {os.path.basename(dst)} produced", good, detail):
                return False
            censuses[hop] = census(dst, f"hop{hop}")
            ok &= check(f"hop {hop}: census read", censuses[hop] is not None)
            current = dst

        # --- the contract ----------------------------------------------------
        for hop, got in censuses.items():
            if not got:
                continue
            ok &= check(
                f"hop {hop}: no FBXASC-escaped names",
                got.get("mangled_names", 0) == 0,
                f"{got.get('mangled_names')} mangled",
            )
            if "orphan_utility_nodes" in got:  # a .ma census only -- never a free OK
                ok &= check(
                    f"hop {hop}: no orphaned utility nodes",
                    got["orphan_utility_nodes"] == 0,
                    f"{got['orphan_utility_nodes']} orphaned",
                )
            if src_census:
                ok &= check(
                    f"hop {hop}: the scene clock is the source's",
                    got["clock"][:2] == src_census["clock"][:2],
                    f"{got['clock'][:2]} vs source {src_census['clock'][:2]}",
                )
        # THE invariant: the second pass decides what the first decided.
        for a, b in ((1, 3), (2, 4)):
            if a in censuses and b in censuses and censuses[a] and censuses[b]:
                drift = {
                    k: (censuses[a][k], censuses[b][k])
                    for k in censuses[a]
                    if k != "clock" and censuses[a][k] != censuses[b].get(k)
                }
                ok &= check(
                    f"FIXED POINT: hop {b} matches hop {a} exactly",
                    not drift,
                    json.dumps(drift),
                )
    except Exception as e:  # noqa: BLE001 -- the report is the deliverable
        import traceback

        lines.append(f"FAIL setup: {e!r}")
        lines.append(traceback.format_exc())
        ok = False
    finally:
        # The carrier is half of what a result means: the routes are independent
        # engines with their own manifest sections, so a report that does not name
        # the one it measured is a report about nothing in particular.
        lines.append(f"     carrier: {opts.via}, {opts.hops} hop(s)")
        if opts.keep:
            lines.append(f"     artifacts kept in {work}")
        else:
            scratch.cleanup(force=True)
    return ok


if __name__ == "__main__":
    result = main()
    for line in lines:
        print(line)
    print(f"===RESULT: {'PASS' if result else 'FAIL'}===")
    sys.stdout.flush()
    sys.exit(0 if result else 1)
