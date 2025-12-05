# !/usr/bin/python
# coding=utf-8
"""Test runner for executing unit tests in Maya via command port.

This module provides utilities to run unittest test suites in Maya remotely
by connecting to Maya's command port. This allows you to run tests from your
IDE/editor while Maya is running.

Usage:
    1. In Maya, open command port:
       >>> import mayatk
       >>> mayatk.openPorts(python=':7002')

    2. From your IDE/terminal:
       >>> python maya_test_runner.py

    Or run specific test files:
       >>> python maya_test_runner.py core_utils_test.py
"""
import socket
import sys
import argparse
from pathlib import Path
from typing import Optional, List


class MayaCommandPort:
    """Connect to Maya's command port and execute Python code."""

    def __init__(self, host: str = "localhost", port: int = 7002):
        """Initialize Maya command port connection.

        Args:
            host: Maya host address
            port: Command port number (default: 7002 for Python)
        """
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None

    def __enter__(self):
        """Context manager entry - establish connection."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.disconnect()

    def connect(self) -> None:
        """Establish connection to Maya command port."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            print(f"✓ Connected to Maya at {self.host}:{self.port}")
        except ConnectionRefusedError:
            print(f"✗ Failed to connect to Maya at {self.host}:{self.port}")
            print("\nMake sure Maya is running and command port is open:")
            print("  In Maya: import mayatk; mayatk.openPorts(python=':7002')")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Connection error: {e}")
            sys.exit(1)

    def disconnect(self) -> None:
        """Close connection to Maya command port."""
        if self.socket:
            self.socket.close()
            self.socket = None
            print("\n✓ Disconnected from Maya")

    def execute(self, code: str, receive_output: bool = True) -> Optional[str]:
        """Execute Python code in Maya.

        Args:
            code: Python code to execute
            receive_output: Whether to wait for and return output

        Returns:
            Output from Maya if receive_output is True, else None
        """
        if not self.socket:
            raise RuntimeError("Not connected to Maya")

        # Send code to Maya
        self.socket.sendall(code.encode("utf-8"))

        if receive_output:
            # Receive response from Maya
            response = b""
            while True:
                try:
                    chunk = self.socket.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    # Check if we've received complete output
                    # Maya sends a null byte at the end
                    if b"\x00" in chunk:
                        break
                except socket.timeout:
                    break

            return response.decode("utf-8", errors="replace").rstrip("\x00")

        return None


def run_tests_in_maya(
    test_files: Optional[List[str]] = None,
    host: str = "localhost",
    port: int = 7002,
    test_dir: Optional[str] = None,
) -> None:
    """Run unittest tests in Maya via command port.

    Args:
        test_files: List of test file names (e.g., ['core_utils_test.py'])
                   If None, runs all *_test.py files
        host: Maya host address
        port: Command port number
        test_dir: Directory containing tests (defaults to this script's directory)
    """
    if test_dir is None:
        test_dir = str(Path(__file__).parent)

    # Build the test execution code
    if test_files:
        test_pattern = f"*{{{','.join(test_files)}}}"
    else:
        test_pattern = "*_test.py"

    test_code = f"""
import unittest
import sys

# Ensure test directory is in path
test_dir = r"{test_dir}"
if test_dir not in sys.path:
    sys.path.insert(0, test_dir)

# Discover and run tests
loader = unittest.TestLoader()
suite = loader.discover(
    start_dir=test_dir,
    pattern="{test_pattern}"
)

# Run tests with verbose output
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

# Print summary
print("\\n" + "="*70)
print(f"Tests run: {{result.testsRun}}")
print(f"Failures: {{len(result.failures)}}")
print(f"Errors: {{len(result.errors)}}")
print(f"Skipped: {{len(result.skipped)}}")
print("="*70)

# Return status code
sys.exit(0 if result.wasSuccessful() else 1)
"""

    print(f"\n{'='*70}")
    print("Maya Test Runner")
    print(f"{'='*70}")
    print(f"Test directory: {test_dir}")
    print(f"Test pattern: {test_pattern}")
    print(f"{'='*70}\n")

    # Connect to Maya and run tests
    with MayaCommandPort(host, port) as maya:
        print("Executing tests in Maya...\n")
        output = maya.execute(test_code, receive_output=True)
        if output:
            print(output)


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Run unittest tests in Maya via command port",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all tests
  python maya_test_runner.py
  
  # Run specific test files
  python maya_test_runner.py core_utils_test.py mat_utils_test.py
  
  # Connect to Maya on different host/port
  python maya_test_runner.py --host 192.168.1.100 --port 7003
        """,
    )
    parser.add_argument(
        "test_files",
        nargs="*",
        help="Specific test files to run (e.g., core_utils_test.py)",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Maya host address (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7002,
        help="Maya command port number (default: 7002)",
    )
    parser.add_argument(
        "--test-dir",
        help="Directory containing tests (default: script directory)",
    )

    args = parser.parse_args()

    run_tests_in_maya(
        test_files=args.test_files if args.test_files else None,
        host=args.host,
        port=args.port,
        test_dir=args.test_dir,
    )


if __name__ == "__main__":
    main()

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
# Command port must be opened in Maya before running tests:
#   import mayatk
#   mayatk.openPorts(python=':7002')
#
# Or using the command_port module directly:
#   from mayatk.env_utils import command_port
#   command_port.openPorts(python=':7002')
# -----------------------------------------------------------------------------
