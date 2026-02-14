# !/usr/bin/python
# coding=utf-8
"""Lightweight material state snapshot and restore.

Captures the minimum state needed to survive a destructive operation
(e.g. ``shaderfx loadGraph``) on a material node:

1. **Texture connections** — via :class:`~mayatk.mat_utils.mat_manifest.MatManifest`.
2. **Scalar attribute values** — non-default, non-driven, non-locked floats/ints/bools
   that would otherwise be reset by a graph swap.

Usage::

    snap = MatSnapshot.capture("myStingrayMat")
    cmds.shaderfx(sfxnode="myStingrayMat", loadGraph="Standard_Transparent.sfx")
    MatSnapshot.restore("myStingrayMat", snap)

For duplicated materials use *source_mat_name*::

    snap = MatSnapshot.capture("origMat")
    # ... duplicate + loadGraph ...
    MatSnapshot.restore("origMat_Fade", snap, source_mat_name="origMat")
"""
import logging
from typing import Any, Dict, Optional

try:
    from maya import cmds
except ImportError:
    pass

from mayatk.mat_utils.mat_manifest import MatManifest

logger = logging.getLogger(__name__)


class MatSnapshot:
    """Capture and restore material state across destructive operations."""

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    @classmethod
    def capture(cls, mat_name: str, objects=None) -> Dict[str, Any]:
        """Snapshot textures and scalar values for *mat_name*.

        Parameters:
            mat_name: Maya material node name (string).
            objects: Optional list of objects assigned to the material.
                If provided, uses ``MatManifest.build`` for texture capture
                which resolves materials from the scene graph.

        Returns:
            Opaque snapshot dict with ``"textures"`` and ``"scalars"`` keys.
        """
        return {
            "textures": cls._capture_textures(mat_name, objects),
            "scalars": cls._capture_scalars(mat_name),
        }

    @classmethod
    def _capture_textures(cls, mat_name: str, objects=None) -> Dict[str, Any]:
        """Delegate texture capture to MatManifest."""
        if objects:
            # Preferred: resolve materials from objects (proven path).
            return MatManifest.build(objects)
        # Fallback: direct material lookup.
        mat_data = MatManifest._process_material(mat_name)
        if mat_data:
            return {"materials": {mat_name: mat_data}}
        return {"materials": {}}

    @classmethod
    def _capture_scalars(cls, mat_name: str) -> Dict[str, Any]:
        """Record non-default, non-driven, settable scalar attribute values."""
        values: Dict[str, Any] = {}

        attrs = cmds.listAttr(mat_name, settable=True, scalar=True) or []
        for attr_name in attrs:
            full = f"{mat_name}.{attr_name}"
            try:
                if not cmds.objExists(full):
                    continue
                # Skip driven attributes (they'll be reconnected, not set).
                if cmds.listConnections(full, source=True, destination=False):
                    continue
                if cmds.getAttr(full, lock=True):
                    continue
                values[attr_name] = cmds.getAttr(full)
            except Exception:
                pass

        return values

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    @classmethod
    def restore(
        cls,
        mat_name: str,
        snapshot: Dict[str, Any],
        source_mat_name: Optional[str] = None,
    ) -> Dict[str, int]:
        """Restore textures and scalar values onto *mat_name*.

        Parameters:
            mat_name: The material to restore state onto.
            snapshot: Dict returned by :meth:`capture`.
            source_mat_name: Original material name in the snapshot (when the
                material has been duplicated/renamed since capture).

        Returns:
            Dict with ``"textures"`` and ``"scalars"`` counts of restored items.
        """
        # Restore scalars first, then textures.  Texture connections
        # override any scalar values that targeted the same attributes.
        scalar_count = cls._restore_scalars(mat_name, snapshot)
        tex_count = cls._restore_textures(mat_name, snapshot, source_mat_name)
        return {"textures": tex_count, "scalars": scalar_count}

    @classmethod
    def _restore_textures(
        cls,
        mat_name: str,
        snapshot: Dict[str, Any],
        source_mat_name: Optional[str] = None,
    ) -> int:
        manifest = snapshot.get("textures", {})
        if not manifest:
            return 0
        return MatManifest.restore(mat_name, manifest, source_mat_name=source_mat_name)

    @classmethod
    def _restore_scalars(cls, mat_name: str, snapshot: Dict[str, Any]) -> int:
        scalars = snapshot.get("scalars", {})
        if not scalars:
            return 0

        restored = 0
        for attr_name, value in scalars.items():
            full = f"{mat_name}.{attr_name}"
            try:
                if not cmds.objExists(full):
                    continue
                # Don't stomp driven attributes (textures were just reconnected).
                if cmds.listConnections(full, source=True, destination=False):
                    continue
                if cmds.getAttr(full, lock=True):
                    continue
                cmds.setAttr(full, value)
                restored += 1
            except Exception:
                pass

        if restored:
            logger.info(f"Restored {restored} scalar value(s) on '{mat_name}'.")
        return restored
