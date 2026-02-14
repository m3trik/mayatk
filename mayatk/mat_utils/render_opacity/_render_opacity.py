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

    # Delegated constants for convenience/docs
    ATTR_NAME = OpacityAttributeMode.ATTR_NAME
    FADE_SUFFIX = OpacityMaterialMode.FADE_SUFFIX
    FADE_ATTRS = OpacityMaterialMode.FADE_ATTRS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        objects: Optional[List] = None,
        mode: str = "attribute",
    ) -> Dict[str, Dict]:
        """Create the opacity mechanism (Attribute, Material graph, or Remove).

        Running this on objects that already have a different mode applied
        will automatically clean up the previous mode first.

        Parameters:
            objects: Objects to process. If None, uses selection.
            mode: ``"attribute"`` — Adds ``opacity`` attribute (Game Engine friendly).
                  ``"material"`` — Prepares StingrayPBS material for transparency.
                  ``"remove"``   — Removes all opacity artifacts from the objects.

        Returns:
            dict: Results of the operation per object.
        """
        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

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
    @CoreUtils.undoable
    def remove(
        cls,
        objects: Optional[List] = None,
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
