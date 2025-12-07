import maya.standalone
import maya.cmds as cmds
import sys
import os

# Prevent UI load
os.environ["MAYA_SKIP_USERSETUP_PY"] = "1"

try:
    maya.standalone.initialize(name="python")

    # Load plugin
    if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
        cmds.loadPlugin("shaderFXPlugin")

    node = cmds.createNode("StingrayPBS")

    print("\n--- ATTRIBUTES ---")
    attrs = cmds.listAttr(node)
    tex_attrs = [a for a in attrs if "TEX_" in a]
    use_attrs = [a for a in attrs if "use_" in a]

    print("Texture Attributes:")
    for a in sorted(tex_attrs):
        print(f"  {a}")

    print("\nUse Attributes:")
    for a in sorted(use_attrs):
        print(f"  {a}")

except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()

# Force exit to avoid hanging on UI threads
os._exit(0)
