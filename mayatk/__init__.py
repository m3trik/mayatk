# !/usr/bin/python
# coding=utf-8
import inspect
import importlib
import pkgutil


__package__ = "mayatk"
__version__ = "0.7.4"

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

CLASS_TO_MODULE = {}
METHOD_TO_MODULE = {}
CLASS_METHOD_TO_MODULE = {}
IMPORTED_MODULES = {}


def build_dictionaries():
    for importer, modname, ispkg in pkgutil.walk_packages(__path__, __name__ + "."):
        module = importlib.import_module(modname)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            CLASS_TO_MODULE[name] = modname
            for method_name, _ in inspect.getmembers(obj, inspect.isfunction):
                METHOD_TO_MODULE[method_name] = (modname, name)
            for method_name, _ in inspect.getmembers(obj, inspect.ismethod):
                CLASS_METHOD_TO_MODULE[method_name] = (modname, name)


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

    if name in METHOD_TO_MODULE:
        module_name, class_name = METHOD_TO_MODULE[name]
        module = import_module(module_name)
        return get_attribute_from_module(module, name, class_name)

    if name in CLASS_METHOD_TO_MODULE:
        module_name, class_name = CLASS_METHOD_TO_MODULE[name]
        module = import_module(module_name)
        return get_attribute_from_module(module, name, class_name)

    raise AttributeError(f"module {__package__} has no attribute '{name}'")


# --------------------------------------------------------------------------------------------
# Build dictionaries at the start
build_dictionaries()

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
