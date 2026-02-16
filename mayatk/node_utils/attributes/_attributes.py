# !/usr/bin/python
# coding=utf-8
"""Consolidated attribute utilities for Maya.

Provides ``Attributes`` — a single authoritative class for creating,
querying, setting, connecting, locking, and filtering Maya node attributes.
Includes a YAML-based preset system for templated attribute bundles.
"""
import contextlib
import fnmatch
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

try:
    import pymel.core as pm
except ImportError:
    pm = None  # type: ignore[assignment]

import pythontk as ptk


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class AttributeTemplate:
    """Defines the configuration for a Maya attribute."""

    def __init__(
        self,
        long_name: str,
        attribute_type: str = "float",
        keyable: bool = True,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        default_value: Optional[Any] = None,
        enum_names: Optional[List[str]] = None,
    ):
        self.long_name = long_name
        self.attribute_type = attribute_type
        self.keyable = keyable
        self.min_value = min_value
        self.max_value = max_value
        self.default_value = default_value
        self.enum_names = enum_names

    def __repr__(self):
        return f"<AttributeTemplate '{self.long_name}' ({self.attribute_type})>"


class Preset(NamedTuple):
    """A named bundle of attributes loaded from a YAML template."""

    description: str
    templates: List[AttributeTemplate]


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class Attributes(ptk.HelpMixin):
    """Consolidated utility for managing Maya node attributes.

    Covers creation, querying, setting, connecting, locking/unlocking,
    filtering, and YAML-based preset management.
    """

    Template = AttributeTemplate
    Preset = Preset

    PRESETS: Dict[str, Preset] = {}
    """Attribute presets keyed by name, auto-loaded from ``templates/*.yaml``."""

    _DATA_TYPES = frozenset(
        {
            "string",
            "stringArray",
            "matrix",
            "doubleArray",
            "int32Array",
            "vectorArray",
            "pointArray",
            "mesh",
            "lattice",
            "nurbsCurve",
            "nurbsSurface",
        }
    )

    # ======================================================================
    # YAML preset system
    # ======================================================================

    @classmethod
    def _load_templates(cls) -> Dict[str, Preset]:
        """Discover and parse YAML template files from the ``templates/`` directory."""
        try:
            import yaml
        except ImportError:
            return {}

        templates_dir = Path(__file__).parent / "templates"
        if not templates_dir.exists():
            return {}

        presets: Dict[str, Preset] = {}
        for path in sorted(templates_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[{cls.__name__}] Skipping {path.name}: {exc}")
                continue

            attrs = []
            for entry in data.get("attributes", []):
                attrs.append(
                    AttributeTemplate(
                        long_name=entry["long_name"],
                        attribute_type=entry.get("type", "float"),
                        keyable=entry.get("keyable", True),
                        min_value=entry.get("min"),
                        max_value=entry.get("max"),
                        default_value=entry.get("default"),
                        enum_names=entry.get("enum_names"),
                    )
                )
            presets[path.stem] = Preset(
                description=data.get("description", ""),
                templates=attrs,
            )
        return presets

    @classmethod
    def apply_preset(cls, name: str, objects) -> List[str]:
        """Look up a named preset and create its attributes on *objects*.

        Parameters:
            name: Key in ``PRESETS``.
            objects: Maya objects to receive the attributes.

        Returns:
            List of ``"node.attr"`` strings for every attribute created.

        Raises:
            KeyError: If *name* is not found in ``PRESETS``.
        """
        if name not in cls.PRESETS:
            available = ", ".join(sorted(cls.PRESETS)) or "(none)"
            raise KeyError(
                f"Preset '{name}' not found. Available: {available}. "
                f"Ensure templates/{name}.yaml exists."
            )
        preset = cls.PRESETS[name]
        added: List[str] = []
        for template in preset.templates:
            added.extend(cls.create_attributes(objects, template))
        return added

    @classmethod
    def remove_preset(cls, name: str, objects) -> None:
        """Remove a preset's attributes from *objects*.

        Disconnects any incoming anim-curve connections before deletion.
        Domain-specific cleanup (e.g. condition nodes for render-opacity)
        is the caller's responsibility.

        Raises:
            KeyError: If *name* is not found in ``PRESETS``.
        """
        if name not in cls.PRESETS:
            available = ", ".join(sorted(cls.PRESETS)) or "(none)"
            raise KeyError(
                f"Preset '{name}' not found. Available: {available}. "
                f"Ensure templates/{name}.yaml exists."
            )
        preset = cls.PRESETS[name]
        for template in preset.templates:
            attr_name = template.long_name
            for obj in pm.ls(objects):
                if not obj.hasAttr(attr_name):
                    continue
                curves = pm.listConnections(obj.attr(attr_name), type="animCurve")
                if curves:
                    pm.delete(curves)
                obj.deleteAttr(attr_name)

    @classmethod
    def create_attributes(cls, objects, template: AttributeTemplate) -> List[str]:
        """Apply an ``AttributeTemplate`` to a list of objects."""
        added: List[str] = []
        for obj in pm.ls(objects):
            if cls.ensure_attribute(obj, template):
                added.append(f"{obj.name()}.{template.long_name}")
        return added

    @classmethod
    def ensure_attribute(cls, obj, template: AttributeTemplate) -> bool:
        """Create an attribute on *obj* from *template* if it doesn't already exist."""
        if obj.hasAttr(template.long_name):
            return True

        kwargs: dict = {
            "longName": template.long_name,
            "keyable": template.keyable,
        }

        if template.attribute_type in cls._DATA_TYPES:
            kwargs["dataType"] = template.attribute_type
        elif template.attribute_type == "enum":
            kwargs["attributeType"] = "enum"
            if template.enum_names:
                kwargs["enumName"] = ":".join(template.enum_names)
        else:
            kwargs["attributeType"] = template.attribute_type

        if template.min_value is not None:
            kwargs["minValue"] = template.min_value
        if template.max_value is not None:
            kwargs["maxValue"] = template.max_value
        if template.default_value is not None:
            kwargs["defaultValue"] = template.default_value

        try:
            pm.addAttr(obj, **kwargs)
            attr = obj.attr(template.long_name)

            if template.keyable:
                attr.setKeyable(True)
            else:
                attr.showInChannelBox(True)

            if template.default_value is not None:
                attr.set(template.default_value)

            return True
        except Exception as e:
            print(
                f"[{cls.__name__}] Failed to add attribute "
                f"{template.long_name} to {obj}: {e}"
            )
            return False

    # ======================================================================
    # Query / Get
    # ======================================================================

    @staticmethod
    def get_attributes(
        node, inc=None, exc=None, exc_defaults=False, quiet=True, **kwargs
    ) -> dict:
        """Retrieve a node's attributes and their current values.

        Parameters:
            node (pm.nt.DependNode): The target node.
            inc (list, optional): Attribute names to include.
            exc (list, optional): Attribute names to exclude (takes priority).
            exc_defaults (bool): If True, exclude attributes still at their default value.
            quiet (bool): Suppress errors during attribute processing.
            **kwargs: Forwarded to ``pm.listAttr``.

        Returns:
            dict: ``{attr_name: value, ...}``
        """
        if inc is None:
            inc = []
        if exc is None:
            exc = []

        list_attr_kwargs = {
            "read": True,
            "hasData": True,
            "settable": True,
            "scalarAndArray": True,
            "multi": True,
        }
        list_attr_kwargs.update(kwargs)

        all_attr_names = pm.listAttr(node, **list_attr_kwargs)
        if exc_defaults:
            for attr_name in all_attr_names:
                try:
                    defaults = pm.attributeQuery(attr_name, node=node, listDefault=True)
                    if defaults:
                        default_value = defaults[0]
                        current_value = pm.getAttr(f"{node}.{attr_name}")
                        if current_value == default_value or (
                            isinstance(current_value, float)
                            and abs(current_value - default_value) < 1e-6
                        ):
                            exc.append(attr_name)
                except Exception:
                    continue

        filtered_attr_names = ptk.filter_list(
            pm.listAttr(node, **list_attr_kwargs), inc, exc
        )

        result: dict = {}
        for attr_name in filtered_attr_names:
            try:
                result[attr_name] = pm.getAttr(f"{node}.{attr_name}")
            except Exception as e:
                if not quiet:
                    print(f"Error processing attribute '{attr_name}' on '{node}': {e}")

        return result

    @classmethod
    def get_type(cls, value) -> str:
        """Determine the Maya attribute type string for a given Python value.

        Parameters:
            value: A Python value (bool, int, float, str, list, tuple, pm.Matrix …).

        Returns:
            str: The corresponding Maya attribute type name (e.g. ``'bool'``,
            ``'double3'``, ``'stringArray'``, ``'matrix'``).

        Raises:
            TypeError: If the value type is unsupported.
        """
        if isinstance(value, bool):
            return "bool"
        elif isinstance(value, int):
            return "long"
        elif isinstance(value, float):
            return "double"
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, (list, tuple, set)):
            element_type = cls.get_type(value[0])
            length = len(value)
            if element_type in ("double", "float", "long", "short"):
                if length == 2:
                    return f"{element_type}2"
                elif length == 3:
                    return f"{element_type}3"
                else:
                    return f"{element_type}Array"
            elif element_type == "string":
                return "stringArray"
            elif element_type == "vector":
                return "vectorArray"
            elif element_type == "point":
                return "pointArray"
            else:
                return "compound"
        elif isinstance(value, pm.Matrix):
            if value.type == "float":
                return "fltMatrix"
            elif value.type == "double":
                return "matrix"
        return "compound"

    @staticmethod
    def get_selected_channels() -> List[str]:
        """Get attributes selected in the channel box.

        Returns:
            list[str]: e.g. ``['tx', 'ry', 'sz']``
        """
        from mayatk.env_utils.channel_box import ChannelBox

        return ChannelBox.get_selected_attrs(sections="main")

    @staticmethod
    def get_channel_box_values(
        objects,
        *args,
        include_locked=False,
        include_nonkeyable=False,
        include_object_name=False,
        as_group=False,
    ) -> dict:
        """Retrieve current channel-box attribute values for *objects*.

        Parameters:
            objects: Objects to query.
            *args (str): Specific attributes to query; defaults to the
                channel-box selection.
            include_locked (bool): Include locked attributes.
            include_nonkeyable (bool): Include non-keyable attributes.
            include_object_name (bool): Prefix keys with object name.
            as_group (bool): If True, return a flat dict (later objects
                overwrite earlier); otherwise nested per-object dicts.

        Returns:
            dict: Per-object or flat attribute→value mapping.
        """
        channel_box = pm.melGlobals["gChannelBoxName"]
        attributes_dict: dict = {}

        for obj in pm.ls(objects):
            if args:
                attrs = list(args)
            else:
                attrs = pm.channelBox(channel_box, query=True, sma=True) or []

            if include_locked:
                attrs += pm.listAttr(obj, locked=True)
            if include_nonkeyable:
                attrs += pm.listAttr(obj, keyable=False)

            if as_group:
                for attr in attrs:
                    attr_name = f"{obj}.{attr}" if include_object_name else attr
                    attributes_dict[attr_name] = pm.getAttr(f"{obj}.{attr}")
            else:
                obj_attrs = {}
                for attr in attrs:
                    obj_attrs[attr] = pm.getAttr(f"{obj}.{attr}")
                if obj_attrs:
                    attributes_dict[str(obj)] = obj_attrs

        return attributes_dict

    # ======================================================================
    # Set / Create
    # ======================================================================

    @classmethod
    def set_attributes(
        cls,
        node,
        create: bool = False,
        quiet: bool = False,
        keyable: bool = False,
        lock: bool = False,
        **attributes,
    ) -> None:
        """Set values on existing node attributes.

        Parameters:
            node (str/obj): Target node.
            create (bool): If True, create missing attributes via
                :meth:`create_or_set`.
            quiet (bool): Suppress warnings on failure.
            keyable (bool): Make attribute keyable.
            lock (bool): Lock the attribute after setting.
            **attributes: ``attr_name=value`` pairs.
        """
        if isinstance(node, str):
            node = pm.PyNode(node)

        for attr, value in attributes.items():
            try:
                if node.attr(attr).isLocked():
                    pm.warning(f"The attribute '{node}.{attr}' is locked.")
                    continue
                pm.setAttr(node.attr(attr), value, keyable=keyable, lock=lock)
            except pm.MayaAttributeError:
                if create:
                    cls.create_or_set(node, keyable=keyable, **{attr: value})
                elif not quiet:
                    pm.warning(f"Attribute '{attr}' does not exist on '{node}'.")
            except Exception as e:
                if not quiet:
                    pm.warning(f"Failed to set '{attr}' on '{node}': {e}")

    @classmethod
    def create_or_set(cls, node, keyable=True, **attributes) -> None:
        """Set attribute values, creating them first if they don't exist.

        Handles compound (double3, float2 …) and data-type attributes
        (string, stringArray, matrix …) automatically.

        Parameters:
            node (str/obj): Target node.
            keyable (bool): Make new attributes keyable.
            **attributes: ``attr_name=value`` pairs.
        """
        if isinstance(node, str):
            node = pm.PyNode(node)

        for attr, value in attributes.items():
            if not pm.attributeQuery(attr, node=node, exists=True):
                cls._create_attr(node, attr, value, keyable)
            cls._set_value(node, attr, value)

    @classmethod
    def _create_attr(cls, node, attr_name, value, keyable):
        """Create an attribute structure on *node* without setting its value."""
        attr_type = cls.get_type(value)

        if attr_type.endswith("3") or attr_type.endswith("2"):
            node.addAttr(
                attr_name,
                numberOfChildren=len(value),
                attributeType="compound",
            )
            suffixes = ["X", "Y", "Z"] if attr_type.endswith("3") else ["X", "Y"]
            child_type = attr_type[:-1]
            for i in range(len(value)):
                node.addAttr(
                    f"{attr_name}{suffixes[i]}",
                    attributeType=child_type,
                    parent=attr_name,
                )
        elif attr_type in cls._DATA_TYPES:
            node.addAttr(attr_name, keyable=keyable, dataType=attr_type)
        else:
            node.addAttr(
                attr_name,
                defaultValue=value,
                keyable=keyable,
                attributeType=attr_type,
            )

    @classmethod
    def _set_value(cls, node, attr_name, value):
        """Set an attribute value, handling compound children automatically."""
        attr_type = cls.get_type(value)

        if attr_type.endswith("3") or attr_type.endswith("2"):
            suffixes = ["X", "Y", "Z"] if attr_type.endswith("3") else ["X", "Y"]
            for i, comp in enumerate(value):
                pm.setAttr(f"{node}.{attr_name}{suffixes[i]}", comp)
        else:
            pm.setAttr(f"{node}.{attr_name}", value)

    @staticmethod
    def create_switch(
        node,
        attr_name: str,
        weighted: bool = False,
        min_value: float = 0.0,
        max_value: float = 1.0,
    ):
        """Create a bool or float (weighted) switch attribute if it doesn't exist.

        Parameters:
            node (pm.PyNode): Node to add the attribute to.
            attr_name (str): Attribute name.
            weighted (bool): Float 0–1 if True, bool if False.
            min_value (float): Min for weighted attribute.
            max_value (float): Max for weighted attribute.

        Returns:
            pm.Attribute: The created or existing attribute.
        """
        if node.hasAttr(attr_name):
            return node.attr(attr_name)

        if weighted:
            pm.addAttr(
                node,
                ln=attr_name,
                at="double",
                min=min_value,
                max=max_value,
                k=True,
                dv=0,
            )
        else:
            pm.addAttr(node, ln=attr_name, at="bool", k=True, dv=0)
        return node.attr(attr_name)

    # ======================================================================
    # Connect
    # ======================================================================

    @staticmethod
    def connect(attr: str, place: str, file: str) -> None:
        """Connect a same-named attribute between two nodes.

        Parameters:
            attr: Attribute name shared by both nodes.
            place: Source (placement) node name.
            file: Destination (file) node name.
        """
        pm.connectAttr(f"{place}.{attr}", f"{file}.{attr}", f=True)

    @staticmethod
    def connect_multi(*args, force=True) -> None:
        """Connect multiple attribute pairs at once.

        Parameters:
            *args: Two-element tuples ``(src_attr, dst_attr)``.
            force (bool): Force connection.

        Example:
            connect_multi(
                (node1.outColor, node2.aiSurfaceShader),
                (node1.outColor, node3.baseColor),
            )
        """
        for frm, to in args:
            try:
                pm.connectAttr(frm, to, force=force)
            except Exception as error:
                print(f"# Error: {__file__} {error} #")

    # ======================================================================
    # Trace / Upstream
    # ======================================================================

    @staticmethod
    def _classify_source(node, node_type, NodeUtils):
        """Classify a source node into a semantic driver type.

        Returns:
            ``(node, type_str)`` or ``None`` if unrecognized.
        """
        if NodeUtils.is_constraint(node):
            return node, "constraint"
        if NodeUtils.is_expression(node):
            return node, "expression"
        if node_type.startswith("animCurve"):
            if NodeUtils.is_driven_key_curve(node):
                return node, "driven_key"
            return node, "keyframe"
        if NodeUtils.is_ik_effector(node):
            return node, "ik"
        if node_type in ("motionPath", "ikHandle"):
            return node, "motion_path" if node_type == "motionPath" else "ik"
        return None

    @classmethod
    def trace_upstream(
        cls,
        plug: str,
        passthrough_types: Optional[set] = None,
        visited: Optional[set] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Trace upstream through passthrough nodes to find the true driver.

        Recursively walks through intermediate utility nodes (pairBlend,
        unitConversion, multiplyDivide, etc.) to find the actual source.

        Parameters:
            plug: Destination plug, e.g. ``"pCube1.translateX"``.
            passthrough_types: Node types to trace through. Uses a
                sensible default set if ``None``.
            visited: Internal cycle-detection set.

        Returns:
            ``(source_node, source_type)`` where *source_type* is one of
            ``"constraint"``, ``"expression"``, ``"driven_key"``,
            ``"keyframe"``, ``"ik"``, ``"motion_path"``, or the raw
            ``nodeType`` string.  ``(None, None)`` if nothing found.
        """
        import maya.cmds as cmds
        from mayatk.node_utils._node_utils import NodeUtils

        if passthrough_types is None:
            passthrough_types = {
                "pairBlend",
                "blendWeighted",
                "blendColors",
                "blendTwoAttr",
                "unitConversion",
                "unitToTimeConversion",
                "timeToUnitConversion",
                "reverse",
                "multiplyDivide",
                "plusMinusAverage",
                "addDoubleLinear",
                "multDoubleLinear",
                "condition",
                "remapValue",
                "clamp",
                "setRange",
                "animBlendNodeAdditive",
                "animBlendNodeAdditiveDA",
                "animBlendNodeAdditiveRotation",
                "animBlendNodeAdditiveScale",
                "animBlendNodeAdditiveDL",
                "animBlendNodeBase",
            }

        if visited is None:
            visited = set()

        if plug in visited:
            return None, None
        visited.add(plug)

        sources = cmds.listConnections(plug, source=True, destination=False) or []
        if not sources:
            return None, None

        source = sources[0]
        node_type = cmds.nodeType(source)

        if node_type in passthrough_types:
            input_plugs = (
                cmds.listConnections(source, source=True, destination=False, plugs=True)
                or []
            )
            for inp in input_plugs:
                inp_node = inp.split(".")[0]
                if inp_node in visited:
                    continue
                inp_type = cmds.nodeType(inp_node)

                classified = cls._classify_source(inp_node, inp_type, NodeUtils)
                if classified:
                    return classified

                result = cls.trace_upstream(inp, passthrough_types, visited)
                if result[0]:
                    return result

            return None, None

        classified = cls._classify_source(source, node_type, NodeUtils)
        return classified if classified else (source, node_type)

    # ======================================================================
    # Lock / Unlock
    # ======================================================================

    @staticmethod
    def _resolve_lock_target(obj):
        """Return the effective transform to lock/unlock.

        If *obj* is a locator, returns its first child transform (the
        control underneath).  Returns ``None`` when no valid target exists.
        """
        shape = obj.getShape()
        if shape and shape.nodeType() == "locator":
            children = pm.listRelatives(obj, children=True, type="transform")
            return children[0] if children else None
        return obj

    @classmethod
    def get_lock_state(cls, objects, unlock: bool = False) -> Dict[str, Dict[str, Any]]:
        """Return lock state for standard transform attributes.

        Parameters:
            objects: Maya transform nodes.
            unlock (bool): If True, unlock the attributes after storing state.

        Returns:
            ``{obj_name: {attr: bool, ...}, ...}`` including per-axis
            (``tx``, ``ty`` …) and group-level (``translate``, ``rotate``,
            ``scale``) summaries.
        """
        objects = pm.ls(objects, transforms=True, long=True)
        attr_groups = {
            "translate": ("tx", "ty", "tz"),
            "rotate": ("rx", "ry", "rz"),
            "scale": ("sx", "sy", "sz"),
        }

        result: Dict[str, Dict[str, Any]] = {}

        for obj in objects:
            obj = cls._resolve_lock_target(obj)
            if obj is None:
                continue

            obj_state: Dict[str, Any] = {}

            for group, attrs in attr_groups.items():
                group_vals: list = []
                for attr in attrs:
                    try:
                        locked = pm.getAttr(f"{obj}.{attr}", lock=True)
                        obj_state[attr] = locked
                        group_vals.append(locked)
                        if unlock and locked:
                            pm.setAttr(f"{obj}.{attr}", lock=False)
                    except Exception:
                        obj_state[attr] = None
                        group_vals.append(None)

                if all(v is True for v in group_vals):
                    obj_state[group] = True
                elif all(v is False for v in group_vals):
                    obj_state[group] = False
                else:
                    obj_state[group] = None

            result[obj.name()] = obj_state

        return result

    @classmethod
    def set_lock_state(
        cls,
        objects,
        lock_state: Optional[Dict[str, Dict[str, bool]]] = None,
        translate: Optional[bool] = None,
        rotate: Optional[bool] = None,
        scale: Optional[bool] = None,
        **kwargs,
    ) -> None:
        """Restore lock state from a saved dict, or bulk lock/unlock.

        Parameters:
            objects: Maya transform nodes.
            lock_state: Per-object state dict from :meth:`get_lock_state`.
            translate/rotate/scale (bool|None): Bulk lock/unlock groups.
            **kwargs: Individual attribute locks (e.g. ``tx=True``).
        """
        objects = pm.ls(objects, transforms=True, long=True)

        for obj in objects:
            obj = cls._resolve_lock_target(obj)
            if obj is None:
                continue

            if lock_state and obj.name() in lock_state:
                state = lock_state[obj.name()]
                for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
                    lock_val = state.get(attr)
                    if lock_val is not None:
                        try:
                            pm.setAttr(f"{obj}.{attr}", lock=lock_val)
                        except Exception:
                            pass
                continue

            attr_map = {
                ("tx", "ty", "tz"): translate,
                ("rx", "ry", "rz"): rotate,
                ("sx", "sy", "sz"): scale,
            }
            for attrs, state in attr_map.items():
                if state is None:
                    continue
                for attr in attrs:
                    try:
                        pm.setAttr(f"{obj}.{attr}", lock=state)
                    except Exception:
                        pass

            for attr, state in kwargs.items():
                if state is None:
                    continue
                try:
                    pm.setAttr(f"{obj}.{attr}", lock=state)
                except Exception:
                    pass

    @classmethod
    @contextlib.contextmanager
    def temporarily_unlock(cls, objects, attributes=None):
        """Context manager: temporarily unlock attributes and restore state on exit.

        Parameters:
            objects: Object(s) to unlock.
            attributes: Specific attributes (e.g. ``['tx', 'ry']``).
                If ``None``, unlocks all standard transform attributes.
        """
        lock_state = cls.get_lock_state(objects, unlock=True)
        try:
            yield
        finally:
            cls.set_lock_state(objects, lock_state=lock_state)

    # ======================================================================
    # Channel-box operations
    # ======================================================================

    _clipboard: Dict[str, Any] = {}
    """Class-level clipboard for :meth:`copy_values` / :meth:`paste_values`."""

    @classmethod
    def copy_values(
        cls, objects, attributes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Copy attribute values from the first object into the class clipboard.

        Parameters:
            objects: Source object(s).  Values are read from the *first*.
            attributes: Attribute names to copy.  If ``None``, uses the
                current channel-box selection via :meth:`get_selected_channels`.

        Returns:
            dict: ``{attr_name: value}`` that was stored.
        """
        nodes = pm.ls(objects)
        if not nodes:
            return {}
        obj = nodes[0]

        if attributes is None:
            attributes = cls.get_selected_channels()
        if not attributes:
            return {}

        values: Dict[str, Any] = {}
        for attr in attributes:
            try:
                values[attr] = pm.getAttr(f"{obj}.{attr}")
            except Exception:
                pass

        cls._clipboard = values
        return values

    @classmethod
    def paste_values(cls, objects, values: Optional[Dict[str, Any]] = None) -> None:
        """Paste attribute values onto *objects*.

        Parameters:
            objects: Target object(s).
            values: ``{attr: value}`` mapping.  If ``None``, the class
                clipboard from the last :meth:`copy_values` call is used.
        """
        if values is None:
            values = cls._clipboard
        if not values:
            return

        for obj in pm.ls(objects):
            for attr, value in values.items():
                try:
                    if not pm.getAttr(f"{obj}.{attr}", lock=True):
                        pm.setAttr(f"{obj}.{attr}", value)
                except Exception:
                    pass

    @staticmethod
    def reset_to_default(objects, attributes: List[str]) -> None:
        """Reset attributes to their default values.

        Parameters:
            objects: Target object(s).
            attributes: Attribute names to reset.
        """
        for node in pm.ls(objects):
            for attr_name in attributes:
                try:
                    defaults = pm.attributeQuery(attr_name, node=node, listDefault=True)
                    if defaults:
                        pm.setAttr(f"{node}.{attr_name}", defaults[0])
                except Exception:
                    pass

    @staticmethod
    def mute(objects, attributes: Optional[List[str]] = None) -> None:
        """Mute channels to suppress animation evaluation.

        Parameters:
            objects: Target object(s).
            attributes: Attribute names.  If ``None``, uses the
                current channel-box selection.
        """
        for obj in pm.ls(objects):
            attrs = attributes or Attributes.get_selected_channels()
            for attr in attrs:
                try:
                    pm.mute(f"{obj}.{attr}")
                except Exception:
                    pass

    @staticmethod
    def unmute(objects, attributes: Optional[List[str]] = None) -> None:
        """Unmute previously muted channels.

        Parameters:
            objects: Target object(s).
            attributes: Attribute names.  If ``None``, uses the
                current channel-box selection.
        """
        for obj in pm.ls(objects):
            attrs = attributes or Attributes.get_selected_channels()
            for attr in attrs:
                try:
                    pm.mute(f"{obj}.{attr}", disable=True, force=True)
                except Exception:
                    pass

    @staticmethod
    def set_channel_box_visibility(
        objects, attributes: List[str], visible: bool = True
    ) -> None:
        """Show or hide attributes in the channel box.

        Hidden attributes become non-keyable and are removed from the
        channel-box display.

        Parameters:
            objects: Target object(s).
            attributes: Attribute names.
            visible (bool): ``True`` to show (keyable), ``False`` to hide.
        """
        for obj in pm.ls(objects):
            for attr in attributes:
                try:
                    if visible:
                        pm.setAttr(f"{obj}.{attr}", keyable=True)
                    else:
                        pm.setAttr(f"{obj}.{attr}", keyable=False, channelBox=False)
                except Exception:
                    pass

    @staticmethod
    def lock_and_hide(objects, attributes: List[str]) -> None:
        """Lock attributes and hide them from the channel box.

        Parameters:
            objects: Target object(s).
            attributes: Attribute names.
        """
        for obj in pm.ls(objects):
            for attr in attributes:
                try:
                    pm.setAttr(
                        f"{obj}.{attr}", lock=True, keyable=False, channelBox=False
                    )
                except Exception:
                    pass

    # ======================================================================
    # Filter
    # ======================================================================

    @staticmethod
    def filter(
        attributes: List[str],
        exclude: Union[str, List[str], None] = None,
        include: Union[str, List[str], None] = None,
        case_sensitive: bool = False,
    ) -> List[str]:
        """Filter attribute names by inclusion/exclusion patterns.

        Supports exact names and wildcards (``*`` and ``?``).

        Parameters:
            attributes: Attribute names to filter.
            exclude: Names or patterns to exclude.
            include: If given, only keep attributes matching these.
            case_sensitive (bool): Default ``False``.

        Returns:
            list[str]: Filtered attribute names.

        Example:
            >>> attrs = ['translateX', 'translateY', 'rotateX', 'visibility']
            >>> Attributes.filter(attrs, exclude='visibility')
            ['translateX', 'translateY', 'rotateX']
            >>> Attributes.filter(attrs, include='translate*')
            ['translateX', 'translateY']
        """
        if not attributes:
            return []

        exclude_patterns = (
            []
            if exclude is None
            else [exclude] if isinstance(exclude, str) else list(exclude)
        )
        include_patterns = (
            []
            if include is None
            else [include] if isinstance(include, str) else list(include)
        )

        def _matches(attr_name: str, pattern: str) -> bool:
            a, p = (
                (attr_name, pattern)
                if case_sensitive
                else (attr_name.lower(), pattern.lower())
            )
            if "*" in p or "?" in p:
                return fnmatch.fnmatch(a, p)
            return a == p

        filtered: List[str] = []
        for attr in attributes:
            if include_patterns and not any(
                _matches(attr, p) for p in include_patterns
            ):
                continue
            if exclude_patterns and any(_matches(attr, p) for p in exclude_patterns):
                continue
            filtered.append(attr)

        return filtered


# --- Auto-load YAML templates at import time ---
Attributes.PRESETS = Attributes._load_templates()
