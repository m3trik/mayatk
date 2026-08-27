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
import shutil
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # _scripts/
WRAPPER_PATH = _REPO / "mayatk" / "mayatk" / "env_utils" / "mayapy-package-manager.bat"
GENERIC_PATH = _REPO / "m3trik" / "package-manager.bat"
# The shared menu is mirrored next to the wrapper (by m3trik/scripts/sync_shared_bat.py)
# so it ships in the wheel — after a bare pip install there is no m3trik/ to fall back to.
MIRROR_PATH = WRAPPER_PATH.parent / "package-manager.bat"
# The wheel the wrapper bootstraps from when it was downloaded on its own.
PACKAGE = "mayatk"

# The smoke test's wrapper cold-starts its own ``mayapy`` to detect Maya. That is
# fast in isolation but can be slow under load (a full-suite run launches its own
# Maya, and cold interpreter starts on a busy/laptop machine are slow — see the
# root CLAUDE.md session-safety note). A tight timeout flaked the whole suite on a
# TimeoutExpired that was purely contention, not a wrapper hang; give real headroom
# while still catching a genuine hang.
SMOKE_TIMEOUT = 120


def _norm_eol(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

# Values whose content the script does not control — filesystem paths, and whatever the user
# types at the prompt. They must reach PowerShell through the environment ($env:NAME), never
# expanded into the command line: a profile like C:\\Users\\O'Brien closes a single-quoted
# literal early and the whole line dies on a parser error.
UNSAFE_PS_VARS = ("%interp%", "%mayapy%", "%blenderpy%", "%fetch_dir%", "%fetch_target%",
                  "%backup_file%", "%module%", "%~dp0", "%~f0", "%cd%")

LABEL_DEF_RE = re.compile(r"^\s*:([A-Za-z_][A-Za-z0-9_]*)\s*$")
GOTO_RE = re.compile(r"\bgoto\s+([A-Za-z_:][A-Za-z0-9_]*)", re.IGNORECASE)
CALL_SUB_RE = re.compile(r"\bcall\s+:([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
CHOICE_RE = re.compile(r"CHOICE\s+/C:([0-9A-Za-z]+)\s+/N", re.IGNORECASE)


def _strip_comments(line: str) -> str:
    """Blank out a comment line. Batch has two forms — `::` and the `REM` keyword — and both
    must go: a checker that reads REM lines flags prose, not code."""
    s = line.strip().lower()
    return "" if s.startswith("::") or s == "rem" or s.startswith("rem ") else line


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

    def section(self, name):
        """Source text of one label's body: from ``:name`` to the next label (exclusive)."""
        start = self.labels[name]
        after = sorted(ln for ln in self.labels.values() if ln > start)
        end = after[0] - 1 if after else len(self.lines)
        return "\n".join(self.lines[start:end])


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

    def test_status_markers_escape_the_bang(self):
        """`[!!]` under EnableDelayedExpansion renders as `[]` — it must be written `[^!^!]`."""
        offenders = [
            (i, l.strip()[:80]) for i, raw in enumerate(self.analyzer.lines, start=1)
            for l in [_strip_comments(raw)] if "[!!]" in l
        ]
        self.assertFalse(offenders, f"{self.analyzer.path.name}: unescaped `!`: {offenders}")

    def test_file_is_ascii_only(self):
        """These run under whatever codepage the console has; the menu header states the rule.
        A stray smart quote or em dash is invisible in a diff and mis-scans under cmd's UTF-8
        codepage bug."""
        try:
            self.analyzer.path.read_bytes().decode("ascii")
        except UnicodeDecodeError as e:
            self.fail(f"{self.analyzer.path.name}: non-ASCII byte at {e.start}: {e.object[e.start:e.end]!r}")

    def test_powershell_strings_have_no_bare_apostrophe(self):
        """Every message is a single-quoted PowerShell literal, so a word like "pip's" closes the
        string early: the line dies on a parser error and its message never reaches the user.
        A deliberate one must be doubled (''), which this pattern already allows."""
        offenders = [
            (i, l.strip()[:90]) for i, raw in enumerate(self.analyzer.lines, start=1)
            for l in [_strip_comments(raw)]
            if "powershell" in l.lower() and re.search(r"[A-Za-z]'[A-Za-z]", l)
        ]
        self.assertFalse(offenders, f"{self.analyzer.path.name}: unescaped apostrophe: {offenders}")

    def test_powershell_takes_paths_from_the_environment(self):
        """Proven live: run from a folder whose name carries an apostrophe, the wrapper's whole
        remedies block vanished — the apostrophe closed the string and the line never ran."""
        offenders = [
            (i, v) for i, raw in enumerate(self.analyzer.lines, start=1)
            for l in [_strip_comments(raw)] if "powershell" in l.lower()
            for v in UNSAFE_PS_VARS if v in l
        ]
        self.assertFalse(offenders, f"{self.analyzer.path.name}: inline path in a PS command: {offenders}")

    def test_typed_value_is_never_reparsed_by_cmd(self):
        """`%module%` is substituted before the line is parsed, so a typed `&` — a local wheel
        under C:\\R&D\\, say — becomes a command separator and whatever follows it runs.
        Measured: the %-form executed an injected `echo`; `!module!` passed it through as an
        argument. Every use is delayed expansion or $env:PM_MODULE."""
        text = "\n".join(_strip_comments(l) for l in self.analyzer.lines)
        self.assertNotIn("%module%", text,
                         f"{self.analyzer.path.name}: the typed value is re-parsed by cmd")

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
            "update", "info", "outdated", "backup", "restore",
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

    def test_no_in_menu_elevation_relaunch(self):
        """The menu used to relaunch itself elevated (`Start-Process <this .bat> -Verb RunAs`)
        and exit. The batfile `runas` verb is `cmd /C "%1" %*` -- unlike `open`, `"%1" %*` --
        and with more than two quote characters on its line cmd strips the first and the last,
        so the elevated window ran a mangled path and closed before drawing anything while the
        parent said Goodbye (measured 2026-08-25, with and without spaces in the path). Nothing
        the menu does needs elevation; the sanctioned path is right-click > Run as administrator
        on the launcher, and the title must say which mode a window is in."""
        text = "\n".join(_strip_comments(l) for l in self.analyzer.lines)
        self.assertNotIn("admin", self.analyzer.labels, "no in-menu elevation item")
        self.assertNotRegex(text, r"(?i)-Verb\s+RunAs", "no in-process elevation relaunch")
        self.assertNotIn("Run as Administrator", text)
        self.assertRegex(text, r'(?im)^fltmc\s*>nul\s*2>&1\s*&&\s*set\s+"mode= \(ADMINISTRATOR\)"',
                         "elevation state must be detected without prompting (fltmc)")
        for sec in ("intro", "main"):
            self.assertIn("PACKAGE MANAGER%mode%", self.analyzer.section(sec),
                          f":{sec} title must carry the elevation state")

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

    def test_error_exit_waits_for_the_user(self):
        """A dead end must hold the window open — a 3s timer closes before anyone reads it."""
        block = self.analyzer.section("validateInterp")
        self.assertIn("pause", block.lower(), f"validateInterp must pause:\n{block}")
        self.assertNotRegex(block, r"(?i)timeout\s+/t", "validateInterp must not auto-close")

    def test_result_reports_a_failed_operation(self):
        """pip's own error does not mention the one cause the user cannot see: blocked traffic."""
        block = self.analyzer.section("result")
        self.assertRegex(block, r'set "op_rc=%ERRORLEVEL%"',
                         "the op's exit code must be captured before ECHO clobbers it")
        self.assertIn("firewall", block.lower(), f":result must name the likely cause:\n{block}")

    def test_prompt_splits_a_list_without_a_pipe(self):
        """Two traps sit in this block, both hit while writing it. `=` cannot be searched for
        with the replace syntax — it is that syntax's own delimiter, so `!module:==!` reads as an
        empty search and matches everything — and a pipe hands the expanded text to a second
        shell, which re-reads a version range's `<`/`>` as redirection."""
        block = self.analyzer.section("promptModule")
        self.assertIn('set "module=!module:, = !"', block, "comma+space must always separate")
        self.assertNotIn("|", block, "a pipe re-parses the expanded value; use unpiped tests")
        self.assertIn('delims==', block, "`=` needs for/f, not the replace syntax")
        for ch in ("<", ">", "["):
            self.assertIn(f'!module:{ch}=!', block, f"no guard for {ch!r} in a requirement")

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

    def test_single_install_skips_the_version_prompt(self):
        """One detected install is not a choice — use it instead of asking the user to confirm."""
        text = "\n".join(self.analyzer.lines)
        self.assertIn("set /a version_count+=1", text, "Wrapper must count detected installs")
        start = text.find('else if "%version_count%"=="1" (')
        self.assertNotEqual(start, -1, 'No `else if "%version_count%"=="1"` branch')
        end = text.find(") else if", start)
        branch = text[start : end if end != -1 else len(text)]
        self.assertNotIn("set /p", branch, f"The single-install branch must not prompt:\n{branch}")

    def test_failed_bootstrap_holds_the_window_open(self):
        """The download dead end must explain itself and wait for a key, not vanish."""
        for label in ("fetchShared", "fetchFailed"):
            self.assertIn(label, self.analyzer.labels, f"Wrapper missing :{label}")
        block = self.analyzer.section("fetchFailed")
        self.assertIn("pause", block.lower(), f":fetchFailed must pause:\n{block}")
        self.assertNotRegex(block, r"(?i)timeout\s+/t", ":fetchFailed must not auto-close")
        self.assertIn("firewall", block.lower(), ":fetchFailed must name the likely cause")

    def test_every_menu_candidate_is_vetted_before_it_is_called(self):
        """`if exist` is not enough: an empty/truncated/intercepted file passes it, then the
        `call` returns instantly and the window closes with nothing on screen — the same
        silent failure the fetch path exists to explain. Every candidate goes through
        :checkGeneric, whatever it came from (sibling copy, monorepo, download)."""
        self.assertIn("checkGeneric", self.analyzer.labels, "Wrapper missing :checkGeneric")
        block = self.analyzer.section("checkGeneric")
        self.assertRegex(block, r"findstr\s+/b\s+/c:\":main\"",
                         f"the menu's signature must be checked, not just existence:\n{block}")
        self.assertRegex(block, r'set "generic="', "a rejected candidate must be cleared")
        handoff = self.analyzer.section("handoff")
        self.assertEqual(
            handoff.count("call :checkGeneric"), 2,
            f"both on-disk candidates must be vetted:\n{handoff}",
        )
        self.assertNotRegex(handoff, r'if exist "%generic%" goto runGeneric',
                            "no candidate may reach the call on an existence check alone")

    def test_bootstrap_fetches_through_the_resolved_interpreter(self):
        """pip, not a raw download: it honours the proxy / index-url / CA bundle this machine is
        configured with, and it fails through the same interpreter every menu operation needs —
        so a blocked box finds out once, here, instead of at the first install."""
        block = self.analyzer.section("fetchShared") + self.analyzer.section("fetchWheel")
        self.assertIn('"%mayapy%" -m pip download', block,
                      f"the resolved interpreter must do the fetching:\n{block}")
        self.assertNotIn("-m pip install", block,
                         "installing the package would write to Program Files and need elevation")
        for flag in ("--no-deps", "--only-binary=:all:", "--retries", "--timeout"):
            self.assertIn(flag, block, f"{flag} missing from the bootstrap download:\n{block}")

    def test_failed_download_leaves_nothing_behind(self):
        """An unusable result must be deleted, and 2 MB of wheel must not outlive the bootstrap."""
        block = self.analyzer.section("fetchShared")
        self.assertIn("call :checkGeneric", block, "the download is vetted like any candidate")
        self.assertRegex(block, r"if defined generic goto :eof\ndel /f /q",
                         f"an unusable download must be deleted:\n{block}")
        self.assertRegex(block, r'if not exist "%fetch_target%" goto :eof',
                         "a failed stage must keep its own name for :fetchFailed to report")
        self.assertEqual(block.count('rd /s /q "%fetch_dir%"'), 2,
                         f"the wheel scratch dir must be cleared before AND after:\n{block}")

    def test_unentered_version_is_a_dead_end(self):
        """`set /p` returns instantly on EOF, so the two prompts bounced forever whenever stdin
        was not a console — a busy loop spawning powershell until the process was killed."""
        self.assertIn("noVersion", self.analyzer.labels, "Wrapper missing :noVersion")
        text = "\n".join(self.analyzer.lines)
        self.assertIn("if not defined maya_version goto noVersion", text,
                      "an unset version must leave the prompt loop")
        block = self.analyzer.section("noVersion")
        self.assertIn("pause", block.lower(), f":noVersion must hold the window:\n{block}")
        self.assertRegex(block, r"exit /b 1", ":noVersion must exit non-zero")

    def test_wheel_entry_matches_where_the_package_ships_the_menu(self):
        """The bootstrap lifts one member out of the wheel by exact name; if the mirror ever moves,
        that lookup returns nothing and the bootstrap dead-ends for a reason no message explains."""
        entry = MIRROR_PATH.relative_to(_REPO / PACKAGE).as_posix()
        self.assertIn(f'set "PM_WHEEL_ENTRY={entry}"', "\n".join(self.analyzer.lines),
                      f"Wrapper does not look for the shipped path {entry!r}")

    def test_shared_menu_mirrored_next_to_wrapper(self):
        # The wrapper's first handoff candidate is `%~dp0package-manager.bat` (the wheel case).
        # The mirror must exist beside the wrapper and match the m3trik SSoT verbatim; if this
        # fails, run `python m3trik/scripts/sync_shared_bat.py`.
        self.assertTrue(MIRROR_PATH.is_file(), f"Shared menu not mirrored next to wrapper: {MIRROR_PATH}")
        if GENERIC_PATH.is_file():
            self.assertEqual(
                _norm_eol(MIRROR_PATH.read_bytes()),
                _norm_eol(GENERIC_PATH.read_bytes()),
                "Mirror drifted from the m3trik SSoT — run m3trik/scripts/sync_shared_bat.py",
            )


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
            capture_output=True, timeout=SMOKE_TIMEOUT, cwd=str(WRAPPER_PATH.parent),
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, f"rc!=0.\nstdout:\n{out}\nstderr:\n{err}")
        self.assertIn("Goodbye", out, f"Did not reach the menu's :end.\n{out}")


@unittest.skipUnless(sys.platform == "win32", "Windows-only smoke test")
class TestPromptSeparators(unittest.TestCase):
    """What the shared prompt hands pip, for every requirement form that contains a comma.

    A comma separates packages, but it is also part of a requirement inside a version range
    (`django>=2.0,<3.0`) and an extras list (`requests[security,socks]`). The real subroutine is
    lifted out and driven directly: CHOICE reads characters, not lines, so a redirected stdin
    makes it grab a digit from the middle of the typed text and the menu cannot be driven whole.
    """

    #                 typed                          ->  what pip receives
    CASES = [
        ("scipy",                          "scipy"),
        ("scipy, numpy==2.1, pandas",      "scipy numpy==2.1 pandas"),
        ("scipy,numpy",                    "scipy numpy"),
        ("a,b,c",                          "a b c"),
        ("tentacletk[maya], mayatk",       "tentacletk[maya] mayatk"),
        # Not lists: the comma belongs to the requirement and must survive untouched.
        ("django>=2.0,<3.0",               "django>=2.0,<3.0"),
        ("django>2.0,<3.0",                "django>2.0,<3.0"),
        ("requests[security,socks]",       "requests[security,socks]"),
        ("scipy, django>=2.0,<3.0",        "scipy django>=2.0,<3.0"),
    ]

    @classmethod
    def setUpClass(cls):
        if not GENERIC_PATH.is_file():
            raise unittest.SkipTest(f"{GENERIC_PATH} not found")

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "temp_tests" / "pm_separators"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        lines = GENERIC_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
        start = lines.index(":promptModule")
        end = next(i for i in range(start + 1, len(lines)) if LABEL_DEF_RE.match(lines[i]))
        self.block = "\n".join(lines[start:end]).rstrip()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _normalize(self, typed):
        """Run the real :promptModule over `typed` and return the value it hands the pip call."""
        (self.tmp / "typed.txt").write_bytes((typed + "\r\n").encode("ascii"))
        harness = "\n".join([
            "@ECHO off",
            "SETLOCAL EnableDelayedExpansion EnableExtensions",
            'set "C_PROMPT="',
            'set "C_RESET="',
            'call :promptModule "x: " < "%~dp0typed.txt"',
            "ECHO OUT=[!module!]",
            "ENDLOCAL",
            "exit /b 0",
            "",
            self.block,
            "",
        ])
        bat = self.tmp / "prompt.bat"
        bat.write_bytes(harness.replace("\n", "\r\n").encode("ascii"))
        proc = subprocess.run(f'cmd.exe /c "{bat}"', input=b"", capture_output=True,
                              timeout=SMOKE_TIMEOUT, cwd=str(self.tmp))
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        m = re.search(r"OUT=\[(.*)\]", out)
        self.assertIsNotNone(m, f"harness produced no OUT line:\n{out}")
        return m.group(1)

    def test_commas_split_packages_but_not_requirements(self):
        for typed, expected in self.CASES:
            with self.subTest(typed=typed):
                self.assertEqual(self._normalize(typed), expected)


@unittest.skipUnless(sys.platform == "win32", "Windows-only smoke test")
class TestBlockedBootstrap(unittest.TestCase):
    """Bootstrap dead ends: explain and wait, never call an unusable menu, never spin.

    Regression: with outbound traffic blocked, curl wrote nothing, the wrapper had no menu
    to hand off to, and the window closed on a 3-second timer — the user saw a flash.
    A copy of the wrapper is run from a scratch dir (no sibling menu, no monorepo above it)
    with its bootstrap URL pointed at a closed port: the same dead end a firewall produces.
    """

    @classmethod
    def setUpClass(cls):
        if not WRAPPER_PATH.is_file():
            raise unittest.SkipTest(f"{WRAPPER_PATH} not found")
        cls.mayapy = _find_mayapy()
        if not cls.mayapy:
            # Without a resolvable mayapy the wrapper loops on its version prompt instead
            # of reaching the handoff, so there is nothing to assert here.
            raise unittest.SkipTest("mayapy.exe not detected — skipping bootstrap test")
        cls.maya_version = Path(cls.mayapy).parents[1].name.replace("Maya", "")

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "temp_tests" / "pm_blocked_fetch"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        # Verbatim copy: the wrapper under test is the shipped one. Only the environment is
        # hostile — %TEMP% is redirected here so the wheel scratch dir can be asserted on, and
        # pip is pointed at a closed port, which is what a blocked index looks like to it.
        self.copy = self.tmp / WRAPPER_PATH.name
        self.copy.write_bytes(WRAPPER_PATH.read_bytes())
        self.scratch = self.tmp / f"{PACKAGE}-pm-bootstrap"
        self.blocked = (
            f'set "TEMP={self.tmp}" & set "TMP={self.tmp}" & '
            'set "PIP_INDEX_URL=http://127.0.0.1:9/simple" & set "PIP_NO_INPUT=1" & '
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args, env_prefix=""):
        # One command LINE, not an argv list: list2cmdline escapes the quotes around the script
        # path and cmd.exe mis-parses those after /c, so the wrapper never runs.
        return subprocess.run(
            f'cmd.exe /c {env_prefix}"{self.copy}" {" ".join(args)}',
            input=b"", capture_output=True, timeout=SMOKE_TIMEOUT, cwd=str(self.tmp),
        )

    def test_blocked_fetch_explains_and_waits(self):
        proc = self._run(self.maya_version, env_prefix=self.blocked)
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 1, f"Expected a failure exit.\n{out}")
        self.assertIn("firewall", out.lower(), f"No firewall hint:\n{out}")
        self.assertIn("Press any key", out, f"Window would close unattended:\n{out}")
        self.assertFalse(
            (self.tmp / "package-manager.bat").exists(),
            "A failed download must not leave a stub the wrapper would call",
        )
        self.assertFalse(self.scratch.exists(), f"Wheel scratch dir survived: {self.scratch}")

    def test_stale_stub_is_rejected_rather_than_called(self):
        """An earlier blocked run can leave an empty package-manager.bat beside the wrapper.
        `if exist` accepted it, the `call` returned instantly, and the window closed with no
        message at all — the same symptom, one step earlier."""
        (self.tmp / "package-manager.bat").write_bytes(b"")
        proc = self._run(self.maya_version, env_prefix=self.blocked)
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 1, f"The empty stub was called.\n{out}")
        self.assertIn("firewall", out.lower(), f"No explanation reached the user:\n{out}")

    def test_an_apostrophe_in_the_path_does_not_eat_the_message(self):
        """A downloaded .bat routinely sits under a profile directory, and a name like O'Brien is
        ordinary. Every remedy line was interpolated into a single-quoted PowerShell literal, so
        the apostrophe closed the string and the whole "here is how to fix it" block was replaced
        by a parser error the user cannot act on."""
        odd = self.tmp / "O'Brien"
        odd.mkdir(exist_ok=True)
        copy = odd / WRAPPER_PATH.name
        copy.write_bytes(WRAPPER_PATH.read_bytes())
        proc = subprocess.run(
            f'cmd.exe /c set "TEMP={odd}" & set "PIP_INDEX_URL=http://127.0.0.1:9/simple"'
            f' & "{copy}" {self.maya_version}',
            input=b"", capture_output=True, timeout=SMOKE_TIMEOUT, cwd=str(odd),
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        self.assertNotIn("missing the terminator", out + err, f"PowerShell parser error:\n{out}\n{err}")
        self.assertIn("Save this file by hand", out, f"The remedies block was eaten:\n{out}")
        self.assertIn(str(odd), out, f"The folder to save into was not shown:\n{out}")

    def test_no_install_and_no_input_terminates(self):
        """No install detected and nothing on stdin: the wrapper must end, not spin. Scanning is
        rooted at %ProgramFiles%, so pointing that at an empty dir is the whole no-Maya case."""
        empty = self.tmp / "no_program_files"
        empty.mkdir(exist_ok=True)
        proc = self._run(env_prefix=f'set "ProgramFiles={empty}" & ')
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 1, f"Expected a failure exit.\n{out}")
        self.assertIn("nothing to do", out.lower(), f"No explanation reached the user:\n{out}")
        self.assertIn("Press any key", out, f"Window would close unattended:\n{out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
