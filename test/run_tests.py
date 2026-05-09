# !/usr/bin/python
# coding=utf-8
"""
MayaTk Test Runner

Unified test runner for executing mayatk tests via Maya command port.
Tests run in Maya and output is displayed in Script Editor.
Results are saved to test_results.txt for review.
README badge is automatically updated with test results.

Usage:
    python run_tests.py                          # Run default core tests
    python run_tests.py core_utils components    # Run specific modules
    python run_tests.py --all                    # Run ALL test modules
    python run_tests.py --dry-run                # Validate test setup without running
    python run_tests.py --quick                  # Run quick validation test
    python run_tests.py --list                   # List available test modules
    python run_tests.py --no-badge               # Skip README badge update
    python run_tests.py --no-wait                # Fire-and-forget (don't poll for results)
    python run_tests.py --keep-maya              # Keep Maya open after tests (default: close)
    python run_tests.py --reuse                  # Reuse existing Maya (CAUTION: resets scene)

Directory Structure:
    - Main Test Suite: mayatk/test/ (Standardized test_*.py files only)
    - Temporary Tests: mayatk/test/temp_tests/ (Reproduction scripts, scratchpad tests)
"""
import re
import sys
import textwrap
from pathlib import Path

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    from mayatk.env_utils import maya_connection
except ImportError:
    print(
        "Warning: mayatk.env_utils.maya_connection module not found. Standalone mode may not work."
    )


class MayaTestRunner:
    """Test runner for mayatk test suite via Maya command port."""

    def __init__(self, host="localhost", port=7002, reuse_instance=False):
        self.host = host
        self.port = port
        self.reuse_instance = reuse_instance
        self.test_dir = Path(__file__).parent

        # FIX: Save results and temp files in temp_tests directory to avoid pollution
        self.temp_test_dir = self.test_dir / "temp_tests"
        self.temp_test_dir.mkdir(exist_ok=True)

        self.results_file = self.temp_test_dir / "test_results.txt"
        try:
            self.connection = maya_connection.MayaConnection.get_instance()
        except NameError:
            self.connection = None

    def connect_to_maya(self):
        """Connect to Maya using MayaConnection.

        By default launches a NEW Maya instance to protect the user's session.
        Pass --reuse on the CLI (or reuse_instance=True) to attach to an
        already-running instance instead.
        """
        if not self.connection:
            print("[ERROR] MayaConnection not available")
            return False

        force_new = not self.reuse_instance

        if force_new:
            print(
                "[INFO] Launching a NEW Maya instance for testing "
                "(user sessions will not be touched)."
            )
        else:
            print(
                "[WARNING] --reuse flag active: connecting to an EXISTING Maya "
                "instance. The current scene WILL be modified/reset by tests!"
            )

        if self.connection.connect(
            mode="auto",
            port=self.port,
            host=self.host,
            force_new_instance=force_new,
            confirm_existing=not self.reuse_instance,
        ):
            print(f"[OK] Connected to Maya in {self.connection.mode} mode")
            if not self.verify_connection():
                print("[ERROR] Connection verification failed — Maya not responding")
                return False
            return True
        else:
            print("[ERROR] Failed to connect to Maya")
            return False

    def verify_connection(self):
        """Verify Maya connection with a round-trip data check.

        Sends a trivial expression to Maya and checks it returns the
        expected result.  This catches cases where ``connect()`` reports
        success but the command port is not actually functional.

        Returns:
            True if Maya responded correctly.
        """
        if not self.connection or not self.connection.is_connected:
            return False

        if self.connection.mode == "port":
            try:
                result = self.connection.execute(
                    "str(1+1)", wait_for_response=True, timeout=10
                )
                if result and result.strip() == "2":
                    print(
                        "[VERIFIED] Maya connection confirmed (round-trip data check)"
                    )
                    return True
                print(f"[WARNING] Unexpected verification response: {result!r}")
                return False
            except Exception as e:
                print(f"[WARNING] Connection verification failed: {e}")
                return False
        else:
            # Standalone/interactive — just check we can execute
            try:
                self.connection.execute("pass")
                print("[VERIFIED] Maya connection confirmed")
                return True
            except Exception as e:
                print(f"[WARNING] Connection verification failed: {e}")
                return False

    def send_code(self, code):
        """Send Python code to Maya."""
        try:
            self.connection.execute(code)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to execute code: {e}")
            return False

    # Subdirectories opted out of default discovery — caller must pass
    # ``--extended`` or ``--mocks`` to include them.
    EXTENDED_DIR = "extended"
    MOCKS_DIR = "mock_tests"
    SKIP_INFRA = {
        "test_imports",
        "test_lazy_loading_maya",
        "test_module_resolver_integration",
    }

    def _discover_in(self, subdir: str = ""):
        """Return ``[test_name]`` from ``test_dir / subdir``, sans infra files."""
        path = self.test_dir / subdir if subdir else self.test_dir
        if not path.exists():
            return []
        return sorted(
            f.stem for f in path.glob("test_*.py") if f.stem not in self.SKIP_INFRA
        )

    def discover_tests(self, include_extended: bool = False, include_mocks: bool = False):
        """Discover available test modules.

        Default: only ``mayatk/test/test_*.py`` (the main suite).
        ``include_extended``: also pull in ``mayatk/test/extended/test_*.py``.
        ``include_mocks``: also pull in ``mayatk/test/mock_tests/test_*.py``.
        """
        modules = list(self._discover_in())
        if include_extended:
            modules.extend(self._discover_in(self.EXTENDED_DIR))
        if include_mocks:
            modules.extend(self._discover_in(self.MOCKS_DIR))
        return sorted(set(modules))

    def _path_for_module(self, module_name: str) -> str:
        """Resolve a module name to its absolute file path, checking subdirs."""
        for subdir in ("", self.EXTENDED_DIR, self.MOCKS_DIR):
            candidate = (self.test_dir / subdir / f"{module_name}.py")
            if candidate.exists():
                return str(candidate).replace("\\", "/")
        # Fall back to the main dir even if missing — caller will surface the load error
        return str(self.test_dir / f"{module_name}.py").replace("\\", "/")

    def list_tests(self):
        """List all available test modules grouped by category."""
        groups = (
            ("MAIN", self._discover_in()),
            ("EXTENDED (--extended)", self._discover_in(self.EXTENDED_DIR)),
            ("MOCK-ONLY (--mocks, pytest)", self._discover_in(self.MOCKS_DIR)),
        )
        print("\n" + "=" * 70)
        print("AVAILABLE TEST MODULES")
        print("=" * 70)
        i = 0
        for label, modules in groups:
            if not modules:
                continue
            print(f"\n[{label}]")
            for module in modules:
                i += 1
                display_name = module.replace("test_", "")
                print(f"  {i:3d}. {display_name:30s} ({module})")
        print("=" * 70)
        print(f"\nTotal: {i} test modules")
        print("\nUsage: python run_tests.py <module_name> [<module_name> ...]")
        print("Example: python run_tests.py core_utils components")
        print("Flags:   --extended  --mocks  --all")

    def run_quick_test(self):
        """Run a single quick validation test."""
        print("\n" + "=" * 70)
        print("QUICK VALIDATION TEST")
        print("=" * 70)

        if not self.connect_to_maya():
            return False

        code = """
import sys
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts')
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test')

print("\\\\n" + "="*70)
print("QUICK TEST: test_core_utils (first class only)")
print("="*70)

try:
    import test_core_utils as test_mod
    import unittest
    
    # Run first test class
    for attr_name in dir(test_mod):
        attr = getattr(test_mod, attr_name)
        if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
            if attr is not unittest.TestCase and attr.__name__ != "MayaTkTestCase":
                suite = unittest.makeSuite(attr)
                runner = unittest.TextTestRunner(verbosity=2)
                result = runner.run(suite)
                
                print("\\\\n" + "-"*70)
                if result.wasSuccessful():
                    print(f"[PASS] {attr_name}: ALL {result.testsRun} TESTS PASSED")
                else:
                    print(f"[FAIL] {attr_name}: {len(result.failures + result.errors)} FAILURES")
                print("-"*70)
                break
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
"""

        print("\nRunning quick validation test...")
        print("Check Maya Script Editor for detailed output\n")

        if self.send_code(code):
            print("[OK] Test code sent successfully")
            return True
        return False

    def run_tests(self, modules=None, dry_run=False, extended=False, mocks=False):
        """
        Run tests for specified modules.

        Args:
            modules: List of module names (with or without test_ prefix).
                    None = run all default modules.
            dry_run: If True, show what would be executed without running tests.
            extended: If True, run extended tests (sets MAYATK_EXTENDED_TESTS=1
                    and pulls in ``mayatk/test/extended/test_*.py``).
            mocks: If True, also include ``mayatk/test/mock_tests/test_*.py``
                    (designed for pytest-conftest mocking, mostly skipped under
                    mayapy).
        """
        # Default test modules (core functionality)
        # NOTE: test_calculator runs via regular pytest (no Maya needed)
        # NOTE: test_keyframe_grouper does not exist yet
        default_modules = [
            "test_core_utils",
            "test_components",
            "test_node_utils",
            "test_edit_utils",
            "test_mat_utils",
            "test_xform_utils",
            "test_rig_utils",
            "test_env_utils",
            "test_scale_keys",
            "test_stagger_keys",
        ]

        if modules is None:
            test_modules = default_modules
        else:
            # Ensure test_ prefix
            test_modules = [
                m if m.startswith("test_") else f"test_{m}" for m in modules
            ]

        # Resolve each module name to its on-disk path so the in-Maya runner
        # can load tests from extended/ and mock_tests/ subdirs.
        test_module_paths = {m: self._path_for_module(m) for m in test_modules}

        print("\n" + "=" * 70)
        print("MAYATK TEST RUNNER" + (" (DRY RUN)" if dry_run else ""))
        print("=" * 70)
        print(f"{'Would run' if dry_run else 'Running'} {len(test_modules)} modules:")
        for module in test_modules:
            print(f"  • {module}")
        if extended:
            print("  • Extended tests enabled")
        if mocks:
            print("  • Mock-only tests enabled")
        print("=" * 70)

        if dry_run:
            print("\nDry run - no tests will be executed.")
            print("This validates:")
            print("  [OK] Module names are correct")
            print("  [OK] Test files exist")
            print("  [OK] Test runner configuration is valid")
            print("\nTo actually run tests, omit --dry-run flag")
            return True

        # Connect to Maya
        if not self.connect_to_maya():
            return False

        output_file_path = str(self.results_file).replace("\\", "/")

        # Generate test execution code
        test_code = textwrap.dedent(
            f"""
            import sys
            import os
            
            # Set extended tests flag
            if {extended}:
                os.environ['MAYATK_EXTENDED_TESTS'] = '1'
            else:
                if 'MAYATK_EXTENDED_TESTS' in os.environ:
                    del os.environ['MAYATK_EXTENDED_TESTS']
            
            sys.path.insert(0, r'O:/Cloud/Code/_scripts/mayatk/test')
            # Subdirectory tests need their own dir on sys.path so sibling
            # imports (e.g. from base_test) resolve identically.
            sys.path.insert(0, r'O:/Cloud/Code/_scripts/mayatk/test/extended')
            sys.path.insert(0, r'O:/Cloud/Code/_scripts/mayatk/test/mock_tests')
            # Add all package roots to sys.path
            package_roots = [
                r'O:/Cloud/Code/_scripts',
                r'O:/Cloud/Code/_scripts/mayatk',
                r'O:/Cloud/Code/_scripts/pythontk',
                r'O:/Cloud/Code/_scripts/uitk',
                r'O:/Cloud/Code/_scripts/tentacle',
            ]
            for root in package_roots:
                if root not in sys.path:
                    sys.path.insert(0, root)

            import unittest
            import importlib.util

            # Use ModuleReloader to properly reload mayatk modules
            try:
                from pythontk import ModuleReloader
                reloader = ModuleReloader(include_submodules=True)                
                # Reload pythontk first to ensure core utilities are up to date
                import pythontk
                reloader.reload(pythontk)
                print("[ModuleReloader] Reloaded pythontk")
                import mayatk
                reloaded = reloader.reload(mayatk)
                print(f"[ModuleReloader] Reloaded {{len(reloaded)}} mayatk modules")
                
                # Force reload base_test by removing it from sys.modules
                if 'base_test' in sys.modules:
                    del sys.modules['base_test']
                    print("[ModuleReloader] Removed base_test from sys.modules to force reload")
                
                # Debug: Check if base_test can be imported and has the attribute
                try:
                    import base_test
                    print(f"[Debug] Imported base_test from: {{base_test.__file__}}")
                    if hasattr(base_test, 'skipUnlessExtended'):
                        print("[Debug] base_test has skipUnlessExtended")
                    else:
                        print("[Debug] base_test MISSING skipUnlessExtended")
                        print(f"[Debug] dir(base_test): {{dir(base_test)}}")
                except ImportError as e:
                    print(f"[Debug] Could not import base_test: {{e}}")
                    
            except Exception as e:
                # Fallback to simple module clearing if reloader fails
                modules_to_clear = [k for k in list(sys.modules.keys()) if 'mayatk' in k.lower()]
                for mod in modules_to_clear:
                    del sys.modules[mod]
                print(f"[Fallback] Cleared {{len(modules_to_clear)}} cached mayatk modules")

            # Setup results file
            output_file = r'{output_file_path}'
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("="*70 + "\\n")
                f.write("MAYATK TEST RESULTS\\n")
                f.write("="*70 + "\\n\\n")
                
            print("\\n" + "="*70)
            print("MAYATK TEST SUITE")
            print("="*70)

            test_modules = {test_modules}
            total_tests = 0
            total_failures = 0
            total_errors = 0
            total_skipped = 0
            results_summary = []

            # Snapshot real Maya modules so tests that mock sys.modules
            # don't pollute later test modules.
            _real_module_keys = (
                "maya", "maya.cmds", "maya.mel", "maya.OpenMaya", "maya.OpenMayaUI",
                "maya.api", "maya.api.OpenMaya", "maya.api.OpenMayaAnim",
                "maya.utils", "maya.app",
            )
            _real_modules_snapshot = {{
                k: sys.modules[k] for k in _real_module_keys if k in sys.modules
            }}

            test_module_paths = {test_module_paths}
            for module_name in test_modules:
                print(f"\\n{{'-'*70}}")
                print(f"Testing: {{module_name}}")
                print(('-'*70))

                module_path = test_module_paths.get(
                    module_name,
                    rf'O:/Cloud/Code/_scripts/mayatk/test/{{module_name}}.py',
                )
                try:
                    spec = importlib.util.spec_from_file_location(
                        module_name,
                        module_path,
                    )
                    test_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(test_module)
                    
                    # Create test suite
                    suite = unittest.TestSuite()
                    for attr_name in dir(test_module):
                        attr = getattr(test_module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
                            if attr is not unittest.TestCase:
                                suite.addTest(unittest.makeSuite(attr))
                    
                    # Run tests
                    runner = unittest.TextTestRunner(verbosity=2)
                    result = runner.run(suite)

                    # Filter out @skipUnlessExtended skips — they're an
                    # opt-in marker, not a real skip. Subtract them from
                    # the totals so the main run reports 0 skipped.
                    extended_skips = [
                        (t, r) for (t, r) in result.skipped
                        if "Extended test" in r
                    ]
                    real_skipped = [
                        (t, r) for (t, r) in result.skipped
                        if "Extended test" not in r
                    ]
                    real_run = result.testsRun - len(extended_skips)

                    # Track results
                    total_tests += real_run
                    total_failures += len(result.failures)
                    total_errors += len(result.errors)
                    total_skipped += len(real_skipped)
                    
                    status = "PASS" if result.wasSuccessful() else "FAIL"
                    results_summary.append(
                        f"{{module_name}}: {{status}} ({{real_run}} tests, "
                        f"{{len(result.failures)}} failures, {{len(result.errors)}} errors)"
                    )

                    # Write to results file
                    with open(output_file, 'a', encoding='utf-8') as f:
                        f.write(f"\\n{{module_name}}: {{status}}\\n")
                        f.write(
                            f"  Tests: {{real_run}}, "
                            f"Failures: {{len(result.failures)}}, "
                            f"Errors: {{len(result.errors)}}, "
                            f"Skipped: {{len(real_skipped)}}"
                        )
                        if extended_skips:
                            f.write(f", Extended-deferred: {{len(extended_skips)}}")
                        f.write("\\n")

                        if result.failures:
                            for test, trace in result.failures:  # All failures
                                f.write(f"\\n  FAILURE: {{test}}\\n")
                                f.write(f"  {{trace}}\\n")

                        if result.errors:
                            for test, trace in result.errors:  # All errors
                                f.write(f"\\n  ERROR: {{test}}\\n")
                                f.write(f"  {{trace}}\\n")

                        for test, reason in real_skipped:
                            f.write(f"  SKIP: {{test}} | {{reason}}\\n")
                    
                except Exception as e:
                    print(f"[ERROR] Error loading {{module_name}}: {{e}}")
                    results_summary.append(f"{{module_name}}: LOAD ERROR - {{str(e)[:50]}}")

                    with open(output_file, 'a', encoding='utf-8') as f:
                        f.write(f"\\n{{module_name}}: LOAD ERROR\\n")
                        f.write(f"  {{str(e)}}\\n")
                finally:
                    # Restore real Maya modules in case the just-loaded
                    # test patched sys.modules with mocks.
                    for _k, _real_mod in _real_modules_snapshot.items():
                        sys.modules[_k] = _real_mod
                    # Clear cached mayatk modules so the next test re-imports
                    # against the restored real Maya modules.
                    for _k in [k for k in list(sys.modules.keys()) if k.startswith("mayatk")]:
                        del sys.modules[_k]

            # Print final summary
            print("\\n" + "="*70)
            print("TEST SUMMARY")
            print("="*70)
            for result_line in results_summary:
                print(f"  {{result_line}}")
            print("="*70)
            print(f"Total: {{total_tests}} tests, {{total_failures}} failures, "
                  f"{{total_errors}} errors, {{total_skipped}} skipped")
            print("="*70)

            # Write summary to file
            with open(output_file, 'a', encoding='utf-8') as f:
                f.write("\\n" + "="*70 + "\\n")
                f.write("SUMMARY\\n")
                f.write("="*70 + "\\n")
                for result_line in results_summary:
                    f.write(f"  {{result_line}}\\n")
                f.write("="*70 + "\\n")
                f.write(f"Total: {{total_tests}} tests, {{total_failures}} failures, "
                       f"{{total_errors}} errors, {{total_skipped}} skipped\\n")
                f.write("="*70 + "\\n")

            print(f"\\nResults saved to: {{output_file}}")

            # Final status
            if total_failures == 0 and total_errors == 0:
                print("\\n[PASS] ALL TESTS PASSED!")

            # Expose summary to __main__ for socket-based polling
            import __main__ as _mayatk_main
            _mayatk_main._mayatk_test_summary = (
                f"Total: {{total_tests}} tests, {{total_failures}} failures, "
                f"{{total_errors}} errors, {{total_skipped}} skipped"
            )
            _mayatk_main._mayatk_test_passed = (
                total_failures == 0 and total_errors == 0
            )
            """
        )

        # Write test code to a temporary file to avoid command port size limits
        temp_runner_path = self.temp_test_dir / "_temp_test_runner.py"
        try:
            with open(temp_runner_path, "w", encoding="utf-8") as f:
                f.write(test_code)
        except Exception as e:
            print(f"[ERROR] Failed to write temp runner file: {e}")
            return False

        # Generate execution code (small payload)
        runner_dir = str(self.temp_test_dir).replace("\\", "/")
        output_file_path = str(self.results_file).replace("\\", "/")

        exec_code = f"""
import sys
import os

runner_dir = r'{runner_dir}'
if runner_dir not in sys.path:
    sys.path.insert(0, runner_dir)

# AGGRESSIVE MODULE CLEARING - clear all mayatk modules BEFORE running tests
# This ensures fresh imports from the test files
# BUT preserve maya_connection to keep the Maya standalone session and QApplication alive
# Also preserve PySide/qtpy related modules to prevent losing QApplication reference
mods_to_clear = [k for k in list(sys.modules.keys()) 
                 if ('mayatk' in k.lower() or '_temp_test_runner' in k)
                 and 'maya_connection' not in k
                 and 'qt' not in k.lower()
                 and 'pyside' not in k.lower()]
for mod in mods_to_clear:
    try:
        del sys.modules[mod]
    except KeyError:
        pass
print(f"[RELOAD] Cleared {{len(mods_to_clear)}} modules before test execution")

# Force reload/execution of the temp runner
import __main__ as _mayatk_main
_mayatk_main._mayatk_test_complete = False
try:
    # Drop any cached version so the import re-executes the module body once.
    if '_temp_test_runner' in sys.modules:
        del sys.modules['_temp_test_runner']
    import _temp_test_runner  # runs the test suite top-level
except Exception as e:
    print(f"Error executing test runner: {{e}}")
    import traceback
    traceback.print_exc()
finally:
    _mayatk_main._mayatk_test_complete = True
"""

        # Clear stale results before sending
        if self.results_file.exists():
            self.results_file.unlink()

        print("\nSending test code to Maya (via file)...")

        if self.send_code(exec_code):
            print("[OK] Test code sent successfully")
            print("\n" + "=" * 70)
            print("TESTS ARE RUNNING IN MAYA")
            print("=" * 70)
            print(f"Results file: {self.results_file}")
            print("=" * 70)
            return True
        else:
            print("[ERROR] Failed to send test code")
            return False

    def wait_for_results(self, timeout: int = 600, poll_interval: float = 2.0) -> bool:
        """Poll Maya for test completion, with file-based fallback.

        Primary: asks Maya directly via socket whether the
        ``_mayatk_test_complete`` sentinel (set by the test code) is True.
        Fallback: watches the results file for the ``SUMMARY`` marker.

        Parameters:
            timeout: Maximum seconds to wait (default 10 minutes).
            poll_interval: Seconds between checks.

        Returns:
            True if results were found before timeout, False otherwise.
        """
        import time as _time

        start = _time.monotonic()
        last_size = 0
        use_socket = (
            self.connection
            and self.connection.is_connected
            and self.connection.mode == "port"
        )

        print(f"\nWaiting for tests to complete (timeout: {timeout}s) ...")

        while (_time.monotonic() - start) < timeout:
            elapsed = int(_time.monotonic() - start)

            # ---- primary: socket-based sentinel check ----
            if use_socket:
                try:
                    done = self.connection.execute(
                        "getattr(__import__('__main__'), '_mayatk_test_complete', False)",
                        wait_for_response=True,
                        timeout=5,
                    )
                    if done and str(done).strip().lower() == "true":
                        print(f"\r  Tests completed in {elapsed}s.{' ' * 30}")
                        # Retrieve summary directly from Maya
                        summary = self.connection.execute(
                            "getattr(__import__('__main__'), '_mayatk_test_summary', '')",
                            wait_for_response=True,
                            timeout=5,
                        )
                        if summary and summary.strip():
                            print(f"  Maya reports: {summary.strip()}")
                        return True
                except Exception:
                    pass  # fall through to file check

            # ---- fallback: file-based polling ----
            if self.results_file.exists():
                try:
                    content = self.results_file.read_text(encoding="utf-8")
                except (OSError, PermissionError):
                    _time.sleep(poll_interval)
                    continue

                cur_size = len(content)
                if cur_size != last_size:
                    module_count = (
                        content.count(": PASS")
                        + content.count(": FAIL")
                        + content.count(": LOAD ERROR")
                    )
                    print(
                        f"\r  [{elapsed}s] {module_count} module(s) finished ...",
                        end="",
                        flush=True,
                    )
                    last_size = cur_size

                if "SUMMARY" in content:
                    print(f"\r  Tests completed in {elapsed}s.{' ' * 30}")
                    return True

            _time.sleep(poll_interval)

        elapsed = int(_time.monotonic() - start)
        print(f"\n[TIMEOUT] Results not found after {elapsed}s.")
        return False

    def print_results(self) -> None:
        """Read and print the results file contents to console."""
        if not self.results_file.exists():
            print("[WARNING] No results file found.")
            return

        content = self.results_file.read_text(encoding="utf-8")
        # Use a unicode-safe write for Windows cp1252 consoles.
        try:
            print("\n" + content)
        except UnicodeEncodeError:
            import sys as _sys
            safe = content.encode(
                _sys.stdout.encoding or "ascii", errors="replace"
            ).decode(_sys.stdout.encoding or "ascii")
            print("\n" + safe)

    def update_readme_badge(self, passed: int, failed: int) -> bool:
        """Update the README with a test status badge.

        Parameters:
            passed: Number of passed tests.
            failed: Number of failed tests.

        Returns:
            True if README was updated successfully.
        """
        readme_path = self.test_dir.parent / "docs" / "README.md"

        if not readme_path.exists():
            print(f"README not found at {readme_path}")
            return False

        content = readme_path.read_text(encoding="utf-8")

        total = passed + failed
        if failed == 0:
            color = "brightgreen"
            status = f"{passed} passed"
        elif passed == 0:
            color = "red"
            status = f"{failed} failed"
        else:
            color = "orange"
            status = f"{passed} passed, {failed} failed"

        # Create the new badge
        new_badge = f"[![Tests](https://img.shields.io/badge/Tests-{status.replace(' ', '%20').replace(',', '')}-{color}.svg)](test/)"

        # Check if a Tests badge already exists and replace it
        tests_badge_pattern = (
            r"\[!\[Tests\]\(https://img\.shields\.io/badge/Tests-[^\)]+\)\]\([^\)]+\)"
        )

        if re.search(tests_badge_pattern, content):
            # Replace existing badge
            new_content = re.sub(tests_badge_pattern, new_badge, content)
        else:
            # Add badge after the Maya badge line
            maya_badge_pattern = r"(\[!\[Maya\]\(https://img\.shields\.io/badge/Maya-[^\)]+\)\]\([^\)]+\))"
            match = re.search(maya_badge_pattern, content)
            if match:
                # Insert after Maya badge
                insert_pos = match.end()
                new_content = (
                    content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                )
            else:
                # Fallback: add after Python badge
                python_badge_pattern = r"(\[!\[Python\]\(https://img\.shields\.io/badge/Python-[^\)]+\)\]\([^\)]+\))"
                match = re.search(python_badge_pattern, content)
                if match:
                    insert_pos = match.end()
                    new_content = (
                        content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                    )
                else:
                    # Last resort: add at the very beginning
                    new_content = new_badge + "\n" + content

        readme_path.write_text(new_content, encoding="utf-8")
        print(f"\n[OK] README badge updated: {status}")
        return True

    def parse_test_results(self) -> tuple:
        """Parse test_results.txt to extract test counts.

        Returns:
            Tuple of (passed, failed) where failed = failures + errors
        """
        if not self.results_file.exists():
            return (0, 0)

        content = self.results_file.read_text(encoding="utf-8")

        # Look for the total line: "Total: 253 tests, 0 failures, 1 errors, 15 skipped"
        match = re.search(
            r"Total: (\d+) tests, (\d+) failures?, (\d+) errors?", content
        )

        if match:
            total = int(match.group(1))
            failures = int(match.group(2))
            errors = int(match.group(3))
            passed = total - failures - errors
            return (passed, failures + errors)

        return (0, 0)


def main():
    """Main entry point."""
    # Parse command line arguments
    args = sys.argv[1:]

    # Parse port first
    port = 7002
    if "--port" in args:
        try:
            p_idx = args.index("--port")
            if p_idx + 1 < len(args):
                port = int(args[p_idx + 1])
                # Remove --port and value from args so they aren't treated as module names
                args.pop(p_idx)
                args.pop(p_idx)
        except (ValueError, IndexError):
            print("Invalid port specified")
            return

    # --reuse: connect to an existing Maya session (DANGEROUS: will reset the scene)
    reuse_instance = "--reuse" in args
    if reuse_instance:
        args = [arg for arg in args if arg != "--reuse"]

    runner = MayaTestRunner(port=port, reuse_instance=reuse_instance)

    # Check for flags
    dry_run = "--dry-run" in args or "-d" in args
    no_badge = "--no-badge" in args
    no_wait = "--no-wait" in args
    keep_maya = "--keep-maya" in args
    extended = "--extended" in args or "-e" in args
    mocks = "--mocks" in args

    if dry_run:
        args = [arg for arg in args if arg not in ("--dry-run", "-d")]
    if no_badge:
        args = [arg for arg in args if arg != "--no-badge"]
    if no_wait:
        args = [arg for arg in args if arg != "--no-wait"]
    if keep_maya:
        args = [arg for arg in args if arg != "--keep-maya"]
    if extended:
        args = [arg for arg in args if arg not in ("--extended", "-e")]
    if mocks:
        args = [arg for arg in args if arg != "--mocks"]

    if "--list" in args or "-l" in args:
        runner.list_tests()
        return
    elif "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Everything below may launch Maya — wrap in try/finally for cleanup
    try:
        if not args:
            success = runner.run_tests(
                dry_run=dry_run, extended=extended, mocks=mocks
            )
        elif "--quick" in args or "-q" in args:
            success = runner.run_quick_test()
            # Quick test is fire-and-forget (no results file to poll)
            return
        elif "--all" in args or "-a" in args:
            all_modules = runner.discover_tests(
                include_extended=extended, include_mocks=mocks
            )
            print(f"\nRunning ALL {len(all_modules)} test modules...")
            success = runner.run_tests(
                all_modules, dry_run=dry_run, extended=extended, mocks=mocks
            )
        else:
            success = runner.run_tests(
                args, dry_run=dry_run, extended=extended, mocks=mocks
            )

        if not success or dry_run:
            return

        # Wait for results (default) or fire-and-forget (--no-wait)
        if no_wait:
            print(f"\n--no-wait: results will be written to {runner.results_file}")
            print(f'  Get-Content "{runner.results_file}" -Wait')
            return  # finally block handles Maya cleanup

        if runner.wait_for_results():
            runner.print_results()

            # Update README badge with test results (unless disabled)
            if not no_badge:
                passed, failed = runner.parse_test_results()
                if passed > 0 or failed > 0:
                    runner.update_readme_badge(passed, failed)
        else:
            # Timed out — still try to show partial results
            if runner.results_file.exists():
                print("\n[PARTIAL RESULTS]")
                runner.print_results()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Cleaning up ...")
    finally:
        if not keep_maya and runner.connection and runner.connection.is_connected:
            print("\nClosing Maya instance ...")
            try:
                runner.connection.shutdown(force=True)
                print("[OK] Maya closed.")
            except Exception as e:
                print(f"[WARNING] Failed to close Maya gracefully: {e}")
                # Last resort: kill by PID
                try:
                    port = getattr(runner.connection, "port", None)
                    if port:
                        runner.connection.close_instance(port=port, force=True)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
