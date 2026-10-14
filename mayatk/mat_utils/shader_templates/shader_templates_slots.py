# !/usr/bin/python
# coding=utf-8
"""Shader Templates panel — the Switchboard slots for ``shader_templates.ui``.

A thin driver over :class:`~mayatk.mat_utils.shader_templates._shader_templates.ShaderTemplates`
(save / restore a shader graph as a YAML template); the shipped templates live
in ``templates/`` beside it.
"""

import os
import logging

try:
    import maya.cmds as cmds
except ImportError:
    pass
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils.shader_templates._shader_templates import ShaderTemplates


class ShaderTemplatesSlots(ptk.LoggingMixin):
    # INFO (not WARNING): the logger is redirected into the panel's txt001,
    # so info-level lines ARE the user feedback ("COMPLETED.", saved paths).
    @property
    def source_images_dir(self) -> str:
        """Where Browse opens: the scene project's texture folder, resolved
        when asked (the scene can change while the panel stays open)."""
        return DataNodes.scene_source_images_dir()

    def __init__(self, switchboard, log_level="INFO"):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shader_templates

        self.image_files = None
        self.last_restored_nodes = None
        # Shipped templates read-only, the user's in the per-user config (an
        # upgrade replaces the package folder) -- blendertk's store shape.
        self._store = ShaderTemplates.store()

        # Setup logging
        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(True)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Dispatch the action:// links the run summary emits (select the
        # created shader, open the saved template).
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

        # NOTE: shader plugins (shaderFXPlugin / mtoa) load lazily in b000 —
        # loading here froze every panel open for seconds (mtoa boots the
        # whole Arnold renderer), and a missing plugin raised out of
        # __init__, killing the slots instance (panel opened dead).

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.setTitle("Shader Templates")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_open_templates_dir",
            setText="Open Templates Directory",
            setToolTip="Open the directory containing shader templates.",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Graph Material",
            setToolTip="Graph the selected material in the Hypershade.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shader Templates",
                body="Save and restore shader networks as reusable YAML "
                "templates. Templates live under the package's "
                "<i>templates/</i> directory.",
                steps=[
                    "Select a material in the scene to capture its full network.",
                    "Press <b>Save Template</b> to write the current "
                    "network out under a new name.",
                    "To restore, pick a template from the combo and press "
                    "<b>Restore Template</b>.",
                ],
                sections=[
                    (
                        "Menu options",
                        [
                            "<b>Open Templates Directory</b> — reveal the "
                            "templates folder in Explorer.",
                            "<b>Graph Material</b> — open the most recently "
                            "restored material in Maya's Hypershade.",
                        ],
                    ),
                ],
            )
        )

    def lbl_graph_material(self):
        """Graph the last restored material in the Hypershade."""
        if self.last_restored_nodes:
            MatUtils.graph_materials(self.last_restored_nodes)
        else:
            cmds.warning("No material has been restored yet.")

    def lbl_open_templates_dir(self):
        """Open the user's shader templates directory in file explorer."""
        ptk.open_explorer(str(self._store.user_dir), create_dir=True)

    def cmb002_init(self, widget):
        """Initialize the ComboBox for shader templates."""
        if not widget.is_initialized:
            widget.restore_state = True
            # Persist the pick by TEXT, not index — the list is rebuilt from
            # os.listdir each show, so a stored index selects whichever
            # template happens to occupy that row next session.
            widget.restore_by = "text"
            widget.refresh_on_show = True
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl000",
                setText="Rename",
                setToolTip="Rename the current template.",
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl001",
                setText="Delete",
                setToolTip="Delete the current template.",
            )
            widget.on_editing_finished.connect(
                lambda text: self.rename_template_safe(widget, text)
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl002",
                setText="Open Template File",
                setToolTip="Open the selected template YAML file in the default editor.",
            )
        self.refresh_templates(widget)

    def refresh_templates(self, widget):
        """Refresh the list of templates: shipped and user (a user template
        shadows a shipped one of the same name)."""
        widget.clear()
        for name in self._store.list():
            widget.addItem(name, str(self._store.path(name, self._store.source(name))))

    def _user_template(self, widget) -> str:
        """The selected template's name when it is the user's, else ''
        (logged): the shipped templates are read-only."""
        name = widget.currentText()
        if not name or widget.currentData() is None:
            self.logger.error("No template selected or data is missing.")
            return ""
        if self._store.source(name) != "user":
            self.logger.error(
                f"'{name}' is a shipped template and read-only; save a copy "
                "under another name to change it."
            )
            return ""
        return name

    @staticmethod
    def _sanitize_name(name) -> str:
        """Strip and drop filesystem-invalid characters from a template name."""
        return "".join(c for c in (name or "").strip() if c not in '\\/:*?"<>|')

    def rename_template_safe(self, widget, new_name):
        """Safe rename that checks for None."""
        current = self._user_template(widget)
        if not current:
            return

        new_name = self._sanitize_name(new_name)
        if not new_name:
            self.logger.error("Invalid template name.")
            return
        if new_name == current:
            return  # name unchanged: no-op
        if self._store.exists(new_name):
            self.logger.error("A template with that name already exists.")
            return

        # The store's own rename: its sidecar and active pointer follow.
        if not self._store.rename(current, new_name):
            self.logger.error(f"Template '{current}' could not be renamed.")
            return
        self.logger.info(f"Template renamed to: {self._store.path(new_name, 'user')}")
        widget.init_slot()

    def lbl000(self):
        """Set the ComboBox as editable to allow renaming."""
        self.ui.cmb002.setEditable(True)
        self.ui.cmb002.menu.hide()

    def lbl001(self):
        """Delete the selected (user) template."""
        name = self._user_template(self.ui.cmb002)
        if not name:
            return
        template_path = self._store.path(name, "user")
        if self._store.delete(name):
            self.logger.info(f"Template deleted: {template_path}")
        self.ui.cmb002.init_slot()

    def lbl002(self):
        """Open the selected template in the default editor."""
        template_path = self.ui.cmb002.currentData()
        if not template_path:
            self.logger.error("No template selected.")
            return
        ptk.open_explorer(template_path)

    def b000(self):
        """Create shader network using selected template."""
        self.ui.txt001.clear()

        yaml_file_path = self.ui.cmb002.currentData()
        if not yaml_file_path:
            self.logger.error("No template selected.")
            return

        # Run banner as ONE record: every log record renders as its own
        # paragraph in the output panel, so a line per fact reads as a stack
        # of blank-line-separated sections instead of one header.
        report = self.logger.isEnabledFor(logging.INFO)
        if report:
            self.logger.log_box(
                "CREATE SHADER NETWORK",
                [
                    f"Template : {self.ui.cmb002.currentText()}",
                    f"Textures : {len(self.image_files or [])}",
                ],
            )

        # Lazy, non-fatal plugin load (see __init__ note). A template that
        # doesn't use a missing plugin's node types still restores fine; one
        # that does surfaces per-node errors from the restorer below.
        for plugin in ("shaderFXPlugin", "mtoa"):
            try:
                Plugins.load(plugin)
            except ValueError as e:
                self.logger.debug(str(e))

        restored_nodes = ShaderTemplates.restore_template(
            yaml_file_path, self.image_files or [], logger=self.logger
        )
        self.last_restored_nodes = list(restored_nodes.values())

        if report:
            # Link the surface shader so the user can jump to it, matching
            # what game_shader / mat_updater emit for a created material.
            # Never let the summary raise: nodeType() throws on a name the
            # restore renamed or failed to create, and losing the whole
            # network to a cosmetic lookup would be absurd.
            shader = None
            for node in self.last_restored_nodes:
                try:
                    if node and cmds.objExists(node):
                        classes = cmds.getClassification(cmds.nodeType(node)) or []
                        if any("shader/surface" in c for c in classes):
                            shader = node
                            break
                except Exception:  # noqa: BLE001 - cosmetic lookup only
                    continue
            summary = [f"Nodes created : {len(self.last_restored_nodes)}"]
            if shader:
                link = self.logger.log_link(
                    CoreUtils.short_name(shader), "select", node=str(shader)
                )
                summary.append(f"Shader        : {link}")
            self.logger.log_box("NETWORK CREATED", summary, level="SUCCESS")

    def b001(self):
        """Load texture maps and update GUI."""
        image_files = self.sb.file_dialog(
            file_types=["*.png", "*.jpg", "*.bmp", "*.tga", "*.tiff", "*.gif"],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )

        if image_files:
            self.image_files = image_files
            self.ui.txt001.clear()
            # ONE record for the whole list — a line per file put a paragraph
            # break between every texture.
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.log_group(
                    f"Textures loaded ({len(image_files)})",
                    [ptk.truncate(img, 60) for img in image_files],
                )

    def b002(self):
        """Save current graph as a new shader template."""
        selected_nodes = cmds.ls(selection=True) or []
        if not selected_nodes:
            self.logger.error("Select a material to save its network.")
            return

        # The panel has no name field — prompt, defaulting to the selected
        # material's name. (This replaced a hardcoded "test" placeholder that
        # could only ever write test.yaml once.)
        default_name = str(selected_nodes[0]).rsplit("|", 1)[-1].replace(":", "_")
        QtWidgets = self.sb.QtWidgets
        name, ok = QtWidgets.QInputDialog.getText(
            self.ui, "Save Template", "Template name:", text=default_name
        )
        name = self._sanitize_name(name)
        if not ok or not name:
            self.logger.info("Save cancelled.")
            return

        file_path = str(self._store.path(name, "user"))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if self._store.exists(name):
            self.logger.error(
                f"Template '{name}' already exists — pick another name, or "
                "rename/delete the existing one from the template dropdown's menu."
            )
            return

        ShaderTemplates.save_template(
            selected_nodes,
            file_path,
            logger=self.logger,
            exclude_types=[
                "shadingEngine",
                "transform",
                "mesh",
                "nurbsCurve",
                "camera",
                "light",
            ],
        )
        # No completion line here — ``save_graph`` already announces the save
        # with the node count and a clickable path, and two records for one
        # event render as two paragraphs saying the same thing.
        self.ui.cmb002.init_slot()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("shader_templates", reload=True)
    ui.show(pos="screen", app_exec=True)
