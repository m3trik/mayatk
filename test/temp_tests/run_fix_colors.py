import sys
import os

# Ensure we can find the local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
mayatk_root = os.path.dirname(current_dir)
scripts_root = os.path.dirname(mayatk_root)

if mayatk_root not in sys.path:
    sys.path.append(mayatk_root)

from mayatk.env_utils.maya_connection import MayaConnection


def run_fix():
    print("Connecting to Maya...")
    conn = MayaConnection.get_instance()
    if not conn.is_connected:
        conn.connect(mode="auto")

    if not conn.is_connected:
        print(
            "Could not connect to Maya. Make sure Maya is running and command port 7002 is open."
        )
        print('In Maya, run: commandPort -n ":7002" -stp "python"')
        return

    print(f"Connected in {conn.mode} mode.")

    # Code to execute in Maya
    code = r"""
import sys
import os

# Add paths if missing
paths = [
    r"o:\Cloud\Code\_scripts\mayatk",
    r"o:\Cloud\Code\_scripts\pythontk"
]
for p in paths:
    if p not in sys.path:
        sys.path.append(p)

import pymel.core as pm
from mayatk.env_utils.maya_connection import MayaConnection
from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

# Reload to ensure we have the latest code
MayaConnection.reload_modules(["mayatk.core_utils.diagnostics.scene_diag"], verbose=True)

print("\n" + "="*50)
print("RUNNING FIX COLOR SPACES DIAGNOSTIC")
print("="*50)

# Debug info
try:
    print(f"Current Scene: {pm.sceneName()}")
    
    # Check CM Prefs
    cm_prefs = {
        "configFilePath": pm.colorManagementPrefs(q=True, configFilePath=True),
        "renderingSpaceName": pm.colorManagementPrefs(q=True, renderingSpaceName=True),
        "viewTransformName": pm.colorManagementPrefs(q=True, viewTransformName=True),
        "policyFileName": pm.colorManagementPrefs(q=True, policyFileName=True),
    }
    print("CM Prefs:", cm_prefs)

    # Check available spaces
    from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
    available = SceneDiagnostics._get_available_color_spaces()
    print(f"Available Spaces ({len(available)}): {available}")
    
    # Inspect a specific node
    if pm.objExists("file429"):
        node = pm.PyNode("file429")
        print(f"\n--- Inspection of file429 ---")
        print(f"Initial colorSpace: '{node.colorSpace.get()}'")
        
        print("Setting to 'Raw'...")
        node.colorSpace.set("Raw")
        print(f"Intermediate colorSpace: '{node.colorSpace.get()}'")
        
        print("Setting back to 'sRGB'...")
        node.colorSpace.set("sRGB")
        print(f"Final colorSpace: '{node.colorSpace.get()}'")

except Exception as e:
    print(f"Error getting debug info: {e}")

# Run the fix
result = SceneDiagnostics.fix_missing_color_spaces(
    verbose=True, 
    scan_all=True,
    auto_detect=True,
    force_update=True # Set to True to force re-assignment of all nodes
)

print("\nDiagnostic Result:")
print(f"Fixed Count: {result['fixed_count']}")
print(f"Color Space Fallback: {result['color_space']}")
print(f"Raw Space Fallback: {result['raw_space']}")
print("="*50 + "\n")
"""

    print("Sending command to Maya...")
    output = conn.execute(code, capture_output=True)

    if output:
        print("\n" + "=" * 50)
        print("MAYA OUTPUT")
        print("=" * 50)
        print(output)
        print("=" * 50 + "\n")
    else:
        print(
            "Command sent! Check your Maya Script Editor for output (no output captured)."
        )


if __name__ == "__main__":
    run_fix()
