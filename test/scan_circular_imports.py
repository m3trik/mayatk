#!/usr/bin/env python
# coding=utf-8
"""
Dynamic package-agnostic circular import scanner.
Introspects package structure and tests based on actual configuration.
Run this BEFORE loading in Maya to identify problems.
"""
import os
import re
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
    """Discover all subpackages by finding directories with __init__.py."""
    subpackages = []
    for item in package_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            # Skip special directories
            if item.name.startswith("_") or item.name in [
                "test",
                "tests",
                "build",
                "dist",
            ]:
                continue
            subpackages.append(item.name)
    return sorted(subpackages)


def discover_regular_modules(subpackage_dir):
    """Find regular module files (not starting with _) in a subpackage."""
    regular_modules = []
    for item in subpackage_dir.glob("*.py"):
        if item.name.startswith("_"):
            continue
        if item.name == "__init__.py":
            continue
        regular_modules.append(item.stem)
    return regular_modules


def extract_class_from_module(module_file):
    """Extract the main class name from a module file (e.g., _core_utils.py -> CoreUtils)."""
    try:
        with open(module_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(module_file))

        # Find classes that look like main utility classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Typically main classes follow pattern: CoreUtils, NodeUtils, etc.
                if "Utils" in node.name or "Mixin" in node.name:
                    return node.name
    except Exception:
        pass
    return None


def discover_implementation_modules(subpackage_dir):
    """Find implementation modules (files starting with _) and extract class names."""
    impl_modules = {}
    for item in subpackage_dir.glob("_*.py"):
        if item.name == "__init__.py":
            continue
        module_name = item.stem  # e.g., '_core_utils'
        class_name = extract_class_from_module(item)
        if class_name:
            impl_modules[module_name] = class_name
    return impl_modules


def build_dynamic_patterns(package_name, subpackages, impl_map, regular_modules):
    """Build regex patterns dynamically based on discovered structure."""
    patterns = []

    # Pattern 1: Direct subpackage imports (from package import subpackage)
    if subpackages:
        subpkg_regex = "|".join(re.escape(sp) for sp in subpackages)
        patterns.append(
            (
                rf"from {package_name} import ({subpkg_regex})(?![._])",
                "Import subpackage directly (should import from implementation module)",
            )
        )

    # Pattern 2: Imports from subpackage __init__ that are NOT regular modules
    # This allows importing regular modules like maya_menu_handler but not classes
    if subpackages and impl_map:
        subpkg_regex = "|".join(re.escape(sp) for sp in subpackages)
        # Build negative lookahead for regular modules
        if regular_modules:
            regular_regex = "|".join(re.escape(mod) for mod in regular_modules)
            patterns.append(
                (
                    rf"from {package_name}\.({subpkg_regex}) import (?!_)(?!{regular_regex}\b)",
                    "Import class from subpackage __init__ (should import from _*.py implementation)",
                )
            )
        else:
            patterns.append(
                (
                    rf"from {package_name}\.({subpkg_regex}) import (?!_)",
                    "Import from subpackage __init__ (should import from _*.py implementation)",
                )
            )

    # Pattern 3: Module-qualified class access (subpackage.ClassName.method)
    for impl_module, class_name in impl_map.items():
        if class_name:
            # Get subpackage from impl_module path
            patterns.append(
                (
                    rf"\w+\.{re.escape(class_name)}\.",
                    f"Accessing {class_name} via module attribute (should import directly)",
                )
            )

    # Pattern 4: Direct subpackage module imports (import package.subpackage)
    if subpackages:
        subpkg_regex = "|".join(re.escape(sp) for sp in subpackages)
        patterns.append(
            (
                rf"import {package_name}\.({subpkg_regex})(?![._])",
                "Import subpackage module (should import from implementation module)",
            )
        )

    return patterns


def scan_for_circular_imports():
    """Scan all package source files for problematic import patterns."""

    print("=" * 70)
    print(f"{package_name.upper()} CIRCULAR IMPORT SCANNER (Dynamic)")
    print("=" * 70)

    package_dir = package_path / package_name

    # Discover package structure
    print(f"\n🔍 Discovering package structure in: {package_dir}")
    subpackages = discover_subpackages(package_dir)
    print(f"   Found {len(subpackages)} subpackages: {', '.join(subpackages)}")

    # Discover implementation modules and their classes
    impl_map = {}
    regular_modules = set()
    for subpkg in subpackages:
        subpkg_dir = package_dir / subpkg
        modules = discover_implementation_modules(subpkg_dir)
        for mod, cls in modules.items():
            impl_map[f"{subpkg}.{mod}"] = cls
        # Discover regular modules (allowed for direct import)
        reg_mods = discover_regular_modules(subpkg_dir)
        regular_modules.update(reg_mods)

    print(f"   Found {len(impl_map)} implementation modules with classes")
    print(f"   Found {len(regular_modules)} regular modules (safe to import)")

    # Build dynamic patterns
    print(f"\n🎯 Building dynamic test patterns...")
    bad_patterns = build_dynamic_patterns(
        package_name, subpackages, impl_map, regular_modules
    )
    print(f"   Generated {len(bad_patterns)} pattern checks")

    issues = []

    # Scan all Python files
    print(f"\n📂 Scanning Python files...")
    scanned_files = 0
    for py_file in package_dir.rglob("*.py"):
        # Skip __pycache__ and test files
        if "__pycache__" in str(py_file) or "test" in str(py_file):
            continue

        scanned_files += 1
        rel_path = py_file.relative_to(package_path)

        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()

        for pattern, description in bad_patterns:
            matches = re.finditer(pattern, content, re.MULTILINE)
            for match in matches:
                # Get line number
                line_num = content[: match.start()].count("\n") + 1
                line_content = content.split("\n")[line_num - 1].strip()

                # Skip commented lines
                if line_content.strip().startswith("#"):
                    continue

                issues.append(
                    {
                        "file": str(rel_path),
                        "line": line_num,
                        "content": line_content,
                        "issue": description,
                        "match": match.group(0),
                    }
                )

    print(f"   Scanned {scanned_files} files")

    # Report findings
    print("\n" + "=" * 70)
    if issues:
        print(f"❌ Found {len(issues)} potential circular import issues:\n")

        current_file = None
        for issue in sorted(issues, key=lambda x: (x["file"], x["line"])):
            if issue["file"] != current_file:
                current_file = issue["file"]
                print(f"\n📄 {current_file}")

            print(f"  Line {issue['line']}: {issue['issue']}")
            print(f"    → {issue['content']}")
            print(f"    Match: '{issue['match']}'")

        print(f"\n{'='*70}")
        print(f"❌ TOTAL: {len(issues)} issues found")
        print("=" * 70)
        return False
    else:
        print("✅ No circular import issues detected!")
        print(f"   Package: {package_name}")
        print(f"   Subpackages: {len(subpackages)}")
        print(f"   Files scanned: {scanned_files}")
        print(f"   All imports follow the correct pattern.")
        print("=" * 70)
        return True


if __name__ == "__main__":
    success = scan_for_circular_imports()
    sys.exit(0 if success else 1)
