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
from mayatk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge, APP  # noqa: E402

_PKG_DIR = _REPO_ROOT / "mayatk" / "uv_utils" / "rizom_bridge"
_SCRIPT_DIR = _PKG_DIR / "scripts"
_TEMPLATE_DIR = _PKG_DIR / "templates"


def _find_rizom() -> "tuple[str, tuple]":
    """Resolve RizomUV via the PRODUCTION discovery (probing it too).

    Goes through the bridge's own ``APP`` spec, so the probe inherits its
    glob priority: the bare ``rizomuv.exe`` is a launcher that ignores
    ``-cfi`` (it hangs a headless run until timeout), so ``Rizomuv_VS``
    must resolve first.
    """
    exe = APP.path
    if not exe:
        sys.exit(APP.not_found_msg)
    return exe, RizomUVBridge._parse_rizom_version(exe)


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
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ]
    faces = [  # quads, 1-based vertex indices
        (1, 2, 3, 4),
        (5, 8, 7, 6),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 8, 4),
        (4, 8, 5, 1),
    ]
    lines = ["# probe cube", "o probe_cube"]
    lines += [f"v {x} {y} {z}" for x, y, z in v]
    # 4 unique vts per face, packed into a rough 3x2 grid of islands.
    vt_lines, f_lines = [], []
    for fi, quad in enumerate(faces):
        u0, v0 = (fi % 3) * 0.33, (fi // 3) * 0.5
        corners = [(u0, v0), (u0 + 0.3, v0), (u0 + 0.3, v0 + 0.45), (u0, v0 + 0.45)]
        base = fi * 4 + 1
        vt_lines += [f"vt {u:.4f} {w:.4f}" for u, w in corners]
        f_lines.append("f " + " ".join(f"{vi}/{base + k}" for k, vi in enumerate(quad)))
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
        faces.append(
            f"f {top_c}/{top_c} {t0 + s + 1}/{t0 + s + 1} {t0 + s2 + 1}/{t0 + s2 + 1}"
        )
    path.write_text("\n".join(lines + vts + faces) + "\n", encoding="ascii")


def write_stacked_obj(path: Path, stacked: int = 3) -> None:
    """Four unique quad islands + ``stacked`` IDENTICAL coincident quads (a
    host-side stack), then the traps a keep-stacked step must NOT weld:
    a quad half-overlapping the stack, a wide+tall pair sharing only a centre
    (a Center-mode stack of different shapes -- MUST stay together), and an
    identical twin offset by 30% (overlap, different centre -- must unstack).
    Face order: 0-3 unique, 4..4+stacked-1 the stack, then half-overlap,
    wide, tall, twin, twin-offset. 3D size == UV size (consistent density)."""
    verts, vts, faces = [], [], []

    def quad(x0, y0, w, h, u0, v0, uw, vh):
        bv, bt = len(verts) + 1, len(vts) + 1
        verts.extend(
            [(x0, y0, 0), (x0 + w, y0, 0), (x0 + w, y0 + h, 0), (x0, y0 + h, 0)]
        )
        vts.extend([(u0, v0), (u0 + uw, v0), (u0 + uw, v0 + vh), (u0, v0 + vh)])
        faces.append(" ".join(f"{bv + k}/{bt + k}" for k in range(4)))

    quad(0, 0, 1.0, 0.5, 0.0, 0.0, 0.10, 0.05)
    quad(2, 0, 0.5, 1.5, 0.2, 0.0, 0.05, 0.15)
    quad(4, 0, 1.2, 1.2, 0.4, 0.0, 0.12, 0.12)
    quad(6, 0, 2.0, 0.3, 0.6, 0.0, 0.20, 0.03)
    for i in range(stacked):
        quad(0, 3 + i * 2, 0.8, 0.8, 0.0, 0.5, 0.08, 0.08)
    y = 3 + stacked * 2
    quad(0, y, 0.8, 0.8, 0.04, 0.5, 0.08, 0.08)  # half-overlaps the stack
    quad(0, y + 2, 3.0, 1.0, 0.30, 0.60, 0.30, 0.10)  # wide  ) share a centre
    quad(0, y + 4, 1.0, 3.0, 0.40, 0.50, 0.10, 0.30)  # tall  ) (0.45, 0.65)
    quad(0, y + 8, 0.8, 0.8, 0.70, 0.60, 0.08, 0.08)  # twin
    quad(0, y + 10, 0.8, 0.8, 0.724, 0.624, 0.08, 0.08)  # twin, 30% offset
    lines = ["# probe stacked", "o probe_stacked"]
    lines += [f"v {x} {y} {z}" for x, y, z in verts]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in vts]
    lines += [f"f {f}" for f in faces]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def write_sprawl_obj(path: Path, objects: int = 8, quads: int = 6) -> None:
    """Quads whose UVs sprawl past the tile and overlap each other heavily.

    The discriminating input for a PACK: the stock cube writer already lays
    its islands out inside 0-1, so "the file was rewritten and the vt lines
    changed" is satisfied by the round trip alone and a packer that did
    nothing still passes. Here nothing starts in the tile, so only a real
    pack can put it there.
    """
    verts, vts, faces = [], [], []
    for o in range(objects):
        faces.append(f"o sprawl_{o}")
        for q in range(quads):
            x0 = (o * quads + q) * 2.5
            base = len(verts) + 1
            verts.extend([(x0, 0, 0), (x0 + 2, 0, 0), (x0 + 2, 1.5, 0), (x0, 1.5, 0)])
            u0, v0 = -0.5 + 0.25 * q, -0.1 + 0.15 * (o % 4)
            vts.extend([(u0, v0), (u0 + 1.4, v0), (u0 + 1.4, v0 + 0.9), (u0, v0 + 0.9)])
            faces.append("f " + " ".join(f"{base + k}/{base + k}" for k in range(4)))
    lines = ["# probe sprawl"]
    lines += [f"v {x} {y} {z}" for x, y, z in verts]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in vts]
    path.write_text("\n".join(lines + faces) + "\n", encoding="ascii")


SUBSET_TAG = "RZ_SUBSET"


def write_subset_obj(path: Path) -> None:
    """Three FIXED quads placed inside the tile, then sixteen quads sprawling
    outside it whose faces carry the subset material -- the host's shell-subset
    tagging, as OBJ ``usemtl``. Face order: 0-2 fixed, 3-18 packed. 3D size is
    10x the UV size throughout (consistent texel density)."""
    verts, vts, faces = [], [], []

    def quad(x0, u0, v0, s, tag):
        bv, bt = len(verts) + 1, len(vts) + 1
        w = s * 10
        verts.extend([(x0, 0, 0), (x0 + w, 0, 0), (x0 + w, w, 0), (x0, w, 0)])
        vts.extend([(u0, v0), (u0 + s, v0), (u0 + s, v0 + s), (u0, v0 + s)])
        faces.append(f"usemtl {tag}")
        faces.append("f " + " ".join(f"{bv + k}/{bt + k}" for k in range(4)))

    for i, (u0, v0, s) in enumerate(
        ((0.40, 0.40, 0.25), (0.05, 0.70, 0.20), (0.75, 0.05, 0.15))
    ):
        quad(i * 10.0, u0, v0, s, "fixed_mat")
    for i in range(16):
        s = 0.2 * (0.8 + (i * 7 % 5) / 4.0)
        quad(100 + i * 8.0, (i % 4) * 0.5 - 0.6, (i // 4) * 0.5 - 0.6, s, SUBSET_TAG)
    lines = ["# probe subset", "o probe_subset"]
    lines += [f"v {x} {y} {z}" for x, y, z in verts]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in vts]
    path.write_text("\n".join(lines + faces) + "\n", encoding="ascii")


STACKED_SUBSET = (0, 4, 5, 6)  # a unique quad + the whole 3-quad stack


def write_stacked_subset_obj(path: Path) -> None:
    """:func:`write_stacked_obj` with :data:`STACKED_SUBSET` carrying the subset
    material: the stack must move as one unit, around fixed islands that
    overlap each other (the wide+tall pair, the twins) and so would group."""
    write_stacked_obj(path)
    out, face = [], 0
    for ln in path.read_text(encoding="ascii").splitlines():
        if ln.startswith("f "):
            out.append(
                f"usemtl {SUBSET_TAG if face in STACKED_SUBSET else 'fixed_mat'}"
            )
            face += 1
        out.append(ln)
    path.write_text("\n".join(out) + "\n", encoding="ascii")


def write_strips_obj(path: Path, deg: float = 30.0) -> None:
    """Twelve thin 8:1 strips whose UVs sit rotated ``deg`` degrees."""
    verts, vts, faces = [], [], []
    a = math.radians(deg)
    for i in range(12):
        bv = len(verts) + 1
        verts.extend(
            [
                (i * 10.0, 0, 0),
                (i * 10.0 + 4, 0, 0),
                (i * 10.0 + 4, 0.5, 0),
                (i * 10.0, 0.5, 0),
            ]
        )
        cu, cv = 0.15 + 0.7 * ((i % 4) / 3), 0.15 + 0.7 * ((i // 4) / 2)
        for x, y in ((-0.12, -0.015), (0.12, -0.015), (0.12, 0.015), (-0.12, 0.015)):
            vts.append(
                (
                    cu + x * math.cos(a) - y * math.sin(a),
                    cv + x * math.sin(a) + y * math.cos(a),
                )
            )
        faces.append("f " + " ".join(f"{bv + k}/{bv + k}" for k in range(4)))
    lines = ["# probe strips", "o probe_strips"]
    lines += [f"v {x} {y} {z}" for x, y, z in verts]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in vts]
    path.write_text("\n".join(lines + faces) + "\n", encoding="ascii")


def _polygons_overlap(a, b, eps: float = 1e-6) -> bool:
    """Separating-axis test for two convex UV polygons (touching is not overlap)."""
    for poly in (a, b):
        for i in range(len(poly)):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
            ax, ay = y0 - y1, x1 - x0
            pa = [ax * x + ay * y for x, y in a]
            pb = [ax * x + ay * y for x, y in b]
            if max(pa) <= min(pb) + eps or max(pb) <= min(pa) + eps:
                return False
    return True


def check_subset(
    region=(0.0, 1.0, 0.0, 1.0),
    fixed=range(3),
    packed=range(3, 19),
    writer=write_subset_obj,
    stack=(),
):
    """Case checker for a subset pack of *writer*'s mesh: the fixed quads come
    back bit-identical, the tagged ones land inside *region*, no two quads
    overlap -- except the members of *stack*, which must stay coincident."""

    def _check(obj_path: Path):
        reference = obj_path.with_name(obj_path.stem + "_reference.obj")
        writer(reference)
        before, after = face_uvs(reference), face_uvs(obj_path)
        reference.unlink()
        problems = []
        if stack:
            spread = max(
                max(math.dist(p, q) for p, q in zip(after[stack[0]], after[i]))
                for i in stack[1:]
            )
            if spread > 1e-6:
                problems.append(f"stack scattered (spread {spread:.2e})")
        drift = max(
            max(math.dist(p, q) for p, q in zip(before[i], after[i])) for i in fixed
        )
        if drift > 1e-9:
            problems.append(f"fixed shells moved (max {drift:.2e})")
        u0, u1, v0, v1 = region
        pts = [p for i in packed for p in after[i]]
        if not all(
            u0 - 1e-6 <= u <= u1 + 1e-6 and v0 - 1e-6 <= v <= v1 + 1e-6 for u, v in pts
        ):
            us, vs = [u for u, _ in pts], [v for _, v in pts]
            problems.append(
                f"packed shells outside {region}: u[{min(us):.3f},{max(us):.3f}] "
                f"v[{min(vs):.3f},{max(vs):.3f}]"
            )
        hits = [
            (i, j)
            for i in packed
            for j in list(fixed) + [k for k in packed if k > i]
            if not (i in stack and j in stack) and _polygons_overlap(after[i], after[j])
        ]
        if hits:
            problems.append(f"{len(hits)} overlapping pair(s), e.g. {hits[:3]}")
        return "; ".join(problems) or None

    return _check


def check_untouched(writer):
    """Case checker: every UV comes back where *writer* put it."""

    def _check(obj_path: Path):
        reference = obj_path.with_name(obj_path.stem + "_reference.obj")
        writer(reference)
        before, after = face_uvs(reference), face_uvs(obj_path)
        reference.unlink()
        drift = max(
            max(math.dist(p, q) for p, q in zip(a, b)) for a, b in zip(before, after)
        )
        return f"UVs moved (max {drift:.2e})" if drift > 1e-9 else None

    return _check


def check_angles(expect_deg: float, tol: float = 0.5):
    """Case checker: every face's first UV edge sits at *expect_deg* (mod 90)."""

    def _check(obj_path: Path):
        off = []
        for i, f in enumerate(face_uvs(obj_path)):
            (u0, v0), (u1, v1) = f[0], f[1]
            ang = math.degrees(math.atan2(v1 - v0, u1 - u0)) % 90.0
            if min(abs(ang - expect_deg), 90 - abs(ang - expect_deg)) > tol:
                off.append(f"{i}:{ang:.1f}")
        return f"faces off {expect_deg} deg: {off[:6]}" if off else None

    return _check


def face_uvs(path: Path):
    """Per-face UV polygons of an OBJ (face order is preserved by Rizom's save)."""
    vts, faces = [], []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = ln.split()
        if not parts:
            continue
        if parts[0] == "vt":
            vts.append((float(parts[1]), float(parts[2])))
        elif parts[0] == "f":
            faces.append([vts[int(tok.split("/")[1]) - 1] for tok in parts[1:]])
    return faces


def check_keep_stacked(stacked: int = 3, expect=True, tol: float = 1e-3):
    """Case checker for :func:`write_stacked_obj` after a pack.

    ``expect=True`` (Keep Stacked on): the coincident stack is still coincident,
    the wide+tall centre-sharing pair still shares a centre, AND the half-overlap
    + offset twin were unstacked (packed away from their neighbours).
    ``expect=False`` (off): the stack itself was scattered."""

    def _dist(a, b):
        return max(math.dist(p, q) for p, q in zip(a, b))

    def _centre(f):
        return (sum(u for u, _ in f) / len(f), sum(v for _, v in f) / len(f))

    def _check(obj_path: Path):
        faces = face_uvs(obj_path)
        stack = faces[4 : 4 + stacked]
        half, wide, tall, twin, twin_off = faces[4 + stacked : 9 + stacked]
        stack_drift = max(_dist(stack[0], f) for f in stack[1:])
        if not expect:
            return (
                None
                if stack_drift > tol
                else "stack still coincident with Keep Stacked off"
            )
        problems = []
        if stack_drift > tol:
            problems.append(f"stack scattered (drift {stack_drift:.4f})")
        if math.dist(_centre(wide), _centre(tall)) > tol:
            problems.append("centre-sharing pair (Center-mode stack) was split")
        if _dist(stack[0], half) < 0.05:
            problems.append("half-overlap was welded to the stack")
        if _dist(twin, twin_off) < 0.05:
            problems.append("offset twin was welded to its neighbour")
        return "; ".join(problems) or None

    return _check


# ---------------------------------------------------------------------------
# Script rendering (same steps as RizomUVBridge._construct_full_script)
# ---------------------------------------------------------------------------


def render_script(user_lua: str, obj_path: Path, overrides: dict = None) -> str:
    from pythontk.str_utils._str_utils import StrUtils

    # Mirror the bridge: expand shared includes (__PACK_BLOCK__) before
    # version-stripping + substitution.
    user_lua = _params.Parameters.expand_includes(user_lua)
    user_lua = _params.Parameters.strip_unsupported(user_lua, RIZOM_VERSION)
    values = _params.Parameters.defaults()
    values.update(overrides or {})
    context = _params.Parameters.render_context(values)
    user_lua = StrUtils.replace_delimited(user_lua, context)

    wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
    return StrUtils.replace_delimited(
        wrapper,
        {
            "EXPORT_PATH": obj_path.as_posix(),
            "FBX_FLAG": "",  # OBJ probe: extension auto-detect
            "USER_SCRIPT": user_lua,
        },
    )


def vt_signature(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    vts = [ln for ln in text.splitlines() if ln.startswith("vt ")]
    return len(vts), hash("\n".join(vts))


def uv_bounds(path: Path) -> "tuple[float, float, float, float]":
    """(min_u, max_u, min_v, max_v) over the OBJ's ``vt`` records."""
    us, vs = [], []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("vt "):
            parts = ln.split()
            us.append(float(parts[1]))
            vs.append(float(parts[2]))
    return (min(us), max(us), min(vs), max(vs))


def check_bounds(umin: float, umax: float, vmin: float, vmax: float, tol: float = 0.02):
    """Case checker: assert the saved OBJ's UVs sit inside the given box."""

    def _check(obj_path: Path):
        lo_u, hi_u, lo_v, hi_v = uv_bounds(obj_path)
        ok = (
            lo_u >= umin - tol
            and hi_u <= umax + tol
            and lo_v >= vmin - tol
            and hi_v <= vmax + tol
        )
        if not ok:
            return (
                f"UVs outside [{umin},{umax}]x[{vmin},{vmax}]: "
                f"got u[{lo_u:.3f},{hi_u:.3f}] v[{lo_v:.3f},{hi_v:.3f}]"
            )
        return None

    return _check


def run_case(
    name: str, user_lua: str, mesh_writer, overrides: dict = None, check=None
) -> dict:
    _SCRATCH.mkdir(
        parents=True, exist_ok=True
    )  # temp_tests/ is gitignored — absent on fresh clones
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
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        rc = proc.returncode
        out_tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    except subprocess.TimeoutExpired:
        rc, out_tail = "TIMEOUT", ""
    elapsed = time.time() - t0

    saved = obj_path.stat().st_mtime != pre_mtime
    post_sig = vt_signature(obj_path) if saved else pre_sig
    check_err = None
    if check is not None and saved:
        check_err = check(obj_path)
    return {
        "name": name,
        "rc": rc,
        "elapsed": f"{elapsed:.1f}s",
        "saved": saved,
        "uvs_changed": saved and post_sig != pre_sig,
        "vt": f"{pre_sig[0]} -> {post_sig[0]}",
        "check_err": check_err,
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

# Probe results on RizomUV 2020.1 (recorded 2026-07 for the phase 1/2 rollout;
# re-run and update when a newer Rizom is installed). SAFE = rc 0 + saved;
# CRASH = access violation (0xC0000409 family) => the field is version-gated
# in parameters.py / the presets and must be re-verified for EFFECT on >= 2022.
#   ZomPack MarginSize .............. SAFE   (shipped ungated)
#   ZomPack SpacingSize ............. SAFE   (shipped <= 2021; PaddingSize >= 2022)
#   ZomPack PaddingSize ............. CRASH  (gated >= 2022, effect owed)
#   ZomPack MapResolution ........... CRASH  (not shipped)
#   ZomPack Resolution .............. SAFE   (shipped UNGATED as of 2026-08-05;
#     distinct from the crashing MapResolution. Measured 2026-08-05 -- this is
#     what makes a single send converge, and omitting it was the cause of
#     "pack has to be sent twice to fill the UV space". Through the real Maya
#     bridge, 2 sends each: 4 DAG instances 0.5766 -> 0.6655 without it, vs
#     0.6777 on send 1 and CONVERGED with it; mixed instances+unique +11.3%,
#     converged. Trade: an all-unique scene is slightly worse at 1024
#     (0.2124 -> 0.2088 on send 1). See MIN_VERSIONS in parameters.py.)
#   ZomPack MaxMutations ............ SAFE but NOT WORTH IT (stays gated >= 2022)
#     No effect on the stacked/instances case at any value 25-250. Helps only a
#     single-mesh grid (0.8084 -> 0.8394) and needs 250 to be stable across
#     passes, at 8x runtime (78s vs 10s). Runtime scales hard: 1000 = 212s,
#     5000 = >75 MIN of CPU -- cap any probe of this field.
#   ZomPack Rotate.Enable ........... SAFE but NO MEASURABLE EFFECT (stays gated)
#     NOTE when probing it: the block already carries its own Rotate={Step=...}
#     table, so an INJECTED second Rotate= key is silently overridden by Lua's
#     last-key-wins. Rewrite the existing table in place instead.
#   ZomPack Scaling.Mode ............ SAFE and REAL, but only 0 vs non-0 is
#     distinguishable at the shipped defaults. Measured 2026-08-05 on a 2-cube
#     OBJ (4:1 linear, 16:1 in 3D area) carrying IDENTICAL incoming UV islands,
#     i.e. maximally inconsistent texel density: Mode=0 keeps the packed
#     big/small island-area ratio at 1.000, Mode=1/2/3/4 all take it to 16.000.
#     Mode 1 vs 2 differ ONLY by a global uniform factor -- at LayoutScalingMode=0
#     mode 1 leaves islands at literal world scale (per-face UV area 4.000 /
#     0.250 == the 3D face areas, layout spanning u[0,5.53] v[0,6.01]) while
#     mode 2 normalizes into the tile (0.042353 / 0.002647, sum 0.270) -- so the
#     default LayoutScalingMode=2 (best fit) renormalizes that factor away and
#     the two modes become equivalent. Values 3 and 4 (not exposed in the panel)
#     save byte-identical to 2. And when the incoming UVs ALREADY have
#     consistent texel density
#     (the normal state of any unwrapped mesh) modes 0/1/2 land on numerically
#     IDENTICAL areas and bounds. => "Pre-scale does nothing" is the expected
#     reading unless the selection has mismatched texel density AND the compare
#     is 0-vs-non-0. Panel wording is what needs to carry this, not the gate.
#     TRAP when re-probing this: on that already-consistent mesh the three modes
#     still save DIFFERENT vt digests even though areas and bounds match to the
#     last digit -- the packer merely permutes equal-sized islands between
#     equivalent slots. Compare per-island AREAS, not a whole-file digest, or
#     you will conclude the modes differ when only the arrangement did.
#   ZomPack Scaling.Mix ............. SAFE but NO EFFECT (gated >= 2022 since
#     2026-09-23). Mix=true vs Mix=false with Mode=2 on the mismatched mesh
#     produced a BYTE-IDENTICAL save (same vt digest), i.e. the differing input
#     was ignored; re-measured through the real Maya bridge 2026-09-23, same.
#     Determinism control for that conclusion: the SAME config re-run
#     from a separate probe script into a separate output file reproduced its
#     numbers exactly (Mode=2/Layout=0 -> 0.042353 / 0.002647 both times), so
#     identical output means identical treatment, not a coincidental collision.
#   ZomPack RecursionDepth .......... SAFE but NO EFFECT in any bridge flow
#     (pinned to 1 since 2026-09-23): 1/2/5 byte-identical on OBJ, through the
#     real Maya bridge, and with keep-stacked groups -- the hierarchy is always
#     RootGroup > one tile > islands, nothing nested to pack first.
#   ZomPack Rotate.Mode / Rotate.Step  SAFE and REAL. Rizom PRE-ORIENTS every
#     island before the step search (Mode defaults to 2 = upright along the
#     minimal bounding box; the RootGroup props read back Rotate={Mode=2,
#     Step=0, Min=0, Max=180}), so 30-degree strips come out axis-aligned at
#     any step. Keeping the incoming angle needs Mode=0 AND Step=0 (either
#     alone still rotates) -- shipped as Rotate off (pack_rotate_off/on).
#     Steps 90/45/30 saved byte-identical on the strips; 15 differs.
#   RootGroup / island readback ..... ZomGet("Lib.Mesh.RootGroup") and
#     ZomGet("Lib.Mesh.Islands") are SAFE and return the whole tree (per-island
#     BBoxUV {umin,umax,vmin,vmax,w,w}, PolyIDs, TopoStable.Selected; per-group
#     IslandIDs + TopoStable.Pack defaults Resolution=200, MaxMutations=1).
#     Indexing below them ("Lib.Mesh.Islands.0") CRASHES natively -- pcall
#     cannot catch it; a bad top-level path ("Mesh.Islands") only errors.
#     Lua's io library works under -cfi: the only readback channel (print()
#     is swallowed). Island-table keys and IslandIDs values differ in type --
#     compare them via tostring().
#   Island SUBSET selection ......... ZomSelect PrimType="Island" with IDs=
#     needs List=true (without it: silent no-op -- that, not a missing
#     feature, is why ID selection "never worked"); Objects={name} selects an
#     object's islands WITHOUT List (with it: no-op); Materials={name} selects
#     the islands carrying that material (OBJ usemtl and Maya FBX alike) --
#     shipped as the shell subset. No-ops: PrimType="Polygon" IDs (selects
#     polygons, not islands, and Convert={Source="Polygon"} does not promote
#     them), IslandGroup Names even with List=true. ZomIslandProperties
#     Pack={Locked=true} and a DefineGroup with Pack.Locked lock NOTHING.
#   ZomPack WorkingSet="Visible&Selected"  SAFE and REAL: unselected islands
#     are the forbidden area and stay put -- ONLY with LayoutScalingMode=0
#     (any other value rescales them too; PostLayoutScalingProcessIslandSelection
#     fits the subset over them instead) and a membership-only tile filing:
#     DistributeInTilesEvenly/ByBBox re-centre every island in its tile unless
#     FreezeIslands=true. Filing: Evenly+Freeze puts EVERY island under the
#     tile (fixed islands beside it then squeezed the subset into a strip);
#     ByBBox+Freeze files by bounds centre; OBJ load files by position; a pack
#     that overflows drops the overflow out of the tile, where the next pack
#     skips it -- hence gather + ByBBox before each refit in pack_block.lua.
#     Coverage of a partial region by fitting to it degenerates into a strip
#     (the packer only fills a whole tile). With an EMPTY selection it packs
#     every island -- pack_block.lua packs nothing when the tag reached no
#     island (case pack_subset_missing_tag). Groups: the packer moves a group
#     only if the GROUP is selected (ZomSelect PrimType="IslandGroup",
#     IslandGroupMode="Group", All=true) -- selected member islands alone left
#     a keep-stacked stack in place on top of fixed islands -- and treats a
#     fixed island INSIDE a group as no obstacle; DefineGroupsByOverlapness
#     honours WorkingSet="Visible&Selected" (groups only the subset), which is
#     how keep_stacked_block.lua keeps fixed islands ungrouped
#     (case pack_subset_keep_stacked).
#   Lua syntax error ................ CRASH 0xC00000FF, like an unknown field:
#     an unterminated string in a probe script took 2020.1 down before any
#     statement ran -- a crash code alone does not implicate the API.
#   ZomIslandGroups DefineGroupsByOverlapness
#     + Properties={Pack={Stacked=true}} SAFE and REAL (shipped as the opt-in
#     PACK_KEEP_STACKED, templates/keep_stacked_block.lua). Measured 2026-08-16
#     on 4 unique + 3 coincident quad islands: default ZomPack scatters the
#     stack (max vertex dist 1.08); the block keeps it coincident (0.0000)
#     and the stack group's UV/3D area ratio equals every other island's
#     (0.12445 across the board -- consistent texel density; coverage even
#     improves, 3 islands -> 1 unit). Without the Stacked property the group
#     forms but is repacked internally into a grid (dist 0.72) -- the property
#     is what makes it rigid. TRAP: on its own it groups ANY overlap (partial
#     overlaps kept at their relative offset, an unpacked layout welded into
#     one frozen clump -- the 2026-08-16 "no packing took place" report);
#     islands that only touch along an edge are not grouped.
#   ZomDeform CenterMode="MultiCOG" ... SAFE and REAL: scales/rotates each
#     island about its OWN centre; x0.001 then x1000 round-trips to 1.4e-14.
#     Shipped as the keep-stacked isolator: shrink -> group-by-overlap ->
#     unshrink groups only islands that share a centre (exact stacks AND a
#     wide+tall pair sharing a centre stay together; a half-overlap and a
#     30%-offset twin are unstacked). Both pack.lua and optimize.lua verified.
#   ZomIslandGroups DefineGroup IslandByPolygonIDs + Pack.Stacked  SAFE and
#     REAL (host-driven alternative, polygon IDs = OBJ face order) -- not
#     shipped: needs a Maya-face -> Rizom-polygon map across the multi-object
#     FBX; the overlap detector above needs nothing from the host.
#   ZomTopoCopy Mode="Stack" ......... CRASH  (0xC00000FF on quad, cube AND
#     cylinder meshes, with and without WorkingSet/Orientation/AreaThreshold)
#     -- Rizom's own Stack Similar is unusable headless on 2020.1; stack on
#     the host (polyUVStackSimilarShells) and send with Keep Stacked instead.
#   Auto.ReWeld ..................... CRASH  (gated >= 2022 in unwrap_hard/hybrid)
#   Auto.BooleanUnoverlap ........... CRASH  (gated >= 2022 in unwrap_hard/hybrid)
#   Auto.SkeletonUnoverlap .......... SAFE   (shipped ungated in unwrap_organic)
#   QuasiDevelopable.FitCones=true .. SAFE   (shipped as FIT_CONES, real effect)
#   SharpEdges + QuasiDevelopable ... CRASH  (unwrap_hybrid preset-gated >= 2022)
#
# Input-data findings (2020.1, probed against the production bridge):
#   Mesh with NO UVs ................ SAFE   (Rizom generates them)
#   Two UV sets / renamed UV set .... SAFE
#   NURBS surface in the selection .. SAFE   (FBX carries no poly for it)
#   Degenerate UVs (every coord
#     collapsed onto one point) ..... CRASH  (rc 0xC00000FF, before ZomSave)
# No output ever reaches stdout/stderr in -cfi mode -- a failing script is only
# diagnosable by running it in RizomUV's Script Editor, which is what the
# bridge's no-save error now says instead of promising a Lua traceback.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--experiments",
        action="store_true",
        help="Also probe candidate (not yet shipped) Lua snippets.",
    )
    ap.add_argument("--only", help="Run a single named case.")
    args = ap.parse_args()

    cases = []
    preset_meshes = {
        "pack": write_cube_obj,
        "optimize": write_cube_obj,
        "unwrap_hard": write_cube_obj,
        "unwrap_organic": write_cylinder_obj,
        # Needs >= 2022 (both segmenters in one Auto block); auto-skipped
        # below its @min_rizom gate.
        "unwrap_hybrid": write_cube_obj,
        # Needs a >= 2022.2 Rizom (island-group selection + pack WorkingSet);
        # auto-skipped below the preset's @min_rizom gate. NOTE: uses OBJ
        # o-groups as stand-ins for the FBX island groups the production
        # bridge sends -- confirm on a gated-in install that they resolve.
        "pack_into_existing": write_cube_obj,
    }
    # Per-preset placeholder overrides (defaults otherwise).
    preset_overrides = {
        # Bridge-injected selection token; a probe run has no Maya-side
        # export map, so name the probe mesh's group directly.
        "pack_into_existing": {"PACK_SELECT_NAMES": '{"probe_cube"}'},
    }
    for preset, writer in preset_meshes.items():
        lua = (_SCRIPT_DIR / f"{preset}.lua").read_text(encoding="utf-8")
        required = _params.Parameters.preset_min_version(lua)
        if required and RIZOM_VERSION < required:
            print(
                f"skip {preset}: needs Rizom >= {'.'.join(map(str, required))} "
                f"(installed {'.'.join(map(str, RIZOM_VERSION))})"
            )
            continue
        cases.append((preset, lua, writer, preset_overrides.get(preset), None))
    # Exercise the weld-off Lua branch too (if false then ... end).
    hard = (_SCRIPT_DIR / "unwrap_hard.lua").read_text(encoding="utf-8")
    cases.append(
        ("unwrap_hard_noweld", hard, write_cube_obj, {"WELD_SEAMS": False}, None)
    )
    # Post-pack placement: pack into UDIM 1012 at quarter coverage and
    # assert the layout landed in the tile's bottom-left quadrant.
    pack = (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
    cases.append(
        (
            "pack_udim_quarter",
            pack,
            write_cube_obj,
            {"TARGET_UDIM": 1012, "UV_AREA": 3},
            check_bounds(1.0, 1.5, 1.0, 1.5),
        )
    )

    # A pack that no-ops still saves a rewritten file with perturbed vt
    # lines, so "saved + uvs_changed" cannot tell packing from a round trip.
    # Start outside the tile and require the layout to land in it.
    cases.append(
        (
            "pack_sprawl",
            pack,
            write_sprawl_obj,
            None,
            check_bounds(0.0, 1.0, 0.0, 1.0),
        )
    )

    # Shell subset: only the tagged quads may move; the fixed ones come back
    # bit-identical and nothing lands on them. Also in a far UDIM (packed in
    # that tile; the fixed quads stay in 1001) and with fixed quads that the
    # subset must pack around rather than onto.
    subset_token = {"PACK_SUBSET": '{"%s"}' % SUBSET_TAG}
    cases.append(("pack_subset", pack, write_subset_obj, subset_token, check_subset()))
    cases.append(
        (
            "pack_subset_udim1002",
            pack,
            write_subset_obj,
            dict(subset_token, TARGET_UDIM=1002),
            check_subset(region=(1.0, 2.0, 0.0, 1.0)),
        )
    )

    # Keep Stacked + subset: the subset's stack moves as ONE unit and lands on
    # no fixed island -- including fixed islands that overlap each other.
    cases.append(
        (
            "pack_subset_keep_stacked",
            pack,
            write_stacked_subset_obj,
            dict(subset_token, PACK_KEEP_STACKED=True),
            check_subset(
                fixed=[i for i in range(12) if i not in STACKED_SUBSET],
                packed=STACKED_SUBSET,
                writer=write_stacked_subset_obj,
                stack=(4, 5, 6),
            ),
        )
    )

    # A tag that reached no island must pack NOTHING: an empty selection makes
    # ZomPack pack every island, fixed ones included.
    cases.append(
        (
            "pack_subset_missing_tag",
            pack,
            write_subset_obj,
            {"PACK_SUBSET": '{"NO_SUCH_TAG"}'},
            check_untouched(write_subset_obj),
        )
    )

    # Rotate off keeps every island's incoming angle (Mode 0 + Step 0 on
    # 2020.1); on, the upright pre-orient straightens 30-degree strips.
    cases.append(
        (
            "pack_rotate_off",
            pack,
            write_strips_obj,
            {"PACK_ROTATE_ENABLE": False},
            check_angles(30.0),
        )
    )
    cases.append(("pack_rotate_on", pack, write_strips_obj, None, check_angles(0.0)))

    # Keep-stacked: with the knob on, islands that arrive coincident leave
    # coincident (grouped in Rizom's Group Stack mode); off = Rizom's normal
    # per-island pack scatters them. Both pack-type presets carry the block.
    optimize = (_SCRIPT_DIR / "optimize.lua").read_text(encoding="utf-8")
    for preset_name, lua in (("pack", pack), ("optimize", optimize)):
        cases.append(
            (
                f"{preset_name}_keep_stacked",
                lua,
                write_stacked_obj,
                {"PACK_KEEP_STACKED": True},
                check_keep_stacked(expect=True),
            )
        )
        cases.append(
            (
                f"{preset_name}_unstacked",
                lua,
                write_stacked_obj,
                {"PACK_KEEP_STACKED": False},
                check_keep_stacked(expect=False),
            )
        )

    if args.experiments:
        cases.append(
            ("exp_weld_then_hard", EXP_WELD_PREFIX + hard, write_cube_obj, None, None)
        )
        cases.append(
            (
                "exp_quasi_developable",
                EXP_QUASI_DEVELOPABLE,
                write_cylinder_obj,
                None,
                None,
            )
        )

    if args.only:
        cases = [c for c in cases if c[0] == args.only]

    results = [
        run_case(name, lua, writer, ov, check) for name, lua, writer, ov, check in cases
    ]

    print(
        f"\n{'case':<24} {'rc':>8} {'time':>7} {'saved':>6} {'uvs_chg':>8} {'vt':>14}"
    )
    ok = True
    for r in results:
        print(
            f"{r['name']:<24} {str(r['rc']):>8} {r['elapsed']:>7} "
            f"{str(r['saved']):>6} {str(r['uvs_changed']):>8} {r['vt']:>14}"
        )
        if r["tail"] and (r["rc"] != 0 or not r["saved"]):
            print(f"    tail: {r['tail']}")
        if r["check_err"]:
            print(f"    CHECK FAILED: {r['check_err']}")
        if r["rc"] != 0 or not r["saved"] or not r["uvs_changed"] or r["check_err"]:
            ok = False
    print("\n===RESULT=== " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
