# !/usr/bin/python
# coding=utf-8
"""mayatk's operations: how an agent reaches Maya.

Two, the ones only this host can declare: a console that runs code in mayapy,
and an image of its scene. Every other mayatk function is reached through them
(``api.search`` finds it, ``maya.python.exec`` calls it), so nothing here wraps
a library function one by one. ``ptk.OperationProvider`` turns these
declarations into the committed ``operations.json`` beside this file, which
``python -m pythontk ops`` (a fresh mayapy per call) and the MCP server (the
one mayapy a session keeps) read; after changing one, rewrite it:
``python -m pythontk ops apply operations.catalog module=mayatk.operations``.
blendertk declares the same two for Blender (``blender.*``).
"""

from pythontk import Operation

OPERATIONS = (
    Operation(
        "maya.python.exec",
        "pythontk:PythonConsole.run",
        effect="code",
        title="Run Python in Maya",
        host="maya",
    ),
    Operation(
        "maya.scene.view",
        "mayatk:CamUtils.capture_view",
        title="An image of the scene",
        host="maya",
    ),
)
