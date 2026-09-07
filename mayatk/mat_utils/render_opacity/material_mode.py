# !/usr/bin/python
# coding=utf-8
"""Clean up what the retired viewport "material mode" left in a scene.

Until 2026-09-05 the render-effect channels could be shown live in Maya's
viewport by *material mode*: each object was moved onto a duplicate of its
material (``X_Fade`` / ``X_Highlight``), the duplicate's ShaderFX graph was
swapped where the effect needed a slot, and the transform's channel was
connected to the duplicate's plug. Every export then had to suspend those
bindings and put the authored values back so the FBX and the sidecar read the
scene as authored.

That mode is gone: it replaced the authored material -- the artist no longer
saw the real asset, every name-keyed consumer saw the copy, and the export
carried a restore step that existed only to undo the preview (the rule is now
root ``CLAUDE.md`` *Design* and ``CODE_STANDARD.md`` s13: previews overlay,
never replace; export is plug-and-play). Lookdev lives in the WebXR push,
which shows the deliverable itself.

What remains here is the migration for scenes saved with the preview on:
:meth:`OpacityMaterialMode.remove` restores the authored values from the
binding record the old mode kept on ``data_internal``, moves each object back
onto its original material, and deletes the orphaned duplicates. It runs from
``RenderEffects.remove`` and the panel's remove actions, so an old scene heals
the first time a channel is removed or re-created on it.
"""

import json
import re
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None
# From this package:
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils.render_opacity.channels import (
    CHANNELS,
    OPACITY,
    ChannelSpec,
    spec_for,
)


class OpacityMaterialMode(ptk.LoggingMixin):
    """Migration for the retired viewport material mode: :meth:`remove` only."""

    #: ``data_internal`` channel the old mode recorded every live binding on:
    #: ``{"<material>:<channel>": {"material", "object", "channel", "restore"}}``
    #: where ``restore`` maps each bound plug to its authored value.
    BINDINGS_CHANNEL = "render_effects_bindings"

    @staticmethod
    def _short(node: str) -> str:
        return node.split("|")[-1].split(":")[-1]

    @classmethod
    def _bindings(cls) -> Dict[str, Dict]:
        raw = DataNodes.get_internal_string(cls.BINDINGS_CHANNEL)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _save_bindings(cls, data: Dict[str, Dict]) -> None:
        DataNodes.set_internal_string(
            cls.BINDINGS_CHANNEL, json.dumps(data) if data else ""
        )

    @classmethod
    def _forget(cls, mat: str, spec: Optional[ChannelSpec] = None) -> None:
        data = cls._bindings()
        for key in list(data):
            entry = data[key]
            if entry.get("material") == cls._short(mat) and (
                spec is None or entry.get("channel") == spec.name
            ):
                del data[key]
        cls._save_bindings(data)

    @staticmethod
    def _leaf_plugs(plug: str) -> List[str]:
        """*plug* itself, or its children for a compound."""
        node, _, attr = plug.partition(".")
        children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
        return [f"{node}.{c}" for c in children] or [plug]

    @classmethod
    def _disconnect_inputs(cls, plug: str, source_node: Optional[str] = None) -> None:
        """Break every input into *plug* -- the plug ITSELF and its children.

        A colour was bound at the parent (``highlightColor -> emissive``) while
        a fan-out bound the children (``opacity -> opacityR/G/B``); walking only
        one level leaves the other connected, and a still-connected plug is
        not settable. *source_node* limits the break to connections from it.
        """
        for target in [plug] + [c for c in cls._leaf_plugs(plug) if c != plug]:
            for src in (
                cmds.listConnections(target, source=True, destination=False, plugs=True)
                or []
            ):
                if source_node is not None and src.split(".")[0] != source_node:
                    continue
                cmds.disconnectAttr(src, target)

    @classmethod
    def remove(cls, objects, spec: Optional[ChannelSpec] = None) -> List[str]:
        """Undo the old mode's work on *objects*' materials, for *spec* or every channel.

        For each material an object wears: break the channel connections the
        record names, write the authored values back, forget the record, then
        move the object from a suffixed duplicate back onto its original and
        delete the duplicate (and its shading group) once nothing uses it. A
        scene the old mode never touched is left exactly as found.

        Returns:
            The materials that carried a binding or were duplicates, short names.
        """
        specs = [spec_for(spec)] if spec is not None else list(CHANNELS.values())
        bindings = cls._bindings()
        touched: List[str] = []
        for obj in cmds.ls(objects):
            obj = str(obj)
            for mat in [
                str(m) for m in MatUtils.get_mats([obj], as_strings=True) or []
            ]:
                for one in specs:
                    entry = bindings.get(f"{cls._short(mat)}:{one.name}")
                    restore = (entry or {}).get("restore") or {}
                    for plug in restore:
                        if cmds.objExists(plug):
                            cls._disconnect_inputs(plug, source_node=obj)
                    for plug, value in restore.items():
                        if not cmds.objExists(plug):
                            continue
                        try:
                            Attributes.set_plug(
                                plug, tuple(value) if isinstance(value, list) else value
                            )
                        except Exception:
                            cls.logger.debug(
                                "Could not restore %s.", plug, exc_info=True
                            )
                    if entry is None and one is OPACITY:
                        # Pre-record opacity binding: the historical reset.
                        plug = f"{mat}.opacity"
                        if cmds.objExists(plug):
                            cls._disconnect_inputs(plug, source_node=obj)
                            if not cmds.getAttr(plug, lock=True):
                                try:
                                    cmds.setAttr(plug, 1.0)
                                except Exception:
                                    pass
                    if entry is not None:
                        cls._forget(mat, one)
                        touched.append(cls._short(mat))
                    if cls._reassign_original(obj, mat, one):
                        touched.append(cls._short(mat))
        return sorted(set(touched))

    @classmethod
    def _reassign_original(cls, obj: str, mat: str, spec: ChannelSpec) -> bool:
        """Move *obj* off a ``<original><suffix>`` duplicate; drop the orphan. True if it was one."""
        mat_name = cls._short(mat)
        if not spec.material_suffix:
            return False
        match = re.match(
            r"^(.+?)" + re.escape(spec.material_suffix) + r"\d*$", mat_name
        )
        if not match:
            return False
        original_name = match.group(1)
        if cmds.objExists(original_name):
            orig_sgs = cmds.listConnections(original_name, type="shadingEngine") or []
            if orig_sgs:
                cmds.sets(obj, edit=True, forceElement=orig_sgs[0])
                cls.logger.info(
                    f"Reassigned {obj} to original material '{original_name}'"
                )
        for sg in cmds.listConnections(mat, type="shadingEngine") or []:
            if not (cmds.sets(sg, q=True) or []):
                cmds.delete(sg)
        if cmds.objExists(mat) and not cmds.listConnections(mat, type="shadingEngine"):
            cmds.delete(mat)
            cls.logger.info(f"Deleted orphaned material: {mat_name}")
        return True
