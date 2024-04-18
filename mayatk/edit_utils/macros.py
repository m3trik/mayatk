# !/usr/bin/python
# coding=utf-8
from typing import Set

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

# from this package:
from mayatk.core_utils import _core_utils
from mayatk.core_utils.macro_manager import MacroManager
from mayatk import node_utils


class DisplayMacros:
    """ """

    @staticmethod
    def m_back_face_culling() -> None:
        """Toggle Back-Face Culling on selected objects, or on all objects if none are selected."""
        objects = pm.ls(selection=True) or pm.ls(type="mesh")
        if objects:
            state: bool = pm.polyOptions(objects, query=True, wireBackCulling=True)[0]
            if state:
                pm.polyOptions(objects, wireBackCulling=False, backCulling=True)
                message = "OFF"
            else:
                pm.polyOptions(objects, wireBackCulling=True, backCulling=False)
                message = "ON"

            pm.inViewMessage(
                statusMessage=f"Back-Face Culling is now <hl>{message}</hl>.",
                pos="topCenter",
                fade=True,
            )
        else:  # Feedback if there are no meshes at all in the scene
            pm.inViewMessage(
                statusMessage="<hl>No mesh objects found in the scene.</hl>",
                pos="topCenter",
                fade=True,
            )

    @staticmethod
    def m_isolate_selected() -> None:
        """Isolate the current selection."""
        currentPanel = _core_utils.CoreUtils.get_panel(withFocus=1)
        state = pm.isolateSelect(currentPanel, query=1, state=1)
        if state:
            pm.isolateSelect(currentPanel, state=0)
            pm.isolateSelect(currentPanel, removeSelected=1)
        else:
            pm.isolateSelect(currentPanel, state=1)
            pm.isolateSelect(currentPanel, addSelected=1)

    @staticmethod
    def m_cycle_display_state() -> None:
        """Cycle the display state of all selected objects based on the first object's state."""
        sel = node_utils.NodeUtils.get_unique_children(
            pm.ls(selection=True, transforms=True)
        )

        try:  # Determine the state of the first object
            first_obj = sel[0]
        except IndexError:
            pm.inViewMessage(
                statusMessage="No objects selected. Please select at least one object.",
                pos="topCenter",
                fade=True,
            )
            return

        # Validate the object and attributes existence
        is_visible = first_obj.visibility.get()
        is_templated = (
            getattr(first_obj, "template", False) and first_obj.template.get()
        )
        xray_query_result = pm.displaySurface(first_obj, xRay=True, query=True)
        is_xray = xray_query_result[0] if xray_query_result else False

        # Define the next state and action based on the initial state
        if is_visible and not is_templated and not is_xray:
            next_state = "XRay"
            action = lambda obj: pm.displaySurface(obj, xRay=True)
        elif is_xray:
            next_state = "Templated"
            action = lambda obj: (
                pm.displaySurface(obj, xRay=False),
                obj.template.set(True),
            )
        elif is_templated:
            next_state = "Hidden"
            action = lambda obj: (obj.template.set(False), obj.visibility.set(False))
        else:  # Assume hidden if not visible, templated, or x-ray
            next_state = "Visible"
            action = lambda obj: obj.visibility.set(True)

        # Apply the state transition to all selected objects
        for obj in sel:
            action(obj)

        pm.inViewMessage(
            statusMessage=f"Display: <hl>{next_state}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @staticmethod
    def m_grid_and_image_planes() -> None:
        """Toggle grid and image plane visibility."""
        image_plane = pm.ls(exactType="imagePlane")

        for obj in image_plane:
            attr = obj + ".displayMode"
            if not pm.getAttr(attr) == 2:
                pm.setAttr(attr, 2)
                pm.grid(toggle=1)
                pm.inViewMessage(
                    statusMessage="Grid is now <hl>ON</hl>.", pos="topCenter", fade=True
                )
            else:
                pm.setAttr(attr, 0)
                pm.grid(toggle=0)
                pm.inViewMessage(
                    statusMessage="Grid is now <hl>OFF</hl>.",
                    pos="topCenter",
                    fade=True,
                )

    @staticmethod
    def m_frame_selected() -> None:
        """Frame selected by a set amount."""
        pm.melGlobals.initVar("int", "toggleFrame_")
        selection = pm.ls(selection=1)
        mode = pm.selectMode(query=1, component=1)
        maskVertex = pm.selectType(query=1, vertex=1)
        maskEdge = pm.selectType(query=1, edge=1)
        maskFacet = pm.selectType(facet=1, query=1)

        def frame_element(toggleFrameVal, fitFactorVal, elementType):
            pm.viewFit(fitFactor=fitFactorVal)
            pm.melGlobals["toggleFrame_"] = toggleFrameVal
            print("frame {} {}".format(elementType, str(pm.melGlobals["toggleFrame_"])))

        if len(selection) == 0:
            pm.viewFit(allObjects=1)
        else:
            if mode == 1:
                if maskVertex == 1:
                    if len(selection) > 1:
                        frame_element(
                            1 if pm.melGlobals["toggleFrame_"] != 1 else 0,
                            0.65 if pm.melGlobals["toggleFrame_"] != 1 else 0.10,
                            "vertices",
                        )
                    else:
                        frame_element(
                            1 if pm.melGlobals["toggleFrame_"] != 1 else 0,
                            0.15 if pm.melGlobals["toggleFrame_"] != 1 else 0.01,
                            "vertex",
                        )
                elif maskEdge == 1:
                    frame_element(
                        1 if pm.melGlobals["toggleFrame_"] != 1 else 0,
                        0.3 if pm.melGlobals["toggleFrame_"] != 1 else 0.9,
                        "edge",
                    )
                elif maskFacet == 1:
                    frame_element(
                        1 if pm.melGlobals["toggleFrame_"] != 1 else 0,
                        0.9 if pm.melGlobals["toggleFrame_"] != 1 else 0.45,
                        "facet",
                    )
            else:
                frame_element(
                    1 if pm.melGlobals["toggleFrame_"] != 1 else 0,
                    0.99 if pm.melGlobals["toggleFrame_"] != 1 else 0.65,
                    "object",
                )

    @classmethod
    def m_smooth_preview(cls) -> None:
        """Toggle smooth mesh preview."""
        selection = pm.ls(selection=1)

        for obj in selection:
            obj = obj.split(".")[0]
            displaySmoothMeshAttr = str(obj) + ".displaySmoothMesh"

            if pm.getAttr(displaySmoothMeshAttr) != 2:
                pm.setAttr(displaySmoothMeshAttr, 2)  # smooth preview on
                pm.displayPref(wireframeOnShadedActive="none")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Off</hl>.",
                )

            elif (
                pm.getAttr(displaySmoothMeshAttr) == 2
                and pm.displayPref(query=1, wireframeOnShadedActive=1) == "none"
            ):
                pm.setAttr(displaySmoothMeshAttr, 2)  # smooth preview on
                shapes = pm.listRelatives(selection, children=1, shapes=1)
                [pm.setAttr(s.displaySubdComps, 1) for s in shapes]
                pm.displayPref(wireframeOnShadedActive="full")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            else:
                pm.setAttr(displaySmoothMeshAttr, 0)  # smooth preview off
                pm.displayPref(wireframeOnShadedActive="full")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>OFF</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            if pm.getAttr(str(obj) + ".smoothLevel") != 1:
                pm.setAttr((str(obj) + ".smoothLevel"), 1)

    @staticmethod
    def m_wireframe() -> None:
        """Toggles the wireframe display state.
        Possible states include: none, shaded, full
        """
        focused_panel = _core_utils.CoreUtils.get_panel(withFocus=True)
        # Check if focused_panel is a modelPanel to avoid errors when it's not
        if not focused_panel or not pm.modelEditor(
            focused_panel, query=True, exists=True
        ):
            print("No focused model panel found.")
            return

        # Query the current wireframe on shaded setting
        state = pm.displayPref(q=True, wireframeOnShadedActive=True)

        if state == "none":  # Full Wireframe
            pm.displayPref(wireframeOnShadedActive="full")
            pm.modelEditor(focused_panel, e=True, wireframeOnShaded=True)
            message = "Wireframe <hl>Full</hl>."
        elif state == "full":  # Wireframe Selected
            pm.displayPref(wireframeOnShadedActive="reduced")
            pm.modelEditor(focused_panel, e=True, wireframeOnShaded=False)
            message = "Wireframe <hl>Reduced</hl>."
        elif state == "reduced":  # Wireframe Off
            pm.displayPref(wireframeOnShadedActive="none")
            pm.modelEditor(focused_panel, e=True, wireframeOnShaded=False)
            message = "Wireframe <hl>None</hl>."
        else:  # Fallback or error condition, you might want to log an error or set a default state
            print(f"Unexpected wireframe state encountered: {state}")
            return

        # Display the message
        pm.inViewMessage(position="topCenter", fade=True, statusMessage=message)

    @classmethod
    def m_shading(cls) -> None:
        """Toggles viewport display mode between wireframe, smooth shaded with textures off,
        and smooth shaded with textures on. The transitions occur in the order mentioned.
        """
        currentPanel = _core_utils.CoreUtils.get_panel(withFocus=True)
        displayAppearance = pm.modelEditor(currentPanel, query=1, displayAppearance=1)
        displayTextures = pm.modelEditor(currentPanel, query=1, displayTextures=1)

        if pm.modelEditor(currentPanel, exists=1):
            if displayAppearance == "wireframe":
                pm.modelEditor(
                    currentPanel,
                    edit=1,
                    displayAppearance="smoothShaded",
                    displayTextures=False,
                )
                pm.inViewMessage(
                    statusMessage="smoothShaded <hl>True</hl>\ndisplayTextures <hl>False</hl>.",
                    fade=True,
                    position="topCenter",
                )
            elif displayAppearance == "smoothShaded" and not displayTextures:
                pm.modelEditor(
                    currentPanel,
                    edit=1,
                    displayAppearance="smoothShaded",
                    displayTextures=True,
                )
                pm.inViewMessage(
                    statusMessage="smoothShaded <hl>True</hl>\ndisplayTextures <hl>True</hl>.",
                    fade=True,
                    position="topCenter",
                )
            else:
                pm.modelEditor(
                    currentPanel,
                    edit=1,
                    displayAppearance="wireframe",
                    displayTextures=False,
                )
                pm.inViewMessage(
                    statusMessage="wireframe <hl>True</hl>.",
                    fade=True,
                    position="topCenter",
                )

    @classmethod
    def m_lighting(cls) -> None:
        """Toggles viewport lighting between different states: default, all lights, active lights,
        and flat lighting. If the lighting mode is not one of these states, it resets to the default state.
        """
        currentPanel = _core_utils.CoreUtils.get_panel(withFocus=True)
        displayLights = pm.modelEditor(currentPanel, query=1, displayLights=1)

        if pm.modelEditor(currentPanel, exists=1):
            if displayLights == "default":
                pm.modelEditor(currentPanel, edit=1, displayLights="all")
                pm.inViewMessage(
                    statusMessage="displayLights <hl>all</hl>.",
                    fade=True,
                    position="topCenter",
                )
            elif displayLights == "all":
                pm.modelEditor(currentPanel, edit=1, displayLights="active")
                pm.inViewMessage(
                    statusMessage="displayLights <hl>active</hl>.",
                    fade=True,
                    position="topCenter",
                )
            elif displayLights == "active":
                pm.modelEditor(currentPanel, edit=1, displayLights="flat")
                pm.inViewMessage(
                    statusMessage="displayLights <hl>flat</hl>.",
                    fade=True,
                    position="topCenter",
                )
            else:
                pm.modelEditor(currentPanel, edit=1, displayLights="default")
                pm.inViewMessage(
                    statusMessage="displayLights <hl>default</hl>.",
                    fade=True,
                    position="topCenter",
                )


class EditMacros:
    """ """

    @staticmethod
    def m_object_selection() -> None:
        """Set object selection mask."""
        object_mode = pm.selectMode(query=True, object=True)
        pm.selectMode(co=object_mode)
        pm.selectMode(object=True)
        pm.selectType(allObjects=True)

    @staticmethod
    def m_vertex_selection() -> None:
        """Set vertex selection mask."""
        pm.selectMode(component=True)
        pm.selectType(vertex=True)

    @staticmethod
    def m_edge_selection() -> None:
        """Set edge selection mask."""
        pm.selectMode(component=True)
        pm.selectType(edge=True)

    @staticmethod
    def m_face_selection() -> None:
        """Set face selection mask."""
        pm.selectMode(component=True)
        pm.selectType(facet=True)

    @staticmethod
    def m_toggle_UV_select_type() -> None:
        """Toggles between UV shell and UV component selection.
        Always switches to UV shell mode unless already in UV shell mode,
        then switches to UV component mode.
        """
        inUVShellMode: bool = pm.selectType(query=True, meshUVShell=True)
        pm.selectMode(component=True)

        if inUVShellMode:  # Switch to UV component mode
            pm.selectType(polymeshUV=True)
            pm.inViewMessage(
                statusMessage="Select Type: <hl>Polymesh UV</hl>",
                fade=True,
                position="topCenter",
            )
        else:  # Switch to UV shell mode
            pm.selectType(meshUVShell=True)
            pm.inViewMessage(
                statusMessage="Select Type: <hl>UV Shell</hl>",
                fade=True,
                position="topCenter",
            )

    @staticmethod
    def m_paste_and_rename() -> None:
        """Paste and rename by removing 'pasted__' prefix and reference file names,
        and handle grouping for the pasted objects elegantly.
        """
        # Get a list of all nodes in the scene before pasting
        before_paste = set(pm.ls())

        # Perform the paste operation
        pm.mel.cutCopyPaste("paste")

        # Get a list of all nodes in the scene after pasting and find the difference
        after_paste = set(pm.ls())
        pasted_nodes = list(after_paste - before_paste)
        if not pasted_nodes:
            return

        def strip_names(nodes: Set[pm.PyNode]) -> None:
            """Strip 'pasted__' prefix and reference file names from node names."""
            for node in nodes:
                base_name = node.nodeName()
                new_name = base_name.replace("pasted__", "").split(":")[-1]

                # Attempt to rename the node with the new name
                try:
                    node.rename(new_name)
                except RuntimeError as e:
                    print(f"Error renaming {node}: {e}")

        # Call strip_names on the pasted nodes
        strip_names(set(pasted_nodes))

        # Identify the topmost new group among the pasted nodes
        top_level_group = next(
            (
                node
                for node in pasted_nodes
                if isinstance(node, pm.nt.Transform)
                and not node in before_paste
                and node.getParent() is None
            ),
            None,
        )

        if top_level_group:
            children = top_level_group.getChildren()

            if len(children) == 1:
                # Unparent the single child to the world (top level of the scene)
                pm.parent(children, world=True)
                pm.delete(top_level_group)  # Delete the now-empty top-level group
            else:  # Rename the top-level group to 'pasted' if there are multiple children
                top_level_group.rename("pasted")

    @staticmethod
    def m_multi_component() -> None:
        """Multi-Component Selection."""
        pm.SelectMultiComponentMask()
        pm.inViewMessage(
            statusMessage="<hl>Multi-Component Selection Mode</hl><br>Mask is now <hl>ON</hl>.",
            fade=True,
            position="topCenter",
        )

    @staticmethod
    def m_merge_vertices() -> None:
        """Merge Vertices."""
        tolerance = 0.001
        selection = pm.ls(selection=1, objectsOnly=1)

        if not selection:
            pm.inViewMessage(
                statusMessage="Warning: <hl>Nothing selected</hl>.<br>Must select an object or component.",
                pos="topCenter",
                fade=True,
            )

        else:
            for obj in selection:
                if pm.selectMode(query=1, component=1):  # merge selected components.
                    if pm.filterExpand(selectionMask=31):  # selectionMask=vertices
                        pm.polyMergeVertex(
                            distance=tolerance,
                            alwaysMergeTwoVertices=True,
                            constructionHistory=True,
                        )
                    else:  # if selection type =edges or facets:
                        pm.mel.MergeToCenter()

                else:  # if object mode. merge all vertices on the selected object.
                    for n, obj in enumerate(selection):
                        # get number of vertices
                        count = pm.polyEvaluate(obj, vertex=1)
                        vertices = (
                            str(obj) + ".vtx [0:" + str(count) + "]"
                        )  # mel expression: select -r geometry.vtx[0:1135];
                        pm.polyMergeVertex(
                            vertices,
                            distance=tolerance,
                            alwaysMergeTwoVertices=False,
                            constructionHistory=False,
                        )

                    # return to original state
                    pm.select(clear=1)

                    for obj in selection:
                        pm.select(obj, add=1)

    @staticmethod
    def m_group() -> None:
        """Group selected object(s)."""
        sel = pm.ls(sl=1)
        try:
            pm.group(sel, name=sel[0])
            pm.xform(sel, centerPivots=True)

        except Exception:  # if nothing selected; create empty group.
            pm.group(empty=True, name="null")


class SelectionMacros:
    """ """

    @staticmethod
    def m_invert_component_selection() -> None:
        """Invert the component selection on the currently selected objects."""
        if not pm.selectMode(query=1, component=1):  # component select mode
            return "Error: Selection must be at the component level."

        objects = pm.ls(sl=1, objectsOnly=1)
        selection = pm.ls(sl=1)

        invert = []
        for obj in objects:
            if pm.selectType(query=1, vertex=1):  # vertex
                selectedVertices = pm.filterExpand(
                    selection, selectionMask=31, expand=1
                )
                allVertices = pm.filterExpand(obj + ".v[*]", sm=31)
                invert += {v for v in allVertices if v not in selectedVertices}

            elif pm.selectType(query=1, edge=1):  # edge
                edges = pm.filterExpand(selection, selectionMask=32, expand=1)
                allEdges = pm.filterExpand(obj + ".e[*]", sm=32)
                invert += {e for e in allEdges if e not in edges}

            elif pm.selectType(query=1, facet=1):  # face
                selectedFaces = pm.filterExpand(selection, selectionMask=34, expand=1)
                allFaces = pm.filterExpand(obj + ".f[*]", sm=34)
                invert += {f for f in allFaces if f not in selectedFaces}

        pm.select(invert, replace=1)


class UiMacros:
    """ """

    @staticmethod
    def m_tentacle_show() -> None:
        """Display the tentacle marking menu."""
        from tentacle import tcl_maya

        tcl_maya.show(key_show="Key_F12")

    @staticmethod
    def m_toggle_panels() -> None:
        """Toggle UI toolbars."""
        # toggle panel menus
        panels = _core_utils.CoreUtils.get_panel(allPanels=1)
        state = int(pm.panel(panels[0], menuBarVisible=1, query=1))
        for panel in panels:
            pm.panel(panel, edit=1, menuBarVisible=(not state))

        pm.mel.toggleMainMenubar(not state)
        pm.mel.ToggleModelEditorBars(not state)


class AnimationMacros:
    """ """

    @staticmethod
    def m_set_selected_keys() -> None:
        """Set keys for any attributes (channels) that are selected in the channel box."""
        sel = pm.ls(selection=True, transforms=1, long=1)
        for obj in sel:
            attrs = _core_utils.CoreUtils.get_selected_channels()
            for attr in attrs:
                attr_ = getattr(obj, attr)
                pm.setKeyframe(attr_)
                # cutKey -cl -t ":" -f ":" -at "tx" -at "ty" -at "tz" pSphere1; #remove keys

    @staticmethod
    def m_unset_selected_keys() -> None:
        """Un-set keys for any attributes (channels) that are selected in the channel box."""
        sel = pm.ls(selection=True, transforms=1, long=1)
        for obj in sel:
            attrs = _core_utils.CoreUtils.get_selected_channels()
            for attr in attrs:
                attr_ = getattr(obj, attr)
                pm.setKeyframe(attr_)
                pm.cutKey(attr_, cl=True)  # remove keys

    # ========================================================================================


class Macros(
    MacroManager,
    DisplayMacros,
    EditMacros,
    SelectionMacros,
    AnimationMacros,
    UiMacros,
):
    """ """

    pass


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
