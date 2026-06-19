# !/usr/bin/python
# coding=utf-8
"""Tests for mayapy-package-manager.bat (thin wrapper) + the shared package-manager.bat (menu).

The wrapper now only detects Maya and hands off to the interpreter-agnostic
``m3trik\\package-manager.bat`` (the shared menu/operations). Validation is structural for both:
parse each file, verify every ``goto``/``call :sub`` resolves to a defined label, no duplicate
labels, helper subroutines end cleanly, the menu's ``CHOICE /C:`` dispatcher branches every key,
SETLOCAL/ENDLOCAL pair, and ``powershell`` uses ``-NoProfile``.

A live smoke test runs the wrapper end-to-end with stdin piped (Windows + detectable mayapy only),
exercising the wrapper→generic handoff. The structural tests run anywhere.
"""
import os
import re
import sys
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # _scripts/
WRAPPER_PATH = _REPO / "mayatk" / "mayatk" / "env_utils" / "mayapy-package-manager.bat"
GENERIC_PATH = _REPO / "m3trik" / "package-manager.bat"

LABEL_DEF_RE = re.compile(r"^\s*:([A-Za-z_][A-Za-z0-9_]*)\s*$")
GOTO_RE = re.compile(r"\bgoto\s+([A-Za-z_:][A-Za-z0-9_]*)", re.IGNORECASE)
CALL_SUB_RE = re.compile(r"\bcall\s+:([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
CHOICE_RE = re.compile(r"CHOICE\s+/C:([0-9A-Za-z]+)\s+/N", re.IGNORECASE)


def _strip_comments(line: str) -> str:
    return "" if line.strip().startswith("::") else line


class _BatAnalyzer:
    """Parse a .bat into labels / goto / call references for structural assertions."""

    def __init__(self, path: Path):
        self.path = path
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.labels = {}
        self.duplicates = []
        for i, raw in enumerate(self.lines, start=1):
            m = LABEL_DEF_RE.match(_strip_comments(raw))
            if not m:
                continue
            name = m.group(1)
            if name in self.labels:
                self.duplicates.append((name, self.labels[name], i))
            self.labels[name] = i
        self.gotos = self._refs(GOTO_RE)
        self.calls = self._refs(CALL_SUB_RE)

    def _refs(self, pattern):
        out = []
        for i, raw in enumerate(self.lines, start=1):
            for m in pattern.finditer(_strip_comments(raw)):
                out.append((m.group(1), i))
        return out


class _StructuralChecks:
    """Reusable structural assertions for a parsed .bat (mixed into per-file TestCases)."""

    analyzer: _BatAnalyzer

    def test_no_duplicate_labels(self):
        self.assertFalse(
            self.analyzer.duplicates,
            f"Duplicate labels in {self.analyzer.path.name}: {self.analyzer.duplicates}",
        )

    def test_all_goto_targets_exist(self):
        missing = [
            (t, ln) for t, ln in self.analyzer.gotos
            if t.lower() != ":eof" and t.lstrip(":") not in self.analyzer.labels
        ]
        self.assertFalse(missing, f"Unresolved goto in {self.analyzer.path.name}: {missing}")

    def test_all_call_targets_exist(self):
        missing = [(t, ln) for t, ln in self.analyzer.calls if t not in self.analyzer.labels]
        self.assertFalse(missing, f"Unresolved call :sub in {self.analyzer.path.name}: {missing}")

    def test_setlocal_has_endlocal(self):
        # A single SETLOCAL scope may be closed on several exit paths, so ENDLOCAL >= SETLOCAL
        # (strict equality wrongly flags multi-exit scripts). The real bug is a SETLOCAL with no
        # ENDLOCAL on some path → ENDLOCAL < SETLOCAL.
        text = "\n".join(_strip_comments(l) for l in self.analyzer.lines)
        sl = len(re.findall(r"\bSETLOCAL\b", text, re.IGNORECASE))
        el = len(re.findall(r"\bENDLOCAL\b", text, re.IGNORECASE))
        self.assertGreaterEqual(sl, 1, f"{self.analyzer.path.name}: no SETLOCAL")
        self.assertGreaterEqual(el, sl, f"{self.analyzer.path.name}: ENDLOCAL {el} < SETLOCAL {sl}")

    def test_powershell_uses_no_profile(self):
        offenders = [
            (i, l.strip()[:80]) for i, raw in enumerate(self.analyzer.lines, start=1)
            for l in [_strip_comments(raw)]
            if re.search(r"\bpowershell\b\s+(?!.*-NoProfile)", l, re.IGNORECASE) and "-Command" in l
        ]
        self.assertFalse(offenders, f"{self.analyzer.path.name}: powershell missing -NoProfile: {offenders}")


class TestGenericMenu(_StructuralChecks, unittest.TestCase):
    """The shared menu/operations file owns the interactive dispatcher."""

    @classmethod
    def setUpClass(cls):
        if not GENERIC_PATH.is_file():
            raise unittest.SkipTest(f"{GENERIC_PATH} not found")
        cls.analyzer = _BatAnalyzer(GENERIC_PATH)

    def test_required_menu_labels_present(self):
        required = {
            "validateInterp", "intro", "main", "install", "uninstall", "list",
            "update", "info", "outdated", "backup", "restore", "admin",
            "header", "result", "promptModule", "end",
        }
        self.assertFalse(sorted(required - set(self.analyzer.labels)),
                         f"Missing menu labels: {sorted(required - set(self.analyzer.labels))}")

    def test_choice_dispatcher_covers_every_key(self):
        chain_re = re.compile(r"^\s*IF\s+ERRORLEVEL\s+(\d+)\s+goto\s+\w+\s*$", re.IGNORECASE)
        choice_lines = [i for i, raw in enumerate(self.analyzer.lines, start=1) if CHOICE_RE.search(raw)]
        self.assertTrue(choice_lines, "Expected a CHOICE dispatcher")
        problems = []
        for idx, start in enumerate(choice_lines):
            keys = CHOICE_RE.search(self.analyzer.lines[start - 1]).group(1)
            limit = choice_lines[idx + 1] - 1 if idx + 1 < len(choice_lines) else len(self.analyzer.lines)
            levels = []
            for j in range(start, limit):
                s = self.analyzer.lines[j].strip()
                if not s:
                    continue
                m = chain_re.match(s)
                if m:
                    levels.append(int(m.group(1)))
                    continue
                break
            if len(levels) < 2:
                continue
            covered = set(levels)
            problems += [f"CHOICE@{start} (/C:{keys}) missing ERRORLEVEL {k}"
                         for k in range(1, len(keys) + 1) if k not in covered]
        self.assertFalse(problems, "\n".join(problems))

    def test_main_has_ctrl_c_fallback(self):
        in_main = False
        for raw in self.analyzer.lines:
            s = raw.strip()
            if s == ":main":
                in_main = True
                continue
            if in_main and s.lower() == "goto main":
                return
            if in_main and LABEL_DEF_RE.match(s) and s != ":main":
                self.fail(":main lacks a trailing `goto main` fallback")
        self.fail(":main not found")

    def test_helper_subs_end_cleanly(self):
        called = {t for t, _ in self.analyzer.calls}
        ordered = sorted((ln, name) for name, ln in self.analyzer.labels.items())
        problems = []
        for idx, (start, name) in enumerate(ordered):
            if name not in called:
                continue
            end = ordered[idx + 1][0] - 1 if idx + 1 < len(ordered) else len(self.analyzer.lines)
            last = ""
            for ln in range(end, start, -1):
                s = _strip_comments(self.analyzer.lines[ln - 1]).strip()
                if s:
                    last = s.lower()
                    break
            if not (last.startswith("goto :eof") or last.startswith("goto:eof")
                    or last.startswith("exit") or GOTO_RE.search(last)):
                problems.append(f":{name} ends with {last!r}")
        self.assertFalse(problems, "Helper subs must end with goto :eof:\n" + "\n".join(problems))


class TestMayapyWrapper(_StructuralChecks, unittest.TestCase):
    """The thin wrapper: detect Maya, resolve mayapy, hand off to the shared menu."""

    @classmethod
    def setUpClass(cls):
        if not WRAPPER_PATH.is_file():
            raise unittest.SkipTest(f"{WRAPPER_PATH} not found")
        cls.analyzer = _BatAnalyzer(WRAPPER_PATH)

    def test_required_wrapper_labels_present(self):
        required = {"setVersion", "validateMayapyPath", "handoff"}
        self.assertFalse(sorted(required - set(self.analyzer.labels)),
                         f"Missing wrapper labels: {sorted(required - set(self.analyzer.labels))}")

    def test_hands_off_to_generic(self):
        text = "\n".join(self.analyzer.lines).lower()
        self.assertIn("package-manager.bat", text, "Wrapper must call the shared package-manager.bat")
        self.assertRegex(text, r'call\s+"%generic%"', "Wrapper must `call` the resolved generic")


def _find_mayapy():
    if sys.platform != "win32":
        return None
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk"
    if not pf.is_dir():
        return None
    cands = []
    for e in pf.iterdir():
        if e.name.startswith("Maya") and e.name[4:].isdigit():
            mp = e / "bin" / "mayapy.exe"
            if mp.is_file():
                cands.append((int(e.name[4:]), mp))
    return str(sorted(cands)[-1][1]) if cands else None


@unittest.skipUnless(sys.platform == "win32", "Windows-only smoke test")
class TestSmokeRun(unittest.TestCase):
    """End-to-end: wrapper detects Maya, hands off to the menu, `0` exits cleanly."""

    @classmethod
    def setUpClass(cls):
        if not WRAPPER_PATH.is_file() or not GENERIC_PATH.is_file():
            raise unittest.SkipTest("package-manager scripts not found")
        cls.mayapy = _find_mayapy()
        if not cls.mayapy:
            raise unittest.SkipTest("mayapy.exe not detected — skipping smoke test")
        cls.maya_version = Path(cls.mayapy).parents[1].name.replace("Maya", "")

    def test_immediate_exit(self):
        proc = subprocess.run(
            ["cmd.exe", "/c", str(WRAPPER_PATH), self.maya_version],
            input=("\n".join(["0"] + [""] * 5) + "\n").encode("utf-8"),
            capture_output=True, timeout=40, cwd=str(WRAPPER_PATH.parent),
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, f"rc!=0.\nstdout:\n{out}\nstderr:\n{err}")
        self.assertIn("Goodbye", out, f"Did not reach the menu's :end.\n{out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
