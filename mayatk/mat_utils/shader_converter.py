# !/usr/bin/python
# coding=utf-8
"""Retype a material in place — legacy Maya shaders to an exportable PBR one.

A blinn/lambert/phong built for a viewport look does not survive an FBX export
into a game engine: the classic shaders carry no metal/rough channels and their
transparency is the inverse of the opacity every PBR shader expects. Converting
by hand means rebuilding the network and re-assigning every face.

This walks the source shader's DECLARED channels (``ShaderAttributeMap`` is the
SSoT for what each shader type calls each logical channel), traces what actually
drives them, builds the target shader, and moves the shading-group membership
across so the geometry keeps its assignment.
"""
from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class _ShaderConverterInternal(object):
    """Internal helpers for :class:`ShaderConverter`."""

    # Nodes a texture is routinely routed THROUGH on its way to a classic
    # shader. Tracing stops at the file node behind them, because the target's
    # own slot wants the raw texture: a StingrayPBS `TEX_normal_map` reads the
    # normal map directly, so carrying blinn's bump2d across would double the
    # conversion.
    PASSTHROUGH_NODES = ("bump2d", "reverse", "luminance", "gammaCorrect")

    @staticmethod
    def _slot_plugs(shader: str, attr: str) -> List[str]:
        """Source plugs driving ``shader.attr`` — the parent's, else children's.

        ``listConnections`` on a compound PARENT reports nothing when only the
        R/G/B children are driven, which is exactly how a scalar gets broadcast
        into a float3 slot.
        """
        if not cmds.attributeQuery(attr, node=shader, exists=True):
            return []
        plugs = (
            cmds.listConnections(
                f"{shader}.{attr}", source=True, destination=False, plugs=True
            )
            or []
        )
        if plugs:
            return plugs
        for suffix in ("R", "G", "B", "X", "Y", "Z"):
            child = f"{attr}{suffix}"
            if not cmds.attributeQuery(child, node=shader, exists=True):
                continue
            plugs = (
                cmds.listConnections(
                    f"{shader}.{child}", source=True, destination=False, plugs=True
                )
                or []
            )
            if plugs:
                return plugs
        return []

    @classmethod
    def _trace_file_node(cls, plug: str) -> Optional[str]:
        """The file node behind *plug*, through any intermediate utility nodes.

        ``blinn.normalCamera`` is driven by a ``bump2d``, never by the file
        directly, so a naive one-hop read finds a utility node and gives up.
        """
        node = plug.split(".")[0]
        if cmds.nodeType(node) == "file":
            return node
        if cmds.nodeType(node) not in cls.PASSTHROUGH_NODES:
            return None
        for candidate in cmds.listHistory(node) or []:
            if cmds.nodeType(candidate) == "file":
                return candidate
        return None

    @staticmethod
    def _constant_value(shader: str, attr: str) -> Optional[Any]:
        """The literal value on an undriven slot, or None if it can't be read."""
        try:
            value = cmds.getAttr(f"{shader}.{attr}")
        except (RuntimeError, ValueError):
            return None
        # getAttr returns [(r, g, b)] for a float3; unwrap the outer list.
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], tuple):
            return value[0]
        return value


class ShaderConverter(ptk.LoggingMixin, _ShaderConverterInternal):
    """Convert materials between shader types, preserving textures and assignments."""

    # Target names accepted by :meth:`convert`, mapped to Maya node types.
    TARGETS = {
        "stingray": "StingrayPBS",
        "standard_surface": "standardSurface",
        "open_pbr": "openPBRSurface",
    }

    # Node types this can read a network off. The PBR trio is included so a
    # standardSurface -> StingrayPBS retype works as well as the legacy case.
    CONVERTIBLE = tuple(ShaderAttributeMap.SHADER_ATTRS)

    @classmethod
    def read_channels(cls, shader: str) -> Dict[str, Dict[str, Any]]:
        """What drives each logical channel of *shader*.

        Parameters:
            shader (str): Source material.

        Returns:
            dict: ``{logical: {"file": node|None, "value": literal|None}}`` for
            every channel the shader's type declares. A channel with neither a
            file nor a value is omitted.
        """
        shader = str(shader)
        node_type = cmds.nodeType(shader)
        attrs = ShaderAttributeMap.SHADER_ATTRS.get(node_type)
        if not attrs:
            return {}

        channels: Dict[str, Dict[str, Any]] = {}
        for logical in ShaderAttributeMap.logical_channels():
            slot = getattr(attrs, logical)
            if not slot:
                continue
            attr = slot[0]

            file_node = None
            for plug in cls._slot_plugs(shader, attr):
                file_node = cls._trace_file_node(plug)
                if file_node:
                    break

            value = None if file_node else cls._constant_value(shader, attr)
            if file_node or value is not None:
                channels[logical] = {"file": file_node, "value": value}
        return channels

    @classmethod
    def _resolve_opacity_mode(
        cls, channels: Dict[str, Dict[str, Any]], opacity_mode: Optional[str]
    ) -> Optional[str]:
        """Pick the StingrayPBS graph when the caller left it open.

        A material that carries an opacity channel needs a graph that HAS an
        opacity slot; ``Standard.sfx`` has none, so defaulting to it would drop
        the channel silently. ``masked`` is the default for a converted
        material because the legacy setups this targets are cutouts (decals,
        foliage) far more often than they are blended glass.
        """
        if opacity_mode is not None:
            return opacity_mode
        return "masked" if "opacity" in channels else "none"

    @classmethod
    @CoreUtils.undoable
    def convert(
        cls,
        materials=None,
        target: str = "stingray",
        opacity_mode: str = None,
        delete_source: bool = True,
        name_suffix: str = "",
        verbose: bool = False,
    ) -> Dict[str, Optional[str]]:
        """Retype *materials*, keeping their textures and geometry assignments.

        Parameters:
            materials: Materials **or** objects (objects resolve to their
                materials). None uses the current selection.
            target (str): One of :attr:`TARGETS` — ``"stingray"``,
                ``"standard_surface"`` or ``"open_pbr"``.
            opacity_mode (str, optional): StingrayPBS only. ``"masked"``
                (alpha cutout), ``"transparent"`` (alpha blend) or ``"none"``.
                Left None, a material with an opacity channel gets ``"masked"``
                and one without gets ``"none"``.
            delete_source (bool): Delete the source shader once its geometry has
                been re-assigned. False leaves it orphaned in the scene.
            name_suffix (str): Appended to the new shader's name. Empty reuses
                the source name, which Maya keeps unique on its own once the
                source is deleted.
            verbose (bool): Log per-channel detail.

        Returns:
            dict: ``{source_material: new_material | None}``. None means the
            material was skipped; the reason is logged.
        """
        import logging

        cls.set_log_level(logging.INFO if verbose else logging.WARNING)

        node_type = cls.TARGETS.get(target)
        if not node_type:
            raise ValueError(
                f"Unknown target '{target}'. Expected one of {sorted(cls.TARGETS)}."
            )

        results: Dict[str, Optional[str]] = {}
        for mat in MatUtils.get_mats(materials, as_strings=True) or []:
            mat = str(mat)
            source_type = cmds.nodeType(mat)
            short = CoreUtils.short_name(mat)

            if source_type == node_type:
                cls.logger.info(f"{short}: already {node_type} — skipped.")
                results[mat] = None
                continue
            if source_type not in cls.CONVERTIBLE:
                cls.logger.warning(
                    f"{short}: '{source_type}' has no channel declaration — skipped."
                )
                results[mat] = None
                continue

            channels = cls.read_channels(mat)
            if not channels:
                cls.logger.warning(f"{short}: nothing to carry over — skipped.")
                results[mat] = None
                continue

            # Built under a scratch name: the source still holds the real one,
            # so creating with it directly would have Maya uniquify (blinn1 ->
            # blinn2) and the converted material would keep the ugly name even
            # after the source is gone.
            new_mat = cls._build_target(
                f"{short}_CONVERTING", node_type, channels, opacity_mode
            )
            cls._apply_channels(new_mat, channels, verbose=verbose)
            cls._transfer_assignments(mat, new_mat)

            if delete_source and cmds.objExists(mat):
                cmds.delete(mat)
            new_mat = cls._claim_name(new_mat, short + name_suffix)

            cls.logger.info(
                f"{short} ({source_type}) -> {CoreUtils.short_name(new_mat)} "
                f"({node_type}): {len(channels)} channel(s)"
            )
            results[mat] = new_mat

        return results

    @staticmethod
    def _claim_name(shader: str, name: str) -> str:
        """Rename *shader* to *name*, keeping its shading group in step.

        Maya uniquifies on collision, so this is safe whether or not the source
        released the name; the result is whatever it actually landed on.
        """
        renamed = cmds.rename(shader, name)
        for sg in cmds.listConnections(renamed, type="shadingEngine") or []:
            if sg.endswith("SG"):
                cmds.rename(sg, f"{renamed}SG")
        return renamed

    @classmethod
    def _build_target(
        cls,
        name: str,
        node_type: str,
        channels: Dict[str, Dict[str, Any]],
        opacity_mode: Optional[str],
    ) -> str:
        """Create the target shader with a graph that exposes what's needed."""
        if node_type == "StingrayPBS":
            # Graph choice decides which slots EXIST, so it has to be made
            # before anything is wired.
            return MatUtils.create_stingray_shader(
                name, opacity_mode=cls._resolve_opacity_mode(channels, opacity_mode)
            )

        shader = cmds.shadingNode(node_type, asShader=True, name=name)
        if "opacity" in channels and cmds.attributeQuery(
            "thinWalled", node=shader, exists=True
        ):
            # Cutout/foliage behavior, not glass — matches GameShader's setup.
            cmds.setAttr(f"{shader}.thinWalled", True)
        return shader

    @classmethod
    def _apply_channels(
        cls, shader: str, channels: Dict[str, Dict[str, Any]], verbose: bool = False
    ) -> List[str]:
        """Wire each carried channel into *shader*'s declared slot.

        Textured channels go through ``ShaderAttributeMap.connect_channel``, so
        the arity broadcast, the no-alpha rescue and the ShaderFX ``use_*``
        toggle are all handled in the one place that owns those mechanics.
        """
        node_type = cmds.nodeType(shader)
        connected = []
        for logical, source in channels.items():
            slot = ShaderAttributeMap.get_attr(node_type, logical)
            if not slot:
                if verbose:
                    cls.logger.info(f"  {logical}: no slot on {node_type} — dropped.")
                continue

            if source["file"]:
                if ShaderAttributeMap.connect_channel(
                    source["file"], logical, shader, shader_type=node_type
                ):
                    connected.append(logical)
                    if verbose:
                        cls.logger.info(f"  {logical} -> {slot[0]}")
                elif verbose:
                    cls.logger.info(f"  {logical}: could not drive {slot[0]}.")
            elif cls._set_constant(shader, slot[0], source["value"]) and verbose:
                cls.logger.info(f"  {logical} = {source['value']}")
        return connected

    @staticmethod
    def _set_constant(shader: str, attr: str, value: Any) -> bool:
        """Copy a literal onto the target slot, tolerating an arity mismatch."""
        if value is None or not cmds.attributeQuery(attr, node=shader, exists=True):
            return False
        try:
            if isinstance(value, (tuple, list)):
                if cmds.getAttr(f"{shader}.{attr}", type=True) in (
                    "float3",
                    "double3",
                ):
                    cmds.setAttr(f"{shader}.{attr}", *value, type="double3")
                else:  # float3 source into a scalar slot — average it
                    cmds.setAttr(f"{shader}.{attr}", sum(value) / len(value))
            else:
                cmds.setAttr(f"{shader}.{attr}", value)
            return True
        except (RuntimeError, ValueError):
            return False

    @staticmethod
    def _transfer_assignments(source: str, target: str) -> int:
        """Move every shading-group membership from *source* to *target*.

        Re-assigning through the target's OWN shading group (rather than
        repointing the source's) keeps face-level assignments intact: the
        members list carries components (``pCube1.f[0:5]``) as readily as whole
        shapes.
        """
        target_sg = (
            cmds.listConnections(target, type="shadingEngine")
            or [MatUtils.create_shading_group(target)]
        )[0]

        moved = 0
        for sg in cmds.listConnections(source, type="shadingEngine") or []:
            members = cmds.sets(sg, query=True, noIntermediate=True) or []
            if not members:
                continue
            cmds.sets(members, edit=True, forceElement=target_sg)
            moved += len(members)
        return moved


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
