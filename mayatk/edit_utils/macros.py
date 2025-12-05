# !/usr/bin/python
# coding=utf-8
from typing import Set

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.ui_utils._ui_utils import UiUtils


class MacroManager(ptk.HelpMixin):
    """Assign macro functions to hotkeys.

    Example:
        class Macros(MacroManager):
            '''A class that inherits from `MacroManager` and holds the actual macro functions.
            '''
            @staticmethod
            def m_back_face_culling():
                    '''Toggle Back-Face Culling.
                    '''
                    sel = pm.ls(selection=True)
                    if sel:
                            currentPanel = getPanel(withFocus=True)
                            state = pm.polyOptions(sel, query=True, wireBackCulling=True)[0]

                            if not state:
                                    pm.polyOptions(sel, gl=True, wireBackCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 0)
                                    pm.inViewMessage(status_message="Back-Face Culling is now <hl>OFF</hl>.>", pos='topCenter', fade=True)
                            else:
                                    pm.polyOptions(sel, gl=True, backCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 1)
                                    pm.inViewMessage(status_message="Back-Face Culling is now <hl>ON</hl>.", pos='topCenter', fade=True)
                    else:
                            print(" Warning: Nothing selected. ")

        #call the `set_macros` function to set a macro for functions you defined in `Macros`.
        mtk.Macros.set_macros(
            "m_back_face_culling,     key=1, cat=Display",
        )
    """

    @classmethod
    def set_macros(cls, *args):
        """Extends `set_macro` to accept a list of strings representing positional and keyword arguments.

        Parameters:
            *args (str): A variable number of strings, each containing the arguments for a single macro. Each string
                    should be in the format "<macro name>, <positional arg1>, <positional arg2>, ..., <keyword arg1>=<value1>,
                    <keyword arg2>=<value2>, ..."
        Example:
            set_macros('m_back_face_culling, key=1, cat=Display', 'm_smooth_preview, key=2, cat=Display') #Calls `set_macro` with the parsed arguments for each macro in `args`.
        """
        for string in args:
            cls.call_with_input(cls.set_macro, string)

    @staticmethod
    def call_with_input(func, input_string):
        """Parses an input string into positional and keyword arguments, and
        calls the given function with those arguments.

        Parameters:
            func (callable): The function to call.
            input_string (str): The input string containing the arguments.

        Returns:
            The result of calling `func` with the parsed arguments.
        """
        args, kwargs = [], {}
        for i in input_string.split(","):
            try:
                key, value = i.split("=")
                kwargs[key.strip()] = value.strip()
            except ValueError:
                args.append(i.strip())

        return func(*args, **kwargs)

    @classmethod
    def set_macro(
        cls, name, key=None, cat=None, ann=None, default=False, delete_existing=True
    ):
        """Sets a default runtime command with a keyboard shortcut.

        Parameters:
            name (str): The command name you provide must be unique. (alphanumeric characters, or underscores)
            cat (str): catagory - Category for the command.
            ann (str): annotation - Description of the command.
            key (str): keyShortcut - Specify what key is being set.
                                    key modifier values are set by adding a '+' between chars. ie. 'sht+z'.
                                    modifiers:
                                            alt, ctl, sht
                                    additional valid keywords are:
                                            Up, Down, Right, Left,
                                            Home, End, Page_Up, Page_Down, Insert
                                            Return, Space
                                            F1 to F12
                                            Tab (Will only work when modifiers are specified)
                                            Delete, Backspace (Will only work when modifiers are specified)
            default (bool): Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.
            delete_existing = Delete any existing (non-default) runtime commands of the given name.
        """
        command = f"if 'm_slots' not in globals(): from {cls.__module__} import {cls.__name__}; global m_slots; m_slots = {cls.__name__}();\nm_slots.{name}();"

        if not ann:  # if no ann is given, try using the method's docstring.
            method = getattr(cls, name)
            ann = method.__doc__.split("\n")[0]  # use only the first line.

        if pm.runTimeCommand(name, exists=True):
            if pm.runTimeCommand(name, query=True, default=True):
                return  # can not delete default runtime commands.
            elif (
                delete_existing
            ):  # delete any existing (non-default) runtime commands of that name.
                pm.runTimeCommand(name, edit=True, delete=True)

        try:  # set runTimeCommand
            pm.runTimeCommand(
                name,
                annotation=ann,
                category=cat,
                command=command,
                default=default,
            )
        except RuntimeError as error:
            print("# Error: {}: {} #".format(__file__, error))
            return error

        # set command
        nameCommand = pm.nameCommand(
            "{0}Command".format(name),
            annotation=ann,
            command=name,
        )

        # set hotkey
        # modifiers
        ctl = False
        alt = False
        sht = False
        for char in key.split("+"):
            if char == "ctl":
                ctl = True
            elif char == "alt":
                alt = True
            elif char == "sht":
                sht = True
            else:
                key = char

        # print(name, char, ctl, alt, sht)
        pm.hotkey(
            keyShortcut=key, name=nameCommand, ctl=ctl, alt=alt, sht=sht
        )  # set only the key press.


class DisplayMacros:
    """ """

    @staticmethod
    def m_component_id_display():
        """Toggle Component Id Display through vertices, edges, faces, UVs, and off."""
        # Query the current state of component ID display settings for vertices, edges, faces, and UVs
        current_state = pm.polyOptions(q=True, displayItemNumbers=True)[:4]

        # Determine the next state to switch to
        if True not in current_state:
            next_state_index = 0  # If all are False, start with vertices
        else:
            # Find the first True, switch to the next state or turn all off if it's the last one
            current_index = current_state.index(True)
            next_state_index = (
                current_index + 1
            ) % 5  # Cycle through 0-4 (vertices, edges, faces, UVs, off)

        # Define the configurations for toggling component IDs
        configurations = [
            {"displayItemNumbers": (1, 0, 0, 0)},  # Vertex IDs
            {"displayItemNumbers": (0, 1, 0, 0)},  # Edge IDs
            {"displayItemNumbers": (0, 0, 1, 0)},  # Face IDs
            {"displayItemNumbers": (0, 0, 0, 1)},  # UV IDs
            {"displayItemNumbers": (0, 0, 0, 0)},  # Turn all off
        ]
        labels = ["vertex IDs", "edge IDs", "face IDs", "UV IDs", "Off"]

        # Apply the selected configuration
        pm.polyOptions(activeObjects=True, **configurations[next_state_index])

        # Display message in the viewport
        pm.inViewMessage(
            amg=f"Component ID Display: <hl>{labels[next_state_index]}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @staticmethod
    def m_normals_display():
        """Toggle face normals, vertex normals, tangents, and off."""
        # Query the current state
        current_tangent = pm.polyOptions(q=True, displayTangent=True)[0]
        current_normal = pm.polyOptions(q=True, displayNormal=True)[0]
        is_facet = pm.polyOptions(q=True, facet=True)[0]
        is_vertex = pm.polyOptions(q=True, point=True)[0]

        # Define the current state based on queries
        if current_tangent:
            current_state = 3  # Tangents are displayed
        elif current_normal and is_vertex:
            current_state = 2  # Vertex normals are displayed
        elif current_normal and is_facet:
            current_state = 1  # Facet normals are displayed
        else:
            current_state = 0  # All displays are off

        # Determine the next state to switch to
        next_state = (current_state + 1) % 4  # Cycle through the states: 0, 1, 2, 3

        # Configuration for each state
        if next_state == 0:
            pm.polyOptions(displayNormal=False, displayTangent=False)
        elif next_state == 1:
            pm.polyOptions(
                displayNormal=True,
                facet=True,
                point=False,
                displayTangent=False,
                sizeNormal=1,
            )
        elif next_state == 2:
            pm.polyOptions(
                displayNormal=True,
                point=True,
                facet=False,
                displayTangent=False,
                sizeNormal=1,
            )
        elif next_state == 3:
            pm.polyOptions(displayTangent=True, displayNormal=False)

        # Messages for each state
        messages = [
            "Normals Display <hl>Off</hl>",
            "<hl>Facet</hl> Normals Display <hl>On</hl>",
            "<hl>Vertex</hl> Normals Display <hl>On</hl>",
            "<hl>Tangent</hl> Display <hl>On</hl>",
        ]

        # Display message in the viewport using inViewMessage
        pm.inViewMessage(amg=messages[next_state], pos="topCenter", fade=True)

    @staticmethod
    def m_soft_edge_display():
        """Toggle Soft Edge Display."""
        # Query the current setting for all edges display
        all_edges_visible = pm.polyOptions(q=True, ae=True)[0]

        # Toggle the edge display based on the current state
        if all_edges_visible:
            # If all edges are currently visible, switch to soft edges only
            pm.polyOptions(ae=False, se=True)
            message = "Soft Edge Display <hl>On</hl>"
        else:
            # If not all edges are visible, it implies soft edges are active; switch to show all edges
            pm.polyOptions(se=False, ae=True)
            message = "All Edges Display <hl>On</hl>"

        # Display message in the viewport using inViewMessage
        pm.inViewMessage(amg=message, pos="topCenter", fade=True)

    @staticmethod
    def m_toggle_visibility():
        """Toggle Visibility"""
        pm.mel.ToggleVisibilityAndKeepSelection()

    @staticmethod
    @CoreUtils.selected
    def m_toggle_uv_border_edges(objects):
        """Toggle the display of UV border edges for the given objects."""
        if not objects:
            pm.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        for obj in pm.ls(objects, flatten=True):
            # Use MEL command to toggle UV border edges visibility
            state = pm.polyOptions(obj, query=True, displayMapBorder=True)[0]
            if state:  # Turn it off
                pm.polyOptions(obj, displayMapBorder=False)
                pm.inViewMessage(
                    statusMessage="UV Border Edges <hl>Hidden</hl>.",
                    pos="topCenter",
                    fade=True,
                )
            else:  # If not displaying UV borders, turn it on
                pm.polyOptions(obj, displayMapBorder=True)
                pm.inViewMessage(
                    statusMessage=f"UV Border Edges <hl>Shown</hl>.",
                    pos="topCenter",
                    fade=True,
                )

    @staticmethod
    @CoreUtils.selected
    def m_back_face_culling(objects) -> None:
        """Toggle Back-Face Culling on selected objects, or on all objects if none are selected."""
        objects = objects or pm.ls(type="mesh")
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
        currentPanel = UiUtils.get_panel(withFocus=1)
        state = pm.isolateSelect(currentPanel, query=1, state=1)
        if state:
            pm.isolateSelect(currentPanel, state=0)
            pm.isolateSelect(currentPanel, removeSelected=1)
        else:
            pm.isolateSelect(currentPanel, state=1)
            pm.isolateSelect(currentPanel, addSelected=1)

    @staticmethod
    @CoreUtils.selected
    def m_cycle_display_state(objects) -> None:
        """Cycle the display state of all selected objects based on the first object's state."""
        sel = NodeUtils.get_unique_children(objects)

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
    @CoreUtils.selected
    def m_wireframe_toggle(objects) -> None:
        """Toggle Wireframe Display on selected objects, or on all objects if none are selected."""
        objects = objects or pm.ls(type="mesh")
        if objects:
            # Check the current state of the first object in the list
            current_state: bool = pm.getAttr(objects[0].overrideShading) == 1
            # Toggle the overrideDisplayType attribute for all objects
            for obj in objects:
                pm.setAttr(f"{obj}.overrideEnabled", 1)
                new_state = 0 if current_state else 1  # 0: Normal, 1: Wireframe
                pm.setAttr(f"{obj}.overrideShading", new_state)

            # Provide feedback message
            message = "Wireframe" if not current_state else "Shaded"
            pm.inViewMessage(
                statusMessage=f"Display mode is now <hl>{message}</hl>.",
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
    @CoreUtils.selected
    def m_frame(objects) -> None:
        """Frame selected by a set amount with three toggle states."""
        pm.melGlobals.initVar("int", "toggleFrame_")
        mode = pm.selectMode(q=True, component=True)
        maskVertex = pm.selectType(q=True, vertex=True)
        maskEdge = pm.selectType(q=True, edge=True)
        maskFacet = pm.selectType(q=True, facet=True)

        # Define toggle states and fit factors
        toggle_states = {
            "vertices": [(0.10, 1), (0.65, 2), (0.01, 0)],
            "vertex": [(0.01, 1), (0.15, 2), (0.01, 0)],
            "edge": [(0.9, 1), (0.3, 2), (0.1, 0)],
            "facet": [(0.45, 1), (0.9, 2), (0.2, 0)],
            "object": [(0.75, 1), (0.99, 2), (0.5, 0)],
        }

        def frame_element(element_type):
            current_toggle = pm.melGlobals["toggleFrame_"]
            fitFactorVal, next_toggle = toggle_states[element_type][current_toggle]
            pm.viewFit(fitFactor=fitFactorVal)
            pm.melGlobals["toggleFrame_"] = next_toggle
            print(f"frame {element_type} {pm.melGlobals['toggleFrame_']}")

        if len(objects) == 0:
            pm.viewFit(allObjects=1)
        else:
            if mode == 1:
                if maskVertex == 1:
                    element_type = "vertices" if len(objects) > 1 else "vertex"
                elif maskEdge == 1:
                    element_type = "edge"
                elif maskFacet == 1:
                    element_type = "facet"
                else:
                    element_type = "object"
            else:
                element_type = "object"

            frame_element(element_type)

    @classmethod
    @CoreUtils.selected
    def m_smooth_preview(cls, objects) -> None:
        """Toggle smooth mesh preview."""
        objs = NodeUtils.get_unique_children(objects)
        for obj in objs:
            if pm.getAttr(obj.displaySmoothMesh) != 2:
                pm.setAttr(obj.displaySmoothMesh, 2)  # smooth preview on
                pm.displayPref(wireframeOnShadedActive="none")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Off</hl>.",
                )

            elif (
                pm.getAttr(obj.displaySmoothMesh) == 2
                and pm.displayPref(query=1, wireframeOnShadedActive=1) == "none"
            ):
                pm.setAttr(obj.displaySmoothMesh, 2)  # smooth preview on
                shapes = pm.listRelatives(objects, children=1, shapes=1)
                [pm.setAttr(s.displaySubdComps, 1) for s in shapes]
                pm.displayPref(wireframeOnShadedActive="full")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            else:
                pm.setAttr(obj.displaySmoothMesh, 0)  # smooth preview off
                pm.displayPref(wireframeOnShadedActive="full")
                pm.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>OFF</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            if pm.getAttr(obj.smoothLevel) != 1:
                pm.setAttr(obj.smoothLevel, 1)

    @staticmethod
    def m_wireframe() -> None:
        """Toggles the wireframe display state.
        Possible states include: none, shaded, full
        """
        focused_panel = UiUtils.get_panel(withFocus=True)
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

    @staticmethod
    def m_material_override():
        """Toggle Material Override"""
        currentPanel = pm.playblast(
            activeEditor=True
        )  # Use playblast to get the active panel with focus
        if not currentPanel:
            pm.inViewMessage(
                statusMessage="No active panel with focus found.",
                pos="topCenter",
                fade=True,
            )
            return

        # Query the current state of default material usage
        state = pm.modelEditor(currentPanel, q=True, useDefaultMaterial=True)

        # Toggle the state of the default material
        pm.modelEditor(currentPanel, edit=True, useDefaultMaterial=not state)

        # Display the toggle state in the viewport
        pm.inViewMessage(
            statusMessage=f"Default Material Override: <hl>{'On' if not state else 'Off'}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @classmethod
    def m_shading(cls) -> None:
        """Toggles viewport display mode between wireframe, smooth shaded with textures off,
        and smooth shaded with textures on. The transitions occur in the order mentioned.
        """
        currentPanel = UiUtils.get_panel(withFocus=True)
        displayAppearance = pm.modelEditor(currentPanel, q=True, displayAppearance=True)
        displayTextures = pm.modelEditor(currentPanel, q=True, displayTextures=True)

        if pm.modelEditor(currentPanel, exists=1):
            if displayAppearance == "wireframe":
                pm.modelEditor(
                    currentPanel,
                    edit=True,
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
                    edit=True,
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
                    edit=True,
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
        currentPanel = UiUtils.get_panel(withFocus=True)
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
    @CoreUtils.undoable
    @CoreUtils.selected
    @CoreUtils.reparent
    @DisplayUtils.add_to_isolation
    def m_combine(objects, allow_multiple_mats: bool = True):
        """Combine multiple meshes.

        Parameters:
            objects (list): List of mesh objects to combine.
            allow_multiple_materials (bool): If False, abort if selected objects use different materials.
        """
        if not objects or len(objects) < 2:
            pm.inViewMessage(
                statusMessage="<hl>Insufficient selection.</hl> Operation requires at least two objects",
                fade=True,
                position="topCenter",
            )
            return None

        if not allow_multiple_mats:
            all_mats = MatUtils.get_mats(objects)
            if len(set(all_mats)) > 1:
                pm.warning(
                    "Cannot combine: selected objects do not share the same material."
                )
                return None

        combined_mesh = pm.polyUnite(objects, centerPivot=True, ch=False)[0]
        combined_mesh = pm.rename(combined_mesh, objects[0].name())

        return combined_mesh

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    @CoreUtils.reparent
    @DisplayUtils.add_to_isolation
    def m_boolean(objects, repair_mesh=True, keep_boolean=True, **kwargs):
        """Perform a boolean operation on two meshes using PyMel, managing shorthand and full parameter names dynamically."""
        a, *b = objects
        if not a or not b:
            pm.inViewMessage(
                statusMessage="<hl>Insufficient selection.</hl> Operation requires at least two objects",
                fade=True,
                position="topCenter",
            )
            return None

        if keep_boolean:
            b = pm.duplicate(b, rr=True)

        if len(b) > 1:  # Combine multiple meshes
            b = pm.polyUnite(b, centerPivot=True, ch=False)[0]

        if repair_mesh:  # Clean any n-gons before running the boolean
            from mayatk import MeshDiagnostics

            MeshDiagnostics.clean_geometry(
                objects=a,
                repair=True,
                nonmanifold=True,
                nsided=True,
                bakePartialHistory=True,
            )
        # Resolve operation type, defaulting to 'union'
        operation_types = {"union": 1, "difference": 2, "intersection": 3}
        operation = kwargs.pop("operation", kwargs.pop("op", "union"))
        if isinstance(operation, str):
            operation = operation_types.get(operation, 1)

        # Resolve name and construction history conflicts
        name = kwargs.pop("name", kwargs.pop("n", a))
        ch = kwargs.pop("constructionHistory", kwargs.pop("ch", False))

        # Perform the boolean operation
        result = pm.polyCBoolOp(a, b, op=operation, n=name, ch=ch, **kwargs)[0]

        return result

    @staticmethod
    @CoreUtils.selected
    def m_lock_vertex_normals(objects):
        """Toggle lock/unlock vertex normals."""
        if not objects:
            pm.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        # Check if the current selection mode is object mode
        is_object_mode = pm.selectMode(q=True, object=True)

        if is_object_mode:  # Use the .vtx[:] notation directly
            objs = [f"{obj}.vtx[:]" for obj in objects]
        else:  # Convert selected components to vertices if in component mode
            objs = pm.polyListComponentConversion(objects, toVertex=True)

        if not objs:
            print("No valid objects or components given.")
            return

        # Determine the current normal state by querying the first vertex
        current_state = all(pm.polyNormalPerVertex(objs, q=True, freezeNormal=True))

        if current_state:
            # If normals are currently locked, unlock them
            pm.polyNormalPerVertex(objs, unFreezeNormal=True)
            pm.inViewMessage(
                statusMessage="Normals <hl>UnLocked</hl>.", pos="topCenter", fade=True
            )
        else:
            # If normals are currently unlocked, lock them
            pm.polyNormalPerVertex(objs, freezeNormal=True)
            pm.inViewMessage(
                statusMessage="Normals <hl>Locked</hl>.", pos="topCenter", fade=True
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
        pm.mel.SelectMultiComponentMask()
        pm.inViewMessage(
            statusMessage="<hl>Multi-Component Selection Mode</hl><br>Mask is now <hl>ON</hl>.",
            fade=True,
            position="topCenter",
        )

    @staticmethod
    @CoreUtils.selected
    def m_merge_vertices(objects, tolerance=0.001) -> None:
        """Merge Vertices."""
        objects = pm.ls(objects, objectsOnly=True)

        if not objects:
            pm.inViewMessage(
                statusMessage="Warning: <hl>Nothing selected</hl>.<br>Must select an object or component.",
                pos="topCenter",
                fade=True,
            )

        else:
            for obj in objects:
                if pm.selectMode(q=True, component=True):  # Merge selected components.
                    if pm.filterExpand(selectionMask=31):  # Vertices
                        pm.polyMergeVertex(
                            distance=tolerance,
                            alwaysMergeTwoVertices=True,
                            constructionHistory=True,
                        )
                    else:  # If selection type is edges or facets:
                        pm.mel.MergeToCenter()

                else:  # If object mode. merge all vertices on the selected object.
                    for n, obj in enumerate(objects):
                        # Get number of vertices
                        count = pm.polyEvaluate(obj, vertex=True)
                        vertices = str(obj) + ".vtx [0:" + str(count) + "]"
                        pm.polyMergeVertex(
                            vertices,
                            distance=tolerance,
                            alwaysMergeTwoVertices=False,
                            constructionHistory=False,
                        )

                    # Return to original state
                    pm.select(clear=True)
                    for obj in objects:
                        pm.select(obj, add=True)

    @staticmethod
    @CoreUtils.selected
    def m_group(objects) -> None:
        """Group selected object(s)."""
        objects = pm.ls(objects, objectsOnly=True)
        if objects:
            grp = pm.group(objects)
            pm.xform(grp, centerPivots=True)
            pm.rename(grp, objects[0].name())
        else:  # If nothing selected, create empty group.
            pm.group(empty=True, name="null")


class SelectionMacros:
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
    def m_invert_selection() -> None:
        """Invert the current selection of geometry or components."""
        objects = pm.ls(selection=True, flatten=True)

        if not objects:
            pm.warning("No valid objects selected to invert.")
            return

        first = objects[0]

        if isinstance(first, (pm.MeshVertex, pm.MeshEdge, pm.MeshFace)):
            EditUtils.invert_components(select=True)
        else:
            EditUtils.invert_geometry(select=True)

    @staticmethod
    @CoreUtils.selected
    def m_toggle_selectability(objects):
        """Toggle selectability of the given objects."""
        if not objects:
            pm.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        for obj in pm.ls(objects, flatten=True):
            try:
                # Ensure attributes exist and are not locked or connected before modifying
                if not obj.hasAttr("overrideEnabled") or obj.overrideEnabled.isLocked():
                    pm.warning(
                        f"Cannot modify overrideEnabled for {obj}: Attribute is locked."
                    )
                    continue
                if (
                    not obj.hasAttr("overrideDisplayType")
                    or obj.overrideDisplayType.isLocked()
                ):
                    pm.warning(
                        f"Cannot modify overrideDisplayType for {obj}: Attribute is locked."
                    )
                    continue
                if (
                    not obj.hasAttr("useOutlinerColor")
                    or obj.useOutlinerColor.isLocked()
                ):
                    pm.warning(
                        f"Cannot modify useOutlinerColor for {obj}: Attribute is locked."
                    )
                    continue
                if not obj.hasAttr("outlinerColor") or obj.outlinerColor.isLocked():
                    pm.warning(
                        f"Cannot modify outlinerColor for {obj}: Attribute is locked."
                    )
                    continue

                override_enabled = obj.overrideEnabled.get()
                current_state = obj.overrideDisplayType.get()

                if override_enabled and current_state == 2:
                    # Object is currently non-selectable, make it selectable
                    obj.overrideDisplayType.set(0)  # Normal mode
                    obj.useOutlinerColor.set(0)  # Disable custom outliner color
                    pm.inViewMessage(
                        statusMessage=f"{obj} <hl>Selectable</hl>.",
                        pos="topCenter",
                        fade=True,
                    )
                else:  # Object is currently selectable, make it non-selectable
                    obj.overrideEnabled.set(1)
                    obj.overrideDisplayType.set(2)  # Reference mode
                    obj.useOutlinerColor.set(1)  # Enable custom outliner color
                    obj.outlinerColor.set(
                        0.3, 0.6, 0.6
                    )  # Set color to desaturated teal
                    pm.inViewMessage(
                        statusMessage=f"{obj} <hl>Non-selectable</hl>.",
                        pos="topCenter",
                        fade=True,
                    )

            except RuntimeError as e:
                pm.warning(f"Failed to modify selectability for {obj}: {e}")

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
    def m_toggle_panels(toggle_menu: bool = True, toggle_panels: bool = True) -> None:
        """Toggle UI toolbars and menu bar in sync.

        Parameters:
            toggle_menu (bool): If True, toggles the visibility of the main menu bar.
            toggle_panels (bool): If True, toggles the visibility of panel toolbars.
        """
        # Get the main Maya window and its menu bar
        main_window = UiUtils.get_main_window()
        menu_bar = main_window.menuBar() if main_window else None

        # Use the visibility of the menu bar as the source state
        if menu_bar:
            current_state = menu_bar.isVisible()
        else:
            current_state = True  # Default to True if menu bar is not found

        # Determine the new state based on the current state of the menu bar
        new_state = not current_state

        # Toggle the main menu bar
        if toggle_menu and menu_bar:
            menu_bar.setVisible(new_state)

        # Toggle the panels based on the new state
        if toggle_panels:
            panels = pm.getPanel(allPanels=True)
            for panel in panels:
                pm.panel(panel, edit=True, menuBarVisible=new_state)
            pm.mel.ToggleModelEditorBars(new_state)


class AnimationMacros:
    """ """

    @staticmethod
    @CoreUtils.selected
    def m_set_selected_keys(objects) -> None:
        """Set keys for any attributes (channels) that are selected in the channel box."""
        for obj in objects:
            attrs = UiUtils.get_selected_channels()
            for attr in attrs:
                attr_ = getattr(obj, attr)
                pm.setKeyframe(attr_)
                # cutKey -cl -t ":" -f ":" -at "tx" -at "ty" -at "tz" pSphere1; #remove keys

    @staticmethod
    @CoreUtils.selected
    def m_unset_selected_keys(objects) -> None:
        """Un-set keys for any attributes (channels) that are selected in the channel box."""
        for obj in objects:
            attrs = UiUtils.get_selected_channels()
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


# #create wrapper
# mel.createMelWrapper(method)

# #set command
# pm.nameCommand('name', annotation='', command=<>)
# pm.hotkey(key='1', altModifier=True, name='name')


# #clear keyboard shortcut
# pm.hotkey(keyShortcut=key, name='', releaseName='', ctl=ctl, alt=alt, sht=sht) #unset the key press name and releaseName.


# #query runTimeCommand
# if pm.runTimeCommand('name', exists=True):


# #delete runTimeCommand
# pm.runTimeCommand('name', edit=True, delete=True)


# #set runTimeCommand
# pm.runTimeCommand(
#             'name',
#             annotation=string,
#             category=string,
#             categoryArray,
#             command=script,
#             commandArray,
#             commandLanguage=string,
#             default=boolean,
#             defaultCommandArray,
#             delete,
#             exists,
#             hotkeyCtx=string,
#             image=string,
#             keywords=string,
#             annotation=string,
#             longAnnotation=string,
#             numberOfCommands,
#             numberOfDefaultCommands,
#             numberOfUserCommands,
#             plugin=string,
#             save,
#             showInHotkeyEditor=boolean,
#             tags=string,
#             userCommandArray,
# )

# -annotation(-ann) string createqueryedit
#         Description of the command.

# -category(-cat) string createqueryedit
#         Category for the command.

# -categoryArray(-caa) query
#         Return all the run time command categories.

# -command(-c) script createqueryedit
#         Command to be executed when runTimeCommand is invoked.

# -commandArray(-ca) query
#         Returns an string array containing the names of all the run time commands.

# -commandLanguage(-cl) string createqueryedit
#         In edit or create mode, this flag allows the caller to choose a scripting language for a command passed to the "-command" flag. If this flag is not specified, then the callback will be assumed to be in the language from which the runTimeCommand command was called. In query mode, the language for this runTimeCommand is returned. The possible values are "mel" or "python".

# -default(-d) boolean createquery
#         Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.

# -defaultCommandArray(-dca) query
#         Returns an string array containing the names of all the default run time commands.

# -delete(-del) edit
#         Delete the specified user run time command.

# -exists(-ex) create
#         Returns true|false depending upon whether the specified object exists. Other flags are ignored.

# -hotkeyCtx(-hc) string createqueryedit
#         hotkey Context for the command.

# -image(-i) string createqueryedit
#         Image filename for the command.

# -keywords(-k) string createqueryedit
#         Keywords for the command. Used for searching for commands in Type To Find. When multiple keywords, use ; as a separator. (Example: "keyword1;keyword2")

# -annotation(-annotation) string createqueryedit
#         Label for the command.

# -longAnnotation(-la) string createqueryedit
#         Extensive, multi-line description of the command. This will show up in Type To Finds more info page in addition to the annotation.

# -numberOfCommands(-nc) query
#         Return the number of run time commands.

# -numberOfDefaultCommands(-ndc) query
#         Return the number of default run time commands.

# -numberOfUserCommands(-nuc) query
#         Return the number of user run time commands.

# -plugin(-p) string createqueryedit
#         Name of the plugin this command requires to be loaded. This flag wraps the script provided into a safety check and automatically loads the plugin referenced on execution if it hasn't been loaded. If the plugin fails to load, the command won't be executed.

# -save(-s) edit
#         Save all the user run time commands.

# -showInHotkeyEditor(-she) boolean createqueryedit
#         Indicate that this run time command should be shown in the Hotkey Editor. Default value is true.

# -tags(-t) string createqueryedit
#         Tags for the command. Used for grouping commands in Type To Find. When more than one tag, use ; as a separator. (Example: "tag1;tag2")

# -userCommandArray(-uca) query
#         Returns an string array containing the names of all the user run time commands.
