# !/usr/bin/python
# coding=utf-8
import inspect
import importlib
import pkgutil


__package__ = "mayatk"
__version__ = '0.6.2'


# Define dictionaries to map class names, method names, class method names, and sub-modules to their respective modules
CLASS_TO_MODULE = {}
METHOD_TO_MODULE = {}
CLASS_METHOD_TO_MODULE = {}
SUBMODULE_TO_MODULE = {}

# Build the dictionaries by iterating over all submodules and sub-packages of the package
for importer, modname, ispkg in pkgutil.walk_packages(__path__, __name__ + "."):
    module = importlib.import_module(modname)
    for name, obj in module.__dict__.items():
        if inspect.isclass(obj):
            CLASS_TO_MODULE[obj.__name__] = modname
            for method_name, method_obj in inspect.getmembers(
                obj, predicate=inspect.isfunction
            ):
                METHOD_TO_MODULE[method_name] = (modname, obj.__name__)
            for method_name, method_obj in inspect.getmembers(
                obj, predicate=inspect.ismethod
            ):
                CLASS_METHOD_TO_MODULE[method_name] = (modname, obj.__name__)
    if not ispkg:
        submodule_name = modname.split(".")[-1]
        SUBMODULE_TO_MODULE[submodule_name] = modname

# Define a dictionary to store imported module objects
IMPORTED_MODULES = {}


def __getattr__(name):
    # Check if the requested attribute is a sub-module we need to import
    if name in SUBMODULE_TO_MODULE:
        module_name = SUBMODULE_TO_MODULE[name]
        if module_name not in IMPORTED_MODULES:
            # If the module hasn't been imported yet, import it and add it to the dictionary
            module = importlib.import_module(module_name)
            IMPORTED_MODULES[module_name] = module
        else:
            module = IMPORTED_MODULES[module_name]
        # Return the requested sub-module object from the module
        return module

    # Check if the requested attribute is a class we need to import
    if name in CLASS_TO_MODULE:
        module_name = CLASS_TO_MODULE[name]
        if module_name not in IMPORTED_MODULES:
            # If the module hasn't been imported yet, import it and add it to the dictionary
            module = importlib.import_module(module_name)
            IMPORTED_MODULES[module_name] = module
        else:
            module = IMPORTED_MODULES[module_name]
        # Return the requested class object from the module
        return getattr(module, name)

    # Check if the requested attribute is a method we need to import
    elif name in METHOD_TO_MODULE:
        module_name, class_name = METHOD_TO_MODULE[name]
        if module_name not in IMPORTED_MODULES:
            # If the module hasn't been imported yet, import it and add it to the dictionary
            module = importlib.import_module(module_name)
            IMPORTED_MODULES[module_name] = module
        else:
            module = IMPORTED_MODULES[module_name]
        # Get the class object and return the requested method object from it
        class_obj = getattr(module, class_name)
        return getattr(class_obj, name)

    # Check if the requested attribute is a class method we need to import
    elif name in CLASS_METHOD_TO_MODULE:
        module_name, class_name = CLASS_METHOD_TO_MODULE[name]
        if module_name not in IMPORTED_MODULES:
            # If the module hasn't been imported yet, import it and add it to the dictionary
            module = importlib.import_module(module_name)
            IMPORTED_MODULES[module_name] = module
        else:
            module = IMPORTED_MODULES[module_name]
        # Get the class object and return the requested class method object from it
        class_obj = getattr(module, class_name)
        return getattr(class_obj, name)

    # If the requested attribute is not a class, method, class method, or sub-module we handle, raise an AttributeError
    raise AttributeError(f"module {__package__} has no attribute '{name}'")


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------


# def __getattr__(attr):
# 	"""This function dynamically imports a module and returns an attribute from the module.

# 	Parameters:
# 		attr (str): The name of the attribute to be imported. The name should be in the format
# 					'module_name.attribute_name' or just 'attribute_name'.
# 	Returns:
# 		(obj) The attribute specified by the `attr` argument.

# 	:Raises:
# 		AttributeError: If the specified attribute is not found in either the original module
# 						or the 'Misc' module within the package.
# 	Example:
# 		<package>.__getattr__('module1.attribute1') #returns: <attribute1 value>
# 		<package>.__getattr__('attribute1') #returns: <attribute1 value>
# 	"""
# 	try:
# 		module = __import__(f"{__package__}.{attr}", fromlist=[f"{attr}"])
# 		setattr(sys.modules[__name__], attr, getattr(module, attr))
# 		return getattr(module, attr)

# 	except (ValueError, ModuleNotFoundError):
# 		module = __import__(f"{__package__}.Misc", fromlist=["Misc"])
# 		return getattr(module, attr)

# 	except AttributeError as error:
# 		raise AttributeError(f"Module '{__package__}' has no attribute '{attr}'") from error
