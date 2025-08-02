# !/usr/bin/python
# coding=utf-8
from typing import List, Dict, Optional, Union
import pymel.core as pm


def unbake_animation(objects=None, threshold=0.001):
    """Unbakes keyframe animation by removing unnecessary keys:
    - Keys where the value is the same as previous and next keys
    - Keys where the value is linearly interpolated between previous and next keys within threshold

    Parameters:
        objects (list, optional): List of objects to unbake. If None, uses selection.
        threshold (float): Tolerance for determining if a key is unnecessary (default: 0.001)

    Returns:
        int: Number of keys removed
    """
    if objects is None:
        objects = pm.selected()

    if not objects:
        pm.warning("No objects selected or provided")
        return 0

    objects = pm.ls(objects)
    keys_removed = 0

    for obj in objects:
        # Get all keyable attributes
        keyable_attrs = pm.listAttr(obj, keyable=True) or []

        for attr_name in keyable_attrs:
            try:
                attr = obj.attr(attr_name)

                # Check if attribute has animation curves
                anim_curves = pm.listConnections(attr, type="animCurve")
                if not anim_curves:
                    continue

                curve = anim_curves[0]

                # Get all keyframes
                key_count = curve.numKeys()
                if key_count <= 2:
                    continue  # Need at least 3 keys to have middle keys to remove

                # Get all key times and values
                key_times = []
                key_values = []
                for i in range(key_count):
                    key_times.append(curve.getTime(i))
                    key_values.append(curve.getValue(i))

                # Find keys to remove (work backwards to maintain indices)
                keys_to_remove = []

                for i in range(key_count - 2, 0, -1):  # Skip first and last keys
                    prev_value = key_values[i - 1]
                    curr_value = key_values[i]
                    next_value = key_values[i + 1]

                    prev_time = key_times[i - 1]
                    curr_time = key_times[i]
                    next_time = key_times[i + 1]

                    # Check if current key is unnecessary
                    should_remove = False

                    # Case 1: Same value as previous and next
                    if (
                        abs(curr_value - prev_value) <= threshold
                        and abs(curr_value - next_value) <= threshold
                    ):
                        should_remove = True

                    # Case 2: Linear interpolation check
                    else:
                        # Calculate expected value if linearly interpolated
                        time_ratio = (curr_time - prev_time) / (next_time - prev_time)
                        expected_value = (
                            prev_value + (next_value - prev_value) * time_ratio
                        )

                        if abs(curr_value - expected_value) <= threshold:
                            should_remove = True

                    if should_remove:
                        keys_to_remove.append(i)

                # Remove the keys
                for key_index in keys_to_remove:
                    curve.remove(key_index)
                    keys_removed += 1

            except Exception as e:
                pm.warning(f"Error processing {obj}.{attr_name}: {str(e)}")
                continue

    print(f"Removed {keys_removed} unnecessary keyframes")
    return keys_removed


# Even simpler version - just find major direction changes
def unbake_animation_direction_based(objects=None, threshold=0.01):
    """Remove keys except where animation changes direction significantly.

    Parameters:
        objects (list, optional): List of objects to unbake. If None, uses selection.
        threshold (float): Minimum value change to consider significant (default: 0.01)
    """
    if objects is None:
        objects = pm.selected()

    if not objects:
        pm.warning("No objects selected or provided")
        return 0

    objects = pm.ls(objects)
    keys_removed = 0

    for obj in objects:
        keyable_attrs = pm.listAttr(obj, keyable=True) or []

        for attr_name in keyable_attrs:
            try:
                attr = obj.attr(attr_name)
                anim_curves = pm.listConnections(attr, type="animCurve")
                if not anim_curves:
                    continue

                curve = anim_curves[0]
                key_count = curve.numKeys()
                if key_count <= 2:
                    continue

                # Find keys to keep
                keys_to_keep = set([0, key_count - 1])  # Always keep first and last

                for i in range(1, key_count - 1):
                    prev_val = curve.getValue(i - 1)
                    curr_val = curve.getValue(i)
                    next_val = curve.getValue(i + 1)

                    # Keep if significant direction change
                    delta1 = curr_val - prev_val
                    delta2 = next_val - curr_val

                    if (
                        abs(delta1) > threshold
                        and abs(delta2) > threshold
                        and ((delta1 > 0 and delta2 < 0) or (delta1 < 0 and delta2 > 0))
                    ):
                        keys_to_keep.add(i)

                # Remove keys not in keep list (work backwards)
                for i in range(key_count - 1, -1, -1):
                    if i not in keys_to_keep:
                        curve.remove(i)
                        keys_removed += 1

            except Exception as e:
                pm.warning(f"Error processing {obj}.{attr_name}: {str(e)}")
                continue

    print(f"Removed {keys_removed} unnecessary keyframes")
    return keys_removed


def unbake_animation_smart(objects=None, threshold=0.001):
    """Smart unbaking - identifies animation segments and preserves only essential keys:
    - Keys where animation changes direction (local min/max)
    - Keys where animation starts/stops (value changes begin/end)
    - First and last keys

    Parameters:
        objects (list, optional): List of objects to unbake. If None, uses selection.
        threshold (float): Minimum value change to consider significant (default: 0.001)

    Returns:
        int: Number of keys removed
    """
    if objects is None:
        objects = pm.selected()

    if not objects:
        pm.warning("No objects selected or provided")
        return 0

    objects = pm.ls(objects)
    keys_removed = 0

    for obj in objects:
        keyable_attrs = pm.listAttr(obj, keyable=True) or []

        for attr_name in keyable_attrs:
            try:
                attr = obj.attr(attr_name)
                anim_curves = pm.listConnections(attr, type="animCurve")
                if not anim_curves:
                    continue

                curve = anim_curves[0]
                key_count = curve.numKeys()
                if key_count <= 2:
                    continue

                print(f"Analyzing {obj}.{attr_name} with {key_count} keys")

                # Get all key data
                keys_data = []
                for i in range(key_count):
                    keys_data.append(
                        {
                            "index": i,
                            "time": curve.getTime(i),
                            "value": curve.getValue(i),
                        }
                    )

                # Find essential keys to preserve
                essential_keys = _find_essential_keys(keys_data, threshold)

                # Remove non-essential keys (work backwards)
                keys_to_remove = []
                for i in range(key_count - 1, -1, -1):
                    if i not in essential_keys:
                        keys_to_remove.append(i)

                print(
                    f"  Keeping {len(essential_keys)} essential keys: {sorted(essential_keys)}"
                )
                print(f"  Removing {len(keys_to_remove)} keys")

                # Remove the keys
                for key_index in keys_to_remove:
                    curve.remove(key_index)
                    keys_removed += 1

            except Exception as e:
                pm.warning(f"Error processing {obj}.{attr_name}: {str(e)}")
                continue

    print(f"Removed {keys_removed} unnecessary keyframes")
    return keys_removed


def _find_essential_keys(keys_data, threshold):
    """Identify which keys are essential for preserving animation shape.

    Parameters:
        keys_data (list): List of key data dictionaries with 'index', 'time', 'value'.
        threshold (float): Minimum value change to consider significant.
    """
    if len(keys_data) <= 2:
        return [0, len(keys_data) - 1]

    essential = set()

    # Always keep first and last keys
    essential.add(0)
    essential.add(len(keys_data) - 1)

    # Find direction changes and significant value changes
    for i in range(1, len(keys_data) - 1):
        prev_key = keys_data[i - 1]
        curr_key = keys_data[i]
        next_key = keys_data[i + 1]

        prev_value = prev_key["value"]
        curr_value = curr_key["value"]
        next_value = next_key["value"]

        # Calculate value deltas
        delta1 = curr_value - prev_value
        delta2 = next_value - curr_value

        # Keep key if it's a direction change (sign change in deltas)
        if abs(delta1) > threshold and abs(delta2) > threshold:
            if (delta1 > 0 and delta2 < 0) or (delta1 < 0 and delta2 > 0):
                essential.add(i)
                print(
                    f"    Direction change at key {i}: {prev_value:.3f} -> {curr_value:.3f} -> {next_value:.3f}"
                )
                continue

        # Keep key if animation starts moving (was static, now changing)
        if abs(delta1) <= threshold and abs(delta2) > threshold:
            essential.add(i)
            print(f"    Animation start at key {i}: {curr_value:.3f}")
            continue

        # Keep key if animation stops moving (was changing, now static)
        if abs(delta1) > threshold and abs(delta2) <= threshold:
            essential.add(i)
            print(f"    Animation stop at key {i}: {curr_value:.3f}")
            continue

    # Find holds (sequences of identical values) and keep only first and last
    _preserve_hold_boundaries(keys_data, essential, threshold)

    return essential


def _preserve_hold_boundaries(keys_data, essential, threshold):
    """For sequences of identical values (holds), keep only the first and last keys.

    Parameters:
        keys_data (list): List of key data dictionaries with 'index', 'time', 'value'.
        essential (set): Set of indices of essential keys to preserve.
        threshold (float): Minimum value change to consider significant.
    """
    i = 0
    while i < len(keys_data):
        # Find start of a potential hold
        hold_start = i
        hold_value = keys_data[i]["value"]

        # Find end of hold
        while (
            i + 1 < len(keys_data)
            and abs(keys_data[i + 1]["value"] - hold_value) <= threshold
        ):
            i += 1

        hold_end = i

        # If we found a hold of 3+ keys, keep only start and end
        if hold_end - hold_start >= 2:
            essential.add(hold_start)
            essential.add(hold_end)
            print(f"    Hold from key {hold_start} to {hold_end}: {hold_value:.3f}")

        i += 1


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    objects = pm.selected()
    keys_removed = unbake_animation_smart(objects, threshold=0.01)
    print(f"Smart unbaking complete. Removed {keys_removed} keys.")

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
