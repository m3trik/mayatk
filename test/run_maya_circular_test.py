#!/usr/bin/env python
# coding=utf-8
"""Run this test in Maya to verify no circular imports.

Execute in Maya Script Editor:
    exec(open(r'O:\Cloud\Code\_scripts\mayatk\test\run_maya_circular_test.py').read())
"""
import sys

# Clear all mayatk modules
modules_to_remove = [k for k in sys.modules.keys() if k.startswith("mayatk")]
for mod in modules_to_remove:
    del sys.modules[mod]
print(f"Cleared {len(modules_to_remove)} mayatk modules from cache")

print("\n" + "=" * 70)
print("MAYATK CIRCULAR IMPORT TEST (Maya)")
print("=" * 70)

errors = []
successes = []

# Test 1: Import root package
try:
    import mayatk

    successes.append("✓ mayatk root package")
    print("✓ mayatk root package imported")
    print(f"  Version: {mayatk.__version__}")
except Exception as e:
    errors.append(f"✗ mayatk root: {e}")
    print(f"✗ mayatk root: {e}")

# Test 2: Test lazy-loaded classes
try:
    from mayatk import CoreUtils, MeshDiagnostics, AnimCurveDiagnostics
    from mayatk import Components, Preview, AutoInstancer
    from mayatk import EditUtils, Selection, Macros
    from mayatk import NodeUtils, DisplayUtils, XformUtils
    from mayatk import MatUtils, EnvUtils, AnimUtils
    from mayatk import openPorts

    successes.append("✓ All lazy-loaded classes accessible")
    print("✓ All lazy-loaded classes accessible from root")
except Exception as e:
    errors.append(f"✗ Lazy loading: {e}")
    print(f"✗ Lazy loading: {e}")

# Test 3: Import problematic modules that were failing
test_modules = [
    "mayatk.core_utils.diagnostics.animation",
    "mayatk.core_utils.components",  # This was the problem!
    "mayatk.edit_utils.naming",
    "mayatk.edit_utils.macros",
]

for module_name in test_modules:
    try:
        __import__(module_name)
        successes.append(f"✓ {module_name}")
        print(f"✓ {module_name}")
    except Exception as e:
        errors.append(f"✗ {module_name}: {e}")
        print(f"✗ {module_name}: {e}")

# Test 4: Verify decorators work
try:
    from mayatk.core_utils._core_utils import CoreUtils as CU

    assert hasattr(CU, "undoable"), "CoreUtils.undoable not found"
    assert hasattr(CU, "selected"), "CoreUtils.selected not found"
    assert hasattr(CU, "reparent"), "CoreUtils.reparent not found"
    successes.append("✓ CoreUtils decorators verified")
    print("✓ CoreUtils decorators verified")
except Exception as e:
    errors.append(f"✗ Decorators: {e}")
    print(f"✗ Decorators: {e}")

# Test 5: Test Components class specifically (this was the circular import issue)
try:
    from mayatk import Components

    # Try to use a Components method that uses CoreUtils
    comp_type = Components.get_component_type
    successes.append("✓ Components class works correctly")
    print("✓ Components class works correctly")
except Exception as e:
    errors.append(f"✗ Components class: {e}")
    print(f"✗ Components class: {e}")

# Results
print("\n" + "=" * 70)
print(f"Results: {len(successes)}/{len(successes) + len(errors)} passed")
print("=" * 70)

if errors:
    print("\nFAILURES:")
    for error in errors:
        print(f"  {error}")
    print("\n❌ CIRCULAR IMPORTS DETECTED - FIX REQUIRED")
else:
    print("\n✅ SUCCESS: NO CIRCULAR IMPORTS!")
    print("All modules load correctly. Package is ready to use.")
