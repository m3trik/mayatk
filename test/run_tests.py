# !/usr/bin/python
# coding=utf-8
import unittest

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=r"O:\Cloud\Code\_scripts\mayatk\test", pattern="*_test.py"
    )
    runner = unittest.TextTestRunner()
    runner.run(suite)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
