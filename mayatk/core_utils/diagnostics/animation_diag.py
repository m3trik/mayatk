# !/usr/bin/python
# coding=utf-8
"""Animation-curve diagnostics and optional repair helpers."""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence, Union
import math

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)

from mayatk.core_utils._core_utils import CoreUtils

PyNodeLike = Union[str, "pm.PyNode"]


class AnimCurveDiagnostics:
    """Utilities for detecting and resolving common animation-curve issues."""

    @classmethod
    @CoreUtils.undoable
    def repair_corrupted_curves(
        cls,
        objects: Optional[Union[PyNodeLike, Sequence[PyNodeLike]]] = None,
        recursive: bool = True,
        delete_corrupted: bool = False,
        fix_infinite: bool = True,
        fix_invalid_times: bool = True,
        time_range_threshold: float = 1e6,
        value_threshold: float = 1e6,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Detect and (optionally) repair corrupted animation curves."""

        return cls._repair_corrupted_curves(
            objects=objects,
            recursive=recursive,
            delete_corrupted=delete_corrupted,
            fix_infinite=fix_infinite,
            fix_invalid_times=fix_invalid_times,
            time_range_threshold=time_range_threshold,
            value_threshold=value_threshold,
            quiet=quiet,
        )

    @classmethod
    @CoreUtils.undoable
    def repair_visibility_tangents(
        cls,
        objects: Optional[Union[PyNodeLike, Sequence[PyNodeLike]]] = None,
        recursive: bool = True,
        quiet: bool = False,
    ) -> int:
        """
        Repair visibility animation curves by forcing 'step' tangents.

        Visibility attributes (boolean/enum) should always use stepped tangents to avoid
        interpolation artifacts (e.g. visibility=0.5).

        Args:
            objects: Specific objects to check. If None, checks all curves in scene.
            recursive: Whether to check hierarchy if objects are transforms.
            quiet: Suppress output messages.

        Returns:
            int: Number of curves repaired.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        # Collect all animation curves
        all_curves = cls._collect_anim_curves(objects, recursive)

        if not all_curves:
            if not quiet:
                pm.warning("No animation curves found.")
            return 0

        # Filter for visibility curves using AnimUtils helper
        vis_curves, _ = AnimUtils._get_visibility_curves(all_curves)

        if not vis_curves:
            if not quiet:
                print("No visibility curves found.")
            return 0

        count = 0
        for curve in vis_curves:
            try:
                # Force step tangents (apply to both sides to avoid interpolation artifacts)
                pm.keyTangent(
                    curve,
                    edit=True,
                    outTangentType="step",
                    inTangentType="step",
                )
                count += 1
            except Exception as e:
                if not quiet:
                    print(f"Failed to repair {curve}: {e}")

        if not quiet:
            print(f"Repaired tangents on {count} visibility curves.")

        return count

    @classmethod
    def _repair_corrupted_curves(
        cls,
        objects: Optional[Union[PyNodeLike, Sequence[PyNodeLike]]] = None,
        recursive: bool = True,
        delete_corrupted: bool = False,
        fix_infinite: bool = True,
        fix_invalid_times: bool = True,
        time_range_threshold: float = 1e6,
        value_threshold: float = 1e6,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Internal implementation shared by public API and legacy wrappers."""

        anim_curves = cls._collect_anim_curves(objects, recursive)

        if not anim_curves:
            if not quiet:
                pm.warning("No animation curves found to check.")
            return {
                "corrupted_found": 0,
                "curves_repaired": 0,
                "curves_deleted": 0,
                "keys_fixed": 0,
                "details": [],
            }

        stats: Dict[str, Any] = {
            "corrupted_found": 0,
            "curves_repaired": 0,
            "curves_deleted": 0,
            "keys_fixed": 0,
            "details": [],
        }

        if not quiet:
            print(
                f"[diagnostic] Checking {len(anim_curves)} animation curves for corruption..."
            )

        for curve in anim_curves:
            try:
                if not pm.objExists(curve):
                    continue

                curve_name = str(curve)
                key_count = pm.keyframe(curve, query=True, keyframeCount=True)

                if not key_count:
                    continue

                try:
                    times = pm.keyframe(curve, query=True, timeChange=True) or []
                    values = pm.keyframe(curve, query=True, valueChange=True) or []
                except RuntimeError as exc:
                    stats["corrupted_found"] += 1
                    stats["details"].append(
                        f"SEVERE CORRUPTION: {curve_name} - Cannot query keyframes: {str(exc)}"
                    )

                    if delete_corrupted:
                        try:
                            pm.delete(curve)
                            stats["curves_deleted"] += 1
                            if not quiet:
                                print(
                                    f"  [!] Deleted severely corrupted curve: {curve_name}"
                                )
                        except Exception:
                            pass
                    continue

                if not times or not values:
                    continue

                is_corrupted = False
                corruption_reasons: List[str] = []
                keys_to_fix: List[int] = []

                if fix_invalid_times:
                    for idx, time in enumerate(times):
                        if math.isnan(time) or math.isinf(time):
                            is_corrupted = True
                            corruption_reasons.append(f"NaN/Inf time at key {idx}")
                            keys_to_fix.append(idx)
                        elif abs(time) > time_range_threshold:
                            is_corrupted = True
                            corruption_reasons.append(
                                f"Invalid time range ({time}) at key {idx}"
                            )
                            keys_to_fix.append(idx)

                if fix_infinite:
                    for idx, value in enumerate(values):
                        if math.isnan(value) or math.isinf(value):
                            is_corrupted = True
                            corruption_reasons.append(f"NaN/Inf value at key {idx}")
                            if idx not in keys_to_fix:
                                keys_to_fix.append(idx)
                        elif abs(value) > value_threshold:
                            is_corrupted = True
                            corruption_reasons.append(
                                f"Extreme value ({value}) at key {idx}"
                            )
                            if idx not in keys_to_fix:
                                keys_to_fix.append(idx)

                if is_corrupted:
                    stats["corrupted_found"] += 1
                    reason_str = ", ".join(corruption_reasons)
                    stats["details"].append(f"CORRUPTED: {curve_name} - {reason_str}")

                    if not quiet:
                        print(f"  [!] Found corruption in {curve_name}:")
                        for reason in corruption_reasons:
                            print(f"      - {reason}")

                    if keys_to_fix:
                        repaired = cls._repair_curve_keys(
                            curve,
                            keys_to_fix,
                            times,
                            values,
                            time_range_threshold,
                            value_threshold,
                            quiet,
                        )

                        if repaired:
                            stats["curves_repaired"] += 1
                            stats["keys_fixed"] += len(keys_to_fix)
                            if not quiet:
                                print(
                                    f"  [✓] Repaired {len(keys_to_fix)} keys in {curve_name}"
                                )
                        elif delete_corrupted:
                            try:
                                pm.delete(curve)
                                stats["curves_deleted"] += 1
                                if not quiet:
                                    print(
                                        f"  [✗] Deleted unrepairable curve: {curve_name}"
                                    )
                            except Exception:
                                if not quiet:
                                    print(f"  [✗] Could not delete curve: {curve_name}")

            except Exception as exc:
                if not quiet:
                    pm.warning(f"Error processing curve {curve}: {str(exc)}")
                continue

        if not quiet:
            print("\n[diagnostic] === Animation Curve Summary ===")
            print(f"  Curves checked: {len(anim_curves)}")
            print(f"  Corrupted found: {stats['corrupted_found']}")
            print(f"  Curves repaired: {stats['curves_repaired']}")
            print(f"  Curves deleted: {stats['curves_deleted']}")
            print(f"  Keys fixed: {stats['keys_fixed']}")

            if stats["corrupted_found"] > 0 and not delete_corrupted:
                print("\n  TIP: Use delete_corrupted=True to remove unfixable curves")

        return stats

    @staticmethod
    def _collect_anim_curves(
        objects: Optional[Union[PyNodeLike, Sequence[PyNodeLike]]],
        recursive: bool,
    ) -> List["pm.PyNode"]:
        if objects is None:
            return pm.ls(type="animCurve")

        targets = pm.ls(objects, flatten=True)
        anim_curves = set()

        for target in targets:
            if not pm.objExists(target):
                continue

            try:
                if pm.nodeType(target).startswith("animCurve"):
                    anim_curves.add(target)
                    continue
            except Exception:
                continue

            connected = pm.listConnections(target, type="animCurve", s=True, d=False)
            if connected:
                anim_curves.update(connected)

            if recursive:
                descendants = (
                    pm.listRelatives(target, allDescendents=True, type="transform")
                    or []
                )
                for descendant in descendants:
                    connected_desc = pm.listConnections(
                        descendant, type="animCurve", s=True, d=False
                    )
                    if connected_desc:
                        anim_curves.update(connected_desc)

        return list(anim_curves)

    @staticmethod
    def _repair_curve_keys(
        curve: "pm.PyNode",
        keys_to_fix: List[int],
        times: List[float],
        values: List[float],
        time_threshold: float,
        value_threshold: float,
        quiet: bool = False,
    ) -> bool:
        """Attempt to remediate or delete specific corrupted keys on a curve."""

        try:
            for key_idx in sorted(keys_to_fix, reverse=True):
                time = times[key_idx]
                value = values[key_idx]

                should_delete = False

                if math.isnan(time) or math.isinf(time) or abs(time) > time_threshold:
                    should_delete = True

                if (
                    math.isnan(value)
                    or math.isinf(value)
                    or abs(value) > value_threshold
                ):
                    should_delete = True

                if should_delete:
                    try:
                        pm.cutKey(curve, index=(key_idx, key_idx), option="keys")
                    except Exception:
                        try:
                            if not math.isnan(time) and not math.isinf(time):
                                pm.cutKey(curve, time=(time, time), option="keys")
                        except Exception:
                            return False

            try:
                remaining_times = pm.keyframe(curve, query=True, timeChange=True) or []
                remaining_values = (
                    pm.keyframe(curve, query=True, valueChange=True) or []
                )

                for t_value in remaining_times:
                    if (
                        math.isnan(t_value)
                        or math.isinf(t_value)
                        or abs(t_value) > time_threshold
                    ):
                        return False

                for v_value in remaining_values:
                    if (
                        math.isnan(v_value)
                        or math.isinf(v_value)
                        or abs(v_value) > value_threshold
                    ):
                        return False

                return True

            except Exception:
                return False

        except Exception as exc:
            if not quiet:
                pm.warning(f"Failed to repair curve keys: {str(exc)}")
            return False
