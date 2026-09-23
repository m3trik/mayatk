# !/usr/bin/python
# coding=utf-8
"""Static analysis guard for mayatk.

Runs pyflakes across the mayatk source tree and fails on any "undefined name"
finding. Catches the class of bug where a module references e.g. ``cmds`` or
``mel`` without importing it — which only manifests at call time and is easy
for unit tests to miss when they stub out the surrounding code.

False-positive filtering: pyflakes 3.x introspects string forward references
in typing annotations. Per CLAUDE.md, mayatk uses string-quoted ``"pm.PyNode"``
type hints intentionally (so files run without pymel installed). Findings
whose source position lies inside a string literal are filtered out — a real
``pm.X(...)`` call would still be flagged.

Runs in any Python interpreter (no Maya needed). Skipped if pyflakes isn't
installed in the active interpreter.
"""

import io
import re
import unittest
from pathlib import Path

MAYATK_ROOT = Path(__file__).resolve().parent.parent / "mayatk"

UNDEFINED_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s+undefined name '(?P<name>[^']+)'"
)

# Source trees that are NOT meant to import-resolve standalone. Files here
# contain ``__PLACEHOLDER__`` tokens substituted at generation time before
# they're handed to a host interpreter (e.g., Marmoset Toolbag's ``mset``).
EXCLUDED_DIRS = (
    "mat_utils/marmoset_bridge/templates",
    "env_utils/blender_bridge/templates",
)


def _is_inside_string_literal(source_line: str, col: int) -> bool:
    """Return True if 1-based column ``col`` in ``source_line`` is inside a
    quoted string literal. Tracks single/double quotes with backslash escapes;
    triple-quotes are out of scope (annotations are single-line)."""
    in_single = in_double = False
    i = 0
    target = col - 1
    while i < len(source_line) and i <= target:
        ch = source_line[i]
        if ch == "\\" and (in_single or in_double):
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        i += 1
    return in_single or in_double


class TestStaticAnalysis(unittest.TestCase):
    """Pyflakes-based guardrail against undefined names in mayatk."""

    def test_no_undefined_names(self):
        try:
            from pyflakes.api import checkPath, iterSourceCode
            from pyflakes.reporter import Reporter
        except ImportError:
            self.skipTest("pyflakes not installed in this interpreter")

        out, err = io.StringIO(), io.StringIO()
        reporter = Reporter(out, err)
        for path in iterSourceCode([str(MAYATK_ROOT)]):
            checkPath(path, reporter)

        findings = self._parse_findings(out.getvalue().splitlines())
        real = self._filter_real_findings(findings)

        self.assertFalse(
            real,
            "Undefined names that will NameError at call time:\n  "
            + "\n  ".join(f"{p}:{ln} -> {n}" for p, ln, n in sorted(real)),
        )

    @staticmethod
    def _parse_findings(lines):
        root_str = str(MAYATK_ROOT)
        for line in lines:
            m = UNDEFINED_RE.match(line)
            if not m:
                continue
            path = m.group("path")
            # pyflakes may emit either forward or back slashes; normalise.
            norm = path.replace("\\", "/")
            root_norm = root_str.replace("\\", "/")
            if not norm.startswith(root_norm):
                continue
            rel = norm[len(root_norm) :].lstrip("/")
            if any(rel.startswith(d + "/") for d in EXCLUDED_DIRS):
                continue
            yield (rel, int(m.group("line")), int(m.group("col")), m.group("name"))

    @staticmethod
    def _filter_real_findings(findings):
        real = []
        cache: dict = {}
        for rel, line_no, col, name in findings:
            abs_path = MAYATK_ROOT / rel
            if abs_path not in cache:
                try:
                    cache[abs_path] = abs_path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    cache[abs_path] = []
            lines = cache[abs_path]
            if 0 < line_no <= len(lines) and _is_inside_string_literal(
                lines[line_no - 1], col
            ):
                continue
            real.append((rel, line_no, name))
        return real


def module_source(raw: bytes):
    """*raw* module bytes as text with any BOM stripped; ``None`` if undecodable.

    ``utf-8-sig``, not ``utf-8``: PowerShell 5.1 writes UTF-8 WITH a BOM, and
    ``ast.parse`` rejects a leading U+FEFF -- decoded as plain utf-8, such a
    module would fall to the SyntaxError skip and pass unscanned.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def windows_only_openers(source: str):
    """``[(line, what)]`` for each Windows-only file-manager launch in *source*.

    ``os.startfile`` does not exist off Windows, and an ``["explorer", ...]``
    command runs nothing there -- so either one is an AttributeError (or a
    silent no-op) the day the panel opens on a macOS or Linux Maya. The shared
    openers, ``ptk.FileUtils.open_explorer`` and ``reveal_in_file_manager``,
    dispatch per platform. Parsed rather than grepped, so a comment that names
    the old call is not a finding.
    """
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "startfile"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            found.append((node.lineno, "os.startfile"))
        elif (
            isinstance(node, ast.List)
            and node.elts
            and isinstance(node.elts[0], ast.Constant)
            and str(node.elts[0].value).lower() == "explorer"
        ):
            found.append((node.lineno, '["explorer", ...]'))
    return found


class TestPortableFileOpeners(unittest.TestCase):
    """No mayatk module opens a folder with a Windows-only call.

    Found 2026-09-22 on the Lightmap Baker's *Open Sourceimages Folder*, with
    three sibling copies (the Scene Exporter's two open-folder buttons, the
    Reference Manager's *Open File Location*): mayatk targets Maya 2025+ on
    every platform, and each of those items raised on a Mac.
    """

    def test_the_scanner_finds_both_shapes(self):
        """The guard below proves nothing if the scanner is blind."""
        source = (
            "import os, subprocess\n"
            "os.startfile(p)  # os.startfile in a comment is not a finding\n"
            'subprocess.Popen(["explorer", "/select,", p])\n'
        )
        self.assertEqual(
            [what for _line, what in windows_only_openers(source)],
            ["os.startfile", '["explorer", ...]'],
        )

    def test_a_module_saved_with_a_bom_is_still_scanned(self):
        """The guard's own decode step, not a codec check: were it plain
        utf-8, the U+FEFF would reach ast.parse and raise here."""
        source = module_source(b"\xef\xbb\xbfimport os\nos.startfile(p)\n")
        self.assertEqual(len(windows_only_openers(source)), 1)

    def test_no_module_launches_a_windows_only_opener(self):
        findings = []
        for path in sorted(MAYATK_ROOT.rglob("*.py")):
            rel = path.relative_to(MAYATK_ROOT).as_posix()
            if any(rel.startswith(d + "/") for d in EXCLUDED_DIRS):
                continue
            try:
                source = module_source(path.read_bytes())
            except OSError:
                continue
            if source is None:
                continue
            try:
                hits = windows_only_openers(source)
            except SyntaxError:
                continue  # the undefined-name guard owns unparsable files
            findings.extend(f"{rel}:{line} {what}" for line, what in hits)
        self.assertFalse(
            findings,
            "Windows-only file opener(s); use ptk.FileUtils.open_explorer or "
            "ptk.FileUtils.reveal_in_file_manager:\n  " + "\n  ".join(findings),
        )


if __name__ == "__main__":
    unittest.main()
