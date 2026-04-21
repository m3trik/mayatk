# !/usr/bin/python
# coding=utf-8
import logging

try:
    import pymel.core as pm
except ImportError:
    pm = None

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)


class DataNodes:
    """Manages the two shared scene data nodes.

    ``data_internal`` (network node) is the single source of truth.
    All tools write their attributes here.

    ``data_export`` (locked transform) is the FBX export surface.
    Attributes are exposed via Maya proxy attrs that alias back to
    ``data_internal``, providing zero-cost synchronisation through
    Maya's dependency graph.

    Usage::

        # Tool registers an attr (creates on both nodes + proxy link):
        DataNodes.mirror_attr("audio_trigger", attributeType="enum",
                              enumName="None", keyable=True)

        # Tool writes to internal — export follows automatically:
        cmds.setAttr("data_internal.audio_trigger", 2)
        assert cmds.getAttr("data_export.audio_trigger") == 2
    """

    INTERNAL = "data_internal"
    EXPORT = "data_export"

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
            pm.PyNode: The ``data_internal`` network node.
        """
        name = DataNodes.INTERNAL

        if pm.objExists(name):
            node = pm.PyNode(name)
        else:
            node = pm.createNode("network", name=name)

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
            pm.PyNode: The ``data_export`` transform.
        """
        name = DataNodes.EXPORT

        if pm.objExists(name):
            return pm.PyNode(name)

        node = pm.group(empty=True, name=name)
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
    # Attribute mirroring
    # ------------------------------------------------------------------

    @staticmethod
    def mirror_attr(attr_name, **add_attr_kwargs):
        """Ensure *attr_name* on ``data_internal`` with a proxy on ``data_export``.

        If the attribute already exists on both nodes with the proxy
        link, this is a no-op.

        Parameters:
            attr_name: Long name of the attribute.
            **add_attr_kwargs: Passed to ``cmds.addAttr`` when creating
                the attribute on ``data_internal`` (e.g.
                ``attributeType="enum"``, ``enumName="None"``).
        """
        internal = DataNodes.ensure_internal()
        export = DataNodes.ensure_export()
        internal_str = str(internal)
        export_str = str(export)

        # Create on internal if missing.
        if not cmds.attributeQuery(attr_name, node=internal_str, exists=True):
            cmds.addAttr(internal_str, longName=attr_name, **add_attr_kwargs)

        # Create proxy on export if missing.
        if not cmds.attributeQuery(attr_name, node=export_str, exists=True):
            cmds.addAttr(
                export_str,
                longName=attr_name,
                proxy=f"{internal_str}.{attr_name}",
            )

    # ------------------------------------------------------------------
    # Legacy migration
    # ------------------------------------------------------------------

    @staticmethod
    def migrate_legacy_carriers():
        """Migrate old ``audio_events*`` carrier transforms to the new nodes.

        Scans the scene for transforms that carry an ``audio_trigger``
        attribute but are not ``data_export``.  For each legacy carrier:

        1. Copies the ``audio_trigger`` enum definition to ``data_internal``.
        2. Reconnects any animation curves to ``data_internal.audio_trigger``.
        3. Copies ``audio_file_map`` and ``audio_manifest`` string attrs.
        4. Deletes the old carrier transform.

        Returns:
            list[str]: Names of carriers that were migrated.
        """
        if cmds is None:
            return []

        export_name = DataNodes.EXPORT
        migrated = []

        for node in cmds.ls(type="transform") or []:
            if node == export_name:
                continue
            if not cmds.attributeQuery("audio_trigger", node=node, exists=True):
                continue

            logger.info(
                "Migrating legacy carrier '%s' → '%s'", node, DataNodes.INTERNAL
            )

            # Ensure target nodes exist.
            internal = DataNodes.ensure_internal()
            internal_str = str(internal)

            # --- Enum definition ---
            raw = cmds.attributeQuery("audio_trigger", node=node, listEnum=True)
            enum_str = raw[0] if raw else "None"

            if not cmds.attributeQuery("audio_trigger", node=internal_str, exists=True):
                cmds.addAttr(
                    internal_str,
                    longName="audio_trigger",
                    attributeType="enum",
                    enumName=enum_str,
                    keyable=True,
                )
            else:
                # Update enum labels to include any from the legacy carrier.
                cmds.addAttr(
                    f"{internal_str}.audio_trigger", edit=True, enumName=enum_str
                )

            # --- Animation curves ---
            src_attr = f"{node}.audio_trigger"
            dst_attr = f"{internal_str}.audio_trigger"
            anim_curves = (
                cmds.listConnections(src_attr, type="animCurve", d=False) or []
            )
            for curve in anim_curves:
                # Disconnect from old, connect to new.
                cmds.disconnectAttr(f"{curve}.output", src_attr)
                try:
                    cmds.connectAttr(f"{curve}.output", dst_attr, force=True)
                except RuntimeError:
                    pass  # already connected

            # --- String attrs (file map, manifest) ---
            for str_attr in ("audio_file_map", "audio_manifest"):
                if cmds.attributeQuery(str_attr, node=node, exists=True):
                    val = cmds.getAttr(f"{node}.{str_attr}") or ""
                    if not cmds.attributeQuery(
                        str_attr, node=internal_str, exists=True
                    ):
                        cmds.addAttr(internal_str, longName=str_attr, dataType="string")
                    cmds.setAttr(f"{internal_str}.{str_attr}", val, type="string")

            # --- Ensure proxy on export node ---
            DataNodes.mirror_attr(
                "audio_trigger", attributeType="enum", enumName=enum_str, keyable=True
            )

            # Delete old carrier.
            # Unlock name first (carriers had lockName=True).
            cmds.lockNode(node, lock=False, lockName=False)
            cmds.delete(node)
            migrated.append(node)

        return migrated
