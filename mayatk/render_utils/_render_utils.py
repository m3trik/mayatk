# !/usr/bin/python
# coding=utf-8
"""Render-control helpers.

Thin, reusable wrappers over Maya's render commands so UI panels stay
declarative and the render plumbing has a single home (SSoT):

* enumerate the renderers the user can pick (built-ins + installed plugins),
* make one of them active (loading its plugin on demand),
* drive the Render View — single render, smart redo, and IPR,
* read and write the key render settings, with named presets over them
  (:meth:`RenderUtils.render_settings`, :meth:`RenderUtils.render_preset_store`),
* ready a scene so Arnold renders what the viewport shows
  (:meth:`RenderUtils.prepare_arnold_preview`).

The Arnold preview *network* for a material is owned by
:class:`mayatk.ArnoldBridge`; this module only decides when one is needed
(``render_utils`` ranks above ``mat_utils`` in the package's layer order).
"""

import os
import re
from typing import Any, Dict, List, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass
import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.mat_utils.arnold_bridge import ArnoldBridge


class RenderUtils(ptk.HelpMixin):
    """Renderer enumeration / selection and Render-View control."""

    # Always-offered built-in renderers (their UI registration is interactive-
    # only, so ``cmds.renderer(namesOfAvailableRenderers=True)`` can miss them
    # when queried headlessly — seed them so the picker is never empty).
    BUILTIN_RENDERERS = {
        "mayaSoftware": "Maya Software",
        "mayaHardware2": "Maya Hardware 2.0",
    }

    # Third-party renderers that register only once their plugin loads.
    # ``{renderer_name: (plugin, ui_label)}`` — used to surface installed-but-
    # unloaded renderers and to load the plugin on demand when one is chosen.
    OPTIONAL_RENDERERS = {
        "arnold": ("mtoa", "Arnold"),
        "vray": ("vrayformaya", "V-Ray"),
        "redshift": ("redshift4maya", "Redshift"),
    }

    # ----------------------------------------------------------- enumeration
    @classmethod
    def get_available_renderers(cls) -> List[Dict[str, object]]:
        """Renderers the user can pick.

        Built-ins, then every currently-registered renderer, then any known
        third-party renderer whose plugin is installed but not yet loaded.

        Returns:
            A list of ``{"name", "label", "loaded"}`` dicts in a stable order
            (built-ins first), de-duplicated by ``name``.
        """
        out: List[Dict[str, object]] = []
        seen = set()

        def _add(name: str, label: str, loaded: bool) -> None:
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "label": label, "loaded": loaded})

        for name, label in cls.BUILTIN_RENDERERS.items():
            _add(name, cls._renderer_label(name, label), True)

        for name in cls._registered_renderers():
            _add(name, cls._renderer_label(name), True)

        for name, (plugin, label) in cls.OPTIONAL_RENDERERS.items():
            if name not in seen and Plugins.available(plugin):
                _add(name, label, False)

        return out

    @staticmethod
    def _registered_renderers() -> List[str]:
        """Renderer names Maya currently has registered (UI-state dependent)."""
        try:
            return cmds.renderer(query=True, namesOfAvailableRenderers=True) or []
        except Exception:
            return []

    @staticmethod
    def _renderer_label(name: str, fallback: Optional[str] = None) -> str:
        """Friendly UI name for a renderer (falls back to a label / the name)."""
        try:
            label = cmds.renderer(name, query=True, rendererUIName=True)
        except Exception:
            label = None
        return label or fallback or name

    # ------------------------------------------------------------- selection
    @staticmethod
    def current_renderer() -> str:
        """The scene's active renderer (``defaultRenderGlobals.currentRenderer``)."""
        return cmds.getAttr("defaultRenderGlobals.currentRenderer")

    @classmethod
    def _ensure_plugin(cls, renderer: str) -> None:
        """Load *renderer*'s plugin on demand; no-op for built-ins."""
        plugin = cls.OPTIONAL_RENDERERS.get(renderer, (None,))[0]
        if plugin:
            Plugins.load(plugin)

    @classmethod
    def set_renderer(cls, name: str) -> None:
        """Make *name* the active renderer, loading its plugin if required."""
        cls._ensure_plugin(name)
        cmds.setAttr("defaultRenderGlobals.currentRenderer", name, type="string")

    # ------------------------------------------------------- render settings
    #: Arnold's scene-wide render options node (one per scene, created by
    #: :meth:`ensure_arnold_options`).
    ARNOLD_OPTIONS = "defaultArnoldRenderOptions"

    #: The key render settings, ``{key: plug}``. The keys are what a render
    #: preset stores, so a preset never depends on a UI's widget names. A
    #: setting whose node is absent (Arnold's, before Arnold is picked) is
    #: simply not reported.
    RENDER_SETTINGS = {
        "width": "defaultResolution.width",
        "height": "defaultResolution.height",
        "camera_samples": f"{ARNOLD_OPTIONS}.AASamples",
        "diffuse_samples": f"{ARNOLD_OPTIONS}.GIDiffuseSamples",
        "specular_samples": f"{ARNOLD_OPTIONS}.GISpecularSamples",
        "transmission_samples": f"{ARNOLD_OPTIONS}.GITransmissionSamples",
        "diffuse_depth": f"{ARNOLD_OPTIONS}.GIDiffuseDepth",
        "specular_depth": f"{ARNOLD_OPTIONS}.GISpecularDepth",
        "transmission_depth": f"{ARNOLD_OPTIONS}.GITransmissionDepth",
    }

    @classmethod
    def ensure_arnold_options(cls) -> str:
        """Load mtoa and create the scene's Arnold options if missing.

        What Maya's Render Settings does when Arnold is picked; MtoA's
        ``createOptions`` is idempotent.

        Returns:
            The options node name.

        Raises:
            Plugins.LoadError: mtoa is not installed.
        """
        Plugins.load("mtoa")
        if not cmds.objExists(cls.ARNOLD_OPTIONS):
            import mtoa.core

            mtoa.core.createOptions()
        return cls.ARNOLD_OPTIONS

    @classmethod
    def render_settings(cls) -> Dict[str, Any]:
        """The scene's current value of every :attr:`RENDER_SETTINGS` key.

        Returns:
            ``{key: value}`` for each setting whose node exists.
        """
        return {
            key: cmds.getAttr(plug)
            for key, plug in cls.RENDER_SETTINGS.items()
            if cmds.objExists(plug)
        }

    @classmethod
    @CoreUtils.undoable
    def set_render_settings(cls, values: Dict[str, Any]) -> int:
        """Write render settings to the scene.

        Arnold's options are created first when *values* names one of them,
        so a preset applies before the first Arnold render. Keys outside
        :attr:`RENDER_SETTINGS` (a preset's ``_meta``, a retired setting) are
        skipped.

        Parameters:
            values: ``{key: value}``, e.g. a loaded render preset.

        Returns:
            The number of settings written.
        """
        known = {k: v for k, v in values.items() if k in cls.RENDER_SETTINGS}
        if any(
            cls.RENDER_SETTINGS[k].startswith(f"{cls.ARNOLD_OPTIONS}.") for k in known
        ):
            cls.ensure_arnold_options()
        for key, value in known.items():
            cmds.setAttr(cls.RENDER_SETTINGS[key], value)
        return len(known)

    @staticmethod
    def render_preset_store() -> "ptk.PresetStore":
        """Named render-setting presets: shipped Arnold tiers + user presets.

        A preset is a ``{key: value}`` dict of :attr:`RENDER_SETTINGS` keys,
        applied with :meth:`set_render_settings`. Built-ins ship as JSON in
        this subpackage's ``presets/`` dir; user presets live under the shared
        config root, so a UI and a script resolve to one set.
        """
        builtin = os.path.join(os.path.dirname(__file__), "presets")
        return ptk.PresetStore("render", package="mayatk", builtin_dir=builtin)

    # --------------------------------------------------------------- cameras
    @staticmethod
    def render_cameras() -> List[str]:
        """The cameras a render can look through, the default first: the
        perspective camera (``persp``, or a namespaced ``ns:persp``), then the
        rest sorted. Each a transform as ``cmds.ls`` names it (the Render View's
        procs take a transform); a camera rig's aim helper ("...Target") is
        left out. Twin of blendertk's ``RenderUtils.render_cameras`` (the scene
        camera first there).

        Returns:
            list: Camera transforms; ``[]`` in a scene without one.
        """
        cameras = set()
        for shape in cmds.ls(type="camera") or []:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parents:
                cameras.update(cmds.ls(parents[0]) or [])
        names = sorted(c for c in cameras if "Target" not in c)
        default = next((c for c in names if c == "persp" or c.endswith(":persp")), None)
        return [default] + [c for c in names if c != default] if default else names

    # ----------------------------------------------------------- render view
    @staticmethod
    def render_camera(camera: str, render_mode: str = "render") -> None:
        """Render *camera* into the Render View, opening it if needed.

        Uses ``renderWindowRenderCamera`` (an interactive Render-View proc) so
        the result surfaces in the editor rather than rendering offscreen. The
        proc's first MEL argument is the render *mode*, not an editor; the
        editor slot is left empty so Maya auto-resolves it via
        ``showRenderView``. Valid ``render_mode`` values: "render",
        "renderRegion", "snapshot", "redoPreviousRender", "iprRender",
        "redoPreviousIprRender". A camera shape stands for its transform
        (the Render View's procs take a transform).
        """
        if cmds.objExists(camera) and cmds.objectType(camera) == "camera":
            parents = cmds.listRelatives(camera, parent=True, fullPath=True) or []
            if parents:
                camera = (cmds.ls(parents[0]) or parents)[0]
        mel.eval(f'renderWindowRenderCamera "{render_mode}" "" "{camera}";')

    @staticmethod
    def redo_previous_render(render_mode: str = "render") -> None:
        """Re-render the last render with its previous settings (fast path)."""
        mel.eval(f'redoPreviousRender "{render_mode}";')

    @staticmethod
    def _ipr_procedure(renderer: str) -> Optional[str]:
        """The renderer's registered start-IPR MEL procedure, or None.

        Registered only once the renderer's plugin is loaded; the query raises
        for an unregistered renderer, which is treated as "no procedure".
        """
        try:
            return (
                cmds.renderer(renderer, query=True, startIprRenderProcedure=True)
                or None
            )
        except Exception:
            return None

    @classmethod
    def supports_ipr(cls, renderer: Optional[str] = None) -> bool:
        """True if *renderer* can start an interactive (IPR) session.

        Lets the UI *disable* an IPR affordance up front rather than failing
        after the user asks for it. Authoritative when the renderer's plugin is
        loaded — it checks for a registered ``startIprRenderProcedure``. A known
        third-party renderer that's installed but not yet loaded hasn't
        registered its procedure yet, but Arnold / V-Ray / Redshift all provide
        IPR, so report True for those without forcing a costly plugin load just
        to gate a checkbox. (:meth:`start_ipr` loads the plugin and re-checks,
        so a renderer that ultimately exposes no procedure still fails safe.)
        """
        renderer = renderer or cls.current_renderer()
        return bool(cls._ipr_procedure(renderer)) or renderer in cls.OPTIONAL_RENDERERS

    @classmethod
    def start_ipr(cls, camera: str, renderer: Optional[str] = None) -> bool:
        """Launch interactive (IPR) realtime rendering for *renderer*.

        Delegates to the renderer's own registered ``startIprRenderProcedure``
        (Arnold / V-Ray / Redshift / Maya Software each register one) instead of
        reimplementing any of them. That procedure owns the full, correct recipe
        — e.g. MtoA's ``arnoldIprStart`` creates the options node, closes the
        legacy Render View, enables progressive refinement, and (critically)
        runs the whole thing through ``evalDeferred`` so the IPR translation
        never fires inside the calling UI event — the re-entrancy that otherwise
        leaves the Arnold render blank/corrupt.

        Returns:
            True if an IPR session was started, False when the renderer exposes
            no IPR procedure (the caller can fall back to a single render).
        """
        renderer = renderer or cls.current_renderer()
        # The procedure is only registered once the renderer's plugin is loaded.
        cls._ensure_plugin(renderer)
        proc = cls._ipr_procedure(renderer)
        if not proc:
            return False

        # Procedure signature: (string editor, int resX, int resY, string camera).
        width = int(cmds.getAttr("defaultResolution.width"))
        height = int(cmds.getAttr("defaultResolution.height"))
        mel.eval(f'{proc} "renderView" {width} {height} "{camera}"')
        return True

    # -------------------------------------------------------- arnold preview
    #: ``aiUserOptions`` token that makes Arnold's CAMERA rays see a mesh
    #: single-sided. ``sidedness`` is the mask of ray types that see both
    #: sides: AI_RAY_ALL (255) minus AI_RAY_CAMERA (1). Shadow and bounce rays
    #: stay double-sided, so a room lit from inside stays closed to its light.
    ARNOLD_CAMERA_CULL = "sidedness 254"

    #: ``fileInfo`` key holding the meshes (UUIDs, space-separated) whose
    #: :attr:`ARNOLD_CAMERA_CULL` the sync added -- the only copies it ever
    #: removes. Scene-level, so it saves with the scene and exports nowhere.
    ARNOLD_CAMERA_CULL_RECORD = "mtk_arnold_camera_cull"

    @classmethod
    @CoreUtils.undoable
    def prepare_arnold_preview(cls) -> Dict[str, List[str]]:
        """Give Arnold what it needs to render what the viewport shows.

        Two things the viewport draws that Arnold does not read:

        * a material MtoA cannot translate (StingrayPBS / ShaderFX) renders
          error magenta, so each gets its :class:`ArnoldBridge`;
        * Viewport 2.0 backface culling is display-only, so a cutaway wall the
          artist looks through renders its unlit back -- from outside, a lit
          room is a black box. :meth:`sync_arnold_backface_culling` mirrors it.

        Both are Arnold-only and left in place: IPR and every later Arnold
        render read the same scene, and neither touches what exports.

        Returns:
            ``{"bridged": [...], "culled": [...]}`` -- the bridge nodes this
            call created or extended, and every mesh now culled for Arnold
            camera rays.
        """
        Plugins.load("mtoa")
        materials = ArnoldBridge.unrenderable_materials()
        bridged = ArnoldBridge().add(materials=materials) if materials else []
        return {"bridged": bridged, "culled": cls.sync_arnold_backface_culling()}

    @classmethod
    @CoreUtils.undoable
    def sync_arnold_backface_culling(cls) -> List[str]:
        """Make Arnold camera rays cull the backfaces Viewport 2.0 culls.

        Derived state, re-synced on every call: each double-sided mesh with
        ``backfaceCulling`` on carries :attr:`ARNOLD_CAMERA_CULL` in its
        ``aiUserOptions``, and the token this sync added leaves a mesh whose
        culling was turned off. What it added is recorded with the scene
        (:attr:`ARNOLD_CAMERA_CULL_RECORD`, by mesh UUID), so an option the
        artist wrote -- the same token included -- is never removed. Composes
        with whatever else the options hold; a mesh whose options already set
        ``sidedness`` is the artist's call and is left alone. A single-sided
        mesh is skipped -- Arnold already culls it for every ray. A duplicate
        of a synced mesh carries a copy the record does not name, so that copy
        stays as authored.

        Returns:
            Full paths of the meshes now culled for Arnold camera rays (empty
            when mtoa is unloaded: ``aiUserOptions`` is an mtoa attribute).
        """
        if not Plugins.is_loaded("mtoa"):
            return []
        token = re.escape(cls.ARNOLD_CAMERA_CULL)
        ours = re.compile(rf"(?:^|\s+){token}(?=\s|$)")
        record = cls.ARNOLD_CAMERA_CULL_RECORD
        added = set(" ".join(cmds.fileInfo(record, query=True) or []).split())
        still_added = set()
        culled: List[str] = []
        for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
            plug = f"{shape}.aiUserOptions"
            if not cmds.attributeQuery("aiUserOptions", node=shape, exists=True):
                continue
            uuid = (cmds.ls(shape, uuid=True) or [""])[0]
            options = cmds.getAttr(plug) or ""
            has = bool(ours.search(options))
            mine = has and uuid in added
            wanted = bool(cmds.getAttr(f"{shape}.backfaceCulling")) and bool(
                cmds.getAttr(f"{shape}.doubleSided")
            )
            value = options
            if wanted and not has and not re.search(r"\bsidedness\b", options):
                value = f"{options.strip()} {cls.ARNOLD_CAMERA_CULL}".strip()
            elif mine and not wanted:
                value = ours.sub("", options).strip()
            if value != options:
                try:
                    cmds.setAttr(plug, value, type="string")
                except RuntimeError:  # locked, or a locked reference edit
                    value = options
            in_value = bool(ours.search(value))
            if in_value and (mine or not has):  # recorded, or added just now
                still_added.add(uuid)
            if wanted and in_value:
                culled.append(shape)
        if still_added != added:
            if still_added:
                cmds.fileInfo(record, " ".join(sorted(still_added)))
            else:
                cmds.fileInfo(remove=record)
        return culled
