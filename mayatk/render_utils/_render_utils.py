# !/usr/bin/python
# coding=utf-8
"""Render-control helpers.

Thin, reusable wrappers over Maya's render commands so UI panels stay
declarative and the render plumbing has a single home (SSoT):

* enumerate the renderers the user can pick (built-ins + installed plugins),
* make one of them active (loading its plugin on demand),
* drive the Render View — single render, smart redo, and IPR.

The Arnold preview *network* itself is a separate concern owned by
:class:`mayatk.ArnoldBridge`; this module only selects/launches renderers.
"""
import os
from typing import Dict, List, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

from mayatk.env_utils._env_utils import EnvUtils


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
            if name not in seen and cls._plugin_installed(plugin):
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

    @staticmethod
    def _plugin_installed(plugin: str) -> bool:
        """True if *plugin* is loaded, or its file is on the plugin search path.

        ``pluginInfo(path=True)`` only answers for already-loaded plugins, so an
        installed-but-unloaded renderer is detected by scanning
        ``MAYA_PLUG_IN_PATH`` for the plugin file (its module .mod adds the dir
        at startup).
        """
        try:
            if cmds.pluginInfo(plugin, query=True, loaded=True):
                return True
        except Exception:
            pass
        try:
            search = (os.environ.get("MAYA_PLUG_IN_PATH", "") or "").split(os.pathsep)
            for directory in search:
                if not directory:
                    continue
                for ext in (".mll", ".so", ".bundle", ".py"):
                    if os.path.exists(os.path.join(directory, plugin + ext)):
                        return True
        except Exception:
            pass
        return False

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
            EnvUtils.load_plugin(plugin)

    @classmethod
    def set_renderer(cls, name: str) -> None:
        """Make *name* the active renderer, loading its plugin if required."""
        cls._ensure_plugin(name)
        cmds.setAttr("defaultRenderGlobals.currentRenderer", name, type="string")

    # ----------------------------------------------------------- render view
    @staticmethod
    def render_camera(camera: str, editor: str = "render") -> None:
        """Render *camera* into the Render View, opening it if needed.

        Uses ``renderWindowRenderCamera`` (an interactive Render-View proc) so
        the result surfaces in the editor rather than rendering offscreen.
        """
        mel.eval(f'renderWindowRenderCamera "{editor}" "" "{camera}";')

    @staticmethod
    def redo_previous_render(editor: str = "render") -> None:
        """Re-render the last render with its previous settings (fast path)."""
        mel.eval(f'redoPreviousRender "{editor}";')

    @staticmethod
    def _ipr_procedure(renderer: str) -> Optional[str]:
        """The renderer's registered start-IPR MEL procedure, or None.

        Registered only once the renderer's plugin is loaded; the query raises
        for an unregistered renderer, which is treated as "no procedure".
        """
        try:
            return cmds.renderer(renderer, query=True, startIprRenderProcedure=True) or None
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
