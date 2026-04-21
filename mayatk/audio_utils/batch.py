# !/usr/bin/python
# coding=utf-8
"""Batch orchestration — undo chunk + dirty-track buffering.

Usage::

    with audio_utils.batch() as b:
        audio_utils.write_key("footstep", frame=12)
        b.mark_dirty(["footstep"])
        # ... more key ops across multiple tracks ...
    # On exit: single compositor.sync() call + single undo entry.

Nested batches flatten — only the outermost triggers the sync.
"""
import logging
import threading
from typing import Iterable, List, Optional

from mayatk.audio_utils import compositor

try:
    import pymel.core as pm
except ImportError:
    pm = None

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)


# Thread-local nesting state.
_state = threading.local()


def _get_stack() -> list:
    if not hasattr(_state, "stack"):
        _state.stack = []
    return _state.stack


class _Batch:
    """One level of nested batch state."""

    def __init__(self) -> None:
        self._dirty: set = set()
        self._full_sync: bool = False

    def mark_dirty(self, track_ids: Optional[Iterable[str]] = None) -> None:
        """Mark tracks dirty for the pending compositor sync.

        When *track_ids* is ``None``, requests a full sync on exit.
        """
        if track_ids is None:
            self._full_sync = True
            return
        self._dirty.update(track_ids)


class _BatchContext:
    """Context manager returned by :func:`batch`."""

    def __init__(self, auto_sync: bool = True, undo: bool = True) -> None:
        self._auto_sync = auto_sync
        self._undo = undo
        self._chunk = None
        self._batch: Optional[_Batch] = None
        self._is_outer: bool = False

    # Delegate dirty-marking to the outermost batch so nested calls
    # aggregate into one sync.
    def mark_dirty(self, track_ids: Optional[Iterable[str]] = None) -> None:
        stack = _get_stack()
        if stack:
            stack[0].mark_dirty(track_ids)

    def __enter__(self) -> "_BatchContext":
        stack = _get_stack()
        self._is_outer = not stack
        if self._is_outer:
            self._batch = _Batch()
            stack.append(self._batch)
            if self._undo and pm is not None:
                self._chunk = pm.UndoChunk()
                self._chunk.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        stack = _get_stack()
        if self._is_outer:
            try:
                if exc_type is None and self._auto_sync and self._batch is not None:
                    if self._batch._full_sync:
                        compositor.sync(tracks=None)
                    elif self._batch._dirty:
                        compositor.sync(tracks=sorted(self._batch._dirty))
            finally:
                if stack and stack[-1] is self._batch:
                    stack.pop()
                if self._chunk is not None:
                    self._chunk.__exit__(exc_type, exc_val, exc_tb)


def batch(auto_sync: bool = True, undo: bool = True) -> _BatchContext:
    """Context manager grouping audio edits into one undo + one sync.

    Parameters:
        auto_sync: If True, compositor.sync() runs on successful exit.
        undo: If True, wraps the body in a ``pm.UndoChunk``.
    """
    return _BatchContext(auto_sync=auto_sync, undo=undo)
