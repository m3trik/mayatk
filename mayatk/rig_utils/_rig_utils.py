# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Union

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
    def create_locator(*, scale: float = 1, **kwargs) -> object:
        """Create a locator with the given scale.

        Parameters:
            scale (float): The desired scale of the locator.
            **kwargs: Additional keyword arguments for the spaceLocator command, including 'name' and 'position'.

        Special Handling:
            If 'position' is provided in kwargs and it is not a tuple or list, it is assumed to be an object.
            The method attempts to get the world space position of this object to use as the locator's position.
            If the position cannot be resolved, it is removed from kwargs.

        Returns:
            pm.nt.Transform: The created locator transform node.
        """
        pos = kwargs.get("position")

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
            pm.scale(loc, scale, scale, scale)  # scale the locator

        return loc

    @classmethod
    @CoreUtils.undo
    def remove_locator(cls, objects):
        """Remove a parented locator from the child object.

        Parameters:
            obj (str/obj/list): The child object or the locator itself.
        """
        for obj in pm.ls(objects, long=True, objectsOnly=True):
            if not pm.objExists(obj):
                continue

            elif (
                NodeUtils.is_locator(obj)
                and not NodeUtils.get_type(obj)
                and not NodeUtils.get_children(obj)
            ):
                pm.delete(obj)
                continue

            # unlock attributes
            cls.set_attr_lock_state(obj, translate=False, rotate=False, scale=False)

            if not NodeUtils.is_locator(obj):
                try:  # if the 'obj' is not a locator, check if it's parent is.
                    obj = NodeUtils.get_parent(obj)
                    if not NodeUtils.is_locator(obj):
                        pm.inViewMessage(
                            status_message="Error: Unable to remove locator for the given object.",
                            pos="topCenter",
                            fade=True,
                        )
                        continue
                except IndexError:
                    pm.inViewMessage(
                        status_message="Error: Unable to remove locator for the given object.",
                        pos="topCenter",
                        fade=True,
                    )
                    continue

            # unparent child object
            children = NodeUtils.get_children(obj)
            for child in children:
                pm.parent(child, world=True)

            # remove locator
            pm.delete(obj)

    @classmethod
    @CoreUtils.undo
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
    @CoreUtils.undo
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
    @CoreUtils.undo
    def create_group_with_first_obj_lra(objects, name="", freeze_transforms=True):
        """Creates a group using the first object to define the local rotation axis.

        Parameters:
            objects (str/obj/list): The objects to group. The first object will be used to define the groups LRA.
            name (str): The group name.
            freeze_transforms (bool): Freeze transforms on group child objects.
        """
        try:
            obj, *other = pm.ls(objects, transforms=1)
        except IndexError:
            print(
                f"{__file__} in create_group_with_first_obj_lra\n\t# Error: Operation requires at least one object. #"
            )
            return None

        # Bake the pivot on the object that will define the LRA.
        XformUtils.bake_pivot(obj, position=True, orientation=True)

        grp = pm.group(empty=True)
        pm.parent(grp, obj)

        pm.setAttr(grp.translate, (0, 0, 0))
        pm.setAttr(grp.rotate, (0, 0, 0))

        objParent = pm.listRelatives(obj, parent=1)
        pm.parent(
            grp, objParent
        )  # parent the instance under the original objects parent.

        try:
            pm.parent(obj, grp)
        except Exception:  # root level objects
            pm.parent(grp, world=True)
            pm.parent(obj, grp)

        for o in other:  # parent any other objects to the new group.
            pm.parent(o, grp)
            if freeze_transforms:
                pm.makeIdentity(o, apply=True)  # freeze transforms on child objects.

        if not name and objParent:  # name the group.
            pm.rename(grp, objParent[0].name())
        elif not name:
            pm.rename(grp, obj.name())
        else:
            pm.rename(grp, name)

        return grp

    @classmethod
    @CoreUtils.undo
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
        scale=1,
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
            scale (float) = The scale of the locator. (default=1)
            lock_translate (bool): Lock the translate values of the child object. (default=False)
            lock_rotation (bool): Lock the rotation values of the child object. (default=False)
            lock_scale (bool): Lock the scale values of the child object. (default=False)
            remove (bool): Removes the locator and any child locks. (not valid with component selections) (default=False)

        Example:
            createLocatorAtSelection(strip='_GEO', suffix='', strip_digits=True, parent=True, lock_translate=True, lock_rotation=True)
        """
        getSuffix = lambda o: (
            loc_suffix
            if NodeUtils.is_locator(o)
            else grp_suffix if NodeUtils.is_group(o) else obj_suffix
        )  # match the correct suffix to the object type.

        for obj in pm.ls(objects, long=True, type="transform"):
            if bake_child_pivot:
                XformUtils.bake_pivot(obj, position=1, orientation=1)

            vertices = pm.filterExpand(obj, sm=31)  # returns a string list.
            if vertices:
                objName = vertices[0].split(".")[0]
                obj = pm.ls(objName)

                loc = cls.create_locator(scale=scale)

                xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(vertices)
                x, y, z = (
                    (xmin + xmax) / 2,
                    (ymin + ymax) / 2,
                    (zmin + zmax) / 2,
                )
                pm.move(x, y, z, loc)

            else:  # object:
                loc = cls.create_locator(scale=scale)
                tempConst = pm.parentConstraint(obj, loc, maintainOffset=False)
                pm.delete(tempConst)

            try:
                if parent:
                    origParent = pm.listRelatives(obj, parent=1)

                    grp = cls.create_group(obj, zero_translation=1, zero_rotation=1)
                    pm.rename(
                        grp,
                        ptk.format_suffix(
                            obj.name(),
                            suffix=getSuffix(grp),
                            strip=(obj_suffix, grp_suffix, loc_suffix),
                            strip_trailing_ints=strip_digits,
                            strip_trailing_alpha=strip_suffix,
                        ),
                    )

                    pm.parent(obj, loc)
                    pm.parent(loc, grp)
                    pm.parent(grp, origParent)

                if freeze_transforms:  # freeze transforms before baking pivot.
                    cls.set_attr_lock_state(
                        obj, translate=False, rotate=False, scale=False
                    )  # assure attributes are unlocked.
                    pm.makeIdentity(obj, apply=True, normal=1)
                    pm.makeIdentity(
                        loc, apply=True, normal=1
                    )  # 1=the normals on polygonal objects will be frozen. 2=the normals on polygonal objects will be frozen only if its a non-rigid transformation matrix.

                pm.rename(
                    loc,
                    ptk.format_suffix(
                        obj.name(),
                        suffix=getSuffix(loc),
                        strip=(obj_suffix, grp_suffix, loc_suffix),
                        strip_trailing_ints=strip_digits,
                        strip_trailing_alpha=strip_suffix,
                    ),
                )
                pm.rename(
                    obj,
                    ptk.format_suffix(
                        obj.name(),
                        suffix=getSuffix(obj),
                        strip=(obj_suffix, grp_suffix, loc_suffix),
                        strip_trailing_ints=strip_digits,
                        strip_trailing_alpha=strip_suffix,
                    ),
                )

                cls.set_attr_lock_state(
                    obj,
                    translate=lock_translate,
                    rotate=lock_rotation,
                    scale=lock_scale,
                )

            except Exception as error:
                pm.delete(loc)
                raise (error)

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
    @CoreUtils.undo
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


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
