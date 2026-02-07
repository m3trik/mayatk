#!/usr/bin/env python
# coding=utf-8
"""
Test both ShadowRig modes: orbit and stretch.

Run this in Maya's Script Editor to verify both modes work correctly.
Creates two test cubes with shadows using each mode side by side.
"""
import pymel.core as pm


def test_shadow_rig_modes():
    """Create test scene with both shadow rig modes for comparison."""
    # Clean up any existing test objects
    for name in [
        "test_cube_orbit",
        "test_cube_stretch",
        "shadow_source",
        "test_cube_orbit_shadow_grp",
        "test_cube_stretch_shadow_grp",
        "combined_shadow_grp",
    ]:
        if pm.objExists(name):
            pm.delete(name)

    # Also clean up contact locators and expressions
    for obj in pm.ls("*_contact_loc", "*_shadow_expr", "*_contact_dm"):
        if pm.objExists(obj):
            pm.delete(obj)

    # Import ShadowRig
    from mayatk.rig_utils.shadow_rig import ShadowRig

    # Create test cubes at different X positions
    cube_orbit = pm.polyCube(name="test_cube_orbit", w=1, h=2, d=1)[0]
    cube_orbit.translate.set(-3, 1, 0)

    cube_stretch = pm.polyCube(name="test_cube_stretch", w=1, h=2, d=1)[0]
    cube_stretch.translate.set(3, 1, 0)

    # Create shadow with ORBIT mode (rotating plane)
    print("\n" + "=" * 60)
    print("Creating ORBIT mode shadow...")
    print("=" * 60)
    shadow_orbit = ShadowRig.create(
        cube_orbit,
        mode="orbit",
        texture_res=256,
        source_name="shadow_source",
    )

    # Create shadow with STRETCH mode (axis-aligned, panning)
    print("\n" + "=" * 60)
    print("Creating STRETCH mode shadow...")
    print("=" * 60)
    shadow_stretch = ShadowRig.create(
        cube_stretch,
        mode="stretch",
        texture_res=256,
        source_name="shadow_source",  # Reuses same source
    )

    # Position the light source between them for easy comparison
    light = pm.PyNode("shadow_source")
    light.translate.set(0, 8, -5)

    # Frame the view
    pm.select([cube_orbit, cube_stretch, light])
    pm.viewFit()
    pm.select(clear=True)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    print("\nTwo cubes created with shadows:")
    print("  LEFT  (X=-3): ORBIT mode  - plane rotates to face away from light")
    print("  RIGHT (X=+3): STRETCH mode - plane stays axis-aligned, scales/translates")
    print("\nMove 'shadow_source' locator to compare behaviors:")
    print("  - ORBIT: Shadow plane rotates, always points away from light")
    print("  - STRETCH: Shadow plane never rotates, only scales X/Z")
    print("\nSelect 'shadow_source' and translate it around to test.")

    return shadow_orbit, shadow_stretch


if __name__ == "__main__":
    test_shadow_rig_modes()
