#!/usr/bin/env python
# coding=utf-8
"""
Dynamic package-agnostic Maya import test.
Introspects package structure and tests everything automatically.
"""
import sys
import ast
from pathlib import Path

# Determine package path dynamically
script_path = Path(__file__).resolve()
package_path = script_path.parent.parent
package_name = package_path.name

if str(package_path) not in sys.path:
    sys.path.insert(0, str(package_path))


def discover_subpackages(package_dir):
    """Discover all subpackages."""
    subpackages = []
    for item in package_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            if item.name.startswith("_") or item.name in [
                "test",
                "tests",
                "build",
                "dist",
            ]:
                continue
            subpackages.append(item.name)
    return sorted(subpackages)


def discover_classes_in_module(module_file):
    """Extract all class names from a module file."""
    try:
        with open(module_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(module_file))

        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
        return classes
    except Exception:
        return []


def discover_implementation_classes(package_dir, subpackages):
    """Discover all classes in implementation modules."""
    impl_classes = {}
    for subpkg in subpackages:
        subpkg_dir = package_dir / subpkg
        for impl_file in subpkg_dir.glob("_*.py"):
            if impl_file.name == "__init__.py":
                continue
            classes = discover_classes_in_module(impl_file)
            for cls in classes:
                impl_classes[cls] = f"{subpkg}.{impl_file.stem}"
    return impl_classes


def test_dynamic_imports():
    """Test package imports dynamically based on discovered structure."""

    print("=" * 70)
    print(f"DYNAMIC MAYA IMPORT TEST FOR {package_name.upper()}")
    print("=" * 70)

    package_dir = package_path / package_name

    # Step 1: Discover structure
    print(f"\n📂 Discovering package structure...")
    subpackages = discover_subpackages(package_dir)
    print(f"   Found {len(subpackages)} subpackages")

    impl_classes = discover_implementation_classes(package_dir, subpackages)
    print(f"   Found {len(impl_classes)} classes in implementation modules")

    # Step 2: Clear any cached imports
    print(f"\n🗑️  Clearing cached imports...")
    to_remove = [key for key in sys.modules.keys() if key.startswith(package_name)]
    for key in to_remove:
        del sys.modules[key]
    print(f"   Removed {len(to_remove)} cached modules")

    # Step 3: Test root package import
    print(f"\n1️⃣ Testing root package import...")
    try:
        pkg = __import__(package_name)
        print(f"   ✅ Successfully imported {package_name}")
    except Exception as e:
        print(f"   ❌ Failed to import {package_name}: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Step 4: Test lazy-loaded classes
    print(f"\n2️⃣ Testing lazy-loaded classes...")
    success_count = 0
    fail_count = 0

    for class_name, module_path in sorted(impl_classes.items()):
        try:
            cls = getattr(pkg, class_name, None)
            if cls is None:
                print(f"   ⚠️  {class_name}: Not exposed in root package")
            else:
                print(f"   ✅ {class_name}: Available via lazy loading")
                success_count += 1
        except Exception as e:
            print(f"   ❌ {class_name}: {e}")
            fail_count += 1

    print(f"\n   Results: {success_count} accessible, {fail_count} failed")

    # Step 5: Test subpackage access
    print(f"\n3️⃣ Testing subpackage accessibility...")
    subpkg_count = 0
    for subpkg in subpackages:
        try:
            sub = getattr(pkg, subpkg, None)
            if sub is not None:
                print(f"   ✅ {subpkg}: Accessible")
                subpkg_count += 1
            else:
                print(f"   ℹ️  {subpkg}: Not exposed (lazy-loading only)")
        except Exception as e:
            print(f"   ❌ {subpkg}: {e}")

    # Step 6: Test decorator functionality (if CoreUtils exists)
    if "CoreUtils" in impl_classes:
        print(f"\n4️⃣ Testing CoreUtils decorator functionality...")
        try:
            CoreUtils = getattr(pkg, "CoreUtils")

            @CoreUtils.undoable
            def test_function():
                return "success"

            result = test_function()
            if result == "success":
                print(f"   ✅ @CoreUtils.undoable decorator works")
            else:
                print(f"   ❌ Decorator didn't return expected value")
        except Exception as e:
            print(f"   ❌ Decorator test failed: {e}")

    # Final summary
    print("\n" + "=" * 70)
    print("✅ DYNAMIC IMPORT TEST COMPLETE")
    print("=" * 70)
    print(f"Package: {package_name}")
    print(f"Subpackages: {len(subpackages)}")
    print(f"Classes found: {len(impl_classes)}")
    print(f"Classes accessible: {success_count}")
    print("=" * 70)

    return fail_count == 0


if __name__ == "__main__":
    success = test_dynamic_imports()
    sys.exit(0 if success else 1)
