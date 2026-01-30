"""Run preview test via Maya command port"""

import sys

sys.path.insert(0, r"O:\Cloud\Code\_scripts")
from mayatk.env_utils import maya_connection

conn = maya_connection.MayaConnection.get_instance()
if conn.connect(mode="port", port=7002):
    print("[OK] Connected to Maya")

    # Read the test file and execute it
    test_file = r"O:\Cloud\Code\_scripts\mayatk\test\temp_tests\run_preview_test.py"
    with open(test_file, "r") as f:
        code = f.read()

    print("[INFO] Sending test code to Maya with capture_output...")
    result = conn.execute(code, capture_output=True)
    print(f"\n{'='*60}")
    print("CAPTURED OUTPUT FROM MAYA:")
    print("=" * 60)
    print(result if result else "(No output captured)")
    print("=" * 60)

    # Also check if output file was created
    output_file = (
        r"O:\Cloud\Code\_scripts\mayatk\test\temp_tests\preview_test_output.txt"
    )
    try:
        with open(output_file, "r") as f:
            print("\nFILE OUTPUT:")
            print("=" * 60)
            print(f.read())
    except FileNotFoundError:
        print("\n[INFO] Output file was not created")
else:
    print("[ERROR] Could not connect to Maya")
