"""Reproduction script for Menu order issue."""

import socket

code = """
from qtpy import QtWidgets
from uitk.widgets.menu import Menu

try:
    p = QtWidgets.QWidget()
    m = Menu(p, fixed_item_height=20)

    def add_items(prefix=""):
        m.add("QPushButton", setText=f"{prefix}0. Refresh")
        m.add("Separator")
        m.add("QPushButton", setText=f"{prefix}2. Save")
        m.add("QComboBox")
        m.add("QLineEdit")
        m.add("QCheckBox")
        m.add("Separator")
        m.add("QPushButton", setText=f"{prefix}7. Convert")
        m.add("QPushButton", setText=f"{prefix}8. Unlink")
        m.add("QPushButton", setText=f"{prefix}9. Unref")
        m.add("Separator")
        m.add("QCheckBox")

    print("=" * 40)
    print("REPRO MENU ORDER TEST")
    print("=" * 40)

    print("--- First Add ---")
    add_items("A")
    
    g = m.gridLayout
    for i in range(g.count()):
        item = g.itemAt(i)
        if item and item.widget():
            w = item.widget()
            r, c, _, _ = g.getItemPosition(i)
            txt = w.text() if hasattr(w, 'text') and callable(w.text) else type(w).__name__
            print(f"[{i:2d}] r={r:2d}: {txt}")

    print("--- Clear ---")
    m.clear()
    print(f"Count after clear: {g.count()}")

    print("--- Second Add ---")
    add_items("B")
    
    for i in range(g.count()):
        item = g.itemAt(i)
        if item and item.widget():
            w = item.widget()
            r, c, _, _ = g.getItemPosition(i)
            txt = w.text() if hasattr(w, 'text') and callable(w.text) else type(w).__name__
            print(f"[{i:2d}] r={r:2d}: {txt}")

    p.deleteLater()
    print("=" * 40)

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
"""

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", 7002))
    sock.sendall(code.encode("utf-8") + b"\n")
    print("Sent repro script to Maya")
    sock.close()
except Exception as e:
    print(f"Connection failed: {e}")
