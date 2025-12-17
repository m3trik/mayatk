# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Dict, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils


class RigUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    @CoreUtils.undoable
    def create_helper(
        name: str,
        helper_type: str = "locator",
        parent: Optional["pm.nt.Transform"] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        cleanup: bool = False,
    ) -> Optional["pm.nt.Transform"]:
        """Create a hidden helper object (e.g., locator, joint) with a consistent naming convention.
        Optionally cleans up (deletes) the helper if it already exists and cleanup is True.

        Parameters:
            name (str): Helper name (should include "__" as per convention).
            helper_type (str): Maya node type to create (e.g., "locator", "joint").
            parent (pm.nt.Transform or None): Optional parent transform.
            position (tuple): Position in world space.
            cleanup (bool): If True, deletes existing helper with same name and returns None.

        Returns:
            pm.nt.Transform or None: The created or existing helper, or None if cleaned up.
        """
        if pm.objExists(name):
            if cleanup:
                pm.delete(name)
                return None
            return pm.PyNode(name)

        if helper_type.lower() == "locator":
            helper = pm.spaceLocator(n=name)
        elif helper_type.lower() == "joint":
            helper = pm.createNode("joint", n=name)
        else:
            helper = pm.createNode(helper_type, n=name)

        if parent is not None:
            helper.setParent(parent)
        else:
            helper.setParent(world=True)

        helper.translate.set(position)
        helper.visibility.set(0)

        return helper

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
            if objects:
                pm.parent(objects, grp)
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
        pos = kwargs.pop("position", None)

        if pos is not None:
            if not isinstance(pos, (tuple, list)):
                transform_node = NodeUtils.get_transform_node([pos])
                if transform_node:
                    pos = pm.xform(transform_node[0], q=True, ws=True, t=True)
                else:
                    pos = None

        loc = pm.spaceLocator(**{k: v for k, v in kwargs.items() if v is not None})

        if pos is not None:
            pm.xform(loc, ws=True, t=pos)

        if scale != 1:
            pm.scale(loc, scale, scale, scale)
        if parent:
            loc.setParent(parent)

        return loc

    @classmethod
    @CoreUtils.undoable
    def create_locator_at_object(
        cls,
        objects: Union[
            str, "pm.nodetypes.Transform", List[Union[str, "pm.nodetypes.Transform"]]
        ],
        parent: bool = True,
        freeze_object: bool = True,
        freeze_locator: bool = True,
        loc_scale: float = 1.0,
        lock_translate: bool = False,
        lock_rotation: bool = False,
        lock_scale: bool = False,
        grp_suffix: str = "_GRP",
        loc_suffix: str = "_LOC",
        obj_suffix: str = "_GEO",
        strip_digits: bool = False,
        strip_trailing_underscores: bool = True,
    ) -> None:
        """Rig object under a zeroed locator aligned to its d manip pivot.

        Parameters:
            objects (str/obj/list): Objects to create locator rigs for.
            parent (bool): Whether to parent object under locator and locator under group.
            freeze_object (bool): Freeze object transforms after setup.
            freeze_locator (bool): Freeze locator transforms after alignment.
            loc_scale (float): Scale of locator display.
            lock_translate (bool): Lock object's translate attributes.
            lock_rotation (bool): Lock object's rotate attributes.
            lock_scale (bool): Lock object's scale attributes.
            grp_suffix (str): Naming suffix for the created group. Default "_GRP".
            loc_suffix (str): Naming suffix for the locator. Default "_LOC".
            obj_suffix (str): Naming suffix for the renamed object. Default "_GEO".
            strip_digits (bool): Whether to strip trailing digits before suffixing.
            strip_trailing_underscores (bool): Whether to strip trailing underscores before adding new suffix.
        """
        import re

        def format_name_with_suffix(base_name: str, suffix: str) -> str:
            strip_tuple = (grp_suffix, loc_suffix, obj_suffix)
            clean_name = ptk.format_suffix(
                base_name,
                suffix="",
                strip=strip_tuple,
                strip_trailing_ints=strip_digits,
            )
            if strip_trailing_underscores:
                clean_name = re.sub(r"_+$", "", clean_name)
            result = f"{clean_name}{suffix}" if suffix else clean_name
            if not result:
                pm.warning(
                    f"[create_locator_at_object] Skipping rename: "
                    f"Attempted to rename '{base_name}' with suffix '{suffix}', "
                    f"but this would result in an empty or invalid name. Using base name instead."
                )
                result = base_name
            return result

        for obj in pm.ls(objects, long=True, type="transform", flatten=True):
            orig_name = obj.nodeName()
            if not orig_name:
                orig_name = obj.name().split("|")[-1]

            # Strip suffixes from the original name once
            base_name_stripped = format_name_with_suffix(orig_name, "")

            mesh_shape = obj.getShape()
            vertices = mesh_shape.vtx[:] if mesh_shape else None
            orig_parent = pm.listRelatives(obj, parent=True)
            is_group = NodeUtils.is_group(obj)

            if not is_group:
                XformUtils.bake_pivot(obj, position=True, orientation=True)

            matrix = XformUtils.get_manip_pivot_matrix(obj, ws=True)

            loc = cls.create_locator(scale=loc_scale)
            pm.xform(loc, matrix=matrix, ws=True)

            if parent:
                grp = pm.group(em=True)
                pm.delete(pm.parentConstraint(loc, grp))
                pm.parent(loc, grp)
                pm.parent(obj, loc)

                if freeze_locator:
                    XformUtils.freeze_transforms(loc, normal=True)

                if orig_parent:
                    pm.parent(grp, orig_parent)

            if vertices:
                pm.polyNormalPerVertex(vertices, unFreezeNormal=True)

            # Freeze object after hierarchy is set up (but not groups)
            if freeze_object and not is_group:
                XformUtils.freeze_transforms(obj, normal=True)

            # Rename group, locator, and object using the clean base name
            # IMPORTANT: Reassign variables after renaming to update PyMEL references
            if parent:
                grp = pm.rename(grp, f"{base_name_stripped}{grp_suffix}")
            loc = pm.rename(loc, f"{base_name_stripped}{loc_suffix}")
            # Only apply obj_suffix if the object is not a group
            if not is_group:
                obj = pm.rename(obj, f"{base_name_stripped}{obj_suffix}")

            if parent:
                XformUtils.freeze_transforms(grp, scale=True)

            cls.set_attr_lock_state(
                obj,
                translate=lock_translate,
                rotate=lock_rotation,
                scale=lock_scale,
            )
            pm.select(loc, replace=True)

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

    @staticmethod
    def get_attr_lock_state(objects, unlock: bool = False) -> dict:
        """Returns lock state for standard transform attributes and optionally unlocks them.

        Parameters:
            objects (list): Maya transform nodes
            unlock (bool): If True, unlocks the attributes after storing their state.

        Returns:
            Dict[str, Dict[str, bool]]: {
                "myObject": {
                    "translate": True,
                    "rotate": False,
                    "scale": None,
                    "tx": True, "ty": True, ...
                }
            }
        """
        objects = pm.ls(objects, transforms=True, long=True)
        attr_groups = {
            "translate": ("tx", "ty", "tz"),
            "rotate": ("rx", "ry", "rz"),
            "scale": ("sx", "sy", "sz"),
        }

        result = {}

        for obj in objects:
            try:
                if NodeUtils.is_locator(obj):
                    obj = pm.listRelatives(obj, children=1, type="transform")[0]
            except IndexError:
                continue

            obj_state = {}

            for group, attrs in attr_groups.items():
                group_vals = []
                for attr in attrs:
                    try:
                        full_attr = f"{obj}.{attr}"
                        locked = pm.getAttr(full_attr, lock=True)
                        obj_state[attr] = locked
                        group_vals.append(locked)
                        if unlock and locked:
                            pm.setAttr(full_attr, lock=False)
                    except Exception:
                        obj_state[attr] = None
                        group_vals.append(None)
                # Set unified group state
                if all(v is True for v in group_vals):
                    obj_state[group] = True
                elif all(v is False for v in group_vals):
                    obj_state[group] = False
                else:
                    obj_state[group] = None

            result[obj.name()] = obj_state

        return result

    @classmethod
    @CoreUtils.undoable
    def set_attr_lock_state(
        cls,
        objects,
        lock_state: Optional[Dict[str, Dict[str, bool]]] = None,
        translate: Optional[bool] = None,
        rotate: Optional[bool] = None,
        scale: Optional[bool] = None,
        **kwargs,
    ) -> None:
        """
        Restore lock state using saved per-axis info, or lock/unlock in bulk.
        """
        objects = pm.ls(objects, transforms=True, long=True)

        for obj in objects:
            try:
                if NodeUtils.is_locator(obj):
                    obj = pm.listRelatives(obj, children=1, type="transform")[0]
            except (IndexError, TypeError):
                continue

            # Restore per-attribute lock state
            if lock_state and obj.name() in lock_state:
                state = lock_state[obj.name()]
                for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
                    lock_val = state.get(attr)
                    if lock_val is not None:
                        try:
                            pm.setAttr(f"{obj}.{attr}", lock=lock_val)
                        except Exception:
                            pass
                continue  # skip bulk lock logic if restoring from lock_state

            # Bulk translate/rotate/scale lock
            attr_map = {
                ("tx", "ty", "tz"): translate,
                ("rx", "ry", "rz"): rotate,
                ("sx", "sy", "sz"): scale,
            }
            for attrs, state in attr_map.items():
                if state is None:
                    continue
                for attr in attrs:
                    try:
                        pm.setAttr(f"{obj}.{attr}", lock=state)
                    except Exception:
                        pass

            # Individual attribute locks from kwargs
            for attr, state in kwargs.items():
                if state is None:
                    continue
                try:
                    pm.setAttr(f"{obj}.{attr}", lock=state)
                except Exception:
                    pass

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
    @CoreUtils.undoable
    def create_switch_attr(
        node: "pm.PyNode",
        attr_name: str,
        weighted: bool = False,
        min_value: float = 0.0,
        max_value: float = 1.0,
    ) -> "pm.Attribute":
        """Create a bool or float (weighted) attribute on the node if it doesn't exist.

        Parameters:
            node (pm.PyNode): Node to add the attribute to.
            attr_name (str): Attribute name.
            weighted (bool): If True, create float (0–1), else bool.
            min_value (float): Min value for weighted attr.
            max_value (float): Max value for weighted attr.

        Returns:
            pm.Attribute: The created or existing attribute.
        """
        if node.hasAttr(attr_name):
            return node.attr(attr_name)

        if weighted:
            pm.addAttr(
                node,
                ln=attr_name,
                at="double",
                min=min_value,
                max=max_value,
                k=True,
                dv=0,
            )
        else:
            pm.addAttr(
                node,
                ln=attr_name,
                at="bool",
                k=True,
                dv=0,
            )
        return node.attr(attr_name)

    @classmethod
    @CoreUtils.undoable
    def connect_switch_to_constraint(
        cls,
        constraint_node: "pm.nt.Constraint",
        constraint_targets: Optional[List["pm.nt.Transform"]] = None,
        attr_name: str = "parent_switch",
        overwrite_existing: bool = False,
        node: Optional["pm.PyNode"] = None,
        weighted: bool = False,
        anchor: Optional[str] = None,
    ) -> dict:
        """
        Create a space switch attribute to drive a constraint node.
        - 1 target, no anchor: bool (on/off toggle)
        - 2 targets: enum or float (blend if weighted)
        - 3+ targets: enum (dropdown snap)

        Parameters:
            constraint_node (pm.nt.Constraint): The constraint node to control.
            constraint_targets (Optional[List[pm.nt.Transform]]): List of target transforms for the constraint. If None, auto-detected.
            attr_name (str): Name of the switch attribute to create.
            overwrite_existing (bool): If True, deletes and recreates the attribute if it exists.
            node (Optional[pm.PyNode]): Node to add the switch attribute to. If None, derived from the driven object.
            weighted (bool): If True, creates a float attribute for smooth blending (2 targets only).
            anchor (Optional[str]): If given, creates a locator at origin as a neutral/anchor/world target with this name.

        Returns:
            dict: Dictionary of created nodes and attributes for further processing.
        """
        if not constraint_node or not isinstance(constraint_node, pm.nt.Constraint):
            raise TypeError(
                "constraint_node must be a valid PyMEL constraint node (pm.nt.Constraint)."
            )

        result = {}
        # Target autodetect if not provided
        if constraint_targets is None:
            if hasattr(constraint_node, "getTargetList"):
                constraint_targets = constraint_node.getTargetList()
            else:
                constraint_targets = [
                    t
                    for t in constraint_node.target.inputs()
                    if isinstance(t, pm.nt.Transform)
                ]

        # Check targets
        if not constraint_targets or len(constraint_targets) < 1:
            pm.warning("No constraint targets found or provided.")
            return result

        # Optionally add anchor as the last target
        if anchor:
            anchor_obj = cls.create_helper(
                name=anchor,
                helper_type="locator",
                position=(0, 0, 0),
            )
            constraint_targets = list(constraint_targets) + [anchor_obj]
            result["anchor_helper"] = anchor_obj

        num_targets = len(constraint_targets)

        if node is None:
            try:
                node = constraint_node.getOutputTransform()
            except Exception:
                driven = pm.listRelatives(
                    constraint_node, type="transform", parent=True
                )
                node = driven[0] if driven else None
        if node is None:
            pm.warning("Could not determine node to add switch attribute to.")
            return result

        # Check for duplicate attribute, handle overwrite
        if node.hasAttr(attr_name):
            if overwrite_existing:
                node.deleteAttr(attr_name)
            else:
                pm.warning(f"{node}.{attr_name} already exists.")
                return result

        weight_alias_list = constraint_node.getWeightAliasList()

        # Ensure number of weights matches number of targets
        if len(weight_alias_list) < num_targets:
            pm.warning("Number of constraint weights does not match number of targets.")
            return result

        # Disconnect all inputs from weights
        for weight_attr in weight_alias_list:
            pm.cutKey(weight_attr, clear=True)
            for conn in weight_attr.listConnections(plugs=True, s=True, d=False):
                pm.disconnectAttr(conn, weight_attr)

        # --- Single target, no anchor: simple bool toggle for constraint on/off ---
        if num_targets == 1:
            node.addAttr(attr_name, at="bool", k=True)
            switch_attr = node.attr(attr_name)
            pm.setAttr(switch_attr, 0)
            result["switch_attr"] = switch_attr

            weight_attr = weight_alias_list[0]
            cond_name = f"{constraint_node.nodeName()}_{attr_name}_cond0"
            cond_node = pm.createNode("condition", name=cond_name)
            cond_node.operation.set(0)  # == compare
            cond_node.firstTerm.set(1)
            pm.connectAttr(switch_attr, cond_node.secondTerm, f=True)
            cond_node.colorIfTrueR.set(1.0)
            cond_node.colorIfFalseR.set(0.0)
            pm.connectAttr(cond_node.outColorR, weight_attr, f=True)
            result["condition_node"] = cond_node
            return result

        # --- Weighted float blend for 2 targets only ---
        if weighted and num_targets == 2:
            node.addAttr(attr_name, at="double", min=0.0, max=1.0, k=True)
            switch_attr = node.attr(attr_name)
            result["switch_attr"] = switch_attr
            pm.setAttr(switch_attr, 0)
            pm.connectAttr(switch_attr, weight_alias_list[0], f=True)
            rev_name = f"{node.nodeName()}_{attr_name}_reverse"
            if pm.objExists(rev_name):
                rev_node = pm.PyNode(rev_name)
            else:
                rev_node = pm.createNode("reverse", name=rev_name)
            pm.connectAttr(switch_attr, rev_node.inputX, f=True)
            pm.connectAttr(rev_node.outputX, weight_alias_list[1], f=True)
            result["reverse_node"] = rev_node
            return result

        # --- Enum dropdown for snap switching (2 or more targets) ---
        enum_names = [t.nodeName() for t in constraint_targets]
        enum_string = ":".join(enum_names)
        node.addAttr(attr_name, at="enum", en=enum_string, k=True)
        switch_attr = node.attr(attr_name)
        pm.setAttr(switch_attr, 0)
        result["switch_attr"] = switch_attr

        # For each weight, create a condition node that checks if switch matches index
        for i, weight_attr in enumerate(weight_alias_list[:num_targets]):
            cond_name = f"{constraint_node.nodeName()}_{attr_name}_cond{i}"
            cond_node = pm.createNode("condition", name=cond_name)
            cond_node.operation.set(0)  # == compare
            pm.setAttr(cond_node.firstTerm, i)
            pm.connectAttr(switch_attr, cond_node.secondTerm, f=True)
            pm.setAttr(cond_node.colorIfTrueR, 1.0)
            pm.setAttr(cond_node.colorIfFalseR, 0.0)
            pm.connectAttr(cond_node.outColorR, weight_attr, f=True)
            result[f"condition_node_{i}"] = cond_node

        return result

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

    @classmethod
    @CoreUtils.undoable
    def rebind_skin_clusters(
        cls,
        meshes: Optional[List["pm.nt.Transform"]] = None,
        temp_dir: Optional[str] = None,
        inherits_transform: Optional[bool] = None,
    ) -> None:
        """Rebinds skinClusters on the given meshes, preserving weights, bind pose, and transform lock state.

        Parameters:
            meshes (List[pm.nt.Transform], optional): Mesh transform nodes to process. If None, all skinned meshes are used.
            temp_dir (str, optional): Directory for exporting temporary weight files. Defaults to Maya temp.
            inherits_transform (bool or None, optional):
                - True: explicitly sets inheritsTransform = True
                - False: explicitly sets inheritsTransform = False
                - None: preserves the original inheritsTransform value
        """
        import os

        if temp_dir is None:
            temp_dir = os.path.join(
                pm.internalVar(userTmpDir=True), "skin_rebind_weights"
            )
        os.makedirs(temp_dir, exist_ok=True)

        mesh_shapes = (
            pm.ls(type="mesh", noIntermediate=True)
            if meshes is None
            else [
                m.getShape()
                for m in meshes
                if isinstance(m, pm.nt.Transform) and m.getShape()
            ]
        )

        for shape in mesh_shapes:
            try:
                skin_clusters = pm.listHistory(shape, type="skinCluster")
                if not skin_clusters:
                    continue

                skin_cluster = skin_clusters[0]
                transform = shape.getParent()
                transform_name = transform.nodeName()

                print(f"Processing: {skin_cluster} on {transform_name}")

                # Preserve inheritsTransform and unlock transform attrs
                original_inherits = transform.inheritsTransform.get()
                lock_state = cls.get_attr_lock_state(transform, unlock=True)

                # Cache influences and bindPreMatrix
                influences = pm.skinCluster(skin_cluster, query=True, influence=True)
                bind_pre_matrices = {
                    jnt: skin_cluster.bindPreMatrix[
                        skin_cluster.indexForInfluenceObject(jnt)
                    ].get()
                    for jnt in influences
                }

                # Export weights
                weight_file = os.path.join(temp_dir, f"{transform_name}_weights.xml")
                pm.deformerWeights(
                    os.path.basename(weight_file),
                    export=True,
                    deformer=skin_cluster,
                    path=temp_dir,
                    shape=shape,
                )

                # Delete original skinCluster
                skin_cluster_name = skin_cluster.nodeName()
                pm.delete(skin_cluster)

                # Recreate skinCluster
                new_skin_cluster = pm.skinCluster(
                    influences,
                    transform,
                    toSelectedBones=True,
                    bindMethod=0,
                    skinMethod=0,
                    normalizeWeights=1,
                    name=skin_cluster_name,
                )

                # Restore bindPreMatrix
                for jnt, mat in bind_pre_matrices.items():
                    index = new_skin_cluster.indexForInfluenceObject(jnt)
                    new_skin_cluster.bindPreMatrix[index].set(mat)

                # Import weights
                pm.deformerWeights(
                    os.path.basename(weight_file),
                    im=True,
                    deformer=new_skin_cluster,
                    method="index",
                    path=temp_dir,
                    shape=shape,
                )

                # Set or restore inheritsTransform
                final_inherits = (
                    original_inherits
                    if inherits_transform is None
                    else inherits_transform
                )
                transform.inheritsTransform.set(final_inherits)
                transform.inheritsTransform.setKeyable(True)
                transform.inheritsTransform.showInChannelBox(True)

                # Restore transform lock state
                cls.set_attr_lock_state(transform, **lock_state[transform.name()])

                print(f"✔ Rebound: {transform_name}")

            except Exception as e:
                print(f"✘ Failed: {shape.name()} → {e}")


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
