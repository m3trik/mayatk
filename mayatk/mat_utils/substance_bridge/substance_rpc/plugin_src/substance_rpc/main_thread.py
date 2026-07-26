# !/usr/bin/python
# coding=utf-8
"""Main-thread marshalling for ops that touch Painter's API.

The HTTP server runs on a daemon thread. ``substance_painter.*`` calls
must run on the Qt main thread -- calling them off-thread is undefined
and tends to crash Painter. This module exposes
:func:`run_on_main_thread` which:

* Inside Painter (Qt event loop alive, current thread != main):
  schedules the call via ``QTimer.singleShot(0, ...)`` and blocks the
  caller on a queue until it completes.
* Outside Painter (no QApplication) OR already on the main thread:
  just calls the function directly. Lets tests run the same code path
  without needing a Qt event loop.

The Qt binding is detected lazily (PySide6 for current Painter, PySide2
for older builds, then None) so the registry/server modules stay
import-safe in environments without Qt.

Vendored copy of marmoset_rpc's marshaller (see registry.py for why).
"""
import os
import queue
import threading


_DEFAULT_TIMEOUT = 60.0

# Tests (and any non-Painter environment that happens to have a stray
# QApplication around) can disable marshalling outright by setting this
# env var. Without it, the marshaller would detect the Qt instance, try
# to schedule via QTimer, and hang because nothing is pumping the event
# loop. Production Painter never sets this.
_DISABLE_ENV = "SUBSTANCE_RPC_DISABLE_MAIN_THREAD"


def _get_qtcore():
    """Return the active Qt binding's ``QtCore`` module, or ``None``."""
    try:
        from PySide6 import QtCore  # type: ignore  # noqa: PLC0415
        return QtCore
    except ImportError:
        pass
    try:
        from PySide2 import QtCore  # type: ignore  # noqa: PLC0415
        return QtCore
    except ImportError:
        pass
    return None


def run_on_main_thread(fn, *args, timeout=_DEFAULT_TIMEOUT, **kwargs):
    """Run *fn* on the Qt main thread; block until it returns or raises.

    Execution modes:
      1. **No Qt** (tests / non-Painter use): call directly. *fn*\\ 's
         exceptions propagate as normal.
      2. **No QApplication instance**: same as (1).
      3. **Already on the main thread**: same as (1) -- no need to
         marshal a call onto the thread it's already on, and the
         QTimer trick would deadlock if we tried.
      4. **Off-thread with a live Qt event loop**: schedule via
         ``QTimer.singleShot``, drain a one-slot queue back, propagate
         the original exception verbatim.

    *timeout* applies only to mode 4. If the main thread is blocked
    (e.g. Painter mid-bake), a ``TimeoutError`` is raised so the HTTP
    request doesn't hang indefinitely.
    """
    if os.environ.get(_DISABLE_ENV) == "1":
        return fn(*args, **kwargs)

    qtcore = _get_qtcore()
    if qtcore is None:
        return fn(*args, **kwargs)

    app = qtcore.QCoreApplication.instance()
    if app is None:
        return fn(*args, **kwargs)

    if qtcore.QThread.currentThread() == app.thread():
        return fn(*args, **kwargs)

    result_q: "queue.Queue[tuple]" = queue.Queue(maxsize=1)

    def _runner():
        try:
            result_q.put(("ok", fn(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001
            result_q.put(("err", exc))

    # ``QTimer.singleShot(0, ...)`` schedules onto the receiver's
    # thread. Without a receiver, the 0-delay overload uses the app's
    # thread by default, which is exactly what we want.
    qtcore.QTimer.singleShot(0, _runner)

    try:
        kind, payload = result_q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(
            f"Main-thread call did not complete within {timeout}s. "
            f"Painter's event loop is probably blocked."
        )

    if kind == "err":
        raise payload
    return payload


def is_main_thread_marshalling_active():
    """True if :func:`run_on_main_thread` will actually marshal a call.

    False when the env-var bypass is set, when Qt isn't installed, or
    when no QApplication exists. Useful for diagnostics -- a log line
    at server start can tell the user whether ops will trampoline onto
    the main thread or just run in-place.
    """
    if os.environ.get(_DISABLE_ENV) == "1":
        return False
    qtcore = _get_qtcore()
    if qtcore is None:
        return False
    app = qtcore.QCoreApplication.instance()
    if app is None:
        return False
    return qtcore.QThread.currentThread() != app.thread()
