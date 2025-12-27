# !/usr/bin/python
# coding=utf-8
"""
Maya Connection Module (Legacy Wrapper)

This module is a wrapper around mayatk.env_utils.maya_connection.
Please use mayatk.env_utils.maya_connection directly in new code.
"""
from mayatk.env_utils.maya_connection import (
    MayaConnection,
    get_connection,
    ensure_maya_connection,
    connect_maya,
    execute_in_maya,
    disconnect_maya,
    reload_modules,
)

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
