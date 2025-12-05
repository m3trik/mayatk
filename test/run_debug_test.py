#!/usr/bin/env python
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_debug_test.txt'
try:
    from pythontk.core_utils.module_reloader import reload_package
    reload_package('mayatk', import_missing=False, verbose=0)
    
    import mayatk
    
    with open(output_file, 'w') as f:
        f.write("Debugging CLASS_TO_MODULE\\n")
        f.write("="*70 + "\\n\\n")
        
        if hasattr(mayatk, 'CLASS_TO_MODULE'):
            f.write(f"Total entries: {len(mayatk.CLASS_TO_MODULE)}\\n\\n")
            f.write("All mappings:\\n")
            for k, v in sorted(mayatk.CLASS_TO_MODULE.items()):
                f.write(f"  {k} -> {v}\\n")
        
        f.write("\\n" + "="*70 + "\\n")
        f.write("Checking module structure\\n")
        f.write("="*70 + "\\n\\n")
        
        # Check if diagnostics module exists
        import sys
        diag_modules = [k for k in sys.modules.keys() if 'diagnostic' in k]
        f.write(f"Modules with 'diagnostic': {diag_modules}\\n\\n")
        
        # Try to import directly
        try:
            from mayatk.core_utils.diagnostics import mesh
            f.write(f"Direct import of mesh module: {mesh}\\n")
            f.write(f"mesh.MeshDiagnostics: {mesh.MeshDiagnostics}\\n")
        except Exception as e:
            f.write(f"Failed to import mesh: {e}\\n")
        
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("Debugging CLASS_TO_MODULE...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(3)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_debug_test.txt", "r") as f:
        print(f.read())
except FileNotFoundError:
    print("File not created")
