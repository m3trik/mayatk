# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils


class DisplayUtils(ptk.HelpMixin):
    @staticmethod
    @core_utils.CoreUtils.undo
    def set_visibility(
        elements, visibility=True, include_ancestors=True, affect_layers=True
    ):
        """Sets the visibility of specified elements in the Maya scene.
        It accepts a wide variety of inputs for the elements parameter, including strings,
        PyMEL objects, or lists of objects. It can also optionally affect the visibility of layers
        and ancestor nodes.

        Parameters:
        elements (str | pm.nt.DependNode | list): A string that represents a Maya object type,
                       a wildcard expression, a single PyMEL object, or a list of PyMEL objects.
        visibility (bool): The visibility state to apply. If True, elements are shown; if False, elements are hidden.
        include_ancestors (bool): If True, will also set visibility for all ancestor transform nodes of the elements.
        affect_layers (bool): If True, will ensure that all layers except the default layer have their visibility set.

        Usage:
        set_visibility('geometry', visibility=True)  # Shows all geometry and their ancestors, affects layers.
        set_visibility('lights', visibility=False, include_ancestors=False)  # Hides all lights without affecting their ancestors.
        set_visibility('nurbsCurves', visibility=True, affect_layers=False)  # Shows all nurbsCurves, doesn't affect layers.
        set_visibility([my_geo1, my_geo2], visibility=False)  # Hides specific geometries provided in a list.
        set_visibility('pCube*', visibility=True)  # Shows all objects with names starting with 'pCube'.
        """
        if affect_layers:
            # Set visibility for all layers except the default layer
            for layer in pm.ls(type="displayLayer"):
                if layer.name() != "defaultLayer" and not layer.isReferenced():
                    try:
                        layer.visibility.set(visibility)
                    except pm.MayaAttributeError:
                        pass  # Skip the layer if visibility cannot be set

        common_visibility_types = [
            "mesh",
            "nurbsCurve",
            "nurbsSurface",
            "subdiv",
            "camera",
            "joint",
            "light",
            "locator",
            "transform",
        ]
        elements = [elements] if isinstance(elements, str) else elements
        if set(elements).intersection(common_visibility_types):
            scene_elements = pm.ls(type=elements)
        else:
            # Use it as a search pattern or type name
            scene_elements = pm.ls(elements)

        for element in scene_elements:
            if include_ancestors:
                # Set visibility for ancestor transform nodes
                ancestors = [
                    ancestor
                    for ancestor in element.getAllParents()
                    if isinstance(ancestor, pm.nt.Transform)
                ]
                for ancestor in ancestors:
                    try:
                        ancestor.visibility.set(visibility)
                    except pm.MayaAttributeError:
                        pass  # Skip the ancestor if visibility cannot be set

            # Set the visibility of the element
            try:
                element.visibility.set(visibility)
            except pm.MayaAttributeError:
                pass  # Skip the element if visibility cannot be set


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
