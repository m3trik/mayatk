# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter connection module.

Process / log plumbing for a Painter session this code launched. The
JSON-RPC client itself lives in the sibling
:mod:`substance_bridge.substance_rpc` namespace; this module wires one
up inside :class:`SubstanceConnection` but no longer re-exports it --
callers wanting the bare client should import it from
``substance_bridge.substance_rpc``.

Two independent capabilities, unified behind :class:`OutputStream`:

1. **Process stdio capture** -- ``subprocess.PIPE`` on Painter's
   stdout/stderr, read line-by-line on background threads
   (:class:`_ProcessReader`).
2. **Log file tailing** -- polls Painter's ``log.txt`` for new bytes
   (:class:`_LogTailer`). One-way; survives log rotation.

Consumers can react to output with either:
- streaming: ``stream.subscribe(callback)`` or ``for src, line in stream:``
- blocking: ``stream.wait_for(pattern, timeout=N)``

Session safety: :class:`SubstanceConnection` always launches a NEW Painter
process. Connecting to an existing session is intentionally not supported.
"""
import os
import time
import queue
import threading
import subprocess
import collections
import logging
from typing import Optional, Callable, Iterator, Union, Pattern, Tuple, List

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher

# PainterRpcClient now lives in the sibling substance_rpc/ namespace.
# SubstanceConnection re-uses it (and DEFAULT_RPC_PORT) below, so the
# import stays at the connection layer.
from .substance_rpc.client import PainterRpcClient, DEFAULT_RPC_PORT

logger = logging.getLogger(__name__)


_DEFAULT_POLL_INTERVAL = 0.5

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


_DEFAULT_HISTORY = 5000


class OutputStream:
    """Thread-safe, multi-consumer text stream with bounded history.

    Each call to :meth:`subscribe` / :meth:`__iter__` / :meth:`wait_for`
    gets its own queue, so multiple consumers can read independently.

    Records are ``(source, line)`` tuples — ``source`` labels like
    ``"stdout"``, ``"stderr"``, ``"log"`` let consumers filter.

    A bounded ring buffer of recent lines is kept so that consumers which
    subscribe after the stream has started can optionally replay history.
    This closes the start-up race where lines pushed between launch and
    the first ``wait_for`` would otherwise be missed.
    """

    def __init__(self, history: int = _DEFAULT_HISTORY):
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[str, str], None]] = []
        self._history: "collections.deque[Tuple[str, str]]" = collections.deque(
            maxlen=history
        )
        self._closed = False

    def push(self, line: str, source: str = "") -> None:
        """Append a line. Called by readers; consumers should not invoke."""
        if self._closed:
            return
        with self._lock:
            self._history.append((source, line))
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(source, line)
            except Exception:
                logger.exception("OutputStream subscriber raised")

    def subscribe(
        self,
        callback: Callable[[str, str], None],
        replay_history: bool = False,
    ) -> Callable[[], None]:
        """Register ``callback(source, line)``.

        If *replay_history* is True, every buffered line is delivered to the
        callback under the same lock that registers it — so no line is lost
        or duplicated relative to future pushes.

        Returns an unsubscribe handle.
        """
        with self._lock:
            if replay_history:
                for src, line in self._history:
                    try:
                        callback(src, line)
                    except Exception:
                        logger.exception("OutputStream replay raised")
            self._subscribers.append(callback)

        def _unsubscribe():
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def history(self) -> List[Tuple[str, str]]:
        """Snapshot the current history buffer."""
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        """Drop buffered lines. Future pushes are unaffected."""
        with self._lock:
            self._history.clear()

    def __iter__(self) -> Iterator[Tuple[str, str]]:
        """Yield buffered + future ``(source, line)`` until the stream is closed."""
        q: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        unsubscribe = self.subscribe(
            lambda src, line: q.put((src, line)), replay_history=True
        )
        try:
            while True:
                try:
                    yield q.get(timeout=_DEFAULT_POLL_INTERVAL)
                except queue.Empty:
                    if self._closed:
                        return
        finally:
            unsubscribe()

    def wait_for(
        self,
        pattern: Union[str, Pattern],
        timeout: Optional[float] = None,
        source: Optional[str] = None,
        include_history: bool = True,
    ) -> Optional[Tuple[str, str]]:
        """Block until a line matches *pattern*, or *timeout* expires.

        Parameters:
            pattern: Substring (``str``) or compiled ``re.Pattern``.
            timeout: Seconds; ``None`` means no limit.
            source: If given, only consider lines from this source.
            include_history: If True (default), buffered lines are checked
                before waiting for new ones. Set False to ignore history
                and only match future events.

        Returns:
            ``(source, line)`` tuple, or ``None`` on timeout / stream closure.
        """
        if isinstance(pattern, str):
            matches = lambda s: pattern in s
        else:
            matches = lambda s: bool(pattern.search(s))

        q: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        unsubscribe = self.subscribe(
            lambda src, line: q.put((src, line)),
            replay_history=include_history,
        )
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        try:
            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    timeout_chunk = min(remaining, _DEFAULT_POLL_INTERVAL)
                else:
                    timeout_chunk = _DEFAULT_POLL_INTERVAL
                try:
                    src, line = q.get(timeout=timeout_chunk)
                except queue.Empty:
                    if self._closed:
                        return None
                    continue
                if source is not None and src != source:
                    continue
                if matches(line):
                    return (src, line)
        finally:
            unsubscribe()

    def close(self) -> None:
        """Mark the stream closed. Pending iterators and waiters will exit."""
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class _ProcessReader(threading.Thread):
    """Reads a subprocess pipe line-by-line into an :class:`OutputStream`."""

    def __init__(self, pipe, target: OutputStream, source: str):
        super().__init__(daemon=True, name=f"painter-{source}-reader")
        self._pipe = pipe
        self._target = target
        self._source = source

    def run(self) -> None:
        try:
            for raw in iter(self._pipe.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._target.push(line, source=self._source)
        finally:
            try:
                self._pipe.close()
            except Exception:
                pass


class _LogTailer(threading.Thread):
    """Tails a log file from its current size forward.

    Handles log rotation: if the file shrinks (rotated), reads from 0.
    """

    def __init__(
        self,
        log_path: str,
        target: OutputStream,
        source: str = "log",
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        tail_from_start: bool = False,
    ):
        super().__init__(daemon=True, name="painter-log-tailer")
        self._path = log_path
        self._target = target
        self._source = source
        self._poll = poll_interval
        self._tail_from_start = tail_from_start
        self._stop_event = threading.Event()
        self._pending = b""

    def stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _file_id(path: str) -> Optional[int]:
        try:
            return os.stat(path).st_ino
        except OSError:
            return None

    def run(self) -> None:
        if self._tail_from_start:
            position = 0
        else:
            position = (
                os.path.getsize(self._path) if os.path.exists(self._path) else 0
            )
        last_id = self._file_id(self._path)
        while not self._stop_event.is_set():
            try:
                if os.path.exists(self._path):
                    current_id = self._file_id(self._path)
                    if current_id is not None and current_id != last_id:
                        # File was replaced (rotated via rename or delete+recreate).
                        position = 0
                        self._pending = b""
                        last_id = current_id
                    size = os.path.getsize(self._path)
                    if size < position:
                        position = 0
                        self._pending = b""
                    if size > position:
                        with open(self._path, "rb") as f:
                            f.seek(position)
                            chunk = f.read(size - position)
                            position = f.tell()
                        self._emit(chunk)
                else:
                    last_id = None
            except OSError:
                logger.debug("LogTailer read error on %s", self._path, exc_info=True)
            self._stop_event.wait(timeout=self._poll)

    def _emit(self, chunk: bytes) -> None:
        data = self._pending + chunk
        *complete, self._pending = data.split(b"\n")
        for raw in complete:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if line:
                self._target.push(line, source=self._source)


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
        self._readers: List[_ProcessReader] = []
        self._tailer: Optional[_LogTailer] = None

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
                    reader = _ProcessReader(pipe, self.output, source=src)
                    reader.start()
                    self._readers.append(reader)

        if self.log_path:
            self._tailer = _LogTailer(
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
          - :class:`_LogTailer` for one-way log streaming (if *log_path*
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
            conn._tailer = _LogTailer(
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
