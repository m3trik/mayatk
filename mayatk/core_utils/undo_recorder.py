# !/usr/bin/python
# coding=utf-8
"""Put OpenMaya edits on Maya's undo queue, as one ordinary undo step.

An OpenMaya write -- ``MFnAnimCurve.addKey``, ``MDGModifier.doIt``,
``MFnMesh.setPoints`` -- bypasses the undo queue: the edit survives Ctrl+Z, and
the undo reverts whatever the user did BEFORE it, against the edited scene
(measured 2026-09-14: after ``AnimUtils.optimize_keys``, one undo left the
optimized curve as it was and reverted the previous edit). Maya only lets a
COMMAND carry undo, so :meth:`UndoRecorder.record` collects a block's undo
objects and hands them to Maya's own ``ufeCmd``, whose undo / redo replay them:
a generic Python command (Maya undoes its own Outliner edits through it) from
the ``ufeSupport`` plugin in Maya's install folder, loaded on first use when the
session has not loaded it. A plugin of mayatk's own cannot carry them: by
default GUI Maya holds every plugin from outside its trusted locations at a
modal "Untrusted Plugin Loading" prompt until someone answers it, and its
install folder is trusted, so loading ``ufeSupport`` raises none (both measured
in a fresh GUI Maya 2025, 2026-09-14). Standalone never prompts, so no headless
test can see the difference -- ``test_undo_recorder`` checks the load instead.

The queue replays commands in the order they ran, so a block commits when it
closes, and a block opened inside another first commits the outer block's edits
so far. Two rules keep an undo exact: spread ``recorder.anim`` into each edit
call rather than keeping the dict (a commit hands its change over, and a kept
one would record later edits out of order), and close a block before a cmds
edit that changes the data its OpenMaya edits changed. An edit to a node that a
recorded cmds step MADE in the same undo step needs no block at all: that step's
undo takes the node away and its redo brings the same node back, edits included
(measured on ``setKeyframe`` curves filled by ``addKeys``).

Cost, measured on Maya 2025: recording is free (removing 150k of 200k keys took
2.09 s recorded, 2.25 s bare), a commit is about 10 microseconds, and an undo
replays in about the edit's own time (1.85 s). While the queue is off --
:meth:`CoreUtils.undo_disabled`, a batch export -- nothing is recorded at all.

Example:
    >>> with UndoRecorder.record() as recorder:
    ...     curve_fn.remove(index, **recorder.anim)
"""

import contextlib
import logging
from typing import Any, Callable, ContextManager, Dict, Iterator, List

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

logger = logging.getLogger(__name__)


class _UndoRecord:
    """An undo / redo callable pair, replayed like an API undo object."""

    __slots__ = ("undoIt", "redoIt")

    def __init__(self, undo: Callable[[], Any], redo: Callable[[], Any]):
        self.undoIt = undo
        self.redoIt = redo


class _UndoStep:
    """One committed block's records, in the protocol ``ufeCmd`` replays."""

    __slots__ = ("_records",)

    def __init__(self, records: List[Any]):
        self._records = records

    def execute(self) -> None:
        """Nothing to run: the block's edits are applied already."""

    def undo(self) -> None:
        for record in reversed(self._records):
            record.undoIt()

    def redo(self) -> None:
        for record in self._records:
            record.redoIt()


class _Recorder:
    """One block's undo objects, in the order their edits were made."""

    def __init__(self, recording: bool):
        self.recording = recording
        self.records: List[Any] = []
        self._anim_change = None

    @property
    def anim(self) -> Dict[str, Any]:
        """The keyword to spread into every ``MFnAnimCurve`` edit of the block.

        ``{"change": MAnimCurveChange}`` while the queue records, ``{}`` while it
        does not -- OpenMaya rejects ``change=None``, so the argument has to be
        absent, and a call site spreads this rather than branching. Read it at
        the edit: the first read opens a change that is committed even if no
        edit uses it.
        """
        if not self.recording:
            return {}
        if self._anim_change is None:
            import maya.api.OpenMayaAnim as oma2

            self._anim_change = oma2.MAnimCurveChange()
            self.records.append(self._anim_change)
        return {"change": self._anim_change}

    def modifier(self, modifier: Any) -> None:
        """Record an ``MDGModifier`` / ``MDagModifier`` after its ``doIt``."""
        self._append(_UndoRecord(modifier.undoIt, modifier.doIt))

    def snapshot(self, undo: Callable[[], Any], redo: Callable[[], Any]) -> None:
        """Record an edit no API undo object covers, as its reverse and re-apply."""
        self._append(_UndoRecord(undo, redo))

    @contextlib.contextmanager
    def state(
        self, read: Callable[[], Any], put: Callable[[Any], Any]
    ) -> Iterator[None]:
        """Record the block's change to what *read* returns, put back by *put*.

        For an edit no API undo object covers (``MFnMesh.setPoints``,
        ``MPlug.setString``): *read* runs before and after the block, and undo /
        redo hand those two results to *put*. A block that raises still records
        what it changed. Neither runs while the queue is off.
        """
        if not self.recording:
            yield
            return
        before = read()
        try:
            yield
        finally:
            after = read()
            self._append(_UndoRecord(lambda: put(before), lambda: put(after)))

    def points(self, fn: Any) -> ContextManager[None]:
        """:meth:`state` for the points of an API 2.0 ``MFnMesh``,
        ``MFnNurbsCurve`` or ``MFnNurbsSurface``.

        Read and put back in object space, whatever space the block writes in.
        """
        import maya.api.OpenMaya as om2

        space = om2.MSpace.kObject
        if isinstance(fn, om2.MFnMesh):

            def put(points):
                fn.setPoints(points, space)
                fn.updateSurface()

            return self.state(lambda: fn.getPoints(space), put)

        update = (
            fn.updateCurve if isinstance(fn, om2.MFnNurbsCurve) else fn.updateSurface
        )

        def put_cvs(points):
            fn.setCVPositions(points, space)
            update()

        return self.state(lambda: fn.cvPositions(space), put_cvs)

    def normals(self, fn: Any) -> ContextManager[None]:
        """:meth:`state` for the face-vertex normals of an API 2.0 ``MFnMesh``,
        locks included.

        Put back in object space: every normal that was locked takes its vector
        again, and every other one is unlocked, following the geometry as it did.
        """
        import maya.api.OpenMaya as om2

        space = om2.MSpace.kObject

        def read():
            table = fn.getNormals(space)
            _counts, normal_ids = fn.getNormalIds()
            vertex_counts, vertex_ids = fn.getVertices()
            faces = [f for f, count in enumerate(vertex_counts) for _ in range(count)]
            # One query per normal, not one per face-vertex sharing it.
            locked = {i for i in set(normal_ids) if fn.isNormalLocked(i)}
            held = [k for k, i in enumerate(normal_ids) if i in locked]
            return (
                om2.MIntArray(faces),
                vertex_ids,
                om2.MVectorArray([om2.MVector(table[normal_ids[k]]) for k in held]),
                om2.MIntArray([faces[k] for k in held]),
                om2.MIntArray([vertex_ids[k] for k in held]),
            )

        def put(normals):
            faces, vertex_ids, vectors, held_faces, held_vertices = normals
            fn.unlockFaceVertexNormals(faces, vertex_ids)
            if len(vectors):
                fn.setFaceVertexNormals(vectors, held_faces, held_vertices, space)
            fn.updateSurface()

        return self.state(read, put)

    def take(self) -> List[Any]:
        """Hand over the records so far; the next curve edit opens a new change."""
        records, self.records = self.records, []
        self._anim_change = None
        return records

    def _append(self, record: _UndoRecord) -> None:
        if not self.recording:
            return
        # Later curve edits start a fresh change AFTER this record, so undo
        # replays in the true reverse order -- a modifier that created a curve
        # must not be undone before the key edits made on it.
        self._anim_change = None
        self.records.append(record)


class UndoRecorder:
    """Record OpenMaya edits as one undo step (see the module docstring)."""

    #: The plugin whose ``ufeCmd`` carries a step: Maya's own, from its install
    #: folder, which Maya trusts -- loading it never prompts.
    _CARRIER_PLUGIN = "ufeSupport"
    #: The open blocks, innermost last.
    _open: List[_Recorder] = []
    #: Whether the carrier is known to be loaded; a failed commit clears it, so
    #: the next one checks again (an unloaded plugin, say) instead of every one.
    _carrier_ready = False
    #: The failure last warned about: a carrier that keeps failing warns once,
    #: not once per block of a long run.
    _warned = ""

    @classmethod
    @contextlib.contextmanager
    def record(cls) -> Iterator[_Recorder]:
        """Collect a block's OpenMaya undo objects and commit them as ONE undo step.

        The block's edits undo together with anything else in the enclosing undo
        chunk, in order. A block that raises still commits what it applied, so a
        partial edit can be undone. Nothing is recorded, and no command runs,
        while the queue is off.

        Yields:
            The recorder: spread ``recorder.anim`` into every ``MFnAnimCurve``
            edit, pass modifiers (after ``doIt``) to ``recorder.modifier``, and
            wrap any other edit in ``recorder.state`` (``recorder.points`` for
            mesh, curve or surface points, ``recorder.normals`` for mesh normals)
            or hand its reverse and re-apply to ``recorder.snapshot``.
        """
        recorder = _Recorder(bool(cmds.undoInfo(query=True, state=True)))
        if cls._open:
            # The outer block's edits so far came first: on the queue first.
            cls._commit(cls._open[-1].take())
        cls._open.append(recorder)
        try:
            yield recorder
        finally:
            cls._open.pop()
            cls._commit(recorder.take())

    @classmethod
    def _commit(cls, records: List[Any]) -> None:
        """Put *records* on the queue as one step; an edit is never lost to this."""
        if not records:
            return
        try:
            if not cls._carrier_ready:
                if not cmds.pluginInfo(cls._CARRIER_PLUGIN, query=True, loaded=True):
                    cmds.loadPlugin(cls._CARRIER_PLUGIN, quiet=True)
                cls._carrier_ready = True
            from maya.internal.ufeSupport import ufeCmdWrapper

            ufeCmdWrapper.execute(_UndoStep(records))
            cls._warned = ""
        except Exception as error:  # the edit is applied; only its undo is lost
            cls._carrier_ready = False
            if str(error) != cls._warned:
                cls._warned = str(error)
                logger.warning("UndoRecorder: this edit is not undoable: %s", error)
