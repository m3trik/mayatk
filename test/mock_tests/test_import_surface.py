# coding=utf-8
"""The mayatk surface imports without Maya (``mayatk/CLAUDE.md``, *Imports*).

Every module try-guards its ``maya.*`` imports so the registry, docs tooling
and these mock tests can load it on a machine with no Maya. The probe runs in a
FRESH interpreter: this directory's conftest plants mock ``maya`` modules in
``sys.modules``, which would hide an unguarded import from an in-process test.
"""

import importlib.machinery
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

# Asked of the PATH, not ``sys.modules``: the probe's fresh interpreter is this
# one's executable, so under mayapy (``run_tests.py --mocks``, where the
# conftest's mocks hide the real ``maya``) it would import the real one.
_MAYA_INSTALLED = importlib.machinery.PathFinder.find_spec("maya") is not None

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "mayatk"

# Modules that cannot load without Maya by construction. Keep it short and say
# why: for anything else the fix is the guarded import, not an entry here.
# (``env_utils.script_output`` was one until 2026-10-29: its dockable mixin base
# now has a stand-in outside Maya, so ``mtk.ScriptConsole`` resolves anywhere.)
MAYA_BOUND = {}

# What a Maya install provides (PyMEL is also banned outright): a module that
# cannot find one of these needs Maya to import. Any other missing top-level
# package is an optional third-party one this machine lacks -- scipy for the
# auto instancer, PyYAML for the shader templates -- which fails the same way
# inside Maya, so it is not this test's finding.
_MAYA_ROOTS = frozenset({"maya", "pymel", "ufe", "mtoa", "arnold", "mayaUsd"})

# One pass over the surface, three findings: each module that fails to import,
# each maya name a module answers ``is None`` for (its no-Maya branch) that its
# guard leaves UNBOUND -- the branch then raises NameError instead of
# answering -- and the module whose import created a QApplication.
_PROBE = r"""
import importlib, json, re, sys
checked = re.compile(r"\b(cmds|mel|om|om2|oma|omui|omr)\s+is\s+(?:not\s+)?None\b")
failed, unbound, app_by = {}, {}, None


def _app_exists():
    widgets = sys.modules.get("qtpy.QtWidgets")
    return widgets is not None and widgets.QApplication.instance() is not None


for name in json.loads(sys.stdin.read()):
    try:
        module = importlib.import_module(name)
    except BaseException as error:
        missing = error.name if isinstance(error, ModuleNotFoundError) else None
        failed[name] = [f"{type(error).__name__}: {error}", missing]
        continue
    finally:
        if app_by is None and _app_exists():
            app_by = name
    try:
        with open(module.__file__, encoding="utf-8") as source:
            text = source.read()
    except (AttributeError, OSError, TypeError):
        continue
    names = {
        alias
        for alias in checked.findall(text)
        if re.search(
            rf"^[ \t]+(?:import maya[\w.]* as {alias}|from maya[\w.]* import {alias})\b",
            text,
            re.M,
        )
        and not hasattr(module, alias)
    }
    if names:
        unbound[name] = sorted(names)
print("@@RESULT@@" + json.dumps({"failed": failed, "unbound": unbound, "app": app_by}))
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


def _probe(names):
    """:data:`_PROBE`'s findings over *names*, from a fresh interpreter."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    # Absolute: the child runs in the repo (cwd below), where an entry
    # given relative to this process's directory names nothing.
    inherited = [
        os.path.abspath(path)
        for path in env.get("PYTHONPATH", "").split(os.pathsep)
        if path
    ]
    env["PYTHONPATH"] = os.pathsep.join([str(_REPO)] + inherited)
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
    if not marker:
        raise AssertionError(
            f"the probe died (rc={proc.returncode}):\n{proc.stderr[-3000:]}"
        )
    return json.loads(marker[-1][len("@@RESULT@@") :])


def setUpModule():
    if _MAYA_INSTALLED:
        raise unittest.SkipTest(
            "Needs an interpreter without Maya -- its probe would import the real one."
        )


class TestImportSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = [name for name in _surface_modules() if name not in MAYA_BOUND]
        cls.found = (
            _probe(cls.names)
            if cls.names
            else {"failed": {}, "unbound": {}, "app": None}
        )

    def test_every_module_imports_without_maya(self):
        self.assertGreater(len(self.names), 100, "the package walk found nothing")
        failed = {}
        for name, (error, missing) in self.found["failed"].items():
            root = (missing or "").partition(".")[0]
            if missing and root not in _MAYA_ROOTS and root != "mayatk":
                with self.subTest(module=name):
                    self.skipTest(f"needs {root!r}, which is not installed here")
                continue
            failed[name] = error
        self.assertEqual(
            failed,
            {},
            "module(s) that need Maya to import -- try-guard the maya imports:\n"
            + "\n".join(f"  {name}: {error}" for name, error in sorted(failed.items())),
        )

    def test_a_maya_name_answered_is_none_is_bound_without_maya(self):
        """A module whose no-Maya branch asks ``cmds is None`` binds the name in
        its guard (the house form: ``except ImportError: cmds = None``). A
        guard ending in ``pass`` leaves it unbound, and the branch meant to
        answer outside Maya raises NameError instead -- found 2026-10-20 in
        ``AudioClips.export_record`` and ``LightUtils``. Added: 2026-10-20"""
        self.assertEqual(
            self.found["unbound"],
            {},
            "module(s) whose guard leaves a name its no-Maya branch reads "
            "unbound -- bind it to None in the guard:\n"
            + "\n".join(
                f"  {name}: {', '.join(aliases)}"
                for name, aliases in sorted(self.found["unbound"].items())
            ),
        )

    def test_importing_the_surface_creates_no_qapplication(self):
        """Imports have no side effects (CODE_STANDARD section 4): a module that
        builds a QApplication at import hands every importer -- the test
        runner, the registry, a headless script -- an application it never
        asked for. ``env_utils.maya_connection`` did, beside the lazy one its
        connect dialog already built. Added: 2026-10-29"""
        self.assertIsNone(
            self.found["app"], "importing this module created a QApplication"
        )

    def test_maya_bound_entries_name_real_modules(self):
        stale = sorted(set(MAYA_BOUND) - set(_surface_modules()))
        self.assertEqual(stale, [], "MAYA_BOUND names a module that no longer exists")


if __name__ == "__main__":
    unittest.main()
