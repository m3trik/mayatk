# !/usr/bin/python
# coding=utf-8
"""Tests for mayapy-package-manager.bat.

The script is a Windows batch file (cmd.exe), so most validation is structural:
parse the file, verify every `goto`/`call :sub` resolves to a defined label,
no labels are duplicated, helper subroutines end cleanly with `goto :eof`,
and the `CHOICE /C:` dispatcher has an `IF ERRORLEVEL` branch for every key.

A live smoke test runs the script end-to-end with stdin piped — only on
Windows, only if mayapy.exe is detectable. The structural tests run anywhere.
"""
import os
import re
import sys
import shutil
import subprocess
import unittest
from pathlib import Path

BAT_PATH = (
    Path(__file__).resolve().parent.parent
    / "mayatk"
    / "env_utils"
    / "mayapy-package-manager.bat"
)

LABEL_DEF_RE = re.compile(r"^\s*:([A-Za-z_][A-Za-z0-9_]*)\s*$")
GOTO_RE = re.compile(r"\bgoto\s+([A-Za-z_:][A-Za-z0-9_]*)", re.IGNORECASE)
CALL_RE = re.compile(r"\bcall\s+:([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
CHOICE_RE = re.compile(r"CHOICE\s+/C:([0-9A-Za-z]+)\s+/N", re.IGNORECASE)
ERRLVL_RE = re.compile(r"^\s*IF\s+ERRORLEVEL\s+(\d+)\s+goto\s+(\w+)", re.IGNORECASE)


def _read_lines():
    """Read the .bat file as a list of stripped-newline lines."""
    return BAT_PATH.read_text(encoding="utf-8").splitlines()


def _strip_comments(line: str) -> str:
    """Strip `::`-style batch comments. `REM` is left alone — `goto`/`call`
    inside REM lines are unusual but not impossible to detect; for our purposes
    structural references in REM lines would be a code smell anyway."""
    s = line.strip()
    if s.startswith("::"):
        return ""
    return line


def _find_mayapy() -> str | None:
    """Return path to the *latest* mayapy.exe detected, mirroring the .bat's
    `latest_version` selection. Returns None if no install or non-Windows."""
    if sys.platform != "win32":
        return None
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    autodesk = Path(program_files) / "Autodesk"
    if not autodesk.is_dir():
        return None
    candidates = []
    for entry in autodesk.iterdir():
        if not entry.name.startswith("Maya"):
            continue
        suffix = entry.name[4:]
        if not suffix.isdigit():
            continue
        mayapy = entry / "bin" / "mayapy.exe"
        if mayapy.is_file():
            candidates.append((int(suffix), mayapy))
    if not candidates:
        return None
    candidates.sort()
    return str(candidates[-1][1])


class TestStructure(unittest.TestCase):
    """Structural validation — runs on any platform."""

    @classmethod
    def setUpClass(cls):
        if not BAT_PATH.is_file():
            raise unittest.SkipTest(f"{BAT_PATH} not found")
        cls.lines = _read_lines()
        cls.labels = cls._collect_labels()
        cls.gotos = cls._collect_refs(GOTO_RE)
        cls.calls = cls._collect_refs(CALL_RE)

    @classmethod
    def _collect_labels(cls):
        """Return {label_name: line_number} for every label definition.
        Errors on duplicates so the caller can produce a clear failure."""
        labels = {}
        dups = []
        for i, raw in enumerate(cls.lines, start=1):
            line = _strip_comments(raw)
            m = LABEL_DEF_RE.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in labels:
                dups.append((name, labels[name], i))
            labels[name] = i
        cls._duplicates = dups
        return labels

    @classmethod
    def _collect_refs(cls, pattern):
        """Return list of (target, line_no) for every reference matching pattern."""
        refs = []
        for i, raw in enumerate(cls.lines, start=1):
            line = _strip_comments(raw)
            for m in pattern.finditer(line):
                refs.append((m.group(1), i))
        return refs

    def test_no_duplicate_labels(self):
        if self._duplicates:
            msg = "\n".join(
                f"  :{name} defined at line {first} and line {second}"
                for name, first, second in self._duplicates
            )
            self.fail(f"Duplicate label definitions:\n{msg}")

    def test_all_goto_targets_exist(self):
        missing = []
        for target, line_no in self.gotos:
            # `goto :eof` is a built-in cmd.exe target — always valid.
            if target.lower() == ":eof":
                continue
            name = target.lstrip(":")
            if name not in self.labels:
                missing.append((target, line_no))
        if missing:
            msg = "\n".join(f"  line {ln}: goto {t}" for t, ln in missing)
            self.fail(f"goto targets without matching label:\n{msg}")

    def test_all_call_targets_exist(self):
        missing = [
            (target, line_no)
            for target, line_no in self.calls
            if target not in self.labels
        ]
        if missing:
            msg = "\n".join(f"  line {ln}: call :{t}" for t, ln in missing)
            self.fail(f"call :sub targets without matching label:\n{msg}")

    def test_helper_subs_end_with_goto_eof(self):
        """`call :sub` returns at `goto :eof` (or end of file). Verify each
        called label's body terminates that way, otherwise execution falls
        through into whatever label follows in source order — a classic batch
        bug."""
        called_labels = {target for target, _ in self.calls}
        # Build a sorted list of (line_no, name) for ALL labels so we can
        # determine each label's body slice.
        ordered = sorted((ln, name) for name, ln in self.labels.items())

        problems = []
        for idx, (start_line, name) in enumerate(ordered):
            if name not in called_labels:
                continue
            end_line = (
                ordered[idx + 1][0] - 1 if idx + 1 < len(ordered) else len(self.lines)
            )
            # Walk the body in reverse to find the last non-blank, non-comment line.
            last_meaningful = None
            for ln in range(end_line, start_line, -1):
                stripped = _strip_comments(self.lines[ln - 1]).strip()
                if stripped:
                    last_meaningful = stripped
                    break
            if last_meaningful is None:
                problems.append(f"  :{name} (line {start_line}) has empty body")
                continue
            # Acceptable terminators: `goto :eof`, `exit /b`, or another `goto LABEL`.
            low = last_meaningful.lower()
            if not (
                low.startswith("goto :eof")
                or low.startswith("goto:eof")
                or low.startswith("exit ")
                or low == "exit"
                or GOTO_RE.search(low)
            ):
                problems.append(
                    f"  :{name} (line {start_line}) ends with: {last_meaningful!r}"
                )
        if problems:
            self.fail(
                "Helper subroutines must end with `goto :eof` (or another "
                "explicit jump) to avoid fall-through:\n" + "\n".join(problems)
            )

    def test_choice_dispatcher_has_branch_for_every_key(self):
        """Chain-style CHOICE dispatchers must have an `IF ERRORLEVEL N goto X`
        line for every key (1..N).

        This catches the menu-dispatcher class of bug — if you add a key to
        ``CHOICE /C:1234567890`` but forget the matching ``IF ERRORLEVEL 11``
        line, that key silently falls through.

        Block-style dispatchers (single ``IF ERRORLEVEL N (...)`` with body,
        optionally with ``ELSE``) are not checked — fall-through after the
        IF block is the natural handler for keys < N, which is valid cmd
        semantics."""
        chain_re = re.compile(
            r"^\s*IF\s+ERRORLEVEL\s+(\d+)\s+goto\s+\w+\s*$", re.IGNORECASE
        )

        choice_lines = [
            i for i, raw in enumerate(self.lines, start=1) if CHOICE_RE.search(raw)
        ]
        self.assertTrue(choice_lines, "Expected at least one CHOICE block")

        problems = []
        for idx, start in enumerate(choice_lines):
            keys = CHOICE_RE.search(self.lines[start - 1]).group(1)
            limit = (
                choice_lines[idx + 1] - 1
                if idx + 1 < len(choice_lines)
                else len(self.lines)
            )
            # Collect contiguous chain-style lines after CHOICE.
            chain_levels = []
            for j in range(start, limit):
                stripped = self.lines[j].strip()
                if not stripped:
                    continue
                m = chain_re.match(stripped)
                if m:
                    chain_levels.append(int(m.group(1)))
                    continue
                # Anything other than a chain line ends the chain segment.
                break

            # Only enforce coverage when this is a chain-style dispatcher (>=2
            # consecutive `IF ERRORLEVEL N goto X` lines). A single such line
            # with subsequent code is a guard, not a dispatcher.
            if len(chain_levels) < 2:
                continue

            covered = set(chain_levels)
            for k in range(1, len(keys) + 1):
                if k not in covered:
                    problems.append(
                        f"  CHOICE at line {start} (/C:{keys}) "
                        f"missing IF ERRORLEVEL {k} goto branch"
                    )

        if problems:
            self.fail("CHOICE dispatcher gaps:\n" + "\n".join(problems))

    def test_main_dispatcher_has_ctrl_c_fallback(self):
        """When CHOICE is interrupted (Ctrl+C / break), errorlevel is 0 and no
        `IF ERRORLEVEL N` matches. Without an explicit fallback `goto`, control
        falls through into whichever label happens to follow in source — a
        latent bug. Verify the main menu's CHOICE block ends with `goto main`."""
        in_main = False
        main_start = None
        for i, raw in enumerate(self.lines, start=1):
            stripped = raw.strip()
            if stripped == ":main":
                in_main = True
                main_start = i
                continue
            if not in_main:
                continue
            if LABEL_DEF_RE.match(stripped) and stripped != ":main":
                self.fail(
                    f":main block (starting line {main_start}) ended without an "
                    "explicit `goto main` fallback after the IF ERRORLEVEL chain"
                )
            if stripped.lower() == "goto main":
                return
        self.fail(":main label not found")

    def test_uses_no_profile_for_powershell(self):
        """Every `powershell` invocation should use `-NoProfile` to avoid
        loading the user's profile (slow, can fail in restricted environments)."""
        offenders = []
        for i, raw in enumerate(self.lines, start=1):
            line = _strip_comments(raw)
            # Match `powershell ` as a command (skip the .ps1 reference if any).
            if re.search(r"\bpowershell\b\s+(?!.*-NoProfile)", line, re.IGNORECASE):
                # Allow lines that are just comments or echo the literal word.
                if "powershell" in line.lower() and "-Command" in line:
                    offenders.append((i, line.strip()[:80]))
        if offenders:
            msg = "\n".join(f"  line {ln}: {snippet}" for ln, snippet in offenders)
            self.fail(f"powershell invocations missing -NoProfile:\n{msg}")

    def test_setlocal_paired_with_endlocal(self):
        text = "\n".join(_strip_comments(l) for l in self.lines)
        setlocal = len(re.findall(r"\bSETLOCAL\b", text, re.IGNORECASE))
        endlocal = len(re.findall(r"\bENDLOCAL\b", text, re.IGNORECASE))
        self.assertEqual(
            setlocal, endlocal,
            f"SETLOCAL count ({setlocal}) does not match ENDLOCAL count ({endlocal})",
        )

    def test_required_labels_present(self):
        """The script's public surface — these labels are referenced by the
        menu and must exist."""
        required = {
            "intro", "setVersion", "validateMayapyPath", "main", "install",
            "uninstall", "list", "update", "info", "outdated", "backup",
            "restore", "admin", "header", "result", "promptModule", "end",
        }
        missing = sorted(required - set(self.labels))
        self.assertFalse(
            missing, f"Required labels missing from script: {missing}"
        )


@unittest.skipUnless(sys.platform == "win32", "Windows-only smoke test")
class TestSmokeRun(unittest.TestCase):
    """End-to-end smoke test: pipe stdin, verify clean exit and expected output."""

    @classmethod
    def setUpClass(cls):
        if not BAT_PATH.is_file():
            raise unittest.SkipTest(f"{BAT_PATH} not found")
        cls.mayapy = _find_mayapy()
        if not cls.mayapy:
            raise unittest.SkipTest("mayapy.exe not detected — skipping smoke test")
        cls.maya_version = Path(cls.mayapy).parents[1].name.replace("Maya", "")

    def _run(self, stdin_lines, timeout=30):
        """Run the .bat with given stdin lines, return (returncode, stdout, stderr).

        Captures bytes (not text) and decodes with errors='replace' — Windows
        PowerShell 5.1 emits OEM-encoded box-drawing chars when stdout is
        piped, which would crash a strict UTF-8 / cp1252 decoder."""
        proc = subprocess.run(
            ["cmd.exe", "/c", str(BAT_PATH), self.maya_version],
            input=("\n".join(stdin_lines) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            cwd=str(BAT_PATH.parent),
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, out, err

    def test_immediate_exit(self):
        """Smoke: `0` (Exit) at the menu produces clean exit code 0."""
        # Menu key '0' -> :end. Some `pause >nul` calls may swallow input,
        # so feed a bunch of trailing newlines as a safety net.
        rc, out, err = self._run(["0"] + [""] * 5, timeout=20)
        self.assertEqual(
            rc, 0, f"Non-zero exit. stdout:\n{out}\nstderr:\n{err}"
        )
        # Should reach the goodbye screen.
        self.assertIn("Goodbye", out, f"Did not reach :end. Output:\n{out}")

    def test_list_then_exit(self):
        """Smoke: `5` (List) -> any-key (pause) -> `0` (Exit). Verifies that
        pip is reachable through the script's wrapper."""
        # Sequence: '5' selects List; pip output prints; pause waits for any key;
        # then '0' selects Exit.
        rc, out, err = self._run(["5"] + [""] * 3 + ["0"] + [""] * 5, timeout=60)
        self.assertEqual(
            rc, 0, f"Non-zero exit. stdout:\n{out}\nstderr:\n{err}"
        )
        # Either we see a Package header from `pip list`, or at minimum the
        # INSTALLED PACKAGES banner from the script.
        self.assertTrue(
            "INSTALLED PACKAGES" in out or "Package" in out,
            f"No expected output from list view:\n{out}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
