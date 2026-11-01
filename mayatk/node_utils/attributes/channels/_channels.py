# !/usr/bin/python
# coding=utf-8
"""Channels — Maya attribute query / mutation logic.

``Channels`` encapsulates all non-UI attribute operations: filtering,
connection classification, table-data building, and mutation helpers.
tentacle's shared Channels panel (``slots/tools/channels.py``) drives it as
``mtk.Channels``, rows keyed by attribute name; what the panel asks of a host
beyond the attribute operations is the "Panel surface" section, which
blendertk's ``Channels`` mirrors where Blender has the concept.
"""

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class _Targets:
    """The nodes a :class:`Channels` acts on: the selection, or a pinned list;
    every one of them, or only the last selected.

    A private part of :class:`Channels`; call it through the facade.
    """

    def __init__(self, channels: "Channels") -> None:
        self._channels = channels  # the facade: its public names are read through it
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
        if not nodes:
            self._pinned_targets = None
            return
        self._pinned_targets = cmds.ls(list(nodes), long=True) or list(nodes)

    def get_selected_nodes(self):
        if self._pinned_targets is not None:
            nodes = [n for n in self._pinned_targets if cmds.objExists(n)]
        else:
            nodes = cmds.ls(sl=True, long=True) or []
        nodes = self._channels.resolve_component_targets(nodes)
        if self._single_object_mode and len(nodes) > 1:
            return [nodes[-1]]
        return nodes


class _ChannelBoxLink:
    """A panel's channel selection and Maya's channel box, kept in step.

    A private part of :class:`Channels`; call it through the facade.
    """

    def __init__(self, channels: "Channels") -> None:
        self._channels = channels  # the facade: its public names are read through it
        # (selection-changed slot, stand-in source) while linked.
        self._link = None

    def link_channel_box(self, on_selection_changed, selected_rows):
        from mayatk.ui_utils.channel_box import ChannelBox

        self._channels.unlink_channel_box()

        def changed(*_args):
            on_selection_changed(self._channels.channel_box_selection())

        def stand_in():
            nodes = self._channels.get_selected_nodes()
            if not nodes:
                return []
            return self._channels._attr_names(
                nodes[0], selected_rows() or [], long=False
            )

        self._link = (changed, stand_in)
        ChannelBox.add_stand_in(stand_in)
        try:
            return bool(ChannelBox.connect_selection_changed(changed))
        except Exception:
            return False

    def unlink_channel_box(self):
        link, self._link = self._link, None
        if link is None:
            return
        from mayatk.ui_utils.channel_box import ChannelBox

        changed, stand_in = link
        try:
            ChannelBox.disconnect_selection_changed(changed)
        except Exception:
            pass
        ChannelBox.remove_stand_in(stand_in)

    def channel_box_selection(self):
        raw = self._channels.get_channel_box_selection()
        nodes = self._channels.get_selected_nodes()
        if not nodes:
            return set(raw)
        return set(self._channels._attr_names(nodes[0], raw, long=True))


class Channels:
    """Maya attribute query / mutation logic.

    Encapsulates attribute querying, filtering, connection
    classification, and mutation so that ``ChannelsSlots`` only handles
    UI wiring. An instance's own state lives in the parts it holds: the
    nodes it acts on (:class:`_Targets`) and its link to Maya's channel
    box (:class:`_ChannelBoxLink`).
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

    #: The storage types (:meth:`get_attr_type`'s answers) a value cell scrubs
    #: and wheel-steps; :attr:`INTEGER_TYPES` are the whole-unit ones among them.
    SCRUBBABLE_TYPES = frozenset(
        {
            "float",
            "int",
            "double",
            "doubleAngle",
            "doubleLinear",
            "time",
            "long",
            "short",
            "byte",
        }
    )
    INTEGER_TYPES = frozenset({"long", "short", "byte", "int"})
    #: The connection states (:meth:`classify_connection`) a plain click on the
    #: panel's key icon keys. Anything else drives the plug (an expression, a
    #: constraint, a driven key ...): only breaking it (Ctrl) applies.
    KEYABLE_STATES = frozenset(
        {"none", "keyframe", "keyframe_active", "keyframe_changed"}
    )
    #: The attribute types :meth:`create_attribute` makes, in the panel's order.
    CREATE_TYPES = ("float", "int", "bool", "string", "enum", "double3")
    #: Maya's own channel editors the panel's header opens
    #: (:meth:`open_editor`): ``{key: (label, tooltip)}``.
    EDITORS = {
        "channel_control": (
            "Channel Control",
            "Open Maya's Channel Control editor.",
        ),
        "connection_editor": (
            "Connection Editor",
            "Open Maya's Connection Editor.",
        ),
    }
    _EDITOR_COMMANDS = {
        "channel_control": "ChannelControlEditor",
        "connection_editor": "ConnectionEditor",
    }

    def __init__(self):
        self._targets = _Targets(self)
        self._channel_box = _ChannelBoxLink(self)

    @property
    def is_pinned(self):
        return self._targets.is_pinned

    @property
    def single_object_mode(self):
        return self._targets.single_object_mode

    @single_object_mode.setter
    def single_object_mode(self, value):
        self._targets.single_object_mode = value

    def pin_targets(self, nodes):
        """Pin the manager to a fixed node list; ``None`` clears the pin.

        Names are normalized to long (DAG) paths where possible so
        downstream ``cmds`` calls match regardless of the input form.
        """
        return self._targets.pin_targets(nodes)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_selected_nodes(self):
        """Return the target node list.

        When pinned, returns the cached list filtered to nodes that still
        exist in the scene.  Otherwise returns the current Maya selection.
        Component selections are resolved to their owning transform (see
        :meth:`resolve_component_targets`).  When ``single_object_mode``
        is enabled, only the most recently selected node is returned.
        """
        return self._targets.get_selected_nodes()

    @staticmethod
    def resolve_component_targets(nodes):
        """Collapse component selections to the transform that owns them.

        A component selection comes back from ``cmds.ls`` as a plug-style
        string (``|pSphere1.vtx[18:39]``).  ``cmds.listAttr`` on one of
        those does *not* list the node's channels — it expands one
        keyable plug per selected point (``pnts[18].pntx``,
        ``pnts[18].pnty``, …), so selecting a few hundred vertices filled
        the table with thousands of per-point tweak rows.

        Maya's own Channel Box shows the *transform's* channels for a
        component selection, so that's what we resolve to; the footer's
        Shape / History buttons remain the way down to the shape node.

        Entries that are already nodes are passed through untouched — an
        explicitly selected shape keeps showing its own attributes.
        Order is preserved (``single_object_mode`` reads the last entry)
        and duplicates are collapsed, since many components resolve to
        one transform.

        Parameters
        ----------
        nodes : list[str]
            Raw selection strings.

        Returns
        -------
        list[str]
        """
        resolved = []
        for name in nodes:
            # Maya forbids "." in node names, so a dot in the leaf of a
            # DAG path can only be a component / attribute suffix.
            if "." not in name.rsplit("|", 1)[-1]:
                resolved.append(name)
                continue
            for obj in cmds.ls(name, objectsOnly=True, long=True) or []:
                if cmds.objectType(obj, isAType="shape"):
                    parents = (
                        cmds.listRelatives(
                            obj, parent=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    resolved.extend(parents or [obj])
                else:
                    resolved.append(obj)
        # Order-preserving dedupe.
        return list(dict.fromkeys(resolved))

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
        - ``"keyframe_changed"`` — keyed, but the value was moved off the
          curve without a key (lost on the next time change)
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

        # A value moved off its curve outranks a key on this frame, as in
        # Maya's Channel Box: either way it is lost on the next time change.
        if result == "keyframe":
            if cls.is_off_curve(plug):
                return "keyframe_changed"
            if cls.has_key_at_current_time(plug):
                return "keyframe_active"
        return result

    @staticmethod
    def is_off_curve(plug, tolerance=1e-6):
        """Return ``True`` if keyed *plug* holds a value its curve does not.

        Moving a keyed object without keying it sets the plug until the next
        time change re-evaluates the curve; Maya's Channel Box shows those
        fields in a lighter red.  Only a plug driven directly by an animCurve
        is judged -- through a conversion or blend node the two values are in
        different units or mixed, so they are not comparable.

        Parameters:
            plug (str): ``node.attr``.
            tolerance (float): Relative tolerance of the comparison.

        Returns:
            bool
        """
        try:
            if not cmds.listConnections(plug, s=True, d=False, type="animCurve"):
                return False
            t = cmds.currentTime(q=True)
            curve = cmds.keyframe(plug, q=True, eval=True, time=(t, t)) or []
            value = cmds.getAttr(plug)
        except Exception:
            return False
        if len(curve) != 1 or not isinstance(value, (int, float)):
            return False
        scale = max(1.0, abs(value), abs(curve[0]))
        return abs(value - curve[0]) > tolerance * scale

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
            ``(is_locked, connection_type)`` with *connection_type* one of
            :meth:`classify_connection`'s values.
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
                Channels._fmt_float(v) if isinstance(v, float) else str(v) for v in val
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
        Channels.set_lock(nodes, [attr_name], not current)

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
                                f"Unknown enum label '{value}' for {node}.{attr_name}"
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
        """Return the shape node name(s) for *nodes*.

        Delegates to :meth:`NodeUtils.get_shape_node`, which resolves the
        shape from a transform, a shape, *or* a construction-history node.
        The last case is what lets the footer *Shape* button keep working
        after the *History* button has swapped the selection to a history
        node — a plain ``listRelatives(shapes=True)`` returns nothing for a
        DG history node, which produced the spurious "No shape nodes found"
        warning and broke shape<->history toggling.
        """
        shapes = NodeUtils.get_shape_node(nodes, returned_type="str")
        if not isinstance(shapes, list):
            shapes = [shapes] if shapes else []
        # get_shape_node returns minimal-unique names; this method's contract
        # (pre-delegation) is full DAG paths.
        return cmds.ls(shapes, long=True) if shapes else []

    @staticmethod
    def get_history_nodes(nodes):
        """Return the construction-history input node(s) for *nodes*.

        Deliberately *not* delegated to ``NodeUtils.get_history_node``
        (unlike :meth:`get_shape_nodes`): that helper resolves a *shape*
        input to the bottom of the construction stack (oldest creator)
        while resolving a *transform* to the top (newest), so pressing
        History from the shape selection would land on a different node
        than pressing it from the transform — breaking the footer
        Shape<->History toggle. This local version lands on the same
        top-of-stack node from either a transform or a shape, keeping the
        toggle stable.
        """
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
        # Decide the action based on the *primary* node's state so the
        # operation is consistent across a multi-selection batch.
        try:
            primary_keys = cmds.keyframe(f"{nodes[0]}.{attr_name}", q=True, time=(t, t))
        except Exception:
            primary_keys = None
        return Channels.set_key_at_current_time(
            nodes, attr_name, keyed=not primary_keys
        )

    @staticmethod
    def set_key_at_current_time(nodes, attr_name, keyed=True):
        """Set (``keyed=True``) or remove (``keyed=False``) the key on
        *attr_name* for *nodes* at the current time.

        Explicit rather than a toggle, so repeating it is harmless: setting
        over an existing key re-keys the current value, and removing where no
        key sits does nothing.

        Returns ``"set"`` or ``"removed"``, or ``None`` if *nodes* is empty.
        """
        if not nodes:
            return None
        t = cmds.currentTime(q=True)
        action = "Set" if keyed else "Remove"
        cmds.undoInfo(openChunk=True, chunkName=f"{action} Key: {attr_name}")
        try:
            for node in nodes:
                plug = f"{node}.{attr_name}"
                try:
                    if keyed:
                        cmds.setKeyframe(plug)
                    else:
                        cmds.cutKey(plug, time=(t, t), clear=True)
                except Exception:
                    pass
        finally:
            cmds.undoInfo(closeChunk=True)
        return "set" if keyed else "removed"

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

        The Channels panel enables its *Freeze Transforms* / *Unfreeze
        Transforms* menu items by it.
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

        cmds.undoInfo(openChunk=True, chunkName="Freeze Transforms")
        try:
            # The bake history is stamped by XformUtils.freeze_transforms
            # itself (store=True), which also traverses the subtree a group
            # freeze flattens — stamping it here as well would double-compose
            # the history and make a later unfreeze overshoot.
            XformUtils.freeze_transforms(nodes, store=store, **kwargs)
        finally:
            cmds.undoInfo(closeChunk=True)
        return True

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
    parse_enum_def = staticmethod(Attributes.parse_enum_def)
    _parse_enum_def = staticmethod(Attributes.parse_enum_def)
    _build_enum_string = staticmethod(Attributes.build_enum_string)
    get_enum_fields = staticmethod(Attributes.get_enum_fields)
    get_enum_label = staticmethod(Attributes.get_enum_label)
    rename_enum_field = staticmethod(Attributes.rename_enum_field)
    add_enum_field = staticmethod(Attributes.add_enum_field)
    delete_enum_field = staticmethod(Attributes.delete_enum_field)

    # ------------------------------------------------------------------
    # Panel surface: what tentacle's shared Channels panel asks of a host
    # (blendertk's ``Channels`` mirrors it where Blender has the concept)
    # ------------------------------------------------------------------

    @staticmethod
    def node_name(node, full=False):
        """*node*'s display name: its leaf name, or with *full* its long path.

        Parameters:
            node (str): A node as :meth:`get_selected_nodes` returns it.
            full (bool): The long DAG path rather than the leaf.

        Returns:
            str | None: ``None`` for no node.
        """
        if not node:
            return None
        return str(node) if full else str(node).rsplit("|", 1)[-1]

    @staticmethod
    def get_custom_attrs(node):
        """*node*'s user-defined attribute names (``[]`` when it has none or is gone)."""
        try:
            return cmds.listAttr(node, userDefined=True) or []
        except Exception:
            return []

    @staticmethod
    def is_locked(node, attr_name):
        """Whether *attr_name* is locked on *node* (``False`` when it is not there)."""
        try:
            return bool(cmds.getAttr(f"{node}.{attr_name}", lock=True))
        except Exception:
            return False

    @staticmethod
    def select_nodes(nodes):
        """Replace the selection with *nodes* (clear it for none); return them."""
        nodes = list(nodes or [])
        if nodes:
            cmds.select(nodes, replace=True)
        else:
            cmds.select(clear=True)
        return nodes

    @staticmethod
    def get_numeric_values(nodes, attr_name):
        """``{node: value}`` for the *nodes* whose *attr_name* exists, is unlocked
        and holds a number: what a scrub or a wheel step moves.

        Returns:
            dict: Values as floats, in *nodes*' order.
        """
        values = {}
        for node in nodes:
            plug = f"{node}.{attr_name}"
            try:
                if not cmds.attributeQuery(attr_name, node=node, exists=True):
                    continue
                if cmds.getAttr(plug, lock=True):
                    continue
                value = cmds.getAttr(plug)
            except Exception:
                continue
            if isinstance(value, (int, float)):
                values[node] = float(value)
        return values

    @staticmethod
    def set_numeric_values(values, attr_name):
        """Set *attr_name* on each node of *values* (``{node: value}``) as one
        undo step; a node that refuses (locked, connected) is skipped."""
        with CoreUtils.undo_chunk(f"Edit Attr: {attr_name}"):
            for node, value in values.items():
                try:
                    cmds.setAttr(f"{node}.{attr_name}", value)
                except Exception:
                    continue

    @staticmethod
    def defer(fn):
        """Run *fn* once Maya is idle (``evalDeferred``); at once outside Maya.

        The panel defers its rebuilds through this, so a burst of scene events
        (a script selecting in a loop) is one rebuild after it, not one each.
        """
        if cmds is None:
            fn()
            return
        cmds.evalDeferred(fn)

    @classmethod
    def open_editor(cls, key):
        """Open the :attr:`EDITORS` editor *key*; ``False`` when it could not be."""
        command = cls._EDITOR_COMMANDS.get(key)
        if command is None:
            return False
        from mayatk.ui_utils._ui_utils import UiUtils

        try:
            return UiUtils.open_editor(command) is not None
        except Exception:
            return False

    # -- Live updates ----------------------------------------------------

    @staticmethod
    def _quiet(fn):
        """*fn* as a host callback: arguments dropped, errors kept out of
        Maya's callback pipeline (an exception there can destabilize the DG)."""

        def call(*_args):
            try:
                fn()
            except Exception:
                pass

        return call

    @classmethod
    def watch_scene(cls, owner, on_rebuild, on_values, widget=None):
        """Call a panel back on the scene events that change what it shows.

        *on_rebuild*: the rows may have changed -- the selection, a scene
        opened, a connection made or broken anywhere. *on_values*: only values
        and key states may have -- the time changed, or a key was set or cut on
        an existing curve (which changes no attribute or connection, so nothing
        else reports it). A callback may run off the main thread or inside a DG
        notification: it should only schedule work.

        Parameters:
            owner: Owns every subscription (:meth:`unwatch`).
            on_rebuild (callable): Takes no arguments.
            on_values (callable): Takes no arguments.
            widget (QWidget): When given, the subscriptions end as it is destroyed.

        Returns:
            bool: True.
        """
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        rebuild, values = cls._quiet(on_rebuild), cls._quiet(on_values)
        for event in ("SelectionChanged", "SceneOpened", "NewSceneOpened"):
            mgr.subscribe(event, rebuild, owner=owner)
        mgr.subscribe("timeChanged", values, owner=owner)
        if widget is not None:
            mgr.connect_cleanup(widget, owner=owner)
        try:
            import maya.api.OpenMaya as om2

            mgr.add_om_callback(
                om2.MDGMessage.addConnectionCallback, rebuild, owner=owner
            )
        except ImportError:
            pass
        try:
            import maya.api.OpenMayaAnim as oma2

            mgr.add_om_callback(
                oma2.MAnimMessage.addAnimCurveEditedCallback, values, owner=owner
            )
        except ImportError:
            pass
        return True

    @classmethod
    def watch_nodes(cls, owner, nodes, on_rebuild, on_attr, on_values):
        """Call a panel back on changes to *nodes* (redo it per selection).

        *on_rebuild()*: an attribute was added or removed. *on_attr(name,
        lock_changed, value_changed)*: one was locked / unlocked and/or set
        (its long name). *on_values()*: a viewport manipulator moved the node
        (transform tools write the matrix directly, so the attribute message
        comes only on release; this fires on the Evaluation Manager's worker
        threads, measured on Maya 2025).

        Returns:
            list: The subscription tokens (:meth:`unwatch`).
        """
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        try:
            import maya.api.OpenMaya as om2
        except ImportError:
            return []
        lock_mask = (
            om2.MNodeMessage.kAttributeLocked | om2.MNodeMessage.kAttributeUnlocked
        )
        set_mask = om2.MNodeMessage.kAttributeSet

        def changed(msg, plug, *_args):
            try:
                locked, valued = bool(msg & lock_mask), bool(msg & set_mask)
                if locked or valued:
                    on_attr(plug.partialName(useLongNames=True), locked, valued)
            except Exception:
                pass

        added_removed, moved = cls._quiet(on_rebuild), cls._quiet(on_values)
        mgr = ScriptJobManager.instance()
        tokens = []
        selection = om2.MSelectionList()
        for node in nodes:
            try:
                selection.clear()
                selection.add(node)
                mobj = selection.getDependNode(0)
            except Exception:
                continue
            for register, callback in (
                (om2.MNodeMessage.addAttributeAddedOrRemovedCallback, added_removed),
                (om2.MNodeMessage.addAttributeChangedCallback, changed),
            ):
                token = mgr.add_om_callback(register, mobj, callback, owner=owner)
                if token is not None:
                    tokens.append(token)
            try:  # only a DAG node has a matrix to watch
                dag_path = om2.MDagPath.getAPathTo(mobj)
            except Exception:
                continue
            token = mgr.add_om_callback(
                om2.MDagMessage.addMatrixModifiedCallback, dag_path, moved, owner=owner
            )
            if token is not None:
                tokens.append(token)
        return tokens

    @staticmethod
    def unwatch(owner=None, tokens=()):
        """End subscriptions: the *tokens* given, and every one *owner* holds."""
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for token in tokens or ():
            mgr.unsubscribe(token)
        if owner is not None:
            mgr.unsubscribe_all(owner)

    # -- The channel box ------------------------------------------------

    def link_channel_box(self, on_selection_changed, selected_rows):
        """Keep a panel's channel selection and Maya's channel box in step.

        Parameters:
            on_selection_changed (callable): Called with the box's selection
                (:meth:`channel_box_selection`) whenever it changes.
            selected_rows (callable): Takes no arguments; returns the long
                names of the channels selected in the panel (``[]`` while it is
                not shown). It stands in for the box while Maya has it emptied
                (tabbed away), so Set Selected Keys and friends act on the
                panel's rows (:meth:`ChannelBox.add_stand_in`).

        Returns:
            bool: True when the box's selection signal is connected (the box
            may not be built yet; link again after a scene change, which can
            replace it).
        """
        return self._channel_box.link_channel_box(on_selection_changed, selected_rows)

    def unlink_channel_box(self):
        """Undo :meth:`link_channel_box` (a no-op when not linked)."""
        return self._channel_box.unlink_channel_box()

    def channel_box_selection(self):
        """The long names of the attributes selected in the channel box.

        Every section. The box reports short names (``tx``) for a selection
        made by script and long ones for a selection made by hand: both come
        back long, as the panel's rows are.

        Returns:
            set[str]
        """
        return self._channel_box.channel_box_selection()

    @staticmethod
    def select_in_channel_box(attr_names):
        """Select *attr_names* in the channel box (best effort: it may be hidden)."""
        from mayatk.ui_utils.channel_box import ChannelBox

        try:
            ChannelBox.select_visual(list(attr_names))
        except Exception:
            pass

    @staticmethod
    def _attr_names(node, attr_names, long=True):
        """*attr_names* as *node*'s long (or short) attribute names, deduped.

        A name *node* does not know stays as given when converting to long, and
        is dropped when converting to short (a stand-in reports only real ones).
        """
        result = []
        for attr in attr_names:
            try:
                if long:
                    name = cmds.attributeQuery(attr, node=node, longName=True)
                else:
                    name = cmds.attributeQuery(attr, node=node, shortName=True)
            except Exception:
                if not long:
                    continue
                name = attr
            if name not in result:
                result.append(name)
        return result
