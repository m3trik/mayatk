# !/usr/bin/python
# coding=utf-8
"""Drive Substance 3D Painter from the outside -- the DCC-free half of the bridge.

:class:`SubstanceEngine` is everything the Painter hand-off does that needs no
DCC: script discovery and header parsing, finding the Painter a script runs in
(by its instance record, or a launch), texture staging (packed-map unpacking,
the Texture Affix, the mesh-map filter) and the result it hands back. It is a
:class:`pythontk.HandoffBridge` whose produce step is written against a few
scene hooks (the *DCC hooks* section of the class); each DCC's
``SubstanceBridge`` subclasses it and supplies only those -- the mesh writers,
the selection, the material manifest, the textures assigned to the selection,
the bake-source export, and where the scene keeps the path of its last export.
A host-free caller (the standalone Substance Scripts app) names a mesh file instead
(``send(..., mesh=path)``) and needs none of them.

A script is a Python file run inside Painter as written (``templates/*.py``,
or the person's own in ``ptk.ScriptTemplate.user_dir("substance")``, which
shadows a built-in of its name), its values given as ``args``
(``substance_rpc``'s ``script.start``). Its header, read without running it::

    \"\"\"Bake Lighting to Diffuse: Iray AO composited onto Base Color.\"\"\"
    WORKS_ON = "project"
    # Rows: __PAINTER_SAVE__ __PAINTER__
    # Row: __AO_RAYS__ int 64 "AO Rays" min=1 max=256

* ``WORKS_ON`` -- what it runs on, which decides the Painter: ``"new"`` a new
  project from the sent mesh (a Painter with no project open, else a launched
  one), ``"mesh"`` the project made from the sent mesh (the Painter holding
  it), ``"project"`` the Project row's ``.spp`` (the Painter holding it, else
  one it opens in) or, with none named, the project open in Painter.
* ``__KEY__`` tokens show the panel's rows; ``Row:`` lines declare the
  script's own (``ptk.ScriptTemplate.declared_rows``). Every value arrives in
  ``args`` by key, beside what the send made: ``MESH``, ``HIGH_POLY``,
  ``MANIFEST``, ``MESH_MAPS``, ``MESH_MAP_FILES``, ``STAGED_TEXTURES``,
  ``OUTPUT_DIR``, ``IMAGES_DIR``, ``TEXTURES_DIR``, ``PROJECT``.
* ``BUILD_MANIFEST``, ``REUSE_RECORDED_EXPORT``, ``FBX_OPTIONS`` and
  ``BRIDGE_MODES`` -- the host export's options, as before.

A send queues the script and returns its job (``result["job"]``);
:meth:`SubstanceEngine.wait` follows it to the end (a ``round_trip`` send
waits there itself).

**Vendored copy -- keep byte-identical.** This file ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``).
The packages cannot import each other and pythontk names no product, so the
Painter half has no shared importable home. It imports pythontk and its own
siblings (relatively) only -- never a DCC, never its host package by name.
"""

import ast
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk import StrUtils

from .connection import APP, SubstanceConnection
from .substance_rpc.client import PainterRpcClient
from .substance_rpc.installer import PLUGIN_SPEC

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
#: A person's own Painter scripts (``ptk.ScriptTemplate.user_dir``): one folder
#: every host and the standalone app read, where a script named like a bundled
#: one runs in its place.
_SCRIPTS_APP = "substance"


# The mode vocabulary a script's ``BRIDGE_MODES`` tuple may name -- taken from
# ``script_template``, never spelled here (an ON-DISK contract every bridge
# shares; ``declared_modes`` folds the old ``roundtrip`` spelling to the canon).
SEND_TO = ptk.SEND_TO
ROUND_TRIP = ptk.ROUND_TRIP
_MODES = (SEND_TO, ROUND_TRIP)

#: What a script runs on (its ``WORKS_ON``): see the module docstring.
WORKS_NEW = "new"
WORKS_MESH = "mesh"
WORKS_PROJECT = "project"
_WORKS_ON = (WORKS_NEW, WORKS_MESH, WORKS_PROJECT)

#: The Painter row's fixed choices; any other value is an instance record's id.
PAINTER_AUTO = "auto"
PAINTER_NEW = "new"


# -- Script header ---------------------------------------------------------

_TEMPLATE_FIELDS = (
    "BRIDGE_MODES",
    "WORKS_ON",
    "BUILD_MANIFEST",
    "FBX_OPTIONS",
    "REUSE_RECORDED_EXPORT",
)
_TEMPLATE_DEFAULTS: Dict[str, Any] = {
    "BRIDGE_MODES": (SEND_TO,),
    "WORKS_ON": WORKS_PROJECT,
    "BUILD_MANIFEST": False,
    "FBX_OPTIONS": {},
    # When True, the export overwrites the mesh path recorded on the host
    # scene (Maya ``fileInfo``, a Blender scene custom property) by the
    # previous send instead of re-deriving it -- so an Update Mesh hits the
    # exact file the open Painter project points at, surviving host restarts
    # and Output Dir drift.
    "REUSE_RECORDED_EXPORT": False,
}
_TEMPLATE_TYPES: Dict[str, type] = {
    "BRIDGE_MODES": (tuple, list),  # normalized to tuple below
    "WORKS_ON": str,
    "BUILD_MANIFEST": bool,
    "FBX_OPTIONS": dict,
    "REUSE_RECORDED_EXPORT": bool,
}


# -- Engine ----------------------------------------------------------------


class SubstanceEngine(ptk.HandoffBridge):
    """The DCC-free Substance Painter hand-off; each DCC's ``SubstanceBridge`` subclasses it.

    A :class:`pythontk.HandoffBridge`: the shared skeleton (``resolve -> preflight
    -> produce -> deliver``) drives the flow, with this class supplying all four
    steps. Its delivery is finding the Painter a script runs in and queueing it
    there, so the engine is its own deliverer.

    The produce step reads and writes the host scene only through the *DCC
    hooks* at the bottom of the class, which a subclass implements. A subclass
    also sets :attr:`payload_prefix` (the scratch namespace a send with no
    Output Dir lands in) and :attr:`DEFAULT_FBX_OPTIONS`.
    """

    #: The plugin in Painter (``pythontk.RpcPluginSpec``): a panel over this
    #: engine offers *Install in Painter* / *Uninstall from Painter* for it.
    RPC_PLUGIN = PLUGIN_SPEC

    #: Executable discovery for this bridge's target app (:class:`pythontk.AppSpec`),
    #: re-exposed from the connection module so callers reach it through the class
    #: namespace: a panel's ``*_init`` gates its launch button on
    #: ``<Bridge>.APP.available`` and shows ``APP.not_found_message`` when unmet.
    APP = APP

    # A script that runs on a project exports nothing -- so an empty selection
    # is allowed.
    requires_objects = False

    # Both carriers: Painter creates a project from USD (8.3+) beside FBX. Flat
    # is fine here -- every instance paints as its own mesh, and nothing comes
    # back as geometry.
    carriers = ("fbx", "usd")
    usd_flattens_instances = True

    #: The host exporter's FBX options tuned for Painter -- the base a script's
    #: ``FBX_OPTIONS`` and the caller's ``fbx_options`` merge over. Host-specific
    #: vocabulary (Maya ``FBXExport*`` flags, Blender ``export_scene.fbx``
    #: kwargs), so the DCC subclass sets it; read-only here.
    DEFAULT_FBX_OPTIONS: Optional[Dict[str, Any]] = None

    #: Scene key holding the last exported mesh path (saved with the scene, so
    #: an Update Mesh from a later session still finds it).
    EXPORT_RECORD_KEY = "substance_bridge_last_fbx"

    #: Seconds a launched Painter has to start serving.
    LAUNCH_TIMEOUT = 180.0

    def __init__(self, painter_path: Optional[str] = None):
        super().__init__()
        self._painter_path = painter_path
        # Painters this bridge launched, oldest first: the only ones it may
        # end, and the first reused by an Auto send. Dead ones are pruned.
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
        return self.resolve_painter_log_path(self.painter_path)

    # -- Painters ----------------------------------------------------------

    @property
    def instances(self) -> List[SubstanceConnection]:
        """The Painters this bridge launched that still run (oldest first)."""
        self._instances = [c for c in self._instances if c.is_alive()]
        return list(self._instances)

    @staticmethod
    def painters() -> List[Dict[str, Any]]:
        """Every running Painter serving the plugin (its instance record):
        what the Painter row lists beside Auto and New."""
        return PainterRpcClient.painters()

    @staticmethod
    def painter_label(record: Dict[str, Any]) -> str:
        """*record*'s Painter as a person reads it: its project, else "no project"."""
        project = (record.get("state") or {}).get("project") or {}
        name = project.get("name") or (os.path.basename(project["file_path"]) if project.get("file_path") else "")
        paused = ", paused" if (record.get("state") or {}).get("paused") else ""
        return f"Painter: {name or 'no project'} (pid {record.get('pid')}{paused})"

    def _connection_for(self, record: Dict[str, Any]) -> Optional[SubstanceConnection]:
        """This bridge's own connection to *record*'s Painter, else an attach."""
        for conn in self.instances:
            if conn.record and conn.record.get("id") == record.get("id"):
                return conn
        try:
            return SubstanceConnection.attach(record)
        except ConnectionRefusedError as e:
            # Its process is gone, or its PID now names another one: the
            # record goes, so the next run does not pick it again.
            ptk.RpcInstances.remove(record.get("label") or PainterRpcClient.LABEL, record.get("id"))
            self.logger.error(str(e))
            return None

    def _start_painter(
        self, painter_exe: Optional[str] = None, gui: bool = True, hidden: bool = False
    ) -> Optional[SubstanceConnection]:
        """Start a Painter (it loads the plugin; *hidden*: on a private desktop,
        :attr:`SubstanceConnection.hidden`), not yet serving (:meth:`_served`)."""
        conn = SubstanceConnection(exe=painter_exe or self._painter_path, gui=gui, hidden=hidden)
        try:
            conn.open()
        except FileNotFoundError as e:
            self.logger.error(str(e))
            return None
        self.logger.info("Waiting for the launched Painter to start ...")
        return conn

    def _served(self, conn: SubstanceConnection) -> str:
        """Wait for a started Painter to serve and keep it, saying nothing (safe
        off the UI thread): ``""``, else why it never served (it is ended)."""
        if not conn.wait_ready(timeout=self.LAUNCH_TIMEOUT):
            conn.close(terminate=True)
            return "The launched Painter never served (see its log)."
        self._instances.append(conn)
        return ""

    def _launch_new(
        self, painter_exe: Optional[str] = None, gui: bool = True, hidden: bool = False
    ) -> Optional[SubstanceConnection]:
        """Launch a Painter, wait for it to serve, and keep it."""
        conn = self._start_painter(painter_exe, gui, hidden)
        error = self._served(conn) if conn is not None else "no Painter started"
        if conn is not None and error:
            self.logger.error(error)
        return None if error else conn

    @staticmethod
    def _holds(record: Dict[str, Any], key: str, path: Optional[str]) -> bool:
        project = (record.get("state") or {}).get("project") or {}
        return PainterRpcClient._same_file(project.get(key), path)

    @staticmethod
    def _empty(record: Dict[str, Any]) -> bool:
        """A free Painter: no project open, and not paused in Painter."""
        state = record.get("state") or {}
        return not state.get("project") and not state.get("paused")

    def pick_painter(
        self,
        works_on: str,
        choice: str = PAINTER_AUTO,
        project: Optional[str] = None,
        mesh: Optional[str] = None,
        painter_exe: Optional[str] = None,
        gui: bool = True,
        hidden: bool = False,
        launch: bool = True,
    ) -> Optional[SubstanceConnection]:
        """The Painter a script that *works_on* runs in; ``None`` (said) when none fits.

        *choice* is the Painter row: :data:`PAINTER_NEW` launches one; a record
        id is that Painter; :data:`PAINTER_AUTO` takes, for a new project, a
        Painter with none open (one this bridge launched first) else a launch;
        for the sent mesh's project, the Painter whose project was made from
        *mesh*; for a named *project*, the Painter holding it, else one with
        no project open, else a launch; for the open project, this bridge's
        latest Painter, else the only one serving. A Painter paused in Painter
        is never taken as a free one. Without *launch*, where it would launch
        one it returns :data:`PAINTER_NEW` (the caller launches).
        """
        launch = (lambda: self._launch_new(painter_exe, gui, hidden)) if launch else (lambda: PAINTER_NEW)  # noqa: E731
        if choice == PAINTER_NEW:
            return launch()
        records = self.painters()
        if choice and choice != PAINTER_AUTO:
            record = next((r for r in records if r.get("id") == choice), None)
            if record is None:
                self.logger.error("That Painter is no longer running: pick another (Refresh).")
                return None
            if works_on == WORKS_NEW and not self._empty(record):
                self.logger.error(f"{self.painter_label(record)} has a project open: a new project needs a Painter with none.")
                return None
            if works_on == WORKS_MESH and not self._holds(record, "mesh_path", mesh):
                self.logger.error(f"{self.painter_label(record)} does not hold the project made from {mesh}.")
                return None
            return self._connection_for(record)

        own = [c.record for c in self.instances if c.record]
        own_ids = {r.get("id") for r in own}
        ordered = [r for r in records if r.get("id") in own_ids] + [r for r in records if r.get("id") not in own_ids]
        if works_on == WORKS_NEW:
            found = next((r for r in ordered if self._empty(r)), None)
            return self._connection_for(found) if found else launch()
        if works_on == WORKS_MESH:
            found = next((r for r in ordered if self._holds(r, "mesh_path", mesh)), None)
            if found is None:
                self.logger.warning(
                    "No running Painter has the project made from this mesh open "
                    f"({mesh}). Open it in Painter, or run New Project first."
                )
            return self._connection_for(found) if found else None
        if project:
            found = next((r for r in ordered if self._holds(r, "file_path", project)), None)
            found = found or next((r for r in ordered if self._empty(r)), None)
            return self._connection_for(found) if found else launch()
        if own:
            return self._connection_for(own[-1])
        if len(records) == 1:
            return self._connection_for(records[0])
        self.logger.error(
            "No project to run on: open one in Painter (with the plugin ticked), "
            "name one in Project, or pick the Painter."
            if not records
            else "Several Painters are running: pick the one to run in (Painter)."
        )
        return None

    # -- Public API -------------------------------------------------------

    def _handoff(
        self,
        objects: Optional[List[Any]],
        template: str,
        mode: str,
        target: Any,
        params: Optional[Dict[str, Any]],
        legacy_kwargs: Dict[str, Any],
        **extras: Any,
    ) -> Optional[Dict[str, Any]]:
        """Pack one ``send()`` call into a :class:`pythontk.HandoffRequest` and run it.

        The shared body of each DCC's public ``send``, whose signature -- the host's
        own export knobs among its keywords -- stays on the DCC class. *target*
        is the Painter (the Painter row's value: ``"auto"``, ``"new"`` or an
        instance record's id); *legacy_kwargs* is that method's ``**``
        catch-all: the pre-restructure ``headless`` / ``enable_remote`` are
        swallowed, anything else is logged as ignored. *extras*
        (``output_dir``, ``painter_exe``, ``mesh``, the host's export options,
        ...) ride in :attr:`pythontk.HandoffRequest.extras`.
        """
        legacy_kwargs = dict(legacy_kwargs)
        legacy_kwargs.pop("headless", None)
        legacy_kwargs.pop("enable_remote", None)
        if legacy_kwargs:
            self.logger.warning("Unknown send() kwargs ignored: %s", list(legacy_kwargs))
        request = ptk.HandoffRequest(
            template=template,
            mode=mode,
            params=params or {},
            extras=dict(extras, target=target),
        )
        return self._run(objects, request)

    # -- HandoffBridge hooks ----------------------------------------------

    def _resolve_objects(self, objects):
        """Pass the selection through unchanged (the selection is read lazily in produce)."""
        return objects

    def _preflight(self, objects, request) -> bool:
        """Refuse before exporting: a script that is missing, does not compile,
        or does not run in the requested mode."""
        template_path = SubstanceEngine.template_path(request.template)
        if not template_path.is_file():
            available = sorted(p.stem for p in self.list_templates())
            self.logger.error(f"No script '{request.template}' at {template_path}. Available: {available}")
            return False
        try:
            compile(template_path.read_text(encoding="utf-8-sig"), str(template_path), "exec")
        except SyntaxError as e:
            self.logger.error(f"{template_path.name} does not compile (line {e.lineno}): {e.msg}")
            return False
        meta = self.parse_template(template_path)
        if request.mode != SEND_TO and request.mode not in meta["BRIDGE_MODES"]:
            self.logger.error(
                f"Script '{request.template}' does not run in mode '{request.mode}'. "
                f"Declared modes: {meta['BRIDGE_MODES']}"
            )
            return False
        mesh = request.get("mesh")
        if meta["WORKS_ON"] in (WORKS_NEW, WORKS_MESH) and mesh and not os.path.isfile(mesh):
            self.logger.error(f"No mesh file at {mesh!r}.")
            return False
        request.extras["_meta"] = meta
        request.extras["_template_path"] = template_path
        return True

    def _export_model(
        self,
        path: str,
        objects: List[Any],
        request: ptk.HandoffRequest,
        fbx_options: Dict[str, Any],
    ) -> None:
        """Write *objects* to *path* in the carrier its extension names.

        Keyed on the EXTENSION, not the request: a reimport overwrites the file
        Painter's open project was created from, whatever carrier that was, so
        the recorded path decides. FBX takes the merged Painter flag set
        (*fbx_options*); USD takes the host's own defaults plus the
        ``usd_options`` extra.
        """
        writers = {"fbx": self._export_model_fbx, "usd": self._export_model_usd}
        writers[self.carrier_of(path)](path, objects, request, fbx_options)

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the mesh (FBX or USD), stage textures, and build the material
        manifest -- for a script that works on a mesh. A host-free send names
        the mesh instead (``mesh=``): it is used as it is, nothing exported."""
        meta = request.extras["_meta"]
        template_path = request.extras["_template_path"]
        given_mesh = request.get("mesh")

        # No Output Dir: the hand-off (manifest, staged texture copies) is the
        # run's detached scratch in the ``payload_prefix`` namespace -- Painter
        # reads it after we return, so allocation sweeps stale leftovers
        # instead of deleting live ones -- while the MESH, which the Painter
        # project is built from and a reimport overwrites, goes to the scene
        # project's ``cache/substance`` (:meth:`_mesh_cache_dir`): a project
        # pointing into temp loses its mesh to the sweep. A named Output Dir
        # takes everything.
        named_dir = request.get("output_dir")
        output_dir = named_dir or self._scratch_dir(request, "handoff")
        mesh_dir = named_dir or self._mesh_cache_dir() or output_dir

        # An Update Mesh-style script overwrites the exact file the open Painter
        # project was created from -- the path recorded on the scene by the
        # previous send -- rather than re-deriving it (which would drift if the
        # Output Dir resolves differently this session).
        recorded_fbx = (
            self._recorded_export_path() if meta.get("REUSE_RECORDED_EXPORT") else None
        )
        if given_mesh:
            fbx_path = given_mesh
            base = os.path.splitext(os.path.basename(given_mesh))[0]
        elif recorded_fbx:
            fbx_path = recorded_fbx
            recorded_dir = os.path.dirname(recorded_fbx)
            if recorded_dir:
                output_dir = recorded_dir
            base = os.path.splitext(os.path.basename(recorded_fbx))[0]
            self.logger.info(
                "Overwriting the previously sent mesh (recorded on the scene): %s",
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
            if mesh_dir != named_dir and not request.get("output_name"):
                # One project cache holds every scene's mesh: named by the
                # scene alone, two same-named scenes would share one file.
                base = self._scene_keyed(base)
            fbx_path = os.path.join(
                mesh_dir, f"{base}{self.payload_extension(request)}"
            )
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.dirname(fbx_path), exist_ok=True)
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")

        # Which rows this script claims, and their effective values. Read
        # before the export because the high-poly leg below is gated on them
        # (a stale panel value must not pollute a template that never asked
        # for the widget -- e.g. ``render.py``).
        from . import parameters as _params

        referenced = _params.Parameters.referenced_keys(
            template_path.read_text(encoding="utf-8")
        )
        merged_params = _params.Parameters.defaults()
        merged_params.update(request.params or {})

        # -- Mesh export ---------------------------------------------------
        # A script that works on a project (WORKS_ON "project") exports
        # nothing; a host-free send's mesh is already a file.
        high_poly_path: Optional[str] = None
        exports = meta["WORKS_ON"] in (WORKS_NEW, WORKS_MESH) and not given_mesh
        if exports:
            # Precedence: defaults < template FBX_OPTIONS < caller's fbx_options.
            merged_options = dict(self.DEFAULT_FBX_OPTIONS or {})
            merged_options.update(meta.get("FBX_OPTIONS", {}))
            if request.get("fbx_options"):
                merged_options.update(request.get("fbx_options"))

            carrier = os.path.splitext(fbx_path)[1].lstrip(".").upper()
            self.logger.info(f"Exporting {carrier} ...")
            try:
                self._export_model(fbx_path, objects, request, merged_options)
            except Exception as e:
                self.logger.error(f"{carrier} export failed: {e}")
                return None
            self.logger.info(
                f'{carrier} written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
            )
            # Remember where this scene's mesh went so a later reimport --
            # even from a fresh host session -- overwrites the same file.
            # Never a temp mesh (no project to cache it in): the scene would
            # then point a later reimport at a file the sweep deletes.
            if not self._is_temp_path(fbx_path):
                self._record_export_path(fbx_path)

            # -- Companion bake-source export --------------------------
            # A wholly separate pass over a wholly separate object set,
            # run after the main export so it can neither reorder nor
            # fail it -- and reading nothing from the export scope, so
            # "Visible Only" stays exactly as wide as the user set it.
            high_poly_path = self._export_bake_source(
                fbx_path, merged_options, referenced, request
            )
        elif not given_mesh:
            self.logger.info("The script works on a Painter project; nothing exported.")

        # -- Stage textures assigned to the selection's materials --------
        # Only when the active template claims the PAINTER_INCLUDE_TEXTURES
        # widget AND the user left it on -- otherwise a stale value in the
        # panel doesn't pollute an unrelated template (e.g. ``render.py``).
        include_textures = exports and "PAINTER_INCLUDE_TEXTURES" in referenced and bool(
            merged_params.get("PAINTER_INCLUDE_TEXTURES", True)
        )
        # Resolve scope once -- shared by texture staging and manifest build.
        # Skipped entirely when neither needs it so render.py-style templates
        # don't pay for a needless selection round-trip.
        scope_objects: List[Any] = []
        build_manifest = exports and meta["BUILD_MANIFEST"]
        if include_textures or build_manifest:
            scope_objects = objects or self._selected_objects()
        texture_prefix, texture_suffix = _params.Parameters.affix_parts(
            merged_params.get("PAINTER_TEXTURE_AFFIX")
        )
        staged_textures: List[str] = []
        if include_textures and scope_objects:
            staged_textures = self._stage_assigned_textures(
                scope_objects,
                output_dir,
                prefix=texture_prefix,
                suffix=texture_suffix,
            )

        # -- Optional material manifest -----------------------------------
        mesh_maps: Dict[str, Dict[str, str]] = {}
        if build_manifest:
            self.logger.info("Building material manifest ...")
            manifest = self._material_manifest(scope_objects)
            if staged_textures:
                manifest["staged_textures"] = staged_textures
            # Per-material mesh-map assignments. ``--mesh-map`` can only
            # apply a map globally; this section is what lets the Painter
            # plugin put each material's AO/normal on the matching texture
            # set instead of on all of them.
            mesh_maps = self._mesh_map_assignments(
                manifest.get("materials", {}),
                staged_textures,
                prefix=texture_prefix,
                suffix=texture_suffix,
            )
            if mesh_maps:
                manifest["mesh_maps"] = mesh_maps
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
            self.logger.info(
                f"Manifest written: "
                f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
            )
            if mesh_maps:
                self.logger.info(
                    "Mesh-map wiring for %d material(s): %s",
                    len(mesh_maps),
                    ", ".join(sorted(mesh_maps)),
                )

        return ptk.Payload(
            primary=fbx_path,
            extras={
                "meta": meta,
                "manifest_path": manifest_path,
                "output_dir": output_dir,
                # Where a template's OWN output falls back to: the named
                # Output Dir, else the scene project's images / texture folder.
                "images_dir": named_dir or self._project_dir("images") or output_dir,
                "textures_dir": (
                    named_dir or self._project_dir("textures") or output_dir
                ),
                "staged_textures": staged_textures,
                "referenced": referenced,
                "high_poly_path": high_poly_path,
                "manifest_built": build_manifest,
                "mesh_maps": mesh_maps,
            },
        )

    def script_args(self, payload: ptk.Payload, request: ptk.HandoffRequest) -> Dict[str, Any]:
        """The values a script gets as ``args``: the rows (the registry's
        defaults under the request's), then what the send made."""
        from . import parameters as _params

        values = _params.Parameters.defaults()
        values.update(request.params or {})
        extras = payload.extras
        manifest = extras["manifest_path"] if extras.get("manifest_built") else ""
        mesh = payload.primary if extras["meta"]["WORKS_ON"] in (WORKS_NEW, WORKS_MESH) else ""
        values.update(
            {
                "MESH": (mesh or "").replace("\\", "/"),
                "HIGH_POLY": (extras.get("high_poly_path") or "").replace("\\", "/"),
                "MANIFEST": manifest.replace("\\", "/"),
                "MESH_MAPS": extras.get("mesh_maps") or {},
                "MESH_MAP_FILES": [p.replace("\\", "/") for p in self.mesh_map_files(extras.get("staged_textures") or [])],
                "STAGED_TEXTURES": [p.replace("\\", "/") for p in extras.get("staged_textures") or []],
                "OUTPUT_DIR": extras["output_dir"].replace("\\", "/"),
                "IMAGES_DIR": (extras.get("images_dir") or extras["output_dir"]).replace("\\", "/"),
                "TEXTURES_DIR": (extras.get("textures_dir") or extras["output_dir"]).replace("\\", "/"),
                "PROJECT": (request.get("project") or values.get("PAINTER_PROJECT") or "").replace("\\", "/"),
            }
        )
        return values

    def _deliver(self, payload, request) -> Optional[Dict[str, Any]]:
        """Find the Painter the script runs in, open its project when it is
        named and not open, and queue the script there (waiting on it for a
        ``round_trip``).

        The waits (a launched Painter starting to serve, a project opening)
        run in the request's ``background`` when it has one -- ``(work, then)``:
        *work* off the UI thread, then ``then(work())`` back on it (a panel's
        ``BackgroundTask``) -- and the result says ``pending``; the request's
        ``on_queued`` then gets the result once the script is queued (or the
        run stopped). Everything said is said on the caller's thread.
        """
        meta = payload.extras["meta"]
        works_on = meta["WORKS_ON"]
        args = self.script_args(payload, request)
        project = args["PROJECT"] if works_on == WORKS_PROJECT else ""
        mesh = args["MESH"]
        result: Dict[str, Any] = {
            "fbx": payload.primary,
            "mode": request.mode,
            "output_dir": payload.extras["output_dir"],
            "connection": None,
            "job": None,
            "delivered": False,
        }
        if payload.extras.get("high_poly_path"):
            result["high_poly"] = payload.extras["high_poly_path"]
        if payload.extras.get("manifest_built"):
            result["manifest"] = payload.extras["manifest_path"]

        background = request.get("background")
        self.refresh_rpc_plugin()
        picked = self.pick_painter(
            works_on,
            request.get("target") or PAINTER_AUTO,
            project=project or None,
            mesh=mesh or None,
            painter_exe=request.get("painter_exe"),
            gui=request.get("gui", True),
            hidden=request.get("hidden", False),
            launch=background is None,
        )
        if picked is None:
            if works_on == WORKS_MESH and os.path.isfile(payload.primary):
                self.logger.warning(
                    f"The mesh is updated on disk ({payload.primary}): in Painter, Edit > "
                    "Project Configuration > Select that file > OK reloads it."
                )
            return result

        ctx = request.get("ctx")
        label = self.label(request.template)
        to_open = project if works_on != WORKS_NEW and project else ""
        if picked == PAINTER_NEW:
            started = None  # launched below: it holds no project yet
        else:
            started = picked
            if works_on != WORKS_NEW:
                held = picked.project()
                if project and held and not PainterRpcClient._same_file(held.get("file_path"), project):
                    # Opening would close the person's project (unsaved edits and all).
                    self.logger.error(
                        f"{self.painter_label(picked.record or {})} has another project open "
                        f"({held.get('name') or held.get('file_path')}): save and close it there, "
                        "or pick a Painter with none (Painter)."
                    )
                    return result
                to_open = project if project and not held else ""
        if to_open and ctx is not None and not ctx.should_change(to_open, "open it in Painter"):
            return result
        if ctx is not None and not ctx.should_change(project or "Painter", f"run {label} in Painter"):
            return result
        if started is None:
            started = self._start_painter(request.get("painter_exe"), request.get("gui", True), request.get("hidden", False))
            if started is None:
                return result
        if to_open:
            self.logger.info(f"Opening {to_open} in Painter ...")
        launched = picked == PAINTER_NEW

        def bring_up() -> str:
            """The waits, saying nothing and never raising: ``""``, else why the
            run stops (said by ``queue``, on the caller's thread)."""
            try:
                error = self._served(started) if launched else ""
            except Exception as e:  # noqa: BLE001
                return f"The launched Painter never served: {e}"
            if error or not to_open:
                return error
            try:
                started.rpc.invoke("project.open", path=to_open, timeout=600)
            except Exception as e:  # noqa: BLE001 -- the run cannot go on
                return f"Painter could not open {to_open}: {e}"
            return ""

        def queue(error: str) -> Dict[str, Any]:
            if error:
                self.logger.error(error)
                return result
            result["connection"], result["painter"] = started, started.record
            expect = None
            if works_on != WORKS_NEW:
                held = started.project()
                if held is None:
                    self.logger.error("Painter has no project open to run this script on.")
                    return result
                expect = {"uuid": held.get("uuid"), "path": held.get("file_path")}
            template_path = request.extras["_template_path"]
            try:
                job = started.rpc.run_script(
                    template_path.read_text(encoding="utf-8-sig"),
                    path=str(template_path),
                    args=args,
                    expect=expect,
                    label=label,
                )
            except Exception as e:  # noqa: BLE001 -- the plugin's refusal, said
                self.logger.error(f"Painter refused {label}: {e}")
                return result
            result.update(job=job, delivered=True)
            self.logger.info(f"{label} queued in {self.painter_label(started.record or {})}.")
            if request.mode == ROUND_TRIP:
                status = self.wait(result)
                result["status"] = status
                result["delivered"] = status.get("state") == "done"
            self._announce_handoff(request.template, request.mode, payload.primary, payload.extras["output_dir"])
            return result

        if background is None or not (launched or to_open):
            return queue(bring_up())
        on_queued = request.get("on_queued") or (lambda _result: None)
        background(bring_up, lambda error: on_queued(queue(error)))
        return dict(result, pending=True)

    def wait(self, result: Dict[str, Any], timeout: float = 3600.0, on_status=None) -> Dict[str, Any]:
        """Follow a send's job to its end (:meth:`follow`) and say how it ended
        (:meth:`report`); its last status."""
        status = self.follow(result, timeout, on_status)
        self.report(status)
        return status

    def cancel(self, result: Dict[str, Any]) -> bool:
        """Ask Painter to stop a send's job: one still queued never starts, a
        running one stops at its next check (a bake under way stops). True
        when Painter took the request."""
        connection, job = result.get("connection"), result.get("job")
        if connection is None or not job:
            return False
        try:
            connection.rpc.cancel(job)
        except Exception as e:  # noqa: BLE001 -- said; the job may already have ended
            self.logger.warning(f"Painter did not take the cancel: {e}")
            return False
        self.logger.info("Asked Painter to stop the script.")
        return True

    @staticmethod
    def follow(result: Dict[str, Any], timeout: float = 3600.0, on_status=None) -> Dict[str, Any]:
        """Wait for a send's job to end, saying nothing (safe off the UI thread);
        its last status (``state``, ``report``, ``console``, ``error``).
        *on_status* gets each status on the way (a panel's progress)."""
        connection, job = result.get("connection"), result.get("job")
        if connection is None or not job:
            return {"state": "failed", "error": "Nothing was queued."}
        try:
            return connection.rpc.wait(job, timeout=timeout, on_status=on_status)
        except (TimeoutError, ConnectionError) as e:
            return {"job": job, "state": "failed", "error": str(e)}

    def conclude(self, task: Any) -> Dict[str, Any]:
        """A panel's end of a background :meth:`follow` (on the UI thread): the
        finished *task*'s status (a failed one when it raised), said
        (:meth:`report`)."""
        try:
            status = task.result()
        except Exception as error:  # noqa: BLE001 -- said, the panel stays up
            status = {"state": "failed", "error": str(error)}
        self.report(status)
        return status

    @classmethod
    def painter_choices(cls) -> List[Tuple[str, str]]:
        """The Painter row's entries: Auto, New Painter, and every running
        Painter serving the plugin, by the project it holds."""
        try:
            records = cls.painters()
        except Exception:  # noqa: BLE001 -- an unreadable record folder lists none
            records = []
        return [("Auto", PAINTER_AUTO), ("New Painter", PAINTER_NEW)] + [
            (cls.painter_label(r), r.get("id")) for r in records
        ]

    def report(self, status: Dict[str, Any]) -> bool:
        """Log a finished job: its report lines, then how it ended. True when it ran."""
        for line in status.get("report") or ():
            self.logger.info(line)
        name = status.get("label") or "The script"
        if status.get("state") == "done":
            self.logger.info(f"{name} finished in Painter.")
            return True
        if status.get("state") == "cancelled":
            self.logger.warning(f"{name} was cancelled.")
        else:
            self.logger.error(f"{name} failed in Painter: {status.get('error')}")
        return False

    @staticmethod
    def label(script: str) -> str:
        """*script*'s name as a person reads it: ``bake_lighting`` -> ``Bake Lighting``."""
        small = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
        words = [w for w in script.replace("-", "_").split("_") if w]
        return " ".join(w if i and w.lower() in small else w[:1].upper() + w[1:] for i, w in enumerate(words)) or script

    # -- Helpers ----------------------------------------------------------

    def refresh_rpc_plugin(self) -> None:
        """Bring the person's install of the plugin up to date when it has
        drifted (a copy predating this package serves stale ops); never install
        one -- that is the person's act (:meth:`install_rpc_plugin`)."""
        try:
            dest = ptk.PluginInstaller.refresh(self.RPC_PLUGIN)
            if dest is not None:
                self.logger.info(
                    f"Refreshed Painter's substance_rpc plugin: {dest}. A Painter already "
                    "open serves it after Python > Reload Plugins Folder."
                )
        except Exception as e:  # noqa: BLE001 -- never block the hand-off
            self.logger.warning(f"substance_rpc plugin refresh failed: {e}")

    def install_rpc_plugin(self) -> Optional[str]:
        """The person's *Install in Painter* (the shared installer,
        :meth:`pythontk.PluginInstaller.install`): installed, ticked in
        Painter's Python menu the first time (unless they set it either way),
        and said. The plugin folder, or ``None``."""
        dest = ptk.PluginInstaller.install(self.RPC_PLUGIN, logger=self.logger)
        return None if dest is None else str(dest)

    #: :class:`pythontk.MapFactory` types Painter accepts as a **mesh map**
    #: (its baked-geometry inputs), mapped to the usage name it files them
    #: under. Everything else a material references -- base color, roughness,
    #: metallic, emission -- is a *material channel*: real data, but not
    #: something ``--mesh-map`` or ``set_mesh_map_resource`` can take.
    #:
    #: Deliberately NOT height/displacement: Painter's bake list is normal,
    #: world-space normal, ID, ambient occlusion, curvature, position and
    #: thickness -- there is no height mesh map, so shipping one as a mesh
    #: map is the same mistake as shipping a base color.
    MESH_MAP_TYPES = {
        "Ambient_Occlusion": "ambient_occlusion",
        "Normal": "normal",
        "Normal_DirectX": "normal",
        "Normal_OpenGL": "normal",
        "Thickness": "thickness",
    }

    @classmethod
    def _mesh_map_assignments(
        cls,
        materials: Dict[str, Dict[str, str]],
        staged: List[str],
        prefix: str = "",
        suffix: str = "",
    ) -> Dict[str, Dict[str, str]]:
        """``{material: {usage: staged_path}}`` for the mesh maps we shipped.

        Painter names each texture set after the FBX material, so a section
        keyed by material name is directly addressable on the far side --
        which is the whole point: ``--mesh-map`` can only apply a map to
        *everything*, and a per-material scene then gets one material's AO
        smeared across every texture set.

        Matching is by **base texture name**, not by path: the manifest
        records where a map lives in the host scene, while the file Painter
        reads is the staged copy -- renamed by the Texture Affix, or produced
        by unpacking a packed source, in which case no manifest slot points at
        it at all. ``MapFactory.get_base_texture_name`` strips the map
        suffix from both sides (and the user affix from the staged side) so
        ``body_ORM.png`` -> ``hero_body_AO.png`` still resolves to the
        material that referenced ``body_ORM.png``.
        """
        if not materials or not staged:
            return {}

        # Staged mesh maps grouped by base texture name -> {usage: path}. Each
        # batch reads its files against each other (`classify_textures`), so a
        # set named for a lobe (`Hero_Coat`) keeps its normal a mesh map.
        by_base: Dict[str, Dict[str, str]] = {}
        readings = ptk.MapFactory.classify_textures(
            staged, prefix=prefix, suffix=suffix
        )
        for path, (map_type, base) in readings.items():
            usage = cls.MESH_MAP_TYPES.get(map_type)
            if usage:
                by_base.setdefault(base, {}).setdefault(usage, path)

        assignments: Dict[str, Dict[str, str]] = {}
        for material, slots in materials.items():
            # Sorted, not a bare set: a material whose slots span two base
            # names could otherwise resolve a usage to a different file on
            # each run, since set iteration order is hash-seed dependent.
            bases = sorted(
                {
                    base
                    for _type, base in ptk.MapFactory.classify_textures(
                        [p for p in slots.values() if p]
                    ).values()
                }
            )
            found: Dict[str, str] = {}
            for base in bases:
                for usage, path in by_base.get(base, {}).items():
                    found.setdefault(usage, path.replace("\\", "/"))
            if found:
                assignments[material] = found
        return assignments

    @classmethod
    def mesh_map_files(cls, paths: List[str]) -> List[str]:
        """The subset of *paths* Painter can actually use as mesh maps.

        Painter files a ``--mesh-map`` by reading the filename suffix; a
        base-color or roughness map handed to that flag is not a mesh map
        under any name, so passing the whole staged set (as this bridge
        used to) just hands Painter files it has no slot for. The paths read
        as one batch (``MapFactory.resolve_map_types``).
        """
        map_types = ptk.MapFactory.resolve_map_types(paths)
        return [p for p in paths if map_types[p] in cls.MESH_MAP_TYPES]

    def _stage_assigned_textures(
        self,
        objects: List[Any],
        output_dir: str,
        prefix: str = "",
        suffix: str = "",
    ) -> List[str]:
        """Copy every texture assigned to *objects*' materials into *output_dir*.

        Reads the resolved texture paths through :meth:`_assigned_texture_paths`
        (the host's ``MatUtils.get_texture_paths``) and copies each existing
        file into *output_dir* so Painter's "Import Baked Maps" dialog can pick
        them up alongside the FBX. Skips paths whose source doesn't exist on
        disk (logs a warning for each).

        *prefix* / *suffix* are the resolved halves of the panel's Texture
        Affix (see :meth:`_affix_basename` for where a suffix lands and why).
        Applying them is idempotent: a name that already carries the affix
        keeps exactly one.

        A channel-packed source (ORM / MRAO / MSAO / MetallicSmoothness /
        AlbedoTransparency) always contributes its **component maps** instead
        of itself -- Painter identifies a map by filename suffix and has no
        concept of a packed one, so the packed file is dead weight while its
        channels are exactly what Painter wants. There is deliberately no knob
        for this: "ship a file Painter cannot read" is not a mode anyone picks
        on purpose. The return stays a flat list of staged paths, so a caller
        that only needs "what landed in the folder" is unaffected.

        Packed sources are staged **first** so that a material carrying both
        ``body_ORM.png`` and an authored ``body_AO.png`` -- which land on the
        same destination name -- ends up with the authored map: it is the real
        thing, the packed file's occlusion channel is a by-product. Left to
        material-graph order the winner would flip run to run.

        Returns the list of staged destination paths, de-duplicated: a
        destination reached twice is one file, and listing it twice would emit
        a duplicate ``--mesh-map`` pair on Painter's command line.
        """
        import shutil

        try:
            paths = self._assigned_texture_paths(objects)
        except Exception as e:  # noqa: BLE001 -- staging is best-effort
            self.logger.warning(f"Texture collection failed: {e}")
            return []

        sources: List[str] = []
        for src in paths:
            src = str(src)
            if not src or not os.path.isfile(src):
                self.logger.warning("Assigned texture missing on disk: %s", src)
                continue
            sources.append(src)

        # Packed first (see docstring): a later plain copy overwrites the
        # component it collides with, which is the precedence we want.
        sources.sort(
            key=lambda p: ptk.MapFactory.resolve_map_type(p) not in self._UNPACKERS
        )
        # The base each affix wraps, the sources read as one batch: a set named
        # for a lobe (`Hero_Coat`) keeps its whole name ahead of the suffix.
        readings = ptk.MapFactory.classify_textures(
            sources, prefix=prefix, suffix=suffix
        )

        staged: List[str] = []
        for src in sources:
            components = self._unpack_packed_map(src, output_dir, prefix, suffix)
            if components is not None:
                staged.extend(components)
                continue
            dst = os.path.join(
                output_dir,
                self._affix_basename(
                    os.path.basename(src), prefix, suffix, core=readings[src][1]
                ),
            )
            try:
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copyfile(src, dst)
            except OSError as e:
                self.logger.warning("Could not stage %s -> %s: %s", src, dst, e)
                continue
            staged.append(dst)
        result = ptk.remove_duplicates(staged)
        # ONE grouped record for the whole staging pass — a line per texture
        # put a paragraph break between every file in the panel's output.
        if result and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(
                f"Staged {len(result)} texture(s)",
                [os.path.basename(p) for p in result],
            )
        return result

    #: Packed map type -> the :class:`pythontk.MapFactory` unpacker for it.
    #: Keys are ``MapFactory.resolve_map_type`` results, so a type the
    #: registry learns later only needs an entry here to become splittable.
    _UNPACKERS = {
        "ORM": "unpack_orm_texture",
        "MRAO": "unpack_mrao_texture",
        "MSAO": "unpack_msao_texture",
        "Metallic_Smoothness": "unpack_metallic_smoothness",
        "Albedo_Transparency": "unpack_albedo_transparency",
    }

    def _unpack_packed_map(
        self, src: str, output_dir: str, prefix: str = "", suffix: str = ""
    ) -> Optional[List[str]]:
        """Split *src* into component maps in *output_dir*, or ``None``.

        ``None`` means "not a packed map" (or the split failed) and the
        caller should stage *src* verbatim -- an unusable packed file in
        the folder still beats no texture at all.

        The components come back named by :class:`pythontk.MapFactory`'s own
        suffixes (``_AO`` / ``_Roughness`` / ...), which is what Painter's
        filename-based detection keys on; the affix is applied afterwards so
        the naming rule matches the plain-copy path exactly.
        """
        map_type = ptk.MapFactory.resolve_map_type(src)
        unpacker = self._UNPACKERS.get(map_type)
        if unpacker is None:
            return None
        try:
            produced = getattr(ptk.MapFactory, unpacker)(
                src, output_dir=output_dir, save=True
            )
        except Exception as e:  # noqa: BLE001 -- fall back to a plain copy
            self.logger.warning(
                "Could not unpack %s map %s (%s); staging it as-is.",
                map_type,
                os.path.basename(src),
                e,
            )
            return None

        produced = [str(p) for p in produced or [] if os.path.isfile(str(p))]
        # One unpack's components are one set (`<base>_AO`, `<base>_Roughness`):
        # read together, a set named for a lobe keeps its name.
        readings = ptk.MapFactory.classify_textures(
            produced, prefix=prefix, suffix=suffix
        )
        components: List[str] = [
            self._apply_affix(path, prefix, suffix, core=readings[path][1])
            for path in produced
        ]
        if not components:
            return None
        self.logger.info(
            "Unpacked %s: %s -> %s",
            map_type,
            os.path.basename(src),
            ", ".join(os.path.basename(p) for p in components),
        )
        return components

    @staticmethod
    def _affix_basename(
        basename: str, prefix: str = "", suffix: str = "", core: Optional[str] = None
    ) -> str:
        """Apply the Texture Affix to one texture filename.

        *core* is the base the affix wraps when the caller read it against the
        file's set (``MapFactory.classify_textures``: a set named for a lobe,
        ``Hero_Coat``, is not ``Hero``); else the file's own base name.

        A **prefix** simply leads the name. A **suffix** lands before the
        map-type token, never after it: Painter classifies a map by the LAST
        token of the filename, so ``body_Normal.png`` + ``_hero`` has to
        become ``body_hero_Normal.png`` -- ``body_Normal_hero.png`` is a map
        Painter cannot recognise, which is the opposite of what staging is
        for. (This is also why the shared
        :meth:`pythontk.MapFactory.resolve_texture_filename`, whose own
        convention puts the user suffix after the map type, isn't reused
        here.)

        Idempotent: an affix already present is stripped before it is
        re-applied, so re-staging the same source never stacks a second copy.
        ``MapFactory.get_base_texture_name`` does that for a prefix, but only
        for a suffix that TRAILS the whole name -- ours sits mid-name, ahead of
        the map type -- so the leading edge of the tail is checked here too. A
        name whose base can't be located in the stem (an exotic spelling) falls
        back to wrapping the whole stem, which is still correct for a prefix
        and no worse than the alternative for a suffix.
        """
        if not prefix and not suffix:
            return basename
        stem, ext = os.path.splitext(basename)
        if core is None:
            core = ptk.MapFactory.get_base_texture_name(
                basename, prefix=prefix, suffix=suffix
            )
        # A suffix the registry left on the base (``body_hero`` from
        # ``body_hero_Normal``): drop it so re-applying can't double it.
        if (
            suffix
            and len(core) > len(suffix)
            and core[-len(suffix) :].lower() == suffix.lower()
        ):
            core = core[: -len(suffix)]
        index = stem.lower().find(core.lower()) if core else -1
        if index < 0:
            return f"{prefix}{stem}{suffix}{ext}"
        # Everything before the base is an affix this call is replacing;
        # everything after it (map-type token, UDIM tile) has to stay last --
        # minus one copy of the suffix if the name already carried it there.
        tail = stem[index + len(core) :]
        if suffix and tail[: len(suffix)].lower() == suffix.lower():
            tail = tail[len(suffix) :]
        return f"{prefix}{core}{suffix}{tail}{ext}"

    @classmethod
    def _apply_affix(
        cls, path: str, prefix: str = "", suffix: str = "", core: Optional[str] = None
    ) -> str:
        """Rename *path* in place to carry the affix; returns the final path.

        The on-disk counterpart to :meth:`_affix_basename` (*core* as there),
        for files a map unpacker has already written under its own name.
        """
        if not prefix and not suffix:
            return path
        directory, base = os.path.split(path)
        renamed = cls._affix_basename(base, prefix, suffix, core=core)
        if renamed == base:
            return path
        dst = os.path.join(directory, renamed)
        try:
            ptk.FileUtils.replace_file(path, dst)
        except OSError:
            return path
        return dst

    def _announce_handoff(
        self, template: str, mode: str, fbx_path: str, output_dir: str
    ) -> None:
        """Log clickable links to the output folder + Painter log.

        The mesh link is only surfaced if the file actually exists -- a
        script that works on a project exports nothing.
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

    # -- Templates --------------------------------------------------------

    @staticmethod
    def template_dirs() -> Tuple[Path, ...]:
        """Where a template name resolves, first match winning: the person's own
        Painter templates, then the bundled ``templates/``."""
        return ptk.ScriptTemplate.search_dirs(_TEMPLATE_DIR, _SCRIPTS_APP)

    @staticmethod
    def template_path(template: str) -> Path:
        """The file *template* is read from (:meth:`template_dirs`); a name found
        nowhere names the bundled path, which is then not a file."""
        found = ptk.ScriptTemplate.find(SubstanceEngine.template_dirs(), template, ".py")
        return found or _TEMPLATE_DIR / f"{template}.py"

    @staticmethod
    def list_templates() -> List[Path]:
        """Return user-visible templates, the person's own first (skips underscore-prefixed)."""
        return ptk.ScriptTemplate.list_templates(SubstanceEngine.template_dirs(), ".py")

    @staticmethod
    def parse_template(template_path: Path) -> Dict[str, Any]:
        """Read a script's header constants without running it.

        Returns a dict with every :data:`_TEMPLATE_FIELDS` key, falling back to
        :data:`_TEMPLATE_DEFAULTS` for any constant the script omits or sets to
        a wrong type.

        Parsing uses :func:`ast.literal_eval`, so a broken script can't
        import-crash the others. Each parsed value is type-checked against
        :data:`_TEMPLATE_TYPES`; a mismatch is logged and the default is used.
        """
        out: Dict[str, Any] = dict(_TEMPLATE_DEFAULTS)
        try:
            tree = ast.parse(template_path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError) as e:
            logger.warning("Could not parse script %s: %s", template_path, e)
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
                logger.warning("Script %s: %s is not a literal; using default.", template_path.name, target.id)
                continue
            expected = _TEMPLATE_TYPES.get(target.id)
            if expected is not None and not isinstance(value, expected):
                logger.warning(
                    "Script %s: %s has type %s, expected %s; using default.",
                    template_path.name,
                    target.id,
                    type(value).__name__,
                    expected,
                )
                continue
            out[target.id] = value
        # BRIDGE_MODES folds legacy spellings through the shared alias table
        # (this parser reads the file with ``ast``, not ``declared_modes``).
        modes = tuple(m for m in ptk.ScriptTemplate.normalize_modes(out.get("BRIDGE_MODES")) if m in _MODES)
        out["BRIDGE_MODES"] = modes or (SEND_TO,)
        if out["WORKS_ON"] not in _WORKS_ON:
            logger.warning(
                "Script %s: WORKS_ON=%r is not one of %s; using %r.",
                template_path.name,
                out["WORKS_ON"],
                _WORKS_ON,
                _TEMPLATE_DEFAULTS["WORKS_ON"],
            )
            out["WORKS_ON"] = _TEMPLATE_DEFAULTS["WORKS_ON"]
        return out

    @staticmethod
    def list_template_modes() -> List[Tuple[str, str]]:
        """Return ``[(stem, mode), ...]`` for every (script, mode) pairing."""
        out: List[Tuple[str, str]] = []
        for path in SubstanceEngine.list_templates():
            for mode in SubstanceEngine.parse_template(path)["BRIDGE_MODES"]:
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

    # -- Project folders ---------------------------------------------------

    #: The scene project's cache kind the mesh a Painter project is built
    #: from lands in when no Output Dir is named (``<project>/cache/substance``).
    MESH_CACHE_KIND = "substance"

    def _mesh_cache_dir(self) -> Optional[str]:
        """``<scene project>/cache/substance``, or ``None`` without a project."""
        ws = self._scene_workspace()
        return ws.cache_dir(self.MESH_CACHE_KIND) if ws is not None else None

    def _scene_keyed(self, base: str) -> str:
        """*base* plus a short key of the open scene's path (``<base>_<key8>``),
        *base* alone while unsaved: the name a scene's mesh takes in the
        project cache, which every scene of the project shares."""
        scene = self._scene_path()
        if not scene:
            return base
        key = ptk.CachedArtifact.key(os.path.normcase(os.path.abspath(scene)), length=8)
        return f"{base}_{key}"

    def _project_dir(self, kind: str) -> Optional[str]:
        """The scene project's ``"images"`` or ``"textures"`` folder, or ``None``."""
        ws = self._scene_workspace()
        if ws is None:
            return None
        return ws.images_dir() if kind == "images" else ws.source_images_dir

    @staticmethod
    def _is_temp_path(path: str) -> bool:
        """True when *path* is scratch ``ptk.TempArtifacts`` holds
        (:meth:`ptk.TempArtifacts.contains`)."""
        return ptk.TempArtifacts.contains(path)

    # -- DCC hooks (the scene I/O a subclass supplies) --------------------

    def _scene_workspace(self) -> Optional["ptk.Workspace"]:
        """The open scene's project (``ptk.Workspace``), or ``None``.

        Where a send with no Output Dir keeps what outlives it (the mesh, a
        render). The default -- no project -- keeps the whole send in scratch.
        """
        return None

    def _export_model_fbx(
        self,
        path: str,
        objects: List[Any],
        request: ptk.HandoffRequest,
        fbx_options: Dict[str, Any],
    ) -> None:
        """Write *objects* to the FBX at *path* with the merged *fbx_options*."""
        raise NotImplementedError

    def _export_model_usd(
        self,
        path: str,
        objects: List[Any],
        request: ptk.HandoffRequest,
        fbx_options: Dict[str, Any],
    ) -> None:
        """Write *objects* to the USD at *path* (host defaults + ``usd_options``)."""
        raise NotImplementedError

    def _selected_objects(self) -> List[Any]:
        """The host's current selection -- the scope when ``send`` got no objects."""
        raise NotImplementedError

    def _material_manifest(self, objects: List[Any]) -> Dict[str, Any]:
        """The ``MatManifest`` dict (``{"materials": {name: {slot: path}}, ...}``)."""
        raise NotImplementedError

    def _assigned_texture_paths(self, objects: List[Any]) -> List[str]:
        """Absolute paths of every texture assigned to *objects*' materials.

        May raise; :meth:`_stage_assigned_textures` logs and stages nothing.
        """
        raise NotImplementedError

    def _export_bake_source(
        self,
        fbx_path: str,
        fbx_options: Dict[str, Any],
        referenced: set,
        request: ptk.HandoffRequest,
    ) -> Optional[str]:
        """Export the scene's bake-source set beside *fbx_path*; the path or ``None``.

        ``None`` when the template doesn't claim ``BAKE_SOURCE_SET``, the scene
        defines no bake source, or the (optional, never fatal) export failed.
        """
        raise NotImplementedError

    @staticmethod
    def _scene_base_name() -> str:
        """The current scene's base name (no extension), or ``'untitled'``."""
        raise NotImplementedError

    @staticmethod
    def _scene_path() -> str:
        """The open scene's file, ``""`` while unsaved (and in this base)."""
        return ""

    @classmethod
    def _recorded_export_path(cls) -> Optional[str]:
        """The mesh path the last send recorded on the scene, or ``None``."""
        raise NotImplementedError

    @classmethod
    def _record_export_path(cls, fbx_path: str) -> None:
        """Persist *fbx_path* on the scene (best-effort; never raises)."""
        raise NotImplementedError
