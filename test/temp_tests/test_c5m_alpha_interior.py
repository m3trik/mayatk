import sys
import os
import maya.standalone
import maya.cmds as cmds

# Ensure paths are set up
sys.path.append(r"o:\Cloud\Code\_scripts\mayatk")
sys.path.append(r"o:\Cloud\Code\_scripts\pythontk")


def run_test():
    print("Initializing Maya...")
    maya.standalone.initialize(name="python")

    import pymel.core as pm

    # Load plugins
    try:
        cmds.loadPlugin("mtoa")
    except:
        print("Could not load mtoa")

    scene_path = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\optimize_scene_textures\scenes\modules\C5M_ALPHA_INTERIOR\C5M_ALPHA_INTERIOR_module.ma"

    print(f"Opening scene: {scene_path}")
    try:
        cmds.file(scene_path, open=True, force=True)
    except Exception as e:
        print(f"Error opening scene: {e}")
        return

    print("Scene opened successfully.")

    import mayatk.mat_utils.material_updater as mat_updater
    import mayatk.mat_utils.game_shader as game_shader
    import pythontk.img_utils.texture_map_factory as texture_factory
    import importlib

    # Reload modules to ensure we have latest code
    importlib.reload(texture_factory)
    importlib.reload(game_shader)
    importlib.reload(mat_updater)

    print("\n--- Starting Material Update Test ---")

    updater = mat_updater.MaterialUpdater()

    # Get all materials in the scene
    materials = pm.ls(type="shadingEngine")
    print(f"Found {len(materials)} shading engines.")

    # Filter for relevant materials (exclude default ones)
    target_materials = []
    for sg in materials:
        if sg.name() in ["initialShadingGroup", "initialParticleSE"]:
            continue
        # Get the surface shader
        shader = sg.surfaceShader.inputs()
        if shader:
            target_materials.append(shader[0])

    print(f"Found {len(target_materials)} target shaders.")
    for mat in target_materials:
        print(f"  - {mat.name()} ({mat.type()})")

    # Run update on all materials
    # We'll use a standard config, e.g., Unity HDRP, which is complex enough to test most things
    # We also want to test the 'rename=False' behavior if possible, but that depends on the factory config.
    # The MaterialUpdater usually sets up the config.

    print("\nRunning update_materials with config='Unity HDRP'...")

    try:
        # We use a dry run first to see what would happen, or just run it?
        # The user said "test this scene extensively". I'll run it.
        # I'll enable verbose logging.

        updater.update_materials(
            materials=target_materials,
            config="Unity HDRP",
            convert=True,
            optimize=False,  # Don't resize for now, just check logic
            verbose=True,
        )

        print("\n--- Update Complete ---")

        # Validation
        # Check if textures are connected
        for mat in target_materials:
            print(f"\nValidating {mat.name()}...")
            # Check connections (basic check)
            # For Unity HDRP/Stingray, we expect certain connections

            # Just listing connections for inspection
            conns = cmds.listConnections(mat.name(), c=True, p=True, d=False, s=True)
            if conns:
                for i in range(0, len(conns), 2):
                    print(f"  {conns[i]} <-- {conns[i+1]}")
            else:
                print("  No input connections found.")

    except Exception as e:
        print(f"ERROR during update: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run_test()
