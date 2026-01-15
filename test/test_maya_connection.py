# !/usr/bin/python
# coding=utf-8
"""
Tests for Maya Connection Module

Verifies the functionality of mayatk.env_utils.maya_connection
"""
import unittest
import sys
import os
from unittest.mock import MagicMock, patch

from base_test import MayaTkTestCase
from mayatk.env_utils.maya_connection import MayaConnection

try:
    import maya.cmds

    MAYA_AVAILABLE = True
except ImportError:
    MAYA_AVAILABLE = False


class TestMayaConnection(MayaTkTestCase):
    """Test cases for MayaConnection class."""

    def setUp(self):
        super().setUp()
        # Reset singleton for testing
        MayaConnection._instance = None

    def test_initialization(self):
        """Test MayaConnection initialization."""
        conn = MayaConnection()
        self.assertIsNone(conn.mode)
        self.assertFalse(conn.is_connected)

    @unittest.skipUnless(MAYA_AVAILABLE, "Maya not available")
    def test_detect_mode_interactive(self):
        """Test mode detection inside Maya."""
        conn = MayaConnection()
        mode = conn._detect_mode()
        self.assertEqual(mode, "interactive")
        self.assertEqual(conn.mode, "interactive")
        self.assertTrue(conn.is_connected)

    @unittest.skipUnless(MAYA_AVAILABLE, "Maya not available")
    def test_connect_interactive(self):
        """Test explicit interactive connection."""
        conn = MayaConnection()
        result = conn.connect(mode="interactive")
        self.assertTrue(result)
        self.assertEqual(conn.mode, "interactive")
        self.assertTrue(conn.is_connected)

    def test_singleton_access(self):
        """Test get_instance singleton behavior."""
        conn1 = MayaConnection.get_instance()
        conn2 = MayaConnection.get_instance()
        self.assertIs(conn1, conn2)
        # Check class name to avoid reload-induced type mismatch
        self.assertEqual(conn1.__class__.__name__, "MayaConnection")

    @unittest.skipUnless(MAYA_AVAILABLE, "Maya not available")
    def test_ensure_connection(self):
        """Test ensure connection logic."""
        conn = MayaConnection.get_instance()
        if not conn.is_connected:
            conn.connect(mode="auto")
        self.assertTrue(conn.is_connected)
        self.assertEqual(conn.mode, "interactive")

    def test_reload_modules(self):
        """Test module reloading functionality."""
        # We'll reload the module itself as a test
        mod_name = "mayatk.env_utils.maya_connection"

        # Capture stdout to verify output
        from io import StringIO

        captured_output = StringIO()
        sys.stdout = captured_output

        try:
            reloaded = MayaConnection.reload_modules(mod_name, verbose=True)
            sys.stdout = sys.__stdout__

            # Verify the module was in the reloaded list
            self.assertIn(mod_name, reloaded)

            # Verify output
            output = captured_output.getvalue()
            self.assertIn("[ModuleReloader]", output)

        except Exception as e:
            sys.stdout = sys.__stdout__
            self.fail(f"reload_modules raised exception: {e}")

    def test_reload_modules_list(self):
        """Test reloading a list of modules."""
        modules = ["mayatk.env_utils.maya_connection", "mayatk.core_utils.core_utils"]
        reloaded = MayaConnection.reload_modules(modules, verbose=False)

        # Check if at least the connection module is in the list
        self.assertIn("mayatk.env_utils.maya_connection", reloaded)

    def test_reload_nonexistent_module(self):
        """Test reloading a non-existent module."""
        mod_name = "mayatk.non_existent_module_xyz"

        # Should not raise exception
        reloaded = MayaConnection.reload_modules(mod_name, verbose=False)
        self.assertNotIn(mod_name, reloaded)

    @unittest.skipUnless(MAYA_AVAILABLE, "Maya not available")
    def test_script_editor_output(self):
        """Test getting and clearing script editor output."""
        import maya.cmds as cmds

        if cmds.about(batch=True):
            print("Skipping script editor test in batch mode")
            return

        conn = MayaConnection()
        conn.connect(mode="interactive")

        # Ensure script editor is open
        import maya.mel as mel
        import maya.cmds as cmds

        if not cmds.control("cmdScrollFieldReporter1", exists=True):
            mel.eval("ScriptEditor;")

        # Clear first
        conn.clear_script_editor()

        # Generate some output
        cmds.warning("Test Warning 123")
        import maya.utils

        maya.utils.processIdleEvents()

        # Get output
        output = conn.get_script_editor_output()
        self.assertIsNotNone(output)
        self.assertIn("Test Warning 123", output)

        # Clear again
        result = conn.clear_script_editor()
        self.assertTrue(result)

        # Verify cleared
        output_after = conn.get_script_editor_output()
        if output_after:
            self.assertNotIn("Test Warning 123", output_after)


class TestMayaConnectionMocked(unittest.TestCase):
    """Test cases for MayaConnection using mocks (for non-interactive paths)."""

    @patch("socket.socket")
    def test_connect_port_success(self, mock_socket_cls):
        """Test successful port connection."""
        # Setup mock socket
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket

        # Create instance directly to avoid singleton/reload issues
        conn = MayaConnection()

        # Mock connect to succeed (no exception)
        result = conn.connect(mode="port", port=12345)

        self.assertTrue(result)
        self.assertEqual(conn.mode, "port")
        self.assertTrue(conn.is_connected)
        mock_socket.connect.assert_called_with(("localhost", 12345))

    @patch("socket.socket")
    def test_connect_port_failure(self, mock_socket_cls):
        """Test failed port connection."""
        # Setup mock socket to raise exception
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket
        mock_socket.connect.side_effect = ConnectionRefusedError("Connection refused")

        conn = MayaConnection()
        result = conn.connect(mode="port")

        self.assertFalse(result)
        self.assertFalse(conn.is_connected)
        self.assertIsNone(conn.mode)

    def test_execute_port(self):
        """Test execute in port mode."""
        conn = MayaConnection()
        conn.mode = "port"
        conn.is_connected = True

        # Patch the method on the instance directly to avoid class mismatch
        with patch.object(conn, "_execute_via_port") as mock_execute:
            conn.execute("print('hello')")
            mock_execute.assert_called_with(
                "print('hello')", 30, wait_for_response=False
            )

    def test_execute_not_connected(self):
        """Test execute raises error when not connected."""
        conn = MayaConnection()
        with self.assertRaises(RuntimeError):
            conn.execute("print('hello')")

    def test_connect_standalone(self):
        """Test standalone connection."""
        # Mock maya.standalone module if it doesn't exist
        if "maya.standalone" not in sys.modules:
            mock_maya = MagicMock()
            mock_standalone = MagicMock()
            mock_maya.standalone = mock_standalone
            with patch.dict(
                sys.modules, {"maya": mock_maya, "maya.standalone": mock_standalone}
            ):
                self._run_connect_standalone_test()
        else:
            self._run_connect_standalone_test()

    def _run_connect_standalone_test(self):
        conn = MayaConnection()
        with patch("maya.standalone.initialize") as mock_initialize:
            result = conn.connect(mode="standalone")

            self.assertTrue(result)
            self.assertEqual(conn.mode, "standalone")
            self.assertTrue(conn.is_connected)
            mock_initialize.assert_called_with(name="python")

    def test_connect_launch_flag(self):
        """Test that launch=True triggers launch logic when connection fails."""
        conn = MayaConnection()

        with (
            patch.object(MayaConnection, "_detect_mode") as mock_detect_mode,
            patch.object(MayaConnection, "_connect_via_port") as mock_connect_port,
            patch.object(MayaConnection, "_launch_maya_gui") as mock_launch_gui,
        ):

            # Scenario: Auto detect sees nothing (returns standalone as fallback)
            mock_detect_mode.return_value = "standalone"

            # Connect logic:
            # 1. detect -> standalone
            # 2. if launch=True and standalone -> mode forced to "port"
            # 3. _connect_via_port calls:
            #    a. First call fails (Maya not running)
            #    b. Launch called
            #    c. Second call succeeds
            mock_connect_port.side_effect = [False, True]
            mock_launch_gui.return_value = True

            result = conn.connect(mode="auto", launch=True)

            self.assertTrue(result)

            # Verify launch was called
            mock_launch_gui.assert_called_once()

            # Verify connect called twice (fail -> launch -> succeed)
            self.assertEqual(mock_connect_port.call_count, 2)

    @patch("subprocess.check_output")
    def test_get_pid_from_port_parses_netstat(self, mock_check_output):
        """Test parsing netstat output for PID resolution."""
        mock_check_output.return_value = (
            "  TCP    0.0.0.0:7003   0.0.0.0:0   LISTENING   1234\n"
            "  TCP    0.0.0.0:70031  0.0.0.0:0   LISTENING   9999\n"
            "  TCP    127.0.0.1:80   0.0.0.0:0   LISTENING   5678\n"
        )

        pid = MayaConnection.get_pid_from_port(7003)
        self.assertEqual(pid, 1234)

    @patch("pythontk.AppLauncher.close_process")
    @patch("mayatk.env_utils.maya_connection.MayaConnection.get_pid_from_port")
    def test_close_instance_by_port(self, mock_get_pid, mock_close_process):
        """Test closing a Maya instance by port."""
        mock_get_pid.return_value = 4321
        mock_close_process.return_value = True

        result = MayaConnection.close_instance(port=7003)
        self.assertTrue(result)
        mock_get_pid.assert_called_with(7003)
        mock_close_process.assert_called_with(4321)

    @patch("pythontk.AppLauncher")
    def test_launch_maya_implementation(self, MockAppLauncher):
        """Test the implementation of launch_maya_gui uses AppLauncher."""
        conn = MayaConnection()

        # Mock AppLauncher behavior
        MockAppLauncher.launch.return_value = MagicMock()
        MockAppLauncher.wait_for_ready.return_value = True

        # Patch socket to simulate connection success during wait loop
        with patch("socket.socket") as MockSocket:
            mock_socket_instance = MockSocket.return_value
            mock_socket_instance.connect_ex.return_value = 0  # Success

            # Test generic launch
            conn._launch_maya_gui(port=7002)
            # Verify executable name ('maya')
            self.assertEqual(MockAppLauncher.launch.call_args[0][0], "maya")

            # Test specific path launch
            conn._launch_maya_gui(port=7002, app_path="/custom/path/to/maya.exe")
            # Verify custom path was used
            self.assertEqual(
                MockAppLauncher.launch.call_args[0][0], "/custom/path/to/maya.exe"
            )


if __name__ == "__main__":
    unittest.main()
