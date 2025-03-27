# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.xform_utils import XformUtils


class RigUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    def create_locator(
        *, scale: float = 1, parent: Optional["pm.nodetypes.Transform"] = None, **kwargs
    ) -> object:
        """Create a locator with the given scale.

        Parameters:
            * (args): Additional arguments for the spaceLocator command.
            scale (float): The desired scale of the locator.
            **kwargs: Additional keyword arguments for the spaceLocator command, including 'name' and 'position'.

        Special Handling:
            If 'position' is provided in kwargs and it is not a tuple or list, it is assumed to be an object.
            The method attempts to get the world space position of this object to use as the locator's position.
            If the position cannot be resolved, it is removed from kwargs.

        Returns:
            pm.nt.Transform: The created locator transform node.
        """
        pos = kwargs.get("position", None)
        if pos is not None:
            if not isinstance(pos, (tuple, list)):
                transform_node = NodeUtils.get_transform_node(pos)
                if transform_node:
                    kwargs["position"] = pm.xform(
                        transform_node[0], q=True, ws=True, t=True
                    )
                else:
                    kwargs.pop("position", None)

        loc = pm.spaceLocator(**{k: v for k, v in kwargs.items() if v is not None})
        if scale != 1:
            pm.scale(loc, scale, scale, scale)
        if parent:
            loc.setParent(parent)

        return loc

    @classmethod
    @CoreUtils.undoable
    def create_locator_at_object(
        cls,
        objects,
        parent=False,
        freeze_transforms=False,
        bake_child_pivot=False,
        grp_suffix="_GRP#",
        loc_suffix="_LOC#",
        obj_suffix="_GEO#",
        strip_digits=False,
        strip_suffix=False,
        loc_scale=1,
        lock_translate=False,
        lock_rotation=False,
        lock_scale=False,
    ):
        """Create locators with the same transforms as any selected object(s).
        If there are vertices selected it will create a locator at the center of the selected vertices bounding box.

        Parameters:
            objects (str/obj/list): A list of objects, or an object name to create locators at.
            parent (bool): Parent the object to the locator. (default=False)
            freeze_transforms (bool): Freeze transforms on the locator. (default=True)
            bake_child_pivot (bool): Bake pivot positions on the child object. (default=True)
            grp_suffix (str): A string appended to the end of the created groups name. (default: '_GRP#')
            loc_suffix (str): A string appended to the end of the created locators name. (default: '_LOC#')
            obj_suffix (str): A string appended to the end of the existing objects name. (default: '_GEO#')
            strip_digits (bool): Strip numeric characters from the string. If the resulting name is not unique, maya will append a trailing digit. (default=False)
            strip_suffix (str): Strip any existing suffix. A suffix is defined by the last '_' (if one exists) and any chars trailing. (default=False)
            loc_scale (float) = The scale of the locator. (default=1)
            lock_translate (bool): Lock the translate values of the child object. (default=False)
            lock_rotation (bool): Lock the rotation values of the child object. (default=False)
            lock_scale (bool): Lock the scale values of the child object. (default=False)
            remove (bool): Removes the locator and any child locks. (not valid with component selections) (default=False)

        Example:
            createLocatorAtSelection(strip='_GEO', suffix='', strip_digits=True, parent=True, lock_translate=True, lock_rotation=True)
        """
        import re

        suffix_strip_regex = (
            re.escape(grp_suffix).replace(r"\#", r"\d$"),
            re.escape(loc_suffix).replace(r"\#", r"\d$"),
            re.escape(obj_suffix).replace(r"\#", r"\d$"),
        )

        def format_name_with_suffix(base_name: str, o) -> str:
            """Return the formatted name based on the base name and object's current type with the appropriate suffix."""
            if NodeUtils.is_locator(o):
                suffix = loc_suffix
            elif NodeUtils.is_group(o):
                suffix = grp_suffix
            else:
                suffix = obj_suffix

            return ptk.format_suffix(
                base_name,
                suffix=suffix,
                strip=suffix_strip_regex,
                strip_trailing_ints=strip_digits,
                strip_trailing_alpha=strip_suffix,
            )

        for obj in pm.ls(objects, long=True, type="transform"):
            base_name = obj.nodeName()  # Use the original object name as the base name
            vertices = pm.filterExpand(obj, sm=31)  # returns a string list.

            if freeze_transforms:
                # Store the current pivot position
                matrix = XformUtils.get_manip_pivot_matrix(obj)
                pm.makeIdentity(obj, apply=True)
                XformUtils.set_manip_pivot_matrix(obj, matrix)

            if bake_child_pivot and not NodeUtils.is_group(obj):
                XformUtils.bake_pivot(obj, position=True, orientation=True)

            if vertices:
                objName = vertices[0].split(".")[0]
                obj = pm.ls(objName)

                loc = cls.create_locator(scale=loc_scale)

                xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(vertices)
                x, y, z = (
                    (xmin + xmax) / 2,
                    (ymin + ymax) / 2,
                    (zmin + zmax) / 2,
                )
                pm.move(x, y, z, loc)

            else:  # Object
                loc = cls.create_locator(scale=loc_scale)
                tempConst = pm.parentConstraint(obj, loc, maintainOffset=False)
                pm.delete(tempConst)

            try:
                if parent:
                    origParent = pm.listRelatives(obj, parent=1)

                    grp = cls.create_group(obj, zero_translation=1, zero_rotation=1)
                    pm.rename(grp, format_name_with_suffix(base_name, grp))
                    pm.parent(obj, loc)
                    pm.parent(loc, grp)
                    pm.parent(grp, origParent)

                if freeze_transforms:  # freeze transforms one last time.
                    # Assure attributes are unlocked.
                    cls.set_attr_lock_state(
                        obj, translate=False, rotate=False, scale=False
                    )
                    pm.makeIdentity(obj, apply=True, normal=1)
                    pm.makeIdentity(loc, apply=True, normal=1)
                    # 1=the normals on polygonal objects will be frozen. 2=the normals on polygonal objects will be frozen only if its a non-rigid transformation matrix.

                pm.rename(loc, format_name_with_suffix(base_name, loc))
                pm.rename(obj, format_name_with_suffix(base_name, obj))

                cls.set_attr_lock_state(
                    obj,
                    translate=lock_translate,
                    rotate=lock_rotation,
                    scale=lock_scale,
                )
            except Exception as error:
                pm.delete(loc)
                raise (error)

    @classmethod
    @CoreUtils.undoable
    def remove_locator(cls, objects):
        """Remove a parented locator from the child object.

        Parameters:
            obj (str/obj/list): The child object or the locator itself.
        """
        for obj in pm.ls(objects, long=True, objectsOnly=True):
            if not pm.objExists(obj):
                continue

            if NodeUtils.is_locator(obj):
                if not NodeUtils.get_type(obj) and not NodeUtils.get_children(obj):
                    pm.delete(obj)
                    continue

                # Unlock attributes
                cls.set_attr_lock_state(obj, translate=False, rotate=False, scale=False)

                # Get the parent and grandparent
                parent = NodeUtils.get_parent(obj)
                grandparent = NodeUtils.get_parent(parent) if parent else None

                # Get children before deleting the locator
                children = NodeUtils.get_children(obj)

                # Unparent children to world
                for child in children:
                    pm.parent(child, world=True)

                # Delete the locator
                pm.delete(obj)

                # Reparent children to grandparent or parent if grandparent doesn't exist
                new_parent = grandparent if grandparent else parent
                if new_parent:
                    for child in children:
                        pm.parent(child, new_parent)

                # Check if the parent is a group and delete it if it has no other children
                if parent and NodeUtils.is_group(parent):
                    parent_children = NodeUtils.get_children(parent)
                    if not parent_children:
                        pm.delete(parent)

            else:
                pm.warning(f"Object '{obj}' is not a locator.")

        return objects

    @classmethod
    @CoreUtils.undoable
    def set_attr_lock_state(
        cls, objects, translate=None, rotate=None, scale=None, **kwargs
    ):
        """Lock/Unlock any attribute for the given objects, by passing it into kwargs as <attr>=<value>.
        A 'True' value locks the attribute, 'False' unlocks, while 'None' leaves the state unchanged.

        Parameters:
            objects (str/obj/list): The object(s) to lock/unlock attributes of.
            translate (bool): Lock/Unlock all translate x,y,z values at once.
            rotate (bool): Lock/Unlock all rotate x,y,z values at once.
            scale (bool): Lock/Unlock all scale x,y,z values at once.

        Example:
            setAttrLockState(objects, translate=False, rotate=True)
        """
        objects = pm.ls(objects, transforms=True, long=True)

        attrs_and_state = {
            (
                "tx",
                "ty",
                "tz",
            ): translate,  # attributes and state. ex. ('tx','ty','tz'):False
            ("rx", "ry", "rz"): rotate,
            ("sx", "sy", "sz"): scale,
        }

        attrs_and_state.update(kwargs)  # update the dict with any values from kwargs.

        for obj in objects:
            try:
                if NodeUtils.is_locator(obj):
                    obj = pm.listRelatives(obj, children=1, type="transform")[0]
            except IndexError:
                return

            for attrs, state in attrs_and_state.items():
                if state is None:
                    continue
                for a in ptk.make_iterable(attrs):
                    pm.setAttr("{}.{}".format(obj, a), lock=state)

    @staticmethod
    @CoreUtils.undoable
    def create_group(
        objects=[],
        name="",
        zero_translation=False,
        zero_rotation=False,
        zero_scale=False,
    ):
        """Create a group containing any given objects.

        Parameters:
            objects (str/obj/list): The object(s) to group.
            name (str): Name the group.
            zero_translation (bool): Freeze translation before parenting.
            zero_rotation (bool): Freeze rotation before parenting.
            zero_scale (bool): Freeze scale before parenting.

        Returns:
            (obj) the group.
        """
        grp = pm.group(empty=True, n=name)
        try:
            pm.parent(grp, objects)
        except Exception as error:
            print(
                f"{__file__} in create_group\n\t# Error: Unable to parent object(s): {error} #"
            )

        if zero_translation:
            for attr in ("tx", "ty", "tz"):
                pm.setAttr(getattr(grp, attr), 0)  # pm.setAttr(node.translate, 0)
        if zero_rotation:
            for attr in ("rx", "ry", "rz"):
                pm.setAttr(getattr(grp, attr), 0)
        if zero_scale:
            for attr in ("sx", "sy", "sz"):
                pm.setAttr(getattr(grp, attr), 0)

        pm.parent(grp, world=True)
        return grp

    @staticmethod
    def constrain(
        target, objects_to_constrain, constraint_type: str = "point", **kwargs
    ) -> None:
        """Constrain all selected objects to the specified target object in Maya.

        Parameters:
            target (str or PyNode): The target object to which the constraints will be applied.
            objects_to_constrain (list): List of objects to be constrained to the target.
            constraint_type (str): The type of constraint to apply. Options are 'point', 'orient', 'parent',
                                   'scale', 'aim', or 'poleVector'. Default is 'point'.
            **kwargs: Additional keyword arguments to be passed to the constraint functions.
                      The 'maintainOffset' argument defaults to False if not provided.

        Example:
            # Point constraint without maintaining offset (default behavior)
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='point')

            # Point constraint with maintaining offset
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='point', maintainOffset=True)

            # Orient constraint without maintaining offset (default behavior)
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='orient')

            # Parent constraint with maintaining offset and additional options
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='parent', maintainOffset=True, skip=['rotateX', 'rotateY'])

            # Scale constraint without maintaining offset (default behavior)
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='scale')

            # Aim constraint without maintaining offset (default behavior)
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='aim', aimVector=[1, 0, 0], upVector=[0, 1, 0], worldUpType='scene')

            # Pole vector constraint without maintaining offset (default behavior)
            ConstraintUtils.constrain_to_last_selected(target, objects_to_constrain, constraint_type='poleVector')
        """
        if constraint_type in ["point", "orient", "parent", "scale"]:
            kwargs.setdefault("maintainOffset", False)

        for obj in objects_to_constrain:
            if constraint_type == "point":
                pm.pointConstraint(target, obj, **kwargs)
            elif constraint_type == "orient":
                pm.orientConstraint(target, obj, **kwargs)
            elif constraint_type == "parent":
                pm.parentConstraint(target, obj, **kwargs)
            elif constraint_type == "scale":
                pm.scaleConstraint(target, obj, **kwargs)
            elif constraint_type == "aim":
                kwargs.pop("maintainOffset", None)  # Remove maintainOffset if present
                pm.aimConstraint(target, obj, **kwargs)
            elif constraint_type == "poleVector":
                kwargs.pop("maintainOffset", None)  # Remove maintainOffset if present
                pm.poleVectorConstraint(target, obj, **kwargs)
            else:
                pm.warning(f"Unsupported constraint type: {constraint_type}")

        print(
            f"Applied {constraint_type} constraint from '{target}' to {len(objects_to_constrain)} objects."
        )

    @classmethod
    @CoreUtils.undoable
    def setup_telescope_rig(
        cls,
        base_locator: Union[str, List[str]],
        end_locator: Union[str, List[str]],
        segments: List[str],
        collapsed_distance: float = 1.0,
    ):
        """Sets up constraints and driven keys to make a series of segments telescope between two locators.

        Parameters:
            base_locator (str/object/list): The base locator.
            end_locator (str/object/list): The end locator.
            segments (List[str]): Ordered list of segment names. Must contain at least two segments.
            collapsed_distance (float): The distance at which the segments are in the collapsed state.

        Raises:
            ValueError: If less than two segments are provided.
        """
        base_locators = pm.ls(base_locator, flatten=True)
        if not base_locators:
            raise ValueError("At least one valid base locator must be provided.")
        base_locator = base_locators[0]

        end_locators = pm.ls(end_locator, flatten=True)
        if not end_locators:
            raise ValueError("At least one valid end locator must be provided.")
        end_locator = end_locators[0]

        segments = pm.ls(segments, flatten=True)
        if len(segments) < 2:
            raise ValueError("At least two segments must be provided.")

        def create_distance_node():
            distance_node = pm.shadingNode(
                "distanceBetween", asUtility=True, name="strut_distance"
            )
            pm.connectAttr(base_locator.translate, distance_node.point1)
            pm.connectAttr(end_locator.translate, distance_node.point2)
            return distance_node

        def create_and_constrain_midpoint_locator(start_locator, end_locator, index):
            midpoint_locator_name = f"segment_locator_{index}"
            midpoint_locator = pm.spaceLocator(name=midpoint_locator_name)
            midpoint_pos = (
                pm.datatypes.Vector(start_locator.getTranslation(space="world"))
                + pm.datatypes.Vector(end_locator.getTranslation(space="world"))
            ) / 2
            midpoint_locator.setTranslation(midpoint_pos, space="world")
            pm.pointConstraint(start_locator, end_locator, midpoint_locator)
            pm.aimConstraint(
                end_locator,
                midpoint_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            return midpoint_locator

        def constrain_segments():
            pm.parentConstraint(base_locator, segments[0], mo=True)
            pm.parentConstraint(end_locator, segments[-1], mo=True)
            if len(segments) > 2:
                for i, segment in enumerate(segments[1:-1], start=1):
                    midpoint_locator = create_and_constrain_midpoint_locator(
                        segments[i - 1], segments[i + 1], i
                    )
                    pm.parent(segment, midpoint_locator)
                    pm.aimConstraint(
                        end_locator,
                        segment,
                        aimVector=(0, 1, 0),
                        upVector=(0, 1, 0),
                        worldUpType="scene",
                    )

        def set_driven_keys(distance_node, initial_distance):
            for segment in segments[1:-1]:
                pm.setDrivenKeyframe(
                    segment + ".scaleY",
                    currentDriver=distance_node.distance,
                    driverValue=initial_distance,
                    value=1,
                )
                pm.setDrivenKeyframe(
                    segment + ".scaleY",
                    currentDriver=distance_node.distance,
                    driverValue=collapsed_distance,
                    value=collapsed_distance / initial_distance,
                )

        def lock_segment_attributes():
            for segment in segments:
                pm.setAttr(segment + ".translateX", lock=True)
                pm.setAttr(segment + ".translateZ", lock=True)
                pm.setAttr(segment + ".rotateX", lock=True)
                pm.setAttr(segment + ".rotateZ", lock=True)
                pm.setAttr(segment + ".scaleX", lock=True)
                pm.setAttr(segment + ".scaleZ", lock=True)

        def constrain_locators():
            pm.aimConstraint(
                end_locator,
                base_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            pm.aimConstraint(
                base_locator,
                end_locator,
                aimVector=(0, -1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )

        distance_node = create_distance_node()
        constrain_locators()
        constrain_segments()

        initial_distance = pm.getAttr(distance_node.distance)
        set_driven_keys(distance_node, initial_distance)
        lock_segment_attributes()

    @staticmethod
    def setupConstraintOverride(
        switchAttr, constraintNode, weightIndex, overrideValue=1.0
    ):
        """Sets up a node network so that when a given boolean switch attribute is on,
        it overrides any keyed values on the specified constraint weight.

        Parameters:
            switchAttr (pm.Attribute): The boolean attribute used as a switch.
            constraintNode (pm.nt.Constraint): The constraint node whose weight you want to override.
            weightIndex (int): The index of the weight in the constraintNode's weight list to override.
            overrideValue (float): The value to force when the switch is on.

        Example:
            setupConstraintOverride(
                switchAttr="SMALL_TOW_TRUCK_CTRL.rearTowSwitch",
                constraintNode=pm.PyNode("C130J_TOWBAR_LOC_pointConstraint1"),
                weightIndex=0,
                overrideValue=1.0,
            )
        """
        # Identify the weight attribute
        weightAliasList = constraintNode.getWeightAliasList()
        if weightIndex >= len(weightAliasList):
            raise IndexError(
                "weightIndex is out of range for this constraint's weight aliases."
            )
        weightAttr = weightAliasList[weightIndex]

        # Remove any existing keyframes on the constraint weight attribute
        # so that it's purely driven by the upcoming node network.
        pm.cutKey(weightAttr, clear=True)  # Remove any existing animation keys

        # Create a keyable original weight attribute to store user-driven (keyed) values
        origWeightAttrName = "origWeight{}".format(weightIndex)
        if not constraintNode.hasAttr(origWeightAttrName):
            pm.addAttr(
                constraintNode,
                ln=origWeightAttrName,
                at="double",
                k=True,
                dv=pm.getAttr(weightAttr),
            )
        origWeightAttr = constraintNode.attr(origWeightAttrName)

        # Disconnect any incoming connections to the constraint weight
        currentConnections = weightAttr.listConnections(plugs=True, s=True, d=False)
        for conn in currentConnections:
            pm.disconnectAttr(conn, weightAttr)

        # Create a condition node to handle the switch
        condNode = pm.createNode(
            "condition", name=constraintNode.name() + "_overrideCondition"
        )
        condNode.operation.set(0)  # "Equal": True if firstTerm == secondTerm
        condNode.firstTerm.set(1)  # We'll compare switchAttr to 1

        # Connect the switch attribute to secondTerm
        pm.connectAttr(switchAttr, condNode.secondTerm, f=True)

        # When switch is True, condition passes overrideValue
        condNode.colorIfTrueR.set(overrideValue)

        # When switch is False, pass through origWeightAttr
        pm.connectAttr(origWeightAttr, condNode.colorIfFalseR, f=True)

        # Connect condition output to the constraint weight
        pm.connectAttr(condNode.outColorR, weightAttr, f=True)

    @staticmethod
    def rig_wheel_rotation(
        control,
        wheels,
        movement_axis="translateZ",
        rotation_axis="rotateX",
        wheel_height=1.0,
        invert_rotation: bool = False,
    ) -> None:
        """Rig wheels to rotate automatically as the control moves along a specified axis.

        Parameters:
            control (str, obj, list): The name of the control that moves the truck.
            wheels (str, obj, list): The names or objects of the wheel objects to be rotated.
            movement_axis (str): The movement axis of the control (e.g., 'translateZ', 'translateX').
            rotation_axis (str): The rotation axis for the wheels (e.g., 'rotateX', 'rotateY').
            wheel_height (float): The height of the wheel (used to calculate radius and circumference).
            invert_rotation (bool): If True, inverts the direction of the wheel rotation.

        Example:
            rig_wheel_rotation(
                control="C130J_TOW_POINT_LOC",
                wheels=("FWD_WHEELS_LOC", "AFT_WHEELS_A_LOC", "AFT_WHEELS_B_LOC"),
                movement_axis="translateZ",
                rotation_axis="rotateX",
                wheel_height=91,
                invert_rotation=False,
            )
        """
        # Calculate the radius and circumference of the wheels
        wheel_radius = wheel_height / 2.0
        wheel_circumference = 2 * 3.14159 * wheel_radius  # 2πr

        # Get the control node using pm.ls to support both strings and PyNodes
        control_nodes = pm.ls(control)
        if not control_nodes:
            pm.warning(f"Control '{control}' not found in the scene.")
            return
        truck_ctrl = control_nodes[0]  # Get the first match from pm.ls

        # Get wheel nodes using pm.ls to support both strings and PyNodes
        wheels = pm.ls(wheels)
        if not wheels:
            pm.warning(f"No valid wheels found for: {wheels}")
            return

        # Calculate rotation direction (invert if necessary)
        rotation_sign = -1 if invert_rotation else 1

        # Generate expression string
        expression_str = f"""
        float $distance = {truck_ctrl}.{movement_axis};
        float $rotation = ($distance / {wheel_circumference}) * 360 * {rotation_sign};
        """
        for wheel in wheels:
            expression_str += f"""
            {wheel}.{rotation_axis} = $rotation;
            """

        # Apply the expression to Maya
        pm.expression(s=expression_str)

        print(
            f"Successfully rigged {len(wheels)} wheels to rotate with {control} movement on {movement_axis}."
        )

    @staticmethod
    def get_joint_chain_from_root(
        root_joint: Union[str, List[str]], reverse: bool = False
    ) -> List[str]:
        """Get the joint chain from the root joint or the first joint in the list if more than one joint is given.

        Parameters:
            root_joint (str): The root joint of the chain.
            reverse (bool): Whether to return the joint chain in reverse order. Default is False.

        Returns:
            List[str]: The joint chain.
        """
        joints = pm.ls(root_joint, type="joint", flatten=True)
        if not joints or len(joints) > 1:
            pm.warning(f"Operation requires a root joint: got {root_joint}")
            return []
        root_joint = joints[0]

        # Traverse the hierarchy to get the joint chain
        joint_chain = []
        current_joint = root_joint
        while current_joint:
            joint_chain.append(current_joint)
            children = pm.listRelatives(current_joint, children=True, type="joint")
            if children:
                current_joint = children[0]
            else:
                current_joint = None

        if reverse:
            joint_chain.reverse()

        return joint_chain

    @staticmethod
    def invert_joint_chain(root_joint, keep_original=False):
        """Create a new joint chain with the same positions as the original, but with reversed hierarchy.

        Parameters:
            root_joint (str): The root joint of the original joint chain.
            keep_original (bool): Whether to keep the original joint chain. Default is False.

        Returns:
            list: The new joint chain with reversed hierarchy.
        """
        # Get the original joint chain starting from the root
        original_joints = pm.listRelatives(
            root_joint, allDescendents=True, type="joint"
        )
        original_joints.append(root_joint)
        original_joints.reverse()  # Now from end joint to root joint

        # Collect positions and radii of the original joints
        joint_positions = [
            joint.getTranslation(space="world") for joint in original_joints
        ]
        joint_radii = [joint.radius.get() for joint in original_joints]

        if not keep_original:
            pm.delete(original_joints)

        # Create a new joint chain along the same positions
        pm.select(clear=True)
        new_joints = []
        for i, pos in enumerate(joint_positions):
            new_joint = pm.joint(position=pos)
            new_joints.append(new_joint)
            # Set the joint radius to match the original
            new_joint.radius.set(joint_radii[i])

        # Unparent all new joints
        for joint in new_joints:
            pm.parent(joint, world=True)

        # Reverse the new joints list to set up reversed hierarchy
        new_joints.reverse()

        # Re-parent joints in reverse order to create reversed hierarchy
        for i in range(len(new_joints) - 1):
            pm.parent(new_joints[i + 1], new_joints[i])

        # Zero out joint orientations before reorienting
        for joint in new_joints:
            joint.jointOrient.set([0, 0, 0])

        # Reorient the joints to point towards their children
        pm.select(new_joints[0], hierarchy=True)
        pm.joint(
            edit=True,
            orientJoint="xyz",
            secondaryAxisOrient="yup",
            zeroScaleOrient=True,
            children=True,
        )

        return new_joints


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
