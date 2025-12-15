import sys
import os

# Ensure paths
sys.path.append(r"O:\Cloud\Code\_scripts\mayatk")
sys.path.append(r"O:\Cloud\Code\_scripts\pythontk")

print("Initializing Maya standalone...")
import maya.standalone

maya.standalone.initialize(name="python")

print("Creating QApplication...")
try:
    from PySide2 import QtWidgets

    app = QtWidgets.QApplication(sys.argv)
except ImportError:
    print("Could not import PySide2 or create QApplication")

print("Importing pymel.core...")
import pymel.core as pm

print("Importing mayatk...")
import mayatk
from mayatk.anim_utils.segment_keys import SegmentKeys

scene_path = r"O:\Cloud\Code\_scripts\mayatk\test\temp_tests\test.ma"

print(f"Opening scene: {scene_path}")
if not os.path.exists(scene_path):
    print(f"File not found: {scene_path}")
    sys.exit(1)

try:
    pm.openFile(scene_path, force=True)
except Exception as e:
    print(f"Error opening file: {e}")
    sys.exit(1)

# Objects of interest
obj_names = ["WARNING_STREAMER_LCTR", "ELEVATOR_ASSEMBLY_LCTR"]
objects = []
for name in obj_names:
    if pm.objExists(name):
        objects.append(pm.PyNode(name))
    else:
        print(f"Object {name} not found!")

if len(objects) < 2:
    print("Not enough objects found to check for shared curves.")
    sys.exit(0)

# Get curves for each
curves_map = {}
for obj in objects:
    curves = pm.listConnections(obj, type="animCurve", s=True, d=False) or []
    curves_map[obj.name()] = set(curves)
    print(f"{obj.name()} has {len(curves)} curves.")

# Check intersection
obj1 = objects[0].name()
obj2 = objects[1].name()

print(f"\nHierarchy Check:")
print(f"{obj1} Parent: {objects[0].getParent()}")
print(f"{obj2} Parent: {objects[1].getParent()}")

shared = curves_map[obj1].intersection(curves_map[obj2])

if shared:
    print(f"\nFOUND SHARED CURVES between {obj1} and {obj2}:")
    for curve in shared:
        print(f"  - {curve.name()} (Type: {curve.type()})")
        # Check what attributes they connect to
        conns = pm.listConnections(curve, p=True, s=False, d=True)
        for conn in conns:
            print(f"      -> {conn}")
else:
    print(f"\nNo shared curves found between {obj1} and {obj2}.")

print("\nRunning Test Stagger (Dry Run)...")
import mayatk
from mayatk.anim_utils.segment_keys import SegmentKeys

# Collect segments manually to see grouping
segments = SegmentKeys.collect_segments(objects)
print(f"Collected {len(segments)} segments.")

# Test 1: Default Grouping (Per Segment)
groups = SegmentKeys.group_segments(segments, mode="per_segment")
print(f"Per-Segment Groups: {len(groups)}")
for i, g in enumerate(groups):
    print(f"  Group {i}: {g['obj']} ({g['start']} - {g['end']})")

# Test 2: Overlap Grouping
groups_overlap = SegmentKeys.group_segments(segments, mode="overlap_groups")
print(f"Overlap Groups: {len(groups_overlap)}")
for i, g in enumerate(groups_overlap):
    print(f"  Group {i}: {g['objects']} ({g['start']} - {g['end']})")
