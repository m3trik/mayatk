"""
Manual test script for Preview class.
Run this in Maya's Script Editor to test Preview functionality.

Usage:
1. Open Maya
2. Run this script in the Script Editor
3. A window will appear with Preview and Create buttons
4. Select some objects and test the workflow
"""

import sys

sys.path.insert(0, r"O:\Cloud\Code\_scripts\mayatk")
sys.path.insert(0, r"O:\Cloud\Code\_scripts\pythontk")

import pymel.core as pm
from PySide6 import QtWidgets, QtCore

# Reload to get latest changes
import importlib
import mayatk.core_utils.preview as preview_module

importlib.reload(preview_module)
from mayatk.core_utils.preview import Preview


class TestOperation:
    """Simple test operation that moves objects up."""

    def __init__(self):
        self.operated_objects = set()

    def perform_operation(self, objects):
        print(f"[TestOperation] Moving {len(objects)} objects up by 1 unit")
        for obj in objects:
            pm.move(obj, 0, 1, 0, r=True)


class TestWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Preview Test")
        self.setWindowFlags(QtCore.Qt.Window)
        self.resize(300, 100)

        layout = QtWidgets.QVBoxLayout(self)

        # Status label
        self.status = QtWidgets.QLabel("Select objects and click Preview")
        layout.addWidget(self.status)

        # Preview checkbox
        self.chk_preview = QtWidgets.QCheckBox("Preview")
        layout.addWidget(self.chk_preview)

        # Create button
        self.btn_create = QtWidgets.QPushButton("Create")
        self.btn_create.setEnabled(False)
        layout.addWidget(self.btn_create)

        # Initialize operation and preview
        self.operation = TestOperation()
        self.preview = Preview(
            self.operation,
            self.chk_preview,
            self.btn_create,
            message_func=self.show_message,
        )

        print("[TestWindow] Preview initialized")
        print(f"[TestWindow] ScriptJobs created: {self.preview.script_jobs}")

    def show_message(self, msg):
        self.status.setText(msg)
        print(f"[Message] {msg}")

    def closeEvent(self, event):
        print("[TestWindow] Cleaning up...")
        self.preview.cleanup()
        super().closeEvent(event)


# Create and show window
if __name__ == "__main__" or True:
    # Clean up any existing window
    try:
        test_window.close()
        test_window.deleteLater()
    except:
        pass

    test_window = TestWindow()
    test_window.show()
    print("\n" + "=" * 50)
    print("TEST INSTRUCTIONS:")
    print("=" * 50)
    print("1. Create a cube: pm.polyCube()")
    print("2. Select the cube")
    print("3. Check the Preview checkbox - cube should move up")
    print("4. Uncheck the Preview checkbox - cube should move back down")
    print("5. Try changing selection while preview is enabled")
    print("=" * 50)
