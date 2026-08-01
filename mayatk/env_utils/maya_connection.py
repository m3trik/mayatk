# !/usr/bin/python
# coding=utf-8
"""
Maya Connection Module

Provides utilities to connect to Maya either via command port or standalone mode.
Supports both interactive Maya sessions and batch testing.
"""

import os
import socket
import sys
import base64
from typing import Optional, Literal, List, Union, Callable

# Initialize QApplication for standalone mode
try:
    from qtpy import QtWidgets

    if not QtWidgets.QApplication.instance():
        _app = QtWidgets.QApplication([])
except ImportError:
    pass
except Exception as e:
    print(f"Warning: Failed to initialize QApplication: {e}")


class MayaConnection:
    """Manages connection to Maya for testing purposes."""

    ConnectionMode = Literal["port", "standalone", "interactive"]
    _instance = None
    _open_command_ports: dict = {}  # {":7001": "mel", ":7002": "python"}
    # Width of the port window a launched Maya may self-heal into when its
    # requested port turns out to be taken at bind time (the pre-launch probe
    # races Maya's slow boot). The startup MEL scans [port, port + SPAN) and
    # the runner's wait loop accepts any port in that window owned by the
    # launched PID.
    PORT_SCAN_SPAN: int = 20

    @staticmethod
    def get_instance() -> "MayaConnection":
        """Get the global Maya connection instance."""
        if MayaConnection._instance is None:
            MayaConnection._instance = MayaConnection()
        return MayaConnection._instance

    @staticmethod
    def _get_script_editor_text() -> str:
        """Get the text from the Maya Script Editor (Internal)."""
        import maya.cmds as cmds
        import maya.mel as mel

        if not cmds.control("cmdScrollFieldReporter1", exists=True):
            mel.eval("ScriptEditor;")
        return cmds.cmdScrollFieldReporter(
            "cmdScrollFieldReporter1", query=True, text=True
        )

    @staticmethod
    def _clear_script_editor_text() -> bool:
        """Clear the Maya Script Editor (Internal)."""
        import maya.cmds as cmds
        import maya.mel as mel

        try:
            if not cmds.control("cmdScrollFieldReporter1", exists=True):
                mel.eval("ScriptEditor;")
            cmds.cmdScrollFieldReporter(
                "cmdScrollFieldReporter1", edit=True, clear=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    def open_command_ports(**kwargs):
        """Open command ports for external script editor.

        Parameters:
            kwargs (str) = 'source type':'port name'
                source type (str) = The string argument is used to indicate which source type would be passed to the commandPort, ex. "mel" or "python".
                port name (str) = Specifies the name of the command port which this command creates.
        Example:
            MayaConnection.open_command_ports(mel=':7001', python=':7002')
        """
        import maya.cmds as cmds
        import maya.mel as mel

        for source_type, port in kwargs.items():
            # cmds.commandPort has no Python-friendly "is this name open" query
            # (passing name= with query=True asks Maya to query the name flag
            # itself, which expects a bool). Use the MEL idiom instead.
            if mel.eval(f'commandPort -q -name "{port}"'):
                cmds.commandPort(name=port, close=True)

            try:
                cmds.commandPort(name=port, sourceType=source_type)
                MayaConnection._open_command_ports[port] = source_type
            except RuntimeError as e:
                print(f"[commandPort] Failed to open {port} ({source_type}): {e}")

    @staticmethod
    def close_command_ports(ports=None):
        """Close the specified Maya command ports.

        Parameters:
            ports: Port names to close.  If *None*, closes all tracked ports.

        Returns:
            list: Names of the ports that were successfully closed.
        """
        import maya.cmds as cmds

        if ports is None:
            ports = list(MayaConnection._open_command_ports.keys())

        closed = []
        for port in ports:
            try:
                cmds.commandPort(name=port, close=True)
                closed.append(port)
            except RuntimeError as e:
                print(f"[commandPort] Failed to close {port}: {e}")
            MayaConnection._open_command_ports.pop(port, None)
        return closed

    @staticmethod
    def _is_port_free(port_num: int) -> bool:
        """Connect-probe: True if nothing is LISTENING on localhost:port.

        The right question when DETECTING an existing session to connect
        to. When CHOOSING a port to open a listener on, use
        :meth:`_tcp_port_bindable` instead — this probe reads a zombie's
        bound-but-not-listening socket as free.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = s.connect_ex(("localhost", port_num))
            s.close()
            return result != 0  # 0 means connected → port in use
        except Exception:
            return True

    @staticmethod
    def _confirm_existing_session(port: int) -> bool:
        """Show a confirmation dialog before connecting to an existing Maya session.

        Attempts a Qt dialog first.  Falls back to a console prompt if no
        display is available (e.g. headless CI).

        Returns:
            True if the user confirms, False if they cancel.
        """
        msg = (
            f"A process is trying to connect to Maya on port {port}.\n\n"
            f"  Host:  {os.environ.get('COMPUTERNAME', 'localhost')}\n"
            f"  Port:  {port}\n"
            f"  PID:   {os.getpid()}\n\n"
            "Connecting may modify or reset the scene in that session "
            "and any unsaved work could be lost.\n\n"
            "Allow this connection?"
        )
        try:
            from qtpy import QtWidgets

            app = QtWidgets.QApplication.instance()
            if app is None:
                app = QtWidgets.QApplication([])

            result = QtWidgets.QMessageBox.warning(
                None,
                "Connect to Existing Maya Session?",
                msg,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,  # default to No — safer
            )
            return result == QtWidgets.QMessageBox.Yes
        except Exception:
            # Fallback: console prompt
            try:
                answer = (
                    input(f"[MayaConnection] {msg}\nProceed? [y/N]: ").strip().lower()
                )
                return answer in ("y", "yes")
            except (EOFError, OSError):
                # Non-interactive environment with no Qt — deny by default
                print(
                    "[MayaConnection] No interactive prompt available. "
                    "Connection to existing session denied for safety."
                )
                return False

    @classmethod
    def _tcp_port_bindable(cls, port: int) -> bool:
        """Could a NEW listener bind this TCP port?

        The right question when CHOOSING a port to open: a connect probe
        (:meth:`_is_port_free`) reads a hung process's bound-but-not-
        listening socket as free, and anything then launched on that port
        can never open it (root cause of the 2026-07-09 launch hangs).
        """
        try:
            from pythontk import NetUtils

            return NetUtils.is_port_bindable(port)
        except AttributeError:
            # Published pythontk without is_port_bindable yet (wheel lag):
            # fall back to the legacy connect probe. Fail OPEN — failing
            # closed reads EVERY port as busy and kills port-mode use
            # outright (worse than missing only the rare zombie-squatter
            # case the bind probe exists for).
            from pythontk import NetUtils

            return not NetUtils.is_port_open("127.0.0.1", port)
        except Exception:
            return False

    @classmethod
    def _find_port_pair(
        cls, mel_start: int = 7001, python_start: int = 7002, max_offset: int = 50
    ) -> tuple:
        """Find two bindable consecutive ports starting from the given defaults.

        Returns:
            tuple: (mel_port_str, python_port_str) e.g. (':7001', ':7002').

        Raises:
            RuntimeError: If no pair of free ports can be found.
        """
        # First, try the exact requested pair
        if cls._tcp_port_bindable(mel_start) and cls._tcp_port_bindable(python_start):
            return (f":{mel_start}", f":{python_start}")

        # Fall back: search from mel_start upward in steps of 2
        for offset in range(2, max_offset * 2, 2):
            mel_p = mel_start + offset
            py_p = mel_p + 1
            if cls._tcp_port_bindable(mel_p) and cls._tcp_port_bindable(py_p):
                return (f":{mel_p}", f":{py_p}")

        raise RuntimeError(
            f"No available port pair found near {mel_start}/{python_start}"
        )

    @staticmethod
    def open_available_command_ports(
        mel_start: int = 7001,
        python_start: int = 7002,
        max_offset: int = 50,
        tag_window: bool = True,
    ) -> dict:
        """Open command ports auto-negotiating around port collisions.

        Use from ``userSetup.py`` so multiple concurrent Maya instances each
        get a unique pair of ports instead of fighting over 7001/7002.

        Parameters:
            mel_start: Preferred MEL port (default 7001).
            python_start: Preferred Python port (default 7002).
            max_offset: Max port-pair offset to scan (default 50).
            tag_window: If True, append ``[Port: <python_port>]`` to the
                main window title so the instance is identifiable.

        Returns:
            dict mapping ``{port_name: source_type}`` for the ports that
            were actually opened (e.g. ``{':7003': 'mel', ':7004': 'python'}``).
        """
        import maya.cmds as cmds

        # Maya auto-opens a port named "commandportDefault" on startup when this
        # optionVar is on. With multiple Maya instances that collides and emits
        # "Could not open command port ... because that name is in use." Since
        # we manage our own ports below, disable the built-in default. Persists
        # to userPrefs.mel; takes effect from the next launch onward.
        if cmds.optionVar(exists="commandportOpenByDefault") and cmds.optionVar(
            query="commandportOpenByDefault"
        ):
            cmds.optionVar(intValue=("commandportOpenByDefault", 0))
            print(
                "[commandPort] Disabled built-in 'commandportOpenByDefault' pref "
                "to prevent name collisions on next launch."
            )

        mel_p, py_p = MayaConnection._find_port_pair(
            mel_start, python_start, max_offset
        )
        MayaConnection.open_command_ports(mel=mel_p, python=py_p)
        opened = {
            p: s
            for p, s in MayaConnection._open_command_ports.items()
            if p in (mel_p, py_p)
        }

        if tag_window:
            try:
                py_num = int(py_p.lstrip(":"))
                main_window = "MayaWindow"
                if cmds.window(main_window, exists=True):
                    current = cmds.window(main_window, query=True, title=True) or "Maya"
                    tag = f" [Port: {py_num}]"
                    if tag not in current:
                        cmds.window(main_window, edit=True, title=f"{current}{tag}")
            except Exception as e:
                print(f"[commandPort] Could not tag window title: {e}")

        # Silent on the happy path (default ports were free). Only announce
        # when auto-negotiation kicked in — that's the interesting case.
        if (mel_p, py_p) != (f":{mel_start}", f":{python_start}"):
            print(f"# Info: Command ports auto-negotiated - mel{mel_p}, python{py_p}")
        return opened

    @staticmethod
    def toggle_command_ports(mel_port: int = 7001, python_port: int = 7002) -> tuple:
        """Toggle Maya command ports on or off.

        If ports are currently tracked as open, closes them.
        Otherwise, opens new ports — using the requested numbers if free,
        or the next available pair if they are busy.

        Parameters:
            mel_port: Preferred port number for MEL (default 7001).
            python_port: Preferred port number for Python (default 7002).

        Returns:
            tuple: (is_open: bool, ports: dict) where *is_open* indicates
                the new state and *ports* maps port name → source type,
                e.g. ``{':7001': 'mel', ':7002': 'python'}``.
        """
        if MayaConnection._open_command_ports:
            closed = MayaConnection.close_command_ports()
            result = (False, {p: "closed" for p in closed})
            print(f"[commandPort] Closed: {', '.join(closed)}")
            return result

        mel_p, py_p = MayaConnection._find_port_pair(mel_port, python_port)
        MayaConnection.open_command_ports(mel=mel_p, python=py_p)
        opened = dict(MayaConnection._open_command_ports)
        port_summary = ", ".join(f"{p} ({s})" for p, s in opened.items())
        print(f"[commandPort] Opened: {port_summary}")
        return (True, opened)

    @staticmethod
    def reload_modules(
        modules: Union[str, List[str]],
        include_submodules: bool = True,
        verbose: bool = True,
    ) -> List[str]:
        """
        Reload specified modules and their submodules using pythontk.ModuleReloader.

        Args:
            modules: Single module name or list of module names to reload.
            include_submodules: Whether to reload submodules recursively.
            verbose: Whether to print reload status.

        Returns:
            List of reloaded module names.
        """
        if isinstance(modules, str):
            modules = [modules]

        reloaded_all = []

        try:
            from pythontk import ModuleReloader

            reloader = ModuleReloader(include_submodules=include_submodules)

            for mod_name in modules:
                try:
                    # Import first to ensure it's loaded
                    __import__(mod_name)
                    mod = sys.modules[mod_name]

                    reloaded = reloader.reload(mod)
                    # Convert module objects to names
                    reloaded_names = [m.__name__ for m in reloaded]
                    reloaded_all.extend(reloaded_names)

                    if verbose:
                        print(
                            f"[ModuleReloader] Reloaded {len(reloaded)} modules for '{mod_name}'"
                        )
                except ImportError:
                    if verbose:
                        print(
                            f"[ModuleReloader] Module '{mod_name}' not found/imported, skipping."
                        )
                except Exception as e:
                    print(f"[ModuleReloader] Error reloading '{mod_name}': {e}")

        except ImportError:
            # Fallback if pythontk is not available
            if verbose:
                print(
                    "[ModuleReloader] pythontk not found, using simple sys.modules clearing fallback."
                )

            for mod_name in modules:
                modules_to_clear = [
                    k for k in list(sys.modules.keys()) if mod_name in k
                ]
                for k in modules_to_clear:
                    del sys.modules[k]
                reloaded_all.extend(modules_to_clear)
                if verbose:
                    print(
                        f"[Fallback] Cleared {len(modules_to_clear)} modules matching '{mod_name}'"
                    )

        return reloaded_all

    def __init__(self):
        self.mode: Optional[self.ConnectionMode] = None
        self.is_connected = False
        self._qapp = None
        # Port-mode target. IMPORTANT: _execute_via_port must use these.
        self.host: str = "localhost"
        self.port: int = 7002
        # PID of the Maya instance THIS connection launched (None if we only
        # ever attached). Session safety: it is the only PID we may kill.
        self._launched_pid: Optional[int] = None

    def connect(
        self,
        mode: ConnectionMode = "auto",
        port: int = 7002,
        host: str = "localhost",
        launch: bool = True,
        app_path: Optional[str] = None,
        force_new_instance: bool = True,
        launch_args: Optional[List[str]] = None,
        confirm_existing: bool = True,
        auto_cleanup: bool = False,
    ) -> bool:
        """
        Connect to Maya using the specified mode.

        By default this launches a **new** Maya instance on an available port
        so that an existing session is never disturbed.  Pass
        ``force_new_instance=False`` to reuse an already-running instance.

        When reusing an existing instance a confirmation dialog is shown by
        default so the user can abort if they have unsaved work.  Pass
        ``confirm_existing=False`` to suppress the dialog (e.g. in automated
        scripts that knowingly reuse a session).

        Parameters:
            mode: Connection mode - "port", "standalone", "interactive", or "auto"
            port: Port number for command port connection (default: 7002).
                  When a launch happens this is a STARTING port, not a
                  guarantee: the launch fallback re-picks a bindable port,
                  and the launched Maya may self-heal to a nearby port if
                  its requested port is taken at bind time. The port
                  actually connected to is stored on ``self.port``.
            host: Hostname for command port connection (default: "localhost")
            launch: If True, attempts to launch Maya GUI with the command port open if connection fails.
            app_path: Optional path to the Maya executable to use when launching.
            force_new_instance: If True (default), finds an available port and launches a new Maya
                instance regardless of existing ones.  This prevents accidentally modifying
                a user's open scene.
            launch_args: Optional list of additional arguments to pass to Maya when launching (e.g. ['-noAutoloadPlugins']).
            confirm_existing: If True (default), shows a confirmation dialog when
                connecting to an existing Maya session (only applies when
                ``force_new_instance=False``).  Set to False to skip the dialog.
            auto_cleanup: If True, register an ``atexit`` handler that calls
                :meth:`shutdown` (with ``force=True``) on interpreter exit —
                including unhandled exceptions and ``KeyboardInterrupt``. Use
                when the caller can't wrap connection use in a ``with`` block.
                The handler is idempotent across repeated ``connect()`` calls
                on the same instance and a no-op once shut down.

        Returns:
            bool: True if connection successful
        """
        if force_new_instance:
            port = self.get_available_port(start_port=port)
            launch = True
            print(
                f"[MayaConnection] Force new instance requested. Selected available port: {port}"
            )
        else:
            # An existing Maya session is on this port — require confirmation.
            if not self._is_port_free(port):
                if confirm_existing and not self._confirm_existing_session(port):
                    print(
                        "[MayaConnection] Connection to existing session CANCELLED by user."
                    )
                    return False
                print(
                    "[MayaConnection] WARNING: Connecting to an EXISTING Maya "
                    f"session on port {port}. Any unsaved work in that session "
                    "may be lost if the caller resets the scene."
                )

        if mode == "auto":
            detected_mode = self._detect_mode()
            if detected_mode == "standalone" and launch:
                # If we detected standalone (meaning no interactive or port found),
                # and user wants to launch, we should switch to port mode to trigger the launch attempt
                mode = "port"
            else:
                mode = detected_mode

        if mode == "port":
            connected = self._connect_via_port(host, port)
            if not connected and launch:
                # Re-probe just before launching: in the reuse path the port
                # was never bind-checked (a zombie can hold it bound-but-not-
                # listening — the connect probe above reads that as "free"),
                # and in the force-new path it may have been taken since the
                # initial scan. Launching on an unbindable port guarantees a
                # 10048 in the new Maya.
                launch_port = self.get_available_port(start_port=port)
                if launch_port != port:
                    print(
                        f"[MayaConnection] Port {port} is not bindable — "
                        f"launching on {launch_port} instead."
                    )
                print(
                    f"[MayaConnection] Connection failed. Launching Maya on port {launch_port}..."
                )
                actual = self._launch_maya_gui(
                    launch_port, app_path, extra_args=launch_args
                )
                if actual:
                    connected = self._connect_via_port(host, actual)
            if connected and auto_cleanup:
                self._register_atexit_cleanup()
            return connected
        elif mode == "standalone":
            connected = self._connect_standalone()
            if connected and auto_cleanup:
                self._register_atexit_cleanup()
            return connected
        elif mode == "interactive":
            connected = self._connect_interactive()
            if connected and auto_cleanup:
                self._register_atexit_cleanup()
            return connected
        else:
            raise ValueError(f"Invalid connection mode: {mode}")

    def _register_atexit_cleanup(self) -> None:
        """Register an ``atexit`` handler that shuts this connection down.

        Idempotent — repeated calls register only once. The handler is a
        no-op if the connection has already been shut down by the time the
        interpreter exits, so explicit ``shutdown()`` plus auto-cleanup is
        safe.
        """
        if getattr(self, "_atexit_registered", False):
            return
        import atexit

        def _cleanup():
            if not getattr(self, "is_connected", False):
                return
            try:
                self.shutdown(force=True)
            except Exception as e:
                print(f"[MayaConnection] atexit cleanup failed: {e}")

        atexit.register(_cleanup)
        self._atexit_registered = True

    def _launch_maya_gui(
        self,
        port: int,
        app_path: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
    ) -> Optional[int]:
        """Launch Maya GUI with command port enabled.

        Returns:
            The port the launched Maya actually opened (it may self-heal to a
            nearby port if the requested one was taken at bind time), or
            ``None`` on failure. Truthiness matches the old bool contract.
        """
        from pythontk import AppLauncher

        scan_end = port + self.PORT_SCAN_SPAN

        # The pre-launch port probe races Maya's slow boot: another process
        # can grab the port during the 15-60s startup window, and a fixed
        # bind then died with WSAEADDRINUSE (10048). Instead the startup
        # command scans [port, scan_end) for the first port that binds a
        # fresh PYTHON-source port (a bind failure — taken by another process
        # OR by a name userSetup already opened in this session — just
        # advances the scan), then opens the matching named pipe and stamps
        # the ACTUAL port into the window title. The title is stamped in both
        # branches, so a bind failure no longer aborts the title stamp and
        # leaves an anonymous Maya window; it is also the runner's
        # authoritative discovery channel (see check_port_open below).
        startup_cmds = [
            "int $mtkPort",
            "int $mtkOpen = 0",
            (
                f"for ($mtkPort = {port}; $mtkPort < {scan_end}; $mtkPort++) {{ "
                'if (!catch(`commandPort -name (":" + $mtkPort) -sourceType "python"`)) { $mtkOpen = 1; break; } '
                "}"
            ),
            (
                "if ($mtkOpen) { "
                'catch(`commandPort -name ("mayatk_" + $mtkPort) -sourceType "python"`); '
                'window -e -title ("Maya [Port: " + $mtkPort + "]") $gMainWindow; '
                "} else { "
                'window -e -title "Maya [Port: FAILED]" $gMainWindow; '
                "}"
            ),
        ]
        startup_cmd = ";".join(startup_cmds)

        args = ["-command", startup_cmd]
        if extra_args:
            args.extend(extra_args)

        if app_path:
            print(
                f"[MayaConnection] Launching specific Maya: {app_path} with args: {args}"
            )
            process = AppLauncher.launch(app_path, args=args, detached=True)
        else:
            print(f"[MayaConnection] Launching Maya with args: {args}")
            process = AppLauncher.launch("maya", args=args, detached=True)

            if not process:
                # Try finding a specific version if 'maya' generic isn't found
                # This is a basic fallback, could be expanded
                print(
                    "[MayaConnection] 'maya' not found in path. Checking for specific versions..."
                )
                for ver in ["2025", "2024", "2023", "2022"]:
                    process = AppLauncher.launch(f"maya{ver}", args=args, detached=True)
                    if process:
                        break

        if not process:
            print("[MayaConnection] Failed to launch Maya executable.")
            return None

        self._launched_pid = process.pid
        print(
            f"[MayaConnection] Maya launched (PID: {process.pid}). "
            f"Waiting for a Command Port in {port}-{scan_end - 1} to open..."
        )

        import re

        discovered = {"port": None}
        port_tag = re.compile(r"\[Port: (\d+)\]")

        def check_port_open(proc):
            """True once OUR launched Maya's python command port is open.

            A bare connect probe on the requested port is not enough: if
            another process stole the port, the probe "succeeds" against a
            stranger's Maya — possibly the user's interactive session.

            1. Authoritative: the window-title tag the startup MEL stamps
               with the port it actually bound. PID-owned netstat rows alone
               can't disambiguate — userSetup may open its own command ports
               (e.g. tentacle's mel/python pair) in the same window, and
               adopting a mel-source port would break python execution.
            2. If no window info is available (mocked runs / probe edge
               cases): accept a scan-window port owned by the launched PID.
            3. If netstat itself is unavailable: legacy connect probe on the
               requested port only.
            """
            titles = []
            try:
                titles = list(AppLauncher.get_window_titles(proc.pid) or [])
            except Exception:
                titles = []
            if titles:
                for t in titles:
                    if "[Port: FAILED]" in t:
                        return False  # scan exhausted — timeout reports it
                    match = port_tag.search(t)
                    if match and port <= int(match.group(1)) < scan_end:
                        discovered["port"] = int(match.group(1))
                        return True
                return False  # window is up, stamp not applied yet — wait

            try:
                # via self, not the class name: the in-session test harness
                # reloads this module, and a hard MayaConnection reference
                # would resolve to the reloaded class, escaping test patches.
                rows = self._iter_listening_tcp()
            except Exception:
                # netstat unavailable — legacy connect probe. Identity can't
                # be verified, so only the requested port is accepted.
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    ok = sock.connect_ex(("localhost", port)) == 0
                    sock.close()
                except Exception:
                    return False
                if ok:
                    discovered["port"] = port
                return ok

            for p, owner in rows:
                if owner == proc.pid and port <= p < scan_end:
                    discovered["port"] = p
                    return True
            return False

        # Wait for port to be actually listening. Maya takes a while to load
        # (UI + UserSetup + plugin autoload); a cold start on a laptop can
        # exceed 3 minutes, which made the runner give up and orphan a Maya
        # that opened its port moments later. Default 6 minutes, overridable
        # via MAYATK_PORT_TIMEOUT for slower/faster machines.
        try:
            port_timeout = int(os.environ.get("MAYATK_PORT_TIMEOUT", "360"))
        except ValueError:
            port_timeout = 360
        if AppLauncher.wait_for_ready(
            process, timeout=port_timeout, check_fn=check_port_open
        ):
            actual = discovered["port"] or port
            if actual != port:
                print(
                    f"[MayaConnection] Requested port {port} was taken — "
                    f"Maya self-healed to port {actual}."
                )
            print(f"[MayaConnection] Maya Command Port {actual} is ready.")
            return actual

        # If we got here, we timed out or process died
        if process.poll() is not None:
            print(
                f"[MayaConnection] Maya process exited prematurely with code {process.returncode}."
            )
        else:
            print("[MayaConnection] Timeout waiting for Maya Command Port.")

        return None

    @staticmethod
    def _iter_listening_tcp() -> List[tuple]:
        """Return ``[(local_port, pid), ...]`` for every LISTENING TCP socket
        (Windows ``netstat -ano`` parse; handles IPv4 and IPv6 rows).

        Raises on netstat failure so callers can distinguish "netstat is
        unavailable" from "no matching socket" and choose their fallback.
        """
        import subprocess
        import re

        output = subprocess.check_output(["netstat", "-ano"], universal_newlines=True)
        # e.g. "  TCP    0.0.0.0:7002    0.0.0.0:0    LISTENING    1234"
        # Listening rows are identified by the foreign address ":0" — the
        # state token is LOCALIZED on non-English Windows (e.g. German
        # "ABHÖREN"), so it must not be matched literally. Anchoring on the
        # LOCAL address field means an ESTABLISHED row (foreign port != 0)
        # never matches.
        pattern = re.compile(r"^\s*TCP\s+\S+:(\d+)\s+\S+:0\s+\S+\s+(\d+)\s*$")
        rows = []
        for line in output.splitlines():
            match = pattern.match(line)
            if match:
                rows.append((int(match.group(1)), int(match.group(2))))
        return rows

    @classmethod
    def get_pid_from_port(cls, port: int) -> Optional[int]:
        """
        Find the process ID (PID) listening on the given TCP port.
        Works on Windows using netstat.
        """
        try:
            for p, pid in cls._iter_listening_tcp():
                if p == int(port):
                    return pid
        except Exception as e:
            print(f"[MayaConnection] Failed to resolve PID from port {port}: {e}")

        return None

    @classmethod
    def get_port_from_pid(
        cls, pid: int, start_port: Optional[int] = None, span: Optional[int] = None
    ) -> Optional[int]:
        """Find a TCP port the given PID is LISTENING on (inverse of
        :meth:`get_pid_from_port`). Used to discover which port a launched
        Maya actually bound after its startup MEL self-healed a collision.

        Parameters:
            pid: Process ID to look up.
            start_port: If given, only ports in ``[start_port, start_port +
                span)`` are considered.
            span: Window width (defaults to :attr:`PORT_SCAN_SPAN`).

        Returns:
            The lowest matching port, or None.
        """
        try:
            rows = cls._iter_listening_tcp()
        except Exception as e:
            print(f"[MayaConnection] Failed to resolve port from PID {pid}: {e}")
            return None

        matches = [p for p, owner in rows if owner == int(pid)]
        if start_port is not None:
            end = start_port + (span or cls.PORT_SCAN_SPAN)
            matches = [p for p in matches if start_port <= p < end]
        return min(matches) if matches else None

    @staticmethod
    def close_instance(
        port: Optional[int] = None, pid: Optional[int] = None, force: bool = False
    ) -> bool:
        """
        Close a Maya instance identified by Port or PID.

        Args:
            port: The command port number.
            pid: The process ID.
            force: If True, force closes the application without prompting to save.
        """
        from pythontk import AppLauncher

        if port and not pid:
            pid = MayaConnection.get_pid_from_port(port)
            if not pid:
                print(f"[MayaConnection] No process found listening on port {port}.")
                # Fallback: Try to find by Window Title if locally launched
                for proc_pid in AppLauncher.get_running_processes("maya"):
                    titles = AppLauncher.get_window_titles(proc_pid)
                    if any(f"Port: {port}" in t for t in titles):
                        print(
                            f"[MayaConnection] Found PID {proc_pid} via Window Title."
                        )
                        pid = proc_pid
                        break

        if pid:
            print(
                f"[MayaConnection] Closing Maya instance (PID: {pid}{', Forced' if force else ''})..."
            )
            return AppLauncher.close_process(pid, force=force)

        return False

    @classmethod
    def get_available_port(cls, start_port: int = 7002, max_check: int = 100) -> int:
        """
        Find an available port starting from start_port.
        Checks both the TCP port and the potential named pipe 'mayatk_{port}'.
        Useful when you want to launch a new Maya instance without conflicting with existing ones.
        """
        import sys
        import os

        for port in range(start_port, start_port + max_check):
            # 1. TCP port — bind-probe, not connect-probe (see
            #    _tcp_port_bindable for why).
            if not cls._tcp_port_bindable(port):
                continue

            # 2. Check Named Pipe (Windows)
            # Maya creates named pipes as \\.\pipe\name on Windows
            # NOTE: os.path.exists on pipes is unreliable for Maya command ports (often returns False even if used).
            # We rely primarily on TCP check. Detection of 'mayatk_{port}' is kept as best-effort.
            pipe_name = f"mayatk_{port}"
            is_pipe_free = True
            if sys.platform == "win32":
                if os.path.exists(f"\\\\.\\pipe\\{pipe_name}"):
                    is_pipe_free = False

            # If both are free (or pipe matching failed to find it), return this port
            if is_pipe_free:
                return port

        raise RuntimeError(
            f"No available ports found in range {start_port}-{start_port + max_check}"
        )

    def _detect_mode(self) -> ConnectionMode:
        """Auto-detect the best connection mode."""
        # 1. Check if we are in Maya GUI (Interactive)
        # We skip this if in batch mode (mayapy) so we can act as a runner (port)
        try:
            import maya.cmds

            if hasattr(maya.cmds, "about") and not maya.cmds.about(batch=True):
                self.mode = "interactive"
                self.is_connected = True
                return "interactive"
        except (ImportError, AttributeError, RuntimeError):
            pass

        # 2. Try command port (Runner for mayapy or external python)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", 7002))
            sock.close()
            if result == 0:
                return "port"
        except Exception:
            pass

        # 3. Fall back to standalone (mayapy / batch execution)
        return "standalone"

    def _connect_via_port(self, host: str, port: int) -> bool:
        """Connect to Maya via command port."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, port))
            sock.close()
            self.mode = "port"
            self.is_connected = True
            self.host = host
            self.port = int(port)
            print(f"[OK] Connected to Maya via command port {host}:{port}")
            return True
        except Exception as e:
            print(f"[ERROR] Could not connect to Maya command port: {e}")
            return False

    def _port_alive(self) -> bool:
        """Return True if the current command port is reachable."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((self.host, int(self.port)))
            sock.close()
            return True
        except Exception:
            return False

    def ensure_connection(
        self,
        launch: bool = True,
        app_path: Optional[str] = None,
        launch_args: Optional[List[str]] = None,
    ) -> bool:
        """Verify the port is reachable; relaunch Maya if it is not.

        Parameters:
            launch: Attempt to launch a new Maya instance if the port
                is down.  Default ``True``.
            app_path: Optional path to Maya executable.
            launch_args: Extra CLI args forwarded to Maya.

        Returns:
            bool: ``True`` if connection is alive after the call.
        """
        if self.mode != "port":
            return self.is_connected

        if self._port_alive():
            return True

        # Port is down — mark disconnected
        self.is_connected = False
        print(f"[MayaConnection] Port {self.port} unreachable.")

        if not launch:
            return False

        # Close the stale instance so the relaunch is clean — but ONLY the
        # Maya THIS connection launched. Session safety hard rule: never
        # kill Maya processes we did not launch (the previous
        # `taskkill /F /IM maya.exe` nuked the user's interactive session
        # along with the stale one). Guard against PID reuse too: if our
        # Maya died long ago, Windows may have recycled the PID onto an
        # unrelated process — kill only if it is still a running Maya.
        if self._launched_pid:
            import time

            from pythontk import AppLauncher

            try:
                still_maya = self._launched_pid in AppLauncher.get_running_processes(
                    "maya"
                )
            except Exception:
                still_maya = False
            if still_maya:
                try:
                    AppLauncher.close_process(self._launched_pid, force=True)
                    time.sleep(2)
                except Exception:
                    pass
            self._launched_pid = None

        # Re-probe: the dead port may be squatted (zombie / stranger), in
        # which case relaunching on it guarantees another failure.
        launch_port = self.get_available_port(start_port=self.port)
        print(f"[MayaConnection] Relaunching Maya on port {launch_port}...")
        actual = self._launch_maya_gui(launch_port, app_path, extra_args=launch_args)
        if actual:
            return self._connect_via_port(self.host, actual)
        return False

    def _connect_standalone(self) -> bool:
        """Initialize Maya in standalone mode."""
        try:
            import maya.standalone

            maya.standalone.initialize(name="python")

            # Initialize QApplication for UI tests
            try:
                from qtpy import QtWidgets

                instance = QtWidgets.QApplication.instance()
                if not instance:
                    print("Initializing QApplication...", flush=True)
                    # Keep reference to avoid garbage collection
                    self._qapp = QtWidgets.QApplication([])
                else:
                    print(f"QApplication already exists: {instance}", flush=True)
            except ImportError as e:
                print(f"Could not import qtpy: {e}", flush=True)
            except Exception as e:
                print(f"Error initializing QApplication: {e}", flush=True)

            self.mode = "standalone"
            self.is_connected = True
            print("[OK] Maya standalone initialized", flush=True)
            return True
        except Exception as e:
            print(f"[ERROR] Could not initialize Maya standalone: {e}")
            return False

    def _connect_interactive(self) -> bool:
        """Verify we're in an interactive Maya session."""
        try:
            import maya.cmds

            maya.cmds.about(version=True)
            self.mode = "interactive"
            self.is_connected = True
            print("[OK] Running in interactive Maya session")
            return True
        except Exception as e:
            print(f"[ERROR] Not in interactive Maya session: {e}")
            return False

    def execute(
        self,
        code: str,
        timeout: int = 30,
        capture_output: bool = False,
        wait_for_response: bool = False,
        output_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Execute Python code in Maya.

        Parameters:
            code: Python code to execute
            timeout: Timeout in seconds (for port mode)
            capture_output: Whether to capture stdout/stderr and return it
            wait_for_response: Whether to wait for and return the result of the last expression (if capture_output is False)
            output_callback: Optional function to call with the captured output

        Returns:
            Output from execution (if capture_output is True) or result of last expression (if wait_for_response is True) or None
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to Maya. Call connect() first.")

        if self.mode == "port":
            if capture_output:
                # Step 1: Execute the code (which stores output in _mayatk_last_captured_output)
                wrapped_code = self._wrap_capture_code(code)
                # We wait for response to ensure execution is complete before retrieving output
                self._execute_via_port(wrapped_code, timeout, wait_for_response=True)

                # Step 2: Retrieve the output (from __main__ namespace)
                result = self._execute_via_port(
                    "__import__('__main__')._mayatk_last_captured_output",
                    timeout,
                    wait_for_response=True,
                )
            else:
                result = self._execute_via_port(
                    code, timeout, wait_for_response=wait_for_response
                )

        elif self.mode in ("standalone", "interactive"):
            if capture_output:
                import io

                capture = io.StringIO()
                old_stdout = sys.stdout
                old_stderr = sys.stderr
                sys.stdout = capture
                sys.stderr = capture
                try:
                    exec(code, globals())
                except Exception:
                    import traceback

                    traceback.print_exc()
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr
                result = capture.getvalue()
            else:
                if wait_for_response:
                    try:
                        # Try to eval first (if it's an expression)
                        result = eval(code, globals())
                    except SyntaxError:
                        # If it's a statement/script, exec it
                        exec(code, globals())
                        result = None
                else:
                    exec(code, globals())
                    result = None

        if output_callback and result:
            output_callback(result)

        return result

    def get_script_editor_output(
        self, last_n_chars: Optional[int] = None
    ) -> Optional[str]:
        """
        Get the full content of the Maya Script Editor history.

        Parameters:
            last_n_chars: If specified, only return the last N characters of output.
                         Useful to avoid returning massive amounts of text.

        Returns:
            String containing the script editor text, or None if failed.
        """
        if self.mode in ("interactive", "standalone"):
            text = self._get_script_editor_text()
            if last_n_chars and text and len(text) > last_n_chars:
                return text[-last_n_chars:]
            return text

        # Port mode: Use cmdScrollFieldReporter directly (simpler, no MEL eval needed)
        code = f"""
import maya.cmds as cmds
global _mayatk_temp_result
_mayatk_temp_result = ""
if cmds.control("cmdScrollFieldReporter1", exists=True):
    text = cmds.cmdScrollFieldReporter("cmdScrollFieldReporter1", query=True, text=True)
    if text:
        _mayatk_temp_result = text{f"[-{last_n_chars}:]" if last_n_chars else ""}
"""
        self.execute(code, wait_for_response=True)
        return self.execute("_mayatk_temp_result", wait_for_response=True)

    def execute_and_capture_editor_output(
        self, code: str, timeout: int = 30, mirror_to_script_output: bool = False
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Execute code and capture the Script Editor output generated by the execution.

        This is different from capture_output which captures stdout/stderr.
        This captures warnings, errors, and info messages that Maya logs to
        the Script Editor (like color space warnings on scene load).

        Parameters:
            code: Python code to execute
            timeout: Timeout in seconds

        Returns:
            Tuple of (execution_result, editor_output_generated)
            - execution_result: Return value from the code if wait_for_response semantics
            - editor_output_generated: The Script Editor text that was added during execution
        """
        # Get current Script Editor length to know where new output starts
        len_code = """
import maya.cmds as cmds
global _mayatk_editor_start_len
_mayatk_editor_start_len = 0
if cmds.control("cmdScrollFieldReporter1", exists=True):
    text = cmds.cmdScrollFieldReporter("cmdScrollFieldReporter1", query=True, text=True)
    _mayatk_editor_start_len = len(text) if text else 0
"""
        self.execute(len_code, wait_for_response=True)

        # Execute the user's code
        result = self.execute(code, timeout=timeout, wait_for_response=True)

        # Get the new output (everything after start_len)
        get_new_code = """
import maya.cmds as cmds
global _mayatk_new_editor_output
_mayatk_new_editor_output = ""
if cmds.control("cmdScrollFieldReporter1", exists=True):
    text = cmds.cmdScrollFieldReporter("cmdScrollFieldReporter1", query=True, text=True)
    if text and len(text) > _mayatk_editor_start_len:
        _mayatk_new_editor_output = text[_mayatk_editor_start_len:]
"""
        self.execute(get_new_code, wait_for_response=True)
        new_output = self.execute("_mayatk_new_editor_output", wait_for_response=True)

        if mirror_to_script_output and new_output:
            try:
                from mayatk.env_utils.script_output import ScriptConsole
                from qtpy import QtGui

                if not ScriptConsole._instance:
                    ScriptConsole.show_console()

                output_widget = ScriptConsole._instance.output
                cursor = output_widget.textCursor()
                cursor.movePosition(QtGui.QTextCursor.End)
                cursor.insertText(new_output)
                output_widget.setTextCursor(cursor)
            except Exception:
                pass

        return result, new_output

    def clear_script_editor(self) -> bool:
        """
        Clear the Maya Script Editor history.

        Returns:
            True if successful.
        """
        if self.mode in ("interactive", "standalone"):
            return self._clear_script_editor_text()

        # Port mode: Use cmdScrollFieldReporter directly
        code = """
import maya.cmds as cmds
global _mayatk_temp_success
_mayatk_temp_success = False
if cmds.control("cmdScrollFieldReporter1", exists=True):
    try:
        cmds.cmdScrollFieldReporter("cmdScrollFieldReporter1", edit=True, clear=True)
        _mayatk_temp_success = True
    except Exception:
        pass
"""
        self.execute(code, wait_for_response=True)
        result = self.execute("_mayatk_temp_success", wait_for_response=True)
        return str(result).strip().lower() == "true"

    def _wrap_capture_code(self, code: str) -> str:
        """Wrap code to capture stdout/stderr and return it as a string."""
        encoded_code = base64.b64encode(code.encode("utf-8")).decode("utf-8")
        return f"""
import sys
import base64
import traceback

# Use a unique name for the buffer to avoid conflicts
_mayatk_output_buffer = []

class _MayatkCapturer:
    def __init__(self, buffer):
        self.buffer = buffer
    def write(self, text):
        self.buffer.append(text)
    def flush(self):
        pass

_mayatk_capturer = _MayatkCapturer(_mayatk_output_buffer)
_mayatk_original_stdout = sys.stdout
_mayatk_original_stderr = sys.stderr
sys.stdout = _mayatk_capturer
sys.stderr = _mayatk_capturer

try:
    _mayatk_code = base64.b64decode("{encoded_code}").decode('utf-8')
    exec(_mayatk_code, globals())
except Exception:
    traceback.print_exc()
finally:
    sys.stdout = _mayatk_original_stdout
    sys.stderr = _mayatk_original_stderr

# Store in __main__ so the value persists across command-port connections
import __main__ as _mayatk_main_mod
_mayatk_main_mod._mayatk_last_captured_output = "".join(_mayatk_output_buffer)
"""

    def _execute_via_port(
        self, code: str, timeout: int, wait_for_response: bool = False
    ) -> Optional[str]:
        """Execute code via command port."""
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(timeout)
            host = getattr(self, "host", "localhost")
            port = int(getattr(self, "port", 7002))
            client.connect((host, port))
            client.sendall(code.encode("utf-8"))

            response = None
            if wait_for_response:
                response_bytes = b""
                while True:
                    try:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        response_bytes += chunk
                        # Maya's command port terminates each response with \x00
                        if b"\x00" in chunk:
                            break
                    except socket.timeout:
                        break
                response = response_bytes.decode("utf-8").strip()
                response = response.replace("\x00", "")

            client.close()
            return response
        except Exception as e:
            print(f"Error executing code: {e}")
            return None

    # ---- context manager --------------------------------------------------

    def __enter__(self) -> "MayaConnection":
        """Enter a managed session.

        If not already connected, ``connect()`` is called with the
        default arguments (which launches a new Maya instance).

        Returns:
            self
        """
        if not self.is_connected:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the managed session.

        * **port** mode — shuts down the Maya process that was launched
          (via :meth:`close_instance`) and resets connection state.
        * **standalone** mode — calls ``maya.standalone.uninitialize()``.
        * **interactive** mode — resets state only (never kills the
          user's Maya).

        Exceptions are not suppressed.
        """
        self.shutdown(force=True)
        return False  # do not suppress exceptions

    def shutdown(self, force: bool = False) -> None:
        """Shut down the connected Maya session and reset state.

        For *port* mode this kills the Maya process.  For *standalone*
        mode it uninitialises the embedded interpreter.  *Interactive*
        mode only resets the connection flag.

        Args:
            force: If True, force closes the application without prompting to save.
        """
        if not self.is_connected:
            return

        mode = self.mode

        if mode == "port":
            try:
                self.close_instance(port=self.port, force=force)
            except Exception as e:
                print(f"[MayaConnection] Error closing Maya instance: {e}")
            # The instance is gone (or unfindable) — drop the PID so a later
            # ensure_connection can't act on a recycled PID.
            self._launched_pid = None

        elif mode == "standalone":
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except Exception:
                pass

        # Interactive mode: never kill the user's Maya

        self.is_connected = False
        self.mode = None
        print("[OK] Maya session closed")

    def disconnect(self):
        """Disconnect from Maya.

        .. deprecated::
            Use :meth:`shutdown` or the context-manager protocol instead.
            ``disconnect`` resets connection state but does **not** close
            the Maya process in port mode.
        """
        if self.mode == "standalone":
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except Exception:
                pass

        self.is_connected = False
        self.mode = None
        print("[OK] Disconnected from Maya")


# The port helpers are the canonical ``MayaConnection.open_command_ports`` /
# ``toggle_command_ports`` / ``open_available_command_ports`` staticmethods
# defined in the class body above. No module-level aliases or self-named
# in-class wrappers — those recurse (a wrapper calling the same qualified
# name it is bound to). Consumers call ``MayaConnection.<name>()`` directly.


if __name__ == "__main__":
    MayaConnection.reload_modules(["mayatk"], include_submodules=True, verbose=True)
    # Example usage
    conn = MayaConnection.get_instance()
    if conn.connect(mode="auto"):
        output = conn.execute('print("Hello from Maya!")', capture_output=True)
        print("Maya Output:", output)
        conn.disconnect()
