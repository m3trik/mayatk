# !/usr/bin/python
# coding=utf-8
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError:
    pass

from mayatk.anim_utils._anim_utils import AnimUtils


class FadeAttributeMode(ptk.LoggingMixin):
    """
    Implements the 'attribute' mode for MaterialFade.

    This mode adds a custom 'fade' float attribute to each object's transform
    and keyframes it. Recommended for per-object control.
    """

    ATTR_NAME = "fade"
    """Custom attribute name used in ``"attribute"`` mode."""

    @classmethod
    def setup(
        cls,
        objects,
        start_frame,
        end_frame,
        val_start,
        val_end,
        warn_no_other_keys=True,
    ) -> Dict[str, Dict]:
        """Add and keyframe 'fade' on each transform."""
        results = {}
        for obj in objects:
            if not obj.hasAttr(cls.ATTR_NAME):
                obj.addAttr(
                    cls.ATTR_NAME,
                    attributeType="float",
                    keyable=True,
                    minValue=0.0,
                    maxValue=1.0,
                    defaultValue=1.0,
                )
                cls.logger.info(f"Added {cls.ATTR_NAME} to {obj}")

            # Key the attribute
            AnimUtils.set_keys_for_attributes(
                [obj], target_times=[start_frame], **{cls.ATTR_NAME: val_start}
            )
            AnimUtils.set_keys_for_attributes(
                [obj], target_times=[end_frame], **{cls.ATTR_NAME: val_end}
            )
            results[obj.name()] = {"attrs_keyed": [f"{obj.name()}.{cls.ATTR_NAME}"]}

            if warn_no_other_keys:
                # Check for existing keys on standard transform attributes.
                has_transform_keys = False
                for attr in ["translate", "rotate", "scale", "visibility"]:
                    if pm.keyframe(obj, attribute=attr, query=True, keyframeCount=True):
                        has_transform_keys = True
                        break

                if not has_transform_keys:
                    cls.logger.warning(
                        f"[{obj.name()}] has only '{cls.ATTR_NAME}' keyed. "
                        "Unity requires at least one transform key (e.g. Translate) to create an AnimationClip. "
                        "Please add a static key to a standard attribute."
                    )

            cls.logger.info(
                f"Keyed {cls.ATTR_NAME} on {obj}: {val_start} -> {val_end} "
                f"({start_frame}-{end_frame})"
            )
        return results

    @classmethod
    def bake(cls, objects, frame_range, sample_by, optimize):
        # Delegate to AnimUtils.bake with filtering
        baked = AnimUtils.bake(
            objects,
            attributes=[cls.ATTR_NAME],
            time_range=(frame_range[0], frame_range[1]),
            sample_by=sample_by,
            preserve_outside_keys=True,
            simulation=False,
            only_keyed=True,
        )

        for curve in baked:
            cls.logger.info(f"Baked fade curve: {curve}")

        if optimize and baked:
            AnimUtils.optimize_keys(objects)

    @classmethod
    def remove(cls, objects):
        for obj in objects:
            if not obj.hasAttr(cls.ATTR_NAME):
                continue
            # Delete anim curves first (deleteAttr errors on connected attrs)
            curves = pm.listConnections(obj.attr(cls.ATTR_NAME), type="animCurve")
            if curves:
                pm.delete(curves)
            obj.deleteAttr(cls.ATTR_NAME)
            cls.logger.info(f"Removed {cls.ATTR_NAME} from {obj}")
