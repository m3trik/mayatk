# !/usr/bin/python
# coding=utf-8
"""Standalone RizomUV headless smoketest / Lua-API probe (no Maya required).

Runs each bundled ``scripts/*.lua`` preset through the real installed
RizomUV (``-cfi``) against generated OBJ meshes, using the same wrapper +
placeholder-substitution path the bridge uses. Verifies:

- RizomUV exits 0 (no access violation from an incompatible Lua field).
- The OBJ on disk was rewritten (the script reached ``ZomSave``).
- The UV (``vt``) data actually changed.

Also probes EXPERIMENTAL Lua snippets (candidate preset improvements) so
API compatibility with the installed Rizom is verified *before* they are
added to a shipped preset -- 2020.1 crashes hard on unknown fields.

Run from the workspace venv (needs uitk for the parameters module):
    python mayatk/test/rizom_headless_probe.py [--experiments]

Not collected by run_tests.py (name doesn't match test_*.py) -- it needs
the external RizomUV executable, so it's a manual gate: run it after ANY
edit to scripts/*.lua or templates/*.lua.
"""
import argparse
import math
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from mayatk.uv_utils.rizom_bridge import parameters as _params  # noqa: E402
from mayatk.uv_utils.rizom_bridge._rizom_bridge import (  # noqa: E402
    _RIZOM_SCAN_GLOBS,
    _parse_rizom_version,
)
from pythontk.core_utils.app_launcher import AppLauncher  # noqa: E402

_PKG_DIR = _REPO_ROOT / "mayatk" / "uv_utils" / "rizom_bridge"
_SCRIPT_DIR = _PKG_DIR / "scripts"
_TEMPLATE_DIR = _PKG_DIR / "templates"


def _find_rizom() -> "tuple[str, tuple]":
    """Resolve RizomUV via the PRODUCTION scan (probing discovery too).

    Uses the bridge's own glob priority: the bare ``rizomuv.exe`` is a
    launcher that ignores ``-cfi`` (it hangs a headless run until
    timeout), so ``scan_install_dirs`` must yield Rizomuv_VS first.
    """
    exe = next(AppLauncher.scan_install_dirs(_RIZOM_SCAN_GLOBS), None)
    if not exe:
        sys.exit("RizomUV not found under 'Program Files\\Rizom Lab'.")
    return exe, _parse_rizom_version(exe)


RIZOM_EXE, RIZOM_VERSION = _find_rizom()
TIMEOUT = 180

# Artifacts land in the gitignored temp_tests sandbox.
_SCRATCH = Path(__file__).parent / "temp_tests" / "_rizom_probe_scratch"


# ---------------------------------------------------------------------------
# OBJ generation
# ---------------------------------------------------------------------------

def write_cube_obj(path: Path) -> None:
    """Unit cube, per-face UVs (6 separate islands -> existing seams)."""
    v = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    faces = [  # quads, 1-based vertex indices
        (1, 2, 3, 4), (5, 8, 7, 6), (1, 5, 6, 2),
        (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1),
    ]
    lines = ["# probe cube"]
    lines += [f"v {x} {y} {z}" for x, y, z in v]
    # 4 unique vts per face, packed into a rough 3x2 grid of islands.
    vt_lines, f_lines = [], []
    for fi, quad in enumerate(faces):
        u0, v0 = (fi % 3) * 0.33, (fi // 3) * 0.5
        corners = [(u0, v0), (u0 + 0.3, v0), (u0 + 0.3, v0 + 0.45), (u0, v0 + 0.45)]
        base = fi * 4 + 1
        vt_lines += [f"vt {u:.4f} {w:.4f}" for u, w in corners]
        f_lines.append(
            "f " + " ".join(f"{vi}/{base + k}" for k, vi in enumerate(quad))
        )
    path.write_text("\n".join(lines + vt_lines + f_lines) + "\n", encoding="ascii")


def write_cylinder_obj(path: Path, segments: int = 24, rows: int = 4) -> None:
    """Capped cylinder with naive cylindrical-projection UVs (one welded wrap:
    the seamless closed surface a real unwrap has to cut)."""
    lines = ["# probe cylinder"]
    vts, faces = [], []
    # Side vertices: rows+1 rings of `segments` verts.
    for r in range(rows + 1):
        z = r / rows * 2.0 - 1.0
        for s in range(segments):
            a = 2 * math.pi * s / segments
            lines.append(f"v {math.cos(a):.5f} {math.sin(a):.5f} {z:.5f}")
    # One vt per vertex (projection; last column wraps -- intentionally shared).
    for r in range(rows + 1):
        for s in range(segments):
            vts.append(f"vt {s / segments:.5f} {r / rows:.5f}")
    for r in range(rows):
        for s in range(segments):
            s2 = (s + 1) % segments
            a = r * segments + s + 1
            b = r * segments + s2 + 1
            c = (r + 1) * segments + s2 + 1
            d = (r + 1) * segments + s + 1
            faces.append(f"f {a}/{a} {b}/{b} {c}/{c} {d}/{d}")
    # Cap centers.
    n_side = (rows + 1) * segments
    lines.append("v 0 0 -1.0")
    lines.append("v 0 0 1.0")
    vts.append("vt 0.5 0.0")
    vts.append("vt 0.5 1.0")
    bot_c, top_c = n_side + 1, n_side + 2
    for s in range(segments):
        s2 = (s + 1) % segments
        faces.append(f"f {bot_c}/{bot_c} {s2 + 1}/{s2 + 1} {s + 1}/{s + 1}")
        t0 = rows * segments
        faces.append(f"f {top_c}/{top_c} {t0 + s + 1}/{t0 + s + 1} {t0 + s2 + 1}/{t0 + s2 + 1}")
    path.write_text("\n".join(lines + vts + faces) + "\n", encoding="ascii")


# ---------------------------------------------------------------------------
# Script rendering (same steps as RizomUVBridge._construct_full_script)
# ---------------------------------------------------------------------------

def render_script(user_lua: str, obj_path: Path, overrides: dict = None) -> str:
    from pythontk.str_utils._str_utils import StrUtils

    user_lua = _params.strip_unsupported(user_lua, RIZOM_VERSION)
    values = _params.defaults()
    values.update(overrides or {})
    context = _params.render_context(values)
    user_lua = StrUtils.replace_delimited(user_lua, context)

    wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
    return StrUtils.replace_delimited(wrapper, {
        "EXPORT_PATH": obj_path.as_posix(),
        "FBX_FLAG": "",  # OBJ probe: extension auto-detect
        "USER_SCRIPT": user_lua,
    })


def vt_signature(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    vts = [ln for ln in text.splitlines() if ln.startswith("vt ")]
    return len(vts), hash("\n".join(vts))


def run_case(name: str, user_lua: str, mesh_writer, overrides: dict = None) -> dict:
    _SCRATCH.mkdir(parents=True, exist_ok=True)  # temp_tests/ is gitignored — absent on fresh clones
    obj_path = _SCRATCH / f"{name}.obj"
    mesh_writer(obj_path)
    pre_sig = vt_signature(obj_path)
    pre_mtime = obj_path.stat().st_mtime

    script = render_script(user_lua, obj_path, overrides)
    lua_path = _SCRATCH / f"{name}.lua"
    lua_path.write_text(script, encoding="utf-8")

    t0 = time.time()
    try:
        proc = subprocess.run(
            [RIZOM_EXE, "-cfi", str(lua_path)],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
        rc = proc.returncode
        out_tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    except subprocess.TimeoutExpired:
        rc, out_tail = "TIMEOUT", ""
    elapsed = time.time() - t0

    saved = obj_path.stat().st_mtime != pre_mtime
    post_sig = vt_signature(obj_path) if saved else pre_sig
    return {
        "name": name,
        "rc": rc,
        "elapsed": f"{elapsed:.1f}s",
        "saved": saved,
        "uvs_changed": saved and post_sig != pre_sig,
        "vt": f"{pre_sig[0]} -> {post_sig[0]}",
        "tail": out_tail,
    }


# ---------------------------------------------------------------------------
# Experimental snippets (candidate preset improvements)
# ---------------------------------------------------------------------------

# Weld all existing seams before autoseam -> true re-unwrap instead of
# accumulating cuts on top of the incoming layout.
EXP_WELD_PREFIX = """\
ZomSelect({PrimType="Edge", WorkingSet="Visible&UnLocked", Select=true, All=true, ResetBefore=true})
ZomWeld({PrimType="Edge", WorkingSet="Visible&UnLocked"})
"""

# Mosaic-style segmentation for organic meshes (no sharp dihedrals to find).
EXP_QUASI_DEVELOPABLE = """\
ZomSelect({
    PrimType="Edge",
    WorkingSet="Visible&UnLocked",
    IslandGroupMode="Group",
    Select=true,
    ResetBefore=true,
    ProtectMapName="Protect",
    FilterIslandVisible=true,
    Auto={
        QuasiDevelopable={Developability=0.5, IslandPolyNBMin=1, FitCones=false, Straighten=true},
        PipesCutter=true,
        HandleCutter=true,
        StoreCoordsUVW=true,
        FlatteningMode=0,
        FlatteningUnfoldParams={
            StopIfZeroMix=true,
            BorderIntersections=true,
            TriangleFlips=true,
        },
    },
})
ZomCut({PrimType="Edge", WorkingSet="Visible&UnLocked"})
ZomUnfold({PrimType="Edge", MinAngle=1e-05, Mix=1, Iterations=10, PreIterations=10,
    StopIfOutOFDomain=false, RoomSpace=0.001, PinMapName="Pin", ProcessNonFlats=true,
    ProcessSelection=true, ProcessAllIfNoneSelected=true, ProcessJustCut=true,
    BorderIntersections=true, TriangleFlips=true})
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", action="store_true",
                    help="Also probe candidate (not yet shipped) Lua snippets.")
    ap.add_argument("--only", help="Run a single named case.")
    args = ap.parse_args()

    cases = []
    preset_meshes = {
        "pack": write_cube_obj,
        "optimize": write_cube_obj,
        "unwrap_hard": write_cube_obj,
        "unwrap_organic": write_cylinder_obj,
    }
    for preset, writer in preset_meshes.items():
        lua = (_SCRIPT_DIR / f"{preset}.lua").read_text(encoding="utf-8")
        cases.append((preset, lua, writer, None))
    # Exercise the weld-off Lua branch too (if false then ... end).
    hard = (_SCRIPT_DIR / "unwrap_hard.lua").read_text(encoding="utf-8")
    cases.append(("unwrap_hard_noweld", hard, write_cube_obj, {"WELD_SEAMS": False}))

    if args.experiments:
        cases.append(("exp_weld_then_hard", EXP_WELD_PREFIX + hard, write_cube_obj, None))
        cases.append(
            ("exp_quasi_developable", EXP_QUASI_DEVELOPABLE, write_cylinder_obj, None)
        )

    if args.only:
        cases = [c for c in cases if c[0] == args.only]

    results = [run_case(name, lua, writer, ov) for name, lua, writer, ov in cases]

    print(f"\n{'case':<24} {'rc':>8} {'time':>7} {'saved':>6} {'uvs_chg':>8} {'vt':>14}")
    ok = True
    for r in results:
        print(f"{r['name']:<24} {str(r['rc']):>8} {r['elapsed']:>7} "
              f"{str(r['saved']):>6} {str(r['uvs_changed']):>8} {r['vt']:>14}")
        if r["tail"] and (r["rc"] != 0 or not r["saved"]):
            print(f"    tail: {r['tail']}")
        if r["rc"] != 0 or not r["saved"] or not r["uvs_changed"]:
            ok = False
    print("\n===RESULT=== " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
