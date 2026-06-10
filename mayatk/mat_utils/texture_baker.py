# !/usr/bin/python
# coding=utf-8
"""Bake an object's shaded surface (material under scene lighting) to a texture.

The low-level, generic **bake primitive** (mat_utils): it only renders each
object's shaded appearance to a per-object texture (with optional UV-set
targeting), independent of any higher-level pipeline. It captures whatever the
render shows -- material x lighting / GI -- not arbitrary AOVs (it does not bake
normal / AO / curvature maps). The lighting *workflow* on top of it (lightmap
UV2 generation, dilation, engine export prep, presets) is
:class:`mayatk.LightmapBaker`, which *composes* this class; use this directly
for one-off / preview bakes.

Two backends, picked automatically by :meth:`TextureBaker.bake`:

* **Arnold** (when the ``mtoa`` plugin is loaded) -- uses
  :func:`arnoldRenderToTexture`. Highest quality available natively in
  Maya 2025; respects all lights / aiSkyDomeLight / GI bounces.
* **convertSolidTx** (always available) -- the built-in MEL command that
  samples the assigned material with current scene lighting and writes
  a PNG. Lower quality than Arnold but zero external dependencies.

Standalone Maya utility: produces texture files on disk. Consumers (the
tentacle lighting UI, custom scripts) decide what to do with the output.
:meth:`TextureBaker.assign_to_diffuse` is provided as an optional,
reversible helper for previewing the result in the viewport.
"""
import glob
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)

import pythontk as ptk

from mayatk.mat_utils._mat_utils import MatUtils


# Heuristic: convertSolidTx is the lowest-common-denominator backend, but
# its output is noisy at default settings. Bumping samples here trades
# bake time for quality without changing the per-call signature.
_CONVERT_SOLID_TX_DEFAULTS: Dict[str, Any] = {
    "antiAlias": True,
    "samplePlane": 0,        # sample on the surface
    "shadows": True,
    "alpha": False,          # keep RGB; alpha handled separately if needed
    "doubleSided": False,
    "componentRange": False,
    "fillTextureSeams": True,
    "fileFormat": "png",
}


class TextureBaker(ptk.LoggingMixin):
    """Bake scene lighting per object to a texture file (PNG, EXR, ...).

    Usage::

        baker = TextureBaker(file_format="exr")
        out = baker.bake(cmds.ls(selection=True), output_dir="C:/tmp/bakes")
        # out: {object_long_name: baked_file_path}

    The caller can then either:
      * import the textures externally (e.g. as anchors/layers in DCC tools), or
      * call :meth:`assign_to_diffuse` to wire each baked texture into the
        object's existing material's color slot for viewport preview.

    Both Arnold and ``convertSolidTx`` backends require:
      * The mesh has UVs (no overlapping checks are performed).
      * At least one material is assigned to the mesh.
      * The scene has lights (otherwise the bake is the material's
        unlit base color).
    """

    def __init__(
        self,
        resolution: int = 2048,
        samples: int = 5,
        file_format: str = "png",
    ):
        super().__init__()
        # Per-instance knobs -- overriding ``TextureBaker.resolution`` at the
        # class scope would mutate global state, so they live on the instance.
        self.resolution = resolution
        self.samples = samples
        self.file_format = file_format
        # State for assign_to_diffuse / restore_diffuse_connections.
        # Each entry: (color_attr, prev_source_plug, prev_static_value, baked_path).
        # prev_source_plug is "" if the slot was driven by a static setAttr.
        # prev_static_value is None when an incoming connection was in place.
        self._restore_state: List[Tuple[str, str, Optional[tuple], str]] = []

    # ------------------------------------------------------------------
    # Backend detection
    # ------------------------------------------------------------------

    @staticmethod
    def arnold_available() -> bool:
        """True if the ``mtoa`` plugin is loaded AND its bake cmd is registered."""
        if cmds is None:
            return False
        try:
            if not cmds.pluginInfo("mtoa", query=True, loaded=True):
                return False
        except RuntimeError:
            return False
        # mtoa registers the bake command on load. Maya 2025 cmds has no
        # listCommands(), so probe the command attribute directly.
        return hasattr(cmds, "arnoldRenderToTexture")

    # ------------------------------------------------------------------
    # Top-level bake API
    # ------------------------------------------------------------------

    def bake(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "bake_",
        suffix: str = "",
        backend: str = "auto",
        uv_set: Optional[Union[str, Dict[str, str]]] = None,
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None,
    ) -> Dict[str, str]:
        """Bake lighting per object to PNG files.

        Parameters:
            objects: Mesh transforms to bake. Defaults to current selection.
            output_dir: Where the PNG files go. Created if missing.
                Defaults to ``<scene_dir>/baked_lighting``.
            prefix: Filename prefix wrapped around the output stem.
            suffix: Filename suffix. Final name is ``{prefix}{stem}{suffix}.{fmt}``
                (applied idempotently via ``StrUtils.apply_affix``), so callers
                can follow the ``<base>_Lightmap`` texture-set convention.
            stem: Output base name per object — the object leaf name by default.
                Pass a ``callable(long_name) -> str`` or a ``{long_name: stem}``
                dict to name the file after something else (e.g. the material's
                texture-set base, so a long node name doesn't become a long
                texture name). A falsy / missing / erroring resolution falls
                back to the leaf. Names that collide (objects sharing a material,
                or duplicate leaf names) are disambiguated with a numeric suffix
                so no bake silently overwrites another.
            backend: ``"auto"`` (default), ``"arnold"``, or ``"convertSolidTx"``.
            uv_set: Bake into this UV set (e.g. the lightmap channel). Both
                backends sample the *current* UV set, so it is made current
                per object for the bake and restored afterward. Pass a ``str``
                to use one set for every object, or a ``{long_object_name:
                set_name}`` dict to target a different set per object (a real
                scene's lightmap set is not named uniformly -- some reuse a
                pre-existing ``UV2`` etc.). ``None`` bakes the current set
                as-is. A shape lacking its set is baked on its current set
                (logged).
            on_progress: Optional ``(done, total, name) -> bool`` callback
                invoked as each object's bake starts (``done`` = objects
                finished so far, 0..N-1), plus one final ``(total, total,
                last_name)`` call on completion so a determinate bar reaches
                100%. Return ``False`` to cancel the remaining bakes. Lets a UI
                drive a progress bar without this primitive knowing about Qt;
                exceptions from it never break the bake.

        Returns:
            ``{long_object_name: absolute_file_path}`` for every successful bake.
            Failures are logged and excluded from the dict.
        """
        if cmds is None:
            self.logger.error("maya.cmds not available; bake aborted.")
            return {}

        if objects is None:
            objects = cmds.ls(selection=True, long=True, transforms=True) or []
        if not objects:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        if output_dir is None:
            scene = cmds.file(query=True, sceneName=True)
            base = os.path.dirname(scene) if scene else cmds.workspace(
                query=True, rootDirectory=True
            )
            output_dir = os.path.join(base, "baked_lighting")
        os.makedirs(output_dir, exist_ok=True)

        backend = self._resolve_backend(backend)
        self.logger.info(
            "Baking %d object(s) -> %s (backend=%s, %dx%d)",
            len(objects), output_dir, backend, self.resolution, self.resolution,
        )

        results: Dict[str, str] = {}
        total = len(objects)
        used: set = set()
        last_leaf = ""
        cancelled = False
        for i, obj in enumerate(objects):
            long_name = cmds.ls(obj, long=True)
            if not long_name:
                self.logger.warning("Skipping unknown object: %s", obj)
                continue
            long_name = long_name[0]
            leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
            last_leaf = leaf
            if not self._tick(on_progress, i, total, leaf):
                self.logger.info("Bake cancelled by caller at %d/%d.", i, total)
                cancelled = True
                break
            name = ptk.StrUtils.apply_affix(
                self._resolve_stem(stem, long_name, leaf), prefix, suffix
            )
            out_path = self._unique_path(output_dir, name, used)
            target_set = uv_set.get(long_name) if isinstance(uv_set, dict) else uv_set
            prev_uv: Dict[str, str] = {}
            try:
                if target_set:
                    prev_uv = self._set_current_uv_set(long_name, target_set)
                if backend == "arnold":
                    # Arnold names the file after the mesh shape, so the actual
                    # written path is detected by _bake_with_arnold (dir-diff)
                    # rather than assumed; map it to our prefixed convention.
                    arnold_out = self._bake_with_arnold(long_name, output_dir)
                    if arnold_out and os.path.abspath(arnold_out) != os.path.abspath(
                        out_path
                    ):
                        os.replace(arnold_out, out_path)
                else:
                    self._bake_with_convert_solid_tx(long_name, out_path)
            except Exception as e:
                self.logger.error("Bake failed for %s: %s", long_name, e)
                continue
            finally:
                self._restore_uv_sets(prev_uv)

            if os.path.exists(out_path):
                results[long_name] = out_path
                self.logger.info("Baked %s -> %s", leaf, out_path)
            else:
                self.logger.warning(
                    "Bake reported success for %s but output missing: %s",
                    leaf, out_path,
                )

        # Final completion tick so a determinate progress bar reaches 100%
        # (the per-object ticks above report the count STARTED, i.e. 0..N-1).
        if not cancelled and total:
            self._tick(on_progress, total, total, last_leaf)

        return results

    def _tick(
        self,
        on_progress: Optional[Callable[[int, int, str], bool]],
        done: int,
        total: int,
        name: str,
    ) -> bool:
        """Invoke the progress callback (if any); never let it break the bake.

        Returns ``True`` to continue, ``False`` only when the callback explicitly
        returns ``False`` (cancel). A missing callback or one that raises is
        treated as "continue" -- the bake is never blocked by progress reporting.
        """
        if on_progress is None:
            return True
        try:
            return on_progress(done, total, name) is not False
        except Exception:
            self.logger.debug("on_progress raised; ignoring.", exc_info=True)
            return True

    def _resolve_stem(
        self,
        stem: Optional[Union[Callable[[str], str], Dict[str, str]]],
        long_name: str,
        leaf: str,
    ) -> str:
        """Output base name for *long_name* — *leaf* unless *stem* resolves one."""
        if stem is None:
            return leaf
        try:
            resolved = stem.get(long_name) if isinstance(stem, dict) else stem(long_name)
        except Exception:
            self.logger.debug(
                "stem resolver raised for %s; using leaf.", long_name, exc_info=True
            )
            return leaf
        return resolved or leaf

    def _unique_path(self, output_dir: str, name: str, used: set) -> str:
        """Collision-free output path for *name*, tracking *used* across the bake.

        Objects that share a material (texture-set stem) or have duplicate leaf
        names would otherwise resolve to the same file and overwrite each other;
        the second gets ``{name}_1``, the third ``{name}_2``, and so on.
        """
        candidate = os.path.join(output_dir, f"{name}.{self.file_format}")
        k = 1
        while candidate in used:
            candidate = os.path.join(output_dir, f"{name}_{k}.{self.file_format}")
            k += 1
        used.add(candidate)
        return candidate

    def _resolve_backend(self, requested: str) -> str:
        if requested == "auto":
            return "arnold" if self.arnold_available() else "convertSolidTx"
        if requested == "arnold":
            if not self.arnold_available():
                self.logger.warning(
                    "Arnold backend requested but mtoa not loaded; "
                    "falling back to convertSolidTx."
                )
                return "convertSolidTx"
            return "arnold"
        if requested == "convertSolidTx":
            return "convertSolidTx"
        raise ValueError(
            f"Unknown backend: {requested!r}. "
            "Expected 'auto', 'arnold', or 'convertSolidTx'."
        )

    # ------------------------------------------------------------------
    # UV-set targeting (both backends sample the current set)
    # ------------------------------------------------------------------

    def _set_current_uv_set(self, obj: str, uv_set: str) -> Dict[str, str]:
        """Make *uv_set* current on every shape of *obj* that has it.

        Returns ``{shape: previous_current_set}`` for restore. Warns (and
        returns ``{}``) when no shape carries *uv_set* -- the bake then falls
        back to whatever set is already current.
        """
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or []
        prev: Dict[str, str] = {}
        for shape in shapes:
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if uv_set not in all_sets:
                continue
            cur = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
            if cur:
                prev[shape] = cur
            if cur != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        if not prev:
            self.logger.warning(
                "UV set %r not found on %s; baking the current set instead.",
                uv_set, obj,
            )
        return prev

    @staticmethod
    def _restore_uv_sets(prev: Dict[str, str]) -> None:
        """Restore current UV sets captured by :meth:`_set_current_uv_set`."""
        for shape, cur in prev.items():
            try:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=cur)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _bake_with_convert_solid_tx(self, obj: str, out_path: str) -> None:
        """Bake one mesh via ``convertSolidTx``.

        ``convertSolidTx`` requires a *shading group* (or material) for its
        first arg. We pick the first SG assigned to *obj*.
        """
        sg = self._first_shading_group(obj)
        if sg is None:
            raise RuntimeError(f"No shading group assigned to {obj!r}.")

        kwargs = dict(_CONVERT_SOLID_TX_DEFAULTS)
        kwargs.update({
            "resolutionX": self.resolution,
            "resolutionY": self.resolution,
            "fileImageName": out_path,
            "fileFormat": self.file_format,
        })
        # The cmd signature is convertSolidTx(material, geom, ...).
        cmds.convertSolidTx(sg, obj, **kwargs)

    def _bake_with_arnold(self, obj: str, output_dir: str) -> Optional[str]:
        """Bake one mesh via Arnold's ``arnoldRenderToTexture``.

        Arnold names the output after the mesh *shape* (e.g. ``pCubeShape``),
        not the transform, so the written file is found by diffing the output
        directory rather than assuming a name. Returns the written path (the
        caller maps it to the prefixed convention), or None if none appeared.
        """
        pattern = os.path.join(output_dir, f"*.{self.file_format}")
        before = set(glob.glob(pattern))
        prev = cmds.ls(selection=True, long=True) or []
        cmds.select(obj, replace=True)
        try:
            cmds.arnoldRenderToTexture(
                folder=output_dir,
                resolution=self.resolution,
                aa_samples=self.samples,
                format=self.file_format,
                all_uvs=False,
            )
        finally:
            if prev:
                cmds.select(prev, replace=True)
            else:
                cmds.select(clear=True)
        new = sorted(set(glob.glob(pattern)) - before)
        return new[-1] if new else None

    @staticmethod
    def _first_shading_group(obj: str) -> Optional[str]:
        """Return the first non-default SG connected to any of *obj*'s shapes.

        Falls back to ``initialShadingGroup`` only if no shape on the
        transform has anything else attached -- prevents an early-return
        on a shape that happens to only carry the default SG when a later
        shape has a real one.
        """
        shapes = cmds.listRelatives(
            obj, shapes=True, noIntermediate=True, fullPath=True
        ) or []
        all_sgs: List[str] = []
        for shape in shapes:
            all_sgs.extend(cmds.listConnections(shape, type="shadingEngine") or [])
        for sg in all_sgs:
            if sg != "initialShadingGroup":
                return sg
        return all_sgs[0] if all_sgs else None

    # ------------------------------------------------------------------
    # Optional: hook baked textures into the material for viewport preview
    # ------------------------------------------------------------------

    def assign_to_diffuse(self, mapping: Dict[str, str]) -> None:
        """Wire each baked PNG into the object's material color slot.

        Mutates the scene -- :meth:`restore_diffuse_connections` undoes it.

            paths = baker.bake(selection)
            baker.assign_to_diffuse(paths)
            # ... preview / export / etc ...
            baker.restore_diffuse_connections()    # leave the scene as found

        Parameters:
            mapping: ``{object_long_name: baked_png_path}`` from :meth:`bake`.
        """
        for obj, path in mapping.items():
            sg = self._first_shading_group(obj)
            if not sg:
                self.logger.warning("No SG for %s; skipping assign.", obj)
                continue
            mat = self._material_from_sg(sg)
            if not mat:
                self.logger.warning("No material on %s; skipping.", sg)
                continue
            color_attr = self._color_attr_for_material(mat)
            if not color_attr:
                self.logger.warning(
                    "Don't know how to set diffuse on %s (type=%s); skipping.",
                    mat, cmds.nodeType(mat),
                )
                continue

            # Remember whatever's currently driving the color so we can
            # restore it later. Two shapes:
            #  - incoming connection -> capture the source plug
            #  - static value        -> capture the tuple of raw floats
            incoming = cmds.listConnections(
                color_attr, plugs=True, source=True, destination=False
            ) or []
            static_value: Optional[tuple] = None
            if not incoming:
                raw = cmds.getAttr(color_attr)
                # Color attrs come back as [(r, g, b)] from cmds.
                static_value = raw[0] if isinstance(raw, list) else raw
            self._restore_state.append((
                color_attr,
                incoming[0] if incoming else "",
                static_value,
                path,
            ))
            if incoming:
                cmds.disconnectAttr(incoming[0], color_attr)

            file_node, _placement = MatUtils.create_file_node(
                path, name=f"baked_{cmds.nodeType(mat)}_{time.time_ns()}"
            )
            cmds.connectAttr(f"{file_node}.outColor", color_attr, force=True)

    def restore_diffuse_connections(self) -> None:
        """Undo :meth:`assign_to_diffuse` -- reconnects previous drivers."""
        while self._restore_state:
            color_attr, prev_source, prev_static, baked_path = self._restore_state.pop()
            try:
                current = cmds.listConnections(
                    color_attr, plugs=True, source=True, destination=False
                ) or []
                # Disconnect whatever assign_to_diffuse hooked up.
                for src in current:
                    cmds.disconnectAttr(src, color_attr)
                # Reconnect the original driver, or restore the static value.
                if prev_source and cmds.objExists(prev_source.split(".")[0]):
                    cmds.connectAttr(prev_source, color_attr, force=True)
                elif prev_static is not None:
                    cmds.setAttr(color_attr, *prev_static, type="double3")
            except RuntimeError as e:
                self.logger.warning(
                    "Could not restore %s: %s", color_attr, e
                )

    @staticmethod
    def _material_from_sg(sg: str) -> Optional[str]:
        mats = cmds.listConnections(f"{sg}.surfaceShader") or []
        return mats[0] if mats else None

    @staticmethod
    def _color_attr_for_material(material: str) -> Optional[str]:
        """Return the plug to wire color into for known material types."""
        node_type = cmds.nodeType(material)
        # Common Maya/Arnold/Stingray base-color slots.
        candidates_by_type = {
            "lambert": "color",
            "blinn": "color",
            "phong": "color",
            "phongE": "color",
            "anisotropic": "color",
            "aiStandardSurface": "baseColor",
            "standardSurface": "baseColor",
            "StingrayPBS": "TEX_color_map",
            "openPBRSurface": "baseColor",
        }
        attr = candidates_by_type.get(node_type)
        if attr and cmds.attributeQuery(attr, node=material, exists=True):
            return f"{material}.{attr}"
        return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick manual smoke test: bake selection into the current workspace.
    paths = TextureBaker().bake()
    for obj, p in paths.items():
        print(f"  {obj} -> {p}")
