# !/usr/bin/python
# coding=utf-8
"""Drive Marmoset Toolbag from the outside -- launch + templated automation.

:class:`MarmosetEngine` is the DCC-agnostic core: it discovers/launches
Toolbag, renders a bundled template with substituted parameters, and
either hands off interactively (``send_to``) or runs headless and
post-processes outputs (``roundtrip``). It takes plain values -- an
already-exported model path, an optional materials-manifest path, and a
plain params dict -- so any host can compose it (the Maya bridge in
mayatk, the standalone Switchboard panel in extapps, a CLI, a test).
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher
from pythontk.core_utils import script_template
from pythontk.str_utils._str_utils import StrUtils

from . import template_params
from .toolbag_log import (
    resolve_toolbag_log_path,
    dispatch_log_lines,
    start_toolbag_log_tail,
)
# Per-run log path derivation lives in _toolbag_helpers so the helper
# (which writes the file) and this module (which surfaces it as a link)
# share one source of truth and can't drift.
from ._toolbag_helpers import derive_per_run_log_path


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"

# Candidate names AppLauncher will try when no explicit path is given.
_TOOLBAG_APP_NAMES = ("toolbag", "Marmoset Toolbag 4", "Marmoset Toolbag 5")
# Install-dir fallback: Toolbag's installer doesn't register the exe under
# ``App Paths`` on every version. Newest ``Marmoset\<version>`` folder wins.
_TOOLBAG_SCAN_GLOBS = (r"{program_files}\Marmoset\*\toolbag.exe",)

# Allowed values for a template's ``BRIDGE_MODES`` tuple.
SEND_TO = "send_to"
ROUNDTRIP = "roundtrip"
_MODES = (SEND_TO, ROUNDTRIP)


# ---------------------------------------------------------------------------
# Template discovery (module-level so UI layers can list templates without a
# live engine instance). Thin wrappers over the shared
# :mod:`pythontk.core_utils.script_template` helpers (``_MODES`` allowed).
# ---------------------------------------------------------------------------

def list_templates() -> List[Path]:
    """Return user-visible templates in ``templates/`` (skips underscore-prefixed)."""
    return script_template.list_templates(_TEMPLATE_DIR, ".py")


def template_modes(template_path: Path) -> Tuple[str, ...]:
    """Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.

    Falls back to ``("send_to",)`` if the constant is absent so legacy templates
    keep working.
    """
    return script_template.template_modes(template_path, _MODES)


def list_template_modes() -> List[Tuple[str, str]]:
    """Return ``[(stem, mode), ...]`` for every (template, mode) pairing.

    A dual-mode template appears twice -- once per mode -- so a UI can show
    one combo entry per (template, mode) pair without baking mode-awareness
    into the combo itself.
    """
    return script_template.list_template_modes(_TEMPLATE_DIR, ".py", _MODES)


class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin):
    """Export-agnostic Marmoset Toolbag automation -- a hand-off :class:`pythontk.Deliverer`.

    The launch-or-roundtrip delivery Strategy for the Maya hand-off bridge (and,
    via its standalone :meth:`send`, any host that already has an exported model):
    discover/launch Toolbag, render a bundled template with substituted params, and
    either hand off interactively (``send_to``) or run headless and post-process the
    outputs (``roundtrip``). Two operating modes per template (declared via
    ``BRIDGE_MODES`` in each ``templates/*.py``):

    * ``send_to`` -- launch Toolbag interactively, fire-and-forget. The user
      drives the rest of the workflow inside Toolbag.
    * ``roundtrip`` -- launch Toolbag headless (auto save & quit), block until
      it exits, then post-process the outputs (e.g. re-collect baked maps).
      Always headless; the headless flag is ignored.

    As a deliverer it is plugged into :class:`mayatk.mat_utils.MarmosetBridge`
    (which produces the FBX + manifests); standalone it composes directly::

        MarmosetEngine().send(model_path="C:/scan/welding.obj", template="lookdev")
        MarmosetEngine().send(model_path=fbx, manifest_path=man, template="bake",
                              mode="roundtrip")
    """

    # How long a roundtrip is allowed to take before we give up on Toolbag.
    ROUNDTRIP_TIMEOUT = 1800  # 30 minutes; bakes can be slow on big meshes.

    # Padding subtracted from ``time.time()`` before launching Toolbag so
    # files written within the first moments of the run survive the mtime
    # filter even on filesystems that round mtime (FAT32: 2s, some SMB
    # shares: 1s). Two seconds covers the worst case we've seen.
    _MTIME_FILTER_PAD_SECONDS = 2.0

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
        found = AppLauncher.resolve_app_path(
            app_names=_TOOLBAG_APP_NAMES,
            scan_globs=_TOOLBAG_SCAN_GLOBS,
        )
        if found:
            self._toolbag_path = found
        return found

    @toolbag_path.setter
    def toolbag_path(self, value: Optional[str]) -> None:
        self._toolbag_path = value

    @property
    def toolbag_log_path(self) -> Optional[str]:
        """Resolve Toolbag's application log file (script prints + tracebacks).

        Three-tier fallback so the engine survives major version bumps
        without hardcoding "Marmoset Toolbag 5"; see
        :func:`.toolbag_log.resolve_toolbag_log_path`.
        """
        return resolve_toolbag_log_path(self.toolbag_path)

    # -- Deliverer Strategy hooks ------------------------------------------

    def preflight(self, bridge, request) -> bool:
        """Validate the (template, mode) before the bridge produces its payload."""
        template_path = _TEMPLATE_DIR / f"{request.template}.py"
        allowed = template_modes(template_path) if template_path.is_file() else ()
        if request.mode not in allowed:
            bridge.logger.error(
                f"Template '{request.template}' does not support mode "
                f"'{request.mode}'. Declared modes: {allowed}"
            )
            return False
        return True

    def deliver(self, bridge, payload, request) -> Optional[Dict[str, Any]]:
        """Hand the produced model + manifests to Toolbag via :meth:`send`.

        The :class:`pythontk.Payload` carries the FBX (``primary``) and the
        ``manifest`` / ``pairs`` sidecar paths in ``extras``; the orchestration
        knobs (``output_dir`` / ``output_name`` / ``toolbag_exe``) ride in
        :attr:`request.extras`.
        """
        return self.send(
            model_path=payload.primary,
            manifest_path=payload.extras.get("manifest"),
            pairs_path=payload.extras.get("pairs"),
            output_dir=request.get("output_dir"),
            output_name=request.get("output_name"),
            toolbag_exe=request.get("toolbag_exe"),
            template=request.template,
            mode=request.mode,
            params=request.params,
        )

    # -- Public API --------------------------------------------------------

    def send(
        self,
        model_path: str,
        manifest_path: Optional[str] = None,
        pairs_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        toolbag_exe: Optional[str] = None,
        template: str = "import",
        mode: str = SEND_TO,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Render *template* in *mode* against *model_path* and hand off to Toolbag.

        Parameters:
            model_path: Path to an existing model file (FBX/OBJ/USD/...) to
                import in Toolbag. Required.
            manifest_path: Optional materials-manifest JSON sidecar
                (``{"materials": {mat: {slot: tex_path}}}``) that templates
                wire onto the imported materials. ``None`` -> no wiring.
            pairs_path: Optional high/low pre-classification JSON sidecar
                consumed by the bake template.
            output_dir: Directory for the rendered script / outputs.
                Defaults to ``<temp>/marmoset_bridge``.
            output_name: Base filename (no extension). Defaults to the
                model file's stem.
            toolbag_exe: Explicit ``toolbag.exe`` path (per-call override).
            template: Template stem (``"import"``, ``"bake"``, ``"lookdev"``).
            mode: ``"send_to"`` or ``"roundtrip"``. Must match one of the
                template's declared :data:`BRIDGE_MODES`.
            params: Plain ``{KEY: value}`` overrides merged on top of
                :data:`template_params.DEFAULTS`.

        Returns:
            A result dict with ``script``, ``mode``, ``output_dir``, and --
            for roundtrip -- ``outputs`` (generated map paths). *None* on
            failure.
        """
        template_path = _TEMPLATE_DIR / f"{template}.py"
        allowed_modes = template_modes(template_path) if template_path.is_file() else ()
        if mode not in allowed_modes:
            self.logger.error(
                f"Template '{template}' does not support mode '{mode}'. "
                f"Declared modes: {allowed_modes}"
            )
            return None

        if not model_path or not os.path.isfile(model_path):
            self.logger.error(f"Model file not found: {model_path}")
            return None

        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "marmoset_bridge")
        os.makedirs(output_dir, exist_ok=True)

        base = output_name or os.path.splitext(os.path.basename(model_path))[0]
        script_path = os.path.join(output_dir, f"{base}_{template}_{mode}.py")

        script = self.render_template(
            template=template,
            mode=mode,
            model_path=model_path,
            manifest_path=manifest_path or "",
            pairs_path=pairs_path,
            output_dir=output_dir,
            params=params,
        )
        if script is None:
            return None

        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        self.logger.info(
            f'Toolbag script written: '
            f'<a href="action://open?path={script_path}">{script_path}</a>'
        )

        result: Dict[str, Any] = {
            "script": script_path,
            "mode": mode,
            "output_dir": output_dir,
        }

        if mode == ROUNDTRIP:
            self.logger.info(
                f"Running Toolbag headless (timeout {self.ROUNDTRIP_TIMEOUT}s) ..."
            )
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
                f'Toolbag launched. Output folder: '
                f'<a href="action://open?path={output_dir}">{output_dir}</a>'
            )

            # Stream Toolbag's log into the caller's logger as it's written.
            if tb_log:
                start_toolbag_log_tail(tb_log, tb_log_offset, proc, self.logger)
                self.logger.info(
                    f'Streaming Toolbag log: '
                    f'<a href="action://open?path={tb_log}">{tb_log}</a>'
                )

            # The per-run <base>.toolbag.log captures only the helper's own
            # prints (deterministic). Surface it as a fallback link.
            if manifest_path:
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
        model_path: str,
        manifest_path: str,
        output_dir: str,
        mode: str = SEND_TO,
        params: Optional[Dict[str, Any]] = None,
        headless: Optional[bool] = None,
        pairs_path: Optional[str] = None,
    ) -> Optional[str]:
        """Return the rendered Toolbag Python script body, or *None* on miss.

        *params* is a plain ``{KEY: value}`` dict merged over
        :data:`template_params.DEFAULTS` and formatted into Python-literal
        token substitutions -- no UI/widget knowledge required.

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

        merged = template_params.defaults()
        merged.update(params or {})
        param_ctx = template_params.to_context(merged)

        if headless is None:
            headless = mode == ROUNDTRIP
        save_path = ""
        if headless:
            save_path = os.path.splitext(model_path)[0] + ".tbscene"

        context = {
            "MODEL_PATH": model_path.replace("\\", "/"),
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
        # caller's logger instead of being dropped on the floor.
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
        map-like file regardless of mtime.

        ``.psd`` is included because Toolbag's BakerObject writes each
        enabled map as a layered PSD (one file per map).
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
        self, template: str, outputs, output_dir: str
    ) -> None:
        """Log roundtrip outputs as clickable ``action://`` URIs for a UI panel."""
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
    import sys

    if len(sys.argv) > 1:
        MarmosetEngine().send(model_path=sys.argv[1], template="lookdev")
