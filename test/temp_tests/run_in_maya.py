import socket
import sys
import os


def run_script_in_maya(script_path):
    HOST = "127.0.0.1"
    PORT = 7002

    with open(script_path, "r") as f:
        script_content = f.read()
        print(f"Sending script content (first 500 chars): {script_content[:500]}")

    # Wrap in a way that handles output if possible, but for now just send it
    # Maya command port expects the command string.

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((HOST, PORT))
            s.sendall(script_content.encode("utf-8"))
            # We might not get a response depending on how the port is set up (command vs eval)
            # But the script prints to the script editor, which I can't see here.
            # Wait, I need the output.

            # If I can't get output back easily, I should make the script write to a file.
    except ConnectionRefusedError:
        print("Could not connect to Maya on port 7002. Is it running?")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_script_in_maya(sys.argv[1])
    else:
        print("Usage: python run_in_maya.py <script_path>")
