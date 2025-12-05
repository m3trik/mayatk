# !/usr/bin/python
# coding=utf-8
import logging
from typing import Callable, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.display_utils._display_utils import DisplayUtils


class Preview:
    def __init__(
        self,
        operation_instance,
        preview_checkbox,
        create_button,
        finalize_func=None,
        message_func=print,
        enable_on_show=False,
        disable_on_hide=True,
    ):
        """Provides an interactive layer for previewing and finalizing operations in a 3D editing environment.

        This class enables real-time previews of operations by linking UI elements to backend functionality.
        It efficiently manages the state and execution of operations, maintaining a clean undo stack and enabling
        rollback of changes during the preview process.

        Parameters:
            operation_instance: Instance implementing a perform_operation method.
            preview_checkbox: QCheckBox instance to toggle the preview.
            create_button: QPushButton instance to finalize the changes.
            finalize_func: Optional callable to finalize changes.
            message_func: Optional callable for messaging, default is print.
            enable_on_show: Boolean, if True enables the preview when the window shows.
            disable_on_hide: Boolean, if True disables the preview when the window hides.

        Usage Example:
            ```python
            class BevelSlots:
                def __init__(self, *args, **kwargs):
                    self.sb = kwargs.get('switchboard')
                    self.ui = self.sb.loaded_ui.bevel
                    self.preview = Preview(
                        self,
                        self.ui.chk000,
                        self.ui.b000,
                        message_func=self.sb.message_box
                    )
                    self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)

                def perform_operation(self, objects):
                    width = self.ui.s000.value()
                    segments = self.ui.s001.value()
                    Bevel.bevel(objects, width, segments)

            # Instantiate BevelSlots.
            # Now toggling the UI checkbox will enable/disable the preview,
            # and clicking the UI button will apply the beveled edges.
            ```
        """
        # Basic input validation
        if not hasattr(operation_instance, "perform_operation"):
            raise ValueError(
                "operation_instance must have a 'perform_operation' method"
            )
        if preview_checkbox is None or create_button is None:
            raise ValueError("preview_checkbox and create_button cannot be None")

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        self.message_func = message_func or self.logger.info

        self.operated_objects = set()
        self.operation_performed = False
        self.needs_undo = False
        self.prevState = None
        self.internal_undo_triggered = False
        self.operation_instance = operation_instance
        self.operation_instance.operated_objects = self.operated_objects
        self.preview_checkbox = preview_checkbox
        self.create_button = create_button
        self.finalize_func = finalize_func
        self.preview_checkbox.clicked.connect(self.toggle)
        self.create_button.clicked.connect(self.finalize_changes)

        # Safely get window reference
        try:
            self.window = self.create_button.window()
        except Exception:
            self.window = None

        self.init_show_hide_behavior(enable_on_show, disable_on_hide)
        # Create a scriptJob to disable preview on undo event
        try:
            self.script_job = pm.scriptJob(
                event=["Undo", self.disable_on_external_undo]
            )
        except Exception as e:
            self.logger.warning(f"Could not create scriptJob: {e}")
            self.script_job = None
        self.is_refreshing = False

    def __del__(self):
        """Ensure the scriptJob is killed when the instance is deleted."""
        try:
            if (
                self.script_job is not None
                and hasattr(pm, "scriptJob")
                and pm.scriptJob(exists=self.script_job)
            ):
                pm.scriptJob(kill=self.script_job, force=True)
        except:
            pass

    def disable_on_external_undo(self):
        """Disables the preview functionality on external undo operations only."""
        if (
            not self.internal_undo_triggered
            and not self.is_refreshing
            and self.preview_checkbox.isChecked()
        ):
            self.disable()
        self.internal_undo_triggered = False  # Reset flag after checking

    def init_show_hide_behavior(self, enable_on_show, disable_on_hide):
        self.enable_on_show = enable_on_show
        self.disable_on_hide = disable_on_hide

        if self.window and hasattr(self.window, "on_show"):
            try:
                self.window.on_show.connect(self.conditionally_enable)
            except Exception as e:
                self.logger.warning(f"Could not connect to on_show signal: {e}")

        if self.window and hasattr(self.window, "on_hide"):
            try:
                self.window.on_hide.connect(self.conditionally_disable)
            except Exception as e:
                self.logger.warning(f"Could not connect to on_hide signal: {e}")

    def conditionally_enable(self):
        if self.enable_on_show:
            self.enable()

    def conditionally_disable(self):
        if self.disable_on_hide:
            self.disable()

    def toggle(self, state):
        """Toggles the preview on or off.

        Parameters:
            state: Boolean state to set.
        """
        if state:
            self.enable()
        else:
            self.disable()

    def enable(self):
        """Enables the preview and sets up the initial state."""
        self.prevState = pm.undoInfo(q=True, state=True)
        pm.undoInfo(state=True)
        pm.undoInfo(openChunk=True, chunkName="PreviewChunk")

        try:
            selected_items = pm.selected()
            if selected_items:
                # Convert components to strings for hashing
                self.operated_objects.update(str(item) for item in selected_items)

                self.needs_undo = False  # Set to False when enabling for the first time

                self.preview_checkbox.blockSignals(True)
                self.preview_checkbox.setChecked(True)
                self.preview_checkbox.blockSignals(False)

                self.create_button.setEnabled(True)
                self.refresh()
                # operation_performed is now determined by the success of refresh()
                self.operation_performed = self.needs_undo
            else:
                self.message_func("No objects selected.")
                self.disable()
        except Exception as e:
            self.logger.exception(f"Exception in enable: {e}")
            # If enable fails, make sure we don't leave needs_undo as True
            self.needs_undo = False

    def disable(self):
        """Disables the preview and reverts to the initial state."""
        try:
            self.undo_if_needed()
        except Exception as e:
            self.logger.warning(f"Error during undo in disable: {e}")

        try:
            pm.undoInfo(closeChunk=True)
        except Exception as e:
            self.logger.warning(f"Error closing undo chunk: {e}")

        if self.prevState is not None:
            try:
                pm.undoInfo(state=self.prevState)
            except Exception as e:
                self.logger.warning(f"Error restoring undo state: {e}")

        self.operated_objects.clear()
        self.preview_checkbox.setChecked(False)
        self.create_button.setEnabled(False)

    def undo_if_needed(self):
        """Executes undo operation if required."""
        if self.needs_undo:
            self.logger.debug("Performing undo as operation was previously successful")
            self.internal_undo_triggered = True
            pm.undoInfo(closeChunk=True)
            try:
                pm.undo()
            except RuntimeError as e:
                self.logger.warning(f"Undo operation failed: {e}")
            finally:
                pm.undoInfo(openChunk=True, chunkName="PreviewChunk")

            self.needs_undo = False
        else:
            self.logger.debug(
                "No undo needed - operation did not complete successfully"
            )

    def refresh(self, *args):
        """Refreshes the preview to reflect any changes."""
        if not self.preview_checkbox.isChecked():
            return
        self.is_refreshing = True
        self.undo_if_needed()
        pm.undoInfo(openChunk=True, chunkName="PreviewChunk")
        operation_successful = False
        try:
            # Convert strings back to PyMel objects for operation
            operated_objects = pm.ls(self.operated_objects, flatten=True)
            self.operation_instance.perform_operation(operated_objects)

            # Add the operated objects to the isolation set if one exists.
            DisplayUtils.add_to_isolation_set(operated_objects)
            operation_successful = True
        except Exception as e:
            self.logger.exception(f"Exception during operation: {e}")
        finally:
            pm.undoInfo(closeChunk=True)
        # Only set needs_undo to True if the operation completed successfully
        self.needs_undo = operation_successful
        self.is_refreshing = False

    def finalize_changes(self):
        """Finalizes the preview changes and calls the finalize_func if provided."""
        self.needs_undo = False
        self.disable()
        if self.finalize_func:
            self.finalize_func()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
