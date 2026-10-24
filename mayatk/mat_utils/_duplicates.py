# !/usr/bin/python
# coding=utf-8
"""Duplicate materials behind :class:`mayatk.MatUtils`.

Grouping materials by a cheap texture fingerprint, then proving each candidate
really is interchangeable with its group's keeper -- equal scalar values, the
same UV placement and color space per texture slot, the same image content --
before anything is merged, and the merge itself.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import logging
import os
import re
from typing import List, Dict

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

logger = logging.getLogger(__name__)


class _DuplicatesInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    # UV-placement attrs that change how a texture reads on the surface —
    # the axes the duplicate-material fingerprint is blind to.
    _PLACE2D_SIG_ATTRS = (
        "repeatU",
        "repeatV",
        "offsetU",
        "offsetV",
        "rotateUV",
        "mirrorU",
        "mirrorV",
        "wrapU",
        "wrapV",
        "stagger",
        "coverageU",
        "coverageV",
        "translateFrameU",
        "translateFrameV",
        "rotateFrame",
    )

    #: Where each placement attr leaves a texture reading exactly where the
    #: UVs put it. ``wrap`` is left out: it only matters outside 0-1.
    _PLACE2D_IDENTITY = {
        "repeatU": 1.0,
        "repeatV": 1.0,
        "offsetU": 0.0,
        "offsetV": 0.0,
        "rotateUV": 0.0,
        "mirrorU": 0.0,
        "mirrorV": 0.0,
        "stagger": 0.0,
        "coverageU": 1.0,
        "coverageV": 1.0,
        "translateFrameU": 0.0,
        "translateFrameV": 0.0,
        "rotateFrame": 0.0,
    }

    @classmethod
    def _placement_is_identity(cls, file_node: str) -> bool:
        """Whether *file_node* reads its image exactly where the UVs put it: no
        tiling, offset, rotation, mirror, stagger or coverage from its
        place2dTexture (none connected counts). An unreadable attr does not."""
        sig = dict(zip(cls._PLACE2D_SIG_ATTRS, cls._placement_signature(file_node)))
        return all(
            sig.get(attr, default) == default
            for attr, default in cls._PLACE2D_IDENTITY.items()
        )

    @classmethod
    def _placement_signature(cls, file_node: str) -> tuple:
        """UV-placement signature of the place2dTexture feeding *file_node*.

        Two file nodes reading the same image with different tiling/offset
        produce visually different materials — this is what lets the
        duplicate verifier tell a shared atlas apart from a true duplicate.
        Empty tuple when no place2d is connected (two bare nodes match).
        """
        p2d = (
            cmds.listConnections(
                file_node, source=True, destination=False, type="place2dTexture"
            )
            or []
        )
        if not p2d:
            return ()
        sig = []
        for attr in cls._PLACE2D_SIG_ATTRS:
            try:
                sig.append(round(cmds.getAttr(f"{p2d[0]}.{attr}"), 5))
            except Exception:
                sig.append(None)
        return tuple(sig)

    @classmethod
    def _materials_are_verified_duplicates(
        cls, mat_a: str, mat_b: str, slots_a: dict, slots_b: dict
    ) -> bool:
        """Pairwise proof that two fingerprint-matched materials are truly
        interchangeable: equal unconnected scalar attribute values, and per
        texture slot identical placement, color space, and image content.

        The fingerprint is a cheap GROUPING heuristic (node type + attr →
        basename); this gate is what makes feeding the result to a
        destructive merge safe.  Conservative by design: anything that can't
        be positively verified fails the pair.
        """
        # 1. Unconnected scalar attributes — generic: both materials are the
        #    same nodeType, so the attribute lists are identical.  Connected
        #    plugs are skipped (their value is texture-driven; the slot check
        #    below owns those).
        for attr in cmds.listAttr(mat_a, settable=True, scalar=True) or []:
            plug_a, plug_b = f"{mat_a}.{attr}", f"{mat_b}.{attr}"
            try:
                if cmds.listConnections(
                    plug_a, source=True, destination=False
                ) or cmds.listConnections(plug_b, source=True, destination=False):
                    continue
                va, vb = cmds.getAttr(plug_a), cmds.getAttr(plug_b)
            except Exception:
                continue  # multi/message/unreadable plug — not comparable
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(va - vb) > 1e-5:
                    return False
            elif va != vb:
                return False

        # 2. Per-slot texture verification.
        if set(slots_a) != set(slots_b):
            return False

        def profile(nodes):
            out = []
            for n in nodes:
                path = cmds.getAttr(f"{n}.fileTextureName") or ""
                try:
                    cspace = cmds.getAttr(f"{n}.colorSpace")
                except Exception:
                    cspace = None
                out.append((path, cspace, cls._placement_signature(n)))
            return sorted(out, key=repr)

        for slot, nodes_a in slots_a.items():
            nodes_b = slots_b[slot]
            if len(nodes_a) != len(nodes_b):
                return False
            for (pa, ca, sa), (pb, cb, sb) in zip(profile(nodes_a), profile(nodes_b)):
                if sa != sb or ca != cb or not cls._textures_identical(pa, pb):
                    return False
        return True

    @staticmethod
    def _is_duplicate_material(material1, material2):
        """Body of :meth:`MatUtils.is_duplicate_material`."""
        material1 = str(material1)
        material2 = str(material2)
        # The file nodes upstream of each: a connection out of a material (its
        # Arnold bridge's emission follows its weight) feeds none of its textures.
        textures1 = set(cmds.ls(cmds.listHistory(material1) or [], type="file"))
        textures2 = set(cmds.ls(cmds.listHistory(material2) or [], type="file"))
        return textures1 == textures2

    @classmethod
    def _find_materials_with_duplicate_textures(cls, materials, strict, verify):
        """Body of :meth:`MatUtils.find_materials_with_duplicate_textures`."""

        def _texture_id(path: str) -> str:
            if strict:  # the same file: case folds only where the OS does
                return os.path.normcase(path)
            return os.path.splitext(os.path.basename(path))[0].lower()

        def _parent_attr(plug: str) -> str:
            parts = plug.split(".", 1)
            if len(parts) < 2:
                return plug
            attr_path = parts[1]
            attr_path = re.sub(r"\[\d+\]", "", attr_path)
            root_attr = attr_path.split(".")[0]
            root_attr = re.sub(r"[RGBXYZA]$", "", root_attr)
            return root_attr or attr_path.split(".")[0]

        if materials is None:
            materials = cmds.ls(mat=True) or []
        else:
            materials = [str(m) for m in materials]
            materials = cmds.ls(materials, mat=True) or []

        material_data = {}
        slot_maps: Dict[str, Dict[tuple, List[str]]] = {}
        for material in materials:
            mat_type = cmds.nodeType(material)

            history = cmds.listHistory(material, pruneDagObjects=True) or []
            file_nodes = cmds.ls(history, type="file") or []
            if not file_nodes:
                continue

            history_set = set(history)

            attr_texture_pairs = []
            slots: Dict[tuple, List[str]] = {}
            for file_node in file_nodes:
                if not cmds.objExists(f"{file_node}.fileTextureName"):
                    continue
                path = cmds.getAttr(f"{file_node}.fileTextureName")
                if not path:
                    continue
                tex_id = _texture_id(path)

                visited = set()
                frontier = [file_node]
                mat_attrs = set()
                while frontier:
                    node = frontier.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    dest_plugs = (
                        cmds.listConnections(
                            node,
                            source=False,
                            destination=True,
                            plugs=True,
                        )
                        or []
                    )
                    for plug in dest_plugs:
                        plug_node = plug.split(".")[0]
                        if plug_node == material:
                            mat_attrs.add(_parent_attr(plug))
                        elif plug_node not in visited and plug_node in history_set:
                            frontier.append(plug_node)

                if mat_attrs:
                    for attr in mat_attrs:
                        attr_texture_pairs.append((attr, tex_id))
                        slots.setdefault((attr, tex_id), []).append(file_node)
                else:
                    attr_texture_pairs.append(("_unresolved", tex_id))
                    slots.setdefault(("_unresolved", tex_id), []).append(file_node)

            if not attr_texture_pairs:
                continue

            fingerprint = (mat_type, frozenset(attr_texture_pairs))
            material_data[material] = fingerprint
            slot_maps[material] = slots

        seen = {}
        for material, fingerprint in material_data.items():
            match_found = False
            for seen_fp, seen_list in seen.items():
                if fingerprint == seen_fp:
                    seen_list.append(material)
                    match_found = True
                    break
            if not match_found:
                seen[fingerprint] = [material]

        duplicates = {}
        for materials_list in seen.values():
            if len(materials_list) > 1:
                materials_list.sort(key=lambda x: (len(x), x))
                original = materials_list[0]
                dups = materials_list[1:]
                if verify:
                    dups = [
                        d
                        for d in dups
                        if cls._materials_are_verified_duplicates(
                            original, d, slot_maps[original], slot_maps[d]
                        )
                    ]
                if dups:
                    duplicates[original] = dups
        # A query prints nothing: its callers report what it found (the
        # Lightmap Baker's table reads it on every refresh).
        return duplicates

    @classmethod
    def _reassign_duplicate_materials(cls, materials, delete, strict, verify):
        """Body of :meth:`MatUtils.reassign_duplicate_materials`."""
        if materials is not None:
            valid_objects = []
            for m in materials:
                m = str(m)
                if cmds.objExists(m):
                    valid_objects.append(m)
                else:
                    cmds.warning(f"Object '{m}' does not exist or is not valid.")

            collected_materials = cmds.ls(valid_objects, mat=True) or []
            if not collected_materials:
                cmds.warning(f"No valid materials found in {materials}")
                return
        else:
            collected_materials = cmds.ls(mat=True) or []

        duplicate_to_original = cls.find_materials_with_duplicate_textures(
            collected_materials, strict=strict, verify=verify
        )
        for original, duplicates in duplicate_to_original.items():
            original_sgs = cmds.listConnections(original, type="shadingEngine")
            if not original_sgs:
                continue
            for duplicate in duplicates:
                duplicate_sgs = set(
                    cmds.listConnections(duplicate, type="shadingEngine") or []
                )
                try:
                    cls._merge_shading_groups(
                        {sg: original_sgs[0] for sg in sorted(duplicate_sgs)},
                        delete=delete,
                    )
                    # Debug, never print: the Lightmap Baker's Combine and the
                    # Scene Exporter call this, and a print lands in the Script
                    # Editor once per material.
                    logger.debug("Reassigned material from %s to %s.", duplicate, original)
                except Exception as e:
                    cmds.warning(f"Could not merge {duplicate} into {original}: {e}")

    @classmethod
    def _merge_shading_groups(cls, pairs: Dict[str, str], delete: bool = True) -> None:
        """Move each shading group's members into the one *pairs* maps it to;
        with *delete*, then delete it and its shaders that no other shading
        group uses. A default node (``initialShadingGroup``, ``lambert1``) is
        emptied, never deleted."""
        defaults = set(cmds.ls(defaultNodes=True) or [])
        slots = cls._SG_SHADER_SLOTS + cls._SG_EXTRA_SHADER_SLOTS
        for dup, kept in pairs.items():
            if dup == kept or not cmds.objExists(dup):
                continue
            members = cmds.sets(dup, q=True)
            if members:
                cmds.sets(members, edit=True, forceElement=kept)
            if not delete or dup in defaults:
                continue
            shaders = set(cls._sg_shaders(dup, slots=slots))
            cmds.delete(dup)
            for shader in sorted(shaders - defaults):
                if cmds.objExists(shader) and not cmds.listConnections(
                    shader, type="shadingEngine"
                ):
                    cmds.delete(shader)
