# !/usr/bin/python
# coding=utf-8
"""
Maya Connection Module (Legacy Wrapper)

This module is a wrapper around mayatk.env_utils.maya_connection.
Please use mayatk.env_utils.maya_connection directly in new code.
"""
from mayatk.env_utils.maya_connection import MayaConnection


# Backward compatibility wrappers
def get_connection():
    return MayaConnection.get_instance()


def ensure_maya_connection(mode="auto"):
    conn = MayaConnection.get_instance()
    if not conn.is_connected:
        conn.connect(mode=mode)
    return conn


def connect_maya(mode="auto", port=7002):
    return MayaConnection.get_instance().connect(mode=mode, port=port)


def execute_in_maya(code):
    return MayaConnection.get_instance().execute(code)


def disconnect_maya():
    MayaConnection.get_instance().disconnect()


def reload_modules(modules, include_submodules=True, verbose=True):
    return MayaConnection.reload_modules(modules, include_submodules, verbose)


# Re-export symbols
__all__ = [
    "MayaConnection",
    "get_connection",
    "ensure_maya_connection",
    "connect_maya",
    "execute_in_maya",
    "disconnect_maya",
    "reload_modules",
]
