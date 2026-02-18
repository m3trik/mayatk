# !/usr/bin/python
# coding=utf-8
"""Programmatic access to Maya's Channel Box.

Provides ``ChannelBox`` — a stateless helper for querying, selecting,
and hooking into the Channel Box without relying on undocumented MEL
globals scattered across call-sites.

Separation of concerns
----------------------
* **WidgetInspector** (devtools.py) — generic Qt introspection primitives.
* **ChannelBox** (this module) — Maya-specific channel box logic that
  *consumes* ``WidgetInspector`` when it needs to touch the underlying
  Qt widget (e.g. programmatic attribute selection).
"""
import logging

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

from qtpy import QtWidgets, QtCore

from mayatk.env_utils.devtools import DevTools, WidgetInspector

log = logging.getLogger(__name__)


class ChannelBox:
    """Query, select, and hook into Maya's Channel Box programmatically.

    All methods are static/classmethod — no instance state is needed.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _control_name():
        """Return the global Maya channel-box control name."""
        return mel.eval("global string $gChannelBoxName; $temp=$gChannelBoxName;")

    @classmethod
    def _widget(cls):
        """Return the channel box root QWidget.

        Always resolves a fresh pointer via ``MQtUtil`` to avoid stale
        wrappers — a common cause of ``RuntimeError`` in Maya 2025+.

        Returns:
            QWidget|None
        """
        name = cls._control_name()
        if not name:
            return None
        try:
            return WidgetInspector.from_maya_control(name)
        except Exception:
            log.debug("_widget: failed to resolve '%s'", name)
            return None

    @classmethod
    def _main_view(cls):
        """Return the channel box as a ``QTableView``.

        In Maya 2025+ the ``mainChannelBox`` control *is* a QTableView.
        Wrapping the pointer as ``QWidget`` and then searching children
        only finds hidden internal views — the real model (with nice
        names and values) lives on the root control itself.

        Calls ``processEvents()`` first to let Maya finish any pending
        widget rebuilds, which stabilises the C++ pointer.

        Returns:
            QTableView|None
        """
        name = cls._control_name()
        if not name:
            return None
        try:
            import maya.OpenMayaUI as omui
            from shiboken6 import wrapInstance

            # Let Maya finish any pending widget rebuilds.
            app = QtWidgets.QApplication.instance()
            if app:
                app.processEvents()

            ptr = omui.MQtUtil.findControl(name)
            if not ptr:
                return None

            view = wrapInstance(int(ptr), QtWidgets.QTableView)
            # Verify the pointer is alive and the model is populated.
            if view.model() is None or view.model().rowCount() == 0:
                return None
            return view
        except (ImportError, RuntimeError, AttributeError) as exc:
            log.debug("_main_view: failed (%s)", exc)
            return None

    @classmethod
    def connect_selection_changed(cls, callback):
        """Connect *callback* to the Channel Box's Qt selection signal.

        Since ``mainChannelBox`` is a ``QTableView`` in Maya 2025+, its
        ``selectionModel()`` emits ``selectionChanged(QItemSelection,
        QItemSelection)`` whenever the user clicks an attribute row.

        The C++ widget pointer can go stale after scene changes, so
        callers should re-invoke this method after ``SelectionChanged``
        or ``SceneOpened`` scriptJob events.

        Parameters
        ----------
        callback : callable
            Receives ``(selected: QItemSelection, deselected: QItemSelection)``.

        Returns
        -------
        bool
            ``True`` if the connection succeeded.
        """
        try:
            view = cls._main_view()
            if view is None:
                log.debug("connect_selection_changed: no view")
                return False
            sel_model = view.selectionModel()
            if sel_model is None:
                return False
            sel_model.selectionChanged.connect(callback)
            log.debug("connect_selection_changed: connected")
            return True
        except (RuntimeError, AttributeError) as exc:
            log.debug("connect_selection_changed: failed (%s)", exc)
            return False

    @classmethod
    def disconnect_selection_changed(cls, callback):
        """Disconnect a previously connected *callback*.

        Safe to call even if the widget has been destroyed.
        """
        try:
            view = cls._main_view()
            if view is None:
                return
            sel_model = view.selectionModel()
            if sel_model is None:
                return
            sel_model.selectionChanged.disconnect(callback)
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    @classmethod
    def get_selected_attrs(cls, sections="all"):
        """Return attribute names currently selected in the channel box.

        Parameters:
            sections (str|list): Which sections to query. One or more of:
                ``'main'`` (sma), ``'shape'`` (ssa), ``'history'`` (sha),
                ``'output'`` (soa), or ``'all'`` (default).

        Returns:
            list[str]: Short attribute names.
        """
        cb = cls._control_name()

        section_map = {
            "main": "sma",
            "shape": "ssa",
            "history": "sha",
            "output": "soa",
        }

        if sections == "all":
            flags = list(section_map.values())
        else:
            if isinstance(sections, str):
                sections = [sections]
            flags = [section_map[s] for s in sections if s in section_map]

        result = []
        for flag in flags:
            attrs = cmds.channelBox(cb, q=True, **{flag: True}) or []
            result.extend(attrs)

        return list(dict.fromkeys(result))  # dedupe, preserve order

    @classmethod
    def get_selected_objects(cls, sections="all"):
        """Return the object names associated with selected channel box attrs.

        Parameters:
            sections (str|list): ``'main'`` (mol), ``'shape'`` (sol),
                ``'history'`` (hol), ``'output'`` (ool), or ``'all'``.

        Returns:
            list[str]: Node names.
        """
        cb = cls._control_name()

        section_map = {
            "main": "mol",
            "shape": "sol",
            "history": "hol",
            "output": "ool",
        }

        if sections == "all":
            flags = list(section_map.values())
        else:
            if isinstance(sections, str):
                sections = [sections]
            flags = [section_map[s] for s in sections if s in section_map]

        result = []
        for flag in flags:
            objs = cmds.channelBox(cb, q=True, **{flag: True}) or []
            result.extend(objs)

        return list(dict.fromkeys(result))

    @classmethod
    def get_selected_plugs(cls, sections="all"):
        """Return fully qualified ``node.attr`` plugs for the current selection.

        Combines ``get_selected_objects`` and ``get_selected_attrs`` section
        by section so each attribute is paired with the correct node.

        Parameters:
            sections (str|list): Same as ``get_selected_attrs``.

        Returns:
            list[str]: e.g. ``['pCube1.translateX', 'pCube1.translateY']``
        """
        cb = cls._control_name()

        section_pairs = {
            "main": ("mol", "sma"),
            "shape": ("sol", "ssa"),
            "history": ("hol", "sha"),
            "output": ("ool", "soa"),
        }

        if sections == "all":
            keys = list(section_pairs.keys())
        else:
            if isinstance(sections, str):
                sections = [sections]
            keys = [s for s in sections if s in section_pairs]

        plugs = []
        for key in keys:
            obj_flag, attr_flag = section_pairs[key]
            objs = cmds.channelBox(cb, q=True, **{obj_flag: True}) or []
            attrs = cmds.channelBox(cb, q=True, **{attr_flag: True}) or []
            for obj in objs:
                for attr in attrs:
                    plugs.append(f"{obj}.{attr}")

        return list(dict.fromkeys(plugs))

    # ------------------------------------------------------------------
    # Selection (write)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_display_names(attr_names):
        """Build a set of every name variant for *attr_names*.

        The channel-box Qt model shows *nice names* ("Translate X") but
        the ``cmds`` API uses *short names* ("translateX").  We build a
        superset so row-matching works regardless of which convention the
        model uses.

        Returns:
            set[str]
        """
        names = set(attr_names)
        sel_nodes = cmds.ls(sl=True) or []
        if not sel_nodes:
            return names
        node = sel_nodes[-1]
        for attr in attr_names:
            plug = f"{node}.{attr}"
            for flag in ("nice", "long", "short"):
                try:
                    n = cmds.attributeName(plug, **{flag: True})
                    if n:
                        names.add(n)
                except Exception:
                    pass
        return names

    @classmethod
    def select(cls, attr_names):
        """Select attributes in the channel box by short name.

        Uses ``cmds.channelBox -select`` as a **best-effort** mechanism.
        In Maya 2025 this flag is accepted but silently does nothing;
        callers that need a guaranteed highlight should use
        ``select_visual`` instead.

        Parameters:
            attr_names (str | list[str]): Short attribute names.
                Pass an empty list / ``None`` to clear the selection.
        """
        if isinstance(attr_names, str):
            attr_names = [attr_names]

        cb = cls._control_name()
        if not cb:
            return

        attr_list = list(attr_names) if attr_names else []
        try:
            cmds.channelBox(cb, e=True, select=attr_list)
        except Exception:
            log.debug("select: cmds.channelBox -select failed", exc_info=True)
            return

        try:
            cmds.channelBox(cb, e=True, update=True)
        except Exception:
            # Maya 2025 refreshAE.mel bug — selection may still have taken effect.
            log.debug("select: update raised (expected in 2025)", exc_info=True)

    @classmethod
    def select_visual(cls, attr_names):
        """Select attributes and ensure the highlight is visible in the UI.

        Wraps the ``mainChannelBox`` as a ``QTableView`` and sets the
        selection directly on its ``QItemSelectionModel``.  This is the
        **only reliable mechanism** in Maya 2025 — ``cmds.channelBox
        -select`` is accepted but silently does nothing.

        Falls back to ``select()`` for older Maya versions where the
        control may not be a ``QTableView``.

        Parameters:
            attr_names (str | list[str]): Short attribute names.
        """
        if isinstance(attr_names, str):
            attr_names = [attr_names]

        # --- Qt path (primary) ---------------------------------------------
        try:
            view = cls._main_view()
            if view is None:
                raise RuntimeError("no view")

            model = view.model()
            sel_model = view.selectionModel()
            if model is None or sel_model is None:
                raise RuntimeError("no model/selectionModel")

            if not attr_names:
                sel_model.clearSelection()
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
                log.debug("select_visual: Qt clear succeeded")
                return

            display_names = cls._resolve_display_names(attr_names)

            selection = QtCore.QItemSelection()
            rows = model.rowCount()
            cols = max(model.columnCount(), 1)
            for r in range(rows):
                cell_text = model.data(model.index(r, 0), QtCore.Qt.DisplayRole)
                if cell_text and str(cell_text) in display_names:
                    selection.merge(
                        QtCore.QItemSelection(
                            model.index(r, 0), model.index(r, cols - 1)
                        ),
                        QtCore.QItemSelectionModel.Select,
                    )

            if selection.isEmpty():
                raise RuntimeError("no matching rows")

            sel_model.select(
                selection,
                QtCore.QItemSelectionModel.ClearAndSelect
                | QtCore.QItemSelectionModel.Rows,
            )

            # Flush so cmds.channelBox -sma sees the new selection immediately.
            app = QtWidgets.QApplication.instance()
            if app:
                app.processEvents()

            log.debug("select_visual: Qt path succeeded")
            return

        except (RuntimeError, AttributeError, ImportError) as exc:
            log.debug("select_visual: Qt path failed (%s), falling back to cmds", exc)

        # --- cmds fallback (best-effort, broken in 2025) -------------------
        cls.select(attr_names)

    @classmethod
    def clear_selection(cls):
        """Deselect all attributes in the channel box."""
        cls.select_visual([])

    # ------------------------------------------------------------------
    # Full attribute queries
    # ------------------------------------------------------------------

    @classmethod
    def get_all_attrs(cls, node=None, section="main"):
        """Return *all* attribute names shown in a channel box section.

        Unlike ``get_selected_attrs`` which returns only highlighted items,
        this returns every attribute that appears, whether selected or not.

        Parameters:
            node (str|None): Node to query.  If ``None``, uses the first
                selected object.
            section (str): ``'main'``, ``'shape'``, ``'history'``, ``'output'``.
                Default ``'main'``.

        Returns:
            list[str]: Attribute short names in display order.
        """
        if node is None:
            sel = cmds.ls(sl=True)
            if not sel:
                return []
            node = sel[0]

        if section == "main":
            # keyable + channelBox-visible is the display rule
            attrs = cmds.listAttr(node, keyable=True) or []
            cb_attrs = cmds.listAttr(node, channelBox=True) or []
            # Merge preserving order, no dupes
            seen = set()
            result = []
            for a in attrs + cb_attrs:
                if a not in seen:
                    seen.add(a)
                    result.append(a)
            return result
        elif section == "shape":
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            if not shapes:
                return []
            return cmds.listAttr(shapes[0], keyable=True) or []
        elif section == "history":
            hist = cmds.listHistory(node, il=1) or []
            if len(hist) < 2:
                return []
            return cmds.listAttr(hist[1], keyable=True) or []
        elif section == "output":
            future = cmds.listHistory(node, future=True) or []
            if len(future) < 2:
                return []
            return cmds.listAttr(future[1], keyable=True) or []
        return []

    @classmethod
    def get_attr_properties(cls, node=None, attrs=None):
        """Get detailed properties for channel box attributes.

        Using this method is safer than iterating ``getAttr`` manually,
        as it filters out complex types that can crash Maya 2025 when queried
        in tight loops.

        Parameters:
            node (str|None): Node to query. Defaults to first selected.
            attrs (list|None): Attribute names. Defaults to all main attrs.

        Returns:
            list[dict]: Each dict has ``name``, ``value``, ``type``,
            ``locked``, ``keyable``, ``connected``, ``min``, ``max``.
        """
        if node is None:
            sel = cmds.ls(sl=True)
            if not sel:
                return []
            node = sel[0]
        if attrs is None:
            attrs = cls.get_all_attrs(node)

        # Types safe to query value for
        SAFE_VALUE_TYPES = {
            "double",
            "float",
            "int",
            "bool",
            "enum",
            "doubleAngle",
            "doubleLinear",
            "time",
            "byte",
            "short",
            "long",
            "string",  # Usually safe unless huge
        }

        result = []
        for attr in attrs:
            plug = f"{node}.{attr}"
            try:
                # 1. Get Type (Critical first step)
                try:
                    attr_type = cmds.getAttr(plug, type=True)
                except Exception:
                    # If we can't get type (e.g. compound multi), skip it entirely
                    continue

                entry = {
                    "name": attr,
                    "type": attr_type,
                    "locked": cmds.getAttr(plug, lock=True),
                    "keyable": cmds.getAttr(plug, keyable=True),
                    "connected": cmds.connectionInfo(plug, isDestination=True),
                }

                # 2. Get Value (Only if safe)
                if attr_type in SAFE_VALUE_TYPES:
                    try:
                        entry["value"] = cmds.getAttr(plug)
                    except Exception:
                        entry["value"] = None
                else:
                    # For complex types (matrix, mesh, Tdata, etc), value is None
                    # Asking for value can cause crashes/hangs on dense geometry attrs
                    entry["value"] = None

                # 3. Min/Max (Only for numeric)
                # attributeQuery is generally safe but let's be expansive
                is_numeric = attr_type not in ("string", "message", "matrix", "enum")

                entry["min"] = None
                entry["max"] = None

                if is_numeric or attr_type == "enum":
                    try:
                        if cmds.attributeQuery(attr, node=node, minExists=True):
                            entry["min"] = cmds.attributeQuery(
                                attr, node=node, min=True
                            )[0]
                        if cmds.attributeQuery(attr, node=node, maxExists=True):
                            entry["max"] = cmds.attributeQuery(
                                attr, node=node, max=True
                            )[0]
                    except Exception:
                        pass  # Ignore query failures on weird attributes

                result.append(entry)
            except Exception:
                continue
        return result

    # ------------------------------------------------------------------
    # Callbacks / watchers
    # ------------------------------------------------------------------

    _selection_callback_id = None
    _selection_watchers = []

    @classmethod
    def watch_selection(cls, callback):
        """Register a callback that fires when channel box selection changes.

        The callback receives ``(selected_attrs: list[str])``.
        Internally uses a Maya ``SelectionChanged`` scriptJob to poll
        the channel box state.

        Parameters:
            callback (callable): ``callback(attrs: list[str])``.

        Returns:
            int: scriptJob ID (pass to ``unwatch_selection`` to remove).
        """
        cls._selection_watchers.append(callback)

        if cls._selection_callback_id is None:

            def _on_selection_changed():
                attrs = cls.get_selected_attrs()
                for cb in cls._selection_watchers:
                    try:
                        cb(attrs)
                    except Exception as e:
                        print(f"ChannelBox watcher error: {e}")

            cls._selection_callback_id = cmds.scriptJob(
                event=["SelectionChanged", _on_selection_changed],
                protected=True,
            )
        return cls._selection_callback_id

    @classmethod
    def unwatch_selection(cls, callback=None):
        """Remove a selection watcher.

        Parameters:
            callback: The callback to remove. If ``None``, removes all.
        """
        if callback is None:
            cls._selection_watchers.clear()
        else:
            cls._selection_watchers = [
                cb for cb in cls._selection_watchers if cb is not callback
            ]

        if not cls._selection_watchers and cls._selection_callback_id is not None:
            try:
                cmds.scriptJob(kill=cls._selection_callback_id, force=True)
            except Exception:
                pass
            cls._selection_callback_id = None

    # ------------------------------------------------------------------
    # Context menu extraction
    # ------------------------------------------------------------------

    @classmethod
    def get_context_menu_actions(cls):
        """Extract all QAction items from the channel box's context menus.

        Useful for discovering hidden/undocumented menu items.

        Returns:
            list[dict]: ``{'text', 'shortcut', 'enabled', 'checkable',
            'checked', 'objectName', 'menu'}``
        """
        widget = cls._widget()
        if widget is None:
            return []
        return WidgetInspector.dump_actions(widget)

    # ------------------------------------------------------------------
    # Snapshot / diff
    # ------------------------------------------------------------------

    @classmethod
    def snapshot(cls, max_depth=4):
        """Capture the full Qt state of the channel box widget tree.

        Returns:
            dict: Serializable snapshot (see ``WidgetInspector.snapshot``).
        """
        widget = cls._widget()
        if widget is None:
            return {}
        return WidgetInspector.snapshot(widget, max_depth=max_depth)

    @classmethod
    def diff(cls, before, after=None):
        """Compare two channel box snapshots.

        Parameters:
            before (dict): Snapshot taken before an action.
            after (dict|None): Snapshot taken after.  If ``None``,
                takes a fresh snapshot now.

        Returns:
            list[str]: Human-readable diff lines.
        """
        if after is None:
            after = cls.snapshot()
        return WidgetInspector.diff_snapshots(before, after)

    # ------------------------------------------------------------------
    # MEL internals exploration
    # ------------------------------------------------------------------

    @classmethod
    def list_mel_procs(cls, pattern="channel[Bb]ox"):
        """Find MEL procedures related to the channel box.

        Parameters:
            pattern (str): Regex to match proc names.

        Returns:
            list[dict]: See ``DevTools.grep_mel_procs``.
        """
        return DevTools.grep_mel_procs(pattern=pattern)

    @classmethod
    def read_mel_proc(cls, proc_name):
        """Read the full source of a channel-box-related MEL procedure.

        Parameters:
            proc_name (str): Exact MEL procedure name.

        Returns:
            str|None
        """
        return DevTools.read_mel_proc(proc_name)

    # ------------------------------------------------------------------
    # Inspection / debugging
    # ------------------------------------------------------------------

    @classmethod
    def dump_tree(cls, max_depth=3):
        """Print the Qt widget tree inside the channel box.

        Parameters:
            max_depth (int): Maximum depth.

        Returns:
            list[str]: Lines printed.
        """
        widget = cls._widget()
        if widget is None:
            print("Channel box widget not found.")
            return []
        return WidgetInspector.dump_tree(widget, max_depth=max_depth)

    @classmethod
    def dump_model(cls, max_rows=50):
        """Print the item-model contents of the main channel box view.

        Parameters:
            max_rows (int): Maximum rows.

        Returns:
            list[dict]
        """
        view = cls._main_view()
        if view is None:
            print("Channel box view not found.")
            return []
        return WidgetInspector.dump_model(view, max_rows=max_rows)

    @classmethod
    def list_signals(cls):
        """List signals on the channel box widget."""
        widget = cls._widget()
        if widget is None:
            print("Channel box widget not found.")
            return []
        return WidgetInspector.list_signals(widget)

    @classmethod
    def list_item_views(cls):
        """List all QAbstractItemView children (main, shape, history, output).

        Returns:
            list[QAbstractItemView]
        """
        widget = cls._widget()
        if widget is None:
            return []
        return WidgetInspector.find_item_views(widget)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...
