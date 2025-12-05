#!/usr/bin/env python
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_lazy_load_test.txt'
with open(output_file, 'w') as f:
    import mayatk
    
    f.write("="*70 + "\\n")
    f.write("Testing Lazy-Loaded Imports\\n")
    f.write("="*70 + "\\n\\n")
    
    # Test each import
    tests = [
        "MeshDiagnostics",
        "AnimCurveDiagnostics",
        "CoreUtils",
        "openPorts",
        "Selection",
        "Components",
    ]
    
    for name in tests:
        try:
            obj = getattr(mayatk, name)
            f.write(f"  ✓ mayatk.{name}: {type(obj).__name__}\\n")
        except AttributeError as e:
            f.write(f"  ✗ mayatk.{name}: AttributeError - {e}\\n")
        except Exception as e:
            f.write(f"  ? mayatk.{name}: {type(e).__name__} - {e}\\n")
    
    # Test DEFAULT_INCLUDE is working
    f.write("\\n" + "="*70 + "\\n")
    f.write("Resolver Configuration\\n")
    f.write("="*70 + "\\n")
    if hasattr(mayatk, 'CLASS_TO_MODULE'):
        f.write(f"CLASS_TO_MODULE entries: {len(mayatk.CLASS_TO_MODULE)}\\n")
        f.write("\\nSample mappings:\\n")
        for i, (k, v) in enumerate(list(mayatk.CLASS_TO_MODULE.items())[:10]):
            f.write(f"  {k} -> {v}\\n")
    else:
        f.write("CLASS_TO_MODULE not found!\\n")
    
    f.write("\\n" + "="*70 + "\\n")
"""

print("Testing lazy-loaded imports in Maya...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(2)

with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_lazy_load_test.txt", "r") as f:
    print(f.read())
