"""
Quick debug test for mirror orientation issue
"""

import pymel.core as pm
from mayatk.edit_utils._edit_utils import EditUtils


def debug_mirror_issue():
    # Get selected object
    sel = pm.ls(sl=True, transforms=True)
    if not sel:
        print("Select a mesh transform first")
        return

    obj = sel[0]
    print(f"Testing mirror on: {obj}")

    # Test different mirror modes
    test_cases = [
        ("poly", False, "world axes poly"),
        ("poly", True, "object axes poly"),
        ("api", False, "world axes api"),
        ("api", True, "object axes api"),
    ]

    for method, use_obj_axes, desc in test_cases:
        # Duplicate for test
        dup = pm.duplicate(
            obj,
            rr=True,
            name=f"{obj.nodeName()}_{method}_{'obj' if use_obj_axes else 'world'}",
        )[0]

        print(f"\n=== Testing {desc} ===")
        print(f"Original pivot: {pm.xform(dup, q=True, ws=True, rp=True)}")
        print(f"Original rotation: {pm.xform(dup, q=True, ws=True, ro=True)}")

        try:
            result = EditUtils.mirror(
                dup,
                axis="x",
                pivot="object",
                mergeMode=0,  # No separation
                use_object_axes=use_obj_axes,
                method=method,
                debug=True,
            )

            if result:
                final_obj = (
                    result
                    if isinstance(result, pm.nt.Transform)
                    else result[0] if result else dup
                )
                print(f"Result pivot: {pm.xform(final_obj, q=True, ws=True, rp=True)}")
                print(
                    f"Result rotation: {pm.xform(final_obj, q=True, ws=True, ro=True)}"
                )

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    debug_mirror_issue()
