# !/usr/bin/python
# coding=utf-8
"""Quick usage examples for the matrices module.

Shows the clean access pattern: matrices.Matrices.<method>()
Similar to how components work in core_utils.
"""

import pymel.core as pm
from mayatk.xform_utils import matrices


def test_basic_usage():
    """Test basic matrix operations."""
    # Pure math - compose from SRT
    mx = matrices.Matrices.from_srt(
        translate=(10, 5, 0), rotate_euler_deg=(0, 45, 0), scale=(2, 2, 2)
    )
    print(f"Created matrix: {mx}")

    # Decompose back
    t, r, s = matrices.Matrices.decompose(mx)
    print(f"Translation: {t}")
    print(f"Rotation: {r}")
    print(f"Scale: {s}")


def test_offset_parent_matrix():
    """Test offsetParentMatrix drive pattern."""
    # Create test nodes
    driver = pm.polyCube(name="driver_GRP")[0]
    ctl = pm.circle(name="arm_CTL")[0]

    pm.move(driver, [5, 0, 0])
    pm.rotate(driver, [0, 45, 0])

    # Drive control using the clean syntax
    matrices.Matrices.drive_with_offset_parent_matrix(
        driver_world=driver, driven_ctl=ctl, name="arm_drive"
    )

    print(f"✓ {ctl} driven by {driver} via offsetParentMatrix")


def test_space_switch():
    """Test multi-space switch."""
    # Create spaces
    world = pm.polyCube(name="world_CTR")[0]
    chest = pm.polyCube(name="chest_CTL")[0]
    head = pm.polyCube(name="head_CTL")[0]

    pm.move(chest, [0, 10, 0])
    pm.move(head, [0, 15, 0])

    # Create hand control
    hand = pm.circle(name="hand_CTL")[0]
    pm.move(hand, [5, 10, 0])

    # Build space switch with clean syntax
    matrices.Matrices.build_space_switch(
        control=hand, space_parents=[world, chest, head], attr_name="space"
    )

    print(f"✓ Space switch created on {hand}.space")
    print(f"  0 = {world.name()}")
    print(f"  1 = {chest.name()}")
    print(f"  2 = {head.name()}")


def test_freeze_transforms():
    """Test freeze to offsetParentMatrix."""
    ctl = pm.circle(name="offset_CTL")[0]
    pm.move(ctl, [5, 3, 2])
    pm.rotate(ctl, [0, 45, 0])

    print(f"Before: T={ctl.t.get()}, R={ctl.r.get()}")

    matrices.Matrices.freeze_to_offset_parent_matrix(ctl)

    print(f"After:  T={ctl.t.get()}, R={ctl.r.get()}")
    print("✓ World position maintained, local TRS zeroed")


if __name__ == "__main__":
    print("\nMatrices Module Usage Examples")
    print("=" * 60)
    print("\nImport pattern:")
    print("  from mayatk.xform_utils import matrices")
    print("\nUsage pattern:")
    print("  matrices.Matrices.<method>()")
    print("\nSimilar to:")
    print("  from mayatk.core_utils import components")
    print("  components.Components.<method>()")
    print("=" * 60)

    # Uncomment to run tests in Maya:
    # pm.newFile(force=True)
    # test_offset_parent_matrix()

    # pm.newFile(force=True)
    # test_space_switch()

    # pm.newFile(force=True)
    # test_freeze_transforms()

    # Pure math works anywhere:
    # test_basic_usage()


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------

"""
Clean Access Pattern
====================

The matrices module follows the same pattern as components:

    from mayatk.xform_utils import matrices
    
    # Use the class methods directly
    matrices.Matrices.from_srt(translate=(0, 0, 0))
    matrices.Matrices.drive_with_offset_parent_matrix(driver, control)
    matrices.Matrices.build_space_switch(control, spaces)

This provides:
- Clear namespace separation
- IDE autocomplete support
- Consistent with mayatk patterns
- Clean, readable code

Compare to components module:
    from mayatk.core_utils import components
    
    components.Components.get_components(obj, 'vtx')
    components.Components.get_border_components(faces)
"""
