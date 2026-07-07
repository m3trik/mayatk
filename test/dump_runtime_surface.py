#!/usr/bin/env mayapy
# coding=utf-8
"""Dump mayatk's live HelpMixin class surface for the runtime-vs-static drift gate.

Runs under mayapy in a FRESH headless standalone Maya (``maya.standalone``) — it
never attaches to a running session, so it is safe per the session-safety rule.
This is the DCC half of the drift gate: the static registry walker cannot import
Maya, so the live surface (which sees metaclass/mixin-injected members) is dumped
here and diffed against the committed registry from a normal shell.

    & "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" mayatk\\test\\dump_runtime_surface.py
    python m3trik/scripts/verify_runtime_surface.py verify mayatk --runtime mayatk/API_RUNTIME.json

Writes ``mayatk/API_RUNTIME.json`` (gitignored build artifact — never committed).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Full ecosystem on path so mayatk's uitk-backed Slots classes resolve.
for _name in ("mayatk", "pythontk", "uitk", "tentacle"):
    _p = REPO / _name
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(REPO / "m3trik" / "scripts"))


def main() -> int:
    import maya.standalone

    maya.standalone.initialize()
    try:
        import verify_runtime_surface as v

        return v.main(["dump", "mayatk"])
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
