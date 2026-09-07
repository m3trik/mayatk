# !/usr/bin/python
# coding=utf-8
import contextlib
from typing import List, Set, Tuple, Dict, Union, Optional, Iterable

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    # Bind the names (house policy) — the `om is None` / `cmds is None` guards
    # below read them, and an unbound name raises NameError instead.
    cmds = om = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.edit_utils.naming._naming import Naming
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.xform_utils._xform_utils import XformUtils


class RigUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    @CoreUtils.undoable
    def create_helper(
        name: str,
        helper_type: str = "locator",
        parent: Optional[str] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        cleanup: bool = False,
    ) -> Optional[str]:
        """Create a hidden helper object (e.g., locator, joint) with a consistent naming convention.
        Optionally cleans up (deletes) the helper if it already exists and cleanup is True.

        Parameters:
            name (str): Helper name (should include "__" as per convention).
            helper_type (str): Maya node type to create (e.g., "locator", "joint").
            parent (str or None): Optional parent transform.
            position (tuple): Position in world space.
            cleanup (bool): If True, deletes existing helper with same name and returns None.

        Returns:
            str or None: The created or existing helper transform name, or None if cleaned up.
        """
        if cmds.objExists(name):
            if cleanup:
                cmds.delete(name)
                return None
            return name

        if helper_type.lower() == "locator":
            helper = cmds.spaceLocator(n=name)[0]
        elif helper_type.lower() == "joint":
            helper = cmds.createNode("joint", n=name)
        else:
            helper = cmds.createNode(helper_type, n=name)

        if parent is not None:
            helper = cmds.parent(helper, parent)[0]
        else:
            current_parent = cmds.listRelatives(helper, parent=True, path=True)
            if current_parent:
                helper = cmds.parent(helper, world=True)[0]

        cmds.setAttr(
            f"{helper}.translate", position[0], position[1], position[2], type="double3"
        )
        cmds.setAttr(f"{helper}.visibility", 0)

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
        grp = cmds.group(empty=True, n=name)
        try:
            if objects:
                cmds.parent(objects, grp)
        except Exception as error:
            print(
                f"{__file__} in create_group\n\t# Error: Unable to parent object(s): {error} #"
            )

        if zero_translation:
            for attr in ("tx", "ty", "tz"):
                cmds.setAttr(f"{grp}.{attr}", 0)
        if zero_rotation:
            for attr in ("rx", "ry", "rz"):
                cmds.setAttr(f"{grp}.{attr}", 0)
        if zero_scale:
            for attr in ("sx", "sy", "sz"):
                cmds.setAttr(f"{grp}.{attr}", 0)

        current_parent = cmds.listRelatives(grp, parent=True, path=True)
        if current_parent:
            grp = cmds.parent(grp, world=True)[0]
        return grp

    @staticmethod
    @DisplayUtils.add_to_isolation
    def create_locator(
        *, scale: float = 1, parent: Optional[str] = None, **kwargs
    ) -> str:
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
            str: The created locator transform name.
        """
        pos = kwargs.pop("position", None)

        if pos is not None:
            if not isinstance(pos, (tuple, list)):
                transform_node = NodeUtils.get_transform_node([pos])
                if transform_node:
                    pos = cmds.xform(transform_node[0], q=True, ws=True, t=True)
                else:
                    pos = None

        loc = cmds.spaceLocator(**{k: v for k, v in kwargs.items() if v is not None})[0]

        if pos is not None:
            cmds.xform(loc, ws=True, t=pos)

        if scale != 1:
            cmds.scale(scale, scale, scale, loc)
        if parent:
            loc = cmds.parent(loc, parent)[0]

        return loc

    @classmethod
    @CoreUtils.undoable
    def create_locator_at_object(
        cls,
        objects: Union[str, List[str]],
        parent: bool = True,
        freeze_object: bool = True,
        freeze_locator: bool = True,
        loc_scale: float = 1.0,
        lock_translate: bool = False,
        lock_rotation: bool = False,
        lock_scale: bool = False,
        grp_suffix: Optional[str] = None,
        grp_affix_mode: str = "auto",
        loc_suffix: Optional[str] = None,
        loc_affix_mode: str = "auto",
        obj_suffix: Optional[str] = None,
        obj_affix_mode: str = "auto",
        strip_digits: bool = False,
        strip_trailing_underscores: bool = True,
        strip_suffix: bool = True,
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
            grp_suffix (str): Naming affix for the created group. ``None`` (default)
                takes the shared naming convention's ``group`` entry ("_GRP" as shipped).
            grp_affix_mode (str): Placement for an explicit *grp_suffix* --
                "auto" / "suffix" / "prefix", as *obj_affix_mode*. Ignored when
                *grp_suffix* is ``None`` (the convention entry carries its own).
            loc_suffix (str): Naming affix for the locator. ``None`` takes the
                convention's ``locator`` entry ("_LOC" as shipped).
            loc_affix_mode (str): Placement for an explicit *loc_suffix* --
                as *grp_affix_mode*.
            obj_suffix (str): Naming affix for the renamed object. ``None``
                (default) resolves it per object from the object's OWN type --
                a mesh takes "_GEO", a camera "_CAM" -- via
                :meth:`mayatk.Naming.affix_for`, and strips whichever
                convention affix the name already carries first. Pass a string
                to pin one spelling for every object, or "" for none.
            obj_affix_mode (str): Placement for an explicit *obj_suffix* --
                "auto" (infer from the delimiter: "_GEO" trails, "GEO_" leads),
                "suffix" or "prefix". Ignored when *obj_suffix* is ``None``:
                the convention entry carries its own placement. Mirrors
                :meth:`mayatk.Naming.suffix_by_type`'s flag of the same name,
                and is what the panel's affix picker reports.
            strip_digits (bool): Whether to strip trailing digits before suffixing.
            strip_trailing_underscores (bool): Whether to strip trailing underscores before adding new suffix.
            strip_suffix (bool): Whether to strip the defined suffixes (grp/loc/obj) from the name before adding new ones.
        """
        import re

        # Every affix is an AffixRule -- a spelling AND the side it lands on --
        # so a convention spelled as a prefix lands as one, and the panel's
        # picker states mean what they say. None => the shared convention
        # (pythontk.NamingConvention), so a studio that renames _GEO to _MSH
        # does not have to find this signature; a literal takes the caller's
        # placement, defaulting to "auto", which reads "_GRP" as the suffix it
        # looks like.
        grp_rule = (
            ptk.NamingConvention.get("group")
            if grp_suffix is None
            else ptk.AffixRule(grp_suffix, grp_affix_mode)
        )
        loc_rule = (
            ptk.NamingConvention.get("locator")
            if loc_suffix is None
            else ptk.AffixRule(loc_suffix, loc_affix_mode)
        )
        # The child keeps its own identity. The GRP and the LOC are nodes this
        # method CREATES, so their type is known here; the child is whatever the
        # user selected, and defaulting it to the mesh affix named a camera
        # "_GEO". None now defers per object to Naming.affix_for.
        obj_suffix_by_type = obj_suffix is None
        obj_rule = (
            None if obj_suffix_by_type else ptk.AffixRule(obj_suffix, obj_affix_mode)
        )
        grp_suffix, loc_suffix = grp_rule.text, loc_rule.text
        if obj_suffix_by_type:
            # A name may carry the affix of a type it is not (a camera an
            # earlier run wrote as "_GEO"), so the whole convention vocabulary
            # is strippable here -- not just the affixes in play. Longest first:
            # format_suffix strips per entry, and "_SG" would otherwise eat the
            # tail of "_LSG".
            strip_affixes = tuple(
                sorted(
                    {a for a in (grp_suffix, loc_suffix) if a}
                    | set(ptk.NamingConvention.all_affixes()),
                    key=lambda a: (-len(a), a),
                )
            )
        else:
            strip_affixes = (grp_suffix, loc_suffix, obj_rule.text)

        def strip_to_base(name: str) -> str:
            """The clean stem all three new names are built from."""
            base = ptk.format_suffix(
                name,
                suffix="",
                strip=strip_affixes if strip_suffix else (),
                strip_trailing_ints=strip_digits,
            )
            if strip_trailing_underscores:
                base = re.sub(r"_+$", "", base)
            if not base:
                cmds.warning(
                    f"[create_locator_at_object] Not stripping '{name}': "
                    f"the configured affixes consume the whole name, which "
                    f"would leave nothing to rename. Using it as-is."
                )
                base = name
            return base

        def rename_by_uuid(uuid: str, new_name: str) -> str:
            """Rename the node identified by ``uuid`` and return its new name.

            Renaming a node reshuffles the DAG paths of its descendants, so a
            descendant's stored *partial* path (e.g. obj's ``loc|leaf``) turns
            stale the instant an ancestor is renamed.  Capturing UUIDs before
            the rename pass and resolving each one just-in-time keeps the path
            current regardless of rename order or leaf-name collisions.
            """
            paths = cmds.ls(uuid, long=True) if uuid else []
            return cmds.rename(paths[0], new_name) if paths else uuid

        objects_str = (
            [str(o) for o in objects]
            if isinstance(objects, (list, tuple, set))
            else [str(objects)]
            if objects
            else []
        )
        for obj in (
            cmds.ls(objects_str, long=True, type="transform", flatten=True) or []
        ):
            orig_name = CoreUtils.leaf_name(obj)

            # Strip suffixes from the original name once
            base_name_stripped = strip_to_base(orig_name)

            mesh_shapes = NodeUtils.get_shapes(obj, no_intermediate=True)
            mesh_shape = mesh_shapes[0] if mesh_shapes else None
            vertex_count = (
                cmds.polyEvaluate(mesh_shape, vertex=True)
                if mesh_shape and cmds.objectType(mesh_shape) == "mesh"
                else 0
            )
            vertices = (
                f"{mesh_shape}.vtx[0:{vertex_count - 1}]"
                if mesh_shape and vertex_count
                else None
            )
            orig_parent = cmds.listRelatives(obj, parent=True, path=True)
            is_group = NodeUtils.is_group(obj)

            # Classify the child from the node itself, before the freeze /
            # reparent pass: the answer belongs to the object, not to where it
            # ends up. A pinned literal needs no classifying.
            child_rule = obj_rule
            if obj_suffix_by_type and not is_group:
                child_rule = Naming.affix_for(obj)
                if not child_rule.text:
                    # An unmapped node type has no entry to follow; keep the
                    # historical mesh affix rather than renaming it bare.
                    child_rule = ptk.NamingConvention.get("mesh")

            if not is_group:
                XformUtils.bake_pivot(obj, position=True, orientation=True)

            matrix = XformUtils.get_manip_pivot_matrix(obj, ws=True)

            # For groups, bake_pivot is skipped so the world matrix only
            # contains the group's own rotation — it misses any custom
            # manipulator-pivot orientation the user has set.  Query the
            # actual manip-pivot orientation so the locator axes match
            # what the user sees in the viewport, and use the bounding-box
            # centre for position so the rig is visually meaningful.
            if is_group:
                children = cmds.listRelatives(obj, children=True, type="transform")
                if children:
                    bb = cmds.exactWorldBoundingBox(
                        obj
                    )  # [xmin,ymin,zmin,xmax,ymax,zmax]
                    center = [
                        (bb[0] + bb[3]) / 2.0,
                        (bb[1] + bb[4]) / 2.0,
                        (bb[2] + bb[5]) / 2.0,
                    ]
                    # Start with the world-matrix rotation as default — `matrix` is an
                    # om.MMatrix from XformUtils.get_manip_pivot_matrix. MMatrix supports
                    # row/col indexing via API 2.0 MMatrix
                    # MMatrix where you index via getElement(r, c).
                    flat = [matrix.getElement(r, c) for r in range(4) for c in range(4)]
                    # Try to capture the actual manip pivot orientation;
                    # fall back to the world-matrix rotation when manipPivot
                    # reports identity (batch mode always returns 0,0,0).
                    prev_sel = cmds.ls(selection=True) or []
                    try:
                        cmds.select(obj, replace=True)
                        rot_deg = cmds.manipPivot(q=True, o=True)[0]
                        if isinstance(rot_deg[0], (list, tuple)):
                            rot_deg = rot_deg[0]
                        if any(abs(c) > 1e-6 for c in rot_deg):
                            from math import radians

                            euler = om.MEulerRotation(
                                radians(rot_deg[0]),
                                radians(rot_deg[1]),
                                radians(rot_deg[2]),
                            )
                            rot_mat = euler.asMatrix()
                            flat = [
                                rot_mat.getElement(r, c)
                                for r in range(4)
                                for c in range(4)
                            ]
                    except Exception:
                        pass  # Keep flat from world matrix
                    finally:
                        if prev_sel:
                            cmds.select(prev_sel, replace=True)
                        else:
                            cmds.select(clear=True)
                    # Override translation with bounding-box centre
                    flat[12] = center[0]
                    flat[13] = center[1]
                    flat[14] = center[2]
                    matrix = om.MMatrix(flat)

            loc = cls.create_locator(scale=loc_scale)
            cmds.xform(loc, matrix=list(matrix), ws=True)

            grp = None
            if parent:
                grp = cmds.group(em=True)
                cmds.delete(cmds.parentConstraint(loc, grp))
                loc = cmds.parent(loc, grp)[0]
                obj = cmds.parent(obj, loc)[0]

                if freeze_locator:
                    # store=False: rig construction. The LOC is already at
                    # identity here (the GRP was parent-constrained to it
                    # before the reparent), so this is a guard, not a move.
                    XformUtils.freeze_transforms(loc, normal=True, store=False)

                if orig_parent:
                    grp = cmds.parent(grp, orig_parent)[0]

            if vertices and mesh_shape and vertex_count:
                # Re-derive vertices from the shape's CURRENT path — earlier
                # parent ops may have invalidated the long path captured at
                # line 240.
                current_shapes = NodeUtils.get_shapes(obj, no_intermediate=True)
                if current_shapes:
                    vertices = f"{current_shapes[0]}.vtx[0:{vertex_count - 1}]"
                    try:
                        cmds.polyNormalPerVertex(vertices, unFreezeNormal=True)
                    except Exception:
                        pass

            # Freeze object after hierarchy is set up (but not groups)
            if freeze_object and not is_group:
                # store=False: rig construction, as above.
                XformUtils.freeze_transforms(obj, normal=True, store=False)

            # Rename group, locator, and object using the clean base name.
            # Capture UUIDs first — while every path is still valid — then
            # rename via rename_by_uuid so an ancestor rename can't invalidate
            # a descendant's stored partial path (see rename_by_uuid above).
            grp_uuid = (cmds.ls(grp, uuid=True) or [None])[0] if grp else None
            loc_uuid = (cmds.ls(loc, uuid=True) or [None])[0]
            obj_uuid = (cmds.ls(obj, uuid=True) or [None])[0]

            if parent and grp_uuid:
                grp = rename_by_uuid(grp_uuid, grp_rule.apply(base_name_stripped))
            loc = rename_by_uuid(loc_uuid, loc_rule.apply(base_name_stripped))
            # Only affix the child if it is not a group (the GRP names that).
            # .apply is a no-op for an empty affix, which is what "" asks for.
            if not is_group:
                obj = rename_by_uuid(obj_uuid, child_rule.apply(base_name_stripped))

            if parent and grp:
                # store=False: the GRP is created identity here, so its
                # "pre-freeze" state is not something a user would unfreeze to.
                XformUtils.freeze_transforms(grp, scale=True, store=False)

            Attributes.set_lock_state(
                obj,
                translate=lock_translate,
                rotate=lock_rotation,
                scale=lock_scale,
            )
            cmds.select(loc, replace=True)

    @classmethod
    @CoreUtils.undoable
    def remove_locator(cls, objects):
        """Remove a parented locator from the child object.

        Parameters:
            obj (str/obj/list): The child object or the locator itself.
        """
        for obj in (
            cmds.ls(CoreUtils.as_strings(objects), long=True, objectsOnly=True) or []
        ):
            if not cmds.objExists(obj):
                continue

            if NodeUtils.is_locator(obj):
                # Unlock attributes
                Attributes.set_lock_state(
                    obj, translate=False, rotate=False, scale=False
                )

                # Full paths throughout -- Maya hands back the shortest
                # UNIQUE name, so a partial path expires the moment the
                # hierarchy around it moves.
                parent = NodeUtils.get_parent(obj, full_path=True)
                grandparent = (
                    NodeUtils.get_parent(parent, full_path=True) if parent else None
                )

                # Children move out from under the locator BEFORE it is
                # deleted -- `cmds.delete` takes the whole subtree with it.
                # One hop, straight to the destination: the old world
                # round-trip invalidated the captured child paths, because a
                # child whose name is not unique in the scene comes back as
                # `LOC|CHILD` and that no longer resolves once it has been
                # unparented. `cmds.parent` is absolute by default, so the
                # world transform survives either way.
                children = NodeUtils.get_children(obj, full_path=True)
                new_parent = grandparent or parent
                if children:
                    if new_parent:
                        cmds.parent(*children, new_parent)
                    else:
                        cmds.parent(*children, world=True)

                cmds.delete(obj)

                # Check if the parent is a group and delete it if it has no other children
                if parent and NodeUtils.is_group(parent):
                    parent_children = NodeUtils.get_children(parent)
                    if not parent_children:
                        cmds.delete(parent)

            else:
                cmds.warning(f"Object '{obj}' is not a locator.")

        return objects

    @staticmethod
    def _translate_is_driven(node: str) -> bool:
        """True when *node*'s ``translate`` has an incoming connection.

        Both the compound and its three children are queried: a compound
        ``listConnections`` does NOT see a child-plug connection, and vice
        versa. Measured — ``parentConstraint``, ``pointConstraint`` and
        ``setKeyframe`` all drive the CHILDREN; only a hand-wired
        ``connectAttr`` normally takes the compound. Querying one alone
        misses real drivers.
        """
        plugs = [f"{node}.translate"] + [f"{node}.translate{a}" for a in "XYZ"]
        return bool(cmds.listConnections(plugs, source=True, destination=False))

    #: Plugs :meth:`_offset_translate` writes.
    _TRANSLATE_ATTRS = ("translate", "translateX", "translateY", "translateZ")

    @staticmethod
    def _unlock_if_needed(node: str, attrs: Tuple[str, ...]):
        """Unlock *attrs* on *node* for the duration of a write — but only when
        some of them are actually locked.

        ``Attributes.temporarily_unlock`` probes every name with
        ``attributeQuery`` before it can act, which costs 2.01 ms for the 16
        pivot plugs; one ``listAttr -locked`` answers the same question in
        0.048 ms and reports compound AND child locks (both measured). On the
        overwhelmingly common unlocked node this skips the unlock/relock round
        trip entirely, which matters because the pivot walk-back runs on every
        node of every rig in the selection.
        """
        locked = cmds.listAttr(node, locked=True)
        if locked and not set(locked).isdisjoint(attrs):
            return Attributes.temporarily_unlock([node], attrs)
        return contextlib.nullcontext()

    @staticmethod
    def _mesh_shapes_with_points(node: str) -> List[str]:
        """*node*'s visible mesh shapes that actually carry vertices.

        ``noIntermediate=True`` drops the Orig shape on a deformed mesh —
        shifting both Orig and Deformed corrupts the deformation graph. The
        vertex filter drops an EMPTY mesh: ``cmds.move`` on the ``.vtx[*]`` of
        a shape with no points raises ``ValueError: No object matches name``,
        which escaped the whole call — after earlier geos had already been
        shifted, so the scene was left corrupted by the abort.
        """
        shapes = (
            cmds.listRelatives(
                node, shapes=True, type="mesh", noIntermediate=True, fullPath=True
            )
            or []
        )
        out = []
        for shape in shapes:
            try:
                if cmds.polyEvaluate(shape, vertex=True):
                    out.append(shape)
            except Exception:  # unreadable shape — nothing to bake into
                continue
        return out

    @classmethod
    def _offset_translate(cls, node: str, offset: "om.MVector") -> None:
        """Add *offset* — already expressed in *node*'s PARENT space — to its
        ``translate``. Callers must have cleared :meth:`_translate_is_driven`.
        """
        with cls._unlock_if_needed(node, cls._TRANSLATE_ATTRS):
            current = cmds.getAttr(f"{node}.translate")[0]
            cmds.setAttr(
                f"{node}.translate",
                current[0] + offset.x,
                current[1] + offset.y,
                current[2] + offset.z,
                type="double3",
            )

    #: Attributes ``xform -preserve`` writes when a pivot is moved: the pivot
    #: itself plus the translate term that holds the node still. Unlocked
    #: explicitly around the write because ``Attributes.get_lock_state`` only
    #: covers translate/rotate/scale.
    _PIVOT_WRITE_ATTRS = tuple(
        f"{base}{axis}"
        for base in (
            "rotatePivot",
            "rotatePivotTranslate",
            "scalePivot",
            "scalePivotTranslate",
        )
        for axis in ("", "X", "Y", "Z")
    )

    @classmethod
    @CoreUtils.undoable
    def restore_rig_anchors(
        cls,
        objects,
        traverse: bool = True,
        skip_animated: bool = True,
        pivot_source: str = "bbox",
    ) -> List[str]:
        """Restore the world-space anchor on a GRP > LOC > GEO rig after a freeze.

        After ``XformUtils.freeze_transforms`` collapses a static locator rig, the
        GRP ends up at local identity and the world position lives in vertex
        coordinates. A zeroed GRP holding a locator that will later be animated
        is poor rig structure — the GRP is supposed to hold the world anchor so
        the locator can animate locally. This function reads the geo's world
        pivot, sets ``GRP.translate`` to it, and shifts the mesh vertices by the
        inverse so the visual position is preserved. End state matches what
        ``create_locator_at_object`` originally produced.

        Animated rigs (LOC with incoming connections on translate/rotate) are
        skipped by default — ``freeze_transforms`` doesn't disturb them, so they
        don't need restoring.

        Compensation model:
            Everything under the GRP travels with it, and every node is held
            still in exactly one way:

            * the LOC is *meant* to travel — it becomes the anchor;
            * a mesh geo cancels the move inside its own **vertex positions**;
            * an INSTANCED geo cannot (its points are shared, so the shift
              would land once per instance transform and leak into instances
              outside the rig), so it is counter-translated on its own
              ``translate`` — instancing and the shared shape stay untouched;
            * everything else under the chain is counter-translated too: a
              second LOC beside the first, a curve or an empty mesh beside the
              geo, a whole rig nested under one. A geo's vertex shift moves its
              POINTS only — the transform itself still travels — so its
              children have to be held as well.

        Limitations:
            * Only **translation** is restored. If the GRP originally held a
              rotation that got baked through the freeze cascade, that
              orientation cannot be recovered from geometry alone.
            * Only a **mesh** carrying vertices can anchor a rig. A rig whose
              geo is a NURBS surface, NURBS curve, subdiv or empty mesh yields
              no candidate and is skipped silently; such a node beside a real
              mesh geo is held on its channel per the model above.
            * A **driven** ``translate`` can receive none of that. A rig whose
              GRP translate is connected is refused outright, before anything
              is mutated — writing it is the operation's last step, and failing
              there would leave the geo vertices already shifted. An instanced
              geo or a passenger whose translate is driven is skipped. Every
              such case warns rather than raises.
            * Vertex positions are modified. If a geo has downstream deformers
              (skinClusters, blendShapes) that depend on the current vertex
              layout, those may need re-binding afterwards.

        Parameters:
            objects (str/obj/list): GRP nodes to restore, or root containers when
                ``traverse=True``. With ``traverse=True`` the subtree under each
                input is scanned for locator-rig chains.
            traverse (bool): When True (default), walk each input's subtree and
                find every GRP > LOC > GEO chain to restore.
            skip_animated (bool): When True (default), skip rigs whose LOC has
                incoming connections on any translate or rotate channel. Only
                the LOC is inspected; a rig whose GRP translate is driven is
                refused unconditionally (see Limitations), since the anchor
                cannot be written to it under any setting.
            pivot_source (str): How to determine the world anchor point.
                * ``"bbox"`` (default) — geo's world bounding-box center
                * ``"rp"`` — geo's world rotate pivot

        Returns:
            List of short names of GRPs whose translate was updated.
        """
        if om is None or cmds is None:
            return []

        valid_sources = {"bbox", "rp"}
        if pivot_source not in valid_sources:
            raise ValueError(
                f"Invalid pivot_source {pivot_source!r}; "
                f"expected one of {sorted(valid_sources)}"
            )

        nodes = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        if not nodes:
            return []

        candidates: List[Tuple[str, str, List[str]]] = []  # (grp, loc, geos)
        seen_grps: Set[str] = set()

        def add_candidate(grp: str) -> None:
            if grp in seen_grps:
                return
            children = (
                cmds.listRelatives(grp, children=True, type="transform", fullPath=True)
                or []
            )
            loc = None
            for c in children:
                if cmds.listRelatives(c, shapes=True, type="locator", fullPath=True):
                    loc = c
                    break
            if not loc:
                return
            geo_xforms = (
                cmds.listRelatives(loc, children=True, type="transform", fullPath=True)
                or []
            )
            # A geo has to carry POINTS to absorb the delta. A zero-vertex
            # mesh is still a ``type='mesh'`` child, so it used to qualify and
            # then crash the vertex move; it now falls through to the rider
            # compensation below like any other passenger.
            geos = [g for g in geo_xforms if cls._mesh_shapes_with_points(g)]
            if not geos:
                return
            seen_grps.add(grp)
            candidates.append((grp, loc, geos))

        for node in nodes:
            if traverse:
                loc_shapes = (
                    cmds.listRelatives(
                        node, allDescendents=True, type="locator", fullPath=True
                    )
                    or []
                )
                for shape in loc_shapes:
                    loc_x = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    if not loc_x:
                        continue
                    grp_list = (
                        cmds.listRelatives(loc_x[0], parent=True, fullPath=True) or []
                    )
                    if not grp_list:
                        continue
                    add_candidate(grp_list[0])
            else:
                add_candidate(node)

        restored: List[str] = []
        anim_attrs = (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        )

        for grp, loc, geos in candidates:
            if skip_animated:
                animated = False
                for attr in anim_attrs:
                    conns = (
                        cmds.listConnections(
                            f"{loc}.{attr}", source=True, destination=False
                        )
                        or []
                    )
                    if conns:
                        animated = True
                        break
                if animated:
                    continue

            # The restore ENDS by writing ``GRP.translate``. If that plug is
            # driven the write raises — and by then the geo vertices have
            # already been shifted, so the rig is left half-restored (measured:
            # both geos displaced 8.0 units with the GRP never moved, and the
            # exception took the remaining rigs down with it). ``skip_animated``
            # only inspects the LOC, so it does not cover this. Refuse the rig
            # here, while nothing has been touched.
            if cls._translate_is_driven(grp):
                cmds.warning(
                    "RigUtils.restore_rig_anchors: skipping "
                    f"'{CoreUtils.leaf_name(grp)}' — its translate is driven, so "
                    "the world anchor cannot be written to it."
                )
                continue

            if pivot_source == "bbox":
                bb_input = geos if len(geos) > 1 else geos[0]
                bb = cmds.exactWorldBoundingBox(bb_input)
                anchor = om.MVector(
                    (bb[0] + bb[3]) / 2.0,
                    (bb[1] + bb[4]) / 2.0,
                    (bb[2] + bb[5]) / 2.0,
                )
            else:  # "rp"
                rp = cmds.xform(geos[0], q=True, ws=True, rp=True)
                anchor = om.MVector(*rp)

            # Use worldMatrix translation (= world position of the local
            # origin), not ``xform -q -ws -t`` which returns the rotate
            # pivot's world position — they differ for nodes with non-zero
            # rotatePivot. The LOC child sees the world-matrix translation.
            grp_world_mat = cmds.xform(grp, q=True, ws=True, matrix=True)
            grp_world = (grp_world_mat[12], grp_world_mat[13], grp_world_mat[14])
            delta_world = anchor - om.MVector(*grp_world)
            if delta_world.length() < 1e-5:
                continue

            grp_parent_list = cmds.listRelatives(grp, parent=True, fullPath=True) or []
            if grp_parent_list:
                parent_mat = om.MMatrix(
                    cmds.xform(grp_parent_list[0], q=True, ws=True, matrix=True)
                )
                delta_in_parent = delta_world * parent_mat.inverse()
            else:
                delta_in_parent = delta_world

            # ``makeIdentity`` bakes the parent's translation into the
            # rotate/scale pivot of EVERY node in the chain (GRP, LOC, and
            # every GEO), not just the leaf. Each one has to walk back by the
            # delta the GRP is about to gain, or the post-restore ws_rp ends
            # up doubled and rotations happen at the wrong place.
            def _shift_pivots(node: str) -> None:
                """Walk *node*'s pivots back by ``delta_world`` — without
                moving *node*.

                Written through ``xform -preserve``, never a bare ``setAttr``.
                Maya's local matrix is

                    ``M = SP⁻¹·S·SH·SP·ST·RP⁻¹·RA·R·RP·RT·T``

                so the pivot terms cancel only while ``R`` is identity and
                ``S`` is 1 — the shape every fixture in the test suite happens
                to have. On anything else a bare pivot write drags the node
                with it by ``rp·(I - R)`` (and the scale analogue), and a
                rotated GRP drags its whole subtree, displacing even identity
                leaves it never touched. ``-preserve`` folds that term into
                ``rotatePivotTranslate`` / ``scalePivotTranslate``, which is
                the same bookkeeping ``makeIdentity`` itself does.
                """
                ws_rp = om.MVector(*cmds.xform(node, q=True, ws=True, rp=True))
                ws_sp = om.MVector(*cmds.xform(node, q=True, ws=True, sp=True))
                target_rp = ws_rp - delta_world
                target_sp = ws_sp - delta_world
                with cls._unlock_if_needed(node, cls._PIVOT_WRITE_ATTRS):
                    cmds.xform(
                        node,
                        worldSpace=True,
                        preserve=True,
                        rotatePivot=(target_rp.x, target_rp.y, target_rp.z),
                    )
                    cmds.xform(
                        node,
                        worldSpace=True,
                        preserve=True,
                        scalePivot=(target_sp.x, target_sp.y, target_sp.z),
                    )

            _shift_pivots(grp)
            _shift_pivots(loc)

            # Every geo is a direct child of the LOC, so one conversion of the
            # delta into LOC space serves all of the instanced ones.
            loc_world = om.MMatrix(cmds.xform(loc, q=True, ws=True, matrix=True))
            delta_in_loc = delta_world * loc_world.inverse()

            # RIDERS: everything under the GRP travels with it. The LOC is
            # SUPPOSED to (it becomes the anchor), and a vertex-compensated geo
            # cancels the move inside its own points — but only its POINTS; the
            # geo transform still travels, so anything parented beneath it is
            # carried along, as is any GRP/LOC child outside the chain. Left
            # alone they are displaced by the full delta: a second LOC beside
            # the first, a whole rig nested under a geo, a curve beside the
            # mesh. Each is held on its own channel instead. Only the TOPMOST
            # node of each branch is listed — compensating it holds its whole
            # subtree, and adding its descendants too would double the shift.
            # Each rider carries the delta ALREADY EXPRESSED in its own
            # parent's space, captured as it is collected. Two of the three
            # conversions are values this method already holds, and pinning
            # them at collection time means the write no longer depends on a
            # parent's world matrix having survived the geo loop unchanged.
            grp_delta = delta_world * om.MMatrix(grp_world_mat).inverse()
            riders: List[Tuple[str, "om.MVector"]] = [
                (c, grp_delta)
                for c in cmds.listRelatives(
                    grp, children=True, type="transform", fullPath=True
                )
                or []
                if c != loc
            ]
            riders += [
                (c, delta_in_loc)
                for c in cmds.listRelatives(
                    loc, children=True, type="transform", fullPath=True
                )
                or []
                if c not in geos
            ]

            for geo in geos:
                # An INSTANCED geo cannot be compensated in vertex space at
                # all: the points are shared, so the shift lands once per
                # instance transform in the rig (a set of four receptacles
                # sharing one shape got shifted four times) and leaks into
                # instances outside the rig entirely. Counter-translate that
                # geo's own channel instead — exact, per-instance, and it
                # never writes to shared geometry. Same line freeze_transforms
                # draws when it refuses to bake into a multiply-instanced
                # shape.
                if NodeUtils.get_instanced_shapes(geo):
                    if cls._translate_is_driven(geo):
                        # Nothing to write and nothing to bake: its driver owns
                        # the channel. A constraint re-solves it once the GRP
                        # moves, so leaving it alone is also the correct answer
                        # there; a plain curve holds a fixed local value and the
                        # geo does ride along, which the warning names.
                        cmds.warning(
                            "RigUtils.restore_rig_anchors: "
                            f"'{CoreUtils.leaf_name(geo)}' is instanced AND its "
                            "translate is driven — it cannot be compensated "
                            "either way, so it is left as-is."
                        )
                        continue
                    cls._offset_translate(geo, -delta_in_loc)
                    # No pivot walk-back here: the counter-translate holds
                    # this geo's world matrix still, so its world pivots never
                    # move and there is nothing to walk back.
                    continue

                geo_world = om.MMatrix(cmds.xform(geo, q=True, ws=True, matrix=True))
                delta_in_geo = delta_world * geo_world.inverse()
                # Only THIS branch adds the geo's children: a vertex-
                # compensated geo moves and its children ride along, while the
                # instanced branch above holds its geo's transform still, so
                # those children never move in the first place.
                riders += [
                    (c, delta_in_geo)
                    for c in cmds.listRelatives(
                        geo, children=True, type="transform", fullPath=True
                    )
                    or []
                ]
                for mesh in cls._mesh_shapes_with_points(geo):
                    cmds.move(
                        -delta_in_geo.x,
                        -delta_in_geo.y,
                        -delta_in_geo.z,
                        f"{mesh}.vtx[*]",
                        relative=True,
                        objectSpace=True,
                    )
                _shift_pivots(geo)

            for rider, rider_delta in riders:
                if cls._translate_is_driven(rider):
                    cmds.warning(
                        "RigUtils.restore_rig_anchors: "
                        f"'{CoreUtils.leaf_name(rider)}' sits under the rig but "
                        "its translate is driven — it cannot be held in place "
                        "and will follow the GRP."
                    )
                    continue
                cls._offset_translate(rider, -rider_delta)

            cls._offset_translate(grp, delta_in_parent)

            restored.append(CoreUtils.leaf_name(grp))

        if restored:
            print(f"RigUtils.restore_rig_anchors: restored {len(restored)} rig(s).")

        return restored

    @classmethod
    @CoreUtils.undoable
    def connect_switch_to_constraint(
        cls,
        constraint_node: str,
        constraint_targets: Optional[List[str]] = None,
        attr_name: str = "parent_switch",
        overwrite_existing: bool = False,
        node: Optional[str] = None,
        weighted: bool = False,
        anchor: Optional[str] = None,
    ) -> dict:
        """
        Create a space switch attribute to drive a constraint node.
        - 1 target, no anchor: bool (on/off toggle)
        - 2 targets: enum or float (blend if weighted)
        - 3+ targets: enum (dropdown snap)

        Parameters:
            constraint_node (str): The constraint node to control.
            constraint_targets (Optional[List[str]]): List of target transforms for the constraint. If None, auto-detected.
            attr_name (str): Name of the switch attribute to create.
            overwrite_existing (bool): If True, deletes and recreates the attribute if it exists.
            node (Optional[str]): Node to add the switch attribute to. If None, derived from the driven object.
            weighted (bool): If True, creates a float attribute for smooth blending (2 targets only).
            anchor (Optional[str]): If given, creates a locator at origin as a neutral/anchor/world target with this name.

        Returns:
            dict: Dictionary of created nodes and attributes for further processing.
        """
        constraint_node = str(constraint_node) if constraint_node else None
        if not constraint_node or not cmds.objExists(constraint_node):
            raise TypeError("constraint_node must be a valid constraint node name.")
        if not cmds.objectType(constraint_node, isAType="constraint"):
            raise TypeError(f"'{constraint_node}' is not a constraint node.")

        # The constraint command (``cmds.parentConstraint`` etc.) is reused for
        # target autodetect, wiring the anchor, and reading weight aliases.
        constraint_type = cmds.objectType(constraint_node)
        constraint_cmd = getattr(cmds, constraint_type, None)

        result = {}
        # Target autodetect if not provided.  ``cmds.<constraint>(node,
        # q=True, targetList=True)`` is the canonical way to read targets
        # off a constraint regardless of internal plug layout.
        if constraint_targets is None:
            target_list: list = []
            if constraint_cmd:
                try:
                    target_list = (
                        constraint_cmd(constraint_node, q=True, targetList=True) or []
                    )
                except Exception:
                    target_list = []
            constraint_targets = [
                t
                for t in dict.fromkeys(target_list)
                if cmds.objExists(t) and cmds.objectType(t, isAType="transform")
            ]

        # Check targets
        if not constraint_targets or len(constraint_targets) < 1:
            cmds.warning("No constraint targets found or provided.")
            return result

        # Resolve the driven object — where the switch attr lives, and what an
        # anchor is constrained to. Done before creating any anchor so a
        # failure here can't orphan a helper locator.
        driven = cmds.listConnections(
            f"{constraint_node}.constraintParentInverseMatrix",
            source=True,
            destination=False,
        ) or cmds.listRelatives(
            constraint_node, type="transform", parent=True, fullPath=True
        )
        driven_obj = driven[0] if driven else None
        if node is None:
            node = driven_obj
        if node is None:
            cmds.warning("Could not determine node to add switch attribute to.")
            return result

        # Check for duplicate attribute, handle overwrite (before any anchor).
        if Attributes.has_attr(node, attr_name):
            if overwrite_existing:
                cmds.deleteAttr(f"{node}.{attr_name}")
            else:
                cmds.warning(f"{node}.{attr_name} already exists.")
                return result

        def query_weight_aliases() -> List[str]:
            if not constraint_cmd:
                return []
            try:
                aliases = (
                    constraint_cmd(constraint_node, q=True, weightAliasList=True) or []
                )
                return [f"{constraint_node}.{a}" for a in aliases]
            except Exception:
                return []

        # Sanity-check weight aliases against the (pre-anchor) targets first —
        # a mismatch here bails cleanly, before any anchor helper is created.
        weight_alias_list = query_weight_aliases()
        if len(weight_alias_list) < len(constraint_targets):
            cmds.warning(
                "Number of constraint weights does not match number of targets."
            )
            return result

        # Optionally add a neutral/world anchor as the last target. The helper
        # is wired into the constraint as a REAL target — re-invoking the
        # constraint command on an already-constrained object appends a new
        # target + weight; without this the anchor would do nothing.
        # maintainOffset=True holds the object's current world pose when the
        # anchor space is active. On failure the helper is deleted (nothing is
        # orphaned); on success the weight aliases are re-read so the new one is
        # included when wiring the switch. Because the count is validated above
        # and Maya always appends exactly one weight, the anchor can only be
        # created once the counts are guaranteed to line up — no dangling target.
        if anchor:
            if not (constraint_cmd and driven_obj):
                cmds.warning(
                    f"Cannot add anchor '{anchor}': constraint type "
                    f"'{constraint_type}' or driven object unresolved."
                )
                return result
            anchor_obj = cls.create_helper(
                name=anchor,
                helper_type="locator",
                position=(0, 0, 0),
            )
            try:
                constraint_cmd(anchor_obj, driven_obj, maintainOffset=True)
            except Exception as e:
                cmds.delete(anchor_obj)
                cmds.warning(f"Could not add anchor '{anchor}' to the constraint: {e}")
                return result
            constraint_targets = list(constraint_targets) + [anchor_obj]
            result["anchor_helper"] = anchor_obj
            weight_alias_list = query_weight_aliases()

        num_targets = len(constraint_targets)

        # Disconnect all inputs from weights
        for weight_attr in weight_alias_list:
            cmds.cutKey(weight_attr, clear=True)
            for conn in (
                cmds.listConnections(weight_attr, plugs=True, s=True, d=False) or []
            ):
                cmds.disconnectAttr(conn, weight_attr)

        switch_attr = f"{node}.{attr_name}"

        # --- Single target, no anchor: simple bool toggle for constraint on/off ---
        if num_targets == 1:
            cmds.addAttr(node, longName=attr_name, at="bool", k=True)
            cmds.setAttr(switch_attr, 0)
            result["switch_attr"] = switch_attr

            weight_attr = weight_alias_list[0]
            cond_name = f"{CoreUtils.leaf_name(constraint_node)}_{attr_name}_cond0"
            cond_node = cmds.createNode("condition", name=cond_name)
            cmds.setAttr(f"{cond_node}.operation", 0)  # == compare
            cmds.setAttr(f"{cond_node}.firstTerm", 1)
            cmds.connectAttr(switch_attr, f"{cond_node}.secondTerm", f=True)
            cmds.setAttr(f"{cond_node}.colorIfTrueR", 1.0)
            cmds.setAttr(f"{cond_node}.colorIfFalseR", 0.0)
            cmds.connectAttr(f"{cond_node}.outColorR", weight_attr, f=True)
            result["condition_node"] = cond_node
            return result

        # --- Weighted float blend for 2 targets only ---
        if weighted and num_targets == 2:
            cmds.addAttr(
                node, longName=attr_name, at="double", min=0.0, max=1.0, k=True
            )
            result["switch_attr"] = switch_attr
            cmds.setAttr(switch_attr, 0)
            cmds.connectAttr(switch_attr, weight_alias_list[0], f=True)
            rev_name = f"{CoreUtils.leaf_name(node)}_{attr_name}_reverse"
            if cmds.objExists(rev_name):
                rev_node = rev_name
            else:
                rev_node = cmds.createNode("reverse", name=rev_name)
            cmds.connectAttr(switch_attr, f"{rev_node}.inputX", f=True)
            cmds.connectAttr(f"{rev_node}.outputX", weight_alias_list[1], f=True)
            result["reverse_node"] = rev_node
            return result

        # --- Enum dropdown for snap switching (2 or more targets) ---
        enum_names = [CoreUtils.leaf_name(t) for t in constraint_targets]
        enum_string = ":".join(enum_names)
        cmds.addAttr(node, longName=attr_name, at="enum", en=enum_string, k=True)
        cmds.setAttr(switch_attr, 0)
        result["switch_attr"] = switch_attr

        # For each weight, create a condition node that checks if switch matches index
        for i, weight_attr in enumerate(weight_alias_list[:num_targets]):
            cond_name = f"{CoreUtils.leaf_name(constraint_node)}_{attr_name}_cond{i}"
            cond_node = cmds.createNode("condition", name=cond_name)
            cmds.setAttr(f"{cond_node}.operation", 0)  # == compare
            cmds.setAttr(f"{cond_node}.firstTerm", i)
            cmds.connectAttr(switch_attr, f"{cond_node}.secondTerm", f=True)
            cmds.setAttr(f"{cond_node}.colorIfTrueR", 1.0)
            cmds.setAttr(f"{cond_node}.colorIfFalseR", 0.0)
            cmds.connectAttr(f"{cond_node}.outColorR", weight_attr, f=True)
            result[f"condition_node_{i}"] = cond_node

        return result

    @staticmethod
    @CoreUtils.undoable
    def create_ik_handle(
        start_joint: str,
        end_joint: str,
        solver: str = "ikRPsolver",
        name: str = "ikHandle",
        parent: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Create an IK handle.

        Parameters:
            start_joint (str): Start joint of the IK chain.
            end_joint (str): End joint of the IK chain.
            solver (str): IK solver type (e.g., "ikRPsolver", "ikSCsolver", "ikSplineSolver").
            name (str): Name of the IK handle.
            parent (str): Optional parent for the IK handle.
            **kwargs: Additional arguments passed to cmds.ikHandle.

        Returns:
            str: The created IK handle name.
        """
        result = cmds.ikHandle(
            startJoint=start_joint,
            endEffector=end_joint,
            solver=solver,
            name=name,
            **kwargs,
        )
        ik_handle = result[0]

        if parent:
            ik_handle = cmds.parent(ik_handle, parent)[0]

        return ik_handle

    @staticmethod
    @CoreUtils.undoable
    def create_pole_vector(
        ik_handle: str,
        mid_joint: str,
        distance: float = 5.0,
        name: str = "poleVector_LOC",
        parent: Optional[str] = None,
    ) -> str:
        """Create a pole vector locator based on the mid joint's position.

        Parameters:
            ik_handle (str): The IK handle to constrain.
            mid_joint (str): The joint to calculate the PV position from.
            distance (float): Offset distance along the pole vector vector.
            name (str): Name of the PV locator.
            parent (str): Optional parent for the PV locator.

        Returns:
            str: The pole vector locator name.
        """
        # Calculate PV position using simple vector math (assuming planar chain)
        # Note: Ideally uses true plane calculation, but this is a reasonable approximation for simple chains.
        # A more robust PV finder would get the projection of mid_joint onto the start-end vector.
        start_joint = cmds.ikHandle(ik_handle, q=True, startJoint=True)
        end_effector = cmds.ikHandle(ik_handle, q=True, endEffector=True)
        # endEffector is the effector node — its parent is the end joint
        end_parents = cmds.listRelatives(end_effector, parent=True, path=True) or []
        end_joint = end_parents[0] if end_parents else end_effector

        start_pos = om.MVector(*cmds.xform(start_joint, q=True, ws=True, t=True))
        mid_pos = om.MVector(*cmds.xform(mid_joint, q=True, ws=True, t=True))
        end_pos = om.MVector(*cmds.xform(end_joint, q=True, ws=True, t=True))

        # Vector from start to end
        v_start_end = end_pos - start_pos
        # Vector from start to mid
        v_start_mid = mid_pos - start_pos

        # Project mid onto start-end vector
        denom = v_start_end * v_start_end
        if denom < 1e-9:
            t = 0.0
        else:
            t = (v_start_mid * v_start_end) / denom
        projected_pos = start_pos + v_start_end * t

        # Vector from projection to mid (orthogonal to chain axis)
        v_pv = mid_pos - projected_pos
        if v_pv.length() < 0.001:
            # Straight chain fallback: use local Z or Y
            # Try Y
            v_pv = om.MVector(0, 1, 0)

        v_pv.normalize()

        pv_pos = mid_pos + v_pv * distance

        pole_vector = RigUtils.create_locator(
            name=name, position=(pv_pos.x, pv_pos.y, pv_pos.z), scale=1.0, parent=parent
        )

        cmds.poleVectorConstraint(pole_vector, ik_handle)

        # Lock generic unused attrs
        Attributes.set_lock_state(pole_vector, rotate=True, scale=True)

        return pole_vector

    @staticmethod
    def get_ik_handles_for_joint(joint: str) -> List[str]:
        """Find IK handles that control a given joint.

        IK-driven joints don't have direct connections from the ikHandle to
        their rotate channels - the solver computes rotations internally.
        This method detects IK influence by checking if the joint is between
        an ikHandle's start and end joints.

        The one-joint form of :meth:`ik_handles_by_joint`; a scan over many
        joints should build that map once instead of calling this per joint.

        Parameters:
            joint: The joint name to check.

        Returns:
            List of ikHandle names affecting this joint, or empty list.

        Example:
            >>> handles = RigUtils.get_ik_handles_for_joint("arm_elbow_jnt")
            >>> print(handles)  # ['arm_ikHandle']
        """
        # A node that no longer exists is not a joint, and this is a QUERY:
        # `cmds.nodeType` RAISES on an unresolvable name, so without the
        # existence check a single stale path aborted whatever was scanning
        # (measured: a production export died here at task 11 of 17, on a shape
        # renamed by an earlier task in the same run).
        if not cmds.objExists(joint) or cmds.nodeType(joint) != "joint":
            return []
        long_name = (cmds.ls(joint, long=True) or [joint])[0]
        return RigUtils.ik_handles_by_joint().get(long_name, [])

    @staticmethod
    def ik_handles_by_joint(
        handles: Optional[Iterable[str]] = None,
    ) -> Dict[str, List[str]]:
        """``{joint (long path): [ikHandle, ...]}`` for every joint an IK chain spans.

        One walk per handle -- end effector up to the start joint, both
        inclusive -- so membership for a whole scene costs O(handles x chain)
        instead of the O(joints x handles x chain) a per-joint scan pays;
        SmartBake's analysis of a 1509-transform scene spent a third of its
        time in that scan.

        Parameters:
            handles: ikHandles to walk. None (default) walks every ikHandle
                in the scene.

        Returns:
            Dict of joint long names to the handles affecting them, handles
            in *handles* (scene) order. Joints on no chain are absent.
        """
        if handles is None:
            handles = cmds.ls(type="ikHandle") or []
        by_joint: Dict[str, List[str]] = {}
        for handle in handles:
            effector = cmds.listConnections(
                f"{handle}.endEffector", source=True, destination=False
            )
            if not effector:
                continue
            end_joint = cmds.listConnections(
                f"{effector[0]}.translateX", source=True, destination=False
            )
            start_joint = cmds.listConnections(
                f"{handle}.startJoint", source=True, destination=False
            )
            if not end_joint or not start_joint:
                continue
            start = (cmds.ls(start_joint[0], long=True) or [start_joint[0]])[0]
            current = (cmds.ls(end_joint[0], long=True) or [end_joint[0]])[0]
            while current:
                by_joint.setdefault(current, []).append(handle)
                if current == start:
                    break
                parent = cmds.listRelatives(
                    current, parent=True, type="joint", fullPath=True
                )
                current = parent[0] if parent else None
        return by_joint

    @staticmethod
    def joint_in_ik_chain(joint: str, start_joint: str, end_joint: str) -> bool:
        """Check if a joint is part of an IK chain between start and end.

        Traverses the joint hierarchy from end_joint up to start_joint,
        checking if the given joint is encountered.

        Parameters:
            joint: The joint to check.
            start_joint: The root joint of the IK chain.
            end_joint: The end joint of the IK chain.

        Returns:
            True if joint is in the chain (inclusive of start and end).

        Example:
            >>> RigUtils.joint_in_ik_chain("elbow_jnt", "shoulder_jnt", "wrist_jnt")
            True
        """
        # Normalize to long paths so the equality checks below are
        # path-consistent (listRelatives fullPath yields long names).
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        start_joint = (cmds.ls(start_joint, long=True) or [start_joint])[0]
        end_joint = (cmds.ls(end_joint, long=True) or [end_joint])[0]

        current = end_joint
        while current:
            if current == joint:
                return True
            if current == start_joint:
                return False
            parent = cmds.listRelatives(
                current, parent=True, type="joint", fullPath=True
            )
            current = parent[0] if parent else None
        return False

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
        joints = cmds.ls(str(root_joint), type="joint", flatten=True, long=True) or []
        if not joints or len(joints) > 1:
            cmds.warning(f"Operation requires a root joint: got {root_joint}")
            return []
        root_joint = joints[0]  # long path — keeps the chain uniformly disambiguated

        # Traverse the hierarchy to get the joint chain
        joint_chain = []
        current_joint = root_joint
        while current_joint:
            joint_chain.append(current_joint)
            children = cmds.listRelatives(
                current_joint, children=True, type="joint", fullPath=True
            )
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
        root_joint = str(root_joint)
        # Get the original joint chain starting from the root
        original_joints = (
            cmds.listRelatives(
                root_joint, allDescendents=True, type="joint", fullPath=True
            )
            or []
        )
        original_joints.append(root_joint)
        original_joints.reverse()  # Now from end joint to root joint

        # Collect positions and radii of the original joints
        joint_positions = [
            cmds.xform(joint, q=True, ws=True, t=True) for joint in original_joints
        ]
        joint_radii = [cmds.getAttr(f"{joint}.radius") for joint in original_joints]

        if not keep_original:
            cmds.delete(original_joints)

        # Create a new joint chain along the same positions
        cmds.select(clear=True)
        new_joints = []
        for i, pos in enumerate(joint_positions):
            new_joint = cmds.joint(position=pos)
            new_joints.append(new_joint)
            # Set the joint radius to match the original
            cmds.setAttr(f"{new_joint}.radius", joint_radii[i])

        # Unparent all new joints
        for joint in new_joints:
            joint_parent = cmds.listRelatives(joint, parent=True, path=True)
            if joint_parent:
                cmds.parent(joint, world=True)

        # Reverse the new joints list to set up reversed hierarchy
        new_joints.reverse()

        # Re-parent joints in reverse order to create reversed hierarchy
        for i in range(len(new_joints) - 1):
            cmds.parent(new_joints[i + 1], new_joints[i])

        # Zero out joint orientations before reorienting
        for joint in new_joints:
            cmds.setAttr(f"{joint}.jointOrient", 0, 0, 0, type="double3")

        # Reorient the joints to point towards their children
        cmds.select(new_joints[0], hierarchy=True)
        cmds.joint(
            edit=True,
            orientJoint="xyz",
            secondaryAxisOrient="yup",
            zeroScaleOrient=True,
            children=True,
        )

        # Direct, not @add_to_isolation: get_transform_node resolves a joint to
        # its parent, so the decorator would drop the chain's last joint.
        DisplayUtils.add_to_isolation_set(new_joints)
        return new_joints

    @classmethod
    @CoreUtils.undoable
    def rebind_skin_clusters(
        cls,
        meshes: Optional[List[str]] = None,
        temp_dir: Optional[str] = None,
        inherits_transform: Optional[bool] = None,
    ) -> Dict[str, list]:
        """Rebinds skinClusters on the given meshes, preserving weights, bind pose, and transform lock state.

        Parameters:
            meshes (List[str], optional): Mesh transform names to process. If None, all skinned meshes are used.
            temp_dir (str, optional): Directory for exporting temporary weight files. Defaults to Maya temp.
            inherits_transform (bool or None, optional):
                - True: explicitly sets inheritsTransform = True
                - False: explicitly sets inheritsTransform = False
                - None: preserves the original inheritsTransform value

        Returns:
            dict: Per-object outcome summary categorizing every resolved input:
                - "rebound" (List[str]): transforms whose skinCluster was rebound.
                - "no_skin_cluster" (List[str]): mesh transforms that had no skinCluster.
                - "wrong_type" (List[str]): inputs that weren't a mesh transform.
                - "failed" (List[Tuple[str, str]]): (name, error) for objects that raised.
        """
        import os

        if temp_dir is None:
            temp_dir = os.path.join(
                cmds.internalVar(userTmpDir=True), "skin_rebind_weights"
            )
        os.makedirs(temp_dir, exist_ok=True)

        result = {
            "rebound": [],
            "no_skin_cluster": [],
            "wrong_type": [],
            "failed": [],
        }

        # Resolve inputs to (shape, transform, label) triples, recording
        # invalid inputs so the caller can distinguish "wrong type" from
        # "no skinCluster".
        shape_inputs = []  # list of (mesh_shape, transform, input_label)
        if meshes is None:
            for shape in cmds.ls(type="mesh", noIntermediate=True) or []:
                transform = NodeUtils.get_parent(shape)
                if transform:
                    shape_inputs.append(
                        (shape, transform, CoreUtils.leaf_name(transform))
                    )
        else:
            for m in meshes:
                label = CoreUtils.leaf_name(m)
                if not cmds.objectType(m, isAType="transform"):
                    result["wrong_type"].append(label)
                    print(f"[SKIP] Not a mesh transform: {label}")
                    continue
                shapes = NodeUtils.get_shapes(m, no_intermediate=True)
                mesh_shapes = [s for s in shapes if cmds.nodeType(s) == "mesh"]
                if not mesh_shapes:
                    result["wrong_type"].append(label)
                    print(f"[SKIP] No mesh shape: {label}")
                    continue
                # ``m`` is the selected transform; use it directly so instanced
                # shapes operate on the chosen parent, not an arbitrary one.
                shape_inputs.append((mesh_shapes[0], m, label))

        for shape, transform, label in shape_inputs:
            try:
                # ``cmds.listHistory`` doesn't accept ``type=``;
                # filter the result post-call.
                history = cmds.listHistory(shape) or []
                skin_clusters = [
                    h for h in history if cmds.nodeType(h) == "skinCluster"
                ]
                if not skin_clusters:
                    result["no_skin_cluster"].append(label)
                    print(f"[SKIP] No skinCluster: {label}")
                    continue

                skin_cluster = skin_clusters[0]
                print(f"Processing: {skin_cluster} on {label}")

                inherits_plug = f"{transform}.inheritsTransform"

                # Preserve inheritsTransform and unlock transform attrs
                original_inherits = cmds.getAttr(inherits_plug)
                lock_state = Attributes.get_lock_state(transform, unlock=True)

                # Cache influences and bindPreMatrix.
                # bindPreMatrix[]/matrix[] are indexed by the LOGICAL plug
                # index, which becomes sparse once any influence has been
                # removed (e.g. remaining plugs at [0, 2] with [1] gone).
                # Resolve each influence's logical index from its matrix[]
                # connection rather than its dense position in the ``-influence``
                # query, which would otherwise read the wrong (or an
                # unpopulated identity) bindPreMatrix plug.
                influences = (
                    cmds.skinCluster(skin_cluster, query=True, influence=True) or []
                )

                def _full_path(node):
                    resolved = cmds.ls(node, long=True)
                    return resolved[0] if resolved else node

                logical_by_influence = {}
                for i in (
                    cmds.getAttr(f"{skin_cluster}.matrix", multiIndices=True) or []
                ):
                    src = (
                        cmds.listConnections(
                            f"{skin_cluster}.matrix[{i}]",
                            source=True,
                            destination=False,
                        )
                        or []
                    )
                    if src:
                        logical_by_influence[_full_path(src[0])] = i

                bind_pre_matrices = {}
                for jnt in influences:
                    idx = logical_by_influence.get(_full_path(jnt))
                    if idx is None:  # Contiguous-cluster fallback.
                        idx = influences.index(jnt)
                    bind_pre_matrices[jnt] = cmds.getAttr(
                        f"{skin_cluster}.bindPreMatrix[{idx}]"
                    )

                # Export weights
                weight_file = os.path.join(temp_dir, f"{label}_weights.xml")
                cmds.deformerWeights(
                    os.path.basename(weight_file),
                    export=True,
                    deformer=skin_cluster,
                    path=temp_dir,
                    shape=shape,
                )

                # Delete original skinCluster
                skin_cluster_name = CoreUtils.leaf_name(skin_cluster)
                cmds.delete(skin_cluster)

                # Recreate skinCluster
                new_skin_cluster = cmds.skinCluster(
                    influences,
                    transform,
                    toSelectedBones=True,
                    bindMethod=0,
                    skinMethod=0,
                    normalizeWeights=1,
                    name=skin_cluster_name,
                )[0]

                # Restore bindPreMatrix
                for jnt, mat in bind_pre_matrices.items():
                    idx = cmds.skinCluster(
                        new_skin_cluster, query=True, influence=True
                    ).index(jnt)
                    cmds.setAttr(
                        f"{new_skin_cluster}.bindPreMatrix[{idx}]",
                        mat,
                        type="matrix",
                    )

                # Import weights
                cmds.deformerWeights(
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
                cmds.setAttr(inherits_plug, final_inherits)
                cmds.setAttr(inherits_plug, keyable=True)
                cmds.setAttr(inherits_plug, channelBox=True)

                # Restore transform lock state
                Attributes.set_lock_state(transform, lock_state=lock_state)

                result["rebound"].append(label)
                print(f"[OK] Rebound: {label}")

            except Exception as e:
                result["failed"].append((label, str(e)))
                print(f"[FAIL] Failed: {label} -> {e}")

        # Detailed one-line console summary of the whole run.
        print(
            "[rebind_skin_clusters] "
            f"rebound={len(result['rebound'])}, "
            f"no_skin_cluster={len(result['no_skin_cluster'])}, "
            f"wrong_type={len(result['wrong_type'])}, "
            f"failed={len(result['failed'])}"
        )
        return result


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
