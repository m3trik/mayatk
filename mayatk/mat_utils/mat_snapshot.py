# !/usr/bin/python
# coding=utf-8
"""Lightweight material state snapshot and restore.

Captures the minimum state needed to survive a destructive operation
(e.g. ``shaderfx loadGraph``) on a material node:

1. **Texture connections** — via :class:`~mayatk.mat_utils.mat_manifest.MatManifest`.
2. **Scalar attribute values** — non-default, non-driven, non-locked floats/ints/bools
   that would otherwise be reset by a graph swap.
3. **Incoming connections, verbatim** — every input plug the material carries,
   which is a superset of (1): the manifest models the mapped PBR channels,
   and it models them at the arity the authoring rule derives rather than the
   one they were found on.

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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pythontk as ptk

try:
    from maya import cmds
except ImportError:
    pass

from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap
from mayatk.node_utils.attributes._attributes import Attributes

logger = logging.getLogger(__name__)

#: One captured input: ``(source node uuid, source attribute, destination
#: attribute)``. The source is held by UUID so a rename between capture and
#: restore cannot break the pair; the destination is the plug the connection
#: was actually found on -- a compound parent or one of its children.
CapturedConnection = Tuple[str, str, str]


class _MatSnapshotInternal:
    """Internal helpers for :class:`MatSnapshot` (per-plug capture mechanics)."""

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

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

    @classmethod
    def _capture_connections(cls, mat_name: str) -> List[CapturedConnection]:
        """Record every input on *mat_name* exactly as it is wired.

        The manifest above models the MAPPED channels only, and models them as
        a texture PATH per logical channel -- so a restore built from it alone
        drops whatever else the material carried (a StingrayPBS preset's own
        ``TEX_global_diffuse_cube`` / ``TEX_global_specular_cube`` /
        ``TEX_brdf_lut`` IBL inputs, an animCurve, a utility node) and re-wires
        the rest at whatever arity ``ShaderAttributeMap`` declares. Both cost
        the round trip its identity: a greyscale map found on a float3 slot's
        compound plug comes back driving the three children instead.

        Recording the wiring itself is what makes the restore a restore.
        """
        pairs: List[CapturedConnection] = []
        try:
            conns = (
                cmds.listConnections(
                    mat_name,
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                )
                or []
            )
        except (RuntimeError, ValueError):
            return pairs

        # Flat [dstPlug, srcPlug, ...] -- the destination is the plug ON the
        # material, which is the arity that must survive the round trip.
        for i in range(0, len(conns), 2):
            dst_attr = conns[i].partition(".")[2]
            src_node, _, src_attr = conns[i + 1].partition(".")
            src_uuid = (cmds.ls(src_node, uuid=True) or [None])[0]
            if src_uuid and src_attr and dst_attr:
                pairs.append((src_uuid, src_attr, dst_attr))
        return pairs

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    @classmethod
    def _restore_textures(
        cls,
        mat_name: str,
        snapshot: Dict[str, Any],
        source_mat_name: Optional[str] = None,
        skip_channels: Iterable[str] = (),
    ) -> int:
        """Re-author the manifest's channels — the fallback, never the default.

        *skip_channels* are the ones :meth:`_restore_connections` already put
        back verbatim; re-authoring those would undo the arity it preserved.
        What is left is a channel whose captured source could not be replayed
        (its node is gone, or the target graph has no such plug), which the
        manifest can still rebuild find-or-create style.
        """
        manifest = snapshot.get("textures", {})
        if not manifest:
            return 0
        if skip_channels:
            manifest = cls._manifest_without(
                manifest, source_mat_name or mat_name, skip_channels
            )
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
                # Don't stomp a driven attribute: this runs BEFORE the wiring
                # is replayed, so an input here is one the destructive op left
                # in place -- setting it would fail, or fight its driver.
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

    @classmethod
    def _restore_connections(
        cls, mat_name: str, snapshot: Dict[str, Any]
    ) -> Tuple[int, Set[str]]:
        """Replay the captured inputs onto *mat_name*, plug for plug.

        Same source plug, same destination plug: a connection found on a
        compound goes back on the compound, one found per child goes back per
        child. A destination the target graph does not expose (a
        ``Standard_Masked`` cutout slot on ``Standard_Transparent``) is skipped
        here and left to the manifest fallback, which knows the channel's
        alternate slot.

        Returns:
            tuple: ``(connections made, root attributes now driven)``. The
            second is what :meth:`_restore_textures` must not re-author.
        """
        pairs = snapshot.get("connections") or []
        scalars = snapshot.get("scalars") or {}
        restored = 0
        driven: Set[str] = set()

        for src_uuid, src_attr, dst_attr in pairs:
            found = cmds.ls(src_uuid) if src_uuid else []
            if not found:
                logger.debug(f"Snapshot source {src_uuid} is gone; leaving to manifest.")
                continue
            src_plug = f"{found[0]}.{src_attr}"
            dst_plug = f"{mat_name}.{dst_attr}"
            if not cmds.objExists(dst_plug) or not cmds.objExists(src_plug):
                logger.debug(f"Snapshot plug {dst_plug} absent on the target graph.")
                continue
            try:
                if not cmds.isConnected(src_plug, dst_plug):
                    cls._clear_conflicting_inputs(mat_name, dst_attr)
                    cmds.connectAttr(src_plug, dst_plug, force=True)
                    restored += 1
            except RuntimeError as e:
                logger.debug(f"Could not replay {src_plug} -> {dst_plug}: {e}")
                continue
            root = cls._root_attr(mat_name, dst_attr)
            driven.add(root)
            cls._enable_unrecorded_toggle(mat_name, root, scalars)

        if restored:
            logger.info(f"Replayed {restored} input connection(s) on '{mat_name}'.")
        return restored, driven

    # ------------------------------------------------------------------
    # Plug helpers
    # ------------------------------------------------------------------

    @classmethod
    def _root_attr(cls, node: str, attr: str) -> str:
        """The top-level attribute *attr* belongs to.

        ``TEX_metallic_mapX`` -> ``TEX_metallic_map``, so a child connection
        can be matched against the slot a logical channel declares.
        """
        root = attr.partition(".")[0].partition("[")[0]
        while True:
            try:
                parent = cmds.attributeQuery(root, node=node, listParent=True)
            except (RuntimeError, ValueError, TypeError):
                return root
            if not parent:
                return root
            root = parent[0]

    @classmethod
    def _clear_conflicting_inputs(cls, node: str, attr: str) -> None:
        """Break the inputs that would fight a connection into ``node.attr``.

        ``connectAttr -force`` replaces the input on the plug it targets, but
        not one held by that plug's ANCESTORS or DESCENDANTS -- and Maya
        refuses a compound connection while its children are driven (and the
        reverse). A restore putting a compound input back where the destructive
        op (or a previous re-authoring restore) left per-child ones has to
        clear those first, or the arity it captured cannot be re-made.

        SIBLINGS are deliberately left alone: replaying a per-child capture
        drives ``…R``, ``…G`` and ``…B`` one at a time, and clearing the whole
        compound each time would leave only the last child connected.
        """
        leaf = attr.rpartition(".")[2].partition("[")[0]
        conflicting = []
        current = leaf
        while True:  # ancestors
            try:
                parent = cmds.attributeQuery(current, node=node, listParent=True)
            except (RuntimeError, ValueError, TypeError):
                break
            if not parent:
                break
            current = parent[0]
            conflicting.append(current)
        try:  # descendants
            conflicting.extend(
                cmds.attributeQuery(leaf, node=node, listChildren=True) or []
            )
        except (RuntimeError, ValueError, TypeError):
            pass

        for name in conflicting:
            plug = f"{node}.{name}"
            if not cmds.objExists(plug):
                continue
            for src in (
                cmds.listConnections(plug, plugs=True, source=True, destination=False)
                or []
            ):
                try:
                    cmds.disconnectAttr(src, plug)
                except RuntimeError:
                    pass

    @classmethod
    def _enable_unrecorded_toggle(
        cls, mat_name: str, attr: str, scalars: Dict[str, Any]
    ) -> bool:
        """Switch on a ShaderFX ``use_*`` companion the snapshot never saw.

        A connected slot is inert while its toggle reads 0. Normally the toggle
        is a scalar the snapshot captured and :meth:`_restore_scalars` has
        already put back -- that value is the truth and is left alone. Only
        where the TARGET graph exposes a toggle the source graph did not (so
        the snapshot is silent about it) does the replayed connection itself
        imply the answer.
        """
        toggle = ShaderAttributeMap.map_toggle_attr(attr)
        if toggle in scalars:
            return False
        try:
            if not cmds.attributeQuery(toggle, node=mat_name, exists=True):
                return False
            cmds.setAttr(f"{mat_name}.{toggle}", 1)
            return True
        except RuntimeError:
            return False

    @classmethod
    def _channels_for_attrs(cls, mat_name: str, attrs: Set[str]) -> Set[str]:
        """The logical channels whose live slot on *mat_name* is among *attrs*."""
        if not attrs:
            return set()
        try:
            node_type = cmds.nodeType(mat_name)
        except RuntimeError:
            return set()

        channels = set()
        for logical in ShaderAttributeMap.logical_channels():
            slot = ShaderAttributeMap.resolve_live_slot(mat_name, logical, node_type)
            if slot and slot[0] in attrs:
                channels.add(logical)
        return channels

    @staticmethod
    def _manifest_without(
        manifest: Dict[str, Any], key: str, channels: Iterable[str]
    ) -> Dict[str, Any]:
        """A COPY of *manifest* with *channels* dropped from the *key* material.

        A copy, never an edit in place: one snapshot is replayed onto every
        duplicate a material split makes (``RenderOpacity`` material mode), so
        dropping a channel here would starve the next restore.
        """
        mat_data = (manifest.get("materials") or {}).get(key)
        if not mat_data:
            return manifest
        remaining = {k: v for k, v in mat_data.items() if k not in channels}
        if remaining == mat_data:
            return manifest
        materials = dict(manifest.get("materials") or {})
        materials[key] = remaining
        return {**manifest, "materials": materials}


class MatSnapshot(_MatSnapshotInternal):
    """Capture and restore material state across destructive operations."""

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    @classmethod
    def capture(cls, mat_name: str, objects=None) -> Dict[str, Any]:
        """Snapshot textures, scalar values and wiring for *mat_name*.

        Parameters:
            mat_name: Maya material node name (string).
            objects: Optional list of objects assigned to the material.
                If provided, uses ``MatManifest.build`` for texture capture
                which resolves materials from the scene graph.

        Returns:
            Opaque snapshot dict with ``"textures"``, ``"scalars"`` and
            ``"connections"`` keys.
        """
        return {
            "textures": cls._capture_textures(mat_name, objects),
            "scalars": cls._capture_scalars(mat_name),
            "connections": cls._capture_connections(mat_name),
        }

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
        """Restore textures, scalar values and wiring onto *mat_name*.

        The captured connections are replayed FIRST and verbatim -- same source
        plug, same destination plug -- because the contract is to put back what
        was there, not to derive what should be there. The manifest is the
        fallback for what verbatim replay could not place: a channel whose
        source node no longer exists, or one whose plug the target graph does
        not expose (where ``ShaderAttributeMap`` knows an alternate slot).

        Parameters:
            mat_name: The material to restore state onto.
            snapshot: Dict returned by :meth:`capture`.
            source_mat_name: Original material name in the snapshot (when the
                material has been duplicated/renamed since capture).

        Returns:
            Dict of counts: ``"connections"`` replayed verbatim, ``"scalars"``
            re-applied, and ``"textures"`` re-authored from the manifest --
            the last being the FALLBACK tally, so an untouched round trip now
            reports 0 there and its channels under ``"connections"``.
        """
        # Scalars first: they carry the ``use_*_map`` toggles, and a value must
        # never be written over a plug the wiring below is about to drive.
        scalar_count = cls._restore_scalars(mat_name, snapshot)
        conn_count, driven = cls._restore_connections(mat_name, snapshot)
        tex_count = cls._restore_textures(
            mat_name,
            snapshot,
            source_mat_name,
            skip_channels=cls._channels_for_attrs(mat_name, driven),
        )
        return {
            "textures": tex_count,
            "scalars": scalar_count,
            "connections": conn_count,
        }

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

        Unlike :meth:`capture` (whose manifest half is a slot -> texture-path
        model, rebuilt on restore through find-or-create file nodes) this keeps
        node identity for the WHOLE upstream network, not just the material's
        own inputs: every node upstream of the materials is recorded by UUID
        with its incoming plug connections, and ``file`` nodes with the
        attributes a rewire changes (path, colour space, ...).
        :meth:`restore_network` then reverses a graph rewrite verbatim -- new
        nodes deleted, stale connections broken, the recorded ones re-made --
        which the per-material snapshot cannot do (it never learns which nodes
        the rewrite *added*).

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
