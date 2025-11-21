import pymel.core as pm
import mayatk as mtk


def test_freeze_standard_cube():
    pm.newFile(force=True)
    cube = pm.polyCube(name="pCube1")[0]

    # Move it so there is something to freeze
    cube.translate.set(10, 20, 30)
    cube.rotate.set(45, 45, 45)
    cube.scale.set(2, 2, 2)

    print(f"Testing freeze on {cube}")
    print(
        f"Initial State: T={cube.translate.get()}, R={cube.rotate.get()}, S={cube.scale.get()}"
    )

    try:
        # Try freezing everything
        mtk.freeze_transforms(cube, t=True, r=True, s=True)
        print("Freeze All successful")
        print(
            f"Post Freeze All: T={cube.translate.get()}, R={cube.rotate.get()}, S={cube.scale.get()}"
        )
    except Exception as e:
        print(f"Freeze All Failed: {e}")

    # Reset
    cube.translate.set(10, 20, 30)
    cube.rotate.set(45, 45, 45)
    cube.scale.set(2, 2, 2)

    try:
        # Try freezing only translation
        mtk.freeze_transforms(cube, t=True)
        print("Freeze Translate successful")
        print(
            f"Post Freeze Translate: T={cube.translate.get()}, R={cube.rotate.get()}, S={cube.scale.get()}"
        )
    except Exception as e:
        print(f"Freeze Translate Failed: {e}")


if __name__ == "__main__":
    try:
        test_freeze_standard_cube()
    except Exception as e:
        print(e)
