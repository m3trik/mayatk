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
from typing import Optional, Literal

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

    def __init__(self):
        self.mode: Optional[self.ConnectionMode] = None
        self.is_connected = False

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
        # Check if already in Maya
        try:
            import maya.cmds

            # Verify we can actually call commands (initialized)
            maya.cmds.about(version=True)

            self.mode = "interactive"
            self.is_connected = True
            return "interactive"
        except (ImportError, AttributeError, RuntimeError):
            pass

        # Try command port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", 7002))
            sock.close()
            if result == 0:
                return "port"
        except:
            pass

        # Fall back to standalone
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
            print(f"✓ Connected to Maya via command port {host}:{port}")
            return True
        except Exception as e:
            print(f"✗ Could not connect to Maya command port: {e}")
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
            print("✓ Maya standalone initialized", flush=True)
            return True
        except Exception as e:
            print(f"✗ Could not initialize Maya standalone: {e}")
            return False

    def _connect_interactive(self) -> bool:
        """Verify we're in an interactive Maya session."""
        try:
            import maya.cmds

            maya.cmds.about(version=True)
            self.mode = "interactive"
            self.is_connected = True
            print("✓ Running in interactive Maya session")
            return True
        except Exception as e:
            print(f"✗ Not in interactive Maya session: {e}")
            return False

    def execute(self, code: str, timeout: int = 30) -> Optional[str]:
        """
        Execute Python code in Maya.

        Parameters:
            code: Python code to execute
            timeout: Timeout in seconds (for port mode)

        Returns:
            Output from execution (port mode) or None
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to Maya. Call connect() first.")

        if self.mode == "port":
            return self._execute_via_port(code, timeout)
        elif self.mode in ("standalone", "interactive"):
            exec(code)
            return None

    def _execute_via_port(self, code: str, timeout: int) -> Optional[str]:
        """Execute code via command port."""
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(timeout)
            client.connect(("localhost", 7002))
            client.sendall(code.encode("utf-8"))
            client.close()
            return None  # Output goes to Maya's script editor
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
        print("✓ Disconnected from Maya")


# Singleton instance
_connection = None


def get_connection() -> MayaConnection:
    """Get the global Maya connection instance."""
    global _connection
    if _connection is None:
        _connection = MayaConnection()
    return _connection


def ensure_maya_connection(mode: str = "auto") -> MayaConnection:
    """
    Ensure Maya is connected and return the connection instance.

    Parameters:
        mode: Connection mode - "port", "standalone", "interactive", or "auto"

    Returns:
        MayaConnection instance
    """
    conn = get_connection()
    if not conn.is_connected:
        conn.connect(mode=mode)
    return conn


# Convenience functions
def connect_maya(mode: str = "auto", port: int = 7002) -> bool:
    """Connect to Maya. Returns True if successful."""
    return get_connection().connect(mode=mode, port=port)


def execute_in_maya(code: str) -> Optional[str]:
    """Execute Python code in Maya."""
    return get_connection().execute(code)


def disconnect_maya():
    """Disconnect from Maya."""
    get_connection().disconnect()


if __name__ == "__main__":
    # Test connection
    conn = get_connection()
    if conn.connect():
        print(f"Connected in {conn.mode} mode")

        # Test execution
        test_code = """
import pymel.core as pm
print(f"Maya version: {pm.about(version=True)}")
"""
        conn.execute(test_code)
        conn.disconnect()
