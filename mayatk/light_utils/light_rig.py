# !/usr/bin/python
# coding=utf-8
"""Light Rig manager: save, manage, swap and restore named light rigs.

The engine is pythontk's, written once (``ptk.LightRig``): :class:`MayaLightRigHost`
is what it asks of Maya, and :class:`LightRig` binds it with Maya's tables and
overrides nothing. Its panel is tentacle's Light Rigs tool
(``slots/tools/light_rig.py``), which tentacle's Lighting menu opens, in Maya
and Blender alike (blendertk binds the same engine).

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

from typing import Any, ContextManager, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.core_utils.units import Units
from mayatk.light_utils._light_utils import LightUtils


class _MayaLightRigHostInternal(object):
    """Maya reads and writes behind :class:`MayaLightRigHost`."""

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
            if ptk.LightRig.same(value, default, 1e-6):
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
        :meth:`_plug` resolves it (callers pass ``LightRig.landed_overrides``).
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


class MayaLightRigHost(_MayaLightRigHostInternal):
    """The ``ptk.LightRigHost`` Maya is: scene groups of lights, plugs, and the
    ``light_rigs`` record on the scene store (``DataNodes``)."""

    package = "mayatk"

    def payload_units(self) -> ContextManager:
        """Centimetres and degrees, whatever the scene works in."""
        return Units.working_as()

    def scene_units(self) -> Tuple[str, str]:
        """``(linear, angle)``: what a driver the rig hands back reconnects in."""
        return Units.working("linear"), Units.working("angle")

    def undo_step(self, name: str) -> ContextManager:
        return CoreUtils.undo_chunk(name)

    def record_store(self) -> Any:
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes

    def exists(self, node: str) -> bool:
        return bool(cmds.objExists(node))

    def defaults(self, kind: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
        return self._structure_of(kind)

    def prepare(self, kinds: set, group: str) -> None:
        """Load MtoA where it is installed (Arnold light types and ``ai*``
        attributes exist only with it), and read each type's structure."""
        if Plugins.available("mtoa"):
            Plugins.load("mtoa")
        for kind in kinds | {cmds.nodeType(s) for s in self._light_shapes(group)}:
            self._structure_of(kind)

    def capture_lights(self, group: str) -> List[Dict[str, Any]]:
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
        return lights

    def remove_group(self, group: str) -> List[str]:
        """Delete *group*, its lights, and the filters and textures only they used."""
        shapes = self._light_shapes(group)
        owned = self._owned_nodes(shapes)
        shape_set = set(shapes)
        doomed = sorted(n for n in owned if not self._used_outside(n, owned, shape_set))
        if doomed:
            cmds.delete(doomed)
        cmds.delete(group)
        return [group] + doomed

    def group_parent(self, group: str) -> Optional[str]:
        parent = cmds.listRelatives(group, parent=True, fullPath=True)
        return parent[0] if parent else None

    def group_lights(
        self, made: List[str], group: str, parent: Optional[str]
    ) -> Tuple[str, List[str]]:
        grp = (
            cmds.group(made, name=group, world=True)
            if made
            else cmds.group(empty=True, name=group, world=True)
        )
        if parent:
            grp = cmds.parent(grp, parent)[0]  # lights keep their world placement
        return grp, [c.split("|")[-1] for c in cmds.listRelatives(grp, children=True) or []]

    def build_light(self, light: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        return self._build_light(light)

    def gobo_filter(self, path: str, rotate: Optional[float]) -> Dict[str, Any]:
        """An ``aiGobo`` with *path* on its slide map (raw colour)."""
        gobo = {
            "type": "aiGobo",
            "attrs": {},
            "textures": {"slidemap": {"file": path, "colorSpace": "Raw"}},
        }
        if rotate is not None:
            gobo["attrs"]["rotate[0]"] = rotate  # mtoa declares it a multi
        return gobo

    def plug(self, node: str, attr: str) -> Optional[Tuple[str, str]]:
        return self._plug(node, attr)

    def plug_key(self, plug: str) -> str:
        return self._plug_key(plug)

    def set_overrides(
        self, node: str, attrs: Dict[str, Any], scene_units: Any = None
    ) -> Tuple[List[str], List[str]]:
        return self._set_overrides(node, attrs, scene_units)

    def current_values(
        self, section: Optional[Dict[str, Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        return self._current_values(section)


class LightRig(ptk.LightRig):
    """Save, manage, swap and restore named light rigs (see the module docstring
    and ``ptk.LightRig``).

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

    HOST = MayaLightRigHost()
    HOST_TYPES = {
        "spot": "spotLight",
        "point": "pointLight",
        "area": "areaLight",
        "aiArea": "aiAreaLight",
        "directional": "directionalLight",
        "photometric": "aiPhotometricLight",
    }
    HOST_SETTINGS = {
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
    HOST_FIXTURE_MODES = {
        "glow": (False, False),
        "emissive": (True, False),
        "lamp": (False, True),
    }
    #: What lets a mesh's emission light the room in Arnold: its visibility to diffuse rays.
    DIFFUSE_VISIBILITY = (
        "aiVisibleInDiffuseReflection",
        "aiVisibleInDiffuseTransmission",
    )
    KELVIN_ATTRS = ("aiUseColorTemperature", "aiColorTemperature")
