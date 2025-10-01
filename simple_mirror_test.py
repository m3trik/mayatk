#!/usr/bin/python
# coding=utf-8
"""
SIMPLE MIRROR DEBUG TEST - Copy and paste this entire script into Maya's Script Editor

This creates a test case and runs mirror operations with detailed logging.
Results will be printed to Maya's Script Editor output.
"""

import pymel.core as pm


def debug_mirror_simple():
    """Simple debug test for mirror function"""

    print("=" * 60)
    print("MAYA MIRROR DEBUG TEST")
    print("=" * 60)

    # Clean up any existing test objects
    for obj in ["testSphere", "testSphere_mirrored"]:
        if pm.objExists(obj):
            pm.delete(obj)

    # Create test hemisphere
    print("\n1. Creating test hemisphere...")
    sphere = pm.polySphere(
        name="testSphere", radius=2, subdivisionsAxis=12, subdivisionsHeight=8
    )[0]

    # Delete half to make hemisphere
    pm.select(f"{sphere}.f[48:95]")
    pm.delete()

    # Position and rotate the hemisphere
    pm.xform(sphere, translation=(3, 1, 0), worldSpace=True)
    pm.xform(sphere, rotation=(15, 30, 0), worldSpace=True)

    pos = pm.xform(sphere, q=True, translation=True, worldSpace=True)
    rot = pm.xform(sphere, q=True, rotation=True, worldSpace=True)
    print(f"   Position: {pos}")
    print(f"   Rotation: {rot}")

    # Test pivot calculations
    print("\n2. Testing pivot calculations...")

    # Import the required modules
    try:
        from mayatk.xform_utils import XformUtils
        from mayatk.edit_utils import EditUtils

        # Set manipulator pivot
        pm.select(sphere)
        pm.manipPivot(rp=True)

        # DEBUG: Test manipulator pivot queries
        print("   DEBUG: Testing manipulator pivot queries...")
        try:
            manip_raw = pm.manipPivot(q=True, p=True)
            print(
                f"   Raw manipPivot query result: {manip_raw} (type: {type(manip_raw)})"
            )

            # Test different manipulator settings
            pm.manipPivot(p=(3, 1, 0))  # Set explicit position
            manip_set = pm.manipPivot(q=True, p=True)
            print(f"   After setting manip pivot to (3,1,0): {manip_set}")

        except Exception as manip_err:
            print(f"   Manipulator pivot query failed: {manip_err}")

        # Test different pivot types
        pivots = ["object", "center", "manip", "world"]
        pivot_results = {}

        for pivot in pivots:
            try:
                result = XformUtils.get_operation_axis_pos(sphere, pivot, None)
                pivot_results[pivot] = result
                print(f"   {pivot}: {result}")
            except Exception as e:
                print(f"   {pivot}: ERROR - {e}")
                pivot_results[pivot] = None

        # Test mirror operations
        print("\n3. Testing mirror operations...")

        test_cases = [
            ("x", "object", True, 0, "Object space X mirror (merged)"),
            ("x", "object", False, 0, "World space X mirror (merged)"),
            ("x", "center", True, 0, "Object space X mirror at center (merged)"),
            ("-x", "object", True, -1, "Object space -X mirror (separated)"),
        ]

        for i, (axis, pivot, use_obj_axes, merge_mode, description) in enumerate(
            test_cases
        ):
            print(f"\n   Test {i+1}: {description}")

            # Duplicate original for each test
            test_obj = pm.duplicate(sphere, name=f"testSphere_test{i+1}")[0]

            try:
                # Get pre-mirror state
                pre_pos = pm.xform(test_obj, q=True, translation=True, worldSpace=True)
                print(f"      Pre-mirror position: {pre_pos}")

                # Calculate pivot for this test
                pivot_pos = XformUtils.get_operation_axis_pos(test_obj, pivot, None)
                print(f"      Pivot position ({pivot}): {pivot_pos}")

                # DEBUG: Test polyMirrorFace parameters directly
                print(f"      DEBUG: Testing polyMirrorFace parameters...")

                # Calculate what parameters would be passed
                axis_mapping = {
                    "x": (0, 0),
                    "-x": (0, 1),
                    "y": (1, 0),
                    "-y": (1, 1),
                    "z": (2, 0),
                    "-z": (2, 1),
                }

                axis_val, axis_direction = axis_mapping[axis]
                world_pivot = XformUtils.get_operation_axis_pos(test_obj, pivot, None)

                if use_obj_axes:
                    # Object space parameters
                    obj_matrix = pm.PyNode(test_obj).getMatrix(worldSpace=True)
                    pivot_point = list(pm.dt.Point(world_pivot) * obj_matrix.inverse())

                    kwargs = {
                        "worldSpace": False,
                        "axis": axis_val,
                        "axisDirection": axis_direction,
                        "pivot": tuple(pivot_point),
                        "mergeMode": 0,
                        "ch": True,
                    }
                else:
                    # World space parameters
                    kwargs = {
                        "worldSpace": True,
                        "axis": axis_val,
                        "axisDirection": axis_direction,
                        "pivot": tuple(world_pivot),
                        "mergeMode": 0,
                        "ch": True,
                    }

                print(f"      polyMirrorFace kwargs: {kwargs}")

                # Test direct polyMirrorFace call
                try:
                    # Get face count before mirror
                    pre_face_count = pm.polyEvaluate(test_obj, face=True)

                    direct_result = pm.polyMirrorFace(test_obj, **kwargs)
                    print(f"      Direct polyMirrorFace result: {direct_result}")

                    if direct_result:
                        # Check if faces were added to the object
                        post_face_count = pm.polyEvaluate(test_obj, face=True)
                        added_faces = post_face_count - pre_face_count
                        print(
                            f"      Faces added: {added_faces} (before: {pre_face_count}, after: {post_face_count})"
                        )

                        # The object itself now contains the mirrored geometry
                        try:
                            result_pos = pm.xform(
                                test_obj,
                                q=True,
                                translation=True,
                                worldSpace=True,
                            )
                            print(f"      Object position after mirror: {result_pos}")
                        except Exception as pos_err:
                            print(f"      Failed to query object position: {pos_err}")

                except Exception as direct_err:
                    print(f"      Direct polyMirrorFace FAILED: {direct_err}")

                # Now try EditUtils.mirror
                try:
                    print(
                        f"      Calling EditUtils.mirror with mergeMode={merge_mode}..."
                    )
                    result = EditUtils.mirror(
                        test_obj,
                        axis=axis,
                        pivot=pivot,
                        use_object_axes=use_obj_axes,
                        mergeMode=merge_mode,
                    )

                    if result:
                        result_obj = result[0] if isinstance(result, list) else result
                        print(
                            f"      Result object: {result_obj} (type: {type(result_obj)})"
                        )

                        try:
                            post_pos = pm.xform(
                                result_obj, q=True, translation=True, worldSpace=True
                            )
                            print(f"      Post-mirror position: {post_pos}")

                            # Check if it's a valid transform
                            if (
                                pm.objExists(result_obj)
                                and pm.nodeType(result_obj) == "transform"
                            ):
                                print(f"      Valid transform node confirmed")
                                post_face_count = pm.polyEvaluate(result_obj, face=True)
                                print(f"      Final face count: {post_face_count}")
                            else:
                                print(
                                    f"      WARNING: Result is not a valid transform node"
                                )

                            print(f"      SUCCESS")
                        except Exception as pos_err:
                            print(f"      Failed to query result position: {pos_err}")
                    else:
                        print(f"      FAILED: No result returned")

                except Exception as mirror_err:
                    print(f"      EditUtils.mirror FAILED: {mirror_err}")

            except Exception as e:
                print(f"      FAILED: {e}")

        print("\n4. Summary:")
        print(
            "   Check the viewport to see if the mirrored objects are positioned correctly."
        )
        print(
            "   The original hemisphere should be mirrored across the specified axis."
        )
        print(
            "   If the mirrors appear in wrong positions, there's likely an issue with"
        )
        print("   coordinate space transformations or pivot calculations.")

    except ImportError as e:
        print(f"\nERROR: Cannot import mayatk modules: {e}")
        print("Make sure mayatk is in your Python path and run this from Maya.")

    print("\n" + "=" * 60)


# Run the test
debug_mirror_simple()
