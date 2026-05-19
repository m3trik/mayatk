# !/usr/bin/python
# coding=utf-8
"""Bake Maya scene lighting into per-object texture files.

Two backends, picked automatically by :meth:`BakeLighting.bake`:

* **Arnold** (when the ``mtoa`` plugin is loaded) -- uses
  :func:`arnoldRenderToTexture`. Highest quality available natively in
  Maya 2025; respects all lights / aiSkyDomeLight / GI bounces.
* **convertSolidTx** (always available) -- the built-in MEL command that
  samples the assigned material with current scene lighting and writes
  a PNG. Lower quality than Arnold but zero external dependencies.

Standalone Maya utility: produces texture files on disk. Consumers (the
tentacle lighting UI, custom scripts) decide what to do with the output.
:meth:`BakeLighting.assign_to_diffuse` is provided as an optional,
reversible helper for previewing the result in the viewport.
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

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


class BakeLighting(ptk.LoggingMixin):
    """Bakes scene lighting per object to PNG textures.

    Usage::

        baker = BakeLighting()
        out = baker.bake(cmds.ls(selection=True), output_dir="C:/tmp/bakes")
        # out: {object_long_name: baked_png_path}

    The caller can then either:
      * import the PNGs externally (e.g. as anchors/layers in DCC tools), or
      * call :meth:`assign_to_diffuse` to wire each baked PNG into the
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
        # Per-instance knobs -- overriding ``BakeLighting.resolution`` at the
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
        return "arnoldRenderToTexture" in (cmds.listCommands() or [])

    # ------------------------------------------------------------------
    # Top-level bake API
    # ------------------------------------------------------------------

    def bake(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        prefix: str = "bake_",
        backend: str = "auto",
    ) -> Dict[str, str]:
        """Bake lighting per object to PNG files.

        Parameters:
            objects: Mesh transforms to bake. Defaults to current selection.
            output_dir: Where the PNG files go. Created if missing.
                Defaults to ``<scene_dir>/baked_lighting``.
            prefix: Filename prefix. Final name is ``{prefix}{leaf}.{fmt}``.
            backend: ``"auto"`` (default), ``"arnold"``, or ``"convertSolidTx"``.

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
        for obj in objects:
            long_name = cmds.ls(obj, long=True)
            if not long_name:
                self.logger.warning("Skipping unknown object: %s", obj)
                continue
            long_name = long_name[0]
            leaf = long_name.rsplit("|", 1)[-1].replace(":", "_")
            out_path = os.path.join(
                output_dir, f"{prefix}{leaf}.{self.file_format}"
            )
            try:
                if backend == "arnold":
                    self._bake_with_arnold(long_name, output_dir, prefix)
                    # Arnold writes <folder>/<obj>.<fmt>; map to our convention.
                    arnold_out = os.path.join(
                        output_dir, f"{leaf}.{self.file_format}"
                    )
                    if os.path.exists(arnold_out) and arnold_out != out_path:
                        os.replace(arnold_out, out_path)
                else:
                    self._bake_with_convert_solid_tx(long_name, out_path)
            except Exception as e:
                self.logger.error("Bake failed for %s: %s", long_name, e)
                continue

            if os.path.exists(out_path):
                results[long_name] = out_path
                self.logger.info("Baked %s -> %s", leaf, out_path)
            else:
                self.logger.warning(
                    "Bake reported success for %s but output missing: %s",
                    leaf, out_path,
                )

        return results

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

    def _bake_with_arnold(self, obj: str, output_dir: str, prefix: str) -> None:
        """Bake one mesh via Arnold's ``arnoldRenderToTexture``.

        Arnold writes ``<output_dir>/<leaf>.<format>`` for the selection;
        the caller renames it to the prefixed convention afterward.
        """
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
    paths = BakeLighting().bake()
    for obj, p in paths.items():
        print(f"  {obj} -> {p}")
