# !/usr/bin/python
# coding=utf-8
"""Channels — Maya attribute query / mutation logic.

``Channels`` encapsulates all non-UI attribute operations: filtering,
connection classification, table-data building, and mutation helpers.
The companion ``channels_slots`` module wraps this class in a
Switchboard UI.
"""
import maya.cmds as cmds

from mayatk.node_utils.attributes._attributes import Attributes


class Channels:
    """Maya attribute query / mutation logic.

    Encapsulates attribute querying, filtering, connection
    classification, and mutation so that ``ChannelsSlots`` only handles
    UI wiring.
    """

    # Maps ComboBox items → kwargs for ``cmds.listAttr``.
    # Use ``_custom_filter`` key for filters that need Python-side logic
    # beyond what ``cmds.listAttr`` supports natively.
    FILTER_MAP = {
        "Custom": {"userDefined": True},
        "Keyable": {"keyable": True},
        "Channel Box": {"_custom_filter": "channel_box"},
        "Locked": {"locked": True},
        "Connected": {"_custom_filter": "connected"},
        "Settable": {"settable": True},
        "Keyed": {"_custom_filter": "keyed"},
        "All": {},
    }

    def __init__(self):
        self._pinned_targets = None
        self._single_object_mode = False

    @property
    def is_pinned(self):
        return self._pinned_targets is not None

    @property
    def single_object_mode(self):
        return self._single_object_mode

    @single_object_mode.setter
    def single_object_mode(self, value):
        self._single_object_mode = bool(value)

    def pin_targets(self, nodes):
        """Pin the manager to a fixed node list; ``None`` clears the pin.

        Names are normalized to long (DAG) paths where possible so
        downstream ``cmds`` calls match regardless of the input form.
        """
        if not nodes:
            self._pinned_targets = None
            return
        self._pinned_targets = cmds.ls(list(nodes), long=True) or list(nodes)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_selected_nodes(self):
        """Return the target node list.

        When pinned, returns the cached list filtered to nodes that still
        exist in the scene.  Otherwise returns the current Maya selection.
        When ``single_object_mode`` is enabled, only the most recently
        selected node is returned.
        """
        if self._pinned_targets is not None:
            nodes = [n for n in self._pinned_targets if cmds.objExists(n)]
        else:
            nodes = cmds.ls(sl=True, long=True) or []
        if self._single_object_mode and len(nodes) > 1:
            return [nodes[-1]]
        return nodes

    @staticmethod
    def get_channel_box_selection():
        """Return all attribute names currently selected in Maya's channel box.

        Aggregates selection from Main, Shape, History, and Output sections.
        """
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            return ChannelBox.get_selected_attrs(sections="all")
        except Exception:
            return []

    @staticmethod
    def get_filter_kwargs(filter_key="Custom", invert=False):
        """Return the ``cmds.listAttr`` kwargs for the given *filter_key*.

        Parameters
        ----------
        filter_key : str
            One of the keys in ``FILTER_MAP``.
        invert : bool
            If ``True`` the ``_invert`` flag is added so ``collect_attr_names``
            will return the complement set.
        """
        kwargs = Channels.FILTER_MAP.get(
            filter_key, Channels.FILTER_MAP["Custom"]
        ).copy()
        if invert:
            kwargs["_invert"] = True
        # Apply channel-box priority ordering for all filters except "All".
        if filter_key != "All":
            kwargs["_priority_sort"] = True
        return kwargs

    @staticmethod
    def query_connected_attrs(node):
        """Return set of attribute names on *node* that have incoming connections."""
        conns = (
            cmds.listConnections(
                node, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        result = set()
        for dst_plug, _src_plug in zip(conns[0::2], conns[1::2]):
            attr_name = str(dst_plug).split(".", 1)[-1]
            result.add(attr_name)
        return result

    @staticmethod
    def collect_attr_names(nodes, filter_kwargs):
        """Return the intersection of attribute names across *nodes*."""
        if not nodes:
            return []

        custom_filter = filter_kwargs.pop("_custom_filter", None)
        invert = filter_kwargs.pop("_invert", False)
        priority_sort = filter_kwargs.pop("_priority_sort", False)

        if custom_filter == "channel_box":
            # Union of keyable attrs + non-keyable attrs shown in channel box
            sets_k = [set(cmds.listAttr(n, keyable=True) or []) for n in nodes]
            sets_c = [set(cmds.listAttr(n, channelBox=True) or []) for n in nodes]
            common = sets_k[0] | sets_c[0]
            for sk, sc in zip(sets_k[1:], sets_c[1:]):
                common &= sk | sc
        elif custom_filter == "connected":
            # Attrs with incoming connections (not a native listAttr flag)
            sets = [Channels.query_connected_attrs(n) for n in nodes]
            common = sets[0]
            for s in sets[1:]:
                common &= s
        else:
            sets = [set(cmds.listAttr(n, **filter_kwargs) or []) for n in nodes]
            common = sets[0]
            for s in sets[1:]:
                common &= s

        if custom_filter == "keyed":
            # Keep only attributes connected to animation curves
            keyed_attrs = set()
            for attr in list(common):
                is_keyed = True
                for node in nodes:
                    try:
                        conns = cmds.listConnections(
                            f"{node}.{attr}",
                            source=True,
                            destination=False,
                            type="animCurve",
                        )
                        if not conns:
                            is_keyed = False
                            break
                    except Exception:
                        is_keyed = False
                        break
                if is_keyed:
                    keyed_attrs.add(attr)
            common = keyed_attrs

        if invert:
            # Scope inversion to AE-visible attrs (visible=True excludes truly internal
            # system attrs like caching/nodeState that listAttr() would include).
            # This makes e.g. "Channel Box" + Invert = hidden-from-CB attrs.
            vis_sets = [set(cmds.listAttr(n, visible=True) or []) for n in nodes]
            vis_common = vis_sets[0]
            for s in vis_sets[1:]:
                vis_common &= s
            common = vis_common - common

        if priority_sort or custom_filter == "channel_box":
            return Channels._sort_channel_box(common)
        return sorted(common)

    # Canonical channel-box attribute ordering:
    # Translate → Rotate → Scale → Visibility, then everything else alphabetically.
    _CHANNEL_BOX_ORDER = [
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    ]

    @classmethod
    def _sort_channel_box(cls, attrs):
        """Sort attributes in canonical channel-box order.

        Priority attributes (Translate, Rotate, Scale, Visibility) appear
        first in a fixed order, followed by all remaining attributes sorted
        alphabetically.
        """
        priority = {name: i for i, name in enumerate(cls._CHANNEL_BOX_ORDER)}
        ordered = []
        remaining = []
        for attr in attrs:
            if attr in priority:
                ordered.append(attr)
            else:
                remaining.append(attr)
        ordered.sort(key=lambda a: priority[a])
        remaining.sort()
        return ordered + remaining

    @classmethod
    def collect_value_strings(cls, nodes, attr_names):
        """Return ``{attr_name: (value_str, conn_type)}`` for the given attrs.

        Lightweight version of :meth:`build_table_data` used by the
        live-update path: skips type/lock detection and simply re-evaluates
        values + connection state for the rows already in the table.
        """
        if not nodes:
            return {}
        primary = nodes[0]
        multi = len(nodes) > 1
        result = {}
        for attr_name in attr_names:
            attr_type = cls.get_attr_type(primary, attr_name)
            if attr_type == "enum":
                val = cls.get_enum_label(primary, attr_name)
                if multi:
                    for other in nodes[1:]:
                        if cls.get_enum_label(other, attr_name) != val:
                            val = "*"
                            break
                val_str = val if val is not None else ""
            else:
                val = cls.get_attr_value(primary, attr_name)
                if multi:
                    for other in nodes[1:]:
                        if cls.get_attr_value(other, attr_name) != val:
                            val = "*"
                            break
                val_str = cls.format_value(val)
            conn_type = cls.classify_connection(primary, attr_name)
            result[attr_name] = (val_str, conn_type)
        return result

    @staticmethod
    def get_attr_value(node, attr_name):
        """Safely get an attribute value, returning ``None`` on failure."""
        try:
            return cmds.getAttr(f"{node}.{attr_name}")
        except Exception:
            return None

    @staticmethod
    def get_attr_type(node, attr_name):
        """Return the Maya attribute type string.

        Prefers ``cmds.getAttr(plug, type=True)`` because it returns the
        actual storage type (e.g. ``"string"`` instead of ``"typed"``),
        falling back to ``attributeQuery`` for compounds where ``getAttr``
        rejects the query.
        """
        plug = f"{node}.{attr_name}"
        try:
            t = cmds.getAttr(plug, type=True)
            if t:
                return t
        except Exception:
            pass
        try:
            return cmds.attributeQuery(attr_name, node=node, attributeType=True)
        except Exception:
            return "?"

    @staticmethod
    def get_incoming_connection(node, attr_name):
        """Return ``'→ src.attr'`` if there is an incoming connection, else ``''``."""
        try:
            conns = cmds.listConnections(
                f"{node}.{attr_name}", source=True, destination=False, plugs=True
            )
            if conns:
                return f"→ {conns[0]}"
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Connection classification
    # ------------------------------------------------------------------

    # Node types that are transparent pass-through utilities.
    _PASSTHROUGH_TYPES = frozenset(
        {
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
    )

    @classmethod
    def classify_connection(cls, node, attr_name):
        """Classify the incoming connection on *node.attr_name*.

        Uses only ``maya.cmds`` — no cross-module imports — to
        avoid SafeMode and circular-import issues.

        Returns one of:
        - ``"none"`` — no incoming connection
        - ``"muted"`` — channel is muted (mute node)
        - ``"keyframe"`` — driven by an animCurve (no key on current frame)
        - ``"keyframe_active"`` — driven by an animCurve with a key set on the current frame
        - ``"expression"`` — driven by an expression
        - ``"driven_key"`` — driven by a set-driven-key curve
        - ``"constraint"`` — driven by a constraint
        - ``"connected"`` — generic incoming connection
        """
        plug = f"{node}.{attr_name}"

        # Fast check: mute node
        try:
            if cmds.listConnections(plug, s=True, d=False, type="mute"):
                return "muted"
        except Exception:
            pass

        # Trace upstream through passthrough nodes
        try:
            result = cls._trace_source(plug)
            if result is None:
                return "none"
        except Exception:
            return "none"

        # Promote "keyframe" to "keyframe_active" when a key exists at current time.
        if result == "keyframe" and cls.has_key_at_current_time(plug):
            return "keyframe_active"
        return result

    @staticmethod
    def has_key_at_current_time(plug):
        """Return ``True`` if *plug* has a keyframe set exactly at the current time."""
        try:
            t = cmds.currentTime(q=True)
            keys = cmds.keyframe(plug, q=True, time=(t, t))
            return bool(keys)
        except Exception:
            return False

    @classmethod
    def _trace_source(cls, plug, visited=None):
        """Walk upstream through passthrough nodes and classify the driver.

        Returns a connection-type string or ``None`` if no source.
        """
        if visited is None:
            visited = set()
        if plug in visited:
            return None
        visited.add(plug)

        sources = cmds.listConnections(plug, s=True, d=False) or []
        if not sources:
            return None

        source = sources[0]
        node_type = cmds.nodeType(source)

        # Transparent utility — recurse through its inputs
        if node_type in cls._PASSTHROUGH_TYPES:
            inputs = cmds.listConnections(source, s=True, d=False, plugs=True) or []
            for inp in inputs:
                inp_node = inp.split(".")[0]
                if inp_node in visited:
                    continue
                classified = cls._classify_source_node(inp_node)
                if classified:
                    return classified
                deeper = cls._trace_source(inp, visited)
                if deeper:
                    return deeper
            return None

        return cls._classify_source_node(source) or "connected"

    @staticmethod
    def _classify_source_node(node):
        """Return a connection-type string for *node*, or ``None``."""
        node_type = cmds.nodeType(node)

        # Constraint — any type inheriting from "constraint"
        inherited = cmds.nodeType(node, inherited=True) or []
        if "constraint" in (t.lower() for t in inherited):
            return "constraint"
        # Expression
        if node_type == "expression":
            return "expression"
        # AnimCurve — distinguish keyframe vs. set-driven key
        if node_type.startswith("animCurve"):
            input_conn = cmds.listConnections(f"{node}.input", s=True, d=False)
            return "driven_key" if input_conn else "keyframe"
        return None

    # ------------------------------------------------------------------
    # Table data building
    # ------------------------------------------------------------------

    @classmethod
    def build_table_data(cls, nodes, filter_kwargs):
        """Build row data and state tuples for the table.

        Returns
        -------
        tuple[list[list[str]], list[tuple[bool, str]]]
            ``(rows, attr_states)`` where each row is
            ``[name, '', '', value_str, type_str]`` and each state is
            ``(is_locked, connection_type)`` with *connection_type* being
            ``"none"``, ``"keyframe"``, ``"expression"``, ``"driven_key"``,
            ``"constraint"``, ``"muted"``, or ``"connected"``.
        """
        attr_names = cls.collect_attr_names(nodes, filter_kwargs)

        primary = nodes[0]
        multi = len(nodes) > 1

        rows = []
        attr_states = []
        for attr_name in attr_names:
            # Value
            attr_type = cls.get_attr_type(primary, attr_name)

            if attr_type == "enum":
                val = cls.get_enum_label(primary, attr_name)
                if multi:
                    for other in nodes[1:]:
                        other_val = cls.get_enum_label(other, attr_name)
                        if other_val != val:
                            val = "*"
                            break
                val_str = val if val is not None else ""
            else:
                val = cls.get_attr_value(primary, attr_name)
                if multi:
                    for other in nodes[1:]:
                        other_val = cls.get_attr_value(other, attr_name)
                        if other_val != val:
                            val = "*"
                            break
                val_str = cls.format_value(val)

            # Type (already fetched above for enum check)

            # Locked
            try:
                locked = cmds.getAttr(f"{primary}.{attr_name}", lock=True)
            except Exception:
                locked = False

            # Connection classification
            conn_type = cls.classify_connection(primary, attr_name)

            rows.append([attr_name, "", "", val_str, attr_type])
            attr_states.append((locked, conn_type))

        if not rows:
            rows = [["", "", "", "", "No attributes"]]
            attr_states = [(False, "none")]

        return rows, attr_states

    # ------------------------------------------------------------------
    # Formatting / parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_float(val, decimals=4):
        """Format a float, stripping all trailing zeros and the trailing
        decimal point if no fractional digits remain.

        Examples (with default *decimals=4*):

        - ``0.0`` → ``"0"``
        - ``1.0`` → ``"1"``
        - ``1.5`` → ``"1.5"``
        - ``0.0001`` → ``"0.0001"``
        - ``1.23456789`` → ``"1.2346"`` (rounded to 4 decimals)
        """
        s = f"{val:.{decimals}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    @staticmethod
    def format_value(val):
        """Convert a Maya attribute value to a display string."""
        if val == "*":
            return "*"
        if val is None:
            return ""
        if isinstance(val, float):
            return Channels._fmt_float(val)
        if isinstance(val, (list, tuple)):
            inner = ", ".join(
                Channels._fmt_float(v) if isinstance(v, float) else str(v)
                for v in val
            )
            return f"({inner})"
        return str(val)

    @staticmethod
    def parse_value(text, attr_type):
        """Convert user-entered text to a Python value for ``cmds.setAttr``."""
        if attr_type in ("double", "float", "doubleLinear", "doubleAngle"):
            return float(text)
        if attr_type in ("long", "short", "byte", "int"):
            return int(float(text))
        if attr_type == "bool":
            return text.lower() in ("1", "true", "yes", "on")
        if attr_type in ("string", "typed"):
            return text
        if attr_type == "enum":
            try:
                return int(text)
            except ValueError:
                return text
        # Compound / unsupported — skip
        return None

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def toggle_lock(nodes, attr_name):
        """Toggle the lock state for *attr_name* on *nodes*."""
        try:
            current = cmds.getAttr(f"{nodes[0]}.{attr_name}", lock=True)
        except Exception:
            return
        new_state = not current
        cmds.undoInfo(openChunk=True, chunkName="Toggle Lock")
        try:
            for node in nodes:
                try:
                    cmds.setAttr(f"{node}.{attr_name}", lock=new_state)
                except Exception:
                    pass
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def break_connections(nodes, attr_name):
        """Break all incoming connections for *attr_name* on *nodes*.

        Temporarily unlocks locked attributes before disconnecting.
        Returns ``True`` if any connections were broken.
        """
        has_conn = False
        for node in nodes:
            try:
                conns = cmds.listConnections(
                    f"{node}.{attr_name}",
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                )
                if conns:
                    has_conn = True
                    break
            except Exception:
                pass
        if not has_conn:
            return False

        cmds.undoInfo(openChunk=True, chunkName="Break Connection")
        try:
            for node in nodes:
                try:
                    plug = f"{node}.{attr_name}"
                    was_locked = cmds.getAttr(plug, lock=True)
                    if was_locked:
                        cmds.setAttr(plug, lock=False)

                    conns = cmds.listConnections(
                        plug,
                        source=True,
                        destination=False,
                        plugs=True,
                        connections=True,
                    )
                    if conns:
                        for dst, src in zip(conns[0::2], conns[1::2]):
                            cmds.disconnectAttr(src, dst)

                    if was_locked:
                        cmds.setAttr(plug, lock=True)
                except Exception as e:
                    cmds.warning(
                        f"Failed to break connection on {node}.{attr_name}: {e}"
                    )
        finally:
            cmds.undoInfo(closeChunk=True)
        return True

    @staticmethod
    def set_lock(nodes, attr_names, lock):
        """Lock or unlock *attr_names* across all *nodes*."""
        action = "Lock" if lock else "Unlock"
        cmds.undoInfo(openChunk=True, chunkName=f"{action} Attrs")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        cmds.setAttr(f"{node}.{attr_name}", lock=lock)
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def reset_to_default(nodes, attr_names):
        """Reset *attr_names* to their default values across all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName="Reset to Default")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        defaults = cmds.attributeQuery(
                            attr_name, node=node, listDefault=True
                        )
                        if defaults:
                            cmds.setAttr(f"{node}.{attr_name}", defaults[0])
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def toggle_keyable(nodes, attr_names):
        """Toggle the keyable state for *attr_names* across all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName="Toggle Keyable")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        plug = f"{node}.{attr_name}"
                        current = cmds.getAttr(plug, keyable=True)
                        cmds.setAttr(plug, keyable=not current)
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def delete_attributes(nodes, attr_names):
        """Delete custom *attr_names* across all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName="Delete Attrs")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        if cmds.attributeQuery(attr_name, node=node, exists=True):
                            # Disconnect incoming anim curves before deletion
                            curves = cmds.listConnections(
                                f"{node}.{attr_name}", type="animCurve"
                            )
                            if curves:
                                cmds.delete(curves)
                            cmds.deleteAttr(f"{node}.{attr_name}")
                    except Exception as e:
                        cmds.warning(f"Failed to delete {node}.{attr_name}: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

    @classmethod
    def set_attribute_value(cls, nodes, attr_name, text):
        """Parse *text* and set *attr_name* on all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName=f"Edit Attr: {attr_name}")
        try:
            for node in nodes:
                if not cmds.attributeQuery(attr_name, node=node, exists=True):
                    continue
                try:
                    attr_type = cls.get_attr_type(node, attr_name)
                    value = cls.parse_value(text, attr_type)
                    if value is None:
                        continue
                    # Resolve enum label strings to integer indices.
                    if attr_type == "enum" and isinstance(value, str):
                        pairs = cls._parse_enum_def(node, attr_name)
                        match = [idx for lab, idx in pairs if lab == value]
                        if match:
                            value = match[0]
                        else:
                            cmds.warning(
                                f"Unknown enum label '{value}' for "
                                f"{node}.{attr_name}"
                            )
                            continue
                    cmds.setAttr(f"{node}.{attr_name}", value)
                except Exception as e:
                    cmds.warning(f"Failed to set {node}.{attr_name}: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

    # Map UI-friendly type names to Maya addAttr argument types.
    _ATTR_TYPE_MAP = {
        "float": ("attributeType", "double"),
        "int": ("attributeType", "long"),
        "bool": ("attributeType", "bool"),
        "string": ("dataType", "string"),
        "enum": ("attributeType", "enum"),
        "double3": ("attributeType", "double3"),
    }
    _NUMERIC_TYPES = frozenset({"float", "int"})

    @classmethod
    def create_attribute(
        cls,
        nodes,
        name,
        attr_type,
        keyable=True,
        min_val=None,
        max_val=None,
        default_val=0.0,
        enum_names="",
    ):
        """Create a custom attribute on *nodes*.

        Parameters
        ----------
        nodes : list
            Maya nodes to receive the attribute.
        name : str
            Attribute long name.
        attr_type : str
            UI type key (``float``, ``int``, ``bool``, ``string``,
            ``enum``, ``double3``).
        keyable : bool
            Whether the attribute is keyable.
        min_val, max_val : float or None
            Numeric range.  *None* means no limit.
        default_val : float
            Default value (numeric types only).
        enum_names : str
            Colon-separated enum labels (e.g. ``"A:B:C"``).
        """
        type_key, maya_type = cls._ATTR_TYPE_MAP.get(
            attr_type, ("attributeType", "double")
        )

        cmds.undoInfo(openChunk=True, chunkName=f"Create Attr: {name}")
        try:
            for obj in nodes:
                if cmds.attributeQuery(name, node=str(obj), exists=True):
                    cmds.warning(f"'{name}' already exists on {obj}.")
                    continue

                kw = {"longName": name, type_key: maya_type, "keyable": keyable}

                if attr_type == "enum":
                    kw["enumName"] = enum_names or "A:B:C"
                elif attr_type in cls._NUMERIC_TYPES:
                    if min_val is not None:
                        kw["minValue"] = min_val
                    if max_val is not None:
                        kw["maxValue"] = max_val
                    kw["defaultValue"] = default_val
                elif attr_type == "bool":
                    kw["defaultValue"] = bool(default_val)

                if attr_type == "double3":
                    # Compound: parent + three children.
                    cmds.addAttr(obj, longName=name, attributeType="double3")
                    for suffix in ("X", "Y", "Z"):
                        cmds.addAttr(
                            obj,
                            longName=f"{name}{suffix}",
                            attributeType="double",
                            parent=name,
                            keyable=keyable,
                        )
                else:
                    cmds.addAttr(obj, **kw)

                # Set the default for numeric types so the attr starts there.
                if (
                    attr_type in cls._NUMERIC_TYPES
                    and default_val != 0.0
                    and cmds.attributeQuery(name, node=str(obj), exists=True)
                ):
                    try:
                        cmds.setAttr(f"{obj}.{name}", default_val)
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def copy_attr_values(nodes, attr_names):
        """Copy attribute values from the primary node to the clipboard."""
        if not nodes or not attr_names:
            return {}
        return Attributes.copy_values(nodes, attr_names)

    @staticmethod
    def paste_attr_values(nodes):
        """Paste previously copied attribute values onto *nodes*."""
        if not nodes:
            return
        cmds.undoInfo(openChunk=True, chunkName="Paste Attribute Values")
        try:
            Attributes.paste_values(nodes)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def rename_attribute(nodes, old_name, new_name):
        """Rename a user-defined attribute on *nodes*.

        Uses ``cmds.renameAttr``.  Only user-defined (custom) attributes
        are renameable in Maya.  Returns ``True`` on success.
        """
        if not new_name or new_name == old_name:
            return False
        cmds.undoInfo(openChunk=True, chunkName="Rename Attribute")
        try:
            for node in nodes:
                plug = f"{node}.{old_name}"
                if not cmds.attributeQuery(old_name, node=node, exists=True):
                    continue
                cmds.renameAttr(plug, new_name)
        finally:
            cmds.undoInfo(closeChunk=True)
        return True

    @staticmethod
    def rename_node(old_name, new_name):
        """Rename a Maya node and return its new full path.

        Returns the original *old_name* unchanged on failure or no-op.
        """
        if not new_name or not old_name:
            return old_name
        short = old_name.rsplit("|", 1)[-1]
        if new_name == short:
            return old_name
        cmds.undoInfo(openChunk=True, chunkName="Rename Node")
        try:
            new_short = cmds.rename(old_name, new_name)
            return cmds.ls(new_short, long=True)[0] if new_short else old_name
        except Exception as e:
            cmds.warning(f"Failed to rename '{old_name}' → '{new_name}': {e}")
            return old_name
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def get_shape_nodes(nodes):
        """Return the shape node name(s) for *nodes*."""
        result = []
        for node in nodes:
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            result.extend(shapes)
        return result

    @staticmethod
    def get_history_nodes(nodes):
        """Return the construction-history input node(s) for *nodes*."""
        result = []
        for node in nodes:
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            for shape in shapes:
                conns = cmds.listConnections(shape, source=True, destination=False)
                if conns:
                    result.append(conns[-1])
                    break
            else:
                # fallback: direct history on the node itself
                hist = cmds.listHistory(node, pruneDagObjects=True) or []
                # skip the node itself
                hist = [h for h in hist if h != node]
                if hist:
                    result.append(hist[0])
        return result

    @staticmethod
    def toggle_key_at_current_time(nodes, attr_name):
        """Set or remove a keyframe on *attr_name* for *nodes* at the current time.

        - If a key already exists at the current time, removes it.
        - Otherwise sets a key at the current time.

        Returns ``"set"`` or ``"removed"`` indicating the resulting action,
        or ``None`` if nothing happened.
        """
        if not nodes:
            return None
        t = cmds.currentTime(q=True)
        result = None
        cmds.undoInfo(openChunk=True, chunkName=f"Toggle Key: {attr_name}")
        try:
            # Decide the action based on the *primary* node's state so the
            # operation is consistent across a multi-selection batch.
            primary_plug = f"{nodes[0]}.{attr_name}"
            try:
                primary_keys = cmds.keyframe(
                    primary_plug, q=True, time=(t, t)
                )
            except Exception:
                primary_keys = None
            removing = bool(primary_keys)

            for node in nodes:
                plug = f"{node}.{attr_name}"
                try:
                    if removing:
                        cmds.cutKey(plug, time=(t, t), clear=True)
                    else:
                        cmds.setKeyframe(plug)
                except Exception:
                    pass
            result = "removed" if removing else "set"
        finally:
            cmds.undoInfo(closeChunk=True)
        return result

    @staticmethod
    def set_breakdown_key(nodes, attr_names):
        """Set a breakdown key on *attr_names* for all *nodes* at the current time."""
        cmds.undoInfo(openChunk=True, chunkName="Set Breakdown Key")
        try:
            plugs = [
                f"{n}.{a}"
                for n in nodes
                for a in attr_names
                if cmds.attributeQuery(a, node=n, exists=True)
            ]
            if plugs:
                cmds.setKeyframe(plugs, breakdown=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def mute_attrs(nodes, attr_names):
        """Mute *attr_names* across all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName="Mute Attrs")
        try:
            Attributes.mute(nodes, attr_names)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def unmute_attrs(nodes, attr_names):
        """Unmute *attr_names* across all *nodes*."""
        cmds.undoInfo(openChunk=True, chunkName="Unmute Attrs")
        try:
            Attributes.unmute(nodes, attr_names)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def hide_attrs(nodes, attr_names):
        """Hide *attr_names* from the channel box."""
        cmds.undoInfo(openChunk=True, chunkName="Hide Attrs")
        try:
            Attributes.set_channel_box_visibility(nodes, attr_names, visible=False)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def show_attrs(nodes, attr_names):
        """Show (unhide) *attr_names* in the channel box."""
        cmds.undoInfo(openChunk=True, chunkName="Show Attrs")
        try:
            Attributes.set_channel_box_visibility(nodes, attr_names, visible=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def lock_and_hide_attrs(nodes, attr_names):
        """Lock and hide *attr_names*."""
        cmds.undoInfo(openChunk=True, chunkName="Lock and Hide")
        try:
            Attributes.lock_and_hide(nodes, attr_names)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def select_connections(nodes, attr_name):
        """Select the upstream node driving *attr_name* on the primary node.

        Returns ``True`` if a connection was selected.
        """
        if not nodes:
            return False
        try:
            conns = cmds.listConnections(
                f"{nodes[0]}.{attr_name}",
                source=True,
                destination=False,
            )
            if conns:
                cmds.select(conns[0], replace=True)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Transform helpers — node-level freeze / unfreeze
    # ------------------------------------------------------------------

    # Maya's ``makeIdentity`` (the engine behind ``XformUtils.freeze_transforms``)
    # operates on T/R/S as whole groups — there is no native way to freeze
    # a single axis like ``translateX`` while leaving ``translateY`` /
    # ``translateZ`` untouched.  These sets describe what counts as a
    # "complete group" so the UI can gate the Freeze action accordingly.
    _FREEZE_GROUPS = {
        "translate": {
            "parents": {"translate", "t"},
            "children": {"translateX", "tx", "translateY", "ty", "translateZ", "tz"},
            "axis_pairs": (
                {"translateX", "tx"},
                {"translateY", "ty"},
                {"translateZ", "tz"},
            ),
        },
        "rotate": {
            "parents": {"rotate", "r"},
            "children": {"rotateX", "rx", "rotateY", "ry", "rotateZ", "rz"},
            "axis_pairs": (
                {"rotateX", "rx"},
                {"rotateY", "ry"},
                {"rotateZ", "rz"},
            ),
        },
        "scale": {
            "parents": {"scale", "s"},
            "children": {"scaleX", "sx", "scaleY", "sy", "scaleZ", "sz"},
            "axis_pairs": (
                {"scaleX", "sx"},
                {"scaleY", "sy"},
                {"scaleZ", "sz"},
            ),
        },
    }

    @classmethod
    def _strip_plug(cls, name):
        """Return ``foo.translateX`` → ``translateX``; passes plain names through."""
        if isinstance(name, str) and "." in name:
            return name.rsplit(".", 1)[-1]
        return name

    @classmethod
    def can_freeze_selection(cls, attr_names):
        """Test if *attr_names* maps to a clean group-level freeze.

        Returns True iff:

        * the selection is empty (caller will freeze every channel), OR
        * every name is a transform channel (parent or per-axis child),
          AND for each group that appears in the selection either the
          parent attribute is selected or all three axes of that group
          are selected.

        Used by ``ChannelsSlots._update_context_menu_state`` to enable /
        disable the *Freeze Transforms* menu item.
        """
        if not attr_names:
            return True

        names = {cls._strip_plug(n) for n in attr_names}

        all_transform_names = set()
        for grp in cls._FREEZE_GROUPS.values():
            all_transform_names |= grp["parents"] | grp["children"]
        if not names.issubset(all_transform_names):
            return False

        for grp in cls._FREEZE_GROUPS.values():
            sel_parents = names & grp["parents"]
            sel_children = names & grp["children"]
            if not sel_parents and not sel_children:
                continue  # group not present in selection at all
            if sel_parents:
                continue  # parent attr counts as a complete group
            if not all(pair & sel_children for pair in grp["axis_pairs"]):
                return False  # partial group → not freezable cleanly
        return True

    @classmethod
    def _groups_from_attrs(cls, attr_names):
        """Return the set of T/R/S group names that *attr_names* touches.

        Pure mapping (no completeness check); use
        :meth:`can_freeze_selection` first to validate that each touched
        group is fully covered.  Returns an empty set for an empty /
        ``None`` input.
        """
        if not attr_names:
            return set()
        names = {cls._strip_plug(n) for n in attr_names}
        return {
            grp_key
            for grp_key, grp in cls._FREEZE_GROUPS.items()
            if (names & grp["parents"]) or (names & grp["children"])
        }

    @classmethod
    def _freeze_kwargs_for_attrs(cls, attr_names):
        """Translate *attr_names* into ``XformUtils.freeze_transforms`` kwargs.

        Thin wrapper around :meth:`_groups_from_attrs` that materialises
        the result as a ``{group: True}`` dict for ``**kwargs`` spread.
        """
        return {grp: True for grp in cls._groups_from_attrs(attr_names)}

    @classmethod
    def freeze_transforms(cls, nodes, attrs=None, store=True):
        """Freeze transforms on *nodes* under cumulative bake semantics.

        Parameters:
            nodes (list): Transform nodes to freeze.
            attrs (list): Optional attribute names (e.g. ``["translate",
                "rotate"]`` or ``["translateX", "translateY",
                "translateZ"]``) restricting the freeze to those groups.
                Must form a clean group-level selection — partial groups
                or non-transform attributes are rejected (use
                :meth:`can_freeze_selection` to pre-check).  Pass ``None``
                (default) to freeze every transform channel.
            store (bool): When True, accumulate the current local TRS
                onto the per-channel bake history so the operation is
                reversible via :meth:`unfreeze_transforms`.

        Cumulative semantics (when *store* is True):
            * Each freeze composes the current local T/R/S onto the
              per-channel bake history attributes.
            * Channels not in *attrs* are not touched.
            * A later ``unfreeze`` of any channel pushes its bake
              history (composed with whatever the user has done since
              the last freeze) back into the local channel.

        Returns:
            bool: True when the freeze ran, False when *attrs* failed
            validation (no freeze performed).
        """
        if not nodes:
            return False
        if not cls.can_freeze_selection(attrs or []):
            return False

        from mayatk.xform_utils._xform_utils import XformUtils

        kwargs = cls._freeze_kwargs_for_attrs(attrs or [])
        freezing_groups = cls._groups_from_attrs(attrs or [])

        cmds.undoInfo(openChunk=True, chunkName="Freeze Transforms")
        try:
            if store:
                cls._store_pre_freeze(nodes, freezing_groups)
            XformUtils.freeze_transforms(nodes, **kwargs)
        finally:
            cmds.undoInfo(closeChunk=True)
        return True

    @classmethod
    def _store_pre_freeze(cls, nodes, freezing_groups):
        """Accumulate the current local TRS onto the per-channel bake history.

        ``freezing_groups`` (empty for full freeze, otherwise a subset of
        ``{"translate", "rotate", "scale"}``) selects which channels to
        update.  Delegates to ``XformUtils.store_transforms`` with
        ``accumulate=True`` so repeated freezes compose instead of
        overwriting.
        """
        from mayatk.xform_utils._xform_utils import XformUtils

        channels = freezing_groups if freezing_groups else None
        XformUtils.store_transforms(nodes, accumulate=True, channels=channels)

    @classmethod
    def unfreeze_transforms(cls, nodes, attrs=None):
        """Restore previously stored transforms on *nodes*.

        Parameters:
            nodes (list): Transform nodes to unfreeze.
            attrs (list): Optional attribute names restricting which
                channel group(s) to restore (e.g. ``["translate"]`` or
                ``["rotateX", "rotateY", "rotateZ"]``).  Must form a
                clean group-level selection — partial groups or
                non-transform attributes are rejected (use
                :meth:`can_freeze_selection` to pre-check).  ``None``
                (default) restores every transform channel and is the
                only mode that deletes the stored attrs afterwards.

        Returns:
            list: Names of nodes whose transforms were restored.  Empty
            when no node had stored data or *attrs* failed validation.
        """
        if not nodes:
            return []

        channels = None
        if attrs is not None:
            if not cls.can_freeze_selection(attrs):
                return []
            groups = cls._groups_from_attrs(attrs)
            if groups:
                channels = groups

        from mayatk.xform_utils._xform_utils import XformUtils

        cmds.undoInfo(openChunk=True, chunkName="Unfreeze Transforms")
        try:
            return XformUtils.restore_transforms(nodes, channels=channels) or []
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def has_unfreeze_info(nodes):
        """Return True when at least one of *nodes* has stored unfreeze data."""
        if not nodes:
            return False
        from mayatk.xform_utils._xform_utils import XformUtils

        statuses = XformUtils.has_stored_transforms(nodes) or {}
        return any(statuses.values())

    # ------------------------------------------------------------------
    # Enum field helpers — thin wrappers around Attributes.*
    # ------------------------------------------------------------------

    # Keep backward-compatible names so existing call sites work unchanged.
    _parse_enum_def = staticmethod(Attributes.parse_enum_def)
    _build_enum_string = staticmethod(Attributes.build_enum_string)
    get_enum_fields = staticmethod(Attributes.get_enum_fields)
    get_enum_label = staticmethod(Attributes.get_enum_label)
    rename_enum_field = staticmethod(Attributes.rename_enum_field)
    add_enum_field = staticmethod(Attributes.add_enum_field)
    delete_enum_field = staticmethod(Attributes.delete_enum_field)
