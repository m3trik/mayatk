# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError:
    pass
from typing import List, Tuple, Union
import pythontk as ptk
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils.naming._naming import Naming


class DuplicateGrid(ptk.LoggingMixin):
    # Output modes selectable from the slots' combobox. Every mode names the
    # result after the source object (``objects[0]``); none leaves the copies
    # loose in the scene.
    #   "combine"  — merge every copy into a single mesh.
    #   "instance" — instanced copies (shared shape), grouped.
    #   "copy"     — independent copies, grouped.
    MODES = ("combine", "instance", "copy")

    @classmethod
    def duplicate_grid(
        cls,
        objects: List[str],
        dimensions: Tuple[int, int, int],
        spacing: Union[float, Tuple[float, float, float]] = 0,
        mode: str = "instance",
    ) -> Union[str, List[str]]:
        """Duplicate objects in a grid pattern.

        The source objects are never reparented or otherwise mutated — only
        read (for the bounding box) and duplicated. This keeps the operation
        safe under the hermetic Preview: an earlier version grouped the
        originals into a temp node and flattened the copies through world,
        which vacated the originals' names, let a copy collide with them, and
        made Preview's path-based rollback delete the user's source object.

        Parameters:
            objects (List[str]): List of objects to duplicate.
            dimensions (Tuple[int, int, int]): Number of copies in x, y, z.
                Negative counts lay the grid out in the opposite direction.
            spacing (float | Tuple[float, float, float]): Extra spacing between
                copies (added to the bounding-box size on each axis). A single
                float applies uniformly to all three axes; a ``(sx, sy, sz)``
                tuple sets the gap per axis.
            mode (str): How the copies are produced — one of :attr:`MODES`.
                ``"combine"`` merges every copy into a single mesh; ``"instance"``
                makes instanced copies (shared shape); ``"copy"`` makes independent
                copies. All three name the output after the source object — the
                combined mesh directly, instance/copy under a grouping transform.

        Returns:
            Union[str, List[str]]: The container group (``"instance"`` /
            ``"copy"``) or a single-element list holding the combined mesh
            (``"combine"``).
        """
        if mode not in cls.MODES:
            raise ValueError(f"Invalid mode {mode!r}; expected one of {cls.MODES}.")

        x_count, y_count, z_count = dimensions
        # Accept a scalar (uniform) or a per-axis (sx, sy, sz) spacing. Keeping
        # the scalar form makes this backward-compatible with every existing
        # caller; the tuple form backs the per-axis Spacing X/Y/Z fields.
        space_x, space_y, space_z = cls._normalize_spacing(spacing)
        cls.logger.info(
            f"Duplicating grid: {dimensions}, spacing: "
            f"({space_x}, {space_y}, {space_z}), mode: {mode}"
        )

        if not objects:
            return []

        objects = cmds.ls(objects, long=True) or []

        # A zero dimension produces no volume — bail before any scene mutation
        # (also matches the documented early-out the slots rely on).
        if not (x_count and y_count and z_count):
            return []

        # Only "instance" shares shapes; "combine"/"copy" need real geometry.
        instance = mode == "instance"

        # Bounding box of the sources, read in place (no temp reparenting).
        bbox = cmds.exactWorldBoundingBox(objects)
        base_x, base_y, base_z = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
        step_x = (base_x + space_x) * (1 if x_count >= 0 else -1)
        step_y = (base_y + space_y) * (1 if y_count >= 0 else -1)
        step_z = (base_z + space_z) * (1 if z_count >= 0 else -1)

        # Prototype unit: a fresh duplicate of each source, grouped so the whole
        # unit replicates as one. The originals stay exactly where they are.
        #
        # The prototype is ALWAYS a real copy (never ``instanceLeaf``), even in
        # instance mode. Instancing the source here would make the copies share
        # the source's OWN shape, and reparenting those instances during
        # replication makes Maya renumber that shared shape — mutating the user's
        # node (violating this method's no-mutation contract) and, worse,
        # corrupting the hermetic preview's path-based rollback so it can delete
        # the shared shape and leave a husk of empty transforms. Instance mode
        # instead shares one fresh shape ACROSS THE COPIES via the instanced
        # replication below, leaving the source pristine.
        proto = cmds.group(em=True, name="temp_grid_proto")
        for obj in objects:
            dup = cmds.duplicate(obj, returnRootsOnly=True)[0]
            cmds.parent(dup, proto)

        # Replicate flat along each axis. Each axis costs O(count) command calls
        # (one duplicate + one batched reparent per step) — total O(X+Y+Z), not
        # O(X*Y*Z) — and the result stays flat, so there is no deep hierarchy to
        # walk and unwrap afterward (the old per-cell flatten was the slow path).
        row = cls._replicate_axis(
            proto, x_count, (step_x, 0, 0), instance, "temp_grid_row"
        )
        plane = cls._replicate_axis(
            row, y_count, (0, step_y, 0), instance, "temp_grid_plane"
        )
        volume = cls._replicate_axis(
            plane, z_count, (0, 0, step_z), instance, "temp_grid_volume"
        )

        final_objects = cmds.listRelatives(volume, children=True, fullPath=True) or []

        # Name every result after the source object (first of the selection).
        base = CoreUtils.short_name(objects[0])

        if mode == "combine":
            return cls._combine(final_objects, volume, base)

        # "instance" / "copy": keep the holder as the result group, named after
        # the source rather than a fixed "grid_duplicated_group".
        return cls._finalize_group(volume, base)

    @staticmethod
    def _normalize_spacing(
        spacing: Union[float, Tuple[float, float, float]],
    ) -> Tuple[float, float, float]:
        """Coerce a scalar or per-axis spacing into an ``(sx, sy, sz)`` tuple.

        A plain number applies uniformly to all three axes (the historical
        contract); any 3-element sequence is used verbatim so callers can pass
        per-axis gaps.
        """
        if isinstance(spacing, (int, float)):
            return float(spacing), float(spacing), float(spacing)
        sx, sy, sz = spacing
        return float(sx), float(sy), float(sz)

    @classmethod
    def _combine(cls, objects: List[str], holder: str, base: str) -> List[str]:
        """Merge the polygonal copies in ``objects`` into one mesh ``{base}_grid``.

        ``polyUnite`` consumes the copies' mesh shapes and emits one new
        transform; the now-empty source transforms remain inside ``holder``,
        which is then deleted. The combined mesh is lifted to world first so the
        holder delete can't take it with it.

        Only polygonal copies can be merged, and ``polyUnite`` demands *at least
        two* of them — it rejects a single (or zero) input with a cryptic
        "needs at least 2 polygonal objects". The selection can carry non-mesh
        objects (locators, curves, empty groups), so the raw copy count is the
        wrong gate; partition the copies by whether they hold real geometry:

          * ``>= 2`` meshes — ``polyUnite`` them (non-mesh copies are discarded
            with the holder, matching combine's one-mesh contract).
          * exactly ``1`` — lift-and-rename it (a 1x1x1 grid, or a lone mesh
            among non-mesh copies); ``polyUnite`` is skipped.
          * ``0`` — nothing to merge; fall back to grouping the copies so the
            grid still gets built, and warn instead of crashing.
        """
        poly = [o for o in objects if cls._is_polygonal(o)]

        if not poly:
            # No geometry to unite -- keep the copies grouped (like "copy" mode)
            # rather than handing polyUnite an empty set and crashing the op.
            cls.logger.warning(
                "Combine: selection has no polygon geometry; returning grouped copies."
            )
            return [cls._finalize_group(holder, base)]

        if len(poly) > 1:
            combined = cmds.polyUnite(poly, ch=False, mergeUVSets=True)[0]
        else:
            combined = poly[0]
        if cmds.listRelatives(combined, parent=True, fullPath=True):
            combined = cmds.parent(combined, world=True)[0]
        if cmds.objExists(holder):
            cmds.delete(holder)
        combined = cmds.rename(combined, Naming.generate_unique_name(f"{base}_grid"))
        DisplayUtils.add_to_isolation_set(combined)
        cls.logger.debug(f"Combined grid into: {combined}")
        return [combined]

    @staticmethod
    def _finalize_group(holder: str, base: str) -> str:
        """Rename ``holder`` to the source-derived ``{base}_grid_grp`` and register
        its children with the isolation set. Returns the renamed group. Shared by
        the ``instance``/``copy`` result and combine's no-geometry fallback."""
        result = cmds.rename(holder, Naming.generate_unique_name(f"{base}_grid_grp"))
        DisplayUtils.add_to_isolation_set(
            cmds.listRelatives(result, children=True, fullPath=True) or []
        )
        return result

    @staticmethod
    def _is_polygonal(obj: str) -> bool:
        """Return True if ``obj`` (itself or any descendant) holds a polygon mesh
        — i.e. something ``polyUnite`` can actually merge. Descends so a group of
        meshes counts, matching how ``polyUnite`` gathers its inputs."""
        # Descendant check first: it resolves the common mesh/group copy in one
        # call. Only genuine non-mesh copies (locators, curves) fall through to
        # the self-check, which catches a bare mesh *shape* passed directly.
        if cmds.listRelatives(obj, allDescendents=True, type="mesh", fullPath=True):
            return True
        return bool(cmds.ls(obj, type="mesh"))

    @staticmethod
    def _replicate_axis(
        src_grp: str,
        count: int,
        step_vec: Tuple[float, float, float],
        instance: bool,
        name: str,
    ) -> str:
        """Replicate ``src_grp``'s children ``abs(count)`` times along ``step_vec``
        into a new flat group, consuming (deleting) ``src_grp``.

        The offset copies are made first, while ``src_grp`` still holds the
        prototype; cell 0 is the prototype's own children, moved last. Reparenting
        preserves world position, so each step's translation is baked in and the
        offsets accumulate cleanly across the three axes.
        """
        dst = cmds.group(em=True, name=name)
        for i in range(1, abs(count)):
            dup = cmds.duplicate(src_grp, instanceLeaf=instance, returnRootsOnly=True)[
                0
            ]
            cmds.xform(
                dup,
                relative=True,
                worldSpace=True,
                translation=[c * i for c in step_vec],
            )
            kids = cmds.listRelatives(dup, children=True, fullPath=True) or []
            if kids:
                cmds.parent(kids, dst)
            cmds.delete(dup)
        kids0 = cmds.listRelatives(src_grp, children=True, fullPath=True) or []
        if kids0:
            cmds.parent(kids0, dst)
        cmds.delete(src_grp)
        return dst


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
