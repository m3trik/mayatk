# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from pythontk import Str, Iter

# from this package:
from mayatk import node_utils


class Rig(object):
    """ """

    @staticmethod
    def create_locator(name=None, pos=(), scale=1):
        """Create a locator with the given scale.

        Parameters:
                name (str): Name the locator.
                pos (tuple): The desired location in world space.
                scale (float) = The desired scale of the locator.

        Returns:
                (obj) locator
        """
        loc = pm.spaceLocator()

        if name:
            pm.rename(loc, name)
        if pos:
            pm.move(pos[0], pos[1], pos[2], loc)
        if scale != 1:
            pm.scale(loc, scale, scale, scale)  # scale the locator

        return loc

    @classmethod
    def remove_locator(cls, objects):
        """Remove a parented locator from the child object.

        Parameters:
                obj (str/obj/list): The child object or the locator itself.
        """
        errorMsg = lambda: pm.inViewMessage(
            status_message="{} in remove_locator\n\t# Error: Unable to remove locator for the given object. #".format(
                __file__
            ),
            pos="topCenter",
            fade=True,
        )

        pm.undoInfo(openChunk=1)

        for obj in pm.ls(objects, long=True, objectsOnly=True):
            if not pm.objExists(obj):
                continue

            elif (
                node_utils.Node.is_locator(obj)
                and not node_utils.Node.get_type(obj)
                and not node_utils.Node.get_children(obj)
            ):
                pm.delete(obj)
                continue

            # unlock attributes
            cls.set_attr_lock_state(
                obj, translate=False, rotate=False, scale=False
            )  # unlock all.

            if not node_utils.Node.is_locator(obj):
                try:  # if the 'obj' is not a locator, check if it's parent is.
                    obj = node_utils.Node.get_parent(obj)
                    if not node_utils.Node.is_locator(obj):
                        errorMsg()
                        continue
                except IndexError as error:
                    errorMsg()
                    continue

            # unparent child object
            children = node_utils.Node.get_children(obj)
            for child in children:
                pm.parent(child, world=True)

            # remove locator
            pm.delete(obj)

        pm.undoInfo(closeChunk=1)

    @staticmethod
    def reset_pivot_transforms(objects):
        """Reset Pivot Transforms"""
        objs = pm.ls(type=("transform", "geometryShape"), sl=1)

        if len(objs) > 0:
            pm.xform(cp=1)

        pm.manipPivot(ro=1, rp=1)

    @staticmethod
    def bake_custom_pivot(objects, position=False, orientation=False):
        """ """
        transforms = pm.ls(objects, transforms=1)
        shapes = pm.ls(objects, shapes=1)
        objects = transforms + pm.listRelatives(
            shapes, path=1, parent=1, type="transform"
        )

        ctx = pm.currentCtx()
        pivotModeActive = 0
        customModeActive = 0
        if ctx in ("RotateSuperContext", "manipRotateContext"):  # Rotate tool
            customOri = pm.manipRotateContext("Rotate", q=1, orientAxes=1)
            pivotModeActive = pm.manipRotateContext("Rotate", q=1, editPivotMode=1)
            customModeActive = pm.manipRotateContext("Rotate", q=1, mode=1) == 3
        elif ctx in ("scaleSuperContext", "manipScaleContext"):  # Scale tool
            customOri = pm.manipScaleContext("Scale", q=1, orientAxes=1)
            pivotModeActive = pm.manipScaleContext("Scale", q=1, editPivotMode=1)
            customModeActive = pm.manipScaleContext("Scale", q=1, mode=1) == 6
        else:  # use the move tool orientation
            customOri = pm.manipMoveContext(
                "Move", q=1, orientAxes=1
            )  # get custom orientation
            pivotModeActive = pm.manipMoveContext("Move", q=1, editPivotMode=1)
            customModeActive = pm.manipMoveContext("Move", q=1, mode=1) == 6
            if not ctx in ("moveSuperContext", "manipMoveContext"):  # Move tool
                otherToolActive = 1  # some other tool

        if orientation and customModeActive:
            if not position:
                pm.mel.error(
                    (pm.mel.uiRes("m_bakeCustomToolPivot.kWrongAxisOriToolError"))
                )
                return

            from math import degrees

            cX, cY, cZ = customOri = [
                degrees(customOri[0]),
                degrees(customOri[1]),
                degrees(customOri[2]),
            ]

            pm.rotate(
                objects, cX, cY, cZ, a=1, pcp=1, pgp=1, ws=1, fo=1
            )  # Set object(s) rotation to the custom one (preserving child transform positions and geometry positions)

        if position:
            for obj in objects:
                # Get pivot in parent space
                m = pm.xform(obj, q=1, m=1)
                p = pm.xform(obj, q=1, os=1, sp=1)
                oldX, oldY, oldZ = old = [
                    (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12]),
                    (p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13]),
                    (p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14]),
                ]

                pm.xform(obj, zeroTransformPivots=1)  # Zero out pivots

                # Translate obj(s) back to previous pivot (preserving child transform positions and geometry positions)
                newX, newY, newZ = new = pm.getAttr(
                    obj.name() + ".translate"
                )  # obj.translate
                pm.move(
                    obj, oldX - newX, oldY - newY, oldZ - newZ, pcp=1, pgp=1, ls=1, r=1
                )

        if pivotModeActive:
            pm.ctxEditMode()  # exit pivot mode

        # Set the axis orientation mode back to obj
        if orientation and customModeActive:
            if ctx in ("RotateSuperContext", "manipRotateContext"):
                pm.manipPivot(rotateToolOri=0)
            elif ctx in ("scaleSuperContext", "manipScaleContext"):
                pm.manipPivot(scaleToolOri=0)
            else:  # Some other tool #Set move tool to obj mode and clear the custom ori. (so the tool won't restore it when activated)
                pm.manipPivot(moveToolOri=0)
                if not ctx in ("moveSuperContext", "manipMoveContext"):
                    pm.manipPivot(ro=1)

    @classmethod
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
        objects = pm.ls(objects, transforms=1, long=True)

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
                if node_utils.Node.is_locator(obj):
                    obj = pm.listRelatives(obj, children=1, type="transform")[0]
            except IndexError as error:
                return

            for attrs, state in attrs_and_state.items():
                if state is None:
                    continue
                for a in Iter.make_list(attrs):
                    pm.setAttr("{}.{}".format(obj, a), lock=state)

    @staticmethod
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

    @classmethod
    def create_group_with_first_obj_lra(cls, objects, name="", freeze_transforms=True):
        """Creates a group using the first object to define the local rotation axis.

        Parameters:
                objects (str/obj/list): The objects to group. The first object will be used to define the groups LRA.
                name (str): The group name.
                freeze_transforms (bool): Freeze transforms on group child objects.
        """
        try:
            obj, *other = pm.ls(objects, transforms=1)
        except IndexError as error:
            print(
                f"{__file__} in create_group_with_first_obj_lra\n\t# Error: Operation requires at least one object. #"
            )
            return None

        pm.undoInfo(openChunk=1)
        cls.bake_custom_pivot(
            obj, position=True, orientation=True
        )  # pm.mel.BakeCustomPivot(obj) #bake the pivot on the object that will define the LRA.

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
        except:  # root level objects
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
        pm.undoInfo(closeChunk=1)

        return grp

    @classmethod
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

        Example: createLocatorAtSelection(strip='_GEO', suffix='', strip_digits=True, parent=True, lock_translate=True, lock_rotation=True)
        """
        getSuffix = (
            lambda o: loc_suffix
            if node_utils.Node.is_locator(o)
            else grp_suffix
            if node_utils.Node.is_group(o)
            else obj_suffix
        )  # match the correct suffix to the object type.

        pm.undoInfo(openChunk=1)

        for obj in pm.ls(objects, long=True, type="transform"):
            if bake_child_pivot:
                cls.bake_custom_pivot(obj, position=1, orientation=1)

            vertices = pm.filterExpand(obj, sm=31)  # returns a string list.
            if vertices:
                objName = vertices[0].split(".")[0]
                obj = pm.ls(objName)

                loc = cls.create_locator(scale=scale)

                xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(vertices)
                x, y, z = pos = (
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
                        Str.format_suffix(
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
                    Str.format_suffix(
                        obj.name(),
                        suffix=getSuffix(loc),
                        strip=(obj_suffix, grp_suffix, loc_suffix),
                        strip_trailing_ints=strip_digits,
                        strip_trailing_alpha=strip_suffix,
                    ),
                )
                pm.rename(
                    obj,
                    Str.format_suffix(
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

        pm.undoInfo(closeChunk=1)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    sel = pm.ls(sl=1)

    Rigtk.create_locator_at_object(
        sel,
        parent=1,
        freeze_transforms=1,
        bake_child_pivot=1,
        grp_suffix="_GRP",
        loc_suffix="_LCTR",
        obj_suffix="_GEO",
        strip_digits=1,
        strip_suffix=1,
        scale=1,
        lock_translate=0,
        lock_rotation=0,
        lock_scale=0,
    )

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------


# the following was replaced with chatgpt code that fixed a: RuntimeError: Can't ungroup leaf-level transforms, but is not tested aside from a few unit test cases.
# def remove_locator(cls, objects):
#       '''Remove a parented locator from the child object.

#       Parameters:
#           obj (str/obj/list): The child object or the locator itself.
#       '''
#       errorMsg = lambda: pm.inViewMessage(
#           status_message="{} in remove_locator\n\t# Error: Unable to remove locator for the given object. #".format(__file__),
#           pos='topCenter',
#           fade=True
#       )

#       pm.undoInfo(openChunk=1)

#       for obj in pm.ls(objects, long=True, objectsOnly=True):
#           if not pm.objExists(obj):
#               continue

#           elif node_utils.Node.is_locator(obj) and not node_utils.Node.get_type(obj) and not node_utils.Node.get_children(obj):
#               pm.delete(obj)
#               continue

#           #unlock attributes
#           cls.set_attr_lock_state(obj, translate=False, rotate=False, scale=False) #unlock all.

#           if not node_utils.Node.is_locator(obj):
#               try: #if the 'obj' is not a locator, check if it's parent is.
#                   obj = node_utils.Node.get_type(obj)
#                   if not node_utils.Node.is_locator(obj):
#                       errorMsg()
#                       continue
#               except IndexError as error:
#                   errorMsg()
#                   continue

#           try: #remove from group
#               grp = node_utils.Node.get_type(obj)
#           except IndexError as error:
#               errorMsg()
#               continue
#           if node_utils.Node.is_group(grp):
#               if grp.split('_')[0]==obj.split('_')[0]:
#                   pm.ungroup(grp)

#           #remove locator
#           pm.ungroup(obj)
#           pm.delete(obj)

#       pm.undoInfo(closeChunk=1)
