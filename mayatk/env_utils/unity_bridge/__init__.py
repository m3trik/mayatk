# !/usr/bin/python
# coding=utf-8
"""The Maya half of the Unity hand-off: export the scope for a Unity project.

The engine (``_unity_bridge.UnityBridge``) and its export rows
(``parameters.Parameters``, ``UnityBridge.params_registry()``); the panel is
extapps' Unity Scripts app, which tentacle opens with this engine injected, and
whose delivery (a ``ptk.FolderDeliverer`` copying into ``Assets/``) the engine
sends through.
"""
