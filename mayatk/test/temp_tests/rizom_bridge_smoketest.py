"""Headless smoke test for the rizom_bridge presets.

Runs in two phases:
  1. Generate a tiny test FBX containing a poly mesh with default UVs (mayapy).
  2. For each preset in mayatk/uv_utils/rizom_bridge/scripts/, build the
     wrapper script with the bridge's own _construct_full_script logic and
     invoke ``Rizomuv_VS.exe -cfi <wrapper>``. Pass = exit 0 and the FBX
     still exists with a non-zero size after the run.

Run with mayapy because phase 1 needs ``maya.cmds``::

    & "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" `
      mayatk/mayatk/test/temp_tests/rizom_bridge_smoketest.py

This file lives under ``mayatk/test/temp_tests/`` which ``MayaUiHandler``
walks recursively when discovering slots, so all side-effecting work is
deferred into ``main()`` -- importing this module must be a no-op.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


TEST_DIR = Path(tempfile.gettempdir()) / "rzbridge_smoketest"
SOURCE_FBX = TEST_DIR / "source.fbx"
RIZOM_FALLBACK = Path(r"C:\Program Files\Rizom Lab\RizomUV 2020.1\Rizomuv_VS.exe")

PARAM_OVERRIDES = {
    # Push every numeric param off its default so the rendered Lua proves
    # the substitution path actually feeds user values through.
    "RECURSION_DEPTH": 3,
    "SCALING_MODE": 1,
    "LAYOUT_SCALING_MODE": 1,
    "ROTATE_STEP": 45,
    "ITERATIONS": 15,
    "PRE_ITERATIONS": 8,
    "MIX": 0.75,
    "ROOM_SPACE": 0.002,
    "MIN_ANGLE": 1e-4,
}


def _bootstrap():
    """Initialise maya.standalone and import bridge symbols. Idempotent."""
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

    import maya.standalone
    maya.standalone.initialize()

    here = Path(__file__).resolve()
    repo = here.parents[4]  # _scripts root
    for pkg in ("pythontk", "uitk", "mayatk"):
        p = repo / pkg
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))

    import maya.cmds as cmds
    from mayatk.uv_utils.rizom_bridge._rizom_bridge import (
        RizomUVBridge,
        _SCRIPT_DIR,
    )
    return cmds, RizomUVBridge, _SCRIPT_DIR


def build_test_fbx(cmds) -> Path:
    """Create a polyCube + polyTorus, export to FBX. Two shells = real packing work."""
    cmds.file(new=True, force=True)
    cube = cmds.polyCube(name="rzCube", w=1, h=1, d=1)[0]
    torus = cmds.polyTorus(name="rzTorus", r=1, sr=0.3, sx=12, sy=12)[0]
    cmds.move(2.5, 0, 0, torus)

    if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
        cmds.loadPlugin("fbxmaya")

    cmds.select([cube, torus], replace=True)
    cmds.file(
        SOURCE_FBX.as_posix(),
        exportSelected=True,
        type="FBX export",
        force=True,
    )
    if not SOURCE_FBX.exists():
        raise RuntimeError("FBX export failed")
    print(f"[setup] wrote {SOURCE_FBX} ({SOURCE_FBX.stat().st_size} bytes)")
    return SOURCE_FBX


def run_preset(preset: str, RizomUVBridge, script_dir: Path) -> tuple:
    """Build a wrapper for *preset* and feed it to RizomUV. Returns (ok, detail)."""
    bridge = RizomUVBridge()
    if not bridge.rizom_path and RIZOM_FALLBACK.exists():
        bridge.rizom_path = str(RIZOM_FALLBACK)
    if not bridge.rizom_path:
        return False, "RizomUV executable not found on this machine"

    work_fbx = TEST_DIR / f"{preset}.fbx"
    shutil.copyfile(SOURCE_FBX, work_fbx)
    bridge.export_path = work_fbx.as_posix()
    bridge._params = PARAM_OVERRIDES

    user_script = (script_dir / f"{preset}.lua").read_text(encoding="utf-8")
    full_script = bridge._construct_full_script(user_script)
    script_file = TEST_DIR / f"{preset}_wrapper.lua"
    script_file.write_text(full_script, encoding="utf-8")

    # Verify no placeholder leaked through (would cause Rizom to fail silently).
    import re

    leaks = re.findall(r"__([A-Z][A-Z0-9_]*)__(?!\")", full_script)
    leaks = [k for k in leaks if k not in {"UpdateUIObjFileName"}]  # Rizom-internal
    if leaks:
        return False, f"unsubstituted placeholders in rendered script: {sorted(set(leaks))}"

    pre_size = work_fbx.stat().st_size
    pre_mtime = work_fbx.stat().st_mtime

    t0 = time.time()
    proc = subprocess.run(
        [bridge.rizom_path, "-cfi", script_file.as_posix()],
        capture_output=True,
        text=True,
        timeout=180,
    )
    elapsed = time.time() - t0

    post_exists = work_fbx.exists()
    post_size = work_fbx.stat().st_size if post_exists else 0
    post_mtime = work_fbx.stat().st_mtime if post_exists else 0

    detail = (
        f"rc={proc.returncode}  elapsed={elapsed:.1f}s  "
        f"size {pre_size} -> {post_size} bytes  "
        f"mtime_changed={post_mtime != pre_mtime}"
    )
    if proc.stdout.strip():
        detail += "\n  stdout: " + proc.stdout.strip().replace("\n", "\n           ")
    if proc.stderr.strip():
        detail += "\n  stderr: " + proc.stderr.strip().replace("\n", "\n           ")

    ok = proc.returncode == 0 and post_exists and post_size > 0 and post_mtime != pre_mtime
    return ok, detail


def main() -> int:
    cmds, RizomUVBridge, script_dir = _bootstrap()

    TEST_DIR.mkdir(parents=True, exist_ok=True)
    build_test_fbx(cmds)

    presets = sorted(p.stem for p in script_dir.glob("*.lua"))
    print(f"[setup] {len(presets)} preset(s) to test: {presets}")

    results = {}
    for preset in presets:
        print(f"\n=== {preset} ===")
        ok, detail = run_preset(preset, RizomUVBridge, script_dir)
        print(("PASS" if ok else "FAIL") + "  " + detail)
        results[preset] = ok

    print("\n--- summary ---")
    for preset, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {preset}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
