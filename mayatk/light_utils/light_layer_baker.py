# !/usr/bin/python
# coding=utf-8
"""Light layers: each light of a rig baked alone, the raw material of a look match.

A *layer* is one light (or a few that move together) lighting the scene alone,
white, at one gain: its lightmaps and reflection probe. Light is additive, so
any mix of the layers -- each weighted and tinted -- is their weighted, tinted
sum, which pythontk's look-match engine predicts in milliseconds
(``ptk.LightLayers`` / ``ptk.LightPredictor``) and its capture renders exactly
in the WebXR viewer (``ptk.LookCapture``). This module makes the bakes; it
never reads a frame.

:meth:`LightLayerBaker.bake` works in the open scene. A production scene can
take minutes to open, so :meth:`LightLayerBaker.launch` starts a fresh
``mayapy`` on it once and feeds it jobs through a folder
(``ptk.JobQueue``) -- no command port, nothing another Maya could answer, and
the scene is never saved:

    python -m mayatk.light_utils.light_layer_baker launch --queue Q --scene S
    python -m mayatk.light_utils.light_layer_baker submit --queue Q job.json --wait
    python -m mayatk.light_utils.light_layer_baker stop --queue Q

A job: ``{"layers": {name: rig payload}, "output": folder, "base": {"existing":
..., "materials": ...}, "objects": [...], "settings": {LightLayerBaker kwargs}}``;
``"white": false`` bakes each payload as it is (a whole rig, its colours kept).
Each layer lands in ``<output>/<name>/``: the maps, the probe, and
``layer.json`` (its kind, spec, gain, timing and lights -- the lightmap record's
form, ``LightUtils.specular_lights``, which a capture draws in as highlights
-- and, from a worker, the save of the scene it was baked from:
``ptk.LightLayerScene``). A worker opens its scene once and opens it again
before a job when the file was saved since: it bakes the scene as saved.

A panorama job -- ``{"op": "panorama", "rig": name or payload, "positions":
{name: [x, y, z]}, "output": folder, "width": 1024}`` -- renders the room under
a whole rig from each point (:meth:`LightLayerBaker.panoramas`): what a
reflection probe placed there would hold.

A beauty job -- ``{"op": "beauty", "rig": name, payload or null, "camera":
{pos, target, up, vfov}, "output": path.exr, "size": [w, h], "aa": 4}`` -- is
an Arnold render of the scene under a rig (:meth:`LightLayerBaker.beauty`):
the look a mix is meant to carry into the viewer.
"""

import contextlib
import copy
import json
import math
import os
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.light_utils._light_utils import LightUtils
from mayatk.light_utils.light_rig import LightRig


class LightLayerBaker(ptk.LoggingMixin):
    """Bake light layers: each layer's lights alone, white, at one gain.

    Parameters:
        gain: What every layer light's intensity is multiplied by. The WebXR
            viewer draws a bake pi darker than an Arnold beauty render of the
            same rig, so pi makes a weight of 1 mean "as the rig says".
        resolution: The lightmap atlases' size: a layer only has to be good
            enough to predict with (256 bakes in about a minute).
        samples, gi_samples, gi_depth, denoise: The bake's quality
            (``LightmapBaker``).
        baker: ``(**settings) -> LightmapBaker``-like factory (tests).

    Example:
        >>> LightLayerBaker().bake({"key": {"lights": [{"type": "area", ...}]}}, out)
    """

    #: The scene group a layer's lights are built under, for the bake only.
    GROUP = "LIGHT_LAYER"
    #: Each layer folder's description of itself.
    LAYER_FILE = ptk.LightLayerInfo.FILE
    #: What :meth:`launch` runs (``__name__`` is ``__main__`` under ``-m``).
    MODULE = "mayatk.light_utils.light_layer_baker"

    def __init__(
        self,
        gain: float = math.pi,
        resolution: int = 256,
        samples: int = 3,
        gi_samples: int = 2,
        gi_depth: int = 4,
        denoise: bool = True,
        baker: Optional[Callable[..., Any]] = None,
    ):
        super().__init__()
        self.gain = float(gain)
        self.settings = {
            "resolution": int(resolution),
            "samples": int(samples),
            "gi_samples": int(gi_samples),
            "gi_depth": int(gi_depth),
            "denoise": bool(denoise),
        }
        self._baker = baker

    # ------------------------------------------------------------------ payloads
    def layer_payload(
        self,
        payload: Mapping[str, Any],
        base: Optional[Mapping[str, Any]] = None,
        others: Optional[List[str]] = None,
        white: bool = True,
    ) -> Dict[str, Any]:
        """The rig that lights the scene with *payload*'s lights alone, as a layer.

        Its lights are white (no colour, no colour temperature: a mix tints a
        layer) and *gain* times as strong; every other light is off; *base*'s
        overrides (``existing``, ``materials``: say, the panels' emission off
        for a light layer) and then the payload's own apply on top.

        Parameters:
            payload: A ``LightRig`` payload (its spelling or look-match's).
            base: Overrides every layer of a job shares.
            others: The light shapes to switch off (default: every light in
                the scene -- ``LightUtils.all_lights``).
            white: False bakes the payload as it is -- its colours and
                strengths, no gain: a whole rig (look-match's ``to_rig``
                carries the gain already), to hold the sum of its layers to.
        """
        canon = LightRig.normalize(dict(payload))
        for light in canon["lights"] if white else ():
            attrs = light.setdefault("attrs", {})
            attrs["color"] = [1.0, 1.0, 1.0]
            attrs["aiUseColorTemperature"] = 0
            attrs.pop("aiColorTemperature", None)
            attrs["intensity"] = float(attrs.get("intensity", 1.0)) * self.gain
        if others is None:
            others = LightUtils.all_lights() if cmds is not None else []
        existing = {shape: {"intensity": 0.0} for shape in others}
        materials: Dict[str, Dict[str, Any]] = {}
        for section, target in (("existing", existing), ("materials", materials)):
            for source in ((base or {}).get(section) or {}, canon.get(section) or {}):
                for node, attrs in source.items():
                    target.setdefault(node, {}).update(attrs)
        canon["existing"], canon["materials"] = existing, materials
        canon["group"] = self.GROUP
        return canon

    @staticmethod
    def emission_target(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """The emission an emission layer lights the room with, as the
        deliverable reads it: ``{"node", "attr", "scale"}``, or None.

        It is the layer's one override of a shader's emission weight
        (``ShaderAttributeMap.EMISSION_WEIGHT_ATTRS``) where it lands -- the
        material an Arnold bridge follows (``ArnoldBridge.authored_plug``) --
        at the value it was baked at (*scale*). A mix weight scales it
        (``LookMatch.to_rig``): weight 1 is the room at *scale*. None when the
        layer sets no emission weight, or several (one weight cannot scale them).
        """
        from mayatk.mat_utils.arnold_bridge import ArnoldBridge
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        landed, _yielded = LightRig.landed_overrides(LightRig.normalize(dict(payload)))
        found: Dict[str, Tuple[str, str, float]] = {}
        for node, attrs in (landed.get("materials") or {}).items():
            for attr, value in attrs.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if not cmds.objExists(f"{node}.{attr}"):
                    continue
                plug = ArnoldBridge.authored_plug(f"{node}.{attr}")
                owner, _, real = plug.partition(".")
                entry = ShaderAttributeMap.EMISSION_WEIGHT_ATTRS.get(
                    cmds.nodeType(owner)
                )
                if entry and entry[0] == real:
                    found[plug] = (owner, real, float(value))
        if len(found) != 1:
            return None
        ((owner, real, value),) = found.values()
        return {"node": owner, "attr": real, "scale": value}

    # ------------------------------------------------------------------ baking
    @staticmethod
    @contextlib.contextmanager
    def _rig_record_kept():
        """The scene's light-rig record (``ptk.SceneRecords.LIGHT_RIGS``) as found,
        after swaps made only for a bake or a render: none of them is a rig the
        artist applied, and a saved scene must not remember them."""
        from mayatk.node_utils.data_nodes import DataNodes

        before = ptk.SceneRecords.LIGHT_RIGS.load(DataNodes, None)
        try:
            yield
        finally:
            if before is None:
                ptk.SceneRecords.LIGHT_RIGS.clear(DataNodes)
            else:
                ptk.SceneRecords.LIGHT_RIGS.save(DataNodes, before)

    def _make_baker(self, lit: bool = True):
        # An emission layer is lit by emission alone, every light at 0 on
        # purpose: the baker's all-lights-off refusal is for mistakes.
        # A measurement (record=False): a layer bake that recorded itself
        # re-pointed the scene's markers at its staged maps and set the
        # scene's real lightmaps aside as superseded (2026-10-17).
        settings = dict(self.settings, refuse_unlit=lit, record=False)
        if self._baker is not None:
            return self._baker(**settings)
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker(
            adaptive=False,
            beside_textures=False,
            include_environment=True,
            reflection_probe=True,
            **settings,
        )

    def bake(
        self,
        layers: Mapping[str, Mapping[str, Any]],
        output_dir: str,
        objects: Optional[List[str]] = None,
        base: Optional[Mapping[str, Any]] = None,
        white: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Bake each layer alone into ``<output_dir>/<name>/``.

        The scene's own lights are handed back as found after each layer (the
        layer goes in as a ``LightRig`` swap and swaps back out). Nothing is
        saved.

        Parameters:
            layers: ``{name: rig payload}``. A payload with no lights is an
                emission layer (its ``materials`` light the room).
            output_dir: Where each layer's folder goes (replaced if present).
            objects: What to bake -- meshes, transforms, or sets (their
                members) -- default: every object the scene's lightmaps cover
                (``LightmapRecords.baked_objects``). Layers meant to sum with
                others must bake what they baked: the atlases' layout follows it.
            base: Overrides every layer shares (see :meth:`layer_payload`).
            white: False bakes each payload as it is (see :meth:`layer_payload`).

        Returns:
            ``{name: {"folder", "files", "seconds"}}``, or ``{"refused": why}``
            for a layer the baker refused.
        """
        with self._rig_record_kept():
            if objects is None:
                from mayatk.light_utils.lightmap_baker.lightmap_records import (
                    LightmapRecords,
                )

                objects = LightmapRecords.baked_objects()
            expanded: List[str] = []
            for node in objects:  # a set bakes its members
                if cmds.objExists(node) and cmds.nodeType(node) == "objectSet":
                    expanded += cmds.sets(node, query=True) or []
                else:
                    expanded.append(node)
            objects = expanded
            if not objects:
                raise ValueError(
                    "Nothing to bake: no objects, and no lightmaps recorded."
                )
            if Plugins.available("mtoa"):
                Plugins.load("mtoa")
            rig = LightRig(group=self.GROUP)
            others = [
                s
                for s in LightUtils.all_lights()
                if not s.startswith(f"|{self.GROUP}|")
            ]
            results: Dict[str, Dict[str, Any]] = {}
            for name, payload in layers.items():
                canon = self.layer_payload(payload, base, others, white=white)
                folder = os.path.join(output_dir, name)
                started = time.time()
                target = None if canon["lights"] else self.emission_target(payload)
                rig.apply(canon, group=self.GROUP, name=f"layer {name}")
                try:
                    # The layer's own lights (every other light is at 0), as the
                    # lightmap record carries them: a capture lights their highlights.
                    lights = LightUtils.specular_lights()
                    with ptk.TempArtifacts("light_layer", policy="scoped") as tmp:
                        stage = tmp.dir_path()
                        result = self._make_baker(lit=bool(canon["lights"])).bake(
                            list(objects),
                            packing="atlas",
                            suffix="_Lightmap",
                            output_dir=stage,
                            on_progress=lambda *a: True,
                        )
                        if result.refused or not result.maps:
                            results[name] = {"refused": result.refused or "no maps"}
                            self.logger.warning(f"Layer {name!r}: {results[name]}")
                            continue
                        files = sorted(
                            set(result.maps.values()) | {result.probe} - {None}
                        )
                        shutil.rmtree(folder, ignore_errors=True)
                        os.makedirs(folder)
                        for path in files:
                            shutil.copy2(
                                path, os.path.join(folder, os.path.basename(path))
                            )
                finally:
                    rig.swap_back(self.GROUP)
                    rig.remove(self.GROUP)
                seconds = round(time.time() - started, 1)
                info = {
                    "name": name,
                    "kind": "light" if canon["lights"] else "emission",
                    "spec": copy.deepcopy(dict(payload)),
                    "gain": self.gain if white else 1.0,
                    "settings": dict(self.settings),
                    "files": [os.path.basename(p) for p in files],
                    "lights": lights,
                    "seconds": seconds,
                }
                if self._opened is not None:  # which save of the scene it shows
                    info["scene"] = dict(self._opened[1])
                if target:  # what a mix weight scales (LookMatch.to_rig)
                    info["target"] = target
                elif not canon["lights"]:
                    self.logger.warning(
                        f"Layer {name!r}: no single emission weight to scale -- "
                        "a rig built from a mix cannot light it."
                    )
                checked = ptk.LightLayerInfo.validate(info)
                if checked.errors:  # what capture_bake reads: the contract, or nothing
                    raise ValueError(
                        f"Layer {name!r}: layer.json does not fit LightLayerInfo: "
                        + "; ".join(checked.errors)
                    )
                ptk.FileUtils.atomic_write_text(
                    os.path.join(folder, self.LAYER_FILE), json.dumps(info, indent=1)
                )
                results[name] = {
                    "folder": folder,
                    "files": info["files"],
                    "seconds": seconds,
                }
                self.logger.info(f"Layer {name!r} baked in {seconds:.0f}s.")
            return results

    def panoramas(
        self,
        rig: Union[str, Mapping[str, Any]],
        positions: Mapping[str, List[float]],
        output_dir: str,
        width: int = 1024,
    ) -> Dict[str, Optional[str]]:
        """The room under *rig*, seen from each point: equirectangular HDRs.

        The rig goes in as it is (a look, not a layer: its colours and levels,
        the bake gain included) and swaps back out after. Each panorama is what
        a reflection probe at that point holds (``TextureBaker.render_panorama``:
        every light visible to camera rays), so it tells what a probe placed
        there would give the surfaces near it.

        Parameters:
            rig: A library name or a ``LightRig`` payload.
            positions: ``{name: [x, y, z]}`` in scene units; each lands at
                ``<output_dir>/<name>.exr``.
            width: Each panorama's width (its height is half).

        Returns:
            ``{name: path or None}`` (``None``: Arnold wrote nothing, logged).
        """
        with self._rig_record_kept():
            from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

            if Plugins.available("mtoa"):
                Plugins.load("mtoa")
            tool = LightRig()
            applied = tool.apply(rig)
            os.makedirs(output_dir, exist_ok=True)
            out: Dict[str, Optional[str]] = {}
            try:
                renderer = LightmapBaker(adaptive=False, **self.settings).baker
                for name, position in positions.items():
                    out[name] = renderer.render_panorama(
                        [float(c) for c in position],
                        os.path.join(output_dir, f"{name}.exr"),
                        width=int(width),
                    )
            finally:
                tool.swap_back(applied["group"])
            return out

    #: Render options a beauty sets, handed back after it.
    _BEAUTY_OPTIONS = {
        "AASamples": 4,
        "GIDiffuseSamples": 2,
        "GIDiffuseDepth": 3,
        "GISpecularDepth": 2,
        "GITransmissionDepth": 4,
        "abortOnLicenseFail": 0,
        "ignoreImagers": 1,
    }

    @staticmethod
    def focal_length(vfov: float, aperture_mm: float) -> float:
        """The focal length (mm) giving a vertical field of view of *vfov* degrees
        across a film *aperture_mm* tall."""
        return (aperture_mm / 2.0) / math.tan(math.radians(vfov) / 2.0)

    def beauty(
        self,
        rig: Optional[Union[str, Mapping[str, Any]]],
        camera: Mapping[str, Any],
        path: str,
        size: Tuple[int, int] = (819, 480),
        aa: int = 4,
    ) -> str:
        """An Arnold render of the scene under *rig*: what a mix should look like.

        Game materials render through their Arnold bridge
        (``TextureBaker.arnold_translation_guard``), as a bake renders them.

        Parameters:
            rig: A library name or ``LightRig`` payload, swapped in for the
                render and back out after; ``None`` renders the scene as it is.
            camera: ``{pos, target, up, vfov}`` in scene units (vfov vertical,
                degrees) -- a temporary camera, deleted after.
            path: The EXR to write.
            size: ``(width, height)``.
            aa: Camera (AA) samples.

        Returns:
            *path*.
        """
        with self._rig_record_kept():
            Plugins.load("mtoa")
            import mtoa.core

            mtoa.core.createOptions()  # a scene never rendered has no render options
            tool = LightRig()
            applied = tool.apply(rig) if rig else None
            width, height = int(size[0]), int(size[1])
            transform = None
            options = "defaultArnoldRenderOptions"
            held = []  # (plug, value, setAttr flags): every setting put back after

            def put(plug, value, **kwargs):
                held.append((plug, cmds.getAttr(plug), kwargs))
                cmds.setAttr(plug, value, **kwargs)

            try:
                transform, shape = cmds.camera(
                    position=list(camera["pos"]),
                    worldCenterOfInterest=list(camera["target"]),
                    worldUp=list(camera.get("up") or (0, 1, 0)),
                )
                aperture = 24.0  # mm, vertical: the focal length carries the view
                cmds.setAttr(f"{shape}.verticalFilmAperture", aperture / 25.4)
                cmds.setAttr(
                    f"{shape}.horizontalFilmAperture", aperture * width / height / 25.4
                )
                cmds.setAttr(f"{shape}.filmFit", 2)  # vertical
                cmds.setAttr(
                    f"{shape}.focalLength",
                    self.focal_length(float(camera["vfov"]), aperture),
                )
                cmds.setAttr(f"{shape}.nearClipPlane", 0.5)
                wanted = dict(self._BEAUTY_OPTIONS, AASamples=int(aa))
                for attr, value in wanted.items():
                    if cmds.attributeQuery(attr, node=options, exists=True):
                        put(f"{options}.{attr}", value)
                put("defaultArnoldDriver.ai_translator", "exr", type="string")
                put("defaultResolution.width", width)
                put("defaultResolution.height", height)
                put("defaultResolution.deviceAspectRatio", width / float(height))
                put("defaultResolution.pixelAspect", 1.0)
                put("defaultRenderGlobals.animation", 0)
                prefix = os.path.splitext(path)[0].replace("\\", "/")
                put("defaultRenderGlobals.imageFilePrefix", prefix, type="string")
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                from mayatk.light_utils.lightmap_baker.lightmap_baker import (
                    LightmapBaker,
                )

                # Game (ShaderFX) materials render ERROR MAGENTA in Arnold: bridged
                # for the render, as a bake bridges them (measured: the whole
                # soldering assembly magenta without it).
                with LightmapBaker(**self.settings).baker.arnold_translation_guard():
                    cmds.arnoldRender(
                        width=width, height=height, camera=shape, batch=True
                    )
            finally:
                for plug, value, kwargs in reversed(held):
                    if value is None and kwargs.get("type") == "string":
                        value = ""  # an empty string attribute reads back as None
                    cmds.setAttr(plug, value, **kwargs)
                if transform and cmds.objExists(transform):
                    cmds.delete(transform)
                if applied:
                    tool.swap_back(applied["group"])
            return path

    # ------------------------------------------------------------------ the worker
    #: The worker's scene: ``(path, ptk.LightLayerScene record)`` as it was
    #: opened, or None outside a worker (a bake in a user's scene records none:
    #: what is in memory need not be any save of it).
    _opened: Optional[Tuple[str, Dict[str, Any]]] = None

    @classmethod
    def _open_scene(cls, scene: str) -> None:
        """Open *scene* for baking: Arnold loaded, its project set, no prompt and
        no script node run (a GUI Maya blocks on their security prompt)."""
        if Plugins.available("mtoa"):
            Plugins.load("mtoa")
        workspace = ptk.Workspace.for_scene(scene)
        if workspace:
            cmds.workspace(workspace.root, openWorkspace=True)
        cmds.file(scene, open=True, force=True, prompt=False, executeScriptNodes=False)
        cls._opened = (scene, ptk.LightLayerScene.of(scene))

    @classmethod
    def _reopen_if_saved(cls) -> bool:
        """Open the worker's scene again when its file was saved since it was
        opened; whether it did. A bake never saves, so until then the scene in
        memory is the save it opened."""
        if cls._opened is None:
            return False
        scene, record = cls._opened
        if not os.path.isfile(scene) or ptk.LightLayerScene.matches(record, scene):
            return False
        print(
            f"{os.path.basename(scene)} was saved since it was opened: opening it again.",
            flush=True,
        )
        cls._open_scene(scene)
        return True

    @classmethod
    def _handle(cls, job: Dict[str, Any]) -> Dict[str, Any]:
        """One queued job (see the module docstring)."""
        cls._reopen_if_saved()
        baker = cls(**(job.get("settings") or {}))
        if job.get("op") == "beauty":
            return {
                "beauty": baker.beauty(
                    job.get("rig"),
                    job["camera"],
                    job["output"],
                    size=tuple(job.get("size") or (819, 480)),
                    aa=job.get("aa", 4),
                ),
                # Arnold writes the scene's rendering space (ACEScg by default),
                # not the Rec.709 a bake is converted to.
                "space": cmds.colorManagementPrefs(query=True, renderingSpaceName=True),
            }
        if job.get("op") == "panorama":
            return {
                "panoramas": baker.panoramas(
                    job["rig"],
                    job["positions"],
                    job["output"],
                    width=job.get("width", 1024),
                ),
                "unit": cmds.currentUnit(query=True, linear=True),
            }
        return {
            "layers": baker.bake(
                job["layers"],
                job["output"],
                objects=job.get("objects"),
                base=job.get("base"),
                white=job.get("white", True),
            )
        }

    @classmethod
    def serve(cls, queue: str, scene: str) -> int:
        """Be the worker in ``mayapy``: open *scene* once, then run every job *queue* gets.

        Never saves the scene. Returns how many jobs ran. Arnold in ``mayapy``
        (and ``mayabatch``) renders with a watermark unless the machine has an
        Arnold batch license (measured: Maya 2025, a named-user Maya license
        alone); :meth:`serve_gui` is the licensed host.
        """
        import maya.standalone

        maya.standalone.initialize(name="python")
        try:
            return ptk.JobQueue(queue).serve(
                cls._handle, setup=lambda: cls._open_scene(scene)
            )
        finally:
            maya.standalone.uninitialize()

    #: The GUI host's poll timer, held so Qt keeps it.
    _gui_timer = None

    @classmethod
    def serve_gui(cls, queue: str, scene: str, poll_ms: int = 1000) -> None:
        """Be the worker in a fresh GUI Maya (where Arnold renders licensed).

        Runs at startup (:meth:`launch` with ``host="gui"``): opens *scene*,
        then takes one job at a time from a Qt timer, so the window answers
        between jobs. The window starts minimized: a worker, not a workspace,
        so it stays off the user's desktop. A stop request ends the worker
        and the Maya with it (never saving).
        """
        from qtpy import QtCore
        from mayatk.ui_utils._ui_utils import UiUtils

        queue_ = ptk.JobQueue(queue)

        def tick() -> None:
            if queue_.stopping():
                cls._gui_timer.stop()
                queue_.end()
                cmds.quit(force=True)
                os._exit(0)  # quit is ignored with userSetup skipped (measured)
            queue_.step(cls._handle)

        def start() -> None:
            window = UiUtils.get_main_window()
            if window is not None:
                window.showMinimized()
            queue_.begin(setup=lambda: cls._open_scene(scene))
            cls._gui_timer = QtCore.QTimer()
            cls._gui_timer.setInterval(int(poll_ms))
            cls._gui_timer.timeout.connect(tick)
            cls._gui_timer.start()

        cmds.evalDeferred(start)

    @classmethod
    def launch(
        cls,
        queue: str,
        scene: str,
        host: str = "gui",
        executable: Optional[str] = None,
    ) -> None:
        """Start a fresh worker on *scene* for *queue*; it outlives this call.

        Parameters:
            host: ``"gui"`` -- a new Maya window (Arnold licensed; the window
                answers between jobs) -- or ``"standalone"``, a ``mayapy``
                (no window; watermarked renders without a batch license).
            executable: ``maya`` / ``mayapy`` to run (default: found).

        Its output goes to ``<queue>/worker.log`` and
        ``ptk.JobQueue(queue).worker()`` shows its heartbeat; a caller that
        leases the queue (``ptk.JobQueue.lease``) has it end once the lease
        lapses with nothing queued. The user's ``userSetup.py`` is skipped: a
        worker opens no command port and loads nothing the scene does not.

        Raises:
            RuntimeError: a worker is already serving *queue*, or Maya did not start.
            FileNotFoundError: no Maya was found.
            ValueError: an unknown *host*.
        """
        if host not in ("gui", "standalone"):
            raise ValueError(f"Unknown host {host!r}: 'gui' or 'standalone'.")
        if ptk.JobQueue(queue).alive():
            raise RuntimeError(f"A worker is already serving {queue}.")
        app = "maya" if host == "gui" else "mayapy"
        executable = executable or ptk.AppLauncher.find_app(app)
        if not executable:
            raise FileNotFoundError(f"{app} not found; pass executable=.")
        os.makedirs(queue, exist_ok=True)
        roots = []
        for module in ("mayatk", "pythontk", "uitk"):
            package = sys.modules.get(module)
            if package is not None and getattr(package, "__file__", None):
                roots.append(os.path.dirname(os.path.dirname(package.__file__)))
        env = ptk.AppLauncher.process_environ()  # the host's, not a stale snapshot
        env["PYTHONPATH"] = os.pathsep.join(
            roots + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        )
        env["MAYA_SKIP_USERSETUP_PY"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        if host == "gui":
            # A MEL file the new Maya runs at startup: no quoting through argv.
            startup = os.path.join(queue, "worker_startup.mel")
            code = (
                f"from {cls.MODULE} import LightLayerBaker; "
                f"LightLayerBaker.serve_gui({queue.replace(chr(92), '/')!r}, "
                f"{scene.replace(chr(92), '/')!r})"
            )
            with open(startup, "w", encoding="utf-8") as f:
                f.write('python("' + code.replace('"', '\\"') + '");\n')
            args = ["-script", startup]
        else:
            args = ["-m", cls.MODULE, "serve", "--queue", queue, "--scene", scene]
        process = ptk.AppLauncher.launch(
            executable,
            args,
            env=env,
            detached=True,
            log=os.path.join(queue, "worker.log"),
        )
        if process is None:
            raise RuntimeError(f"{executable} did not start (see the log).")

    # ------------------------------------------------------------------ command line
    @classmethod
    def cli(cls, argv: Optional[List[str]] = None) -> int:
        """The command line (``python -m mayatk.light_utils.light_layer_baker``)."""
        command = ptk.CLI.Command
        queue = ("--queue", {"required": True})
        scene = ("--scene", {"required": True})
        return ptk.CLI.dispatch(
            "Light layers: bake each light of a rig alone, in a Maya worker.",
            {
                "launch": command(
                    "start a fresh Maya worker on a scene",
                    cls._cli_launch,
                    [
                        queue,
                        scene,
                        (
                            "--host",
                            {"default": "gui", "choices": ("gui", "standalone")},
                        ),
                    ],
                ),
                "serve": command(
                    "be the mayapy worker (what launch --host standalone runs)",
                    lambda args: cls.serve(args.queue, args.scene),
                    [queue, scene],
                ),
                "submit": command(
                    "queue a job (JSON file)",
                    cls._cli_submit,
                    [queue, ("job", {}), ("--wait", {"action": "store_true"})],
                ),
                "status": command(
                    "the worker's heartbeat",
                    lambda args: print(
                        json.dumps(ptk.JobQueue(args.queue).worker(), indent=1)
                    ),
                    [queue],
                ),
                "stop": command(
                    "end the worker after its current job",
                    lambda args: ptk.JobQueue(args.queue).stop(),
                    [queue],
                ),
            },
            argv,
        )

    @classmethod
    def _cli_launch(cls, args) -> None:
        cls.launch(args.queue, args.scene, host=args.host)
        print(f"worker starting on {args.scene}; log: {args.queue}/worker.log")

    @staticmethod
    def _cli_submit(args) -> None:
        queue = ptk.JobQueue(args.queue)
        with open(args.job, encoding="utf-8") as f:
            job_id = queue.submit(json.load(f))
        print(job_id)
        if args.wait:
            print(json.dumps(queue.wait(job_id), indent=1))


if __name__ == "__main__":
    sys.exit(LightLayerBaker.cli())
