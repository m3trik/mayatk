# coding=utf-8
"""The mayatk surface imports without Maya (``mayatk/CLAUDE.md``, *Imports*).

Every module try-guards its ``maya.*`` imports so the registry, docs tooling
and these mock tests can load it on a machine with no Maya. The probe runs in a
FRESH interpreter: this directory's conftest plants mock ``maya`` modules in
``sys.modules``, which would hide an unguarded import from an in-process test.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REAL_MAYA_LOADED = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "mayatk"

# Modules that cannot load without Maya by construction. Keep it short and say
# why: for anything else the fix is the guarded import, not an entry here.
MAYA_BOUND = {
    "mayatk.env_utils.script_output": (
        "ScriptConsole subclasses maya.app.general.mayaMixin."
        "MayaQWidgetDockableMixin at class definition"
    ),
}

_PROBE = r"""
import importlib, json, sys
failed = {}
for name in json.loads(sys.stdin.read()):
    try:
        importlib.import_module(name)
    except BaseException as error:
        failed[name] = f"{type(error).__name__}: {error}"
print("@@RESULT@@" + json.dumps(failed))
"""


def _surface_modules():
    """Every mayatk module, minus host-run templates and uitk's ui caches."""
    names = []
    for path in sorted(_PKG.rglob("*.py")):
        parts = list(path.relative_to(_REPO).with_suffix("").parts)
        if "templates" in parts:
            continue  # exec-templates: they run inside Blender / Marmoset
        stem = path.stem
        if stem.endswith("_ui") and path.with_name(stem[:-3] + ".ui").exists():
            continue  # the gitignored compile cache uitk writes beside a .ui
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if all(part.isidentifier() for part in parts):
            names.append(".".join(parts))
    return names


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest("Mock-based suite -- skipped when real Maya is loaded.")


class TestImportSurface(unittest.TestCase):
    def test_every_module_imports_without_maya(self):
        names = [name for name in _surface_modules() if name not in MAYA_BOUND]
        self.assertGreater(len(names), 100, "the package walk found nothing")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in (str(_REPO), env.get("PYTHONPATH", "")) if path
        )
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE],
            input=json.dumps(names),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(_REPO),
            timeout=900,
        )
        marker = [
            line for line in proc.stdout.splitlines() if line.startswith("@@RESULT@@")
        ]
        self.assertTrue(
            marker, f"the probe died (rc={proc.returncode}):\n{proc.stderr[-3000:]}"
        )
        failed = json.loads(marker[-1][len("@@RESULT@@") :])
        self.assertEqual(
            failed,
            {},
            "module(s) that need Maya to import -- try-guard the maya imports:\n"
            + "\n".join(f"  {name}: {error}" for name, error in sorted(failed.items())),
        )

    def test_maya_bound_entries_name_real_modules(self):
        stale = sorted(set(MAYA_BOUND) - set(_surface_modules()))
        self.assertEqual(stale, [], "MAYA_BOUND names a module that no longer exists")


if __name__ == "__main__":
    unittest.main()
