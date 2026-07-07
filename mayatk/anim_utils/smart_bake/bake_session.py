# !/usr/bin/python
# coding=utf-8
"""Persistence and restore engine for SmartBake's nondestructive manifest.

A *bake session* is a JSON record of everything a ``SmartBake.bake()`` call
changed, persisted as a string attr on the ``data_internal`` node so it
survives scene save/reopen (and never rides into the FBX — export-facing
attrs live on ``data_export``, which is not touched here).

Restore data lives **in the scene**, not beside it:

- Anything bake mutates in place (SDK curves converted by ``bakeResults``,
  a child's own visibility curve keyed over by the inherited-visibility
  pass) is *stashed* first: a disconnected ``duplicate()`` of the animCurve,
  registered via a message connection on ``data_internal`` and locked so
  *Optimize Scene Size* cannot purge it.
- Everything else is recorded as node/plug references (name + UUID, so
  renames don't break restore) plus scalar state (``nodeState``,
  ``ikBlend``, static visibility values).

``restore_session()`` reverses the most recent (or a named) session:
deletes the override layer, unmutes drivers to their recorded states,
re-enables IK handles, restores visibility, deletes baked base-layer
curves, reconnects the recorded driver network, and unstashes curves.

Why a manifest when the override layer is already nondestructive?
The layer only isolates the *baked keys* in layer mode. Everything else
SmartBake touches has no layer to hide behind:

- **Base-layer mode** converts SDK curves in place (the original node is
  deleted) and disconnects driver networks — only the stash + connection
  snapshot can rebuild them.
- **Inherited-visibility bake** keys the BASE layer by necessity (FBX
  ``BakeComplexAnimation`` doesn't evaluate visibility through layer
  blend nodes) and merges into the child's own vis curve when one exists.
- **Muted drivers** (``mute_drivers=True``) need their prior ``nodeState``
  values back, and **ikBlend** needs re-enabling after base-layer DIC.
- **Cross-session identity**: the manifest records *which* layer/nodes
  were SmartBake artifacts, so Unbake works after scene save/reopen
  without guessing from names.
"""
import json
import time
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)

_session_counter = itertools.count()


# ---------------------------------------------------------------------------
# Node / plug references (rename-safe via UUID)
# ---------------------------------------------------------------------------


def node_ref(node: str) -> Dict[str, Optional[str]]:
    """Return a rename-safe reference ``{"name", "uuid"}`` for *node*."""
    uuids = cmds.ls(node, uuid=True) or []
    return {"name": str(node), "uuid": uuids[0] if uuids else None}


def resolve_ref(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]:
    """Resolve a :func:`node_ref` back to a live node name, or ``None``."""
    if not ref:
        return None
    uuid = ref.get("uuid")
    if uuid:
        found = cmds.ls(uuid)
        if found:
            return found[0]
    name = ref.get("name")
    if name and cmds.objExists(name):
        return name
    return None


def plug_ref(plug: str) -> Dict[str, Optional[str]]:
    """Return a rename-safe reference ``{"name", "uuid", "attr"}`` for *plug*."""
    node, _, attr = plug.partition(".")
    ref = node_ref(node)
    ref["attr"] = attr
    return ref


def resolve_plug(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]:
    """Resolve a :func:`plug_ref` back to ``"node.attr"``, or ``None``."""
    if not ref:
        return None
    node = resolve_ref(ref)
    if not node:
        return None
    return f"{node}.{ref['attr']}"


def _trace_out_of_conversion(src_plug: str) -> str:
    """If *src_plug* sits on a unitConversion, return its real upstream plug.

    ``connectAttr`` re-inserts unit conversions automatically on restore, so
    recording the conversion node itself would only create a dangling
    reference once bake deletes it.
    """
    node = src_plug.partition(".")[0]
    if cmds.nodeType(node) == "unitConversion":
        upstream = (
            cmds.listConnections(
                f"{node}.input", source=True, destination=False, plugs=True
            )
            or []
        )
        if upstream:
            return upstream[0]
    return src_plug


def _trace_into_conversion(dst_plug: str) -> str:
    """If *dst_plug* sits on a unitConversion, return its real downstream plug."""
    node = dst_plug.partition(".")[0]
    if cmds.nodeType(node) == "unitConversion":
        downstream = (
            cmds.listConnections(
                f"{node}.output", source=False, destination=True, plugs=True
            )
            or []
        )
        if downstream:
            return downstream[0]
    return dst_plug


# ---------------------------------------------------------------------------
# Manifest store (JSON on data_internal)
# ---------------------------------------------------------------------------


class BakeSessionStore:
    """LIFO stack of bake-session manifests on the ``data_internal`` node."""

    ATTR = "smart_bake_sessions"
    STASH_REGISTRY_ATTR = "smart_bake_stash"
    SCHEMA_VERSION = 1

    @classmethod
    def load(cls) -> List[dict]:
        """Return all persisted sessions (oldest first)."""
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_internal_string(cls.ATTR)
        if not raw:
            return []
        try:
            sessions = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("SmartBake: session manifest is corrupt; ignoring.")
            return []
        return sessions if isinstance(sessions, list) else []

    @classmethod
    def save(cls, sessions: List[dict]) -> None:
        from mayatk.node_utils.data_nodes import DataNodes

        DataNodes.set_internal_string(cls.ATTR, json.dumps(sessions))

    @classmethod
    def push(cls, session: dict) -> None:
        sessions = cls.load()
        sessions.append(session)
        cls.save(sessions)

    @classmethod
    def peek(cls, session_id: Optional[str] = None) -> Optional[dict]:
        """Return the latest session (or the one matching *session_id*)."""
        sessions = cls.load()
        if not sessions:
            return None
        if session_id is None:
            return sessions[-1]
        for session in reversed(sessions):
            if session.get("id") == session_id:
                return session
        return None

    @classmethod
    def pop(cls, session_id: Optional[str] = None) -> Optional[dict]:
        """Remove and return the latest session (or the matching one)."""
        sessions = cls.load()
        if not sessions:
            return None
        if session_id is None:
            session = sessions.pop()
        else:
            session = None
            for i in range(len(sessions) - 1, -1, -1):
                if sessions[i].get("id") == session_id:
                    session = sessions.pop(i)
                    break
            if session is None:
                return None
        cls.save(sessions)
        return session

    @classmethod
    def list_ids(cls) -> List[str]:
        return [s.get("id") for s in cls.load() if s.get("id")]

    @classmethod
    def new_session_id(cls) -> str:
        stamp = time.strftime("%Y%m%d%H%M%S")
        return f"sb_{stamp}_{next(_session_counter)}"


# ---------------------------------------------------------------------------
# Curve stashing
# ---------------------------------------------------------------------------


def _ensure_stash_registry() -> str:
    """Ensure the message-multi registry attr on data_internal; return the node."""
    from mayatk.node_utils.data_nodes import DataNodes

    internal = str(DataNodes.ensure_internal())
    if not cmds.attributeQuery(
        BakeSessionStore.STASH_REGISTRY_ATTR, node=internal, exists=True
    ):
        cmds.addAttr(
            internal,
            longName=BakeSessionStore.STASH_REGISTRY_ATTR,
            attributeType="message",
            multi=True,
            indexMatters=False,
        )
    return internal


def stash_curve(curve: str) -> dict:
    """Duplicate *curve* into a locked, registered stash node.

    Records the curve's live input/output wiring (traced through
    unitConversion nodes, which ``connectAttr`` recreates on demand) so
    :func:`unstash_curve` can rebuild the original network.

    Returns:
        The stash record for the session manifest.
    """
    record: Dict[str, Any] = {
        "original_name": str(curve),
        "inputs": [],  # [[src plug_ref, attr-on-curve], ...]
        "outputs": [],  # [[attr-on-curve, dst plug_ref], ...]
    }

    in_pairs = (
        cmds.listConnections(
            curve, source=True, destination=False, plugs=True, connections=True
        )
        or []
    )
    for i in range(0, len(in_pairs), 2):
        dest_on_curve = in_pairs[i].partition(".")[2]
        src = _trace_out_of_conversion(in_pairs[i + 1])
        record["inputs"].append([plug_ref(src), dest_on_curve])

    out_pairs = (
        cmds.listConnections(
            curve, source=False, destination=True, plugs=True, connections=True
        )
        or []
    )
    for i in range(0, len(out_pairs), 2):
        src_on_curve = out_pairs[i].partition(".")[2]
        dst = _trace_into_conversion(out_pairs[i + 1])
        if src_on_curve == "message":
            continue
        record["outputs"].append([src_on_curve, plug_ref(dst)])

    dup = cmds.duplicate(curve, name=f"{curve}__smartBakeStash")[0]
    internal = _ensure_stash_registry()
    cmds.connectAttr(
        f"{dup}.message",
        f"{internal}.{BakeSessionStore.STASH_REGISTRY_ATTR}",
        nextAvailable=True,
    )
    cmds.lockNode(dup, lock=True)
    record["stash"] = node_ref(dup)
    return record


def unstash_curve(
    record: dict,
    warnings: Optional[List[str]] = None,
    fallback_dst: Optional[str] = None,
) -> Optional[str]:
    """Reconnect a stashed curve into its recorded network.

    Unlocks and deregisters the stash node, renames it back to the original
    curve name (when free), then reconnects recorded inputs and outputs.
    If a recorded output destination no longer exists (e.g. bake deleted a
    pairBlend), *fallback_dst* is used when given, otherwise a warning is
    appended.

    Returns:
        The restored curve name, or ``None`` if the stash node is gone.
    """
    warnings = warnings if warnings is not None else []
    stash = resolve_ref(record.get("stash"))
    if not stash:
        warnings.append(
            f"Stashed curve for '{record.get('original_name')}' not found — "
            "was it deleted manually?"
        )
        return None

    cmds.lockNode(stash, lock=False)

    # Deregister from the stash registry.
    msg_conns = (
        cmds.listConnections(
            f"{stash}.message", source=False, destination=True, plugs=True
        )
        or []
    )
    for dst in msg_conns:
        if BakeSessionStore.STASH_REGISTRY_ATTR in dst:
            cmds.disconnectAttr(f"{stash}.message", dst)

    original = record.get("original_name")
    if original and not cmds.objExists(original):
        try:
            stash = cmds.rename(stash, original.rpartition("|")[2])
        except RuntimeError:
            # e.g. the original namespace no longer exists — reconnecting
            # under the stash name is still a full restore of the data.
            pass

    for src_ref, curve_attr in record.get("inputs", []):
        src = resolve_plug(src_ref)
        if not src:
            warnings.append(
                f"Input '{src_ref.get('name')}.{src_ref.get('attr')}' for restored "
                f"curve '{stash}' no longer exists."
            )
            continue
        try:
            if not cmds.isConnected(src, f"{stash}.{curve_attr}"):
                cmds.connectAttr(src, f"{stash}.{curve_attr}", force=True)
        except RuntimeError as e:
            warnings.append(f"Could not reconnect '{src}' -> '{stash}.{curve_attr}': {e}")

    connected_out = False
    for curve_attr, dst_ref in record.get("outputs", []):
        dst = resolve_plug(dst_ref)
        if not dst:
            warnings.append(
                f"Destination '{dst_ref.get('name')}.{dst_ref.get('attr')}' for "
                f"restored curve '{stash}' no longer exists."
            )
            continue
        try:
            if not cmds.isConnected(f"{stash}.{curve_attr}", dst):
                cmds.connectAttr(f"{stash}.{curve_attr}", dst, force=True)
            connected_out = True
        except RuntimeError as e:
            warnings.append(f"Could not reconnect '{stash}.{curve_attr}' -> '{dst}': {e}")

    if not connected_out and fallback_dst:
        try:
            cmds.connectAttr(f"{stash}.output", fallback_dst, force=True)
            connected_out = True
        except RuntimeError as e:
            warnings.append(
                f"Fallback reconnect '{stash}.output' -> '{fallback_dst}' failed: {e}"
            )

    return stash


def discard_stash(record: dict) -> None:
    """Delete a stash node that is no longer needed (bake was a no-op)."""
    stash = resolve_ref(record.get("stash"))
    if not stash:
        return
    try:
        cmds.lockNode(stash, lock=False)
        cmds.delete(stash)
    except RuntimeError:
        pass


def collect_upstream_curves(plug: str, passthrough_types: Set[str]) -> List[str]:
    """Return all animCurves feeding *plug*, traced through passthrough nodes.

    Walks source connections breadth-first: animCurves are collected
    (SDK curves, keys blending through a pairBlend, …); nodes whose type is
    in *passthrough_types* are traversed; anything else (constraints,
    expressions, motion paths) terminates the walk — those drivers survive
    bake and are handled by the connection snapshot instead.
    """
    found: List[str] = []
    visited: Set[str] = set()

    def _sources(node_or_plug: str) -> List[str]:
        return (
            cmds.listConnections(node_or_plug, source=True, destination=False) or []
        )

    frontier = _sources(plug)
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        node_type = cmds.nodeType(node)
        if node_type.startswith("animCurve"):
            found.append(node)
        elif node_type in passthrough_types:
            frontier.extend(_sources(node))
    return found


def snapshot_connections(plug: str) -> List[List[dict]]:
    """Record incoming connection pairs for *plug* (and its parent compound).

    animCurve sources are skipped — they are handled by the stash mechanism.
    unitConversion sources are traced through to the real driver plug.

    Returns:
        List of ``[src plug_ref, dst plug_ref]`` pairs.
    """
    pairs: List[List[dict]] = []
    plugs_to_check = [plug]

    node, _, attr = plug.partition(".")
    try:
        parents = cmds.attributeQuery(attr, node=node, listParent=True) or []
    except RuntimeError:
        parents = []  # alias attrs (e.g. blendShape weight names) can't be queried
    if parents:
        plugs_to_check.append(f"{node}.{parents[0]}")

    for p in plugs_to_check:
        conns = (
            cmds.listConnections(
                p, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        for i in range(0, len(conns), 2):
            dst = conns[i]
            src = _trace_out_of_conversion(conns[i + 1])
            src_node = src.partition(".")[0]
            if cmds.nodeType(src_node).startswith("animCurve"):
                continue
            pairs.append([plug_ref(src), plug_ref(dst)])
    return pairs


# ---------------------------------------------------------------------------
# Restore engine
# ---------------------------------------------------------------------------


@dataclass
class RestoreResult:
    """Result container for ``SmartBake.restore()``."""

    success: bool = False
    """True if the session was found, restorable, and processed."""

    session_id: Optional[str] = None
    """The session that was restored (or attempted)."""

    warnings: List[str] = field(default_factory=list)
    """Per-item issues (missing nodes, failed reconnects). Restore continues
    past these — they flag partial fidelity, not failure."""

    restored_layer: Optional[str] = None
    unmuted: List[str] = field(default_factory=list)
    reconnected: List[str] = field(default_factory=list)
    unstashed: List[str] = field(default_factory=list)
    visibility_restored: List[str] = field(default_factory=list)
    ik_restored: List[str] = field(default_factory=list)


def _delete_plug_curves(plug: str) -> int:
    """Delete animCurves directly connected to *plug*. Returns count."""
    curves = (
        cmds.listConnections(plug, source=True, destination=False, type="animCurve")
        or []
    )
    existing = [c for c in curves if cmds.objExists(c)]
    if existing:
        cmds.delete(existing)
    return len(existing)


def restore_session(session: dict) -> RestoreResult:
    """Reverse everything recorded in *session*. See module docstring."""
    result = RestoreResult(session_id=session.get("id"))

    if not session.get("restorable", True):
        msg = (
            f"Bake session '{result.session_id}' was destructive "
            "(delete_inputs=True) — driver nodes were deleted and cannot be "
            "rebuilt from the manifest."
        )
        backup = session.get("backup_path")
        if backup:
            msg += f" A pre-bake backup was saved: {backup}"
        result.warnings.append(msg)
        logger.warning(msg)
        return result

    # 1. Override layer — deleting it removes the baked layer curves and
    # blend nodes; Maya restores the direct driver connections.
    layer_ref = session.get("override_layer")
    if layer_ref:
        layer = resolve_ref(layer_ref)
        if layer and cmds.objExists(layer):
            cmds.delete(layer)
            result.restored_layer = layer
        else:
            result.warnings.append(
                f"Override layer '{layer_ref.get('name')}' not found — "
                "already deleted?"
            )

    # 2. Base-layer baked curves — delete before reconnecting so orphaned
    # baked curves don't linger after force-connects.
    for entry in session.get("baked_plugs", []):
        node = resolve_ref(entry.get("ref"))
        if not node:
            result.warnings.append(
                f"Baked object '{entry.get('ref', {}).get('name')}' not found."
            )
            continue
        for channel in entry.get("channels", []):
            _delete_plug_curves(f"{node}.{channel}")

    # 3. Reconnect the recorded driver network (constraints, expressions,
    # motion paths — anything bake disconnected).
    for src_ref, dst_ref in session.get("connections", []):
        src = resolve_plug(src_ref)
        dst = resolve_plug(dst_ref)
        if not src or not dst:
            missing = src_ref if not src else dst_ref
            result.warnings.append(
                f"Recorded connection endpoint "
                f"'{missing.get('name')}.{missing.get('attr')}' no longer exists."
            )
            continue
        try:
            if not cmds.isConnected(src, dst):
                cmds.connectAttr(src, dst, force=True)
            result.reconnected.append(f"{src} -> {dst}")
        except RuntimeError as e:
            result.warnings.append(f"Could not reconnect '{src}' -> '{dst}': {e}")

    # 4. Unstash curves (SDKs, pairBlend-fed key curves).
    for record in session.get("stashed_curves", []):
        restored = unstash_curve(record, warnings=result.warnings)
        if restored:
            result.unstashed.append(restored)

    # 5. Visibility.
    for entry in session.get("visibility", []):
        obj = resolve_ref(entry.get("object"))
        if not obj:
            result.warnings.append(
                f"Visibility object '{entry.get('object', {}).get('name')}' not found."
            )
            continue
        vis_plug = f"{obj}.visibility"
        _delete_plug_curves(vis_plug)
        stash_record = entry.get("stash")
        if stash_record:
            restored = unstash_curve(
                stash_record, warnings=result.warnings, fallback_dst=vis_plug
            )
            if restored:
                result.visibility_restored.append(obj)
        else:
            try:
                cmds.setAttr(vis_plug, entry.get("original_value", 1.0))
                result.visibility_restored.append(obj)
            except RuntimeError as e:
                result.warnings.append(f"Could not restore '{vis_plug}': {e}")

    # 6. IK handles — bakeResults(disableImplicitControl=True) zeroes
    # ikBlend even when baking to a layer; put it back.
    for entry in session.get("ik_handles", []):
        handle = resolve_ref(entry.get("ref"))
        if not handle:
            result.warnings.append(
                f"IK handle '{entry.get('ref', {}).get('name')}' not found."
            )
            continue
        try:
            if not entry.get("had_incoming", False):
                _delete_plug_curves(f"{handle}.ikBlend")
            cmds.setAttr(f"{handle}.ikBlend", entry.get("ik_blend", 1.0))
            result.ik_restored.append(handle)
        except RuntimeError as e:
            result.warnings.append(f"Could not restore ikBlend on '{handle}': {e}")

    # 7. Unmute drivers to their recorded states.
    for entry in session.get("muted_drivers", []):
        node = resolve_ref(entry.get("ref"))
        if not node:
            result.warnings.append(
                f"Muted driver '{entry.get('ref', {}).get('name')}' not found."
            )
            continue
        try:
            if cmds.attributeQuery("nodeState", node=node, exists=True):
                cmds.setAttr(f"{node}.nodeState", entry.get("prior_state", 0))
                result.unmuted.append(node)
        except RuntimeError as e:
            result.warnings.append(f"Could not unmute '{node}': {e}")

    result.success = True
    return result


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
