# !/usr/bin/python
# coding=utf-8
"""Run a script in Painter: the one way code reaches Painter over RPC.

``script.start(source, path, args, expect, label)`` checks the script compiles
and that Painter holds the project it was meant for, queues it and returns its
job at once; ``script.status(job)`` reports it, ``script.cancel(job)`` stops
it. A job runs when Painter is not busy (``project.execute_when_not_busy``: not
mid-bake, mid-load or mid-export) and its shelves have loaded (a Painter just
launched is still finding its export presets), on the main thread, each piece of it (the
script, then each idle step) one entry in Painter's undo history named for the
script (``layerstack.ScopedModification(label)``) when a project is open as
that piece starts; what Painter itself does not undo (a project created,
opened or saved) stays done.

A script is Python, run as written, with these names given:

* ``args`` -- a :class:`ScriptArgs`: its values by key (``args["MESH"]``,
  ``args.get("ITERATIONS", 4)``), plus ``args.report(line)``,
  ``args.progress(fraction, text)``, ``args.cancelled()``,
  ``args.check_cancel()`` (raises :class:`ScriptCancelled` once asked) and
  ``args.when_idle(fn)``: a step run once Painter is idle again (a save after
  ``project.create``, which returns before Painter finished loading); the job
  ends after its last step;
* ``substance_painter`` -- Painter's API.

What it prints is the job's console. A job's states: ``queued``, ``running``,
``done``, ``failed``, ``cancelled`` (the shape a Unity Scripts job has).

Paused in Painter (:func:`set_paused`, the plugin's menu), a run is refused and
a queued one ends cancelled, saying so.
"""

import contextlib
import os
import time
import traceback
import uuid

from .. import register

#: Jobs by id, oldest first; the last :data:`KEEP` finished ones are kept.
_JOBS = {}
KEEP = 50
_FINISHED = ("done", "failed", "cancelled")

#: Paused from Painter's own menu (``M3trik Scripts > Pause scripts``): no
#: run starts until it is resumed there; a run under way finishes. Nothing over
#: RPC resumes it: switching back on is the person's, in Painter.
_PAUSE = {"on": False}
PAUSED = "Scripts are paused in Painter (M3trik Scripts > Pause scripts): resume them there to run this."


def set_paused(on):
    """Pause (or resume) runs, and say so in the instance record."""
    from .. import PLUGIN  # noqa: PLC0415

    _PAUSE["on"] = bool(on)
    PLUGIN.set_state(paused=_PAUSE["on"])


def is_paused():
    """True while runs are paused in Painter."""
    return _PAUSE["on"]


class ScriptCancelled(Exception):
    """Raised in a script by :meth:`ScriptArgs.check_cancel` once it is cancelled."""


class ScriptArgs(dict):
    """A running script's values, and its line back to whoever started it."""

    def __init__(self, values, job):
        super().__init__(values or {})
        self._job = job

    def report(self, line):
        """Add *line* to the job's report: what the panel shows as the result."""
        self._job["report"].append(str(line))

    def progress(self, fraction, text=""):
        """Say how far the script is (0..1), with an optional *text*."""
        self._job["progress"] = {"fraction": float(fraction), "text": str(text)}

    def cancelled(self):
        """True once the job was asked to stop."""
        return bool(self._job["cancel"])

    def check_cancel(self):
        """Raise :class:`ScriptCancelled` once the job was asked to stop."""
        if self._job["cancel"]:
            raise ScriptCancelled()

    def when_idle(self, fn):
        """Run *fn* (no arguments) once Painter is idle, after the script and
        any step before it: the job ends after its last step."""
        self._job["_steps"].append(fn)


class _Console(object):
    """A stream writing into the job's console, a line at a time."""

    def __init__(self, job):
        self._job = job
        self._part = ""

    def write(self, text):
        self._part += str(text)
        *lines, self._part = self._part.split("\n")
        self._job["console"].extend(line for line in lines if line.strip())
        return len(text)

    def flush(self):
        if self._part.strip():
            self._job["console"].append(self._part)
        self._part = ""


def _same_path(a, b):
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))  # noqa: E731
    return bool(a and b) and norm(a) == norm(b)


def _refuse_other_project(expect):
    """Raise unless Painter holds the project *expect* names (``uuid`` and/or
    ``path``): a run picked for one project never lands on another, whatever
    was opened since."""
    if not expect:
        return
    from .project_ops import project_info  # noqa: PLC0415

    info = project_info()
    meant = expect.get("path") or expect.get("uuid")
    if not info["is_open"]:
        raise RuntimeError(f"Painter has no project open; this run was meant for {meant}.")
    if expect.get("uuid") and info.get("uuid") and expect["uuid"] != info["uuid"]:
        raise RuntimeError(f"Painter now has {info.get('name') or 'another project'} open; this run was meant for {meant}.")
    if expect.get("path") and not _same_path(expect["path"], info.get("file_path")):
        raise RuntimeError(f"Painter now has {info.get('file_path') or 'an unsaved project'} open; this run was meant for {meant}.")


@contextlib.contextmanager
def _undo_entry(label):
    """One entry in Painter's history for the run, when a project is open."""
    scope = None
    try:
        import substance_painter.layerstack as layerstack  # noqa: PLC0415
        import substance_painter.project as project  # noqa: PLC0415

        if project.is_open():
            scope = layerstack.ScopedModification(label)
    except Exception:  # noqa: BLE001 -- no undo scope here: run without one
        scope = None
    if scope is None:
        yield
        return
    with scope:
        yield


#: Seconds a job waits for Painter's shelves to finish loading before it runs anyway.
SHELF_WAIT = 120.0


def _after(ms, fn):
    """Run *fn* in *ms* on Painter's main thread (its event loop); at once
    where there is none to wait on (a test process, an op off that thread)."""
    try:
        from PySide6 import QtCore  # noqa: PLC0415 -- Painter's own binding

        app = QtCore.QCoreApplication.instance()
        if app is not None and QtCore.QThread.currentThread() is app.thread():
            QtCore.QTimer.singleShot(ms, fn)
            return
    except ImportError:
        pass
    fn()


def _shelves_loading():
    """True while a shelf is still discovering its resources (a Painter just
    launched): a script naming a shelf export preset or material would not find
    it yet, and ``project.is_busy`` does not say so."""
    try:
        import substance_painter.resource as resource  # noqa: PLC0415
    except ImportError:
        return False
    shelves = getattr(resource, "Shelves", None)
    if shelves is None:
        return False
    try:
        return any(shelf.is_crawling() for shelf in shelves.all())
    except Exception as exc:  # noqa: BLE001
        # ServiceNotFoundError: Painter has not started its services yet.
        return type(exc).__name__ == "ServiceNotFoundError"


def _later(fn):
    """Run *fn* once Painter is idle (not busy, its shelves loaded, at most
    :data:`SHELF_WAIT` for those), after this op has answered."""
    deadline = time.monotonic() + SHELF_WAIT

    def when_idle():
        if _shelves_loading() and time.monotonic() < deadline:
            _after(500, when_idle)
            return
        try:
            import substance_painter.project as project  # noqa: PLC0415

            project.execute_when_not_busy(fn)
        except ImportError:
            fn()

    _after(0, when_idle)


def _step(job, fn):
    """Run one piece of the job (the script, or one of its idle steps) in its
    undo entry, its output the job's console; False when the job ended in it.
    Painter must still hold the project the run was meant for: the job waited
    for Painter to be idle, and a person may have opened another meanwhile."""
    console = _Console(job)
    try:
        _refuse_other_project(job["expect"])
        with _undo_entry(job["label"]), contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
            fn()
        return True
    except ScriptCancelled:
        job["state"] = "cancelled"
    except BaseException as exc:  # noqa: BLE001 -- a script's failure is its job's, never Painter's
        job["state"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["traceback"] = traceback.format_exc()
    finally:
        console.flush()
    return False


def _end(job, state=None):
    if state:
        job["state"] = state
    job["ended"] = time.time()
    job["_steps"] = []
    _prune()


def _run(job, code):
    if job["cancel"]:
        _end(job, "cancelled")
        return
    if _PAUSE["on"]:  # paused while it waited for Painter to be idle
        job["error"] = PAUSED
        _end(job, "cancelled")
        return
    job["state"], job["started"] = "running", time.time()
    namespace = {"__name__": "__substance_script__", "__file__": job["path"], "args": ScriptArgs(job["args"], job)}
    try:
        import substance_painter  # noqa: PLC0415

        namespace["substance_painter"] = substance_painter
    except ImportError:
        pass
    if not _step(job, lambda: exec(code, namespace)):
        _end(job)
    elif job["_steps"]:
        _later(lambda: _next_step(job))
    else:
        _end(job, "done")


def _next_step(job):
    """The job's next idle step; the job ends after the last."""
    if job["cancel"]:
        _end(job, "cancelled")
        return
    if not _step(job, job["_steps"].pop(0)):
        _end(job)
    elif job["_steps"]:
        _later(lambda: _next_step(job))
    else:
        _end(job, "done")


def _prune():
    finished = [k for k, j in _JOBS.items() if j["state"] in _FINISHED]
    for key in finished[: max(0, len(finished) - KEEP)]:
        del _JOBS[key]


def _public(job):
    return {k: v for k, v in job.items() if k not in ("args", "cancel", "_steps")}


@register("script.start")
def start(source="", path="", args=None, expect=None, label=""):
    """Queue *source* (a script's text; *path* names it in tracebacks) with
    *args*; ``{"job", "state"}``.

    Raises before queueing while runs are paused in Painter, when it does not
    compile, or when *expect* (``{uuid, path}`` of the project it was meant
    for) is not the project Painter holds.
    """
    if _PAUSE["on"]:
        raise RuntimeError(PAUSED)
    code = compile(source, path or "<script>", "exec")
    _refuse_other_project(expect)
    job_id = uuid.uuid4().hex
    _JOBS[job_id] = job = {
        "job": job_id,
        "path": path,
        "label": label or os.path.splitext(os.path.basename(path or "script"))[0],
        "state": "queued",
        "queued": time.time(),
        "args": dict(args or {}),
        "cancel": False,
        "report": [],
        "console": [],
        "progress": None,
        "error": None,
        "traceback": None,
        "_steps": [],
        "expect": expect,
    }
    _later(lambda: _run(job, code))
    return {"job": job_id, "state": job["state"]}


@register("script.status")
def status(job=""):
    """The job's state, report, console, progress and error; ``None`` for a job
    this Painter does not know (it restarted, or the job was pruned)."""
    found = _JOBS.get(job)
    return None if found is None else _public(found)


@register("script.cancel")
def cancel(job=""):
    """Stop the job: a queued one never starts; a running one stops at its next
    ``args.check_cancel()``. The job's status after."""
    found = _JOBS.get(job)
    if found is None:
        return None
    if found["state"] not in _FINISHED:
        found["cancel"] = True
    return _public(found)


@register("script.jobs")
def jobs():
    """Every kept job's status, oldest first."""
    return [_public(j) for j in _JOBS.values()]
