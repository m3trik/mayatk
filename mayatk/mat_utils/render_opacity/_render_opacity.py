# !/usr/bin/python
# coding=utf-8
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
except ImportError:
    pass

# From this package:
from mayatk.core_utils._core_utils import CoreUtils

# Import delegate classes
from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode
from mayatk.mat_utils.render_opacity.material_mode import OpacityMaterialMode


class RenderOpacity(ptk.LoggingMixin):
    """
    Manages per-object opacity for engine-ready transparency control.

    Adds a keyable ``opacity`` attribute to object transforms and optionally
    prepares the material graph for viewport feedback.

    .. note:: This class does not handle animation/keyframing directly.
              It sets up the *mechanism* (Attribute or Shader Graph) for you
              to animate manually or via valid pipeline export paths.

    Two modes of operation:

    **mode="attribute"** (Recommended):
        Adds a custom ``opacity`` float attribute (0-1) to object transforms.
        Use for per-object opacity in Game Engines.

    **mode="material"** (Legacy):
        Loads the Transparency graph onto StingrayPBS materials.
    """

    ATTR_NAME = OpacityAttributeMode.ATTR_NAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def objects_with_visibility_keys(cls, objects) -> List:
        """Return the subset of *objects* that have keyframes on visibility."""
        result = []
        for obj in objects:
            try:
                keys = pm.keyframe(
                    obj, attribute="visibility", query=True, timeChange=True
                )
                if keys:
                    result.append(obj)
            except Exception:
                pass
        return result

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        objects=None,
        mode: str = "attribute",
        delete_visibility_keys: bool = False,
    ) -> Dict[str, Dict]:
        """Create the opacity mechanism (Attribute, Material graph, or Remove).

        Running this on objects that already have a different mode applied
        will automatically clean up the previous mode first.

        Parameters:
            objects: Objects to process. If None, uses selection.
            mode: ``"attribute"`` — Adds ``opacity`` attribute (Game Engine friendly).
                  ``"material"`` — Prepares StingrayPBS material for transparency.
                  ``"remove"``   — Removes all opacity artifacts from the objects.
            delete_visibility_keys: If ``True``, existing visibility keyframes
                are deleted before creating the opacity setup.  If ``False``
                (default), objects that have visibility keys are skipped and
                a warning is logged.

        Returns:
            dict: Results of the operation per object.

        Raises:
            RuntimeError: When *delete_visibility_keys* is ``False`` and one
                or more objects have visibility keyframes.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        # --- Handle existing visibility keys --------------------------
        vis_keyed = cls.objects_with_visibility_keys(objects)
        if vis_keyed:
            names = [o.name() for o in vis_keyed]
            if delete_visibility_keys:
                for obj in vis_keyed:
                    pm.cutKey(obj, attribute="visibility", clear=True)
                    # Reset visibility to on after removing keys
                    obj.visibility.set(True)
                cls.logger.info("Deleted visibility keys on: %s", ", ".join(names))
            else:
                msg = (
                    f"Visibility keys found on: {', '.join(names)}. "
                    "Enable 'Delete Visibility Keys' or remove them "
                    "manually before applying opacity."
                )
                raise RuntimeError(msg)

        # Always clean existing state first.  Material mode must be
        # removed before attribute mode because the proxy disconnect
        # needs the opacity attribute to still exist on the transform.
        cls.remove(objects)

        if mode == "remove":
            return {}
        elif mode == "attribute":
            return OpacityAttributeMode.create(objects)
        elif mode == "material":
            return OpacityMaterialMode.create(objects)
        else:
            cls.logger.error(f"Unknown mode: {mode}")
            return {}

    # Legacy alias support
    setup = create

    @classmethod
    def ensure_connections(cls, objects=None) -> None:
        """Re-establish opacity driver connections on objects that already
        have the ``opacity`` attribute but lost their wiring — typically
        after a **Duplicate** operation in Maya.

        This is lightweight and idempotent; safe to call before every
        keyframe operation.

        Parameters:
            objects: Objects to check. If *None*, uses the current selection.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            return
        OpacityAttributeMode.ensure_connections(objects)
        OpacityMaterialMode.ensure_connections(objects)

    @classmethod
    def remove(
        cls,
        objects=None,
        mode: Optional[str] = None,
    ) -> None:
        """Remove attributes or reset material settings.

        Parameters:
            objects: Objects to clean. If None, uses selection.
            mode: ``"attribute"``, ``"material"``, or ``None`` (cleans both).
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            # cls.logger.warning("No objects selected.")
            return

        modes = [mode] if mode else ["material", "attribute"]

        # Material mode must be cleaned BEFORE attribute mode — the
        # proxy disconnect needs the opacity attribute to still exist.
        if "material" in modes:
            OpacityMaterialMode.remove(objects)
        if "attribute" in modes:
            OpacityAttributeMode.remove(objects)
