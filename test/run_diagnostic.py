"""Diagnostic test - write output to file instead of just Script Editor."""

import socket
import time

code = """
import sys
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts')

# Write to diagnostic file
diag_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\diagnostic.txt'

with open(diag_file, 'w') as f:
    f.write("="*70 + "\\n")
    f.write("DIAGNOSTIC TEST\\n")
    f.write("="*70 + "\\n\\n")
    
    # Test 1: Can we import ModuleReloader?
    try:
        from pythontk import ModuleReloader
        f.write("[OK] Imported ModuleReloader\\n")
    except Exception as e:
        f.write(f"[ERROR] Failed to import ModuleReloader: {e}\\n")
        raise
    
    # Test 2: Can we create a reloader?
    try:
        reloader = ModuleReloader(include_submodules=True)
        f.write("[OK] Created ModuleReloader instance\\n")
    except Exception as e:
        f.write(f"[ERROR] Failed to create reloader: {e}\\n")
        raise
    
    # Test 3: Can we reload pythontk?
    try:
        count = reloader.reload('pythontk')
        f.write(f"[OK] Reloaded {count} pythontk modules\\n")
    except Exception as e:
        f.write(f"[ERROR] Failed to reload pythontk: {e}\\n")
        import traceback
        f.write(traceback.format_exc())
        raise
    
    # Test 4: Check if new code is loaded
    try:
        from pythontk.img_utils import texture_map_factory
        
        if hasattr(texture_map_factory.TextureMapFactory, '_apply_workflow'):
            f.write("[ERROR] OLD CODE LOADED - has _apply_workflow\\n")
        else:
            f.write("[OK] NEW CODE LOADED - no _apply_workflow\\n")
            
        if hasattr(texture_map_factory.TextureMapFactory, 'prepare_maps'):
            f.write("[OK] Has prepare_maps method\\n")
        else:
            f.write("[ERROR] Missing prepare_maps method\\n")
            
    except Exception as e:
        f.write(f"[ERROR] Failed to check module: {e}\\n")
        import traceback
        f.write(traceback.format_exc())
        raise
    
    f.write("\\n[SUCCESS] All diagnostic tests passed\\n")
    
print("Diagnostic complete - check diagnostic.txt")
"""

# Connect and send
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)

try:
    sock.connect(("localhost", 7002))
    print("Connected to Maya")
    sock.send((code + "\n").encode("utf-8"))
    print("Diagnostic code sent")
    time.sleep(3)
    print("\nCheck o:\\Cloud\\Code\\_scripts\\mayatk\\test\\diagnostic.txt for results")
finally:
    sock.close()
