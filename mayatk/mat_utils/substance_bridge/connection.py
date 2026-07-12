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

Session safety: :class:`SubstanceConnection` always launches a NEW Painter
process. Connecting to an existing session is intentionally not supported.
"""
import os
import subprocess
from typing import Optional, Tuple, List

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher

# Generic stream/tail machinery is pythontk's app-agnostic mechanism; this
# module keeps only the Painter-specific shell that composes it.
from pythontk.core_utils.process_stream import LogTailer, OutputStream, ProcessReader

# PainterRpcClient now lives in the sibling substance_rpc/ namespace.
# SubstanceConnection re-uses it (and DEFAULT_RPC_PORT) below, so the
# import stays at the connection layer.
from .substance_rpc.client import PainterRpcClient, DEFAULT_RPC_PORT


# Canonical names AppLauncher will try when resolving Painter without an
# explicit path. Shared with :class:`SubstanceBridge` so both layers walk
# the same list and a future name change only needs to be made once.
PAINTER_APP_NAMES: Tuple[str, ...] = (
    "Adobe Substance 3D Painter",
    "Adobe Substance 3D Painter.exe",
    "Painter",
)


def find_painter_exe() -> Optional[str]:
    """Single source of truth for Painter executable discovery.

    Walks :data:`PAINTER_APP_NAMES` against :class:`AppLauncher`.
    Both :class:`SubstanceBridge` and :class:`SubstanceConnection` delegate
    here so a future name/discovery change only happens in one place.
    """
    for name in PAINTER_APP_NAMES:
        found = AppLauncher.find_app(name)
        if found:
            return found
    return None


def default_log_path() -> Optional[str]:
    """Return the standard Substance Painter log path, or None if absent.

    Painter writes to ``%LOCALAPPDATA%\\Adobe\\Adobe Substance 3D Painter\\log.txt``
    on Windows.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    path = os.path.join(local, "Adobe", "Adobe Substance 3D Painter", "log.txt")
    return path if os.path.exists(path) else None


class SubstanceConnection(ptk.LoggingMixin):
    """Launch Painter and expose its stdio, log, and RPC under one object.

    Always launches a NEW Painter instance.
    """

    def __init__(
        self,
        mesh_path: Optional[str] = None,
        exe: Optional[str] = None,
        rpc_port: int = DEFAULT_RPC_PORT,
        enable_remote: bool = True,
        log_path: Optional[str] = None,
        capture_stdio: bool = True,
        tail_log_from_start: bool = False,
        extra_args: Optional[List[str]] = None,
    ):
        super().__init__()
        self.mesh_path = mesh_path
        self.exe = exe
        self.rpc_port = rpc_port
        self.enable_remote = enable_remote
        self.log_path = log_path if log_path is not None else default_log_path()
        self.capture_stdio = capture_stdio
        self.tail_log_from_start = tail_log_from_start
        self.extra_args = list(extra_args) if extra_args else []

        self.process: Optional[subprocess.Popen] = None
        self.output: OutputStream = OutputStream()
        self.rpc: Optional[PainterRpcClient] = None
        self._readers: List[ProcessReader] = []
        self._tailer: Optional[LogTailer] = None

    def _resolve_executable(self) -> str:
        if self.exe:
            if os.path.isabs(self.exe):
                if os.path.exists(self.exe):
                    return self.exe
                self.logger.warning(
                    "Explicit exe path does not exist: %s. Trying discovery.",
                    self.exe,
                )
            else:
                found = AppLauncher.find_app(self.exe)
                if found:
                    return found
                self.logger.warning(
                    "Explicit exe hint '%s' not found via discovery. "
                    "Trying canonical names.",
                    self.exe,
                )
        found = find_painter_exe()
        if found:
            return found
        raise FileNotFoundError(
            "Could not find Substance Painter. Pass exe= or install Painter."
        )

    def open(self) -> "SubstanceConnection":
        """Launch Painter and start readers, tailer, and RPC client."""
        executable_path = self._resolve_executable()

        cmd: List[str] = [executable_path]
        if self.mesh_path:
            cmd.extend(["--mesh", self.mesh_path])
        if self.enable_remote:
            cmd.append("--enable-remote-scripting")
        cmd.extend(self.extra_args)

        cwd = os.path.dirname(self.mesh_path) if self.mesh_path else None

        popen_kwargs: dict = {"cwd": cwd, "shell": False}
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
        self.process = subprocess.Popen(cmd, **popen_kwargs)

        if self.capture_stdio:
            for src in ("stdout", "stderr"):
                pipe = getattr(self.process, src, None)
                if pipe is not None:
                    reader = ProcessReader(pipe, self.output, source=src)
                    reader.start()
                    self._readers.append(reader)

        if self.log_path:
            self._tailer = LogTailer(
                self.log_path,
                self.output,
                tail_from_start=self.tail_log_from_start,
            )
            self._tailer.start()

        if self.enable_remote:
            self.rpc = PainterRpcClient(port=self.rpc_port)

        return self

    def close(self, terminate: bool = False, timeout: float = 5.0) -> None:
        """Stop readers and tailer; optionally terminate Painter."""
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
        """True if Painter is reachable through this connection.

        Owned connection (we launched the process): the OS process handle is
        the authority -- ``poll()`` returns ``None`` while it's running.

        Attached connection (someone else launched Painter): no process
        handle exists, so liveness is inferred from RPC reachability.
        """
        if self.process is not None:
            return self.process.poll() is None
        if self.rpc is not None:
            return self.rpc.ping(timeout=0.5)
        return False

    @classmethod
    def attach(
        cls,
        port: int,
        host: str = "127.0.0.1",
        log_path: Optional[str] = None,
        tail_log_from_start: bool = False,
        verify_alive: bool = True,
        verify_timeout: float = 2.0,
    ) -> "SubstanceConnection":
        """Bind to a running Painter on *port* without launching anything.

        Use this when a Painter instance was launched elsewhere (e.g. an
        earlier :meth:`open` call or, in theory, by the user) with
        ``--enable-remote-scripting`` exposed on *port*.

        The connection wires up:
          - :class:`PainterRpcClient` for JS dispatch.
          - :class:`LogTailer` for one-way log streaming (if *log_path*
            resolves; defaults to :func:`default_log_path`).

        stdio capture is intentionally unavailable -- the OS pipes were
        never inherited by this process. Use :meth:`open` if you need them.

        Parameters:
            port: RPC port the running Painter is listening on.
            host: Host. Defaults to loopback.
            log_path: Override for the log file location.
            tail_log_from_start: Replay existing log content rather than
                tailing from EOF.
            verify_alive: If True, ping the port and raise
                :class:`ConnectionRefusedError` on no response. Set False
                to defer the check (e.g. the caller will ping themselves).
            verify_timeout: Seconds to wait for the verification ping.

        Returns:
            A new :class:`SubstanceConnection` with ``process=None`` and
            ``rpc`` already wired.

        Raises:
            ConnectionRefusedError: If *verify_alive* is True and no
                Painter responds within *verify_timeout*.
        """
        conn = cls(
            mesh_path=None,
            exe=None,
            rpc_port=port,
            enable_remote=True,
            log_path=log_path,
            capture_stdio=False,
            tail_log_from_start=tail_log_from_start,
        )
        conn.rpc = PainterRpcClient(host=host, port=port)
        if verify_alive and not conn.rpc.ping(timeout=verify_timeout):
            raise ConnectionRefusedError(
                f"No Painter RPC on {host}:{port} within {verify_timeout}s."
            )
        if conn.log_path:
            conn._tailer = LogTailer(
                conn.log_path,
                conn.output,
                tail_from_start=conn.tail_log_from_start,
            )
            conn._tailer.start()
        return conn

    def __enter__(self) -> "SubstanceConnection":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
