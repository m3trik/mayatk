# !/usr/bin/python
# coding=utf-8
import json
from typing import Optional


try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# from this package:
from mayatk.display_utils._display_utils import DisplayUtils


class DataNodes:
    """Manages the two shared scene data nodes.

    ``data_internal`` (network node) is the single source of truth for
    tool-authored state.  A ``network`` node never serialises into an FBX,
    so anything here persists with the scene but can't leak into exports.

    ``data_export`` (locked, hidden transform) is the FBX export surface —
    its attrs ride into the FBX as user properties.

    Two mechanisms, by the nature of the value:

    - :meth:`set_export_string` — regenerated-at-export artifacts (JSON
      manifests, wire strings) as plain string channels on ``data_export``.
    - :meth:`set_internal_string` — scene-persistent state that must never
      export (restore manifests, app state).

    A third mechanism (``mirror_attr`` — authored on ``data_internal`` with a
    Maya proxy aliasing it on ``data_export``) was retired once its only
    producer migrated to a regenerated export channel; old scenes carrying the
    proxy pair are healed by that producer (see
    ``AudioClips._drop_legacy_manifest_proxy``).
    """

    INTERNAL = "data_internal"
    EXPORT = "data_export"

    # Well-known export channels — plain string attrs on the export node, read
    # downstream (e.g. FbxUtils realizes `fbx_takes`; Unity reads `shot_metadata`).
    FBX_TAKES = "fbx_takes"
    SHOT_METADATA = "shot_metadata"

    _LOCATOR_ATTR = "data_export_locator"

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
        path** — the root-level node ``ensure_*`` creates — with ties broken
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

    @staticmethod
    def ensure_internal():
        """Get or create the shared network node. Idempotent.

        The node's name is locked to prevent accidental renaming.
        The node itself stays unlocked so tools can freely add and
        write attributes.

        Returns:
            str: Name of the ``data_internal`` network node.
        """
        name = DataNodes.INTERNAL

        node = DataNodes._resolve(name)
        if node is None:
            node = cmds.createNode("network", name=name)

        # Migrate: older scenes may have the node fully locked.
        node_str = str(node)
        if cmds.lockNode(node_str, q=True, lock=True)[0]:
            cmds.lockNode(node_str, lock=False)

        # Lock name only — prevents rename, keeps attrs writable.
        cmds.lockNode(node_str, lock=False, lockName=True)
        return node

    @staticmethod
    def ensure_export():
        """Get or create the shared FBX export transform. Idempotent.

        The node is a locked, hidden transform with a zero-scale
        locator shape to prevent deletion by *Optimize Scene Size*.
        All nine transform channels are locked and hidden, and the node is
        flagged ``hiddenInOutliner`` — it's pipeline plumbing, not user
        content, so it never draws an Outliner row (while staying fully
        selectable and exportable by script).

        Returns:
            str: Name of the ``data_export`` transform.
        """
        name = DataNodes.EXPORT

        node = DataNodes._resolve(name)
        if node is None:
            node = cmds.group(empty=True, name=name)
        node_str = str(node)

        # The full protection set is applied idempotently, so a pre-existing
        # plain transform (hand-authored, or adopted from an import) heals to
        # the same contract as a freshly created carrier — without the locator
        # shape *Optimize Scene Size* would still delete it as an "empty"
        # transform, which is the exact failure the shape exists to prevent.

        # Migrate: older scenes may have the node fully locked (attrs must
        # stay writable — same migration ensure_internal performs).
        if cmds.lockNode(node_str, q=True, lock=True)[0]:
            cmds.lockNode(node_str, lock=False)

        # Add protective locator shape (prevents Optimize Scene Size
        # from deleting this empty transform).
        shapes = cmds.listRelatives(node_str, shapes=True, fullPath=True) or []
        if not shapes:
            shape = cmds.createNode(
                "locator",
                name=f"{name}Shape",
                parent=node_str,
                skipSelect=True,
            )
            cmds.setAttr(f"{shape}.localScaleX", 0)
            cmds.setAttr(f"{shape}.localScaleY", 0)
            cmds.setAttr(f"{shape}.localScaleZ", 0)
            if not cmds.attributeQuery(
                DataNodes._LOCATOR_ATTR, node=shape, exists=True
            ):
                cmds.addAttr(shape, ln=DataNodes._LOCATOR_ATTR, at="bool", dv=True)
                cmds.setAttr(f"{shape}.{DataNodes._LOCATOR_ATTR}", True)

        # Lock and hide all transform channels.
        for attr in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
            "scaleX",
            "scaleY",
            "scaleZ",
        ):
            cmds.setAttr(
                f"{node_str}.{attr}", lock=True, keyable=False, channelBox=False
            )

        # Keep the carrier out of the Outliner entirely (transform + shape).
        DisplayUtils.set_hidden_in_outliner(node_str)

        # Lock name only.
        cmds.lockNode(node_str, lock=False, lockName=True)
        return node

    # ------------------------------------------------------------------
    # Node access (resolve without creating)
    # ------------------------------------------------------------------

    @staticmethod
    def get_internal_node(create: bool = True) -> Optional[str]:
        """The ``data_internal`` node (created when *create*), else ``None``.

        Mirror of ``btk.DataNodes.get_internal_node`` — the sanctioned way
        for a consumer to resolve the carrier without creating it (a reader
        must never leave a stray node behind in a scene that has no data).
        """
        if create:
            return DataNodes.ensure_internal()
        return DataNodes._resolve(DataNodes.INTERNAL) if cmds is not None else None

    @staticmethod
    def get_export_node(create: bool = True) -> Optional[str]:
        """The ``data_export`` node (created when *create*), else ``None``.

        Mirror of ``btk.DataNodes.get_export_node``. Consumers that fold the
        carrier into an export set resolve it here instead of hand-rolling
        ``cmds.ls`` — this is the one place that applies the duplicate-name
        tie-break (see :meth:`_resolve`).
        """
        if create:
            return DataNodes.ensure_export()
        return DataNodes._resolve(DataNodes.EXPORT) if cmds is not None else None

    # ------------------------------------------------------------------
    # String channels (plain attrs on either carrier)
    # ------------------------------------------------------------------

    @staticmethod
    def _set_string(name: str, attr: str, value: str) -> Optional[str]:
        """Shared write behind both carriers' public setters.

        Creates the carrier and attr on demand for a real *value*. An empty
        *value* clears the channel without creating anything: the attr is set
        to ``""`` when it already exists, and nothing is created otherwise —
        a producer can always clear without leaving an empty carrier behind
        (matching the blendertk mirror's ``_set_string`` on both carriers).

        Returns:
            str | None: The carrier node, or ``None`` when an empty *value*
            had nothing to clear.
        """
        if not value:
            node = DataNodes._resolve(name)
            if node is None or not cmds.attributeQuery(attr, node=node, exists=True):
                return None
            cmds.setAttr(f"{node}.{attr}", "", type="string")
            return node
        ensure = (
            DataNodes.ensure_internal
            if name == DataNodes.INTERNAL
            else DataNodes.ensure_export
        )
        node = str(ensure())
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr(f"{node}.{attr}", value, type="string")
        return node

    @staticmethod
    def _get_string(name: str, attr: str) -> Optional[str]:
        """Shared read behind both carriers' public getters — ``None`` when
        the carrier, the attr, or a value is absent (a cleared channel reads
        back as ``None``)."""
        if cmds is None:
            return None
        node = DataNodes._resolve(name)
        if node is None or not cmds.attributeQuery(attr, node=node, exists=True):
            return None
        return cmds.getAttr(f"{node}.{attr}") or None

    @staticmethod
    def set_internal_string(attr: str, value: str) -> Optional[str]:
        """Write *value* to a plain string attr on ``data_internal``.

        Carrier for tool-authored state that must persist with the scene but
        never ride into the FBX (``data_export`` attrs are exported as user
        properties; ``data_internal`` is not part of the export set).  Used
        e.g. by ``SmartBake`` for its restore manifest.  Empty-value clear
        semantics: see :meth:`_set_string`.

        Returns:
            str | None: Name of the ``data_internal`` node, or ``None`` when
            an empty *value* had nothing to clear.
        """
        return DataNodes._set_string(DataNodes.INTERNAL, attr, value)

    @staticmethod
    def get_internal_string(attr: str) -> Optional[str]:
        """Return the string value of an internal-node channel, or ``None``."""
        return DataNodes._get_string(DataNodes.INTERNAL, attr)

    @staticmethod
    def set_export_string(attr: str, value: str) -> Optional[str]:
        """Write *value* to a plain string attr on the export node.

        Generic carrier for export-time data (e.g. ``fbx_takes``,
        ``shot_metadata``).  These channels are regenerated export artifacts,
        not tool-authored state, so they live as plain attrs on ``data_export``
        rather than on the ``data_internal`` SSoT.  The value rides into the
        FBX as a user property.  Empty-value clear semantics: see
        :meth:`_set_string`.

        Returns:
            str | None: Name of the ``data_export`` node, or ``None`` when an
            empty *value* had nothing to clear.
        """
        return DataNodes._set_string(DataNodes.EXPORT, attr, value)

    @staticmethod
    def get_export_string(attr: str) -> Optional[str]:
        """Return the string value of an export-node channel, or ``None``."""
        return DataNodes._get_string(DataNodes.EXPORT, attr)

    @staticmethod
    def set_export_json(attr: str, payload) -> Optional[str]:
        """Publish *payload* as a JSON export channel — the one-call form of
        the producer publish/clear idiom (build manifest → empty? clear the
        channel : serialize and write).  A falsy *payload* clears the channel
        (never creating the carrier just to hold an empty manifest); anything
        else is ``json.dumps``-ed onto the channel.

        Returns:
            str | None: Name of the ``data_export`` node, or ``None`` when a
            clear had nothing to do.
        """
        return DataNodes.set_export_string(
            attr, json.dumps(payload) if payload else ""
        )

    # ------------------------------------------------------------------
    # Inspection — read every channel a scene actually carries
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(raw: str):
        """Parse *raw* as JSON, or return it unchanged when it isn't JSON.

        The channels are producer-owned JSON blobs (shot metadata, audio
        manifests, ``ShotStore.to_dict()`` …) but a few carry plain wire
        strings — best-effort decode keeps both readable in a dump.
        """
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    @staticmethod
    def dump(decode: bool = True) -> dict:
        """Return every tool-authored channel on both data nodes.

        Where :meth:`get_internal_string` / :meth:`get_export_string` read a
        single *known* channel, ``dump`` discovers whatever a scene actually
        carries — it reads every user-defined attribute off ``data_internal``
        and ``data_export`` and groups them by node::

            {
                "data_internal": {"shot_store": {...}, "audio_clip_voice": 1},
                "data_export":   {"fbx_takes": [...], "shot_metadata": {...}},
            }

        Most channels are producer-owned JSON strings (best-effort decoded);
        a few are plain values — e.g. the audio tool's per-track ``enum``
        attrs (``AudioClips.ensure_track_attr``) — and are returned as-is.
        New producer channels appear automatically (nothing is keyed to the
        well-known constants), which makes this the read side of the node for
        diagnostics and the primitive behind the "Scene Metadata" tool button.

        Parameters:
            decode: When True (default), *string* values that are valid JSON
                are parsed to their Python objects; non-JSON strings and
                non-string values are returned unchanged. When False, string
                values are the raw stored string.

        Returns:
            dict: ``{node_name: {attr: value}}``. A node absent from the
            scene contributes an empty dict; empty string channels are
            skipped.
        """
        result = {}
        for name in (DataNodes.INTERNAL, DataNodes.EXPORT):
            channels = {}
            node = DataNodes._resolve(name) if cmds is not None else None
            if node is not None:
                for attr in cmds.listAttr(node, userDefined=True) or []:
                    try:
                        value = cmds.getAttr(f"{node}.{attr}")
                    except (RuntimeError, ValueError):
                        continue  # message/connection-only or unreadable attr
                    if value is None:
                        continue
                    if isinstance(value, str):
                        if not value:
                            continue  # empty / cleared channel
                        value = DataNodes._decode(value) if decode else value
                    channels[attr] = value
            result[name] = channels
        return result

    @staticmethod
    def format_dump(decode: bool = True) -> str:
        """Pretty-printed JSON of :meth:`dump`, or ``""`` when nothing is stored.

        The one-call text form for both the console (``print(
        DataNodes.format_dump())``) and the viewer dialog. Returns an empty
        string when neither node carries any channel, so callers can treat a
        falsy result as "no scene data". ``default=str`` guards the rare
        non-JSON-native attr value (e.g. a matrix channel) against a
        serialization error.
        """
        data = DataNodes.dump(decode=decode)
        if not any(data.values()):
            return ""
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
