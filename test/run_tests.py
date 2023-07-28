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

    for _, obj in test_module.__dict__.items():
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
        test_module = __import__(
            f"test.{module_name}_test", fromlist=[f"{module_name}_test"]
        )
        importlib.reload(module)
        importlib.reload(test_module)
        msg_start = f"-> Starting tests for {module_name} .."
        print(f"{'-'*len(msg_start)}\n{msg_start}")
        run_test_cases(test_module)
        print(f"<- {module_name} tests completed.\n{'-'*len(msg_start)}")


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    run_tests(
        [
            "utils",
            "node_utils",
            "component_utils",
            "edit_utils",
            "xform_utils",
            "rig_utils",
            "mat_utils",
        ]
    )

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
