#!/usr/bin/env python
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_reload_test.txt'
try:
    # Use pythontk's module reloader
    from pythontk.core_utils.module_reloader import reload_package
    
    # Define predicate to skip problematic modules
    def skip_ui_modules(mod):
        # Skip UI modules that might have import issues
        name = mod.__name__
        skip_patterns = ['_ui', 'reference_manager_ui']
        return not any(pattern in name for pattern in skip_patterns)
    
    # Reload mayatk and all submodules (only those already imported)
    reloaded = reload_package('mayatk', import_missing=False, verbose=1)
    
    # Now test
    import mayatk
    
    with open(output_file, 'w') as f:
        f.write(f"Reloaded {len(reloaded)} modules\\n")
        f.write(f"Version: {mayatk.__version__}\\n\\n")
        
        # Test MeshDiagnostics
        try:
            md = getattr(mayatk, 'MeshDiagnostics')
            f.write(f"SUCCESS: MeshDiagnostics = {md}\\n")
        except Exception as e:
            import traceback
            f.write(f"FAILED: MeshDiagnostics\\n")
            f.write(traceback.format_exc())
            f.write("\\n")
        
        # Test AnimCurveDiagnostics
        try:
            acd = getattr(mayatk, 'AnimCurveDiagnostics')
            f.write(f"SUCCESS: AnimCurveDiagnostics = {acd}\\n")
        except Exception as e:
            f.write(f"FAILED: AnimCurveDiagnostics\\n")
            f.write(str(e) + "\\n")
        
        # Test openPorts
        try:
            op = getattr(mayatk, 'openPorts')
            f.write(f"SUCCESS: openPorts = {op}\\n")
        except Exception as e:
            f.write(f"FAILED: openPorts: {e}\\n")
        
        # Show CLASS_TO_MODULE
        if hasattr(mayatk, 'CLASS_TO_MODULE'):
            f.write(f"\\nCLASS_TO_MODULE has {len(mayatk.CLASS_TO_MODULE)} entries\\n")
            # Check if our classes are in there
            if 'MeshDiagnostics' in mayatk.CLASS_TO_MODULE:
                f.write(f"  MeshDiagnostics -> {mayatk.CLASS_TO_MODULE['MeshDiagnostics']}\\n")
            else:
                f.write("  MeshDiagnostics NOT in CLASS_TO_MODULE\\n")
            if 'AnimCurveDiagnostics' in mayatk.CLASS_TO_MODULE:
                f.write(f"  AnimCurveDiagnostics -> {mayatk.CLASS_TO_MODULE['AnimCurveDiagnostics']}\\n")
            else:
                f.write("  AnimCurveDiagnostics NOT in CLASS_TO_MODULE\\n")
        
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("Reloading mayatk in Maya using pythontk.ModuleReloader...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(4)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_reload_test.txt", "r") as f:
        print(f.read())
except FileNotFoundError:
    print("File not created")
