#!/usr/bin/env python
# coding=utf-8
"""
Dynamic maya.cmds / mel.eval syntax checker.

Checks three things against the real Maya command registry in standalone:
  1. Command names  — cmds.X and leading identifiers in mel.eval() strings
  2. Flag names     — every keyword argument to cmds.X() is actually called
                      with that flag=True in Maya standalone; Maya's own parser
                      raises TypeError for unknown flags before touching the scene.

Run with mayapy (required for validation phase):
    mayapy check_cmds_syntax.py
    mayapy check_cmds_syntax.py mayatk/mayatk tentacle/tentacle
    mayapy check_cmds_syntax.py --all
    mayapy check_cmds_syntax.py --report

Exit codes: 0 = all OK, 1 = errors found
"""

import ast
import io
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent

ALL_PACKAGES = [
    "mayatk/mayatk",
    "tentacle/tentacle",
    "uitk/uitk",
    "pythontk/pythontk",
]
DEFAULT_PACKAGES = ALL_PACKAGES

# MEL statement-leading identifiers that are language keywords, not commands.
MEL_KEYWORDS = frozenset(
    {
        "if", "else", "elif", "while", "for", "do", "switch", "case",
        "break", "continue", "return", "proc", "global", "local",
        "int", "float", "string", "vector", "matrix",
        "true", "false", "yes", "no", "in",
    }
)

# Commands that require optional plugins — whatIs returns 'Unknown' until loaded.
PLUGIN_CMDS = frozenset(
    {
        "arnoldRender",                      # mtoa (Arnold)
        "shaderfx",                          # ShaderFX plugin
        "gpuCache",                          # gpuCache plugin
        "AbcExport", "AbcImport",            # Alembic
        "FBXExport", "FBXImport",            # FBX plugin
        "FBXImportMode", "FBXUICallBack",    # FBX plugin (UI/import-mode helpers)
        "FBXExportBakeComplexAnimation",     # FBX plugin (export-option setter)
        "gameExporter",                      # Game Exporter
        "u3dLayout", "u3dAutoSeam",          # Unfold3D UV plugin
        "u3dUnfold", "u3dOptimize",          # Unfold3D UV plugin
        "SendToUnrealSelection",             # Unreal live-link
        "SendToUnitySelection",              # Unity live-link
    }
)

# MEL procedures only defined in interactive / full-UI Maya.
INTERACTIVE_MEL_PROCS = frozenset(
    {
        "changeSelectMode",
        "hypershadePanelMenuCommand",
        "hyperShadePanelMenuCommand",
        "createAssignNewMaterialTreeLiser",
        "createAssignNewMaterialTreeLister",
        "artUserPaintTool",
        "performFileImport",
        "performFileExport",
        "redoPreviousRender",
        "performAlignUV",
        "performLinearAlignUV",
        # Modeling Toolkit — require active viewport
        "dR_multiCutTool", "dR_connectTool", "dR_quadDrawTool",
        "dR_selConstraintAngle", "dR_selConstraintBorder",
        "dR_selConstraintEdgeLoop", "dR_selConstraintEdgeRing",
        "dR_selConstraintElement", "dR_selConstraintUVEdgeLoop",
        "dR_selConstraintOff",
    }
)

# MEL language constructs that whatIs reports as 'Unknown' but are valid syntax.
MEL_SYNTAX_CONSTRUCTS = frozenset({"source", "catchQuiet", "catch"})

# All names to suppress from the error report (not bugs, just not in base standalone).
SUPPRESSED = PLUGIN_CMDS | INTERACTIVE_MEL_PROCS | MEL_SYNTAX_CONSTRUCTS

# ---------------------------------------------------------------------------
# Probe-call error classification
# ---------------------------------------------------------------------------
# Maya validates flag names BEFORE touching the scene, so calling
# cmds.X(flag=True) with no positional args tells us whether the flag is
# recognised regardless of whether a real scene object exists.

# TypeError sub-strings that mean the flag NAME is not recognised.
# Maya 2025 raises TypeError with "Invalid flag 'name'" for unknown flags.
# These patterns must only match flag-NAME errors, not mode/combination errors
# (which come as RuntimeError, not TypeError).
_FLAG_NAME_ERROR_FRAGMENTS: Tuple[str, ...] = (
    "Invalid flag '",               # Maya 2025: TypeError "Invalid flag 'badName'"
    "unexpected keyword argument",  # Python C-extension fallback for some commands
    "is not a valid flag",
    "not a recognized flag",
)

# RuntimeError messages that mean the flag IS valid but used in wrong mode/combination.
# "Invalid flag combination" / "Invalid flag for query" etc. are NOT flag-name errors.
_MODE_ERROR_FRAGMENTS: Tuple[str, ...] = (
    "Invalid flag combination",
    "Invalid flag for query",
    "Invalid flag for create",
    "Invalid flag for edit",
)

# RuntimeError sub-strings that mean flag OK, but no valid scene object present.
_SCENE_ERROR_FRAGMENTS: Tuple[str, ...] = (
    "No object matches name",
    "No valid objects",
    "Nothing is selected",
    "nothing is currently selected",
    "does not exist",
    "No nodes given",
    "No objects found",
    "requires at least",
    "Cannot find procedure",
    "No nodes of",
    "Nothing to",
    "no valid",
    "not found",
    "Object does not",
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    file: str      # relative to SCRIPTS_DIR
    line: int
    kind: str      # "cmds" | "mel" | "flag"
    name: str      # command name OR flag name
    context: str   # mel snippet | "" | cmd_name (for "flag" kind)


class FlagUse(NamedTuple):
    file: str
    line: int
    cmd: str    # command name
    flag: str   # keyword argument name


# ---------------------------------------------------------------------------
# Phase 1 – AST scan (no Maya needed)
# ---------------------------------------------------------------------------


class _CmdsExtractor(ast.NodeVisitor):
    """Collect maya.cmds attribute accesses, mel.eval strings, and cmds call flags."""

    def __init__(self) -> None:
        self._cmds_aliases: Set[str] = set()
        self._mel_aliases: Set[str] = set()
        self._maya_aliases: Set[str] = set()
        self.cmds_calls: List[Tuple[int, str]] = []        # (line, cmd_name)
        self.mel_calls: List[Tuple[int, str]] = []         # (line, mel_string)
        self.flag_uses: List[Tuple[int, str, List[str]]] = []  # (line, cmd_name, [flags])

    # -- import tracking -------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "maya.cmds":
                self._cmds_aliases.add(alias.asname or "cmds")
            elif alias.name == "maya.mel":
                self._mel_aliases.add(alias.asname or "mel")
            elif alias.name == "maya":
                self._maya_aliases.add(alias.asname or "maya")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "maya":
            for alias in node.names:
                if alias.name == "cmds":
                    self._cmds_aliases.add(alias.asname or "cmds")
                elif alias.name == "mel":
                    self._mel_aliases.add(alias.asname or "mel")
        self.generic_visit(node)

    # -- cmds.X attribute access (for command-name checking) -------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            if node.value.id in self._cmds_aliases:
                self.cmds_calls.append((node.lineno, node.attr))
        elif (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "cmds"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in self._maya_aliases
        ):
            self.cmds_calls.append((node.lineno, node.attr))
        self.generic_visit(node)

    # -- cmds.X(flag=...) call (for flag checking) and mel.eval("...") --------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # mel.eval("...") detection
        is_mel_eval = (
            isinstance(func, ast.Attribute)
            and func.attr == "eval"
            and isinstance(func.value, ast.Name)
            and func.value.id in self._mel_aliases
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == "eval"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "mel"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id in self._maya_aliases
        )

        if is_mel_eval:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self.mel_calls.append((node.lineno, arg.value))

        # cmds.X(flag=value) — extract keyword argument names
        cmd_name: Optional[str] = None
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in self._cmds_aliases
        ):
            cmd_name = func.attr
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "cmds"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id in self._maya_aliases
        ):
            cmd_name = func.attr

        if cmd_name and not cmd_name.startswith("_") and not cmd_name[0].isupper():
            # kw.arg is None for **kwargs expansions — skip those
            flags = [kw.arg for kw in node.keywords if kw.arg is not None]
            if flags:
                self.flag_uses.append((node.lineno, cmd_name, flags))

        self.generic_visit(node)


# ---------------------------------------------------------------------------


def _mel_command_names(mel_string: str) -> List[str]:
    """Extract leading command/procedure names from each MEL statement."""
    names: List[str] = []
    seen: Set[str] = set()

    def _add(n: str) -> None:
        if n not in seen:
            seen.add(n)
            names.append(n)

    for stmt in re.split(r"[;\n]+", mel_string):
        stmt = stmt.strip()
        if not stmt or stmt.startswith("//") or stmt.startswith("/*"):
            continue
        stmt = re.sub(r"^\$\w+\s*=\s*", "", stmt).lstrip()
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\b", stmt)
        if m:
            name = m.group(1)
            if name not in MEL_KEYWORDS:
                _add(name)

    for m in re.finditer(r"`([a-zA-Z_][a-zA-Z0-9_]*)\b", mel_string):
        name = m.group(1)
        if name not in MEL_KEYWORDS:
            _add(name)

    return names


def _probe_flag(cmds_mod, cmd_name: str, flag_name: str) -> str:
    """
    Call cmds.cmd_name(flag_name=True) in Maya and classify the outcome.

    Maya validates flag NAMES before touching the scene, so an unrecognised
    name raises TypeError with "Invalid flag 'name'" immediately.

    Mode/combination errors ("Invalid flag combination", "Invalid flag for
    query") come as RuntimeError and mean the flag IS valid — just used in
    the wrong mode or combination.

    Returns one of:
      "ok"         — executed without error
      "flag_error" — flag NAME not recognised by Maya  ← only this is reported
      "type_error" — flag exists but True is the wrong value type
      "mode_error" — flag exists; wrong mode or flag combination
      "no_scene"   — flag OK; command needs real scene objects
      "other"      — other runtime condition; flag validity unknown
    """
    try:
        getattr(cmds_mod, cmd_name)(**{flag_name: True})
        return "ok"
    except TypeError as e:
        msg = str(e)
        # Case-sensitive check: "Invalid flag '" is Maya's exact format.
        if any(f in msg for f in _FLAG_NAME_ERROR_FRAGMENTS):
            return "flag_error"
        # "Error retrieving default arguments" etc. — flag exists, wrong type
        return "type_error"
    except RuntimeError as e:
        msg = str(e)
        msg_lo = msg.lower()
        # Mode/combination errors: flag IS valid, just used incorrectly
        if any(f in msg for f in _MODE_ERROR_FRAGMENTS):
            return "mode_error"
        if any(f.lower() in msg_lo for f in _SCENE_ERROR_FRAGMENTS):
            return "no_scene"
        return "other"
    except Exception:
        return "other"


def scan_file(filepath: str) -> Tuple[List[Finding], List[FlagUse]]:
    """Parse one Python file and return command findings and flag uses."""
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return [], []

    visitor = _CmdsExtractor()
    visitor.visit(tree)

    if not visitor.cmds_calls and not visitor.mel_calls and not visitor.flag_uses:
        return [], []

    rel = os.path.relpath(filepath, str(SCRIPTS_DIR))
    findings: List[Finding] = []
    flag_uses: List[FlagUse] = []

    for line, name in visitor.cmds_calls:
        if name.startswith("_") or name[0].isupper():
            continue
        findings.append(Finding(rel, line, "cmds", name, ""))

    for line, mel_str in visitor.mel_calls:
        for name in _mel_command_names(mel_str):
            context = mel_str[:60].replace("\n", " ")
            findings.append(Finding(rel, line, "mel", name, context))

    for line, cmd_name, flags in visitor.flag_uses:
        for flag in flags:
            flag_uses.append(FlagUse(rel, line, cmd_name, flag))

    return findings, flag_uses


def scan_paths(paths: List[Path]) -> Tuple[List[Finding], List[FlagUse]]:
    """Walk all .py files in each path and collect findings and flag uses."""
    all_findings: List[Finding] = []
    all_flag_uses: List[FlagUse] = []
    for base in paths:
        if not base.exists():
            print(f"  Warning: path not found: {base}", file=sys.stderr)
            continue
        py_files = sorted(f for f in base.rglob("*.py") if "__pycache__" not in f.parts)
        print(f"  {len(py_files):4d} files  ->  {base.relative_to(SCRIPTS_DIR)}")
        for f in py_files:
            findings, flag_uses = scan_file(str(f))
            all_findings.extend(findings)
            all_flag_uses.extend(flag_uses)
    return all_findings, all_flag_uses


# ---------------------------------------------------------------------------
# Phase 2 – Maya validation (must run inside mayapy)
# ---------------------------------------------------------------------------


def validate(
    findings: List[Finding], flag_uses: List[FlagUse]
) -> Tuple[Dict[Finding, bool], int]:
    """
    Validate command names (via whatIs) and flags (by actual execution).

    Flag probing: for each unique (cmd, flag) pair call cmds.cmd(flag=True)
    in Maya standalone.  Maya parses flag names before touching the scene, so
    an unrecognised flag raises TypeError immediately — this is the signal we
    use, not help-text parsing.
    """
    import maya.cmds as cmds
    import maya.mel as mel

    results: Dict[Finding, bool] = {}

    # --- A: command-name validation via whatIs --------------------------------
    unique_names = {f.name for f in findings}
    print(f"\n  Checking {len(unique_names)} unique command name(s) via whatIs...")
    validity: Dict[str, bool] = {}
    for name in sorted(unique_names):
        try:
            result = mel.eval(f'whatIs "{name}"')
            validity[name] = (result != "Unknown")
        except Exception:
            validity[name] = False
    for f in findings:
        results[f] = validity.get(f.name, False)

    # --- B: flag-name validation by execution ---------------------------------
    checkable = [fu for fu in flag_uses if fu.cmd not in SUPPRESSED]
    unique_pairs: List[Tuple[str, str]] = sorted(
        {(fu.cmd, fu.flag) for fu in checkable}
    )
    print(f"  Probing {len(unique_pairs)} unique (cmd, flag) pair(s) by execution...")

    probe_cache: Dict[Tuple[str, str], str] = {}
    for cmd_name, flag_name in unique_pairs:
        probe_cache[(cmd_name, flag_name)] = _probe_flag(cmds, cmd_name, flag_name)

    flag_errors = [
        (cmd, flag) for (cmd, flag), status in probe_cache.items()
        if status == "flag_error"
    ]
    if flag_errors:
        print(f"  Found {len(flag_errors)} bad flag(s) by execution")

    for fu in checkable:
        if probe_cache.get((fu.cmd, fu.flag)) == "flag_error":
            results[Finding(fu.file, fu.line, "flag", fu.flag, fu.cmd)] = False

    return results, len(unique_pairs)


# ---------------------------------------------------------------------------
# Phase 3 – Report
# ---------------------------------------------------------------------------


def report(results: Dict[Finding, bool], flag_probe_count: int = 0, out=None) -> int:
    """Print a human-readable report. Returns total error count."""
    if out is None:
        out = sys.stdout

    all_errors = [f for f, ok in results.items() if not ok and f.name not in SUPPRESSED]
    suppressed = [f for f, ok in results.items() if not ok and f.name in SUPPRESSED]
    ok_count = sum(1 for ok in results.values() if ok)
    total = len(results)

    cmd_errors = [f for f in all_errors if f.kind in ("cmds", "mel")]
    flag_errors = [f for f in all_errors if f.kind == "flag"]

    def _by_file(error_list: List[Finding]) -> Dict[str, List[Finding]]:
        d: Dict[str, List[Finding]] = defaultdict(list)
        for f in error_list:
            d[f.file].append(f)
        return d

    if cmd_errors:
        print("\n--- UNKNOWN COMMANDS ---", file=out)
        for filepath in sorted(_by_file(cmd_errors)):
            print(f"\n  {filepath}", file=out)
            for f in sorted(_by_file(cmd_errors)[filepath], key=lambda x: x.line):
                ctx = f"  [{f.context}]" if f.context else ""
                print(f"    line {f.line:<5d} [{f.kind}]  {f.name}{ctx}", file=out)

    if flag_errors:
        print("\n--- UNKNOWN FLAGS ---", file=out)
        for filepath in sorted(_by_file(flag_errors)):
            print(f"\n  {filepath}", file=out)
            for f in sorted(_by_file(flag_errors)[filepath], key=lambda x: x.line):
                print(f"    line {f.line:<5d} [flag]  cmds.{f.context}({f.name}=...)", file=out)

    if suppressed:
        sup_names = sorted({f.name for f in suppressed})
        print(f"\n--- SUPPRESSED (plugin / interactive / MEL construct) ---", file=out)
        print(f"  {', '.join(sup_names)}", file=out)

    print(f"\n{'=' * 60}", file=out)
    cmd_refs = sum(1 for f in results if f.kind in ("cmds", "mel"))
    print(
        f"Commands : {cmd_refs} refs  |  {len(cmd_errors)} unknown  |  {len(suppressed)} suppressed",
        file=out,
    )
    print(
        f"Flags    : {flag_probe_count} unique (cmd, flag) pairs probed  |  {len(flag_errors)} unknown",
        file=out,
    )
    if all_errors:
        bad = sorted({f.name for f in all_errors})
        print(f"Unknown  : {', '.join(bad)}", file=out)
        print("FAIL", file=out)
    else:
        print("PASS - all commands and flags accepted by Maya", file=out)

    return len(all_errors)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    args = sys.argv[1:]
    scan_all = False
    write_report = False
    path_args: List[str] = []

    for arg in args:
        if arg == "--all":
            scan_all = True
        elif arg == "--report":
            write_report = True
        elif not arg.startswith("--"):
            path_args.append(arg)

    if scan_all:
        targets = [SCRIPTS_DIR / p for p in ALL_PACKAGES]
    elif path_args:
        targets = [
            Path(p) if Path(p).is_absolute() else SCRIPTS_DIR / p
            for p in path_args
        ]
    else:
        targets = [SCRIPTS_DIR / p for p in DEFAULT_PACKAGES]

    print("=== maya.cmds / mel.eval syntax checker ===")
    print(f"Root : {SCRIPTS_DIR}")
    print(f"Scan : {[str(t.relative_to(SCRIPTS_DIR)) for t in targets]}")
    print()

    # Phase 1: scan
    print("Phase 1: scanning source files...")
    findings, flag_uses = scan_paths(targets)
    print(f"  {len(findings)} command reference(s), {len(flag_uses)} flag use(s) collected")

    if not findings and not flag_uses:
        print("\nNothing to validate.")
        return 0

    # Phase 2: Maya standalone
    print("\nPhase 2: initializing Maya standalone...")
    import maya.standalone
    maya.standalone.initialize()
    print("  Maya ready")

    error_count = 0
    try:
        print("\nPhase 3: validating...")
        results, flag_probe_count = validate(findings, flag_uses)
        error_count = report(results, flag_probe_count)

        if write_report:
            report_path = Path(__file__).parent / "cmds_syntax_report.txt"
            buf = io.StringIO()
            report(results, flag_probe_count, out=buf)
            report_path.write_text(buf.getvalue(), encoding="utf-8")
            print(f"\nReport saved: {report_path}")
    finally:
        maya.standalone.uninitialize()

    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
