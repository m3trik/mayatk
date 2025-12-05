#!/usr/bin/env python
# Quick connection test
import socket
import sys


def test_maya_connection(host="localhost", port=7002):
    """Test if Maya command port is open and responsive."""
    print(f"Testing connection to Maya at {host}:{port}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))

        # Send simple test command
        test_code = "print('Maya connection test successful!')\n"
        sock.sendall(test_code.encode("utf-8"))

        # Receive response
        response = sock.recv(4096).decode("utf-8", errors="replace").rstrip("\x00")
        sock.close()

        print(f"✓ Connected successfully!")
        print(f"Response: {response}")
        return True

    except ConnectionRefusedError:
        print(f"✗ Connection refused - Maya command port is not open")
        print(f"\nTo open the port, run this in Maya:")
        print(f"  import mayatk")
        print(f"  mayatk.openPorts(python=':{port}')")
        return False

    except socket.timeout:
        print(f"✗ Connection timeout")
        return False

    except Exception as e:
        print(f"✗ Error: {e}")
        return False


if __name__ == "__main__":
    success = test_maya_connection()
    sys.exit(0 if success else 1)
