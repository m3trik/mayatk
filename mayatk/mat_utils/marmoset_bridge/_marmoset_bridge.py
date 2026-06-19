# !/usr/bin/python
# coding=utf-8
"""Maya-side glue for the Marmoset Toolbag engine.

:class:`MarmosetBridge` is the Maya half of the split: a
:class:`pythontk.HandoffBridge` whose ``_produce`` exports the current
selection to FBX, builds a :class:`MatManifest` material sidecar and a
Maya-DAG-classified high/low bake-pairs sidecar, and whose **deliverer** is the
DCC-agnostic :class:`._marmoset_engine.MarmosetEngine` (a
:class:`pythontk.Deliverer`) that renders the Toolbag template and launches /
round-trips Toolbag.

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

import pythontk as ptk

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


class MarmosetBridge(ptk.HandoffBridge):
    """Export the Maya selection to Marmoset Toolbag with templated automation.

    A :class:`pythontk.HandoffBridge` whose ``_produce`` exports the selection to
    FBX with a :class:`MatManifest` sidecar and a bake-pairs sidecar, and whose
    deliverer is the DCC-agnostic :class:`MarmosetEngine` (renders the Toolbag
    template + launches / round-trips). The public ``send()`` is the shared
    skeleton; its app-specific knobs (``output_dir`` / ``output_name`` /
    ``toolbag_exe`` / ``fbx_options`` / ``preset_file``) ride as keyword extras.

    Usage::

        MarmosetBridge().send(template="bake", mode="roundtrip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

    def __init__(self, toolbag_path: Optional[str] = None):
        super().__init__()
        # The Toolbag-side launch/roundtrip Strategy (also usable standalone).
        self.deliverer = MarmosetEngine(toolbag_path)
        # The panel redirects only the bridge's logger (`BridgeSlotsBase`); route
        # the engine's delivery-phase output (Toolbag launch, output links,
        # roundtrip results) through the SAME logger so it reaches the log panel.
        # `LoggingMixin.logger` is a non-data ClassProperty, so this instance
        # attribute shadows it for this engine only (standalone engines keep
        # their own logger).
        self.deliverer.logger = self.logger

    # Back-compat: expose the engine's resolved Toolbag path on the bridge.
    @property
    def toolbag_path(self) -> Optional[str]:
        return self.deliverer.toolbag_path

    @toolbag_path.setter
    def toolbag_path(self, value: Optional[str]) -> None:
        self.deliverer.toolbag_path = value

    def params_defaults(self) -> Dict[str, Any]:
        from mayatk.mat_utils.marmoset_bridge import parameters as _params

        return _params.defaults()

    def render_template(self, *args, **kwargs) -> Optional[str]:
        """Render a Toolbag script body (delegates to the engine deliverer)."""
        return self.deliverer.render_template(*args, **kwargs)

    # ------------------------------------------------------------------ hooks
    def _resolve_objects(self, objects):
        """Return the objects to export; ``None`` -> current selection."""
        if not objects:
            objects = cmds.ls(selection=True, long=True)
        return objects or []

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX + material manifest (+ bake-pairs sidecar) into ``output_dir``.

        Resolves ``output_dir`` / ``output_name`` (stamping them back into
        ``request.extras`` so the engine deliverer writes its script alongside),
        then returns a :class:`pythontk.Payload` carrying the FBX + sidecar paths.
        """
        output_dir = request.get("output_dir") or os.path.join(
            tempfile.gettempdir(), "maya_marmoset_bridge"
        )
        os.makedirs(output_dir, exist_ok=True)
        base = request.get("output_name") or self._scene_base_name()
        # Keep produce + deliver on the same dir/name.
        request.extras["output_dir"] = output_dir
        request.extras["output_name"] = base

        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if request.get("fbx_options"):
            merged_options.update(request.get("fbx_options"))

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting
        # so we get a clear FBX-export error instead of "Invalid file type".
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
        _high_suffix = request.params.get("HIGH_SUFFIX", "_high") or ""
        _low_suffix = request.params.get("LOW_SUFFIX", "_low") or ""
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

        return ptk.Payload(
            primary=fbx_path,
            extras={"manifest": manifest_path, "pairs": actual_pairs_path},
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
