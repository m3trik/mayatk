# !/usr/bin/python
# coding=utf-8
import os
import inspect
import importlib
import pkgutil


__package__ = "mayatk"
__version__ = "0.9.31"

"""Dynamic Attribute Resolver for Module-based Packages

This module implements a dynamic attribute resolver that allows accessing attributes
(classes, methods, and class methods) from a package's modules using the pattern
'<package>.<attribute>'. The resolver builds dictionaries that map attribute names to
their respective module names, enabling efficient and maintainable access to these
attributes.

Key Components:
- CLASS_TO_MODULE: Dictionary mapping class names to their module names.
- METHOD_TO_MODULE: Dictionary mapping method names to their module and class names.
- CLASS_METHOD_TO_MODULE: Dictionary mapping class method names to module and class names.
- build_dictionaries(): Function to populate the dictionaries by inspecting package modules.
- import_module(): Function to import a module by its name.
- get_attribute_from_module(): Function to retrieve an attribute from a module.
- __getattr__(): Special method invoked when an attribute lookup is not found.

Usage:
- Access attributes like classes, methods, and class methods dynamically.
- Improves maintainability by automatically associating attributes with their modules.
- Follows an order of class, method, and class method resolution.

Note: This module is intended for use with package-based projects where attributes are
distributed across modules in a structured manner.
"""
# Dictionaries to map class names, method names, and class methods to their respective modules.
CLASS_TO_MODULE = {}
METHOD_TO_MODULE = {}
CLASS_METHOD_TO_MODULE = {}
IMPORTED_MODULES = {}

# Dictionaries to map module names to their parent package names for modules not included in included_modules.
MODULE_TO_PARENT = {}


# Optimization 2: More efficient method detection
def _add_class_methods(class_obj, module_name, class_name):
    """Helper function to add class methods to the method dictionaries."""
    # Use __dict__ for faster iteration on user-defined methods
    for method_name in class_obj.__dict__:
        method = getattr(class_obj, method_name)
        if callable(method) and not method_name.startswith("_"):
            METHOD_TO_MODULE[method_name] = (module_name, class_name)
            CLASS_METHOD_TO_MODULE[method_name] = (module_name, class_name)


def build_dictionaries(include=None):
    base_path = os.path.dirname(__file__)
    base_package = __name__

    if base_package == "__main__":
        raise EnvironmentError("build_dictionaries cannot be run as a script.")

    include = include or {}

    # Pre-compute nested module paths for efficiency
    nested_paths = {
        f"{base_package}.{key}": (key, classes)
        for key, classes in include.items()
        if "." in key
    }

    for importer, modname, ispkg in pkgutil.walk_packages(
        path=[base_path], prefix=base_package + "."
    ):
        module_name_component = modname.split(".")[-1]

        # Check direct match first (most common case)
        if module_name_component in include:
            classes_to_include = include[module_name_component]
        # Check pre-computed nested paths
        elif modname in nested_paths:
            _, classes_to_include = nested_paths[modname]
        else:
            continue

        try:
            module = importlib.import_module(modname)
        except ImportError as e:
            print(f"Failed to import module {modname}: {e}")
            continue

        # Handle wildcard - expose all classes
        if "*" in classes_to_include:
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == modname:
                    CLASS_TO_MODULE[name] = modname
                    _add_class_methods(obj, modname, name)
        else:
            # Handle specific class names
            for class_name in classes_to_include:
                obj = getattr(module, class_name, None)
                if obj and inspect.isclass(obj) and obj.__module__ == modname:
                    CLASS_TO_MODULE[class_name] = modname
                    _add_class_methods(obj, modname, class_name)


def import_module(module_name):
    if module_name not in IMPORTED_MODULES:
        IMPORTED_MODULES[module_name] = importlib.import_module(module_name)
    return IMPORTED_MODULES[module_name]


def get_attribute_from_module(module, attribute_name, class_name=None):
    if class_name:
        class_obj = getattr(module, class_name)
        return getattr(class_obj, attribute_name)
    return getattr(module, attribute_name)


def __getattr__(name):
    if name in CLASS_TO_MODULE:
        module = import_module(CLASS_TO_MODULE[name])
        return get_attribute_from_module(module, name)
    elif name in METHOD_TO_MODULE:
        module_name, class_name = METHOD_TO_MODULE[name]
        module = import_module(module_name)
        return get_attribute_from_module(module, name, class_name)
    elif name in CLASS_METHOD_TO_MODULE:
        module_name, class_name = CLASS_METHOD_TO_MODULE[name]
        module = import_module(module_name)
        return get_attribute_from_module(module, name, class_name)
    elif name in MODULE_TO_PARENT:
        parent_module = import_module(MODULE_TO_PARENT[name])
        return get_attribute_from_module(parent_module, name)
    else:
        raise AttributeError(f"module {__package__} has no attribute '{name}'")


# --------------------------------------------------------------------------------------------
# Unified include dictionary supporting both simple modules and nested module paths
include = {
    # Legacy modules - expose all classes using wildcard
    "_anim_utils": ["*"],
    "_cam_utils": ["*"],
    "_core_utils": ["*"],
    "_display_utils": ["*"],
    "_edit_utils": ["*"],
    "_env_utils": ["*"],
    "_mat_utils": ["*"],
    "_node_utils": ["*"],
    "_rig_utils": ["*"],
    "_ui_utils": ["*"],
    "_uv_utils": ["*"],
    "_xform_utils": ["*"],
    # Specific classes from modules
    "components": ["Components"],
    "macros": ["Macros"],
    "maya_menu_handler": ["MayaMenuHandler"],
    "naming": ["Naming"],
    "ui_manager": ["UiManager"],
    # Selection utilities
    "edit_utils.selection": ["Selection"],
    # Add hierarchy manager support (these will now work!):
    "env_utils.hierarchy_manager.manager": ["HierarchyManager"],
    "env_utils.hierarchy_manager.core": ["DiffResult", "RepairAction", "FileFormat"],
    "env_utils.hierarchy_manager.swapper": ["ObjectSwapper"],
    # Examples of wildcard usage:
    # "some_module": ["*"],  # Expose all classes from some_module
}

build_dictionaries(include=include)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
