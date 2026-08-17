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

The exact-wiring counterpart, for a rewire that must be undone verbatim
(the Scene Exporter's staged texture conversion)::

    snap = MatSnapshot.capture_network(materials)
    MatUpdater.update_materials(materials, config=...)   # rewires the graph
    MatSnapshot.restore_network(snap)                     # puts it back

Both pairs also come as scopes — the restore runs from a ``finally`` (a
failing restore is logged, never raised, and never masks the body's error),
and the same object can be handed to
``TaskFactory.stage_deferred_context`` when the restore must outlive the
frame (an export that reads the mutation)::

    with MatSnapshot.restored("myStingrayMat"):
        cmds.shaderfx(sfxnode="myStingrayMat", loadGraph="Standard_Transparent.sfx")

    with MatSnapshot.network_scope(materials):
        MatUpdater.update_materials(materials, config=...)
"""
import contextlib
import logging
from typing import Any, Dict, Optional

import pythontk as ptk

try:
    from maya import cmds
except ImportError:
    pass

from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.node_utils.attributes._attributes import Attributes

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

    # ------------------------------------------------------------------
    # Scopes
    # ------------------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def restored(cls, mat_name: str, objects=None):
        """Scope form of :meth:`capture` / :meth:`restore` (manifest + scalars).

        For a destructive op on the material *node* (``shaderfx loadGraph``):
        the textures are reconnected and the scalars reset on exit, whatever
        the body did.
        """
        snapshot = cls.capture(mat_name, objects)
        try:
            yield snapshot
        finally:
            with ptk.CoreUtils.teardown_guard(logger, f"material {mat_name!r}"):
                cls.restore(mat_name, snapshot)

    @classmethod
    @contextlib.contextmanager
    def network_scope(cls, materials):
        """Scope form of :meth:`capture_network` / :meth:`restore_network`.

        For a graph *rewrite* (the Map Updater): the exact upstream wiring of
        *materials* is put back on exit, whatever the body did.
        """
        snapshot = cls.capture_network(materials)
        try:
            yield snapshot
        finally:
            with ptk.CoreUtils.teardown_guard(logger, "shading network"):
                cls.restore_network(snapshot)

    # ------------------------------------------------------------------
    # Exact-wiring network snapshot
    # ------------------------------------------------------------------

    # Attributes a rewire tool touches on a ``file`` node besides the graph.
    _FILE_NODE_ATTRS = (
        "fileTextureName",
        "colorSpace",
        "ignoreColorSpaceFileRules",
        "alphaIsLuminance",
        "uvTilingMode",
    )

    @classmethod
    def capture_network(cls, materials) -> Dict[str, Any]:
        """Record the exact upstream wiring of *materials* so it can be undone.

        Unlike :meth:`capture` (a slot -> texture-path manifest, rebuilt on
        restore through find-or-create file nodes) this keeps node identity:
        every node upstream of the materials is recorded by UUID with its
        incoming plug connections, and ``file`` nodes with the attributes a
        rewire changes (path, colour space, ...). :meth:`restore_network`
        then reverses a graph rewrite verbatim -- new nodes deleted, stale
        connections broken, the recorded ones re-made -- which a manifest
        cannot do (it never learns which slots the rewrite *added*).

        Parameters:
            materials: Material node names (or nodes).

        Returns:
            Opaque snapshot dict for :meth:`restore_network`.
        """
        nodes: Dict[str, Dict[str, Any]] = {}
        for mat in materials:
            for node in cmds.listHistory(str(mat)) or []:
                if node in nodes or cmds.ls(node, dag=True):
                    continue
                uuid = cmds.ls(node, uuid=True)[0]
                conns = (
                    cmds.listConnections(
                        node,
                        source=True,
                        destination=False,
                        plugs=True,
                        connections=True,
                    )
                    or []
                )
                # Flat [dstPlug, srcPlug, ...]; keep the SOURCE by UUID too so a
                # rename between capture and restore cannot break the pair.
                pairs = []
                for i in range(0, len(conns), 2):
                    dst_attr = conns[i].partition(".")[2]
                    src_node, _, src_attr = conns[i + 1].partition(".")
                    src_uuid = (cmds.ls(src_node, uuid=True) or [None])[0]
                    if src_uuid:
                        pairs.append((src_uuid, src_attr, dst_attr))
                entry: Dict[str, Any] = {"uuid": uuid, "connections": pairs}
                if cmds.nodeType(node) == "file":
                    entry["attrs"] = {
                        a: cmds.getAttr(f"{node}.{a}")
                        for a in cls._FILE_NODE_ATTRS
                        if cmds.objExists(f"{node}.{a}")
                    }
                nodes[node] = entry
        return {"materials": [str(m) for m in materials], "nodes": nodes}

    @classmethod
    def restore_network(cls, snapshot: Dict[str, Any]) -> Dict[str, int]:
        """Reverse everything a graph rewrite did since :meth:`capture_network`.

        1. Delete every non-DAG node now upstream of the snapshot's materials
           that the snapshot does not know (the rewrite created it) -- except
           shading engines, and except a node that also feeds something
           OUTSIDE the snapshotted network (a pre-existing node the rewrite
           merely wired in): that one is only unplugged by step 2.
        2. On every recorded node: break incoming connections the snapshot
           lacks, re-make the recorded ones, and put a ``file`` node's
           recorded attributes back.

        Returns:
            ``{"deleted": n, "reconnected": n, "attrs": n}`` counts.
        """
        by_uuid = {e["uuid"]: name for name, e in snapshot["nodes"].items()}
        counts = {"deleted": 0, "reconnected": 0, "attrs": 0}

        def _resolve(uuid):
            found = cmds.ls(uuid) if uuid else []
            return found[0] if found else None

        # 1. Nodes the rewrite created.
        strangers = []
        for mat in snapshot["materials"]:
            if not cmds.objExists(mat):
                continue
            for node in cmds.listHistory(mat) or []:
                if cmds.ls(node, dag=True) or cmds.nodeType(node) == "shadingEngine":
                    continue
                if cmds.ls(node, uuid=True)[0] not in by_uuid and node not in strangers:
                    strangers.append(node)
        # A consumer outside the network keeps a stranger alive; the default
        # registries every shading node hangs off (defaultTextureList1, ...)
        # are not consumers.
        known = set(strangers) | {n for n in map(_resolve, by_uuid) if n}
        doomed = [
            n
            for n in strangers
            if all(
                d in known or cmds.ls(d, defaultNodes=True)
                for d in (
                    cmds.listConnections(n, source=False, destination=True) or []
                )
            )
        ]
        if doomed:
            cmds.delete(doomed)
            counts["deleted"] = len(doomed)

        # 2. Recorded nodes: wiring and attributes.
        for entry in snapshot["nodes"].values():
            node = _resolve(entry["uuid"])
            if not node:
                continue
            wanted = set()
            for src_uuid, src_attr, dst_attr in entry["connections"]:
                src = _resolve(src_uuid)
                if src:
                    wanted.add((f"{src}.{src_attr}", f"{node}.{dst_attr}"))
            conns = (
                cmds.listConnections(
                    node, source=True, destination=False, plugs=True, connections=True
                )
                or []
            )
            current = {(conns[i + 1], conns[i]) for i in range(0, len(conns), 2)}
            for src_plug, dst_plug in current - wanted:
                try:
                    cmds.disconnectAttr(src_plug, dst_plug)
                except RuntimeError:
                    pass
            for src_plug, dst_plug in wanted - current:
                try:
                    cmds.connectAttr(src_plug, dst_plug, force=True)
                    counts["reconnected"] += 1
                except RuntimeError as e:
                    logger.debug(f"Could not reconnect {src_plug} -> {dst_plug}: {e}")
            for attr, value in (entry.get("attrs") or {}).items():
                plug = f"{node}.{attr}"
                try:
                    if cmds.getAttr(plug) == value or cmds.getAttr(plug, lock=True):
                        continue
                    if attr == "fileTextureName" and isinstance(value, str):
                        # NOT cmds.setAttr: it auto-expands a resolvable
                        # relative path straight back to absolute, so restoring
                        # a snapshot over a relativized scene would leave it
                        # holding absolute paths -- the exact thing "Scene
                        # Untouched" promises not to do. set_plug_literal
                        # raises on an unwritable plug, which keeps the
                        # ``counts["attrs"]`` tally below honest.
                        Attributes.set_plug_literal(plug, value)
                    elif isinstance(value, str):
                        cmds.setAttr(plug, value, type="string")
                    else:
                        cmds.setAttr(plug, value)
                    counts["attrs"] += 1
                except RuntimeError as e:
                    logger.debug(f"Could not restore {plug}: {e}")

        if any(counts.values()):
            logger.info(
                "Restored shading network: "
                f"{counts['deleted']} node(s) removed, "
                f"{counts['reconnected']} connection(s) re-made, "
                f"{counts['attrs']} attribute(s) reset."
            )
        return counts
