# !/usr/bin/python
# coding=utf-8
"""Animation layers behind :class:`mayatk.AnimUtils`.

Creating, listing and deleting animation layers, and the throwaway override
layer :meth:`AnimUtils.create_preview_layer` plays foreign curves through.

Reached through :class:`mayatk.AnimUtils`; nothing here is called directly.
"""

try:
    import maya.cmds as cmds
except Exception:
    cmds = None


class _AnimLayerInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _create_animation_layer(
        name,
        override,
        additive,
        attributes,
        objects,
        weight,
        mute,
        solo,
        lock,
        preferred,
        parent,
        unique_name,
        timestamp_suffix,
        color,
    ):
        """Body of :meth:`AnimUtils.create_animation_layer`."""
        import time as time_module

        # Handle additive shorthand
        if additive:
            override = False

        # Build unique layer name
        layer_name = name
        if timestamp_suffix:
            timestamp = time_module.strftime("%Y%m%d_%H%M%S")
            layer_name = f"{name}_{timestamp}"

        if unique_name:
            base_name = layer_name
            counter = 1
            while cmds.objExists(layer_name):
                layer_name = f"{base_name}_{counter}"
                counter += 1

        # Create the layer
        layer = cmds.animLayer(layer_name, override=override)

        # Set layer properties
        if weight != 1.0:
            cmds.animLayer(layer, edit=True, weight=weight)

        if mute:
            cmds.animLayer(layer, edit=True, mute=True)

        if solo:
            cmds.animLayer(layer, edit=True, solo=True)

        if lock:
            cmds.animLayer(layer, edit=True, lock=True)

        if preferred:
            cmds.animLayer(layer, edit=True, preferred=True)

        if parent:
            cmds.animLayer(layer, edit=True, parent=parent)

        if color:
            # animLayer has no color flag; best-effort via the node's
            # attribute when present.  Warn instead of silently no-oping so
            # a caller relying on the color knows it didn't apply.
            try:
                if cmds.attributeQuery("ghostColor", node=layer, exists=True):
                    cmds.setAttr(f"{layer}.ghostColor", *color, type="float3")
                else:
                    cmds.warning(
                        f"create_animation_layer: '{layer}' has no color "
                        f"attribute; 'color' was ignored."
                    )
            except RuntimeError as e:
                cmds.warning(
                    f"create_animation_layer: could not set color on '{layer}': {e}"
                )

        # Add attributes from objects (all keyable attributes)
        if objects:
            for obj in objects:
                if not cmds.objExists(obj):
                    continue
                keyable_attrs = cmds.listAttr(obj, keyable=True) or []
                for attr in keyable_attrs:
                    attr_path = f"{obj}.{attr}"
                    try:
                        cmds.animLayer(layer, edit=True, attribute=attr_path)
                    except RuntimeError:
                        pass  # Attribute may not be animatable

        # Add explicit attributes
        if attributes:
            for attr_path in attributes:
                try:
                    cmds.animLayer(layer, edit=True, attribute=attr_path)
                except RuntimeError:
                    pass  # Attribute may not exist or not be animatable

        return layer

    @staticmethod
    def _get_animation_layers(include_base, muted_only, active_only):
        """Body of :meth:`AnimUtils.get_animation_layers`."""
        layers = cmds.ls(type="animLayer") or []

        if not include_base:
            layers = [lyr for lyr in layers if lyr != "BaseAnimation"]

        if muted_only:
            layers = [
                lyr for lyr in layers if cmds.animLayer(lyr, query=True, mute=True)
            ]
        elif active_only:
            layers = [
                lyr for lyr in layers if not cmds.animLayer(lyr, query=True, mute=True)
            ]

        return layers

    @staticmethod
    def _delete_animation_layer(layer, merge_to_base):
        """Body of :meth:`AnimUtils.delete_animation_layer`."""
        if not cmds.objExists(layer):
            return False

        try:
            if merge_to_base:
                # animLayer -attribute lists the plugs that live on the
                # layer — those are the bake targets.  (-affectedLayers is a
                # selection-based query and returns LAYER names, not plugs.)
                layer_plugs = cmds.animLayer(layer, query=True, attribute=True) or []
                if layer_plugs:
                    cmds.bakeResults(
                        layer_plugs,
                        destinationLayer="BaseAnimation",
                        removeBakedAttributeFromLayer=True,
                    )
            cmds.delete(layer)
            return True
        except RuntimeError as e:
            cmds.warning(f"delete_animation_layer: failed on '{layer}': {e}")
            return False

    @classmethod
    def _create_preview_layer(cls, sources, gate, name):
        """Body of :meth:`AnimUtils.create_preview_layer`."""
        # ``preferred=False``: a preferred layer becomes the target of the
        # user's own setKeyframe — a preview must never capture their keys.
        layer = cls.create_animation_layer(
            name, override=True, unique_name=True, preferred=False
        )
        pasted = 0
        for plug, src in sources.items():
            times = cmds.keyframe(src, query=True, timeChange=True) or []
            if not times:
                continue
            node, _, attr = str(plug).partition(".")
            cmds.animLayer(layer, edit=True, attribute=plug)
            # Membership alone spawns no layer curve; one key on the layer does.
            # Diffing the layer's curve list around it is the only unambiguous
            # way to learn WHICH curve is this plug's (verified Maya 2025).
            before = set(cmds.animLayer(layer, query=True, animCurves=True) or [])
            first_value = cmds.keyframe(
                src, query=True, valueChange=True, time=(times[0], times[0])
            )
            cmds.setKeyframe(
                node,
                attribute=attr,
                time=times[0],
                value=first_value[0] if first_value else 0.0,
                animLayer=layer,
            )
            after = set(cmds.animLayer(layer, query=True, animCurves=True) or [])
            new = after - before
            if len(new) != 1:
                cmds.warning(f"create_preview_layer: no layer curve spawned for {plug}")
                continue
            layer_curve = new.pop()
            cmds.copyKey(src, time=(times[0], times[-1]))
            cmds.pasteKey(layer_curve, option="replaceCompletely")
            pasted += len(times)
        if not pasted:
            cmds.delete(layer)
            raise ValueError("create_preview_layer: no source curve holds a key")
        if gate is not None:
            start, end = float(gate[0]), float(gate[1])
            for t, w in ((start - 1, 0.0), (start, 1.0), (end, 1.0), (end + 1, 0.0)):
                cmds.setKeyframe(
                    layer, attribute="weight", time=t, value=w, outTangentType="step"
                )
        return layer

    @staticmethod
    def _remove_preview_layer(layer):
        """Body of :meth:`AnimUtils.remove_preview_layer`."""
        if not layer or not cmds.objExists(layer):
            return False
        if cmds.nodeType(layer) != "animLayer":
            raise ValueError(f"remove_preview_layer: {layer!r} is not an animLayer")
        cmds.delete(layer)
        return True
