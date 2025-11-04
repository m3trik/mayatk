# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Dict, ClassVar, Optional, Union, Any

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils


class AnimUtils(ptk.HelpMixin):
    """Animation utilities for Maya.

    For help on this class use: AnimUtils.help()

    BEST PRACTICES FOR GETTING ANIMATION CURVES:
    ============================================

    When working with animation curves, use these methods to ensure you capture ALL curve types
    (including visibility, custom attributes, etc.):

    1. For simple object-to-curves conversion:
       curves = AnimUtils.objects_to_curves(objects, recursive=False)

    2. For common patterns (scene curves, selected keys, object curves):
       curves = AnimUtils.get_anim_curves(objects=None, selected_keys_only=False, recursive=False)

    3. Both methods use pm.listConnections(type="animCurve") which properly captures all curve types.

    AVOID querying keyframes at the object level for curve operations:
       - pm.keyframe(obj, query=True, timeChange=True) # May miss some attributes

    PREFERRED approach - work with curves directly:
       - Get curves first using objects_to_curves() or get_anim_curves()
       - Then query/modify the curves: pm.keyframe(curve, query=True, timeChange=True)
    """

    # Map frame rate types to their numerical values
    FRAME_RATE_VALUES: ClassVar[Dict[str, int]] = {
        "game": 15,
        "film": 24,
        "pal": 25,
        "ntsc": 30,
        "show": 48,
        "palf": 50,
        "ntscf": 60,
    }

    @staticmethod
    def objects_to_curves(
        objects: Union["pm.PyNode", str, List[Union["pm.PyNode", str]]],
        recursive: bool = False,
    ) -> List["pm.PyNode"]:
        """Converts objects into a list of animation curves.
        Optionally recurses through the objects to find animation curves on children.
        Ensures no duplicates are returned.

        Parameters:
            objects: Single object, string, or list of objects (can be keyed objects or curves).
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of unique animation curves.
        """
        # Use pm.ls to handle various forms of input (single object, string, list)
        objects = pm.ls(objects, flatten=True)
        anim_curves = set()  # Use a set to ensure no duplicates

        for obj in objects:
            # Early return if obj doesn't exist
            if not pm.objExists(obj):
                continue

            # If the object is an animCurve, add it directly
            if pm.nodeType(obj).startswith("animCurve"):
                anim_curves.add(obj)
            else:  # Otherwise, list connections for animCurves of the object
                connected_curves = pm.listConnections(
                    obj, type="animCurve", s=True, d=False
                )
                if connected_curves:
                    anim_curves.update(connected_curves)

                # If recursive, look through all descendants for animation curves
                if recursive:
                    descendants = (
                        pm.listRelatives(obj, allDescendents=True, type="transform")
                        or []
                    )
                    for desc in descendants:
                        connected_curves = pm.listConnections(
                            desc, type="animCurve", s=True, d=False
                        )
                        if connected_curves:
                            anim_curves.update(connected_curves)

        # Return the results as a list, preserving the unique set of animCurves
        return list(anim_curves)

    @classmethod
    def get_anim_curves(
        cls,
        objects: Optional[List["pm.PyNode"]] = None,
        selected_keys_only: bool = False,
        recursive: bool = False,
    ) -> List["pm.PyNode"]:
        """Get animation curves from objects, selected keys, or all scene curves.

        This is a higher-level convenience method that handles common patterns for getting
        animation curves. It properly handles visibility and all other attribute types by
        working directly with animation curve nodes rather than querying at the object level.

        This method should be used when you need to:
        - Get all curves in a scene
        - Get curves from selected graph editor keys
        - Get curves from specific objects (with optional recursion)

        Parameters:
            objects: Objects to get curves from. If None, uses all scene curves or selected keys.
            selected_keys_only: If True, gets curves from selected keys in graph editor.
                               Only applies when objects is None.
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of unique animation curves.

        Example:
            # Get all animation curves in the scene
            all_curves = AnimUtils.get_anim_curves()

            # Get curves from selected keys
            selected_curves = AnimUtils.get_anim_curves(selected_keys_only=True)

            # Get curves from specific objects
            curves = AnimUtils.get_anim_curves(objects=pm.selected())

            # Get curves from objects and their children
            curves = AnimUtils.get_anim_curves(objects=pm.selected(), recursive=True)
        """
        if objects is None:
            if selected_keys_only:
                # Get animation curves from selected keys in graph editor
                anim_curves = pm.keyframe(query=True, sl=True, name=True)
                return list(set(anim_curves)) if anim_curves else []
            else:
                # Get all animation curves in the scene
                return pm.ls(type="animCurve")
        else:
            # Use existing objects_to_curves method for objects
            # This uses pm.listConnections which properly gets ALL curve types including visibility
            return cls.objects_to_curves(objects, recursive=recursive)

    @classmethod
    def get_static_curves(
        cls,
        objects: List["pm.PyNode"],
        value_tolerance: float = 1e-5,
        recursive: bool = False,
    ) -> List["pm.PyNode"]:
        """Detects static curves (curves with constant values).

        Parameters:
            objects: List of PyNodes (curves or objects).
            value_tolerance: The value tolerance to consider for static curves (difference between keyframe values).
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of static curves.
        """
        from math import isclose

        curves = cls.objects_to_curves(objects, recursive=recursive)
        static_curves = []

        for curve in curves:
            values = pm.keyframe(curve, query=True, valueChange=True)
            if not values or len(values) <= 1:
                continue

            # Check if the curve is static (all values are the same)
            if all(isclose(v, values[0], abs_tol=value_tolerance) for v in values):
                static_curves.append(curve)

        return static_curves

    @classmethod
    @CoreUtils.undoable
    def get_redundant_flat_keys(
        cls,
        objects: List["pm.PyNode"],
        value_tolerance: float = 1e-5,
        remove: bool = False,
        recursive: bool = False,
    ) -> List[Tuple[float, float]]:
        """Detects redundant flat keys in curves and optionally deletes them.

        Parameters:
            objects: List of PyNodes (curves or objects).
            value_tolerance: The value tolerance to consider for redundant flat keys.
            remove: If True, the redundant keys are deleted.
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of redundant key ranges as tuples (start_time, end_time).
        """
        import numpy as np

        curves = cls.objects_to_curves(objects, recursive=recursive)
        redundant = []

        for curve in curves:
            times = pm.keyframe(curve, query=True, timeChange=True) or []
            if len(times) < 3:
                continue

            # Use actual keyframe values instead of curve.evaluate() to avoid interpolation issues
            values = pm.keyframe(curve, query=True, valueChange=True) or []
            if len(values) != len(times):
                continue  # Safety check

            values = np.array(values)
            remove_indices = []

            # Find internal flat segments (at least three consecutive identical values)
            i = 1
            while i < len(values) - 1:
                if (
                    abs(values[i] - values[i - 1]) < value_tolerance
                    and abs(values[i] - values[i + 1]) < value_tolerance
                ):
                    # Found a potential flat segment, find its full extent
                    start = i - 1
                    end = i + 1

                    # Extend the flat segment as far as possible
                    while (
                        end < len(values)
                        and abs(values[end] - values[start]) < value_tolerance
                    ):
                        end += 1

                    # Only remove internal keys if we have at least 3 consecutive identical values
                    # and preserve the first and last key of the flat segment
                    if end - start >= 3:  # At least 3 keys in the flat segment
                        remove_indices.extend(list(range(start + 1, end - 1)))

                    i = end
                else:
                    i += 1

            # Remove keys at the identified times
            removed_times = []
            if remove and remove_indices:
                # Remove in reverse order to avoid index shifting issues
                for idx in sorted(set(remove_indices), reverse=True):
                    pm.cutKey(curve, time=(times[idx], times[idx]), option="keys")
                    removed_times.append(times[idx])

            if remove_indices:
                redundant.append((curve, [times[idx] for idx in remove_indices]))

        return redundant

    @classmethod
    def simplify_curve(
        cls,
        objects: List["pm.PyNode"],
        value_tolerance: float = 1e-5,
        time_tolerance: float = 1e-5,
        recursive: bool = False,
    ) -> List["pm.PyNode"]:
        """Simplifies curves by removing unnecessary keyframes.

        Parameters:
            objects: List of PyNodes (curves or objects).
            value_tolerance: The value tolerance for simplification.
            time_tolerance: The time tolerance for simplification.
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of simplified curves.
        """
        curves = cls.objects_to_curves(objects, recursive=recursive)
        simplified_curves = []

        for curve in curves:
            plugs = pm.listConnections(
                curve, plugs=True, source=False, destination=True
            )
            if plugs and pm.objExists(plugs[0].node()):
                pm.simplify(
                    plugs[0],
                    valueTolerance=value_tolerance,
                    timeTolerance=time_tolerance,
                )
                simplified_curves.append(curve)

        return simplified_curves

    @classmethod
    @CoreUtils.undoable
    def optimize_keys(
        cls,
        objects: Union[str, "pm.PyNode", List[Union[str, "pm.PyNode"]]],
        value_tolerance: float = 0.001,
        time_tolerance: float = 0.001,
        remove_flat_keys: bool = True,
        remove_static_curves: bool = True,
        simplify_keys: bool = False,
        recursive: bool = True,
        quiet: bool = False,
    ) -> List["pm.PyNode"]:
        """Optimize animation keys for the given objects by removing static curves,
        redundant flat keys, and simplifying curves.

        Parameters:
            objects (str, PyNode, or list): The objects to optimize.
            value_tolerance (float): Tolerance for value comparison.
            time_tolerance (float): Tolerance for time comparison.
            remove_flat_keys (bool): Whether to remove redundant flat keys.
            remove_static_curves (bool): Whether to remove static curves.
            simplify_keys (bool): Whether to simplify curves.
            recursive (bool): Whether to search through children of objects.
            quiet (bool): If True, suppress output messages.

        Returns:
            list: A list of modified curves.
        """
        modified = []
        static_curves_deleted = 0
        flat_keys_deleted = 0
        simplified_curves = 0

        # Convert the input objects into curves (avoid duplicates)
        targets = pm.ls(objects, flatten=True)
        anim_curves = cls.objects_to_curves(targets, recursive=recursive)

        if not quiet:
            print(f"[optimize] Processing {len(anim_curves)} curves...")

        # Phase 1: Remove static curves (if remove_static_curves is True)
        if remove_static_curves:
            static_curves = cls.get_static_curves(
                anim_curves, value_tolerance=value_tolerance
            )
            static_curves_deleted += len(static_curves)
            for curve in static_curves:
                anim_curves.remove(curve)  # Remove deleted curve from anim_curves
                pm.delete(curve)

        # Phase 2: Remove redundant flat keys (if remove_flat_keys is True)
        if remove_flat_keys:
            redundant_keys_to_delete = cls.get_redundant_flat_keys(
                anim_curves, value_tolerance=value_tolerance, remove=True
            )
            flat_keys_deleted += sum(len(keys) for _, keys in redundant_keys_to_delete)

        # Phase 3: Simplify curves (if simplify_keys is True)
        if simplify_keys:
            cls.simplify_curve(
                anim_curves,
                value_tolerance=value_tolerance,
                time_tolerance=time_tolerance,
            )
            simplified_curves += len(anim_curves)

        modified.extend(anim_curves)

        if not quiet:
            print(f"[optimize] → {static_curves_deleted} static curves deleted")
            print(f"[optimize] → {flat_keys_deleted} flat keys removed")
            print(f"[optimize] → {simplified_curves} curves simplified")

        return modified

    @staticmethod
    def get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]:
        """Get tangent information (in and out angles and weights) for a given attribute at a specific time.

        Parameters:
            attr_name (str): The name of the attribute.
            time (float): The time at which to query the tangent information.

        Returns:
            Dict[str, Any]: A dictionary containing tangent information.
        """
        return {
            "inAngle": pm.keyTangent(attr_name, query=True, time=(time,), inAngle=True)[
                0
            ],
            "outAngle": pm.keyTangent(
                attr_name, query=True, time=(time,), outAngle=True
            )[0],
            "inWeight": pm.keyTangent(
                attr_name, query=True, time=(time,), inWeight=True
            )[0],
            "outWeight": pm.keyTangent(
                attr_name, query=True, time=(time,), outWeight=True
            )[0],
        }

    @classmethod
    def format_frame_rate_str(cls, key: str) -> str:
        """Formats and returns a user-friendly frame rate description based on the internal key.

        Parameters:
            key (str): The internal frame rate key.

        Returns:
            str: A formatted frame rate string for display.
        """
        value = cls.FRAME_RATE_VALUES.get(key, None)
        if value is None:
            return "Unknown Frame Rate"
        else:
            return f"{value} fps {key.upper()}"

    @staticmethod
    def set_current_frame(time=1, update=True, relative=False):
        """Set the current frame on the timeslider.

        Parameters:
        time (int): The desired frame number.
        update (bool): Change the current time, but do not update the world. (default=True)
        relative (bool): If True; the frame will be moved relative to
                    it's current position using the frame value as a move amount.
        Example:
            set_current_frame(24, relative=True, update=1)
        """
        currentTime = 0
        if relative:
            currentTime = pm.currentTime(query=True)

        pm.currentTime(currentTime + time, edit=True, update=update)

    @staticmethod
    @CoreUtils.undoable
    def move_keys_to_frame(
        objects=None,
        frame=None,
        time_range=None,
        selected_keys_only=False,
        retain_spacing=False,
        channel_box_attrs_only=False,
    ):
        """Move keyframes to the given frame with comprehensive control options.

        Parameters:
            objects (list, optional): Objects to move keys for. If None, uses selection.
            frame (int or float, optional): The frame to move keys to.
                                                   If None, uses the current time.
            time_range (tuple, optional): (start_frame, end_frame) to limit which keys to move.
                                         If None, moves all keys.
            selected_keys_only (bool): If True, only moves selected keys from the graph editor.
                                 If False, moves all keys in the specified time range.
            retain_spacing (bool): If True, maintains relative spacing between objects.
                                   If False, moves each object's first key to the target frame.
            channel_box_attrs_only (bool): If True, only affects attributes selected in the channel box.
                                    Works in combination with selected_keys_only.
        Returns:
            bool: True if keys were moved successfully, False otherwise.
        """
        # Get target frame (use current time if not specified)
        if frame is None:
            frame = pm.currentTime(query=True)

        # Get objects to work with
        if objects is None:
            objects = pm.selected()

        if not objects:
            pm.warning("No objects specified or selected.")
            return False

        objects = pm.ls(objects)  # Ensure we have PyMel objects

        # Get channel box attributes if filtering is requested
        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )
            if not channel_box_attrs:
                pm.warning("No attributes selected in channel box.")
                return False

        # If working with selected keys, validate there are selected keys
        if selected_keys_only:
            all_active_key_times = pm.keyframe(query=True, sl=True, tc=True)
            if not all_active_key_times:
                pm.warning("No keyframes selected.")
                return False

        keys_moved = 0

        # If maintaining spacing, calculate the global offset from the earliest key across all objects
        global_offset = None
        if retain_spacing:
            earliest_key_time = None

            # Find the earliest key across all objects
            for obj in objects:
                if selected_keys_only:
                    keys = pm.keyframe(obj, query=True, name=True, sl=True)
                    for node in keys:
                        if time_range:
                            active_key_times = pm.keyframe(
                                node, query=True, sl=True, tc=True, time=time_range
                            )
                        else:
                            active_key_times = pm.keyframe(
                                node, query=True, sl=True, tc=True
                            )

                        if active_key_times:
                            obj_earliest = min(active_key_times)
                            if (
                                earliest_key_time is None
                                or obj_earliest < earliest_key_time
                            ):
                                earliest_key_time = obj_earliest
                else:
                    if time_range:
                        keys = pm.keyframe(
                            obj, query=True, timeChange=True, time=time_range
                        )
                    else:
                        keys = pm.keyframe(obj, query=True, timeChange=True)

                    if keys:
                        obj_earliest = min(keys)
                        if (
                            earliest_key_time is None
                            or obj_earliest < earliest_key_time
                        ):
                            earliest_key_time = obj_earliest

            if earliest_key_time is not None:
                global_offset = frame - earliest_key_time

        for obj in objects:
            # Get keyframes based on selection preference and time range
            if selected_keys_only:
                # Use AnimUtils pattern for selected keys
                keys = pm.keyframe(obj, query=True, name=True, sl=True)

                # Filter by channel box attributes if requested
                if channel_box_attrs_only:
                    filtered_keys = []
                    for node in keys:
                        # Get the attribute name from the curve node
                        connections = pm.listConnections(
                            node, plugs=True, destination=True, source=False
                        )
                        if connections:
                            attr_name = connections[0].attrName()
                            if attr_name in channel_box_attrs:
                                filtered_keys.append(node)
                    keys = filtered_keys

                for node in keys:
                    if time_range:
                        active_key_times = pm.keyframe(
                            node, query=True, sl=True, tc=True, time=time_range
                        )
                    else:
                        active_key_times = pm.keyframe(
                            node, query=True, sl=True, tc=True
                        )

                    if active_key_times:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            first_key_time = min(active_key_times)
                            offset = frame - first_key_time

                        # Move keys using AnimUtils pattern
                        pm.keyframe(
                            node,
                            edit=True,
                            time=(min(active_key_times), max(active_key_times)),
                            relative=True,
                            timeChange=offset,
                        )
                        keys_moved += len(active_key_times)
            else:
                # Handle all keys in time range
                if channel_box_attrs_only:
                    # Move keys only for channel box attributes
                    for attr in channel_box_attrs:
                        if not obj.hasAttr(attr):
                            continue

                        attr_name = f"{obj}.{attr}"
                        if time_range:
                            keys = pm.keyframe(
                                attr_name, query=True, timeChange=True, time=time_range
                            )
                        else:
                            keys = pm.keyframe(attr_name, query=True, timeChange=True)

                        if keys:
                            # Calculate offset - use global offset if maintaining spacing
                            if retain_spacing and global_offset is not None:
                                offset = global_offset
                            else:
                                first_key_time = min(keys)
                                offset = frame - first_key_time

                            # Move the keys
                            if time_range:
                                pm.keyframe(
                                    attr_name,
                                    edit=True,
                                    time=time_range,
                                    relative=True,
                                    timeChange=offset,
                                )
                            else:
                                pm.keyframe(
                                    attr_name,
                                    edit=True,
                                    relative=True,
                                    timeChange=offset,
                                )
                            keys_moved += len(keys)
                else:
                    # Move all keys on object
                    if time_range:
                        keys = pm.keyframe(
                            obj, query=True, timeChange=True, time=time_range
                        )
                    else:
                        keys = pm.keyframe(obj, query=True, timeChange=True)

                    if keys:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            first_key_time = min(keys)
                            offset = frame - first_key_time

                        # Move the keys using moveKey
                        if time_range:
                            pm.moveKey(obj, time=time_range, timeSlice=offset)
                        else:
                            pm.moveKey(obj, timeSlice=offset)

                        keys_moved += len(keys)

        if keys_moved > 0:
            selection_type = "selected" if selected_keys_only else "all"
            range_info = f" in range {time_range}" if time_range else ""
            spacing_info = " (maintaining relative spacing)" if retain_spacing else ""
            pm.displayInfo(
                f"Moved {keys_moved} {selection_type} keys to frame {frame}{range_info}{spacing_info}"
            )
            return True
        else:
            pm.warning("No keyframes found to move.")
            return False

    @staticmethod
    @CoreUtils.undoable
    def set_keys_for_attributes(
        objects, target_times=None, refresh_channel_box=False, **kwargs
    ):
        """Sets keyframes for the specified attributes on given objects at given times.

        Automatically detects whether to apply the same values to all objects (shared mode)
        or different values per object (per-object mode) based on the data structure.

        Parameters:
            objects (list): The objects to set the keyframes on.
            target_times (int/list, optional): Frame(s) to set keys at. Default: current time.
            refresh_channel_box (bool, optional): Update channel box after setting keys. Default: False.
            **kwargs: Can be used in two modes:

                SHARED MODE - Same values to all objects:
                    Attribute names as keys with their values.

                PER-OBJECT MODE - Different values per object:
                    Pass the per-object dictionary unpacked. The function auto-detects this mode when
                    the first kwarg value is a dict containing attribute/value pairs.
                    Format when unpacking: {obj_name: {attr: value, ...}, ...}

        Example:
            # Shared mode - same values to all objects
            set_keys_for_attributes([obj1, obj2], translateX=5, translateY=10)
            set_keys_for_attributes(objects, target_times=[10, 15, 20], translateX=5)

            # Per-object mode - different values per object (auto-detected)
            data = {'pCube1': {'translateX': 5.0}, 'pCube2': {'translateX': 10.0}}
            set_keys_for_attributes([obj1, obj2], **data)

            # With times and refresh
            set_keys_for_attributes(objects, target_times=10, refresh_channel_box=True, translateX=5)
        """
        if target_times is None:
            target_times = [pm.currentTime(query=True)]
        elif isinstance(target_times, int):
            target_times = [target_times]

        # Auto-detect per-object mode: if first remaining kwarg value is a dict of attributes
        per_object_mode = False
        if kwargs:
            first_value = next(iter(kwargs.values()))
            # Per-object mode if the value is a dict (and likely contains attribute mappings)
            if isinstance(first_value, dict):
                per_object_mode = True

        if per_object_mode:
            # Per-object mode: Each object gets its specific attribute values
            # kwargs structure: {obj_name: {attr: value, ...}, ...}
            per_object_data = kwargs

            for obj in pm.ls(objects):
                obj_name = str(obj)

                # Try to find matching stored data
                obj_attrs = per_object_data.get(obj_name)

                if not obj_attrs:
                    # Try short name if full path didn't match
                    short_name = obj.nodeName()
                    obj_attrs = per_object_data.get(short_name)

                    # Try matching stored short names against current long name
                    if not obj_attrs:
                        for stored_name in per_object_data.keys():
                            if stored_name.split("|")[-1] == short_name:
                                obj_attrs = per_object_data.get(stored_name)
                                break

                if obj_attrs:
                    for attr, value in obj_attrs.items():
                        attr_full_name = f"{obj}.{attr}"
                        for time in target_times:
                            pm.setKeyframe(attr_full_name, time=time, value=value)
        else:
            # Shared mode: All objects get the same attribute values
            # kwargs structure: {attr: value, attr2: value2, ...}
            for obj in pm.ls(objects):
                for attr, value in kwargs.items():
                    attr_full_name = f"{obj}.{attr}"
                    for time in target_times:
                        pm.setKeyframe(attr_full_name, time=time, value=value)

        if refresh_channel_box:
            pm.mel.eval("channelBoxCommand -update;")

    @staticmethod
    def filter_objects_with_keys(
        objects: Optional[Union[str, List[str]]] = None,
        keys: Optional[List[str]] = None,
    ) -> List[object]:
        """Filter the given objects for those with specific keys set. If no objects are given, use all scene objects. If no specific keys are given, check all keys.

        Parameters:
            objects: The objects (or their names) to filter. Can be a single object or a list of objects. If None, all scene objects are used.
            keys: Specific keys to check for. If none are provided, all keys are checked.

        Returns:
            List of transforms with the specified keys set.
        """
        if objects is None:
            objects = pm.ls(type="transform")
        else:
            objects = pm.ls(objects, type="transform")

        if keys is None:
            keys = pm.listAttr(objects, keyable=True)

        filtered_objects = []
        for obj in objects:
            for key in ptk.make_iterable(keys):
                if obj.hasAttr(key):
                    attr = obj.attr(key)
                    if pm.keyframe(attr, query=True, name=True):
                        filtered_objects.append(obj)
                        break  # No need to check other keys if one is found

        return filtered_objects

    @classmethod
    @CoreUtils.undoable
    def adjust_key_spacing(
        cls,
        objects: Optional[List[str]] = None,
        spacing: int = 1,
        time: int = 0,
        relative: bool = True,
        preserve_keys: bool = False,
    ):
        """Adjusts the spacing between keyframes for specified objects at a given time,
        with an option to preserve and adjust a keyframe at the specified time.

        Parameters:
            objects (Optional[List[str]]): Objects to adjust keyframes for. If None, adjusts all scene objects.
            spacing (int): Spacing to add or remove. Negative values remove spacing.
            time (int): Time at which to start adjusting spacing.
            relative (bool): If True, time is relative to the current frame.
            preserve_keys (bool): Preserves and adjusts a keyframe at the specified time if it exists.
        """
        if spacing == 0:
            return

        current_time = pm.currentTime(query=True)
        adjusted_time = time + current_time if relative else time

        if objects is None:
            objects = pm.ls(type="transform", long=True)

        keyframe_movements = []

        for obj in objects:
            for attr in pm.listAnimatable(obj):
                attr_name = f"{obj}.{attr.split('.')[-1]}"
                keyframes = pm.keyframe(attr_name, query=True)

                if keyframes:
                    key_exists_at_time = adjusted_time in keyframes
                    for key in keyframes:
                        if key >= adjusted_time:
                            new_time = max(key + spacing, 0)
                            tangent_info = cls.get_tangent_info(attr_name, key)
                            keyframe_movements.append(
                                (
                                    attr_name,
                                    key,
                                    new_time,
                                    key_exists_at_time,
                                    tangent_info,
                                )
                            )
        for (
            attr_name,
            key,
            new_time,
            key_exists_at_time,
            tangent_info,
        ) in keyframe_movements:
            value = pm.getAttr(attr_name, time=key)
            pm.setKeyframe(attr_name, time=(new_time,), value=value)
            pm.keyTangent(attr_name, time=(new_time,), edit=True, **tangent_info)

            if key != adjusted_time or (
                key == adjusted_time and not key_exists_at_time
            ):
                pm.cutKey(attr_name, time=(key, key))

            if key == adjusted_time and not preserve_keys:
                pm.cutKey(attr_name, time=(adjusted_time, adjusted_time))

    @staticmethod
    def add_intermediate_keys(
        objects: Union[str, "pm.nt.Transform", List[Union[str, "pm.nt.Transform"]]],
        start: int,
        end: int,
        percent: Optional[float] = None,
        include_flat: bool = False,
    ) -> None:
        """Keys selected or animated attributes on given object(s) between `start` and `end`.
        If attributes are selected in the channel box, only those will be keyed.

        Parameters:
            objects (str/list): One or more objects to key.
            start (int): Start frame.
            end (int): End frame.
            percent (float): Optional percent (0–100) of frames to key, evenly distributed.
            include_flat (bool): If False, skips keys where value doesn't vary across time.
        """
        from math import isclose

        targets = pm.ls(objects, flatten=True)
        attrs = pm.channelBox("mainChannelBox", q=True, selectedMainAttributes=True)
        if not attrs:
            attrs = set()
            for obj in targets:
                for plug in obj.listAttr(keyable=True, scalar=True):
                    if plug.isConnected():
                        attrs.add(plug.attrName())
            attrs = list(attrs)

        if not attrs:
            pm.warning("No keyable or connected attributes found.")
            return

        frames = list(range(start + 1, end))
        if percent is not None:
            count = max(1, int(len(frames) * (percent / 100.0)))
            step = max(1, len(frames) // count)
            frames = frames[::step]

        frame_values = {}
        for frame in frames:
            pm.currentTime(frame, edit=True)
            frame_values[frame] = {}
            for obj in targets:
                obj_data = frame_values[frame].setdefault(obj, {})
                for attr in attrs:
                    plug = obj.attr(attr)
                    if plug.isConnected() and plug.isKeyable():
                        obj_data[attr] = plug.get()

        for frame, obj_data in frame_values.items():
            pm.currentTime(frame, edit=True)
            for obj, attr_values in obj_data.items():
                for attr, value in attr_values.items():
                    plug = obj.attr(attr)
                    if not include_flat:
                        try:
                            val_prev = plug.get(time=frame - 1)
                            val_next = plug.get(time=frame + 1)
                            if isclose(value, val_prev, abs_tol=1e-6) and isclose(
                                value, val_next, abs_tol=1e-6
                            ):
                                continue
                        except Exception:
                            continue
                    plug.set(value)
                    plug.setKey()

    @staticmethod
    @CoreUtils.undoable
    def invert_selected_keys(time=1, relative=True, delete_original=False):
        """Duplicate any selected keyframes and paste them inverted at the given time.

        Parameters:
            time (int): The desired start time for the inverted keys.
            relative (bool): Start time position as relative or absolute.
            delete_original (bool): Delete the original keyframes after inverting.

        Example:
            invert_selected_frames(time=48, relative=0)
        """
        # Validate selection and keyframes
        selection = pm.selected()
        if not selection:
            raise RuntimeError("No objects selected.")

        allActiveKeyTimes = pm.keyframe(query=True, sl=True, tc=True)
        if not allActiveKeyTimes:
            raise RuntimeError("No keyframes selected.")

        maxTime = max(allActiveKeyTimes)
        inversionPoint = maxTime + time if relative else time

        # Store keyframe data
        keyframe_data = []
        for obj in selection:
            keys = pm.keyframe(obj, query=True, name=True, sl=True)
            for node in keys:
                activeKeyTimes = pm.keyframe(node, query=True, sl=True, tc=True)
                for t in activeKeyTimes:
                    keyVal = pm.keyframe(node, query=True, time=(t,), eval=True)[0]
                    invertedTime = inversionPoint - (t - maxTime)
                    keyframe_data.append((node, t, keyVal, invertedTime))

        # Optionally delete original keyframes
        if delete_original:
            for obj in selection:
                keys = pm.keyframe(obj, query=True, name=True, sl=True)
                for node in keys:
                    pm.cutKey(
                        node, time=(min(allActiveKeyTimes), max(allActiveKeyTimes))
                    )

        # Create inverted keyframes
        for node, t, keyVal, invertedTime in keyframe_data:
            pm.setKeyframe(node, time=invertedTime, value=keyVal)
            tangent_info = pm.keyTangent(
                node, query=True, time=t, inAngle=True, outAngle=True
            )
            if tangent_info:
                inAngle, outAngle = tangent_info
                inAngleVal = -outAngle[0] if isinstance(outAngle, list) else -outAngle
                outAngleVal = -inAngle[0] if isinstance(inAngle, list) else -inAngle
                pm.keyTangent(
                    node,
                    edit=True,
                    time=invertedTime,
                    inAngle=inAngleVal,
                    outAngle=outAngleVal,
                )

    @staticmethod
    @CoreUtils.undoable
    def stagger_keyframes(
        objects: list,
        start_frame: int = None,
        interval: Union[int, Tuple[int, int]] = None,
        offset: float = 0,
        smooth_tangents: bool = False,
        invert: bool = False,
        group_overlapping: bool = False,
    ):
        """Stagger the keyframes of selected objects with various positioning controls.

        If keys are selected, only those keys are staggered. If no keys are selected, all keys are staggered.

        Parameters:
            objects (list): List of objects whose keyframes need to be staggered.
            start_frame (int, optional): Override starting frame. If None, uses earliest keyframe.
            interval (int or tuple, optional): If set, place each animation at regular frame intervals
                (e.g., 100 = animations start at frames 0, 100, 200, 300...).
                Can be a single int or a tuple (base_interval, overlap_interval).
                When a tuple, if placing an animation would cause overlap with the previous one,
                it skips to the next overlap_interval position instead.
                When used, offset is ignored.
            offset (float, optional): Offset/spacing between animations. Can be:
                        - Positive value: Gap in frames between animations (e.g., 10 = 10 frame gap)
                        - Zero: End-to-start with no gap (default)
                        - Negative value: Overlap in frames (e.g., -5 = 5 frames of overlap)
                        - Float between -1.0 and 1.0: Percentage of animation duration
                        (e.g., 0.5 = 50% of duration gap, -0.3 = 30% overlap)
            smooth_tangents (bool, optional): If True, adjusts tangents for smooth transitions (default is False).
            invert (bool, optional): If True, the objects list is processed in reverse order (default is False).
            group_overlapping (bool, optional): If True, treats objects with overlapping keyframes as a single block.
                Objects in the same group will be moved together. (default is False).
        """
        if not objects:
            pm.warning("No objects provided.")
            return

        objects = pm.ls(objects, type="transform", flatten=True)
        if invert:
            objects = list(reversed(objects))

        # Collect all keyframe data for each object
        obj_keyframe_data = []
        first_keyframe = None
        last_keyframe = None

        for obj in objects:
            keyframes = pm.keyframe(obj, query=True, selected=True)
            if not keyframes:
                keyframes = pm.keyframe(obj, query=True)

            if keyframes:
                keyframes = sorted(set(keyframes))
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "keyframes": keyframes,
                        "start": keyframes[0],
                        "end": keyframes[-1],
                        "duration": keyframes[-1] - keyframes[0],
                    }
                )

                if first_keyframe is None or keyframes[0] < first_keyframe:
                    first_keyframe = keyframes[0]
                if last_keyframe is None or keyframes[-1] > last_keyframe:
                    last_keyframe = keyframes[-1]

        if not obj_keyframe_data:
            pm.warning("No keyframes found on the provided objects.")
            return

        # Group overlapping objects if requested
        if group_overlapping:
            obj_keyframe_data = AnimUtils._group_overlapping_keyframes(
                obj_keyframe_data
            )

        # Use provided start_frame or earliest keyframe
        base_frame = start_frame if start_frame is not None else first_keyframe

        # Apply stagger based on interval or offset
        if interval is not None:
            # Handle interval as tuple (base_interval, overlap_interval) or single int
            if isinstance(interval, tuple) and len(interval) == 2:
                base_interval, overlap_interval = interval
            else:
                base_interval = interval
                overlap_interval = None

            # Place each animation at regular frame intervals
            previous_end = None  # Track the end of the previous animation

            for i, data in enumerate(obj_keyframe_data):
                objects_in_group = data.get("objects", [data["obj"]])
                group_start = data["start"]  # Use the group's start frame
                group_end = data["end"]
                duration = data["duration"]

                # Calculate target start position
                target_start = base_frame + (i * base_interval)

                # Check for overlap if overlap_interval is specified
                if overlap_interval is not None and previous_end is not None:
                    # If the target start would overlap with the previous animation's end
                    if target_start < previous_end:
                        # Find the next available overlap_interval position that doesn't overlap
                        overlap_count = 1
                        while target_start < previous_end:
                            target_start = (
                                base_frame
                                + (i * base_interval)
                                + (overlap_count * overlap_interval)
                            )
                            overlap_count += 1

                shift_amount = target_start - group_start

                if shift_amount != 0:
                    for obj in objects_in_group:
                        obj_keyframes = pm.keyframe(obj, query=True, selected=True)
                        if not obj_keyframes:
                            obj_keyframes = pm.keyframe(obj, query=True)
                        if obj_keyframes:
                            try:
                                pm.keyframe(
                                    obj,
                                    edit=True,
                                    time=(min(obj_keyframes), max(obj_keyframes)),
                                    relative=True,
                                    timeChange=shift_amount,
                                )
                            except RuntimeError as e:
                                pm.warning(f"Failed to move keys for {obj}: {e}")

                # Update previous_end to track this animation's new end position
                previous_end = target_start + duration
        else:
            # Sequential stagger with offset
            current_frame = base_frame

            for data in obj_keyframe_data:
                objects_in_group = data.get("objects", [data["obj"]])
                group_start = data["start"]  # Use the group's start frame
                duration = data["duration"]

                # Calculate offset in frames
                # If offset is between -1.0 and 1.0, treat as percentage of duration
                if -1.0 < offset < 1.0:
                    offset_frames = duration * offset
                else:
                    offset_frames = offset

                shift_amount = current_frame - group_start
                if shift_amount != 0:
                    for obj in objects_in_group:
                        obj_keyframes = pm.keyframe(obj, query=True, selected=True)
                        if not obj_keyframes:
                            obj_keyframes = pm.keyframe(obj, query=True)
                        if obj_keyframes:
                            try:
                                pm.keyframe(
                                    obj,
                                    edit=True,
                                    time=(min(obj_keyframes), max(obj_keyframes)),
                                    relative=True,
                                    timeChange=shift_amount,
                                )
                            except RuntimeError as e:
                                pm.warning(f"Failed to move keys for {obj}: {e}")

                # Update current frame for next object/group
                # Positive offset = gap, negative = overlap
                current_frame = current_frame + duration + offset_frames

        if smooth_tangents:
            # Ensure smooth transitions for the staggered keyframes
            for data in obj_keyframe_data:
                objects_in_group = data.get("objects", [data["obj"]])
                keyframes = data["keyframes"]
                for obj in objects_in_group:
                    try:
                        # Only smooth the keyframes that were actually staggered
                        pm.keyTangent(
                            obj,
                            edit=True,
                            time=(keyframes[0], keyframes[-1]),
                            outTangentType="auto",
                            inTangentType="auto",
                        )
                    except RuntimeError as e:
                        pm.warning(f"Failed to adjust tangents for {obj}: {e}")

    @staticmethod
    def _group_overlapping_keyframes(obj_keyframe_data: List[dict]) -> List[dict]:
        """Helper method to group objects with overlapping keyframe ranges into single blocks.

        Objects are considered overlapping if their keyframe time ranges intersect.
        Grouped objects are treated as a single unit during staggering operations.

        Parameters:
            obj_keyframe_data (List[dict]): List of dictionaries containing object keyframe data.
                Each dict should have 'obj', 'keyframes', 'start', 'end', and 'duration' keys.

        Returns:
            List[dict]: List of grouped object data. Each group contains:
                - 'objects': List of objects in the group
                - 'keyframes': Combined keyframe times
                - 'start': Earliest keyframe in the group
                - 'end': Latest keyframe in the group
                - 'duration': Total duration of the group
                - 'obj': Representative object (for backward compatibility)

        Example:
            # Objects with overlapping keyframes [1-10], [5-15], [20-30]
            # Would be grouped as: [[obj1, obj2]], [[obj3]]
        """
        if not obj_keyframe_data:
            return []

        # Sort by start frame
        sorted_data = sorted(obj_keyframe_data, key=lambda x: x["start"])

        groups = []
        current_group = {
            "objects": [sorted_data[0]["obj"]],
            "keyframes": sorted_data[0]["keyframes"],
            "start": sorted_data[0]["start"],
            "end": sorted_data[0]["end"],
            "duration": sorted_data[0]["duration"],
            "obj": sorted_data[0][
                "obj"
            ],  # Representative object for backward compatibility
        }

        for i in range(1, len(sorted_data)):
            data = sorted_data[i]

            # Check if this object overlaps with the current group
            if data["start"] <= current_group["end"]:
                # Overlapping - add to current group
                current_group["objects"].append(data["obj"])
                # Merge keyframes
                current_group["keyframes"] = sorted(
                    set(current_group["keyframes"] + data["keyframes"])
                )
                # Update group boundaries
                current_group["end"] = max(current_group["end"], data["end"])
                current_group["duration"] = (
                    current_group["end"] - current_group["start"]
                )
            else:
                # Not overlapping - start new group
                groups.append(current_group)
                current_group = {
                    "objects": [data["obj"]],
                    "keyframes": data["keyframes"],
                    "start": data["start"],
                    "end": data["end"],
                    "duration": data["duration"],
                    "obj": data["obj"],
                }

        # Add the last group
        groups.append(current_group)

        return groups

    @staticmethod
    @CoreUtils.undoable
    def align_selected_keyframes(
        objects: Optional[List[Union[str, "pm.PyNode"]]] = None,
        target_frame: Optional[float] = None,
        use_earliest: bool = True,
    ) -> bool:
        """Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.

        This method finds the earliest (or latest) selected keyframe across all objects and shifts
        each object's selected keyframes so they start at the same frame. Only processes selected
        keyframes from the graph editor.

        Parameters:
            objects (Optional[List[Union[str, pm.PyNode]]]): Objects to align. If None, uses current selection.
            target_frame (Optional[float]): Specific frame to align to. If None, aligns to the earliest
                                           (or latest if use_earliest=False) selected keyframe.
            use_earliest (bool): If True, aligns to the earliest selected keyframe. If False, aligns
                                to the latest. Only used when target_frame is None. Default is True.

        Returns:
            bool: True if keyframes were successfully aligned, False otherwise.

        Example:
            # Align selected keyframes to their earliest frame
            align_selected_keyframes()

            # Align selected keyframes to frame 10
            align_selected_keyframes(target_frame=10)

            # Align selected keyframes to their latest frame
            align_selected_keyframes(use_earliest=False)
        """
        # Get objects to work with
        if objects is None:
            objects = pm.selected()

        if not objects:
            pm.warning("No objects specified or selected.")
            return False

        # Ensure we have transform nodes, not shape nodes
        objects = pm.ls(objects, type="transform", flatten=True)

        if not objects:
            pm.warning("No valid transform nodes found.")
            return False

        # Collect selected keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            # Get animation curve nodes with selected keyframes
            curve_nodes = pm.keyframe(obj, query=True, name=True, selected=True)

            if not curve_nodes:
                continue

            # For each curve node, get the selected keyframe times
            obj_selected_times = []
            for node in curve_nodes:
                selected_times = pm.keyframe(
                    node, query=True, selected=True, timeChange=True
                )
                if selected_times:
                    obj_selected_times.extend(selected_times)

            if obj_selected_times:
                obj_selected_times = sorted(set(obj_selected_times))
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "curve_nodes": curve_nodes,
                        "times": obj_selected_times,
                        "start": min(obj_selected_times),
                        "end": max(obj_selected_times),
                    }
                )

        if not obj_keyframe_data:
            pm.warning("No selected keyframes found on any objects.")
            return False

        # Determine the alignment target frame
        if target_frame is None:
            if use_earliest:
                target_frame = min(data["start"] for data in obj_keyframe_data)
            else:
                target_frame = max(data["start"] for data in obj_keyframe_data)

        # Align each object's selected keyframes
        for data in obj_keyframe_data:
            obj = data["obj"]
            curve_nodes = data["curve_nodes"]
            times = data["times"]
            current_start = data["start"]

            # Calculate the shift amount
            shift_amount = target_frame - current_start

            if abs(shift_amount) < 1e-6:  # Skip if already aligned (within tolerance)
                continue

            # Shift the selected keyframes for this object
            # Use the time range of selected keys to target only those keys
            min_time = min(times)
            max_time = max(times)

            # Move the keyframes - must target the object, not individual curve nodes
            # to ensure all selected keys move together
            pm.keyframe(
                obj,
                edit=True,
                time=(min_time, max_time),
                relative=True,
                timeChange=shift_amount,
                option="over",  # Only affect keyframes in the time range
            )

        pm.displayInfo(
            f"Aligned selected keyframes for {len(obj_keyframe_data)} object(s) to frame {target_frame:.2f}"
        )
        return True

    @staticmethod
    @CoreUtils.undoable
    def set_visibility_keys(
        objects: Optional[List[Union[str, "pm.PyNode"]]] = None,
        visible: bool = True,
        when: str = "start",
        offset: int = 0,
        group_overlapping: bool = False,
    ) -> int:
        """Sets visibility keyframes for objects with options for timing and grouping.

        This method creates visibility keyframes at specific points in the animation timeline,
        with support for grouping objects that have overlapping keyframe ranges.

        Parameters:
            objects (Optional[List[Union[str, pm.PyNode]]]): Objects to set visibility keys on.
                If None, uses current selection.
            visible (bool): Visibility state to set (True = visible, False = hidden). Default is True.
            when (str): When to set the visibility key. Options:
                - "start": At the start of each object's keyframe range
                - "end": At the end of each object's keyframe range
                - "both": At both start and end
                - "before_start": One frame before the start
                - "after_end": One frame after the end
                Default is "start".
            offset (int): Frame offset to apply to the keyframe timing. Positive values move
                keys later, negative values move keys earlier. Default is 0.
            group_overlapping (bool): If True, treats objects with overlapping keyframe ranges
                as a single group, setting visibility keys based on the group's combined range.
                Default is False.

        Returns:
            int: Number of visibility keyframes created.

        Example:
            # Hide objects at the start of their animation
            set_visibility_keys(visible=False, when="start")

            # Make objects visible at the end of their animation with 5 frame offset
            set_visibility_keys(visible=True, when="end", offset=5)

            # Set visibility for grouped overlapping animations
            set_visibility_keys(visible=True, when="both", group_overlapping=True)
        """
        # Get objects to work with
        if objects is None:
            objects = pm.selected()

        if not objects:
            pm.warning("No objects specified or selected.")
            return 0

        # Ensure we have transform nodes
        objects = pm.ls(objects, type="transform", flatten=True)

        if not objects:
            pm.warning("No valid transform nodes found.")
            return 0

        # Collect keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            keyframes = pm.keyframe(obj, query=True)

            if keyframes:
                keyframes = sorted(set(keyframes))
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "keyframes": keyframes,
                        "start": keyframes[0],
                        "end": keyframes[-1],
                        "duration": keyframes[-1] - keyframes[0],
                    }
                )

        if not obj_keyframe_data:
            pm.warning("No keyframes found on the provided objects.")
            return 0

        # Group overlapping objects if requested
        if group_overlapping:
            obj_keyframe_data = AnimUtils._group_overlapping_keyframes(
                obj_keyframe_data
            )

        # Determine visibility value (0 or 1)
        visibility_value = 1 if visible else 0

        # Set visibility keyframes based on 'when' parameter
        keys_created = 0

        for data in obj_keyframe_data:
            objects_in_group = data.get("objects", [data["obj"]])
            start_frame = data["start"]
            end_frame = data["end"]

            # Determine target frames based on 'when' parameter
            target_frames = []

            if when == "start":
                target_frames = [start_frame + offset]
            elif when == "end":
                target_frames = [end_frame + offset]
            elif when == "both":
                target_frames = [start_frame + offset, end_frame + offset]
            elif when == "before_start":
                target_frames = [start_frame - 1 + offset]
            elif when == "after_end":
                target_frames = [end_frame + 1 + offset]
            else:
                pm.warning(f"Invalid 'when' parameter: {when}. Using 'start'.")
                target_frames = [start_frame + offset]

            # Set visibility keys for each object in the group
            for obj in objects_in_group:
                for frame in target_frames:
                    pm.setKeyframe(
                        obj, attribute="visibility", time=frame, value=visibility_value
                    )
                    keys_created += 1

        pm.displayInfo(
            f"Created {keys_created} visibility keyframe(s) for {len(obj_keyframe_data)} object(s)/group(s)"
        )
        return keys_created

    @staticmethod
    @CoreUtils.undoable
    def snap_keys_to_frames(
        objects: Optional[List[Union[str, "pm.PyNode"]]] = None,
        method: str = "nearest",
        selected_only: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> int:
        """Snaps keyframes with decimal time values to whole frame numbers.

        This method rounds keyframe times to the nearest whole number, useful for cleaning up
        keyframes that have been scaled, retimed, or imported with fractional frame values.

        Parameters:
            objects (Optional[List[Union[str, pm.PyNode]]]): Objects to process keyframes for.
                If None, uses current selection.
            method (str): Rounding method to use. Options:
                - "nearest": Round to nearest whole number (default)
                - "floor": Always round down
                - "ceil": Always round up
                - "half_up": Round .5 and above up, below .5 down (standard rounding)
                - "preferred": Round to aesthetically pleasing numbers when very close (within ~1 frame).
                  Examples: 24→25, 19→20, 18→20, 99→100. Conservative approach.
                - "aggressive_preferred": Round to preferred numbers even when farther away.
                  Examples: 48.x→50, 73.x→75, 88.x→90, 23.x→25, 7.x→10. More aggressive rounding.
            selected_only (bool): If True, only snap selected keyframes. If False, snap all
                keyframes on the objects. Default is False.
            time_range (Optional[Tuple[float, float]]): (start_time, end_time) to limit which
                keyframes to snap. If None, processes all keyframes. Default is None.

        Returns:
            int: Number of keyframes that were snapped to whole frames.

        Example:
            # Snap all keyframes to nearest whole frame
            snap_keys_to_frames()

            # Snap only selected keyframes, always rounding down
            snap_keys_to_frames(method="floor", selected_only=True)

            # Snap keyframes in a specific time range
            snap_keys_to_frames(time_range=(10, 100))

            # Snap to preferred round numbers (conservative)
            snap_keys_to_frames(method="preferred")

            # Snap to preferred round numbers (aggressive)
            snap_keys_to_frames(method="aggressive_preferred")
        """
        import math

        # Get objects to work with
        if objects is None:
            objects = pm.selected()

        if not objects:
            pm.warning("No objects specified or selected.")
            return 0

        # Ensure we have PyMel objects
        objects = pm.ls(objects, flatten=True)

        # Define rounding function based on method
        if method == "floor":
            round_func = math.floor
        elif method == "ceil":
            round_func = math.ceil
        elif method == "half_up":
            round_func = lambda x: math.floor(x + 0.5)
        elif method == "preferred":
            round_func = ptk.round_to_preferred
        elif method == "aggressive_preferred":
            round_func = ptk.round_to_aggressive_preferred
        else:  # "nearest" or default
            round_func = round

        keys_snapped = 0
        keys_to_move = (
            {}
        )  # Store keyframe data to move: {(obj, attr, old_time): new_time}

        for obj in objects:
            # Get animation curves
            if selected_only:
                curve_nodes = pm.keyframe(obj, query=True, name=True, selected=True)
            else:
                curve_nodes = pm.keyframe(obj, query=True, name=True)

            if not curve_nodes:
                continue

            for curve in curve_nodes:
                # Query keyframe times
                if selected_only:
                    if time_range:
                        keyframe_times = pm.keyframe(
                            curve,
                            query=True,
                            selected=True,
                            timeChange=True,
                            time=time_range,
                        )
                    else:
                        keyframe_times = pm.keyframe(
                            curve, query=True, selected=True, timeChange=True
                        )
                else:
                    if time_range:
                        keyframe_times = pm.keyframe(
                            curve, query=True, timeChange=True, time=time_range
                        )
                    else:
                        keyframe_times = pm.keyframe(curve, query=True, timeChange=True)

                if not keyframe_times:
                    continue

                # Get the attribute name for this curve
                connections = pm.listConnections(
                    curve, plugs=True, destination=True, source=False
                )
                if not connections:
                    continue

                attr_name = str(connections[0])

                # Find keyframes with decimal values
                for time in keyframe_times:
                    # Check if time has decimal places
                    if time != int(time):
                        new_time = round_func(time)

                        # Store the keyframe data for moving
                        key = (obj, attr_name, time)
                        keys_to_move[key] = new_time

        # Now move all the keyframes
        for (obj, attr_name, old_time), new_time in keys_to_move.items():
            try:
                # Get the keyframe value and tangent info at the old time
                value = pm.keyframe(
                    attr_name, query=True, time=(old_time,), valueChange=True
                )
                if not value:
                    continue
                value = value[0]

                # Get tangent information
                in_tangent_type = pm.keyTangent(
                    attr_name, query=True, time=(old_time,), inTangentType=True
                )[0]
                out_tangent_type = pm.keyTangent(
                    attr_name, query=True, time=(old_time,), outTangentType=True
                )[0]

                # Try to get angle and weight info
                try:
                    in_angle = pm.keyTangent(
                        attr_name, query=True, time=(old_time,), inAngle=True
                    )[0]
                    out_angle = pm.keyTangent(
                        attr_name, query=True, time=(old_time,), outAngle=True
                    )[0]
                    in_weight = pm.keyTangent(
                        attr_name, query=True, time=(old_time,), inWeight=True
                    )[0]
                    out_weight = pm.keyTangent(
                        attr_name, query=True, time=(old_time,), outWeight=True
                    )[0]
                    has_tangent_data = True
                except:
                    has_tangent_data = False

                # Delete the old keyframe
                pm.cutKey(attr_name, time=(old_time, old_time), option="keys")

                # Create new keyframe at rounded time
                pm.setKeyframe(attr_name, time=new_time, value=value)

                # Restore tangent types
                pm.keyTangent(
                    attr_name,
                    edit=True,
                    time=(new_time,),
                    inTangentType=in_tangent_type,
                    outTangentType=out_tangent_type,
                )

                # Restore tangent angles and weights if available
                if has_tangent_data:
                    try:
                        pm.keyTangent(
                            attr_name,
                            edit=True,
                            time=(new_time,),
                            inAngle=in_angle,
                            outAngle=out_angle,
                            inWeight=in_weight,
                            outWeight=out_weight,
                        )
                    except:
                        pass  # Some tangent types don't support angle/weight editing

                keys_snapped += 1

            except Exception as e:
                pm.warning(
                    f"Failed to snap keyframe for {attr_name} at time {old_time}: {e}"
                )

        if keys_snapped > 0:
            pm.displayInfo(
                f"Snapped {keys_snapped} keyframe(s) to whole frames using '{method}' method"
            )
        else:
            pm.displayInfo("No keyframes with decimal values found to snap")

        return keys_snapped

    @classmethod
    @CoreUtils.undoable
    def transfer_keyframes(
        cls,
        objects: List[Union[str, object]],
        relative: bool = False,
        transfer_tangents: bool = False,
    ):
        """Transfer keyframes from the first selected object to the subsequent objects.

        Parameters:
            objects (List[Union[str, object]]): List of objects. The first object is the source, and the rest are targets.
            relative (bool): If True, apply keyframes relative to the current values of the target objects.
            transfer_tangents (bool): If True, transfer the tangent handles along with the keyframes.
        """
        resolved_objects = pm.ls(objects)
        if len(resolved_objects) < 2:
            pm.warning("Please provide at least one source and one target object.")
            return

        source_obj = resolved_objects[0]
        target_objs = resolved_objects[1:]

        # Get keyframe times from the source object
        keyframe_times = pm.keyframe(source_obj, query=True, timeChange=True)
        if not keyframe_times:
            pm.warning(f"No keyframes found on source object '{source_obj}'.")
            return

        # Get keyable attributes from the source object that have keyframes
        keyable_attributes = pm.listAttr(source_obj, keyable=True)
        keyframe_attributes = [
            attr
            for attr in keyable_attributes
            if pm.keyframe(source_obj.attr(attr), query=True, name=True)
        ]

        # Store initial values for target objects
        initial_values = {
            target: {
                attr: target.attr(attr).get()
                for attr in keyframe_attributes
                if target.hasAttr(attr)
            }
            for target in target_objs
        }

        # Copy keyframes from source to each target
        for target_obj in target_objs:
            for attr in keyframe_attributes:
                if not target_obj.hasAttr(attr):
                    continue
                initial_value = initial_values[target_obj][attr]
                for time in keyframe_times:
                    values = pm.keyframe(
                        source_obj.attr(attr),
                        query=True,
                        time=(time,),
                        valueChange=True,
                    )
                    if values:
                        value = values[0]
                        if relative:
                            value += (
                                initial_value
                                - pm.keyframe(
                                    source_obj.attr(attr),
                                    query=True,
                                    time=(keyframe_times[0],),
                                    valueChange=True,
                                )[0]
                            )
                        pm.setKeyframe(target_obj.attr(attr), time=time, value=value)

                        if transfer_tangents:
                            tangent_info = cls.get_tangent_info(
                                source_obj.attr(attr), time
                            )
                            pm.keyTangent(
                                target_obj.attr(attr),
                                time=(time,),
                                edit=True,
                                **tangent_info,
                            )

    @staticmethod
    def parse_time_range(
        time: Union[None, int, str, Tuple, List],
        recursive_callback: Optional[callable] = None,
    ) -> Union[Tuple[float, float], None, List]:
        """Parse time specification into a time range tuple for keyframe operations.

        This helper method handles various time specifications and converts them into
        time ranges suitable for Maya keyframe operations. It supports recursive processing
        for complex time specifications like pipe-separated strings or multi-element sequences.

        Parameters:
            time (None, int, str, tuple, list): Time specification to parse.
                Accepts:
                - None or 'all': Returns None (entire timeline)
                - int: Returns (time, time) for specific frame
                - 'current': Returns (current_time, current_time)
                - 'before': Returns (-1000000, current_time - 1)
                - 'after': Returns (current_time + 1, 1000000)
                - tuple/list of 2 elements: Returns (start, end) range
                - tuple/list of 3+ elements: Returns list for recursive processing
                - Pipe-separated strings: Returns list for recursive processing
            recursive_callback (callable, optional): Function to call for recursive processing
                of pipe-separated strings or multi-element sequences. Should accept the same
                parameters as the calling function.

        Returns:
            Union[Tuple[float, float], None, List]:
                - None: Process entire timeline
                - Tuple[float, float]: (start_time, end_time) range
                - List: Multiple time values/ranges requiring recursive processing

        Example:
            # Single frame
            time_range = parse_time_range(10)  # Returns (10, 10)

            # Current frame
            time_range = parse_time_range('current')  # Returns (current_time, current_time)

            # Before current frame
            time_range = parse_time_range('before')  # Returns (-1000000, current_time - 1)

            # Range
            time_range = parse_time_range((5, 15))  # Returns (5, 15)

            # Multiple frames (returns list for recursive processing)
            time_values = parse_time_range((1, 5, 10, 20))  # Returns [1, 5, 10, 20]

            # Pipe-separated (returns list for recursive processing)
            time_values = parse_time_range('before|current')  # Returns ['before', 'current']
        """
        # Handle pipe-separated time strings - return list for recursive processing
        if isinstance(time, str) and "|" in time:
            return [p.strip() for p in time.split("|")]

        # Handle tuples/lists with more than 2 values - return list for recursive processing
        if isinstance(time, (list, tuple)) and len(time) > 2:
            return list(time)

        # Determine time range for single time specification
        time_range = None

        if isinstance(time, str):
            time_lower = time.lower()
            current_time = pm.currentTime(query=True)

            if time_lower == "current":
                time_range = (current_time, current_time)
            elif time_lower == "before":
                # From very early time to just before current
                time_range = (-1000000, current_time - 1)
            elif time_lower == "after":
                # From just after current to very late time
                time_range = (current_time + 1, 1000000)
            elif time_lower == "all":
                time_range = None  # Process all
        elif isinstance(time, (list, tuple)) and len(time) == 2:
            time_range = (time[0], time[1])
        elif isinstance(time, int):
            time_range = (time, time)

        return time_range

    @staticmethod
    @CoreUtils.undoable
    def delete_keys(objects, *attributes, time=None, channel_box_only=False):
        """Deletes keyframes for specified attributes on given objects, optionally within a time range.

        This function can delete keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list): The list of objects from which to delete keyframes.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be deleted (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe deletion.
                    Accepts:
                    - None or 'all': Delete all keyframes (entire timeline)
                    - int: Delete keyframes at specific frame
                    - 'current': Delete keyframes at current frame
                    - 'before': Delete all keyframes before current frame (excluding current)
                    - 'after': Delete all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Delete keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Delete at each frame recursively
            channel_box_only (bool): If True, only deletes keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' deletes both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes are selected in channel box will result in no deletion

        Example Usage:
            delete_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Delete keyframes at frame 10
            delete_keys([obj1, obj2], time='current') # Delete keyframes at current frame
            delete_keys([obj1, obj2], time='before') # Delete all keyframes before current (excluding current)
            delete_keys([obj1, obj2], time='after') # Delete all keyframes after current (excluding current)
            delete_keys([obj1, obj2], time='before|current') # Delete up to and including current
            delete_keys([obj1, obj2], time='after|current') # Delete from and after current
            delete_keys([obj1, obj2], time='before|current|after') # Delete all keyframes (equivalent to 'all')
            delete_keys([obj1, obj2], time=(5, 15)) # Delete all keyframes between frames 5 and 15
            delete_keys([obj1, obj2], time=(1, 5, 10, 20)) # Delete keyframes at frames 1, 5, 10, and 20
            delete_keys([obj1, obj2], 'rotateX', 'rotateY') # Delete all keyframes for specified attributes
            delete_keys([obj1, obj2], channel_box_only=True) # Delete only for channel box selected attributes
        """
        if objects is None:
            objects = pm.selected()

        objects = pm.ls(objects, flatten=True)

        if not objects:
            pm.warning("No objects specified or selected.")
            return

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )
            if not cb_attrs:
                pm.warning("No attributes selected in channel box.")
                return
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = AnimUtils.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            for t in time_range:
                AnimUtils.delete_keys(
                    objects, *attributes, time=t, channel_box_only=False
                )
            return

        for obj in objects:
            if attributes:  # Delete keyframes for specified attributes
                for attr in attributes:
                    if time_range:
                        pm.cutKey(f"{obj}.{attr}", time=time_range, clear=True)
                    else:
                        pm.cutKey(f"{obj}.{attr}", clear=True)
            else:  # Delete keyframes for all attributes
                if time_range:
                    pm.cutKey(obj, time=time_range, clear=True)
                else:
                    pm.cutKey(obj, clear=True)

    @staticmethod
    def select_keys(
        objects: Optional[List[Union[str, "pm.PyNode"]]] = None,
        *attributes: str,
        time: Union[None, int, str, Tuple, List] = None,
        channel_box_only: bool = False,
        add_to_selection: bool = False,
    ) -> int:
        """Selects keyframes for specified attributes on given objects, optionally within a time range.

        This function selects keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list, optional): The list of objects from which to select keyframes. If None, uses selection.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be selected (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe selection.
                    Accepts:
                    - None or 'all': Select all keyframes (entire timeline)
                    - int: Select keyframes at specific frame
                    - 'current': Select keyframes at current frame
                    - 'before': Select all keyframes before current frame (excluding current)
                    - 'after': Select all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Select keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Select at each frame recursively
            channel_box_only (bool): If True, only selects keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.
            add_to_selection (bool): If True, adds to existing keyframe selection. If False, replaces selection.
                                    Default is False.

        Returns:
            int: Number of keyframes selected.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' selects both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes selected in channel box will result in no selection

        Example Usage:
            select_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Select keyframes at frame 10
            select_keys([obj1, obj2], time='current') # Select keyframes at current frame
            select_keys([obj1, obj2], time='before') # Select all keyframes before current (excluding current)
            select_keys([obj1, obj2], time='after') # Select all keyframes after current (excluding current)
            select_keys([obj1, obj2], time='before|current') # Select up to and including current
            select_keys([obj1, obj2], time='after|current') # Select from and after current
            select_keys([obj1, obj2], time='before|current|after') # Select all keyframes (equivalent to 'all')
            select_keys([obj1, obj2], time=(5, 15)) # Select all keyframes between frames 5 and 15
            select_keys([obj1, obj2], time=(1, 5, 10, 20)) # Select keyframes at frames 1, 5, 10, and 20
            select_keys([obj1, obj2], 'rotateX', 'rotateY') # Select all keyframes for specified attributes
            select_keys([obj1, obj2], channel_box_only=True) # Select only for channel box selected attributes
            select_keys([obj1, obj2], time='current', add_to_selection=True) # Add current frame keys to selection
        """
        if objects is None:
            objects = pm.selected()

        objects = pm.ls(objects, flatten=True)

        if not objects:
            pm.warning("No objects specified or selected.")
            return 0

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = pm.channelBox(
                "mainChannelBox", query=True, selectedMainAttributes=True
            )
            if not cb_attrs:
                pm.warning("No attributes selected in channel box.")
                return 0
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = AnimUtils.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            total_selected = 0
            for i, t in enumerate(time_range):
                # Only replace selection on first iteration, add to selection afterward
                add = add_to_selection or (i > 0)
                count = AnimUtils.select_keys(
                    objects,
                    *attributes,
                    time=t,
                    channel_box_only=False,
                    add_to_selection=add,
                )
                total_selected += count
            return total_selected

        # Clear selection if not adding to it
        if not add_to_selection:
            pm.selectKey(clear=True)

        keys_selected = 0

        for obj in objects:
            if attributes:  # Select keyframes for specified attributes
                for attr in attributes:
                    if time_range:
                        pm.selectKey(f"{obj}.{attr}", time=time_range, add=True)
                        # Count selected keys
                        selected = pm.keyframe(
                            f"{obj}.{attr}", query=True, selected=True, timeChange=True
                        )
                        if selected:
                            keys_selected += len(selected)
                    else:
                        pm.selectKey(f"{obj}.{attr}", add=True)
                        # Count selected keys
                        selected = pm.keyframe(
                            f"{obj}.{attr}", query=True, selected=True, timeChange=True
                        )
                        if selected:
                            keys_selected += len(selected)
            else:  # Select keyframes for all attributes
                if time_range:
                    pm.selectKey(obj, time=time_range, add=True)
                    # Count selected keys
                    selected = pm.keyframe(
                        obj, query=True, selected=True, timeChange=True
                    )
                    if selected:
                        keys_selected += len(selected)
                else:
                    pm.selectKey(obj, add=True)
                    # Count selected keys
                    selected = pm.keyframe(
                        obj, query=True, selected=True, timeChange=True
                    )
                    if selected:
                        keys_selected += len(selected)

        return keys_selected

    @staticmethod
    def get_frame_ranges(
        objects: List[str],
        precision: Optional[int] = None,
        gap: Optional[int] = None,
    ) -> Dict[str, List[Tuple[int, int]]]:
        """Calculate frame ranges for a list of objects based on their keyframes.

        This method analyzes the keyframes of given objects and determines continuous
        frame ranges. It supports optional rounding of frame numbers to a specified precision
        and allows for specifying a gap threshold to split ranges.

        Parameters:
            objects (List[str]): List of object names to analyze.
            precision (Optional[int]): Precision for rounding frame numbers. If provided,
                                    frame numbers will be rounded to the nearest multiple
                                    of this value.
            gap (Optional[int]): Maximum allowed gap between consecutive keyframes in a
                                range. If the gap between two consecutive keyframes exceeds
                                this value, a new range will be started.
        Returns:
            Dict[str, List[Tuple[int, int]]]: Dictionary mapping object names to lists of
                                            frame ranges. Each frame range is represented
                                            as a tuple (start_frame, end_frame). If an
                                            object has no keyframes, the range will be
                                            [(None, None)].
        """

        def round_to_nearest(value: float, base: int) -> int:
            return int(base * round(value / base))

        frame_ranges = {}
        for obj in objects:
            keyframes = pm.keyframe(obj, query=True)
            if keyframes:
                keyframes = sorted(set(keyframes))  # Ensure unique, sorted keyframes
                ranges = []
                start_frame = keyframes[0]
                last_frame = keyframes[0]

                for kf in keyframes[1:]:
                    if gap is not None and kf - last_frame > gap:
                        end_frame = last_frame
                        if precision:
                            start_frame = round_to_nearest(start_frame, precision)
                            end_frame = round_to_nearest(end_frame, precision)
                        ranges.append((start_frame, end_frame))
                        start_frame = kf
                    last_frame = kf

                end_frame = last_frame
                if precision is not None:
                    start_frame = round_to_nearest(start_frame, precision)
                    end_frame = round_to_nearest(end_frame, precision)
                ranges.append((start_frame, end_frame))

                frame_ranges[obj] = ranges
            else:
                frame_ranges[obj] = [(None, None)]  # No keyframes, no range

        return frame_ranges

    @staticmethod
    @CoreUtils.undoable
    def tie_keyframes(objects: List["pm.nt.Transform"] = None, absolute: bool = False):
        """Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
        by setting keyframes only on the attributes that already have keyframes,
        at the start and end of the specified animation range.

        Parameters:
            objects (List[pm.nt.Transform], optional): List of PyMel transform nodes to process.
            If None, all keyed objects in the scene will be used.
            absolute (bool, optional): If True, uses the absolute start and end keyframes
            across all objects as the range. If False, uses the scene's playback range. Default is False.
        """
        if objects is None:  # Get all objects that have keyframes
            objects = pm.ls(type="transform")
            objects = [obj for obj in objects if pm.keyframe(obj, query=True)]

        if not objects:
            pm.warning("No keyed objects found.")
            return

        # Determine the keyframe range
        if absolute:  # Use the absolute start and end keyframes of all objects
            all_keyframes = pm.keyframe(objects, query=True, timeChange=True)
            if not all_keyframes:
                pm.warning("No keyframes found on any objects.")
                return
            start_frame = min(all_keyframes)
            end_frame = max(all_keyframes)
        else:  # Use the start and end frames of the entire scene's playback range
            start_frame = pm.playbackOptions(query=True, minTime=True)
            end_frame = pm.playbackOptions(query=True, maxTime=True)

        for obj in objects:  # Get all the attributes that have keyframes
            keyed_attrs = pm.keyframe(obj, query=True, name=True)

            if keyed_attrs:
                for attr in keyed_attrs:
                    # Set a keyframe at the start and end of the determined range for the specific attribute
                    pm.setKeyframe(attr, time=start_frame)
                    pm.setKeyframe(attr, time=end_frame)

        pm.displayInfo("Keyframes tied to the range for keyed attributes.")

    @staticmethod
    @CoreUtils.undoable
    def untie_keyframes(
        objects: List["pm.nt.Transform"] = None, absolute: bool = False
    ):
        """Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.

        This method intelligently removes keyframes at the start and end of the animation range
        that were likely added by tie_keyframes. It preserves actual animation keys by checking
        if the next/previous keyframe has a different value (indicating real animation).

        Parameters:
            objects (List[pm.nt.Transform], optional): List of PyMel transform nodes to process.
                If None, all keyed objects in the scene will be used.
            absolute (bool, optional): If True, uses the absolute start and end keyframes
                across all objects as the range. If False, uses the scene's playback range.
                Default is False.

        Example:
            # Remove bookend keys added by tie_keyframes
            untie_keyframes()

            # Remove bookend keys for specific objects
            untie_keyframes([obj1, obj2])

            # Use absolute keyframe range instead of playback range
            untie_keyframes(absolute=True)
        """
        if objects is None:  # Get all objects that have keyframes
            objects = pm.ls(type="transform")
            objects = [obj for obj in objects if pm.keyframe(obj, query=True)]

        if not objects:
            pm.warning("No keyed objects found.")
            return

        # Determine the keyframe range
        if absolute:  # Use the absolute start and end keyframes of all objects
            all_keyframes = pm.keyframe(objects, query=True, timeChange=True)
            if not all_keyframes:
                pm.warning("No keyframes found on any objects.")
                return
            range_start = min(all_keyframes)
            range_end = max(all_keyframes)
        else:  # Use the start and end frames of the entire scene's playback range
            range_start = pm.playbackOptions(query=True, minTime=True)
            range_end = pm.playbackOptions(query=True, maxTime=True)

        keys_removed = 0

        for obj in objects:
            # Get all the attributes that have keyframes
            keyed_attrs = pm.keyframe(obj, query=True, name=True)

            if keyed_attrs:
                for attr in keyed_attrs:
                    # Get all keyframe times for this attribute
                    keyframe_times = pm.keyframe(attr, query=True, timeChange=True)

                    if not keyframe_times or len(keyframe_times) < 2:
                        continue  # Need at least 2 keys to have bookends

                    keyframe_times = sorted(keyframe_times)

                    # Check start keyframe (use tolerance for float comparison)
                    if (
                        abs(keyframe_times[0] - range_start) < 1e-5
                        and len(keyframe_times) > 1
                    ):
                        # Get values of first two keyframes
                        start_value = pm.keyframe(
                            attr,
                            query=True,
                            time=(keyframe_times[0],),
                            valueChange=True,
                        )[0]
                        next_value = pm.keyframe(
                            attr,
                            query=True,
                            time=(keyframe_times[1],),
                            valueChange=True,
                        )[0]

                        # If values are the same, this is likely a bookend key
                        if abs(start_value - next_value) < 1e-5:
                            pm.cutKey(attr, time=(range_start, range_start), clear=True)
                            keys_removed += 1

                    # Check end keyframe (re-query in case we removed the start key)
                    keyframe_times = pm.keyframe(attr, query=True, timeChange=True)
                    if keyframe_times and len(keyframe_times) > 1:
                        keyframe_times = sorted(keyframe_times)

                        if abs(keyframe_times[-1] - range_end) < 1e-5:
                            # Get values of last two keyframes
                            end_value = pm.keyframe(
                                attr,
                                query=True,
                                time=(keyframe_times[-1],),
                                valueChange=True,
                            )[0]
                            prev_value = pm.keyframe(
                                attr,
                                query=True,
                                time=(keyframe_times[-2],),
                                valueChange=True,
                            )[0]

                            # If values are the same, this is likely a bookend key
                            if abs(end_value - prev_value) < 1e-5:
                                pm.cutKey(attr, time=(range_end, range_end), clear=True)
                                keys_removed += 1

        if keys_removed > 0:
            pm.displayInfo(f"Removed {keys_removed} bookend keyframe(s).")
        else:
            pm.displayInfo("No bookend keyframes found to remove.")

    @classmethod
    @CoreUtils.undoable
    def insert_keyframe_gap(
        cls,
        duration: Union[int, Tuple[int, int]],
        objects: Optional[List["pm.PyNode"]] = None,
        selected_keys_only: bool = False,
    ):
        """Create a gap in keyframes by moving keyframes forward in time.

        This method shifts keyframes to create an empty gap at a specified location in the timeline.
        Keys AFTER the gap start will be moved forward to ensure the full gap range is clear.
        The actual shift amount is calculated based on where the first key after gap_start is located,
        ensuring it moves to gap_end (or beyond).

        Parameters:
            duration (int or tuple): Either:
                                    - An int: number of frames for the gap. Uses current timeline
                                      position as the start. The first key after current time will
                                      move to (current_time + duration), with all subsequent keys
                                      maintaining their relative spacing.
                                    - A tuple (start, end): Creates a gap from start to end frames.
                                      The first key after 'start' will move to 'end', with all
                                      subsequent keys maintaining their relative spacing. This ensures
                                      the entire range from start to end is cleared.
            objects (list, optional): Objects to affect. If None, uses all animated objects in the scene.
            selected_keys_only (bool): If True, only affects selected keyframes in the graph editor.

        Returns:
            dict: Summary with 'keys_moved' count, 'affected_objects' list, gap info, and actual offset used.

        Example:
            # Create a 10-frame gap starting at current time
            # If current time is 50 and first key after 50 is at 52, keys move by 8 frames
            # so the key at 52 ends up at 60
            insert_keyframe_gap(duration=10)

            # Create a gap from frame 1700 to 2100
            # Keys at 1700 stay at 1700. If first key after 1700 is at 1900,
            # it moves to 2100 (shift of 200), clearing frames 1701-2100
            insert_keyframe_gap(duration=(1700, 2100))

            # Create a gap only for selected objects
            insert_keyframe_gap(duration=5, objects=pm.selected())
        """
        # Parse duration parameter
        if isinstance(duration, tuple):
            if len(duration) != 2:
                pm.warning("Duration tuple must have exactly 2 elements (start, end).")
                return {"keys_moved": 0, "affected_objects": []}
            gap_start, gap_end = duration
            gap_duration = gap_end - gap_start
            if gap_duration <= 0:
                pm.warning(
                    f"Duration end frame ({gap_end}) must be greater than start frame ({gap_start})."
                )
                return {"keys_moved": 0, "affected_objects": []}
            # When using tuple, we move keys at or after gap_start
            move_from_frame = gap_start
        else:
            gap_duration = duration
            if gap_duration <= 0:
                pm.warning("Gap duration must be greater than 0.")
                return {"keys_moved": 0, "affected_objects": []}
            # When using int, use current time as the start
            gap_start = int(pm.currentTime(query=True))
            gap_end = gap_start + gap_duration
            move_from_frame = gap_start

        # Get animation curves to work with
        anim_curves = cls.get_anim_curves(
            objects=objects, selected_keys_only=selected_keys_only, recursive=False
        )

        if not anim_curves:
            if selected_keys_only:
                pm.warning("No keyframes selected.")
            elif objects:
                pm.warning("No animation found on specified objects.")
            else:
                pm.warning("No animated objects found.")
            return {"keys_moved": 0, "affected_objects": []}

        # Define the time range for keys to move (everything AFTER move_from_frame, not including it)
        # Use a large upper bound to capture all keyframes beyond the playback range
        max_time = pm.playbackOptions(query=True, maxTime=True)
        # Add a small epsilon to exclude keys exactly at move_from_frame
        time_range = (move_from_frame + 0.001, max_time + 10000)

        # First pass: find the earliest key after gap_start across all curves
        earliest_key = None
        for curve in anim_curves:
            if selected_keys_only:
                key_times = pm.keyframe(
                    curve, query=True, sl=True, timeChange=True, time=time_range
                )
            else:
                key_times = pm.keyframe(
                    curve, query=True, timeChange=True, time=time_range
                )

            if key_times:
                curve_earliest = min(key_times)
                if earliest_key is None or curve_earliest < earliest_key:
                    earliest_key = curve_earliest

        # If no keys found, nothing to do
        if earliest_key is None:
            pm.warning(f"No keyframes found after frame {move_from_frame}.")
            return {
                "keys_moved": 0,
                "affected_objects": [],
                "gap_start": gap_start,
                "gap_end": gap_end,
                "gap_duration": gap_duration,
            }

        # Calculate the offset needed to move the earliest key to gap_end
        # This ensures the full gap range is cleared
        actual_offset = gap_end - earliest_key

        keys_moved = 0
        affected_objects_set = set()

        # Second pass: move all keys by the calculated offset
        for curve in anim_curves:
            if selected_keys_only:
                key_times = pm.keyframe(
                    curve, query=True, sl=True, timeChange=True, time=time_range
                )
            else:
                key_times = pm.keyframe(
                    curve, query=True, timeChange=True, time=time_range
                )

            if key_times:
                # Move these keys forward by actual_offset
                pm.keyframe(
                    curve,
                    edit=True,
                    time=(min(key_times), max(key_times)),
                    relative=True,
                    timeChange=actual_offset,
                )
                keys_moved += len(key_times)

                # Track the object this curve is connected to
                connections = pm.listConnections(curve, source=False, destination=True)
                if connections:
                    affected_objects_set.update(connections)

        affected_objects = list(affected_objects_set)

        # Report results
        if keys_moved > 0:
            selection_type = "selected" if selected_keys_only else "all"
            pm.displayInfo(
                f"Created gap from frame {gap_start} to {gap_end}. "
                f"First key was at {earliest_key}, moved {keys_moved} {selection_type} keyframes "
                f"by {actual_offset} frames on {len(affected_objects)} objects."
            )
        else:
            pm.warning(f"No keyframes found after frame {move_from_frame}.")

        return {
            "keys_moved": keys_moved,
            "affected_objects": affected_objects,
            "gap_start": gap_start,
            "gap_end": gap_end,
            "gap_duration": gap_duration,
            "actual_offset": actual_offset,
            "earliest_key": earliest_key,
        }

    @staticmethod
    def create_playblast(
        filepath: str = None,
        start_frame: int = None,
        end_frame: int = None,
        camera_name: str = "persp",
        **kwargs,
    ) -> str:
        """Creates a playblast in Maya, outputting to an .avi file.

        Parameters:
            filepath (str): Output path for the playblast file. Uses scene name if not specified.
            start_frame (int): Starting frame for the playblast. Defaults to Maya's playback start.
            end_frame (int): Ending frame for the playblast. Defaults to Maya's playback end.
            camera_name (str): Camera to use for the playblast. Defaults to "persp".
            **kwargs: Additional keyword arguments passed to `pm.playblast()`.

        Returns:
            str: Filepath where the playblast video was created.

        Raises:
            ValueError: If the scene is not saved, filepath is invalid, or camera doesn't exist.
            RuntimeError: If the playblast fails to create a video file.
        """
        import os

        # Format filepath using ptk.format_path
        filepath = ptk.format_path(filepath) if filepath else None

        # Default to scene name if no filepath provided or it's a directory
        if not filepath or os.path.isdir(filepath):
            scene_name = pm.sceneName()
            if not scene_name:
                raise ValueError(
                    "No scene name found. Save the scene or provide a filepath."
                )
            scene_name_with_ext = (
                os.path.basename(scene_name).rsplit(".", 1)[0] + ".avi"
            )
            filepath = os.path.join(filepath or "", scene_name_with_ext)
        else:
            if not filepath.endswith(".avi"):
                filepath += ".avi"

        # Check camera exists
        if not pm.objExists(camera_name):
            raise ValueError(f"Camera '{camera_name}' does not exist.")

        # Set frame range from playback options if not provided
        start_frame = start_frame or int(pm.playbackOptions(q=True, minTime=True))
        end_frame = end_frame or int(pm.playbackOptions(q=True, maxTime=True))

        # Define essential playblast parameters with defaults, allowing overrides from kwargs
        playblast_params = {
            "format": kwargs.get("format", "avi"),
            "compression": kwargs.get("compression", "none"),
            "forceOverwrite": kwargs.get("forceOverwrite", True),
            "viewer": kwargs.get("viewer", False),
            "widthHeight": kwargs.get("widthHeight", (1920, 1080)),
            "quality": kwargs.get("quality", 100),
            "showOrnaments": kwargs.get("showOrnaments", True),
            "percent": kwargs.get("percent", 100),
        }
        playblast_params.update(kwargs)

        # Set up and override model panel cameras
        model_panels = pm.getPanel(type="modelPanel")
        original_cameras = {
            panel: pm.modelEditor(panel, q=True, camera=True) for panel in model_panels
        }
        for panel in model_panels:
            pm.modelEditor(panel, e=True, camera=camera_name)

        try:
            pm.playblast(
                filename=filepath,
                startTime=start_frame,
                endTime=end_frame,
                **playblast_params,
            )
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                raise RuntimeError(
                    f"Playblast failed; no .avi file created at {filepath}"
                )

            print(f"Playblast video created at: {filepath}")
            return filepath
        finally:
            for panel in model_panels:
                pm.modelEditor(panel, e=True, camera=original_cameras[panel])


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
