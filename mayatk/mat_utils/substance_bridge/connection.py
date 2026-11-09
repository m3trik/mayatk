# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter connection module.

Process / log plumbing for a Painter session this code launched. The
JSON-RPC client itself lives in the sibling
:mod:`substance_bridge.substance_rpc` namespace; this module wires one
up inside :class:`SubstanceConnection` but no longer re-exports it --
callers wanting the bare client should import it from
``substance_bridge.substance_rpc``.

Two independent capabilities, unified behind the app-agnostic
:class:`pythontk.OutputStream` (see ``pythontk.core_utils.process_stream``):

1. **Process stdio capture** -- ``subprocess.PIPE`` on Painter's
   stdout/stderr, read line-by-line on background threads
   (:class:`pythontk.ProcessReader`).
2. **Log file tailing** -- polls Painter's ``log.txt`` for new bytes
   (:class:`pythontk.LogTailer`). One-way; survives log rotation.

Consumers can react to output with either:
- streaming: ``stream.subscribe(callback)`` or ``for src, line in stream:``
- blocking: ``stream.wait_for(pattern, timeout=N)``

A Painter is found by its instance record (``substance_rpc``'s, read by
:meth:`PainterRpcClient.painters`), never by a port: :meth:`SubstanceConnection.open`
launches one and waits for the record whose PID is the one it launched;
:meth:`SubstanceConnection.attach` takes a record a caller chose.

Session safety: the tooling (agents, tests) only ever launches
(:meth:`SubstanceConnection.open`). :meth:`attach` is a product's, run when a
person asks it to work in a Painter they opened and enabled the plugin in;
:meth:`close` ends only a Painter this connection launched.

**Vendored copy -- keep byte-identical.** This file ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``).
"""

import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pythontk as ptk
from pythontk import AppLauncher

# Generic stream/tail machinery is pythontk's app-agnostic mechanism; this
# module keeps only the Painter-specific shell that composes it.
from pythontk import LogTailer, OutputStream, ProcessReader

from .substance_rpc.client import PainterRpcClient
from .substance_rpc.installer import Installer


# Declarative Painter discovery. ONE AppSpec carries the canonical names AND the
# user-facing "couldn't find it" sentence, so every layer -- SubstanceBridge,
# SubstanceConnection, and the availability gate that greys the panel's launch
# button -- reads one declaration and a future name change happens once.
APP = ptk.AppSpec(
    name="Adobe Substance 3D Painter",
    app_names=(
        "Adobe Substance 3D Painter",
        "Adobe Substance 3D Painter.exe",
        "Painter",
    ),
    # Linux installs to /opt/Adobe (system) or ~/Adobe, on no PATH; ``?`` takes
    # the space or underscore either spelling uses.
    scan_globs=(
        "/opt/Adobe/Adobe?Substance?3D?Painter/Adobe?Substance?3D?Painter",
        "~/Adobe/Adobe?Substance?3D?Painter/Adobe?Substance?3D?Painter",
    ),
    not_found_msg=(
        "Adobe Substance 3D Painter not found. Install it, or pass an explicit "
        "executable path."
    ),
)


class SubstanceConnection(ptk.LoggingMixin):
    """One Painter: launched here (its stdio, log and process) or attached by
    its instance record; :attr:`rpc` reaches it once it serves."""

    #: Qt platforms that open no window.
    _WINDOWLESS = ("offscreen", "minimal")

    def __init__(
        self,
        exe: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        gui: bool = True,
        log_path: Optional[str] = None,
        capture_stdio: bool = True,
        tail_log_from_start: bool = False,
        hidden: bool = False,
    ):
        super().__init__()
        self.exe = exe
        self.extra_args = list(extra_args) if extra_args else []
        #: ``False`` runs Painter on Qt's offscreen platform (it has no
        #: headless option): no window, a project still made and baked.
        self.gui = gui
        #: Windows: run Painter's window on a private desktop (``AppLauncher``'s
        #: hidden launch): a real GPU surface (offscreen, Painter's Vulkan logs
        #: surface errors) that no one sees or can type into, ended with this
        #: process.
        self.hidden = hidden
        self.log_path = log_path if log_path is not None else SubstanceConnection.default_log_path()
        self.capture_stdio = capture_stdio
        self.tail_log_from_start = tail_log_from_start

        self.process: Optional[subprocess.Popen] = None
        self.output: OutputStream = OutputStream()
        self.rpc: Optional[PainterRpcClient] = None
        #: The instance record this connection reaches (``None`` until it serves).
        self.record: Optional[Dict[str, Any]] = None
        self._readers: List[ProcessReader] = []
        self._tailer: Optional[LogTailer] = None

    def _resolve_executable(self) -> str:
        if self.exe:
            if os.path.isabs(self.exe):
                if os.path.exists(self.exe):
                    return self.exe
                self.logger.warning("Explicit exe path does not exist: %s. Trying discovery.", self.exe)
            else:
                found = AppLauncher.find_app(self.exe)
                if found:
                    return found
                self.logger.warning(
                    "Explicit exe hint '%s' not found via discovery. Trying canonical names.", self.exe
                )
        found = SubstanceConnection.find_painter_exe()
        if found:
            return found
        raise FileNotFoundError("Could not find Substance Painter. Pass exe= or install Painter.")

    def launch_env(self) -> Dict[str, str]:
        """The launch's environment: the plugin's launch root on Painter's
        plugin path (:meth:`Installer.launch_env`) and, without :attr:`gui`, the
        offscreen platform; a windowless platform this process carries (a test
        run's) is dropped when the window should show."""
        env = Installer.launch_env()
        platform = env.get("QT_QPA_PLATFORM", "").split(":")[0]
        if not self.gui:
            env["QT_QPA_PLATFORM"] = "offscreen"
        elif platform in self._WINDOWLESS:
            env.pop("QT_QPA_PLATFORM")
        return env

    def open(self) -> "SubstanceConnection":
        """Launch a new Painter (it loads the plugin) and start its readers and
        log tail; :meth:`wait_ready` then finds it serving."""
        cmd: List[str] = [self._resolve_executable()] + self.extra_args
        popen_kwargs: dict = {"shell": False, "env": self.launch_env()}
        if self.capture_stdio:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.PIPE
        else:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        self.logger.info("Launching Painter: %s", cmd)
        if self.hidden:
            self.process = AppLauncher.launch(cmd[0], args=cmd[1:], env=popen_kwargs["env"], detached=False, hidden=True)
            if self.process is None:
                raise FileNotFoundError(f"Could not launch {cmd[0]} hidden.")
            self._tail_log()
            return self
        self.process = subprocess.Popen(cmd, **popen_kwargs)

        if self.capture_stdio:
            for src in ("stdout", "stderr"):
                pipe = getattr(self.process, src, None)
                if pipe is not None:
                    reader = ProcessReader(pipe, self.output, source=src)
                    reader.start()
                    self._readers.append(reader)
        self._tail_log()
        return self

    def _tail_log(self) -> None:
        if self.log_path:
            self._tailer = LogTailer(self.log_path, self.output, tail_from_start=self.tail_log_from_start)
            self._tailer.start()

    def wait_ready(self, timeout: float = 180.0, poll: float = 0.5) -> bool:
        """Wait until the launched Painter serves: its own instance record (the
        PID this connection launched), answering. False on timeout or exit."""
        if self.rpc is not None:
            return True
        if self.process is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.logger.error("Painter exited (code %s) before it served.", self.process.returncode)
                return False
            for record in PainterRpcClient.painters():
                if record.get("pid") == self.process.pid:
                    client = PainterRpcClient.for_instance(record)
                    if client.ping(timeout=1.0):
                        self.record, self.rpc = record, client
                        return True
            time.sleep(poll)
        return False

    @classmethod
    def attach(cls, record: Dict[str, Any], log_path: Optional[str] = None) -> "SubstanceConnection":
        """A connection to the running Painter *record* names
        (:meth:`PainterRpcClient.painters`), launching nothing.

        Raises:
            ConnectionRefusedError: it no longer answers.
        """
        conn = cls(log_path=log_path, capture_stdio=False)
        conn.record, conn.rpc = record, PainterRpcClient.for_instance(record)
        if not conn.rpc.ping(timeout=2.0):
            raise ConnectionRefusedError(f"Painter (pid {record.get('pid')}) no longer answers.")
        return conn  # no log tail: nothing would end it, and the log is Painter's own

    @property
    def launched(self) -> bool:
        """True for a Painter this connection launched (the only kind it ends)."""
        return self.process is not None

    def project(self) -> Optional[Dict[str, Any]]:
        """The project Painter holds now (``name``, ``uuid``, ``file_path``), or ``None``."""
        if self.rpc is None:
            return None
        info = self.rpc.project_info()
        return {k: info.get(k) for k in ("name", "uuid", "file_path")} if info.get("is_open") else None

    def close(self, terminate: bool = False, timeout: float = 5.0) -> None:
        """Stop the log tail; with *terminate*, end the Painter this connection
        launched (an attached one is never ended)."""
        if self._tailer:
            self._tailer.stop()
            self._tailer = None
        if terminate and self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.output.close()

    def is_alive(self) -> bool:
        """True while Painter is reachable: a launched one while its process
        runs, an attached one while it answers."""
        if self.process is not None:
            return self.process.poll() is None
        if self.rpc is not None:
            return self.rpc.ping(timeout=0.5)
        return False

    def __enter__(self) -> "SubstanceConnection":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def find_painter_exe() -> Optional[str]:
        """Single source of truth for Painter executable discovery.

        Delegates to :data:`APP`, the module's single discovery declaration.
        Both :class:`SubstanceBridge` and :class:`SubstanceConnection` call here
        so a future name/discovery change only happens in one place.

        Uses ``resolve()`` (an uncached look) rather than ``APP.path``: this is
        the LAUNCH path, where an app installed mid-session must be found. The
        cached ``APP.available`` is for the availability gate, which asks on every
        panel show and must not re-scan Program Files each time.
        """
        return APP.resolve()

    @staticmethod
    def default_log_path() -> Optional[str]:
        """Return the standard Substance Painter log path, or None if absent.

        Painter writes to ``%LOCALAPPDATA%\\Adobe\\Adobe Substance 3D Painter\\log.txt``
        on Windows; on Linux, Qt's matching app-data folder under
        ``$XDG_DATA_HOME`` (``~/.local/share``).
        """
        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA")
        else:
            local = ptk.UserConfig.xdg_home("data")
        if not local:
            return None
        path = os.path.join(local, "Adobe", "Adobe Substance 3D Painter", "log.txt")
        return path if os.path.exists(path) else None
