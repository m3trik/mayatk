# !/usr/bin/python
# coding=utf-8
"""LOD groups in Maya: read, group, generate and conform to the one convention.

A LOD group is a node ``<base>`` whose children ``<base>_LOD0..n`` are its
levels, most detailed first (``ptk.LodChain``: the names Unity's importer
reads). Here the node is Maya's own ``lodGroup``, whose screen-height
thresholds Maya's FBX writer states as an FbxLODGroup (measured 2026-11-10,
with Animation on or off) and whose camera switch previews the levels in the
viewport.

Adopted, not rebuilt: the ``lodGroup`` node, and ``polyReduce`` for each
generated level (on a copy cleaned of non-manifold vertices, which
``polyReduce`` refuses: measured on 2 of 4 NIST parts, through
``MeshDiagnostics.clean_geometry``). ``EditUtils.decimate`` was weighed: it
reduces by a percentage of faces, quads kept, not to a triangle target. Maya's own Generate LOD
Meshes is NOT the generator: it holds each level in a ``LOD_<n>`` container
with the source's short name repeated, which Unity does not read and warns on
(measured); :meth:`LodUtils.conform` turns that form into the convention.
Grouping keeps each mesh's own transform: the group sits at identity where
the mesh's parent space was, so nothing moves. Twin of blendertk's
``LodUtils``.
"""

from typing import Any, Dict, List, Optional, Sequence

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = mel = None

import logging

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils

logger = logging.getLogger(__name__)


class LodUtils(ptk.HelpMixin):
    """Read, group, generate and conform LOD groups (``ptk.LodChain``)."""

    #: The camera a new ``lodGroup`` switches by in the viewport.
    CAMERA = "persp"
    #: The :meth:`generate` keywords this host honours (a shared panel greys
    #: the rest): polyReduce holds borders, hard edges and UV borders.
    GENERATE_OPTIONS = ("levels", "ratio", "preserve_borders", "preserve_hard_edges", "preserve_uv_borders")

    # ------------------------------------------------------------ read
    @staticmethod
    def groups(objects=None) -> List[Dict[str, Any]]:
        """The LOD groups among *objects* and below them (the whole scene when
        ``None``), as ``ptk.LodChain.groups`` reads them: each level with its
        triangles and its world box (its subtree's)."""
        if objects is not None and not objects:
            return []  # nothing chosen reads nothing (cmds.ls of [] lists the scene)
        roots = cmds.ls(objects, long=True, type="transform") if objects else None
        if objects and not roots:
            return []
        paths = set(roots or [])
        found = (
            cmds.listRelatives(roots, allDescendents=True, type="transform", fullPath=True)
            if roots
            else cmds.ls(type="transform", long=True)
        )
        paths.update(found or [])
        return ptk.LodChain.groups(LodUtils.nodes(sorted(paths)))

    @staticmethod
    def nodes(paths: Sequence[str]) -> List[Dict[str, Any]]:
        """*paths* (full transform paths) as ``ptk.LodChain`` reads nodes: a
        level with its triangles and its world box (its subtree's), counted
        for levels alone -- a scene's other nodes cost a name parse."""
        nodes = []
        for path in paths:
            triangles = bounds = None
            if ptk.LodChain.parse(path):
                if cmds.listRelatives(path, shapes=True, type="mesh", noIntermediate=True):
                    count = cmds.polyEvaluate(path, triangle=True)
                    triangles = count if isinstance(count, int) else None
                box = cmds.exactWorldBoundingBox(path)
                bounds = (tuple(box[:3]), tuple(box[3:])) if box and len(box) == 6 else None
            nodes.append(
                {"path": path, "parent": path.rpartition("|")[0] or None, "triangles": triangles, "bounds": bounds}
            )
        return nodes

    # ------------------------------------------------------------ the record
    @staticmethod
    def switches_by_distance(group: str) -> bool:
        """Whether *group* is a ``lodGroup`` switching by camera distance
        (Maya's default), which no screen height states (:meth:`heights`)."""
        try:
            return cmds.nodeType(group) == "lodGroup" and not cmds.getAttr(f"{group}.useScreenHeightPercentage")
        except (RuntimeError, ValueError):
            return False

    @staticmethod
    def heights(group: str, count: int) -> List[float]:
        """*group*'s switch heights for *count* levels: a ``lodGroup``'s
        screen-height thresholds, else the default ladder -- a group switching
        by distance among them (:meth:`switches_by_distance`: a distance is no
        screen height). The cull is 0: a ``lodGroup`` states none (its last
        level draws however far)."""
        try:
            if cmds.nodeType(group) == "lodGroup" and cmds.getAttr(f"{group}.useScreenHeightPercentage"):
                values = cmds.getAttr(f"{group}.percentageThreshold") or []
                values = list(values[0]) if values and isinstance(values[0], (list, tuple)) else list(values)
                heights = ptk.LodChain.from_fbx_thresholds(values[: count - 1])
                if len(heights) == count:
                    ptk.LodChain.check_heights(heights)
                    return heights
        except (RuntimeError, ValueError):
            pass
        return ptk.LodChain.default_heights(count)

    @classmethod
    def export_record(cls, ctx) -> Optional[Any]:
        """The ``lods`` record for the export, or ``None`` when it ships no LOD
        group -- the ``ptk.SceneRecords.LODS`` producer (``FbxUtils.PRODUCERS``).
        Pure: it reads the groups under the export's scope and never writes.
        Each valid group is listed with the levels the write ships
        (``ctx.ships``) and its heights (:meth:`heights`); a group left with
        fewer than two levels ships as plain nodes and is not listed, and a
        group shipping some of its levels keeps their switches
        (``ptk.LodChain.kept_heights``). The record's Unity consumer builds one
        LODGroup per entry (Unity's own merges every level-named mesh of a file
        onto its root and drops the heights)."""
        if cmds is None:
            return None
        entries = ptk.LodChain.record_groups(
            cls.groups(list(ctx.scope) if ctx.scope else None), ctx.ships, cls.heights
        )
        if not entries:
            return None
        return ptk.SceneRecords.LODS.make({"groups": entries})

    # ------------------------------------------------------------ author
    #: What drives a transform over time besides its own keys: a key on an
    #: animation layer (its blend node sits between curve and plug), a
    #: constraint, an expression, a motion path.
    _DRIVERS = ("animCurve", "animBlendNodeBase", "constraint", "expression", "motionPath")

    @classmethod
    def ineligible(
        cls, mesh: str, levels: int = ptk.LodChain.DEFAULT_LEVELS, ratio: float = ptk.LodChain.DEFAULT_RATIO
    ) -> Optional[str]:
        """Why :meth:`generate` leaves *mesh* (a transform) alone, or None: a
        level is a static copy of it, so a skinned or blend-shaped mesh, an
        instanced one, one moving (keyed, layered, constrained, expression- or
        path-driven, itself or above), one holding other transforms, or one
        too light for a lower level needs authored LODs. Twin of blendertk's."""
        shapes = cmds.listRelatives(mesh, shapes=True, type="mesh", noIntermediate=True, fullPath=True)
        if not shapes:
            return "no mesh"
        if ptk.LodChain.parse(mesh):
            return "a LOD level already"
        history = cmds.listHistory(shapes, pruneDagObjects=True) or []
        if cmds.ls(history, type=("skinCluster", "blendShape")):
            return "skinned or with blend shapes"
        if len(cmds.listRelatives(shapes[0], allParents=True, fullPath=True) or []) > 1:
            return "instanced"
        parts = cmds.ls(mesh, long=True)[0].split("|")
        for end in range(2, len(parts) + 1):
            node = "|".join(parts[:end])
            if any(cmds.listConnections(node, source=True, destination=False, type=kind) for kind in cls._DRIVERS):
                return "animated"
        # After the drivers: a constraint is a DAG child of what it drives.
        if cmds.listRelatives(mesh, children=True, type="transform"):
            return "holds other nodes"
        triangles = cmds.polyEvaluate(mesh, triangle=True)
        if not ptk.LodChain.ladder(triangles if isinstance(triangles, int) else 0, levels, ratio):
            return "too few triangles for a lower level"
        return None

    @staticmethod
    def _chosen(objects) -> List[str]:
        """The transforms of *objects*, else of the selection; nothing for
        nothing (``cmds.ls`` of an empty list lists the whole scene)."""
        chosen = objects if objects is not None else cmds.ls(selection=True)
        if not chosen:
            return []
        return cmds.ls(chosen, long=True, type="transform") or []

    @classmethod
    def _new_group(cls, name: str, parent: Optional[str]) -> str:
        """A ``lodGroup`` named *name* under *parent*, switching by :attr:`CAMERA`."""
        group = cmds.createNode("lodGroup", name=name, parent=parent, skipSelect=True)
        group = cmds.ls(group, long=True)[0]
        shapes = cmds.listRelatives(cls.CAMERA, shapes=True, fullPath=True) or []
        if shapes:
            cmds.connectAttr(f"{shapes[0]}.worldMatrix[0]", f"{group}.cameraMatrix")
            cmds.connectAttr(f"{shapes[0]}.focalLength", f"{group}.focalLength")
        cmds.setAttr(f"{group}.useScreenHeightPercentage", True)
        return group

    @staticmethod
    def set_heights(group: str, heights: Sequence[float]) -> None:
        """Set *group*'s switch heights (one per level, the cull last; see
        ``ptk.LodChain``) as the ``lodGroup``'s screen-height thresholds; a
        ``lodGroup`` states no cull, so the last height is not kept."""
        ptk.LodChain.check_heights(heights)
        cmds.setAttr(f"{group}.useScreenHeightPercentage", True)
        for index, percent in enumerate(ptk.LodChain.to_fbx_thresholds(heights)):
            cmds.setAttr(f"{group}.percentageThreshold[{index}]", percent)

    @classmethod
    @CoreUtils.undoable
    def create_group(
        cls,
        objects=None,
        name: Optional[str] = None,
        heights: Optional[Sequence[float]] = None,
    ) -> Optional[str]:
        """Group *objects* (the selection when ``None``), most detailed first,
        as the levels of one LOD group: each renamed ``<base>_LOD<n>``, parented
        (world placement kept) under a new ``lodGroup`` ``<base>`` beside the
        first. One undo step.

        Parameters:
            objects: The level transforms, LOD0 first.
            name: The base; default the first's base (``chair`` for
                ``chair_LOD0``) or its short name.
            heights: The switch heights; default ``ptk.LodChain.default_heights``.

        Returns:
            The group's path, or None for fewer than two objects.
        """
        levels = cls._chosen(objects)
        if len(levels) < 2:
            return None
        first = levels[0].rsplit("|", 1)[-1]
        parsed = ptk.LodChain.parse(first)
        base = name or (parsed[0] if parsed else first)
        parent = (cmds.listRelatives(levels[0], parent=True, fullPath=True) or [None])[0]
        uuids = cmds.ls(levels, uuid=True)
        for index, uuid in enumerate(uuids):  # first: frees <base> for the group
            cmds.rename(cmds.ls(uuid, long=True)[0], ptk.LodChain.level_name(base, index))
        group = cls._new_group(base, parent)
        for uuid in uuids:
            cmds.parent(cmds.ls(uuid, long=True)[0], group)
        cls.set_heights(group, heights or ptk.LodChain.default_heights(len(levels)))
        return cmds.ls(group, long=True)[0]

    @staticmethod
    def _reduce_copy(source: str, name: str, target: int, preserve: Dict[str, bool]) -> Optional[str]:
        """A copy of *source* named *name*, reduced to *target* triangles."""
        copy = cmds.duplicate(source, name=name, renameChildren=True)[0]
        copy = cmds.ls(copy, long=True)[0]
        if cmds.polyInfo(copy, nonManifoldVertices=True) or cmds.polyInfo(copy, nonManifoldEdges=True):
            cmds.select(copy, replace=True)  # Mesh > Cleanup: repair non-manifold (geometry too) and lamina, no history
            mel.eval('polyCleanupArgList 4 {"0","1","0","0","0","0","0","0","0","1e-05","0","1e-05","0","1e-05","0","2","1","0"}')
        cmds.polyTriangulate(copy, constructionHistory=False)
        cmds.polyReduce(
            copy,
            version=1,
            termination=2,
            triangleCount=int(target),
            keepBorder=preserve["borders"],
            keepFaceGroupBorder=preserve["borders"],
            keepHardEdge=preserve["hard_edges"],
            keepCreaseEdge=preserve["hard_edges"],
            keepMapBorder=preserve["uv_borders"],
            keepColorBorder=preserve["uv_borders"],
            keepQuadsWeight=0.0,
            replaceOriginal=True,
            constructionHistory=False,
        )
        return copy

    @classmethod
    @CoreUtils.undoable
    def generate(
        cls,
        objects=None,
        levels: int = ptk.LodChain.DEFAULT_LEVELS,
        ratio: float = ptk.LodChain.DEFAULT_RATIO,
        preserve_borders: bool = True,
        preserve_hard_edges: bool = True,
        preserve_uv_borders: bool = True,
        *,
        heights: Optional[Sequence[float]] = None,
    ) -> List[str]:
        """Make a LOD group of each mesh in *objects* (the selection when
        ``None``): the mesh becomes ``<name>_LOD0`` under a new ``lodGroup``
        ``<name>`` (at identity where its parent space was; it keeps its own
        transform) and each lower level a reduced copy beside it, half the
        last's triangles by default (``ptk.LodChain.ladder``), stopping above
        the floor. A mesh :meth:`ineligible` names is skipped and said (a
        level is a static copy: a skinned, animated or instanced mesh needs
        authored LODs). One undo step.

        Returns:
            The groups made, by path.
        """
        preserve = {"borders": preserve_borders, "hard_edges": preserve_hard_edges, "uv_borders": preserve_uv_borders}
        meshes: List[str] = []
        skipped: Dict[str, List[str]] = {}
        for candidate in cls._chosen(objects):
            reason = cls.ineligible(candidate, levels, ratio)
            if reason is None:
                meshes.append(candidate)
            elif reason != "no mesh":
                skipped.setdefault(reason, []).append(candidate.rsplit("|", 1)[-1])
        for reason, names in skipped.items():
            logger.info("Create LODs left %d mesh(es) alone (%s): %s", len(names), reason, ", ".join(sorted(names)))
        held = cmds.ls(selection=True, uuid=True)  # by uuid: the meshes are renamed
        made = []
        try:
            for mesh in meshes:
                triangles = cmds.polyEvaluate(mesh, triangle=True)
                targets = ptk.LodChain.ladder(triangles if isinstance(triangles, int) else 0, levels, ratio)
                if not targets:
                    continue
                base = mesh.rsplit("|", 1)[-1]
                parent = (cmds.listRelatives(mesh, parent=True, fullPath=True) or [None])[0]
                # By uuid from here: a short name can match a namesake elsewhere.
                (uuid,) = cmds.ls(mesh, uuid=True)
                cmds.rename(mesh, ptk.LodChain.level_name(base, 0))
                group = cls._new_group(base, parent)
                # The group sits at identity in the mesh's parent space: relative
                # keeps its local transform to the digit (absolute re-derives it).
                cmds.parent(cmds.ls(uuid, long=True)[0], group, relative=True)
                lod0 = cmds.ls(uuid, long=True)[0]
                for index, target in enumerate(targets, 1):
                    cls._reduce_copy(lod0, ptk.LodChain.level_name(base, index), target, preserve)
                count = len(targets) + 1
                cls.set_heights(group, heights if heights and len(heights) == count else ptk.LodChain.default_heights(count))
                made.append(cmds.ls(group, long=True)[0])
        finally:  # a copy's duplicate and cleanup select it; the user's selection comes back
            kept = cmds.ls(held, long=True) if held else []
            cmds.select(kept, replace=True) if kept else cmds.select(clear=True)
        return made

    @classmethod
    @CoreUtils.undoable
    def conform(cls, objects=None) -> List[str]:
        """Turn the ``LOD_<n>`` container groups among *objects* (Maya's Generate
        LOD Meshes) into the convention, which Unity reads: each container's
        one mesh moves up beside it as ``<mesh>_LOD<n>`` and the emptied
        container goes; a container holding several meshes is renamed
        ``<group>_LOD<n>`` instead (a level that is a subtree). One undo step.

        Returns:
            The groups conformed, by path.
        """
        conformed = []
        for group in cls.groups(objects):
            if group["form"] != "containers" or group["issues"]:
                continue
            holder = group["parent"]
            for index, container in enumerate(group["levels"]):
                inside = cmds.listRelatives(container, children=True, type="transform", fullPath=True) or []
                if len(inside) == 1:
                    name = ptk.LodChain.level_name(inside[0].rsplit("|", 1)[-1], index)
                    moved = cmds.parent(inside[0], holder)[0]
                    cmds.rename(f"{holder}|{moved.rsplit('|', 1)[-1]}", name)
                    cmds.delete(container)
                else:
                    cmds.rename(container, ptk.LodChain.level_name(holder.rsplit("|", 1)[-1], index))
            conformed.append(holder)
        return conformed
