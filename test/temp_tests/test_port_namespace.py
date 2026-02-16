"""Quick test to find persistent namespace across Maya command port connections."""

import socket
import time


def send(code):
    s = socket.socket()
    s.settimeout(5)
    s.connect(("localhost", 7002))
    s.sendall(code.encode())
    data = b""
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\x00" in chunk:
                break
        except socket.timeout:
            break
    s.close()
    return data.decode().replace("\x00", "").strip()


# Test 1: Plain global (expected to fail across connections)
r = send("_plain_var = 42")
print(f"Set plain: {r!r}")
time.sleep(0.3)
r = send("_plain_var")
print(f"Read plain: {r!r}")

# Test 2: __main__ namespace
r = send("import __main__\n__main__._test_val = 'hello_world'")
print(f"\nSet __main__: {r!r}")
time.sleep(0.3)
r = send("__import__('__main__')._test_val")
print(f"Read __main__: {r!r}")

# Test 3: sys.modules trick
r = send(
    "import sys\nsys.modules.setdefault('_mayatk_ns', type(sys)('_mayatk_ns'))\nsys.modules['_mayatk_ns'].output = 'captured!'"
)
print(f"\nSet sys.modules: {r!r}")
time.sleep(0.3)
r = send("__import__('sys').modules['_mayatk_ns'].output")
print(f"Read sys.modules: {r!r}")

# Test 4: builtins
r = send("import builtins\nbuiltins._mayatk_result = 'from_builtins'")
print(f"\nSet builtins: {r!r}")
time.sleep(0.3)
r = send("__import__('builtins')._mayatk_result")
print(f"Read builtins: {r!r}")
