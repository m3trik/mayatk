# !/usr/bin/python
# coding=utf-8
import os
import inspect
from typing import Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.ui_utils._ui_utils import UiUtils
from mayatk.node_utils.attributes._attributes import Attributes


class MacroManager(ptk.HelpMixin):
    """Assign macro functions to hotkeys.

    Example:
        class Macros(MacroManager):
            '''A class that inherits from `MacroManager` and holds the actual macro functions.
            '''
            @staticmethod
            def m_back_face_culling():
                    '''Toggle Back-Face Culling.
                    '''
                    sel = cmds.ls(selection=True)
                    if sel:
                            currentPanel = getPanel(withFocus=True)
                            state = cmds.polyOptions(sel, query=True, wireBackCulling=True)[0]

                            if not state:
                                    cmds.polyOptions(sel, gl=True, wireBackCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 0)
                                    cmds.inViewMessage(status_message="Back-Face Culling is now <hl>OFF</hl>.>", pos='topCenter', fade=True)
                            else:
                                    cmds.polyOptions(sel, gl=True, backCulling=True)
                                    Macros.setWireframeOnShadedOption(currentPanel, 1)
                                    cmds.inViewMessage(status_message="Back-Face Culling is now <hl>ON</hl>.", pos='topCenter', fade=True)
                    else:
                            print(" Warning: Nothing selected. ")

        #call the `set_macros` function to set a macro for functions you defined in `Macros`.
        mtk.Macros.set_macros(
            "m_back_face_culling,     key=1, cat=Display",
        )
    """

    @classmethod
    def set_macros(cls, *args):
        """Extends `set_macro` to accept a list of strings representing positional and keyword arguments.

        Parameters:
            *args (str): A variable number of strings, each containing the arguments for a single macro. Each string
                    should be in the format "<macro name>, <positional arg1>, <positional arg2>, ..., <keyword arg1>=<value1>,
                    <keyword arg2>=<value2>, ..."
        Example:
            set_macros('m_back_face_culling, key=1, cat=Display', 'm_smooth_preview, key=2, cat=Display') #Calls `set_macro` with the parsed arguments for each macro in `args`.
        """
        for string in args:
            cls.call_with_input(cls.set_macro, string)

    @staticmethod
    def call_with_input(func, input_string):
        """Parses an input string into positional and keyword arguments, and
        calls the given function with those arguments.

        Parameters:
            func (callable): The function to call.
            input_string (str): The input string containing the arguments.

        Returns:
            The result of calling `func` with the parsed arguments.
        """
        args, kwargs = [], {}
        for i in input_string.split(","):
            try:
                key, value = i.split("=")
                kwargs[key.strip()] = value.strip()
            except ValueError:
                args.append(i.strip())

        return func(*args, **kwargs)

    @classmethod
    def set_macro(
        cls, name, key=None, cat=None, ann=None, default=False, delete_existing=True
    ):
        """Sets a default runtime command with a keyboard shortcut.

        Parameters:
            name (str): The command name you provide must be unique. (alphanumeric characters, or underscores)
            cat (str): catagory - Category for the command.
            ann (str): annotation - Description of the command.
            key (str): keyShortcut - Specify what key is being set.
                                    key modifier values are set by adding a '+' between chars. ie. 'sht+z'.
                                    modifiers:
                                            alt, ctl, sht
                                    additional valid keywords are:
                                            Up, Down, Right, Left,
                                            Home, End, Page_Up, Page_Down, Insert
                                            Return, Space
                                            F1 to F12
                                            Tab (Will only work when modifiers are specified)
                                            Delete, Backspace (Will only work when modifiers are specified)
            default (bool): Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.
            delete_existing = Delete any existing (non-default) runtime commands of the given name.
        """
        command = f"if 'm_slots' not in globals(): from {cls.__module__} import {cls.__name__}; global m_slots; m_slots = {cls.__name__}();\nm_slots.{name}();"

        if not ann:  # if no ann is given, try using the method's docstring.
            method = getattr(cls, name)
            doc = method.__doc__ or ""
            ann = doc.split("\n")[0].strip()  # use only the first line.

        if cmds.runTimeCommand(name, exists=True):
            if cmds.runTimeCommand(name, query=True, default=True):
                return  # can not delete default runtime commands.
            elif (
                delete_existing
            ):  # delete any existing (non-default) runtime commands of that name.
                cmds.runTimeCommand(name, edit=True, delete=True)

        try:  # set runTimeCommand
            cmds.runTimeCommand(
                name,
                annotation=ann,
                category=cat,
                command=command,
                default=default,
            )
        except RuntimeError as error:
            print("# Error: {}: {} #".format(__file__, error))
            return error

        # set command
        nameCommand = cmds.nameCommand(
            "{0}Command".format(name),
            annotation=ann,
            command=name,
        )

        # set hotkey
        ctl, alt, sht, key = cls._parse_key(key)
        cmds.hotkey(
            keyShortcut=key, name=nameCommand, ctl=ctl, alt=alt, sht=sht
        )  # set only the key press.

    # ------------------------------------------------------------------
    # Management API (UI-agnostic — fully functional without the UI)
    # ------------------------------------------------------------------
    # The methods below are the single source of truth for discovering,
    # querying, applying, clearing, and persisting macro bindings. Both the
    # ``MacroManager`` UI panel and the ``userSetup`` startup path are thin
    # consumers; nothing here imports Qt or the switchboard. A *binding* is a
    # ``{"key": <maya-token>, "cat": <category>}`` dict; a *binding set* maps
    # macro name -> binding and is exactly what a preset file stores.

    MACRO_PREFIX = "m_"
    PRESET_NAME = "macro_manager"
    PRESET_PACKAGE = "mayatk"
    DEFAULT_PRESET = "default"

    # Source tokens that should keep a fixed casing in the humanized label
    # (e.g. ``m_toggle_UV_select_type`` -> "Toggle UV Select Type"). Tokens
    # already upper-case in the method name (``UV``) are preserved as-is.
    _LABEL_ACRONYMS = {"uv": "UV", "uvs": "UVs", "id": "ID", "ui": "UI", "3d": "3D"}

    # Modifier-order is canonical (ctl, alt, sht) so equivalent chords compare
    # equal regardless of how they were typed.
    _MOD_ORDER = ("ctl", "alt", "sht")
    # Qt KeySequence modifier name -> Maya token.
    _QT_MOD_MAP = {
        "ctrl": "ctl",
        "control": "ctl",
        "alt": "alt",
        "shift": "sht",
        "meta": "ctl",
        "cmd": "ctl",
    }

    @classmethod
    def list_available_macros(cls) -> Dict[str, str]:
        """Discover every ``m_*`` macro callable, mapped to its annotation.

        Returns:
            ``{macro_name: first_docstring_line}`` sorted by name. DCC-free —
            pure class introspection, so the UI can populate its table without
            a live Maya.
        """
        macros: Dict[str, str] = {}
        for name in dir(cls):
            if not name.startswith(cls.MACRO_PREFIX):
                continue
            try:
                attr = inspect.getattr_static(cls, name)
            except AttributeError:
                continue
            func = (
                attr.__func__
                if isinstance(attr, (staticmethod, classmethod))
                else attr
            )
            if not callable(func):
                continue
            doc = (getattr(func, "__doc__", "") or "").strip()
            macros[name] = doc.split("\n")[0].strip() if doc else ""
        return dict(sorted(macros.items()))

    @classmethod
    def macro_label(cls, name: str) -> str:
        """Humanize a macro name for display, e.g. ``m_back_face_culling`` ->
        "Back Face Culling" (acronyms like ``UV`` / ``ID`` are preserved)."""
        base = name[len(cls.MACRO_PREFIX):] if name.startswith(cls.MACRO_PREFIX) else name
        words = []
        for word in base.split("_"):
            if not word:
                continue
            low = word.lower()
            if low in cls._LABEL_ACRONYMS:
                words.append(cls._LABEL_ACRONYMS[low])
            elif word.isupper() and len(word) > 1:
                words.append(word)  # already an acronym in the source name
            else:
                words.append(word.capitalize())
        return " ".join(words)

    @classmethod
    def macro_category(cls, name: str) -> str:
        """Default category for a macro, derived from the ``*Macros`` mixin that
        defines it (e.g. a method on :class:`DisplayMacros` -> ``"Display"``).

        The defining mixin is the single source of truth for a macro's category,
        so every discoverable macro has a sensible default with no per-macro
        annotation to maintain. Returns ``""`` when *name* isn't defined on a
        ``*Macros`` mixin.
        """
        for klass in cls.__mro__:
            kname = klass.__name__
            if kname.endswith("Macros") and kname != "Macros" and name in vars(klass):
                stem = kname[: -len("Macros")]
                return cls._LABEL_ACRONYMS.get(stem.lower(), stem)
        return ""

    @classmethod
    def list_categories(cls) -> List[str]:
        """Sorted distinct default categories across all discoverable macros.

        Derived from the ``*Macros`` mixins (via :meth:`macro_category`) so the
        category set never drifts from the code organization — adding a new
        ``<Domain>Macros`` mixin contributes its category automatically.
        """
        cats = {cls.macro_category(name) for name in cls.list_available_macros()}
        cats.discard("")
        return sorted(cats)

    @classmethod
    def macro_help(cls, name: str) -> str:
        """Return a macro's full (dedented) docstring — the single source of
        truth for its UI tooltip. Empty string when the macro has no docstring."""
        try:
            attr = inspect.getattr_static(cls, name)
        except AttributeError:
            return ""
        func = (
            attr.__func__
            if isinstance(attr, (staticmethod, classmethod))
            else attr
        )
        return (inspect.getdoc(func) or "").strip()

    @classmethod
    def get_current_bindings(cls) -> Dict[str, dict]:
        """Return the *live* key + category for every available macro.

        Reads Maya's actual hotkey registry (via
        :func:`mayatk.ui_utils.hotkey_collisions.live_hotkey_map`) and the live
        ``runTimeCommand`` category, so the result reflects bindings made through
        this panel *and* Maya's own Hotkey Editor — the "just works after you set
        it" contract. Outside an interactive Maya the live registry is empty, so
        every macro reports no key.

        Returns:
            ``{macro_name: {"key": str, "cat": str}}`` for every available macro.
            Unbound macros report an empty ``key``; ``cat`` falls back to the
            mixin-derived default (:meth:`macro_category`) so it is never empty.
        """
        live: Dict[str, str] = {}
        if cmds is not None:
            try:
                from mayatk.ui_utils.hotkey_collisions import live_hotkey_map

                live = live_hotkey_map()
            except Exception:
                live = {}

        bindings: Dict[str, dict] = {}
        for name in cls.list_available_macros():
            cat = ""
            if cmds is not None:
                try:
                    if cmds.runTimeCommand(name, exists=True):
                        cat = cmds.runTimeCommand(name, query=True, category=True) or ""
                except Exception:
                    pass
            # Fall back to the mixin-derived default so every macro has a
            # category even when unbound / no live runTimeCommand category.
            bindings[name] = {
                "key": live.get(name, ""),
                "cat": cat or cls.macro_category(name),
            }
        return bindings

    @classmethod
    def apply_bindings(cls, bindings: Dict[str, dict]) -> None:
        """Apply a binding set ``{name: {"key", "cat"}}``.

        An entry with a falsy ``key`` clears that macro's hotkey instead of
        setting one. ``set_macro`` remains the single low-level applier.
        """
        for name, spec in (bindings or {}).items():
            if not isinstance(spec, dict):
                continue
            key = spec.get("key")
            cat = spec.get("cat")
            if not key:
                cls.clear_hotkey(name)
                continue
            cls.set_macro(name, key=key, cat=cat)

    @classmethod
    def clear_hotkey(cls, name: str, key: Optional[str] = None) -> None:
        """Unbind ``name``'s hotkey (the runtime command itself is kept).

        When *key* is omitted it is resolved from the active/``default`` preset
        so the correct chord is released.
        """
        if cmds is None:
            return
        if key is None:  # resolve the live key Maya currently has bound
            try:
                from mayatk.ui_utils.hotkey_collisions import live_hotkey_map

                key = live_hotkey_map().get(name)
            except Exception:
                key = None
        if not key:
            return
        ctl, alt, sht, k = cls._parse_key(key)
        try:
            cmds.hotkey(
                keyShortcut=k, name="", releaseName="", ctl=ctl, alt=alt, sht=sht
            )
        except RuntimeError as error:
            print(f"# Error: {__file__}: clear_hotkey({name}): {error} #")

    @classmethod
    def unset_macro(cls, name: str, key: Optional[str] = None) -> None:
        """Clear ``name``'s hotkey and delete its (non-default) runtime command."""
        cls.clear_hotkey(name, key=key)
        if cmds is None:
            return
        try:
            if cmds.runTimeCommand(name, exists=True) and not cmds.runTimeCommand(
                name, query=True, default=True
            ):
                cmds.runTimeCommand(name, edit=True, delete=True)
        except RuntimeError as error:
            print(f"# Error: {__file__}: unset_macro({name}): {error} #")

    @classmethod
    def find_conflicts(cls, bindings: Dict[str, dict]) -> Dict[str, List[str]]:
        """Return ``{normalized_key: [macro, ...]}`` for keys bound more than once."""
        from collections import defaultdict

        by_key: Dict[str, List[str]] = defaultdict(list)
        for name, spec in (bindings or {}).items():
            key = spec.get("key") if isinstance(spec, dict) else None
            if key:
                by_key[cls._normalize_key(key)].append(name)
        return {k: v for k, v in by_key.items() if len(v) > 1}

    # --- key-format helpers (pure string, DCC-free) -------------------

    @staticmethod
    def _parse_key(key: str) -> Tuple[bool, bool, bool, str]:
        """Split a Maya key token into ``(ctl, alt, sht, key)``.

        Modifiers (``ctl``/``alt``/``sht``) are matched case-insensitively; the
        remaining token is the key itself (e.g. ``"i"``, ``"F3"``).
        """
        ctl = alt = sht = False
        k = str(key)
        for char in str(key).split("+"):
            token = char.strip()
            low = token.lower()
            if low == "ctl":
                ctl = True
            elif low == "alt":
                alt = True
            elif low == "sht":
                sht = True
            else:
                k = token
        return ctl, alt, sht, k

    @classmethod
    def _normalize_key(cls, key: str) -> str:
        """Canonical form of a key token (sorted modifiers, lowercased letter)."""
        ctl, alt, sht, k = cls._parse_key(key)
        present = {"ctl": ctl, "alt": alt, "sht": sht}
        mods = [m for m in cls._MOD_ORDER if present[m]]
        k = k.lower() if len(k) == 1 else k
        return "+".join(mods + [k])

    @classmethod
    def qt_sequence_to_maya_key(cls, sequence: str) -> str:
        """Convert a Qt key-sequence string (``"Ctrl+Shift+I"``) to a Maya token.

        Returns ``""`` when *sequence* carries no non-modifier key.
        """
        if not sequence:
            return ""
        present = {"ctl": False, "alt": False, "sht": False}
        key = None
        for part in str(sequence).split("+"):
            token = part.strip()
            mod = cls._QT_MOD_MAP.get(token.lower())
            if mod:
                present[mod] = True
            elif token:
                key = token
        if key is None:
            return ""
        if len(key) == 1:
            key = key.lower()
        mods = [m for m in cls._MOD_ORDER if present[m]]
        return "+".join(mods + [key])

    @classmethod
    def maya_key_to_qt_sequence(cls, key: str) -> str:
        """Convert a Maya key token (``"ctl+sht+i"``) to a Qt key-sequence string."""
        if not key:
            return ""
        ctl, alt, sht, k = cls._parse_key(key)
        seq: List[str] = []
        if ctl:
            seq.append("Ctrl")
        if alt:
            seq.append("Alt")
        if sht:
            seq.append("Shift")
        seq.append(k.upper() if len(k) == 1 else k)
        return "+".join(seq)

    # --- preset persistence (PresetStore-backed, DCC-free) ------------

    @classmethod
    def _preset_store(cls) -> "ptk.PresetStore":
        """Two-tier store: shipped ``macro_manager/presets`` + a writable user tier.

        Resolves to the same files the UI's ``uitk.PresetManager`` uses (relative
        ``preset_dir="mayatk/macro_manager"``), so headless and GUI share one
        source of truth.
        """
        builtin_dir = os.path.join(
            os.path.dirname(__file__), "macro_manager", "presets"
        )
        return ptk.PresetStore(
            cls.PRESET_NAME, package=cls.PRESET_PACKAGE, builtin_dir=builtin_dir
        )

    @classmethod
    def list_presets(cls) -> List[str]:
        """Return all preset names (built-in + user, user shadows built-in)."""
        return cls._preset_store().list()

    @classmethod
    def load_preset(cls, name: str) -> Dict[str, dict]:
        """Return the binding set stored under *name* (``_meta`` stripped)."""
        data = cls._preset_store().load(name)
        return {k: v for k, v in data.items() if k != "_meta"}

    @classmethod
    def save_preset(
        cls, name: str, bindings: Optional[Dict[str, dict]] = None
    ) -> str:
        """Save *bindings* (default: the current bindings) as user preset *name*.

        Sets *name* as the active preset and returns the written path (as str).
        """
        if bindings is None:
            bindings = cls.get_current_bindings()
        data: Dict[str, object] = {"_meta": {"version": 1}}
        data.update(bindings)
        store = cls._preset_store()
        path = store.save(name, data)
        store.active = name
        return str(path)

    @classmethod
    def delete_preset(cls, name: str) -> bool:
        """Delete a *user* preset (built-ins are read-only). Returns success."""
        return cls._preset_store().delete(name)

    @classmethod
    def get_active_preset(cls) -> Optional[str]:
        """The last-selected/applied preset name, or ``None``."""
        return cls._preset_store().active

    @classmethod
    def set_active_preset(cls, name: Optional[str]) -> None:
        """Set (or clear, with ``None``) the active-preset pointer."""
        cls._preset_store().active = name

    @classmethod
    def apply_saved_macros(cls, name: Optional[str] = None) -> None:
        """Apply a saved preset/template's bindings to Maya on demand.

        This is **not** a startup requirement — hotkeys set through the Macro
        Manager are registered as non-default ``runTimeCommand`` + ``hotkey``
        entries, which Maya persists and restores on its own. Use this to apply
        a named template explicitly (e.g. the shipped ``default`` set) from the
        UI or a pipeline.

        Resolution order: explicit *name* -> the persisted active preset ->
        the shipped ``default`` template. A stale active pointer falls back to
        ``default``. Loading an explicit *name* makes it active.
        """
        store = cls._preset_store()
        target = name or store.active or cls.DEFAULT_PRESET
        try:
            bindings = cls.load_preset(target)
        except (KeyError, ValueError, OSError):
            if target == cls.DEFAULT_PRESET:
                return
            try:
                bindings = cls.load_preset(cls.DEFAULT_PRESET)
            except (KeyError, ValueError, OSError):
                return
        cls.apply_bindings(bindings)
        if name:
            store.active = name


class DisplayMacros:
    """ """

    @staticmethod
    def m_component_id_display():
        """Toggle Component Id Display through vertices, edges, faces, UVs, and off."""
        # Query the current state of component ID display settings for vertices, edges, faces, and UVs
        current_state = cmds.polyOptions(q=True, displayItemNumbers=True)[:4]

        # Determine the next state to switch to
        if True not in current_state:
            next_state_index = 0  # If all are False, start with vertices
        else:
            # Find the first True, switch to the next state or turn all off if it's the last one
            current_index = current_state.index(True)
            next_state_index = (
                current_index + 1
            ) % 5  # Cycle through 0-4 (vertices, edges, faces, UVs, off)

        # Define the configurations for toggling component IDs
        configurations = [
            {"displayItemNumbers": (1, 0, 0, 0)},  # Vertex IDs
            {"displayItemNumbers": (0, 1, 0, 0)},  # Edge IDs
            {"displayItemNumbers": (0, 0, 1, 0)},  # Face IDs
            {"displayItemNumbers": (0, 0, 0, 1)},  # UV IDs
            {"displayItemNumbers": (0, 0, 0, 0)},  # Turn all off
        ]
        labels = ["vertex IDs", "edge IDs", "face IDs", "UV IDs", "Off"]

        # Apply the selected configuration
        cmds.polyOptions(activeObjects=True, **configurations[next_state_index])

        # Display message in the viewport
        cmds.inViewMessage(
            amg=f"Component ID Display: <hl>{labels[next_state_index]}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @staticmethod
    def m_normals_display():
        """Toggle face normals, vertex normals, tangents, and off."""
        # polyOptions returns None when no polygon mesh is in the selection.
        tangent_state = cmds.polyOptions(q=True, displayTangent=True)
        if not tangent_state:
            cmds.inViewMessage(
                amg="Select a polygon mesh.", pos="topCenter", fade=True
            )
            return

        current_tangent = tangent_state[0]
        current_normal = cmds.polyOptions(q=True, displayNormal=True)[0]
        is_facet = cmds.polyOptions(q=True, facet=True)[0]
        is_vertex = cmds.polyOptions(q=True, point=True)[0]

        # Define the current state based on queries
        if current_tangent:
            current_state = 3  # Tangents are displayed
        elif current_normal and is_vertex:
            current_state = 2  # Vertex normals are displayed
        elif current_normal and is_facet:
            current_state = 1  # Facet normals are displayed
        else:
            current_state = 0  # All displays are off

        # Determine the next state to switch to
        next_state = (current_state + 1) % 4  # Cycle through the states: 0, 1, 2, 3

        # Configuration for each state
        if next_state == 0:
            cmds.polyOptions(displayNormal=False, displayTangent=False)
        elif next_state == 1:
            cmds.polyOptions(
                displayNormal=True,
                facet=True,
                point=False,
                displayTangent=False,
                sizeNormal=1,
            )
        elif next_state == 2:
            cmds.polyOptions(
                displayNormal=True,
                point=True,
                facet=False,
                displayTangent=False,
                sizeNormal=1,
            )
        elif next_state == 3:
            cmds.polyOptions(displayTangent=True, displayNormal=False)

        # Messages for each state
        messages = [
            "Normals Display <hl>Off</hl>",
            "<hl>Facet</hl> Normals Display <hl>On</hl>",
            "<hl>Vertex</hl> Normals Display <hl>On</hl>",
            "<hl>Tangent</hl> Display <hl>On</hl>",
        ]

        # Display message in the viewport using inViewMessage
        cmds.inViewMessage(amg=messages[next_state], pos="topCenter", fade=True)

    @staticmethod
    def m_soft_edge_display():
        """Toggle Soft Edge Display."""
        # Query the current setting for all edges display
        all_edges_visible = cmds.polyOptions(q=True, ae=True)[0]

        # Toggle the edge display based on the current state
        if all_edges_visible:
            # If all edges are currently visible, switch to soft edges only
            cmds.polyOptions(ae=False, se=True)
            message = "Soft Edge Display <hl>On</hl>"
        else:
            # If not all edges are visible, it implies soft edges are active; switch to show all edges
            cmds.polyOptions(se=False, ae=True)
            message = "All Edges Display <hl>On</hl>"

        # Display message in the viewport using inViewMessage
        cmds.inViewMessage(amg=message, pos="topCenter", fade=True)

    @staticmethod
    def m_toggle_visibility():
        """Toggle visibility of the selected objects, keeping them selected.

        Hides shown objects (and shows hidden ones) without clearing the
        selection, so you can repeatedly flip the same set on and off.
        """
        mel.eval("ToggleVisibilityAndKeepSelection")

    @staticmethod
    @CoreUtils.selected
    def m_toggle_uv_border_edges(objects):
        """Toggle the display of UV border edges for the given objects."""
        if not objects:
            cmds.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        for obj in cmds.ls(objects, flatten=True):
            # Use MEL command to toggle UV border edges visibility
            state = cmds.polyOptions(obj, query=True, displayMapBorder=True)[0]
            if state:  # Turn it off
                cmds.polyOptions(obj, displayMapBorder=False)
                cmds.inViewMessage(
                    statusMessage="UV Border Edges <hl>Hidden</hl>.",
                    pos="topCenter",
                    fade=True,
                )
            else:  # If not displaying UV borders, turn it on
                cmds.polyOptions(obj, displayMapBorder=True)
                cmds.inViewMessage(
                    statusMessage=f"UV Border Edges <hl>Shown</hl>.",
                    pos="topCenter",
                    fade=True,
                )

    @staticmethod
    @CoreUtils.selected
    def m_back_face_culling(objects) -> None:
        """Toggle Back-Face Culling on selected objects, or on all objects if none are selected."""
        objects = objects or cmds.ls(type="mesh")
        if objects:
            state: bool = cmds.polyOptions(objects, query=True, wireBackCulling=True)[0]
            if state:
                cmds.polyOptions(objects, wireBackCulling=False, backCulling=True)
                message = "OFF"
            else:
                cmds.polyOptions(objects, wireBackCulling=True, backCulling=False)
                message = "ON"

            cmds.inViewMessage(
                statusMessage=f"Back-Face Culling is now <hl>{message}</hl>.",
                pos="topCenter",
                fade=True,
            )
        else:  # Feedback if there are no meshes at all in the scene
            cmds.inViewMessage(
                statusMessage="<hl>No mesh objects found in the scene.</hl>",
                pos="topCenter",
                fade=True,
            )

    @staticmethod
    def m_isolate_selected() -> None:
        """Isolate the current selection in the active 3D viewport."""
        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            cmds.inViewMessage(
                statusMessage="Isolate Select requires an active <hl>3D viewport</hl>.",
                pos="topCenter",
                fade=True,
            )
            return

        state = cmds.isolateSelect(panel, query=1, state=1)
        if state:
            cmds.isolateSelect(panel, state=0)
            cmds.isolateSelect(panel, removeSelected=1)
        else:
            cmds.isolateSelect(panel, state=1)
            cmds.isolateSelect(panel, addSelected=1)

    @staticmethod
    @CoreUtils.selected
    def m_cycle_display_state(objects) -> None:
        """Cycle the display state of all selected objects based on the first object's state."""
        sel = NodeUtils.get_unique_children(objects)

        try:  # Determine the state of the first object
            first_obj = sel[0]
        except IndexError:
            cmds.inViewMessage(
                statusMessage="No objects selected. Please select at least one object.",
                pos="topCenter",
                fade=True,
            )
            return

        # Validate the object and attributes existence
        is_visible = cmds.getAttr(f"{first_obj}.visibility")
        is_templated = cmds.attributeQuery(
            "template", node=first_obj, exists=True
        ) and cmds.getAttr(f"{first_obj}.template")
        xray_query_result = cmds.displaySurface(first_obj, xRay=True, query=True)
        is_xray = xray_query_result[0] if xray_query_result else False

        # Define the next state and action based on the initial state
        if is_visible and not is_templated and not is_xray:
            next_state = "XRay"
            action = lambda obj: cmds.displaySurface(obj, xRay=True)
        elif is_xray:
            next_state = "Templated"
            action = lambda obj: (
                cmds.displaySurface(obj, xRay=False),
                cmds.setAttr(f"{obj}.template", True),
            )
        elif is_templated:
            next_state = "Hidden"
            action = lambda obj: (
                cmds.setAttr(f"{obj}.template", False),
                cmds.setAttr(f"{obj}.visibility", False),
            )
        else:  # Assume hidden if not visible, templated, or x-ray
            next_state = "Visible"
            action = lambda obj: cmds.setAttr(f"{obj}.visibility", True)

        # Apply the state transition to all selected objects
        for obj in sel:
            action(obj)

        cmds.inViewMessage(
            statusMessage=f"Display: <hl>{next_state}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @staticmethod
    @CoreUtils.selected
    def m_wireframe_toggle(objects) -> None:
        """Toggle Wireframe Display on selected objects, or on all objects if none are selected."""
        objects = objects or cmds.ls(type="mesh")
        if objects:
            # Check the current state of the first object in the list
            current_state: bool = cmds.getAttr(f"{objects[0]}.overrideShading") == 1
            # Toggle the overrideDisplayType attribute for all objects
            for obj in objects:
                cmds.setAttr(f"{obj}.overrideEnabled", 1)
                new_state = 0 if current_state else 1  # 0: Normal, 1: Wireframe
                cmds.setAttr(f"{obj}.overrideShading", new_state)

            # Provide feedback message
            message = "Wireframe" if not current_state else "Shaded"
            cmds.inViewMessage(
                statusMessage=f"Display mode is now <hl>{message}</hl>.",
                pos="topCenter",
                fade=True,
            )

    @staticmethod
    def m_grid_and_image_planes() -> None:
        """Toggle grid and image plane visibility."""
        image_plane = cmds.ls(exactType="imagePlane")

        for obj in image_plane:
            attr = obj + ".displayMode"
            if not cmds.getAttr(attr) == 2:
                cmds.setAttr(attr, 2)
                cmds.grid(toggle=1)
                cmds.inViewMessage(
                    statusMessage="Grid is now <hl>ON</hl>.", pos="topCenter", fade=True
                )
            else:
                cmds.setAttr(attr, 0)
                cmds.grid(toggle=0)
                cmds.inViewMessage(
                    statusMessage="Grid is now <hl>OFF</hl>.",
                    pos="topCenter",
                    fade=True,
                )

    @staticmethod
    @CoreUtils.selected
    def m_frame(objects) -> None:
        """Frame the selection, cycling zoom level on repeated presses.

        Pressing the hotkey repeatedly steps through three progressively
        tighter / looser fit factors, so one key both frames and fine-tunes
        the framing. With nothing selected, frames all objects. Honors the
        active component mask (vertex / edge / face) for component framing.
        """
        # Initialise the MEL global variable used to track the toggle state.
        mel.eval('global int $toggleFrame_; if (!`exists "toggleFrame_"`) {$toggleFrame_=0;}')
        mode = cmds.selectMode(q=True, component=True)
        maskVertex = cmds.selectType(q=True, vertex=True)
        maskEdge = cmds.selectType(q=True, edge=True)
        maskFacet = cmds.selectType(q=True, facet=True)

        # Define toggle states and fit factors
        toggle_states = {
            "vertices": [(0.10, 1), (0.65, 2), (0.01, 0)],
            "vertex": [(0.01, 1), (0.15, 2), (0.01, 0)],
            "edge": [(0.9, 1), (0.3, 2), (0.1, 0)],
            "facet": [(0.45, 1), (0.9, 2), (0.2, 0)],
            "object": [(0.75, 1), (0.99, 2), (0.5, 0)],
        }

        def _get_toggle() -> int:
            try:
                return int(mel.eval("$tmp=$toggleFrame_;") or 0)
            except Exception:
                return 0

        def _set_toggle(val: int) -> None:
            mel.eval(f"global int $toggleFrame_; $toggleFrame_={val};")

        def frame_element(element_type):
            current_toggle = _get_toggle()
            fitFactorVal, next_toggle = toggle_states[element_type][current_toggle]
            cmds.viewFit(fitFactor=fitFactorVal)
            _set_toggle(next_toggle)
            print(f"frame {element_type} {next_toggle}")

        if len(objects) == 0:
            cmds.viewFit(allObjects=1)
        else:
            if mode == 1:
                if maskVertex == 1:
                    element_type = "vertices" if len(objects) > 1 else "vertex"
                elif maskEdge == 1:
                    element_type = "edge"
                elif maskFacet == 1:
                    element_type = "facet"
                else:
                    element_type = "object"
            else:
                element_type = "object"

            frame_element(element_type)

    @classmethod
    @CoreUtils.selected
    def m_smooth_preview(cls, objects) -> None:
        """Toggle smooth mesh preview."""
        objs = NodeUtils.get_unique_children(objects)
        for obj in objs:
            if cmds.getAttr(f"{obj}.displaySmoothMesh") != 2:
                cmds.setAttr(f"{obj}.displaySmoothMesh", 2)  # smooth preview on
                cmds.displayPref(wireframeOnShadedActive="none")
                cmds.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Off</hl>.",
                )

            elif (
                cmds.getAttr(f"{obj}.displaySmoothMesh") == 2
                and cmds.displayPref(query=1, wireframeOnShadedActive=1) == "none"
            ):
                cmds.setAttr(f"{obj}.displaySmoothMesh", 2)  # smooth preview on
                shapes = cmds.listRelatives(objects, children=1, shapes=1) or []
                [cmds.setAttr(f"{s}.displaySubdComps", 1) for s in shapes]
                cmds.displayPref(wireframeOnShadedActive="full")
                cmds.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>ON</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            else:
                cmds.setAttr(f"{obj}.displaySmoothMesh", 0)  # smooth preview off
                cmds.displayPref(wireframeOnShadedActive="full")
                cmds.inViewMessage(
                    position="topCenter",
                    fade=1,
                    statusMessage="S-Div Preview <hl>OFF</hl>.<br>Wireframe <hl>Full</hl>.",
                )

            if cmds.getAttr(f"{obj}.smoothLevel") != 1:
                cmds.setAttr(f"{obj}.smoothLevel", 1)

    @staticmethod
    def m_wireframe() -> None:
        """Toggles the wireframe display state.
        Possible states include: none, shaded, full
        """
        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            print("No model panel found.")
            return

        # Query the current wireframe on shaded setting
        state = cmds.displayPref(q=True, wireframeOnShadedActive=True)

        if state == "none":  # Full Wireframe
            cmds.displayPref(wireframeOnShadedActive="full")
            cmds.modelEditor(panel, e=True, wireframeOnShaded=True)
            message = "Wireframe <hl>Full</hl>."
        elif state == "full":  # Wireframe Selected
            cmds.displayPref(wireframeOnShadedActive="reduced")
            cmds.modelEditor(panel, e=True, wireframeOnShaded=False)
            message = "Wireframe <hl>Reduced</hl>."
        elif state == "reduced":  # Wireframe Off
            cmds.displayPref(wireframeOnShadedActive="none")
            cmds.modelEditor(panel, e=True, wireframeOnShaded=False)
            message = "Wireframe <hl>None</hl>."
        else:  # Fallback or error condition, you might want to log an error or set a default state
            print(f"Unexpected wireframe state encountered: {state}")
            return

        # Display the message
        cmds.inViewMessage(position="topCenter", fade=True, statusMessage=message)

    @staticmethod
    def m_material_override():
        """Toggle the viewport's default-material override.

        Replaces all assigned materials in the active viewport with Maya's flat
        default material so you can read silhouette and form without texture or
        shading distraction. Press again to restore the real materials.
        """
        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            cmds.inViewMessage(
                statusMessage="Material Override requires an active <hl>3D viewport</hl>.",
                pos="topCenter",
                fade=True,
            )
            return

        # Query the current state of default material usage
        state = cmds.modelEditor(panel, q=True, useDefaultMaterial=True)

        # Toggle the state of the default material
        cmds.modelEditor(panel, edit=True, useDefaultMaterial=not state)

        # Display the toggle state in the viewport
        cmds.inViewMessage(
            statusMessage=f"Default Material Override: <hl>{'On' if not state else 'Off'}</hl>.",
            pos="topCenter",
            fade=True,
        )

    @classmethod
    def m_shading(cls) -> None:
        """Toggles viewport display mode between wireframe, smooth shaded with textures off,
        and smooth shaded with textures on. The transitions occur in the order mentioned.
        """
        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            return

        displayAppearance = cmds.modelEditor(panel, q=True, displayAppearance=True)
        displayTextures = cmds.modelEditor(panel, q=True, displayTextures=True)

        if displayAppearance == "wireframe":
            cmds.modelEditor(
                panel,
                edit=True,
                displayAppearance="smoothShaded",
                displayTextures=False,
            )
            cmds.inViewMessage(
                statusMessage="smoothShaded <hl>True</hl>\ndisplayTextures <hl>False</hl>.",
                fade=True,
                position="topCenter",
            )
        elif displayAppearance == "smoothShaded" and not displayTextures:
            cmds.modelEditor(
                panel,
                edit=True,
                displayAppearance="smoothShaded",
                displayTextures=True,
            )
            cmds.inViewMessage(
                statusMessage="smoothShaded <hl>True</hl>\ndisplayTextures <hl>True</hl>.",
                fade=True,
                position="topCenter",
            )
        else:
            cmds.modelEditor(
                panel,
                edit=True,
                displayAppearance="wireframe",
                displayTextures=False,
            )
            cmds.inViewMessage(
                statusMessage="wireframe <hl>True</hl>.",
                fade=True,
                position="topCenter",
            )

    @classmethod
    def m_lighting(cls) -> None:
        """Toggles viewport lighting between different states: default, all lights, active lights,
        and flat lighting. If the lighting mode is not one of these states, it resets to the default state.
        """
        panel = UiUtils.get_model_panel(with_focus=True)
        if not panel:
            return

        displayLights = cmds.modelEditor(panel, query=1, displayLights=1)

        if displayLights == "default":
            cmds.modelEditor(panel, edit=1, displayLights="all")
            cmds.inViewMessage(
                statusMessage="displayLights <hl>all</hl>.",
                fade=True,
                position="topCenter",
            )
        elif displayLights == "all":
            cmds.modelEditor(panel, edit=1, displayLights="active")
            cmds.inViewMessage(
                statusMessage="displayLights <hl>active</hl>.",
                fade=True,
                position="topCenter",
            )
        elif displayLights == "active":
            cmds.modelEditor(panel, edit=1, displayLights="flat")
            cmds.inViewMessage(
                statusMessage="displayLights <hl>flat</hl>.",
                fade=True,
                position="topCenter",
            )
        else:
            cmds.modelEditor(panel, edit=1, displayLights="default")
            cmds.inViewMessage(
                statusMessage="displayLights <hl>default</hl>.",
                fade=True,
                position="topCenter",
            )


class EditMacros:
    """ """

    @staticmethod
    @CoreUtils.undoable
    def m_group(objects=None):
        """Group the given objects (or selection), center the pivot, and rename the group.

        DEPRECATED: Use EditUtils.group_objects instead.
        """
        return EditUtils.group_objects(objects)

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.reparent
    @DisplayUtils.add_to_isolation
    def m_combine(
        objects=None,
        group_by_material=False,
        cluster_by_distance=False,
        threshold=10000.0,
        **kwargs,
    ):
        """Combine multiple meshes.

        Parameters:
            objects (list): List of mesh objects to combine.
            group_by_material (bool): Combine objects into groups based on their assigned materials.
            cluster_by_distance (bool): If True, further subdivide material groups based on spatial proximity.
            threshold (float): The maximum distance between objects to be considered in the same cluster.
        """
        EditUtils.combine_objects(
            objects=objects,
            group_by_material=group_by_material,
            cluster_by_distance=cluster_by_distance,
            threshold=threshold,
            **kwargs,
        )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    @CoreUtils.reparent
    @DisplayUtils.add_to_isolation
    def m_boolean(objects, repair_mesh=True, keep_boolean=True, **kwargs):
        """Perform a boolean operation on two meshes using cmds, managing shorthand and full parameter names dynamically."""
        a, *b = objects
        if not a or not b:
            cmds.inViewMessage(
                statusMessage="<hl>Insufficient selection.</hl> Operation requires at least two objects",
                fade=True,
                position="topCenter",
            )
            return None

        if keep_boolean:
            b = cmds.duplicate(b, rr=True)

        if len(b) > 1:  # Combine multiple meshes
            b = cmds.polyUnite(b, centerPivot=True, ch=False)[0]

        if repair_mesh:  # Clean any n-gons before running the boolean
            from mayatk import MeshDiagnostics

            MeshDiagnostics.clean_geometry(
                objects=a,
                repair=True,
                nonmanifold=True,
                nsided=True,
                bakePartialHistory=True,
            )
        # Resolve operation type, defaulting to 'union'
        operation_types = {"union": 1, "difference": 2, "intersection": 3}
        operation = kwargs.pop("operation", kwargs.pop("op", "union"))
        if isinstance(operation, str):
            operation = operation_types.get(operation, 1)

        # Resolve name and construction history conflicts
        name = kwargs.pop("name", kwargs.pop("n", a))
        ch = kwargs.pop("constructionHistory", kwargs.pop("ch", False))

        # Perform the boolean operation
        result = cmds.polyCBoolOp(a, b, op=operation, n=name, ch=ch, **kwargs)[0]

        return result

    @staticmethod
    @CoreUtils.selected
    def m_lock_vertex_normals(objects):
        """Toggle lock/unlock vertex normals."""
        if not objects:
            cmds.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        # Check if the current selection mode is object mode
        is_object_mode = cmds.selectMode(q=True, object=True)

        if is_object_mode:  # Use the .vtx[:] notation directly
            objs = [f"{obj}.vtx[:]" for obj in objects]
        else:  # Convert selected components to vertices if in component mode
            converted = cmds.polyListComponentConversion(objects, toVertex=True)
            objs = cmds.ls(converted, flatten=True) if converted else []

        if not objs:
            print("No valid objects or components given.")
            return

        # Determine the current normal state by querying the first vertex
        current_state = all(cmds.polyNormalPerVertex(objs, q=True, freezeNormal=True))

        if current_state:
            # If normals are currently locked, unlock them
            cmds.polyNormalPerVertex(objs, unFreezeNormal=True)
            cmds.inViewMessage(
                statusMessage="Normals <hl>UnLocked</hl>.", pos="topCenter", fade=True
            )
        else:
            # If normals are currently unlocked, lock them
            cmds.polyNormalPerVertex(objs, freezeNormal=True)
            cmds.inViewMessage(
                statusMessage="Normals <hl>Locked</hl>.", pos="topCenter", fade=True
            )

    @staticmethod
    def m_paste_and_rename() -> None:
        """Paste and rename by removing 'pasted__' prefix and reference file names,
        and handle grouping for the pasted objects elegantly.
        """
        # Get a list of all nodes in the scene before pasting
        before_paste = set(cmds.ls())

        # Perform the paste operation
        try:
            mel.eval("cutCopyPaste paste")
        except Exception:
            cmds.inViewMessage(
                statusMessage="<hl>Nothing to paste from</hl>.",
                pos="topCenter",
                fade=True,
            )
            return

        # Get a list of all nodes in the scene after pasting and find the difference
        after_paste = set(cmds.ls())
        pasted_nodes = list(after_paste - before_paste)
        if not pasted_nodes:
            return

        # Track by UUID — survives the rename/reparent operations below.
        pasted_uuids = cmds.ls(pasted_nodes, uuid=True) or []

        def resolve(uuid: str):
            found = cmds.ls(uuid) or []
            return found[0] if found else None

        # Identify the topmost new transform among the pasted nodes (names still valid here).
        top_level_uuid = None
        for uid in pasted_uuids:
            node = resolve(uid)
            if not node or cmds.objectType(node) != "transform":
                continue
            if cmds.listRelatives(node, parent=True, fullPath=True):
                continue
            top_level_uuid = uid
            break

        if top_level_uuid:
            top = resolve(top_level_uuid)
            children = cmds.listRelatives(top, children=True, fullPath=True) or []
            if len(children) == 1:
                # Unparent the single child and discard the wrapper group.
                cmds.parent(children, world=True)
                cmds.delete(top)
            else:
                cmds.rename(top, "pasted")

        # Strip 'pasted__' prefix and reference namespaces from remaining pasted nodes.
        for uid in pasted_uuids:
            node = resolve(uid)
            if not node:
                continue
            base_name = node.split("|")[-1]
            new_name = base_name.replace("pasted__", "").split(":")[-1]
            if new_name == base_name:
                continue
            try:
                cmds.rename(node, new_name)
            except RuntimeError as e:
                print(f"Error renaming {node}: {e}")

    @staticmethod
    def m_multi_component() -> None:
        """Enable the multi-component selection mask.

        Lets you select vertices, edges, and faces simultaneously without
        switching component modes — useful for mixed-component operations.
        """
        mel.eval("SelectMultiComponentMask")
        cmds.inViewMessage(
            statusMessage="<hl>Multi-Component Selection Mode</hl><br>Mask is now <hl>ON</hl>.",
            fade=True,
            position="topCenter",
        )

    @staticmethod
    @CoreUtils.selected
    def m_merge_vertices(objects, tolerance=0.001) -> None:
        """Merge vertices within a small distance tolerance.

        In component mode, merges the selected vertices (or collapses selected
        edges / faces to their center). In object mode, merges every coincident
        vertex on each selected mesh. Tolerance defaults to 0.001.
        """
        objects = cmds.ls(objects, objectsOnly=True)

        if not objects:
            cmds.inViewMessage(
                statusMessage="Warning: <hl>Nothing selected</hl>.<br>Must select an object or component.",
                pos="topCenter",
                fade=True,
            )

        else:
            for obj in objects:
                if cmds.selectMode(q=True, component=True):  # Merge selected components.
                    if cmds.filterExpand(selectionMask=31):  # Vertices
                        cmds.polyMergeVertex(
                            distance=tolerance,
                            alwaysMergeTwoVertices=True,
                            constructionHistory=True,
                        )
                    else:  # If selection type is edges or facets:
                        mel.eval("MergeToCenter")

                else:  # If object mode. merge all vertices on the selected object.
                    for n, obj in enumerate(objects):
                        # Get number of vertices
                        count = cmds.polyEvaluate(obj, vertex=True)
                        vertices = str(obj) + ".vtx [0:" + str(count) + "]"
                        cmds.polyMergeVertex(
                            vertices,
                            distance=tolerance,
                            alwaysMergeTwoVertices=False,
                            constructionHistory=False,
                        )

                    # Return to original state
                    cmds.select(clear=True)
                    for obj in objects:
                        cmds.select(obj, add=True)


class SelectionMacros:
    """ """

    @staticmethod
    def m_object_selection() -> None:
        """Set object selection mask."""
        object_mode = cmds.selectMode(query=True, object=True)
        cmds.selectMode(co=object_mode)
        cmds.selectMode(object=True)
        cmds.selectType(allObjects=True)

    @staticmethod
    def m_vertex_selection() -> None:
        """Set vertex selection mask."""
        cmds.selectMode(component=True)
        cmds.selectType(vertex=True)

    @staticmethod
    def m_edge_selection() -> None:
        """Set edge selection mask."""
        cmds.selectMode(component=True)
        cmds.selectType(edge=True)

    @staticmethod
    def m_face_selection() -> None:
        """Set face selection mask."""
        cmds.selectMode(component=True)
        cmds.selectType(facet=True)

    @staticmethod
    def m_invert_selection() -> None:
        """Invert the current selection of geometry or components."""
        objects = cmds.ls(selection=True, flatten=True)

        if not objects:
            cmds.warning("No valid objects selected to invert.")
            return

        first = str(objects[0])

        # Components have a "node.type[index]" format; bare nodes don't.
        if "." in first and "[" in first:
            EditUtils.invert_components(select=True)
        else:
            EditUtils.invert_geometry(select=True)

    @staticmethod
    @CoreUtils.selected
    def m_toggle_selectability(objects):
        """Toggle selectability of the given objects."""
        if not objects:
            cmds.inViewMessage(
                statusMessage="Operation requires at least one selected object.",
                pos="topCenter",
                fade=True,
            )
            return

        def _attr_ok(node, attr):
            if not cmds.attributeQuery(attr, node=node, exists=True):
                return False
            return not cmds.getAttr(f"{node}.{attr}", lock=True)

        for obj in cmds.ls(objects, flatten=True):
            try:
                required = (
                    "overrideEnabled",
                    "overrideDisplayType",
                    "useOutlinerColor",
                    "outlinerColor",
                )
                blocked = next((a for a in required if not _attr_ok(obj, a)), None)
                if blocked:
                    cmds.warning(
                        f"Cannot modify {blocked} for {obj}: missing or locked."
                    )
                    continue

                override_enabled = cmds.getAttr(f"{obj}.overrideEnabled")
                current_state = cmds.getAttr(f"{obj}.overrideDisplayType")

                if override_enabled and current_state == 2:
                    # Object is currently non-selectable, make it selectable
                    cmds.setAttr(f"{obj}.overrideDisplayType", 0)  # Normal mode
                    cmds.setAttr(f"{obj}.useOutlinerColor", 0)
                    cmds.inViewMessage(
                        statusMessage=f"{obj} <hl>Selectable</hl>.",
                        pos="topCenter",
                        fade=True,
                    )
                else:  # Object is currently selectable, make it non-selectable
                    cmds.setAttr(f"{obj}.overrideEnabled", 1)
                    cmds.setAttr(f"{obj}.overrideDisplayType", 2)  # Reference mode
                    cmds.setAttr(f"{obj}.useOutlinerColor", 1)
                    cmds.setAttr(
                        f"{obj}.outlinerColor", 0.3, 0.6, 0.6, type="double3"
                    )  # desaturated teal
                    cmds.inViewMessage(
                        statusMessage=f"{obj} <hl>Non-selectable</hl>.",
                        pos="topCenter",
                        fade=True,
                    )

            except RuntimeError as e:
                cmds.warning(f"Failed to modify selectability for {obj}: {e}")

    @staticmethod
    def m_toggle_UV_select_type() -> None:
        """Toggles between UV shell and UV component selection.
        Always switches to UV shell mode unless already in UV shell mode,
        then switches to UV component mode.
        """
        inUVShellMode: bool = cmds.selectType(query=True, meshUVShell=True)
        cmds.selectMode(component=True)

        if inUVShellMode:  # Switch to UV component mode
            cmds.selectType(polymeshUV=True)
            cmds.inViewMessage(
                statusMessage="Select Type: <hl>Polymesh UV</hl>",
                fade=True,
                position="topCenter",
            )
        else:  # Switch to UV shell mode
            cmds.selectType(meshUVShell=True)
            cmds.inViewMessage(
                statusMessage="Select Type: <hl>UV Shell</hl>",
                fade=True,
                position="topCenter",
            )

    @staticmethod
    def m_invert_component_selection() -> None:
        """Invert the component selection on the currently selected objects."""
        if not cmds.selectMode(query=1, component=1):  # component select mode
            return "Error: Selection must be at the component level."

        objects = cmds.ls(sl=1, objectsOnly=1)
        selection = cmds.ls(sl=1)

        invert = []
        for obj in objects:
            if cmds.selectType(query=1, vertex=1):  # vertex
                selectedVertices = (
                    cmds.filterExpand(selection, selectionMask=31, expand=1) or []
                )
                allVertices = cmds.filterExpand(obj + ".v[*]", sm=31) or []
                invert += {v for v in allVertices if v not in selectedVertices}

            elif cmds.selectType(query=1, edge=1):  # edge
                edges = cmds.filterExpand(selection, selectionMask=32, expand=1) or []
                allEdges = cmds.filterExpand(obj + ".e[*]", sm=32) or []
                invert += {e for e in allEdges if e not in edges}

            elif cmds.selectType(query=1, facet=1):  # face
                selectedFaces = (
                    cmds.filterExpand(selection, selectionMask=34, expand=1) or []
                )
                allFaces = cmds.filterExpand(obj + ".f[*]", sm=34) or []
                invert += {f for f in allFaces if f not in selectedFaces}

        cmds.select(invert, replace=1)


class UiMacros:
    """ """

    @staticmethod
    def m_toggle_panels(toggle_menu: bool = True, toggle_panels: bool = True) -> None:
        """Toggle UI toolbars and menu bar in sync.

        Parameters:
            toggle_menu (bool): If True, toggles the visibility of the main menu bar.
            toggle_panels (bool): If True, toggles the visibility of panel toolbars.
        """
        # Get the main Maya window and its menu bar
        main_window = UiUtils.get_main_window()
        menu_bar = main_window.menuBar() if main_window else None

        # Use the visibility of the menu bar as the source state
        if menu_bar:
            current_state = menu_bar.isVisible()
        else:
            current_state = True  # Default to True if menu bar is not found

        # Determine the new state based on the current state of the menu bar
        new_state = not current_state

        # Toggle the main menu bar
        if toggle_menu and menu_bar:
            menu_bar.setVisible(new_state)

        # Toggle the panels based on the new state
        if toggle_panels:
            panels = cmds.getPanel(allPanels=True)
            for panel in panels:
                cmds.panel(panel, edit=True, menuBarVisible=new_state)
            mel.eval(f"ToggleModelEditorBars {new_state}")


class AnimationMacros:
    """ """

    @staticmethod
    @CoreUtils.selected
    def m_set_selected_keys(objects) -> None:
        """Set keys for any attributes (channels) that are selected in the channel box."""
        for obj in objects:
            attrs = Attributes.get_selected_channels()
            for attr in attrs:
                cmds.setKeyframe(f"{obj}.{attr}")
                # cutKey -cl -t ":" -f ":" -at "tx" -at "ty" -at "tz" pSphere1; #remove keys

    @staticmethod
    @CoreUtils.selected
    def m_unset_selected_keys(objects) -> None:
        """Un-set keys for any attributes (channels) that are selected in the channel box."""
        for obj in objects:
            attrs = Attributes.get_selected_channels()
            for attr in attrs:
                target = f"{obj}.{attr}"
                cmds.setKeyframe(target)
                cmds.cutKey(target, cl=True)  # remove keys

    # ========================================================================================


class Macros(
    MacroManager,
    DisplayMacros,
    EditMacros,
    SelectionMacros,
    AnimationMacros,
    UiMacros,
):
    """ """

    pass


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# #create wrapper
# mel.createMelWrapper(method)

# #set command
# cmds.nameCommand('name', annotation='', command=<>)
# cmds.hotkey(key='1', altModifier=True, name='name')


# #clear keyboard shortcut
# cmds.hotkey(keyShortcut=key, name='', releaseName='', ctl=ctl, alt=alt, sht=sht) #unset the key press name and releaseName.


# #query runTimeCommand
# if cmds.runTimeCommand('name', exists=True):


# #delete runTimeCommand
# cmds.runTimeCommand('name', edit=True, delete=True)


# #set runTimeCommand
# cmds.runTimeCommand(
#             'name',
#             annotation=string,
#             category=string,
#             categoryArray,
#             command=script,
#             commandArray,
#             commandLanguage=string,
#             default=boolean,
#             defaultCommandArray,
#             delete,
#             exists,
#             hotkeyCtx=string,
#             image=string,
#             keywords=string,
#             annotation=string,
#             longAnnotation=string,
#             numberOfCommands,
#             numberOfDefaultCommands,
#             numberOfUserCommands,
#             plugin=string,
#             save,
#             showInHotkeyEditor=boolean,
#             tags=string,
#             userCommandArray,
# )

# -annotation(-ann) string createqueryedit
#         Description of the command.

# -category(-cat) string createqueryedit
#         Category for the command.

# -categoryArray(-caa) query
#         Return all the run time command categories.

# -command(-c) script createqueryedit
#         Command to be executed when runTimeCommand is invoked.

# -commandArray(-ca) query
#         Returns an string array containing the names of all the run time commands.

# -commandLanguage(-cl) string createqueryedit
#         In edit or create mode, this flag allows the caller to choose a scripting language for a command passed to the "-command" flag. If this flag is not specified, then the callback will be assumed to be in the language from which the runTimeCommand command was called. In query mode, the language for this runTimeCommand is returned. The possible values are "mel" or "python".

# -default(-d) boolean createquery
#         Indicate that this run time command is a default command. Default run time commands will not be saved to preferences.

# -defaultCommandArray(-dca) query
#         Returns an string array containing the names of all the default run time commands.

# -delete(-del) edit
#         Delete the specified user run time command.

# -exists(-ex) create
#         Returns true|false depending upon whether the specified object exists. Other flags are ignored.

# -hotkeyCtx(-hc) string createqueryedit
#         hotkey Context for the command.

# -image(-i) string createqueryedit
#         Image filename for the command.

# -keywords(-k) string createqueryedit
#         Keywords for the command. Used for searching for commands in Type To Find. When multiple keywords, use ; as a separator. (Example: "keyword1;keyword2")

# -annotation(-annotation) string createqueryedit
#         Label for the command.

# -longAnnotation(-la) string createqueryedit
#         Extensive, multi-line description of the command. This will show up in Type To Finds more info page in addition to the annotation.

# -numberOfCommands(-nc) query
#         Return the number of run time commands.

# -numberOfDefaultCommands(-ndc) query
#         Return the number of default run time commands.

# -numberOfUserCommands(-nuc) query
#         Return the number of user run time commands.

# -plugin(-p) string createqueryedit
#         Name of the plugin this command requires to be loaded. This flag wraps the script provided into a safety check and automatically loads the plugin referenced on execution if it hasn't been loaded. If the plugin fails to load, the command won't be executed.

# -save(-s) edit
#         Save all the user run time commands.

# -showInHotkeyEditor(-she) boolean createqueryedit
#         Indicate that this run time command should be shown in the Hotkey Editor. Default value is true.

# -tags(-t) string createqueryedit
#         Tags for the command. Used for grouping commands in Type To Find. When more than one tag, use ; as a separator. (Example: "tag1;tag2")

# -userCommandArray(-uca) query
#         Returns an string array containing the names of all the user run time commands.
