"""Quick debug: check env vars and grep_mel_procs behavior in Maya."""

import sys, os

scripts_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
for pkg in ("mayatk", "pythontk", "uitk"):
    p = os.path.join(scripts_root, pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

from mayatk.env_utils.maya_connection import MayaConnection

conn = MayaConnection.get_instance()
conn.connect(mode="port", port=7002)

print("=== ENV VARS ===")
r = conn.execute(
    "import os\n"
    "print('MAYA_LOCATION:', os.environ.get('MAYA_LOCATION', '(not set)'))\n"
    "print('MAYA_SCRIPT_PATH (first 500):', os.environ.get('MAYA_SCRIPT_PATH', '(not set)')[:500])\n",
    capture_output=True,
    timeout=10,
)
print(r)

print("\n=== mel.eval('env') type ===")
r = conn.execute(
    "import maya.mel as mel\n"
    "raw = mel.eval('env')\n"
    "print('type:', type(raw).__name__)\n"
    "if isinstance(raw, list):\n"
    "    print('len:', len(raw))\n"
    "    for v in raw[:5]: print(' ', v)\n"
    "else:\n"
    "    print('val:', repr(raw[:200]))\n",
    capture_output=True,
    timeout=10,
)
print(r)

print("\n=== grep_mel_procs debug ===")
r = conn.execute(
    "import os, sys\n"
    "scripts_root = r'O:\\Cloud\\Code\\_scripts'\n"
    "for pkg in ('mayatk','pythontk','uitk'):\n"
    "    p = os.path.join(scripts_root, pkg)\n"
    "    if p not in sys.path: sys.path.insert(0, p)\n"
    "to_del = [k for k in sys.modules if k.startswith('mayatk')]\n"
    "for k in to_del: del sys.modules[k]\n"
    "to_del = [k for k in sys.modules if k.startswith('pythontk')]\n"
    "for k in to_del: del sys.modules[k]\n"
    "import mayatk\n"
    "from mayatk.env_utils.devtools import DevTools\n"
    "try:\n"
    "    procs = DevTools.grep_mel_procs('channelBox')\n"
    "    print(f'Found {len(procs)} procs')\n"
    "    for p in procs[:3]:\n"
    "        print(f'  {p}')\n"
    "except Exception as e:\n"
    "    print(f'ERROR: {e}')\n"
    "    import traceback; traceback.print_exc()\n",
    capture_output=True,
    timeout=60,
)
print(r)

print("\n=== read_mel_proc debug ===")
r = conn.execute(
    "from mayatk.env_utils.devtools import DevTools\n"
    "try:\n"
    "    src = DevTools.read_mel_proc('selectedChannelBoxAttributes')\n"
    "    if src:\n"
    "        print(f'Found {len(src)} chars')\n"
    "        print(src[:200])\n"
    "    else:\n"
    "        print('Not found')\n"
    "except Exception as e:\n"
    "    print(f'ERROR: {e}')\n"
    "    import traceback; traceback.print_exc()\n",
    capture_output=True,
    timeout=30,
)
print(r)
