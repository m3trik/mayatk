import pymel.core as pm
import maya.cmds as cmds


def repro():
    pm.newFile(force=True)

    # Create 2 canisters
    canisters = []
    for i in range(2):
        body = pm.polyCylinder(r=1, h=4, name=f"Body_{i}")[0]
        lid = pm.polySphere(r=1, name=f"Lid_{i}")[0]
        lid.setTranslation([0, 2.5, 0])
        lid.setScale([1, 0.2, 1])
        grp = pm.group(body, lid, name=f"Canister_{i}")

        # Rotate
        grp.rotate.set([45, 45, 0])
        canisters.append(grp)

    # Combine
    all_parts = []
    for grp in canisters:
        all_parts.extend(grp.getChildren())
        pm.parent(grp.getChildren(), world=True)
        pm.delete(grp)

    combined = pm.polyUnite(all_parts, name="Combined", ch=False)[0]

    # Separate
    print(f"Separating {combined}...")
    shells = pm.polySeparate(combined, ch=False)
    print(f"Shells: {shells}")

    # Check Volumes and Shapes
    for shell in shells:
        try:
            shell = pm.PyNode(shell)
        except:
            continue

        if isinstance(shell, pm.nodetypes.Transform):
            shape = shell.getShape()
            print(f"Shell: {shell}, Shape: {shape}")

            try:
                vol = pm.polyEvaluate(shell, volume=True)
                print(f"  Volume (Transform): {vol}")
            except Exception as e:
                print(f"  Volume (Transform) Failed: {e}")

            if shape:
                try:
                    vol = pm.polyEvaluate(shape, volume=True)
                    print(f"  Volume (Shape): {vol}")
                except Exception as e:
                    print(f"  Volume (Shape) Failed: {e}")

            # Check BBox
            bbox = shell.getBoundingBox(space="world")
            print(f"  BBox Center: {bbox.center()}")


if __name__ == "__main__":
    repro()
