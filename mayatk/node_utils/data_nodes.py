# !/usr/bin/python
# coding=utf-8
"""The Maya scene store: two carrier nodes behind ``ptk.SceneStoreBase``.

Every tool-authored scene record (``ptk.SceneRecords``) is stored here as a
string attribute on one of two nodes, and nothing else about a record --
its key, version, encoding, clear semantics -- is decided in this module:

- ``data_internal`` (:attr:`ptk.Scope.PRIVATE`), a ``network`` node.  A
  network node never serialises into an FBX, so what lives here persists
  with the scene and cannot leak into a deliverable.
- ``data_export`` (:attr:`ptk.Scope.DELIVERABLE`), a locked, hidden
  transform.  Its attrs ride into the FBX as user properties -- the one
  in-band metadata surface every consumer reads.

Producers and consumers never call this class for a record: they go through
the record's declaration (``ptk.SceneRecords.LIGHTMAPS.load(DataNodes)``,
``ptk.ExportSnapshot.publish(DataNodes, {...})``) and this class only answers
``read`` / ``write`` / ``values`` per scope, plus the carrier lifecycle a Maya
scene needs (resolution of a duplicate short name, the protection set, and a
keep-alive input so no keyed attribute's curve is ever the carrier's last
input) and the crossings -- another scene's records meeting this scene's
(``ptk.RecordTransfer``): a hand-off's sidecar sections, and the carriers a
referenced module brings along when its reference is imported.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple


try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.display_utils._display_utils import DisplayUtils

_Scope = ptk.Scope
logger = logging.getLogger(__name__)


class DataNodes(ptk.SceneStoreBase):
    """The two shared scene data nodes, as a ``ptk.SceneStoreBase``.

    Blender's ``btk.DataNodes`` is the name-and-behavior mirror; the record
    layer above both is shared, so a producer ported across DCCs changes
    nothing but the store it is handed.
    """

    INTERNAL = "data_internal"
    EXPORT = "data_export"
    NAMES: Dict[ptk.Scope, str] = {_Scope.PRIVATE: INTERNAL, _Scope.DELIVERABLE: EXPORT}

    #: Record keys readers used to spell here; the declarations are
    #: ``ptk.SceneRecords`` and these are the same strings, not copies.
    FBX_TAKES = ptk.SceneRecords.FBX_TAKES.key
    SHOT_METADATA = ptk.SceneRecords.SHOTS.key

    _LOCATOR_ATTR = "data_export_locator"
    #: Message attr on ``data_internal`` fed by an undeletable default node.
    #: Maya deletes a ``network`` node when the source of its ONLY input
    #: connection is deleted -- measured 2026-09-18: a keyed audio-track enum
    #: whose animCurve was the carrier's sole input took the carrier, and every
    #: record on it, with it when that curve was cut. A permanent second input
    #: from ``time1`` makes the rule unreachable.
    _KEEP_ALIVE_ATTR = "keepAlive"
    _KEEP_ALIVE_SOURCE = "time1.message"

    # ------------------------------------------------------------------
    # Name resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(name: str) -> Optional[str]:
        """Canonical node for *name*, or ``None`` when absent.

        A duplicate short name (an imported second carrier parented under a
        group) makes every bare-name plug query ambiguous: ``attributeQuery``
        raises, ``setAttr`` raises, and ``getAttr`` silently returns a *list*
        of both values. The scene's canonical carrier is the **shallowest
        path** -- the root-level node ``ensure_*`` creates -- with ties broken
        lexically for determinism. Returns the bare name when it is unique so
        the public methods keep their stable short-name return values.
        """
        matches = cmds.ls(name, long=True) or []
        if not matches:
            return None
        if len(matches) == 1:
            return name
        return min(matches, key=lambda path: (path.count("|"), path))

    # ------------------------------------------------------------------
    # Node lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def ensure_internal(cls) -> str:
        """Get or create the shared network node. Idempotent.

        The node's name is locked to prevent accidental renaming; the node
        itself stays unlocked so records can be written.  Created, locked and
        kept alive outside the undo queue: the carrier outlives any one tool's
        edit (created inside a tool's undo chunk, an undo of that chunk deleted
        it with every record other tools had written outside the queue since
        -- measured 2026-09-15).  A tool's own record writes still undo.

        Returns:
            str: Name of the ``data_internal`` network node.
        """
        from mayatk.core_utils._core_utils import CoreUtils

        name = cls.INTERNAL
        with CoreUtils.undo_disabled():
            node = cls._resolve(name)
            if node is None:
                # skipSelect: a data node is bookkeeping -- created mid-tool (a
                # record, a manifest), it must never steal the selection.
                node = cmds.createNode("network", name=name, skipSelect=True)
            node_str = str(node)
            # Migrate: older scenes may have the node fully locked.
            if cmds.lockNode(node_str, q=True, lock=True)[0]:
                cmds.lockNode(node_str, lock=False)
            cls._ensure_keep_alive(node_str)
            # Lock name only -- prevents rename, keeps attrs writable.
            cmds.lockNode(node_str, lock=False, lockName=True)
        return node_str

    @classmethod
    def _ensure_keep_alive(cls, node: str) -> None:
        """Give *node* its permanent input from an undeletable default node."""
        if not cmds.attributeQuery(cls._KEEP_ALIVE_ATTR, node=node, exists=True):
            cmds.addAttr(node, longName=cls._KEEP_ALIVE_ATTR, attributeType="message")
        plug = f"{node}.{cls._KEEP_ALIVE_ATTR}"
        if not cmds.listConnections(plug, source=True, destination=False):
            cmds.connectAttr(cls._KEEP_ALIVE_SOURCE, plug, force=True)

    @classmethod
    def ensure_export(cls) -> str:
        """Get or create the shared FBX export transform. Idempotent.

        A locked, hidden transform with a zero-scale locator shape (so
        *Optimize Scene Size* never deletes it as an empty transform), all nine
        transform channels locked and hidden, flagged ``hiddenInOutliner`` on
        transform and shape -- pipeline plumbing, not user content, while
        staying fully selectable and exportable by script.  The protection set
        is applied idempotently, so a pre-existing plain transform (hand-made
        or imported) heals to the same contract.  Created outside the undo
        queue for the reason :meth:`ensure_internal` gives.

        Returns:
            str: Name of the ``data_export`` transform.
        """
        from mayatk.core_utils._core_utils import CoreUtils

        # The WHOLE ensure runs outside the undo queue, like ensure_internal's:
        # creating only the transform outside it left the locator shape and the
        # locks inside the caller's chunk, so an undo stripped the carrier's
        # protection (Optimize Scene Size could delete it; it drew an Outliner
        # row again) while the node itself survived.
        with CoreUtils.undo_disabled():
            return cls._ensure_export(cls.EXPORT)

    @classmethod
    def _ensure_export(cls, name: str) -> str:
        """:meth:`ensure_export`'s body; the caller holds the undo guard."""
        node = cls._resolve(name)
        if node is None:
            # skipSelect, as ensure_internal: ``group -empty`` selected the new
            # transform, so the first record a tool wrote took the selection.
            node = cmds.createNode("transform", name=name, skipSelect=True)
        node_str = str(node)

        # Migrate: older scenes may have the node fully locked (attrs must
        # stay writable -- same migration ensure_internal performs).
        if cmds.lockNode(node_str, q=True, lock=True)[0]:
            cmds.lockNode(node_str, lock=False)

        shapes = cmds.listRelatives(node_str, shapes=True, fullPath=True) or []
        if not shapes:
            shape = cmds.createNode(
                "locator", name=f"{name}Shape", parent=node_str, skipSelect=True
            )
            for axis in "XYZ":
                cmds.setAttr(f"{shape}.localScale{axis}", 0)
            if not cmds.attributeQuery(cls._LOCATOR_ATTR, node=shape, exists=True):
                cmds.addAttr(shape, ln=cls._LOCATOR_ATTR, at="bool", dv=True)
                cmds.setAttr(f"{shape}.{cls._LOCATOR_ATTR}", True)

        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                cmds.setAttr(
                    f"{node_str}.{attr}{axis}",
                    lock=True,
                    keyable=False,
                    channelBox=False,
                )

        # Keep the carrier out of the Outliner entirely (transform + shape).
        DisplayUtils.set_hidden_in_outliner(node_str)
        cmds.lockNode(node_str, lock=False, lockName=True)
        return node_str

    # ------------------------------------------------------------------
    # Node access (resolve without creating)
    # ------------------------------------------------------------------

    @classmethod
    def get_internal_node(cls, create: bool = True) -> Optional[str]:
        """The ``data_internal`` node (created when *create*), else ``None``.

        The sanctioned way to resolve the carrier without creating it -- a
        reader must never leave a stray node behind in a scene without data.
        """
        if create:
            return cls.ensure_internal()
        return cls._resolve(cls.INTERNAL) if cmds is not None else None

    @classmethod
    def get_export_node(cls, create: bool = True) -> Optional[str]:
        """The ``data_export`` node (created when *create*), else ``None``.

        The one place that applies the duplicate-name tie-break (see
        :meth:`_resolve`): a producer resolves WHERE TO WRITE here.
        """
        if create:
            return cls.ensure_export()
        return cls._resolve(cls.EXPORT) if cmds is not None else None

    @classmethod
    def get_export_nodes(cls) -> List[str]:
        """Every ``data_export`` carrier in the scene, canonical first (long paths).

        The plural of :meth:`get_export_node`, and a different question.
        Resolving *which carrier to write to* must collapse to exactly one (a
        duplicate short name makes every plug query ambiguous), but deciding
        *what to ship* must not: a referenced module publishes onto its own
        namespaced carrier, and ``cmds.ls("data_export")`` does not match
        ``NS:data_export`` at all -- so the single-carrier resolver reported
        "one carrier, and it is the root's" for a scene whose entire lightmap
        manifest lived on ``PROD_ROOM:data_export``, and a selection export
        shipped a GLB with no manifest that previewed UNLIT.

        Safe to ship several: the GLB reader resolves a channel by walking
        nodes for the key, and the conversion strips every node's FBX handoff
        block before writing its own.  Transforms only (never the locator
        shape), deduped, the scene's OWN carrier first: shallowest path, then
        un-namespaced before namespaced, then lexical -- the same order
        :meth:`get_export_node` picks, so the two agree on the canonical one.
        """
        if cmds is None:
            return []
        matches = (
            cmds.ls(
                cls.EXPORT,
                f"*:{cls.EXPORT}",
                long=True,
                recursive=True,
                type="transform",
            )
            or []
        )
        return sorted(
            set(matches),
            key=lambda path: (
                path.count("|"),
                ":" in path.rsplit("|", 1)[-1],
                path,
            ),
        )

    # ------------------------------------------------------------------
    # The store contract (ptk.SceneStoreBase)
    # ------------------------------------------------------------------

    @classmethod
    def _carrier(cls, scope: ptk.Scope, create: bool = False) -> Optional[str]:
        """The carrier node of *scope*, created on demand when *create*."""
        if cmds is None:
            return None
        if _Scope(scope) is _Scope.PRIVATE:
            return cls.get_internal_node(create)
        return cls.get_export_node(create)

    @classmethod
    def read(cls, scope: ptk.Scope, key: str) -> Optional[str]:
        """The string channel *key* in *scope*, or ``None`` when the carrier,
        the attr or a value is absent.  A cleared channel reads as ``None``;
        a non-string attribute (a keyed enum, a weight float) is not a
        channel and reads as ``None`` too."""
        node = cls._carrier(scope)
        if node is None or not cmds.attributeQuery(key, node=node, exists=True):
            return None
        try:
            value = cmds.getAttr(f"{node}.{key}")
        except (RuntimeError, ValueError):
            return None  # a message / connection-only attr is no channel
        return value if isinstance(value, str) and value else None

    @classmethod
    def write(cls, scope: ptk.Scope, key: str, text: Optional[str]) -> Optional[str]:
        """Store *text* on *key* in *scope*.

        Creates the carrier and the attr on demand for a real *text*.  An
        empty *text* CLEARS: the attr is set to ``""`` when it exists and
        nothing is created otherwise -- a record can always be cleared without
        leaving an empty carrier behind.

        Returns:
            str | None: The carrier node, or ``None`` when a clear had nothing
            to clear.
        """
        if not text:
            node = cls._carrier(scope)
            if node is None:
                return None
            if cls._drop_retired_proxy(node, key):
                return node  # the retired pair IS the record; now it is gone
            if not cmds.attributeQuery(key, node=node, exists=True):
                return None
            cmds.setAttr(f"{node}.{key}", "", type="string")
            return node
        node = str(cls._carrier(scope, create=True))
        cls._drop_retired_proxy(node, key)
        if not cmds.attributeQuery(key, node=node, exists=True):
            cmds.addAttr(node, longName=key, dataType="string")
        cmds.setAttr(f"{node}.{key}", text, type="string")
        return node

    @classmethod
    def _drop_retired_proxy(cls, node: str, key: str) -> bool:
        """Drop a channel still stored the retired ``mirror_attr`` way;
        return whether one was there.

        Before 2026-07 a record could be authored on ``data_internal`` and
        exposed on ``data_export`` as a Maya PROXY of it.  A plain string attr
        cannot replace a proxy in place, and a write through the proxy lands on
        its private source while the FBX exports the proxy ambiguously -- so a
        write that finds one deletes it, and its now-purposeless private
        source, first.  Every record path heals this way, the export pipeline
        included; a no-op on every current scene.
        """
        plug = f"{node}.{key}"
        if not cmds.attributeQuery(key, node=node, exists=True) or not cmds.addAttr(
            plug, query=True, usedAsProxy=True
        ):
            return False
        cmds.deleteAttr(plug)
        # The retired mechanism named the source like the channel.
        internal = cls._resolve(cls.INTERNAL)
        if internal and cmds.attributeQuery(key, node=internal, exists=True):
            cmds.deleteAttr(f"{internal}.{key}")
        return True

    @classmethod
    def values(cls, scope: ptk.Scope) -> Dict[str, object]:
        """Every user-defined attribute value on the carrier of *scope* --
        string records and the non-string attrs some tools key on it (the
        audio tool's per-track enums, the emissive-group weights).  Message
        and connection-only attrs (the keep-alive, a registry) are skipped."""
        node = cls._carrier(scope)
        return {} if node is None else cls._node_values(node)

    @classmethod
    def _node_values(cls, node: str) -> Dict[str, object]:
        """:meth:`values` for one named carrier node."""
        result: Dict[str, object] = {}
        for attr in cmds.listAttr(node, userDefined=True) or []:
            if attr == cls._KEEP_ALIVE_ATTR:
                continue
            try:
                value = cmds.getAttr(f"{node}.{attr}")
            except (RuntimeError, ValueError):
                continue  # message / connection-only or unreadable attr
            if value is not None:
                result[attr] = value
        return result

    @classmethod
    def dump_export_nodes(cls, decode: bool = True) -> Dict[str, Dict[str, object]]:
        """Every ``data_export`` carrier's channels, keyed by node (long path).

        The plural of :meth:`dump`'s deliverable slice, as :meth:`get_export_nodes`
        is of :meth:`get_export_node`: an export ships every carrier, a referenced
        module's ``NS:data_export`` included, and ``dump`` reads only the
        canonical one. Same value rules as ``dump``: cleared channels skipped,
        strings JSON-decoded when *decode*. Creates nothing.
        """
        return {
            node: cls._dumped(cls._node_values(node), decode)
            for node in cls.get_export_nodes()
        }

    # ------------------------------------------------------------------
    # Crossings -- another scene's records meeting this scene's
    # ------------------------------------------------------------------

    #: The record owners (``ptk.SceneStoreBase.OWNERS``: what each hook means
    #: and when it runs) -- resolved lazily, like ``FbxUtils.PRODUCERS``.
    OWNERS: Dict[str, Tuple[str, str]] = {
        ptk.SceneRecords.SHOT_STORE.key: (
            "mayatk.anim_utils.shots._shots",
            "ShotStore",
        ),
        ptk.SceneRecords.KEY_STASH.key: (
            "mayatk.anim_utils.key_stash._key_stash",
            "KeyStash",
        ),
        ptk.SceneRecords.SMART_BAKE_SESSIONS.key: (
            "mayatk.anim_utils.smart_bake.bake_session",
            "BakeSessionStore",
        ),
        ptk.SceneRecords.EMISSIVE_REGISTRY.key: (
            "mayatk.mat_utils.emissive_groups",
            "EmissiveGroups",
        ),
    }

    @classmethod
    def carriers_in(cls, namespace: str) -> Dict[ptk.Scope, str]:
        """The carriers a referenced module keeps under *namespace*
        (``NS:data_internal`` / ``NS:data_export``), by scope; a scope it has
        none of is left out.  Creates nothing.  What ``merge_carriers`` /
        ``discard_carriers`` settle once the reference is imported."""
        ns = str(namespace or "").strip(":")
        found: Dict[ptk.Scope, str] = {}
        if cmds is None or not ns:
            return found
        for scope, name in cls.NAMES.items():
            kind = "network" if _Scope(scope) is _Scope.PRIVATE else "transform"
            matches = cmds.ls(f"{ns}:{name}", long=True, type=kind) or []
            if matches:
                found[_Scope(scope)] = matches[0]
        return found

    # -- the carrier hooks of ``ptk.SceneStoreBase``'s crossings --------------

    @classmethod
    def _live_carriers(cls, carriers: Mapping[Any, str]) -> Dict[ptk.Scope, str]:
        """*carriers* (node names) that exist, by scope, as long names."""
        found: Dict[ptk.Scope, str] = {}
        if cmds is None:
            return found
        for scope, node in (carriers or {}).items():
            live = cmds.ls(str(node), long=True) if node else []
            if live:
                found[_Scope(scope)] = live[0]
        return found

    @classmethod
    def _foreign_carriers(cls, carriers: Mapping[Any, str]) -> Dict[ptk.Scope, str]:
        """:meth:`_live_carriers` less this scene's own carrier of a scope --
        an import that stripped the namespace made the module's the scene's
        carrier when the scene had none."""
        found: Dict[ptk.Scope, str] = {}
        for scope, node in cls._live_carriers(carriers).items():
            own = cls._carrier(scope)
            if own and node == (cmds.ls(own, long=True) or [None])[0]:
                continue
            found[scope] = node
        return found

    @classmethod
    def _carrier_values(cls, carrier: str) -> Dict[str, object]:
        return cls._node_values(carrier)

    @classmethod
    def _rederive(cls, specs, ctx) -> None:
        """Produce *specs* again from the merged scene (authoring context,
        ``FbxUtils.publish``) -- the other copy spelled names as its own
        scene did."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        try:
            FbxUtils.publish(
                FbxUtils.export_context(mode=ptk.ExportContext.AUTHORING), only=specs
            )
        except Exception as error:  # noqa: BLE001 - the merge stands without them
            logger.warning("Deliverable records not re-derived.", exc_info=True)
            ctx.note(f"Deliverable records were not produced again ({error}).")

    @classmethod
    def _delete_carrier(cls, carrier: str) -> None:
        """Delete another scene's carrier, its name unlocked first."""
        if not cmds.objExists(carrier):
            return
        try:
            cmds.lockNode(carrier, lock=False, lockName=False)
            cmds.delete(carrier)
        except RuntimeError:
            logger.warning("Could not delete the foreign carrier %s.", carrier)

    @classmethod
    def _crossing(cls):
        """One undo step for a settle."""
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.undo_chunk()

    #: Attribute types :meth:`_carry_attributes` can recreate on another node.
    _MOVABLE_TYPES = (
        "bool",
        "long",
        "short",
        "byte",
        "enum",
        "float",
        "double",
        "doubleLinear",
        "doubleAngle",
        "time",
    )

    @classmethod
    def _carry_attributes(cls, carrier: str, scope: ptk.Scope, ctx) -> None:
        """Move *carrier*'s non-record user attributes -- a keyed audio-track
        enum, a group's keyed weight -- to this scene's carrier of *scope*,
        connections included; a name this scene's carrier already has stays
        behind, noted.  String channels are records (merged by rule), a
        message attribute is its owner's to carry (:attr:`OWNERS`), and a
        compound's children travel with nothing -- none is movable alone."""
        attrs = []
        for attr in cmds.listAttr(carrier, userDefined=True) or []:
            if attr == cls._KEEP_ALIVE_ATTR or cmds.attributeQuery(
                attr, node=carrier, listParent=True
            ):
                continue
            try:
                kind = cmds.getAttr(f"{carrier}.{attr}", type=True)
            except (RuntimeError, ValueError):
                continue
            if kind in cls._MOVABLE_TYPES:
                attrs.append(attr)
        if not attrs:
            return
        target = str(cls._carrier(scope, create=True))
        for attr in attrs:
            if cmds.attributeQuery(attr, node=target, exists=True):
                ctx.note(
                    f"{cls.name(scope)}.{attr}: this scene's own was kept; the "
                    "other scene's was not moved."
                )
                continue
            cls._clone_attribute(carrier, target, attr)
            cmds.copyAttr(
                carrier, target, attribute=[attr], values=True, inConnections=True
            )

    @staticmethod
    def _clone_attribute(src: str, dst: str, attr: str) -> None:
        """Add *attr* to *dst* shaped like *src*'s: type, enum names, hard and
        soft range, default and keyability."""
        plug = f"{src}.{attr}"
        kind = cmds.getAttr(plug, type=True)
        kwargs: Dict[str, Any] = {"longName": attr, "attributeType": kind}
        if kind == "enum":
            kwargs["enumName"] = ":".join(
                cmds.attributeQuery(attr, node=src, listEnum=True) or []
            )
        for exists, value, key in (
            ("minExists", "minimum", "minValue"),
            ("maxExists", "maximum", "maxValue"),
            ("softMinExists", "softMin", "softMinValue"),
            ("softMaxExists", "softMax", "softMaxValue"),
        ):
            if cmds.attributeQuery(attr, node=src, **{exists: True}):
                kwargs[key] = cmds.attributeQuery(attr, node=src, **{value: True})[0]
        default = cmds.attributeQuery(attr, node=src, listDefault=True)
        if default:
            kwargs["defaultValue"] = default[0]
        kwargs["keyable"] = bool(cmds.getAttr(plug, keyable=True))
        cmds.addAttr(dst, **kwargs)

