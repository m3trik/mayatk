#!/usr/bin/env python
# Test mayatk imports with file output
import socket

test_code = """
import sys
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_test_output.txt'

with open(output_file, 'w') as f:
    f.write("="*70 + "\\n")
    f.write("Testing mayatk Import Structure\\n")
    f.write("="*70 + "\\n\\n")
    
    # Test basic import
    try:
        import mayatk
        f.write(f"✓ mayatk imported - version {mayatk.__version__}\\n")
    except Exception as e:
        f.write(f"✗ Failed to import mayatk: {e}\\n")
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
    
    f.write("\\nTesting lazy-loaded imports:\\n")
    for name, code in tests:
        try:
            exec(code)
            f.write(f"  ✓ {name}\\n")
        except AttributeError as e:
            f.write(f"  ✗ {name}: {e}\\n")
        except Exception as e:
            f.write(f"  ? {name}: {type(e).__name__}: {e}\\n")
    
    # Test method access
    f.write("\\nTesting method access:\\n")
    try:
        from mayatk import MeshDiagnostics, AnimCurveDiagnostics
        f.write(f"  ✓ MeshDiagnostics.clean_geometry: {callable(MeshDiagnostics.clean_geometry)}\\n")
        f.write(f"  ✓ AnimCurveDiagnostics.repair_corrupted_curves: {callable(AnimCurveDiagnostics.repair_corrupted_curves)}\\n")
    except Exception as e:
        f.write(f"  ✗ Error: {e}\\n")
    
    # Show what's available
    f.write("\\nResolver information:\\n")
    if hasattr(mayatk, 'CLASS_TO_MODULE'):
        f.write(f"  Classes registered: {len(mayatk.CLASS_TO_MODULE)}\\n")
        f.write(f"  Sample classes: {list(mayatk.CLASS_TO_MODULE.keys())[:10]}\\n")
    
    f.write("\\n" + "="*70 + "\\n")
    f.write("Test Complete\\n")
    f.write("="*70 + "\\n")

print('Test results written to:', output_file)
"""

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

print("Test code sent to Maya. Waiting for results...")
import time

time.sleep(2)

# Read the results
try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_test_output.txt", "r") as f:
        print(f.read())
except FileNotFoundError:
    print("Output file not created - check Maya for errors")
