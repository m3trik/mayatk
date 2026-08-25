#!/usr/bin/python
# coding=utf-8
"""Shadow doctor for embedded-DCC installs (companion of package-manager.bat).

A ``pip install`` without elevation falls back to the USER site, which sits on
``sys.path`` AHEAD of the interpreter's own site-packages -- so a stray copy of
a dist the host bundles (shiboken6, PySide6, numpy in mayapy) silently overrides
the host's, and every Qt import can die with ``DLL load failed while importing
QtWidgets`` (the 2026-08-24 m3trik-desktop incident). pip cannot warn about
this; this scan does.

Read-only: prints ``[!!]`` lines for each user-site dist that shadows a
DIFFERENT version of the same dist elsewhere on ``sys.path``; silent when
clean. Always exits 0 -- it advises, it never blocks.

SSoT: ``m3trik/pm_doctor.py``, mirrored next to each ``package-manager.bat``
copy by ``m3trik/scripts/sync_shared_bat.py``.
"""

import os
import site
import sys
from importlib import metadata

# Packaging tooling is EXPECTED to be newer in the user site (``pip install
# --upgrade pip`` lands there without elevation) and shadowing it is harmless --
# flagging it would train users to ignore the warning. Runtime libs are the signal.
_TOOLING = {"pip", "setuptools", "wheel"}


def find_shadows():
    """``[(name, user_site_version, bundled_version)]`` for shadowed dists."""
    try:
        user_site = os.path.normcase(os.path.normpath(site.getusersitepackages()))
    except Exception:
        return []
    seen = {}
    for dist in metadata.distributions():
        try:
            name = (dist.metadata["Name"] or "").lower()
            loc = os.path.normcase(os.path.normpath(str(dist.locate_file(""))))
            ver = dist.version
        except Exception:
            continue
        if name and name not in _TOOLING:
            seen.setdefault(name, []).append((loc, ver))
    findings = []
    for name, copies in sorted(seen.items()):
        user = [c for c in copies if c[0].startswith(user_site)]
        base = [c for c in copies if not c[0].startswith(user_site)]
        if user and base and user[0][1] != base[0][1]:
            findings.append((name, user[0][1], base[0][1]))
    return findings


def main():
    findings = find_shadows()
    if findings:
        print("")
        print("  [!!] User-site packages shadow this interpreter's own copies:")
        for name, user_ver, base_ver in findings:
            print(
                "       %s: user-site %s overrides bundled %s"
                % (name, user_ver, base_ver)
            )
        print("       If the host app misbehaves (Qt 'DLL load failed', numpy")
        print("       version warnings), remove the user-site copy with:")
        print('         "%s" -m pip uninstall <name>' % sys.executable)
    return 0


if __name__ == "__main__":
    sys.exit(main())
