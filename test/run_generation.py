import socket
import sys
import time

print("Starting run_generation.py...")


def send_code(code, host="localhost", port=7002):
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(10)
        client.connect((host, port))
        client.sendall(code.encode("utf-8"))
        client.close()
        print("Code sent to Maya.")
        return True
    except Exception as e:
        print(f"Failed to send code: {e}")
        return False


code = """
def log_debug(msg):
    try:
        with open(r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\generation_debug_v2.log', 'a') as f:
            f.write(str(msg) + "\\n")
    except:
        pass

log_debug("DEBUG: STARTING GENERATION SCRIPT")

import sys
import os
import importlib
# Ensure path is present
path = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test'
if path not in sys.path:
    sys.path.insert(0, path)

try:
    # Force reload of dependencies
    keys = [k for k in sys.modules.keys() if 'shader_templates' in k]
    log_debug(f"DEBUG: Found shader_templates modules: {keys}")
    for k in keys:
        del sys.modules[k]
        
    import mayatk.mat_utils.shader_templates._shader_templates as st_module
    log_debug("DEBUG: Reloaded _shader_templates")

    if 'generate_templates' in sys.modules:
        del sys.modules['generate_templates']
    import generate_templates as gen
    importlib.reload(gen)
    log_debug("DEBUG: Imported generate_templates from " + str(gen.__file__))

    gen.generate_templates()
    log_debug("DEBUG: Templates generated successfully.")
except Exception as e:
    log_debug(f"DEBUG: Error generating templates: {e}")
    import traceback
    log_debug(traceback.format_exc())
"""

if __name__ == "__main__":
    send_code(code)
