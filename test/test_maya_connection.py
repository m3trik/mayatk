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
from mayatk.env_utils.maya_connection import (
    MayaConnection,
    reload_modules,
    get_connection,
    ensure_maya_connection,
)


class TestMayaConnection(MayaTkTestCase):
    """Test cases for MayaConnection class."""

    def setUp(self):
        super().setUp()
        # Reset singleton for testing
        import mayatk.env_utils.maya_connection as mc

        mc._connection = None

    def test_initialization(self):
        """Test MayaConnection initialization."""
        conn = MayaConnection()
        self.assertIsNone(conn.mode)
        self.assertFalse(conn.is_connected)

    def test_detect_mode_interactive(self):
        """Test mode detection inside Maya."""
        conn = MayaConnection()
        mode = conn._detect_mode()
        self.assertEqual(mode, "interactive")
        self.assertEqual(conn.mode, "interactive")
        self.assertTrue(conn.is_connected)

    def test_connect_interactive(self):
        """Test explicit interactive connection."""
        conn = MayaConnection()
        result = conn.connect(mode="interactive")
        self.assertTrue(result)
        self.assertEqual(conn.mode, "interactive")
        self.assertTrue(conn.is_connected)

    def test_singleton_access(self):
        """Test get_connection singleton behavior."""
        conn1 = get_connection()
        conn2 = get_connection()
        self.assertIs(conn1, conn2)
        # Check class name to avoid reload-induced type mismatch
        self.assertEqual(conn1.__class__.__name__, "MayaConnection")

    def test_ensure_connection(self):
        """Test ensure_maya_connection."""
        conn = ensure_maya_connection()
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
            reloaded = reload_modules(mod_name, verbose=True)
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
        reloaded = reload_modules(modules, verbose=False)

        # Check if at least the connection module is in the list
        self.assertIn("mayatk.env_utils.maya_connection", reloaded)

    def test_reload_nonexistent_module(self):
        """Test reloading a non-existent module."""
        mod_name = "mayatk.non_existent_module_xyz"

        # Should not raise exception
        reloaded = reload_modules(mod_name, verbose=False)
        self.assertNotIn(mod_name, reloaded)

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
        conn = MayaConnection()

        # Mock maya.standalone.initialize
        # We patch it where it exists (in the maya.standalone module)
        with patch("maya.standalone.initialize") as mock_initialize:
            result = conn.connect(mode="standalone")

            self.assertTrue(result)
            self.assertEqual(conn.mode, "standalone")
            self.assertTrue(conn.is_connected)
            mock_initialize.assert_called_with(name="python")
