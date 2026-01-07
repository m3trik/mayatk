import sys
import os

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import pythontk as ptk

print("LogHandler" in dir(ptk))
try:
    print(ptk.LogHandler)
except AttributeError:
    print("ptk.LogHandler not found")
