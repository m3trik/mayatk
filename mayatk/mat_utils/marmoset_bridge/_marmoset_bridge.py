# !/usr/bin/python
# coding=utf-8
import json
import logging
import os
import re
import tempfile
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

        script = self.render_template(
            template=template,
            mode=mode,
            fbx_path=fbx_path,
            manifest_path=manifest_path,
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
            "OUTPUT_DIR": output_dir.replace("\\", "/"),
            "SAVE_PATH": save_path.replace("\\", "/"),
            "SHOULD_QUIT": "True" if headless else "False",
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
        # Snapshot the output dir contents so we can subtract pre-existing
        # files from the "newly generated" list.
        pre_existing = self._snapshot_outputs(output_dir)

        toolbag = exe or self.toolbag_path
        if not toolbag:
            self.logger.error("Marmoset Toolbag not found; cannot roundtrip.")
            return None

        try:
            result = AppLauncher.run(
                toolbag,
                args=["-run", script_path],
                timeout=self.ROUNDTRIP_TIMEOUT,
            )
        except Exception as e:
            self.logger.error(f"Toolbag roundtrip failed: {e}")
            return None

        if getattr(result, "returncode", 0) != 0:
            self.logger.error(
                f"Toolbag exited with code {result.returncode}. See stdout/stderr above."
            )

        post = self._snapshot_outputs(output_dir)
        return sorted(post - pre_existing)

    @staticmethod
    def _snapshot_outputs(output_dir: str) -> "set[str]":
        """Return the set of map-like files currently under *output_dir*."""
        exts = (".tga", ".tif", ".tiff", ".png", ".exr", ".jpg")
        snap: List[str] = []
        for root, _, files in os.walk(output_dir):
            for f in files:
                if f.lower().endswith(exts):
                    snap.append(os.path.join(root, f))
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
