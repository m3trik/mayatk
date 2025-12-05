#!/usr/bin/env python
# coding=utf-8
"""Test for circular import resolution in mayatk package.

This test validates that all modules can be imported without circular dependency errors.
Run this in Maya or via command port.
"""
import sys
import os

# Ensure mayatk is in path
script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)


def test_circular_imports():
    """Test that all mayatk modules can be imported without circular dependency errors."""

    print("\n" + "=" * 60)
    print("MAYATK CIRCULAR IMPORT TEST")
    print("=" * 60)

    # First, clean any existing mayatk imports
    modules_to_remove = [k for k in sys.modules.keys() if k.startswith("mayatk")]
    for mod in modules_to_remove:
        del sys.modules[mod]

    errors = []
    successful_imports = []

    # Test 1: Import root package
    try:
        import mayatk

        successful_imports.append("mayatk (root)")
        print("✓ mayatk root package imported")
    except Exception as e:
        errors.append(f"mayatk root: {e}")
        print(f"✗ mayatk root: {e}")

    # Test 2: Import modules that were failing
    test_modules = [
        "mayatk.core_utils.diagnostics.animation",
        "mayatk.edit_utils.naming",
        "mayatk.edit_utils.macros",
        "mayatk.nurbs_utils.image_tracer",
        "mayatk.edit_utils._edit_utils",
        "mayatk.xform_utils._xform_utils",
        "mayatk.uv_utils._uv_utils",
        "mayatk.rig_utils._rig_utils",
        "mayatk.rig_utils.wheel_rig",
        "mayatk.rig_utils.tube_rig",
        "mayatk.mat_utils._mat_utils",
        "mayatk.mat_utils.stingray_arnold_shader",
        "mayatk.light_utils.hdr_manager",
        "mayatk.env_utils.workspace_manager",
        "mayatk.env_utils.reference_manager",
        "mayatk.display_utils._display_utils",
        "mayatk.cam_utils._cam_utils",
        "mayatk.anim_utils._anim_utils",
        "mayatk.core_utils.preview",
        "mayatk.edit_utils.bridge",
        "mayatk.edit_utils.bevel",
        "mayatk.edit_utils.selection",
        "mayatk.edit_utils.primitives",
        "mayatk.edit_utils.snap",
        "mayatk.edit_utils.cut_on_axis",
        "mayatk.edit_utils.duplicate_linear",
        "mayatk.edit_utils.duplicate_grid",
        "mayatk.edit_utils.mirror",
    ]

    for module_name in test_modules:
        try:
            __import__(module_name)
            successful_imports.append(module_name)
            print(f"✓ {module_name}")
        except Exception as e:
            errors.append(f"{module_name}: {e}")
            print(f"✗ {module_name}: {e}")

    # Test 3: Test that lazy-loaded classes are accessible
    try:
        from mayatk import CoreUtils, MeshDiagnostics, AnimCurveDiagnostics
        from mayatk import Components, Preview, AutoInstancer
        from mayatk import EditUtils, Selection, Macros

        successful_imports.append("All lazy-loaded classes accessible")
        print("✓ All lazy-loaded classes accessible from root")
    except Exception as e:
        errors.append(f"Lazy loading: {e}")
        print(f"✗ Lazy loading: {e}")

    # Test 4: Verify decorators work
    try:
        from mayatk.core_utils._core_utils import CoreUtils

        # Check that decorators exist
        assert hasattr(CoreUtils, "undoable"), "CoreUtils.undoable not found"
        assert hasattr(CoreUtils, "selected"), "CoreUtils.selected not found"
        assert hasattr(CoreUtils, "reparent"), "CoreUtils.reparent not found"
        successful_imports.append("CoreUtils decorators verified")
        print("✓ CoreUtils decorators verified")
    except Exception as e:
        errors.append(f"Decorators: {e}")
        print(f"✗ Decorators: {e}")

    # Results
    print("\n" + "=" * 60)
    print(
        f"Results: {len(successful_imports)}/{len(successful_imports) + len(errors)} passed"
    )
    print("=" * 60)

    if errors:
        print("\nFAILURES:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\nSUCCESS: NO CIRCULAR IMPORTS DETECTED!")
        return True


if __name__ == "__main__":
    success = test_circular_imports()
    sys.exit(0 if success else 1)
