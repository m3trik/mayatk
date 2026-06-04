# !/usr/bin/python
# coding=utf-8
"""Maya-side glue for the Marmoset Toolbag engine.

:class:`MarmosetBridge` is the Maya half of the split: it exports the
current selection to FBX, builds a :class:`MatManifest` material sidecar
and a Maya-DAG-classified high/low bake-pairs sidecar, then delegates the
Toolbag-side work (template render, launch, roundtrip) to its
DCC-agnostic base, :class:`._marmoset_engine.MarmosetEngine`.

Everything Marmoset-specific but DCC-agnostic (Toolbag discovery/launch,
log handling, template rendering, the in-Toolbag helpers, the RPC client)
is bundled alongside this module in the ``marmoset_bridge`` subpackage:
the Toolbag SDK glue is not a generic pythontk utility, so it lives with
its consumer (mirroring ``substance_bridge``). This module owns only what
genuinely needs Maya. The standalone extapps ``marmoset_workflow`` panel
keeps its own copy of the same engine, since it cannot import mayatk.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Sequence

try:
    from maya import cmds
except ImportError:
    pass

# DCC-agnostic engine (bundled in this subpackage) + the names the slots
# import from this module.
from mayatk.mat_utils.marmoset_bridge._marmoset_engine import (  # noqa: F401
    MarmosetEngine,
    SEND_TO,
    ROUNDTRIP,
    _TEMPLATE_DIR,
    list_templates,
    template_modes,
    list_template_modes,
)

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils.mat_manifest import MatManifest

logger = logging.getLogger(__name__)

# FBX options tuned for Marmoset Toolbag.
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportBakeComplexAnimation": False,
}


def _classify_maya_chain(
    dag_path: str, high_suffix: str, low_suffix: str
) -> Optional[str]:
    """Walk *dag_path* leaf-to-root in Maya, return ``'high'``/``'low'``/None.

    Mirrors the Toolbag-side ``_classify_by_chain`` in
    :mod:`._toolbag_helpers`, but operates on Maya
    DAG paths via ``cmds.listRelatives`` -- so we can run it BEFORE the FBX
    export flattens the hierarchy.
    """
    cur = dag_path
    visited = 0
    while cur and visited < 64:
        leaf = cur.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
        if high_suffix and stem.endswith(high_suffix):
            return "high"
        if low_suffix and stem.endswith(low_suffix):
            return "low"
        parents = cmds.listRelatives(cur, parent=True, fullPath=True) or []
        cur = parents[0] if parents else None
        visited += 1
    return None


def build_bake_pairs_manifest(
    objects: Sequence[str], high_suffix: str, low_suffix: str
) -> Dict[str, str]:
    """Build the ``{mesh_short_name: 'high'|'low'}`` sidecar for the bake.

    Toolbag's FBX importer flattens parent transforms on the way in, so
    a ``bake_high`` group that the user named in Maya doesn't survive
    long enough for the Toolbag-side chain classifier to see it. We
    compute the classification HERE -- while we still have the full
    Maya parent chain -- and ship the result as a JSON sidecar that the
    rendered bake template reads after import.

    For each selected object, finds every mesh-transform descendant
    (and the object itself if it has a mesh shape), walks each one's
    Maya parent chain, and records a classification if any ancestor (or
    the mesh itself) carries *high_suffix* or *low_suffix*. Meshes with
    no matching ancestor are simply omitted -- ``split_high_low`` will
    fall through to its own chain walk / "rest is X" rules for them.
    """
    if not (high_suffix or low_suffix):
        return {}

    visited = set()
    mesh_xforms: List[str] = []
    for obj in objects:
        try:
            descendants = cmds.listRelatives(
                obj, allDescendents=True, type="transform", fullPath=True
            ) or []
        except Exception:
            descendants = []
        for x in [obj] + descendants:
            if x in visited:
                continue
            visited.add(x)
            shapes = cmds.listRelatives(
                x, shapes=True, type="mesh", fullPath=True
            ) or []
            if shapes:
                mesh_xforms.append(x)

    out: Dict[str, str] = {}
    for mesh_path in mesh_xforms:
        cls = _classify_maya_chain(mesh_path, high_suffix, low_suffix)
        if cls:
            leaf = mesh_path.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            out[leaf] = cls
    return out


class MarmosetBridge(MarmosetEngine):
    """Export the Maya selection to Marmoset Toolbag with templated automation.

    A :class:`MarmosetEngine` that prepends a Maya export step:
    :meth:`send` takes Maya *objects* (defaulting to the current
    selection), exports them to FBX with a :class:`MatManifest` sidecar
    and a bake-pairs sidecar, then delegates to
    :meth:`MarmosetEngine.send` with the produced file paths.

    Usage::

        MarmosetBridge().send(template="bake", mode="roundtrip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        toolbag_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        preset_file: Optional[str] = None,
        template: str = "import",
        mode: str = SEND_TO,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Export *objects*, build sidecars, and hand the FBX to the engine.

        Parameters mirror the previous bridge API. *objects* defaults to
        the current selection; the FBX, material manifest, and (when a
        high/low suffix matches) bake-pairs sidecar are written into
        *output_dir*, then :meth:`MarmosetEngine.send` renders the
        template and launches Toolbag.
        """
        # Fail fast on a bad template/mode before doing an expensive export.
        template_path = _TEMPLATE_DIR / f"{template}.py"
        allowed_modes = template_modes(template_path) if template_path.is_file() else ()
        if mode not in allowed_modes:
            self.logger.error(
                f"Template '{template}' does not support mode '{mode}'. "
                f"Declared modes: {allowed_modes}"
            )
            return None

        if not objects:
            objects = cmds.ls(selection=True, long=True)
        if not objects:
            self.logger.warning("Nothing selected to export.")
            return None

        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "maya_marmoset_bridge")
        os.makedirs(output_dir, exist_ok=True)

        base = output_name or self._scene_base_name()
        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if fbx_options:
            merged_options.update(fbx_options)

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting
        # so we get a clear FBX-export error instead of "Invalid file type".
        FbxUtils.load_plugin()

        self.logger.info("Exporting FBX ...")
        try:
            FbxUtils.export(
                file_path=fbx_path,
                objects=objects,
                preset_file=preset_file,
                options=merged_options,
                selection_only=True,
            )
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None
        self.logger.info(
            f'FBX written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
        )

        self.logger.info("Building material manifest ...")
        manifest = MatManifest.build(objects)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(
            f'Manifest written: '
            f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
        )

        # Bake-pairs sidecar: Maya-side parent-chain classification, written
        # while we still have the full DAG (Toolbag's FBX importer flattens
        # empty parent transforms). The bake template reads this back to
        # classify meshes regardless of what survived the round trip.
        from mayatk.mat_utils.marmoset_bridge import parameters as _params
        _merged_params = _params.defaults()
        _merged_params.update(params or {})
        _high_suffix = _merged_params.get("HIGH_SUFFIX", "_high") or ""
        _low_suffix = _merged_params.get("LOW_SUFFIX", "_low") or ""
        bake_pairs = build_bake_pairs_manifest(objects, _high_suffix, _low_suffix)
        actual_pairs_path: Optional[str] = None
        if bake_pairs:
            with open(pairs_path, "w", encoding="utf-8") as fh:
                json.dump(bake_pairs, fh, indent=2)
            self.logger.info(
                f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                f'pre-classified): '
                f'<a href="action://open?path={pairs_path}">{pairs_path}</a>'
            )
            actual_pairs_path = pairs_path

        # Delegate Toolbag-side work to the DCC-agnostic engine.
        return MarmosetEngine.send(
            self,
            model_path=fbx_path,
            manifest_path=manifest_path,
            pairs_path=actual_pairs_path,
            output_dir=output_dir,
            output_name=base,
            toolbag_exe=toolbag_exe,
            template=template,
            mode=mode,
            params=params,
        )

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MarmosetBridge()
    bridge.send(template="bake", mode=ROUNDTRIP)
