"""
Direct test for Preview class - Maya Operation Test
Tests that real Maya operations (polyBevel3) work with value changes.
"""

import sys

sys.path.insert(0, r"O:/Cloud/Code/_scripts/mayatk")
sys.path.insert(0, r"O:/Cloud/Code/_scripts/pythontk")

output_file = r"O:/Cloud/Code/_scripts/mayatk/test/temp_tests/preview_test_output.txt"
lines = []


def log(msg):
    print(msg)
    lines.append(str(msg))


def save_output():
    with open(output_file, "w") as f:
        f.write("\n".join(lines))


try:
    import importlib
    import mayatk.core_utils.preview as pm_mod

    importlib.reload(pm_mod)
    from mayatk.core_utils.preview import Preview
    from PySide6 import QtWidgets, QtCore
    import pymel.core as pm

    log("=" * 60)
    log("PREVIEW CLASS - MAYA OPERATION TEST")
    log("=" * 60)

    # Create test objects
    if pm.objExists("test_cube"):
        pm.delete("test_cube")
    cube = pm.polyCube(name="test_cube", w=2, h=2, d=2)[0]
    # Select some edges for bevel
    edges = [f"{cube}.e[0]", f"{cube}.e[1]", f"{cube}.e[2]"]
    pm.select(edges)
    log(f"[Setup] Created cube, selected edges: {len(pm.selected())} items")
    log(f"[Setup] Selection type: {type(pm.selected()[0])}")

    # Mock widgets with slider (like real bevel UI)
    class BevelOp:
        operated_objects = set()

        def __init__(self, width_slider):
            self.width_slider = width_slider

        def perform_operation(self, objects):
            width = self.width_slider.value() / 100.0  # Convert to 0-1 range
            log(f"[BevelOp] Beveling {len(objects)} edges with width={width}")
            # Convert string back to edge components
            edges_to_bevel = pm.ls(objects, flatten=True)
            if edges_to_bevel:
                pm.polyBevel3(
                    edges_to_bevel,
                    fraction=width,
                    segments=1,
                    offsetAsFraction=True,
                    constructionHistory=True,
                )

    chk = QtWidgets.QCheckBox("Preview")
    btn = QtWidgets.QPushButton("Create")
    slider = QtWidgets.QSlider()
    slider.setRange(1, 50)  # Width 0.01 to 0.5
    slider.setValue(10)  # Start at 0.1
    op = BevelOp(slider)

    log(f"[Init] Creating Preview instance...")
    preview = Preview(op, chk, btn, message_func=log)
    log(f"[Init] ScriptJobs: {preview.script_jobs}")

    # Connect slider to refresh (like real usage)
    slider.valueChanged.connect(preview.refresh)

    log("")
    log("--- TEST 1: Enable preview ---")
    log(f"[Before] checkbox.isChecked()={chk.isChecked()}")
    log(f"[Before] Selection: {pm.selected()}")
    chk.setChecked(True)
    QtCore.QCoreApplication.processEvents()

    log(f"[After enable] checkbox.isChecked()={chk.isChecked()}")
    log(f"[After enable] needs_undo={preview.needs_undo}")
    log(f"[After enable] selection_job={preview.selection_job}")
    log(f"[After enable] Selection: {pm.selected()}")

    if not chk.isChecked():
        log("[FAIL] Checkbox unchecked immediately after enable!")
        log(f"  - Current selection: {pm.selected()}")
        log(f"  - operated_objects: {preview.operated_objects}")
        save_output()
        preview.cleanup()
        raise SystemExit(1)

    log("[OK] Checkbox stayed checked after enable!")

    log("")
    log("--- TEST 2: Change slider value ---")
    log(f"[Before] slider.value()={slider.value()}")
    log(f"[Before] Selection: {pm.selected()}")
    slider.setValue(20)  # Change to width 0.2
    QtCore.QCoreApplication.processEvents()
    log(f"[After] slider.value()={slider.value()}")
    log(f"[After slider change] checkbox.isChecked()={chk.isChecked()}")
    log(f"[After slider change] needs_undo={preview.needs_undo}")
    log(f"[After slider change] Selection: {pm.selected()}")

    if not chk.isChecked():
        log("[FAIL] Checkbox unchecked after slider change!")
        log(f"  - Current selection: {[str(s) for s in pm.selected()]}")
        log(f"  - operated_objects: {preview.operated_objects}")
    else:
        log("[OK] Checkbox stayed checked after slider change!")

    log("")
    log("--- TEST 3: Another slider change ---")
    slider.setValue(30)
    QtCore.QCoreApplication.processEvents()
    log(f"[After 2nd slider change] checkbox.isChecked()={chk.isChecked()}")
    log(f"[After 2nd slider change] Selection: {pm.selected()}")

    if not chk.isChecked():
        log("[FAIL] Checkbox unchecked after 2nd slider change!")
    else:
        log("[OK] Checkbox stayed checked after 2nd slider change!")

    log("")
    log("--- TEST 4: Manually disable preview ---")
    chk.setChecked(False)
    QtCore.QCoreApplication.processEvents()
    log(f"[After disable] checkbox.isChecked()={chk.isChecked()}")

    # Cleanup
    preview.cleanup()
    pm.delete("test_cube")
    log("")
    log("=" * 60)
    log("TEST COMPLETE")
    log("=" * 60)

except Exception as e:
    import traceback

    log(f"[ERROR] {e}")
    log(traceback.format_exc())

finally:
    save_output()
