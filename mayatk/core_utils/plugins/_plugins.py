# !/usr/bin/python
# coding=utf-8
"""Every Maya plug-in mayatk loads goes through one door: :class:`Plugins`.

GUI Maya holds a plug-in from outside its trusted locations at a modal
"Untrusted Plugin Loading" prompt until someone answers it -- an artist rightly
distrusts a plug-in they never asked for, and an unattended session waits
forever. Maya's install folder is trusted, and so is a plug-in folder declared
to Maya as it starts (a module's ``plug-ins:`` line, or ``MAYA_PLUG_IN_PATH``
in the environment it starts with). A plug-in loaded by path is not, even with
its folder appended to ``MAYA_PLUG_IN_PATH`` by then (all measured in fresh GUI
Maya 2025 sessions, 2026-09-14 and 2026-10-03). Standalone Maya never prompts.

So :meth:`Plugins.load` loads every plug-in by name, as Maya resolves it on its
own plug-in path, and refuses a path outright. mayatk ships no plug-in of its
own: what its tools need, Maya already carries -- :class:`UndoRecorder` rides
Maya's ``ufeSupport``, and the articulated rig's end control solves in a MEL
expression -- so nothing mayatk does can raise the prompt.

Example:
    >>> Plugins.load("fbxmaya")  # Maya's own, from its install
"""

import os
from typing import ClassVar, List, Optional, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:  # the surface imports without Maya (registry, docs, mock tests)
    cmds = mel = None


class Plugins:
    """The one door every Maya plug-in load in mayatk goes through (see the
    module docstring for why it loads by name only)."""

    class LoadError(ValueError, RuntimeError):
        """A plug-in did not load. A ``ValueError``, as ``EnvUtils.load_plugin``
        raised, and a ``RuntimeError``, as ``cmds.loadPlugin`` raises: every
        caller's ``except`` keeps the meaning it had before the door."""

    #: A plug-in file, by the extensions Maya loads.
    EXTENSIONS: ClassVar[Tuple[str, ...]] = (".py", ".mll", ".so", ".bundle")

    @staticmethod
    def is_loaded(name: str) -> bool:
        """Whether the plug-in *name* is loaded.

        False rather than an error for a plug-in this Maya does not know:
        "unknown" and "not loaded" are the same answer to every caller.

        Parameters:
            name (str): The plug-in's name, e.g. ``"mtoa"``, ``"fbxmaya"``.
        """
        try:
            return bool(cmds.pluginInfo(name, query=True, loaded=True))
        except Exception:
            return False

    @classmethod
    def available(cls, name: str) -> bool:
        """Whether the plug-in *name* is loaded or loads by name: its file is in
        a folder on Maya's plug-in search path (a renderer's module puts its
        folder there at startup). Never loads it -- loading a renderer boots it.

        Parameters:
            name (str): The plug-in's name, with or without its extension.
        """
        if cls.is_loaded(name):
            return True
        return any(cls._file(folder, name) for folder in cls._search_path())

    @classmethod
    def load(cls, name: str) -> None:
        """Load the plug-in *name* unless it is loaded -- the one way mayatk
        loads a plug-in. Always by name, so Maya resolves it on its plug-in
        path, where GUI Maya trusts it.

        Parameters:
            name (str): The plug-in's name -- ``"mtoa"``, ``"fbxmaya"`` -- with
                or without its extension; never a path.

        Raises:
            Plugins.LoadError: *name* is a path, or the plug-in is unknown or
                fails to load.
        """
        if os.path.dirname(name):
            raise cls.LoadError(
                f"{name}: a plug-in loads by name, from a folder Maya started with "
                "-- loaded by path, GUI Maya stops at its untrusted-plug-in prompt."
            )
        if cls.is_loaded(name):
            return
        try:
            cmds.loadPlugin(name, quiet=True)
        except RuntimeError as error:
            raise cls.LoadError(f"Failed to load plugin {name}: {error}") from error

    @classmethod
    def _file(cls, folder: str, name: str) -> Optional[str]:
        """The file of plug-in *name* in *folder*, by any extension Maya loads
        unless *name* carries one."""
        stem, ext = os.path.splitext(os.path.basename(name))
        if ext.lower() not in cls.EXTENSIONS:
            stem, ext = os.path.basename(name), ""
        for suffix in (ext,) if ext else cls.EXTENSIONS:
            path = os.path.join(folder, stem + suffix)
            if os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _search_path() -> List[str]:
        """Maya's plug-in search path, as Maya reads it (MEL ``getenv``)."""
        try:
            value = mel.eval('getenv "MAYA_PLUG_IN_PATH"')
        except Exception:
            value = os.environ.get("MAYA_PLUG_IN_PATH", "")
        return [entry for entry in str(value or "").split(os.pathsep) if entry.strip()]
