import pymel.core as pm
import maya.cmds as cmds


def debug_stingray():
    try:
        # Load plugin if needed
        if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin")

        # Create node
        node = pm.shadingNode("StingrayPBS", asShader=True)
        pm.setAttr(f"{node}.initgraph", True)

        print(f"Node created: {node}")

        # List all attributes that look like TEX_metallic_map
        print("\n--- Searching for TEX_metallic_map attributes ---")
        attrs = cmds.listAttr(str(node))
        relevant_attrs = [a for a in attrs if "TEX_metallic_map" in a]
        for a in relevant_attrs:
            print(f"  {a}")

        # Check specific attribute details
        if "TEX_metallic_map" in relevant_attrs:
            print("\n--- TEX_metallic_map Details ---")
            attr_type = cmds.getAttr(f"{node}.TEX_metallic_map", type=True)
            print(f"  Type: {attr_type}")

            # Check children
            try:
                children = cmds.attributeQuery(
                    "TEX_metallic_map", node=str(node), listChildren=True
                )
                print(f"  Children: {children}")
            except Exception as e:
                print(f"  No children found via attributeQuery: {e}")

            # Check if compound
            is_compound = cmds.attributeQuery(
                "TEX_metallic_map", node=str(node), multi=True
            )  # multi is not compound but check anyway
            print(f"  Is Multi: {is_compound}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    try:
        import maya.standalone

        maya.standalone.initialize()
    except:
        pass
    debug_stingray()
