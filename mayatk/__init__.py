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


def build_dictionaries(included_modules=None, included_classes=None):
    base_path = os.path.dirname(__file__)
    base_package = __name__

    if base_package == "__main__":
        raise EnvironmentError("build_dictionaries cannot be run as a script.")

    included_classes = included_classes or {}

    for importer, modname, ispkg in pkgutil.walk_packages(
        path=[base_path], prefix=base_package + "."
    ):
        module_name_component = modname.split(".")[-1]

        # If included_modules is defined, register the entire module
        if included_modules and module_name_component in included_modules:
            try:
                module = importlib.import_module(modname)
            except ImportError as e:
                print(f"Failed to import module {modname}: {e}")
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == modname:
                    CLASS_TO_MODULE[name] = modname
                    method_members = inspect.getmembers(
                        obj,
                        lambda member: inspect.isfunction(member)
                        or inspect.ismethod(member),
                    )
                    for method_name, _ in method_members:
                        METHOD_TO_MODULE[method_name] = (modname, name)
                        CLASS_METHOD_TO_MODULE[method_name] = (modname, name)

        # If a class from this module is in included_classes, add only that class
        elif module_name_component in included_classes:
            try:
                module = importlib.import_module(modname)
            except ImportError as e:
                print(f"Failed to import module {modname}: {e}")
                continue

            for class_name in included_classes[module_name_component]:
                obj = getattr(module, class_name, None)
                if obj and inspect.isclass(obj) and obj.__module__ == modname:
                    CLASS_TO_MODULE[class_name] = modname


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
# Classes and methods from these modules will be exposed at package level.
included_modules = [
    "_anim_utils",
    "_cam_utils",
    "_core_utils",
    "_display_utils",
    "_edit_utils",
    "_env_utils",
    "_mat_utils",
    "_node_utils",
    "_rig_utils",
    "_ui_utils",
    "_uv_utils",
    "_xform_utils",
]
# These modules are not included in the package, but their classes will be exposed at package level.
included_classes = {
    "components": ["Components"],
    "macros": ["Macros"],
    "maya_menu_handler": ["MayaMenuHandler"],
    "naming": ["Naming"],
    "ui_manager": ["UiManager"],
}

build_dictionaries(included_modules=included_modules, included_classes=included_classes)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
