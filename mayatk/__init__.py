# !/usr/bin/python
# coding=utf-8
import sys


__package__ = 'mayatk'
__version__ = '0.5.8'


def __getattr__(attr):
	"""This function dynamically imports a module and returns an attribute from the module. 

	Parameters:
		attr (str): The name of the attribute to be imported. The name should be in the format 
					'module_name.attribute_name' or just 'attribute_name'.
	Return:
		(obj) The attribute specified by the `attr` argument.

	:Raises:
		AttributeError: If the specified attribute is not found in either the original module 
						or the 'Core' module within the package.
	Example:
		<package>.__getattr__('module1.attribute1') #returns: <attribute1 value>
		<package>.__getattr__('attribute1') #returns: <attribute1 value>
	"""
	try:
		module = __import__(f"{__package__}.{attr}", fromlist=[f"{attr}"])
		setattr(sys.modules[__name__], attr, getattr(module, attr))
		return getattr(module, attr)

	except (ValueError, ModuleNotFoundError):
		module = __import__(f"{__package__}.Core", fromlist=["Core"])
		return getattr(module, attr)

	except AttributeError as error:
		raise AttributeError(f"Module '{__package__}' has no attribute '{attr}'") from error

# --------------------------------------------------------------------------------------------









# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------