import pymel.core as pm
import maya.cmds as cmds
import maya.api.OpenMaya as om
import math


def repro_manip_loss():
    # 1. Setup Scene
    pm.newFile(force=True)
    sphere = pm.polySphere(name="testSphere")[0]
    pm.select(sphere)

    # 2. Set Custom Pivot using manipPivot
    # We must ensure the move tool is active
    pm.setToolTo("Move")

    # Set a custom rotation and translation for the manipulator
    # manipPivot command modifies the custom pivot
    custom_pos = (5.0, 5.0, 5.0)
    custom_rot = (45.0, 0.0, 0.0)

    pm.manipPivot(p=custom_pos, o=custom_rot)

    # helper to read
    def read_manip():
        p = pm.manipPivot(q=True, p=True)[0]
        r = pm.manipPivot(q=True, o=True)[0]
        return p, r

    initial_p, initial_r = read_manip()
    print(f"Initial: P={initial_p}, R={initial_r}")

    # 3. Perform operation that changes selection (simulate duplicate)
    pm.undoInfo(openChunk=True)
    try:
        dup = pm.duplicate(sphere)[0]
        pm.select(dup)  # This happens during duplicate
    finally:
        pm.undoInfo(closeChunk=True)

    print("Operation done (Selection changed).")

    # 4. Undo
    pm.undo()
    print("Undo done.")

    # 5. Read again
    # We need to make sure node is selected (Undo should have restored selection)
    final_p, final_r = read_manip()
    print(f"Final: P={final_p}, R={final_r}")

    # Check
    # Rounding for float comparison
    def is_close(t1, t2):
        return all(abs(x - y) < 0.001 for x, y in zip(t1, t2))

    if is_close(initial_p, final_p) and is_close(initial_r, final_r):
        print("SUCCESS: Pivot persisted.")
    else:
        print("FAILURE: Pivot lost.")


if __name__ == "__main__":
    repro_manip_loss()
