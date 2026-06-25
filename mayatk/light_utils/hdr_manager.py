# !/usr/bin/python
# coding=utf-8
"""Arnold HDR environment manager.

Provides :class:`HdrManager` — a thin wrapper around an ``aiSkyDomeLight``
+ ``file`` texture network for image-based lighting, and
:class:`HdrManagerSlots` — the matching Switchboard slots class for the
``hdr_manager.ui`` panel.

Knobs the manager owns on the skydome:

* ``hdr_env``    — file texture (``.exr`` / ``.hdr``) wired into ``color``.
* ``intensity``  — linear multiplier on the light output.
* ``exposure``   — photographic stops (log2) via Arnold's ``aiExposure``;
  returns 0.0 / no-ops on older mtoa builds that don't expose it.
* ``rotation``   — Y rotation (azimuth) on the transform parent.
* ``visibility`` — primary-ray (``camera``) flag controlling whether the
  HDR is visible *as a backdrop* rather than just as lighting (in the render).
* ``sky_radius`` / ``preview`` — ``skyRadius``; the dome's display-sphere size
  in the interactive viewport (0 = hidden). Shows the HDR as a Viewport 2.0
  backdrop without rendering; lighting is unaffected (the dome is at infinity).
  ``preview`` is the on/off boolean over it (on → :attr:`PREVIEW_SKY_RADIUS`).
* ``resolution`` — importance-sampling resolution of the HDR (``resolution``).
* ``samples``    — light samples (``aiSamples``) governing IBL noise.
* ``diffuse`` / ``specular`` — per-component contribution scales
  (``aiDiffuse`` / ``aiSpecular``).

The skydome's scalar attributes (``intensity``, ``exposure``, ``resolution``,
``samples``, ``diffuse``, ``specular``) are all read/written through
:meth:`_get_light_attr` / :meth:`_set_light_attr`, which fall back to the
Arnold default (and no-op the write) when the attribute is absent — so the
manager stays robust across mtoa versions. (``rotation`` lives on the transform
and ``visibility`` is the ``camera`` flag, so those go direct.)

Maya 2025+ / Arnold (``mtoa``) plugin required for any network mutation.
"""
import os
import shutil
import subprocess
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class HdrManager(ptk.LoggingMixin, ptk.HelpMixin):
    """Manage a single ``aiSkyDomeLight`` + connected ``file`` texture.

    The class is intentionally stateless beyond a class-level node name:
    every method/property re-queries the scene, so external edits to the
    skydome are picked up on the next read.
    """

    # Trailing underscore preserved for backward compatibility with
    # existing scenes and tests that look up the node by this exact name.
    hdr_env_name: str = "aiSkyDomeLight_"

    # Sky radius applied when the viewport-preview toggle switches on — Arnold's
    # own default (a fresh aiSkyDomeLight is 1000), large enough to enclose a
    # typical scene so the HDR shows as a Viewport 2.0 backdrop. The exact size
    # is fine-tuned manually in the viewport; off = 0 (the manager's create-time
    # default, which hides the dome sphere).
    PREVIEW_SKY_RADIUS: float = 1000.0

    # ------------------------------------------------------------------
    # Plugin
    # ------------------------------------------------------------------

    @staticmethod
    def arnold_loaded() -> bool:
        """True if ``mtoa`` is *already* loaded — cheap, side-effect-free query.

        Unlike :meth:`arnold_available` this never triggers ``loadPlugin``,
        so it's safe for read-only UI gating (e.g. on panel open). Loading
        ``mtoa`` boots the whole Arnold renderer and costs seconds — defer
        that to the first mutating action, not merely showing the panel.
        """
        if cmds is None:
            return False
        try:
            return bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
        except Exception:
            return False

    @staticmethod
    def arnold_available() -> bool:
        """True if the ``mtoa`` plugin can be loaded right now.

        Loads ``mtoa`` if it isn't already (use :meth:`arnold_loaded` for a
        non-loading check). Never raises — returns ``False`` on any failure.
        """
        if HdrManager.arnold_loaded():
            return True
        if cmds is None:
            return False
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except Exception:
            return False
        return HdrManager.arnold_loaded()

    @classmethod
    def ensure_plugin_loaded(cls) -> bool:
        """Backward-compat alias for :meth:`arnold_available`."""
        return cls.arnold_available()

    # ------------------------------------------------------------------
    # Skydome accessors
    # ------------------------------------------------------------------

    @property
    def hdr_env(self) -> Optional[str]:
        """The active skydome shape node, or ``None`` if none exists.

        Prefers the canonically-named dome (this manager's, or an older scene's),
        but **adopts any** ``aiSkyDomeLight`` in the scene so the panel mirrors a
        skydome the user created another way — Arnold's own *Lights* menu, an
        imported/referenced scene, or a renamed node — instead of reporting
        ``None`` (and then silently building a *second* dome on the next pick).
        Among non-canonical domes it prefers one already wired to a ``file``
        texture (a real HDR environment) and is otherwise deterministic (first by
        name).
        """
        # Without mtoa loaded the aiSkyDomeLight type isn't registered and no
        # such node can exist — short-circuit to skip the ls (and the
        # "Unknown object type" warning it emits) on every UI sync. This keeps
        # panel open / refresh cheap now that Arnold is no longer force-loaded.
        if not self.arnold_loaded():
            return None
        domes = sorted(cmds.ls(exactType="aiSkyDomeLight") or [])
        if not domes:
            return None
        if self.hdr_env_name in domes:
            return self.hdr_env_name
        for dome in domes:
            if self._connected_file_node(dome):
                return dome
        return domes[0]

    @hdr_env.setter
    def hdr_env(self, tex: Optional[str]) -> None:
        """Set (and lazily create) the skydome's HDR file texture.

        Passing ``None`` or an empty string is a no-op — use
        :meth:`clear` to remove the network. As a side effect this makes Arnold
        the active renderer (see :meth:`_ensure_arnold_renderer`): an
        ``aiSkyDomeLight`` renders in no other renderer, so applying an HDR
        implies "render with Arnold".
        """
        if not tex:
            return
        if not self.arnold_available():
            self.logger.warning("Arnold (mtoa) plugin not available — cannot set HDR env.")
            return

        tex = str(tex)
        # Hard guard: an incomplete/corrupt image (a truncated or partially
        # synced cloud HDR, an interrupted export) loads as a null texture in
        # Viewport 2.0, which then crashes computing the skydome light's IBL
        # intensity (AtilImageHandler::GetIBLIntensity → access violation).
        # Refuse it here so every caller — slots, create_network, external
        # code — is safe. A merely *missing* file is allowed through: Maya
        # shows a checker for it and never reaches the IBL crash path.
        if os.path.isfile(tex):
            ok, reason = ptk.ImgUtils.validate_image_integrity(tex)
            if not ok:
                self.logger.warning(
                    "HDR rejected (%s); refusing to wire it into the skydome — "
                    "Viewport 2.0 would crash loading the incomplete image for "
                    "IBL: %s",
                    reason,
                    tex,
                )
                return

        # Only once we're committed to wiring a valid texture: this is the single
        # choke point for every apply path (create_network, the UI swap, the add
        # flow), so making Arnold active here covers them all. (A refused
        # incomplete image above returns before this, leaving the renderer alone.)
        self._ensure_arnold_renderer()

        node = self.hdr_env or self._create_skydome()
        file_node = self._connected_file_node(node)
        if file_node is None:
            # Fresh network — create_file_node wires path + colorSpace.
            self._attach_file_node(node, tex)
        else:
            # Existing file node — swap path in place, keep wiring intact.
            cmds.setAttr(f"{file_node}.fileTextureName", tex, type="string")
            # HDR/EXR data is scene-linear; tag colorSpace so Maya
            # doesn't double-apply an sRGB → linear conversion.
            try:
                cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
            except Exception:
                pass

    @property
    def hdr_env_transform(self) -> Optional[str]:
        """Transform parent of the skydome shape, or ``None``."""
        shape = self.hdr_env
        if not shape:
            return None
        return NodeUtils.get_transform_node(shape)

    @property
    def hdr_file_node(self) -> Optional[str]:
        """The ``file`` node currently driving ``color`` on the skydome."""
        node = self.hdr_env
        return self._connected_file_node(node) if node else None

    @property
    def hdr_file_path(self) -> Optional[str]:
        """Current HDR file path on disk, or ``None``."""
        file_node = self.hdr_file_node
        if not file_node:
            return None
        path = cmds.getAttr(f"{file_node}.fileTextureName")
        return path or None

    # ------------------------------------------------------------------
    # Continuous attributes
    # ------------------------------------------------------------------

    @property
    def visibility(self) -> bool:
        """Primary-ray visibility of the HDR (skydome as backdrop)."""
        node = self.hdr_env
        return bool(cmds.getAttr(f"{node}.camera")) if node else False

    @visibility.setter
    def visibility(self, state: bool) -> None:
        node = self.hdr_env
        if node:
            cmds.setAttr(f"{node}.camera", bool(state))

    def set_hdr_map_visibility(self, state: bool) -> None:
        """Backward-compat shim for :attr:`visibility`."""
        if not self.arnold_available():
            return
        self.visibility = state

    @property
    def sky_radius(self) -> float:
        """Viewport-preview dome radius (``skyRadius``); 0 = no viewport backdrop.

        Controls only the dome's *display* sphere in the interactive viewport —
        lighting and reflections are unaffected (the dome is at infinity). The
        manager creates the dome at ``skyRadius=0`` (hidden); raising it to a
        value that encloses the scene shows the HDR as a Viewport 2.0 backdrop
        without rendering. Distinct from :attr:`visibility` (the ``camera`` flag,
        which is the backdrop *in the Arnold render*).
        """
        return self._get_light_attr("skyRadius", 0.0)

    @sky_radius.setter
    def sky_radius(self, value: float) -> None:
        self._set_light_attr("skyRadius", max(0.0, float(value)))

    @property
    def preview(self) -> bool:
        """Viewport-preview visibility — True when the dome shows in the viewport.

        Convenience boolean over :attr:`sky_radius`: reads ``True`` when the
        dome's display sphere is sized (``skyRadius`` > 0), and on set toggles it
        to :attr:`PREVIEW_SKY_RADIUS` (on) or 0 (off). The exact size is then
        fine-tuned manually in the viewport. Distinct from :attr:`visibility`
        (the ``camera`` flag — the backdrop *in the Arnold render*).
        """
        return self.sky_radius > 0.0

    @preview.setter
    def preview(self, state: bool) -> None:
        self.sky_radius = self.PREVIEW_SKY_RADIUS if state else 0.0

    @property
    def rotation(self) -> float:
        """Y rotation (degrees, azimuth) of the skydome transform; 0 if absent."""
        transform = self.hdr_env_transform
        return float(cmds.getAttr(f"{transform}.rotateY")) if transform else 0.0

    @rotation.setter
    def rotation(self, degrees: float) -> None:
        transform = self.hdr_env_transform
        # No-op on a stale / partly-deleted network rather than throwing up into
        # the UI slot — a slider drag must never error.
        if not transform or not cmds.objExists(transform):
            return
        # setAttr, NOT cmds.rotate: the latter's positionals are (angleX, angleY,
        # angleZ, *objects), so passing the transform first made Maya parse the
        # angle as an object ("Object N is invalid") and raise on every set — the
        # swallowed exception is why the dome once never rotated. The dome only
        # ever spins around Y from a default pivot, so a direct setAttr suffices.
        try:
            cmds.setAttr(f"{transform}.rotateY", float(degrees))
        except Exception as e:  # a slider drag must never crash Maya
            self.logger.debug("rotation set skipped (%s): %s", transform, e)

    @property
    def intensity(self) -> float:
        """Linear light-output multiplier on the skydome; 1.0 if absent."""
        return self._get_light_attr("intensity", 1.0)

    @intensity.setter
    def intensity(self, value: float) -> None:
        self._set_light_attr("intensity", float(value))

    @property
    def exposure(self) -> float:
        """Photographic stops (log2) on the skydome's ``aiExposure``.

        Returns 0.0 when the skydome or attribute is absent (older mtoa).
        """
        return self._get_light_attr("aiExposure", 0.0)

    @exposure.setter
    def exposure(self, stops: float) -> None:
        self._set_light_attr("aiExposure", float(stops))

    @property
    def resolution(self) -> int:
        """Importance-sampling resolution of the HDR (``resolution``); 1000 if absent.

        Higher = cleaner light and shadows from bright spots (e.g. a sun
        baked into the HDR), at the cost of a longer sampling precompute.
        """
        return self._get_light_attr("resolution", 1000, cast=int)

    @resolution.setter
    def resolution(self, value: int) -> None:
        self._set_light_attr("resolution", int(value))

    @property
    def samples(self) -> int:
        """Light samples (``aiSamples``) — soft-IBL noise control; 1 if absent."""
        return self._get_light_attr("aiSamples", 1, cast=int)

    @samples.setter
    def samples(self, value: int) -> None:
        self._set_light_attr("aiSamples", int(value))

    @property
    def diffuse(self) -> float:
        """Diffuse contribution scale (``aiDiffuse``); 1.0 if absent."""
        return self._get_light_attr("aiDiffuse", 1.0)

    @diffuse.setter
    def diffuse(self, value: float) -> None:
        self._set_light_attr("aiDiffuse", float(value))

    @property
    def specular(self) -> float:
        """Specular contribution scale (``aiSpecular``); 1.0 if absent."""
        return self._get_light_attr("aiSpecular", 1.0)

    @specular.setter
    def specular(self, value: float) -> None:
        self._set_light_attr("aiSpecular", float(value))

    # ------------------------------------------------------------------
    # Network lifecycle
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_network(
        self,
        hdrMap: str = "",
        hdrMapVisibility: bool = False,
        intensity: Optional[float] = None,
        exposure: Optional[float] = None,
        rotation: Optional[float] = None,
        resolution: Optional[int] = None,
        samples: Optional[int] = None,
        diffuse: Optional[float] = None,
        specular: Optional[float] = None,
        preview: Optional[bool] = None,
    ) -> Optional[str]:
        """Apply settings to the (lazily-created) skydome network.

        Only non-``None`` knobs are written, so callers can update a subset
        without disturbing the rest. Returns the skydome shape node, or
        ``None`` if Arnold is unavailable.
        """
        if not self.arnold_available():
            self.logger.warning("Arnold (mtoa) not available — create_network skipped.")
            return None

        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)
        for attr, value in (
            ("intensity", intensity),
            ("exposure", exposure),
            ("rotation", rotation),
            ("resolution", resolution),
            ("samples", samples),
            ("diffuse", diffuse),
            ("specular", specular),
            ("preview", preview),
        ):
            if value is not None:
                setattr(self, attr, value)
        return self.hdr_env

    @CoreUtils.undoable
    def clear(self) -> None:
        """Remove the skydome and its connected file/place2d nodes."""
        node = self.hdr_env
        if not node:
            return
        file_node = self._connected_file_node(node)
        place2d = (
            cmds.listConnections(file_node, type="place2dTexture") or []
            if file_node else []
        )
        transform = NodeUtils.get_transform_node(node)
        for n in [*place2d, file_node, transform or node]:
            if n and cmds.objExists(n):
                try:
                    cmds.delete(n)
                except Exception as e:
                    self.logger.debug("Could not delete %s: %s", n, e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_arnold_renderer(self) -> None:
        """Make Arnold the active renderer if it isn't already.

        ``aiSkyDomeLight`` renders in **no other renderer** — a skydome built
        while Maya Software / Hardware (the fresh-scene default) is active renders
        **black no matter the visibility flag**, which is the usual cause of a
        "the HDR is set but the render is black" report. Applying an HDR therefore
        implies "render with Arnold". Delegates to :class:`RenderUtils` (the
        renderer-selection SSoT) and never raises — renderer bookkeeping must not
        break wiring the texture. mtoa is already loaded by every caller.
        """
        try:
            from mayatk.render_utils._render_utils import RenderUtils

            if RenderUtils.current_renderer() != "arnold":
                RenderUtils.set_renderer("arnold")
        except Exception as e:
            self.logger.debug("could not set Arnold as the active renderer: %s", e)

    def _get_light_attr(self, attr: str, default, cast=float):
        """Read numeric *attr* off the skydome shape.

        Returns *default* (cast left untouched) when the skydome or the
        attribute is absent — keeps the manager robust across mtoa builds
        that may not expose a given knob.
        """
        node = self.hdr_env
        if not node or not cmds.attributeQuery(attr, node=node, exists=True):
            return default
        return cast(cmds.getAttr(f"{node}.{attr}"))

    def _set_light_attr(self, attr: str, value) -> None:
        """Set numeric *attr* on the skydome shape; no-op if it's absent."""
        node = self.hdr_env
        if not node:
            return
        if cmds.attributeQuery(attr, node=node, exists=True):
            cmds.setAttr(f"{node}.{attr}", value)
        else:
            self.logger.debug("%s not present on %s; skipping.", attr, node)

    def _create_skydome(self) -> str:
        """Create the named ``aiSkyDomeLight`` and stash its transform."""
        node = NodeUtils.create_render_node(
            "aiSkyDomeLight",
            "asLight",
            name=self.hdr_env_name,
            camera=0,        # hidden from primary rays by default
            skyRadius=0,     # hide the sphere preview in viewport
        )
        transform = NodeUtils.get_transform_node(node)
        if transform:
            try:
                cmds.setAttr(f"{transform}.hiddenInOutliner", 1)
            except Exception:
                pass
            # Best-effort outliner refresh — silently no-op under mayapy
            # / batch where the panel doesn't exist.
            try:
                cmds.outlinerEditor("outlinerPanel1", edit=True, refresh=True)
            except Exception:
                pass
        return node

    @staticmethod
    def _connected_file_node(skydome: str) -> Optional[str]:
        """Return the file node feeding ``skydome.color``, if any."""
        files = cmds.listConnections(
            f"{skydome}.color", source=True, destination=False, type="file"
        ) or []
        return files[0] if files else None

    @staticmethod
    def _attach_file_node(skydome: str, path: str) -> str:
        """Create a file+place2d pair and wire it into ``skydome.color``.

        Naming uses the image stem so the file node mirrors the texture
        (avoids the ``X_file_file`` collision that ``create_file_node``
        would otherwise produce when fed a name already ending in ``_file``).
        """
        stem = os.path.splitext(os.path.basename(path))[0] or "hdr"
        file_node, _ = MatUtils.create_file_node(
            path, name=stem, color_space="Raw"
        )
        cmds.connectAttr(f"{file_node}.outColor", f"{skydome}.color", force=True)
        return file_node


class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the HDR Manager UI.

    Composition over inheritance: routes events through ``self.manager``
    rather than carrying business logic. The dropdown is a *live mirror* of
    the scene's HDR environment — it always shows the active map (or ``None``
    when no skydome is wired). Scene open / new / import and undo / redo all
    re-sync it via :class:`ScriptJobManager`, so it never drifts from reality.
    """

    # File-dialog filter for the browse button.
    HDR_FILTER: str = "HDR Images (*.exr *.hdr);;All Files (*.*)"

    # Sentinel userData for the dropdown's explicit "None" entry — selecting it
    # removes the scene's HDR environment. Distinct from "nothing picked yet"
    # (the placeholder / an unset combo, which carry no userData / None) so the
    # slot can tell an intentional clear from an empty selection.
    NONE_TOKEN: str = "<none>"
    NONE_LABEL: str = "None"

    # Add-HDR mode picker — index → (label, token). Index is the source of
    # truth so reordering the labels can't silently rename the dispatch token.
    _ADD_MODES: tuple = (
        ("Copy to sourceimages", "copy"),
        ("Move to sourceimages", "move"),
        ("Link to original location", "link"),
    )

    # Filesystem op per import mode (single source for both the single-file
    # and folder-batch import paths).
    _IO_OPS = {"copy": shutil.copy2, "move": shutil.move}

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hdr_manager
        self.manager = HdrManager()

        # Option-box plugins on the rotation slider, wired in
        # _setup_rotation_slider (deferred): an inline degrees value plus two
        # view toggles — viewport visibility (skyRadius preview) and render
        # visibility (camera flag). Held so _sync_ui_to_scene can push live
        # scene state into them.
        self._rotation_value = None
        self._viewport_toggle = None
        self._render_toggle = None

        # Keep the dropdown a live mirror of the scene's HDR environment.
        # Scene open / new / import swap the whole environment AND the
        # sourceimages set the list is drawn from → re-scan disk + re-sync.
        # Undo / Redo only flip which network is live (the files on disk are
        # untouched) → re-sync widgets + selection, but skip the disk re-scan so
        # a recursive sourceimages walk doesn't run on every undo of anything.
        # All are valid Maya scriptJob events, but each subscribe is guarded so
        # an event unavailable on some Maya build can't block the panel open.
        mgr = ScriptJobManager.instance()
        for events, handler in (
            (("SceneOpened", "NewSceneOpened", "SceneImported"), self._on_scene_changed),
            (("Undo", "Redo"), self._sync_ui_to_scene),
        ):
            for event in events:
                try:
                    mgr.subscribe(event, handler, owner=self)
                except Exception as e:
                    self.logger.debug(
                        "HDR Manager: scriptJob %r unavailable (%s)", event, e
                    )
        mgr.connect_cleanup(self.ui, owner=self)

        # Initial population is deferred to the next event-loop tick. The
        # switchboard constructs this slots instance *mid-load* — child
        # widgets (footer, spinboxes, slider) aren't wired onto self.ui until
        # register_children runs after __init__ returns, so touching them now
        # hits AttributeError on None. By the next tick the UI is fully wired.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Populate the combobox and sync widgets from the scene.

        Deferred from __init__ (see there) so the full UI is registered
        before any ``self.ui.<widget>`` access.
        """
        # Per-field reset buttons (uitk option-box) on the Intensity / Exposure
        # / Resolution / Samples / Diffuse / Specular spin boxes: click resets a
        # field to its default; Alt/Ctrl+click bypasses it (greyed, restorable).
        # Done here rather than __init__ because the spin boxes aren't wired onto
        # self.ui until register_children runs (see __init__); wrap before the
        # _sync_ui_to_scene reads below, since wrapping reparents the widgets.
        self.sb.add_reset_buttons(self.ui)
        self._setup_rotation_slider()

        self._refresh_combo()
        self._sync_ui_to_scene()

        # Gate UI affordances on plugin state — but do NOT force-load Arnold
        # here. loadPlugin("mtoa") boots the whole renderer (seconds) and was
        # the cause of the panel's slow open. Only report when it isn't loaded
        # yet; the real load happens lazily on the first mutating action.
        if not self.manager.arnold_loaded():
            self.ui.footer.setText("Arnold (mtoa) not loaded — loads on first use.")

    def _setup_rotation_slider(self) -> None:
        """Wire the rotation slider's option box: inline degrees value + two view toggles.

        Done here (deferred) rather than a ``slider000_init`` hook because
        wrapping a widget in its option box reparents it — and the switchboard
        only self-heals such deferred wrappers after ``register_children`` (the
        same reason :meth:`add_reset_buttons` runs here, see ``__init__``).

        The slider drives :attr:`HdrManager.rotation` (Y spin). Its option box
        holds, left-to-right:

        * a :class:`ValueOption` — the exact angle, typeable + live;
        * a **viewport-visibility** toggle (:attr:`HdrManager.preview` — the
          dome's ``skyRadius`` display in the interactive viewport); and
        * a **render-visibility** toggle (:attr:`HdrManager.visibility` — the
          ``camera`` flag, the backdrop in the Arnold render).

        Both toggles are non-persistent (``settings_key=False``): they mirror
        live scene state (set in :meth:`_sync_ui_to_scene`), never a stored UI
        pref — like the dropdown's ``restore_state=False``. The two toggles are
        built directly (not via ``add_toggle``) so both option instances are
        captured — ``find_option`` returns only the first of a shared type.
        """
        from uitk.widgets.optionBox.options.toggle import ToggleOption
        from uitk.widgets.optionBox.options.value import ValueOption

        ob = self.ui.slider000.option_box
        # ValueOption sorts ahead of the buttons (see OptionBox._option_order),
        # so the exact-angle field sits flush against the slider, left of both
        # toggles. Viewport toggle added before render → viewport is the first
        # (left) button (option sort is stable within the shared "toggle" key).
        ob.add_value(width=46, suffix="°")
        self._viewport_toggle = ToggleOption(
            wrapped_widget=self.ui.slider000,
            icon="screen",
            tooltip_on="HDR shown as a backdrop in the viewport. Click to hide.",
            tooltip_off="HDR hidden in the viewport (skyRadius 0). Click to show.",
            initial=False,
            settings_key=False,
        )
        self._viewport_toggle.toggled.connect(self._on_viewport_visible)
        self._render_toggle = ToggleOption(
            wrapped_widget=self.ui.slider000,
            icon="camera",
            tooltip_on="HDR visible as a backdrop in the Arnold render. Click to hide.",
            tooltip_off="HDR hidden from the render (lighting only). Click to show.",
            initial=False,
            settings_key=False,
        )
        self._render_toggle.toggled.connect(self._on_render_visible)
        ob.add_option(self._viewport_toggle)
        ob.add_option(self._render_toggle)
        # Force the lazy wrap now so the field + toggles render with the panel.
        _ = ob.container
        self._rotation_value = ob.find_option(ValueOption)

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def _notify(
        self,
        message: str,
        *,
        level: str = "info",
        detail: Optional[str] = None,
        dialog: bool = False,
        dialog_text: Optional[str] = None,
    ) -> None:
        """Surface feedback consistently across the panel.

        Routes a single call to three places so the user sees the right
        amount of detail in the right place:

          * the **footer** — short, colour-coded by *level*
            (``info``/``success``/``warning``/``error``);
          * the **console** (Maya Script Editor) — the full *detail* at the
            matching log level, so errors carry the path/reason, not just the
            elided one-liner;
          * a modal **MessageBox** — only when *dialog* is True, for failures
            that need the user to act before retrying. The dialog stays
            *digestible*: it shows *dialog_text* (or the short *message*),
            never the full *detail* — long raw paths belong in the console,
            not in a popup.

        Parameters:
            message: Short footer text.
            level: Severity — drives footer colour, log level, and dialog prefix.
            detail: Full text for the console only; defaults to *message*.
            dialog: Also raise a blocking MessageBox (auto-coloured prefix).
            dialog_text: Digestible body for the dialog; defaults to *message*.
        """
        self.ui.footer.setText(message, level=level)

        full = detail or message
        logger = self.logger
        {
            "error": logger.error,
            "warning": logger.warning,
        }.get(level, logger.info)(full)

        if dialog:
            prefix = {
                "error": "Error:",
                "warning": "Warning:",
                "success": "Result:",
            }.get(level, "Note:")
            # Keep the popup digestible — the short message / explicit
            # dialog_text, not the full console detail (which can carry a long
            # raw path). MessageBox.setText auto-colours these level prefixes
            # (see uitk _html_style.PREFIX_STYLES). "Ok" makes the box modal.
            body = dialog_text or message
            self.sb.message_box(f"{prefix} {body}", "Ok")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure header menu and refresh button."""
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._refresh_and_sync_combo)
        widget.menu.add("Separator", setTitle="Sourceimages")
        widget.menu.add(
            "QPushButton",
            setText="Open Sourceimages Folder",
            setObjectName="open_sourceimages",
            setToolTip="Open the workspace's sourceimages folder in Explorer.",
        )
        widget.menu.add("Separator", setTitle="Network")
        widget.menu.add(
            "QPushButton",
            setText="Clear Network",
            setObjectName="clear_network",
            setToolTip="Delete the skydome and its connected file / place2d nodes.",
        )
        widget.set_help_text(
            fmt(
                title="HDR Manager",
                body="Manage the scene's Arnold HDR environment lighting "
                "(aiSkyDomeLight + file + place2dTexture network).",
                steps=[
                    "Pick an HDR / EXR from the dropdown to light the scene "
                    "(lists files in <i>sourceimages</i>); pick <b>None</b> to "
                    "remove the HDR environment.",
                    "Open the dropdown's option menu (▸) → <b>Add HDR(s)…</b> to "
                    "add images — one dialog picks loose files and/or a whole "
                    "folder; the import mode is set just below it.",
                    "Adjust <b>Intensity</b> (linear), <b>Exposure</b> (stops), "
                    "and <b>Resolution</b> (HDR importance-sampling res).",
                    "Drag the rotation slider to spin the environment around Y. "
                    "Its option box (▸) holds the exact angle plus two view "
                    "toggles: <b>viewport visibility</b> (show the HDR as a "
                    "viewport backdrop — fine-tune its size manually) and "
                    "<b>render visibility</b> (the HDR as a backdrop in the "
                    "Arnold <i>render</i>).",
                ],
                sections=[
                    ("Advanced Options (collapsible)", [
                        "<b>Samples</b> — light samples; raise to clean up "
                        "soft-IBL noise (<i>aiSamples</i>).",
                        "<b>Diffuse</b> / <b>Specular</b> — scale the dome's "
                        "diffuse vs specular contribution independently "
                        "(<i>aiDiffuse</i> / <i>aiSpecular</i>).",
                    ]),
                    ("Add HDR(s)… (option-box menu ▸)", [
                        "One dialog picks <b>loose files and/or a whole folder</b>; "
                        "folders are expanded to their .hdr/.exr contents. "
                        "Incomplete/corrupt files are skipped.",
                        "Files already inside <i>sourceimages</i> (any subfolder) "
                        "are used in place — never duplicated; the dropdown lists "
                        "them automatically.",
                        "<b>Copy</b> — duplicate an <i>external</i> file into "
                        "sourceimages (default; keeps scenes portable).",
                        "<b>Move</b> — relocate an external file into sourceimages.",
                        "<b>Link</b> — wire each in at its original path.",
                    ]),
                    ("Dropdown right-click", [
                        "Select skydome / file / transform nodes.",
                        "Reveal the texture in Explorer.",
                    ]),
                    ("Header menu", [
                        "<b>Open Sourceimages Folder</b> — Explorer shortcut.",
                        "<b>Clear Network</b> — delete the skydome and its "
                        "file / place2d nodes.",
                    ]),
                ],
                notes=[
                    "Requires the Arnold (mtoa) plugin to be loaded — the "
                    "footer reports if it's missing.",
                    "Applying an HDR switches the active renderer to <b>Arnold</b> "
                    "— an aiSkyDomeLight renders in no other renderer (a Maya "
                    "Software/Hardware render would be black regardless of Visible).",
                ],
            )
        )

    def cmb000_init(self, widget) -> None:
        """Wire the HDR dropdown: option-box plugins, context menu, auto-refresh."""
        # Re-scan disk AND re-point at the live scene HDR every time the user
        # opens the dropdown, so newly-saved HDRs appear and the active map is
        # highlighted without hitting the header refresh.
        widget.before_popup_shown.connect(self._refresh_and_sync_combo)

        # The dropdown mirrors the LIVE scene HDR, never a persisted UI value —
        # opt out of cross-session state restore. Otherwise the switchboard
        # restores the last index on panel open and (signals unblocked) fires
        # cmb000, auto-loading a stale HDR onto a fresh scene instead of showing
        # the true state. ComboBox.add() resets restore_state on every populate
        # (= not has_header), so _refresh_combo re-asserts this False each time;
        # this initial set covers the pre-refresh window.
        widget.restore_state = False

        # Right-click → context menu (MenuMixin on uitk ComboBox). Kept
        # separate from the option-box menu below (different Menu instance).
        widget.configure_menu(trigger_button="right")
        widget.menu.add(
            "QPushButton",
            setText="Select Skydome",
            setObjectName="ctx_select_skydome",
            setToolTip="Select the aiSkyDomeLight shape node in the scene.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select Transform",
            setObjectName="ctx_select_transform",
            setToolTip="Select the skydome's transform parent.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select File Node",
            setObjectName="ctx_select_file_node",
            setToolTip="Select the file texture node driving the skydome color.",
        )
        widget.menu.add("Separator")
        widget.menu.add(
            "QPushButton",
            setText="Reveal in Explorer",
            setObjectName="ctx_reveal_in_explorer",
            setToolTip="Open the HDR file's containing folder in Explorer.",
        )

        # Option-box menu (▸) on the combobox — the panel's sole add affordance.
        # The "Add HDR(s)…" button sits ABOVE the import-mode combo: one dialog
        # picks loose files and/or a whole folder, imported per the mode below.
        # (Non-slot objectName + explicit connect avoids a double-fire if the
        # menu auto-wires buttons to slots by objectName.)
        widget.option_box.menu.setTitle("Add HDR")
        add_btn = widget.option_box.menu.add(
            "QPushButton",
            setText="Add HDR(s)…",
            setObjectName="add_hdr_btn",
            setToolTip=(
                "Add HDR/EXR images — opens one dialog where you can pick loose "
                "files and/or a whole folder.\nEach is imported using the mode "
                "below. Incomplete/corrupt files are skipped."
            ),
        )
        add_btn.clicked.connect(self.add_hdr)
        widget.option_box.menu.add("Separator")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_add_mode",
            setToolTip=(
                "What 'Add HDR(s)…' does with the picked file(s):\n"
                "  • Copy — duplicate into sourceimages (default, scenes stay portable).\n"
                "  • Move — relocate into sourceimages (originals are removed).\n"
                "  • Link — leave in place and wire the original path directly\n"
                "    (won't appear in the dropdown afterward)."
            ),
            addItems=[label for label, _token in self._ADD_MODES],
        )

    # ------------------------------------------------------------------
    # Scene → UI sync
    # ------------------------------------------------------------------

    def _on_scene_changed(self) -> None:
        self._refresh_combo()
        self._sync_ui_to_scene()

    def _refresh_combo(self) -> None:
        """Repopulate the HDR combobox from the workspace sourceimages.

        Preserves the user's current selection across rebuilds (by data,
        not index) so refreshing on dropdown-open doesn't snap them back
        to the first entry.
        """
        previous_data = self.ui.cmb000.currentData()
        src = EnvUtils.get_env_info("sourceimages")

        if not src or not os.path.isdir(src):
            # Block signals so the rebuild doesn't fire cmb000 → set hdr_env.
            self.ui.cmb000.blockSignals(True)
            try:
                self.ui.cmb000.add([], clear=True)
                # Mirror the LIVE scene, never a persisted pick — re-assert the
                # opt-out (ComboBox.add resets restore_state on every populate).
                self.ui.cmb000.restore_state = False
                self._prepend_none_item()
            finally:
                self.ui.cmb000.blockSignals(False)
            # High-frequency path (fires on every dropdown open); colour the
            # footer but skip _notify so we don't spam the console log.
            self.ui.footer.setText(
                "No sourceimages directory in workspace.", level="warning"
            )
            return

        # Recursive so HDRs kept in a sourceimages *subfolder* (e.g.
        # ``sourceimages/hdr/``) list in the dropdown — they're already in
        # the project, so the add flow leaves them in place rather than
        # duplicating them into the root.
        hdr_info = ptk.get_dir_contents(
            src,
            ["filename", "filepath"],
            recursive=True,
            inc_files=["*.exr", "*.hdr"],
            group_by_type=True,
        )
        count = len(hdr_info["filename"])
        self.ui.footer.setText(
            f"{count} HDR{'s' if count != 1 else ''} in sourceimages."
        )

        # Skip the destructive clear+repopulate when the listed HDRs are
        # unchanged. _refresh_combo runs on EVERY dropdown-open
        # (before_popup_shown); rebuilding the item model right before the popup
        # shows can leave the popup view's selection desynced so the first click
        # is dropped (the "I have to reopen it to select" symptom). The disk
        # listing is the only input, so an unchanged listing → leave it intact.
        if self._listed_paths_match(hdr_info["filepath"]):
            return

        # Block signals so the rebuild doesn't fire cmb000 → set hdr_env
        # → re-trigger refresh while we're still rebuilding.
        self.ui.cmb000.blockSignals(True)
        try:
            # ComboBox.add() drives both userData and visible text. No header is
            # used: a header would paint a fixed "HDR Map:" label at index -1 and
            # (via ComboBox.check_index) snap the selection back to -1 after every
            # pick — hiding the active map AND zeroing currentData() so the apply
            # silently no-ops. The combo must instead display the live selection.
            self.ui.cmb000.add(
                zip(hdr_info["filename"], hdr_info["filepath"]),
                ascending=False,
                clear=True,
            )
            # Mirror the LIVE scene, never a persisted pick — re-assert the
            # opt-out (ComboBox.add resets restore_state on every populate).
            self.ui.cmb000.restore_state = False
            # Explicit "None" entry at the top so the user can clear the HDR
            # environment from the same dropdown that sets it.
            self._prepend_none_item()

            # Restore prior selection by data, not index.
            if previous_data:
                idx = self.ui.cmb000.findData(previous_data)
                if idx >= 0:
                    self.ui.cmb000.setCurrentIndex(idx)
        finally:
            self.ui.cmb000.blockSignals(False)

    def _listed_paths_match(self, new_paths) -> bool:
        """True if the dropdown already lists exactly *new_paths*.

        Compares the combo's real HDR entries (skipping the ``None`` sentinel)
        against a fresh disk listing, normalized for slash/case, so an unchanged
        ``sourceimages`` lets :meth:`_refresh_combo` skip the destructive
        repopulate. Order-independent — only membership matters for "did the
        available HDRs change". A transient unlisted-active row (a Link-mode HDR
        outside sourceimages) won't be in the listing, so this returns False and
        the rebuild still runs — correct, since that row is re-surfaced after.
        """
        combo = self.ui.cmb000
        current = set()
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data != self.NONE_TOKEN:
                current.add(self._norm_path(data))
        return current == {self._norm_path(p) for p in new_paths}

    def _prepend_none_item(self) -> None:
        """Insert the explicit 'None' entry at the top of the HDR dropdown.

        Selecting it removes the scene's HDR environment (see :meth:`cmb000` /
        :meth:`_apply_selection`). Carries :attr:`NONE_TOKEN` as userData so it's
        distinguishable from a real HDR path and from the unset placeholder.
        Callers run with the combo's signals blocked, so the implicit
        index shift from inserting at row 0 fires no slot.
        """
        self.ui.cmb000.insertItem(0, self.NONE_LABEL, self.NONE_TOKEN)

    def _select_combo_path(self, path: str) -> bool:
        """Select the dropdown entry whose file matches *path*.

        The combo stores the raw ``get_dir_contents`` filepaths, which can mix
        slash styles — ``os.path.join`` on Maya's forward-slash workspace path
        yields e.g. ``C:/proj/sourceimages\\x.hdr`` — while callers pass an
        ``os.path.normpath`` result (all backslashes). A plain ``findData`` then
        misses, leaving the just-added map unselected in the dropdown. Compare
        path-normalized + case-folded so those still match. Returns True on a
        hit (and selects it, signals blocked); False if no entry matches (e.g.
        a Link-mode file that lives outside sourceimages).
        """
        target = self._norm_path(path)
        combo = self.ui.cmb000
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and self._norm_path(data) == target:
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(i)
                finally:
                    combo.blockSignals(False)
                return True
        return False

    def _sync_ui_to_scene(self) -> None:
        """Pull live scene state into the UI widgets.

        When no skydome exists (fresh scene, after :meth:`clear_network`),
        falls back to neutral defaults so the controls don't display
        stale values from a deleted node.
        """
        has_env = bool(self.manager.hdr_env)
        rotation = int(round(self.manager.rotation)) % 360 if has_env else 0
        intensity = self.manager.intensity if has_env else 1.0
        exposure = self.manager.exposure if has_env else 0.0
        visible = self.manager.visibility if has_env else False
        preview = self.manager.preview if has_env else False
        resolution = self.manager.resolution if has_env else 1000
        samples = self.manager.samples if has_env else 1
        diffuse = self.manager.diffuse if has_env else 1.0
        specular = self.manager.specular if has_env else 1.0

        for widget, setter, value in (
            (self.ui.slider000, "setSliderPosition", rotation),
            (self.ui.spn_intensity, "setValue", intensity),
            (self.ui.spn_exposure, "setValue", exposure),
            (self.ui.spn_resolution, "setValue", resolution),
            (self.ui.spn_samples, "setValue", samples),
            (self.ui.spn_diffuse, "setValue", diffuse),
            (self.ui.spn_specular, "setValue", specular),
        ):
            widget.blockSignals(True)
            try:
                getattr(widget, setter)(value)
            finally:
                widget.blockSignals(False)

        # The two view toggles and the inline degrees field live in the rotation
        # slider's option box, not the .ui — push live state into them directly.
        # Toggles set silently (emit=False, no scene write); the value field is
        # refreshed since the blocked setSliderPosition above didn't fire the
        # slider's valueChanged that normally mirrors it.
        if self._viewport_toggle is not None:
            self._viewport_toggle.set_on(preview, emit=False)
        if self._render_toggle is not None:
            self._render_toggle.set_on(visible, emit=False)
        if self._rotation_value is not None:
            self._rotation_value.refresh()

        # Reflect the LIVE scene HDR in the dropdown so the combo always shows
        # what's actually lighting the scene (it's restore_state=False, so it
        # never shows a persisted pick).
        self._select_active_in_combo(has_env=has_env)

    def _select_active_in_combo(self, has_env: Optional[bool] = None) -> None:
        """Point the dropdown at the scene's active HDR — or ``None`` if absent.

        Keeps the combo a live mirror of the environment:

        * skydome wired to a **listed** file → select that row;
        * skydome wired to an **unlisted** file (e.g. a Link-mode HDR living
          outside ``sourceimages``) → surface it as a transient row so the combo
          still shows the *real* active map, not a misleading ``None``;
        * **no** skydome → select the explicit ``None`` entry (a clear "no HDR",
          not a blank box).

        Signals are blocked throughout so re-pointing never re-fires
        :meth:`cmb000`. Any transient row is rebuilt away by the next
        :meth:`_refresh_combo` and re-added here if the link is still active.
        """
        if has_env is None:
            has_env = bool(self.manager.hdr_env)
        current_path = self.manager.hdr_file_path if has_env else None
        if current_path and self._select_combo_path(current_path):
            return
        combo = self.ui.cmb000
        combo.blockSignals(True)
        try:
            if current_path:
                combo.addItem(os.path.basename(current_path), current_path)
                combo.setCurrentIndex(combo.count() - 1)
            else:
                idx = combo.findData(self.NONE_TOKEN)
                combo.setCurrentIndex(idx if idx >= 0 else -1)
        finally:
            combo.blockSignals(False)

    def _refresh_and_sync_combo(self) -> None:
        """Repopulate the list from disk, then re-point at the active HDR.

        Wired to the dropdown's pre-popup signal and the header Refresh action,
        so opening or refreshing the list always lands on (and highlights) the
        environment that's actually live in the scene. Only the combo is
        touched — the level/rotation widgets are left alone so a mid-edit value
        isn't clobbered just by opening the dropdown.
        """
        self._refresh_combo()
        self._select_active_in_combo()

    # ------------------------------------------------------------------
    # Read-only convenience
    # ------------------------------------------------------------------

    @property
    def hdr_map(self) -> Optional[str]:
        """Selected HDR file path from the combobox."""
        return self.ui.cmb000.currentData()

    @property
    def hdr_map_visibility(self) -> bool:
        """Render 'Visible' flag — read from the rotation slider's render toggle.

        Falls back to ``False`` if the toggle isn't wired yet (e.g. before
        :meth:`_setup_rotation_slider`)."""
        return bool(self._render_toggle.is_on) if self._render_toggle else False

    @property
    def hdr_map_preview(self) -> bool:
        """Viewport-preview flag — read from the rotation slider's viewport toggle.

        Falls back to ``False`` if the toggle isn't wired yet."""
        return bool(self._viewport_toggle.is_on) if self._viewport_toggle else False

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def cmb000(self, index, widget) -> None:
        """HDR map selection — the panel's sole apply action (always deferred).

        Every apply is pushed to Maya idle via :meth:`_defer_to_idle`:
        selecting a map (swap the live texture or build a new network) and
        selecting **None** (delete the network) all **mutate the scene**, and
        doing that *synchronously* inside the combobox ``currentIndexChanged``
        callback is unsafe. Mid popup-teardown the Qt event loop is re-entrant,
        and a live Arnold IPR re-translates the skydome from inside that
        re-entrancy — which leaves the render **black and stuck black even
        after reopening the RenderView** (and cold-start ``loadPlugin("mtoa")``
        + render-node creation there can crash Maya). Deferring to Maya idle
        (not a Qt tick — see :meth:`_defer_to_idle`) runs the mutation only
        once the popup has fully torn down; the idle read picks up the final
        pick if several arrive at once.
        """
        path = widget.currentData()
        if not path:  # placeholder / nothing picked
            return
        if path == self.NONE_TOKEN:
            self.ui.footer.setText("Removing HDR…", level="info")
        else:
            self.ui.footer.setText(f"Applying {os.path.basename(path)}…", level="info")
        self._defer_to_idle(self._apply_selection)

    def _on_viewport_visible(self, state) -> None:
        """Toggle the HDR's viewport backdrop — the rotation slider's first toggle.

        Sets :attr:`HdrManager.preview`, sizing the dome's ``skyRadius`` so the
        HDR shows in the interactive viewport (on) or hides it (off). Applied
        synchronously like the other live controls. Lighting is unaffected (the
        dome is at infinity); the exact size is fine-tuned manually in the
        viewport. Distinct from :meth:`_on_render_visible` (the Arnold *render*
        backdrop).
        """
        self.manager.preview = bool(state)

    def _on_render_visible(self, state) -> None:
        """Toggle skydome primary-ray visibility — the rotation slider's second toggle.

        Sets the ``camera`` flag (the HDR as a backdrop in the Arnold *render*).
        Applied **synchronously**, exactly like the other live controls
        (intensity / exposure / rotation), so an active Arnold IPR picks up the
        flag change immediately through its normal attribute-edit callback.

        Deliberately NOT deferred. Unlike the map swap (:meth:`cmb000`), a toggle
        has no combobox-popup-teardown re-entrancy to escape; worse, deferring it
        to ``evalDeferred(lowestPriority=True)`` let an active IPR (which keeps
        the idle queue busy) starve the ``setAttr``, so toggling Visible appeared
        to do nothing in the RenderView.
        """
        self.manager.set_hdr_map_visibility(bool(state))

    def slider000(self, value, widget) -> None:
        """Rotate the HDR around Y."""
        self.manager.rotation = value

    def spn_intensity(self, value) -> None:
        self.manager.intensity = value

    def spn_exposure(self, value) -> None:
        self.manager.exposure = value

    def spn_resolution(self, value) -> None:
        self.manager.resolution = value

    def spn_samples(self, value) -> None:
        self.manager.samples = value

    def spn_diffuse(self, value) -> None:
        self.manager.diffuse = value

    def spn_specular(self, value) -> None:
        self.manager.specular = value

    def _validate_or_warn(self, path: str, *, dialog: bool = True) -> bool:
        """True if *path* is safe to wire into the skydome.

        A missing file is allowed through (Maya shows a checker, no crash); an
        existing but incomplete/corrupt image — e.g. a truncated or partially
        synced cloud HDR — is refused, because wiring it crashes Viewport 2.0
        when it loads the image to compute the light's IBL intensity (null
        AtilImage → access violation). On refusal the failure is surfaced via
        :meth:`_notify` (colour-coded footer + full console detail, and a
        modal dialog when *dialog* is True).
        """
        if not os.path.isfile(path):
            return True
        ok, reason = ptk.ImgUtils.validate_image_integrity(path)
        if ok:
            return True
        name = os.path.basename(path)
        self._notify(
            f"{name} isn't fully downloaded ({reason})",
            level="error",
            detail=(
                f"HDR not loaded — only part of the file is on disk ({reason}):\n"
                f"{path}\n\n"
                "This is almost always an online-only cloud file (Dropbox / "
                "OneDrive) that hasn't finished syncing — Maya's viewport would "
                "crash trying to load it. In Explorer, right-click it → 'Make "
                "available offline' / 'Always keep on this device', wait for the "
                "download to finish, then retry. If the file is already fully "
                "local, it's truncated/corrupt — re-export or re-download it."
            ),
            dialog=dialog,
            # Digestible popup — filename + the fix, no raw path (that's logged
            # to the console for anyone who needs it).
            dialog_text=(
                f"{name} isn't fully downloaded ({reason}).\n\n"
                "It's almost certainly an online-only cloud file (Dropbox / "
                "OneDrive) that hasn't finished syncing. In Explorer, "
                "right-click it → 'Make available offline', wait for it to "
                "download, then retry.\n\n(Full path in the Script Editor.)"
            ),
        )
        return False

    def _clear_environment(self, absent_msg: str, *, absent_level: str = "info") -> bool:
        """Remove the skydome network, resync the UI, and report.

        Single owner of the clear path — shared by the deferred apply's "None"
        selection (:meth:`_apply_selection`) and the header's Clear Network
        action (:meth:`clear_network`). Returns True when
        a network was cleared; when none is present, reports *absent_msg* at
        *absent_level* and returns False. The ``hdr_env`` getter is None unless
        mtoa is already loaded, so this never forces a plugin load (and
        :meth:`HdrManager.clear` only touches existing nodes) — no
        ``arnold_available`` gate needed.
        """
        if not self.manager.hdr_env:
            self._notify(absent_msg, level=absent_level)
            return False
        self.manager.clear()
        self._sync_ui_to_scene()
        self._notify("HDR environment cleared.", level="success")
        return True

    def _defer_to_idle(self, callback) -> None:
        """Run *callback* on Maya's idle queue, escaping any nested Qt event loop.

        Scene mutations driven off the combo signal must not run inside the
        combo popup's teardown: that is a *re-entrant* Qt event loop, and a
        live Arnold IPR re-translating from within it leaves the RenderView
        stuck. ``QTimer.singleShot(0)`` is serviced by whatever event loop is
        currently spinning — the nested popup loop included — so it does **not**
        escape the re-entrancy. ``cmds.evalDeferred`` defers to Maya's idle
        queue, which the nested Qt loop never services, so the mutation runs
        only once the popup has fully torn down.

        Normal priority — **not** ``lowestPriority``. Both ride Maya's idle
        queue, so both need the event loop to *tick*; the difference is how
        starved they get. Verified live in a fresh Maya: a ``lowestPriority``
        callback stayed unrun for 7 s+ even with some activity, whereas a
        normal-priority one fires as soon as the loop has any activity to process
        — which an interacting user (the panel has focus, the cursor moves, the
        viewport repaints) or a running IPR always provides. So the prior
        ``lowestPriority`` starved the map-apply: the user picked a map and
        nothing happened ("the dropdown doesn't change the HDR") — an unverified
        "let Arnold settle first" precaution that traded the black-render for a
        dead dropdown. Normal priority still escapes the popup-teardown
        re-entrancy (same idle queue the nested Qt loop never services) but runs
        under real use. (A *totally* dead session can still delay it — that's
        inherent to idle deferral, but doesn't occur while a user is actually
        driving the panel.)
        """
        cmds.evalDeferred(callback)

    def _apply_selection(self) -> None:
        """Apply the current dropdown selection — clear / swap / build.

        The single, **deferred** apply for every dropdown action (see
        :meth:`cmb000` for why it must run off the combo signal). Re-reads the
        dropdown so it always applies the latest pick:

        * **None** → remove the skydome network;
        * **live network + a map** → swap the file texture in place (cheap
          ``setAttr``), preserving the live light settings;
        * **no network + a map** → build the network from the current UI state.
        """
        path = self.hdr_map
        if path == self.NONE_TOKEN:
            self._clear_environment("HDR is set to None — no environment to clear.")
            return
        if not path:
            self._notify("Pick or browse for an HDR first.", level="warning")
            self._select_active_in_combo()  # re-mirror the scene (see below)
            return
        # Casual dropdown selection → surface a bad file in the footer/console,
        # but don't interrupt with a modal (applying is a single click). On
        # rejection re-point the dropdown at the live scene HDR: the click
        # already moved the combo's display (and the popup's current-item
        # marker) to the rejected pick, so leaving it there makes the combo lie —
        # field shows the new map while the marker (= currentIndex) snaps back to
        # the still-active old map on the next open. Re-mirroring keeps the
        # dropdown an honest reflection of what's actually lighting the scene
        # (e.g. an online-only cloud HDR that can't be wired in is refused here).
        if not self._validate_or_warn(path, dialog=False):
            self._select_active_in_combo()
            return
        # ``hdr_env`` is truthy only when mtoa is already loaded (the getter
        # short-circuits otherwise), so a live network means a cheap in-place
        # swap with no plugin load and the live light settings preserved.
        if self.manager.hdr_env:
            self.manager.hdr_env = path
            self._sync_ui_to_scene()
            self._notify(f"HDR: {os.path.basename(path)}", level="success")
            return
        # No network yet → build it from the current UI state.
        if not self.manager.arnold_available():
            self._notify("Arnold (mtoa) plugin not loaded.", level="warning")
            self._select_active_in_combo()
            return
        self.manager.create_network(
            hdrMap=path,
            hdrMapVisibility=self.hdr_map_visibility,
            intensity=self.ui.spn_intensity.value(),
            exposure=self.ui.spn_exposure.value(),
            rotation=self.ui.slider000.sliderPosition(),
            resolution=self.ui.spn_resolution.value(),
            samples=self.ui.spn_samples.value(),
            diffuse=self.ui.spn_diffuse.value(),
            specular=self.ui.spn_specular.value(),
            preview=self.hdr_map_preview,
        )
        self._notify(f"Applied: {os.path.basename(path)}", level="success")

    def add_hdr(self) -> None:
        """Add HDR(s) from one dialog — pick loose files and/or a whole folder.

        Option-box menu action (the panel's sole add affordance). Selected
        directories are expanded to their ``.hdr`` / ``.exr`` contents; loose
        files are taken as-is. Everything is imported per the current mode.
        Picking a *single* loose file gets the careful UX (modal on a bad file,
        overwrite prompt); a folder or several files is a bulk add (skip+count).
        """
        start = EnvUtils.get_env_info("sourceimages") or ""
        selected = self._pick_hdr_paths(start)
        if not selected:
            return

        dirs = [p for p in selected if os.path.isdir(p)]
        files = [p for p in selected if os.path.isfile(p)]
        paths = list(files)
        for d in dirs:
            paths.extend(
                ptk.get_dir_contents(d, "filepath", inc_files=["*.exr", "*.hdr"]) or []
            )

        # One explicit loose file → careful; a folder or multiple → bulk.
        careful = len(files) == 1 and not dirs
        if dirs and not files:
            where = os.path.basename(dirs[0].rstrip("/\\")) or dirs[0]
        else:
            where = "selection"
        self._add_hdrs(paths, where=where, careful=careful)

    def _pick_hdr_paths(self, start: str) -> list:
        """Open one dialog that selects HDR/EXR files *and/or* folders.

        Qt has no native "files or directories" mode, so this drives a
        non-native ``QFileDialog`` with the internal item views switched to
        extended (multi) selection — letting the user pick loose files, a
        folder, or a mix, all returned by ``selectedFiles()``. Returns ``[]``
        on cancel.
        """
        QtW = self.sb.QtWidgets
        dialog = QtW.QFileDialog(
            self.ui, "Add HDR(s) — pick files and/or a folder", start
        )
        dialog.setFileMode(QtW.QFileDialog.ExistingFiles)
        dialog.setOption(QtW.QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilters(self.HDR_FILTER.split(";;"))
        # ``findChildren`` with a tuple of types isn't portable across bindings;
        # collect the list + tree views separately.
        views = dialog.findChildren(QtW.QListView) + dialog.findChildren(
            QtW.QTreeView
        )
        for view in views:
            view.setSelectionMode(QtW.QAbstractItemView.ExtendedSelection)
        if dialog.exec_():
            return dialog.selectedFiles() or []
        return []

    def _add_hdrs(self, paths: list, *, where: str, careful: bool) -> None:
        """Import HDR/EXR *paths* (loose files and/or folder contents) per mode.

        Single importer behind every add flow. *careful* selects the UX:

          * ``True`` — an explicit single-file pick: a bad file raises the
            actionable modal, and a real same-named collision in sourceimages
            prompts before overwrite.
          * ``False`` — a bulk add (a folder or several files): incomplete /
            corrupt files are skipped into a count (no per-file dialog), and an
            existing same-named file is reused rather than clobbered.

        Copy / Move bring each file into the ``sourceimages`` root (so it lists
        in the dropdown); Link wires it in place. Wires the last good HDR into
        the skydome and reports a summary. *where* is a short label for it.
        """
        if not paths:
            self._notify(f"No HDR/EXR files in {where}.", level="warning")
            return

        mode = self._add_mode()
        # Only Copy/Move need a destination; Link wires files in place.
        src = EnvUtils.get_env_info("sourceimages") if mode != "link" else None
        if mode != "link" and (not src or not os.path.isdir(src)):
            self._notify(
                "No sourceimages directory — set a Maya project first.",
                level="error",
                dialog=True,
            )
            return

        added, skipped, last, did_io = 0, 0, None, False
        for path in paths:
            if careful:
                # Explicit pick — surface a bad file with the modal guidance.
                if not self._validate_or_warn(path):
                    return
            elif not ptk.ImgUtils.validate_image_integrity(path)[0]:
                skipped += 1
                continue

            if mode == "link":
                last, added = path, added + 1
                continue

            # A file already inside sourceimages (root OR any subfolder) is used
            # in place — never duplicated into the root. The recursive dropdown
            # lists it regardless of depth, so Copy/Move on an already-project
            # file is a no-op rather than a duplicate.
            if self._is_under_dir(path, src):
                last, added = os.path.normpath(path), added + 1
                continue

            final = os.path.normpath(os.path.join(src, os.path.basename(path)))
            if os.path.exists(final):
                if careful:
                    if not self._confirm_overwrite(final):
                        self._notify(f"{mode.capitalize()} cancelled.", level="info")
                        return
                    # confirmed → fall through and overwrite
                elif ptk.ImgUtils.validate_image_integrity(final)[0]:
                    last, added = final, added + 1  # reuse a usable existing file
                    continue
                else:
                    skipped += 1  # existing is corrupt; never clobber in bulk
                    continue
            try:
                self._IO_OPS[mode](path, final)
            except OSError as e:
                self.logger.error("Add — %s of %s failed: %s", mode, path, e)
                if careful:
                    self._notify(
                        f"{mode.capitalize()} failed: {e}",
                        level="error",
                        detail=f"{mode} of {path} → {final} failed: {e}",
                        dialog=True,
                    )
                    return
                skipped += 1
                continue
            last, added, did_io = final, added + 1, True

        self._refresh_combo()
        if last:
            # Normalized match — combo data may be mixed-slash (os.path.join on
            # Maya's forward-slash workspace path) vs. last's normpath.
            self._select_combo_path(last)
            self.manager.hdr_env = last
            self._sync_ui_to_scene()

        self._notify_add_result(
            added, skipped, last, where=where, careful=careful, mode=mode, did_io=did_io
        )

    def _add_hdrs_from_folder(self, directory: str) -> None:
        """Bulk-add every ``.hdr`` / ``.exr`` in *directory* (per current mode)."""
        paths = (
            ptk.get_dir_contents(directory, "filepath", inc_files=["*.exr", "*.hdr"])
            or []
        )
        where = os.path.basename(directory.rstrip("/\\")) or directory
        self._add_hdrs(paths, where=where, careful=False)

    def _notify_add_result(
        self, added, skipped, last, *, where, careful, mode, did_io
    ) -> None:
        """Footer summary for an add — single rich line vs. bulk count."""
        if careful and added:
            name = os.path.basename(last)
            if mode == "link":
                verb = "Linked"
            elif did_io:
                verb = {"copy": "Copied & set", "move": "Moved & set"}[mode]
            else:
                verb = "Set"  # reused an existing sourceimages copy
            self._notify(f"{verb}: {name}", level="success")
        elif added:
            msg = f"Added {added} HDR{'s' if added != 1 else ''} from {where}"
            if skipped:
                msg += f" ({skipped} skipped — incomplete/corrupt)"
            self._notify(msg, level="success")
        else:
            self._notify(
                f"No usable HDRs in {where} ({skipped} incomplete/corrupt).",
                level="warning",
            )

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def open_sourceimages(self) -> None:
        """Open the workspace's sourceimages folder in Explorer."""
        src = EnvUtils.get_env_info("sourceimages")
        if not src or not os.path.isdir(src):
            self._notify("No sourceimages directory in workspace.", level="warning")
            return
        os.startfile(src)

    def clear_network(self) -> None:
        """Delete the skydome network and reset the UI to defaults."""
        self._clear_environment("No HDR network in scene.")

    # ------------------------------------------------------------------
    # Context-menu actions (right-click on cmb000)
    # ------------------------------------------------------------------

    def ctx_select_skydome(self) -> None:
        node = self.manager.hdr_env
        if not node:
            self._notify("No skydome in scene.", level="warning")
            return
        cmds.select(node, replace=True)
        self._notify(f"Selected: {node}", level="success")

    def ctx_select_transform(self) -> None:
        node = self.manager.hdr_env_transform
        if not node:
            self._notify("No skydome transform in scene.", level="warning")
            return
        cmds.select(node, replace=True)
        self._notify(f"Selected: {node}", level="success")

    def ctx_select_file_node(self) -> None:
        node = self.manager.hdr_file_node
        if not node:
            self._notify("No file node connected to the skydome.", level="warning")
            return
        cmds.select(node, replace=True)
        self._notify(f"Selected: {node}", level="success")

    def ctx_reveal_in_explorer(self) -> None:
        path = self.manager.hdr_file_path
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        # Fall back to the workspace sourceimages folder so the user can
        # still navigate from there when the texture is missing/unsaved.
        src = EnvUtils.get_env_info("sourceimages")
        if src and os.path.isdir(src):
            os.startfile(src)
        else:
            self._notify(
                "HDR file not found and no sourceimages folder.", level="warning"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_path(path) -> str:
        """Case-folded, separator-normalized path for robust comparison.

        The combo stores raw ``get_dir_contents`` filepaths, which can mix slash
        styles (``os.path.join`` on Maya's forward-slash workspace →
        ``C:/proj/sourceimages\\x.hdr``) while callers pass ``os.path.normpath``
        results — so equality must go through this, not a raw string compare.
        """
        return os.path.normcase(os.path.normpath(str(path)))

    @staticmethod
    def _is_under_dir(path: str, directory: str) -> bool:
        """True if *path* lies inside *directory* (root or any depth)."""
        p = HdrManagerSlots._norm_path(path)
        d = HdrManagerSlots._norm_path(directory)
        return p == d or p.startswith(d + os.sep)

    def _add_mode(self) -> str:
        """Return the active Add-HDR mode token: ``copy`` / ``move`` / ``link``.

        Falls back to ``copy`` if the option-box widget hasn't been
        attached yet (e.g. during early init or tests).
        """
        try:
            idx = self.ui.cmb000.option_box.menu.cmb_add_mode.currentIndex()
        except AttributeError:
            return "copy"
        if 0 <= idx < len(self._ADD_MODES):
            return self._ADD_MODES[idx][1]
        return "copy"

    def _confirm_overwrite(self, target_path: str) -> bool:
        """Ask the user before overwriting an existing file in sourceimages."""
        QtWidgets = self.sb.QtWidgets
        reply = QtWidgets.QMessageBox.question(
            self.ui,
            "Overwrite existing HDR?",
            f"{os.path.basename(target_path)} already exists in sourceimages.\n"
            "Overwrite it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return reply == QtWidgets.QMessageBox.Yes


# ----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("hdr_manager", reload=True)
    ui.show(pos="screen", app_exec=True)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
