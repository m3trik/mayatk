# !/usr/bin/python
# coding=utf-8
"""Arnold HDR environment manager.

Provides :class:`HdrManager` — a thin wrapper around an ``aiSkyDomeLight``
+ ``file`` texture network for image-based lighting. Its panel is tentacle's HDR
Manager (``slots/tools/hdr_manager.py``), which drives blendertk's twin too.

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
from typing import Callable, Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.display_utils._display_utils import DisplayUtils
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

    #: The knob properties of this host's environment: tentacle's HDR Manager
    #: shows a control only for a knob listed here (blendertk's twin, a world
    #: shader, has no ``samples`` or ``preview``).
    KNOBS: tuple = (
        "rotation",
        "visibility",
        "preview",
        "intensity",
        "exposure",
        "resolution",
        "samples",
        "diffuse",
        "specular",
    )

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
        return Plugins.is_loaded("mtoa")

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
            Plugins.load("mtoa")
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
        domes = sorted(cmds.ls(exactType="aiSkyDomeLight", long=True) or [])
        if not domes:
            return None
        matched = next(
            (d for d in domes if d.rsplit("|", 1)[-1] == self.hdr_env_name), None
        )
        if matched:
            return matched
        for dome in domes:
            if self._connected_file_node(dome):
                return dome
        return domes[0]

    @hdr_env.setter
    @CoreUtils.undoable
    def hdr_env(self, tex: Optional[str]) -> None:
        """Set (and lazily create) the skydome's HDR file texture, as ONE undo
        chunk (a dropdown pick is one user action).

        Passing ``None`` or an empty string is a no-op — use
        :meth:`clear` to remove the network. As a side effect this makes Arnold
        the active renderer (see :meth:`_ensure_arnold_renderer`): an
        ``aiSkyDomeLight`` renders in no other renderer, so applying an HDR
        implies "render with Arnold".
        """
        if not tex:
            return
        if not self.arnold_available():
            self.logger.warning(
                "Arnold (mtoa) plugin not available — cannot set HDR env."
            )
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
            if file_node
            else []
        )
        transform = NodeUtils.get_transform_node(node)
        for n in [*place2d, file_node, transform or node]:
            if n and cmds.objExists(n):
                try:
                    cmds.delete(n)
                except Exception as e:
                    self.logger.debug("Could not delete %s: %s", n, e)

    def select(self, part: str = "skydome") -> Optional[str]:
        """Select the environment's *part*, replacing the selection.

        Parameters:
            part: ``"skydome"`` (the light's shape), ``"transform"`` (its parent)
                or ``"file_node"`` (the texture driving its color).

        Returns:
            The node selected, or None (the selection left alone) when the scene
            has none. Maya's alone: blendertk's twin has no ``select`` (a world is
            not a selectable object).
        """
        node = {
            "skydome": lambda: self.hdr_env,
            "transform": lambda: self.hdr_env_transform,
            "file_node": lambda: self.hdr_file_node,
        }[part]()
        if not node:
            return None
        cmds.select(node, replace=True)
        return node

    @staticmethod
    def defer_apply(callback: Callable[[], None]) -> None:
        """Run *callback*, a scene edit a dropdown pick drives, on Maya's idle
        queue: off the combo signal.

        The combo popup's teardown is a re-entrant Qt loop, and a live Arnold IPR
        re-translating the skydome inside it leaves the RenderView stuck black (a
        cold mtoa load + node creation there can crash Maya). A
        ``QTimer.singleShot(0)`` is serviced by that nested loop too;
        ``cmds.evalDeferred`` is not. Normal priority, never ``lowestPriority``:
        verified live, a lowest-priority callback is starved while Maya sits idle,
        so a pick did nothing. Twin of blendertk's, which runs the edit at once (a
        world edit has no such hazard).
        """
        cmds.evalDeferred(callback)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    @classmethod
    def import_hdrs(
        cls,
        paths,
        folder: Optional[str],
        mode: str = "copy",
        on_conflict="reuse",
        strict: bool = False,
    ) -> dict:
        """Bring HDR/EXR *paths* into the project's texture folder (``sourceimages``)
        -- the HDR Manager's Add, for a script too. Twin of blendertk's.

        ``ptk.FileUtils.import_files`` into *folder*, each file checked by
        ``ptk.ImgUtils.validate_image_integrity`` (a partly synced cloud HDR is
        left out: Viewport 2.0 crashes loading one). Lighting the scene from an
        imported file is :attr:`hdr_env`'s. The folder is the caller's: it is
        the one the panel lists, and ``env_utils`` ranks above ``light_utils``.

        Parameters:
            paths: The HDR/EXR files.
            folder: The project's texture folder -- the one the panel lists
                (``EnvUtils.source_images_dir()``); a link does not read it.
            mode: ``"copy"`` / ``"move"`` into sourceimages (a file already under
                it is used in place), or ``"link"``: used where they are.
            on_conflict: A same-named file already in sourceimages: ``"reuse"``
                (when complete; else the source is left out), ``"overwrite"``, or
                ``(existing) -> bool`` -- True replaces it, False cancels.
            strict: Stop at the first refusal (the result's ``failed``) instead
                of leaving it out -- the one file a user picked by hand.

        Returns:
            dict: ``ptk.FileUtils.import_files``' result (``added``, ``skipped``,
            ``io``, ``failed``, ``cancelled``).

        Raises:
            ValueError: ``"copy"`` / ``"move"`` without an existing *folder*
                (``import_files``' refusal, worded for the user).
        """
        return ptk.FileUtils.import_files(
            paths,
            folder,
            mode=mode,
            on_conflict=on_conflict,
            validate=ptk.ImgUtils.validate_image_integrity,
            strict=strict,
        )

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
            camera=0,  # hidden from primary rays by default
            skyRadius=0,  # hide the sphere preview in viewport
        )
        transform = NodeUtils.get_transform_node(node)
        if transform:
            # Best-effort: hides the row and refreshes the panels, no-op under
            # mayapy / batch where no outliner panel exists.
            DisplayUtils.set_hidden_in_outliner(transform)
        return node

    @staticmethod
    def _connected_file_node(skydome: str) -> Optional[str]:
        """Return the file node feeding ``skydome.color``, if any."""
        files = (
            cmds.listConnections(
                f"{skydome}.color", source=True, destination=False, type="file"
            )
            or []
        )
        return files[0] if files else None

    @staticmethod
    def _attach_file_node(skydome: str, path: str) -> str:
        """Create a file+place2d pair and wire it into ``skydome.color``.

        Naming uses the image stem so the file node mirrors the texture
        (avoids the ``X_file_file`` collision that ``create_file_node``
        would otherwise produce when fed a name already ending in ``_file``).
        """
        stem = os.path.splitext(os.path.basename(path))[0] or "hdr"
        file_node, _ = MatUtils.create_file_node(path, name=stem, color_space="Raw")
        cmds.connectAttr(f"{file_node}.outColor", f"{skydome}.color", force=True)
        return file_node


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
