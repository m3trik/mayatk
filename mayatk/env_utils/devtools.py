# !/usr/bin/python
# coding=utf-8
import os
import re
import sys
import inspect
import importlib
import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui

from qtpy import QtWidgets, QtCore

try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken2 import wrapInstance

from pythontk.core_utils._core_utils import CoreUtils


class DevTools(CoreUtils):
    """Tools for inspecting Maya's environment and debugging."""

    # ------------------------------------------------------------------
    # Echo / tracing
    # ------------------------------------------------------------------

    @staticmethod
    def echo_all(state=True):
        """Toggle the 'Echo All Commands' state in the Script Editor.

        Parameters:
            state (bool): True to enable, False to disable.
        """
        cmds.commandEcho(state=state)
        print(f"Echo All Commands: {'ON' if state else 'OFF'}")

    # ------------------------------------------------------------------
    # File / script locators
    # ------------------------------------------------------------------

    @staticmethod
    def find_mel(name):
        """Find the file path of a MEL procedure or script.

        Parameters:
            name (str): The name of the MEL procedure or script.

        Returns:
            (str|None): The absolute path to the file if found, else None.
        """
        result = mel.eval('whatIs "{}"'.format(name))

        if result.startswith("Mel procedure found in:") or result.startswith(
            "Script found in:"
        ):
            path = result.split("found in: ")[-1].strip()
            return os.path.normpath(path)
        return None

    @staticmethod
    def find_python(name):
        """Find the file path of a Python module or object.

        Parameters:
            name (str): The name of the module or object (e.g. 'os', 'pymel.core.selected').

        Returns:
            (str|None): The absolute path to the file if found, else None.
        """
        # Try import first
        try:
            module = importlib.import_module(name)
            if hasattr(module, "__file__"):
                return os.path.normpath(module.__file__)
        except ImportError:
            pass

        # Check sys.modules
        if name in sys.modules:
            module = sys.modules[name]
            if hasattr(module, "__file__"):
                return os.path.normpath(module.__file__)

        # Check if it's an object in a module
        try:
            parts = name.split(".")
            if len(parts) > 1:
                module_name = ".".join(parts[:-1])
                obj_name = parts[-1]
                module = importlib.import_module(module_name)
                obj = getattr(module, obj_name)
                return os.path.normpath(inspect.getfile(obj))
        except (ImportError, AttributeError, TypeError, ValueError):
            pass

        return None

    @classmethod
    def find(cls, name):
        """Find the file path of a MEL or Python object.

        Parameters:
            name (str): The name of the object to find.

        Returns:
            (str|None): The path if found, matches MEL first then Python.
        """
        # Check MEL first
        path = cls.find_mel(name)
        if path:
            return path

        # Check Python
        path = cls.find_python(name)
        if path:
            return path

        return None

    # ------------------------------------------------------------------
    # Grep / search
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_files(root, extensions, recursive=True):
        """Walk *root* and yield file paths matching *extensions*.

        Uses ``os.walk`` directly — no dependency on ``pythontk``.

        Parameters:
            root (str): Directory to scan.
            extensions (list[str]): File extensions to include,
                e.g. ``['.mel']``.  An empty list or ``['*']`` means all.
            recursive (bool): Walk subdirectories.
        """
        import fnmatch

        match_all = not extensions or "*" in extensions
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in filenames:
                if match_all or any(
                    fnmatch.fnmatch(fn, f"*{ext}") for ext in extensions
                ):
                    yield os.path.join(dirpath, fn)
            if not recursive:
                break

    @staticmethod
    def grep_maya_dir(
        query,
        root_paths=None,
        ext=".mel",
        recursive=True,
        regex=False,
        context=0,
        max_results=500,
    ):
        """Search for a string or regex in files within Maya's script paths.

        Parameters:
            query (str): The string or regex pattern to search for.
            root_paths (str|list, optional): Directories to search.
                Defaults to ``MAYA_SCRIPT_PATH``.
            ext (str|list, optional): File extension filter(s).
                ``'.mel'``, ``['.mel', '.py']``, or ``'*'`` for all.
            recursive (bool): Search recursively. Default ``True``.
            regex (bool): Treat *query* as a Python regex. Default ``False``.
            context (int): Number of surrounding lines to include.
                ``0`` = matching line only (default).
            max_results (int): Stop after this many matches. Default 500.

        Returns:
            list[dict]: Each dict has keys ``path``, ``line``, ``text``,
            and optionally ``before`` / ``after`` (lists of context lines).
        """
        if root_paths is None:
            sep = ";" if os.name == "nt" else ":"
            root_paths = os.environ.get("MAYA_SCRIPT_PATH", "").split(sep)
            # Also include the Maya install scripts directory
            maya_location = os.environ.get("MAYA_LOCATION", "")
            if maya_location:
                for subdir in ("scripts", "Python/Lib"):
                    p = os.path.join(maya_location, subdir)
                    if os.path.isdir(p) and p not in root_paths:
                        root_paths.append(p)
        elif isinstance(root_paths, str):
            root_paths = [root_paths]

        # Normalise ext to a list
        if isinstance(ext, str):
            ext = [ext]

        pattern = re.compile(query, re.IGNORECASE) if regex else None
        results = []

        # Normalise ext list to bare extensions (e.g. '.mel')
        ext_list = []
        for e in ext:
            e = e.lstrip("*")
            if e and e != ".*":
                ext_list.append(e)

        valid_roots = [p for p in root_paths if os.path.isdir(p)]
        for root in valid_roots:
            for file_path in DevTools._collect_files(root, ext_list, recursive):
                try:
                    with open(file_path, "r", errors="ignore") as f:
                        all_lines = f.readlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(all_lines):
                    match = pattern.search(line) if regex else (query in line)
                    if match:
                        entry = {
                            "path": file_path,
                            "line": i + 1,
                            "text": line.rstrip(),
                        }
                        if context > 0:
                            start = max(0, i - context)
                            end = min(len(all_lines), i + context + 1)
                            entry["before"] = [l.rstrip() for l in all_lines[start:i]]
                            entry["after"] = [
                                l.rstrip() for l in all_lines[i + 1 : end]
                            ]
                        results.append(entry)
                        if len(results) >= max_results:
                            return results
        return results

    @staticmethod
    def grep_mel_procs(
        pattern="",
        root_paths=None,
        recursive=True,
        include_args=True,
    ):
        """Scan MEL files for ``proc`` declarations matching a pattern.

        Useful for discovering internal/undocumented MEL procedures.

        Parameters:
            pattern (str): Regex pattern to match against procedure names.
                Empty string matches all procs.
            root_paths (str|list, optional): Directories to scan.
                Defaults to ``MAYA_SCRIPT_PATH`` + Maya install scripts.
            recursive (bool): Search recursively. Default ``True``.
            include_args (bool): Include argument signatures. Default ``True``.

        Returns:
            list[dict]: Each dict has ``name``, ``path``, ``line``,
            ``return_type``, ``scope`` (``global`` | ``local``),
            and ``signature`` (if *include_args*).
        """
        if root_paths is None:
            sep = ";" if os.name == "nt" else ":"
            root_paths = os.environ.get("MAYA_SCRIPT_PATH", "").split(sep)
            maya_location = os.environ.get("MAYA_LOCATION", "")
            if maya_location:
                for subdir in ("scripts",):
                    p = os.path.join(maya_location, subdir)
                    if os.path.isdir(p) and p not in root_paths:
                        root_paths.append(p)
        elif isinstance(root_paths, str):
            root_paths = [root_paths]

        # Regex that captures:  [global] proc [return_type] name(args...)
        proc_re = re.compile(
            r"^\s*(global\s+)?proc\s+"
            r"(?:(\w+(?:\[\])?)\s+)?"  # optional return type
            r"(\w+)"  # procedure name
            r"\s*\(([^)]*)\)",  # args
        )
        name_filter = re.compile(pattern, re.IGNORECASE) if pattern else None
        results = []

        valid_roots = [p for p in root_paths if os.path.isdir(p)]
        for root in valid_roots:
            for file_path in DevTools._collect_files(root, [".mel"], recursive):
                try:
                    with open(file_path, "r", errors="ignore") as f:
                        for i, line in enumerate(f):
                            m = proc_re.match(line)
                            if not m:
                                continue
                            scope = "global" if m.group(1) else "local"
                            ret_type = m.group(2) or "void"
                            name = m.group(3)
                            args = m.group(4).strip()
                            if name_filter and not name_filter.search(name):
                                continue
                            entry = {
                                "name": name,
                                "path": file_path,
                                "line": i + 1,
                                "return_type": ret_type,
                                "scope": scope,
                            }
                            if include_args:
                                entry["signature"] = f"{name}({args})"
                            results.append(entry)
                except (OSError, UnicodeDecodeError):
                    continue
        return results

    @staticmethod
    def read_mel_proc(proc_name):
        """Extract the full source text of a named MEL procedure.

        Uses ``whatIs`` to find the file, then parses the proc body
        (including nested braces) from disk.

        Parameters:
            proc_name (str): Exact MEL procedure name.

        Returns:
            str|None: Full source text, or ``None`` if not found.
        """
        result = mel.eval(f'whatIs "{proc_name}"')
        if "found in:" not in result:
            return None

        path = result.split("found in:")[-1].strip()
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            return None

        proc_re = re.compile(
            rf"^\s*(?:global\s+)?proc\s+(?:\w+(?:\[\])?\s+)?{re.escape(proc_name)}\s*\("
        )
        try:
            with open(path, "r", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            return None

        start_line = None
        for i, line in enumerate(lines):
            if proc_re.match(line):
                start_line = i
                break
        if start_line is None:
            return None

        # Walk forward counting braces to find closing }
        brace_depth = 0
        body_lines = []
        for line in lines[start_line:]:
            body_lines.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0 and "{" in "".join(body_lines):
                break

        return "".join(body_lines)

    @classmethod
    def find_all(cls, name):
        """Return *all* locations where *name* is defined (MEL + Python).

        Unlike :meth:`find` (which returns the first hit), this collects
        every ``whatIs`` result and every importable Python path.

        Parameters:
            name (str): MEL procedure, Python module, or qualified name.

        Returns:
            list[dict]: ``{'type': 'mel'|'python', 'path': str}``.
        """
        hits = []

        # MEL
        try:
            result = mel.eval(f'whatIs "{name}"')
            if "found in:" in result:
                path = result.split("found in:")[-1].strip()
                hits.append({"type": "mel", "path": os.path.normpath(path)})
        except RuntimeError:
            pass

        # Python module
        try:
            module = importlib.import_module(name)
            if hasattr(module, "__file__") and module.__file__:
                hits.append(
                    {"type": "python", "path": os.path.normpath(module.__file__)}
                )
        except ImportError:
            pass

        # Python qualified name  (e.g. pymel.core.selected)
        parts = name.rsplit(".", 1)
        if len(parts) == 2:
            try:
                mod = importlib.import_module(parts[0])
                obj = getattr(mod, parts[1], None)
                if obj is not None:
                    f = inspect.getfile(obj)
                    p = os.path.normpath(f)
                    if not any(h["path"] == p for h in hits):
                        hits.append({"type": "python", "path": p})
            except (ImportError, TypeError, OSError):
                pass

        return hits

    @staticmethod
    def list_mel_globals(pattern=""):
        """List global MEL variables whose names match a pattern.

        Queries Maya's ``env`` command and filters by regex.

        Parameters:
            pattern (str): Regex to match variable names (case-insensitive).
                Empty string returns all.

        Returns:
            list[str]: Variable names (with ``$`` prefix).
        """
        raw = mel.eval("env") or []
        # mel.eval("env") returns a list in Maya Python, not a string
        if isinstance(raw, str):
            names = [v.strip() for v in raw.split("\n") if v.strip()]
        else:
            names = [v.strip() for v in raw if isinstance(v, str) and v.strip()]
        if pattern:
            filt = re.compile(pattern, re.IGNORECASE)
            names = [n for n in names if filt.search(n)]
        return names

    # ------------------------------------------------------------------
    # MEL globals / sourcing
    # ------------------------------------------------------------------

    @staticmethod
    def get_mel_global(var_name, type_hint="string"):
        """Get the value of a global MEL variable.

        Parameters:
            var_name (str): The name of the variable (without $).
            type_hint (str): The type of the variable (string, int, float, string[], etc).

        Returns:
            The value of the variable.
        """
        cmd = "global {} ${}; $__tmp = ${};".format(type_hint, var_name, var_name)
        try:
            return mel.eval(cmd)
        except RuntimeError as e:
            print(f"Error retrieving global var ${var_name}: {e}")
            return None

    @staticmethod
    def source_mel(path):
        """Source a MEL script.

        Parameters:
            path (str): The path to the MEL script.
        """
        path = path.replace("\\", "/")
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        mel.eval(f'source "{path}"')


class WidgetInspector(CoreUtils):
    """Deep PyQt/PySide inspection tools for reverse-engineering Maya widgets.

    Provides utilities to resolve Maya control names to ``QWidget`` instances,
    walk widget hierarchies, list signals/slots, dump properties, and identify
    internal item-view models — useful for hooking into undocumented UI
    like the Channel Box, Attribute Editor, Outliner, etc.
    """

    # ------------------------------------------------------------------
    # Widget resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap(ptr, base_type=QtWidgets.QWidget):
        """Wrap a raw Maya pointer as a QWidget.

        Parameters:
            ptr: Integer pointer from ``MQtUtil``.
            base_type: Qt class to wrap as.

        Returns:
            QWidget or None.
        """
        if ptr is None:
            return None
        return wrapInstance(int(ptr), base_type)

    @classmethod
    def from_maya_control(cls, control_name):
        """Resolve a Maya control name to a QWidget.

        Parameters:
            control_name (str): Maya UI control name (e.g. the result of
                ``$gChannelBoxName``).

        Returns:
            QWidget|None
        """
        ptr = omui.MQtUtil.findControl(control_name)
        if not ptr:
            ptr = omui.MQtUtil.findLayout(control_name)
        if not ptr:
            ptr = omui.MQtUtil.findMenuItem(control_name)
        return cls._wrap(ptr)

    @classmethod
    def from_mel_global(cls, var_name):
        """Resolve a MEL global variable that holds a control name to a QWidget.

        Parameters:
            var_name (str): Global MEL variable name *without* the dollar sign
                (e.g. ``gChannelBoxName``).

        Returns:
            QWidget|None
        """
        control_name = DevTools.get_mel_global(var_name)
        if not control_name:
            return None
        return cls.from_maya_control(control_name)

    @staticmethod
    def main_window():
        """Return Maya's main window as a QWidget."""
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QMainWindow)
        return None

    # ------------------------------------------------------------------
    # Hierarchy traversal
    # ------------------------------------------------------------------

    @classmethod
    def walk(cls, widget, depth=0, max_depth=-1):
        """Recursively yield ``(depth, widget)`` for all descendants.

        Parameters:
            widget (QWidget): Root widget.
            depth (int): Current depth (used internally).
            max_depth (int): Maximum depth to traverse (-1 = unlimited).

        Yields:
            tuple[int, QWidget]
        """
        yield depth, widget
        if max_depth != -1 and depth >= max_depth:
            return
        for child in widget.children():
            if isinstance(child, QtWidgets.QWidget):
                yield from cls.walk(child, depth + 1, max_depth)

    @classmethod
    def find_children_by_type(cls, widget, type_name):
        """Find all descendants matching a Qt class name string.

        Parameters:
            widget (QWidget): Root widget to search from.
            type_name (str): Qt class name, e.g. ``'QTreeView'``, ``'QLineEdit'``.

        Returns:
            list[QWidget]
        """
        return [w for _, w in cls.walk(widget) if type(w).__name__ == type_name]

    @classmethod
    def find_child_by_name(cls, widget, object_name):
        """Find first descendant whose ``objectName`` matches.

        Parameters:
            widget (QWidget): Root widget.
            object_name (str): The Qt objectName to match.

        Returns:
            QWidget|None
        """
        for _, w in cls.walk(widget):
            if w.objectName() == object_name:
                return w
        return None

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def dump_tree(widget, max_depth=3):
        """Print an indented widget tree for debugging.

        Parameters:
            widget (QWidget): Root widget.
            max_depth (int): Depth to print. Default 3.

        Returns:
            list[str]: The lines printed (useful for further processing).
        """
        lines = []
        for depth, w in WidgetInspector.walk(widget, max_depth=max_depth):
            indent = "  " * depth
            cls_name = type(w).__name__
            obj_name = w.objectName() or "(no name)"
            visible = "V" if w.isVisible() else "H"
            size = f"{w.width()}x{w.height()}"
            line = f"{indent}{cls_name}  [{obj_name}]  {visible}  {size}"
            lines.append(line)
            print(line)
        return lines

    @staticmethod
    def dump_properties(widget):
        """Print all Qt dynamic properties on a widget.

        Parameters:
            widget (QWidget): The widget to inspect.

        Returns:
            dict: Property name -> value mapping.
        """
        meta = widget.metaObject()
        props = {}
        for i in range(meta.propertyCount()):
            prop = meta.property(i)
            name = prop.name()
            try:
                value = widget.property(name)
            except Exception:
                value = "<error>"
            props[name] = value
        for name, value in props.items():
            print(f"  {name}: {value}")
        return props

    @staticmethod
    def list_signals(widget):
        """List all signals defined on a widget's class.

        Parameters:
            widget (QWidget): The widget to inspect.

        Returns:
            list[str]: Signal signatures.
        """
        meta = widget.metaObject()
        signals = []
        for i in range(meta.methodCount()):
            method = meta.method(i)
            if method.methodType() == QtCore.QMetaMethod.Signal:
                sig = method.methodSignature()
                if isinstance(sig, bytes):
                    sig = sig.decode()
                signals.append(sig)
        for s in signals:
            print(f"  signal: {s}")
        return signals

    @staticmethod
    def list_slots(widget):
        """List all slots defined on a widget's class.

        Parameters:
            widget (QWidget): The widget to inspect.

        Returns:
            list[str]: Slot signatures.
        """
        meta = widget.metaObject()
        slots = []
        for i in range(meta.methodCount()):
            method = meta.method(i)
            if method.methodType() == QtCore.QMetaMethod.Slot:
                sig = method.methodSignature()
                if isinstance(sig, bytes):
                    sig = sig.decode()
                slots.append(sig)
        for s in slots:
            print(f"  slot: {s}")
        return slots

    # ------------------------------------------------------------------
    # Advanced introspection
    # ------------------------------------------------------------------

    @classmethod
    def find_by_property(cls, widget, prop_name, value=None, max_depth=-1):
        """Find descendants that have a Qt property matching criteria.

        Parameters:
            widget (QWidget): Root widget.
            prop_name (str): Property name to check.
            value: If not ``None``, must match the property value.
            max_depth (int): Maximum traversal depth (``-1`` = unlimited).

        Returns:
            list[QWidget]
        """
        hits = []
        for _, w in cls.walk(widget, max_depth=max_depth):
            v = w.property(prop_name)
            if v is None:
                continue
            if value is None or v == value:
                hits.append(w)
        return hits

    @classmethod
    def snapshot(cls, widget, max_depth=4):
        """Capture the full state of a widget subtree as a serializable dict.

        The snapshot includes class name, object name, visibility, geometry,
        Qt properties, children, and item-model contents (if present).
        Useful for diffing state before/after an action.

        Parameters:
            widget (QWidget): Root widget.
            max_depth (int): Maximum traversal depth.

        Returns:
            dict: Nested dict tree.
        """

        def _snap(w, depth):
            meta = w.metaObject()
            props = {}
            for i in range(meta.propertyCount()):
                prop = meta.property(i)
                name = prop.name()
                try:
                    val = w.property(name)
                    # Only keep JSON-serialisable types
                    if isinstance(val, (str, int, float, bool, type(None))):
                        props[name] = val
                except Exception:
                    pass

            node = {
                "class": type(w).__name__,
                "objectName": w.objectName() or "",
                "visible": w.isVisible(),
                "geometry": [w.x(), w.y(), w.width(), w.height()],
                "properties": props,
            }

            # Item model snapshot
            if isinstance(w, QtWidgets.QAbstractItemView):
                model = w.model()
                if model:
                    rows = []
                    for r in range(min(model.rowCount(), 20)):
                        cols = []
                        for c in range(model.columnCount()):
                            cols.append(str(model.data(model.index(r, c)) or ""))
                        rows.append(cols)
                    node["model_rows"] = rows

            # Recurse children
            if depth < max_depth:
                children = []
                for child in w.children():
                    if isinstance(child, QtWidgets.QWidget):
                        children.append(_snap(child, depth + 1))
                if children:
                    node["children"] = children

            return node

        return _snap(widget, 0)

    @staticmethod
    def diff_snapshots(before, after, path=""):
        """Compare two snapshots and return a list of differences.

        Parameters:
            before (dict): Snapshot taken before an action.
            after (dict): Snapshot taken after.
            path (str): Internal — current path for reporting.

        Returns:
            list[str]: Human-readable diff lines.
        """
        diffs = []
        prefix = path or before.get("objectName") or before.get("class", "?")

        # Compare scalar fields
        for key in ("class", "objectName", "visible"):
            bv, av = before.get(key), after.get(key)
            if bv != av:
                diffs.append(f"{prefix}.{key}: {bv!r} -> {av!r}")

        # Compare geometry
        bg, ag = before.get("geometry", []), after.get("geometry", [])
        if bg != ag:
            diffs.append(f"{prefix}.geometry: {bg} -> {ag}")

        # Compare properties
        bp = before.get("properties", {})
        ap = after.get("properties", {})
        all_keys = set(bp) | set(ap)
        for k in sorted(all_keys):
            bv, av = bp.get(k), ap.get(k)
            if bv != av:
                diffs.append(f"{prefix}.properties.{k}: {bv!r} -> {av!r}")

        # Compare model rows
        bm = before.get("model_rows", [])
        am = after.get("model_rows", [])
        if bm != am:
            diffs.append(f"{prefix}.model_rows changed ({len(bm)} -> {len(am)} rows)")

        # Recurse children
        bc = before.get("children", [])
        ac = after.get("children", [])
        for i in range(max(len(bc), len(ac))):
            bchild = bc[i] if i < len(bc) else None
            achild = ac[i] if i < len(ac) else None
            child_path = f"{prefix}[{i}]"
            if bchild is None:
                diffs.append(f"{child_path}: ADDED {achild.get('class', '?')}")
            elif achild is None:
                diffs.append(f"{child_path}: REMOVED {bchild.get('class', '?')}")
            else:
                diffs.extend(WidgetInspector.diff_snapshots(bchild, achild, child_path))

        return diffs

    @classmethod
    def connect_signal_logger(cls, widget, signal_name=None, callback=None):
        """Connect a logger to signals on *widget* so you can trace when they fire.

        Parameters:
            widget (QWidget): Target widget.
            signal_name (str|None): Specific signal to watch (e.g.
                ``'clicked(QModelIndex)'``).  If ``None``, logs **all**
                signals.
            callback (callable|None): ``callback(widget, signal_name, *args)``.
                Defaults to ``print``.

        Returns:
            list[tuple]: ``(signal_signature, slot)`` pairs that were connected.
        """
        if callback is None:

            def callback(w, sig, *args):
                print(
                    f"[signal] {type(w).__name__}({w.objectName()!r}).{sig}  args={args}"
                )

        meta = widget.metaObject()
        connected = []
        for i in range(meta.methodCount()):
            method = meta.method(i)
            if method.methodType() != QtCore.QMetaMethod.Signal:
                continue
            sig = method.methodSignature()
            if isinstance(sig, bytes):
                sig = sig.decode()
            if signal_name and sig != signal_name:
                continue
            # Build a slot that captures the signal name
            _sig = sig

            def _make_slot(s):
                def _slot(*args):
                    callback(widget, s, *args)

                return _slot

            slot = _make_slot(_sig)
            try:
                signal_obj = getattr(widget, sig.split("(")[0], None)
                if signal_obj is not None:
                    signal_obj.connect(slot)
                    connected.append((sig, slot))
            except (AttributeError, RuntimeError):
                pass

        return connected

    @classmethod
    def dump_actions(cls, widget):
        """List all QAction items attached to a widget (menus, context menus).

        Parameters:
            widget (QWidget): Widget to inspect.

        Returns:
            list[dict]: ``{'text', 'shortcut', 'enabled', 'checkable', 'checked', 'objectName'}``
        """
        results = []
        # BLOCKED: Causes hard crash in Maya 2025
        return results

        try:
            actions = widget.actions()
        except RuntimeError:
            actions = []

        for action in actions:
            try:
                results.append(
                    {
                        "text": action.text(),
                        "shortcut": (
                            action.shortcut().toString() if action.shortcut() else ""
                        ),
                        "enabled": action.isEnabled(),
                        "checkable": action.isCheckable(),
                        "checked": action.isChecked(),
                        "objectName": action.objectName(),
                    }
                )
            except RuntimeError:
                pass

        # Also search QMenu children
        # BLOCKED: Causes hard crash in Maya 2025
        # for menu in widget.findChildren(QtWidgets.QMenu):
        #     try:
        #         menu_name = menu.objectName() or type(menu).__name__
        #         for action in menu.actions():
        #             results.append(
        #                 {
        #                     "text": action.text(),
        #                     "shortcut": (
        #                         action.shortcut().toString()
        #                         if action.shortcut()
        #                         else ""
        #                     ),
        #                     "enabled": action.isEnabled(),
        #                     "checkable": action.isCheckable(),
        #                     "checked": action.isChecked(),
        #                     "objectName": action.objectName(),
        #                     "menu": menu_name,
        #                 }
        #             )
        #     except RuntimeError:
        #         continue

        return results

    # ------------------------------------------------------------------
    # Item-view model inspection
    # ------------------------------------------------------------------

    @classmethod
    def find_item_views(cls, widget):
        """Find all QAbstractItemView descendants (QTreeView, QListView, etc.).

        Parameters:
            widget (QWidget): Root widget.

        Returns:
            list[QAbstractItemView]
        """
        return [
            w for _, w in cls.walk(widget) if isinstance(w, QtWidgets.QAbstractItemView)
        ]

    @staticmethod
    def dump_model(view, max_rows=50):
        """Print the contents of the model attached to a QAbstractItemView.

        Parameters:
            view (QAbstractItemView): The item view to inspect.
            max_rows (int): Limit rows printed to prevent flooding.

        Returns:
            list[dict]: Row data as ``{'row': int, 'columns': list[str]}``.
        """
        model = view.model()
        if model is None:
            print("  (no model)")
            return []
        rows = []
        for row in range(min(model.rowCount(), max_rows)):
            cols = []
            for col in range(model.columnCount()):
                idx = model.index(row, col)
                cols.append(str(model.data(idx) or ""))
            entry = {"row": row, "columns": cols}
            rows.append(entry)
            print(f"  row {row}: {cols}")
        if model.rowCount() > max_rows:
            print(f"  ... ({model.rowCount() - max_rows} more rows)")
        return rows

    @staticmethod
    def get_selection_model(view):
        """Return the QItemSelectionModel for a view.

        Parameters:
            view (QAbstractItemView): The item view.

        Returns:
            QItemSelectionModel|None
        """
        return view.selectionModel()


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...
