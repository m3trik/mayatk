# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class Preview:
    def __init__(self, *args, **kwargs):
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
            class BevelEdgesSlots:
                def __init__(self):
                    self.sb = self.switchboard()
                    self.ui = self.sb.bevel_edges
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
                    BevelEdges.bevel_edges(objects, width, segments)

            # Instantiate BevelEdgesSlots.
            # Now toggling the UI checkbox will enable/disable the preview,
            # and clicking the UI button will apply the beveled edges.
            ```
        """
        self.init_preview(*args, **kwargs)

    def init_preview(
        self,
        operation_instance,
        preview_checkbox,
        create_button,
        finalize_func=None,
        message_func=print,
        enable_on_show=True,
        disable_on_hide=True,
    ):
        """Initialize the state for preview operations."""
        self.operated_objects = set()
        self.operation_performed = False
        self.needs_undo = False
        self.prevState = None
        self.operation_instance = operation_instance
        self.operation_instance.operated_objects = self.operated_objects
        self.preview_checkbox = preview_checkbox
        self.create_button = create_button
        self.finalize_func = finalize_func
        self.message_func = message_func
        self.preview_checkbox.clicked.connect(self.toggle)
        self.create_button.clicked.connect(self.finalize_changes)
        self.window = self.create_button.window()

        self._enable_on_show = None
        self.enable_on_show = enable_on_show

        self._disable_on_hide = None
        self.disable_on_hide = disable_on_hide

    @property
    def enable_on_show(self):
        """Boolean property to enable or disable the preview when the window shows."""
        return self._enable_on_show

    @enable_on_show.setter
    def enable_on_show(self, value):
        if self._enable_on_show is not None and hasattr(self.window, "on_show"):
            self.window.on_show.disconnect(self.enable)

        self._enable_on_show = value

        if self._enable_on_show and hasattr(self.window, "on_show"):
            self.window.on_show.connect(self.enable)

    @property
    def disable_on_hide(self):
        """Boolean property to enable or disable the preview when the window hides."""
        return self._disable_on_hide

    @disable_on_hide.setter
    def disable_on_hide(self, value):
        if self._disable_on_hide is not None and hasattr(self.window, "on_hide"):
            self.window.on_hide.disconnect(self.disable)

        self._disable_on_hide = value

        if self._disable_on_hide and hasattr(self.window, "on_hide"):
            self.window.on_hide.connect(self.disable)

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
            selected_objs = pm.selected()
            if selected_objs:
                self.operated_objects.update(selected_objs)
                self.needs_undo = True

                self.preview_checkbox.blockSignals(True)  # Block signals temporarily
                self.preview_checkbox.setChecked(True)
                self.preview_checkbox.blockSignals(False)  # Unblock signals

                self.create_button.setEnabled(True)
                self.refresh()  # Perform the operation directly
                self.operation_performed = True
            else:
                self.message_func("No objects selected.")
                self.disable()
        except Exception as e:
            print(f"Exception in enable: {e}")

    def disable(self):
        """Disables the preview and reverts to the initial state."""
        self.undo_if_needed()
        pm.undoInfo(closeChunk=True)

        if self.prevState is not None:
            pm.undoInfo(state=self.prevState)

        self.operated_objects.clear()
        self.preview_checkbox.setChecked(False)
        self.create_button.setEnabled(False)

    def undo_if_needed(self):
        """Executes undo operation if required."""
        if self.needs_undo:
            pm.undoInfo(closeChunk=True)
            try:
                pm.undo()
            except RuntimeError:
                pass
            finally:
                pm.undoInfo(openChunk=True, chunkName="PreviewChunk")

            # Filter out non-existent or invalid nodes
            valid_operated_objects = {
                obj for obj in self.operated_objects if pm.objExists(obj)
            }

            if valid_operated_objects:
                try:
                    pm.select(valid_operated_objects)
                except pm.MayaNodeError:
                    print(
                        f"Failed to reselect some objects. Only existing objects will be selected."
                    )

            self.needs_undo = False

    def refresh(self, *args):
        """Refreshes the preview to reflect any changes.

        Parameters:
            *args: Any additional arguments. (Currently unused)
        """
        if not self.preview_checkbox.isChecked():
            return
        self.undo_if_needed()
        pm.undoInfo(openChunk=True, chunkName="PreviewChunk")
        try:
            self.operation_instance.perform_operation(self.operated_objects)
        except Exception as e:
            print(f"Exception during operation: {e}")
        finally:
            pm.undoInfo(closeChunk=True)
        self.needs_undo = True

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
