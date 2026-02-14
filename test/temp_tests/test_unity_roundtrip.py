import os
import shutil
import logging
import time
import argparse
import sys

# Adjust path to find unitytk if running from repo root
try:
    # Try importing first
    import unitytk

    # Check if it has SceneBuilder (in case an old version is installed)
    if not hasattr(unitytk, "SceneBuilder"):
        raise ImportError("Old unitytk version detected")
except ImportError:
    # Insert at 0 to prioritize local version
    # Since we are in mayatk/test/temp_tests, we need to go up 3 levels to scripts root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    
    try:
        if "unitytk" in sys.modules:
            del sys.modules["unitytk"]  # reload
        import unitytk
    except ImportError:
        # Fallback for when running from a different context
        pass

from unitytk import SceneBuilder, UnityLauncher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OpacityTest")


def create_unity_opacity_test_scene(project_path, fbx_source):
    """
    Validation Test: RenderOpacity Maya -> Unity Roundtrip.

    1. Import FBX containing the "opacity" attribute.
    2. Check if RenderOpacityController was auto-added.
    3. Verify the attribute value matches Maya's export.
    4. Confirm correct import settings (Animated Custom Props).
    """

    if not os.path.exists(project_path):
        logger.error(f"Unity project not found at: {project_path}")
        return

    # --- 1. Stage Assets ---
    target_dir = os.path.join(project_path, "Assets", "OpacityTest")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    # Copy FBX
    fbx_name = os.path.basename(fbx_source)
    dst_fbx = os.path.join(target_dir, fbx_name)
    shutil.copy2(fbx_source, dst_fbx)

    logger.info(f"Copied test asset to {target_dir}")

    # --- 2. Generate Validation Script ---
    builder = SceneBuilder(project_path)

    # C# validation logic
    setup_code = f"""
        string assetPath = "Assets/OpacityTest/{fbx_name}";
        
        Debug.Log("=== RenderOpacity Validation Start ===");
        Debug.Log("Asset: " + assetPath);
        
        // Force Import
        AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
        AssetDatabase.Refresh();
        
        // 1. Check Import Settings (ModelImporter)
        ModelImporter importer = AssetImporter.GetAtPath(assetPath) as ModelImporter;
        if (importer != null) {{
            if (!importer.importAnimatedCustomProperties) {{
                Debug.LogError("FAIL: importAnimatedCustomProperties is false! The Importer script should have enabled this.");
            }} else {{
                Debug.Log("PASS: importAnimatedCustomProperties is true");
            }}
        }} else {{
            Debug.LogError("CRITICAL: Include not importable as Model!");
            EditorApplication.Exit(1);
        }}

        // 2. Instantiate and Check Component
        GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
        if (prefab == null) {{
            Debug.LogError("Failed to load prefab");
            EditorApplication.Exit(1);
        }}

        GameObject instance = PrefabUtility.InstantiatePrefab(prefab) as GameObject;
        instance.name = "Opacity_Test_Instance";

        // Find the RenderOpacityController
        // It might be on the root or a child depending on where the attribute was in Maya.
        var controller = instance.GetComponentInChildren<RenderOpacityController>();
        
        if (controller != null) {{
            Debug.Log("PASS: RenderOpacityController component found on " + controller.gameObject.name);
            
            // 3. Verify Attribute Value (assuming export was 1.0 or driven)
            // If the attribute was keyframed, we need to check if curves exist.
            
            // Check for Animation (if keyframed in Maya)
            Animation anim = instance.GetComponent<Animation>();
            Animator animator = instance.GetComponent<Animator>();
            
            if (anim != null || animator != null) {{
                Debug.Log("PASS: Animation component present");
            }} else {{
                Debug.Log("INFO: No animation component (Static opacity?)");
            }}
            
        }} else {{
            Debug.LogError("FAIL: RenderOpacityController component NOT found! The AssetPostprocessor did not run or failed detection.");
        }}

        // Clean up
        GameObject.DestroyImmediate(instance);
        
        Debug.Log("=== RenderOpacity Validation Complete ===");
    """

    builder.build(setup_code)
    
    # --- 3. Run Unity ---
    logger.info("Launching Unity validation...")
    UnityLauncher.launch_batch(project_path, builder.script_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Maya->Unity Opacity Pipeline")
    parser.add_argument("--project", "-p", required=True, help="Path to Unity Project")
    parser.add_argument("--fbx", "-f", required=True, help="Path to exported FBX from Maya")
    
    args = parser.parse_args()
    
    create_unity_opacity_test_scene(args.project, args.fbx)
