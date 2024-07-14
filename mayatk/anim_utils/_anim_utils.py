# !/usr/bin/python
# coding=utf-8
from typing import List, Dict, ClassVar, Optional, Union, Any

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils


class AnimUtils(ptk.HelpMixin):
    """ """

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
        value = cls.FRAME_RATE_VALUES.get(key, 0)
        if value == 0:
            return "Unknown Frame Rate"
        else:
            return f"{value} fps {key.upper()}"

    @staticmethod
    def setCurrentFrame(time=1, update=True, relative=False):
        """Set the current frame on the timeslider.

        Parameters:
        time (int): The desired frame number.
        update (bool): Change the current time, but do not update the world. (default=True)
        relative (bool): If True; the frame will be moved relative to
                    it's current position using the frame value as a move amount.
        Example:
            setCurrentFrame(24, relative=True, update=1)
        """
        currentTime = 0
        if relative:
            currentTime = pm.currentTime(query=True)

        pm.currentTime(currentTime + time, edit=True, update=update)

    @staticmethod
    @CoreUtils.undo
    def set_keys_for_attributes(objects, **kwargs):
        """Sets keyframes for the specified attributes on given objects at given times.

        Parameters:
            objects (list): The objects to set the keyframes on.
            **kwargs: Attribute/value pairs and optionally 'target_times'.
                      If 'target_times' is not provided, the current Maya time is used.
        Usage:
            set_keys_for_attributes(objects, translateX=5, translateY=10)
            set_keys_for_attributes(objects, translateX=5, target_times=[10, 15, 20])
        """
        target_times = kwargs.pop("target_times", [pm.currentTime(query=True)])
        if isinstance(target_times, int):
            target_times = [target_times]

        for obj in pm.ls(objects):
            for attr, value in kwargs.items():
                attr_full_name = f"{obj}.{attr}"
                for time in target_times:
                    pm.setKeyframe(attr_full_name, time=time, value=value)

    @staticmethod
    def filter_objects_with_keys(
        objects: Optional[Union[str, List[str]]] = None,
        keys: Optional[List[str]] = None,
    ) -> List[pm.nt.Transform]:
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
    @CoreUtils.undo
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
    @CoreUtils.undo
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
    @CoreUtils.undo
    def stagger_keyframes(
        objects: list,
        offset: int = 1,
        invert: bool = False,
        smooth_tangents: bool = False,
    ):
        """Stagger the keyframes of selected objects such that the keyframes of each subsequent object start after the previous object.

        If keys are selected, only those keys are staggered. If no keys are selected, all keys are staggered.

        Parameters:
            objects (list): List of objects whose keyframes need to be staggered.
            offset (int, optional): Offset amount to adjust the keyframe stagger (default is 1).
            invert (bool, optional): If True, the objects list is processed in reverse order (default is False).
            smooth_tangents (bool, optional): If True, adjusts tangents for smooth transitions (default is False).
        """
        if not objects:
            pm.warning("No objects provided.")
            return

        objects = pm.ls(objects, type="transform")
        if invert:
            objects = list(reversed(objects))

        # Determine the first keyframe to maintain the starting frame consistency
        first_keyframe = None
        for obj in objects:
            keyframes = pm.keyframe(obj, query=True, selected=True)
            if not keyframes:
                keyframes = pm.keyframe(obj, query=True)

            if keyframes:
                keyframes = sorted(set(keyframes))
                if first_keyframe is None or keyframes[0] < first_keyframe:
                    first_keyframe = keyframes[0]

        if first_keyframe is None:
            pm.warning("No keyframes found on the provided objects.")
            return

        previous_end_frame = first_keyframe

        for obj in objects:
            keyframes = pm.keyframe(obj, query=True, selected=True)
            if not keyframes:
                keyframes = pm.keyframe(obj, query=True)

            if keyframes:
                keyframes = sorted(set(keyframes))

                if previous_end_frame is not None:
                    shift_amount = previous_end_frame + offset - keyframes[0]
                    pm.keyframe(
                        obj,
                        edit=True,
                        time=(keyframes[0], keyframes[-1]),
                        relative=True,
                        timeChange=shift_amount,
                    )

                    previous_end_frame = keyframes[-1] + shift_amount
                else:
                    previous_end_frame = keyframes[-1] + offset

        if smooth_tangents:
            # Ensure smooth transitions if necessary
            for obj in objects:
                try:
                    pm.keyTangent(
                        obj, edit=True, outTangentType="auto", inTangentType="auto"
                    )
                except RuntimeError as e:
                    pm.warning(f"Failed to adjust tangents for {obj}: {e}")

    @classmethod
    @CoreUtils.undo
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
    def delete_keys(objects, *attributes, time=None):
        """Deletes keyframes for specified attributes on given objects, optionally within a time range.

        This function can delete keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. The time range can be a single integer (to delete keyframes at a specific time)
        or a tuple/list of two integers specifying the start and end times.

        Parameters:
            objects (list): The list of objects from which to delete keyframes.
            *attributes (str): Variable length argument list of attribute names.
                               If empty, keyframes for all attributes will be deleted.
                               Can accept a list by unpacking when calling the function using *
            time (None, int, tuple, list): Specifies the time range for keyframe deletion.
                                           Accepts a single integer (specific time),
                                           a tuple/list of two integers (start and end time),
                                           or None (entire timeline).
        Example Usage:
            delete_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Delete keyframes at time=10 for specified attributes
            delete_keys([obj1, obj2], time=(5, 15)) # Delete all keyframes between time 5 and 15
            delete_keys([obj1, obj2], 'rotateX', 'rotateY') # Delete all keyframes for 'rotateX' and 'rotateY'
        """
        # Determine time range for deletion
        time_range = None
        if isinstance(time, (list, tuple)) and len(time) == 2:
            time_range = (time[0], time[1])
        elif isinstance(time, int):
            time_range = (time, time)

        for obj in objects:
            if attributes:
                # Delete keyframes for specified attributes
                for attr in attributes:
                    if time_range:
                        pm.cutKey(f"{obj}.{attr}", time=time_range, clear=True)
                    else:
                        pm.cutKey(f"{obj}.{attr}", clear=True)
            else:
                # Delete keyframes for all attributes
                if time_range:
                    pm.cutKey(obj, time=time_range, clear=True)
                else:
                    pm.cutKey(obj, clear=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
