# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect


class Main(unittest.TestCase):
    """ """

    def perform_test(self, case):
        """ """
        for expression, expected_result in case.items():
            m = str(expression).split("(")[
                0
            ]  # ie. 'self.set_case' from "self.set_case('xxx', 'upper')"

            try:
                path = os.path.abspath(inspect.getfile(eval(m)))
            except TypeError:
                path = ""

            result = eval(expression)
            self.assertEqual(
                result,
                expected_result,
                "\n\nError: {}\n  Call:     {}\n  Expected: {} {}\n  Returned: {} {}".format(
                    path,
                    expression.replace("self.", "", 1),
                    type(expected_result),
                    expected_result,
                    type(result),
                    result,
                ),
            )


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
