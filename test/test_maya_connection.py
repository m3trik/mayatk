# !/usr/bin/python
# coding=utf-8
"""
Tests for Maya Connection Module

Verifies the functionality of mayatk.env_utils.maya_connection
"""

import unittest
import sys
from unittest.mock import MagicMock, patch

from base_test import MayaTkTestCase
from mayatk.env_utils.maya_connection import MayaConnection
import maya.cmds as cmds

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

    def test_get_available_port_skips_bound_but_not_listening(self):
        # Regression (2026-07-09): a hung Maya can hold a bound socket without
        # listening. A connect probe reads that as "free", the runner launches
        # a new Maya on it, its commandPort can't bind, and the port never
        # opens. The availability check must be bind-based.
        import socket

        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))  # bound, deliberately NOT listening
        squatted = squatter.getsockname()[1]
        try:
            picked = MayaConnection.get_available_port(start_port=squatted)
            self.assertNotEqual(picked, squatted)
        finally:
            squatter.close()

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

    def test_port_helpers_are_not_self_recursive(self):
        """Regression: the encapsulation pass duplicated the module-level port
        helpers (``open_command_ports`` / ``toggle_command_ports`` /
        ``open_available_command_ports``) as in-class staticmethods of the SAME
        name whose body called ``MayaConnection.<same_name>(...)`` — i.e.
        themselves — and, being defined later in the class body, they shadowed
        the real implementations. Any call then recursed infinitely. The names
        must resolve to the canonical implementations, never the self-wrapper.
        """
        import inspect

        for name in (
            "open_command_ports",
            "toggle_command_ports",
            "open_available_command_ports",
        ):
            src = inspect.getsource(getattr(MayaConnection, name))
            self.assertNotIn(
                "Wrapper for MayaConnection",
                src,
                f"MayaConnection.{name} resolves to the recursive self-wrapper "
                f"instead of the real implementation",
            )

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

        if cmds.about(batch=True):
            print("Skipping script editor test in batch mode")
            return

        conn = MayaConnection()
        conn.connect(mode="interactive")

        # Ensure script editor is open
        import maya.mel as mel

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
        result = conn.connect(
            mode="port", port=12345, force_new_instance=False, confirm_existing=False
        )

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
        # launch=False: without it this test LAUNCHED A REAL MAYA — the
        # default launch fallback is real when only sockets are mocked.
        result = conn.connect(
            mode="port", force_new_instance=False, confirm_existing=False, launch=False
        )

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
        """Test standalone connection routing.

        Patches ``_connect_standalone`` directly so the test verifies that
        ``connect(mode="standalone")`` delegates correctly without depending
        on whether ``maya.standalone.initialize`` is patchable in the current
        runtime (it isn't reliably under interactive Maya / command-port test
        runs, where ``maya.standalone`` may be lazily-loaded or shimmed).
        """
        conn = MayaConnection()

        def fake_connect_standalone():
            conn.mode = "standalone"
            conn.is_connected = True
            return True

        with patch.object(
            conn, "_connect_standalone", side_effect=fake_connect_standalone
        ) as mock_connect:
            result = conn.connect(
                mode="standalone", force_new_instance=False, confirm_existing=False
            )

            self.assertTrue(result)
            self.assertEqual(conn.mode, "standalone")
            self.assertTrue(conn.is_connected)
            mock_connect.assert_called_once_with()

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

            result = conn.connect(
                mode="auto",
                launch=True,
                force_new_instance=False,
                confirm_existing=False,
            )

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
        # close_process now accepts an optional `force` kwarg.
        mock_close_process.assert_called_with(4321, force=False)

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

    # ---- context manager -------------------------------------------------

    @patch("socket.socket")
    def test_context_manager_connects_and_shuts_down(self, mock_socket_cls):
        """with MayaConnection() should connect on enter and shutdown on exit."""
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket

        conn = MayaConnection()

        with (
            patch.object(conn, "connect", wraps=conn.connect) as spy_connect,
            patch.object(conn, "shutdown") as mock_shutdown,
        ):
            # Force connect to succeed
            spy_connect.side_effect = lambda **kw: (
                setattr(conn, "is_connected", True) or True
            )

            with conn:
                self.assertTrue(conn.is_connected)

            mock_shutdown.assert_called_once()

    def test_context_manager_already_connected(self):
        """__enter__ should not call connect() again if already connected."""
        conn = MayaConnection()
        conn.is_connected = True
        conn.mode = "port"

        with (
            patch.object(conn, "connect") as mock_connect,
            patch.object(conn, "shutdown"),
        ):
            with conn:
                pass
            mock_connect.assert_not_called()

    def test_context_manager_does_not_suppress_exceptions(self):
        """Exceptions inside the with-block must propagate."""
        conn = MayaConnection()
        conn.is_connected = True
        conn.mode = "port"

        with patch.object(conn, "shutdown"):
            with self.assertRaises(RuntimeError):
                with conn:
                    raise RuntimeError("boom")

    @patch.object(MayaConnection, "close_instance")
    def test_shutdown_port_mode(self, mock_close):
        """shutdown() in port mode should call close_instance(port=...)."""
        conn = MayaConnection()
        conn.is_connected = True
        conn.mode = "port"
        conn.port = 7005

        conn.shutdown()

        mock_close.assert_called_once_with(port=7005, force=False)
        self.assertFalse(conn.is_connected)
        self.assertIsNone(conn.mode)

    def test_shutdown_standalone_mode(self):
        """shutdown() in standalone mode should uninitialise Maya."""
        conn = MayaConnection()
        conn.is_connected = True
        conn.mode = "standalone"

        with patch("maya.standalone.uninitialize") as mock_uninit:
            conn.shutdown()
            mock_uninit.assert_called_once()

        self.assertFalse(conn.is_connected)
        self.assertIsNone(conn.mode)

    def test_shutdown_interactive_mode_no_kill(self):
        """shutdown() in interactive mode should NOT kill Maya — only reset state."""
        conn = MayaConnection()
        conn.is_connected = True
        conn.mode = "interactive"

        with patch.object(MayaConnection, "close_instance") as mock_close:
            conn.shutdown()
            mock_close.assert_not_called()

        self.assertFalse(conn.is_connected)
        self.assertIsNone(conn.mode)

    def test_shutdown_noop_when_not_connected(self):
        """shutdown() should be a no-op when not connected."""
        conn = MayaConnection()
        conn.is_connected = False

        with patch.object(MayaConnection, "close_instance") as mock_close:
            conn.shutdown()  # should not raise
            mock_close.assert_not_called()

    # ---- auto_cleanup ---------------------------------------------------

    def test_auto_cleanup_registers_atexit_handler(self):
        """connect(auto_cleanup=True) registers an atexit handler exactly once."""
        conn = MayaConnection()

        with (
            patch("atexit.register") as mock_register,
            patch.object(conn, "_connect_interactive", return_value=True),
        ):
            conn.connect(mode="interactive", auto_cleanup=True)
            self.assertEqual(mock_register.call_count, 1)

            # Calling connect again must NOT register a second handler.
            conn.connect(mode="interactive", auto_cleanup=True)
            self.assertEqual(mock_register.call_count, 1)

    def test_auto_cleanup_skipped_when_connect_fails(self):
        """A failed connect must NOT register the cleanup handler."""
        conn = MayaConnection()

        with (
            patch("atexit.register") as mock_register,
            patch.object(conn, "_connect_interactive", return_value=False),
        ):
            conn.connect(mode="interactive", auto_cleanup=True)
            mock_register.assert_not_called()

    def test_auto_cleanup_default_off(self):
        """Without auto_cleanup, no atexit handler is registered."""
        conn = MayaConnection()

        with (
            patch("atexit.register") as mock_register,
            patch.object(conn, "_connect_interactive", return_value=True),
        ):
            conn.connect(mode="interactive")
            mock_register.assert_not_called()

    def test_auto_cleanup_handler_calls_shutdown_when_connected(self):
        """The registered handler must shutdown an active connection."""
        conn = MayaConnection()
        captured_handler = []

        def _fake_connect():
            conn.is_connected = True
            return True

        with (
            patch("atexit.register", side_effect=captured_handler.append),
            patch.object(conn, "_connect_interactive", side_effect=_fake_connect),
        ):
            conn.connect(mode="interactive", auto_cleanup=True)

        self.assertEqual(len(captured_handler), 1)
        self.assertTrue(conn.is_connected)

        with patch.object(conn, "shutdown") as mock_shutdown:
            captured_handler[0]()
            mock_shutdown.assert_called_once_with(force=True)

    def test_auto_cleanup_handler_skips_when_already_shut_down(self):
        """The handler must NOT call shutdown if already shut down."""
        conn = MayaConnection()
        captured_handler = []

        def _fake_connect():
            conn.is_connected = True
            return True

        with (
            patch("atexit.register", side_effect=captured_handler.append),
            patch.object(conn, "_connect_interactive", side_effect=_fake_connect),
        ):
            conn.connect(mode="interactive", auto_cleanup=True)

        # Caller already shut down explicitly.
        conn.is_connected = False

        with patch.object(conn, "shutdown") as mock_shutdown:
            captured_handler[0]()
            mock_shutdown.assert_not_called()

    def test_auto_cleanup_handler_swallows_shutdown_errors(self):
        """An exception during shutdown must not propagate out of the handler."""
        conn = MayaConnection()
        captured_handler = []

        def _fake_connect():
            conn.is_connected = True
            return True

        with (
            patch("atexit.register", side_effect=captured_handler.append),
            patch.object(conn, "_connect_interactive", side_effect=_fake_connect),
        ):
            conn.connect(mode="interactive", auto_cleanup=True)

        with patch.object(conn, "shutdown", side_effect=RuntimeError("boom")):
            # Must not raise — atexit handlers should swallow errors so the
            # interpreter can finish exiting cleanly.
            captured_handler[0]()


class TestPortCollisionResilience(unittest.TestCase):
    """Regression tests for WSAEADDRINUSE (10048) on launched-Maya command ports.

    Root causes covered (all mock-only — no Maya session required):
    1. The pre-launch port probe races Maya's slow boot: another process can
       grab the port during the 15-60s window, so the startup MEL must
       self-heal by scanning forward for a bindable port.
    2. The runner's wait loop connect-probed the requested port blindly, so
       when the port was stolen it "succeeded" against a STRANGER's Maya
       (session-safety hazard). It must only accept a port owned by the PID
       it launched.
    3. The force_new_instance=False launch fallback reused a port the
       connect just failed on without checking bindability (guaranteed 10048
       when a zombie squats it bound-but-not-listening).
    4. ensure_connection() killed ALL maya.exe processes (taskkill /IM) —
       including the user's interactive session — violating the session
       safety hard rule. It may only kill the PID it launched.
    """

    NETSTAT_ROWS = [(7002, 9999), (7003, 4242), (7050, 4242), (80, 5678)]

    # ---- netstat inverse lookup -----------------------------------------

    @patch.object(MayaConnection, "_iter_listening_tcp")
    def test_get_port_from_pid_returns_lowest_owned_port(self, mock_rows):
        mock_rows.return_value = self.NETSTAT_ROWS
        self.assertEqual(MayaConnection.get_port_from_pid(4242), 7003)

    @patch.object(MayaConnection, "_iter_listening_tcp")
    def test_get_port_from_pid_respects_scan_range(self, mock_rows):
        mock_rows.return_value = self.NETSTAT_ROWS
        # 7050 is owned by 4242 but outside [7002, 7002 + span)
        self.assertEqual(
            MayaConnection.get_port_from_pid(4242, start_port=7002, span=10), 7003
        )
        self.assertIsNone(
            MayaConnection.get_port_from_pid(4242, start_port=7010, span=10)
        )

    @patch.object(MayaConnection, "_iter_listening_tcp")
    def test_get_port_from_pid_none_for_unknown_pid(self, mock_rows):
        mock_rows.return_value = self.NETSTAT_ROWS
        self.assertIsNone(MayaConnection.get_port_from_pid(1111))

    def test_get_pid_from_port_still_parses_netstat(self):
        """The shared netstat parse must keep get_pid_from_port working.

        Listening rows are identified by the locale-independent foreign
        address ``:0`` — the state token is localized on non-English
        Windows (e.g. German ABHOEREN), so it must not be relied on.
        An ESTABLISHED row for the same local port must not match.
        """
        with patch("subprocess.check_output") as mock_out:
            mock_out.return_value = (
                "  TCP    127.0.0.1:7003 127.0.0.1:52345   ESTABLISHED   7777\n"
                "  TCP    0.0.0.0:7003   0.0.0.0:0   LISTENING   1234\n"
                "  TCP    0.0.0.0:70031  0.0.0.0:0   LISTENING   9999\n"
                "  TCP    [::]:7004      [::]:0      ABHOEREN    4321\n"
            )
            self.assertEqual(MayaConnection.get_pid_from_port(7003), 1234)
            self.assertEqual(MayaConnection.get_pid_from_port(7004), 4321)

    def test_find_port_pair_skips_bound_but_not_listening(self):
        """Same zombie-blindness as get_available_port's 2026-07-09
        regression, in the in-session pair scan: a bound-but-not-listening
        squatter read as "free" by the connect probe, so
        toggle_command_ports handed Maya a dead-on-arrival pair."""
        import socket

        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))  # bound, deliberately NOT listening
        squatted = squatter.getsockname()[1]
        try:
            mel_p, py_p = MayaConnection._find_port_pair(
                mel_start=squatted, python_start=squatted + 1
            )
            self.assertNotEqual(mel_p, f":{squatted}")
            self.assertNotEqual(py_p, f":{squatted + 1}")
        finally:
            squatter.close()

    # ---- startup MEL self-heals a taken port ----------------------------

    @patch("pythontk.AppLauncher")
    def test_startup_command_scans_for_bindable_port(self, MockAppLauncher):
        """The -command MEL must scan forward for a bindable port instead of
        binding the requested port once and erroring (10048) if it's taken,
        and must stamp the ACTUAL port into the window title."""
        MockAppLauncher.launch.return_value = MagicMock(pid=4242)
        MockAppLauncher.wait_for_ready.return_value = True

        conn = MayaConnection()
        conn._launch_maya_gui(port=7002)

        args = MockAppLauncher.launch.call_args[1]["args"]
        cmd = args[args.index("-command") + 1]

        # A scan loop over [port, port + span), not a single fixed bind.
        self.assertIn("for (", cmd)
        self.assertIn(str(7002 + MayaConnection.PORT_SCAN_SPAN), cmd)
        # Bind failures are caught (so the scan continues) …
        self.assertIn("catch(`commandPort", cmd)
        # … and the title reflects the port actually bound, not the request.
        self.assertIn('"Maya [Port: " + $', cmd)
        # The old fixed-title stamp must be gone (it lied after self-heal).
        self.assertNotIn('"Maya [Port: 7002]"', cmd)

    # ---- runner adopts only the port owned by the launched PID ----------

    @staticmethod
    def _wait_calls_check_fn(process, timeout=None, check_fn=None):
        return check_fn(process)

    @patch("pythontk.AppLauncher")
    def test_launch_adopts_pid_owned_port(self, MockAppLauncher):
        """When the requested port was stolen, the runner must discover the
        self-healed port via PID ownership and return it."""
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.side_effect = self._wait_calls_check_fn

        conn = MayaConnection()
        with patch.object(
            MayaConnection,
            "_iter_listening_tcp",
            return_value=[(7002, 9999), (7003, 4242)],
        ):
            actual = conn._launch_maya_gui(port=7002)

        self.assertEqual(actual, 7003)

    @patch("pythontk.AppLauncher")
    def test_launch_never_adopts_foreign_port(self, MockAppLauncher):
        """A port opened by a DIFFERENT process (e.g. the user's Maya) must
        never satisfy the wait — the old connect-probe accepted it."""
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.side_effect = self._wait_calls_check_fn

        conn = MayaConnection()
        with patch.object(
            MayaConnection,
            "_iter_listening_tcp",
            return_value=[(7002, 9999)],  # stranger owns the requested port
        ):
            actual = conn._launch_maya_gui(port=7002)

        self.assertFalse(actual)

    @patch("pythontk.AppLauncher")
    def test_launch_prefers_title_stamped_port(self, MockAppLauncher):
        """userSetup can open its own command ports in the launched session
        (e.g. tentacle's mel/python pair), so lowest-PID-owned-port discovery
        would adopt a MEL-source port and break python execution. The
        window-title tag stamped by the startup MEL is authoritative."""
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.side_effect = self._wait_calls_check_fn
        MockAppLauncher.get_window_titles.return_value = [
            "untitled - Autodesk MAYA 2025.3: untitled [Port: 7004]"
        ]

        conn = MayaConnection()
        with patch.object(
            MayaConnection,
            "_iter_listening_tcp",
            return_value=[(7002, 4242), (7003, 4242), (7004, 4242)],
        ):
            actual = conn._launch_maya_gui(port=7002)

        self.assertEqual(actual, 7004)

    @patch("pythontk.AppLauncher")
    def test_launch_waits_while_title_unstamped(self, MockAppLauncher):
        """A window without the port tag means the startup MEL hasn't run
        yet — the PID-owned netstat fallback must not fire early and adopt a
        userSetup-opened port."""
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.side_effect = self._wait_calls_check_fn
        MockAppLauncher.get_window_titles.return_value = [
            "untitled - Autodesk MAYA 2025.3: untitled"
        ]

        conn = MayaConnection()
        with patch.object(
            MayaConnection,
            "_iter_listening_tcp",
            return_value=[(7002, 4242)],  # userSetup port, already open
        ):
            actual = conn._launch_maya_gui(port=7002)

        self.assertFalse(actual)

    @patch("pythontk.AppLauncher")
    def test_launch_failed_title_reports_failure(self, MockAppLauncher):
        """The FAILED title stamp (scan exhausted) must never be adopted."""
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.side_effect = self._wait_calls_check_fn
        MockAppLauncher.get_window_titles.return_value = ["Maya [Port: FAILED]"]

        conn = MayaConnection()
        actual = conn._launch_maya_gui(port=7002)

        self.assertFalse(actual)

    @patch("pythontk.AppLauncher")
    def test_launch_records_launched_pid(self, MockAppLauncher):
        proc = MagicMock(pid=4242)
        MockAppLauncher.launch.return_value = proc
        MockAppLauncher.wait_for_ready.return_value = True

        conn = MayaConnection()
        conn._launch_maya_gui(port=7002)
        self.assertEqual(conn._launched_pid, 4242)

    # ---- connect() launch fallback re-picks an unbindable port ----------

    def test_connect_launch_fallback_repicks_port_and_connects_to_actual(self):
        conn = MayaConnection()

        with (
            patch.object(MayaConnection, "_is_port_free", return_value=True),
            patch.object(
                MayaConnection, "get_available_port", return_value=7007
            ) as mock_avail,
            patch.object(
                MayaConnection, "_connect_via_port", side_effect=[False, True]
            ) as mock_connect,
            patch.object(
                MayaConnection, "_launch_maya_gui", return_value=7007
            ) as mock_launch,
        ):
            result = conn.connect(
                mode="port",
                port=7002,
                force_new_instance=False,
                confirm_existing=False,
            )

        self.assertTrue(result)
        # The fallback must re-probe rather than launch on the dead port …
        mock_avail.assert_called_with(start_port=7002)
        self.assertEqual(mock_launch.call_args[0][0], 7007)
        # … and the post-launch connect must target the ACTUAL port.
        self.assertEqual(mock_connect.call_args_list[-1][0], ("localhost", 7007))

    # ---- ensure_connection must not kill Mayas it didn't launch ---------

    @patch("time.sleep")
    @patch("subprocess.run")
    @patch("pythontk.AppLauncher")
    def test_ensure_connection_never_taskkills_all_mayas(
        self, MockAppLauncher, mock_run, _sleep
    ):
        conn = MayaConnection()
        conn.mode = "port"
        conn.is_connected = True
        conn.port = 7002

        with (
            patch.object(MayaConnection, "_port_alive", return_value=False),
            patch.object(MayaConnection, "get_available_port", return_value=7002),
            patch.object(MayaConnection, "_launch_maya_gui", return_value=7002),
            patch.object(MayaConnection, "_connect_via_port", return_value=True),
        ):
            # No launched PID recorded → nothing may be killed.
            conn.ensure_connection()
            MockAppLauncher.close_process.assert_not_called()

            # A recorded launched PID that is still a running Maya →
            # exactly that PID is killed.
            conn.is_connected = True
            conn._launched_pid = 555
            MockAppLauncher.get_running_processes.return_value = [555]
            conn.ensure_connection()
            MockAppLauncher.close_process.assert_called_once_with(555, force=True)
            self.assertIsNone(conn._launched_pid)

            # A recorded PID that is NO LONGER a Maya (process died and
            # Windows recycled the PID) → must not be killed.
            conn.is_connected = True
            conn._launched_pid = 777
            MockAppLauncher.get_running_processes.return_value = [555]
            MockAppLauncher.close_process.reset_mock()
            conn.ensure_connection()
            MockAppLauncher.close_process.assert_not_called()
            self.assertIsNone(conn._launched_pid)

        # The indiscriminate `taskkill /F /IM maya.exe` must be gone.
        for call in mock_run.call_args_list:
            self.assertNotIn("taskkill", " ".join(map(str, call[0][0])))


if __name__ == "__main__":
    unittest.main()
