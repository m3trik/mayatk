# !/usr/bin/python
# coding=utf-8
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher
from pythontk.str_utils._str_utils import StrUtils

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils.mat_manifest import MatManifest

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"

# Candidate names AppLauncher will try when no explicit path is given.
_TOOLBAG_APP_NAMES = ["toolbag", "Marmoset Toolbag 4", "Marmoset Toolbag 5"]

# FBX options tuned for Marmoset Toolbag.
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportBakeComplexAnimation": False,
}

# Allowed values for a template's ``BRIDGE_MODES`` tuple.
SEND_TO = "send_to"
ROUNDTRIP = "roundtrip"
_MODES = (SEND_TO, ROUNDTRIP)

# ``BRIDGE_MODES = (...,)`` literal -- parsed without importing the template.
_BRIDGE_MODES_RE = re.compile(
    r"^\s*BRIDGE_MODES\s*=\s*\(([^)]*)\)", re.MULTILINE
)


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


# Per-run log path derivation lives in _toolbag_helpers so the helper
# (which writes the file) and this module (which surfaces it as a link)
# share one source of truth and can't drift.
from mayatk.mat_utils.marmoset_bridge._toolbag_helpers import (  # noqa: E402
    derive_per_run_log_path,
)


# Lines starting with these prefixes are Toolbag's startup chatter (shader
# preloads, image preloads) and are too noisy to forward to the bridge
# log panel. They're harmless and arrive in bursts hundreds of lines deep.
_NOISE_PREFIXES = ("opening code ", "opening image ", "opening shader ")


def classify_log_line(line: str) -> "Optional[Tuple[str, str]]":
    """Map a Toolbag log line to ``(level, line)`` for routing into the bridge logger.

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


def _start_toolbag_log_tail(
    log_path: str,
    start_offset: int,
    process,
    logger,
    poll_interval: float = 0.4,
    file_wait_timeout: float = 60.0,
) -> "threading.Thread":
    """Tail *log_path* from *start_offset* in a daemon thread.

    Reads new content as Toolbag writes it, classifies each line, and
    emits to *logger* at the routed level so errors land in the bridge
    panel in red without the user having to open the log file. Stops
    when *process* exits.

    On a fresh Toolbag install, ``log.txt`` may not exist yet at launch
    time -- Toolbag creates it on its first write. The thread polls for
    the file's appearance up to *file_wait_timeout* seconds before
    giving up.

    Defensive: any I/O error inside the thread is swallowed so a
    diagnostic feature can't crash Maya.
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


def list_templates() -> "list[Path]":
    """Return user-visible templates in ``templates/`` (skips underscore-prefixed)."""
    return sorted(
        p for p in _TEMPLATE_DIR.glob("*.py") if not p.stem.startswith("_")
    )


def template_modes(template_path: Path) -> Tuple[str, ...]:
    """Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.

    Falls back to ``("send_to",)`` if the constant is absent so legacy templates
    keep working. We parse with a regex rather than importing because templates
    contain raw ``__KEY__`` placeholders that aren't valid Python before
    substitution.
    """
    try:
        text = template_path.read_text(encoding="utf-8")
    except OSError:
        return (SEND_TO,)
    m = _BRIDGE_MODES_RE.search(text)
    if not m:
        return (SEND_TO,)
    modes = tuple(
        item.strip().strip("'\"")
        for item in m.group(1).split(",")
        if item.strip()
    )
    valid = tuple(mode for mode in modes if mode in _MODES)
    return valid or (SEND_TO,)


def _classify_maya_chain(
    dag_path: str, high_suffix: str, low_suffix: str
) -> Optional[str]:
    """Walk *dag_path* leaf-to-root in Maya, return ``'high'``/``'low'``/None.

    Mirrors the Toolbag-side ``_classify_by_chain`` in
    :mod:`._toolbag_helpers`, but operates on Maya DAG paths via
    ``cmds.listRelatives`` -- so we can run it BEFORE the FBX export
    flattens the hierarchy.
    """
    cur = dag_path
    visited = 0
    while cur and visited < 64:
        leaf = cur.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
        if high_suffix and stem.endswith(high_suffix):
            return "high"
        if low_suffix and stem.endswith(low_suffix):
            return "low"
        parents = cmds.listRelatives(cur, parent=True, fullPath=True) or []
        cur = parents[0] if parents else None
        visited += 1
    return None


def build_bake_pairs_manifest(
    objects: Sequence[str], high_suffix: str, low_suffix: str
) -> Dict[str, str]:
    """Build the ``{mesh_short_name: 'high'|'low'}`` sidecar for the bake.

    Toolbag's FBX importer flattens parent transforms on the way in, so
    a ``bake_high`` group that the user named in Maya doesn't survive
    long enough for the Toolbag-side chain classifier to see it. We
    compute the classification HERE -- while we still have the full
    Maya parent chain -- and ship the result as a JSON sidecar that the
    rendered bake template reads after import.

    For each selected object, finds every mesh-transform descendant
    (and the object itself if it has a mesh shape), walks each one's
    Maya parent chain, and records a classification if any ancestor (or
    the mesh itself) carries *high_suffix* or *low_suffix*. Meshes with
    no matching ancestor are simply omitted -- ``split_high_low`` will
    fall through to its own chain walk / "rest is X" rules for them.
    """
    if not (high_suffix or low_suffix):
        return {}

    visited = set()
    mesh_xforms: List[str] = []
    for obj in objects:
        try:
            descendants = cmds.listRelatives(
                obj, allDescendents=True, type="transform", fullPath=True
            ) or []
        except Exception:
            descendants = []
        for x in [obj] + descendants:
            if x in visited:
                continue
            visited.add(x)
            shapes = cmds.listRelatives(
                x, shapes=True, type="mesh", fullPath=True
            ) or []
            if shapes:
                mesh_xforms.append(x)

    out: Dict[str, str] = {}
    for mesh_path in mesh_xforms:
        cls = _classify_maya_chain(mesh_path, high_suffix, low_suffix)
        if cls:
            leaf = mesh_path.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            out[leaf] = cls
    return out


def list_template_modes() -> "list[tuple[str, str]]":
    """Return ``[(stem, mode), ...]`` for every (template, mode) pairing.

    A dual-mode template appears twice -- once per mode -- so the UI can show
    one combo entry per (template, mode) pair without baking mode-awareness
    into the combo itself.
    """
    out: List[Tuple[str, str]] = []
    for path in list_templates():
        for mode in template_modes(path):
            out.append((path.stem, mode))
    return out


class MarmosetBridge(ptk.LoggingMixin):
    """Export Maya selection to Marmoset Toolbag with templated automation.

    Two operating modes per template (declared via ``BRIDGE_MODES`` in each
    ``templates/*.py``):

    * ``send_to`` -- launch Toolbag interactively, fire-and-forget. The user
      drives the rest of the workflow inside Toolbag.
    * ``roundtrip`` -- launch Toolbag headless (auto save & quit), block until
      it exits, then let Maya post-process the outputs (e.g. re-import baked
      maps). Always headless; the headless flag is ignored.

    Usage::

        MarmosetBridge().send(template="bake", mode="roundtrip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

    # How long a roundtrip is allowed to take before we give up on Toolbag.
    ROUNDTRIP_TIMEOUT = 1800  # 30 minutes; bakes can be slow on big meshes.

    def __init__(self, toolbag_path: Optional[str] = None):
        self._toolbag_path = toolbag_path

    # -- Toolbag path resolution -------------------------------------------

    @property
    def toolbag_path(self) -> Optional[str]:
        """Resolve the Toolbag executable path.

        If an explicit path was provided at init it wins. Otherwise we ask
        ``AppLauncher.find_app`` for each candidate -- and finally walk the
        standard install roots (Toolbag's installer doesn't register the exe
        under ``App Paths`` on every version).
        """
        if self._toolbag_path:
            return self._toolbag_path
        for name in _TOOLBAG_APP_NAMES:
            found = AppLauncher.find_app(name)
            if found:
                self._toolbag_path = found
                return found
        for found in self._scan_install_dirs():
            self._toolbag_path = found
            return found
        return None

    @toolbag_path.setter
    def toolbag_path(self, value: Optional[str]) -> None:
        self._toolbag_path = value

    @staticmethod
    def _scan_install_dirs():
        """Yield candidate ``toolbag.exe`` paths under standard install roots."""
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for root in roots:
            marm = Path(root) / "Marmoset"
            if not marm.is_dir():
                continue
            for sub in sorted(marm.iterdir(), reverse=True):
                if not sub.is_dir():
                    continue
                candidate = sub / "toolbag.exe"
                if candidate.is_file():
                    yield str(candidate)

    @property
    def toolbag_log_path(self) -> Optional[str]:
        """Resolve Toolbag's application log file (where script prints + tracebacks land).

        Three-tier fallback so the bridge survives major version bumps
        without hardcoding "Marmoset Toolbag 5":

        1. Parse ``Marmoset Toolbag <N>`` out of :attr:`toolbag_path` and
           try ``%LOCALAPPDATA%/Marmoset Toolbag <N>/log.txt``.
        2. Scan ``%LOCALAPPDATA%`` for any ``Marmoset Toolbag *`` folder
           containing ``log.txt`` and pick the most-recently-modified one.
        3. Return *None* if nothing is found; callers fall back to the
           per-run ``<base>.toolbag.log`` written by the helper's ``begin_log``.

        Marmoset has kept this naming convention across Toolbag 3, 4, and 5.
        """
        return resolve_toolbag_log_path(self.toolbag_path)

    # -- Public API --------------------------------------------------------

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        toolbag_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        preset_file: Optional[str] = None,
        template: str = "import",
        mode: str = SEND_TO,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Export objects, render *template* in *mode*, and hand off to Toolbag.

        Parameters:
            objects: Nodes to export. Defaults to current selection.
            output_dir: Directory for FBX / manifest / script artefacts.
                Defaults to ``<temp>/maya_marmoset_bridge``.
            output_name: Base filename (without extension).
                Defaults to the Maya scene name or ``"untitled"``.
            toolbag_exe: Explicit ``toolbag.exe`` path (per-call override).
            fbx_options: FBX MEL overrides merged on top of defaults.
            preset_file: Optional FBX export preset path.
            template: Template stem (``"import"``, ``"bake"``, ``"lookdev"``).
            mode: ``"send_to"`` (interactive, fire-and-forget) or
                ``"roundtrip"`` (headless, block + post-process). Must match
                one of the template's declared :data:`BRIDGE_MODES`.
            params: Placeholder overrides, e.g. ``{"BAKE_WIDTH": 4096}``.

        Returns:
            A result dict with ``script``, ``mode``, and -- for roundtrip --
            ``outputs`` (list of generated map paths). *None* on failure.
        """
        template_path = _TEMPLATE_DIR / f"{template}.py"
        allowed_modes = template_modes(template_path) if template_path.is_file() else ()
        if mode not in allowed_modes:
            self.logger.error(
                f"Template '{template}' does not support mode '{mode}'. "
                f"Declared modes: {allowed_modes}"
            )
            return None

        if not objects:
            objects = cmds.ls(selection=True, long=True)
        if not objects:
            self.logger.warning("Nothing selected to export.")
            return None

        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "maya_marmoset_bridge")
        os.makedirs(output_dir, exist_ok=True)

        base = output_name or self._scene_base_name()
        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")
        script_path = os.path.join(output_dir, f"{base}_{template}_{mode}.py")

        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if fbx_options:
            merged_options.update(fbx_options)

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting
        # so we get a clear FBX-export error instead of "Invalid file type".
        FbxUtils.load_plugin()

        self.logger.info("Exporting FBX ...")
        try:
            FbxUtils.export(
                file_path=fbx_path,
                objects=objects,
                preset_file=preset_file,
                options=merged_options,
                selection_only=True,
            )
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None
        self.logger.info(f"FBX written: {fbx_path}")

        self.logger.info("Building material manifest ...")
        manifest = MatManifest.build(objects)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(f"Manifest written: {manifest_path}")

        # Bake-pairs sidecar: Maya-side parent-chain classification, written
        # while we still have the full DAG (Toolbag's FBX importer flattens
        # empty parent transforms). The bake template reads this back to
        # classify meshes regardless of what survived the round trip.
        # Skipped entirely when there's nothing to record -- the template's
        # ``os.path.isfile`` check then falls through to its own chain walk
        # on the un-flattened (if any) own-name suffixes.
        from mayatk.mat_utils.marmoset_bridge import parameters as _params
        _merged_params = _params.defaults()
        _merged_params.update(params or {})
        _high_suffix = _merged_params.get("HIGH_SUFFIX", "_high") or ""
        _low_suffix = _merged_params.get("LOW_SUFFIX", "_low") or ""
        bake_pairs = build_bake_pairs_manifest(
            objects, _high_suffix, _low_suffix
        )
        if bake_pairs:
            with open(pairs_path, "w", encoding="utf-8") as fh:
                json.dump(bake_pairs, fh, indent=2)
            self.logger.info(
                f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                f"pre-classified): {pairs_path}"
            )

        script = self.render_template(
            template=template,
            mode=mode,
            fbx_path=fbx_path,
            manifest_path=manifest_path,
            pairs_path=pairs_path,
            output_dir=output_dir,
            params=params,
        )
        if script is None:
            return None

        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        self.logger.info(f"Toolbag script written: {script_path}")

        result: Dict[str, Any] = {
            "script": script_path,
            "mode": mode,
            "output_dir": output_dir,
        }

        if mode == ROUNDTRIP:
            self.logger.info(f"Running Toolbag headless (timeout {self.ROUNDTRIP_TIMEOUT}s) ...")
            outputs = self._run_roundtrip(script_path, output_dir, toolbag_exe)
            if outputs is None:
                return None
            result["outputs"] = outputs
            self._announce_outputs(template, outputs, output_dir)
        else:
            # send_to mode is fire-and-forget on Toolbag's side -- once
            # launched, the only diagnostic channel is its log.txt. Snapshot
            # the current end-of-file BEFORE launch so the tail thread reads
            # only this session's content (log.txt is append-only across
            # sessions).
            tb_log = self.toolbag_log_path
            tb_log_offset = 0
            if tb_log and os.path.isfile(tb_log):
                try:
                    tb_log_offset = os.path.getsize(tb_log)
                except OSError:
                    tb_log_offset = 0

            self.logger.info("Launching Marmoset Toolbag ...")
            proc = self._launch_toolbag(script_path, toolbag_exe)
            if proc is None:
                self.logger.error(
                    "Could not launch Marmoset Toolbag. "
                    "Pass toolbag_exe= or add toolbag to your PATH."
                )
                return None
            self.logger.info(
                f'Toolbag launched. Output folder: <a href="action://open?path={output_dir}">'
                f'{output_dir}</a>'
            )

            # Stream Toolbag's log into the bridge panel as it gets written.
            # Errors come through red (e.g. "cannot open image", "MatField
            # not found", helper's "! slot: ...") and skips come through
            # yellow -- the user sees what went wrong without having to
            # open a separate log file.
            if tb_log:
                _start_toolbag_log_tail(
                    tb_log, tb_log_offset, proc, self.logger
                )
                self.logger.info(
                    f'Streaming Toolbag log: '
                    f'<a href="action://open?path={tb_log}">{tb_log}</a>'
                )

            # The per-run <base>.toolbag.log captures only the helper's own
            # prints (deterministic). Surface it as a fallback link in case
            # the tail thread misses anything (e.g. encoding hiccups).
            per_run = derive_per_run_log_path(manifest_path)
            self.logger.info(
                f'Per-run log: '
                f'<a href="action://open?path={per_run}">{per_run}</a>'
            )

        return result

    # -- Template rendering -----------------------------------------------

    def render_template(
        self,
        template: str,
        fbx_path: str,
        manifest_path: str,
        output_dir: str,
        mode: str = SEND_TO,
        params: Optional[Dict[str, Any]] = None,
        headless: Optional[bool] = None,
        pairs_path: Optional[str] = None,
    ) -> Optional[str]:
        """Return the rendered Toolbag Python script body, or *None* on miss.

        *mode* drives whether Toolbag should save+quit (``roundtrip``) or stay
        open (``send_to``). The legacy ``headless`` kwarg still works -- if
        passed, it overrides the mode-derived default.
        """
        template_path = _TEMPLATE_DIR / f"{template}.py"
        if not template_path.is_file():
            available = sorted(p.stem for p in list_templates())
            self.logger.error(
                f"Template '{template}' not found at {template_path}. "
                f"Available: {available}"
            )
            return None

        body = template_path.read_text(encoding="utf-8")

        from mayatk.mat_utils.marmoset_bridge import parameters as _params

        merged = _params.defaults()
        merged.update(params or {})
        param_ctx = _params.render_context(merged)

        if headless is None:
            headless = mode == ROUNDTRIP
        save_path = ""
        if headless:
            save_path = os.path.splitext(fbx_path)[0] + ".tbscene"

        context = {
            "FBX_PATH": fbx_path.replace("\\", "/"),
            "MANIFEST_PATH": manifest_path.replace("\\", "/"),
            "PAIRS_PATH": (pairs_path or "").replace("\\", "/"),
            "OUTPUT_DIR": output_dir.replace("\\", "/"),
            "SAVE_PATH": save_path.replace("\\", "/"),
            "SHOULD_QUIT": "True" if headless else "False",
            # Path to the package directory; rendered scripts sys.path.insert
            # this so they can ``from _toolbag_helpers import ...``.
            "TOOLBAG_HELPERS_DIR": str(_PKG_DIR).replace("\\", "/"),
        }
        context.update(param_ctx)

        return StrUtils.replace_delimited(body, context)

    # -- Roundtrip --------------------------------------------------------

    # Padding subtracted from ``time.time()`` before launching Toolbag so
    # files written within the first moments of the run survive the mtime
    # filter even on filesystems that round mtime (FAT32: 2s, some SMB
    # shares: 1s). Two seconds covers the worst case we've seen.
    _MTIME_FILTER_PAD_SECONDS = 2.0

    def _run_roundtrip(
        self,
        script_path: str,
        output_dir: str,
        exe: Optional[str] = None,
    ) -> Optional[List[str]]:
        """Run Toolbag blocking, then return the list of generated map paths."""
        toolbag = exe or self.toolbag_path
        if not toolbag:
            self.logger.error("Marmoset Toolbag not found; cannot roundtrip.")
            return None

        # mtime floor for "new this session". A path-based pre/post diff
        # missed overwrites entirely -- Toolbag replaces ``bake_*.psd`` in
        # place on re-bakes, so the set diff was empty even though every
        # file got fresh content.
        mtime_floor = time.time() - self._MTIME_FILTER_PAD_SECONDS

        try:
            result = AppLauncher.run(
                toolbag,
                args=["-run", script_path],
                timeout=self.ROUNDTRIP_TIMEOUT,
            )
        except Exception as e:
            self.logger.error(f"Toolbag roundtrip failed: {e}")
            return None

        # Replay Toolbag's stdout through the same classifier the send_to
        # tail uses, so roundtrip diagnostics show up colour-coded in the
        # bridge panel instead of being dropped on the floor.
        stdout = getattr(result, "stdout", "") or ""
        if stdout:
            dispatch_log_lines(stdout.splitlines(), self.logger)

        if getattr(result, "returncode", 0) != 0:
            self.logger.error(
                f"Toolbag exited with code {result.returncode}. See stdout above."
            )

        return sorted(self._snapshot_outputs(output_dir, since=mtime_floor))

    @staticmethod
    def _snapshot_outputs(
        output_dir: str, since: Optional[float] = None
    ) -> "set[str]":
        """Return the set of map-like files under *output_dir*.

        When *since* is given, restrict to files whose mtime is at or
        after that Unix-epoch cutoff. ``None`` (default) returns every
        map-like file regardless of mtime -- used by callers that just
        want to enumerate. Roundtrip passes the launch time so files
        Toolbag wrote / overwrote this session come back while
        pre-existing untouched files don't.

        ``.psd`` is included because Toolbag's BakerObject writes each
        enabled map as a layered PSD (one file per map) using its own
        ``<basename>_<MapSuffix>.psd`` naming convention. Without .psd
        in this list the post-bake diff is empty and the bridge wrongly
        reports "no new map files" even after a successful bake.
        """
        exts = (".tga", ".tif", ".tiff", ".png", ".exr", ".jpg", ".psd")
        snap: List[str] = []
        for root, _, files in os.walk(output_dir):
            for f in files:
                if not f.lower().endswith(exts):
                    continue
                full = os.path.join(root, f)
                if since is not None:
                    try:
                        if os.path.getmtime(full) < since:
                            continue
                    except OSError:
                        continue
                snap.append(full)
        return set(snap)

    def _announce_outputs(
        self, template: str, outputs: Sequence[str], output_dir: str
    ) -> None:
        """Log roundtrip outputs as clickable ``action://`` URIs for the UI panel."""
        if not outputs:
            self.logger.warning(
                f"'{template}' roundtrip produced no new map files in "
                f"{output_dir}. Check the Toolbag stdout above for bake errors."
            )
            return
        self.logger.info(f"Roundtrip generated {len(outputs)} map file(s):")
        for path in outputs:
            self.logger.info(f'  <a href="action://open?path={path}">{path}</a>')
        self.logger.info(
            f'Open output folder: '
            f'<a href="action://open?path={output_dir}">{output_dir}</a>'
        )

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"

    def _launch_toolbag(self, script_path: str, exe: Optional[str] = None):
        """Launch Toolbag with ``-run <script>``.

        Resolution order:

        1. Explicit *exe* (per-call override). No fallback -- if the caller
           hands us a specific path and it fails, we return *None* rather
           than silently launching some other Toolbag we found on PATH.
        2. ``self.toolbag_path`` (cached / scanned), with the candidate-name
           list as fallback so a user without an App-Paths entry still gets
           launched.

        Returns the ``subprocess.Popen`` object or *None*.
        """
        if exe:
            return AppLauncher.launch(exe, args=["-run", script_path])

        candidates: List[str] = []
        if self.toolbag_path:
            candidates.append(self.toolbag_path)
        for name in _TOOLBAG_APP_NAMES:
            if name not in candidates:
                candidates.append(name)

        for name in candidates:
            proc = AppLauncher.launch(name, args=["-run", script_path])
            if proc:
                return proc
        return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MarmosetBridge()
    bridge.send(template="bake", mode=ROUNDTRIP)
