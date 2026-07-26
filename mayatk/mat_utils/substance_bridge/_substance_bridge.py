# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge -- export Maya selection and hand off to Painter.

Architecturally mirrors :mod:`mayatk.mat_utils.marmoset_bridge`:

* :class:`SubstanceBridge` -- export/launch logic; template-driven.
* :mod:`templates/*.py` -- declarative metadata describing each handoff.
* :mod:`parameters` -- UI-tunable knob registry referenced by templates.
* :mod:`connection` -- live process I/O (stdout / log tail / RPC).

Marmoset's templates are Python scripts executed by Toolbag's ``-run`` flag.
Painter has no analogous CLI; its automation surface is ``--mesh`` at launch
plus the HTTP endpoint served by our Painter-side ``substance_rpc`` plugin
(see :mod:`substance_bridge.substance_rpc`; installed automatically on send).
So Substance templates are *descriptive* (metadata constants parsed via
:mod:`ast`, not executed) and the bridge translates them into a launch +
optional RPC dispatch (structured ``RPC_OPS`` and/or a legacy-JS
``RPC_SCRIPT`` routed through ``substance_painter.js.evaluate``).
"""

import ast
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk
from pythontk.str_utils._str_utils import StrUtils

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.mat_utils.substance_bridge.connection import SubstanceConnection
from mayatk.mat_utils.substance_bridge.substance_rpc import DEFAULT_RPC_PORT

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


# Allowed values for a template's ``BRIDGE_MODES`` tuple.
SEND_TO = "send_to"
ROUNDTRIP = "roundtrip"
_MODES = (SEND_TO, ROUNDTRIP)

# Allowed values for a template's ``TARGET_INSTANCE`` field and the
# matching ``target=`` kwarg on :meth:`SubstanceBridge.send`.
#
# - ``"auto"``  -- reuse an existing managed instance if one is live, else launch.
# - ``"new"``   -- always launch a fresh Painter (current default).
# - ``"current"`` -- require a live managed instance; error if none.
#
# ``target=<int>`` on ``send()`` is a fourth shape -- attach to that specific
# RPC port. It maps to "current"-style constraints (no fresh launch).
TARGET_AUTO = "auto"
TARGET_NEW = "new"
TARGET_CURRENT = "current"
_TARGETS = (TARGET_AUTO, TARGET_NEW, TARGET_CURRENT)


# FBX options tuned for Substance Painter (same as the pre-restructure bridge).
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportApplyConstantKeyReducer": False,
    "FBXExportBakeComplexAnimation": False,
    "FBXExportCacheFile": False,
    "FBXExportConstraints": False,
    "FBXExportConvertUnitString": "cm",
    "FBXExportFileVersion": "FBX202000",
    "FBXExportGenerateLog": False,
    "FBXExportHardEdges": False,
    "FBXExportInAscii": False,
    "FBXExportIncludeChildren": True,
    "FBXExportInputConnections": False,
    "FBXExportInstances": False,
    "FBXExportQuaternion": "euler",
    "FBXExportReferencedAssetsContent": False,
    "FBXExportScaleFactor": 1.0,
    "FBXExportShapes": False,
    "FBXExportSmoothMesh": False,
    "FBXExportUpAxis": "y",
    "FBXExportUseSceneName": False,
}


# -- Template introspection ------------------------------------------------

_TEMPLATE_FIELDS = (
    "BRIDGE_MODES",
    "LAUNCH_ARGS",
    "RPC_SCRIPT",
    "RPC_OPS",
    "BUILD_MANIFEST",
    "TARGET_INSTANCE",
    "FBX_OPTIONS",
    "EXPORT_FBX",
    "REUSE_RECORDED_EXPORT",
    "NO_CONNECTION_HINT",
)
_TEMPLATE_DEFAULTS: Dict[str, Any] = {
    "BRIDGE_MODES": (SEND_TO,),
    "LAUNCH_ARGS": [],
    # Legacy-JS body dispatched via the plugin's ``js.evaluate`` op
    # (Painter's ``substance_painter.js.evaluate`` -- the ``alg.*`` API).
    "RPC_SCRIPT": "",
    # Structured RPC calls: ``[("op.name", {"kwarg": value}), ...]``.
    # Dispatched (in order, before RPC_SCRIPT) via the substance_rpc
    # plugin's ``{op, kwargs}`` wire. String kwarg values get the same
    # ``__KEY__`` substitution as LAUNCH_ARGS (raw, unquoted).
    "RPC_OPS": [],
    "BUILD_MANIFEST": False,
    "TARGET_INSTANCE": TARGET_AUTO,
    "FBX_OPTIONS": {},
    # When False, skip Maya FBX export entirely -- the template targets an
    # existing Painter project (e.g. render-current-view) and doesn't care
    # about the Maya selection. The slot also relaxes its "nothing selected"
    # guard in that case.
    "EXPORT_FBX": True,
    # When True, the export overwrites the FBX path recorded (in the Maya
    # scene's fileInfo) by the previous send instead of re-deriving it --
    # so a reimport hits the exact file the open Painter project points
    # at, surviving Maya restarts and Output Dir drift.
    "REUSE_RECORDED_EXPORT": False,
    # Logged (after __KEY__ substitution) when the template needs a live
    # Painter connection and none is reachable. Turns a dead-end error
    # into user instructions -- e.g. "reload the mesh manually".
    "NO_CONNECTION_HINT": "",
}


_TEMPLATE_TYPES: Dict[str, type] = {
    "BRIDGE_MODES": (tuple, list),  # normalized to tuple below
    "LAUNCH_ARGS": list,
    "RPC_SCRIPT": str,
    "RPC_OPS": list,
    "BUILD_MANIFEST": bool,
    "TARGET_INSTANCE": str,
    "FBX_OPTIONS": dict,
    "EXPORT_FBX": bool,
    "REUSE_RECORDED_EXPORT": bool,
    "NO_CONNECTION_HINT": str,
}


# -- Painter log resolution (mirror of marmoset's version-aware resolver) --


# -- Bridge ----------------------------------------------------------------


class SubstanceBridge(ptk.HandoffBridge):
    """Export Maya selection to Substance Painter via a chosen template.

    A :class:`pythontk.HandoffBridge`: the shared skeleton (``resolve -> preflight
    -> produce -> deliver``) drives the flow, with this class supplying all four
    steps. Unlike the simpler bridges its delivery (Painter launch/attach + JSON-RPC
    round-trip + managed-instance registry) is deeply stateful and unique, so the
    bridge is its own deliverer (it overrides :meth:`_deliver`/:meth:`_preflight`
    rather than plugging in a shared :class:`pythontk.Deliverer`).

    Two operating modes per template (declared via ``BRIDGE_MODES``):

    * ``send_to`` -- launch Painter interactively, fire-and-forget.
    * ``roundtrip`` -- launch Painter with remote scripting, send the
      template's ``RPC_SCRIPT`` body, and wait for the call to complete.

    Usage::

        SubstanceBridge().send()                       # default: import template
        SubstanceBridge().send(template="import", mode="send_to")

    Backward-compatible with the pre-restructure API: legacy kwargs
    (``headless``, ``enable_remote``) are accepted and ignored if not
    meaningful to the template-driven model.
    """

    # Default ceiling for roundtrip RPC calls.
    ROUNDTRIP_TIMEOUT = 1800  # 30 minutes

    # Some templates (e.g. render-current-view) operate on an already-loaded
    # Painter project and export nothing -- so an empty selection is allowed.
    requires_objects = False

    def __init__(self, painter_path: Optional[str] = None):
        super().__init__()
        self._painter_path = painter_path
        # Managed Painter instances launched by this bridge, in insertion
        # order (oldest -> newest). Pruned of dead entries on each lookup.
        self._instances: List[SubstanceConnection] = []

    # -- Painter path resolution ------------------------------------------

    @property
    def painter_path(self) -> Optional[str]:
        """Resolve the Painter executable path via :func:`find_painter_exe`."""
        if self._painter_path:
            return self._painter_path
        found = SubstanceConnection.find_painter_exe()
        if found:
            self._painter_path = found
        return found

    @painter_path.setter
    def painter_path(self, value: Optional[str]) -> None:
        self._painter_path = value

    @property
    def painter_log_path(self) -> Optional[str]:
        """Path to Painter's application ``log.txt``, or *None* if absent."""
        return SubstanceBridge.resolve_painter_log_path(self.painter_path)

    # -- Managed-instance registry ----------------------------------------

    @property
    def instances(self) -> List[SubstanceConnection]:
        """Live snapshot of managed connections (oldest -> newest, dead pruned)."""
        self._instances = [c for c in self._instances if c.is_alive()]
        return list(self._instances)

    def find_live_managed(self) -> Optional[SubstanceConnection]:
        """Return the most-recently-launched managed instance whose RPC pings.

        Prunes dead entries from the registry as a side effect.
        """
        self._instances = [c for c in self._instances if c.is_alive()]
        for conn in reversed(self._instances):
            if conn.rpc and conn.rpc.ping(timeout=0.5):
                return conn
        return None

    # -- Target resolution ------------------------------------------------

    @staticmethod
    def _validate_target(template_target: str, user_target: Any) -> None:
        """Raise :class:`ValueError` if *user_target* is incompatible.

        *template_target* is one of :data:`_TARGETS`; *user_target* is the
        caller's ``target=`` kwarg -- either a member of :data:`_TARGETS`
        or an ``int`` port.
        """
        if isinstance(user_target, int):
            if template_target == TARGET_NEW:
                raise ValueError(
                    "Template declares TARGET_INSTANCE='new'; cannot target "
                    f"specific port {user_target} (would skip the launch)."
                )
            return
        if user_target not in _TARGETS:
            raise ValueError(
                f"Invalid target={user_target!r}; expected one of {_TARGETS} "
                "or an int RPC port."
            )
        if template_target == TARGET_NEW and user_target == TARGET_CURRENT:
            raise ValueError(
                "Template declares TARGET_INSTANCE='new'; cannot target "
                "'current' (template requires a fresh launch)."
            )
        if template_target == TARGET_CURRENT and user_target == TARGET_NEW:
            raise ValueError(
                "Template declares TARGET_INSTANCE='current'; cannot target "
                "'new' (template requires an existing instance)."
            )

    def _resolve_connection(
        self,
        target: Union[str, int],
        launch_args: List[str],
        wants_rpc: bool,
        painter_exe: Optional[str] = None,
    ) -> Optional[SubstanceConnection]:
        """Return the connection that the current :meth:`send` should use.

        Pure routing logic; does not export FBX or send RPC. Returns
        ``None`` on error (after logging). New launches register in
        :attr:`_instances`; explicit-port attaches also register so a
        subsequent ``target="auto"`` can reuse them.

        - ``target=<int>``: attach to that port. Error if no RPC responds.
        - ``target="new"``: always launch a new Painter.
        - ``target="current"``: reuse a managed instance; error if none.
        - ``target="auto"``: reuse a managed instance if available, else launch.
        """
        if isinstance(target, int):
            try:
                conn = SubstanceConnection.attach(port=target)
            except ConnectionRefusedError as e:
                self.logger.error(str(e))
                return None
            self._instances.append(conn)
            return conn

        if target == TARGET_NEW:
            return self._launch_new(launch_args, wants_rpc, painter_exe)

        # auto / current: try the registry first.
        live = self.find_live_managed()
        if live is not None:
            self.logger.info(
                "Reusing managed Painter instance on port %d.", live.rpc_port
            )
            return live

        # Registry miss: probe the default RPC port. A Painter with the
        # substance_rpc plugin answers here even when this bridge (or this
        # Maya session) didn't launch it -- the discovery that makes
        # "current" work across Maya restarts and user-launched Painters.
        try:
            conn = SubstanceConnection.attach(
                port=DEFAULT_RPC_PORT, verify_timeout=1.0
            )
        except ConnectionRefusedError:
            conn = None
        if conn is not None:
            self.logger.info(
                "Attached to running Painter on port %d.", DEFAULT_RPC_PORT
            )
            self._instances.append(conn)
            return conn

        if target == TARGET_CURRENT:
            self.logger.error(
                "No running Painter with the substance_rpc plugin is "
                "reachable. Launch one first (e.g. send the 'import' "
                "template) or pass target='new'."
            )
            return None

        # target == auto, nothing reachable: launch.
        return self._launch_new(launch_args, wants_rpc, painter_exe)

    def _launch_new(
        self,
        launch_args: List[str],
        wants_rpc: bool,
        painter_exe: Optional[str] = None,
    ) -> Optional[SubstanceConnection]:
        """Launch a fresh Painter and register it in the managed list.

        *painter_exe* overrides the bridge's default ``_painter_path``
        for this launch only -- no instance state mutation.
        """
        conn = SubstanceConnection(
            mesh_path=None,  # template owns --mesh via LAUNCH_ARGS
            exe=painter_exe or self._painter_path,
            enable_remote=wants_rpc,
            extra_args=launch_args,
        )
        try:
            conn.open()
        except FileNotFoundError as e:
            self.logger.error(str(e))
            return None
        self._instances.append(conn)
        return conn

    # -- Public API -------------------------------------------------------

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        painter_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        preset_file: Optional[str] = None,
        template: str = "import",
        mode: str = SEND_TO,
        target: Union[str, int] = TARGET_AUTO,
        params: Optional[Dict[str, Any]] = None,
        **legacy_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Export *objects*, render *template* in *mode*, hand off to Painter.

        Parameters:
            objects: Nodes to export. Defaults to current selection.
            output_dir: Where the FBX (and optional manifest) lands.
                Defaults to ``<temp>/maya_substance_bridge``.
            output_name: Base filename without extension. Defaults to the
                Maya scene name or ``"untitled"``.
            painter_exe: Explicit ``Adobe Substance 3D Painter.exe`` override.
            fbx_options: FBX MEL overrides merged on top of defaults.
            preset_file: Optional FBX export preset path.
            template: Template stem under ``templates/`` (``"import"`` etc.).
            mode: ``"send_to"`` (fire-and-forget) or ``"roundtrip"``.
                Must match one of the template's declared
                :data:`BRIDGE_MODES`.
            target: Which Painter to send to. One of:
                - ``"auto"`` (default) -- reuse a managed live instance if
                  one exists; otherwise launch new.
                - ``"new"`` -- always launch a fresh Painter.
                - ``"current"`` -- require an existing managed instance;
                  error if none is reachable.
                - ``int`` -- attach to that explicit RPC port.
                The template's ``TARGET_INSTANCE`` constant constrains
                which values are valid; conflicts surface as errors.
            params: Placeholder overrides, e.g. ``{"PAINTER_RESOLUTION": 4096}``.
            **legacy_kwargs: Swallowed (``headless``, ``enable_remote``) for
                backward compatibility with the pre-restructure API.

        Returns:
            A result dict with ``fbx``, ``mode``, ``connection`` (the
            :class:`SubstanceConnection`, or *None* on a hint-declaring
            template's graceful fallback), ``output_dir``, ``delivered``
            (False when the RPC leg was skipped or failed on a
            ``send_to`` template), and -- for RPC templates --
            ``rpc_results`` (one value per ``RPC_OPS`` entry) and/or
            ``rpc_result`` (the ``RPC_SCRIPT`` return). *None* on
            failure.
        """
        # Swallow legacy kwargs without surprises.
        legacy_kwargs.pop("headless", None)
        legacy_kwargs.pop("enable_remote", None)
        if legacy_kwargs:
            self.logger.warning(
                "Unknown send() kwargs ignored: %s", list(legacy_kwargs)
            )

        # Pack the Painter-specific knobs into the request extras and run the
        # shared skeleton (resolve -> preflight -> produce -> deliver).
        request = ptk.HandoffRequest(
            template=template,
            mode=mode,
            params=params or {},
            extras={
                "output_dir": output_dir,
                "output_name": output_name,
                "painter_exe": painter_exe,
                "fbx_options": fbx_options,
                "preset_file": preset_file,
                "target": target,
            },
        )
        return self._run(objects, request)

    # -- HandoffBridge hooks ----------------------------------------------

    def _resolve_objects(self, objects):
        """Pass the selection through unchanged (lazy ``cmds.ls`` happens in produce)."""
        return objects

    def _preflight(self, objects, request) -> bool:
        """Validate the template / mode / target before exporting."""
        template_path = _TEMPLATE_DIR / f"{request.template}.py"
        if not template_path.is_file():
            available = sorted(p.stem for p in SubstanceBridge.list_templates())
            self.logger.error(
                f"Template '{request.template}' not found at {template_path}. "
                f"Available: {available}"
            )
            return False

        meta = SubstanceBridge.parse_template(template_path)
        if request.mode not in meta["BRIDGE_MODES"]:
            self.logger.error(
                f"Template '{request.template}' does not support mode "
                f"'{request.mode}'. Declared modes: {meta['BRIDGE_MODES']}"
            )
            return False

        try:
            self._validate_target(meta["TARGET_INSTANCE"], request.get("target"))
        except ValueError as e:
            self.logger.error(str(e))
            return False

        # Narrow the default "auto" to the template's declared target so a
        # 'current'-only template (e.g. reimport) never silently launches a
        # fresh Painter, and a 'new'-only template never grabs a stale one.
        # An explicit user choice (validated compatible above) still wins.
        if request.get("target") == TARGET_AUTO and meta["TARGET_INSTANCE"] in (
            TARGET_NEW,
            TARGET_CURRENT,
        ):
            request.extras["target"] = meta["TARGET_INSTANCE"]

        # Carry the parsed metadata + path forward (parsed once).
        request.extras["_meta"] = meta
        request.extras["_template_path"] = template_path
        return True

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX, stage textures, and build the material manifest."""
        meta = request.extras["_meta"]
        template_path = request.extras["_template_path"]

        output_dir = request.get("output_dir") or os.path.join(
            tempfile.gettempdir(), "maya_substance_bridge"
        )

        # Reimport-style templates overwrite the exact file the open Painter
        # project was created from -- the path recorded in the scene's
        # fileInfo by the previous send -- rather than re-deriving it (which
        # would drift if the Output Dir resolves differently this session).
        recorded_fbx = (
            self._recorded_export_path()
            if meta.get("REUSE_RECORDED_EXPORT")
            else None
        )
        if recorded_fbx:
            fbx_path = recorded_fbx
            recorded_dir = os.path.dirname(recorded_fbx)
            if recorded_dir:
                output_dir = recorded_dir
            base = os.path.splitext(os.path.basename(recorded_fbx))[0]
            self.logger.info(
                "Overwriting the previously sent mesh (recorded in scene "
                "fileInfo): %s",
                fbx_path,
            )
        else:
            if meta.get("REUSE_RECORDED_EXPORT"):
                self.logger.info(
                    "No previously recorded export for this scene; deriving "
                    "the path from the scene name. Painter's project must "
                    "point at the same file for the reload to apply."
                )
            base = request.get("output_name") or self._scene_base_name()
            base = StrUtils.sanitize(base, preserve_case=True)
            fbx_path = os.path.join(output_dir, f"{base}.fbx")
        os.makedirs(output_dir, exist_ok=True)
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")

        # -- FBX export ----------------------------------------------------
        # Templates that operate on an already-loaded Painter project (e.g.
        # render the current view) declare EXPORT_FBX=False and skip this
        # phase entirely. Defaults to True for compat with import/reimport.
        if meta.get("EXPORT_FBX", True):
            # Precedence: defaults < template FBX_OPTIONS < caller's fbx_options.
            merged_options = dict(_DEFAULT_FBX_OPTIONS)
            merged_options.update(meta.get("FBX_OPTIONS", {}))
            if request.get("fbx_options"):
                merged_options.update(request.get("fbx_options"))

            FbxUtils.load_plugin()
            self.logger.info("Exporting FBX ...")
            try:
                FbxUtils.export(
                    file_path=fbx_path,
                    objects=objects,
                    preset_file=request.get("preset_file"),
                    options=merged_options,
                    selection_only=True,
                )
            except Exception as e:
                self.logger.error(f"FBX export failed: {e}")
                return None
            self.logger.info(
                f'FBX written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
            )
            # Remember where this scene's mesh went so a later reimport --
            # even from a fresh Maya session -- overwrites the same file.
            self._record_export_path(fbx_path)
        else:
            self.logger.info(
                "Template declares EXPORT_FBX=False; skipping Maya FBX export."
            )

        # -- Stage textures assigned to the selection's materials --------
        # Only when the active template claims the PAINTER_INCLUDE_TEXTURES
        # widget AND the user left it on -- otherwise a stale value in the
        # panel doesn't pollute an unrelated template (e.g. ``render.py``).
        from mayatk.mat_utils.substance_bridge import parameters as _params

        referenced = _params.Parameters.referenced_keys(
            template_path.read_text(encoding="utf-8")
        )
        merged_params = _params.Parameters.defaults()
        merged_params.update(request.params or {})
        include_textures = "PAINTER_INCLUDE_TEXTURES" in referenced and bool(
            merged_params.get("PAINTER_INCLUDE_TEXTURES", True)
        )
        # Resolve scope once -- shared by texture staging and manifest build.
        # Skipped entirely when neither needs it so render.py-style templates
        # don't pay for a needless ``cmds.ls`` round-trip.
        scope_objects: List[str] = []
        if include_textures or meta["BUILD_MANIFEST"]:
            scope_objects = objects or cmds.ls(selection=True, long=True) or []
        staged_textures: List[str] = []
        if include_textures and scope_objects:
            staged_textures = self._stage_assigned_textures(
                scope_objects,
                output_dir,
                prefix=str(merged_params.get("PAINTER_TEXTURE_PREFIX", "")),
            )

        # -- Optional material manifest -----------------------------------
        if meta["BUILD_MANIFEST"]:
            self.logger.info("Building material manifest ...")
            manifest = MatManifest.build(scope_objects)
            if staged_textures:
                manifest["staged_textures"] = staged_textures
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
            self.logger.info(
                f"Manifest written: "
                f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
            )

        return ptk.Payload(
            primary=fbx_path,
            extras={
                "meta": meta,
                "manifest_path": manifest_path,
                "output_dir": output_dir,
                "staged_textures": staged_textures,
                "referenced": referenced,
            },
        )

    def _deliver(self, payload, request) -> Optional[Dict[str, Any]]:
        """Render the launch args, resolve the Painter connection, dispatch RPC."""
        from mayatk.mat_utils.substance_bridge import parameters as _params

        meta = payload.extras["meta"]
        fbx_path = payload.primary
        manifest_path = payload.extras["manifest_path"]
        output_dir = payload.extras["output_dir"]
        staged_textures = payload.extras["staged_textures"]
        referenced = payload.extras["referenced"]
        mode = request.mode

        # -- Render placeholders ------------------------------------------
        cli_ctx, js_ctx = self._build_contexts(
            fbx_path=fbx_path,
            manifest_path=manifest_path,
            output_dir=output_dir,
            params=request.params,
        )
        launch_args = self._render_launch_args(meta["LAUNCH_ARGS"], cli_ctx)
        # Dynamic argv extensions that don't fit the static __KEY__ shape:
        # - ``--mesh-map <path>`` per staged texture (variable-length).
        # - ``--split-by-udim`` as a bare presence flag (no value follows).
        if "--mesh" in launch_args:
            for tex_path in staged_textures:
                launch_args.extend(["--mesh-map", tex_path])
            merged_params = _params.Parameters.defaults()
            merged_params.update(request.params or {})
            if "PAINTER_SPLIT_BY_UDIM" in referenced and bool(
                merged_params.get("PAINTER_SPLIT_BY_UDIM", False)
            ):
                launch_args.append("--split-by-udim")
        rpc_script = StrUtils.replace_delimited(meta["RPC_SCRIPT"], js_ctx)
        rpc_ops = self._render_rpc_ops(meta["RPC_OPS"], cli_ctx)
        no_connection_hint = StrUtils.replace_delimited(
            meta.get("NO_CONNECTION_HINT", ""), cli_ctx
        ).strip()

        # RPC needs the Painter-side substance_rpc plugin. Install (or
        # refresh) it now so any Painter launched below -- and every future
        # launch -- serves the endpoint. Idempotent and cheap when present.
        wants_rpc = mode == ROUNDTRIP or bool(rpc_script.strip()) or bool(rpc_ops)
        self.ensure_rpc_plugin()

        # -- Resolve target connection ------------------------------------
        # The template's LAUNCH_ARGS is authoritative for any fresh launch.
        # ``_resolve_connection`` decides between attach / reuse / launch
        # based on *target* (and the template's TARGET_INSTANCE constraint
        # already validated in preflight). The per-call ``painter_exe``
        # overrides the bridge default only for fresh launches; reused/attached
        # instances use whatever Painter is already running.
        connection = self._resolve_connection(
            request.get("target"),
            launch_args,
            wants_rpc,
            painter_exe=request.get("painter_exe"),
        )
        if connection is None:
            # A template that declares a fallback hint degrades gracefully:
            # the produce phase already did its useful work (e.g. reimport's
            # FBX overwrite), so surface the manual next step instead of
            # discarding the run.
            if no_connection_hint:
                self.logger.warning(no_connection_hint)
                self._announce_handoff(request.template, mode, fbx_path, output_dir)
                return {
                    "fbx": fbx_path,
                    "mode": mode,
                    "connection": None,
                    "output_dir": output_dir,
                    "delivered": False,
                }
            return None

        result: Dict[str, Any] = {
            "fbx": fbx_path,
            "mode": mode,
            "connection": connection,
            "output_dir": output_dir,
            "delivered": True,
        }
        if meta["BUILD_MANIFEST"]:
            result["manifest"] = manifest_path

        # -- Optional RPC dispatch ----------------------------------------
        # RPC_OPS first (structured {op, kwargs} calls), then RPC_SCRIPT
        # (legacy-JS body via the plugin's js.evaluate shim).
        if (rpc_ops or rpc_script.strip()) and connection.rpc is not None:
            self.logger.info("Waiting for Painter RPC to become ready ...")
            if not connection.rpc.wait_until_ready(timeout=60):
                self.logger.error(
                    "Painter RPC never came up. The substance_rpc plugin is "
                    "installed but not active in this Painter: tick it once "
                    "in Painter's Python menu (use Python > Reload Plugins "
                    "Folder if it isn't listed yet) -- Painter remembers it."
                )
                if no_connection_hint:
                    self.logger.warning(no_connection_hint)
                result["delivered"] = False
                if mode == ROUNDTRIP:
                    connection.close()
                    return None
            else:
                try:
                    for op_name, op_kwargs in rpc_ops:
                        self.logger.info(f"RPC: {op_name} ...")
                        result.setdefault("rpc_results", []).append(
                            connection.rpc.invoke(op_name, **op_kwargs)
                        )
                    if rpc_script.strip():
                        self.logger.info("Sending template RPC script ...")
                        result["rpc_result"] = connection.rpc.eval_js(rpc_script)
                except Exception as e:
                    self.logger.error(f"RPC dispatch failed: {e}")
                    result["delivered"] = False
                    if mode == ROUNDTRIP:
                        connection.close()
                        return None

        self._announce_handoff(request.template, mode, fbx_path, output_dir)
        return result

    # -- Helpers ----------------------------------------------------------

    #: Scene fileInfo key holding the last exported FBX path (saved with the
    #: scene, so a reimport from a later Maya session still finds it).
    EXPORT_RECORD_KEY = "substance_bridge_last_fbx"

    def ensure_rpc_plugin(self) -> None:
        """Install the Painter-side substance_rpc plugin if it isn't already.

        Idempotent (symlink-first via :class:`pythontk` PluginInstaller).
        Failure is non-fatal -- the send continues and RPC-dependent steps
        fall back to their hints -- but is logged so the user knows why a
        one-click reimport didn't happen.
        """
        try:
            from mayatk.mat_utils.substance_bridge.substance_rpc import Installer

            if Installer.is_installed():
                return
            dest = Installer.install()
            if dest is None:
                self.logger.warning(
                    "Could not resolve Painter's plugins folder; install the "
                    "substance_rpc plugin manually (see substance_rpc/installer.py)."
                )
                return
            self.logger.info(
                f"Installed Painter RPC plugin: {dest}. To activate it: in "
                "Painter, use Python > Reload Plugins Folder (or relaunch "
                "Painter), then ensure 'substance_rpc' is ticked in the "
                "Python menu -- Painter remembers it after the first time."
            )
        except Exception as e:  # noqa: BLE001 -- never block the handoff
            self.logger.warning(f"substance_rpc plugin install failed: {e}")

    @classmethod
    def _recorded_export_path(cls) -> Optional[str]:
        """Return the FBX path recorded by the last export, or ``None``."""
        try:
            values = cmds.fileInfo(cls.EXPORT_RECORD_KEY, query=True)
        except Exception:  # noqa: BLE001 -- no Maya / no scene
            return None
        if not values or not values[0]:
            return None
        return values[0].replace("\\", "/")

    @classmethod
    def _record_export_path(cls, fbx_path: str) -> None:
        """Persist *fbx_path* in the scene's fileInfo (forward slashes)."""
        try:
            cmds.fileInfo(cls.EXPORT_RECORD_KEY, fbx_path.replace("\\", "/"))
        except Exception as e:  # noqa: BLE001 -- recording is best-effort
            logger.debug("Could not record export path in fileInfo: %s", e)

    @staticmethod
    def _render_rpc_ops(
        rpc_ops: List[Tuple[str, Dict[str, Any]]], context: Dict[str, str]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Render ``__KEY__`` placeholders inside each op's string kwargs.

        Non-string kwarg values (bools, numbers, lists) pass through
        untouched -- they're already typed JSON values on the wire.
        """
        rendered: List[Tuple[str, Dict[str, Any]]] = []
        for op_name, op_kwargs in rpc_ops:
            rendered.append(
                (
                    op_name,
                    {
                        k: (
                            StrUtils.replace_delimited(v, context)
                            if isinstance(v, str)
                            else v
                        )
                        for k, v in op_kwargs.items()
                    },
                )
            )
        return rendered

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"

    def _build_contexts(
        self,
        fbx_path: str,
        manifest_path: str,
        output_dir: str,
        params: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Compose two placeholder contexts -- one CLI-raw, one JS-escaped.

        ``LAUNCH_ARGS`` substitution wants raw values (no quoting --
        ``subprocess`` handles argv splitting). ``RPC_SCRIPT`` substitution
        wants JS literals (quoted/escaped) so the rendered text drops cleanly
        into a JavaScript body. The internal slot tokens (``FBX_PATH`` etc.)
        appear unmodified in both contexts.
        """
        from mayatk.mat_utils.substance_bridge import parameters as _params

        merged = _params.Parameters.defaults()
        merged.update(params or {})

        internal: Dict[str, str] = {
            "FBX_PATH": fbx_path.replace("\\", "/"),
            "MANIFEST_PATH": manifest_path.replace("\\", "/"),
            "OUTPUT_DIR": output_dir.replace("\\", "/"),
            "PAINTER_HELPERS_DIR": str(_PKG_DIR).replace("\\", "/"),
        }

        cli_ctx = dict(internal)
        cli_ctx.update(_params.Parameters.render_cli_context(merged))

        js_ctx = dict(internal)
        js_ctx.update(_params.Parameters.render_js_context(merged))
        return cli_ctx, js_ctx

    @staticmethod
    def _render_launch_args(
        launch_args: List[str], context: Dict[str, str]
    ) -> List[str]:
        """Render ``__KEY__`` placeholders inside each ``LAUNCH_ARGS`` entry.

        Adjacent ``(--flag, "")`` pairs are dropped: a template can declare
        an optional flag like ``["--template", "__PATH__"]`` and the user
        leaving the value empty produces no argv entry rather than a
        broken ``--template ""`` that Painter would reject.
        """
        rendered = [
            StrUtils.replace_delimited(arg, context) if isinstance(arg, str) else arg
            for arg in launch_args
        ]
        out: List[str] = []
        i = 0
        while i < len(rendered):
            cur = rendered[i]
            nxt = rendered[i + 1] if i + 1 < len(rendered) else None
            if (
                isinstance(cur, str)
                and cur.startswith("-")
                and isinstance(nxt, str)
                and nxt == ""
            ):
                i += 2  # drop the flag and its empty value
                continue
            out.append(cur)
            i += 1
        return out

    def _stage_assigned_textures(
        self,
        objects: List[str],
        output_dir: str,
        prefix: str = "",
    ) -> List[str]:
        """Copy every texture assigned to *objects*' materials into *output_dir*.

        Walks the shading networks via
        :meth:`mayatk.mat_utils.MatUtils.get_texture_paths` and copies each
        resolved file into *output_dir* so Painter's "Import Baked Maps"
        dialog can pick them up alongside the FBX. Skips paths whose
        source doesn't exist on disk (logs a warning for each).

        If *prefix* is non-empty, each destination filename gets *prefix*
        prepended. The operation is idempotent: a basename that already
        starts with *prefix* has it stripped first, so the staged file
        ends up as ``<prefix><tail>`` no matter how the source was named.

        Returns the list of staged destination paths -- the same payload
        the manifest records under ``"staged_textures"``.
        """
        import shutil

        try:
            from mayatk.mat_utils._mat_utils import MatUtils
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Texture collection skipped (could not import MatUtils): {e}"
            )
            return []

        try:
            paths = MatUtils.get_texture_paths(objects=objects, absolute=True)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Texture collection failed: {e}")
            return []

        staged: List[str] = []
        for src in paths:
            src = str(src)
            if not src or not os.path.isfile(src):
                self.logger.warning("Assigned texture missing on disk: %s", src)
                continue
            base = os.path.basename(src)
            if prefix and base.startswith(prefix):
                base = base[len(prefix) :]
            dst = os.path.join(output_dir, f"{prefix}{base}")
            try:
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copyfile(src, dst)
            except OSError as e:
                self.logger.warning("Could not stage %s -> %s: %s", src, dst, e)
                continue
            staged.append(dst)
            self.logger.info("Staged texture: %s", dst)
        return staged

    def _announce_handoff(
        self, template: str, mode: str, fbx_path: str, output_dir: str
    ) -> None:
        """Log clickable links to the output folder + Painter log.

        The FBX link is only surfaced if the file actually exists --
        EXPORT_FBX=False templates (e.g. ``render``) skip the export
        and there's no file to point at.
        """
        if os.path.isfile(fbx_path):
            self.logger.info(
                f"[{template}/{mode}] FBX: "
                f'<a href="action://open?path={fbx_path}">{fbx_path}</a>'
            )
        else:
            self.logger.info(f"[{template}/{mode}] (no FBX export)")
        self.logger.info(
            f'Output folder: <a href="action://open?path={output_dir}">{output_dir}</a>'
        )
        log = self.painter_log_path
        if log:
            self.logger.info(
                f'Painter log: <a href="action://open?path={log}">{log}</a>'
            )

    @staticmethod
    def list_templates() -> List[Path]:
        """Return user-visible templates in ``templates/`` (skips underscore-prefixed)."""
        return sorted(
            p for p in _TEMPLATE_DIR.glob("*.py") if not p.stem.startswith("_")
        )

    @staticmethod
    def parse_template(template_path: Path) -> Dict[str, Any]:
        """Read a template's metadata constants without executing the file.

        Returns a dict with ``BRIDGE_MODES`` / ``LAUNCH_ARGS`` / ``RPC_SCRIPT`` /
        ``BUILD_MANIFEST`` keys, falling back to :data:`_TEMPLATE_DEFAULTS`
        for any constant the template omits or sets to a wrong type.

        Parsing uses :func:`ast.literal_eval` so malformed templates can't
        import-crash other templates. Each parsed value is type-checked against
        :data:`_TEMPLATE_TYPES`; mismatches are logged and the default is used,
        so a single bad template never silently produces a broken launch line.
        """
        out: Dict[str, Any] = dict(_TEMPLATE_DEFAULTS)
        try:
            tree = ast.parse(template_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as e:
            logger.warning("Could not parse template %s: %s", template_path, e)
            return out
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or target.id not in _TEMPLATE_FIELDS:
                continue
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                logger.warning(
                    "Template %s: %s is not a literal; using default.",
                    template_path.name,
                    target.id,
                )
                continue
            expected = _TEMPLATE_TYPES.get(target.id)
            if expected is not None and not isinstance(value, expected):
                logger.warning(
                    "Template %s: %s has type %s, expected %s; using default.",
                    template_path.name,
                    target.id,
                    type(value).__name__,
                    expected,
                )
                continue
            out[target.id] = value
        # Normalize BRIDGE_MODES to a tuple of valid mode strings.
        modes = tuple(
            m for m in out.get("BRIDGE_MODES", ()) if isinstance(m, str) and m in _MODES
        )
        out["BRIDGE_MODES"] = modes or (SEND_TO,)
        # LAUNCH_ARGS must be a list of strings -- coerce non-strings or fall back.
        if not all(isinstance(a, str) for a in out["LAUNCH_ARGS"]):
            logger.warning(
                "Template %s: LAUNCH_ARGS contains non-string entries; using default.",
                template_path.name,
            )
            out["LAUNCH_ARGS"] = list(_TEMPLATE_DEFAULTS["LAUNCH_ARGS"])
        # RPC_OPS must be a list of (op_name, kwargs_dict) pairs -- normalize
        # list-shaped pairs to tuples; any malformed entry voids the field so
        # a half-broken template can't dispatch a partial op sequence.
        ops_norm: List[Tuple[str, Dict[str, Any]]] = []
        for entry in out["RPC_OPS"]:
            if (
                isinstance(entry, (tuple, list))
                and len(entry) == 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], dict)
            ):
                ops_norm.append((entry[0], dict(entry[1])))
            else:
                logger.warning(
                    "Template %s: RPC_OPS entry %r is not an "
                    "(op_name, kwargs_dict) pair; ignoring RPC_OPS.",
                    template_path.name,
                    entry,
                )
                ops_norm = []
                break
        out["RPC_OPS"] = ops_norm
        # Normalize TARGET_INSTANCE to a known mode; fall back to default.
        if out["TARGET_INSTANCE"] not in _TARGETS:
            logger.warning(
                "Template %s: TARGET_INSTANCE=%r is not one of %s; using default.",
                template_path.name,
                out["TARGET_INSTANCE"],
                _TARGETS,
            )
            out["TARGET_INSTANCE"] = _TEMPLATE_DEFAULTS["TARGET_INSTANCE"]
        return out

    @staticmethod
    def list_template_modes() -> List[Tuple[str, str]]:
        """Return ``[(stem, mode), ...]`` for every (template, mode) pairing."""
        out: List[Tuple[str, str]] = []
        for path in SubstanceBridge.list_templates():
            for mode in SubstanceBridge.parse_template(path)["BRIDGE_MODES"]:
                out.append((path.stem, mode))
        return out

    @staticmethod
    def resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]:
        """Return the path to Painter's application log.

        Painter (unlike Toolbag) doesn't version its install directory name, so
        the log path is just ``%LOCALAPPDATA%\\Adobe\\Adobe Substance 3D Painter\\log.txt``.
        *painter_exe* is accepted for shape-parity with marmoset's resolver and
        as an extension point if Adobe ever ships versioned install dirs.

        Implementation delegates to :func:`connection.default_log_path` -- single
        source of truth for the log file location.
        """
        return SubstanceConnection.default_log_path()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    SubstanceBridge().send()
