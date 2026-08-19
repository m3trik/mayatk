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

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher
from pythontk.core_utils import script_template
from pythontk.str_utils._str_utils import StrUtils

from . import template_params
from .toolbag_log import ToolbagLog

# Per-run log path derivation lives in _toolbag_helpers so the helper
# (which writes the file) and this module (which surfaces it as a link)
# share one source of truth and can't drift.
from ._toolbag_helpers import ToolbagHelpers


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"

# Declarative Toolbag discovery. ONE AppSpec carries the candidate names, the
# install-dir fallback (Toolbag's installer doesn't register the exe under ``App
# Paths`` on every version; newest ``Marmoset\<version>`` folder wins) AND the
# user-facing "couldn't find it" sentence -- so launch, the availability gate that
# greys the panel's launch button, and the message explaining why all read one
# declaration instead of three copies that drift.
APP = ptk.AppSpec(
    name="Marmoset Toolbag",
    app_names=("toolbag", "Marmoset Toolbag 4", "Marmoset Toolbag 5"),
    scan_globs=(r"{program_files}\Marmoset\*\toolbag.exe",),
    not_found_msg=(
        "Marmoset Toolbag not found. Install it, or set "
        "MarmosetBridge().toolbag_path to the executable."
    ),
)
# Still a name of its own: the LAUNCH fallback below iterates the candidates and
# hands each to ``AppLauncher.launch``, which is a different call than discovery.
_TOOLBAG_APP_NAMES = APP.app_names

# The mode vocabulary a template's ``BRIDGE_MODES`` tuple may name -- taken from
# ``script_template``, never spelled here. These strings are an ON-DISK contract shared by
# every bridge in the ecosystem, so a local copy is a second dialect of a file format, and
# that is exactly how this module's old ``roundtrip`` drifted from the canonical
# ``round_trip``. Templates carrying the old spelling still load: ``declared_modes`` folds
# it to the canon on the way in (``script_template._MODE_ALIASES``).
SEND_TO = script_template.SEND_TO
ROUND_TRIP = script_template.ROUND_TRIP
#: Deprecated alias for :data:`ROUND_TRIP`, kept because it was a public export. Bound to
#: the canonical value, so the two cannot drift apart.
ROUNDTRIP = ROUND_TRIP
_MODES = (SEND_TO, ROUND_TRIP)


# ---------------------------------------------------------------------------
# Template discovery (module-level so UI layers can list templates without a
# live engine instance). Thin wrappers over the shared
# :mod:`pythontk.core_utils.script_template` helpers (``_MODES`` allowed).
# ---------------------------------------------------------------------------


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
                              mode="round_trip")
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
        found = APP.path
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
        return ToolbagLog.resolve_toolbag_log_path(self.toolbag_path)

    # -- Deliverer Strategy hooks ------------------------------------------

    def preflight(self, bridge, request) -> bool:
        """Validate the (template, mode) before the bridge produces its payload."""
        template_path = _TEMPLATE_DIR / f"{request.template}.py"
        allowed = (
            MarmosetEngine.template_modes(template_path)
            if template_path.is_file()
            else ()
        )
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
        knobs (``output_dir`` / ``texture_dir`` / ``output_name`` /
        ``toolbag_exe``) ride in :attr:`request.extras`.
        """
        return self.send(
            model_path=payload.primary,
            manifest_path=payload.extras.get("manifest"),
            pairs_path=payload.extras.get("pairs"),
            source_model_path=payload.extras.get("source_model"),
            output_dir=request.get("output_dir"),
            texture_dir=request.get("texture_dir"),
            texture_set_aliases=request.get("texture_set_aliases"),
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
        source_model_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        texture_dir: Optional[str] = None,
        texture_set_aliases: Optional[Dict[str, str]] = None,
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
            source_model_path: Optional companion model file holding the bake
                *source* geometry (the scene's Bake Source set). When set, the
                bake template imports it separately and parents it into the
                baker's High container -- explicit classification that
                survives identical mesh names on both sides.
            output_dir: Directory for the rendered script and handoff
                artifacts. Defaults to a swept ``<temp>`` handoff dir.
            texture_dir: Where a roundtrip's baked MAPS land. Defaults to
                *output_dir*. A host with a project texture folder points this
                at it (the Maya bridge: ``<sourceimages>/baked``) so the maps
                are written where the scene already references textures from.
            texture_set_aliases: ``{texture set name: name to file it under}``.
                Toolbag names each output after the material it baked, so a
                host whose material names drift between runs (Maya's
                ``<mat>_BAKED`` on a re-bake) would get a fresh generation of
                map files each time. Renaming them afterwards is not an
                option -- these are production textures the moment they land
                -- so the swap happens on the way OUT of the scratch dir,
                where nothing has been published yet.
            output_name: Base filename (no extension). Defaults to the
                model file's stem.
            toolbag_exe: Explicit ``toolbag.exe`` path (per-call override).
            template: Template stem (``"import"``, ``"bake"``, ``"lookdev"``).
            mode: ``"send_to"`` or ``"round_trip"``. Must match one of the
                template's declared :data:`BRIDGE_MODES`.
            params: Plain ``{KEY: value}`` overrides merged on top of
                :data:`template_params.DEFAULTS`.

        Returns:
            A result dict with ``script``, ``mode``, ``output_dir``, and --
            for roundtrip -- ``outputs`` (generated map paths). *None* on
            failure.
        """
        template_path = _TEMPLATE_DIR / f"{template}.py"
        allowed_modes = (
            MarmosetEngine.template_modes(template_path)
            if template_path.is_file()
            else ()
        )
        if mode not in allowed_modes:
            self.logger.error(
                f"Template '{template}' does not support mode '{mode}'. "
                f"Declared modes: {allowed_modes}"
            )
            return None

        if not model_path or not os.path.isfile(model_path):
            self.logger.error(f"Model file not found: {model_path}")
            return None

        if source_model_path and not os.path.isfile(source_model_path):
            self.logger.warning(
                f"Bake-source model file not found (ignored): {source_model_path}"
            )
            source_model_path = None

        if not output_dir:
            # Detached policy: Toolbag reads the artifacts after we return, so
            # there is no deterministic delete -- allocation sweeps stale
            # leftovers of the same prefix instead (see ptk.TempArtifacts).
            output_dir = ptk.TempArtifacts("marmoset_bridge", policy="detached").dir_path(
                name="handoff"
            )
        os.makedirs(output_dir, exist_ok=True)

        base = output_name or os.path.splitext(os.path.basename(model_path))[0]
        script_path = os.path.join(output_dir, f"{base}_{template}_{mode}.py")

        # Roundtrips bake into a LOCAL scratch dir and move the results
        # afterwards: Toolbag writing straight to a cloud-synced output dir
        # has been observed to drop a file's leading bytes (a PNG landing
        # without its signature). The move is a plain buffered copy we can
        # verify; on failure the scratch dir is kept for inspection.
        bake_artifacts: Optional[ptk.TempArtifacts] = None
        bake_dir = output_dir
        if mode == ROUND_TRIP:
            bake_artifacts = ptk.TempArtifacts("marmoset_bake", policy="scoped")
            bake_dir = bake_artifacts.dir_path()

        script = self.render_template(
            template=template,
            mode=mode,
            model_path=model_path,
            manifest_path=manifest_path or "",
            pairs_path=pairs_path,
            source_model_path=source_model_path,
            output_dir=bake_dir,
            params=params,
        )
        if script is None:
            return None

        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        self.logger.info(
            f"Toolbag script written: "
            f'<a href="action://open?path={script_path}">{script_path}</a>'
        )

        # Production maps get a destination of their own: the handoff
        # artifacts (script, FBX, .tbscene) belong beside the scene, while the
        # MAPS are project textures and belong wherever the host's materials
        # reference textures from. A host with such a folder names it -- and
        # what landing them anywhere else costs is that host's story, told
        # where it applies (the Maya bridge's ``baked_texture_dir``).
        map_dir = texture_dir or output_dir

        result: Dict[str, Any] = {
            "script": script_path,
            "mode": mode,
            "output_dir": output_dir,
            "texture_dir": map_dir,
        }

        if mode == ROUND_TRIP:
            self.logger.info(
                f"Running Toolbag headless (timeout {self.ROUNDTRIP_TIMEOUT}s) ..."
            )
            outputs = self._run_roundtrip(script_path, bake_dir, toolbag_exe)
            if outputs is None:
                return None
            if bake_artifacts is not None:
                # Bake outputs carry the template's constant 'bake_' stem
                # (never the scene name -- the texture set / material is the
                # identity); the stem is stripped here so production files
                # land as <material>_<suffix>.<ext>.
                outputs, clean = self._relocate_outputs(
                    outputs,
                    bake_dir,
                    map_dir,
                    strip_stem=(
                        f"{template_params.TemplateParams.BAKE_OUTPUT_STEM}_"
                        if template == "bake"
                        else ""
                    ),
                    set_aliases=texture_set_aliases,
                )
                if clean:
                    bake_artifacts.cleanup()
                else:
                    self.logger.warning(
                        f"Some baked maps could not be verified after the "
                        f"copy; scratch dir kept: {bake_dir}"
                    )
            result["outputs"] = outputs
            self._announce_outputs(template, outputs, map_dir)
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
                f"Toolbag launched. Output folder: "
                f'<a href="action://open?path={output_dir}">{output_dir}</a>'
            )

            # Stream Toolbag's log into the caller's logger as it's written.
            if tb_log:
                ToolbagLog.start_toolbag_log_tail(
                    tb_log, tb_log_offset, proc, self.logger
                )
                self.logger.info(
                    f"Streaming Toolbag log: "
                    f'<a href="action://open?path={tb_log}">{tb_log}</a>'
                )

            # The per-run <base>.toolbag.log captures only the helper's own
            # prints (deterministic). Surface it as a fallback link.
            if manifest_path:
                per_run = ToolbagHelpers.derive_per_run_log_path(manifest_path)
                self.logger.info(
                    f'Per-run log: <a href="action://open?path={per_run}">{per_run}</a>'
                )

        return result

    # -- Template rendering -----------------------------------------------

    def _auto_map_roster(self, manifest_path: Optional[str]) -> Dict[str, bool]:
        """The ``AUTO_MAPS`` map roster read off the material manifest.

        Host-side rather than in the template: the roster also decides the
        bake's bit depth (``derive_bake_values``), and the panel's own log is
        where the user can see what auto resolved to before Toolbag launches.
        A missing or unreadable manifest degrades to the geometry maps with a
        warning -- silently baking the fixed roster instead would contradict
        the toggle the user just set.

        The log line states that the per-map toggles are not read, because
        ``AUTO_MAPS`` defaults ON and a caller can supersede itself without
        ever having heard of it. Deliberately NOT a warning naming the
        conflicting toggles: a panel send reports every parameter it holds,
        visible or not, so that would fire on almost every ordinary bake --
        the map rows are already greyed on screen, which says the same thing
        without crying wolf on the primary path.
        """
        manifest: Dict[str, Any] = {}
        if manifest_path and os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh) or {}
            except (OSError, ValueError) as e:
                self.logger.warning(f"Auto Maps: could not read the manifest ({e}).")
        elif manifest_path:
            self.logger.warning(
                f"Auto Maps: material manifest not found ({manifest_path})."
            )
        roster = template_params.TemplateParams.derive_auto_maps(manifest)
        # Named by the map TAXONOMY, not the token: those names are the
        # filename suffixes the bake writes, so the log doubles as the list of
        # files to expect ("MAP_AO".title() would have said "Ao").
        enabled = sorted(
            template_params.TemplateParams.MAP_KEY_TYPES[key]
            for key, on in roster.items()
            if on
        )
        self.logger.info(
            f"Auto Maps: baking {', '.join(enabled)} — from the source "
            f"materials' wired textures; the per-map toggles are not read "
            f"(pass AUTO_MAPS=False to choose the roster by hand)."
        )
        return roster

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
        source_model_path: Optional[str] = None,
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
            available = sorted(p.stem for p in MarmosetEngine.list_templates())
            self.logger.error(
                f"Template '{template}' not found at {template_path}. "
                f"Available: {available}"
            )
            return None

        body = template_path.read_text(encoding="utf-8")

        merged = template_params.TemplateParams.defaults()
        merged.update(params or {})
        # AUTO_MAPS: the map roster comes from what the materials actually
        # carry, so it is resolved HERE -- before the managed values below,
        # which read the enabled maps to pick the bake's bit depth. Gated on
        # the template DECLARING the token: a panel reports every parameter it
        # holds, visible or not, so a lookdev send carries whatever the user
        # last left this toggle on and would otherwise log an Auto Maps line
        # (and re-read a manifest) for a template with no maps to enable.
        if merged.get("AUTO_MAPS") and "__AUTO_MAPS__" in body:
            merged.update(self._auto_map_roster(manifest_path))
        # Managed bake tokens (edge padding, bit depth): derived from the
        # in-house primitives, overwriting any caller value -- these have a
        # single source of truth, not a widget.
        merged.update(template_params.TemplateParams.derive_bake_values(merged))
        param_ctx = template_params.TemplateParams.to_context(merged)

        if headless is None:
            headless = mode == ROUND_TRIP
        save_path = ""
        if headless:
            save_path = os.path.splitext(model_path)[0] + ".tbscene"

        context = {
            "MODEL_PATH": model_path.replace("\\", "/"),
            "MANIFEST_PATH": manifest_path.replace("\\", "/"),
            "PAIRS_PATH": (pairs_path or "").replace("\\", "/"),
            "SOURCE_MODEL_PATH": (source_model_path or "").replace("\\", "/"),
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
            ToolbagLog.dispatch_log_lines(stdout.splitlines(), self.logger)

        if getattr(result, "returncode", 0) != 0:
            self.logger.error(
                f"Toolbag exited with code {result.returncode}. See stdout above."
            )

        return sorted(self._snapshot_outputs(output_dir, since=mtime_floor))

    def _relocate_outputs(
        self,
        outputs,
        src_root: str,
        dst_root: str,
        strip_stem: str = "",
        set_aliases: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[str], bool]:
        """Copy baked maps from the local scratch dir into *dst_root*.

        Returns ``(final_paths, all_verified)``. Each copy is size-verified
        (with one retry) -- the whole point of the scratch hop is that a
        cloud-synced destination can mangle a direct app write, so a copy we
        cannot verify keeps its scratch original around for recovery.

        *strip_stem*: leading basename prefix removed on the way over (the
        bake template writes under a constant ``bake_`` stem; the production
        files should carry only the texture-set / material identity).

        *set_aliases*: ``{set name: name to file it under}``, applied to the
        texture-set token that leads the remaining basename. This copy is the
        LAST point at which a map's name is still private -- past it the file
        is a production texture some material references, and renaming it
        would be a change to the project rather than to a scratch file.
        """
        import shutil

        final: List[str] = []
        all_ok = True
        if not outputs:
            # A bake that produced nothing must not leave an empty folder
            # behind in the destination -- which is a project texture folder,
            # not scratch (see the caller's ``map_dir``).
            return final, all_ok
        os.makedirs(dst_root, exist_ok=True)
        for src in sorted(outputs):
            rel = os.path.relpath(src, src_root)
            if strip_stem:
                head, base = os.path.split(rel)
                if base.startswith(strip_stem) and len(base) > len(strip_stem):
                    rel = os.path.join(head, base[len(strip_stem):])
            if set_aliases:
                head, base = os.path.split(rel)
                # Longest set name first: an alias for ``M_BAKED`` has to be
                # tried before one for ``M`` could claim its prefix.
                for name in sorted(set_aliases, key=len, reverse=True):
                    if base.startswith(f"{name}_"):
                        rel = os.path.join(
                            head, f"{set_aliases[name]}{base[len(name):]}"
                        )
                        break
            dst = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            ok = False
            for attempt in range(2):
                try:
                    shutil.copy2(src, dst)
                    if os.path.getsize(dst) == os.path.getsize(src):
                        ok = True
                        break
                except OSError as e:
                    self.logger.warning(f"Copy failed for {rel}: {e}")
                if attempt == 0:
                    # A cloud-synced destination can briefly lag behind its
                    # own write; give it a beat before the one retry.
                    time.sleep(0.5)
            if ok:
                final.append(dst)
            else:
                all_ok = False
                final.append(src)  # surface the readable scratch copy instead
        return final, all_ok

    @staticmethod
    def _snapshot_outputs(output_dir: str, since: Optional[float] = None) -> "set[str]":
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

    def _announce_outputs(self, template: str, outputs, output_dir: str) -> None:
        """Log roundtrip outputs as clickable ``action://`` URIs for a UI panel."""
        if not outputs:
            self.logger.warning(
                f"'{template}' roundtrip produced no new map files in "
                f"{output_dir}. Check the Toolbag stdout above for bake errors."
            )
            return
        # ONE grouped record, not a line per file: every log record renders as
        # its own paragraph in the panel, so a 12-map bake read as 14
        # blank-line-separated sections. Links come from ``log_link`` rather
        # than hand-written <a> markup so the href escaping stays in one place.
        folder_link = self.logger.log_link(output_dir, "open", path=output_dir)
        self.logger.log_group(
            f"Roundtrip generated {len(outputs)} map file(s) — {folder_link}",
            [
                self.logger.log_link(os.path.basename(p), "open", path=p)
                for p in outputs
            ],
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

    @staticmethod
    def list_templates() -> List[Path]:
        """Return user-visible templates in ``templates/`` (skips underscore-prefixed)."""
        return script_template.ScriptTemplate.list_templates(_TEMPLATE_DIR, ".py")

    @staticmethod
    def template_modes(template_path: Path) -> Tuple[str, ...]:
        """Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.

        Falls back to ``("send_to",)`` if the constant is absent so legacy templates
        keep working.
        """
        return script_template.ScriptTemplate.template_modes(template_path, _MODES)

    @staticmethod
    def list_template_modes() -> List[Tuple[str, str]]:
        """Return ``[(stem, mode), ...]`` for every (template, mode) pairing.

        A dual-mode template appears twice -- once per mode -- so a UI can show
        one combo entry per (template, mode) pair without baking mode-awareness
        into the combo itself.
        """
        return script_template.ScriptTemplate.list_template_modes(
            _TEMPLATE_DIR, ".py", _MODES
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        MarmosetEngine().send(model_path=sys.argv[1], template="lookdev")
