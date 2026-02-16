# !/usr/bin/python
# coding=utf-8
"""UI slots for the Attribute Manager.

Provides ``AttributeManagerController`` for Maya attribute query/mutation logic,
and ``AttributeManagerSlots`` — a single-table interface for inspecting,
editing, locking, and managing Maya node attributes.
"""
import maya.cmds as cmds
import maya.mel as mel

from uitk.widgets.footer import FooterStatusController
from mayatk.node_utils.attributes._attributes import Attributes


class AttributeManagerController:
    """Controller for Maya attribute operations.

    Encapsulates all Maya attribute querying, filtering, and mutation logic
    so that ``AttributeManagerSlots`` only handles UI wiring.
    """

    # Maps ComboBox items → kwargs for ``cmds.listAttr``.
    # Use ``_custom_filter`` key for filters that need Python-side logic
    # beyond what ``cmds.listAttr`` supports natively.
    FILTER_MAP = {
        "Custom": {"userDefined": True},
        "Keyable": {"keyable": True},
        "Channel Box": {"_custom_filter": "channel_box"},
        "Locked": {"locked": True},
        "Connected": {"connected": True},
        "Settable": {"settable": True},
        "Visible": {"visible": True},
        "Keyed": {"_custom_filter": "keyed"},
        "All": {},
    }

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    @staticmethod
    def get_selected_nodes():
        """Return the current Maya selection as string node names.

        Uses ``maya.cmds`` exclusively to avoid triggering
        ``pymel.core.system`` import during SafeMode initialisation.
        """
        return cmds.ls(sl=True, long=True) or []

    @staticmethod
    def get_channel_box_selection():
        """Return all attribute names currently selected in Maya's channel box.

        Aggregates selection from Main, Shape, History, and Output sections.
        """
        try:
            from mayatk.env_utils.channel_box import ChannelBox

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
        kwargs = AttributeManagerController.FILTER_MAP.get(
            filter_key, AttributeManagerController.FILTER_MAP["Custom"]
        ).copy()
        if invert:
            kwargs["_invert"] = True
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

        if custom_filter == "channel_box":
            # Union of keyable attrs + non-keyable attrs shown in channel box
            sets_k = [set(cmds.listAttr(n, keyable=True) or []) for n in nodes]
            sets_c = [set(cmds.listAttr(n, channelBox=True) or []) for n in nodes]
            common = sets_k[0] | sets_c[0]
            for sk, sc in zip(sets_k[1:], sets_c[1:]):
                common &= sk | sc
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
            # Invert: return all attrs minus the filtered set
            all_sets = [set(cmds.listAttr(n) or []) for n in nodes]
            all_common = all_sets[0]
            for s in all_sets[1:]:
                all_common &= s
            common = all_common - common

        if custom_filter == "channel_box":
            return AttributeManagerController._sort_channel_box(common)
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

    @staticmethod
    def get_attr_value(node, attr_name):
        """Safely get an attribute value, returning ``None`` on failure."""
        try:
            return cmds.getAttr(f"{node}.{attr_name}")
        except Exception:
            return None

    @staticmethod
    def get_attr_type(node, attr_name):
        """Return the Maya attribute type string."""
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

        Uses only ``maya.cmds`` — no pymel or cross-module imports — to
        avoid SafeMode and circular-import issues.

        Returns one of:
        - ``"none"`` — no incoming connection
        - ``"muted"`` — channel is muted (mute node)
        - ``"keyframe"`` — driven by an animCurve
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
            return result
        except Exception:
            return "none"

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
            val = cls.get_attr_value(primary, attr_name)
            if multi:
                for other in nodes[1:]:
                    other_val = cls.get_attr_value(other, attr_name)
                    if other_val != val:
                        val = "*"
                        break
            val_str = cls.format_value(val)

            # Type
            attr_type = cls.get_attr_type(primary, attr_name)

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
    def format_value(val):
        """Convert a Maya attribute value to a display string."""
        if val == "*":
            return "*"
        if val is None:
            return ""
        if isinstance(val, float):
            return f"{val:.4f}"
        if isinstance(val, (list, tuple)):
            inner = ", ".join(
                f"{v:.4f}" if isinstance(v, float) else str(v) for v in val
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
        if attr_type in ("string",):
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
                    attr_type = cmds.attributeQuery(
                        attr_name, node=node, attributeType=True
                    )
                    value = cls.parse_value(text, attr_type)
                    if value is not None:
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


class AttributeManagerSlots:
    """Switchboard slots for the Attribute Manager UI.

    Layout
    ------
    - **Header menu**: Global actions (Create Attribute).
    - **ComboBox**: Filter displayed attributes (Custom, Keyable, All).
    - **Table**: One row per attribute on the primary selection.
      Columns: Name | Lock | Connect | Value | Type.
      Lock and Connect are narrow icon-only columns (clickable toggles, color-coded).
    - **Context menu**: Per-row operations (Lock/Unlock, Delete, Reset to Default).
    """

    # Column indices — Name | Lock | Connect | Value | Type
    COL_NAME = 0
    COL_LOCK = 1
    COL_CONN = 2
    COL_VALUE = 3
    COL_TYPE = 4

    _ROW_SELECTION_COLUMNS = {
        "name": 0,
        "value": 3,
        "type": 4,
    }

    # Single source of truth for all icon/state colours.
    # Desaturated Maya channel-box colour scheme.
    ACTION_COLOR_MAP = {
        "off": "#555555",  # dim grey — inactive / default
        "locked": "#8a9bb0",  # bluish grey — lock icon
        "keyframe": "#c86464",  # desaturated red — keyed
        "connected": "#c8b448",  # desaturated yellow — generic connection
        "expression": "#b478c8",  # desaturated purple — expression-driven
        "driven_key": "#6898b8",  # desaturated light-blue — set-driven key
        "constraint": "#5878b8",  # desaturated blue — constraint
        "muted": "#888850",  # olive — muted channel
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.attribute_manager
        self.controller = AttributeManagerController()
        self._scene_change_job_ids = None
        self._refresh_pending = False
        self._footer_controller = self._create_footer_controller()

        # Force-connect table selection signal (in case tbl000_init guard skipped it)
        try:
            self.ui.tbl000.itemSelectionChanged.disconnect(
                self._on_table_selection_changed
            )
        except Exception:
            pass
        self.ui.tbl000.itemSelectionChanged.connect(self._on_table_selection_changed)

        # Channel Box → Table sync via Qt signal (instant, replaces polling)
        self._last_cb_selection = set()
        self._cb_signal_connected = False
        self._syncing_selection = False
        self._connect_cb_signal()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Populate the header menu with global actions."""
        widget.menu.setTitle("Actions:")

        # --- Create Attribute ---
        widget.menu.add("Separator", setTitle="Create")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Create Attribute …",
            setToolTip="Add a new custom attribute to the selected objects.",
            setObjectName="show_create_menu",
        )

        # --- Visibility ---
        widget.menu.add("Separator", setTitle="Visibility")
        self._chk_show_type = widget.menu.add(
            "QCheckBox",
            setText="Show Type",
            setChecked=False,
            setToolTip="Toggle the Type column in the attribute table.",
            setObjectName="chk_show_type",
        )
        self._chk_show_type.toggled.connect(self._on_toggle_type_column)

        # --- Selection ---
        widget.menu.add("Separator", setTitle="Selection")
        widget.menu.add(
            "QPushButton",
            setText="Select Shape Node",
            setObjectName="hdr_select_shape",
            setToolTip="Select the shape node(s) of the current selection.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select History Node",
            setObjectName="hdr_select_history",
            setToolTip="Select the construction-history node(s) of the current selection.",
        )
        widget.menu.hdr_select_shape.clicked.connect(self._hdr_select_shape)
        widget.menu.hdr_select_history.clicked.connect(self._hdr_select_history)

        # --- Maya Editors ---
        widget.menu.add("Separator", setTitle="Maya Editors")
        widget.menu.add(
            "QPushButton",
            setText="Channel Control …",
            setObjectName="hdr_channel_control",
            setToolTip="Open Maya's Channel Control editor.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Connection Editor …",
            setObjectName="hdr_connection_editor",
            setToolTip="Open Maya's Connection Editor.",
        )
        widget.menu.hdr_channel_control.clicked.connect(
            lambda: mel.eval("ChannelControlEditor")
        )
        widget.menu.hdr_connection_editor.clicked.connect(
            lambda: mel.eval("ConnectionEditor")
        )

    # --- Header action handlers ---

    def _hdr_select_shape(self):
        """Select the shape node(s) for the current selection."""
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        shapes = self.controller.get_shape_nodes(nodes)
        if shapes:
            cmds.select(shapes, replace=True)
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No shape nodes found.")

    def _hdr_select_history(self):
        """Select the history (construction) node(s) for the current selection."""
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        history = self.controller.get_history_nodes(nodes)
        if history:
            cmds.select(history, replace=True)
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No history nodes found.")

    def show_create_menu(self, *args):
        """Show the *Create Attribute* popup."""
        from uitk import Menu

        menu = Menu(parent=self.ui, position="cursor")
        menu.setTitle("Create Attribute")

        # -- Identity -------------------------------------------------------
        menu.add("QLabel", setText="Name:", row=0, col=0)
        le_name = menu.add(
            "QLineEdit",
            setPlaceholderText="my_attribute",
            setObjectName="le_attr_name",
            row=0,
            col=1,
        )
        menu.add("QLabel", setText="Type:", row=1, col=0)
        cmb_type = menu.add(
            "QComboBox",
            setObjectName="cmb_attr_type",
            addItems=["float", "int", "bool", "string", "enum", "double3"],
            row=1,
            col=1,
        )

        # -- Numeric range --------------------------------------------------
        sep_range = menu.add("Separator", setTitle="Range", row=2)
        lbl_default = menu.add("QLabel", setText="Default:", row=3, col=0)
        spn_default = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_default",
            setMinimum=-1e9,
            setMaximum=1e9,
            row=3,
            col=1,
        )
        lbl_min = menu.add("QLabel", setText="Min:", row=4, col=0)
        spn_min = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_min",
            setMinimum=-1e9,
            setMaximum=1e9,
            row=4,
            col=1,
        )
        lbl_max = menu.add("QLabel", setText="Max:", row=5, col=0)
        spn_max = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_max",
            setMinimum=-1e9,
            setMaximum=1e9,
            setValue=1.0,
            row=5,
            col=1,
        )

        # -- Enum names -----------------------------------------------------
        sep_enum = menu.add("Separator", setTitle="Enum", row=6)
        lbl_enum = menu.add("QLabel", setText="Names:", row=7, col=0)
        le_enum = menu.add(
            "QLineEdit",
            setPlaceholderText="A:B:C",
            setToolTip="Colon-separated enum labels.",
            setObjectName="le_enum_names",
            row=7,
            col=1,
        )

        # -- Options --------------------------------------------------------
        menu.add("Separator", row=8)
        chk_keyable = menu.add(
            "QCheckBox",
            setText="Keyable",
            setChecked=True,
            setObjectName="chk_keyable",
            row=9,
        )
        btn = menu.add("QPushButton", setText="Create", row=10)

        # -- Reactive show/hide ---------------------------------------------
        _numeric_widgets = [
            sep_range,
            lbl_default,
            spn_default,
            lbl_min,
            spn_min,
            lbl_max,
            spn_max,
        ]
        _enum_widgets = [sep_enum, lbl_enum, le_enum]

        def _on_type_changed(text):
            is_numeric = text in ("float", "int", "double3")
            is_enum = text == "enum"
            for w in _numeric_widgets:
                w.setVisible(is_numeric)
            for w in _enum_widgets:
                w.setVisible(is_enum)

        cmb_type.currentTextChanged.connect(_on_type_changed)
        _on_type_changed(cmb_type.currentText())

        # -- Create handler -------------------------------------------------
        def _on_create():
            name = le_name.text().strip()
            if not name:
                self.sb.message_box("Warning: Attribute name cannot be empty.")
                return
            sel = cmds.ls(sl=True)
            if not sel:
                self.sb.message_box("Warning: Nothing selected.")
                return

            attr_type = cmb_type.currentText()
            self.controller.create_attribute(
                sel,
                name,
                attr_type,
                keyable=chk_keyable.isChecked(),
                min_val=spn_min.value() if spn_min.isEnabled() else None,
                max_val=spn_max.value() if spn_max.isEnabled() else None,
                default_val=spn_default.value(),
                enum_names=le_enum.text().strip() if attr_type == "enum" else "",
            )
            menu.hide()
            self._refresh_table(self.ui.tbl000)

        btn.clicked.connect(_on_create)
        menu.show()

    def _on_toggle_type_column(self, visible):
        """Show or hide the Type column in the attribute table."""
        self.ui.tbl000.setColumnHidden(self.COL_TYPE, not visible)

    # ------------------------------------------------------------------
    # Filter ComboBox
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Populate filter combobox."""
        widget.addItems(
            [
                k
                for k in AttributeManagerController.FILTER_MAP.keys()
                if not k.startswith("_")
            ]
        )

    def cmb000(self, index):
        """Filter changed — refresh table."""
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Invert Checkbox
    # ------------------------------------------------------------------

    def chk000(self, state):
        """Invert checkbox toggled — refresh table."""
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        """One-time table setup: signals, context menu, scriptJobs."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self._handle_cell_edit)

            self._setup_action_columns(widget)
            self._setup_context_menu(widget)
            self._setup_scene_change_callbacks(widget)

            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)
            widget.itemSelectionChanged.connect(self._on_table_selection_changed)

            try:
                widget.destroyed.connect(self.cleanup_scene_callbacks)
            except Exception:
                pass

        self._refresh_table(widget)

    def _setup_action_columns(self, widget):
        """Register Lock and Connect as icon-toggle action columns."""
        clr = self.ACTION_COLOR_MAP

        widget.actions.add(
            self.COL_LOCK,
            states={
                "locked": {
                    "icon": "lock",
                    "color": clr["locked"],
                    "tooltip": "Locked — click to unlock",
                    "action": self._on_icon_cell_clicked,
                },
                "unlocked": {
                    "icon": "unlock",
                    "color": clr["off"],
                    "tooltip": "Unlocked — click to lock",
                    "action": self._on_icon_cell_clicked,
                },
            },
        )

        conn_states = {
            "none": {
                "icon": "disconnect",
                "color": clr["off"],
                "tooltip": "Not connected",
                "action": self._on_icon_cell_clicked,
            },
        }
        for key in (
            "connected",
            "keyframe",
            "expression",
            "driven_key",
            "constraint",
            "muted",
        ):
            conn_states[key] = {
                "icon": "connect",
                "color": clr.get(key, clr["connected"]),
                "tooltip": f"{key.replace('_', ' ').title()} — click to break",
                "action": self._on_icon_cell_clicked,
            }
        widget.actions.add(self.COL_CONN, states=conn_states)

    def _setup_context_menu(self, widget):
        """Build the table's right-click context menu and bind handlers."""
        menu = widget.menu
        menu.setTitle("Attribute Actions:")

        # fmt: off
        _items = [
            ("Edit",       None),
            ("Lock",       "ctx_lock",            "Lock the selected attribute(s)."),
            ("Unlock",     "ctx_unlock",          "Unlock the selected attribute(s)."),
            ("Reset to Default", "ctx_reset_default", "Reset the attribute to its default value."),
            ("Toggle Keyable",   "ctx_toggle_keyable", "Toggle the keyable state of the attribute."),
            ("Values",     None),
            ("Copy Values",  "ctx_copy_values",   "Copy selected attribute values to clipboard."),
            ("Paste Values", "ctx_paste_values",  "Paste attribute values from clipboard."),
            ("Channel Box", None),
            ("Breakdown",  "ctx_breakdown",       "Set a breakdown key on the selected attribute(s)."),
            ("Mute",       "ctx_mute",            "Mute selected attribute(s) — suppress animation."),
            ("Unmute",     "ctx_unmute",          "Unmute selected attribute(s)."),
            ("Hide Selected",     "ctx_hide",     "Hide the attribute from the channel box."),
            ("Lock and Hide",     "ctx_lock_and_hide", "Lock the attribute and hide it from the channel box."),
            ("Select Connection", "ctx_select_connection", "Select the upstream node driving this attribute."),
            ("Break Connection",  "ctx_break_connection",  "Break incoming connection(s) on the selected attribute(s)."),
            ("Manage",     None),
            ("Delete Attribute",  "ctx_delete",   "Delete the selected custom attribute(s)."),
        ]
        # fmt: on

        handler_map = {
            "ctx_lock": self._ctx_lock,
            "ctx_unlock": self._ctx_unlock,
            "ctx_reset_default": self._ctx_reset_default,
            "ctx_toggle_keyable": self._ctx_toggle_keyable,
            "ctx_copy_values": self._ctx_copy_values,
            "ctx_paste_values": self._ctx_paste_values,
            "ctx_breakdown": self._ctx_breakdown,
            "ctx_mute": self._ctx_mute,
            "ctx_unmute": self._ctx_unmute,
            "ctx_hide": self._ctx_hide,
            "ctx_lock_and_hide": self._ctx_lock_and_hide,
            "ctx_select_connection": self._ctx_select_connection,
            "ctx_break_connection": self._ctx_break_connection,
            "ctx_delete": self._ctx_delete,
        }

        for entry in _items:
            label = entry[0]
            obj_name = entry[1] if len(entry) > 1 else None
            tooltip = entry[2] if len(entry) > 2 else ""

            if obj_name is None:
                # Section separator
                menu.add("Separator", setTitle=label)
            else:
                menu.add(
                    "QPushButton",
                    setText=label,
                    setObjectName=obj_name,
                    setToolTip=tooltip,
                )
                handler = handler_map.get(obj_name)
                if handler:
                    widget.register_menu_action(
                        obj_name,
                        lambda sel, fn=handler: fn(sel),
                        columns=self._ROW_SELECTION_COLUMNS,
                    )

    # ------------------------------------------------------------------
    # Table data
    # ------------------------------------------------------------------

    def _get_filter_kwargs(self):
        """Return the ``cmds.listAttr`` kwargs for the active filter."""
        cmb = getattr(self.ui, "cmb000", None)
        key = cmb.currentText() if cmb else "Custom"

        chk = getattr(self.ui, "chk000", None)
        invert = bool(chk and chk.isChecked())

        return self.controller.get_filter_kwargs(key, invert)

    def _refresh_table(self, widget):
        """Rebuild the table from the current selection and filter."""
        cmds.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.blockSignals(True)
            widget.clear()

            nodes = self.controller.get_selected_nodes()
            if not nodes:
                widget.add(
                    [["No selection", "", "", "", ""]],
                    headers=["Name", "", "", "Value", "Type"],
                )
                self._configure_columns(widget)
                return

            filter_kwargs = self._get_filter_kwargs()
            rows, attr_states = self.controller.build_table_data(nodes, filter_kwargs)

            widget.add(rows, headers=["Name", "", "", "Value", "Type"])
            self._configure_columns(widget)

            # Set action states (icon colours are handled by the action column config)
            for row_idx, (is_locked, conn_type) in enumerate(attr_states):
                widget.actions.set(
                    row_idx,
                    self.COL_LOCK,
                    "locked" if is_locked else "unlocked",
                )
                widget.actions.set(
                    row_idx,
                    self.COL_CONN,
                    conn_type,  # "none", "keyframe", "expression", etc.
                )

            # Make name cells editable for user-defined attrs and
            # store the original name so renames can be detected.
            self._set_name_editability(widget, nodes)

            # Sync table selection with channel box selection.
            # Fetch fresh CB data *before* syncing so the table reflects
            # the current state rather than a stale cache.
            self._last_cb_selection = self._normalize_cb_attrs(
                set(self.controller.get_channel_box_selection())
            )
            self._sync_table_to_channel_box(widget)

        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)
            cmds.waitCursor(state=False)

        # Restore column visibility from the Show Type checkbox.
        chk = getattr(self, "_chk_show_type", None)
        if chk is not None:
            widget.setColumnHidden(self.COL_TYPE, not chk.isChecked())

        if self._footer_controller:
            self._footer_controller.update()

    def _sync_table_to_channel_box(self, widget):
        """Select table rows matching the current channel box selection.

        Uses ``QItemSelectionModel.select()`` with ``Select | Rows`` so
        that multiple matching rows are highlighted additively after an
        initial clear.
        """
        cb_attrs = self._last_cb_selection

        # Block signals to prevent loop with _on_table_selection_changed
        was_blocked = widget.signalsBlocked()
        widget.blockSignals(True)
        try:
            widget.clearSelection()

            if not cb_attrs:
                return

            sel_model = widget.selectionModel()
            model = widget.model()
            QSel = self.sb.QtCore.QItemSelectionModel
            for row_idx in range(widget.rowCount()):
                name_item = widget.item(row_idx, self.COL_NAME)
                if name_item and name_item.text().strip() in cb_attrs:
                    sel_model.select(
                        model.index(row_idx, 0),
                        QSel.Select | QSel.Rows,
                    )
        finally:
            widget.blockSignals(was_blocked)

    def _on_table_selection_changed(self):
        """Push table selection to the Maya Channel Box.

        Sends table-selected attribute names (long names) to
        ``ChannelBox.select_visual`` and updates the cache
        with the same long names so the CB signal handler
        doesn’t fight us.
        """
        if self.ui.tbl000.signalsBlocked() or self._syncing_selection:
            return

        selected_items = self.ui.tbl000.selectedItems()
        attr_names = []

        # Gather unique names from selected rows (COLUMN 0)
        for item in selected_items:
            if item.column() == self.COL_NAME:
                name = item.text().strip()
                if name and name not in attr_names:
                    attr_names.append(name)

        self._syncing_selection = True
        try:
            from mayatk.env_utils.channel_box import ChannelBox

            ChannelBox.select_visual(attr_names)
            self._last_cb_selection = set(attr_names)
        except Exception:
            pass
        finally:
            self._syncing_selection = False

    def _on_icon_cell_clicked(self, row, col):
        """Handle clicks on the Lock or Connect icon columns."""
        tbl = self.ui.tbl000
        name_item = tbl.item(row, self.COL_NAME)
        if not name_item or not name_item.text():
            return
        attr_name = name_item.text().strip()
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        if col == self.COL_LOCK:
            self.controller.toggle_lock(nodes, attr_name)
            self._refresh_table(tbl)

        elif col == self.COL_CONN:
            # Only attempt to break if the attr is actually connected.
            state = tbl.actions.get(row, col)
            if state and state != "none":
                self.controller.break_connections(nodes, attr_name)
                self._refresh_table(tbl)

    def _configure_columns(self, widget):
        """Set column resize modes and widths."""
        header = widget.horizontalHeader()
        header.setSectionsMovable(False)
        QHV = self.sb.QtWidgets.QHeaderView

        # Data columns
        header.setSectionResizeMode(self.COL_NAME, QHV.Interactive)
        widget.setColumnWidth(self.COL_NAME, 160)

        # Remaining data columns
        header.setSectionResizeMode(self.COL_VALUE, QHV.Stretch)
        header.setSectionResizeMode(self.COL_TYPE, QHV.Interactive)
        widget.setColumnWidth(self.COL_TYPE, 80)

    # ------------------------------------------------------------------
    # Inline editing
    # ------------------------------------------------------------------

    def _set_name_editability(self, widget, nodes):
        """Make name cells editable for user-defined attrs.

        Stores the original attribute name in ``Qt.UserRole`` so
        ``_handle_cell_edit`` can detect rename attempts.
        """
        Qt = self.sb.QtCore.Qt
        primary = nodes[0] if nodes else None
        user_attrs = (
            set(cmds.listAttr(primary, userDefined=True) or []) if primary else set()
        )

        for row_idx in range(widget.rowCount()):
            item = widget.item(row_idx, self.COL_NAME)
            if not item:
                continue
            attr_name = item.text().strip()
            # Store original name for rename detection.
            item.setData(Qt.UserRole, attr_name)
            if attr_name in user_attrs:
                item.setFlags(item.flags() | Qt.ItemIsEditable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

    def _handle_cell_edit(self, row, col):
        """Handle inline editing of the Name or Value column."""
        tbl = self.ui.tbl000
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        if col == self.COL_NAME:
            item = tbl.item(row, col)
            if not item:
                return
            Qt = self.sb.QtCore.Qt
            old_name = item.data(Qt.UserRole)
            new_name = item.text().strip()
            if not old_name or not new_name or new_name == old_name:
                return
            try:
                self.controller.rename_attribute(nodes, old_name, new_name)
            except Exception as exc:
                self.sb.message_box(f"Warning: Cannot rename '{old_name}': {exc}")
            self._refresh_table(tbl)
            return

        if col != self.COL_VALUE:
            return

        name_item = tbl.item(row, self.COL_NAME)
        if not name_item:
            return
        attr_name = name_item.text().strip()
        if not attr_name:
            return

        new_text = tbl.item(row, col).text().strip()
        self.controller.set_attribute_value(nodes, attr_name, new_text)

    # ------------------------------------------------------------------
    # Sync with Channel Box
    # ------------------------------------------------------------------

    def _connect_cb_signal(self):
        """Connect to the Channel Box's QItemSelectionModel signal.

        Safe to call repeatedly — disconnects any previous connection
        first.  Called from ``__init__`` and after every scene change
        (which may invalidate the C++ pointer).
        """
        try:
            from mayatk.env_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_selection_changed)
        except Exception:
            pass

        try:
            from mayatk.env_utils.channel_box import ChannelBox

            self._cb_signal_connected = ChannelBox.connect_selection_changed(
                self._on_cb_selection_changed
            )
        except Exception:
            self._cb_signal_connected = False

    def _on_cb_selection_changed(self, selected, deselected):
        """Slot for Channel Box ``selectionModel().selectionChanged``.

        Translates the Qt signal into table row highlights.
        """
        if self._syncing_selection:
            return

        try:
            from mayatk.env_utils.channel_box import ChannelBox

            raw_sel = set(ChannelBox.get_selected_attrs(sections="all"))
        except Exception:
            return

        current_sel = self._normalize_cb_attrs(raw_sel)

        if current_sel != self._last_cb_selection:
            self._syncing_selection = True
            try:
                self._last_cb_selection = current_sel
                self._sync_table_to_channel_box(self.ui.tbl000)
            finally:
                self._syncing_selection = False

    def _normalize_cb_attrs(self, cb_attrs):
        """Resolve channel-box attribute names to long names.

        The channel box may return short names (``tx``) or long names
        (``translateX``) depending on how the selection was made
        (Qt ``select_visual`` produces short names; manual clicks produce
        long names).  Normalising to long names via
        ``cmds.attributeQuery(longName=True)`` lets us compare against the
        table, which always shows long names from ``cmds.listAttr``.
        """
        if not cb_attrs:
            return set()
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return set(cb_attrs)
        node = nodes[0]
        result = set()
        for attr in cb_attrs:
            try:
                long = cmds.attributeQuery(attr, node=node, longName=True)
                result.add(long)
            except Exception:
                result.add(attr)
        return result

    # ------------------------------------------------------------------
    # Context menu handlers
    # ------------------------------------------------------------------

    def _selected_attrs_and_nodes(self, selection):
        """Extract attribute names and nodes from a menu *selection* payload.

        Returns ``(attr_names, nodes)`` or ``(None, None)`` if either is empty.
        """
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_names:
            return None, None
        return attr_names, nodes

    def _ctx_lock(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_lock(nodes, attrs, lock=True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_unlock(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_lock(nodes, attrs, lock=False)
        self._refresh_table(self.ui.tbl000)

    def _ctx_reset_default(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.reset_to_default(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_toggle_keyable(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.toggle_keyable(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_copy_values(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        copied = self.controller.copy_attr_values(nodes, attrs)
        if copied:
            self.sb.message_box(f"Result: Copied {len(copied)} attribute value(s).")

    def _ctx_paste_values(self, selection):
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        self.controller.paste_attr_values(nodes)
        self._refresh_table(self.ui.tbl000)

    def _ctx_breakdown(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_breakdown_key(nodes, attrs)

    def _ctx_mute(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.mute_attrs(nodes, attrs)

    def _ctx_unmute(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.unmute_attrs(nodes, attrs)

    def _ctx_hide(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.hide_attrs(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_lock_and_hide(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.lock_and_hide_attrs(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_select_connection(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        if not self.controller.select_connections(nodes, attrs[0]):
            self.sb.message_box(f"Warning: No incoming connection on '{attrs[0]}'.")

    def _ctx_break_connection(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        any_broken = any(self.controller.break_connections(nodes, a) for a in attrs)
        if any_broken:
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No connections to break.")

    def _ctx_delete(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.delete_attributes(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # ScriptJob lifecycle
    # ------------------------------------------------------------------

    def _setup_scene_change_callbacks(self, widget):
        """Register Maya scriptJobs and OpenMaya callbacks.

        Uses ``MDGMessage.addConnectionCallback`` to detect connection
        changes (make / break) so the table updates its color-coded
        icons immediately.
        """
        self.cleanup_scene_callbacks()

        events = [
            "SelectionChanged",
            "SceneOpened",
            "NewSceneOpened",
        ]

        def _callback(*args):
            self._on_scene_change(widget)

        job_ids = []
        for event in events:
            try:
                jid = cmds.scriptJob(
                    event=[event, _callback],
                    protected=False,
                )
                job_ids.append(jid)
            except Exception as e:
                print(f"AttributeManager: scriptJob '{event}' failed: {e}")

        self._scene_change_job_ids = job_ids

        # --- OpenMaya connection callback ---
        try:
            import maya.api.OpenMaya as om2

            def _on_connection_change(src_plug, dst_plug, made, *args):
                """Fires when any DG connection is made or broken."""
                self._on_scene_change(widget)

            cb_id = om2.MDGMessage.addConnectionCallback(_on_connection_change)
            self._om_callback_ids = [cb_id]
        except Exception:
            self._om_callback_ids = []

    def _on_scene_change(self, widget):
        """Debounced callback for scriptJob events."""
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def _do_refresh():
            self._refresh_pending = False
            try:
                # If widget is dead or hidden, skip refresh
                if not widget:  # or not widget.isVisible():
                    return
            except Exception:
                # Widget likely destroyed
                self.cleanup_scene_callbacks()
                return

            self._refresh_table(widget)

            # Reconnect the CB signal — the C++ pointer may have changed.
            self._connect_cb_signal()

        cmds.evalDeferred(_do_refresh)

    def cleanup_scene_callbacks(self):
        """Kill active scriptJobs, remove OpenMaya callbacks, and
        disconnect the Channel Box selection signal."""
        # Disconnect CB signal
        try:
            from mayatk.env_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_selection_changed)
        except Exception:
            pass
        self._cb_signal_connected = False

        if self._scene_change_job_ids is None and not getattr(
            self, "_om_callback_ids", None
        ):
            return

        ids = list(self._scene_change_job_ids or [])
        self._scene_change_job_ids = None

        om_ids = list(getattr(self, "_om_callback_ids", []))
        self._om_callback_ids = []

        def _kill(job_ids, om_cb_ids):
            for jid in job_ids:
                try:
                    if cmds.scriptJob(exists=jid):
                        cmds.scriptJob(kill=jid, force=True)
                except Exception:
                    pass
            if om_cb_ids:
                try:
                    import maya.api.OpenMaya as om2

                    for cb_id in om_cb_ids:
                        om2.MMessage.removeCallback(cb_id)
                except Exception:
                    pass

        cmds.evalDeferred(lambda: _kill(ids, om_ids))

    def __del__(self):
        self.cleanup_scene_callbacks()

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._resolve_footer_text,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _resolve_footer_text(self) -> str:
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return "No selection"
        names = ", ".join(n.rsplit("|", 1)[-1] for n in nodes[:3])
        suffix = f" (+{len(nodes) - 3})" if len(nodes) > 3 else ""
        return f"{names}{suffix}"


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
