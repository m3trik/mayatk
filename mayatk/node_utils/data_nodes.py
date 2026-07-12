# !/usr/bin/python
# coding=utf-8
import json
from typing import Optional


try:
    import maya.cmds as cmds
except ImportError:
    cmds = None


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

        if cmds.objExists(name):
            node = name
        else:
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
        All nine transform channels are locked and hidden.

        Returns:
            str: Name of the ``data_export`` transform.
        """
        name = DataNodes.EXPORT

        if cmds.objExists(name):
            return name

        node = cmds.group(empty=True, name=name)
        node_str = str(node)

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

        # Lock name only.
        cmds.lockNode(node_str, lock=False, lockName=True)
        return node

    # ------------------------------------------------------------------
    # Internal string channels (plain attrs on the internal node)
    # ------------------------------------------------------------------

    @staticmethod
    def set_internal_string(attr: str, value: str) -> str:
        """Write *value* to a plain string attr on ``data_internal`` (create if needed).

        Carrier for tool-authored state that must persist with the scene but
        never ride into the FBX (``data_export`` attrs are exported as user
        properties; ``data_internal`` is not part of the export set).  Used
        e.g. by ``SmartBake`` for its restore manifest.

        Returns:
            str: Name of the ``data_internal`` node.
        """
        internal = str(DataNodes.ensure_internal())
        if not cmds.attributeQuery(attr, node=internal, exists=True):
            cmds.addAttr(internal, longName=attr, dataType="string")
        cmds.setAttr(f"{internal}.{attr}", value, type="string")
        return internal

    @staticmethod
    def get_internal_string(attr: str) -> Optional[str]:
        """Return the string value of an internal-node channel, or ``None``."""
        if cmds is None or not cmds.objExists(DataNodes.INTERNAL):
            return None
        node = DataNodes.INTERNAL
        if not cmds.attributeQuery(attr, node=node, exists=True):
            return None
        return cmds.getAttr(f"{node}.{attr}") or None

    # ------------------------------------------------------------------
    # Export string channels (plain attrs on the export node)
    # ------------------------------------------------------------------

    @staticmethod
    def set_export_string(attr: str, value: str) -> Optional[str]:
        """Write *value* to a plain string attr on the export node (create if needed).

        Generic carrier for export-time data (e.g. ``fbx_takes``,
        ``shot_metadata``).  These channels are regenerated export artifacts,
        not tool-authored state, so they live as plain attrs on ``data_export``
        rather than on the ``data_internal`` SSoT.  The value rides into the
        FBX as a user property.

        An empty *value* clears the channel without creating the carrier just
        to hold an empty manifest (matching the blendertk mirror): the attr is
        set to ``""`` when it already exists, and nothing is created otherwise.

        Returns:
            str | None: Name of the ``data_export`` node, or ``None`` when an
            empty *value* had nothing to clear.
        """
        if not value:
            if not cmds.objExists(DataNodes.EXPORT) or not cmds.attributeQuery(
                attr, node=DataNodes.EXPORT, exists=True
            ):
                return None
            cmds.setAttr(f"{DataNodes.EXPORT}.{attr}", "", type="string")
            return DataNodes.EXPORT
        export = str(DataNodes.ensure_export())
        if not cmds.attributeQuery(attr, node=export, exists=True):
            cmds.addAttr(export, longName=attr, dataType="string")
        cmds.setAttr(f"{export}.{attr}", value, type="string")
        return export

    @staticmethod
    def get_export_string(attr: str) -> Optional[str]:
        """Return the string value of an export-node channel, or ``None``."""
        if cmds is None or not cmds.objExists(DataNodes.EXPORT):
            return None
        node = DataNodes.EXPORT
        if not cmds.attributeQuery(attr, node=node, exists=True):
            return None
        return cmds.getAttr(f"{node}.{attr}") or None

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
        for node in (DataNodes.INTERNAL, DataNodes.EXPORT):
            channels = {}
            if cmds is not None and cmds.objExists(node):
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
            result[node] = channels
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
