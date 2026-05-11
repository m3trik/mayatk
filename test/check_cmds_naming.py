"""Scan for Maya cmds object-naming pitfalls across mayatk and tentacle.

Reports four classes of issue:
  1. Discarded return from a name-mutating command (rename, parent, polyUnite,
     polyCombine, polySeparate, polyChipOff, duplicate, instance, group).
     The new name lives in the return value; throwing it away leaves any
     downstream reference to the old name pointing at a stale or shifted node.
  2. Mixed long/short flag forms or two synonyms of the same flag in one call.
  3. Stale variable use after `cmds.rename(x, ...)` — same scope, no reassign.
  4. Hardcoded Maya auto-name literals ("pCube1", "polySurface3", ...).
"""
from __future__ import annotations
import ast
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap Maya — needed for cmds.help() to build the flag alias table.
# ---------------------------------------------------------------------------
import maya.standalone

maya.standalone.initialize()
import maya.cmds as cmds  # noqa: E402

REPOS = [
    Path(r"o:\Cloud\Code\_scripts\mayatk\mayatk"),
    Path(r"o:\Cloud\Code\_scripts\tentacle\tentacle"),
]

# Commands whose return value carries a freshly-minted node name. Discarding
# the return is the canonical Maya footgun — Maya may de-clash the requested
# name (pCube1 → pCube2) and any later code using the old name silently
# operates on the wrong node, or fails outright.
#
# HIGH: return is almost always required (rename clash, combine creates new
# transform, separate splits into N new transforms).
# LOW: return is usually safe to discard (parent typically keeps the child's
# name unless there's a clash; duplicate/instance/group sometimes called for
# side effect only).
NAME_MUTATING_HIGH = {"rename", "polyUnite", "polyCombine", "polySeparate", "polyChipOff"}
NAME_MUTATING_LOW = {"parent", "duplicate", "instance", "group"}
NAME_MUTATING = NAME_MUTATING_HIGH | NAME_MUTATING_LOW

# Default Maya auto-name patterns. Hardcoded references to these break the
# moment the scene already has a node by that name.
AUTO_NAME_PATTERNS = [
    re.compile(r"^(pCube|pSphere|pPlane|pCylinder|pCone|pTorus|pPyramid|pPipe|pHelix|pDisc|pPrism|pPlatonic)\d+$"),
    re.compile(r"^(polySurface|polyToFacePart|polyCombine|polyUnite|polySeparate)\d+$"),
    re.compile(r"^(group|locator|null|joint|cluster|lambert|blinn|phong|aiStandardSurface)\d+$"),
    re.compile(r"^(persp|top|front|side|default)\d+$"),
]


# ---------------------------------------------------------------------------
# Build flag alias map from cmds.help() — authoritative, version-correct.
# ---------------------------------------------------------------------------
def build_flag_aliases(commands: set[str]) -> dict[str, dict[str, str]]:
    """For each command, return {flag_form: canonical_long_name}."""
    out: dict[str, dict[str, str]] = {}
    for cmd in commands:
        if not hasattr(cmds, cmd):
            continue
        try:
            text = cmds.help(cmd) or ""
        except RuntimeError:
            continue
        flags: dict[str, str] = {}
        for line in text.splitlines():
            s = line.strip()
            # Format: "-shortName(s) -longName(L) [argTypes...]" or "-longName(L) ..."
            m = re.match(r"^-([A-Za-z0-9]+)(?:\([SL]\))?\s+-([A-Za-z0-9]+)(?:\([SL]\))?", s)
            if m:
                a, b = m.group(1), m.group(2)
                # The longer one is the canonical long name.
                long_, short = (a, b) if len(a) > len(b) else (b, a)
                flags[short] = long_
                flags[long_] = long_
        if flags:
            out[cmd] = flags
    return out


# ---------------------------------------------------------------------------
# AST walker — collect every cmds.<name>(...) call with context.
# ---------------------------------------------------------------------------
class CallInfo:
    __slots__ = ("file", "lineno", "col", "cmd", "kwargs", "str_args", "is_stmt", "first_arg_name")

    def __init__(self, file, lineno, col, cmd, kwargs, str_args, is_stmt, first_arg_name):
        self.file = file
        self.lineno = lineno
        self.col = col
        self.cmd = cmd
        self.kwargs = kwargs
        self.str_args = str_args
        self.is_stmt = is_stmt
        self.first_arg_name = first_arg_name


def parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_cmds_call(node: ast.Call) -> str | None:
    """Return the cmds.* attribute name if this is a cmds call, else None."""
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "cmds":
        return f.attr
    return None


def collect_calls(filepath: Path) -> list[CallInfo]:
    src = filepath.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(filepath))
    except SyntaxError:
        return []
    parents = parent_map(tree)
    calls: list[CallInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        cmd = _is_cmds_call(node)
        if cmd is None:
            continue
        kwargs = [kw.arg for kw in node.keywords if kw.arg]
        str_args = [
            a.value
            for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        first_arg_name = None
        if node.args and isinstance(node.args[0], ast.Name):
            first_arg_name = node.args[0].id
        is_stmt = isinstance(parents.get(id(node)), ast.Expr)
        calls.append(
            CallInfo(
                file=filepath,
                lineno=node.lineno,
                col=node.col_offset,
                cmd=cmd,
                kwargs=kwargs,
                str_args=str_args,
                is_stmt=is_stmt,
                first_arg_name=first_arg_name,
            )
        )
    return calls


# ---------------------------------------------------------------------------
# Stale-variable-after-rename: per-function scope, no leak across function
# boundaries; also reset by reassignment (incl. for-target, with-as).
# ---------------------------------------------------------------------------
_SCOPE_BARRIER = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _walk_no_funcdef(node: ast.AST):
    """Like ast.walk but stops at fresh-scope boundaries (functions + comprehensions)."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if isinstance(c, _SCOPE_BARRIER):
                continue
            todo.append(c)


def _assigned_names(stmt: ast.stmt) -> set[str]:
    """Names that get bound by this statement (does not recurse into nested defs)."""
    out: set[str] = set()
    for n in _walk_no_funcdef(stmt):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
                elif isinstance(tgt, (ast.Tuple, ast.List)):
                    for el in tgt.elts:
                        if isinstance(el, ast.Name):
                            out.add(el.id)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            if isinstance(n.target, ast.Name):
                out.add(n.target.id)
        elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
        elif isinstance(n, ast.With):
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    out.add(item.optional_vars.id)
    return out


def find_stale_after_rename(filepath: Path) -> list[tuple[int, str, str]]:
    """Per-function: cmds.rename(x, ...) -> later use of x without reassign."""
    src = filepath.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(filepath))
    except SyntaxError:
        return []
    parents = parent_map(tree)
    results: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str, int]] = set()  # dedupe (use_line, var, rename_line)

    def _rename_rebinds_same_var(call: ast.Call, var: str) -> bool:
        """True if `var = cmds.rename(var, ...)` (var rebound to fresh name)."""
        node: ast.AST = call
        while True:
            p = parents.get(id(node))
            if p is None:
                return False
            if isinstance(p, ast.Assign):
                return any(isinstance(t, ast.Name) and t.id == var for t in p.targets)
            if isinstance(p, ast.AnnAssign):
                return isinstance(p.target, ast.Name) and p.target.id == var
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module, ast.ClassDef)):
                return False
            node = p

    def scan_function(body: list[ast.stmt]):
        renamed: dict[str, int] = {}
        for stmt in body:
            # Nested functions/classes own their own scope — skipped here
            # because the outer ast.walk loop will scan_function() them.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            # 1. Reassignments inside this stmt clear stale flags.
            for name in _assigned_names(stmt):
                renamed.pop(name, None)
            # 2. Find uses of currently-renamed vars within this stmt body
            #    (excluding the cmds.rename call itself, which is what binds it).
            for n in _walk_no_funcdef(stmt):
                if (
                    isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load)
                    and n.id in renamed
                    and n.lineno > renamed[n.id]
                ):
                    key = (n.lineno, n.id, renamed[n.id])
                    if key not in seen:
                        seen.add(key)
                        results.append((n.lineno, n.id, f"renamed at line {renamed[n.id]}"))
            # 3. Record new renames seen in this stmt — but if the rename's
            #    return is captured back into the same variable (anywhere in
            #    a containing assignment, even nested inside an if/loop), the
            #    variable is fresh, not stale.
            for n in _walk_no_funcdef(stmt):
                if (
                    isinstance(n, ast.Call)
                    and _is_cmds_call(n) == "rename"
                    and n.args
                    and isinstance(n.args[0], ast.Name)
                ):
                    var = n.args[0].id
                    if not _rename_rebinds_same_var(n, var):
                        renamed[var] = n.lineno

    # Each function/method/lambda + module gets its own scope.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_function(node.body)
        elif isinstance(node, ast.Module):
            scan_function(node.body)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def relpath(p: Path) -> str:
    for repo in REPOS:
        try:
            return str(p.relative_to(repo.parent))
        except ValueError:
            continue
    return str(p)


def main() -> int:
    py_files: list[Path] = []
    for repo in REPOS:
        py_files.extend(p for p in repo.rglob("*.py") if "build" not in p.parts)

    print(f"Scanning {len(py_files)} files across {len(REPOS)} repos...\n")

    all_calls: list[CallInfo] = []
    for f in py_files:
        all_calls.extend(collect_calls(f))

    unique_cmds = {c.cmd for c in all_calls}
    print(f"Resolving flag-alias map for {len(unique_cmds)} unique cmds...")
    aliases = build_flag_aliases(unique_cmds)
    print(f"Got aliases for {len(aliases)} commands.\n")

    # ---- Issue 1: discarded return from name-mutating command -------------
    issue1_high: list[CallInfo] = [
        c for c in all_calls if c.cmd in NAME_MUTATING_HIGH and c.is_stmt
    ]
    issue1_low: list[CallInfo] = [
        c for c in all_calls if c.cmd in NAME_MUTATING_LOW and c.is_stmt
    ]

    # ---- Issue 2: duplicate-alias flags (one call passes two synonyms) ----
    # Pure long-vs-short stylistic mixing is dropped — it's not a bug, both
    # forms are accepted. We only flag genuinely duplicated flags.
    issue2: list[tuple[CallInfo, str]] = []
    for c in all_calls:
        amap = aliases.get(c.cmd)
        if not amap:
            continue
        seen_canonical: dict[str, str] = {}
        for k in c.kwargs:
            canonical = amap.get(k)
            if canonical is None:
                continue
            if canonical in seen_canonical and seen_canonical[canonical] != k:
                issue2.append(
                    (c, f"two flags alias to {canonical!r}: {seen_canonical[canonical]!r} and {k!r}")
                )
            seen_canonical[canonical] = k

    # ---- Issue 3: stale var after rename ----------------------------------
    issue3: list[tuple[Path, int, str, str]] = []
    for f in py_files:
        for lineno, var, info in find_stale_after_rename(f):
            issue3.append((f, lineno, var, info))

    # ---- Issue 4: hardcoded Maya auto-name literals -----------------------
    issue4: list[tuple[CallInfo, str]] = []
    for c in all_calls:
        for s in c.str_args:
            base = s.split(".")[0].split("|")[-1].split(":")[-1]
            for pat in AUTO_NAME_PATTERNS:
                if pat.match(base):
                    issue4.append((c, s))
                    break

    # ---- Print report -----------------------------------------------------
    def header(title, count):
        bar = "=" * 70
        print(f"\n{bar}\n[{count}] {title}\n{bar}")

    header("HIGH: discarded return from name-clash-prone command", len(issue1_high))
    print("Hint: cmds.rename and poly{Unite,Combine,Separate,ChipOff} return")
    print("the actual new name (Maya may de-clash). Discarding it leaves any")
    print("downstream reference to the old name pointing at a stale node.")
    for c in issue1_high:
        print(f"  {relpath(c.file)}:{c.lineno}  cmds.{c.cmd}(...)")

    header("LOW: discarded return from cmds.parent / duplicate / instance / group", len(issue1_low))
    print("Hint: usually safe (return matters only on name clashes).")
    for c in issue1_low:
        print(f"  {relpath(c.file)}:{c.lineno}  cmds.{c.cmd}(...)")

    header("Duplicate-alias flags in one call (real bug)", len(issue2))
    for c, msg in issue2:
        print(f"  {relpath(c.file)}:{c.lineno}  cmds.{c.cmd}: {msg}")

    header("Stale var after cmds.rename(x, ...)", len(issue3))
    print("Hint: per-function scope, but no branch analysis — review each.")
    for f, lineno, var, info in issue3:
        print(f"  {relpath(f)}:{lineno}  use of {var!r} ({info})")

    header("Hardcoded Maya auto-name literals", len(issue4))
    print("Hint: these break if Maya already assigned the name to another node.")
    for c, s in issue4:
        print(f"  {relpath(c.file)}:{c.lineno}  cmds.{c.cmd}(... {s!r} ...)")

    total = len(issue1_high) + len(issue1_low) + len(issue2) + len(issue3) + len(issue4)
    print(f"\n{'=' * 70}")
    print(f"TOTAL: {total} potential issues")
    print(f"  1a. discarded (HIGH):     {len(issue1_high)}")
    print(f"  1b. discarded (LOW):      {len(issue1_low)}")
    print(f"  2.  duplicate-alias:      {len(issue2)}")
    print(f"  3.  stale after rename:   {len(issue3)}")
    print(f"  4.  hardcoded auto-names: {len(issue4)}")
    return 0 if (len(issue1_high) + len(issue2) + len(issue4)) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
