import pymel.core as pm
import maya.cmds as cmds


def inspect_stingray():
    if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
        pm.loadPlugin("shaderFXPlugin")

    node = pm.shadingNode("StingrayPBS", asShader=True)
    print(f"Node type: {node.type()}")

    print("\nAll Attributes (first 100):")
    attrs = pm.listAttr(node)
    for i, attr in enumerate(attrs):
        if i > 100:
            break
        print(attr)


if __name__ == "__main__":
    try:
        inspect_stingray()
    except Exception as e:
        print(e)
