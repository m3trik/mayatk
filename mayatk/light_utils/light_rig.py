# !/usr/bin/python
# coding=utf-8
"""Light Rig manager: save, manage, swap and restore named light rigs.

:class:`LightRig` is the engine; :class:`LightRigSlots` drives the
``light_rig.ui`` panel (tentacle's Lighting menu opens it).

A *rig* is the lights under one scene group, plus the plugs it overrides on
nodes it does not own (the office lights turned off, a material's emission),
and how each light fixture of the scene lights the room (``fixtures``:
``ptk.LightRigFixture`` -- glow, emissive or lamp). Rigs are plain JSON
payloads in the rig library every host and tool shares
(``ptk.LightRigPayload.library``), so a rig saved in one scene lights another,
and a look match outside Maya saves into the same library.

Payload, version 1::

    {"version": 1,
     "group": "MOODY_LIGHTS",                    # the scene group its lights live under
     "lights": [{"name": ..., "type": "spotLight",
                 "pos": [x, y, z], "rot": [x, y, z],        # world space: cm, degrees
                 "scale": [...], "rotate_order": n, "visible": False,   # when not the default
                 "attrs": {attr: value},          # what differs from a fresh light
                 "textures": {attr: {"file": ..., "colorSpace": ..., "plug": "outColor"}},
                 "user_attrs": {attr: {"type": ..., "value": ...}},
                 "filters": [{"type": "aiGobo", "attrs": {...}, "textures": {...}}]}],
     "existing": {node: {attr: value}},          # overrides on scene lights it does not own
     "materials": {node: {attr: value}},         # overrides on materials
     "fixtures": [{"name": ..., "mode": "lamp",  # glow | emissive | lamp: set as
                   "meshes": [...], "lamps": {light: intensity}}],   # existing overrides
     ...}                                        # anything else (viewer, source) is carried

A light may also be spelled the way the look-match tool writes it -- ``type``
``spot`` / ``point`` / ``area`` / ``aiArea``, ``aim``, ``cone``, ``exposure``,
``radius``, ``gobo`` ... (:attr:`LightRig.FRIENDLY`); :meth:`LightRig.normalize`
turns that into the form above.

A payload's lengths are centimetres and its angles degrees, whatever units the
scene works in (``ptk.LightRigPayload``): capture, apply, compare and save run
under ``Units.working_as()``, so a rig saved in a metre scene lands in a
centimetre one where it was. A driver an override took over is reconnected in
the scene's own units, which size the unit conversion Maya inserts.

Applying a rig never deletes a light it does not own. Its overrides win: one
on a driven plug takes it over -- the connection is broken, reported, and
reconnected by swap back or undo -- so a rig sets the state it was made in
wherever it goes; a locked plug is left alone and reported. An override of an
Arnold bridge's emission lands on the material the bridge follows
(``ArnoldBridge.authored_plug``): the export ships the material, so a rig can
never bake one glow and ship another.
"""

import copy
import dataclasses
import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.core_utils.units import Units
from mayatk.light_utils._light_utils import LightUtils


class _LightRigInternal(object):
    """Scene reads and writes behind :class:`LightRig`."""

    #: node type -> ({attr: value of a fresh node}, {child attr: compound parent});
    #: filled once per type and session, from a throwaway node.
    _structure: Dict[str, Tuple[Dict[str, Any], Dict[str, str]]] = {}
    #: Attributes every DAG node carries (display, bookkeeping): never part of a rig.
    _base_attrs: Optional[set] = None
    #: Destinations that only register a node in a scene list (never "a user").
    _REGISTRIES = (
        "defaultRenderUtilityList",
        "defaultTextureList",
        "defaultShaderList",
    )
    #: User-attribute types a rig rebuilds.
    _USER_TYPES = ("bool", "long", "short", "byte", "float", "double", "string")

    # ------------------------------------------------------------- attributes
    @classmethod
    def _bookkeeping(cls) -> set:
        """Every attribute a plain dependency node and a plain DAG shape carry:
        what a light inherits rather than defines (read from throwaway nodes)."""
        if cls._base_attrs is None:
            names = set()
            with CoreUtils.undo_disabled():
                for kind in ("network", "locator"):
                    made = cmds.createNode(kind, skipSelect=True)
                    try:
                        names.update(cmds.listAttr(made) or [])
                    finally:
                        cmds.delete(
                            cmds.listRelatives(made, parent=True, fullPath=True) or made
                        )
            cls._base_attrs = names
        return cls._base_attrs

    @staticmethod
    def _is_light_type(kind: str) -> bool:
        return kind in LightUtils.ARNOLD_LIGHT_TYPES or "light" in (
            cmds.nodeType(kind, isTypeName=True, inherited=True) or []
        )

    @classmethod
    def _structure_of(cls, kind: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """A node type's fresh attribute values and its compound children.

        Read from a throwaway node made the way a rig makes one (a light through
        ``shadingNode -asLight``), outside the undo queue: a capture is a query.
        Read in centimetres and degrees, the payload's units, whatever the
        scene's: the cache outlives a switch of them.
        """
        if kind not in cls._structure:
            with CoreUtils.undo_disabled(), Units.working_as():
                if cls._is_light_type(kind):
                    made = cmds.shadingNode(kind, asLight=True, skipSelect=True)
                    node = (
                        cmds.listRelatives(made, shapes=True, fullPath=True) or [made]
                    )[0]
                else:
                    made = node = cmds.createNode(kind, skipSelect=True)
                try:
                    skip = cls._bookkeeping()
                    values, parents = {}, {}
                    for attr in (
                        cmds.listAttr(node, settable=True, scalar=True, write=True)
                        or []
                    ):
                        if "." in attr or attr in skip:
                            continue
                        try:
                            value = cmds.getAttr(f"{node}.{attr}")
                        except (RuntimeError, ValueError):
                            continue
                        if not isinstance(value, (bool, int, float, str)):
                            continue
                        values[attr] = value
                        parent = cmds.attributeQuery(attr, node=node, listParent=True)
                        if parent:
                            parents[attr] = parent[0]
                    cls._structure[kind] = (values, parents)
                finally:
                    cmds.delete(made)
        return cls._structure[kind]

    @staticmethod
    def _texture_of(source_plug: str) -> Optional[Dict[str, str]]:
        """``{"file", "colorSpace", "plug"}`` when *source_plug* is a file texture's output."""
        node, _, attr = source_plug.partition(".")
        if cmds.nodeType(node) != "file":
            return None
        return {
            "file": cmds.getAttr(node + ".fileTextureName") or "",
            "colorSpace": cmds.getAttr(node + ".colorSpace") or "",
            "plug": attr,
        }

    @classmethod
    def _capture_attrs(cls, node: str) -> Dict[str, Any]:
        """``attrs`` / ``textures`` / ``user_attrs`` of *node*: what differs from a fresh one."""
        defaults, parents = cls._structure_of(cmds.nodeType(node))
        incoming = (
            cmds.listConnections(
                node,
                source=True,
                destination=False,
                connections=True,
                plugs=True,
                skipConversionNodes=True,
            )
            or []
        )
        textures, driven = {}, set()
        for dst, src in zip(incoming[0::2], incoming[1::2]):
            attr = dst.partition(".")[2].split("[")[0]
            if attr not in defaults and attr not in parents.values():
                continue  # aiFilters, light-set and linker plugs: rebuilt by other means
            tex = cls._texture_of(src)
            if tex:
                textures[attr] = tex
                driven.add(attr)
        attrs, changed = {}, set()
        for attr, default in defaults.items():
            if parents.get(attr, attr) in driven:
                continue
            value = cmds.getAttr(f"{node}.{attr}")
            if cls._same(value, default, 1e-6):
                continue
            if attr in parents:
                changed.add(parents[attr])
            else:
                attrs[attr] = value
        for parent in changed:
            children = cmds.attributeQuery(parent, node=node, listChildren=True) or []
            attrs[parent] = [cmds.getAttr(f"{node}.{c}") for c in children]
        # Elements of multi attributes (an aiGobo's rotate[0]): a fresh node has none.
        skip = cls._bookkeeping()
        for attr in (
            cmds.listAttr(node, multi=True, settable=True, scalar=True, write=True)
            or []
        ):
            base = attr.split("[")[0]
            if "[" not in attr or "." in attr or base in skip or base in driven:
                continue
            try:
                value = cmds.getAttr(f"{node}.{attr}")
            except (RuntimeError, ValueError):
                continue
            if isinstance(value, (bool, int, float, str)):
                attrs[attr] = value
        user = {}
        for attr in cmds.listAttr(node, userDefined=True) or []:
            try:
                kind = cmds.getAttr(f"{node}.{attr}", type=True)
                value = cmds.getAttr(f"{node}.{attr}")
            except (RuntimeError, ValueError):
                continue
            if kind in cls._USER_TYPES:
                user[attr] = {"type": kind, "value": value}
        out = {}
        for key, value in (
            ("attrs", attrs),
            ("textures", textures),
            ("user_attrs", user),
        ):
            if value:
                out[key] = value
        return out

    # ------------------------------------------------------------- scene graph
    @staticmethod
    def _light_shapes(group: str) -> List[str]:
        if not cmds.objExists(group):
            return []
        root = cmds.ls(group, long=True)[0]
        return [s for s in LightUtils.all_lights() if s.startswith(root + "|")]

    @staticmethod
    def _filters(shape: str) -> List[str]:
        if not cmds.attributeQuery("aiFilters", node=shape, exists=True):
            return []
        out = []
        for i in cmds.getAttr(f"{shape}.aiFilters", multiIndices=True) or []:
            out += (
                cmds.listConnections(
                    f"{shape}.aiFilters[{i}]", source=True, destination=False
                )
                or []
            )[:1]
        return out

    @classmethod
    def _owned_nodes(cls, shapes: List[str]) -> set:
        """The filters and texture nodes a rig's lights made: what goes with them."""
        owned = set()
        for node in shapes + [f for s in shapes for f in cls._filters(s)]:
            if node not in shapes:
                owned.add(node)
            for tex in (
                cmds.listConnections(node, source=True, destination=False, type="file")
                or []
            ):
                owned.add(tex)
                owned.update(
                    cmds.listConnections(
                        tex, source=True, destination=False, type="place2dTexture"
                    )
                    or []
                )
        return owned

    @classmethod
    def _used_outside(cls, node: str, owned: set, shapes: set) -> bool:
        for dst in (
            cmds.listConnections(node, source=False, destination=True, shapes=True)
            or []
        ):
            if cmds.nodeType(dst) in cls._REGISTRIES or dst in owned:
                continue
            if set(cmds.ls(dst, long=True) or []) & shapes:
                continue
            return True
        return False

    # ------------------------------------------------------------- writes
    @staticmethod
    def _set(plug: str, value: Any) -> None:
        if isinstance(value, str):
            cmds.setAttr(plug, value, type="string")
        elif isinstance(value, (list, tuple)):
            try:
                cmds.setAttr(plug, *value)
            except RuntimeError:
                cmds.setAttr(plug, *value, type="double3")
        else:
            cmds.setAttr(plug, value)

    @staticmethod
    def _owner(node: str, attr: str) -> Optional[str]:
        """*node*, or its shape, whichever carries *attr* (an override names the transform)."""
        for candidate in [node] + (
            cmds.listRelatives(node, shapes=True, fullPath=True) or []
        ):
            if cmds.attributeQuery(attr, node=candidate, exists=True):
                return candidate
        return None

    @staticmethod
    def _driver(plug: str) -> Optional[str]:
        """The plug driving *plug* (past a unit conversion), or None."""
        sources = cmds.listConnections(
            plug, source=True, destination=False, plugs=True, skipConversionNodes=True
        )
        return sources[0] if sources else None

    @classmethod
    def _plug(cls, node: str, attr: str) -> Optional[Tuple[str, str]]:
        """``(named, landed)`` for an override of ``node.attr``, or None when there
        is no such attribute: the plug on *node* or its shape (:meth:`_owner`),
        and the one the override lands on -- for an Arnold bridge's emission
        the material it follows (:meth:`ArnoldBridge.authored_plug`), what the
        export ships, so a rig cannot bake one glow and ship another."""
        from mayatk.mat_utils.arnold_bridge import ArnoldBridge

        real = "visibility" if attr == "visible" else attr
        owner = cls._owner(node, real)
        if owner is None:
            return None
        plug = f"{owner}.{real}"
        return plug, ArnoldBridge.authored_plug(plug)

    @staticmethod
    def _plug_key(plug: str) -> str:
        """*plug* under its node's full path: two spellings of one plug compare equal."""
        node, _, attr = plug.partition(".")
        return f"{(cmds.ls(node, long=True) or [node])[0]}.{attr}"

    @classmethod
    def landed_overrides(
        cls, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[str]]:
        """*payload*'s overrides as they land: ``({section: {node: {attr:
        value}}}, yielded)``.

        An override of a plug that follows another (an Arnold bridge's
        emission, :meth:`_plug`) lands on the plug it follows. Where the
        payload also names that plug itself, its own value wins and the
        follower is dropped -- one *yielded* line when the two disagree (a rig
        built from a look mix carries the bake's bridge state beside the
        material level it sets). Apply, compare and a layer's emission target
        read the overrides through this.
        """
        sections = {s: payload.get(s) or {} for s in ("existing", "materials")}
        named = {
            cls._plug_key(f"{node}.{attr}"): value
            for nodes in sections.values()
            for node, attrs in nodes.items()
            if cmds.objExists(node)
            for attr, value in attrs.items()
        }
        landed: Dict[str, Dict[str, Dict[str, Any]]] = {}
        yielded: List[str] = []
        for section, nodes in sections.items():
            for node, attrs in nodes.items():
                kept = {}
                for attr, value in attrs.items():
                    found = cls._plug(node, attr) if cmds.objExists(node) else None
                    follows = found and found[1] != found[0]
                    key = cls._plug_key(found[1]) if follows else None
                    if key in named:
                        if named[key] != value:
                            yielded.append(
                                f"{node}.{attr}: follows {found[1]}, which the rig "
                                f"sets to {named[key]!r}"
                            )
                        continue
                    kept[attr] = value
                if kept:
                    landed.setdefault(section, {})[node] = kept
        return landed, yielded

    @classmethod
    def _set_overrides(
        cls,
        node: str,
        attrs: Dict[str, Any],
        scene_units: Optional[Tuple[str, str]] = None,
    ) -> Tuple[List[str], List[str]]:
        """Set *attrs* on a node the rig does not own: ``(left alone, disconnected)``,
        one line each with the reason.

        A plug a connection drives is disconnected and set: the rig's state wins
        wherever it is applied -- a scene may drive from a texture what the
        rig's own scene left constant (an office's emission) -- and the
        snapshot keeps the connection (:meth:`_current_values`), so swap back
        and undo reconnect it. A value ``{"from": plug}`` reconnects, under
        *scene_units* (``(linear, angle)``, the scene's own): Maya sizes the
        unit conversion it inserts from the working units at connect time, so
        the link comes back as the scene made it. Each override lands where
        :meth:`_plug` resolves it (callers pass :meth:`landed_overrides`).
        """
        if not cmds.objExists(node):
            return [f"{node}: not in the scene"], []
        skipped, disconnected = [], []
        for attr, value in attrs.items():
            found = cls._plug(node, attr)
            if found is None:
                skipped.append(f"{node}.{attr}: no such attribute")
                continue
            plug = found[1]
            owner, _, real = plug.partition(".")
            if cmds.getAttr(plug, lock=True):
                skipped.append(f"{node}.{attr}: locked, left alone")
                continue
            if isinstance(value, dict) and "from" in value:
                if cmds.objExists(value["from"]):
                    with Units.working_as(*(scene_units or (None, None))):
                        cmds.connectAttr(value["from"], plug, force=True)
                else:
                    skipped.append(f"{node}.{attr}: its driver {value['from']} is gone")
                continue
            driven = [
                child
                for child in cmds.attributeQuery(real, node=owner, listChildren=True)
                or []
                if cls._driver(f"{owner}.{child}")
            ]
            if driven:  # one channel driven: the compound cannot be set whole
                skipped.append(
                    f"{node}.{attr}: its {', '.join(driven)} driven, left alone"
                )
                continue
            driver = cls._driver(plug)
            if driver:
                for source in (
                    cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    )
                    or []
                ):
                    cmds.disconnectAttr(source, plug)
                disconnected.append(f"{node}.{attr}: disconnected from {driver}")
            cls._set(plug, value)
        return skipped, disconnected

    @classmethod
    def _current_values(
        cls, section: Optional[Dict[str, Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        """The scene's current values of the plugs *section* names (missing ones
        dropped); a driven plug's is its driver, ``{"from": plug}``."""
        out = {}
        for node, attrs in (section or {}).items():
            if not cmds.objExists(node):
                continue
            for attr in attrs:
                found = cls._plug(node, attr)
                if found is None:
                    continue
                plug = found[1]
                driver = cls._driver(plug)
                if driver:
                    out.setdefault(node, {})[attr] = {"from": driver}
                    continue
                value = cmds.getAttr(plug)
                if isinstance(value, list) and value and isinstance(value[0], tuple):
                    value = list(value[0])
                out.setdefault(node, {})[attr] = value
        return out

    @staticmethod
    def _connect_texture(tex: Dict[str, str], plug: str, name: str) -> str:
        node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=name)
        cmds.setAttr(node + ".ignoreColorSpaceFileRules", 1)
        cmds.setAttr(node + ".fileTextureName", tex["file"], type="string")
        if tex.get("colorSpace"):
            cmds.setAttr(node + ".colorSpace", tex["colorSpace"], type="string")
        cmds.connectAttr(f"{node}.{tex.get('plug') or 'outColor'}", plug, force=True)
        return node

    @classmethod
    def _apply_attrs(cls, node: str, entry: Dict[str, Any], label: str) -> List[str]:
        skipped = []
        for attr, value in (entry.get("attrs") or {}).items():
            if not cmds.attributeQuery(attr.split("[")[0], node=node, exists=True):
                skipped.append(
                    f"{label}.{attr}: a {cmds.nodeType(node)} has no such attribute"
                )
                continue
            cls._set(f"{node}.{attr}", value)
        for attr, spec in (entry.get("user_attrs") or {}).items():
            if not cmds.attributeQuery(attr, node=node, exists=True):
                if spec["type"] == "string":
                    cmds.addAttr(node, longName=attr, dataType="string")
                else:
                    cmds.addAttr(node, longName=attr, attributeType=spec["type"])
            cls._set(f"{node}.{attr}", spec["value"])
        for attr, tex in (entry.get("textures") or {}).items():
            cls._connect_texture(tex, f"{node}.{attr}", f"{label}_{attr}_file")
        return skipped

    @classmethod
    def _build_light(cls, light: Dict[str, Any]) -> Tuple[str, List[str]]:
        made = cmds.shadingNode(light["type"], asLight=True)
        tf = (
            made
            if cmds.nodeType(made) == "transform"
            else cmds.listRelatives(made, parent=True, fullPath=True)[0]
        )
        tf = cmds.rename(tf, light["name"])
        shape = cmds.listRelatives(tf, shapes=True, fullPath=True)[0]
        shape = cmds.rename(shape, light["name"] + "Shape")
        skipped = (
            []
            if tf == light["name"]
            else [f"{light['name']}: the name was taken, built as {tf}"]
        )
        if light.get("rotate_order"):
            cmds.setAttr(tf + ".rotateOrder", light["rotate_order"])
        cmds.xform(tf, worldSpace=True, translation=light.get("pos") or (0, 0, 0))
        if light.get("rot"):
            cmds.xform(tf, worldSpace=True, rotation=light["rot"])
        if light.get("scale"):
            cmds.xform(tf, scale=light["scale"])
        if light.get("visible") is False:
            cmds.setAttr(tf + ".visibility", 0)
        skipped += cls._apply_attrs(shape, light, light["name"])
        for i, filt in enumerate(light.get("filters") or []):
            node = cmds.shadingNode(
                filt["type"], asUtility=True, name=f"{light['name']}_{filt['type']}"
            )
            skipped += cls._apply_attrs(node, filt, f"{light['name']}.{filt['type']}")
            cmds.connectAttr(node + ".message", f"{shape}.aiFilters[{i}]", force=True)
        return tf, skipped

    # ------------------------------------------------------------- values
    #: Maya's rotate orders by index: the axes in the order they apply.
    _ROTATE_ORDERS = ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx")

    @classmethod
    def _rotation_matrix(cls, rot, order: int = 0) -> List[List[float]]:
        """The 3x3 rotation of Euler *rot* (degrees) in Maya rotate order *order*."""
        out = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        for axis in cls._ROTATE_ORDERS[order or 0]:
            a = math.radians(rot["xyz".index(axis)])
            c, s = math.cos(a), math.sin(a)
            m = {
                "x": [[1, 0, 0], [0, c, -s], [0, s, c]],
                "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]],
            }[axis]
            out = [
                [sum(m[i][k] * out[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)
            ]
        return out

    @classmethod
    def _same_rotation(cls, a, order_a: int, b, order_b: int, tolerance: float) -> bool:
        """Whether two Eulers (each in its own rotate order) are one orientation."""
        ma, mb = cls._rotation_matrix(a, order_a), cls._rotation_matrix(b, order_b)
        return all(
            abs(ma[i][j] - mb[i][j]) <= tolerance for i in range(3) for j in range(3)
        )

    @staticmethod
    def _same(a: Any, b: Any, tolerance: float) -> bool:
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) == bool(b)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(
                _LightRigInternal._same(x, y, tolerance) for x, y in zip(a, b)
            )
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(
                _LightRigInternal._same(a[k], b[k], tolerance) for k in a
            )
        if isinstance(a, str) and isinstance(b, str):
            return a == b or os.path.normcase(os.path.normpath(a)) == os.path.normcase(
                os.path.normpath(b)
            )
        return a == b

    @staticmethod
    def _fmt(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.4g}"
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(_LightRigInternal._fmt(v) for v in value) + "]"
        return json.dumps(value) if isinstance(value, (dict, str)) else str(value)

    @classmethod
    def _diff_dict(
        cls, prefix: str, a: Dict[str, Any], b: Dict[str, Any], tolerance: float
    ) -> List[str]:
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in b:
                out.append(f"{prefix}{key} {cls._fmt(a[key])} -> (unset)")
            elif key not in a:
                out.append(f"{prefix}{key} (unset) -> {cls._fmt(b[key])}")
            elif not cls._same(a[key], b[key], tolerance):
                out.append(f"{prefix}{key} {cls._fmt(a[key])} -> {cls._fmt(b[key])}")
        return out

    @staticmethod
    def _plugs(section: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
        return {
            f"{node}.{attr}": v
            for node, attrs in (section or {}).items()
            for attr, v in attrs.items()
        }

    @staticmethod
    def _rounded(value: Any) -> Any:
        if isinstance(value, float):
            return float(f"{value:.5g}") + 0.0  # + 0.0: -0.0 and 0.0 print alike
        if isinstance(value, (list, tuple)):
            return [_LightRigInternal._rounded(v) for v in value]
        if isinstance(value, dict):
            return {k: _LightRigInternal._rounded(v) for k, v in value.items()}
        return value


class LightRig(_LightRigInternal, ptk.LoggingMixin):
    """Save, manage, swap and restore named light rigs (see the module docstring).

    Parameters:
        group: The scene group a rig's lights live under when neither the call
            nor the payload names one.
        store: The rig library; default ``ptk.LightRigPayload.library()``,
            shared with every scene, host and tool.

    Example:
        >>> rig = LightRig(group="MOODY_LIGHTS")
        >>> rig.save("moody_v1", description="first pass")
        >>> rig.apply("moody_v2")   # the scene's rig swapped; the outgoing one kept
        >>> rig.swap_back()         # and back again
    """

    PAYLOAD_VERSION = 1
    DEFAULT_GROUP = "LIGHT_RIG"

    # The host-neutral spelling is pythontk's contract (``ptk.LightRigLight``);
    # these tables map each of its names onto Maya's, and building them KeyErrors
    # on a name the contract gained that nothing here maps yet.
    _MAYA_TYPES = {
        "spot": "spotLight",
        "point": "pointLight",
        "area": "areaLight",
        "aiArea": "aiAreaLight",
        "directional": "directionalLight",
        "photometric": "aiPhotometricLight",
    }
    _MAYA_SETTINGS = {
        "intensity": "intensity",
        "exposure": "aiExposure",
        "cone": "coneAngle",
        "penumbra": "penumbraAngle",
        "dropoff": "dropoff",
        "radius": "aiRadius",
        "spread": "aiSpread",
        "normalize": "aiNormalize",
        "samples": "aiSamples",
        "decay": "decayRate",
        "specular": "aiSpecular",
        "diffuse": "aiDiffuse",
        "volume": "aiVolume",
        "roundness": "aiRoundness",
        "camera": "aiCamera",
        "color": "color",
    }
    #: Neutral light types -> node types.
    TYPES = dict(
        zip(ptk.LightRigLight.TYPES, map(_MAYA_TYPES.__getitem__, ptk.LightRigLight.TYPES))
    )
    #: Neutral settings -> attributes.
    FRIENDLY = dict(
        zip(
            ptk.LightRigLight.SETTINGS,
            map(_MAYA_SETTINGS.__getitem__, ptk.LightRigLight.SETTINGS),
        )
    )
    #: Keys a light may carry that build nothing (look-match notes).
    NOTES = ptk.LightRigLight.NOTES
    _MAYA_FIXTURE_MODES = {
        "glow": (False, False),
        "emissive": (True, False),
        "lamp": (False, True),
    }
    #: Fixture modes (``ptk.LightRigFixture.MODES``) -> ``(its meshes seen by
    #: diffuse rays, its lamps on)``.
    FIXTURE_MODES = dict(
        zip(
            ptk.LightRigFixture.MODES,
            map(_MAYA_FIXTURE_MODES.__getitem__, ptk.LightRigFixture.MODES),
        )
    )
    #: What lets a mesh's emission light the room in Arnold: its visibility to diffuse rays.
    _DIFFUSE_VISIBILITY = (
        "aiVisibleInDiffuseReflection",
        "aiVisibleInDiffuseTransmission",
    )
    #: The contract's structural keys: everything a light declares but its settings and notes.
    _LIGHT_KEYS = tuple(
        f.name
        for f in dataclasses.fields(ptk.LightRigLight)
        if f.name not in ptk.LightRigLight.SETTINGS + ptk.LightRigLight.NOTES
    )
    #: Payload keys the engine reads; every other top-level key is carried untouched.
    _OWN_KEYS = ("version", "group", "lights", "existing", "materials", "applied")

    def __init__(
        self,
        group: str = DEFAULT_GROUP,
        store: Optional["ptk.PresetStore"] = None,
        log_level: str = "WARNING",
    ):
        super().__init__()
        self.logger.setLevel(log_level)
        self.group = group
        self._store = store

    @property
    def store(self) -> "ptk.PresetStore":
        """The rig library."""
        if self._store is None:
            self._store = ptk.LightRigPayload.library()
        return self._store

    # ------------------------------------------------------------- payloads
    @staticmethod
    def aim_rotation(pos, aim) -> List[float]:
        """World rotation (xyz, degrees) pointing a light's -Z from *pos* at *aim*."""
        d = [a - p for a, p in zip(aim, pos)]
        n = math.sqrt(sum(c * c for c in d)) or 1.0
        dx, dy, dz = (c / n for c in d)
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, dy))))
        yaw = math.degrees(math.atan2(-dx, -dz))
        return [pitch, yaw, 0.0]

    @classmethod
    def normalize(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """*payload* in the canonical form (module docstring); look-match spellings translated.

        Raises:
            ValueError: the payload is newer than this version reads, or a light
                carries a key nothing builds from.
        """
        version = payload.get("version", cls.PAYLOAD_VERSION)
        if version > cls.PAYLOAD_VERSION:
            raise ValueError(
                f"Light rig payload version {version} is newer than this mayatk reads "
                f"({cls.PAYLOAD_VERSION}); update mayatk."
            )
        out = copy.deepcopy(payload)
        out["version"] = cls.PAYLOAD_VERSION
        out["lights"] = [
            cls._normalize_light(light) for light in payload.get("lights") or []
        ]
        existing = cls.fixture_overrides(payload.get("fixtures"))
        for node, attrs in (payload.get("existing") or {}).items():  # named outright: wins
            existing.setdefault(node, {}).update(
                {cls.FRIENDLY.get(k, k): v for k, v in attrs.items()}
            )
        out["existing"] = existing
        out["materials"] = copy.deepcopy(payload.get("materials") or {})
        return out

    @classmethod
    def fixture_overrides(
        cls, fixtures: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        """The overrides that put each fixture in its mode (``ptk.LightRigFixture``):
        ``{node: {attr: value}}`` on its meshes (seen by diffuse rays or not)
        and its lamps (on at their intensity, or off). :meth:`normalize` sets
        them as ``existing`` overrides, under what the rig names outright, so
        apply, compare, swap back and undo treat them like any other.

        Raises:
            ValueError: a fixture outside the contract (an unknown mode, no meshes).
        """
        out: Dict[str, Dict[str, Any]] = {}
        for fixture in fixtures or []:
            checked = ptk.LightRigFixture.validate(fixture)
            if checked.errors:
                raise ValueError(
                    f"Fixture {fixture.get('name')!r}: " + "; ".join(checked.errors)
                )
            lit, lamps_on = cls.FIXTURE_MODES[fixture["mode"]]
            for mesh in fixture["meshes"]:
                out.setdefault(mesh, {}).update(
                    {attr: lit for attr in cls._DIFFUSE_VISIBILITY}
                )
            for lamp, intensity in (fixture.get("lamps") or {}).items():
                out.setdefault(lamp, {})["intensity"] = (
                    float(intensity) if lamps_on else 0.0
                )
        return out

    @classmethod
    def _normalize_light(cls, light: Dict[str, Any]) -> Dict[str, Any]:
        unknown = set(light) - set(cls._LIGHT_KEYS) - set(cls.FRIENDLY) - set(cls.NOTES)
        if unknown:
            raise ValueError(
                f"Light {light.get('name')!r}: unknown key(s) {sorted(unknown)}."
            )
        attrs = copy.deepcopy(light.get("attrs") or {})
        for key, attr in cls.FRIENDLY.items():
            if key in light:
                attrs[attr] = copy.deepcopy(light[key])
        if "kelvin" in light and "color" not in attrs:
            attrs.update(aiUseColorTemperature=1, aiColorTemperature=light["kelvin"])
        out = {
            "name": light["name"],
            "type": cls.TYPES.get(light["type"], light["type"]),
            "pos": list(light.get("pos") or (0, 0, 0)),
        }
        if light.get("rot") is not None:
            out["rot"] = list(light["rot"])
        elif light.get("aim") is not None:
            out["rot"] = cls.aim_rotation(out["pos"], light["aim"])
        for key in ("scale", "rotate_order", "visible", "textures", "user_attrs"):
            if light.get(key) not in (None, {}, [], 0) or (
                key == "visible" and light.get(key) is False
            ):
                out[key] = copy.deepcopy(light[key])
        if attrs:
            out["attrs"] = attrs
        filters = copy.deepcopy(light.get("filters") or [])
        if light.get("gobo"):
            gobo = {
                "type": "aiGobo",
                "attrs": {},
                "textures": {"slidemap": {"file": light["gobo"], "colorSpace": "Raw"}},
            }
            if light.get("gobo_rotate") is not None:
                gobo["attrs"]["rotate[0]"] = light[
                    "gobo_rotate"
                ]  # mtoa declares it a multi
            filters.append(gobo)
        if filters:
            out["filters"] = filters
        return out

    @classmethod
    def diff(
        cls, a: Dict[str, Any], b: Dict[str, Any], tolerance: float = 1e-4
    ) -> List[str]:
        """What changes from rig *a* to rig *b*, one line each (empty when they match).

        Floats match within *tolerance* (relative above 1); an attribute set on
        one side only reads as ``(unset)`` -- compare two captures, or a capture
        with a saved capture, for a default-blind comparison.
        """
        a, b = cls.normalize(a), cls.normalize(b)
        la = {light["name"]: light for light in a["lights"]}
        lb = {light["name"]: light for light in b["lights"]}
        out = [f"- {n} ({la[n]['type']})" for n in sorted(set(la) - set(lb))]
        out += [f"+ {n} ({lb[n]['type']})" for n in sorted(set(lb) - set(la))]
        for name in sorted(set(la) & set(lb)):
            x, y = la[name], lb[name]
            if x["type"] != y["type"]:
                out.append(f"{name}: type {x['type']} -> {y['type']}")
            if not cls._same_rotation(
                x.get("rot") or [0, 0, 0],
                x.get("rotate_order", 0),
                y.get("rot") or [0, 0, 0],
                y.get("rotate_order", 0),
                tolerance,
            ):
                out.append(
                    f"{name}: rot {cls._fmt(x.get('rot') or [0, 0, 0])} -> {cls._fmt(y.get('rot') or [0, 0, 0])}"
                )
            for key, default in (
                ("pos", [0, 0, 0]),
                ("scale", [1, 1, 1]),
                ("rotate_order", 0),
                ("visible", True),
            ):
                if not cls._same(x.get(key, default), y.get(key, default), tolerance):
                    out.append(
                        f"{name}: {key} {cls._fmt(x.get(key, default))} -> {cls._fmt(y.get(key, default))}"
                    )
            for key in ("attrs", "textures", "user_attrs"):
                prefix = (
                    f"{name}: "
                    if key == "attrs"
                    else f"{name}: {key[:-1].replace('_', ' ')} "
                )
                out += cls._diff_dict(
                    prefix, x.get(key) or {}, y.get(key) or {}, tolerance
                )
            fx, fy = x.get("filters") or [], y.get("filters") or []
            if [f["type"] for f in fx] != [f["type"] for f in fy]:
                out.append(
                    f"{name}: filters {[f['type'] for f in fx]} -> {[f['type'] for f in fy]}"
                )
                continue
            for i, (f, g) in enumerate(zip(fx, fy)):
                for key in ("attrs", "textures"):
                    out += cls._diff_dict(
                        f"{name}: {f['type']}[{i}] ",
                        f.get(key) or {},
                        g.get(key) or {},
                        tolerance,
                    )
        for section in ("existing", "materials"):
            out += cls._diff_dict(
                f"{section} ",
                cls._plugs(a.get(section)),
                cls._plugs(b.get(section)),
                tolerance,
            )
        return out

    @classmethod
    def fingerprint(cls, payload: Dict[str, Any]) -> str:
        """A short hash of a rig's lights, blind to float noise (what "edited" compares)."""
        lights = cls._rounded(cls.normalize(payload)["lights"])
        return hashlib.sha1(
            json.dumps(lights, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]

    # ------------------------------------------------------------- the scene
    @Units.working_as()
    def capture(self, group: Optional[str] = None) -> Dict[str, Any]:
        """The lights under *group* as a payload (their overrides are not included:
        see :meth:`save`), in centimetres and degrees whatever the scene's units."""
        group = group or self.group
        lights = []
        for shape in self._light_shapes(group):
            tf = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            entry = {
                "name": tf.split("|")[-1],
                "type": cmds.nodeType(shape),
                "pos": cmds.xform(tf, query=True, worldSpace=True, translation=True),
                "rot": cmds.xform(tf, query=True, worldSpace=True, rotation=True),
            }
            scale = cmds.xform(tf, query=True, relative=True, scale=True)
            if any(abs(s - 1.0) > 1e-9 for s in scale):
                entry["scale"] = scale
            if cmds.getAttr(tf + ".rotateOrder"):
                entry["rotate_order"] = cmds.getAttr(tf + ".rotateOrder")
            if not cmds.getAttr(tf + ".visibility"):
                entry["visible"] = False
            entry.update(self._capture_attrs(shape))
            filters = [
                dict({"type": cmds.nodeType(f)}, **self._capture_attrs(f))
                for f in self._filters(shape)
            ]
            if filters:
                entry["filters"] = filters
            lights.append(entry)
        return {
            "version": self.PAYLOAD_VERSION,
            "group": group,
            "lights": sorted(lights, key=lambda light: light["name"]),
        }

    # ------------------------------------------------------------- scene state
    @staticmethod
    def _record() -> Dict[str, Any]:
        """This scene's ``ptk.SceneRecords.LIGHT_RIGS``: ``{group: entry}``."""
        from mayatk.node_utils.data_nodes import DataNodes

        return dict(ptk.SceneRecords.LIGHT_RIGS.load(DataNodes, {}) or {})

    @staticmethod
    def _key(group: str) -> str:
        return group.split("|")[-1]

    def _entry(self, group: str) -> Dict[str, Any]:
        """The record's entry for *group*: ``rig``, ``print``, ``extras``, ``previous``."""
        return dict(self._record().get(self._key(group)) or {})

    def _write_entry(self, group: str, entry: Dict[str, Any]) -> None:
        from mayatk.node_utils.data_nodes import DataNodes

        record = self._record()
        record[self._key(group)] = entry
        ptk.SceneRecords.LIGHT_RIGS.save(DataNodes, record)

    def applied(self, group: Optional[str] = None) -> Tuple[Optional[str], bool]:
        """``(rig name, edited since)`` for the rig in *group*; ``(None, False)`` when none went in."""
        group = group or self.group
        entry = self._entry(group)
        if not cmds.objExists(group) or "print" not in entry:
            return None, False
        name = entry.get("rig") or None
        return name, self.fingerprint(self.capture(group)) != entry["print"]

    @Units.working_as()
    def compare(
        self, rig: Union[str, Dict[str, Any]], group: Optional[str] = None
    ) -> List[str]:
        """What applying *rig* would change in the scene, one line each (empty: nothing).

        Unlike :meth:`diff`, an attribute one side leaves at its default is
        compared as that default, and only the plugs *rig* overrides count.
        The scene is read in the payload's centimetres and degrees.
        """
        target = self.normalize(self.load(rig) if isinstance(rig, str) else rig)
        group = group or target.get("group") or self.group
        scene = self.capture(group)
        landed, _yielded = self.landed_overrides(target)
        for section in ("existing", "materials"):
            target[section] = landed.get(section) or {}
            scene[section] = self._current_values(target[section])
        return self.diff(
            self._with_defaults(scene, target), self._with_defaults(target, scene)
        )

    @classmethod
    def _with_defaults(
        cls, payload: Dict[str, Any], other: Dict[str, Any]
    ) -> Dict[str, Any]:
        """*payload* with every attribute *other* sets on a same-named light filled in at its default."""
        out = cls.normalize(payload)
        twins = {light["name"]: light for light in cls.normalize(other)["lights"]}
        for light in out["lights"]:
            twin = twins.get(light["name"])
            if not twin or twin["type"] != light["type"]:
                continue
            defaults, parents = cls._structure_of(light["type"])
            attrs = light.setdefault("attrs", {})
            for attr in twin.get("attrs") or {}:
                if attr in attrs:
                    continue
                children = [c for c, p in parents.items() if p == attr]
                if children:
                    attrs[attr] = [defaults[c] for c in children]
                elif attr in defaults:
                    attrs[attr] = defaults[attr]
        return out

    def scene_rigs(self) -> List[Tuple[str, Optional[str]]]:
        """``(group, rig name)`` for every rig group in the scene."""
        return [
            (group, entry.get("rig") or None)
            for group, entry in self._record().items()
            if "print" in entry and cmds.objExists(group)
        ]

    def can_swap_back(self, group: Optional[str] = None) -> bool:
        """Whether :meth:`swap_back` has a rig to return to (in *group*, or any group)."""
        record = self._record()
        if group:
            return bool((record.get(self._key(group)) or {}).get("previous"))
        return any(entry.get("previous") for entry in record.values())

    @CoreUtils.undoable(name="Light Rig: Remove")
    def remove(self, group: Optional[str] = None) -> List[str]:
        """Delete *group*, its lights, and the filters and textures only they used."""
        group = group or self.group
        if not cmds.objExists(group):
            return []
        shapes = self._light_shapes(group)
        owned = self._owned_nodes(shapes)
        shape_set = set(shapes)
        doomed = sorted(n for n in owned if not self._used_outside(n, owned, shape_set))
        if doomed:
            cmds.delete(doomed)
        cmds.delete(group)
        return [group] + doomed

    def apply(
        self,
        rig: Union[str, Dict[str, Any]],
        group: Optional[str] = None,
        snapshot: bool = True,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Swap the scene's rig for *rig* (a library name or a payload).

        The lights under the group are replaced; the overrides are set on the
        nodes they name. With *snapshot*, the outgoing rig -- its lights and the
        current values of every plug either rig overrides -- is kept first, so
        :meth:`swap_back` returns to it. One undo reverts the swap. The rig is
        read in centimetres and degrees whatever the scene's units; the scene
        keeps its own.

        Returns:
            dict: ``name``, ``group``, ``lights`` (built), ``skipped``
            (overrides and attributes left alone, with the reason) and
            ``disconnected`` (driven plugs the rig took over; swap back and
            undo reconnect them).
        """
        # A driver the rig hands back is reconnected in these (_set_overrides).
        scene_units = (Units.working("linear"), Units.working("angle"))
        with Units.working_as():
            if isinstance(rig, str):
                name = name or rig
                rig = self.load(rig)
            canon = self.normalize(rig)
            canon.pop("applied", None)
            group = group or canon.get("group") or self.group
            if Plugins.available(
                "mtoa"
            ):  # Arnold light types and ai* attributes exist only with it
                Plugins.load("mtoa")
            kinds = {light["type"] for light in canon["lights"]}
            kinds |= {
                f["type"]
                for light in canon["lights"]
                for f in light.get("filters") or []
            }
            for kind in kinds | {cmds.nodeType(s) for s in self._light_shapes(group)}:
                self._structure_of(kind)  # outside the swap's undo chunk
            previous = None
            if snapshot:
                held, _ = self.applied(group)
                previous = self._snapshot(group, also=canon)
                if held:
                    previous["applied"] = held
            return self._build(canon, group, name, previous, scene_units)

    def swap_back(self, group: Optional[str] = None) -> Dict[str, Any]:
        """Return to the rig the last :meth:`apply` replaced in *group* (again to toggle back).

        With no *group*: the one group in this scene with a rig to return to.

        Raises:
            LookupError: nothing has been replaced in this scene, or several
                groups could swap back and none was named.
        """
        if group is None:
            groups = [g for g, e in self._record().items() if e.get("previous")]
            if len(groups) > 1:
                raise LookupError(
                    f"Several rig groups can swap back ({', '.join(groups)}): name one."
                )
            group = groups[0] if groups else self.group
        previous = self._entry(group).get("previous")
        if not previous:
            raise LookupError(
                "No rig to swap back to: no Apply has replaced one in this scene."
            )
        return self.apply(previous, group=group, name=previous.get("applied"))

    @CoreUtils.undoable(name="Light Rig: Apply")
    def _build(
        self,
        canon: Dict[str, Any],
        group: str,
        name: Optional[str],
        previous: Optional[Dict[str, Any]] = None,
        scene_units: Optional[Tuple[str, str]] = None,
    ) -> Dict[str, Any]:
        report = {
            "name": name,
            "group": group,
            "lights": [],
            "skipped": [],
            "disconnected": [],
        }
        kept = self._entry(group).get("previous")  # a build without a snapshot keeps it
        # The group keeps its place: a scene parks its rig under a group the exporter skips.
        parent = (
            cmds.listRelatives(group, parent=True, fullPath=True)
            if cmds.objExists(group)
            else None
        )
        self.remove(group)
        landed, report["skipped"] = self.landed_overrides(canon)
        for section in ("existing", "materials"):
            for node, attrs in (landed.get(section) or {}).items():
                skipped, disconnected = self._set_overrides(node, attrs, scene_units)
                report["skipped"] += skipped
                report["disconnected"] += disconnected
        made = []
        for light in canon["lights"]:
            tf, skipped = self._build_light(light)
            made.append(tf)
            report["skipped"] += skipped
        grp = (
            cmds.group(made, name=group, world=True)
            if made
            else cmds.group(empty=True, name=group, world=True)
        )
        if parent:
            grp = cmds.parent(grp, parent[0])[0]  # lights keep their world placement
        report["group"] = grp
        report["lights"] = [
            c.split("|")[-1] for c in cmds.listRelatives(grp, children=True) or []
        ]
        entry = {
            "rig": name or "",
            "print": self.fingerprint(self.capture(grp)),
            "extras": {
                k: v
                for k, v in canon.items()
                if k not in ("version", "group", "lights", "applied")
            },
        }
        if previous is not None or kept:
            entry["previous"] = previous if previous is not None else kept
        self._write_entry(grp, entry)  # inside the undo chunk: undo restores it
        return report

    def _snapshot(
        self, group: str, also: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """The rig in *group* as a payload: its lights, the current values of the
        plugs it (and *also*) overrides, and the sections it was applied with."""
        payload = self.capture(group)
        extras = self._entry(group).get("extras") or {}
        for section in ("existing", "materials"):
            plugs = {**{n: dict(a) for n, a in (extras.get(section) or {}).items()}}
            for node, attrs in ((also or {}).get(section) or {}).items():
                plugs.setdefault(node, {}).update(attrs)
            values = self._current_values(plugs)
            if values:
                payload[section] = values
        for key, value in extras.items():
            if key not in ("existing", "materials"):
                payload[key] = value
        return payload

    # ------------------------------------------------------------- the library
    def names(self) -> List[str]:
        """The saved rigs, sorted."""
        return sorted(self.store.list())

    def load(self, name: str) -> Dict[str, Any]:
        """A saved rig's payload. Raises ``KeyError`` when there is none."""
        return self.store.load(name)

    @Units.working_as()
    def save(
        self,
        name: str,
        description: Optional[str] = None,
        group: Optional[str] = None,
        force: bool = False,
    ) -> str:
        """Save the rig in *group* as *name*; returns the file written.

        The payload holds the group's lights, the current values of the plugs
        the rig it came from overrides, and that rig's carried sections, in
        centimetres and degrees whatever the scene's units; the library entry
        records *description* and the rig it came from (``parent``).

        Raises:
            PermissionError: *name* is locked (``PresetReadOnlyError``).
        """
        group = group or self.group
        payload = self._snapshot(group)
        parent, _ = self.applied(group)
        path = self.store.save(name, payload, force=force)
        fields = {"lights": len(payload["lights"]), "group": group}
        if description is not None:
            fields["description"] = description
        if parent and parent != name:
            fields["parent"] = parent
        self.store.set_info(name, **fields)
        if cmds.objExists(group):  # the scene's rig IS this saved rig now
            entry = self._entry(group)
            entry.update(
                rig=name,
                print=self.fingerprint(payload),
                extras={
                    k: v
                    for k, v in payload.items()
                    if k not in ("version", "group", "lights")
                },
            )
            self._write_entry(group, entry)
        return str(path)

    def delete(self, name: str) -> bool:
        """Delete a saved rig (a locked one stays). Returns ``True`` when one went."""
        return self.store.delete(name)

    def rename(self, old: str, new: str) -> bool:
        """Rename a saved rig; ``False`` when *new* is taken or *old* is locked or missing."""
        return self.store.rename(old, new)


class LightRigSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for ``light_rig.ui``: a thin driver over :class:`LightRig`.

    The dropdown is the library. Picking a rig only shows it -- **Apply** puts it
    in the scene and **Swap Back** returns to the rig that replaced. Verbs on a
    rig live on the dropdown (＋ saves the scene's rig; ☰ renames, duplicates,
    locks, compares with the scene and deletes; a double-click renames).
    """

    HELP_URL = "https://github.com/m3trik/mayatk/blob/main/docs/light_rig.md"

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.light_rig
        self.engine = LightRig()
        mgr = ScriptJobManager.instance()
        for event in ("SceneOpened", "NewSceneOpened"):
            try:
                mgr.subscribe(event, self.refresh, owner=self)
            except Exception as e:
                self.logger.debug("Light Rigs: scriptJob %r unavailable (%s)", event, e)
        mgr.connect_cleanup(self.ui, owner=self)
        # The switchboard builds this instance mid-load: widgets are wired after __init__.
        self.sb.QtCore.QTimer.singleShot(0, self.refresh)

    # ------------------------------------------------------------- state
    def _picked(self) -> Optional[str]:
        return self.ui.cmb000.currentData() or None

    def _scene_group(self) -> str:
        """The group a save captures: the scene's rig group, else the engine's default."""
        rigs = self.engine.scene_rigs()
        return rigs[0][0] if rigs else self.engine.group

    def _notify(
        self, message: str, level: str = "info", detail: Optional[str] = None
    ) -> None:
        self.ui.footer.setText(message, level=level)
        {"error": self.logger.error, "warning": self.logger.warning}.get(
            level, self.logger.info
        )(detail or message)

    def refresh(self) -> None:
        """Re-read the library, select the scene's rig, and report the scene."""
        cmb = self.ui.cmb000
        picked = self._picked()
        scene = self.engine.scene_rigs()
        applied = {name for _, name in scene if name}
        cmb.blockSignals(True)
        try:
            cmb.clear()
            for name in self.engine.names():
                info = (
                    self.engine.store.info(name)
                    if self.engine.store.source(name) == "user"
                    else {}
                )
                note = "locked" if info.get("read_only") else (info.get("status") or "")
                index = cmb.add_cells(
                    {"name": name, "lights": str(info.get("lights", "")), "note": note},
                    name,
                )
                tip = [name + ("  (in the scene)" if name in applied else "")]
                if info.get("description"):
                    tip.append(info["description"])
                if info.get("parent"):
                    tip.append(f"from {info['parent']}")
                cmb.setItemData(index, "\n".join(tip), self.sb.QtCore.Qt.ToolTipRole)
            target = (
                picked
                if picked in self.engine.names()
                else next(iter(applied & set(self.engine.names())), None)
            )
            index = cmb.findData(target) if target else -1
            cmb.setCurrentIndex(max(index, 0) if cmb.count() else -1)
            cmb.restore_state = False
        finally:
            cmb.blockSignals(False)
        self.ui.b001.setEnabled(self.engine.can_swap_back())
        if not scene:
            self._notify(
                "No rig in the scene: ＋ saves the lights under a group as one."
            )
            return
        group, name = scene[0]
        _, edited = self.engine.applied(group)
        self._notify(
            f"Scene: {name or 'an unsaved rig'}{' (edited)' if edited else ''} in {group}",
            level="warning" if edited else "info",
        )

    # ------------------------------------------------------------- widgets
    def header_init(self, widget) -> None:
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh)
        widget.menu.add(
            "QPushButton",
            setText="Open Rig Folder",
            setObjectName="open_rig_folder",
            setToolTip="Open the folder the rig library lives in.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Light Rigs",
                body="Save the scene's lights as named rigs, swap between them, and restore any one.",
                steps=[
                    "<b>＋</b> on the dropdown saves the lights under the rig group (and the plugs its rig overrides).",
                    "Pick a rig and press <b>Apply</b>: it replaces the lights in its group. Lights outside the "
                    "group are never deleted; an override on a driven plug takes it over (Swap Back reconnects it), "
                    "and a locked plug is left alone -- both reported.",
                    "<b>Swap Back</b> returns to the rig Apply replaced; press it again to toggle.",
                    "<b>☰</b> renames, duplicates, locks, compares with the scene and deletes; a double-click renames.",
                ],
                notes=[
                    "Rigs are shared JSON presets (light_rigs): every scene, and the look-match tool, see the same library."
                ],
            ),
            url=self.HELP_URL,
        )

    def cmb000_init(self, widget) -> None:
        widget.set_cells(
            [
                {"key": "name", "label": "Rig"},
                {"key": "lights", "label": "Lights", "editable": False},
                {"key": "note", "label": "", "editable": False},
            ],
            cell_format="{name}",
        )
        widget.option_box.add_action(
            callback=self.prompt_save,
            icon="add",
            tooltip="Save the scene's rig… (the lights under its group)",
        )
        widget.option_box.add_action(
            callback=self._show_rig_menu,
            icon="menu",
            tooltip="Rename, duplicate, lock, compare with the scene or delete the rig shown.",
        )
        widget.setPlaceholderText("No saved rigs: ＋ saves the scene's rig")
        widget.rename_on_double_click = True
        widget.on_cells_edited.connect(self._on_cells_edited)
        widget.before_popup_shown.connect(self.refresh)
        widget.setContextMenuPolicy(self.sb.QtCore.Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos: self._show_rig_menu(widget.mapToGlobal(pos))
        )

    def cmb000(self, index, widget) -> None:
        """Show the picked rig (picking never changes the scene)."""
        name = widget.currentData()
        if not name:
            return
        info = (
            self.engine.store.info(name)
            if self.engine.store.source(name) == "user"
            else {}
        )
        desc = f" — {info['description']}" if info.get("description") else ""
        self.ui.footer.setText(
            f"{name}: {info.get('lights', '?')} lights in {info.get('group', '?')}{desc}"
        )

    def b000(self) -> None:
        """Apply the picked rig: it replaces the scene's rig (Swap Back returns)."""
        name = self._picked()
        if not name:
            self._notify("Pick a rig to apply.", "warning")
            return
        try:
            report = self.engine.apply(name)
        except (KeyError, ValueError) as e:
            self._notify(f"Could not apply {name}: {e}", "error")
            return
        self.refresh()
        notes = report["disconnected"] + report["skipped"]
        if notes:
            self._notify(
                f"Applied {name}; {len(report['disconnected'])} driven plug(s) taken over "
                f"(Swap Back reconnects), {len(report['skipped'])} override(s) left alone.",
                "warning",
                detail="\n".join(notes),
            )
        else:
            self._notify(
                f"Applied {name}: {len(report['lights'])} lights in {report['group']}.",
                "success",
            )

    def b001(self) -> None:
        """Swap Back: return to the rig the last Apply replaced."""
        try:
            report = self.engine.swap_back()
        except LookupError as e:
            self._notify(str(e), "warning")
            return
        self.refresh()
        self._notify(
            f"Swapped back to {report['name'] or 'the unsaved rig'}.", "success"
        )

    def open_rig_folder(self) -> None:
        """Header menu: the folder the rig library lives in."""
        ptk.FileUtils.open_explorer(
            str(self.engine.store.user_dir), create_dir=True, logger=self.logger
        )

    # ------------------------------------------------------------- verbs
    def _form(
        self,
        title: str,
        ok: str,
        name: str = "",
        description: str = "",
        group: Optional[str] = None,
    ):
        from uitk.widgets.formPanel import FormPanel

        fields = [
            {
                "name": "name",
                "label": "Name",
                "value": name,
                "placeholder": "e.g. moody_v3",
            },
            {
                "name": "description",
                "label": "Description",
                "value": description,
                "placeholder": "Optional",
            },
        ]
        if group is not None:
            fields.append(
                {
                    "name": "group",
                    "label": "Group",
                    "value": group,
                    "hint": "The scene group whose lights are the rig.",
                }
            )

        def validate(values) -> str:
            n = values["name"].strip()
            return "" if n else "Name the rig."

        return FormPanel(
            fields,
            title=title,
            parent=self.ui,
            ok_text=ok,
            validate=validate,
            output=False,
        )

    def _ask(self, title: str, text: str) -> bool:
        Q = self.sb.QtWidgets.QMessageBox
        return Q.question(self.ui, title, text, Q.Yes | Q.No, Q.No) == Q.Yes

    def prompt_save(self) -> None:
        """＋: save the scene's rig under a new (or existing) name."""
        form = self._form(
            "Save rig", "Save", name=self._picked() or "", group=self._scene_group()
        )
        try:
            if not form.exec_panel():
                return
            values = form.values()
        finally:
            form.deleteLater()
        name, group = (
            values["name"].strip(),
            values["group"].strip() or self.engine.group,
        )
        if self.engine.store.exists(name) and not self._ask(
            "Replace rig?", f"Replace the saved rig {name}?"
        ):
            return
        try:
            self.engine.save(
                name,
                description=values["description"].strip() or None,
                group=group,
                force=False,
            )
        except PermissionError:
            self._notify(
                f"{name} is locked: unlock it (☰) or save under another name.", "error"
            )
            return
        self.refresh()
        self.ui.cmb000.setCurrentIndex(max(self.ui.cmb000.findData(name), 0))
        self._notify(f"Saved {name}.", "success")

    def _show_rig_menu(self, pos=None) -> None:
        from uitk.widgets.context_menu import ContextMenu

        cmb, name = self.ui.cmb000, self._picked()
        menu = ContextMenu(parent=cmb)
        if not name:
            menu.add("Save a rig first (＋)", setEnabled=False)
        else:
            store = self.engine.store
            user = store.source(name) == "user"
            locked = user and bool(store.info(name).get("read_only"))
            menu.add_separator(name)
            menu.add(
                "Rename…", callback=cmb.begin_rename, setEnabled=user and not locked
            )
            menu.add("Duplicate…", callback=lambda: self._duplicate(name))
            menu.add(
                "Edit Description…",
                callback=lambda: self._describe(name),
                setEnabled=user,
            )
            menu.add(
                "Unlock" if locked else "Lock",
                callback=lambda: self._lock(name, not locked),
                setEnabled=user,
                setToolTip="A locked rig cannot be saved over, renamed or deleted.",
            )
            menu.add(
                "Compare With Scene",
                callback=lambda: self._compare(name),
                setToolTip="What applying it would change in the scene's rig.",
            )
            menu.add_separator()
            menu.add(
                "Delete",
                callback=lambda: self._delete(name),
                setEnabled=user and not locked,
            )
        if pos is None:
            pos = cmb.mapToGlobal(self.sb.QtCore.QPoint(0, cmb.height()))
        menu.exec_(pos)

    def _on_cells_edited(self, index: int, cells: dict) -> None:
        old = self.ui.cmb000.itemData(index)
        new = (cells.get("name") or "").strip()
        if old and new and new != old:
            if self.engine.rename(old, new):
                self._notify(f"Renamed {old} to {new}.", "success")
            else:
                self._notify(
                    f"Could not rename {old}: {new} is taken, or {old} is locked.",
                    "error",
                )
        self.refresh()

    def _duplicate(self, name: str) -> None:
        form = self._form(f"Duplicate {name}", "Duplicate", name=f"{name}_copy")
        try:
            if not form.exec_panel():
                return
            values = form.values()
        finally:
            form.deleteLater()
        new = values["name"].strip()
        if self.engine.store.exists(new):
            self._notify(f"{new} already exists.", "error")
            return
        self.engine.store.save(new, self.engine.load(name))
        info = (
            self.engine.store.info(name)
            if self.engine.store.source(name) == "user"
            else {}
        )
        self.engine.store.set_info(
            new,
            parent=name,
            description=values["description"].strip() or info.get("description"),
            lights=info.get("lights"),
            group=info.get("group"),
        )
        self.refresh()

    def _describe(self, name: str) -> None:
        form = self._form(
            f"Describe {name}",
            "Save",
            name=name,
            description=self.engine.store.info(name).get("description") or "",
        )
        try:
            if not form.exec_panel():
                return
            values = form.values()
        finally:
            form.deleteLater()
        self.engine.store.set_info(
            name, description=values["description"].strip() or None
        )
        self.refresh()

    def _lock(self, name: str, state: bool) -> None:
        self.engine.store.set_info(name, read_only=True if state else None)
        self.refresh()

    def _compare(self, name: str) -> None:
        group = (
            self.engine.normalize(self.engine.load(name)).get("group")
            or self._scene_group()
        )
        changes = self.engine.compare(name, group)
        if not changes:
            self._notify(f"{name} matches the scene's rig in {group}.", "success")
            return
        shown = changes[:25] + (
            [f"(+{len(changes) - 25} more in the Script Editor)"]
            if len(changes) > 25
            else []
        )
        self.logger.warning("Scene -> %s:\n%s", name, "\n".join(changes))
        self.sb.message_box(
            f"Applying {name} would change {len(changes)} thing(s):\n"
            + "\n".join(shown),
            "Ok",
        )

    def _delete(self, name: str) -> None:
        if not self._ask(
            "Delete rig?", f"Delete the saved rig {name}? The scene is not changed."
        ):
            return
        self._notify(
            f"Deleted {name}." if self.engine.delete(name) else f"{name} is locked.",
            "success" if not self.engine.store.exists(name) else "error",
        )
        self.refresh()


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("light_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
