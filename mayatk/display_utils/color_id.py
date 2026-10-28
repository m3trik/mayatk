from __future__ import annotations

from typing import List, Tuple, Optional
import random

try:
    import maya.cmds as cmds
except ModuleNotFoundError:
    pass

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class ColorUtils:
    @staticmethod
    def assign_material(obj: str, color: Tuple[float, float, float]) -> str:
        """Assigns a material to an object based on the RGB value. Creates the material if it does not exist."""
        # Convert the RGB tuple to a hex string for the material name
        color_name = "_".join(f"{int(c * 255):02X}" for c in color)
        material_name = f"ID_{color_name}"

        # Check if the material already exists
        if not cmds.objExists(material_name):
            # Create a new material and shading group
            material = MatUtils.create_mat("lambert", prefix="ID_", name=color_name)
            cmds.setAttr(
                f"{material}.color", color[0], color[1], color[2], type="double3"
            )
        else:
            material = material_name

        # Assign the material to the object
        MatUtils.assign_mat(obj, material_name)
        return material

    @classmethod
    def set_color_attribute(
        cls,
        obj: str,
        color: Tuple[float, float, float],
        attr_type: str,
        force: bool = False,
    ) -> None:
        """Applies color based on the attribute type specified, optionally overriding attribute locks."""
        try:
            if attr_type == "outliner":
                if Attributes.has_attr(obj, "useOutlinerColor"):
                    Attributes.set_plug(f"{obj}.useOutlinerColor", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'useOutlinerColor'.")

                if Attributes.has_attr(obj, "outlinerColor"):
                    Attributes.set_plug(f"{obj}.outlinerColor", color, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'outlinerColor'.")

            elif attr_type == "vertex":
                shapes = cmds.listRelatives(obj, shapes=True, type="mesh")
                if not shapes:
                    cmds.warning(
                        f"{obj} does not have a mesh shape for vertex color assignment."
                    )
                    return
                try:
                    cmds.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    cmds.warning(f"Error applying vertex color to {obj}: {e}")

            elif attr_type == "material":
                cls.assign_material(obj, color)

            elif attr_type == "wireframe":
                if cmds.nodeType(obj) == "lambert":
                    cmds.warning(f"{obj} is a material, not a mesh.")
                    return

                if Attributes.has_attr(obj, "overrideEnabled"):
                    Attributes.set_plug(f"{obj}.overrideEnabled", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideEnabled'.")

                if Attributes.has_attr(obj, "overrideRGBColors"):
                    Attributes.set_plug(f"{obj}.overrideRGBColors", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideRGBColors'.")

                if Attributes.has_attr(obj, "overrideColorRGB"):
                    Attributes.set_plug(f"{obj}.overrideColorRGB", color, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideColorRGB'.")

        except Exception as e:
            cmds.warning(f"Color assignment failed on {obj}: {e}")

    @classmethod
    def _material_colors(cls, obj: str) -> List[Tuple[float, float, float]]:
        """Every material color assigned to ``obj`` (a multi-shader mesh gives several).

        Resolves through the object's **shapes**: shading engines connect to the shape, so a
        transform-only ``listConnections`` sees nothing — which made the Material channel's
        read + select-by-color match nothing at all (verified in mayapy)."""
        nodes = cmds.ls(obj, long=True) or []
        if not nodes:
            return []
        shapes = cmds.listRelatives(nodes, shapes=True, fullPath=True) or []
        shading_groups = (
            cmds.listConnections(nodes + shapes, type="shadingEngine") or []
        )
        if not shading_groups:
            return []
        materials = cmds.ls(cmds.listConnections(shading_groups) or [], materials=True)
        return [
            tuple(cmds.getAttr(f"{mat}.color")[0])
            for mat in dict.fromkeys(materials or [])
            if Attributes.has_attr(mat, "color")
        ]

    @classmethod
    def get_material_color(cls, obj: str) -> Optional[Tuple[float, float, float]]:
        """Gets the color of the object's material (the first, on a multi-shader mesh)."""
        colors = cls._material_colors(obj)
        return colors[0] if colors else None

    @staticmethod
    def get_wireframe_color(
        obj: str,
        normalize: bool = False,
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the wireframe color of the given object."""
        if not Attributes.has_attr(obj, "overrideEnabled") or not cmds.getAttr(
            f"{obj}.overrideEnabled"
        ):
            return None
        if not Attributes.has_attr(obj, "overrideRGBColors") or not cmds.getAttr(
            f"{obj}.overrideRGBColors"
        ):
            return None
        if not Attributes.has_attr(obj, "overrideColorRGB"):
            return None

        color = cmds.getAttr(f"{obj}.overrideColorRGB")[0]
        if not normalize:
            color = tuple(int(c * 255) for c in color)
        return color

    @staticmethod
    def get_vertex_color(
        obj: str, vertex_id: int
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the color of a specific vertex on the object."""
        colors = cmds.polyColorPerVertex(
            f"{obj}.vtx[{vertex_id}]", query=True, rgb=True
        )
        return colors if colors else None

    @staticmethod
    def get_average_vertex_color(obj: str) -> Optional[Tuple[float, float, float]]:
        """Average of the object's vertex colors, or None when its mesh has none
        (twin of blendertk's ``ColorId.get_average_vertex_color``)."""
        if not cmds.listRelatives(obj, shapes=True, type="mesh"):
            return None
        try:
            colors = cmds.polyColorPerVertex(f"{obj}.vtx[*]", query=True, rgb=True)
        except RuntimeError:
            return None
        # Flat [r, g, b, r, g, b, ...]; grouped into triples.
        triples = list(zip(colors[0::3], colors[1::3], colors[2::3])) if colors else []
        if not triples:
            return None
        return tuple(sum(c) / len(triples) for c in zip(*triples))

    @staticmethod
    def get_outliner_color(obj: str) -> Optional[Tuple[float, float, float]]:
        """The object's outliner color while ``useOutlinerColor`` is on, else None
        (twin of blendertk's ``ColorId.get_outliner_color``)."""
        if not (
            Attributes.has_attr(obj, "useOutlinerColor")
            and cmds.getAttr(f"{obj}.useOutlinerColor")
            and Attributes.has_attr(obj, "outlinerColor")
        ):
            return None
        return tuple(cmds.getAttr(f"{obj}.outlinerColor")[0])

    @staticmethod
    def set_vertex_color(objects: List[str], color: Tuple[float, float, float]) -> None:
        """Applies the specified color to the object's vertices."""
        for obj in cmds.ls(objects, long=True) or []:
            shapes = cmds.listRelatives(obj, shapes=True, type="mesh")
            if shapes:
                try:
                    cmds.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    print(f"Error applying vertex color to {obj}: {e}")

    @staticmethod
    def get_color_difference(
        color1: Tuple[float, float, float], color2: Tuple[float, float, float]
    ) -> float:
        """Calculate the average difference between two RGB colors."""
        return sum(abs(c1 - c2) for c1, c2 in zip(color1, color2)) / 3.0

    # ── Set Per Color (grouping aid; blendertk's twin uses ID collections) ──
    #: Stamp carrying the set's exact color. Membership discovery keys on this attribute, never
    #: on the ``ID_`` name, so a user's own set called ``ID_*`` is never adopted or deleted.
    _ID_SET_ATTR = "mtk_color_id"

    @staticmethod
    def _id_set_name(color: Tuple[float, float, float]) -> str:
        return "ID_" + "".join(
            f"{int(max(0.0, min(1.0, c)) * 255):02X}" for c in color[:3]
        )

    @classmethod
    def _stamped_sets(cls) -> List[str]:
        """Every objectSet this tool created (identified by its stamp)."""
        return [
            s
            for s in (cmds.ls(sets=True) or [])
            if cmds.attributeQuery(cls._ID_SET_ATTR, node=s, exists=True)
        ]

    @classmethod
    def add_to_color_set(
        cls, objects: List[str], color: Tuple[float, float, float]
    ) -> Optional[str]:
        """Group ``objects`` into a stamped ``ID_<HEX>`` objectSet; returns it (None if empty).

        Objects keep every other set membership, and a recolor *moves* them out of any other
        stamped set rather than piling up. Sets emptied by the move are deleted."""
        objects = [o for o in cmds.ls(objects, long=True) or [] if o]
        if not objects:
            return None
        # Stamp-keyed lookup, never name-keyed (see _ID_SET_ATTR).
        node = next(
            (
                s
                for s in cls._stamped_sets()
                if cls.get_color_difference(
                    cmds.getAttr(f"{s}.{cls._ID_SET_ATTR}")[0], color
                )
                < 1e-6
            ),
            None,
        )
        if node is None:
            node = cmds.sets(name=cls._id_set_name(color), empty=True)
            cmds.addAttr(node, longName=cls._ID_SET_ATTR, attributeType="double3")
            for axis in "XYZ":
                cmds.addAttr(
                    node,
                    longName=f"{cls._ID_SET_ATTR}{axis}",
                    attributeType="double",
                    parent=cls._ID_SET_ATTR,
                )
            cmds.setAttr(f"{node}.{cls._ID_SET_ATTR}", *color[:3], type="double3")
        for obj in objects:
            for other in cls._stamped_sets():
                if other != node and cmds.sets(obj, isMember=other):
                    cmds.sets(obj, remove=other)
            if not cmds.sets(obj, isMember=node):
                cmds.sets(obj, add=node)
        cls._gc_color_sets()
        return node

    @classmethod
    def get_color_set_color(cls, obj: str) -> Optional[Tuple[float, float, float]]:
        """The exact color stamped on the object's ID set, or None."""
        for node in cls._stamped_sets():
            if cmds.sets(obj, isMember=node):
                return tuple(cmds.getAttr(f"{node}.{cls._ID_SET_ATTR}")[0])
        return None

    @classmethod
    def remove_from_color_sets(cls, objects: List[str]) -> None:
        """Drop ``objects`` from every stamped ID set; delete any left empty."""
        for obj in cmds.ls(objects, long=True) or []:
            for node in cls._stamped_sets():
                if cmds.sets(obj, isMember=node):
                    cmds.sets(obj, remove=node)
        cls._gc_color_sets()

    @classmethod
    def _gc_color_sets(cls) -> None:
        """Delete stamped ID sets that no longer hold anything (members are never touched —
        verified in mayapy: deleting an objectSet leaves its members alive)."""
        for node in cls._stamped_sets():
            if not (cmds.sets(node, q=True) or []):
                cmds.delete(node)


class ColorId(ColorUtils):
    @classmethod
    @CoreUtils.undoable(name="Color ID: Set Color")
    def apply_color(
        cls,
        objects: List[str],
        color: Optional[Tuple[float, float, float]] = None,
        apply_to_material: bool = False,
        apply_to_vertex: bool = False,
        apply_to_wireframe: bool = False,
        apply_to_outliner: bool = False,
        set_per_color: bool = False,
    ) -> None:
        """Applies color based on given criteria to objects, as one undo step.

        ``set_per_color`` additionally groups the batch into an ``ID_<HEX>`` objectSet — a
        grouping aid orthogonal to the color channels (mirrors blendertk's ID-collection twin)."""
        if color is None:
            color = (random.random(), random.random(), random.random())
        if set_per_color:
            cls.add_to_color_set(objects, color)
        for obj in cmds.ls(objects, long=True) or []:
            if apply_to_vertex:
                cls.set_color_attribute(obj, color, attr_type="vertex", force=True)
            if apply_to_wireframe:
                cls.set_color_attribute(obj, color, attr_type="wireframe", force=True)
            if apply_to_outliner:
                cls.set_color_attribute(obj, color, attr_type="outliner", force=True)
            if apply_to_material:
                cls.set_color_attribute(obj, color, attr_type="material", force=True)

    @classmethod
    def get_objects_by_color(
        cls,
        target_color: Tuple[float, float, float],
        threshold: float = 0.1,
        check_material_color: bool = False,
        check_vertex_color: bool = False,
        check_wireframe_color: bool = False,
        check_outliner_color: bool = False,
        check_set: bool = False,
    ) -> List[str]:
        """Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors."""
        matching_objects = []

        for obj in NodeUtils.list_transforms(geometry=True, long=True):
            matched = False
            if check_material_color and not matched:
                matched = any(
                    cls.get_color_difference(c, target_color) <= threshold
                    for c in cls._material_colors(obj)
                )
            if check_wireframe_color and not matched:
                # normalize=True is the 0-1 form (the flag reads backwards) — the same
                # units as target_color, and the same override-enabled gate this branch
                # used to spell out inline.
                wireframe_color = cls.get_wireframe_color(obj, normalize=True)
                matched = (
                    wireframe_color is not None
                    and cls.get_color_difference(wireframe_color, target_color)
                    <= threshold
                )
            if check_outliner_color and not matched:
                outliner_color = cls.get_outliner_color(obj)
                matched = (
                    outliner_color is not None
                    and cls.get_color_difference(outliner_color, target_color)
                    <= threshold
                )
            if check_set and not matched:
                set_color = cls.get_color_set_color(obj)
                if (
                    set_color is not None
                    and cls.get_color_difference(set_color, target_color) <= threshold
                ):
                    matched = True
            if check_vertex_color and not matched:
                vertex_color = cls.get_average_vertex_color(obj)
                matched = (
                    vertex_color is not None
                    and cls.get_color_difference(vertex_color, target_color)
                    <= threshold
                )
            if matched:
                matching_objects.append(obj)

        return matching_objects

    @classmethod
    def select_by_color(
        cls, target_color: Tuple[float, float, float], threshold: float = 0.1, **checks
    ) -> List[str]:
        """Select the objects :meth:`get_objects_by_color` finds, replacing the
        selection (none found clears it); returns them. ``checks`` are its
        ``check_*`` channel flags (the Color ID panel's Select By Color)."""
        found = cmds.ls(
            cls.get_objects_by_color(target_color, threshold=threshold, **checks),
            long=True,
        ) or []
        if found:
            cmds.select(found, replace=True)
        else:
            cmds.select(clear=True)
        return found

    @classmethod
    def get_color(
        cls,
        obj: str,
        check_material_color: bool = False,
        check_vertex_color: bool = False,
        check_wireframe_color: bool = False,
        check_outliner_color: bool = False,
        check_set: bool = False,
    ) -> Optional[Tuple[float, float, float]]:
        """The first color ``obj`` carries on the enabled channels (0-1 RGB), or None.

        The Color ID panel's Get Color. Read in the order Outliner, Set, Wireframe,
        Material, Vertex: the exact stamps first, the averaged vertex colors last
        (blendertk's twin reads the same order)."""
        readers = (
            (check_outliner_color, cls.get_outliner_color),
            (check_set, cls.get_color_set_color),
            (
                check_wireframe_color,
                lambda o: cls.get_wireframe_color(o, normalize=True),
            ),
            (check_material_color, cls.get_material_color),
            (check_vertex_color, cls.get_average_vertex_color),
        )
        for enabled, read in readers:
            color = read(obj) if enabled else None
            if color is not None:
                return tuple(color[:3])
        return None

    @staticmethod
    def lead_object() -> Optional[str]:
        """The object Get Color reads: the last selected transform (Maya's lead
        object), or None without one (blendertk's twin: the active object). A
        component selection names no object, so it reads None too."""
        selection = cmds.ls(selection=True, long=True, type="transform") or []
        return selection[-1] if selection else None

    @staticmethod
    def scene_objects() -> List[str]:
        """Every object a scene-wide Color ID action covers (the panel's Ctrl+click
        Reset): the geometry transforms, the walk :meth:`get_objects_by_color` makes.
        Transforms, not the shapes ``cmds.ls(geometry=True)`` yields: Set Color writes
        the outliner and wireframe channels on the transform."""
        return cmds.ls(NodeUtils.list_transforms(geometry=True), long=True) or []

    @classmethod
    @CoreUtils.undoable(name="Color ID: Reset")
    def reset_colors(
        cls,
        objects: List[str],
        reset_outliner: bool = True,
        reset_wireframe: bool = True,
        reset_vertex: bool = True,
        reset_material: bool = True,
        reset_sets: bool = True,
    ) -> None:
        """Resets colors to default for given objects, with options to specify which color types to reset (one undo step)."""
        if reset_sets:
            cls.remove_from_color_sets(objects)
        for obj in cmds.ls(objects, long=True) or []:
            if reset_outliner:
                if Attributes.has_attr(obj, "useOutlinerColor"):
                    cmds.setAttr(f"{obj}.useOutlinerColor", 0)
                else:
                    cmds.warning(f"{obj} has no attribute 'useOutlinerColor'.")

            if reset_wireframe:
                if Attributes.has_attr(obj, "overrideEnabled"):
                    cmds.setAttr(f"{obj}.overrideEnabled", 0)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideEnabled'.")

            if reset_material:
                mats = MatUtils.get_mats(obj)
                MatUtils.assign_mat(obj, "lambert1")
                for mat in mats:
                    # Sweep ONLY the tool's own ``ID_*`` materials, and only
                    # once nothing uses them (blendertk parity: user materials
                    # are never deleted). Membership, not connection, is the
                    # orphan test -- an ID material's shading group stays wired
                    # to ``outColor`` even with zero members, so the old
                    # ``is_connected(delete=True)`` sweep could never fire and
                    # one orphaned ID network accumulated per applied color.
                    if not str(mat).startswith("ID_"):
                        continue
                    sgs = (
                        cmds.listConnections(f"{mat}.outColor", type="shadingEngine")
                        or []
                    )
                    members = [m for sg in sgs for m in (cmds.sets(sg, q=True) or [])]
                    if not members:
                        cmds.delete([mat] + sgs)

        if reset_vertex:
            cls.reset_vertex_colors(objects)

    @staticmethod
    def reset_vertex_colors(objects: List[str]) -> None:
        """Resets vertex colors for the given object(s), handling potential errors gracefully.

        Accepts transforms *or* mesh shapes: the Ctrl+click reset passes
        ``cmds.ls(geometry=True)``, which yields shapes — filtering to transforms alone made
        that path silently skip every mesh."""
        nodes = cmds.ls(objects, long=True) or []
        shapes = set(cmds.ls(nodes, type="mesh", long=True) or [])
        transforms = cmds.ls(nodes, type="transform", long=True) or []
        if transforms:
            shapes.update(
                cmds.listRelatives(
                    transforms, children=True, shapes=True, fullPath=True
                )
                or []
            )

        for shape in shapes:
            if cmds.nodeType(shape) == "mesh":
                try:
                    color_sets = cmds.polyColorSet(shape, query=True, allColorSets=True)
                    if color_sets:
                        for color_set in color_sets:
                            cmds.polyColorSet(shape, delete=True, colorSet=color_set)
                except RuntimeError as e:
                    print(f"Error removing vertex colors from {shape}: {e}")

    # Desaturated defaults so swatches aren't all white on first launch.
    DEFAULT_SWATCH_COLORS = [
        (180, 120, 120),  # muted red
        (180, 150, 120),  # muted orange
        (180, 180, 120),  # muted yellow
        (120, 180, 120),  # muted green
        (120, 180, 160),  # muted teal
        (120, 180, 180),  # muted cyan
        (120, 150, 180),  # muted blue
        (120, 120, 180),  # muted indigo
        (150, 120, 180),  # muted purple
        (180, 120, 180),  # muted magenta
        (180, 120, 150),  # muted pink
        (160, 160, 160),  # muted gray
    ]

    #: The swatch palettes' preset store (uitk ``PresetManager.preset_dir``), which the
    #: tentacle panel reads. Kept as the legacy "color_manager" (the tool was renamed
    #: Color Manager -> Color ID): it keys existing saved palettes and the
    #: PresetManager legacy-migration table, and is never user-visible.
    SWATCH_PRESET_DIR = "mayatk/color_manager"


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
