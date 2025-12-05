# !/usr/bin/python
# coding=utf-8
"""Setup Maya for remote test execution.

Run this script in Maya's Script Editor to open command ports
and prepare the environment for remote test execution.

Usage (in Maya Script Editor):
    import sys
    sys.path.insert(0, r'O:\Cloud\Code\_scripts\mayatk\test')
    import setup_maya_for_tests
    setup_maya_for_tests.setup()
"""
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
    raise


def setup(python_port: int = 7002, mel_port: int = 7001) -> None:
    """Setup Maya for remote test execution.

    Opens command ports and ensures mayatk is importable.

    Args:
        python_port: Port number for Python commands (default: 7002)
        mel_port: Port number for MEL commands (default: 7001)
    """
    print("=" * 70)
    print("Setting up Maya for Remote Test Execution")
    print("=" * 70)

    # Open command ports
    print(f"\n1. Opening command ports...")

    # Close existing ports if open
    for port_name in [f":{python_port}", f":{mel_port}"]:
        try:
            pm.commandPort(name=port_name, close=True)
            print(f"   ✓ Closed existing port {port_name}")
        except RuntimeError:
            pass  # Port wasn't open

    # Open new ports
    try:
        pm.commandPort(name=f":{python_port}", sourceType="python")
        print(f"   ✓ Opened Python port :{python_port}")
    except RuntimeError as error:
        print(f"   ✗ Failed to open Python port: {error}")

    try:
        pm.commandPort(name=f":{mel_port}", sourceType="mel")
        print(f"   ✓ Opened MEL port :{mel_port}")
    except RuntimeError as error:
        print(f"   ✗ Failed to open MEL port: {error}")

    # Verify mayatk is importable
    print(f"\n2. Verifying mayatk installation...")
    try:
        import mayatk

        print(f"   ✓ mayatk version {mayatk.__version__} loaded")
        print(f"   ✓ Package path: {mayatk.__file__}")
    except ImportError as error:
        print(f"   ✗ Failed to import mayatk: {error}")
        print("\n   Add mayatk to your Python path:")
        print("   import sys")
        print("   sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts')")
        return

    # Test basic functionality
    print(f"\n3. Testing basic mayatk functionality...")
    try:
        # Test that we can access core utilities
        from mayatk import CoreUtils

        print(f"   ✓ CoreUtils accessible")

        # Test diagnostics
        from mayatk import MeshDiagnostics, AnimCurveDiagnostics

        print(f"   ✓ MeshDiagnostics accessible")
        print(f"   ✓ AnimCurveDiagnostics accessible")

    except ImportError as error:
        print(f"   ✗ Import error: {error}")
    except Exception as error:
        print(f"   ✗ Error: {error}")

    print(f"\n{'=' * 70}")
    print("Maya is ready for remote test execution!")
    print(f"{'=' * 70}")
    print(f"\nRun tests from your IDE with:")
    print(f"  python maya_test_runner.py")
    print(f"\nOr run specific tests:")
    print(f"  python maya_test_runner.py core_utils_test.py")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    setup()

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
# This script should be run in Maya's Script Editor or via mayapy.
# It prepares Maya to receive test commands via command port.
# -----------------------------------------------------------------------------
