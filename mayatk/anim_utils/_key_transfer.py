# !/usr/bin/python
# coding=utf-8
"""Key copy / paste / transfer behind :class:`mayatk.AnimUtils`.

:meth:`AnimUtils.copy_keys` / :meth:`AnimUtils.paste_keys` (attribute values
captured and re-keyed with their tangents) and
:meth:`AnimUtils.transfer_keyframes` (one object's keys onto others).

Reached through :class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Dict, Optional, Set

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None


from mayatk.anim_utils._tangents import (
    _KEYTANGENT_IN_TANGENT_REMAP,
    _SETKEY_IN_TANGENT_REMAP,
    _SETKEY_OUT_TANGENT_REMAP,
)


class _KeyTransferInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @classmethod
    def _transfer_keyframes(cls, objects, relative, transfer_tangents, optimize):
        """Body of :meth:`AnimUtils.transfer_keyframes`."""
        resolved_objects = cmds.ls(objects, long=True)
        if len(resolved_objects) < 2:
            cmds.warning("Please provide at least one source and one target object.")
            return

        source_obj = resolved_objects[0]
        target_objs = resolved_objects[1:]

        if optimize:
            cls.optimize_keys([source_obj], quiet=True)

        # Check if keyframes are selected, if not use all keyframes
        selected_curves = cmds.keyframe(
            source_obj, query=True, name=True, selected=True
        )

        if selected_curves:
            # Use only selected keyframes and their attributes
            keyframe_times = cls.get_keyframe_times(
                selected_curves, mode="selected", from_curves=True
            )
            keyframe_attributes = cls._curves_to_attributes(selected_curves, source_obj)
        else:
            # Use all animation curves and keyframes from the source object
            all_curves = cls.objects_to_curves([source_obj])
            if not all_curves:
                cmds.warning(f"No keyframes found on source object '{source_obj}'.")
                return

            keyframe_times = cls.get_keyframe_times(all_curves, from_curves=True)
            keyframe_attributes = cls._curves_to_attributes(all_curves, source_obj)

        if not keyframe_times or not keyframe_attributes:
            cmds.warning(f"No keyframes found on source object '{source_obj}'.")
            return

        # Store initial values for target objects (for relative mode)
        initial_values = {
            target: {
                attr: cmds.getAttr(f"{target}.{attr}")
                for attr in keyframe_attributes
                if cmds.attributeQuery(attr, node=str(target), exists=True)
            }
            for target in target_objs
        }

        src_str = str(source_obj)

        # Copy keyframes from source to each target
        for target_obj in target_objs:
            for attr in keyframe_attributes:
                try:
                    if not cmds.attributeQuery(attr, node=str(target_obj), exists=True):
                        cmds.warning(
                            f"Skipping attribute '{attr}': not found on '{target_obj}'."
                        )
                        continue

                    initial_value = initial_values[target_obj].get(attr)
                    if initial_value is None:
                        continue

                    src_plug = f"{src_str}.{attr}"
                    tgt_plug = f"{target_obj}.{attr}"

                    # Pre-compute the relative offset once per attribute using
                    # this attribute's own first key (not the global earliest).
                    relative_offset = 0.0
                    if relative:
                        attr_first_val = cmds.keyframe(
                            src_plug,
                            query=True,
                            time=(keyframe_times[0], keyframe_times[-1]),
                            valueChange=True,
                        )
                        if attr_first_val:
                            relative_offset = initial_value - attr_first_val[0]

                    for time in keyframe_times:
                        values = cmds.keyframe(
                            src_plug,
                            query=True,
                            time=(time,),
                            valueChange=True,
                        )
                        if values:
                            value = values[0]
                            if relative:
                                value += relative_offset

                            cmds.setKeyframe(tgt_plug, time=time, value=value)

                            if transfer_tangents:
                                tangent_info = cls.get_tangent_info(src_plug, time)
                                cls.set_tangent_info(tgt_plug, time, tangent_info)
                except Exception as e:
                    cmds.warning(
                        f"Could not transfer attribute '{attr}' to '{target_obj}': {e}"
                    )

    @classmethod
    def _copy_keys(cls, objects, mode, resolution_order, tangent_detail):
        """Body of :meth:`AnimUtils.copy_keys`."""
        if objects is None:
            objects = cmds.ls(selection=True)
        objects = cmds.ls(objects, flatten=True, long=True)
        if not objects:
            cmds.warning("No objects specified or selected.")
            return {}

        # Resolve "auto" into the best concrete mode, with optional CB filter.
        # Pass pre-resolved *objects* so _resolve_keys skips redundant cmds.ls.
        cb_filter: Optional[Set[str]] = None
        if mode == "auto":
            resolved = cls._resolve_keys(
                objects,
                mode="auto",
                resolution_order=resolution_order
                or ("selected", "channel_box", "current_frame"),
            )
            mode = resolved["mode"]
            cb_filter = resolved["cb_attrs"]

        result: Dict[str, Dict[str, float]] = {}

        if mode == "current_frame":
            current = cmds.currentTime(query=True)
            for obj in objects:
                obj_str = str(obj)
                curves = (
                    cmds.listConnections(
                        obj_str, type="animCurve", source=True, destination=False
                    )
                    or []
                )
                if not curves:
                    continue
                obj_data: Dict[str, float] = {}
                for crv in curves:
                    # Get the attribute this curve drives
                    conns = (
                        cmds.listConnections(
                            crv, destination=True, source=False, plugs=True
                        )
                        or []
                    )
                    for plug in conns:
                        attr = plug.split(".")[-1]
                        # A shared curve can also drive plugs on OTHER
                        # nodes — only read attrs that exist on this one.
                        if not cmds.attributeQuery(attr, node=obj_str, exists=True):
                            continue
                        try:
                            obj_data[attr] = cmds.getAttr(
                                f"{obj_str}.{attr}", time=current
                            )
                        except RuntimeError:
                            continue
                if obj_data:
                    result[obj_str] = obj_data

        elif mode == "selected":
            # Get curves with selected keys, scoped to the given objects —
            # the scene-wide graph-editor selection may include curves that
            # belong to unrelated objects.
            sel_curves = cmds.keyframe(query=True, selected=True, name=True) or []
            if sel_curves and objects:
                obj_curve_set = set(cls.objects_to_curves(objects))
                sel_curves = [c for c in sel_curves if c in obj_curve_set]
            if not sel_curves:
                cmds.warning("No keys selected in the Graph Editor.")
                return {}

            def _collect_selected_keys(curves, attr_filter):
                """Collect key data from *curves*, optionally filtering by attrs."""
                collected: Dict[str, Dict[str, Any]] = {}
                for crv in curves:
                    conns = (
                        cmds.listConnections(
                            crv, destination=True, source=False, plugs=True
                        )
                        or []
                    )
                    if not conns:
                        continue
                    plug = conns[0]
                    parts = plug.split(".", 1)
                    obj_name = parts[0]
                    attr = parts[1] if len(parts) > 1 else ""
                    if not attr:
                        continue
                    # attr_filter carries Channel Box SHORT names — match
                    # against all spellings of this plug's attribute.
                    if attr_filter is not None and cls._plug_attr_names(
                        plug
                    ).isdisjoint(attr_filter):
                        continue
                    times = (
                        cmds.keyframe(crv, query=True, selected=True, timeChange=True)
                        or []
                    )
                    values = (
                        cmds.keyframe(crv, query=True, selected=True, valueChange=True)
                        or []
                    )
                    if times and values:
                        key_list = []
                        for t, v in zip(times, values):
                            itt = cmds.keyTangent(
                                crv, q=True, time=(t, t), inTangentType=True
                            )
                            ott = cmds.keyTangent(
                                crv, q=True, time=(t, t), outTangentType=True
                            )
                            kd = {
                                "time": t,
                                "value": v,
                                "inTangentType": itt[0] if itt else "auto",
                                "outTangentType": ott[0] if ott else "auto",
                            }
                            if tangent_detail:
                                ia = cmds.keyTangent(
                                    crv, q=True, time=(t, t), inAngle=True
                                )
                                oa = cmds.keyTangent(
                                    crv, q=True, time=(t, t), outAngle=True
                                )
                                iw = cmds.keyTangent(
                                    crv, q=True, time=(t, t), inWeight=True
                                )
                                ow = cmds.keyTangent(
                                    crv, q=True, time=(t, t), outWeight=True
                                )
                                kd["inAngle"] = ia[0] if ia else 0.0
                                kd["outAngle"] = oa[0] if oa else 0.0
                                kd["inWeight"] = iw[0] if iw else 1.0
                                kd["outWeight"] = ow[0] if ow else 1.0
                            key_list.append(kd)
                        attr_entry = key_list
                        if tangent_detail:
                            pre = cmds.setInfinity(plug, q=True, preInfinite=True)
                            post = cmds.setInfinity(plug, q=True, postInfinite=True)
                            attr_entry = {
                                "keys": key_list,
                                "preInfinity": pre[0] if pre else "constant",
                                "postInfinity": post[0] if post else "constant",
                            }
                        collected.setdefault(obj_name, {})[attr] = attr_entry
                return collected

            result = _collect_selected_keys(sel_curves, cb_filter)
            # If the CB filter eliminated everything, fall back to all
            # selected keys so the user isn't silently blocked.
            if not result and cb_filter is not None:
                result = _collect_selected_keys(sel_curves, None)

        else:  # channel_box (default)
            attrs = cls._get_channel_box_attrs()
            if attrs:
                for obj in objects:
                    obj_data = {}
                    for attr in attrs:
                        try:
                            obj_data[attr] = cmds.getAttr(f"{obj}.{attr}")
                        except (RuntimeError, ValueError):
                            continue  # Attr absent/unreadable on this object.
                    if obj_data:
                        result[str(obj)] = obj_data

        return result

    @classmethod
    def _paste_keys(
        cls,
        objects,
        copied_data,
        target_time,
        match_source,
        refresh_channel_box,
        **kwargs,
    ):
        """Body of :meth:`AnimUtils.paste_keys`."""
        if not copied_data:
            cmds.warning("No copied data to paste.")
            return 0

        if objects is None:
            objects = cmds.ls(selection=True)
        objects = cmds.ls(objects, flatten=True, long=True)
        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        if target_time is None:
            target_time = cmds.currentTime(query=True)

        # When not matching by name, merge all source attrs into one dict
        merged_attrs: Optional[Dict[str, Any]] = None
        if not match_source:
            merged_attrs = {}
            for src_attrs in copied_data.values():
                merged_attrs.update(src_attrs)

        keys_set = 0

        for obj in objects:
            if not match_source:
                obj_attrs = merged_attrs
            else:
                obj_name = str(obj)
                short_name = obj_name.split("|")[-1]

                # Try to find matching stored data
                obj_attrs = copied_data.get(obj_name)
                if not obj_attrs:
                    obj_attrs = copied_data.get(short_name)
                if not obj_attrs:
                    for stored_name in copied_data:
                        if stored_name.split("|")[-1] == short_name:
                            obj_attrs = copied_data[stored_name]
                            break

            if obj_attrs:
                for attr, data in obj_attrs.items():
                    plug = f"{obj}.{attr}"
                    # Unwrap tangent_detail envelope if present.
                    infinity = None
                    if isinstance(data, dict) and "keys" in data:
                        infinity = (
                            data.get("preInfinity", "constant"),
                            data.get("postInfinity", "constant"),
                        )
                        data = data["keys"]
                    if isinstance(data, list):
                        # --- Multi-key paste (selected mode) ---
                        if not data:
                            continue
                        # Multi-key blocks paste at a single anchor time —
                        # take the first entry of a list target.
                        anchor = target_time
                        if isinstance(anchor, (list, tuple)):
                            if len(anchor) > 1:
                                cmds.warning(
                                    "paste_keys: multi-key data pastes at a "
                                    "single time; using the first target time."
                                )
                            anchor = anchor[0]
                        base_time = data[0]["time"]
                        offset = float(anchor) - base_time

                        for kd in data:
                            t = kd["time"] + offset
                            v = kd["value"]
                            itt = kd.get("inTangentType", "auto")
                            ott = kd.get("outTangentType", "auto")
                            kw = dict(
                                time=t,
                                value=v,
                                # setKeyframe rejects some tangent types
                                # keyTangent accepts ("fixed" on either side,
                                # "step" in) — remap those only here; the
                                # set_tangent_info pass below restores the
                                # stored types verbatim.
                                inTangentType=_SETKEY_IN_TANGENT_REMAP.get(itt, itt),
                                outTangentType=_SETKEY_OUT_TANGENT_REMAP.get(ott, ott),
                            )
                            kw.update(kwargs)
                            cmds.setKeyframe(plug, **kw)
                            # Re-assert the ORIGINAL stored tangent data via
                            # set_tangent_info: angles/weights first, types
                            # last.  The final type pass keeps auto types
                            # (spline/auto/clamped) as themselves instead of
                            # the implicit "fixed" an angle edit causes, and
                            # re-asserting a stored "fixed" keeps its exact
                            # angle (remapping it to "auto" — the old
                            # behavior — let Maya recalculate the handle).
                            tangent_info = {
                                # "step" is out-tangent-only; its in-side
                                # form is "stepnext".
                                "inTangentType": _KEYTANGENT_IN_TANGENT_REMAP.get(
                                    itt, itt
                                ),
                                "outTangentType": ott,
                            }
                            if "inAngle" in kd:
                                tangent_info.update(
                                    inAngle=kd["inAngle"],
                                    outAngle=kd["outAngle"],
                                    inWeight=kd["inWeight"],
                                    outWeight=kd["outWeight"],
                                )
                            try:
                                cls.set_tangent_info(plug, t, tangent_info)
                            except RuntimeError as e:
                                cmds.warning(
                                    f"paste_keys: tangent restore on {plug} "
                                    f"at {t} failed: {e}"
                                )
                        # Restore infinity types when present (undoable,
                        # unlike an MFnAnimCurve edit).
                        if infinity:
                            try:
                                cmds.setInfinity(
                                    plug,
                                    preInfinite=infinity[0],
                                    postInfinite=infinity[1],
                                )
                            except RuntimeError as e:
                                cmds.warning(
                                    f"paste_keys: could not restore infinity "
                                    f"on {plug}: {e}"
                                )
                    else:
                        # --- Scalar paste (current_frame / channel_box) ---
                        times = (
                            [target_time]
                            if not isinstance(target_time, (list, tuple))
                            else list(target_time)
                        )
                        for t in times:
                            cls._set_key_preserving_tangents(plug, t, data, **kwargs)
                keys_set += 1

        if refresh_channel_box:
            mel.eval("channelBoxCommand -update;")

        return keys_set
