# !/usr/bin/python
# coding=utf-8
from typing import Any, Union, List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils


class DisplayUtils(ptk.HelpMixin):
    NODES_WITH_VISIBILITY = [
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

    @classmethod
    @core_utils.CoreUtils.undo
    def set_visibility(
        cls,
        elements: Union[str, object, List],
        visibility: bool = True,
        include_ancestors: bool = True,
        affect_layers: bool = True,
    ) -> None:
        """Sets the visibility of specified elements in the Maya scene.
        It accepts a wide variety of inputs for the elements parameter, including strings,
        PyMEL objects, or lists of objects. It can also optionally affect the visibility of layers
        and ancestor nodes.

        Parameters:
            elements (str, pm.nt.DependNode, list): A string that represents a Maya object type,
                           a wildcard expression, a single PyMEL object, or a list of PyMEL objects.
            visibility (bool): The visibility state to apply. If True, elements are shown; if False, elements are hidden.
            include_ancestors (bool): If True, will also set visibility for all ancestor transform nodes of the elements.
            affect_layers (bool): If True, will ensure that all layers except the default layer have their visibility set.

        Example:
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

        elements = [elements] if isinstance(elements, str) else elements
        if set(elements).intersection(cls.NODES_WITH_VISIBILITY):
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

    @staticmethod
    def set_wireframe_on_shaded(editor: str, state: bool, **kwargs: Any) -> None:
        """Set wireframe on shaded for the specified model editor panel.

        Parameters:
            editor (str): The name of the model editor panel.
            state (bool): True to enable wireframe on shaded, False to disable.
            kwargs: Additional keyword arguments for the modelEditor command.

        Notes:
            The displayAppearance parameter in modelEditor command can have values:
            "wireframe", "points", "boundingBox", "smoothShaded", "flatShaded".
            This method adjusts the wireframeOnShaded setting based on the shading mode.
        """
        modeIsShaded = pm.modelEditor(editor, query=True, displayAppearance=True) in [
            "smoothShaded",
            "flatShaded",
        ]

        if state and modeIsShaded:
            pm.modelEditor(editor, edit=True, wireframeOnShaded=1, **kwargs)
        else:
            pm.modelEditor(editor, edit=True, wireframeOnShaded=0, **kwargs)

    @staticmethod
    def add_to_isolation_set(objects: Union[str, object, List[Union[str, object]]]):
        """Adds the specified transform objects to the current isolation set if isolation mode is active in the current view panel.

        Parameters:
            objects (str, obj, list): Transform objects to be added to the isolation set.
        """
        # Use pm.ls to ensure all inputs are converted to PyMel transform nodes, even if passed as strings
        objects = pm.ls(objects, type="transform")

        # Get the currently active model panel
        currentPanel = pm.paneLayout("viewPanes", q=True, pane1=True)

        # Check if isolation mode is active
        if pm.modelEditor(currentPanel, q=True, viewSelected=True):
            # Retrieve the isolation set associated with the current panel
            isoSet = pm.modelEditor(currentPanel, q=True, viewObjects=True)

            # Add the specified transform nodes to the isolation set
            for obj in objects:
                if pm.objExists(obj.name()):  # Ensure the object exists in the scene
                    pm.sets(isoSet, add=obj)
        else:
            print("Isolation mode is not active in the current view panel.")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
