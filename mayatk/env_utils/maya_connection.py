# !/usr/bin/python
# coding=utf-8
"""
Maya Connection Module

Provides utilities to connect to Maya either via command port or standalone mode.
Supports both interactive Maya sessions and batch testing.
"""
import socket
import sys
import os
import textwrap
import base64
import inspect
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
                print(f"Warning: Could not open {source_type} port {port}")

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
        self, mode: ConnectionMode = "auto", port: int = 7002, host: str = "localhost"
    ) -> bool:
        """
        Connect to Maya using the specified mode.

        Parameters:
            mode: Connection mode - "port", "standalone", "interactive", or "auto"
            port: Port number for command port connection (default: 7002)
            host: Hostname for command port connection (default: "localhost")

        Returns:
            bool: True if connection successful
        """
        if mode == "auto":
            mode = self._detect_mode()

        if mode == "port":
            return self._connect_via_port(host, port)
        elif mode == "standalone":
            return self._connect_standalone()
        elif mode == "interactive":
            return self._connect_interactive()
        else:
            raise ValueError(f"Invalid connection mode: {mode}")

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

                # Step 2: Retrieve the output
                result = self._execute_via_port(
                    "_mayatk_last_captured_output", timeout, wait_for_response=True
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
        self, code: str, timeout: int = 30
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

# Store the result in a global variable to be retrieved by a subsequent command
global _mayatk_last_captured_output
_mayatk_last_captured_output = "".join(_mayatk_output_buffer)
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
                    except socket.timeout:
                        break
                # Maya's command port returns the result of the last expression.
                # Our wrapped code ends with `_capturer.getvalue()`, so that's what we get.
                # However, the raw response might contain null bytes or other artifacts.
                response = response_bytes.decode("utf-8").strip()
                # Remove null bytes if any
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
