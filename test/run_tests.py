# !/usr/bin/python
# coding=utf-8
import importlib


def run_test_cases(test_module):
	"""Runs all test cases within a test module.

	Parameters:
		test_module (str)(module): A module object or a string representing the module name.

	"""
	if isinstance(test_module, str):
		test_module = importlib.import_module(test_module)
		
	for name, obj in test_module.__dict__.items():
		if isinstance(obj, type) and obj.__module__ == test_module.__name__:
			obj = obj()
			for method_name in dir(obj):
				if method_name.startswith("test_"):
					test_case = getattr(obj, method_name)
					test_case()


def run_tests(module_names):
	"""Reloads the non-test and test modules and runs the test cases within the test modules.

	Parameters:
		module_names (list): A list of strings representing the names of the non-test modules.

	"""
	for module_name in module_names:
		module = __import__(f"mayatk.{module_name}", fromlist=[module_name])
		test_module = __import__(f"test.{module_name}_test", fromlist=[f"{module_name}_test"])
		importlib.reload(module)
		importlib.reload(test_module)
		msg_start = f'-> Starting tests for {module_name} ..'
		print (f"{'-'*len(msg_start)}\n{msg_start}")
		run_test_cases(test_module)
		print (f"<- {module_name} tests completed.\n{'-'*len(msg_start)}")

# --------------------------------------------------------------------------------------------

if __name__=='__main__':

	run_tests([
		'Core',
		'Node',
		'Cmpt',
		'Edit',
		'Xform',
		'Rig',
	])

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------



# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------

# from mayatk import Core; from test import core_test; importlib.reload(Core); importlib.reload(core_test); run_tests(core_test)
# from mayatk import Node; from test import node_test; importlib.reload(Node); importlib.reload(node_test); run_tests(node_test)
# from mayatk import Cmpt; from test import cmpt_test; importlib.reload(Cmpt); importlib.reload(cmpt_test); run_tests(cmpt_test)
# from mayatk import Edit; from test import edit_test; importlib.reload(Edit); importlib.reload(edit_test); run_tests(edit_test)
# from mayatk import Xform; from test import xform_test; importlib.reload(Xform); importlib.reload(xform_test); run_tests(xform_test)
# from mayatk import Rig; from test import rig_test; importlib.reload(Rig); importlib.reload(rig_test); run_tests(rig_test)

# from test import core_test;  core_test.unittest.main(exit=False)
# from test import edit_test;  edit_test.unittest.main(exit=False)
# from test import cmpt_test;  cmpt_test.unittest.main(exit=False)
# from test import rig_test;   rig_test.unittest.main(exit=False)
# from test import xform_test; xform_test.unittest.main(exit=False)