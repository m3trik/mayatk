import pymel.core as pm
from mayatk.core_utils.preview import Preview
from qtpy import QtWidgets
import sys

# Minimal Mock
if not QtWidgets.QApplication.instance():
    try:
        app = QtWidgets.QApplication(sys.argv)
    except:
        pass
else:
    app = QtWidgets.QApplication.instance()

pm.newFile(f=True)
c = pm.polyCube()[0]


def op(objects):
    for obj in objects:
        pm.move(obj, 0, 1, 0, r=True)


class MockOp:
    def perform_operation(self, objs):
        op(objs)


op_inst = MockOp()
chk = QtWidgets.QCheckBox("P")
btn = QtWidgets.QPushButton("C")
prev = Preview(op_inst, chk, btn, message_func=lambda x: None)

# Enable
pm.select(c)
prev.enable()
# Refresh
prev.refresh()

# Manual Undo
print("Undoing...")
pm.undo()
print("Undone.")

name = pm.undoInfo(q=True, redoName=True)
print(f"RedoName: '{name}'")
