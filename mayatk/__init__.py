# !/usr/bin/python
# coding=utf-8
import sys
import inspect
import importlib


__package__ = 'mayatk'
__version__ = '0.5.4'


def __getattr__(attr):
	"""This function dynamically imports a module and returns an attribute from the module. 

	:Parameters:
		attr (str): The name of the attribute to be imported. The name should be in the format 
					'module_name.attribute_name' or just 'attribute_name'.
	:Return:
		(obj) The attribute specified by the `attr` argument.

	:Raises:
		AttributeError: If the specified attribute is not found in either the original module 
						or the 'Core' module within the package.
	:Example:
		<package>.__getattr__('module1.attribute1') #returns: <attribute1 value>
		<package>.__getattr__('attribute1') #returns: <attribute1 value>
	"""
	try:
		module_name, attribute = attr.split('.')
		module = importlib.import_module(f"{__package__}.{module_name}")
		setattr(sys.modules[__name__], attr, getattr(module, attribute))
		return getattr(module, attribute)

	except ValueError:
		module = importlib.import_module(f"{__package__}.Core")
		return getattr(module, attr)

	except AttributeError as error:
		raise AttributeError(f"Module '{__package__}' or '{__package__}.Core' has no attribute '{attr}'") from error

# --------------------------------------------------------------------------------------------









# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------