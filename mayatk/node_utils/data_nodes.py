# !/usr/bin/python
# coding=utf-8
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

    Three mechanisms, by the nature of the value:

    - :meth:`set_export_string` — regenerated-at-export artifacts (JSON
      manifests, wire strings) as plain string channels on ``data_export``.
    - :meth:`set_internal_string` — scene-persistent state that must never
      export (restore manifests, app state).
    - :meth:`mirror_attr` — authored, edited-over-time values: the attr
      lives on ``data_internal`` with a Maya proxy on ``data_export`` that
      aliases it (zero-cost sync through the dependency graph)::

        DataNodes.mirror_attr("my_flag", attributeType="enum",
                              enumName="off:on", keyable=True)
        cmds.setAttr("data_internal.my_flag", 1)      # author here
        assert cmds.getAttr("data_export.my_flag") == 1
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
        ``shot_metadata``).  Unlike :meth:`mirror_attr`, this is a plain attr on
        ``data_export`` — these channels are regenerated export artifacts, not
        tool-authored state, so they don't belong on the ``data_internal`` SSoT.
        The value rides into the FBX as a user property.

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
