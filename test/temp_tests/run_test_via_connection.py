import sys
import os
import time

# Ensure local packages are found
sys.path.insert(0, r"o:\Cloud\Code\_scripts\mayatk")
sys.path.insert(0, r"o:\Cloud\Code\_scripts\pythontk")
sys.path.insert(0, r"o:\Cloud\Code\_scripts\uitk")

try:
    from mayatk.env_utils.maya_connection import MayaConnection
except ImportError as e:
    print(f"Failed to import MayaConnection: {e}")
    sys.exit(1)


def run():
    print("Initializing Maya Connection...")
    conn = MayaConnection.get_instance()

    # Try to connect, launching if necessary
    # Note: connect(mode='port', launch=True) will look for maya.exe
    if not conn.connect(mode="port", launch=True, force_new_instance=True):
        print("Failed to connect/launch Maya.")
        sys.exit(1)

    print("Connected to Maya.")

    # Read the test script
    test_script_path = (
        r"o:\Cloud\Code\_scripts\mayatk\test\temp_tests\check_channel_box_selection.py"
    )
    with open(test_script_path, "r") as f:
        script_content = f.read()

    # Append the function call to ensure it runs
    script_content += "\n\ntest_channel_box_selection()"

    print(f"Executing test script: {test_script_path}")

    try:
        # execute() with capture_output=True should return stdout/stderr from Maya
        output = conn.execute(script_content, capture_output=True, timeout=120)
        print("\n--- Maya Output ---\n")
        print(output)
        print("\n-------------------\n")

        # Also check the output file just in case the capture missed something or the script wrote to it
        output_file = r"o:\Cloud\Code\_scripts\mayatk\test\temp_tests\test_output.txt"
        if os.path.exists(output_file):
            print(f"\n--- Output File ({output_file}) ---\n")
            with open(output_file, "r") as f:
                print(f.read())
            print("\n----------------------------------\n")

    except Exception as e:
        print(f"Error executing script: {e}")
        import traceback

        traceback.print_exc()

    # Optional: Disconnect/Shutdown?
    # conn.disconnect()


if __name__ == "__main__":
    run()
