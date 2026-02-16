# !/usr/bin/python
# coding=utf-8
"""
Maya Connection Module

Provides utilities to connect to Maya either via command port or standalone mode.
Supports both interactive Maya sessions and batch testing.
"""
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

        for source_type, port in kwargs.items():
            try:  # close existing open port.
                cmds.commandPort(name=port, close=True)
            except RuntimeError:
                pass

            try:  # open new port.
                cmds.commandPort(name=port, sourceType=source_type)
            except RuntimeError:
                # Port likely in use by another instance. Valid behavior, suppress warning.
                pass

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

    def connect(
        self,
        mode: ConnectionMode = "auto",
        port: int = 7002,
        host: str = "localhost",
        launch: bool = False,
        app_path: Optional[str] = None,
        force_new_instance: bool = False,
        launch_args: Optional[List[str]] = None,
    ) -> bool:
        """
        Connect to Maya using the specified mode.

        Parameters:
            mode: Connection mode - "port", "standalone", "interactive", or "auto"
            port: Port number for command port connection (default: 7002).
                  If force_new_instance is True, this acts as the starting port to scan from.
            host: Hostname for command port connection (default: "localhost")
            launch: If True, attempts to launch Maya GUI with the command port open if connection fails.
            app_path: Optional path to the Maya executable to use when launching.
            force_new_instance: If True, finds an available port and launches a new Maya instance regardless of existing ones.
            launch_args: Optional list of additional arguments to pass to Maya when launching (e.g. ['-noAutoloadPlugins']).

        Returns:
            bool: True if connection successful
        """
        if force_new_instance:
            port = self.get_available_port(start_port=port)
            launch = True
            # Build connection string for the log
            print(
                f"[MayaConnection] Force new instance requested. Selected available port: {port}"
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
                print(
                    f"[MayaConnection] Connection failed. Launching Maya on port {port}..."
                )
                if self._launch_maya_gui(port, app_path, extra_args=launch_args):
                    return self._connect_via_port(host, port)
            return connected
        elif mode == "standalone":
            return self._connect_standalone()
        elif mode == "interactive":
            return self._connect_interactive()
        else:
            raise ValueError(f"Invalid connection mode: {mode}")

    def _launch_maya_gui(
        self,
        port: int,
        app_path: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
    ) -> bool:
        """Launch Maya GUI with command port enabled."""
        from pythontk import AppLauncher
        import time

        # Command to open port on startup and configure UI
        # 1. Open TCP port for external connection (check if not already open to avoid "Name in use" error)
        # 2. Open unique named pipe (mayatk_PORT). Use catch to ignore OS-level pipe collisions (zombie pipes).
        # 3. Update Window Title to identify this instance
        # Note: Using braces in MEL statements to ensure valid syntax for nested ifs.
        startup_cmds = [
            f'if (!`commandPort -q -name ":{port}"`) commandPort -name ":{port}" -sourceType "python"',
            f'if (!`commandPort -q -name "mayatk_{port}"`) {{ if (catch(`commandPort -name "mayatk_{port}" -sourceType "python"`)) {{ }} }}',
            f'window -e -title "Maya [Port: {port}]" $gMainWindow',
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
            return False

        print(
            f"[MayaConnection] Maya launched (PID: {process.pid}). Waiting for Command Port {port} to open..."
        )

        def check_port_open(proc):
            """Callback to check if command port is listening."""
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                # socket.connect_ex returns 0 on success
                result = sock.connect_ex(("localhost", port))
                sock.close()
                return result == 0
            except:
                return False

        # Wait for port to be actually listening
        # Maya takes a while to load (UI + UserSetup), giving it 3 minutes
        if AppLauncher.wait_for_ready(process, timeout=180, check_fn=check_port_open):
            print("[MayaConnection] Maya Command Port is ready.")
            return True

        # If we got here, we timed out or process died
        if process.poll() is not None:
            print(
                f"[MayaConnection] Maya process exited prematurely with code {process.returncode}."
            )
        else:
            print("[MayaConnection] Timeout waiting for Maya Command Port.")

        return False

    @staticmethod
    def get_pid_from_port(port: int) -> Optional[int]:
        """
        Find the process ID (PID) listening on the given TCP port.
        Works on Windows using netstat.
        """
        import subprocess
        import re

        try:
            # Run netstat -ano to get all connections and PIDs
            output = subprocess.check_output(
                ["netstat", "-ano"], universal_newlines=True
            )
            # Look for: TCP    0.0.0.0:PORT    ...    LISTENING    PID
            # Use strict regex to avoid partial matches (e.g. 7002 matches 70021)
            # Regex: TCP \s+ IP:PORT \s+ ... \s+ PID
            pattern = re.compile(
                r"TCP\s+(?:\d{1,3}\.){3}\d{1,3}:" + str(port) + r"\s+.*\s+(\d+)\s*$"
            )

            for line in output.splitlines():
                if f":{port}" in line:
                    match = pattern.search(line.strip())
                    if match:
                        return int(match.group(1))
        except Exception as e:
            print(f"[MayaConnection] Failed to resolve PID from port {port}: {e}")

        return None

    @staticmethod
    def close_instance(port: Optional[int] = None, pid: Optional[int] = None) -> bool:
        """
        Close a Maya instance identified by Port or PID.

        Args:
            port: The command port number.
            pid: The process ID.
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
            print(f"[MayaConnection] Closing Maya instance (PID: {pid})...")
            return AppLauncher.close_process(pid)

        return False

    @staticmethod
    def get_available_port(start_port: int = 7002, max_check: int = 100) -> int:
        """
        Find an available port starting from start_port.
        Checks both the TCP port and the potential named pipe 'mayatk_{port}'.
        Useful when you want to launch a new Maya instance without conflicting with existing ones.
        """
        import sys
        import os

        for port in range(start_port, start_port + max_check):
            # 1. Check TCP Port
            is_tcp_free = False
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # If we successfully connect (0), the port is taken.
                # If we fail (non-zero), the port is likely free.
                if s.connect_ex(("localhost", port)) != 0:
                    is_tcp_free = True
                s.close()
            except:
                pass

            if not is_tcp_free:
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
        except:
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

        # Kill stale Maya process if one exists so we get a clean launch
        import subprocess

        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "maya.exe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            import time

            time.sleep(2)
        except Exception:
            pass

        print(f"[MayaConnection] Relaunching Maya on port {self.port}...")
        if self._launch_maya_gui(self.port, app_path, extra_args=launch_args):
            return self._connect_via_port(self.host, self.port)
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

    def disconnect(self):
        """Disconnect from Maya."""
        if self.mode == "standalone":
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except:
                pass

        self.is_connected = False
        self.mode = None
        print("[OK] Disconnected from Maya")


# Module-level aliases for backward compatibility and ease of use
def open_command_ports(**kwargs):
    """Wrapper for MayaConnection.open_command_ports."""
    MayaConnection.open_command_ports(**kwargs)


if __name__ == "__main__":

    MayaConnection.reload_modules(["mayatk"], include_submodules=True, verbose=True)
    # Example usage
    conn = MayaConnection.get_instance()
    if conn.connect(mode="auto"):
        output = conn.execute('print("Hello from Maya!")', capture_output=True)
        print("Maya Output:", output)
        conn.disconnect()
