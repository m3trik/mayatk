import sys
import os

# Add script paths
sys.path.append(r"o:\Cloud\Code\_scripts\mayatk")
sys.path.append(r"o:\Cloud\Code\_scripts\pythontk")

import maya.standalone

maya.standalone.initialize(name="python")

import pymel.core as pm
from mayatk.mat_utils.mat_updater import MatUpdater


def run_test():
    scene_path = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\optimize_scene_textures\scenes\modules\C5M_ALPHA_INTERIOR\C5M_ALPHA_INTERIOR_module.ma"

    print(f"Opening scene: {scene_path}")
    try:
        pm.openFile(scene_path, force=True)
    except Exception as e:
        print(f"Error opening scene: {e}")
        return

    print("Scene opened successfully.")

    updater = MatUpdater()

    print("\nRunning MatUpdater (Dry Run)...")
    # We want to see what it would do.
    # Let's assume we want to convert to Unity HDRP or just optimize?
    # The user asked to "test the dry run", implying checking the output logs.

    results = updater.update_materials(
        config={
            "preset": "Unity HDRP",  # Use a preset to trigger map logic
            "dry_run": True,
            "optimize": True,  # Enable optimization to see size limits
            "max_size": 2048,  # Set a limit to see if it triggers
        },
        verbose=True,
    )

    print("\nDry Run Complete.")
    print(f"Processed {len(results)} materials.")


if __name__ == "__main__":
    run_test()
