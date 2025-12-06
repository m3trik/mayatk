"""Simple test to verify Maya command port and module reloading."""

import socket
import time

code = """
import sys
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts')

print("="*70)
print("MODULE RELOAD TEST")
print("="*70)

# Test ModuleReloader
try:
    from pythontk import ModuleReloader
    print("[OK] Imported ModuleReloader")
    
    reloader = ModuleReloader(include_submodules=True)
    print("[OK] Created reloader instance")
    
    # Reload pythontk
    count = reloader.reload('pythontk')
    print(f"[OK] Reloaded {count} pythontk modules")
    
    # Check if texture_map_factory has the new code
    from pythontk.img_utils import texture_map_factory
    if hasattr(texture_map_factory.TextureMapFactory, '_apply_workflow'):
        print("[ERROR] OLD CODE LOADED - has _apply_workflow method")
    else:
        print("[OK] NEW CODE LOADED - no _apply_workflow method")
        
    # Check if we have prepare_maps
    if hasattr(texture_map_factory.TextureMapFactory, 'prepare_maps'):
        print("[OK] Has prepare_maps static method")
    else:
        print("[ERROR] Missing prepare_maps method")
        
    print("\\n[SUCCESS] Module reload test complete")
    
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()

print("="*70)
"""

# Connect and send
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)

try:
    sock.connect(("localhost", 7002))
    print("Connected to Maya")
    sock.send((code + "\n").encode("utf-8"))
    print("Code sent - check Maya Script Editor for output")
    time.sleep(3)
finally:
    sock.close()
