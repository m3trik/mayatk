# !/usr/bin/python
# coding=utf-8
"""The viewport half of the render-effect channels: isolate a material, bind it.

Shows a channel live in the viewport by giving each object its own material
(duplicating one shared with anything outside the selection -- the Maya twin
of the GLB pass's clone) and connecting the transform's channel attribute to
the plug that shows it: ``opacity`` on the transparent StingrayPBS graph,
``emissive_intensity`` for a highlight, ``emission`` on a standardSurface.
Which plug, per shader type, is the channel's ``viewport`` row in
:mod:`~mayatk.mat_utils.render_opacity.channels`.

Every binding is recorded on the ``data_internal`` carrier, so an export can
put the material back the way it was authored for the duration of the write
and re-bind afterwards (:meth:`OpacityMaterialMode.suspend_for_export` /
:meth:`OpacityMaterialMode.resume_after_export`) -- measured 2026-09-04: a
driven ``emissive_intensity`` reached the FBX material and, through
FBX2glTF, the GLB's ``emissiveFactor``; a driven standardSurface ``emission``
reached the ``SceneState`` sidecar. The record survives a session, so a
scene saved with a preview on is still restorable at export.
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
from mayatk.mat_utils.mat_snapshot import MatSnapshot
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode
from mayatk.mat_utils.render_opacity.channels import (
    CHANNELS,
    OPACITY,
    ChannelSpec,
    ViewportBinding,
    spec_for,
)


class OpacityMaterialMode(ptk.LoggingMixin):
    """
    Implements the 'material' mode for the render-effect channels.

    This mode acts as a "Visual Attribute Mode":
    1. Adds the channel's attribute(s) to each object (via AttributeMode).
    2. Ensures each object has a unique material (to allow independent control).
    3. Connects Object.<channel> -> Material.<plug> for viewport feedback.
    """

    FADE_ATTRS = ("base_colorR", "base_colorG", "base_colorB", "opacity")
    """StingrayPBS attributes keyframed in ``"material"`` mode."""

    FADE_SUFFIX = OPACITY.material_suffix
    """Suffix appended to material names in ``"material"`` mode (opacity)."""

    #: ``data_internal`` channel recording every live viewport binding.
    BINDINGS_CHANNEL = "render_effects_bindings"

    @classmethod
    def get_stingray_mats(cls, objects: Optional[list] = None) -> list:
        """Return unique StingrayPBS materials assigned to *objects*."""
        return MatUtils.get_mats(objects, mat_type="StingrayPBS", as_strings=True)

    @classmethod
    def _bindable_mats(cls, objects, spec: ChannelSpec) -> Dict[str, List[str]]:
        """``{material: [object, ...]}`` for materials of a type the channel binds."""
        mat_map: Dict[str, List[str]] = {}
        for obj in cmds.ls(objects):
            try:
                mats = MatUtils.get_mats([obj], as_strings=True)
            except Exception:
                continue
            for m in mats:
                if cmds.nodeType(m) in spec.viewport:
                    mat_map.setdefault(str(m), []).append(str(obj))
        return mat_map

    @classmethod
    def create(cls, objects, spec: ChannelSpec = OPACITY) -> Dict[str, Dict]:
        """
        Bind the channel to each object's material for viewport feedback.
        Automatically handles material duplication if shared by unselected objects.
        """
        spec = spec_for(spec)
        objects = cmds.ls(objects)
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        results = {}
        mat_map = cls._bindable_mats(objects, spec)
        if not mat_map:
            cls.logger.warning(
                "No bindable materials (%s) found on selection.",
                ", ".join(sorted(spec.viewport)),
            )
            return {}

        for mat, targets in mat_map.items():
            binding = spec.viewport[cmds.nodeType(mat)]
            original_mat_name = cls._short(mat)
            # Snapshot texture paths and scalar values BEFORE any graph swap
            # (loadGraph destroys all external connections and resets attributes).
            snapshot = MatSnapshot.capture(original_mat_name, objects=targets)

            final_mat = cls._isolate_from_outsiders(mat, targets, spec)

            # Vital Step: Ensure the graph is loaded on the base material BEFORE
            # we potentially duplicate it further, so duplicates inherit it.
            if cls._ensure_stingray_graph(final_mat, binding):
                cls._expose_attributes(final_mat)
                # Restore textures and scalar values lost by loadGraph / duplicate.
                MatSnapshot.restore(
                    cls._short(final_mat), snapshot, source_mat_name=original_mat_name
                )
                cls.logger.info(f"Loaded ShaderFX graph on: {cls._short(final_mat)}")

            for i, target_obj in enumerate(targets):
                OpacityAttributeMode.create([target_obj], spec)
                if len(targets) > 1 and i > 0:
                    # Others get a fresh duplicate for independent control.
                    my_mat = cls._duplicate_for(final_mat, target_obj, spec)
                    MatSnapshot.restore(
                        cls._short(my_mat), snapshot, source_mat_name=original_mat_name
                    )
                else:
                    my_mat = final_mat
                cls._bind(target_obj, my_mat, spec, binding)

            results[cls._short(final_mat)] = {"status": "configured"}

        # Force Channel Box refresh if UI is active (without
        # changing the user's selection — that is a UI concern).
        if not cmds.about(batch=True):
            try:
                cmds.channelBox("mainChannelBox", edit=True, update=True)
            except Exception:
                pass

        return results

    # ------------------------------------------------------------------
    # Isolation
    # ------------------------------------------------------------------

    @staticmethod
    def _short(node: str) -> str:
        return node.split("|")[-1].split(":")[-1]

    @classmethod
    def _ensure_stingray_graph(cls, mat: str, binding: ViewportBinding) -> bool:
        """Make sure a StingrayPBS carries a graph that exposes the binding's plug.

        A StingrayPBS node's slots come from its ShaderFX graph, and a bare
        node (created in batch, or by ``duplicate`` of one) has none. Opacity
        needs the *transparent* graph specifically -- ``opacity`` is its slot
        alone. Every other channel binds a slot all three stock graphs share
        (``emissive_intensity``), so the node keeps whatever graph it has and a
        bare one gets the opaque ``Standard.sfx``.

        Returns:
            True when a graph was (re)loaded -- the caller must then restore
            the snapshot, because ``loadGraph`` drops every connection.
        """
        if cmds.nodeType(mat) != "StingrayPBS":
            return False
        if binding.graph == "transparent":
            return MatUtils.ensure_transparent_graph(mat)
        if cmds.attributeQuery(binding.plug, node=mat, exists=True):
            return False
        mode = MatUtils.get_stingray_opacity_mode(mat) or "none"
        return MatUtils.load_stingray_graph(mat, mode)

    @classmethod
    def _isolate_from_outsiders(
        cls, mat: str, targets: List[str], spec: ChannelSpec
    ) -> str:
        """Duplicate *mat* for *targets* when anything outside them shares it."""
        sgs = cmds.listConnections(mat, type="shadingEngine")
        if not sgs:
            return mat
        sg = sgs[0]
        members = cmds.ls(cmds.sets(sg, q=True) or [], flatten=True) or []
        member_transforms = set()
        for m in members:
            # Strip component suffix (.f[0], .vtx[3], …) to get the node
            node = str(m).split(".")[0]
            # ``isAType='shape'`` for the inheritance-aware check (concrete
            # shapes report 'mesh', 'nurbsSurface', ... as their objectType).
            if cmds.objectType(node, isAType="shape"):
                node = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[
                    0
                ]
            if node:
                member_transforms.add((cmds.ls(node, long=True) or [node])[0])
        target_transforms = set(cmds.ls(targets, long=True) or [])
        if member_transforms.issubset(target_transforms):
            return mat  # already exclusive to the selection
        final_mat = cls._duplicate_for(mat, targets, spec)
        cls.logger.info(
            f"Duplicated {cls._short(mat)} -> {cls._short(final_mat)} to isolate selection."
        )
        return final_mat

    @classmethod
    def _duplicate_for(cls, mat: str, targets, spec: ChannelSpec) -> str:
        """Duplicate *mat* (+ a shading group) and assign *targets* to it."""
        base_name = cls._short(mat)
        # Prevent recursive naming (Mat_Fade_Fade): an already-suffixed name
        # is duplicated as-is and Maya auto-increments it (Mat_Fade1).
        new_name = (
            base_name
            if base_name.endswith(spec.material_suffix)
            else f"{base_name}{spec.material_suffix}"
        )
        new_mat = cmds.duplicate(mat, name=new_name)[0]
        orig_sg = (cmds.listConnections(mat, type="shadingEngine") or [None])[0]
        sg_name = f"{cls._short(orig_sg)}_Copy" if orig_sg else f"{new_mat}SG"
        new_sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=sg_name
        )
        cmds.connectAttr(f"{new_mat}.outColor", f"{new_sg}.surfaceShader")
        # ``cmds.sets(forceElement=...)`` expects members positionally.
        for t in cmds.ls(targets):
            cmds.sets(t, edit=True, forceElement=new_sg)
        return new_mat

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------

    @classmethod
    def _bind(
        cls, obj: str, mat: str, spec: ChannelSpec, binding: ViewportBinding
    ) -> None:
        """Connect ``obj.<channel>`` (and its colour) to the material's plugs."""
        if not cmds.attributeQuery(binding.plug, node=mat, exists=True):
            cls.logger.warning(
                "%s has no %r plug (graph lacks the slot) -- %s not bound.",
                mat,
                binding.plug,
                spec.name,
            )
            return
        restore: Dict[str, object] = {}
        source = f"{obj}.{spec.name}"
        targets = (
            [f"{mat}.{binding.plug}{ch}" for ch in "RGB"]
            if binding.fan_out
            else [f"{mat}.{binding.plug}"]
        )
        restore[f"{mat}.{binding.plug}"] = cls._value(f"{mat}.{binding.plug}")
        for plug in targets:
            cls._connect(source, plug)
        if binding.color_plug and spec.color_attr:
            color_plug = f"{mat}.{binding.color_plug}"
            if cmds.attributeQuery(binding.color_plug, node=mat, exists=True):
                restore[color_plug] = cls._value(color_plug)
                cls._connect(f"{obj}.{spec.color_attr}", color_plug)
        if binding.toggle:
            attr, value = binding.toggle
            plug = f"{mat}.{attr}"
            if cmds.attributeQuery(attr, node=mat, exists=True):
                try:
                    if not cmds.getAttr(plug, lock=True):
                        restore[plug] = cls._value(plug)
                        cmds.setAttr(plug, value)
                except Exception:
                    pass
        cls._record(mat, obj, spec, restore)
        cls.logger.info(f"Connected {source} -> {mat}.{binding.plug}")

    @staticmethod
    def _value(plug: str):
        value = cmds.getAttr(plug)
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            return list(value[0])
        return value

    @staticmethod
    def _connect(source: str, plug: str) -> None:
        inputs = (
            cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        )
        for existing in inputs:
            if existing != source:
                cmds.disconnectAttr(existing, plug)
        if not cmds.isConnected(source, plug):
            cmds.connectAttr(source, plug, force=True)

    @classmethod
    def _expose_attributes(cls, mat):
        """Ensure standard opacity attributes are keyable."""
        for attr_name in ["opacity", "use_opacity_map"]:
            if cmds.attributeQuery(attr_name, node=mat, exists=True):
                try:
                    plug = f"{mat}.{attr_name}"
                    # Force unlock if locked (ShaderFX sometimes locks inputs)
                    if cmds.getAttr(plug, lock=True):
                        cmds.setAttr(plug, lock=False)
                    # Ensure it is keyable for the artist
                    cmds.setAttr(plug, keyable=True)
                except Exception as e:
                    cls.logger.warning(
                        f"Failed to expose attribute '{attr_name}' on {mat}: {e}"
                    )

    # ------------------------------------------------------------------
    # Binding record (data_internal) + export suspend / resume
    # ------------------------------------------------------------------

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
    def _record(
        cls, mat: str, obj: str, spec: ChannelSpec, restore: Dict[str, object]
    ) -> None:
        """Record a binding; an existing record's authored values WIN.

        A second ``preview()`` re-binds a plug that is already driven, so the
        value read off it is the preview's, not the artist's -- the first
        record is the one taken before anything was connected.
        """
        data = cls._bindings()
        key = f"{cls._short(mat)}:{spec.name}"
        previous = (data.get(key) or {}).get("restore") or {}
        data[key] = {
            "material": cls._short(mat),
            "object": cls._short(obj),
            "channel": spec.name,
            "restore": {**restore, **previous},
        }
        cls._save_bindings(data)

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

    @classmethod
    def suspend_for_export(cls) -> List[str]:
        """Put every bound material back to its authored values for an export.

        Disconnects the channel plugs and writes the recorded authored values,
        so the FBX material and the ``SceneState`` sidecar read what the artist
        authored rather than the preview's current frame. The record stays,
        so :meth:`resume_after_export` can re-bind.

        Returns:
            The materials suspended.
        """
        suspended: List[str] = []
        for entry in cls._bindings().values():
            mat = entry.get("material")
            if not mat or not cmds.objExists(mat):
                continue
            for plug, value in (entry.get("restore") or {}).items():
                if not cmds.objExists(plug):
                    continue
                cls._disconnect_inputs(plug)
                try:
                    Attributes.set_plug(
                        plug, tuple(value) if isinstance(value, list) else value
                    )
                except Exception:
                    cls.logger.debug(
                        "Could not restore %s for export.", plug, exc_info=True
                    )
            suspended.append(mat)
        if suspended:
            cls.logger.info(
                "Render effects: %d viewport binding(s) suspended for export.",
                len(suspended),
            )
        return suspended

    @classmethod
    def resume_after_export(cls) -> List[str]:
        """Re-bind every recorded viewport binding after an export."""
        resumed: List[str] = []
        for entry in cls._bindings().values():
            mat, obj, channel = (
                entry.get("material"),
                entry.get("object"),
                entry.get("channel"),
            )
            if not (mat and obj and channel in CHANNELS):
                continue
            if not (cmds.objExists(mat) and cmds.objExists(obj)):
                continue
            spec = CHANNELS[channel]
            binding = spec.viewport.get(cmds.nodeType(mat))
            if binding is None or not OpacityAttributeMode.has_channel(obj, spec):
                continue
            # Re-connect without re-recording: the authored values are the
            # ones already on record.
            source = f"{obj}.{spec.name}"
            targets = (
                [f"{mat}.{binding.plug}{ch}" for ch in "RGB"]
                if binding.fan_out
                else [f"{mat}.{binding.plug}"]
            )
            for plug in targets:
                if cmds.objExists(plug):
                    cls._connect(source, plug)
            if binding.color_plug and spec.color_attr:
                color_plug = f"{mat}.{binding.color_plug}"
                if cmds.objExists(color_plug):
                    cls._connect(f"{obj}.{spec.color_attr}", color_plug)
            if binding.toggle:
                attr, value = binding.toggle
                plug = f"{mat}.{attr}"
                if cmds.objExists(plug) and not cmds.getAttr(plug, lock=True):
                    cmds.setAttr(plug, value)
            resumed.append(mat)
        return resumed

    @staticmethod
    def _leaf_plugs(plug: str) -> List[str]:
        """*plug* itself, or its children for a compound."""
        node, _, attr = plug.partition(".")
        children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
        return [f"{node}.{c}" for c in children] or [plug]

    @classmethod
    def _disconnect_inputs(cls, plug: str, source_node: Optional[str] = None) -> None:
        """Break every input into *plug* -- the plug ITSELF and its children.

        A colour is bound at the parent (``highlightColor -> emissive``) while
        a fan-out binds the children (``opacity -> opacityR/G/B``); walking only
        one level leaves the other connected, and a still-connected plug is
        not settable -- measured 2026-09-04: the restore skipped silently and
        the preview colour shipped in the deliverable. *source_node* limits
        the break to connections from that node.
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
    def ensure_connections(cls, objects) -> None:
        """Re-establish ``Transform.<channel> → Material.<plug>`` proxy
        connections that were lost (e.g. after a duplicate operation).

        Only attempts reconnection when the object has the channel attribute
        and is assigned a material of a type the channel binds that exposes
        the plug and is not already driven by another object.
        """
        for obj in cmds.ls(objects):
            for spec in OpacityAttributeMode.channels_on(obj):
                for mat in MatUtils.get_mats([obj], as_strings=True) or []:
                    binding = spec.viewport.get(cmds.nodeType(mat))
                    if binding is None or binding.fan_out:
                        continue
                    plug = f"{mat}.{binding.plug}"
                    if not cmds.attributeQuery(binding.plug, node=mat, exists=True):
                        continue
                    source = f"{obj}.{spec.name}"
                    if cmds.isConnected(source, plug):
                        continue
                    # Driven by another object: skip to avoid stealing.
                    if cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    ):
                        continue
                    # Only re-bind materials this mode set up (on record).
                    if f"{cls._short(mat)}:{spec.name}" not in cls._bindings():
                        continue
                    cmds.connectAttr(source, plug, force=True)
                    cls.logger.info(f"Reconnected {source} -> {plug}")

    @classmethod
    def remove(cls, objects, spec: Optional[ChannelSpec] = None):
        """Remove material-mode artifacts from *objects*.

        - Disconnects ``Transform.<channel>`` → ``Material.<plug>`` proxies and
          restores the authored values.
        - Reassigns objects from suffixed duplicates back to originals.
        - Deletes orphaned duplicate materials and their shading groups.

        .. note:: The Standard_Transparent graph is **not** reverted.
           Reverting a ShaderFX graph risks data-loss and shader instability.
        """
        specs = [spec_for(spec)] if spec is not None else list(CHANNELS.values())
        bindings = cls._bindings()
        for obj in cmds.ls(objects):
            obj = str(obj)
            for mat in [
                str(m) for m in MatUtils.get_mats([obj], as_strings=True) or []
            ]:
                for one in specs:
                    key = f"{cls._short(mat)}:{one.name}"
                    entry = bindings.get(key)
                    binding = one.viewport.get(cmds.nodeType(mat))
                    if binding is None:
                        continue
                    # 1. Disconnect proxy + restore authored values
                    for plug in [f"{mat}.{binding.plug}"] + (
                        [f"{mat}.{binding.color_plug}"] if binding.color_plug else []
                    ):
                        if cmds.objExists(plug):
                            cls._disconnect_inputs(plug, source_node=obj)
                    for plug, value in ((entry or {}).get("restore") or {}).items():
                        if cmds.objExists(plug):
                            try:
                                Attributes.set_plug(
                                    plug,
                                    tuple(value) if isinstance(value, list) else value,
                                )
                            except Exception:
                                pass
                    if entry is None and one is OPACITY:
                        # Legacy (pre-record) opacity binding: the historical reset.
                        plug = f"{mat}.opacity"
                        if cmds.objExists(plug) and not cmds.getAttr(plug, lock=True):
                            try:
                                cmds.setAttr(plug, 1.0)
                            except Exception:
                                pass
                    cls._forget(mat, one)
                    # 2. If a suffixed duplicate, reassign to original & clean up
                    cls._reassign_original(obj, mat, one)

    @classmethod
    def _reassign_original(cls, obj: str, mat: str, spec: ChannelSpec) -> None:
        mat_name = cls._short(mat)
        match = re.match(
            r"^(.+?)" + re.escape(spec.material_suffix) + r"\d*$", mat_name
        )
        if not match or not spec.material_suffix:
            return
        original_name = match.group(1)
        if cmds.objExists(original_name):
            orig_sgs = cmds.listConnections(original_name, type="shadingEngine") or []
            if orig_sgs:
                cmds.sets(obj, edit=True, forceElement=orig_sgs[0])
                cls.logger.info(
                    f"Reassigned {obj} to original material '{original_name}'"
                )
        # Delete orphaned duplicate SG + material
        for sg in cmds.listConnections(mat, type="shadingEngine") or []:
            if not (cmds.sets(sg, q=True) or []):
                cmds.delete(sg)
                cls.logger.info(f"Deleted orphaned SG: {sg}")
        if cmds.objExists(mat) and not cmds.listConnections(mat, type="shadingEngine"):
            cmds.delete(mat)
            cls.logger.info(f"Deleted orphaned material: {mat_name}")
