# !/usr/bin/python
# coding=utf-8
"""Emissive Groups' Maya half: a group's faces wear a copy of their shader made
for the group (a duplicate sharing the base's input connections, so its maps,
with its own shading group), its emission weight keyed by the group's weight.
A copy is tagged on the shader node itself: ``emissiveGroup`` (the group),
``emissiveGroupBase`` (a message connection from the base) and
``emissiveGroupStrength`` (the base's emission weight it was keyed against).
``EmissiveGroups`` is the tool's API in Maya: pythontk's host-free
``EmissiveGroupsModel`` over this half; its panel is tentacle's.

The emission scalar a copy keys is the shader's own
(``ShaderAttributeMap.EMISSION_WEIGHT_ATTRS``: Standard Surface's and
aiStandardSurface's ``emission``, openPBR's ``emissionLuminance``,
StingrayPBS's ``emissive_intensity``). openPBR's is a luminance in nits,
which only gates its emission (the map's ``gate`` mode): the copy keys its
nits, so the viewport dims with the weight, and its ``render_effects`` entry
states the strength the ``materials`` record states its base at, 1
(:meth:`MayaEmissiveGroups.published_strength`). A Lambert, Blinn or Phong glows by
its ``incandescence`` colour (the materials record's glTF emissive colour):
its copy's incandescence is the base's times the weight, keyed per channel,
the base's colour kept on the copy (``emissiveGroupColour``) as what the
weight scales. A base with no emission at all (black incandescence, an
emission weight of 0) or a mapped incandescence, which no weight can scale,
is refused by name.
"""

import contextlib
from typing import Any, Dict, List, Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.node_utils.data_nodes import DataNodes

FaceMap = Dict[str, List[int]]


class _MayaEmissiveGroupsInternal:
    """Implementation-detail base for :class:`MayaEmissiveGroups`."""

    TAG_GROUP = "emissiveGroup"
    TAG_BASE = "emissiveGroupBase"
    TAG_STRENGTH = "emissiveGroupStrength"
    #: A colour shader's copy: the base's incandescence the weight scales.
    TAG_COLOUR = "emissiveGroupColour"
    #: The colour a Lambert, Blinn or Phong glows by.
    COLOUR_ATTR = "incandescence"
    #: Marks the weight proxies this tool stages (beside the transport's own
    #: ``curveProxy`` marker), so its cleanup takes only its own.
    PROXY_MARKER = "emissiveGroupProxy"
    #: A shading group's inputs a copy's own group takes from its base's.
    SG_INPUTS = ("displacementShader", "volumeShader", "aiSurfaceShader", "aiVolumeShader")
    #: Attributes no copy compares with its base (the node's own bookkeeping).
    UNCOMPARED = ("caching", "frozen", "nodeState", "isHistoricallyInteresting")

    @staticmethod
    def _transform(node: str) -> Optional[str]:
        """*node*'s transform as a long name (a shape's parent), else ``None``."""
        found = cmds.ls(node, long=True) or []
        if not found:
            return None
        if cmds.nodeType(found[0]) == "transform":
            return found[0]
        parent = cmds.listRelatives(found[0], parent=True, fullPath=True) or []
        return parent[0] if parent else None

    @classmethod
    def _face_map(cls, components) -> FaceMap:
        """Face components (and whole meshes) as a ``FaceMap`` keyed by long
        transform. Read compact (``f[a:b]`` ranges) and expanded here: a
        flattened list of a dense mesh's faces costs Maya one string per face."""
        out: Dict[str, set] = {}
        transforms: Dict[str, Optional[str]] = {}  # one query per node, not per range
        for comp in components:
            node, sep, span = str(comp).partition(".f[")
            if node not in transforms:
                transforms[node] = cls._transform(node)
            obj = transforms[node]
            if not obj:
                continue
            indices = out.setdefault(obj, set())
            start, _, stop = span.rstrip("]").partition(":")
            if not sep or start == "*":  # the whole mesh
                indices.update(range(int(cmds.polyEvaluate(obj, face=True))))
            else:
                indices.update(range(int(start), int(stop or start) + 1))
        return {obj: sorted(indices) for obj, indices in out.items()}

    @staticmethod
    def _components(face_map: FaceMap) -> List[str]:
        """``FaceMap`` -> face components, contiguous indices as ``f[a:b]`` ranges;
        an empty index list names the whole object."""
        comps = []
        for obj, indices in face_map.items():
            if not indices:
                comps.append(obj)
                continue
            ordered = sorted(set(int(i) for i in indices))
            start = prev = ordered[0]
            for index in ordered[1:] + [None]:
                if index is not None and index == prev + 1:
                    prev = index
                    continue
                comps.append(f"{obj}.f[{start}]" if start == prev else f"{obj}.f[{start}:{prev}]")
                if index is not None:
                    start = prev = index
        return comps

    @staticmethod
    def _sgs(material: str) -> List[str]:
        """The shading groups *material* is the surface shader of."""
        return sorted(
            set(
                cmds.listConnections(
                    f"{material}.outColor", source=False, destination=True, type="shadingEngine"
                )
                or []
            )
        )

    @classmethod
    def _sg(cls, material: str) -> str:
        """*material*'s shading group, made when it has none."""
        found = cls._sgs(material)
        if found:
            return found[0]
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material}SG")
        cmds.connectAttr(f"{material}.outColor", f"{sg}.surfaceShader", force=True)
        return sg

    @staticmethod
    def _shader_of(sg: str) -> Optional[str]:
        found = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
        return found[0] if found else None

    @staticmethod
    def _emission_attr(material: str) -> Optional[str]:
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        entry = ShaderAttributeMap.EMISSION_WEIGHT_ATTRS.get(cmds.nodeType(material))
        return entry[0] if entry else None

    @classmethod
    def _colour_shader(cls, material: str) -> bool:
        """A shader that glows by a colour alone (Lambert, Blinn, Phong)."""
        return cls._emission_attr(material) is None and bool(
            cmds.attributeQuery(cls.COLOUR_ATTR, node=material, exists=True)
        )

    @classmethod
    def _mapped(cls, plug: str) -> bool:
        """Whether something other than keys drives *plug* (a map)."""
        sources = cmds.listConnections(plug, source=True, destination=False, skipConversionNodes=True) or []
        return any("animCurve" not in cmds.nodeType(src) for src in sources)

    @classmethod
    def _reference(cls, material: str) -> List[float]:
        """What a colour shader's weight scales: a copy's stored base colour,
        else (a base) its own incandescence."""
        if cmds.attributeQuery(cls.TAG_COLOUR, node=material, exists=True):
            return [float(c) for c in cmds.getAttr(f"{material}.{cls.TAG_COLOUR}")[0]]
        return [float(c) for c in cmds.getAttr(f"{material}.{cls.COLOUR_ATTR}")[0]]

    @classmethod
    def _channels(cls, material: str) -> List[tuple]:
        """``(plug, reference)`` per channel of *material*'s emission: the
        scalar (reference 1), or a colour shader's three incandescence
        channels, each against the colour its weight scales."""
        attr = cls._emission_attr(material)
        if attr:
            return [(f"{material}.{attr}", 1.0)]
        if cls._colour_shader(material):
            return [
                (f"{material}.{cls.COLOUR_ATTR}{c}", r)
                for c, r in zip("RGB", cls._reference(material))
            ]
        return []

    @classmethod
    def _lead(cls, material: str) -> Optional[tuple]:
        """The channel the weight is read off: the brightest reference."""
        found = [ch for ch in cls._channels(material) if ch[1] > 0.0]
        return max(found, key=lambda ch: ch[1]) if found else None

    @classmethod
    def _plug(cls, material: str) -> Optional[str]:
        lead = cls._lead(material)
        return lead[0] if lead else None

    @staticmethod
    def _keys(plug: str) -> List[tuple]:
        flat = cmds.keyframe(plug, query=True, timeChange=True, valueChange=True) or []
        return [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat), 2)]

    @staticmethod
    def _driven(plug: str) -> bool:
        return bool(cmds.connectionInfo(plug, isDestination=True))

    @classmethod
    def _copy_keys(cls, src: str, dst: str, factor: float) -> None:
        """*src*'s keys onto *dst*, their shape kept, values times *factor*."""
        cmds.copyKey(src)
        cmds.pasteKey(dst, option="replaceCompletely")
        if abs(factor - 1.0) > 1e-12:
            cmds.scaleKey(dst, valueScale=factor, valuePivot=0.0)

    @staticmethod
    def _carrier() -> Optional[str]:
        """The scene's own carrier (the root-level one): a grouped module's
        ``data_export`` keeps its legacy weights."""
        return DataNodes.get_export_node(create=False)


class MayaEmissiveGroups(_MayaEmissiveGroupsInternal, ptk.PureEmissiveGroupsHost):
    """The Maya scene operations of the Emissive Groups model
    (``ptk.EmissiveGroupsHost``)."""

    data_nodes = DataNodes

    # Faces and materials -------------------------------------------------

    def faces(self, faces=None) -> FaceMap:
        source = faces if faces is not None else (cmds.ls(sl=True) or [])
        if isinstance(source, dict):
            source = self._components(source)
        source = [str(s) for s in (source if isinstance(source, (list, tuple)) else [source])]
        if not source:
            return {}
        converted = cmds.polyListComponentConversion(source, toFace=True) or []
        return self._face_map(cmds.filterExpand(converted, sm=34, expand=False) or [])

    def face_materials(self, face_map: FaceMap) -> Dict[str, FaceMap]:
        out: Dict[str, FaceMap] = {}
        for obj, indices in face_map.items():
            wanted = set(indices)
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True, noIntermediate=True) or []
            for sg in sorted(set(cmds.listConnections(shapes, type="shadingEngine") or [])):
                material = self._shader_of(sg)
                if not material:
                    continue
                held = self._face_map(cmds.sets(sg, query=True) or []).get(obj, [])
                hit = sorted(wanted.intersection(held))
                if hit:
                    out.setdefault(material, {}).setdefault(obj, []).extend(hit)
        return out

    def material_faces(self, material: str) -> FaceMap:
        out: Dict[str, set] = {}
        for sg in self._sgs(material):
            for obj, indices in self._face_map(cmds.sets(sg, query=True) or []).items():
                out.setdefault(obj, set()).update(indices)
        return {obj: sorted(i) for obj, i in out.items() if i}

    def material(self, name: str) -> Optional[str]:
        if not name or not cmds.objExists(name):
            return None
        return name if cmds.attributeQuery("outColor", node=name, exists=True) else None

    def assign(self, material: str, face_map: FaceMap) -> None:
        cmds.sets(self._components(face_map), edit=True, forceElement=self._sg(material))

    def select(self, face_map: FaceMap) -> None:
        cmds.select(self._components(face_map), replace=True)

    # Copies --------------------------------------------------------------

    def copies(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for node in cmds.ls(f"*.{self.TAG_GROUP}", objectsOnly=True, recursive=True) or []:
            base = cmds.listConnections(f"{node}.{self.TAG_BASE}", source=True, destination=False) or []
            out[node] = {
                "group": cmds.getAttr(f"{node}.{self.TAG_GROUP}") or "",
                "base": base[0] if base else None,
                "strength": float(cmds.getAttr(f"{node}.{self.TAG_STRENGTH}") or 0.0),
            }
        return out

    def make_copy(self, base: str, name: str) -> str:
        copy = cmds.duplicate(base, inputConnections=True, name=name)[0]
        for plug, _r in self._channels(copy):
            if self._driven(plug):  # the base's own curve, shared by -ic
                value = cmds.getAttr(plug)
                for src in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                    cmds.disconnectAttr(src, plug)
                cmds.setAttr(plug, value)
        sg = self._sg(copy)
        for base_sg in self._sgs(base)[:1]:
            for attr in self.SG_INPUTS:
                if not cmds.attributeQuery(attr, node=base_sg, exists=True):
                    continue
                for src in cmds.listConnections(f"{base_sg}.{attr}", source=True, destination=False, plugs=True) or []:
                    cmds.connectAttr(src, f"{sg}.{attr}", force=True)
        return copy

    def tag(self, copy: str, group: str, base: str, strength: float) -> None:
        if not cmds.attributeQuery(self.TAG_GROUP, node=copy, exists=True):
            cmds.addAttr(copy, longName=self.TAG_GROUP, dataType="string")
        if not cmds.attributeQuery(self.TAG_BASE, node=copy, exists=True):
            cmds.addAttr(copy, longName=self.TAG_BASE, attributeType="message")
        if not cmds.attributeQuery(self.TAG_STRENGTH, node=copy, exists=True):
            cmds.addAttr(copy, longName=self.TAG_STRENGTH, attributeType="double")
        cmds.setAttr(f"{copy}.{self.TAG_GROUP}", group, type="string")
        cmds.setAttr(f"{copy}.{self.TAG_STRENGTH}", float(strength))
        cmds.connectAttr(f"{base}.message", f"{copy}.{self.TAG_BASE}", force=True)
        if self._colour_shader(copy):
            self._set_reference(copy, self._reference(base))

    def _set_reference(self, copy: str, colour) -> None:
        if not cmds.attributeQuery(self.TAG_COLOUR, node=copy, exists=True):
            cmds.addAttr(copy, longName=self.TAG_COLOUR, attributeType="double3")
            for c in "RGB":
                cmds.addAttr(copy, longName=f"{self.TAG_COLOUR}{c}", attributeType="double", parent=self.TAG_COLOUR)
        cmds.setAttr(f"{copy}.{self.TAG_COLOUR}", *[float(c) for c in colour], type="double3")

    def delete_material(self, material: str) -> None:
        if cmds.objExists(material):
            cmds.delete(self._sgs(material) + [material])

    def material_state(self, material: str) -> Dict[str, Any]:
        skip = set(self.UNCOMPARED) | {self._emission_attr(material) or ""}
        colour = self._colour_shader(material)
        state: Dict[str, Any] = {}
        if colour:  # the colour the weight scales, compared as the scalar is not
            state["emission colour"] = self._reference(material)
        for attr in cmds.listAttr(material, keyable=True, scalar=True) or []:
            # A multi's children (``lightDataArray.lightDirectionX``) name no
            # plug without an index: the node's runtime data, not a value.
            if attr in skip or "." in attr or attr.startswith(self.TAG_GROUP):
                continue
            if colour and attr.startswith(self.COLOUR_ATTR):
                continue
            plug = f"{material}.{attr}"
            try:
                if not self._driven(plug):
                    state[attr] = cmds.getAttr(plug)
            except (RuntimeError, ValueError):
                pass
        pairs = cmds.listConnections(material, source=True, destination=False, connections=True, plugs=True) or []
        for dst, src in zip(pairs[::2], pairs[1::2]):
            attr = dst.split(".", 1)[1]
            if attr in skip or attr.startswith(self.TAG_GROUP):
                continue
            if colour and attr.startswith(self.COLOUR_ATTR):
                continue
            state[f"<{attr}"] = src
        return state

    def sync_copy(self, copy: str, base: str) -> None:
        want, have = self.material_state(base), self.material_state(copy)
        for key in sorted(set(want) | set(have)):
            if not key.startswith("<"):
                continue
            plug = f"{copy}.{key[1:]}"
            if key in have and have[key] != want.get(key):
                cmds.disconnectAttr(have[key], plug)
            if key in want and have.get(key) != want[key]:
                cmds.connectAttr(want[key], plug, force=True)
        for key, value in want.items():
            if key == "emission colour" or key.startswith("<") or self._driven(f"{copy}.{key}"):
                continue
            if have.get(key) != value:
                cmds.setAttr(f"{copy}.{key}", value)
        if self._colour_shader(copy) and have.get("emission colour") != want.get("emission colour"):
            # The weight is kept, re-keyed against the base's new colour.
            keys, weight = self.emission_keys(copy), self.emission(copy)
            self.clear_emission_keys(copy)
            self._set_reference(copy, want["emission colour"])
            for frame, value in keys:
                self.key_emission(copy, value, frame)
            if not keys and weight is not None:
                self.set_emission(copy, weight)

    # Emission ------------------------------------------------------------

    def emission(self, material: str) -> Optional[float]:
        if not cmds.objExists(material):
            return None
        if self._colour_shader(material):
            if self._mapped(f"{material}.{self.COLOUR_ATTR}"):
                return None  # a map no weight can scale on this shader
            if not cmds.attributeQuery(self.TAG_COLOUR, node=material, exists=True):
                return 1.0  # a base: its colour is its whole glow (black: refused)
        lead = self._lead(material)
        if lead is None:
            return 0.0 if self._channels(material) else None
        return float(cmds.getAttr(lead[0])) / lead[1]

    def emission_color(self, material: str) -> Optional[List[float]]:
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        if self._colour_shader(material):
            if self._mapped(f"{material}.{self.COLOUR_ATTR}"):
                return None
            return self._reference(material)
        node_type = cmds.nodeType(material)
        slot = ShaderAttributeMap.get_attr(node_type, "emission")
        if slot and cmds.listConnections(f"{material}.{slot[0]}", source=True, destination=False):
            return None  # mapped
        attr = ShaderAttributeMap.constant_attr(node_type, "emission")
        if not attr or not cmds.attributeQuery(attr, node=material, exists=True):
            return None
        return [float(c) for c in cmds.getAttr(f"{material}.{attr}")[0][:3]]

    def set_emission(self, material: str, value: float) -> None:
        for plug, ref in self._channels(material):
            if not self._driven(plug):
                cmds.setAttr(plug, ref * float(value))

    def emission_channel(self, material: str) -> Optional[str]:
        """What keys *material*'s emission scalar: its plug (blendertk's twin
        names its fcurve), or ``None`` when it has none."""
        return self._plug(material)

    def emission_keys(self, material: str):
        lead = self._lead(material)
        return [(f, v / lead[1]) for f, v in self._keys(lead[0])] if lead else []

    def emission_scale(self, material: str) -> float:
        lead = self._lead(material)  # the plug :meth:`_plug` names
        return lead[1] if lead else 1.0

    def published_strength(self, material: str, strength: float) -> float:
        # As the materials record states the shader's weight
        # (``ShaderAttributeMap.emission_weight``): a ``gate`` scalar --
        # openPBR's emissionLuminance, in nits -- decides whether it emits and
        # never scales it; a ``multiply`` one (Standard Surface,
        # aiStandardSurface, StingrayPBS) and a colour shader's 1 are the
        # strength as they stand.
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        entry = ShaderAttributeMap.EMISSION_WEIGHT_ATTRS.get(cmds.nodeType(material))
        if entry is not None and entry[1] == "gate":
            return 1.0 if float(strength) > 0.0 else 0.0
        return float(strength)

    def key_emission(self, material: str, value: float, frame: Optional[float] = None) -> None:
        for plug, ref in self._channels(material):
            kwargs = {"value": ref * float(value)}
            if frame is not None:
                kwargs["time"] = frame
            cmds.setKeyframe(plug, **kwargs)

    def clear_emission_keys(self, material: str) -> None:
        for plug, _ref in self._channels(material):
            if not self._keys(plug):
                continue
            value = cmds.getAttr(plug)
            cmds.cutKey(plug, clear=True)
            cmds.setAttr(plug, value)

    def copy_emission(self, src: str, dst: str, factor: float) -> None:
        lead = self._lead(src)
        if lead is not None and self._keys(lead[0]):
            # Each channel of *dst* takes the source's curve, its shape kept,
            # in its own terms: the weight times its reference.
            for plug, ref in self._channels(dst):
                self._copy_keys(lead[0], plug, factor * ref / lead[1])
            return
        self.clear_emission_keys(dst)
        weight = self.emission(src)
        if weight is not None:
            self.set_emission(dst, weight * factor)

    def scale_emission(self, material: str, factor: float) -> None:
        for plug, _ref in self._channels(material):
            if self._keys(plug):
                cmds.scaleKey(plug, valueScale=factor, valuePivot=0.0)
            elif not self._driven(plug):
                cmds.setAttr(plug, float(cmds.getAttr(plug)) * factor)

    # Export --------------------------------------------------------------

    def stage_emission_proxy(self, material: str, name: str, factor: float) -> Optional[str]:
        if cmds.objExists(name):
            return None
        carrier = str(DataNodes.ensure_export())
        proxy = cmds.createNode("transform", name=name, parent=carrier, skipSelect=True)
        proxy = (cmds.ls(proxy, long=True) or [proxy])[0]
        for marker in (ptk.MeshConvert.CURVE_PROXY_MARKER, self.PROXY_MARKER):
            cmds.addAttr(proxy, longName=marker, attributeType="bool")
            cmds.setAttr(f"{proxy}.{marker}", True)
        lead = self._lead(material)
        self._copy_keys(lead[0], f"{proxy}.scaleX", factor / lead[1])
        return proxy

    def emission_proxies(self) -> List[str]:
        return [
            node.split("|")[-1]
            for node in cmds.ls(f"*.{self.PROXY_MARKER}", objectsOnly=True, long=True, recursive=True) or []
        ]

    def remove_emission_proxies(self, names=None) -> List[str]:
        wanted = None if names is None else set(names)
        removed = []
        for node in cmds.ls(f"*.{self.PROXY_MARKER}", objectsOnly=True, long=True, recursive=True) or []:
            leaf = node.split("|")[-1]
            if cmds.objExists(node) and (wanted is None or leaf in wanted):
                removed.append(leaf)
                cmds.delete(node)
        return removed

    def undo_step(self, label: str):
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.undo_chunk(label) if cmds is not None else contextlib.nullcontext()

    # The old tool's data -------------------------------------------------

    def legacy_store_names(self, prefix: str) -> List[str]:
        return [n[len(prefix):] for n in cmds.ls(f"{prefix}*", type="objectSet") or []]

    def legacy_members(self, store: str) -> Optional[FaceMap]:
        if not cmds.objExists(store):
            return None
        found = cmds.sets(store, query=True) or []
        return self._face_map(cmds.filterExpand(found, sm=34, expand=False) or [])

    def adopt_legacy_weight(self, attr: str, copy: str, factor: float) -> bool:
        carrier = self._carrier()
        if carrier is None or not cmds.attributeQuery(attr, node=carrier, exists=True):
            return False
        plug = f"{carrier}.{attr}"
        if not self._keys(plug):
            return False
        for channel, ref in self._channels(copy):
            self._copy_keys(plug, channel, factor * ref)
        return True

    def remove_legacy(self, stores: List[str], attrs: List[str], color_set: str) -> List[str]:
        removed = []
        for store in stores:
            if cmds.objExists(store) and cmds.nodeType(store) == "objectSet":
                cmds.delete(store)
                removed.append(store)
        carrier = self._carrier()
        for attr in attrs:
            if carrier and cmds.attributeQuery(attr, node=carrier, exists=True):
                plug = f"{carrier}.{attr}"
                curves = cmds.listConnections(plug, type="animCurve") or []
                if curves:
                    cmds.delete(curves)
                cmds.deleteAttr(plug)
                removed.append(plug)
        for mesh in cmds.ls(type="mesh", noIntermediate=True) or []:
            if color_set in (cmds.polyColorSet(mesh, query=True, allColorSets=True) or []):
                cmds.polyColorSet(mesh, delete=True, colorSet=color_set)
                removed.append(f"{mesh}: {color_set}")
        return removed

    def extra_warnings(self) -> List[str]:
        return [
            f"Leftover export curve proxy {node!r} (an export was interrupted) -- "
            "remove_export_proxies() clears it."
            for node in cmds.ls(f"*.{self.PROXY_MARKER}", objectsOnly=True, recursive=True) or []
        ]


class EmissiveGroups(ptk.EmissiveGroupsModel):
    """The Emissive Groups API in Maya: pythontk's model over the Maya half."""

    HOST = MayaEmissiveGroups()
