#!/usr/bin/env python
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_attr_test.txt'
try:
    import mayatk
    with open(output_file, 'w') as f:
        f.write("Testing getattr on mayatk...\\n")
        
        # Try to get MeshDiagnostics
        try:
            md = getattr(mayatk, 'MeshDiagnostics')
            f.write(f"✓ MeshDiagnostics: {md}\\n")
        except Exception as e:
            import traceback
            f.write(f"✗ MeshDiagnostics failed:\\n")
            f.write(traceback.format_exc())
            
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("OUTER EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("Testing attribute access...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(3)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_attr_test.txt", "r") as f:
        print(f.read())
except FileNotFoundError:
    print("File not created")
