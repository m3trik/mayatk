#!/usr/bin/env python
import socket
import time

# Very simple test - just try to import mayatk
test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_basic_test.txt'
with open(output_file, 'w') as f:
    f.write("Starting test...\\n")
    try:
        import mayatk
        f.write(f"SUCCESS: mayatk imported, version {mayatk.__version__}\\n")
    except Exception as e:
        import traceback
        f.write(f"FAILED to import mayatk\\n")
        f.write(f"Error: {e}\\n")
        f.write("\\nTraceback:\\n")
        f.write(traceback.format_exc())
"""

print("Sending basic import test to Maya...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(2)

# Read results
try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_basic_test.txt", "r") as f:
        content = f.read()
        print(content)
        if "SUCCESS" in content:
            print("\n✓ Maya can import mayatk!")
        else:
            print("\n✗ Maya cannot import mayatk - see error above")
except FileNotFoundError:
    print("✗ Test file not created")
