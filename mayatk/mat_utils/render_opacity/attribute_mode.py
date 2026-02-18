# !/usr/bin/python
# coding=utf-8
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError:
    pass

from mayatk.node_utils.attributes._attributes import Attributes


class OpacityAttributeMode(ptk.LoggingMixin):
    """
    Implements the 'attribute' mode for RenderOpacity.

    This mode adds a custom ``opacity`` float attribute (0-1) to each object's
    transform.  Recommended for per-object control in Game Engines.

    .. note:: Keyframing logic has been removed to serve as a pure attribute framework.
    """

    ATTR_NAME = "opacity"
    """Custom attribute name used in ``"attribute"`` mode."""

    @classmethod
    def create(cls, objects) -> Dict[str, Dict]:
        """Add 'opacity' attribute on each transform (no keyframes)."""
        results = {}

        Attributes.apply_preset("opacity", objects)

        for obj in pm.ls(objects):
            # Drive Visibility from Opacity
            # Logic: If Opacity > 0 -> Visibility = 1. Else 0.
            # This allows the engine/viewport to cull the object completely when fully transparent.
            cls._connect_visibility_driver(obj)

            results[obj.name()] = {"attrs_created": [f"{obj.name()}.{cls.ATTR_NAME}"]}
            cls.logger.info(f"Verified {cls.ATTR_NAME} on {obj}")

        return results

    @classmethod
    def _connect_visibility_driver(cls, obj):
        """Connect opacity -> condition -> visibility."""
        if obj.visibility.isLocked():
            return

        # Check if already driven by our specific condition setup
        inputs = obj.visibility.inputs()
        if (
            inputs
            and isinstance(inputs[0], pm.nt.Condition)
            and inputs[0].name().endswith("_VisDriver")
        ):
            return  # Already setup

        if inputs and not isinstance(inputs[0], pm.nt.Condition):
            # Driven by something else (e.g. animator), don't stomp.
            cls.logger.info(
                f"[{obj.name()}] Visibility already driven by {inputs[0]}. Skipping auto-hide setup."
            )
            return

        # Create Condition Node
        # if opacity > 0: visibility = 1
        # else: visibility = 0
        cond = pm.createNode("condition", name=f"{obj.nodeName()}_VisDriver")
        cond.operation.set(2)  # Greater Than
        cond.secondTerm.set(0.0)
        cond.colorIfTrueR.set(1.0)
        cond.colorIfFalseR.set(0.0)

        pm.connectAttr(obj.attr(cls.ATTR_NAME), cond.firstTerm)
        pm.connectAttr(cond.outColorR, obj.visibility, force=True)

    @classmethod
    def remove(cls, objects):
        for obj in objects:
            if not obj.hasAttr(cls.ATTR_NAME):
                continue

            # Clean up Visibility Driver
            inputs = obj.visibility.inputs()
            if (
                inputs
                and isinstance(inputs[0], pm.nt.Condition)
                and inputs[0].name().endswith("_VisDriver")
            ):
                pm.delete(inputs[0])
                # Reset visibility to default
                obj.visibility.set(True)

            # Delete anim curves first (deleteAttr errors on connected attrs)
            curves = pm.listConnections(obj.attr(cls.ATTR_NAME), type="animCurve")
            if curves:
                pm.delete(curves)
            obj.deleteAttr(cls.ATTR_NAME)
            cls.logger.info(f"Removed {cls.ATTR_NAME} from {obj}")
