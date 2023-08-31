# !/usr/bin/python
# coding=utf-8

from mayatk.mat_utils._mat_utils import MatUtils
import pythontk as ptk

ptk.append_path(r"O:\Cloud\Code\_scripts\mayatk\mayatk\mat_utils", recursive=True)
try:
    import MaterialX
except ImportError as error:
    print(__file__, error)

# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
