import socket


def send_code(code, host="localhost", port=7002):
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect((host, port))
        client.sendall(code.encode("utf-8"))
        client.close()
        print("Code sent.")
    except Exception as e:
        print(f"Error: {e}")


send_code(
    "with open(r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\check_maya_log.txt', 'w') as f: f.write('Maya is alive')"
)
