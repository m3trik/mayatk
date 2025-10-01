#!/usr/bin/python
# coding=utf-8
"""
Mirror Function Debug Test Module

This module creates test scenarios and logs detailed information about the mirror function
to help diagnose issues with pivot calculations and coordinate transformations.
"""

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError as error:
    print(f"Maya import error: {error}")

import os
import sys
from typing import Dict, List, Any

# Add the mayatk path if running as standalone
mayatk_path = os.path.dirname(os.path.dirname(__file__))
if mayatk_path not in sys.path:
    sys.path.insert(0, mayatk_path)

try:
    from mayatk.edit_utils import EditUtils
    from mayatk.xform_utils import XformUtils
    from mayatk.core_utils import NodeUtils
except ImportError as e:
    print(f"Import error: {e}")
    print("Please run this from Maya with mayatk in the Python path")


class MirrorDebugger:
    """Debug class for testing mirror functionality"""

    def __init__(self):
        self.test_results = []
        self.current_test = ""

    def log(self, message: str, level: str = "INFO"):
        """Log messages with test context"""
        full_message = f"[{level}] {self.current_test}: {message}"
        print(full_message)
        self.test_results.append(full_message)

    def create_test_sphere(self, name: str = "testSphere", radius: float = 2.0) -> str:
        """Create a test sphere for mirroring tests"""
        # Delete existing test sphere if it exists
        if pm.objExists(name):
            pm.delete(name)

        # Create sphere
        sphere = pm.polySphere(
            name=name, radius=radius, subdivisionsAxis=12, subdivisionsHeight=8
        )[0]

        # Delete half to create hemisphere for testing
        pm.select(f"{name}.f[48:95]")  # Select upper half faces
        pm.delete()

        # Move to a test position
        pm.xform(sphere, translation=(3, 1, 0), worldSpace=True)
        pm.xform(sphere, rotation=(15, 30, 0), worldSpace=True)

        self.log(
            f"Created test hemisphere '{name}' at position (3, 1, 0) with rotation (15, 30, 0)"
        )
        return str(sphere)

    def test_pivot_calculations(self, obj: str) -> Dict[str, Any]:
        """Test all pivot calculation methods"""
        self.current_test = "PIVOT_CALC"

        results = {}
        pivot_types = [
            "object",
            "manip",
            "world",
            "center",
            "xmin",
            "xmax",
            "ymin",
            "ymax",
            "zmin",
            "zmax",
        ]

        # Set manipulator pivot to object for testing
        pm.select(obj)
        pm.manipPivot(rp=True)

        for pivot_type in pivot_types:
            try:
                # Test both single axis and full vector
                single_axis = XformUtils.get_operation_axis_pos(obj, pivot_type, 0)
                full_vector = XformUtils.get_operation_axis_pos(obj, pivot_type, None)

                results[pivot_type] = {
                    "single_axis": single_axis,
                    "full_vector": full_vector,
                    "success": True,
                }

                self.log(
                    f"Pivot '{pivot_type}': single={single_axis}, full={full_vector}"
                )

            except Exception as e:
                results[pivot_type] = {"error": str(e), "success": False}
                self.log(f"Pivot '{pivot_type}' FAILED: {e}", "ERROR")

        return results

    def test_coordinate_transformations(self, obj: str) -> Dict[str, Any]:
        """Test coordinate space transformations"""
        self.current_test = "COORD_TRANSFORM"

        # Get object matrix
        obj_node = pm.PyNode(obj)
        obj_matrix = obj_node.getMatrix(worldSpace=True)

        # Test world to object space transformation
        world_points = [
            [0, 0, 0],  # origin
            [1, 0, 0],  # x-axis
            [0, 1, 0],  # y-axis
            [0, 0, 1],  # z-axis
            [3, 1, 0],  # object position
        ]

        results = {"object_matrix": list(obj_matrix), "transformations": []}

        for i, world_point in enumerate(world_points):
            try:
                # Transform to object space
                local_point = list(pm.dt.Point(world_point) * obj_matrix.inverse())

                # Transform back to world space
                back_to_world = list(pm.dt.Point(local_point) * obj_matrix)

                transform_result = {
                    "world_input": world_point,
                    "local_result": local_point,
                    "back_to_world": back_to_world,
                    "round_trip_error": [
                        abs(a - b) for a, b in zip(world_point, back_to_world)
                    ],
                }

                results["transformations"].append(transform_result)

                self.log(
                    f"Point {i}: World{world_point} -> Local{local_point} -> World{back_to_world}"
                )

            except Exception as e:
                self.log(f"Coordinate transform {i} FAILED: {e}", "ERROR")

        return results

    def test_mirror_operation(
        self,
        obj: str,
        axis: str = "x",
        pivot: str = "object",
        use_object_axes: bool = True,
    ) -> Dict[str, Any]:
        """Test a complete mirror operation with detailed logging"""
        self.current_test = f"MIRROR_{axis}_{pivot}_{use_object_axes}"

        try:
            # Get initial object state
            initial_pos = pm.xform(obj, q=True, translation=True, worldSpace=True)
            initial_rot = pm.xform(obj, q=True, rotation=True, worldSpace=True)

            self.log(f"Initial position: {initial_pos}")
            self.log(f"Initial rotation: {initial_rot}")

            # Test pivot calculation
            pivot_result = XformUtils.get_operation_axis_pos(obj, pivot, None)
            self.log(f"Pivot calculation result: {pivot_result}")

            # Perform mirror operation
            self.log(
                f"Calling mirror with axis='{axis}', pivot='{pivot}', use_object_axes={use_object_axes}"
            )

            result = EditUtils.mirror(
                obj,
                axis=axis,
                pivot=pivot,
                use_object_axes=use_object_axes,
                mergeMode=0,  # Don't separate for testing
            )

            if result:
                result_obj = result[0] if isinstance(result, list) else result

                # Get final object state
                final_pos = pm.xform(
                    result_obj, q=True, translation=True, worldSpace=True
                )
                final_rot = pm.xform(result_obj, q=True, rotation=True, worldSpace=True)

                self.log(f"Mirror successful, result: {result_obj}")
                self.log(f"Final position: {final_pos}")
                self.log(f"Final rotation: {final_rot}")

                return {
                    "success": True,
                    "result_object": str(result_obj),
                    "initial_pos": initial_pos,
                    "initial_rot": initial_rot,
                    "final_pos": final_pos,
                    "final_rot": final_rot,
                    "pivot_used": pivot_result,
                }
            else:
                self.log("Mirror operation returned no result", "ERROR")
                return {"success": False, "error": "No result returned"}

        except Exception as e:
            self.log(f"Mirror operation FAILED: {e}", "ERROR")
            return {"success": False, "error": str(e)}

    def run_comprehensive_test(self) -> Dict[str, Any]:
        """Run all mirror tests and return comprehensive results"""
        print("=" * 80)
        print("STARTING COMPREHENSIVE MIRROR DEBUG TEST")
        print("=" * 80)

        # Create test object
        test_obj = self.create_test_sphere("debugSphere")

        # Test 1: Pivot calculations
        print("\n" + "-" * 60)
        print("TEST 1: PIVOT CALCULATIONS")
        print("-" * 60)
        pivot_results = self.test_pivot_calculations(test_obj)

        # Test 2: Coordinate transformations
        print("\n" + "-" * 60)
        print("TEST 2: COORDINATE TRANSFORMATIONS")
        print("-" * 60)
        coord_results = self.test_coordinate_transformations(test_obj)

        # Test 3: Mirror operations
        print("\n" + "-" * 60)
        print("TEST 3: MIRROR OPERATIONS")
        print("-" * 60)

        mirror_tests = [
            ("x", "object", True),
            ("x", "object", False),
            ("x", "center", True),
            ("x", "center", False),
            ("x", "manip", True),
            ("x", "world", True),
            ("-x", "object", True),
            ("y", "object", True),
            ("z", "object", True),
        ]

        mirror_results = {}
        for axis, pivot, use_obj_axes in mirror_tests:
            # Recreate test object for each test
            test_obj = self.create_test_sphere(
                f"debugSphere_{axis}_{pivot}_{use_obj_axes}"
            )
            mirror_results[f"{axis}_{pivot}_{use_obj_axes}"] = (
                self.test_mirror_operation(test_obj, axis, pivot, use_obj_axes)
            )

        # Compile final results
        final_results = {
            "pivot_calculations": pivot_results,
            "coordinate_transformations": coord_results,
            "mirror_operations": mirror_results,
            "all_logs": self.test_results,
        }

        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)

        # Summary of pivot tests
        failed_pivots = [
            k for k, v in pivot_results.items() if not v.get("success", True)
        ]
        if failed_pivots:
            print(f"FAILED PIVOT CALCULATIONS: {failed_pivots}")
        else:
            print("ALL PIVOT CALCULATIONS: PASSED")

        # Summary of mirror tests
        failed_mirrors = [
            k for k, v in mirror_results.items() if not v.get("success", True)
        ]
        if failed_mirrors:
            print(f"FAILED MIRROR OPERATIONS: {failed_mirrors}")
        else:
            print("ALL MIRROR OPERATIONS: PASSED")

        print(f"\nTotal log entries: {len(self.test_results)}")
        print("=" * 80)

        return final_results


def run_mirror_debug_test():
    """Main function to run the debug test"""
    debugger = MirrorDebugger()
    return debugger.run_comprehensive_test()


if __name__ == "__main__":
    # Run the test when executed directly
    results = run_mirror_debug_test()
