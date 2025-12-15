"""Reload modules in Maya to pick up code changes."""

import socket
import time

# Command to reload modules
reload_command = """
import sys
import importlib

# Reload pythontk modules
if 'pythontk.img_utils.texture_map_factory' in sys.modules:
    from pythontk.img_utils import texture_map_factory
    importlib.reload(texture_map_factory)
    print("✓ Reloaded texture_map_factory")
    
if 'mayatk.mat_utils.stingray_arnold_shader' in sys.modules:
    from mayatk.mat_utils import stingray_arnold_shader
    importlib.reload(stingray_arnold_shader)
    print("✓ Reloaded stingray_arnold_shader")

print("Module reload complete")
"""

# Connect to Maya
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)

try:
    sock.connect(("localhost", 7002))
    print("Connected to Maya on localhost:7002")

    # Send reload command
    sock.send((reload_command + "\n").encode("utf-8"))
    print("Sent reload command")

    # Wait for response
    time.sleep(2)
    response = sock.recv(4096).decode("utf-8", errors="ignore")
    print("\nMaya response:")
    print(response)

finally:
    sock.close()
