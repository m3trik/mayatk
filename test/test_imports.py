#!/usr/bin/env python
# Test mayatk imports in Maya
import socket
import sys


def run_maya_code(code, host="localhost", port=7002):
    """Execute code in Maya and return output."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((host, port))
    sock.sendall(code.encode("utf-8"))

    response = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
        if b"\x00" in chunk:
            break

    sock.close()
    return response.decode("utf-8", errors="replace").rstrip("\x00")


# Test code to run in Maya
test_code = """
import sys
print("="*70)
print("Testing mayatk Import Structure")
print("="*70)

# Test basic import
try:
    import mayatk
    print(f"✓ mayatk imported - version {mayatk.__version__}")
except Exception as e:
    print(f"✗ Failed to import mayatk: {e}")
    sys.exit(1)

# Test lazy-loaded classes
tests = [
    ("MeshDiagnostics", "from mayatk import MeshDiagnostics"),
    ("AnimCurveDiagnostics", "from mayatk import AnimCurveDiagnostics"),
    ("CoreUtils", "from mayatk import CoreUtils"),
    ("openPorts", "from mayatk import openPorts"),
    ("Selection", "from mayatk import Selection"),
    ("Components", "from mayatk import Components"),
]

print("\\nTesting lazy-loaded imports:")
for name, code in tests:
    try:
        exec(code)
        print(f"  ✓ {name}")
    except AttributeError as e:
        print(f"  ✗ {name}: {e}")
    except Exception as e:
        print(f"  ? {name}: {e}")

# Test that we can access methods
print("\\nTesting method access:")
try:
    print(f"  ✓ MeshDiagnostics.clean_geometry: {callable(MeshDiagnostics.clean_geometry)}")
    print(f"  ✓ AnimCurveDiagnostics.repair_corrupted_curves: {callable(AnimCurveDiagnostics.repair_corrupted_curves)}")
except Exception as e:
    print(f"  ✗ Error: {e}")

# Show what's available
print("\\nRegistered names in mayatk:")
if hasattr(mayatk, 'CLASS_TO_MODULE'):
    print(f"  Classes: {len(mayatk.CLASS_TO_MODULE)}")
    print(f"  Sample: {list(mayatk.CLASS_TO_MODULE.keys())[:5]}")

print("="*70)
print("Test Complete")
print("="*70)
"""

if __name__ == "__main__":
    print("Executing import tests in Maya...\n")
    try:
        output = run_maya_code(test_code)
        print(output)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
