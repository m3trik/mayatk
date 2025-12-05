#!/usr/bin/env python
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_openports_test.txt'
try:
    from pythontk.core_utils.module_reloader import reload_package
    reload_package('mayatk', import_missing=False, verbose=0)
    
    import mayatk
    
    with open(output_file, 'w') as f:
        # Check if openPorts is registered
        if hasattr(mayatk, 'CLASS_TO_MODULE'):
            if 'openPorts' in mayatk.CLASS_TO_MODULE:
                f.write(f"openPorts -> {mayatk.CLASS_TO_MODULE['openPorts']}\\n")
            else:
                f.write("openPorts NOT in CLASS_TO_MODULE\\n")
        
        # Try direct import
        try:
            from mayatk.env_utils.command_port import openPorts as op_direct
            f.write(f"Direct import works: {op_direct}\\n")
        except Exception as e:
            f.write(f"Direct import failed: {e}\\n")
        
        # Try via mayatk
        try:
            op = mayatk.openPorts
            f.write(f"Via mayatk: {op}\\n")
        except AttributeError as e:
            f.write(f"Via mayatk failed: {e}\\n")
        
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("Testing openPorts...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(2)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_openports_test.txt", "r") as f:
        print(f.read())
except FileNotFoundError:
    print("File not created")
