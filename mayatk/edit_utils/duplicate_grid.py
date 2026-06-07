# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
from typing import List, Tuple, Union
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils.preview import Preview
from mayatk.core_utils._core_utils import short_name
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
        spacing: float = 0,
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
            spacing (float): Extra spacing between copies (added to bounding box).
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
        cls.logger.info(
            f"Duplicating grid: {dimensions}, spacing: {spacing}, mode: {mode}"
        )

        if not objects:
            return []

        # A zero dimension produces no volume — bail before any scene mutation
        # (also matches the documented early-out the slots rely on).
        if not (x_count and y_count and z_count):
            return []

        # Only "instance" shares shapes; "combine"/"copy" need real geometry.
        instance = mode == "instance"

        # Bounding box of the sources, read in place (no temp reparenting).
        bbox = cmds.exactWorldBoundingBox(objects)
        base_x, base_y, base_z = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
        step_x = (base_x + spacing) * (1 if x_count >= 0 else -1)
        step_y = (base_y + spacing) * (1 if y_count >= 0 else -1)
        step_z = (base_z + spacing) * (1 if z_count >= 0 else -1)

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
        row = cls._replicate_axis(proto, x_count, (step_x, 0, 0), instance, "temp_grid_row")
        plane = cls._replicate_axis(row, y_count, (0, step_y, 0), instance, "temp_grid_plane")
        volume = cls._replicate_axis(plane, z_count, (0, 0, step_z), instance, "temp_grid_volume")

        final_objects = cmds.listRelatives(volume, children=True, fullPath=True) or []

        # Name every result after the source object (first of the selection).
        base = short_name(objects[0])

        if mode == "combine":
            return cls._combine(final_objects, volume, base)

        # "instance" / "copy": keep the holder as the result group, named after
        # the source rather than a fixed "grid_duplicated_group".
        result = cmds.rename(volume, Naming.generate_unique_name(f"{base}_grid_grp"))
        DisplayUtils.add_to_isolation_set(
            cmds.listRelatives(result, children=True, fullPath=True) or []
        )
        return result

    @classmethod
    def _combine(cls, objects: List[str], holder: str, base: str) -> List[str]:
        """Merge ``objects`` into a single mesh named ``{base}_grid``.

        ``polyUnite`` consumes the copies' shapes and emits one new transform;
        the now-empty source transforms remain inside ``holder``, which is then
        deleted. The combined mesh is lifted to world first so the holder delete
        can't take it with it. A lone copy (e.g. a 1x1x1 grid) skips polyUnite —
        which rejects a single input — and is just lifted out and renamed.
        """
        if len(objects) > 1:
            combined = cmds.polyUnite(objects, ch=False, mergeUVSets=True)[0]
        else:
            combined = objects[0]
        if cmds.listRelatives(combined, parent=True, fullPath=True):
            combined = cmds.parent(combined, world=True)[0]
        if cmds.objExists(holder):
            cmds.delete(holder)
        combined = cmds.rename(combined, Naming.generate_unique_name(f"{base}_grid"))
        DisplayUtils.add_to_isolation_set(combined)
        cls.logger.debug(f"Combined grid into: {combined}")
        return [combined]

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
            dup = cmds.duplicate(src_grp, instanceLeaf=instance, returnRootsOnly=True)[0]
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


class DuplicateGridSlots(ptk.LoggingMixin):
    # Defensive backstop, in parity with DuplicateLinear/Radial. duplicate_grid
    # neither deletes nor mutates the source (instance mode now shares a fresh
    # shape across the copies, not the source's own — see duplicate_grid), so the
    # rollback's node-diff already restores cleanly. MUTATES_SELECTION=True keeps
    # the Preview duplicate+hide UUID-restore backstop anyway, as cheap insurance
    # against the source being touched on the (headlessly-untestable) interactive
    # path.
    MUTATES_SELECTION = True

    # Total duplicates above which we ask the user to confirm before building.
    BULK_THRESHOLD = 1000

    def __init__(self, switchboard, log_level="INFO"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_grid

        # Initialize Logger
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[Duplicate Grid] ")

        # Largest grid count the user has approved this session (so a confirmed
        # bulk build doesn't re-prompt on every preview refresh).
        self._confirmed_count = 0

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
        )

        self.sb.connect_multi(
            self.ui,
            "s000-3",
            "valueChanged",
            self.preview.refresh,
        )

        # Output mode: how the copies are produced. Every option names the result
        # after the source object; combine yields one mesh, the others a group.
        self.ui.cmb000.add(
            [
                ("Combine", "combine"),
                ("Instance", "instance"),
                ("Unique", "copy"),
            ],
            prefix="Output:",
        )
        self.ui.cmb000.setAsCurrent("instance")
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Duplicate Grid",
                body="Duplicate selected objects into a 3D grid layout.",
                steps=[
                    "Select one or more transforms.",
                    "Set per-axis counts <b>X</b> / <b>Y</b> / <b>Z</b> and a "
                    "uniform <b>Spacing</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Duplicate</b> "
                    "to commit.",
                ],
                sections=[
                    ("Output", [
                        "<b>Combine</b> — merge every copy into a single mesh "
                        "named after the source.",
                        "<b>Instance</b> — instanced copies that share one shape "
                        "(cheaper; editing any copy updates the rest), grouped "
                        "and named after the source.",
                        "<b>Unique</b> — independent copies, grouped and named "
                        "after the source.",
                    ]),
                ],
                notes=[
                    "Counts can be negative to lay the grid out in the opposite "
                    "direction. Very large grids prompt for confirmation first.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()
        self._confirmed_count = 0

    def perform_operation(self, objects, contract):
        dimensions = (
            self.ui.s000.value(),
            self.ui.s001.value(),
            self.ui.s002.value(),
        )
        spacing = self.ui.s003.value()
        mode = self.ui.cmb000.currentData()

        if not self._confirm_bulk(dimensions, objects, contract):
            self.copies = []
            return

        self.copies = DuplicateGrid.duplicate_grid(
            objects,
            dimensions,
            spacing,
            mode,
        )

    def _confirm_bulk(self, dimensions, objects, contract) -> bool:
        """Gate large builds behind a confirmation dialog.

        Returns True to proceed. The approved magnitude is cached so a confirmed
        bulk build doesn't re-prompt on every preview refresh; on decline during
        a live preview the preview is switched off (deferred — we're inside a
        refresh critical section) so dragging the count up doesn't re-prompt on
        every tick.
        """
        x, y, z = dimensions
        total = abs(x) * abs(y) * abs(z) * max(len(objects), 1)
        if total <= self.BULK_THRESHOLD or total <= self._confirmed_count:
            return True

        proceed = (
            self.sb.message_box(
                f"This will create <b>{total:,}</b> objects, which may be slow.<br>"
                "Continue?",
                "Yes",
                "No",
            )
            == "Yes"
        )
        if proceed:
            self._confirmed_count = total
            return True

        # Declined: stop the preview so the refresh storm (and re-prompting)
        # ends. Deferred because disable() no-ops while a refresh is in flight.
        if contract is not None:
            try:
                cmds.evalDeferred(self.preview.disable)
            except Exception:
                pass
        return False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
