# !/usr/bin/python
# coding=utf-8
"""Mock-based tests for the ``mayatk.mat_utils.marmoset_bridge.marmoset_rpc`` package.

Three surfaces under test:

1. The installer: sandbox ``LOCALAPPDATA``, verify resolution + install.
2. The plugin's op registry + HTTP handler (run a real server on a
   localhost port, hit it with the real client -- catches integration
   bugs that a pure mock wouldn't).
3. The ``MarmosetConnection`` client behaviour on connection failure.

The plugin's auto-start is disabled per-test by setting
``MARMOSET_RPC_AUTOSTART=0`` before import, so tests can drive
``start_server()`` / ``stop_server()`` explicitly on a free port.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.request


# ----------------------------------------------------------------------
# Plugin env-var configuration BEFORE import:
#   AUTOSTART=0 -- don't bind a port on module import; tests drive
#       start_server() / stop_server() explicitly so a stale binding
#       can't sabotage the next run.
#   DISABLE_MAIN_THREAD=1 -- some other test in the mock_tests suite
#       (e.g. uitk widget tests) creates a QApplication that persists.
#       Without this, our marshaller would detect that QApp, try to
#       QTimer.singleShot onto it, and hang waiting for an event loop
#       that pytest isn't pumping. Production Toolbag never sets this.
# ----------------------------------------------------------------------
os.environ["MARMOSET_RPC_AUTOSTART"] = "0"
os.environ["MARMOSET_RPC_DISABLE_MAIN_THREAD"] = "1"

# Make the plugin importable. It lives at plugin_src/marmoset_rpc/ so we
# put plugin_src/ on sys.path (just like the install does for Toolbag).
_PLUGIN_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "mayatk",
        "mat_utils",
        "marmoset_bridge",
        "marmoset_rpc",
        "plugin_src",
    )
)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

# Now import everything that depends on the above setup.
import marmoset_rpc as plugin  # noqa: E402  (the plugin module entry)
from marmoset_rpc import registry as plugin_registry  # noqa: E402
from marmoset_rpc import server as plugin_server  # noqa: E402
from marmoset_rpc import main_thread as plugin_main_thread  # noqa: E402
from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (  # noqa: E402
    MarmosetConnection,
    Call,
    Result,
    run_batch,
    install,
    uninstall,
    is_installed,
    user_plugin_dir,
)
from mayatk.mat_utils.marmoset_bridge.marmoset_rpc.installer import (  # noqa: E402
    _plugin_source_dir,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _free_port():
    """Find a port that's almost certainly free (bind+close)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ======================================================================
# Op registry
# ======================================================================
class TestRegistry(unittest.TestCase):
    """The registry powers extensibility; built-ins must be present and
    duplicate registration must fail loud."""

    def test_built_in_ops_registered_via_modular_load(self):
        """Importing the plugin must auto-import every op module under
        ``ops/`` so their @register side-effects populate the registry."""
        ops = plugin.all_ops()
        for required in (
            "system.ping",
            "system.list_ops",
            "system.describe",
            "system.version",
            "scene.summary",
            "scene.list_materials",
        ):
            self.assertIn(required, ops)

    def test_register_rejects_duplicate(self):
        @plugin.register("test.unique_xyz")
        def _a():
            return 1

        try:
            with self.assertRaises(ValueError):
                @plugin.register("test.unique_xyz")
                def _b():
                    return 2
        finally:
            plugin_registry._OPS.pop("test.unique_xyz", None)

    def test_ping_returns_pong(self):
        self.assertEqual(plugin.get_op("system.ping")(), "pong")

    def test_describe_returns_signature_and_doc(self):
        """describe(name) gives an agent everything it needs to call
        the op without reading source."""
        @plugin.register("test.with_sig")
        def _fn(path, count=3):
            """One-line doc."""
            return path, count

        try:
            d = plugin_registry.describe("test.with_sig")
            self.assertEqual(d["name"], "test.with_sig")
            self.assertEqual(d["doc"], "One-line doc.")
            param_names = [p["name"] for p in d["params"]]
            self.assertEqual(param_names, ["path", "count"])
            # 'path' has no default -> "<required>"; 'count' has 3.
            self.assertEqual(d["params"][0]["default"], "<required>")
            self.assertEqual(d["params"][1]["default"], "3")
        finally:
            plugin_registry._OPS.pop("test.with_sig", None)

    def test_describe_all_ops_returns_list(self):
        """No-arg describe returns a list with every registered op."""
        result = plugin_registry.describe()
        self.assertIsInstance(result, list)
        names = {d["name"] for d in result}
        self.assertIn("system.ping", names)
        self.assertIn("scene.summary", names)

    def test_describe_unknown_returns_none(self):
        self.assertIsNone(plugin_registry.describe("does.not.exist"))


# ======================================================================
# HTTP server <-> client integration. Real server on a real port, but
# all-localhost so it doesn't hit the network.
# ======================================================================
class TestServerClientIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        plugin.start_server(port=cls.port)
        cls.conn = MarmosetConnection(port=cls.port)

    @classmethod
    def tearDownClass(cls):
        plugin.stop_server()

    def test_ping_succeeds_against_running_server(self):
        self.assertTrue(self.conn.ping(timeout=2.0))

    def test_invoke_returns_op_value(self):
        self.assertEqual(self.conn.invoke("system.ping"), "pong")

    def test_invoke_list_ops_returns_all_registered(self):
        ops = self.conn.invoke("system.list_ops")
        self.assertIn("system.ping", ops)
        self.assertIn("system.list_ops", ops)
        self.assertIn("system.version", ops)

    def test_invoke_unknown_op_raises_runtimeerror(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.invoke("does.not.exist")
        self.assertIn("Unknown op", str(ctx.exception))

    def test_invoke_failing_op_propagates_exception_text(self):
        @plugin.register("test.always_fail")
        def _boom():
            raise ValueError("intentional test failure")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.invoke("test.always_fail")
            self.assertIn("ValueError", str(ctx.exception))
            self.assertIn("intentional test failure", str(ctx.exception))
        finally:
            plugin_registry._OPS.pop("test.always_fail", None)

    def test_invoke_passes_kwargs_through(self):
        @plugin.register("test.echo_kwargs")
        def _echo(a=None, b=None):
            return {"a": a, "b": b}
        try:
            self.assertEqual(
                self.conn.invoke("test.echo_kwargs", a=1, b="x"),
                {"a": 1, "b": "x"},
            )
        finally:
            plugin_registry._OPS.pop("test.echo_kwargs", None)

    def test_list_ops_convenience(self):
        ops = self.conn.list_ops()
        self.assertIn("system.ping", ops)

    def test_describe_via_dedicated_endpoint(self):
        """Client.describe(op) round-trips through POST /describe."""
        d = self.conn.describe("system.ping")
        self.assertIsNotNone(d)
        self.assertEqual(d["name"], "system.ping")
        self.assertIn("Heartbeat", d["doc"])

    def test_describe_all_via_dedicated_endpoint(self):
        """describe('') returns the full op catalogue."""
        listing = self.conn.describe("")
        self.assertIsInstance(listing, list)
        names = {d["name"] for d in listing}
        self.assertIn("system.ping", names)
        self.assertIn("scene.summary", names)


# ======================================================================
# Job / Call / run_batch -- one-shot pipeline mode
# ======================================================================
class TestRunBatch(unittest.TestCase):
    """Batch mode connects, fires every Call, returns one Result each."""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        plugin.start_server(port=cls.port)

    @classmethod
    def tearDownClass(cls):
        plugin.stop_server()

    def test_run_batch_returns_result_per_call(self):
        results = run_batch(
            [Call("system.ping"), Call("system.list_ops")],
            port=self.port,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].op, "system.ping")
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].value, "pong")
        self.assertTrue(results[1].ok)
        self.assertIn("system.ping", results[1].value)

    def test_run_batch_records_failures_without_aborting(self):
        """Default behaviour: every call runs, failures captured in
        Result.error rather than raising."""
        results = run_batch(
            [
                Call("system.ping"),
                Call("does.not.exist"),  # will fail
                Call("system.list_ops"),
            ],
            port=self.port,
        )
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertIn("Unknown op", results[1].error)
        self.assertTrue(results[2].ok)

    def test_run_batch_stops_on_error_when_requested(self):
        results = run_batch(
            [
                Call("system.ping"),
                Call("does.not.exist"),  # will fail
                Call("system.list_ops"),  # must NOT run
            ],
            port=self.port,
            stop_on_error=True,
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)

    def test_run_batch_forwards_kwargs(self):
        @plugin.register("test.batch_echo")
        def _echo(value=None):
            return value
        try:
            results = run_batch(
                [Call("test.batch_echo", kwargs={"value": "hello"})],
                port=self.port,
            )
            self.assertTrue(results[0].ok)
            self.assertEqual(results[0].value, "hello")
        finally:
            plugin_registry._OPS.pop("test.batch_echo", None)

    def test_run_batch_raises_when_plugin_unreachable(self):
        with self.assertRaises(ConnectionError):
            run_batch([Call("system.ping")], port=_free_port())


# ======================================================================
# Connection failure path -- no server running.
# ======================================================================
class TestConnectionWithoutServer(unittest.TestCase):
    def test_ping_returns_false_when_nothing_listening(self):
        conn = MarmosetConnection(port=_free_port())
        self.assertFalse(conn.ping(timeout=0.5))

    def test_invoke_raises_connectionerror_when_nothing_listening(self):
        conn = MarmosetConnection(port=_free_port())
        with self.assertRaises(ConnectionError):
            conn.invoke("system.ping", timeout=0.5)


# ======================================================================
# Installer: sandbox LOCALAPPDATA, verify resolution + install.
# ======================================================================
class TestInstaller(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="marm_rpc_install_")
        self._env_patch = unittest.mock.patch.dict(
            os.environ, {"LOCALAPPDATA": self._tmp}, clear=False
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stage_toolbag_dir(self, version_suffix="5"):
        """Pretend Toolbag has been installed for this user."""
        d = os.path.join(self._tmp, f"Marmoset Toolbag {version_suffix}")
        os.makedirs(os.path.join(d, "plugins"), exist_ok=True)
        return d

    def test_user_plugin_dir_resolves_from_install_path(self):
        self._stage_toolbag_dir("5")
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        result = user_plugin_dir(exe)
        self.assertEqual(
            os.path.normpath(str(result)),
            os.path.normpath(os.path.join(self._tmp, "Marmoset Toolbag 5", "plugins")),
        )

    def test_user_plugin_dir_falls_back_to_scan(self):
        self._stage_toolbag_dir("4")
        self._stage_toolbag_dir("5")
        # No version in exe -- scan should pick the existing dir.
        # (Tier 2 picks by mtime; either is acceptable as long as result
        # is one of the two real installs.)
        exe = r"D:\custom\toolbag.exe"
        result = user_plugin_dir(exe)
        self.assertIsNotNone(result)
        self.assertTrue(str(result).endswith("plugins"))

    def test_install_creates_plugin_at_target_dir(self):
        self._stage_toolbag_dir("5")
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"

        path = install(toolbag_exe=exe)
        self.assertIsNotNone(path)
        plugin_init = os.path.join(str(path), "__init__.py")
        self.assertTrue(
            os.path.isfile(plugin_init),
            f"Plugin __init__.py missing at {plugin_init}",
        )
        self.assertTrue(is_installed(toolbag_exe=exe))

    def test_install_idempotent_without_force(self):
        self._stage_toolbag_dir("5")
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        first = install(toolbag_exe=exe)
        # Touch the install so we can prove a second call DOESN'T rewrite it.
        marker = os.path.join(str(first), "_marker.txt")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("untouched")

        second = install(toolbag_exe=exe)
        self.assertEqual(str(first), str(second))
        self.assertTrue(os.path.isfile(marker), "Idempotent install wiped the dir.")

    def test_install_force_rewrites(self):
        self._stage_toolbag_dir("5")
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        first = install(toolbag_exe=exe)
        marker = os.path.join(str(first), "_marker.txt")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("delete me")

        install(toolbag_exe=exe, force=True)
        self.assertFalse(
            os.path.isfile(marker),
            "force=True should have rebuilt the install dir.",
        )

    def test_uninstall_removes_plugin(self):
        self._stage_toolbag_dir("5")
        exe = r"C:\Program Files\Marmoset\Toolbag 5\toolbag.exe"
        install(toolbag_exe=exe)
        self.assertTrue(is_installed(toolbag_exe=exe))
        self.assertTrue(uninstall(toolbag_exe=exe))
        self.assertFalse(is_installed(toolbag_exe=exe))

    def test_install_returns_none_when_no_toolbag(self):
        # No LOCALAPPDATA Toolbag dirs at all.
        # Override env var so the scan finds nothing.
        with unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": self._tmp}):
            result = install(toolbag_exe=None)
        self.assertIsNone(result)

    def test_plugin_source_dir_exists_in_package(self):
        """Sanity: the source we install from must actually be in the package."""
        src = _plugin_source_dir()
        self.assertTrue(src.is_dir(), f"plugin source missing: {src}")
        self.assertTrue(
            (src / "__init__.py").is_file(),
            f"plugin __init__.py missing: {src / '__init__.py'}",
        )


# ======================================================================
# Main-thread marshalling
# ======================================================================
class TestMainThreadMarshalling(unittest.TestCase):
    """Two execution modes the marshaller must support correctly:

      1. **No Qt event loop** (tests, agent inspection, headless CLI):
         the marshaller short-circuits and calls *fn* directly. Any
         exception *fn* raises must propagate verbatim.
      2. **Qt event loop + same thread**: same short-circuit. Crossing a
         thread boundary we're already on would deadlock the QTimer trick.

    Mode 3 (off-thread with a live Qt event loop) requires a real Qt
    QApplication and isn't exercised here -- it'd need a running event
    loop in the test, which complicates pytest. The fallback paths cover
    the test environment cleanly.
    """

    def test_no_qt_calls_fn_directly_and_passes_args(self):
        captured = {}

        def _fn(a, b=None):
            captured["a"] = a
            captured["b"] = b
            return a + (b or 0)

        result = plugin_main_thread.run_on_main_thread(_fn, 2, b=5)
        self.assertEqual(result, 7)
        self.assertEqual(captured, {"a": 2, "b": 5})

    def test_no_qt_propagates_exception(self):
        def _fn():
            raise ValueError("simulated op failure")

        with self.assertRaises(ValueError) as ctx:
            plugin_main_thread.run_on_main_thread(_fn)
        self.assertIn("simulated op failure", str(ctx.exception))

    def test_is_main_thread_marshalling_active_false_in_tests(self):
        """Without a QApplication we never marshal; useful diagnostic."""
        self.assertFalse(plugin_main_thread.is_main_thread_marshalling_active())

    def test_server_dispatch_uses_marshaller(self):
        """Sanity: the server's _dispatch path imports run_on_main_thread
        and uses it -- a regression here would silently bypass the
        main-thread guarantee on real Toolbag installs."""
        import inspect
        source = inspect.getsource(plugin_server._Handler._dispatch)
        self.assertIn("run_on_main_thread", source)


# ======================================================================
# Connect / shutdown lifecycle. AppLauncher is patched so we never
# actually launch Toolbag during tests.
# ======================================================================
class TestConnectShutdown(unittest.TestCase):
    """connect() must:
      * Reuse an existing reachable plugin (no launch).
      * Launch Toolbag if not reachable, then wait for /health.
      * Honour force_new=True by launching even if reachable.
      * Time out cleanly if Toolbag dies before the plugin starts.

    shutdown() must only act on the process we launched -- the safety
    contract that lets users keep their own Toolbag open.
    """

    def setUp(self):
        # Patch AppLauncher's launch + find_app + close_process so we
        # never touch the real Toolbag binary.
        self.exe_patch = unittest.mock.patch(
            "pythontk.AppLauncher.find_app",
            return_value=r"C:\fake\toolbag.exe",
        )
        self.launch_patch = unittest.mock.patch(
            "pythontk.AppLauncher.launch"
        )
        self.close_patch = unittest.mock.patch(
            "pythontk.AppLauncher.close_process"
        )
        self.exe_patch.start()
        self.mock_launch = self.launch_patch.start()
        self.mock_close = self.close_patch.start()

    def tearDown(self):
        self.exe_patch.stop()
        self.launch_patch.stop()
        self.close_patch.stop()

    def test_connect_reuses_existing_reachable_plugin(self):
        """If a server is already up, no launch happens."""
        port = _free_port()
        plugin.start_server(port=port)
        try:
            conn = MarmosetConnection(port=port)
            self.assertTrue(conn.connect(timeout=1.0))
            self.mock_launch.assert_not_called()
        finally:
            plugin.stop_server()

    def test_connect_force_new_launches_even_if_reachable(self):
        """force_new=True must bypass the reuse path."""
        port = _free_port()
        plugin.start_server(port=port)
        try:
            self.mock_launch.return_value = unittest.mock.MagicMock(
                pid=1234, poll=lambda: None
            )
            conn = MarmosetConnection(port=port)
            # Will return True because ping() succeeds anyway, but
            # the launch must still have been called.
            conn.connect(force_new=True, timeout=1.0)
            self.mock_launch.assert_called_once()
        finally:
            plugin.stop_server()

    def test_connect_launches_when_plugin_not_reachable(self):
        """No server -> launch + wait. The launch side-effect starts the
        server on a Timer, so the connect() poll loop has to retry at
        least once before /health responds. Avoids the race the
        sleep-before-start version had with the initial ping."""
        import time
        port = _free_port()

        def _on_launch(*_a, **_kw):
            # Simulate Toolbag spinning up: defer the plugin server
            # start by 200ms so the initial ping has time to fail first.
            proc = unittest.mock.MagicMock()
            proc.poll.return_value = None
            threading.Timer(0.2, lambda: plugin.start_server(port=port)).start()
            return proc

        self.mock_launch.side_effect = _on_launch

        try:
            conn = MarmosetConnection(port=port)
            self.assertTrue(conn.connect(timeout=5.0, poll_interval=0.1))
            self.mock_launch.assert_called_once()
        finally:
            # Give the deferred starter a moment to land, then tear down.
            time.sleep(0.3)
            plugin.stop_server()

    def test_connect_returns_false_if_process_dies(self):
        """If the launched Toolbag exits before the plugin comes up,
        connect() returns False rather than waiting out the full timeout."""
        proc = unittest.mock.MagicMock()
        proc.poll.return_value = 1   # Already dead.
        self.mock_launch.return_value = proc

        conn = MarmosetConnection(port=_free_port())
        self.assertFalse(conn.connect(timeout=2.0, poll_interval=0.1))

    def test_connect_raises_when_no_exe_found(self):
        self.exe_patch.stop()
        with unittest.mock.patch(
            "pythontk.AppLauncher.find_app", return_value=None
        ):
            # Use a free port -- a real Toolbag on the dev machine could be
            # listening on 8765 and would make connect() short-circuit on
            # the ping path, never reaching the find_app branch.
            with self.assertRaises(FileNotFoundError):
                MarmosetConnection(port=_free_port()).connect(timeout=0.5)
        # Restart the patch so tearDown can stop it cleanly.
        self.exe_patch = unittest.mock.patch(
            "pythontk.AppLauncher.find_app",
            return_value=r"C:\fake\toolbag.exe",
        )
        self.exe_patch.start()

    def test_connect_auto_cleanup_registers_atexit_hook(self):
        """``auto_cleanup=True`` must register an atexit handler so that
        a Toolbag we launched gets killed if the caller bails before
        explicit shutdown. Mirrors MayaConnection's contract."""
        # Patch atexit.register so we can inspect what got registered
        # without actually firing it.
        with unittest.mock.patch("atexit.register") as mock_register:
            port = _free_port()
            plugin.start_server(port=port)
            try:
                conn = MarmosetConnection(port=port)
                conn.connect(timeout=1.0, auto_cleanup=True)
                mock_register.assert_called_once()
                self.assertTrue(getattr(conn, "_atexit_registered", False))
            finally:
                plugin.stop_server()

    def test_connect_auto_cleanup_is_idempotent(self):
        """Calling connect() twice with auto_cleanup must register only one
        atexit handler -- otherwise repeated connects would stack cleanups."""
        with unittest.mock.patch("atexit.register") as mock_register:
            port = _free_port()
            plugin.start_server(port=port)
            try:
                conn = MarmosetConnection(port=port)
                conn.connect(timeout=1.0, auto_cleanup=True)
                conn.connect(timeout=1.0, auto_cleanup=True)
                self.assertEqual(mock_register.call_count, 1)
            finally:
                plugin.stop_server()

    def test_shutdown_only_terminates_launched_process(self):
        """A connection that didn't launch anything must not call
        close_process. This is the session-safety contract."""
        conn = MarmosetConnection()
        # Manually set _launched_process to simulate having launched.
        proc = unittest.mock.MagicMock(pid=4321)
        conn._launched_process = proc
        conn.shutdown(force=True)
        self.mock_close.assert_called_once_with(4321, force=True)

    def test_shutdown_noop_when_nothing_launched(self):
        conn = MarmosetConnection()
        conn.shutdown(force=True)
        self.mock_close.assert_not_called()


import threading  # noqa: E402  (used by TestConnectShutdown)


if __name__ == "__main__":
    unittest.main()
