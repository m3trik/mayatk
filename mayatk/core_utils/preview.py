# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class Preview:
    """A class to handle preview functionality in a GUI.
    It provides functionalities for preview toggle, enabling/disabling preview,
    refreshing the preview, and finalizing the changes.

    Example:
        from PySide2.QtWidgets import QApplication, QPushButton, QCheckBox, QSlider

        def operation_func():
            print("Operation performed.")

        def finalize_func():
            print("Changes finalized.")

        app = QApplication([])  # Required to create a QApplication instance first

        preview_checkbox = QCheckBox()  # Replace with actual instance.
        create_button = QPushButton()  # Replace with actual instance.
        value_slider = QSlider()  # Example widget that emits a signal

        preview = Preview(preview_checkbox, create_button, operation_func, finalize_func)

        # Connect value_slider's valueChanged signal to the preview's refresh method
        value_slider.valueChanged.connect(preview.refresh)

        # Checking the checkbox will execute `operation_func` and print "Operation performed."
        preview_checkbox.setChecked(True)

        # Moving the slider will also call `operation_func`
        value_slider.setValue(5)

        # Pressing the button will execute `finalize_func` and print "Changes finalized."
        create_button.click()

        app.exec_()  # Start the application's event loop
    """

    def __init__(self, *args, **kwargs):
        """Initialize the Preview class with preview-related UI elements and functions."""
        self.init_preview_mixin(*args, **kwargs)

    def init_preview_mixin(
        self,
        preview_checkbox,
        create_button,
        operation_func=None,
        finalize_func=None,
        message_func=print,
    ):
        """Initialize the mixin for the preview. Connects UI elements to their respective functionalities.

        Parameters:
            preview_checkbox (QWidget): The UI element for the preview checkbox.
            create_button (QWidget): The UI element for the create/apply button.
            operation_func (callable, optional): The function to execute for the preview operation.
            finalize_func (callable, optional): The function to finalize the changes.
            message_func (callable, optional): The function to display messages. Defaults to print.
        """
        self.preview_checkbox = preview_checkbox
        self.create_button = create_button
        self.operation_func = operation_func
        self.finalize_func = finalize_func
        self.message_func = message_func
        self.needs_undo = False

        self.preview_checkbox.clicked.connect(self.toggle_preview)
        self.create_button.clicked.connect(self.finalize_changes)
        self.window = self.create_button.window()
        if hasattr(self.window, "on_hide"):
            # Un-check the preview button on window hide event.
            self.window.on_hide.connect(self.disable_preview)

    def toggle_preview(self, state):
        """Toggle the preview state based on the provided state.

        Parameters:
            state (bool): The state to set the preview to. True for enabled, False for disabled.
        """
        if state:
            self.enable_preview()
        else:
            self.disable_preview()

    def enable_preview(self):
        """Enable the preview, perform the operation, and enable the 'Apply Changes' button."""
        if pm.selected():
            self.needs_undo = False
            self.refresh()
            self.preview_checkbox.setChecked(True)
            self.create_button.setEnabled(True)
            # Mute the command reporting.
            pm.commandEcho(state=False)
        else:
            self.message_func("No objects selected.")
            self.disable_preview()

    def disable_preview(self):
        """Disable the preview, undo the last operation if needed, and disable the 'Apply Changes' button."""
        self.undo_if_needed()
        self.preview_checkbox.setChecked(False)
        self.create_button.setEnabled(False)
        # Restore the command reporting to its default state.
        pm.commandEcho(state=True)

    def refresh(self, *args):
        """Refresh the preview if the checkbox is checked and there's no previous operation pending.

        Note:
            *args is needed because the method may be called as a slot in PyQt,
            which might pass extra arguments depending on the signal that triggered the slot.
        """
        if self.operation_func is None:
            raise ValueError("Operation function not defined!")

        if not self.preview_checkbox.isChecked():
            return

        # If we've already performed an operation, undo it before doing a new one.
        self.undo_if_needed()

        pm.undoInfo(openChunk=True)
        self.operation_func()
        pm.undoInfo(closeChunk=True)

        self.needs_undo = True

    def finalize_changes(self):
        """Apply Changes and emit signal.
        This disables the preview and, if defined, calls the finalize function.
        """
        self.needs_undo = False
        self.disable_preview()

        if self.finalize_func:
            self.finalize_func()

    def undo_if_needed(self):
        """Undo the previous operation if there is one pending.

        This method will attempt to perform an undo operation if `self.needs_undo` is True.
        If there's no command to undo, it catches the RuntimeError and passes.
        """
        if self.needs_undo:
            try:
                pm.undo()
            except RuntimeError:  # There are no more commands to undo.
                pass


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
