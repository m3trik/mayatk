# !/usr/bin/python
# coding=utf-8
"""Articulated Rig panel -- the Switchboard slots for ``articulated_rig.ui``.

A thin driver over :class:`ArticulatedRig`: analyze the selection into a plan
(the joints table), correct a joint's type in the table, build; then the
post-rig edits -- the viewport grab, split a link off with a new joint,
fold one back, limits from the pose, rebuild, remove. The table shows the
plan before a build and the rig after one; a type changed there edits the
plan, or retypes the built joint with its animation carried across.
"""

from typing import List, Optional

try:
    import maya.cmds as cmds
except ImportError:  # the surface imports without Maya (registry, docs, mock tests)
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.rig_utils.articulated_rig._articulated_rig import ArticulatedRig
from mayatk.rig_utils.articulated_rig.grab_tool import ArticulatedRigGrab

try:
    from qtpy import QtCore, QtWidgets
except ImportError:  # a headless surface: the slots are never built there
    QtCore = QtWidgets = None


class ArticulatedRigSlots(ptk.LoggingMixin):
    """Slots for the Articulated Rig panel."""

    #: The joint types a table row offers.
    TYPES = ("hinge", "swivel", "universal", "ball", "slide")
    #: ``cmb_insert_type``'s entries, by index (None: what the geometry says).
    INSERT_TYPES = (None, "slide", "hinge", "swivel", "universal", "ball")
    COLUMNS = ["Part", "Type", "Hangs Off", "Limits", "Why"]

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.articulated_rig
        #: The proposal the table shows before a build, or None.
        self.plan: Optional[dict] = None
        #: The rig the table shows after one, or None.
        self.rig: Optional[ArticulatedRig] = None

        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        for name in (
            "btn_analyze",
            "btn_build",
            "btn_grab",
            "btn_insert",
            "btn_fold",
            "btn_limits_from_pose",
            "btn_clear_limits",
            "btn_rebuild",
            "btn_remove",
        ):
            getattr(self.ui, name).clicked.connect(getattr(self, name))
        self._init_table()
        self._init_tooltips()

    def _on_log_link_clicked(self, url) -> None:
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Articulated Rig",
                body="Rig a prop of rigid parts on hinges, swivels, balls and "
                "slides -- a desk lamp, a magnifier arm, a boom -- as a skeleton "
                "that ships: Unity and the WebXR preview pose it, and a hand can "
                "grab it there.",
                steps=[
                    "Select the prop's group (or its parts, root first, with "
                    "<b>Selection Order</b>).",
                    "<b>Analyze Selection</b>: the table proposes one joint per "
                    "moving part -- where the parts meet, and how.",
                    "Correct any joint's <b>Type</b> in the table.",
                    "<b>Build Rig</b>, then pose it with the controls or the "
                    "<b>Grab Tool</b>.",
                ],
                notes=[
                    "A slide is found where one tube runs inside another; its "
                    "travel comes from their overlap.",
                    "Hinge knobs off the arm's plane give the joints' positions; "
                    "the plane itself gives every hinge's axis.",
                    "<b>Split Off Selected</b> adds a joint to a built rig (a "
                    "telescope you left out) -- at 0 it changes no pose, so your "
                    "keys hold.",
                    "Every build and edit is one undo step.",
                ],
            )
        )

    def _init_tooltips(self):
        tip = self.sb.tooltip.fmt
        ui = self.ui
        ui.cmb_source.setToolTip(
            tip(
                title="Parts",
                body="How the selection becomes the rig's links.",
                notes=[
                    "<b>Auto</b>: each part (a group picked expands to its parts) "
                    "is a link; the chain is walked from the lowest part through "
                    "the parts that touch.",
                    "<b>Selection Order</b>: the parts as selected, root first.",
                ],
            )
        )
        ui.btn_grab.setToolTip(
            tip(
                title="Grab Tool",
                body="Drag a rigged part in the viewport; the rig follows, "
                "solved as Unity and the WebXR preview solve a hand's grab.",
                notes=["One undo step per drag; keyed on release when Auto Key is on."],
            )
        )
        ui.btn_insert.setToolTip(
            tip(
                title="Split Off Selected",
                body="Give the selected parts a joint of their own, hung off "
                "the link they rode -- a slide for a tube inside a tube.",
                notes=[
                    "The new joint starts at 0: every key already made still holds."
                ],
            )
        )
        ui.btn_limits_from_pose.setToolTip(
            tip(
                title="Set Limits From Pose",
                body="Pose a joint to where it physically stops, select its "
                "control, press: each channel's current value becomes its limit "
                "on that side.",
            )
        )

    # ================================================================ table
    def _init_table(self):
        table = self.ui.tbl_joints
        table.setColumnCount(len(self.COLUMNS))
        table.setHorizontalHeaderLabels(self.COLUMNS)
        table.verticalHeader().setVisible(False)
        table.set_cell_widget_click_columns([1])

    def _rows(self) -> List[dict]:
        """``{"id", "part", "type", "parent", "limits", "why"}`` per joint of
        the plan or of the rig."""
        if self.plan is not None:
            links = self.plan["links"]
            return [
                {
                    "id": None,
                    "index": i,
                    "part": CoreUtils.leaf_name(links[j["link"]][0]),
                    "type": j["type"],
                    "parent": CoreUtils.leaf_name(links[j["parent"]][0]),
                    "limits": self._limits_text(j.get("limits")),
                    "why": j.get("reason", ""),
                }
                for i, j in enumerate(self.plan["joints"])
            ]
        if self.rig is not None:
            spec = self.rig.spec
            links = spec["links"]
            return [
                {
                    "id": j["id"],
                    "index": i,
                    "part": links[j["link"]][0]["name"],
                    "type": j["type"],
                    "parent": links[j["parent"]][0]["name"],
                    "limits": self._limits_text(j.get("limits")),
                    "why": j.get("reason", ""),
                }
                for i, j in enumerate(spec["joints"])
            ]
        return []

    @staticmethod
    def _limits_text(limits) -> str:
        fmt = lambda v: "-" if v is None else f"{v:.3g}"  # noqa: E731
        return (
            ", ".join(
                f"{c} {fmt(lo)}..{fmt(hi)}" for c, (lo, hi) in (limits or {}).items()
            )
            or "free"
        )

    def refresh_table(self):
        table = self.ui.tbl_joints
        rows = self._rows()
        if not rows:
            # ``add`` of nothing is a one-column table with no headers.
            table.setRowCount(0)
            return
        table.add(
            [[r["part"], r["type"], r["parent"], r["limits"], r["why"]] for r in rows],
            headers=self.COLUMNS,
        )
        for row, entry in enumerate(rows):
            combo = QtWidgets.QComboBox()
            combo.addItems(self.TYPES)
            combo.setCurrentText(entry["type"])
            combo.currentTextChanged.connect(
                lambda kind, e=entry: self._retype(e, kind)
            )
            table.setCellWidget(row, 1, combo)
        table.resizeColumnsToContents()

    def _retype(self, entry: dict, kind: str) -> None:
        try:
            if self.plan is not None:
                joint = self.plan["joints"][entry["index"]]
                joint["type"] = kind
                # A type's own limits only: a slide's travel means nothing to a hinge.
                channels = ArticulatedRig.JOINT_TYPES[kind][0]
                joint["limits"] = {
                    c: v
                    for c, v in (joint.get("limits") or {}).items()
                    if c in channels
                }
                self.logger.info(f"{entry['part']}: planned as a {kind}.")
            elif self.rig is not None:
                self.rig.edit_joint(entry["id"], type=kind)
                self.logger.info(
                    f"{entry['part']}: rebuilt as a {kind}, animation kept."
                )
        except Exception as error:  # noqa: BLE001 -- a panel reports, never raises
            self.logger.error(f"Could not retype {entry['part']}: {error}")
        # Deferred: this runs inside the combo's own signal, and a refresh
        # replaces the cell widgets -- deleting the sender mid-emit.
        QtCore.QTimer.singleShot(0, self.refresh_table)

    # ============================================================== actions
    def _selection(self) -> List[str]:
        return cmds.ls(selection=True, transforms=True, long=True) or []

    def _selected_rig(self) -> Optional[ArticulatedRig]:
        for node in self._selection():
            rig = ArticulatedRig.for_node(node)
            if rig is not None:
                self.rig = rig
                return rig
        if self.rig is not None:
            try:
                _ = self.rig.group
                return self.rig
            except RuntimeError:
                self.rig = None
        self.logger.error("Select a part or control of an articulated rig.")
        return None

    def btn_analyze(self):
        selection = self._selection()
        if not selection:
            self.logger.error("Select the prop (its group, or its parts).")
            return
        ordered = self.ui.cmb_source.currentIndex() == 1
        try:
            self.plan = ArticulatedRig.analyze(selection, ordered=ordered)
        except Exception as error:  # noqa: BLE001
            self.logger.error(f"Analysis failed: {error}")
            return
        self.rig = None
        lines = [
            f"{CoreUtils.leaf_name(self.plan['links'][j['link']][0])}: {j['type']} ({j['reason']})"
            for j in self.plan["joints"]
        ]
        self.logger.log_group("Proposed joints", lines)
        for part in self.plan["unreached"]:
            self.logger.warning(
                f"{CoreUtils.leaf_name(part)} touches nothing; left out."
            )
        self.refresh_table()

    @CoreUtils.undoable(name="Articulated Rig: Build", suspend_refresh=True)
    def btn_build(self):
        if self.plan is None:
            self.btn_analyze()
        if self.plan is None:
            return
        name = self.ui.txt_name.text().strip() or None
        try:
            self.rig = ArticulatedRig.create(
                self.plan["links"], self.plan["joints"], name=name
            )
        except Exception as error:  # noqa: BLE001
            self.logger.error(f"Build failed: {error}")
            self.sb.message_box(f"Build failed:<br>{error}")
            return
        self.plan = None
        link = self.logger.log_link(self.rig.name, "select", node=self.rig.group)
        self.logger.info(f"Built {link}: {len(self.rig.joint_ids())} joints.")
        self.refresh_table()

    def btn_grab(self):
        ArticulatedRigGrab.activate()
        self.logger.info("Grab Tool: drag a rigged part in the viewport.")

    def btn_insert(self):
        rig = self._selected_rig()
        if rig is None:
            return
        rigged = {m["uuid"] for link in rig.spec["links"] for m in link}
        parts = [
            n
            for n in self._selection()
            if (cmds.ls(n, uuid=True) or [None])[0] in rigged
        ]
        if not parts:
            self.logger.error("Select the parts to split off (not their controls).")
            return
        kind = self.INSERT_TYPES[self.ui.cmb_insert_type.currentIndex()]
        try:
            rig.insert_joint(parts, joint_type=kind)
        except Exception as error:  # noqa: BLE001
            self.logger.error(f"Split failed: {error}")
            return
        self.logger.info(
            f"Split off {', '.join(CoreUtils.leaf_name(p) for p in parts)}."
        )
        self.refresh_table()

    def btn_fold(self):
        rig = self._selected_rig()
        if rig is None:
            return
        ids = {rig.joint_id_of(n) for n in self._selection()} - {None}
        for joint_id in ids:
            try:
                rig.remove_joint(joint_id)
                self.logger.info(f"Folded {joint_id} back into the link it hung off.")
            except Exception as error:  # noqa: BLE001
                self.logger.error(f"Could not fold {joint_id}: {error}")
        self.refresh_table()

    def btn_limits_from_pose(self):
        rig = self._selected_rig()
        if rig is None:
            return
        for joint_id in {rig.joint_id_of(n) for n in self._selection()} - {None}:
            ctrl = rig.control(joint_id)
            joint = next(j for j in rig.spec["joints"] if j["id"] == joint_id)
            for channel in ArticulatedRig.JOINT_TYPES[joint["type"]][0]:
                value = cmds.getAttr(f"{ctrl}.{channel}")
                if abs(value) < 1.0e-6:
                    continue  # at rest: this channel says nothing about a stop
                side = "min" if value < 0 else "max"
                rig.set_limit_from_pose(joint_id, channel, side)
                self.logger.info(f"{joint_id}.{channel} {side} = {value:.3g}")
        self.refresh_table()

    def btn_clear_limits(self):
        rig = self._selected_rig()
        if rig is None:
            return
        for joint_id in {rig.joint_id_of(n) for n in self._selection()} - {None}:
            joint = next(j for j in rig.spec["joints"] if j["id"] == joint_id)
            for channel in ArticulatedRig.JOINT_TYPES[joint["type"]][0]:
                rig.set_limits(joint_id, channel, None, None)
            self.logger.info(f"{joint_id}: limits cleared.")
        self.refresh_table()

    def btn_rebuild(self):
        rig = self._selected_rig()
        if rig is None:
            return
        rig.rebuild()
        self.logger.info(f"Rebuilt {rig.name}; animation carried across.")
        self.refresh_table()

    def btn_remove(self):
        rig = self._selected_rig()
        if rig is None:
            return
        name = rig.name
        rig.teardown()
        self.rig = None
        self.logger.info(f"Removed {name}; every part is back as it was.")
        self.refresh_table()


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("articulated_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
