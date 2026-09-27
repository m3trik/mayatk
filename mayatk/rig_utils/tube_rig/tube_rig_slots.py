# !/usr/bin/python
# coding=utf-8
"""Tube Rig panel — the Switchboard slots for ``tube_rig.ui``.

Thin event handlers over :class:`TubeRig` / :class:`TubePath`, plus the rig
mode table (:data:`RIG_MODES`) that drives which options each mode exposes.
"""

from typing import Callable, List, Optional
from dataclasses import dataclass

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.rig_utils._rig_utils import RigUtils

# TubePath (tube_path.py) is pure geometry, no scene objects.
from mayatk.rig_utils.tube_rig.tube_path import TubePath
from mayatk.rig_utils.tube_rig._tube_rig import TubeRig, _TubeRigInternal


# ======================================================================
# Rig Configuration (UI Logic)
# ======================================================================


@dataclass
class RigModeConfig:
    """Defines a rig mode's strategy and available options."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # UI State (Default: Editable)
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


# Rig Mode Registry
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=True,  # hoses bow on compression, not accordion
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=False,  # AnchorStrategy implements no volume system
        squash_editable=False,  # ...nor squash
        auto_bend_editable=False,  # ...nor a mid control to bend
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        # A handful of controls, each spreading its rotation across the
        # joints it owns. One control per joint is technically FK and
        # practically unanimatable: an Auto build puts a joint on every edge
        # loop, so a single curve costs twenty-odd keys in lockstep and one
        # missed control corners the tube.
        num_controls=5,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
    ),
]


# ======================================================================
# UI Slots (thin event handlers — delegates to TubeRig / TubePath)
# ======================================================================


class TubeRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (tube_rig.ui)
        self.ui = self.sb.loaded_ui.tube_rig

        # Configure SpinBox custom display
        # -1 indicates "Auto": joint count derived from edge loops; joint size
        # derived from the measured tube radius.
        self.ui.s000.setCustomDisplayValues(-1, "Auto")
        self.ui.s002.setCustomDisplayValues(-1, "Auto")

        # Populate the mode combobox. The mode names are self-describing under the
        # "Global Options" group, so the combo carries no extra label: the old
        # setTextOverlay("Mode:") floated a translucent QLabel ON TOP of the item
        # text, so the two overlapped into an unreadable smear (reported bug) —
        # removed. (A display-only prefix can't stand in: QStyleSheetStyle paints a
        # themed combo's label from its own currentText, ignoring such adornments.)
        self.ui.cmb_preset.clear()
        for mode in RIG_MODES:
            self.ui.cmb_preset.addItem(mode.name, mode)

        self.ui.cmb_preset.currentIndexChanged.connect(self.apply_mode)
        # Apply initial mode
        if len(RIG_MODES) > 0:
            self.apply_mode(0)

        # Auto-Bend needs the 3-control spline layout — keep the checkbox
        # gated as the control count changes (apply_mode syncs it per mode).
        self.ui.s001.valueChanged.connect(self._sync_auto_bend_gate)

        self._init_tooltips()

        # Keep the window tall enough for the selected step. QToolBox wraps each
        # page in a QScrollArea whose minimum under-reports its content height, so
        # the window's show-time fit (which targets minimumSizeHint) leaves a taller
        # step (e.g. Step 2) clipped behind a scrollbar. Re-fit height on page change
        # to sizeHint, which DOES reflect the current page.
        self.ui.toolbox_steps.currentChanged.connect(self._fit_window_to_step)

    def _fit_window_to_step(self, *_) -> None:
        """Fit the window's height to the newly-selected toolbox page.

        Wired to ``toolbox_steps.currentChanged``. QToolBox scroll areas
        under-report their minimum height, so switching to a taller step would
        otherwise clip the page behind a scrollbar. Resize the height to
        ``sizeHint`` — which reflects the current page, unlike the
        ``minimumSizeHint`` the show-time ``fit_height_to_content`` targets —
        while preserving width, matching the window's own height-only resize
        helpers so a user-widened panel keeps its width (plain ``adjustSize``
        would snap it back). Deferred one event-loop tick so the page-switch
        layout has settled before the window re-measures.
        """
        win = self.ui.window()
        self.sb.QtCore.QTimer.singleShot(
            0, lambda: win.resize(win.width(), win.sizeHint().height())
        )

    def txt000_init(self, widget):
        """Rig-name field — optional, so clearing back to auto-naming is a state."""
        widget.option_box.clear_option = True

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Tube Rig",
                body="Generate joint rigs along tube-shaped meshes. The tool "
                "auto-detects the tube's centerline via edge loops or surface "
                "normals, and sizes controls to the measured tube radius.",
                sections=[
                    (
                        "Quick start (One-Click)",
                        [
                            "Select a tube mesh.",
                            "Pick a <b>Mode</b> preset — irrelevant options "
                            "disable per mode.",
                            "Press <b>Full Rig</b> — it runs Steps 1 → 2 → 3 "
                            "with the parameter values set in each step page.",
                        ],
                    ),
                    (
                        "Step-by-step",
                        [
                            "<b>Step 1</b> creates the joints, <b>Step 2</b> "
                            "the controls, <b>Step 3</b> the skin bind — the "
                            "same operations One-Click runs, one at a time.",
                            "Each step's tooltip states exactly what to "
                            "select, and in what order.",
                        ],
                    ),
                    (
                        "Utility",
                        [
                            "<b>Add End Constraints</b> pins the tube ends to "
                            "anchor objects.",
                            "<b>Remove Rig</b> / <b>Rename Rig</b> act on "
                            "whatever rig the selection touches — also on rigs "
                            "built in an earlier session.",
                        ],
                    ),
                ],
                notes=[
                    "Every button is a <b>single undo</b> — one <b>Ctrl+Z</b> "
                    "reverts a whole build, bind or removal, batch runs "
                    "included. The viewport is held still while one runs; the "
                    "footer shows what it is doing.",
                    "<b>Joints = Auto</b> reads the tube's edge loops and "
                    "places one joint per loop.",
                    "Select several tubes to rig them in one go; a typed "
                    "name is suffixed <b>_01</b>, <b>_02</b>, …",
                    "Re-running a step replaces that step's previous result.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and step."""
        ui = self.ui

        ui.cmb_preset.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Mode",
                body="Selects the build strategy and presets the step "
                "parameters below. Options a mode doesn't support are "
                "disabled while it is active.",
                sections=[
                    (
                        "Spline (Hose/Cable)",
                        [
                            "IK-spline chain with start / mid / end controls.",
                            "Good for: hoses, cables, organic tubes.",
                            "Supports: stretch, squash, volume, twist, auto-bend.",
                        ],
                    ),
                    (
                        "Anchor (Piston)",
                        [
                            "Two independent end joints with distance-driven stretch.",
                            "Good for: pistons, hydraulics, struts.",
                            "Always exactly 2 joints; stretch only.",
                        ],
                    ),
                    (
                        "FK Chain (Tail/Tentacle)",
                        [
                            "Nested FK controls, one per joint.",
                            "Good for: tails, tentacles, hand-keyed tubes.",
                        ],
                    ),
                ],
            )
        )
        ui.txt000.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Name",
                body="Base name for every node the rig creates (group, "
                "joints, controls, skinCluster).",
                notes=["Empty = derived from the mesh name."],
            )
        )
        ui.s000.setToolTip(
            self.sb.tooltip.fmt(
                title="Number of Joints",
                body="Joint count along the tube centerline.",
                rows=[
                    ("Auto", "one joint per edge loop (most precise)"),
                    ("N", "evenly resampled along the centerline"),
                ],
                notes=["Anchor rigs always use exactly 2 end joints."],
            )
        )
        ui.s001.setToolTip(
            self.sb.tooltip.fmt(
                title="Number of Controls",
                body="Driver control count for the Spline rig. Increase for "
                "complex shapes.",
                notes=[
                    "<b>Auto-Bend</b> requires exactly 3 controls.",
                    "FK rigs create one control per joint instead.",
                ],
            )
        )
        ui.s002.setToolTip(
            self.sb.tooltip.fmt(
                title="Joint Size",
                body="Joint display radius in the viewport.",
                rows=[("Auto", "half the measured tube radius")],
                notes=[
                    "Display only — control sizes always scale to the "
                    "measured tube radius."
                ],
            )
        )
        ui.chk000.setToolTip(
            self.sb.tooltip.fmt(
                title="Reverse Direction",
                body="Builds the joint chain from the far end (swaps start/end).",
                notes=["Applies to Step 1 and the One-Click build."],
            )
        )
        ui.chk_stretch.setToolTip(
            self.sb.tooltip.fmt(
                title="Stretch",
                bullets=[
                    "<b>Spline:</b> joints scale along the tube to follow "
                    "the curve length.",
                    "<b>Anchor:</b> the start joint stretches toward the end control.",
                ],
            )
        )
        ui.chk_twist.setToolTip(
            self.sb.tooltip.fmt(
                title="Twist",
                body="Advanced spline twist driven by the start/end control rotation.",
                notes=["Spline only. Adds a <b>roll</b> attribute to the end control."],
            )
        )
        ui.chk_squash.setToolTip(
            self.sb.tooltip.fmt(
                title="Squash",
                body="Joints compress when the curve shortens.",
                notes=["Spline only."],
            )
        )
        ui.chk_volume.setToolTip(
            self.sb.tooltip.fmt(
                title="Volume Preservation",
                body="Bulges when squashed, thins when stretched.",
                notes=["Spline only."],
            )
        )
        ui.chk_auto_bend.setToolTip(
            self.sb.tooltip.fmt(
                title="Auto-Bend (Mid)",
                body="The mid control bows outward automatically as the ends "
                "compress toward each other.",
                notes=["Spline only — requires exactly <b>3</b> controls."],
            )
        )
        ui.b001.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 1 — Create Joints",
                body="Places this rig's joints along the tube's centerline.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down it)</i>.",
                    "Press <b>Create Joints</b>.",
                ],
                notes=[
                    "Anchor mode creates its 2 end joints instead of a chain.",
                    "Re-running replaces this rig's previous joints.",
                ],
            )
        )
        ui.b002.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 2 — Create Controls",
                body="Builds the active mode's control rig on the joints from Step 1.",
                steps=[
                    "Select the root joint <i>(Anchor: either or both end joints)</i>.",
                    "Press <b>Create IK / Controls</b>.",
                ],
                sections=[
                    (
                        "Creates",
                        [
                            "<b>Spline:</b> IK spline, start/mid/end controls, "
                            "twist, stretch, auto-bend.",
                            "<b>Anchor:</b> two end controls with distance stretch.",
                            "<b>FK:</b> nested FK controls, one per joint.",
                        ],
                    ),
                ],
                notes=["Control size is proportional to the tube's radius."],
            )
        )
        ui.b003.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 3 — Bind Skin",
                body="Smooth-binds the tube mesh to the joints with "
                "ring-uniform parametric weights — the same solver the "
                "One-Click build uses.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the tube mesh <i>last</i>.",
                    "Press <b>Bind Joints to Mesh</b>.",
                ],
                notes=["Re-running replaces the mesh's existing skinCluster."],
            )
        )
        ui.b004.setToolTip(
            self.sb.tooltip.fmt(
                title="Constrain Ends to Anchors",
                body="Constrains one or both tube ends to external anchor "
                "objects (each end's control follows its anchor) with "
                "distance-falloff skin weighting at the contact points.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the anchor object(s) — one per end to "
                    "constrain.",
                    "Press <b>Add End Constraints</b>.",
                ],
                notes=[
                    "Requires a bound tube — run <b>Step 3</b> first.",
                    "Each anchor constrains its nearest tube end — selection "
                    "order doesn't matter. A single anchor leaves the other end "
                    "free (a plugged-in loom whose base is part of the module).",
                    "Falloff spans ≈2× the tube radius.",
                ],
            )
        )
        ui.b005.setToolTip(
            self.sb.tooltip.fmt(
                title="Remove Rig",
                body="Deletes the rig — joints, controls, IK, utility nodes and "
                "the skin bind — and restores the mesh's viewport display. The "
                "tube mesh itself is kept.",
                steps=[
                    "Select the tube mesh, or any joint / control of the rig(s).",
                    "Press <b>Remove Rig</b>.",
                ],
                notes=[
                    "Works on rigs built in an earlier session — the rig is "
                    "read back from the scene.",
                    "Several rigs selected are removed together.",
                    "<b>Ctrl+Z</b> puts the rig back — bind included — in one "
                    "step, however many rigs were removed.",
                ],
            )
        )
        ui.b006.setToolTip(
            self.sb.tooltip.fmt(
                title="Rename Rig",
                body="Renames every node of the rig — group, joints, controls, "
                "sets and utility nodes — to the name in the <b>Rig Name</b> "
                "field.",
                steps=[
                    "Type the new name in <b>Rig Name</b>.",
                    "Select the tube mesh, or any joint / control of the rig.",
                    "Press <b>Rename Rig</b>.",
                ],
                notes=[
                    "One rig at a time.",
                    "Referenced rigs can't be renamed — rename them in their "
                    "source scene.",
                ],
            )
        )
        ui.b007.setToolTip(
            self.sb.tooltip.fmt(
                title="Rebind Skin",
                body="Re-solves the tube's bind from the rig already in the "
                "scene. Use it when the mesh stopped following the controls "
                "but the joints and controls are still there — a UV unwrap, "
                "<b>Delete History</b> or <b>Bake Non-Deformer History</b> on "
                "the tube removes the skinCluster and leaves everything else "
                "standing.",
                steps=[
                    "Select the tube mesh, or any joint / control of the rig.",
                    "Press <b>Rebind Skin</b>.",
                ],
                sections=[
                    (
                        "Older rigs",
                        [
                            "A rig built before the scene record existed is "
                            "linked to its tube only THROUGH the bind, so once "
                            "the bind is gone the tube looks like plain "
                            "geometry.",
                            "Select the tube <b>and</b> one of the rig's joints "
                            "or controls together — one rig, one tube at a time.",
                            "Only needed once: the rebind stamps the record.",
                        ],
                    ),
                ],
                notes=[
                    "Nothing has to have been saved — the weights are solved "
                    "from the centerline, so the rebind reproduces the "
                    "original bind.",
                    "Joints, controls and their animation are untouched "
                    "(unlike <b>One-Click Rig</b>, which rebuilds from "
                    "scratch).",
                    "Rebind at the rig's DEFAULT pose: a bind destroyed while "
                    "the rig was posed has already baked that pose into the "
                    "mesh, and no rebind can undo that.",
                ],
            )
        )
        ui.b000.setToolTip(
            self.sb.tooltip.fmt(
                title="One-Click Rig",
                body="Runs <b>Step 1 → Step 2 → Step 3</b> in order, using "
                "the parameter values set in each step's page.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down it)</i>.",
                    "Press <b>Full Rig</b>.",
                ],
                notes=[
                    "Rebuilding on an already-rigged mesh tears the old rig "
                    "down first.",
                    "<b>Ctrl+Z</b> reverts the whole run in one step — the "
                    "teardown of a previous rig included.",
                ],
            )
        )

    def _sync_auto_bend_gate(self, *_):
        """Gate Auto-Bend on the 3-control spline layout."""
        mode = self.get_mode()
        allowed = bool(
            mode and mode.auto_bend_editable and int(self.ui.s001.value()) == 3
        )
        self.ui.chk_auto_bend.setEnabled(allowed)
        if not allowed:
            self.ui.chk_auto_bend.setChecked(False)

    def apply_mode(self, index: int):
        """Apply mode values and constraints to UI widgets."""
        mode = self.ui.cmb_preset.itemData(index)
        if not mode:
            # Fallback if somehow data is missing or index invalid (shouldn't happen with correct usage)
            mode = RIG_MODES[0] if RIG_MODES else None

        if not mode:
            return

        # Step 1: Joints
        self.ui.s000.setValue(mode.num_joints)
        self.ui.s000.setEnabled(mode.num_joints_editable)

        # Step 1.5: Controls Count
        self.ui.s001.setValue(mode.num_controls)
        self.ui.s001.setEnabled(mode.num_controls_editable)

        # Step 2: Controls
        self.ui.chk_stretch.setChecked(mode.enable_stretch)
        self.ui.chk_stretch.setEnabled(mode.stretch_editable)

        self.ui.chk_squash.setChecked(mode.enable_squash)
        self.ui.chk_squash.setEnabled(mode.squash_editable)

        self.ui.chk_volume.setChecked(mode.enable_volume)
        self.ui.chk_volume.setEnabled(mode.volume_editable)

        self.ui.chk_auto_bend.setChecked(mode.enable_auto_bend)
        self.ui.chk_auto_bend.setEnabled(mode.auto_bend_editable)

        self.ui.chk_twist.setChecked(mode.enable_twist)
        self.ui.chk_twist.setEnabled(mode.twist_editable)

        self._sync_auto_bend_gate()

    def get_mode(self) -> RigModeConfig:
        """Get the current rig mode config."""
        mode = self.ui.cmb_preset.currentData()
        return mode if mode else (RIG_MODES[0] if RIG_MODES else None)

    def get_strategy(self) -> str:
        """Get the current strategy from the mode combobox."""
        return self.get_mode().strategy

    @staticmethod
    def _unique_auto_rig_name(leaf: str) -> str:
        """An unused ``<leaf>_RIG`` for a rig the user did not name.

        ``short_name`` drops the DAG path AND the namespace, so two tubes in
        one batch -- ``machineA:hose`` / ``machineB:hose``, or plain duplicates
        under different parents -- derive the same name. Left alone, tube 2's
        ``build()`` finds tube 1's ``<name>_GRP``, tears it down (taking its
        skinCluster, joints and controls) and the summary still reports both as
        built, leaving tube 1's mesh unbound and display-locked.

        Only the DERIVED name is uniquified: a name the artist typed still
        means "rebuild that rig", which is the rerun path.
        """
        base = f"{leaf}_RIG"
        name, i = base, 1
        while cmds.objExists(f"{name}_GRP"):
            name = f"{base}_{i:02d}"
            i += 1
        return name

    def get_tube_rig(self, obj, rig_name: Optional[str] = None):
        """Get the tube rig instance for the given object (the mesh, a joint,
        a control, or anything under the rig group); create one if none exists.

        *rig_name* names a NEW rig (a batch build's per-tube name); default
        is the Rig Name field, else an unused ``<mesh>_RIG``. An existing rig
        keeps its own name.
        """
        if obj is None:
            return None
        # for_node resolves raw nodes itself — pre-resolving a joint through
        # get_transform_node yields a *list*, which defeats the lookup.
        rig = TubeRig.for_node(str(obj))
        if rig is not None:
            return rig

        # New rig: bind to the mesh transform when one resolves (tolerates
        # group picks); otherwise keep the plain transform (b002 constructs
        # from a joint after a restart, when the registry is empty).
        shape = TubePath._resolve_mesh_shape(obj)
        if shape:
            target = NodeUtils.get_parent(shape, type=None, full_path=True) or str(
                shape
            )
        else:
            target = NodeUtils.get_transform_node(str(obj)) or str(obj)
            if isinstance(target, (set, list, tuple)):
                target = next(iter(target), str(obj))
        rig_name = rig_name or self.ui.txt000.text()
        if not rig_name:
            rig_name = self._unique_auto_rig_name(CoreUtils.short_name(target))
        return TubeRig(target, rig_name=rig_name)

    def _batch_targets(self) -> List[str]:
        """The tube meshes a build button acts on.

        Object-mode picks rig in batch (one rig per selected object). An
        edge selection is inherently single-tube — it names the path on ONE
        mesh — so it resolves to just that mesh.
        """
        objs = cmds.ls(selection=True, objectsOnly=True, flatten=True) or []
        if cmds.filterExpand(selectionMask=32):
            return objs[:1]
        return list(dict.fromkeys(objs))

    def _batch_names(self, objs: List[str]) -> List[Optional[str]]:
        """Per-object rig names for a batch: the typed name suffixed ``_01``,
        ``_02``, ... when several tubes share it; None (the per-mesh auto
        name) when the field is empty."""
        typed = self.ui.txt000.text().strip()
        if not typed or len(objs) == 1:
            return [typed or None] * len(objs)
        return [f"{typed}_{i + 1:02d}" for i in range(len(objs))]

    def _selected_rigs(self) -> List[TubeRig]:
        """Distinct rigs the selection touches (mesh, joint, control, group)
        — resolved from the scene record when the session registry has no
        entry (a rig built before a restart)."""
        rigs: List[TubeRig] = []
        for obj in cmds.ls(selection=True, objectsOnly=True, flatten=True) or []:
            rig = TubeRig.for_node(obj)
            if rig is not None and rig not in rigs:
                rigs.append(rig)
        return rigs

    @staticmethod
    def _batch_summary(what: str, done: List[str], failed: List[str]) -> str:
        lines = []
        if done:
            lines.append(f"{what}: {', '.join(done)}")
        if failed:
            lines.append("Failed:\n  " + "\n  ".join(failed))
        return "\n".join(lines)

    @staticmethod
    def _cancel_note(remaining: int) -> str:
        """Say so when the user cancelled a batch — and that undo is still one step.

        The whole run shares one undo chunk, so a half-finished batch is not a
        mess to clean up by hand; saying that is the difference between a
        cancel the user trusts and one they don't.

        Parameters:
            remaining (int): Items the run never reached. ``0`` (a completed
                run) yields no note. Counted from the loop index rather than
                derived from the result lists, so an item that FAILED is not
                also counted as skipped.
        """
        if remaining <= 0:
            return ""
        return (
            f"<br><br>Cancelled with {remaining} left — Ctrl+Z reverts the "
            "whole run in one step."
        )

    def _expand_step_joints(self, joints: List[str]) -> List[str]:
        """Expand a single joint to its full rig joint set (b002/b003/b004).

        A single joint expands to its chain; for Anchor rigs (sibling end
        joints, no chain) it expands to the joints sharing its parent group.
        """
        joints = [str(j) for j in joints]
        if len(joints) != 1:
            return joints
        if self.get_strategy() == "anchor":
            parent = NodeUtils.get_parent(joints[0], type=None, full_path=True)
            siblings = (
                cmds.listRelatives(parent, children=True, type="joint", fullPath=True)
                if parent
                else None
            )
            if siblings and len(siblings) == 2:
                return [str(j) for j in siblings]
            return joints
        return [str(j) for j in RigUtils.get_joint_chain_from_root(joints[0])]

    def _selected_step_joints(self) -> List[str]:
        """The selected joints for a step operation, chain-expanded."""
        sel = cmds.ls(selection=True, flatten=True) or []
        return self._expand_step_joints(cmds.ls(sel, type="joint", flatten=True) or [])

    def _existing_controls(self, tube_rig) -> Optional[str]:
        """First pre-existing control-rig node for *tube_rig*, or None.

        Delegates control lookup to ``TubeRig._end_control`` — the SSoT for
        the builders' naming conventions (a hand-rolled name check here
        previously tested ``<rig>_start``, which never exists: the builders
        suffix ``_CTRL``, so leftover anchor controls went undetected).
        """
        ik = f"{tube_rig.rig_name}_ikHandle"
        if cmds.objExists(ik):
            return ik
        return tube_rig._end_control(0) or tube_rig._end_control(-1)

    def _phase_hook(self, update: Callable, prefix: str = "") -> Callable:
        """A ``TubeRig`` progress hook that writes phase text to the footer.

        Every rig operation runs with the viewport suspended (one undo step,
        no per-command redraw), so the footer is the only thing telling the
        user the tool is working rather than hung. The engine reports phases
        indeterminately, which is why the bar is a marquee: a rig's phase
        count varies with the strategy and the options, so a percentage would
        be a fiction.

        Parameters:
            update: The footer's ``update(value, text)`` callable.
            prefix: Prepended to every phase message — carries a batch run's
                "[2/5] " counter, which the engine cannot know about.
        """
        adapted = self.sb.progress_adapter(update)
        if not prefix:
            return adapted

        def prefixed(current=None, total=0, message=None) -> bool:
            return adapted(current, total, f"{prefix}{message}" if message else message)

        return prefixed

    @staticmethod
    def _batch_prefix(index: int, total: int) -> str:
        """Progress-line prefix: ``[2/5]`` for a multi-item run, empty for one."""
        return f"[{index + 1}/{total}] " if total > 1 else ""

    def create_joints_from_tube(self, obj, rig_name: Optional[str] = None):
        """Step 1 — create this rig's joints from the tube mesh (mode-aware)."""
        strategy = self.get_strategy()
        num_joints = 2 if strategy == "anchor" else self.ui.s000.value()
        edges = cmds.filterExpand(selectionMask=32)  # optional user edge selection

        tube_rig = self.get_tube_rig(obj, rig_name=rig_name)
        try:
            centerline, num_joints = tube_rig.resolve_centerline(
                num_joints, edges=edges
            )
        except ValueError as e:  # e.g. selection resolves to no polygon mesh
            self.sb.message_box(str(e))
            return []

        if not centerline or len(centerline) < 2:
            self.sb.message_box(
                "Failed to extract a valid centerline from the tube mesh."
            )
            return []

        if self.ui.chk000.isChecked():
            centerline = list(centerline)[::-1]

        joint_radius, _ = tube_rig.resolve_sizes(centerline, self.ui.s002.value())
        if strategy == "anchor":
            return tube_rig.create_anchor_joints(centerline, radius=joint_radius)
        return tube_rig.generate_joint_chain(
            centerline=centerline, num_joints=num_joints, radius=joint_radius
        )

    @CoreUtils.undoable(name="Tube Rig: Full Rig", suspend_refresh=True)
    def b000(self):
        """One-Click Rig — runs Steps 1 → 2 → 3 with the step parameters,
        once per selected tube."""
        objs = self._batch_targets()
        if not objs:
            self.sb.message_box("Select one or more polygon tube meshes to rig.")
            return

        strategy = self.get_strategy()
        edges = cmds.filterExpand(selectionMask=32)
        built, failed = [], []
        skipped = 0
        names = self._batch_names(objs)
        with self.ui.footer.progress(text="Tube Rig: building…") as update:
            for i, (obj, rig_name) in enumerate(zip(objs, names)):
                prefix = self._batch_prefix(i, len(objs))
                if not update(None, f"{prefix}Rigging {CoreUtils.leaf_name(obj)}…"):
                    skipped = len(objs) - i
                    break
                tube_rig = self.get_tube_rig(obj, rig_name=rig_name)
                try:
                    tube_rig.build(
                        strategy=strategy,
                        progress=self._phase_hook(update, prefix),
                        num_joints=self.ui.s000.value(),
                        num_controls=self.ui.s001.value(),
                        radius=self.ui.s002.value(),
                        reverse=self.ui.chk000.isChecked(),
                        edges=edges,
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                        enable_squash=self.ui.chk_squash.isChecked(),
                        enable_volume=self.ui.chk_volume.isChecked(),
                        enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                        enable_twist=self.ui.chk_twist.isChecked(),
                    )
                    built.append(tube_rig.rig_name)
                except Exception as e:
                    failed.append(f"{CoreUtils.leaf_name(obj)}: {e}")
                    self.sb.logger.error(f"Build Error ({obj}): {e}", exc_info=True)
        self.sb.message_box(
            self._batch_summary(f"Tube rig ({strategy}) created", built, failed)
            + self._cancel_note(skipped)
        )

    @CoreUtils.undoable(name="Tube Rig: Create Joints", suspend_refresh=True)
    def b001(self):
        """Step 1: Create Joints from Tube — once per selected tube."""
        objs = self._batch_targets()
        if not objs:
            self.sb.message_box(
                "Select the tube mesh (or an edge loop on it) to create joints."
            )
            return

        done = []
        skipped = 0
        names = self._batch_names(objs)
        with self.ui.footer.progress(text="Tube Rig: creating joints…") as update:
            for i, (obj, rig_name) in enumerate(zip(objs, names)):
                prefix = self._batch_prefix(i, len(objs))
                if not update(
                    None, f"{prefix}Creating joints on {CoreUtils.leaf_name(obj)}…"
                ):
                    skipped = len(objs) - i
                    break
                joints = self.create_joints_from_tube(obj, rig_name=rig_name)
                if joints:  # failures already message-boxed their reason
                    done.append(
                        f"{len(joints)} ({CoreUtils.leaf_name(obj)})"
                        if len(objs) > 1
                        else str(len(joints))
                    )
        if done:
            self.sb.message_box(
                f"Joints created: {', '.join(done)}" + self._cancel_note(skipped)
            )
        elif skipped:  # cancelled before the first tube — silence reads as broken
            self.sb.message_box(f"No joints created.{self._cancel_note(skipped)}")

    @CoreUtils.undoable(name="Tube Rig: Create Controls", suspend_refresh=True)
    def b002(self):
        """Step 2: Create IK / Controls (mode dependent)."""
        strategy = self.get_strategy()

        joints = self._selected_step_joints()
        if not joints:
            self.sb.message_box(
                "Select the root joint created in Step 1.\n"
                "(Anchor: either or both end joints.)"
            )
            return
        if strategy == "anchor" and len(joints) != 2:
            self.sb.message_box(
                f"Anchor rigs use exactly 2 end joints (got {len(joints)}).\n"
                "Run Step 1 in Anchor mode to create them."
            )
            return
        if strategy == "spline" and len(joints) < 2:
            self.sb.message_box(
                "Spline IK needs a chain of at least 2 joints — select its root."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])
        tube_rig.joints = joints  # step-created or manual chains alike

        existing = self._existing_controls(tube_rig)
        if existing:
            self.sb.message_box(
                f"Controls already exist for '{tube_rig.rig_name}' ({existing}).\n"
                "Delete the previous controls or rebuild with One-Click Rig."
            )
            return

        # Control size follows the measured tube radius (falls back to the
        # joints' display radii when the rig has no resolvable mesh).
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())

        try:
            with self.ui.footer.progress(
                text=f"Tube Rig: creating controls on {len(joints)} joints…"
            ) as update:
                update()
                if strategy == "spline":
                    controls, _, _ = tube_rig.create_spline_controls(
                        joints,
                        size=size,
                        num_controls=self.ui.s001.value(),
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                        enable_squash=self.ui.chk_squash.isChecked(),
                        enable_volume=self.ui.chk_volume.isChecked(),
                        enable_twist=self.ui.chk_twist.isChecked(),
                        enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                    )
                    kind = "Spline IK"
                elif strategy == "anchor":
                    controls = tube_rig.create_anchor_controls(
                        joints,
                        size=size,
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                    )
                    kind = "Anchor"
                else:
                    controls = tube_rig.create_fk_controls(joints, size=size)
                    kind = "FK"
        except ValueError as e:
            self.sb.message_box(str(e))
            return

        ctrl_names = ", ".join(CoreUtils.leaf_name(c) for c in controls)
        self.sb.message_box(
            f"{kind} controls created on {len(joints)} joints.\nControls: {ctrl_names}"
        )

    @CoreUtils.undoable(name="Tube Rig: Bind Skin", suspend_refresh=True)
    def b003(self):
        """Step 3: Bind Joint Chain to Tube."""
        sel = cmds.ls(selection=True, flatten=True) or []
        if len(sel) < 2:
            self.sb.message_box(
                "Select the root joint, then Shift-select the tube mesh last.\n"
                "Usage: [Root Joint] → [Tube Mesh]"
            )
            return
        obj = sel[-1]

        if not TubePath._resolve_mesh_shape(obj):
            self.sb.message_box(
                f"'{CoreUtils.leaf_name(obj)}' is not a polygon mesh — select the tube "
                "mesh last."
            )
            return

        joints = self._selected_step_joints()
        if len(joints) < 2:
            self.sb.message_box(
                "Select the root joint of the chain (at least 2 joints), then "
                "the tube mesh."
            )
            return

        tube_rig = self.get_tube_rig(obj)
        with self.ui.footer.progress(
            text=f"Tube Rig: binding {CoreUtils.leaf_name(obj)} to "
            f"{len(joints)} joints…"
        ) as update:
            update()
            skin_cluster = tube_rig.bind_joint_chain(obj, joints)
        if not skin_cluster:
            self.sb.message_box(
                "Failed to bind the joint chain — see the Script Editor for details."
            )
            return
        self.sb.message_box(
            f"Skinned '{CoreUtils.leaf_name(obj)}' to {len(joints)} joints "
            f"({CoreUtils.leaf_name(skin_cluster)})."
        )

    @CoreUtils.undoable(name="Tube Rig: End Constraints", suspend_refresh=True)
    def b004(self):
        """Utility: Constrain Ends to Anchors — one anchor or both.

        Each anchor constrains its NEAREST tube end. A single anchor leaves
        the other end free (a plugged-in loom whose base is part of the module
        the rig rides). Two anchors must sit at different ends: forcing a pair
        onto both ends and leaving the primitive's own nearest-end guard to
        override one of them silently replaced the first anchor with the
        second (production wire looms — the base object's pivot sat nearer the PLUG
        end), so that case is refused with the reason instead.
        """
        sel = cmds.ls(selection=True, flatten=True) or []
        root = cmds.ls(sel[:1], type="joint", flatten=True) or []
        anchors = [s for s in sel[1:] if not cmds.ls(s, type="joint")]
        if not root or not anchors:
            self.sb.message_box(
                "Select the root joint, then the anchor object for each tube end "
                "to constrain (one or two)."
            )
            return
        if len(anchors) > 2:
            self.sb.message_box(
                f"A tube has two ends — got {len(anchors)} anchors. Select one "
                "anchor per end to constrain."
            )
            return
        joints = self._expand_step_joints([root[0]])
        if len(joints) < 2:
            self.sb.message_box(
                "Could not derive the joint chain from the selection — "
                "select the rig's ROOT joint (the chain start)."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])

        # Falloff weighting needs a bound mesh — fail up front with the fix.
        bound = tube_rig.skin_cluster or (
            cmds.listConnections(f"{joints[0]}.worldMatrix[0]", type="skinCluster")
        )
        if not bound:
            self.sb.message_box(
                "The joints aren't bound to a mesh yet — run Step 3 (Bind Skin) first."
            )
            return

        # Each anchor to its nearest end — selection order can't cross the
        # constraints, and two anchors off one end are refused rather than
        # silently collapsed onto it.
        p_start = om.MVector(*_TubeRigInternal._xform_t_ws(joints[0]))
        p_end = om.MVector(*_TubeRigInternal._xform_t_ws(joints[-1]))
        nearest = []
        for anchor in anchors:
            a = om.MVector(*_TubeRigInternal._xform_t_ws(anchor))
            nearest.append(0 if (a - p_start).length() <= (a - p_end).length() else -1)
        if len(anchors) == 2 and nearest[0] == nearest[1]:
            which = "start" if nearest[0] == 0 else "end"
            self.sb.message_box(
                f"Both anchors sit nearest the tube's {which} end "
                f"({CoreUtils.leaf_name(anchors[0])}, "
                f"{CoreUtils.leaf_name(anchors[1])}).\n"
                "Select one anchor per end — or a single anchor to constrain "
                "just that end."
            )
            return

        # Falloff proportional to the tube: ≈2× its radius.
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())
        falloff = size * 2.0

        lines = []
        with self.ui.footer.progress(text="Tube Rig: constraining ends…") as update:
            for anchor, idx in zip(anchors, nearest):
                end = "start" if idx == 0 else "end"
                update(None, f"Constraining {end} to {CoreUtils.leaf_name(anchor)}…")
                result = tube_rig.constrain_end_with_falloff(
                    joints, anchor, falloff=falloff, joint_index=idx
                )
                lines.append(
                    f"  {end} <- {CoreUtils.leaf_name(anchor)}: "
                    f"{CoreUtils.leaf_name(result) if result else 'failed'}"
                )
        self.sb.message_box("End constraints added:\n" + "\n".join(lines))

    @CoreUtils.undoable(name="Tube Rig: Remove", suspend_refresh=True)
    def b005(self):
        """Utility: Remove Rig — tear down every rig the selection touches."""
        rigs = self._selected_rigs()
        if not rigs:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of the rig(s) to remove."
            )
            return
        removed, failed = [], []
        skipped = 0
        with self.ui.footer.progress(text="Tube Rig: removing…") as update:
            for i, rig in enumerate(rigs):
                prefix = self._batch_prefix(i, len(rigs))
                if not update(None, f"{prefix}Removing {rig.rig_name}…"):
                    skipped = len(rigs) - i
                    break
                try:
                    rig.teardown(progress=self._phase_hook(update, prefix))
                    removed.append(rig.rig_name)
                except Exception as e:
                    failed.append(f"{rig.rig_name}: {e}")
                    self.sb.logger.error(
                        f"Remove Error ({rig.rig_name}): {e}", exc_info=True
                    )
        self.sb.message_box(
            self._batch_summary("Rig removed", removed, failed)
            + self._cancel_note(skipped)
        )

    @CoreUtils.undoable(name="Tube Rig: Rename", suspend_refresh=True)
    def b006(self):
        """Utility: Rename Rig — to the Rig Name field, every node included."""
        new_name = self.ui.txt000.text().strip()
        if not new_name:
            self.sb.message_box("Type the new name in the Rig Name field first.")
            return
        rigs = self._selected_rigs()
        if len(rigs) != 1:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of ONE rig to rename."
                if not rigs
                else f"Select one rig at a time (got {len(rigs)})."
            )
            return
        old = rigs[0].rig_name
        try:
            with self.ui.footer.progress(
                text=f"Tube Rig: renaming {old} → {new_name}…"
            ) as update:
                update()
                renamed = rigs[0].rename(new_name)
        except ValueError as e:
            self.sb.message_box(str(e))
            return
        if renamed == old:
            self.sb.message_box(f"Rig name unchanged: {old}")
            return
        self.sb.message_box(f"Rig renamed: {old} -> {renamed}")

    def _orphan_meshes(self) -> List[str]:
        """Selected meshes that resolve to no rig.

        A rig built before the scene record existed is reachable from its mesh
        only through the skinCluster's first influence, so once the bind is
        destroyed — the very thing Rebind Skin repairs — the tube looks like
        plain geometry. Pairing it with a selected joint / control is the only
        way back in.
        """
        orphans = []
        for obj in cmds.ls(selection=True, objectsOnly=True, flatten=True) or []:
            if not TubePath._resolve_mesh_shape(obj):
                continue
            if TubeRig.for_node(obj) is not None:
                continue
            path = NodeUtils.get_transform_node(obj)
            if isinstance(path, (list, tuple, set)):
                path = next(iter(path), None)
            if path and str(path) not in orphans:
                orphans.append(str(path))
        return orphans

    @CoreUtils.undoable(name="Tube Rig: Rebind Skin", suspend_refresh=True)
    def b007(self):
        """Utility: Rebind Skin — re-solve the bind for every rig the selection touches."""
        rigs = self._selected_rigs()
        orphans = self._orphan_meshes()

        if not rigs:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of the rig(s) to rebind."
                + (
                    "\n\nThe selected mesh isn't linked to any rig — if its bind "
                    "was already destroyed, Shift-select one of the rig's joints "
                    "or controls as well."
                    if orphans
                    else ""
                )
            )
            return

        # Legacy rescue: one rig + one unlinked mesh is an unambiguous pairing.
        # More than one of either is not, and guessing would bind the wrong
        # tube — make the user disambiguate instead.
        pair_mesh = None
        if orphans:
            if len(rigs) == 1 and len(orphans) == 1:
                pair_mesh = orphans[0]
            else:
                self.sb.message_box(
                    f"Can't tell which mesh belongs to which rig "
                    f"({len(rigs)} rig(s), {len(orphans)} unlinked mesh(es)).\n"
                    "Rebind one rig at a time: select its tube plus one of its "
                    "joints or controls."
                )
                return

        rebound, failed = [], []
        skipped = 0
        with self.ui.footer.progress(text="Tube Rig: rebinding skin…") as update:
            for i, rig in enumerate(rigs):
                prefix = self._batch_prefix(i, len(rigs))
                if not update(None, f"{prefix}Rebinding {rig.rig_name}…"):
                    skipped = len(rigs) - i
                    break
                try:
                    rig.rebind_skin(mesh=pair_mesh)
                    rebound.append(rig.rig_name)
                except Exception as e:
                    failed.append(f"{rig.rig_name}: {e}")
                    self.sb.logger.error(
                        f"Rebind Error ({rig.rig_name}): {e}", exc_info=True
                    )
        self.sb.message_box(
            self._batch_summary("Skin rebound", rebound, failed)
            + self._cancel_note(skipped)
        )

    # -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("tube_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
