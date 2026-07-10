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
``blendertk.uv_utils.shell_xform`` (a subset — the Maya-only align /
orient ops have no bpy analogue).
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
from uitk.widgets.mixins.tooltip_mixin import fmt

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
            fmt(
                title="Shell Xform",
                body="Move, flip, rotate, align, orient, and distribute the "
                "selected UV shells.",
                steps=[
                    "Select mesh(es), faces, or UVs.",
                    "<b>Move</b> nudges the selection by one whole UV tile.",
                    "<b>Flip / Rotate</b> mirrors or spins the UVs about their "
                    "center (rotation amount = the angle field).",
                    "<b>Straighten / Mirror / Distribute</b> each expose their "
                    "own options in the option box (▸).",
                ],
                sections=[
                    ("Align / Orient", [
                        "<b>Align</b> snaps the selection's U or V to its min / "
                        "center / max, or spreads them along a line.",
                        "<b>Orient Shells</b> squares each shell to the nearest "
                        "axis; <b>To Edges</b> orients to a selected edge.",
                    ]),
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
    def b023(self):
        """Move To UV Space: Left"""
        UvUtils.move_to_uv_space(cmds.ls(sl=True) or [], -1, 0)

    def b024(self):
        """Move To UV Space: Down"""
        UvUtils.move_to_uv_space(cmds.ls(sl=True) or [], 0, -1)

    def b025(self):
        """Move To UV Space: Up"""
        UvUtils.move_to_uv_space(cmds.ls(sl=True) or [], 0, 1)

    def b026(self):
        """Move To UV Space: Right"""
        UvUtils.move_to_uv_space(cmds.ls(sl=True) or [], 1, 0)

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

    def tb005(self, widget):
        """Straighten UV"""
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
            setToolTip="Distribute along U.",
        )
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Distribute V",
            setObjectName="chk024",
            setToolTip="Distribute along V.",
        )

    def tb006(self, widget):
        """Distribute: evenly space the selected UV shells horizontally or vertically."""
        u = widget.option_box.menu.chk023.isChecked()
        v = widget.option_box.menu.chk024.isChecked()

        if u:
            mel.eval('texDistributeShells 0 0 "right" {}')  # 'left', 'right'
        if v:
            mel.eval('texDistributeShells 0 0 "down" {}')  # 'up', 'down'

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
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Preserve Footprint",
            setObjectName="chk034",
            setChecked=True,
            setToolTip="If enabled, preserves the exact UV point set using one-to-one reassignment.\nIf disabled, performs a geometric mirror around the pivot.",
        )

    @CoreUtils.undoable
    def tb008(self, widget):
        """Mirror UVs (footprint-preserving by default)."""
        mirror_u = widget.option_box.menu.chk031.isChecked()
        mirror_v = widget.option_box.menu.chk032.isChecked()
        per_shell = widget.option_box.menu.chk033.isChecked()
        preserve_position = widget.option_box.menu.chk034.isChecked()

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
