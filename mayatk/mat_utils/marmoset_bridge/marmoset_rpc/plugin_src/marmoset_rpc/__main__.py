# !/usr/bin/python
# coding=utf-8
"""What Toolbag runs: the plugin as its **Plugins** menu lists it (a folder
plugin's ``__main__.py``), and what a bridge launches its own Toolbag with
(``toolbag.exe -run <this file>``).

Toolbag runs this file as a script, so the package beside it is imported by
its folder's parent: this copy, never another of the same name. Importing it
serves (from Toolbag's update callback, no thread); picking the plugin again
after Toolbag shut it down serves again.
"""

import os
import sys

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:  # a host that runs it without __file__
    import mset  # noqa: PLC0415

    _HERE = os.path.dirname(os.path.abspath(mset.getPluginPath()))
_PARENT = os.path.dirname(_HERE)
if sys.path[:1] != [_PARENT]:
    sys.path.insert(0, _PARENT)

import marmoset_rpc  # noqa: E402 -- serves on import inside Toolbag

marmoset_rpc.start_server()
