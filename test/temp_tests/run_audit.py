import sys
import os
from pathlib import Path

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from mayatk.env_utils import maya_connection


def run_audit():
    connection = maya_connection.MayaConnection()
    if not connection.connect(mode="auto", port=7002):
        print("Failed to connect to Maya")
        return

    code = """
import sys
import os
import unittest

# Ensure paths
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts')
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts\\mayatk\\test')

# Import the test module
import temp_tests.repro_user_scene as audit
from importlib import reload
reload(audit)

print("="*70)
print("RUNNING SCENE AUDIT")
print("="*70)

# Run the tests
suite = unittest.TestLoader().loadTestsFromModule(audit)
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

if result.wasSuccessful():
    print("[PASS] Audit successful")
else:
    print("[FAIL] Audit failed")
"""
    print("Sending audit code to Maya...")
    connection.execute(code)
    print("Done.")


if __name__ == "__main__":
    run_audit()
