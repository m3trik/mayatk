import pymel.core as pm
import maya.standalone
import os
import sys


def inspect_sorting():
    print("Initializing Maya Standalone...")
    sys.stdout.flush()
    maya.standalone.initialize(name="python")

    scene_path = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\instance_separator\3_example_of_a_split_assembly_alt.ma"

    if not os.path.exists(scene_path):
        return

    try:
        pm.openFile(scene_path, force=True)
    except Exception:
        return

    print("\n--- Shell Order Analysis ---")
    original_mesh_name = "original_combined_mesh"
    if not pm.objExists(original_mesh_name):
        print(f"{original_mesh_name} not found.")
        return

    orig = pm.PyNode(original_mesh_name)
    # Duplicate to avoid modifying the scene
    dup = pm.duplicate(orig)[0]

    # Separate
    try:
        shells = pm.polySeparate(dup)
    except Exception as e:
        print(f"Error separating: {e}")
        return

    print(f"Separated into {len(shells)} shells.")

    # Check first 10 shells
    for i in range(min(10, len(shells))):
        shell = shells[i]
        bbox = shell.getBoundingBox()
        volume = bbox.width() * bbox.height() * bbox.depth()
        print(f"Shell [{i}] Vol: {volume:.2f}")


if __name__ == "__main__":
    try:
        inspect_sorting()
    except Exception as e:
        print(f"An error occurred: {e}")
