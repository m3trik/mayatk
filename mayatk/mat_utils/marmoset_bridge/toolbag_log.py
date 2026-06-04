# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag log-file resolution, classification, and live tailing.

DCC-agnostic. These helpers locate Toolbag's application ``log.txt``
(robust to major-version bumps), map raw log lines to a routed level
(``info`` / ``warning`` / ``error`` / suppressed), and tail the file into
a logger while a launched Toolbag process runs.

Used by :class:`MarmosetEngine` for ``send_to`` runs, where Toolbag's
stdout isn't captured by the caller.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple


# Match ``Toolbag <N>`` -- the version-bearing dir name in both layouts
# Marmoset ships: ``Marmoset\Toolbag 5\toolbag.exe`` (Program Files install,
# with a backslash separator) and ``Marmoset Toolbag 5\log.txt`` (LOCALAPPDATA
# user data, single dir name with a space). The 'Marmoset ' prefix is
# hardcoded by the construction site, so the regex only needs the version.
_TOOLBAG_VERSION_RE = re.compile(r"Toolbag\s+(\d+)", re.IGNORECASE)


def resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]:
    """Return the path to Toolbag's application log, robust to version bumps.

    Tier 1: parse the major version out of *toolbag_exe* and return
            ``%LOCALAPPDATA%/Marmoset Toolbag <N>/log.txt`` unconditionally.
            The file may not exist yet on a fresh Toolbag install -- but
            the directory naming convention is deterministic, and Toolbag
            will create it as soon as it writes anything.
    Tier 2: no version parseable from the exe path (custom install,
            sandbox, dev build). Scan ``%LOCALAPPDATA%`` for
            ``Marmoset Toolbag *`` directories with an existing
            ``log.txt`` and pick the most recently modified.
    Tier 3: return *None* -- callers should fall back to the per-run log
            written by the helper's ``begin_log``.

    The naming convention has held across Toolbag 3, 4, and 5; this code
    survives the next major as long as Marmoset keeps the pattern.
    """
    local_app = os.environ.get("LOCALAPPDATA")
    if not local_app:
        return None
    local_app_path = Path(local_app)

    # Tolerate non-string input (test code patches AppLauncher and the
    # cached toolbag_path can be a MagicMock); only the str branch is
    # parseable, anything else falls through to the LOCALAPPDATA scan.
    if isinstance(toolbag_exe, str) and toolbag_exe:
        m = _TOOLBAG_VERSION_RE.search(toolbag_exe)
        if m:
            # Trust the convention. Don't require log.txt to exist yet --
            # if Toolbag was just installed, the consumer (tail thread,
            # clickable link) will see it appear shortly.
            return str(local_app_path / f"Marmoset Toolbag {m.group(1)}" / "log.txt")

    # Tier 2: any 'Marmoset Toolbag *' dir under LOCALAPPDATA, newest log wins.
    newest: Optional[Path] = None
    newest_mtime = -1.0
    if local_app_path.is_dir():
        for sub in local_app_path.glob("Marmoset Toolbag *"):
            log = sub / "log.txt"
            if log.is_file():
                mt = log.stat().st_mtime
                if mt > newest_mtime:
                    newest_mtime = mt
                    newest = log
    return str(newest) if newest else None


# Lines starting with these prefixes are Toolbag's startup chatter (shader
# preloads, image preloads) and are too noisy to forward to the bridge
# log panel. They're harmless and arrive in bursts hundreds of lines deep.
_NOISE_PREFIXES = ("opening code ", "opening image ", "opening shader ")


def classify_log_line(line: str) -> Optional[Tuple[str, str]]:
    """Map a Toolbag log line to ``(level, line)`` for routing into a logger.

    *level* is one of ``"info"``, ``"warning"``, ``"error"``. Returns
    *None* for lines that should be suppressed (Toolbag's preload spam).

    The rules favour false-positive "warning"/"error" over silence -- a
    misclassified info line shown in yellow is less harmful than a real
    failure shown in white.
    """
    s = line.strip()
    if not s:
        return None
    low = s.lower()

    if s.startswith(_NOISE_PREFIXES):
        return None

    # Hard errors -- helper's ``! slot: ...`` lines and Toolbag's own
    # failure messages.
    if (
        s.startswith("!")
        or s.startswith("ERROR:")
        or s.startswith("Traceback")
        or "matfield not found" in low
        or "cannot open image" in low
        or "attributeerror" in low
        or low.startswith("error ")
    ):
        return ("error", line)

    # Warnings -- helper skips, Toolbag's "failed"/"could not", and
    # helper meta-messages that signal "the wire pass did nothing"
    # (empty manifest, no matching materials, etc.). These would
    # otherwise be silent infos and the user wouldn't notice that
    # nothing actually wired.
    if (
        s.startswith("SKIP")
        or s.startswith("?")
        or "failed" in low
        or "could not" in low
        or low.startswith("warning")
        or "nothing to wire" in low
        or "manifest empty or missing" in low
        or "no skyboxobject in scene" in low
    ):
        return ("warning", line)

    return ("info", line)


def dispatch_log_lines(lines, logger) -> None:
    """Forward each classified line to *logger* at its routed level.

    Used by both the send_to tail thread (lines arrive over time) and the
    roundtrip post-processor (lines arrive as a single captured string).
    """
    for raw in lines:
        classified = classify_log_line(raw)
        if classified is None:
            continue
        level, msg = classified
        getattr(logger, level)(msg)


def start_toolbag_log_tail(
    log_path: str,
    start_offset: int,
    process,
    logger,
    poll_interval: float = 0.4,
    file_wait_timeout: float = 60.0,
):
    """Tail *log_path* from *start_offset* in a daemon thread.

    Reads new content as Toolbag writes it, classifies each line, and
    emits to *logger* at the routed level so errors land in the caller's
    panel in red without the user having to open the log file. Stops
    when *process* exits.

    On a fresh Toolbag install, ``log.txt`` may not exist yet at launch
    time -- Toolbag creates it on its first write. The thread polls for
    the file's appearance up to *file_wait_timeout* seconds before
    giving up.

    Defensive: any I/O error inside the thread is swallowed so a
    diagnostic feature can't crash the host.
    """
    import threading
    import time

    def run() -> None:
        try:
            # Wait for Toolbag to create the log file. Bail if the
            # process dies before that ever happens.
            wait_start = time.time()
            while not os.path.isfile(log_path):
                if process.poll() is not None:
                    return
                if time.time() - wait_start > file_wait_timeout:
                    return
                time.sleep(poll_interval)

            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(start_offset)
                buffered = ""
                while process.poll() is None:
                    chunk = fh.read()
                    if not chunk:
                        time.sleep(poll_interval)
                        continue
                    buffered += chunk
                    lines = buffered.split("\n")
                    # Last fragment may be a partial line; hold it.
                    buffered = lines.pop()
                    dispatch_log_lines(lines, logger)
                # Final flush after process exit (anything Toolbag wrote
                # between our last read and shutdown).
                tail = fh.read()
                if tail:
                    buffered += tail
                if buffered:
                    dispatch_log_lines(buffered.split("\n"), logger)
        except Exception:
            # Daemon thread; never propagate.
            pass

    t = threading.Thread(target=run, daemon=True, name="MarmosetLogTail")
    t.start()
    return t
