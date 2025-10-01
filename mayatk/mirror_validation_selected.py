# Mirror Validation Test for Selected Object
# Usage: Select a single mesh transform and run:
# import mayatk.mirror_validation_selected as mvs; mvs.run()
# Prints a summary of mirror plane accuracy for several axis/pivot combinations.

from __future__ import annotations
import math
from collections import defaultdict
import pymel.core as pm
from pymel.core.general import MayaNodeError

try:
    from mayatk.edit_utils._edit_utils import EditUtils
    from mayatk.xform_utils._xform_utils import XformUtils
except Exception:
    # Fallback relative imports if run standalone inside same package
    from edit_utils._edit_utils import EditUtils  # type: ignore
    from xform_utils._xform_utils import XformUtils  # type: ignore

# Configuration
AXES = ["x", "-x", "y", "-y", "z", "-z"]
PIVOTS = [
    "object",
    "center",
    "xmin",
    "xmax",
]  # xmin/xmax only meaningful for x-axis but safe
METHODS = ["poly", "api"]  # Two mirror implementations
USE_OBJECT_AXES = [False]  # Add True if you want to test local axes (world tests first)
REFLECT_NEGATIVE_PIVOT = [True, False]  # Only applies to negative axes in poly method
MAX_CASES = 80  # Safety cap
VERT_ERROR_THRESHOLD = 1e-4  # Positional tolerance

# ---------------------------------------------------------------------------
# Safe ops
# ---------------------------------------------------------------------------


def _safe_exists(node):
    try:
        return bool(node) and pm.objExists(node)
    except Exception:
        return False


def _safe_delete(nodes):
    for n in nodes:
        if not n:
            continue
        try:
            if isinstance(n, str):
                if pm.objExists(n):
                    pm.delete(n)
            else:  # PyNode or list
                if isinstance(n, (list, tuple)):
                    _safe_delete(n)
                else:
                    if _safe_exists(n):
                        pm.delete(n)
        except MayaNodeError:
            # Already deleted / renamed
            pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _get_world_points(obj):
    import maya.api.OpenMaya as om

    sel = om.MSelectionList()
    sel.add(obj.name())
    dag = sel.getDagPath(0)
    fn = om.MFnMesh(dag)
    pts = fn.getPoints(om.MSpace.kWorld)
    return [(p.x, p.y, p.z) for p in pts]


def _mirror_coordinate(val, plane):
    return plane - (val - plane)


def _axis_index(axis: str) -> int:
    a = axis.lstrip("-")
    return {"x": 0, "y": 1, "z": 2}[a]


def _evaluate_pair(original_pts, mirrored_pts, axis_idx, plane_coord):
    """Reflect each original point about plane and measure nearest identical point in mirrored set.
    Returns (avg_error, max_error, matched_ratio)."""
    # Build hash buckets on rounded coords for O(1) membership
    precision = 5
    factor = 10**precision
    bucket = defaultdict(int)
    for p in mirrored_pts:
        key = (
            int(round(p[0] * factor)),
            int(round(p[1] * factor)),
            int(round(p[2] * factor)),
        )
        bucket[key] += 1

    total_err = 0.0
    max_err = 0.0
    exact_hits = 0
    near_hits = 0
    tol = VERT_ERROR_THRESHOLD

    for p in original_pts:
        q = list(p)
        q[axis_idx] = _mirror_coordinate(p[axis_idx], plane_coord)
        key = (
            int(round(q[0] * factor)),
            int(round(q[1] * factor)),
            int(round(q[2] * factor)),
        )
        if bucket.get(key):
            # Perfect (within rounding) match
            bucket[key] -= 1
            exact_hits += 1
        else:
            # Compute true distance to closest by simple scan (fallback)
            # This is only done for misses and small meshes so acceptable.
            best = None
            for m in mirrored_pts:
                d = (
                    (q[0] - m[0]) ** 2 + (q[1] - m[1]) ** 2 + (q[2] - m[2]) ** 2
                ) ** 0.5
                if best is None or d < best:
                    best = d
            if best is None:
                continue
            if best <= tol:
                near_hits += 1
            total_err += best
            max_err = max(max_err, best)
    denom = max(1, len(original_pts) - exact_hits)  # avoid zero-div
    avg_err = total_err / float(denom)
    matched_ratio = (
        (exact_hits + near_hits) / float(len(original_pts)) if original_pts else 0.0
    )
    return avg_err, max_err, matched_ratio


# ---------------------------------------------------------------------------
# Core test runner
# ---------------------------------------------------------------------------


def run(limit_cases: int = MAX_CASES, verbose: bool = True, keep_failures: bool = True):
    sel = pm.ls(sl=True, transforms=True)
    if not sel:
        pm.warning("Select a single mesh transform before running.")
        return
    source = sel[0]
    base_name = f"{source.nodeName()}__MIRRORTEST_BASE"
    base = pm.duplicate(source, rr=True, name=base_name)[0]

    cases = []
    for axis in AXES:
        for pivot in PIVOTS:
            for method in METHODS:
                for use_obj in USE_OBJECT_AXES:
                    for refl in REFLECT_NEGATIVE_PIVOT:
                        if len(cases) >= limit_cases:
                            break
                        cases.append((axis, pivot, method, use_obj, refl))

    results = []
    for axis, pivot, method, use_obj, refl in cases:
        # Duplicate fresh working copy
        work_name = f"{base_name}__{axis}_{pivot}_{method}"
        working = pm.duplicate(base, rr=True, name=work_name)[0]
        axis_idx = _axis_index(axis)
        try:
            pivot_world = XformUtils.get_operation_axis_pos(working, pivot)
        except Exception:
            pivot_world = [0.0, 0.0, 0.0]
        plane = pivot_world[axis_idx]

        # Capture original (pre-mirror) points now, before operation possibly deletes/renames working.
        try:
            orig_pts = _get_world_points(working)
        except Exception as e:
            results.append(
                {
                    "axis": axis,
                    "pivot": pivot,
                    "method": method,
                    "local": use_obj,
                    "refl": refl,
                    "error": f"orig sample fail: {e}",
                }
            )
            _safe_delete([working])
            continue

        pre_transforms = set(pm.ls(transforms=True))
        # Perform mirror
        try:
            mirror_return = EditUtils.mirror(
                working,
                axis=axis,
                pivot=pivot,
                mergeMode=-1,
                use_object_axes=use_obj,
                method=method,
                reflect_negative_pivot=refl,
            )
        except Exception as e:
            results.append(
                {
                    "axis": axis,
                    "pivot": pivot,
                    "method": method,
                    "local": use_obj,
                    "refl": refl,
                    "error": str(e),
                }
            )
            _safe_delete([working])
            continue

        post_transforms = set(pm.ls(transforms=True))
        created = [
            t for t in post_transforms - pre_transforms if t.nodeName() != work_name
        ]

        # Determine mirrored object
        mirrored_obj = None
        if isinstance(mirror_return, pm.nt.Transform):
            mirrored_obj = mirror_return
        elif isinstance(mirror_return, (list, tuple)) and mirror_return:
            # Pick first transform element
            for item in mirror_return:
                if isinstance(item, pm.nt.Transform):
                    mirrored_obj = item
                    break
        if not mirrored_obj:
            # Fallback to created transforms
            if created:
                # Prefer one whose name contains axis or pivot for heuristics
                preferred = [
                    c for c in created if axis in c.nodeName() or pivot in c.nodeName()
                ]
                mirrored_obj = preferred[0] if preferred else created[0]
        # If still none and working still exists, treat as SKIP (operation collapsed to same mesh)
        if not mirrored_obj:
            if not pm.objExists(working):
                results.append(
                    {
                        "axis": axis,
                        "pivot": pivot,
                        "method": method,
                        "local": use_obj,
                        "refl": refl,
                        "error": "No mirrored transform produced; original deleted",
                    }
                )
            else:
                results.append(
                    {
                        "axis": axis,
                        "pivot": pivot,
                        "method": method,
                        "local": use_obj,
                        "refl": refl,
                        "status": "SKIP",
                        "note": "Mirror modified in-place; cannot compare",
                    }
                )
            continue

        # Gather mirrored vertex data
        try:
            mir_pts = _get_world_points(mirrored_obj)
            avg_err, max_err, matched = _evaluate_pair(
                orig_pts, mir_pts, axis_idx, plane
            )
        except Exception as e:
            avg_err = max_err = 0.0
            matched = 0.0
            results.append(
                {
                    "axis": axis,
                    "pivot": pivot,
                    "method": method,
                    "local": use_obj,
                    "refl": refl,
                    "error": f"eval fail: {e}",
                }
            )
            _safe_delete([working, mirrored_obj])
            continue

        # Dynamic thresholds: bbox limit pivots (xmin/xmax/ymin/ymax/zmin/zmax) select half the mesh,
        # so mirrored result won't fully match original by design. Relax match ratio in those cases.
        is_limit = pivot in {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
        min_match = 0.65 if is_limit else 0.90
        status = (
            "PASS"
            if (max_err < VERT_ERROR_THRESHOLD and matched >= min_match)
            else "FAIL"
        )
        reason = "bbox-limit relaxed" if is_limit else "strict"
        results.append(
            {
                "axis": axis,
                "pivot": pivot,
                "method": method,
                "local": use_obj,
                "refl": refl,
                "plane": round(plane, 5),
                "avgErr": avg_err,
                "maxErr": max_err,
                "matched": matched,
                "status": status,
            }
        )
        if verbose:
            print(
                f"[MirrorTest] {status} axis={axis:<3} pivot={pivot:<6} method={method:<4} local={use_obj} reflNeg={refl} plane={plane:.4f} maxErr={max_err:.6f} matched={matched:.2%} ({reason})"
            )
        # cleanup operands (keep mirrored if you want to inspect on fail)
        if status == "PASS":
            _safe_delete([working, mirrored_obj])
        else:
            if keep_failures:
                print(f" -> Objects kept for inspection: {working}, {mirrored_obj}")
            else:
                _safe_delete([working, mirrored_obj])

    # Final summary
    total = len(results)
    fails = [r for r in results if r.get("status") == "FAIL" or "error" in r]
    print("\n=== Mirror Validation Summary ===")
    print(f"Total Cases: {total}  Passed: {total-len(fails)}  Failed: {len(fails)}")
    if fails:
        print("-- Failures / Errors --")
        for r in fails:
            if "error" in r:
                print(
                    f"ERROR axis={r['axis']} pivot={r['pivot']} method={r['method']} local={r['local']} refl={r['refl']}: {r['error']}"
                )
            else:
                print(
                    f"FAIL axis={r['axis']} pivot={r['pivot']} method={r['method']} local={r['local']} refl={r['refl']} maxErr={r['maxErr']:.6f} matched={r['matched']:.2%}"
                )
    print("================================")
    if not (keep_failures and any(r.get("status") == "FAIL" for r in results)):
        _safe_delete([base])
    return results


if __name__ == "__main__":
    run()
