# !/usr/bin/python
# coding=utf-8
"""Dedicated UV shell-transform panel.

Provides :class:`ShellXformSlots` — the Switchboard slots class for the
``shell_xform.ui`` panel. It gathers every UV shell transform into one
polished, grouped window: the four move-to-UV-space arrows, Flip / Rotate,
the Straighten / Mirror / Distribute tools, plus Align / Orient shell
helpers.

The panel is co-located with its engine (:class:`mayatk.UvUtils`) and
discovered automatically by :class:`mayatk.ui_utils.MayaUiHandler`, so
``self.sb.handlers.marking_menu.show("shell_xform")`` works from anywhere
with no explicit registration (the tentacle UV panel's Transform group
exposes it via a ``More..`` button). Blender ships the mirror panel in
``blendertk.uv_utils.shell_xform`` at full parity (as of 2026-07-11): Align /
Orient / Gather / Randomize are realized via native ``bpy.ops.uv`` operators
and bmesh helpers (see ``tentacle/docs/parity_map.py``).
"""

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)

import pythontk as ptk
from uitk import IconManager

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.uv_utils._uv_utils import UvUtils


class ShellXformSlots(ptk.LoggingMixin):
    """Switchboard slots for the Shell Xform panel (``shell_xform.ui``).

    Composition over inheritance: the slots dispatch to :class:`mayatk.UvUtils`
    and Maya's native UV commands. Widget naming follows the cross-DCC parity
    convention — ``bNNN`` / ``tbNNN`` / ``sNNN`` are the ops Blender mirrors
    (move / flip / rotate / straighten / mirror / distribute); the Maya-only
    ops (align / orient / gather / randomize) use semantic names and have no
    Blender twin (see ``tentacle/docs/parity_map.py``).
    """

    # SVG arrow icon installed on each move-pad button (Rotate keeps its glyphs).
    _MOVE_ICONS = {
        "b023": "arrow_left",
        "b025": "arrow_up",
        "b024": "arrow_down",
        "b026": "arrow_right",
    }

    # Move-pad scope -> step in UV units, carried as the combo item's *data* so
    # the label and the step it means cannot drift apart. `None` = derived at
    # click time from the selection's own UV bounds.
    _MOVE_SCOPES = {
        "Tile": 1.0,
        "Half Tile": 0.5,
        "Quarter Tile": 0.25,
        "Selection Bounds": None,
    }

    # Snap modes for the move pad's option-box button, in cycle order (the
    # indices ARE the cycle positions, so `_snap_states` must list them in this
    # order). One tri-state button rather than two: the modes are mutually
    # exclusive answers to a single question — snap to what?
    _SNAP_OFF, _SNAP_GRID, _SNAP_SHELL = range(3)

    # A UV extent at or below this is treated as collapsed: dividing by it would
    # blow the grid math up, so the arrow falls back to a whole tile.
    _MIN_EXTENT = 1e-6

    # Map size the tile border padding derives from. The normalized margin is
    # map-size-invariant (``uv_tile_margin`` == 1/512 at every resolution), so
    # this only names the rule — the panel needs no map-size control.
    _MAP_SIZE = 4096

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shell_xform

        # The move-pad icons are installed on the next event-loop tick: the
        # switchboard builds this slots instance mid-load, so the child widgets
        # aren't wired onto self.ui until register_children runs after __init__.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self):
        """Install the move-pad arrow icons (deferred; see __init__)."""
        for name, icon in self._MOVE_ICONS.items():
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setText("")
                IconManager.set_icon(widget, icon, size=(16, 16))

    def header_init(self, widget):
        """Header menu — Open UV Editor + panel help."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.menu.add(
            "QPushButton",
            setText="Open UV Editor",
            setObjectName="open_uv_editor",
            setToolTip="Open Maya's UV Editor to inspect the result.",
        )
        widget.menu.open_uv_editor.clicked.connect(self.open_uv_editor)
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shell Xform",
                body="Move, flip, rotate, align, orient, and distribute the "
                "selected UV shells.",
                steps=[
                    "Select mesh(es), faces, or UVs.",
                    "<b>Move</b> nudges the selection by one <i>scope</i> — a "
                    "whole tile, a fraction of one, or the selection's own size.",
                    "The snap button beside the scope cycles three modes: "
                    "<b>grey ▦</b> off (offset by the scope, keeping any drift), "
                    "<b>blue ▦</b> snap to the scope's grid inset by the border "
                    "padding, and <b>amber ⌖</b> snap to the next <i>shell</i> — "
                    "park against the nearest neighbour in that direction, "
                    "skipping gaps too small to fit and falling back to the grid "
                    "when nothing lies ahead.",
                    "<b>Gather to Tile</b> moves shells sitting outside the "
                    "selection's UDIM tile into it — whole-tile offsets keep "
                    "each shell's sub-tile position (no repack).",
                    "<b>Flip / Rotate</b> mirrors or spins the UVs about their "
                    "center (rotation amount = the angle field).",
                    "<b>Straighten / Mirror / Distribute</b> each expose their "
                    "own options in the option box (▸).",
                ],
                sections=[
                    (
                        "Align / Orient",
                        [
                            "<b>Align</b> snaps the selection's U or V to its min / "
                            "center / max, or spreads them along a line.",
                            "<b>Orient Shells</b> squares each shell to the nearest "
                            "axis; <b>To Edges</b> orients to a selected edge.",
                        ],
                    ),
                ],
            )
        )

    def _selection_or_warn(
        self,
        message="<b>Nothing selected.</b><br>The operation requires at least one selected object.",
    ):
        """Current selection, or an empty list (after showing *message*) when nothing
        is selected. Shared selection guard for the op slots — mirrors the Blender
        twin's ``_mesh_selection`` so the check lives in one place.
        """
        selection = cmds.ls(sl=True) or []
        if not selection:
            self.sb.message_box(message)
        return selection

    # ------------------------------------------------------------------ move to UV space (b023-b026)
    def cmb_move_scope_init(self, widget):
        """Move scope — how far one arrow press travels, plus the snap button.

        Items are built from ``_MOVE_SCOPES`` with the step as item data, so the
        step is read straight off the current item — a label edited in one place
        can no longer mean a different distance somewhere else. Snap rides along
        as an option-box button rather than extra items, because the mode
        composes with every scope instead of replacing one.
        """
        if widget.is_initialized:
            return
        widget.add(self._MOVE_SCOPES)
        # The ActionOption owns its own index persistence, so the restored mode
        # is whatever the user left it on. A fresh key: the old one held a bool
        # and would restore as a state index.
        self._snap_action = widget.option_box.set_action(
            states=self._snap_states(),
            settings_key="shell_xform_move_snap_mode",
        )

    def _snap_states(self):
        """Option-box cycle states for the snap button, in `_SNAP_*` order.

        Icon *and* tint change per state: two enabled modes are easy to confuse
        by colour alone, and the off state has to read as inert at a glance.
        Colours come from the shared status palette so they track the theme.
        """
        status = ptk.Palette.status()
        return [
            {
                "icon": "grid",
                "color": status["locked"][0],
                "tooltip": "Snap: off. Arrows offset by the scope, keeping any "
                "sub-tile drift. Click to snap to the grid.",
            },
            {
                "icon": "grid",
                "color": status["info"][0],
                "tooltip": "Snap: grid. Arrows land the selection on the scope's "
                "grid, inset by the tile border padding. Click to snap to shells.",
            },
            {
                "icon": "target",
                "color": status["warn"][0],
                "tooltip": "Snap: shell. Arrows park the selection against the "
                "next shell in that direction, keeping the border padding, and "
                "skip gaps too small to fit. Falls back to the grid when nothing "
                "lies ahead. Click to turn snapping off.",
            },
        ]

    def _snap_mode(self) -> int:
        """Current snap mode — `_SNAP_OFF`, `_SNAP_GRID`, or `_SNAP_SHELL`."""
        action = getattr(self, "_snap_action", None)
        return self._SNAP_OFF if action is None else int(action.current_state)

    def _move_step(self, bounds) -> tuple:
        """Per-axis ``(step_u, step_v)`` for the current scope.

        The step comes from the current item's data; ``None`` means "derive it
        from the selection", which is what *bounds* — a ``(u_min, v_min, u_max,
        v_max)`` tuple — is for. A degenerate extent (a shell collapsed on one
        axis) falls back to a whole tile so the arrow still does something.
        """
        step = self.ui.cmb_move_scope.currentData()
        if step is not None:
            return (step, step)

        u_min, v_min, u_max, v_max = bounds
        width, height = u_max - u_min, v_max - v_min
        return (
            width if width > self._MIN_EXTENT else 1.0,
            height if height > self._MIN_EXTENT else 1.0,
        )

    def _move(self, du: int, dv: int):
        """Nudge the selected UVs one step along ``(du, dv)``.

        The snap mode picks the rule: ``_SNAP_OFF`` offsets by the scope,
        ``_SNAP_GRID`` lands on the scope's padded grid, and ``_SNAP_SHELL``
        parks against the next neighbouring shell. Shell snap ignores the scope
        entirely — the neighbour sets the distance — and degrades to the grid
        rule whenever nothing lies ahead, so the arrow never reads as dead.
        """
        selection = self._selection_or_warn(
            "<b>Nothing selected.</b><br>Select a mesh, faces, or UVs to move."
        )
        if not selection:
            return

        bounds = UvUtils.get_uv_bounds(selection)
        if bounds is None:
            self.sb.message_box(
                "<b>No UVs found.</b><br>Select a mesh, faces, edges, or UVs."
            )
            return

        mode = self._snap_mode()
        snap = mode != self._SNAP_OFF
        margin = self._border_margin() if snap else 0.0

        offset_u = offset_v = None
        if mode == self._SNAP_SHELL:
            blockers = UvUtils.get_neighbor_shell_bounds(selection)
            # Only the travelled axis can resolve — the other's direction is 0,
            # which `next_clear_offset` reports as None.
            offset_u = ptk.MathUtils.next_clear_offset(
                bounds, blockers, 0, du, margin=margin
            )
            offset_v = ptk.MathUtils.next_clear_offset(
                bounds, blockers, 1, dv, margin=margin
            )

        if offset_u is None and offset_v is None:
            # Snap anchors on the selection's lower-left corner, so "up" means the
            # shell's bottom edge lands on the next grid line — what the eye expects.
            # The grid is offset by the tile border padding, so a snapped shell sits
            # just inside the line rather than on it (a shell exactly on a tile seam
            # bleeds across it at render time). Snapping the *unpadded* anchor and
            # adding the margin back keeps the grid uniform in both directions —
            # padding the result instead would strand the reverse press on the
            # margin it just added, and the arrow would read as dead.
            step_u, step_v = self._move_step(bounds)
            offset_u = ptk.MathUtils.step_offset(
                bounds[0] - margin, step_u, du, snap=snap
            )
            offset_v = ptk.MathUtils.step_offset(
                bounds[1] - margin, step_v, dv, snap=snap
            )

        UvUtils.move_to_uv_space(selection, offset_u or 0.0, offset_v or 0.0)

    def _border_margin(self) -> float:
        """Normalized tile border the snap keeps clear.

        Gather derives the same margin inside the engine from the same
        ``_MAP_SIZE``, so both routes inset by an identical amount.
        """
        return ptk.MathUtils.uv_tile_margin(self._MAP_SIZE)

    def b023(self):
        """Move To UV Space: Left"""
        self._move(-1, 0)

    def b024(self):
        """Move To UV Space: Down"""
        self._move(0, -1)

    def b025(self):
        """Move To UV Space: Up"""
        self._move(0, 1)

    def b026(self):
        """Move To UV Space: Right"""
        self._move(1, 0)

    def gather_to_udim(self):
        """Move shells sitting outside the selection's UDIM tile into it.

        The cheap counterpart to a repack: each stray shell keeps its
        sub-tile position, inset by the same border padding the snap uses.
        The target tile is the one most of the selection's shells already
        occupy, so the majority stays put.
        """
        selection = self._selection_or_warn(
            "<b>Nothing selected.</b><br>Select mesh(es), faces, or UVs to gather."
        )
        if not selection:
            return

        moved = UvUtils.gather_to_udim(selection, map_size=self._MAP_SIZE)
        if moved is None:
            self.sb.message_box(
                "<b>No UVs found.</b><br>Select a mesh, faces, edges, or UVs."
            )
        elif not moved:
            self.sb.message_box(
                "<b>Nothing to gather.</b><br>Every shell is in the tile."
            )

    # ------------------------------------------------------------------ flip / rotate (b034-b037)
    def _flip_uvs(self, axis):
        """Geometrically flip the selected UVs across *axis* ('u'/'v'), each shell
        about its own center. Dispatches to the canonical, undoable
        ``UvUtils.mirror_uvs`` (matches Maya's local ``polyFlipUV`` — keeps bounds).
        """
        selection = self._selection_or_warn(
            "<b>Nothing selected.</b><br>Select a mesh, faces, or UVs to flip."
        )
        if selection:
            UvUtils.mirror_uvs(
                selection, axis=axis, per_shell=True, preserve_position=False
            )

    def b034(self):
        """Flip U: mirror the selected UVs horizontally about each shell's center."""
        self._flip_uvs("u")

    def b035(self):
        """Flip V: mirror the selected UVs vertically about each shell's center."""
        self._flip_uvs("v")

    @CoreUtils.undoable
    def _rotate_uvs(self, angle):
        """Rotate the selected UVs by *angle* degrees about their shared centroid.

        The per-UV ``polyEditUV`` loop is wrapped in a single undo chunk so one
        Ctrl+Z reverts the whole rotation (not one UV at a time).
        """
        selected_objects = self._selection_or_warn()
        if not selected_objects:
            return

        selected_uvs = cmds.polyListComponentConversion(selected_objects, toUV=True)
        selected_uvs = cmds.ls(selected_uvs, flatten=True) or []
        if not selected_uvs:
            self.sb.message_box(
                "<b>No UVs found.</b><br>Select a mesh, faces, edges, or UVs."
            )
            return

        all_u, all_v = [], []
        for uv in selected_uvs:
            u, v = cmds.polyEditUV(uv, query=True, uValue=True, vValue=True)
            all_u.append(u)
            all_v.append(v)

        pivot_u = sum(all_u) / len(all_u)
        pivot_v = sum(all_v) / len(all_v)

        for uv in selected_uvs:
            cmds.polyEditUV(
                uv, pivotU=pivot_u, pivotV=pivot_v, angle=angle, relative=True
            )

    def b036(self):
        """Rotate the selected UVs counter-clockwise by the s041 angle."""
        self._rotate_uvs(self.ui.s041.value())

    def b037(self):
        """Rotate the selected UVs clockwise by the s041 angle."""
        self._rotate_uvs(-self.ui.s041.value())

    def s041(self, value, widget):
        """Rotate Angle — passive input; read by the Rotate buttons (b036/b037). Nothing to do."""

    # ------------------------------------------------------------------ tb005  Straighten
    def tb005_init(self, widget):
        """Initialize Straighten UV"""
        widget.option_box.menu.setTitle("Straighten")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Angle: ",
            setObjectName="s001",
            set_limits=[0, 360],
            setValue=30,
            setToolTip="Set the maximum angle used for straightening uv's.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten UV",
            setObjectName="chk018",
            setChecked=True,
            setToolTip="Unfold UV's along a horizonal contraint.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten V",
            setObjectName="chk019",
            setChecked=True,
            setToolTip="Unfold UV's along a vertical constaint.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten Shell",
            setObjectName="chk020",
            setToolTip="Straighten a UV shell by unfolding UV's around a selected UV's edgeloop.",
        )

    @CoreUtils.undoable
    def tb005(self, widget):
        """Straighten UV

        Chunked: ``texStraightenUVs`` walks the shells one at a time, moving UVs
        per edge loop, and ``texStraightenShell`` is a second op on top — so
        without the chunk one press costs many presses of Ctrl+Z to revert.
        """
        u = widget.option_box.menu.chk018.isChecked()
        v = widget.option_box.menu.chk019.isChecked()
        angle = widget.option_box.menu.s001.value()
        straighten_shell = widget.option_box.menu.chk020.isChecked()

        if u and v:
            mel.eval(f'texStraightenUVs "UV" {angle}')
        elif u:
            mel.eval(f'texStraightenUVs "U" {angle}')
        elif v:
            mel.eval(f'texStraightenUVs "V" {angle}')

        if straighten_shell:
            mel.eval("texStraightenShell")

    # ------------------------------------------------------------------ tb006  Distribute
    def tb006_init(self, widget):
        """Initialize Distribute"""
        widget.option_box.menu.setTitle("Distribute")
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Distribute U",
            setObjectName="chk023",
            setChecked=True,
            setToolTip="Distribute along U, leaving the panel's tile border "
            "padding between adjacent shells.",
        )
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Distribute V",
            setObjectName="chk024",
            setToolTip="Distribute along V, leaving the panel's tile border "
            "padding between adjacent shells.",
        )

    @CoreUtils.undoable
    def tb006(self, widget):
        """Distribute: evenly space the selected UV shells horizontally or vertically.

        The gap left between adjacent shells is the panel's tile spacing
        (:meth:`_border_margin`) — the same clearance the move pad's shell snap
        parks against and Gather insets by; without it the shells butt edge to
        edge and the row bleeds across its own seams at render time. Chunked
        because ``texDistributeShells`` moves one shell per command.
        """
        u = widget.option_box.menu.chk023.isChecked()
        v = widget.option_box.menu.chk024.isChecked()
        # Fixed notation, not repr: MEL parses the literal, and a small enough
        # float would reach it as `1.9e-06`.
        spacing = f"{self._border_margin():.9f}"

        if u:
            mel.eval(f'texDistributeShells 0 {spacing} "right" {{}}')  # 'left', 'right'
        if v:
            mel.eval(f'texDistributeShells 0 {spacing} "down" {{}}')  # 'up', 'down'

    # ------------------------------------------------------------------ tb008  Mirror
    def tb008_init(self, widget):
        """Initialize Mirror UVs.

        Mirrors UVs across U or V. By default this uses the footprint-preserving
        reassignment mode (preserve_position=True), which keeps the UV point set
        unchanged and only reassigns which UV gets which point.
        """
        widget.option_box.menu.setTitle("Mirror UVs")
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Mirror U",
            setObjectName="chk031",
            setChecked=True,
            setToolTip="Mirror across U. Default mode preserves the UV footprint.",
        )
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Mirror V",
            setObjectName="chk032",
            setToolTip="Mirror across V. Default mode preserves the UV footprint.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Per Shell",
            setObjectName="chk033",
            setChecked=True,
            setToolTip="If enabled, mirrors each UV shell independently.",
        )
        # Preserve Footprint vs Geometric Mirror are two distinct algorithms, not a
        # modifier — a combobox names both states.
        mode = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_mirror_mode",
            setToolTip="Preserve Footprint: keeps the exact UV point set via one-to-one reassignment.\nGeometric Mirror: reflects the UVs around the pivot.",
        )
        mode.addItems(["Preserve Footprint", "Geometric Mirror"])
        mode.setCurrentText(
            "Preserve Footprint"
        )  # preserve prior default (checkbox on)

    @CoreUtils.undoable
    def tb008(self, widget):
        """Mirror UVs (footprint-preserving by default)."""
        mirror_u = widget.option_box.menu.chk031.isChecked()
        mirror_v = widget.option_box.menu.chk032.isChecked()
        per_shell = widget.option_box.menu.chk033.isChecked()
        preserve_position = (
            widget.option_box.menu.cmb_mirror_mode.currentText() == "Preserve Footprint"
        )

        axis = "u" if mirror_u and not mirror_v else "v"

        selection = self._selection_or_warn()
        if not selection:
            return

        UvUtils.mirror_uvs(
            selection,
            axis=axis,
            per_shell=per_shell,
            preserve_position=preserve_position,
        )

    # ------------------------------------------------------------------ Align
    def align_u_min(self):
        """Align the selected UVs to their minimum U (left)."""
        mel.eval('performAlignUV "minU"')

    def align_u_avg(self):
        """Align the selected UVs to their average U (center)."""
        mel.eval('performAlignUV "avgU"')

    def align_u_max(self):
        """Align the selected UVs to their maximum U (right)."""
        mel.eval('performAlignUV "maxU"')

    def align_v_min(self):
        """Align the selected UVs to their minimum V (bottom)."""
        mel.eval('performAlignUV "minV"')

    def align_v_avg(self):
        """Align the selected UVs to their average V (center)."""
        mel.eval('performAlignUV "avgV"')

    def align_v_max(self):
        """Align the selected UVs to their maximum V (top)."""
        mel.eval('performAlignUV "maxV"')

    def linear_align(self):
        """Linearly align the selected UVs between their two end points."""
        mel.eval("performLinearAlignUV")

    # ------------------------------------------------------------------ Orient
    def orient_shells(self):
        """Orient each shell to run parallel with its nearest U/V axis."""
        objects = self._selection_or_warn(
            "<b>Nothing selected.</b><br>Select mesh(es) or UVs to orient."
        )
        if objects:
            UvUtils.orient_shells(objects)

    def orient_edges(self):
        """Orient the shell so its selected edge runs along U or V."""
        # texOrientEdge rotates each shell so its selected edge runs along
        # U or V. Requires a mesh/UV edge selection (mask 32 = poly edges).
        edges = cmds.filterExpand(cmds.ls(sl=True) or [], selectionMask=32)
        if not edges:
            self.sb.message_box(
                "<b>No edge selected.</b><br>Select a UV/mesh edge to orient the shell to."
            )
            return
        mel.eval("texOrientEdge")

    def gather_shells(self):
        """Gather the selected shells together toward the 0-1 UV space."""
        mel.eval("UVGatherShells")

    def randomize_shells(self):
        """Randomly offset the selected shells."""
        mel.eval("RandomizeShells")

    # ------------------------------------------------------------------ header
    def open_uv_editor(self):
        """Open Maya's UV Editor (TextureViewWindow)."""
        mel.eval("TextureViewWindow")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("shell_xform", reload=True)
    ui.show(pos="screen", app_exec=True)
