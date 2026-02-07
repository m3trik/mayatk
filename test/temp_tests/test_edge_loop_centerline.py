"""
Test script for edge loop centerline extraction.
Run this in Maya's Script Editor (Python tab).

Tests:
1. get_edge_loop_centers() extracts correct number of loops from a cylinder
2. Centerline points match edge loop positions
3. num_joints=-1 creates joints at each edge ring
"""

import pymel.core as pm

# Clean scene
pm.newFile(force=True)

# Import after scene reset to ensure clean state
from mayatk.rig_utils.tube_rig import TubePath, TubeRig

print("\n" + "=" * 60)
print("TEST: Edge Loop Centerline Extraction")
print("=" * 60)

# Create a cylinder with known edge loop count
# sx=8 (subdivisions around), sy=5 (subdivisions along height)
# This creates 6 edge loops (5 internal + 2 caps, but caps are faces not loops)
# Actually sy=5 gives 5 divisions along the height = 6 rows of vertices
cyl = pm.polyCylinder(
    r=1, h=10, sx=8, sy=5, sz=1, ax=(0, 1, 0), name="TestTube", ch=False
)[0]

print(f"\nCreated cylinder: {cyl}")
print(f"  - Height: 10")
print(f"  - Subdivisions Y: 5 (should give 6 edge loops)")

# Test 1: Extract edge loop centers directly
print("\n--- Test 1: get_edge_loop_centers ---")
centerline, loop_count = TubePath.get_edge_loop_centers(cyl)

print(f"Loop count found: {loop_count}")
print(f"Centerline points: {len(centerline)}")

# With sy=5, we expect 6 rows of vertices (edge loops)
expected_loops = 6
if loop_count == expected_loops:
    print(f"✓ PASS: Found expected {expected_loops} edge loops")
else:
    print(f"✗ FAIL: Expected {expected_loops} loops, got {loop_count}")

# Print centerline Y positions to verify they're evenly spaced
print("\nCenterline Y positions:")
for i, pt in enumerate(centerline):
    print(f"  Point {i}: Y = {pt.y:.2f}")

# Test 2: Unified dispatcher with num_joints=-1
print("\n--- Test 2: TubePath.get_centerline(num_joints=-1) ---")
pts, resolved = TubePath.get_centerline(cyl, num_joints=-1)
print(f"Resolved num_joints: {resolved}")
if resolved == expected_loops:
    print(f"✓ PASS: Unified dispatcher resolved {resolved} joints")
else:
    print(f"✗ FAIL: Expected {expected_loops}, got {resolved}")

# Test 3: Unified dispatcher with explicit count (should use bbox)
print("\n--- Test 3: TubePath.get_centerline(num_joints=5) ---")
pts3, resolved3 = TubePath.get_centerline(cyl, num_joints=5)
print(f"Resolved num_joints: {resolved3}")
if resolved3 == 5:
    print(f"✓ PASS: Explicit count preserved")
else:
    print(f"✗ FAIL: Expected 5, got {resolved3}")

# Test 4: Create rig with num_joints=-1
print("\n--- Test 4: TubeRig with num_joints=-1 ---")
pm.select(clear=True)

rig = TubeRig(cyl, rig_name="EdgeLoopTest")
bundle = rig.build(strategy="spline", num_joints=-1)

joint_count = len(bundle.joints)
print(f"Joints created: {joint_count}")

if joint_count == loop_count:
    print(f"✓ PASS: Joint count ({joint_count}) matches loop count ({loop_count})")
else:
    print(f"✗ FAIL: Joint count ({joint_count}) != loop count ({loop_count})")

# Print joint Y positions
print("\nJoint Y positions:")
for i, jnt in enumerate(bundle.joints):
    pos = jnt.getTranslation(space="world")
    print(f"  Joint {i}: Y = {pos.y:.2f}")

# Test 3: Verify controls were created
print("\n--- Test 3: Controls Created ---")
if bundle.controls and len(bundle.controls) == 3:
    print(f"✓ PASS: 3 controls created (start, mid, end)")
    for ctrl in bundle.controls:
        print(f"  - {ctrl.name()}")
else:
    print(
        f"✗ FAIL: Expected 3 controls, got {len(bundle.controls) if bundle.controls else 0}"
    )

# Test 4: Test with different cylinder
print("\n--- Test 4: Different mesh (sy=10) ---")
pm.newFile(force=True)

cyl2 = pm.polyCylinder(
    r=0.5, h=5, sx=6, sy=10, sz=1, ax=(1, 0, 0), name="HorizontalTube", ch=False
)[0]

centerline2, loop_count2 = TubePath.get_edge_loop_centers(cyl2)
expected_loops2 = 11  # sy=10 gives 11 rows

print(f"Cylinder with sy=10, axis=(1,0,0)")
print(f"Loop count found: {loop_count2}")
print(f"Expected: {expected_loops2}")

if loop_count2 == expected_loops2:
    print(f"✓ PASS: Found expected {expected_loops2} edge loops")
else:
    print(f"✗ FAIL: Expected {expected_loops2} loops, got {loop_count2}")

print("\n" + "=" * 60)
print("TESTS COMPLETE")
print("=" * 60)
