# !/usr/bin/python
# coding=utf-8
"""Reference Manager panel — the Switchboard slots for ``reference_manager.ui``.

:class:`ReferenceManagerSlots` handles widget init and events and delegates to
:class:`~mayatk.env_utils.reference_manager.reference_manager_controller.ReferenceManagerController`.
"""

import contextlib
import html
import os

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from mayatk.core_utils.script_job_manager import ScriptJobManager

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.reference_manager._reference_manager import (
    ReferenceManager,
)
from mayatk.env_utils.reference_manager.reference_manager_controller import (
    ReferenceManagerController,
    _scratch_twins,
)


class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin):
    """UI event handlers and widget initialization for the Reference Manager interface.

    This class handles pure UI interactions including:
    - Widget initialization and setup (tables, buttons, checkboxes)
    - Event slot connections and signal handling
    - User input processing (text changes, button clicks, selections)
    - Menu and context menu setup
    - UI state synchronization during initialization

    Widget Responsibilities:
    - txt000: Root directory input with browse, workspace options, and pin values for directory history
    - txt001: File filter input with enable/strip options
    - cmb000: Workspace selection dropdown
    - tbl000: File table with reference selection and context menu
    - Various buttons and checkboxes for reference operations

    New Features:
    - Pin Values: txt000 now supports pinning frequently used directories for quick access
      Users can pin current directory and select from previously pinned directories
      Directories are persisted under the key "reference_manager_directories",
      host-namespaced by uitk (-> "..._maya") so the blendertk twin -- which
      passes the same key by design -- keeps its own list

    The slots class maintains no business logic - it purely routes UI events
    to the appropriate controller methods.
    """

    # File-type classification for this panel (mirror of blendertk, inverted). NATIVE types
    # list + reference directly — exactly what the engine's workspace scan covers (Maya
    # references .ma/.mb natively, .fbx through the FBX plugin, USD through mayaUsd's
    # translator); FOREIGN types are cross-DCC rows baked through the blender_bridge before
    # they can be referenced. The header's Include Types row toggles each type;
    # _INCLUDE_TYPES is the column order (shared across both panels).
    _INCLUDE_TYPES = ("ma", "mb", "fbx", "usd", "blend")
    NATIVE_EXTENSIONS = tuple(t.lstrip("*") for t in ReferenceManager.SCENE_FILE_TYPES)
    FOREIGN_EXTENSIONS = (".blend",)
    # An include type that lists more than its own spelling: a USD layer or package is any
    # of .usd/.usda/.usdc/.usdz. Every other type lists just ``.<type>``.
    _INCLUDE_TYPE_EXTENSIONS = {"usd": ptk.USD_EXTENSIONS}
    # Rows Unlink and Import brings in with no reference behind them: a foreign row
    # converts; a USD row imports natively — the one way in for a stage whose skins a live
    # read refuses (UsdUtils.live_read_options).
    _IMPORT_EXTENSIONS = (*FOREIGN_EXTENSIONS, *ptk.USD_EXTENSIONS)
    # Default-checked include types for this panel — its own native scene types.
    _INCLUDE_DEFAULTS = (".ma", ".mb")

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager

        # Flag to prevent checkbox events during initialization
        self._initializing = True

        self.controller = ReferenceManagerController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        mgr = ScriptJobManager.instance()
        mgr.subscribe(
            "SceneOpened",
            self.controller.refresh_file_list,
            owner=self,
        )
        mgr.connect_cleanup(self.ui, owner=self)

        self._setup_footer_actions()
        self.controller._update_workspace_footer()

        # Initialization complete
        self._initializing = False

        # Initial sync of selection to existing references, and apply the
        # Notes-column visibility once persisted checkbox state is restored.
        # Use a timer to ensure UI is fully initialized first.
        def _post_init():
            # Selection sync is the critical existing behavior — run it first
            # so the cosmetic column-visibility apply can't preempt it if it
            # ever raises (defer_with_timer swallows+logs the exception).
            self.controller.sync_selection_to_references()
            self._apply_notes_column_visibility()

        self.sb.defer_with_timer(_post_init, ms=100)

        self.logger.debug("ReferenceManagerSlots initialized.")

    def _setup_footer_actions(self):
        """Build the footer actions once, then (re)wire their signals to THIS instance.

        Same construction/wiring split as ``tbl000_init``: the footer QWidget persists
        across a slots reload while this instance (and ``self.controller``) rebuilds, so
        an unguarded rebuild would duplicate the buttons and a bare ``connect`` would
        leave clicks bound to a dead ``self``.

        The footer hosts the panel's primary action, **Save To Workspace**. The
        naming conventions it applies stay in the header menu: Rename, Delete and the
        list filters read them too, so they are panel-wide settings, not Save's.
        Hovering the button previews the exact path it would write.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None or not hasattr(footer, "add_widget"):
            return
        if not getattr(footer, "_rm_actions_built", False):
            footer._rm_actions_built = True
            self._build_footer_actions(footer)
        self._wire_footer_signals(footer)

    def _build_footer_actions(self, footer):
        """One-time footer construction, left to right: Un-Reference All, then Save
        To Workspace as the outermost, primary action."""
        arrow = self.sb.QtGui.QCursor(self.sb.QtCore.Qt.ArrowCursor)
        height = max(footer.height() - 2, 1)

        # Add order IS the left-to-right order: add_widget(side="right") inserts
        # each widget just before the size grip.
        unref_btn = self.sb.QtWidgets.QPushButton("Un-Reference All", footer)
        unref_btn.setToolTip("Remove all references from the scene.")
        unref_btn.setCursor(arrow)
        unref_btn.setFixedHeight(height)
        footer.add_widget(unref_btn, side="right", background=True)
        footer._rm_unref_btn = unref_btn

        save_btn = self.sb.QtWidgets.QPushButton("Save To Workspace", footer)
        save_btn.setObjectName("btn_save_footer")
        save_btn.setCursor(arrow)
        save_btn.setFixedHeight(height)
        # Static fallback only — _wire_footer_signals binds the live tooltip that
        # shows the exact path Save would write.
        save_btn.setToolTip(
            "Save the current scene into the workspace using the header menu's "
            "Naming options."
        )
        footer.add_widget(save_btn, side="right", background=True)
        footer._rm_save_btn = save_btn

    def _wire_footer_signals(self, footer):
        """(Re)wire the footer actions + live tooltips to this instance (idempotent)."""
        save_btn = getattr(footer, "_rm_save_btn", None)
        if save_btn is not None:
            self._rewire_signal(save_btn, save_btn.clicked, self.btn_save_scene, "save")
            # Live tooltip: hovering Save shows the full path it would write, per
            # the current scene + naming options (re-binding replaces the provider).
            self.sb.tooltip.bind(save_btn, self.controller._save_scene_preview)
        unref_btn = getattr(footer, "_rm_unref_btn", None)
        if unref_btn is not None:
            self._rewire_signal(
                unref_btn, unref_btn.clicked, self.btn_unreference_all, "unref"
            )

    def header_init(self, widget):
        """Initialize the header for the reference manager."""
        # Gesture-scoped window: pin button + auto-hide on key_show release. Runs on every call
        # (declarative, cheap) and the signal is re-wired idempotently because the header QWidget
        # can outlive this slots instance — a bare .connect() on a second call would leave a stale
        # connection bound to a dead ``self`` alongside the live one. Goes through
        # ``_rewire_signal`` (drops only OUR prior connection): a blanket ``disconnect()`` makes
        # libpyside warn "Failed to disconnect (None) from signal" on the first, unconnected call.
        widget.config_buttons("refresh", "menu", "collapse", "pin")
        # Tap-to-pin: letting the marking-menu key go right after this panel opens pins it,
        # same as a click on the pin button would — the user came here to work, not to peek.
        # Holding the key still auto-hides on release, so a glance costs nothing. Explicit
        # per-tool opt-in (an assignment, not the process-wide UiHandler.pin_on_tap default)
        # so it survives regardless of that preference.
        widget.pin_on_tap = True
        self._rewire_signal(
            widget, widget.refresh_requested, self.btn_refresh, "hdr_refresh"
        )

        # One-time menu build: a repeat call (e.g. the offscreen test harness's documented
        # "drive *_init explicitly" pattern) must not re-append every Naming / Filter /
        # Include-Types control — duplicate header controls. Only the widget CONSTRUCTION is
        # guarded; config_buttons + the signal above stay outside so a reload still re-targets
        # them at the current ``self``.
        if widget.is_initialized:
            # The header outlives a reload: re-target the live preview at this
            # instance's controller (the edits themselves are auto-wired).
            self.controller._wire_structure_tooltip(widget.menu)
            return
        # Save / load the naming + filter settings as named presets.
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = "mayatk/reference_manager"

        # --- Naming: panel-wide conventions ----------------------------------
        # Save applies all three; Rename applies case + suffix and moves a
        # {name} folder; Delete removes an emptied {name} folder; the Filter
        # options below match against suffix + folder structure.
        widget.menu.add("Separator", setTitle="Naming:")
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_case_style",
            setToolTip="Case convention applied to the file name on Save and Rename.",
            addItems=[
                "None",
                "camel",
                "pascal",
                "title",
                "upper",
                "lower",
                "capitalize",
            ],
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_suffix",
            setPlaceholderText="Suffix (e.g. _v01)…",
            setToolTip="Suffix appended to the file name on Save and Rename (excluded "
            "from case formatting) — also what Filter by Suffix and Hide Suffix match.",
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_subfolder_structure",
            setText="{scenes}",
            setPlaceholderText="Folder Structure (e.g. {scenes}/{name})…",
        )
        # Live tooltip: hovering the Folder Structure field shows the placeholders
        # resolved against the current workspace + scene, plus the real save dir.
        self.controller._wire_structure_tooltip(widget.menu)

        # --- Filter / Display: narrow the list + shorten displayed names -----
        widget.menu.add("Separator", setTitle="Filter / Display:")
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Suffix",
            setObjectName="chk_filter_suffix",
            setChecked=False,
            setToolTip="Show only files whose name ends with the Suffix above.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Folder Structure",
            setObjectName="chk_filter_folder_structure",
            setChecked=False,
            setToolTip="Show only files whose location matches the Folder Structure above.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Suffix",
            setObjectName="chk_hide_suffix",
            setChecked=False,
            setToolTip="Hide the suffix from the displayed file name.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Extension",
            setObjectName="chk_hide_extension",
            setChecked=False,
            setToolTip="Hide the file extension from the displayed file name.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Show Notes Column",
            setObjectName="chk_show_notes_column",
            setChecked=False,
            setToolTip="Show the Notes column (per-file comments / metadata). Hidden by default.",
        )

        # Foreign-scene conversion route (mirror across both panels). FBX (default):
        # instancing is native to the format on BOTH sides, so shared geometry
        # survives with no sidecar replay in the path — and when FBX's texture
        # manifest does fail, the loss is VISIBLE (classic-model materials) and
        # structurally harmless. USD: richer material graphs plus native animation,
        # but instance relationships are rebuilt from the conversion sidecar, and
        # that rebuild currently fails SILENTLY (see .claude/BACKLOG.md) — a scene
        # that looks correct but no longer shares shapes. Opt in per scene.
        #
        # Item order is APPEND-ONLY: uitk persists a combo by INDEX, so reordering
        # would retroactively flip every stored pick. The default moves via
        # setCurrentIndex, never by moving items. The objectName was renamed off
        # `cmb_foreign_route` when the default changed, deliberately orphaning the
        # old key so the new default reaches profiles that had already stored one.
        widget.menu.add("Separator", setTitle="Foreign Scenes:")
        widget.menu.add(
            "QComboBox",
            addItems=["Convert via USD", "Convert via FBX"],
            setCurrentIndex=1,  # FBX
            setObjectName="cmb_conversion_route",
            setToolTip=(
                "Intermediate used when opening / importing / referencing a foreign "
                "scene.\n"
                "FBX (default): instancing is carried by the format itself, so a "
                "scene keeps its shared shapes without a rebuild step.\n"
                "USD: richer materials, plus animation arrives natively — but "
                "instances are rebuilt from a sidecar. Prefer it for look-heavy "
                "scenes, and check instancing survived."
            ),
        )

        # No Rig setting: how a scene's rig logic travels is asked per scene, and
        # only when it has some (controller._resolve_rig_mode).

        # Include Types — a single horizontal row of per-type toggles (mirror across both
        # panels). Replaces the old "Hide Binary Files" + "Include Blender Scenes" checkboxes:
        # .ma/.mb/.fbx/USD list + reference natively; .blend lists as a foreign row baked
        # through the blender_bridge before it can be referenced.
        self._add_include_types_row(widget.menu)

        # --- Operations: bulk reference actions ------------------------------
        widget.menu.add("Separator", setTitle="Operations:")
        widget.menu.add(
            "QPushButton",
            setText="Convert to Assembly",
            setObjectName="btn_convert_assembly",
            setToolTip="Replace every reference with an assembly-definition representation.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Unlink and Import All",
            setObjectName="btn_unlink_import_all",
            setToolTip="Import every reference's contents as native nodes (removes the "
            "reference links). The button beside it cycles the namespace handling.",
        )
        self.controller._add_unlink_namespace_action(widget.menu.btn_unlink_import_all)
        # Un-Reference All lives on the footer only (_setup_footer_actions).
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Reference Manager",
                body="A workspace scene-file manager: browse a project's scene files, open / save / "
                "rename / delete them, and reference them into the current scene.",
                steps=[
                    "Set a <b>Root Directory</b> (▸ to browse / recent); pick a <b>Workspace</b>.",
                    "Click the row's <b>reference</b> icon to reference / un-reference, the <b>open</b> "
                    "icon to open the scene, the <b>display</b> icon to cycle Normal → Reference → Template.",
                    "<b>Double-click</b> the name to rename; right-click for the full action menu.",
                    "<b>Double-click</b> the <b>Notes</b> column to annotate a file (saved as sidecar metadata).",
                ],
                sections=[
                    (
                        "Footer",
                        [
                            "<b>Un-Reference All</b> removes every reference from the scene.",
                            "<b>Save To Workspace</b> saves the current scene into the "
                            "workspace using the <b>Naming</b> options; hovering the "
                            "button previews the exact path it would write.",
                        ],
                    ),
                    (
                        "Header menu",
                        [
                            "<b>Naming</b> (case / suffix / folder structure) is "
                            "panel-wide: Save and Rename apply it, and the filters "
                            "below match against it.",
                            "<b>Filter by Suffix / Folder Structure</b> narrow the list; "
                            "<b>Hide Suffix / Extension</b> shorten the displayed name; "
                            "<b>Show Notes Column</b> reveals Notes.",
                            "<b>Include Types</b> (ma / mb / fbx / usd / blend) picks which file "
                            "types list; .ma/.mb/.fbx/USD reference natively, a foreign (Blender) "
                            "row's reference icon bakes it to a cached .ma and references that — "
                            "right-click <b>Unlink and Import</b> for a local copy instead (a "
                            "USD row too).",
                            "<b>Operations</b>: <b>Convert to Assembly</b>, <b>Unlink and Import "
                            "All</b>. The button beside Unlink cycles what "
                            "an unlink does with the reference's namespace — remove it, keep it "
                            "on every node, or keep it on the top-level node(s) only.",
                        ],
                    ),
                    (
                        "Filter field (▸ option box)",
                        [
                            "Toggle the filter on/off; <b>Ignore Case</b>; choose what it matches — "
                            "<b>Files</b>, <b>Notes</b>, or both.",
                        ],
                    ),
                ],
                notes=[
                    "<b>Right-click</b> a file row for per-reference actions "
                    "(open, rename, delete, copy path, reveal, …).",
                    "The <b>Workspaces</b> field's ▸ action sets Maya's project to the "
                    "selected workspace (opening a scene from the list does it "
                    "automatically).",
                ],
            )
        )

    def _add_include_types_row(self, menu):
        """Add the Include Types row — one checkbox per file type, side-by-side under a titled
        separator (mirror across both panels). Uses ``Menu.add_row`` so the single-column menu is
        not reflowed; each checkbox is exposed as ``menu.chk_include_<type>`` and re-filters on toggle.

        This panel's native scene types (.ma/.mb) default on; .fbx and USD are native but off
        by default (a workspace's FBX / USD exports are usually noise in a scene list), and the
        foreign .blend type is off.
        """
        tooltip = {
            "ma": "List the workspace's Maya ASCII scenes (.ma) — referenced natively.",
            "mb": "List the workspace's Maya binary scenes (.mb) — referenced natively.",
            "fbx": "List the workspace's FBX files (.fbx) — referenced natively via the FBX plugin.",
            "usd": "List the workspace's USD layers and packages (.usd / .usda / .usdc / .usdz) — "
            "referenced natively via mayaUsd.",
            "blend": "List the workspace's Blender scenes (.blend) — foreign rows bake via a headless Blender.",
        }
        items = [
            (
                "QCheckBox",
                {
                    "setObjectName": f"chk_include_{t}",
                    "setText": t,
                    "setChecked": bool(
                        set(self._type_extensions(t)) & set(self._INCLUDE_DEFAULTS)
                    ),
                    "setToolTip": tooltip[t],
                },
            )
            for t in self._INCLUDE_TYPES
        ]
        for cb in menu.add_row(items, title="Include Types:", justify="expand"):
            cb.toggled.connect(lambda *_: self.controller.refresh_file_list())

    @classmethod
    def _type_extensions(cls, include_type):
        """Every extension the Include Types toggle *include_type* lists (``usd`` -> the
        four USD spellings; any other type -> ``.<type>``). Mirror of blendertk's."""
        return tuple(
            cls._INCLUDE_TYPE_EXTENSIONS.get(include_type, (f".{include_type}",))
        )

    @classmethod
    def _is_importable(cls, path):
        """True if Unlink and Import can bring *path*'s row in with no reference behind it
        (:attr:`_IMPORT_EXTENSIONS`)."""
        return (
            bool(path) and os.path.splitext(path)[1].lower() in cls._IMPORT_EXTENSIONS
        )

    def _included_extensions(self):
        """The set of extensions (``.ma`` … ``.blend``) whose Include Types checkbox is checked.

        Defensive: the header menu may not be built yet during a sibling ``*_init``; falls back to
        the panel defaults so an early refresh still lists this panel's native scenes.
        """
        header = getattr(self.ui, "header", None)
        menu = getattr(header, "menu", None) if header else None
        if menu is None:
            return set(self._INCLUDE_DEFAULTS)
        included = set()
        for t in self._INCLUDE_TYPES:
            chk = getattr(menu, f"chk_include_{t}", None)
            if chk is not None and chk.isChecked():
                included.update(self._type_extensions(t))
        return included

    def tbl000_init(self, widget):
        """Table setup: (re)wire signals every show, one-time context-menu build, then populate.

        ``_wire_table_signals`` runs unconditionally because the ``tbl000`` QWidget can outlive
        this slots instance — a reload builds a NEW ``ReferenceManagerSlots`` (and a fresh
        ``self.controller``, see ``__init__``) on the SAME persisted widget, which already
        carries ``is_initialized`` from the earlier session. Without the re-wire, the row
        action-column handlers / ``itemDoubleClicked`` / ``itemSelectionChanged`` / context-menu
        actions stay bound to the OLD, now-orphaned ``self``/``self.controller`` and silently
        no-op. The context menu's ITEMS (the ``QPushButton`` widgets) stay in the one-time block
        since they mutate the widget, which persists — building them twice is the user-reported
        duplicate context-menu entries. Mirror of blendertk's ``tbl000_init`` / channels'
        ``_wire_table_signals``.

        Order matters: the one-time construction (``setColumnCount``) MUST precede
        ``_wire_table_signals`` — ``actions.add`` sizes its column, and Qt 6.5's
        ``QHeaderView.setSectionResizeMode`` on a not-yet-existing section is a native
        access violation (hard-crashed Maya 2025 at panel launch).
        """
        if not widget.is_initialized:
            widget.is_initialized = True
            widget.setColumnCount(5)
            widget.setHorizontalHeaderLabels(["FILES:", "", "", "", "NOTES:"])

            # Column layout: FILES on the left, NOTES in the middle, the three
            # icon action columns (Reference / Open / Display) pinned to the
            # right. FILES stretches to fill (and NOTES shares the free space
            # when shown), so resizing the window grows a real column instead of
            # leaving dead space beside the fixed-width icon columns.
            #
            # ``moveSection`` is VISUAL-only: it shifts NOTES (logical col 4) to
            # visual position 1 without changing any logical index, so every
            # column reference in this module (actions on 1/2/3, NOTES on 4)
            # stays valid. The icon columns keep their Fixed square sizing
            # (applied by ``TableActions``); a hidden NOTES contributes no width,
            # so FILES simply takes the whole remainder.
            header = widget.horizontalHeader()
            QHeaderView = self.sb.QtWidgets.QHeaderView
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(4, QHeaderView.Stretch)
            header.moveSection(header.visualIndex(4), 1)

            # Use NoEditTriggers and handle editing manually to prevent conflicts with double-click
            widget.setEditTriggers(self.sb.QtWidgets.QAbstractItemView.NoEditTriggers)
            widget.setSelectionBehavior(self.sb.QtWidgets.QAbstractItemView.SelectRows)
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)
            widget.setSortingEnabled(True)
            widget.verticalHeader().setVisible(False)

            # Add context menu
            widget.menu.add(
                "QPushButton",
                setText="Open",
                setObjectName="btn_open_scene",
                setToolTip="Open this scene file — reads it back from disk when it is already\n"
                "the open scene (the action reads 'Reopen' then).",
            )

            widget.menu.add(
                "QPushButton",
                setText="Rename",
                setObjectName="btn_rename_scene",
                setToolTip="Rename this scene file.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Delete",
                setObjectName="btn_delete_scene",
                setToolTip="Delete this scene file.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Reference / Unreference",
                setObjectName="btn_toggle_reference",
                setToolTip="Toggle reference state for this scene.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Unlink and Import",
                setObjectName="btn_unlink_import",
                setToolTip="Make an active reference's data local, or, for a foreign (Blender)\n"
                "row, convert + import its contents via a headless-Blender FBX conversion\n"
                "(a USD row with no reference imports natively).\n"
                "Namespaces are handled per the namespace button beside the header\n"
                "menu's Unlink and Import All.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Open File Location",
                setObjectName="btn_open_file_location",
                setToolTip="Open the containing folder in the file explorer.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Copy Path",
                setObjectName="btn_copy_path",
                setToolTip="Copy the file's full path to the clipboard.",
            )

            self.logger.debug(
                "tbl000 table widget initialized with context menu and rename functionality."
            )
        self._wire_table_signals(widget)

    @staticmethod
    def _rewire_signal(widget, signal, slot, key):
        """Connect *signal* to *slot*, first dropping ONLY this panel's prior connection for *key*.

        A blanket ``signal.disconnect()`` (no args) would also strip the widget's OWN internal
        connections — e.g. the table wires ``customContextMenuRequested`` → ``_show_context_menu``
        in its ``__init__`` (right-click menu), and Qt wires the item delegate's ``closeEditor``
        for the edit lifecycle; nuking those broke the context menu and left a dangling editor on
        the next ``clear()`` (a live-Maya crash). Storing the ``QMetaObject.Connection`` per
        (widget, key) lets a fresh slots instance drop exactly the dead connection — the QWidget
        can outlive the instance across a reload — without touching anything else.

        The stored connection is dropped through the STATIC ``QObject.disconnect``, which is the
        API that takes a ``Connection``: the signal-instance form expects a *slot* and, handed a
        Connection it can't match, emits a ``RuntimeWarning`` ("Failed to disconnect (…) from
        signal …") instead of raising — a warning no ``except`` can swallow. A Connection also
        goes falsy the moment it breaks (PySide drops the binding when the receiving slots
        instance is collected), so a dead one is skipped outright.
        """
        from qtpy import QtCore

        conns = getattr(widget, "_rm_signal_conns", None)
        if conns is None:
            conns = {}
            widget._rm_signal_conns = conns
        old = conns.get(key)
        if old:
            try:
                QtCore.QObject.disconnect(old)
            except (RuntimeError, TypeError):
                pass
        conns[key] = signal.connect(slot)

    def _wire_table_signals(self, widget):
        """(Re)wire tbl000's action columns + Qt signals + context-menu handlers to this instance.

        Idempotent and safe to call on every ``tbl000_init`` (the QWidget can outlive the slots
        instance, and ``__init__`` builds a fresh ``self.controller`` every time).
        ``TableActions.add`` / ``register_menu_action`` are themselves idempotent (dict-keyed —
        each call overwrites the prior entry, no accumulation); the raw Qt signals re-wire through
        ``_rewire_signal`` (precise per-connection disconnect — a blanket disconnect would strip the
        table's own internal handlers). Mirror of blendertk's ``_wire_table_signals``.
        """
        # Single source of truth for the "current scene" highlight colour
        current_clr = widget.ACTION_COLOR_MAP["current"][0]

        # Action column (index 1) — "Reference" icon
        widget.actions.add(
            1,
            states={
                "unreferenced": {
                    "icon": "link",
                    "color": "#555555",
                    "tooltip": "Not referenced — click to add reference",
                    "action": self._toggle_reference_at_row,
                },
                "referenced": {
                    "icon": "link",
                    "color": "#6b8fa3",
                    "tooltip": "Referenced — click to remove reference",
                    "action": self._toggle_reference_at_row,
                },
            },
        )

        # Action column (index 2) — "Open" icon
        widget.actions.add(
            2,
            states={
                "default": {
                    "icon": "open_external",
                    "color": "#555555",
                    "tooltip": "Open Scene",
                    "action": self._open_scene_at_row,
                },
                "current": {
                    "icon": "open_external",
                    "color": current_clr,
                    "tooltip": "Current Scene",
                    "action": self._open_scene_at_row,
                },
            },
        )

        # Action column (index 3) — tri-state display-mode icon
        widget.actions.add(
            3,
            states={
                "off": {
                    "icon": "grid",
                    "color": "#555555",
                    "tooltip": "Display: Normal — click to lock (Reference)",
                    "action": self._cycle_display_mode_at_row,
                },
                "reference": {
                    "icon": "lock",
                    "color": "#d4a84a",
                    "tooltip": "Display: Reference (locked, normal shading) — click for Template (wireframe + locked)",
                    "action": self._cycle_display_mode_at_row,
                },
                "template": {
                    "icon": "grid",
                    "color": "#6b8fa3",
                    "tooltip": "Display: Template (wireframe + locked) — click to restore Normal",
                    "action": self._cycle_display_mode_at_row,
                },
                "unavailable": {
                    "icon": "grid",
                    "color": "#3a3a3a",
                    "tooltip": "Display overrides are only available for active references",
                },
            },
        )

        for key, sig, slot in (
            # Double-click FIRST to ensure it gets priority.
            ("dbl", widget.itemDoubleClicked, self.tbl000_item_double_clicked),
            ("sel", widget.itemSelectionChanged, self.controller.handle_item_selection),
            # Capture which row was right-clicked so row context-menu actions operate only on
            # that row (independent of multi-selection).
            ("ctx", widget.customContextMenuRequested, self._capture_context_row),
            ("chg", widget.itemChanged, self.tbl000_item_changed),
            ("editor", widget.itemDelegate().closeEditor, self.tbl000_editor_closed),
        ):
            self._rewire_signal(widget, sig, slot, key)

        # Switchboard auto-wires `clicked` for any QPushButton whose
        # objectName matches a Slots method, so only register handlers
        # for items mapping to non-slot callables — registering both
        # causes the handler to fire twice.
        widget.register_menu_action("btn_rename_scene", self.controller.rename_scene)
        widget.register_menu_action("btn_delete_scene", self.controller.delete_scene)

    def tbl000_item_double_clicked(self, item):
        """Handle double-click to prepare item for editing."""
        self.logger.debug(
            f"Double-click detected on item: {item.text() if item else 'None'}"
        )

        # Only handle the filename column (index 0), and only for editable rows.
        # A foreign (.blend) row is a cross-DCC import target, not a local scene to rename
        # on disk, so its name cell is intentionally non-editable — skip it here.
        if (
            item
            and item.column() == 0
            and (item.flags() & self.sb.QtCore.Qt.ItemIsEditable)
        ):
            self.logger.debug(f"Starting edit for item: {item.text()}")

            # Prepare the item for editing (show full filename)
            self.controller.prepare_item_for_edit(item)

            # Manually start editing since we disabled automatic edit triggers
            table = self.ui.tbl000
            table.editItem(item)

        elif item and item.column() == 4:  # Notes column
            self.logger.debug(f"Starting edit for notes: {item.text()}")
            table = self.ui.tbl000
            table.editItem(item)

    def tbl000_item_changed(self, item):
        """Handle item changes when user renames a file via inline edit."""
        if item.column() == 0:  # Only handle the filename column (at index 0)
            # Only process if this item is being edited
            if not self.controller.is_item_being_edited(item):
                return

            new_name = item.text().strip()
            if not new_name:
                # If empty, restore the original display name
                self.controller.restore_item_display(item)
                return

            old_path = item.data(self.sb.QtCore.Qt.UserRole)
            if not old_path or not os.path.exists(old_path):
                self.controller.restore_item_display(item)
                return

            old_filename = os.path.basename(old_path)

            # If name unchanged, just restore display
            if new_name == old_filename:
                self.controller.restore_item_display(item)
                return

            # Ensure the new name keeps the original extension if the user omitted it
            _, old_ext = os.path.splitext(old_filename)
            _, new_ext = os.path.splitext(new_name)
            if not new_ext:
                new_name += old_ext

            new_path = os.path.join(os.path.dirname(old_path), new_name)

            # A case-only rename's target "exists" on a case-insensitive file
            # system: it is the file itself.
            if os.path.exists(new_path) and not ptk.FileUtils.is_same_file(
                new_path, old_path
            ):
                self.sb.message_box(f"Target file already exists:<br>{new_name}")
                self.controller.restore_item_display(item)
                return

            # Clear editing state up front: renaming the OPEN scene re-opens it, and that fires
            # SceneOpened -> a table rebuild, which re-uses these items — a still-set editing
            # item would read the rebuild's setText as another inline rename.
            self.controller._editing_item = None

            # A per-scene folder ({name} in the structure) follows under the typed
            # name less the suffix -- the literal edit applies no case convention.
            _case, suffix, structure = self.controller._naming_options()
            folder = (
                ptk.StrUtils.strip_suffix(os.path.splitext(new_name)[0], [suffix])
                if "{name}" in structure
                else None
            )
            try:
                # Same disk-side rename as the context menu's Rename — sidecar carried along,
                # and the open scene saved then re-opened on its new path.
                if (
                    self.controller._rename_scene_file(
                        old_path, new_path, folder=folder
                    )
                    is None
                ):
                    self.controller.restore_item_display(item)
                    return
                self.logger.info(f"Inline renamed {old_path} to {new_path}")

                # Refresh the table with invalidated cache
                self.controller.refresh_file_list(invalidate=True)
            except Exception as e:
                self.logger.error(f"Inline rename failed: {e}")
                self.sb.message_box(f"Rename failed:<br>{html.escape(str(e))}")
                self.controller.restore_item_display(item)

        elif item.column() == 4:  # Notes column
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            if not file_path:
                return

            new_comments = item.text()
            try:
                ptk.Metadata.enable_sidecar = True
                ptk.Metadata.sidecar_only = True
                ptk.Metadata.set(file_path, Comments=new_comments)
                self.logger.info(f"Updated comments for {file_path}")
            except PermissionError:
                sidecar = file_path + ".metadata.json"
                msg = (
                    f"Cannot save notes — permission denied:\n{sidecar}\n\n"
                    "The file or folder may be read-only (e.g. a shared team "
                    "folder on a synced drive with restricted permissions)."
                )
                try:
                    cmds.warning(msg)
                except Exception:
                    pass
                self.logger.warning(msg)
            except Exception as e:
                self.logger.error(f"Failed to set metadata for {file_path}: {e}")

    def tbl000_editor_closed(self, editor, hint):
        """Handle when the rename editor is closed."""
        # Get the item that was being edited
        current_item = self.ui.tbl000.currentItem()
        if current_item and current_item.column() == 0:  # Files column is at index 0
            # Restore the display name (either original or newly edited)
            self.controller.restore_item_display(current_item)

    def _capture_context_row(self, pos):
        """Store which row was right-clicked so context-menu actions are scoped to it, and
        label that row's Open action.

        Runs in the same event-loop tick as the table's own ``customContextMenuRequested`` ->
        ``menu.show()``, so the relabel lands before the popup's first paint whichever slot Qt
        calls first (``show()`` only schedules the paint). The menu's width is set by its
        longest entry — 'Reference / Unreference' — so 'Reopen' never needs a re-layout.
        """
        idx = self.ui.tbl000.indexAt(pos)
        row = idx.row() if idx.isValid() else -1
        self.controller._context_menu_row = row if row >= 0 else None
        self._label_open_action()

    def _label_open_action(self):
        """Read the context menu's Open action as 'Reopen' when the right-clicked row is the
        open scene.

        Opening the row you are already in is a reload-from-disk, not an open; saying so is
        the only signal that the click will discard whatever is unsaved (the prompt then asks
        about). Reads the row through :meth:`_context_row`, so it always labels exactly what
        :meth:`btn_open_scene` will act on.

        Guarded on ``has_menu``, not ``getattr(table, "menu", None)``: uitk's ``MenuMixin``
        builds the menu lazily on first ``.menu`` access, so the convenient getattr would
        CREATE one on a table that has none. Mirror of blendertk's.
        """
        table = self.ui.tbl000
        if not getattr(table, "has_menu", False):
            return
        btn = getattr(table.menu, "btn_open_scene", None)
        if btn is None:
            return
        row = self._context_row()
        item = table.item(row, 0) if row is not None else None
        path = item.data(self.sb.QtCore.Qt.UserRole) if item else None
        btn.setText("Reopen" if path and self._is_current(path) else "Open")

    def _context_row(self):
        """Return the row that was right-clicked, validated against current row count, or None."""
        row = self.controller._context_menu_row
        if row is None:
            return None
        if 0 <= row < self.ui.tbl000.rowCount():
            return row
        return None

    def _get_row_reference_namespaces(self, row):
        """Return namespaces of the file at the given row, if it's a current reference."""
        t = self.ui.tbl000
        item = t.item(row, 0)
        if item is None:
            return []
        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not file_path:
            return []
        norm_path = os.path.normcase(os.path.normpath(file_path))
        namespaces = []
        for ref in self.controller.current_references:
            try:
                if os.path.normcase(os.path.normpath(ref.path)) == norm_path:
                    namespaces.append(ref.namespace)
            except Exception:
                continue
        return namespaces

    def _find_reference_for_path(self, file_path):
        """The active reference whose file is *file_path* (directly or through its bake), or None."""
        norm_fp = os.path.normcase(os.path.normpath(file_path))
        for ref in self.controller.current_references:
            if norm_fp in (
                os.path.normcase(os.path.normpath(ref.path)),
                self.controller._bake_source_key(ref.path),
            ):
                return ref
        return None

    def _select_item_silently(self, item, selected: bool) -> None:
        """Set *item*'s selection without firing ``itemSelectionChanged``.

        A programmatic selection change must not trigger ``handle_item_selection``
        (the selection->reference sync), which would fight an explicit toggle — e.g.
        removing a just-added reference whose row is still non-selectable. Restores
        the table's prior blocked state so it is safe under nesting.
        """
        t = self.ui.tbl000
        blocked = t.blockSignals(True)
        try:
            item.setSelected(selected)
        finally:
            t.blockSignals(blocked)

    def _toggle_reference_at_row(self, row, col):
        """Toggle reference state for the scene at the given row.

        A file is either **open** or **referenced**, never both: referencing the currently-open
        scene first closes it (a new empty scene), since a scene can't be referenced into itself.

        Foreign rows toggle identically (parity rule): the click bakes the source to a
        cached ``.ma`` and references THAT, and a second click removes the same reference
        — the bake's source sidecar resolves a row back to its reference, so the round
        trip works even in a session that did not perform the bake.
        """
        t = self.ui.tbl000
        item = t.item(row, 0)
        if not item:
            return

        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not file_path:
            return

        ref_match = self._find_reference_for_path(file_path)

        if ref_match:
            # Currently referenced — remove it
            self.controller.remove_references(ref_match.namespace)
            t.actions.set(row, 1, "unreferenced")
            t.actions.set(row, 3, "unavailable")
            self._select_item_silently(item, False)
            self.logger.debug(f"Unreferenced: {file_path}")
        else:
            # Referencing the open scene into itself is invalid — close it first (guarded).
            closed_current = self._is_current(file_path)
            if closed_current and not self._close_scene():
                return  # user declined discarding unsaved changes
            # Not referenced — add it. A foreign row references its bake, not its own path.
            namespace = item.text()
            ref_path = file_path
            if self.controller._is_foreign(file_path):
                # The bake's file name is a cache hash (and the display text may be
                # suffix/extension-stripped) — neither makes a sane namespace, so use the
                # source scene's own stem.
                namespace = os.path.splitext(os.path.basename(file_path))[0]
                ref_path = self._bake_foreign_path(file_path)
                if not ref_path:
                    return
            success = self.controller.add_reference(namespace, ref_path)
            if success:
                t.actions.set(row, 1, "referenced")
                # Reflect any display-override state baked into the source file
                disp_mode = "off"
                norm_ref = os.path.normcase(os.path.normpath(ref_path))
                for ref in self.controller.current_references:
                    try:
                        if os.path.normcase(os.path.normpath(ref.path)) == norm_ref:
                            disp_mode = self.controller.get_reference_display_mode(ref)
                            if disp_mode != "off":
                                break
                    except Exception:
                        continue
                t.actions.set(row, 3, disp_mode)
                if not self.controller._is_foreign(file_path):
                    # A foreign row's name cell is intentionally non-selectable (it must
                    # stay out of the selection->reference sync).
                    #
                    # Select silently: when this row was the just-closed current scene,
                    # its name item is still flagged non-selectable (the table hasn't
                    # refreshed yet). An unblocked setSelected fires
                    # handle_item_selection, which drops the non-selectable item from
                    # its "selected" set and then removes the reference we just added as
                    # a stale-selection diff — the bug that made referencing an open
                    # scene take two clicks (close, then reference). The trailing
                    # refresh_file_list re-syncs the real selection.
                    self._select_item_silently(item, True)
                self.logger.debug(f"Referenced: {file_path} (as {ref_path})")
                if closed_current:
                    # Closing the scene changed this row's Open column too (no longer current);
                    # the inline sets above only touch the ref/display columns, so resync. Safe
                    # here at the end — the row/item refs are already consumed.
                    self.controller.refresh_file_list()

    def _foreign_route(self):
        """The conversion route from the header menu — ``"fbx"`` (default) / ``"usd"``.

        FBX: format-native instancing + the classic-model/manifest material route.
        USD: native materials / animation, with instancing rebuilt from a sidecar.

        USD is returned only when explicitly selected, so a missing/unbuilt menu
        falls back to the same route the engine defaults to.
        """
        menu = getattr(getattr(self.ui, "header", None), "menu", None)
        combo = getattr(menu, "cmb_conversion_route", None) if menu else None
        if combo is not None and "USD" in combo.currentText():
            return "usd"
        return "fbx"

    def _resolve_conversion(self, path):
        """Route + rig-mode decision for converting *path*: the kwargs
        ``import_scene`` / ``bake_scene`` take verbatim (``via`` + ``rig_mode``),
        or ``None`` if the user cancelled. Mirror of blendertk's.
        """
        rig_mode = self._resolve_rig_mode(path)
        if rig_mode is None:
            return None
        return {"via": self._foreign_route(), "rig_mode": rig_mode}

    def _resolve_rig_mode(self, path):
        """Decide how a ``.blend``'s rig logic travels (schema 15.1). When the scene
        carries constraints / IK / drivers, prompt Transfer rig / Bake -- the
        conversion-time counterpart of the unsaved-changes confirmation. Returns
        ``"rig"`` / ``"bake"``, ``"auto"`` (no rig logic: nothing to ask), or
        ``None`` if the user cancelled. Mirror of blendertk's.

        No Raw outcome, unlike blendertk's: both Blender exporters sample the
        EVALUATED scene, so on this direction raw converts exactly like bake.
        ``message_box`` takes standard Qt button names only, so the outcomes ride
        Yes (transfer) / No (bake) with the text saying which is which.
        """
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        if not BlenderSceneImport.scene_has_complex_animation(path):
            return "auto"
        choice = self.sb.message_box(
            f"<hl>{os.path.basename(path)}</hl> has rig logic (constraints, IK, "
            "drivers).<br><br><b>Yes</b> = Transfer the rig as editable "
            "relationships where Maya can build them, baking the rest.<br>"
            "<b>No</b> = Bake everything to keyframes.",
            "Yes",
            "No",
            "Cancel",
        )
        return {"Yes": "rig", "No": "bake"}.get(choice)

    @contextlib.contextmanager
    def _conversion_progress(self, text):
        """Footer progress for one foreign-scene conversion; yields the engine's
        ``progress(current, total, message)`` callback (mirror of blendertk's).

        A conversion blocks for as long as the scene takes: a headless Blender
        converts, then a headless Maya bakes. The engine streams both children's
        progress markers into this callback (``pythontk.ProgressRelay``); every tick
        pumps the UI, so the panel repaints instead of freezing, and an Esc-hold on
        the bar stops the running child (``pythontk.OperationCancelled``). The busy
        cursor is the switchboard's owned scope, never a raw override pair.
        """
        with self.sb.busy_cursor():
            with self.sb.progress(
                ui=self.ui, total=100, text=text, busy=True
            ) as update:
                yield self.sb.progress_adapter(update)

    def _footer_status(self, text, level="warning"):
        """Leave *text* in the footer's status label (no-op without a footer)."""
        footer = getattr(self.ui, "footer", None)
        if footer is not None:
            footer.setText(text, level=level)

    def _bake_foreign_path(self, path):
        """Bake the foreign scene at *path* to a cached .ma; return its path or None.

        Maya references FBX natively, so this bake is symmetry rather than necessity:
        both panels reference a cached NATIVE scene, so the referenced-file surface
        behaves identically no matter which DCC the row came from. Both stages are
        cached, so re-referencing an unchanged scene is instant; a first run reports
        into the footer (:meth:`_conversion_progress`, Esc-hold stops it).
        """
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        # Decide (may prompt) before the progress starts, so the modal shows a
        # normal cursor; a cancel returns None like any other no-bake.
        conv = self._resolve_conversion(path)
        if conv is None:
            return None
        name = os.path.basename(path)
        try:
            with self._conversion_progress(f"Converting {name}") as progress:
                return BlenderSceneImport().bake_scene(path, progress=progress, **conv)
        except ptk.OperationCancelled:
            self._footer_status(f"Stopped converting {name}.")
        except FileNotFoundError as e:
            # The error names what is missing -- Blender, or the scene itself.
            self._error_box("Can't reference", name, e)
        except Exception as e:  # noqa: BLE001 — surface the bake error to the user
            self.logger.warning(f"Foreign scene bake failed for {path}: {e}")
            self._error_box("Reference failed for", name, e)
        return None

    # off -> reference -> template -> off
    _DISPLAY_MODE_CYCLE = {
        "off": "reference",
        "reference": "template",
        "template": "off",
    }

    def _cycle_display_mode_at_row(self, row, col):
        """Cycle the display mode (off → reference → template → off) at the given row."""
        t = self.ui.tbl000
        item = t.item(row, 0)
        if not item:
            return

        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not file_path:
            return

        norm_fp = os.path.normcase(os.path.normpath(file_path))
        matched_refs = [
            ref
            for ref in self.controller.current_references
            if os.path.normcase(os.path.normpath(ref.path)) == norm_fp
        ]

        if not matched_refs:
            # Race: the reference was removed externally between table sync
            # and this click. Reset the cell to its true state silently.
            t.actions.set(row, 3, "unavailable")
            return

        current = "off"
        for ref in matched_refs:
            mode = self.controller.get_reference_display_mode(ref)
            if mode != "off":
                current = mode
                break
        new_mode = self._DISPLAY_MODE_CYCLE.get(current, "off")

        any_success = False
        for ref in matched_refs:
            if self.controller.set_reference_display_mode(ref, new_mode):
                any_success = True
        if not any_success:
            self.sb.message_box(
                "Display override had no effect — no top-level transforms "
                "found for the selected reference."
            )
            return
        t.actions.set(row, 3, new_mode)
        self.logger.debug(f"Display mode {current!r} -> {new_mode!r}: {file_path}")

    def _open_scene_at_row(self, row, col):
        """Toggle Open at ``row``: open the scene, or **close** it (new empty scene) if it is
        already the current scene — a second click on the open row's Open icon closes it, the
        mirror of the reference icon's toggle.

        Open and Reference are mutually exclusive, but opening enforces that for free: loading a
        file replaces the whole session, so a reference to it (a reference node in the *previous*
        scene) is discarded and the file becomes the open scene — no explicit un-reference needed.
        """
        t = self.ui.tbl000
        item = t.item(row, 0)
        if not item:
            return
        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not file_path:
            return
        if self._is_current(file_path):
            if self._close_scene():
                self.logger.info("Closed the scene (new empty scene).")
                # file-new fires NewSceneOpened, not the subscribed SceneOpened, so nothing
                # auto-refreshes — resync the table so this row drops its 'current' state.
                self.controller.refresh_file_list()
        else:
            self._open_path(file_path)

    # ------------------------------------------------------------------ open / close helpers
    def _current_scene_path(self):
        """Normalized path of the currently-open scene (or '')."""
        scene = cmds.file(q=True, sceneName=True) or ""
        return os.path.normcase(os.path.normpath(scene)) if scene else ""

    @staticmethod
    def _foreign_scratch_path(path):
        """Deterministic scratch .ma a foreign row is baked+opened into (see open_scene):
        ``<temp>/mtk_opened_<hash>/<stem>_<ext>.ma`` — ``scene.blend`` opens as
        ``scene_blend.ma``, so the title bar and the Save-As default say what it was
        converted from, and it can never shadow a sibling ``scene.ma``; the hash of the
        source's full path keeps same-named scenes in different projects apart.
        ``ptk.ScratchTwins`` owns the naming, the age sweep and the discard; mirror of
        blendertk's (``.blend`` there).
        """
        return _scratch_twins().path_for(path)

    def _is_current(self, path, current=None):
        """True if *path*'s scene is the one currently open (filepath-authoritative).

        A native row matches when the open scene IS that file; a foreign row matches when the open
        scene is that row's deterministic 'opened as new' scratch bake — so a second Open click on
        either closes it. Pass a pre-computed *current* (normalized, from
        :meth:`_current_scene_path`) to reuse one query across a whole table rebuild. Mirror of
        blendertk.
        """
        if not path:
            return False
        cur = current if current is not None else self._current_scene_path()
        if not cur:
            return False
        target = (
            self._foreign_scratch_path(path)
            if self.controller._is_foreign(path)
            else path
        )
        return os.path.normcase(os.path.normpath(target)) == cur

    def _discard_stale_scratches(self):
        """Discard every untouched scratch twin that is no longer the open scene — the
        current scene was replaced (closed, or another opened over it). A twin the user
        saved into is kept (``ptk.ScratchTwins.discard_except``). Mirror of blendertk's."""
        _scratch_twins().discard_except(cmds.file(q=True, sceneName=True) or "")

    def _confirm_discard_unsaved(self):
        """True if it's OK to replace the current scene: nothing unsaved, the user saved, or
        the user chose to discard.

        False cancels the caller's operation — either the user picked Cancel (which is also
        what Esc / the close box map to), or they picked Save and the save did not complete
        (a failure, or a name prompt they backed out of). In both cases the work is still
        unsaved and must not be thrown away.
        """
        if not cmds.file(q=True, modified=True):
            return True
        choice = self.sb.message_box(
            "The current scene has changes, do you want to save?",
            "Save",
            "Discard",
            "Cancel",
        )
        if choice == "Save":
            return self._save_current_scene()
        return choice == "Discard"

    def _save_current_scene(self):
        """Save the open scene in place — or, when it cannot be written where it came from,
        through the panel's Save To Workspace prompt (which asks for a name and applies the
        header's naming conventions, and writes a ``.ma``). True once the scene is clean on
        disk; both paths report their own failures, so the caller only has to honor the result.

        'Cannot be written in place' covers a scene that has never been saved AND one opened
        from a non-Maya format — an ``.fbx`` or USD row opens through its translator and
        keeps that file as its scene name, which ``EnvUtils.SCENE_SAVE_TYPES`` (``.ma`` /
        ``.mb``) has no save type for.
        """
        current = cmds.file(q=True, sceneName=True) or ""
        if os.path.splitext(current)[1].lower() in EnvUtils.SCENE_SAVE_TYPES:
            return self.controller._save_open_scene(current)
        self.controller.save_scene()
        # save_scene has no return value and several bail-outs (declined name prompt, bad
        # workspace, declined overwrite) — the modified flag is the authoritative answer.
        return not cmds.file(q=True, modified=True)

    def _open_path(self, path):
        """Open *path* (replaces the whole session), offering to save the current scene's
        changes first. False when the user backed out of that prompt.

        The single guarded entry point every Open in this panel goes through — the Open
        column, the row context menu's Open/Reopen — because ``open_scene`` passes
        ``force=True``, so Maya's own "save your changes?" prompt never fires and the guard
        here is the only thing standing between a click and the session's unsaved work.
        ``open_scene`` fires SceneOpened, whose scriptJob refreshes the table. Mirror of
        blendertk's ``_open_path``.
        """
        if not self._confirm_discard_unsaved():
            return False
        self.controller.open_scene(path)
        return True

    def _close_scene(self):
        """Close the current scene (a new empty scene — Maya's file-new), guarding unsaved
        changes. A foreign row's untouched scratch copy is removed with it (one the user
        saved into is kept — see :meth:`_discard_stale_scratches`). Returns True if the
        scene was closed, False if the user declined."""
        if not self._confirm_discard_unsaved():
            return False
        if not self.controller.new_scene():
            return False
        self._discard_stale_scratches()
        return True

    # ------------------------------------------------------------------ import (no reference)
    def _error_box(self, lead: str, name: str, error) -> None:
        """Report *error* for the scene file *name*, in a message box.

        Escaped: the box renders rich text, and an error's own text is not
        markup -- pxr quotes prim paths as ``</>``, a repr reads ``<...>`` --
        so unescaped, the part that named the problem vanished.
        """
        self.sb.message_box(
            f"{lead} <hl>{html.escape(name)}</hl>:<br>{html.escape(str(error))}"
        )

    def _import_paths(self, paths):
        """Import each row in *paths* as LOCAL data, with no reference behind it (blocking).

        Delegates to ``mtk.BlenderSceneImport().import_scene``. A foreign (Blender) scene
        converts there — a fresh headless Blender writes FBX (default) or USD per the
        header-menu route, which is imported (FBX: materials rebuilt from the manifest;
        USD: native) and cleaned up (the same bridge the Scene menu's 'Import Blender
        Scene' uses). A USD row needs no conversion: the same engine imports it natively
        (``UsdUtils.import_scene``, which neutralizes a skin the reader would crash on and
        restores it after — the way in for a stage a live reference refuses for its skins,
        :class:`UsdReadRefused`). A conversion
        reports into the footer (:meth:`_conversion_progress`, Esc-hold stops it), and a
        missing Blender install surfaces as a clear message, not a raw traceback.
        """
        paths = [p for p in (paths or []) if self._is_importable(p)]
        if not paths:
            self.sb.message_box("Select a Blender scene (.blend) or USD row to import.")
            return
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        # Decide per scene (may prompt) BEFORE any progress starts, so every question
        # comes up front rather than between minutes-long conversions; a cancelled
        # scene is dropped from the batch. A USD row converts nothing, so asks nothing.
        # Mirror of blendertk's.
        plan = []
        for path in paths:
            if self.controller._is_usd(path):
                plan.append((path, {}))
                continue
            conv = self._resolve_conversion(path)
            if conv is not None:
                plan.append((path, conv))
        if not plan:
            return

        total, done = 0, 0
        importer = BlenderSceneImport()
        for path, conv in plan:
            name = os.path.basename(path)
            try:
                with self._conversion_progress(f"Importing {name}") as progress:
                    total += len(importer.import_scene(path, progress=progress, **conv))
                done += 1
            except ptk.OperationCancelled:
                self._footer_status(f"Stopped importing {name}.")
                break
            except FileNotFoundError as e:
                # The error names what is missing. A missing Blender fails every
                # conversion after this one, so stop; a USD import needs no Blender,
                # so all it can be missing is its own file -- carry on.
                self._error_box("Can't import", name, e)
                if self.controller._is_usd(path):
                    continue
                return
            except Exception as e:  # noqa: BLE001 — surface the conversion error to the user
                self.logger.warning(f"Scene import failed for {path}: {e}")
                self._error_box("Import failed for", name, e)
        self.logger.info(f"Imported {total} object(s) from {done} scene(s).")
        self.controller.refresh_file_list(invalidate=False)

    def btn_open_file_location(self):
        """Open the containing folder of the right-clicked scene file in the file explorer."""
        t = self.ui.tbl000
        row = self._context_row()
        if row is None:
            self.sb.message_box("No scene file selected.")
            return
        item = t.item(row, 0)
        if not item:
            return

        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not file_path:
            self.sb.message_box("Scene file path not found.")
            return

        # Selects the file where it exists, else opens its folder -- on every
        # platform. ``explorer /select`` and ``os.startfile`` were Windows-only:
        # the item raised on a macOS or Linux Maya.
        try:
            ptk.FileUtils.reveal_in_file_manager(file_path)
        except FileNotFoundError:
            parent = os.path.dirname(os.path.normpath(file_path))
            self.sb.message_box(f"Directory not found:<br>{parent}")

    def txt000_init(self, widget):
        """Initialize the text input for the current working directory with pin values."""
        self.logger.debug(
            f"txt000_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )
        if not widget.is_initialized:
            widget.option_box.pin(
                settings_key="reference_manager_directories",
                single_click_restore=True,
            )

            from uitk.widgets.optionBox.options.browse import BrowseOption

            # Directory picker — folded into the option menu as "Set Directory…"
            # (fired via the b000 slot) rather than a standalone folder icon.
            self._browse_option = BrowseOption(
                wrapped_widget=widget,
                mode="directory",
                title="Select a root directory",
                start_dir=lambda: self.controller.current_workspace,
            )

            widget.option_box.menu.add(
                "QPushButton",
                setText="Set Directory…",
                setObjectName="b000",
                setToolTip="Browse for a root directory.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Open Directory",
                setObjectName="b006",
                setToolTip="Open the current directory in the file explorer.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Set To Current Workspace",
                setObjectName="b001",
                setToolTip="Set the root folder to that of the current workspace.",
            )
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Recursive Search",
                setObjectName="chk000",
                setChecked=True,
                setToolTip="Also search sub-folders.",
            )
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Ignore Empty Workspaces",
                setObjectName="chk003",
                setChecked=True,
                setToolTip="Skip workspaces that contain no scene files.",
            )
            widget.set_validator(
                "dir",
                debounce_ms=500,
                invalid_tooltip="Invalid directory",
            )
            widget.validated.connect(
                lambda _ok, text: self.controller.update_current_dir(text)
            )
            self.logger.debug(
                "txt000 text input initialized with pin values for directory history."
            )

        self.controller.update_current_dir()

    def txt001_init(self, widget):
        """Initialize the filter text input with filtering options."""
        if not widget.is_initialized:
            widget.option_box.clear_option = True
            # Toggle owns its on/off persistence under ``settings_key`` so the
            # state survives Maya sessions. ``initial`` only applies on first
            # launch; the persisted value takes precedence on subsequent runs.
            widget.option_box.set_toggle(
                icon="filter",
                tooltip_on="Filter enabled. Click to disable.",
                tooltip_off="Filter disabled. Click to enable.",
                initial=self.controller._filter_enabled,
                gate_wrapped=True,  # grey out the field while the filter is off
                on_toggled=self._toggle_filter,
                settings_key="reference_manager_filter",
            )
            # Sync the controller flag from the restored toggle state. Must run
            # before the first ``refresh_file_list`` so the predicate (which
            # reads ``controller._filter_enabled``) and the icon agree at
            # startup. Safe by ordering: all ``*_init`` slots run before any
            # ready-handler triggers a list refresh.
            from uitk.widgets.optionBox.options.toggle import ToggleOption

            toggle = widget.option_box.find_option(ToggleOption)
            self.controller._filter_enabled = toggle.is_on
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Ignore Case",
                setObjectName="chk_ignore_case",
                setChecked=True,
                setToolTip="Ignore case when filtering.",
            )
            widget.option_box.menu.add(
                "QComboBox",
                setObjectName="cmb_filter_target",
                setToolTip="Choose what the filter text matches against.",
                addItems=[
                    "Filter: All",
                    "Filter: Files",
                    "Filter: Notes",
                ],
            )

            self.logger.debug(
                "txt001 filter text input initialized with filter options."
            )

    def txt001(self, text):
        """Handle the filter text input."""
        self.logger.debug(f"txt001 filter text changed: {text}")
        self.controller._filter_text = text.strip()
        self.controller.refresh_file_list(invalidate=True)

    def cmb000_init(self, widget):
        # The ▸ action beside the combo commits the selection to Maya's project
        # (browsing alone never touches it — only opening a scene does,
        # automatically). Runs on every init on purpose: set_action replaces the
        # prior ActionOption, which is what re-targets the callback at THIS
        # instance's controller after a slots reload (the widget persists).
        widget.option_box.set_action(
            callback=self.controller.set_maya_project,
            icon="home",
            tooltip="Set Maya's project to the selected workspace.\n"
            "Opening a scene from the list sets it automatically; this "
            "applies it without opening anything.",
        )
        # Delegate to the single source of truth for populating cmb000
        self.controller._update_workspace_combo()

    def cmb000(self, index, widget):
        """Handle workspace selection changes."""
        # Handle the case where index is -1 (no selection) which can happen during clearing/repopulating
        if index == -1:
            self.logger.debug(
                f"cmb000 changed to index {index} (no selection) - ignoring"
            )
            return

        # Skip processing during directory updates to prevent cascading triggers
        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("cmb000 called during directory update - ignoring")
            return

        path = widget.itemData(index)
        self.logger.debug(f"cmb000 changed to index {index}, path: {path}")

        if path and os.path.isdir(path):
            # Use centralized method - invalidate=False since we're just switching workspaces
            self.controller.set_workspace(path, invalidate=False)
        else:
            self.logger.warning(f"Invalid workspace path selected: {path}")

    def chk000(self, checked):
        """Handle the recursive search toggle."""
        # Skip processing during initialization or directory updates to prevent unwanted triggers
        if getattr(self, "_initializing", False):
            self.logger.debug("chk000 called during initialization - ignoring")
            return

        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("chk000 called during directory update - ignoring")
            return

        self.logger.debug(
            f"chk000 recursive search toggled: {checked} (type: {type(checked)})"
        )

        # Convert Qt checkbox state to boolean
        # Qt.Unchecked = 0, Qt.PartiallyChecked = 1, Qt.Checked = 2
        if isinstance(checked, int):
            checked_bool = checked == 2  # Qt.Checked
        else:
            checked_bool = bool(checked)

        old_recursive = self.controller.recursive_search

        self.logger.debug(
            f"chk000 old_recursive: {old_recursive}, new_recursive: {checked_bool}"
        )

        # Don't process if the value hasn't actually changed (avoid UI triggering loops)
        if old_recursive == checked_bool:
            self.logger.debug("chk000 recursive search unchanged, no refresh needed")
            return

        self.controller.recursive_search = checked_bool

        self.logger.debug("chk000 recursive search changed, updating workspace combo")
        # Use the centralized workspace combo update method
        self.controller._update_workspace_combo()

    def chk003(self, checked):
        """Handle the ignore empty workspaces toggle."""
        # Skip processing during initialization or directory updates to prevent unwanted triggers
        if getattr(self, "_initializing", False):
            self.logger.debug("chk003 called during initialization - ignoring")
            return

        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("chk003 called during directory update - ignoring")
            return

        self.logger.debug(
            f"chk003 ignore empty workspaces toggled: {checked} (type: {type(checked)})"
        )

        # Convert Qt checkbox state to boolean
        # Qt.Unchecked = 0, Qt.PartiallyChecked = 1, Qt.Checked = 2
        if isinstance(checked, int):
            checked_bool = checked == 2  # Qt.Checked
        else:
            checked_bool = bool(checked)

        old_ignore_empty = self.controller.ignore_empty_workspaces

        self.logger.debug(
            f"chk003 old_ignore_empty: {old_ignore_empty}, new_ignore_empty: {checked_bool}"
        )

        # Don't process if the value hasn't actually changed (avoid UI triggering loops)
        if old_ignore_empty == checked_bool:
            self.logger.debug(
                "chk003 ignore empty workspaces unchanged, no refresh needed"
            )
            return

        self.controller.ignore_empty_workspaces = checked_bool

        self.logger.debug(
            "chk003 ignore empty workspaces changed, updating workspace combo"
        )
        # Use the centralized workspace combo update method
        self.controller._update_workspace_combo()

    def _toggle_filter(self, enabled):
        """Toggle filter enabled state via the option box action."""
        self.logger.debug(f"Filter toggled: {enabled}")
        self.controller._filter_enabled = enabled
        self.controller.refresh_file_list(invalidate=False)

    def chk_ignore_case(self, checked):
        """Handle the ignore case checkbox."""
        self.logger.debug(f"chk_ignore_case changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_filter_suffix(self, checked):
        """Handle the filter by suffix checkbox."""
        self.logger.debug(f"chk_filter_suffix changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_hide_suffix(self, checked):
        """Handle the hide suffix checkbox."""
        self.logger.debug(f"chk_hide_suffix changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_hide_extension(self, checked):
        """Handle the hide extension checkbox."""
        self.logger.debug(f"chk_hide_extension changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_show_notes_column(self, checked):
        """Toggle visibility of the Notes (metadata) column."""
        self.logger.debug(f"chk_show_notes_column changed: {checked}")
        self._apply_notes_column_visibility()

    def _apply_notes_column_visibility(self):
        """Show/hide the Notes column (index 4) per the header toggle.

        Hidden by default — the column is shown only when the
        ``chk_show_notes_column`` header checkbox is checked. Toggling
        visibility is a view-only operation; the notes data is still fetched
        and remains available for filtering even while the column is hidden.
        """
        chk = getattr(self.ui.header.menu, "chk_show_notes_column", None)
        show = chk.isChecked() if chk else False
        self.ui.tbl000.setColumnHidden(4, not show)

    def txt_suffix(self, text):
        """Suffix edited: re-filter if a suffix-dependent option is on."""
        self._on_naming_field_changed()

    def txt_subfolder_structure(self, text):
        """Folder structure edited: re-filter if a dependent option is on."""
        self._on_naming_field_changed()

    def _on_naming_field_changed(self):
        """Re-filter when a naming field changes while a dependent filter /
        display option is on. Checks all three options for either field: cheap,
        and one rule for both."""
        header_menu = self.ui.header.menu
        for chk_name in (
            "chk_hide_suffix",
            "chk_filter_suffix",
            "chk_filter_folder_structure",
        ):
            chk = getattr(header_menu, chk_name, None)
            if chk is not None and chk.isChecked():
                self.controller.refresh_file_list(invalidate=False)
                return

    def chk_filter_folder_structure(self, checked):
        """Handle the filter by folder structure checkbox."""
        self.logger.debug(f"chk_filter_folder_structure changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def b000(self):
        """Browse for a root directory."""
        if hasattr(self, "_browse_option"):
            self._browse_option.browse()
            return

        start_dir = self.ui.txt000.text()
        if not os.path.isdir(start_dir):
            start_dir = self.controller.current_workspace

        selected_directory = self.sb.dir_dialog(
            "Select a root directory", start_dir=start_dir
        )
        self.logger.debug(f"b000 browse selected directory: {selected_directory}")
        if selected_directory:
            self.ui.txt000.setText(selected_directory)

    def b006(self):
        """Open the current directory in the file explorer."""
        current_dir = self.ui.txt000.text()
        ptk.FileUtils.open_explorer(current_dir, logger=self.logger)

    def b001(self):
        """Set dir to current workspace."""
        self.logger.debug("b001 set to current workspace clicked.")
        self.ui.txt000.setText(self.controller.current_workspace)

    def btn_open_scene(self):
        """Open the scene file at the right-clicked row — a **reopen** (reload from disk) when
        that row is already the open scene, which is what the action is labelled then."""
        t = self.ui.tbl000
        row = self._context_row()
        if row is None:
            self.sb.message_box("No scene selected.")
            return
        item = t.item(row, 0)
        if not item:
            return
        file_path = item.data(self.sb.QtCore.Qt.UserRole)
        if file_path:
            self._open_path(file_path)

    def btn_toggle_reference(self):
        """Toggle reference state for the right-clicked row."""
        row = self._context_row()
        if row is None:
            return
        self._toggle_reference_at_row(row, 1)

    def btn_copy_path(self):
        """Copy the right-clicked row's full file path to the clipboard."""
        row = self._context_row()
        item = self.ui.tbl000.item(row, 0) if row is not None else None
        path = item.data(self.sb.QtCore.Qt.UserRole) if item else None
        if not path:
            self.sb.message_box("No scene selected.")
            return
        path = os.path.normpath(path)
        self.sb.QtWidgets.QApplication.clipboard().setText(path)
        footer = getattr(self.ui, "footer", None)
        if footer is not None:
            footer.setStatusText(f"Copied: {path}", level="success")
            self.sb.defer_with_timer(self.controller._update_workspace_footer, ms=3000)

    def btn_unlink_import(self):
        """Unlink and import at the right-clicked row — covers both cases (mirror of Blender).

        An active reference has its data made local (unlink + import); a foreign (Blender) row
        with no active reference is converted and its contents imported via the headless-Blender
        bridge (the old 'Import (convert)' behaviour, folded in here), and a USD row with none is
        imported natively (:meth:`_import_paths`).
        """
        row = self._context_row()
        if row is None:
            self.sb.message_box("No scene selected.")
            return
        namespaces = self._get_row_reference_namespaces(row)
        if namespaces:
            self.controller.unlink_references(namespaces)
            return
        # No DIRECT reference. A foreign (Blender) row references its BAKE, so its namespace
        # isn't found by a path match — resolve it through the bake source (same as the toggle
        # path). If it IS referenced, make that reference local; otherwise import it fresh.
        item = self.ui.tbl000.item(row, 0)
        path = item.data(self.sb.QtCore.Qt.UserRole) if item else None
        if not (path and self._is_importable(path)):
            self.sb.message_box("No active reference selected.")
            return
        norm_fp = os.path.normcase(os.path.normpath(path))
        bake_ns = [
            ref.namespace
            for ref in self.controller.current_references
            if norm_fp
            in (
                os.path.normcase(os.path.normpath(ref.path)),
                self.controller._bake_source_key(ref.path),
            )
        ]
        if bake_ns:
            self.controller.unlink_references(bake_ns)
        else:
            self._import_paths([path])

    def btn_save_scene(self):
        """Save the current scene to the workspace."""
        self.controller.save_scene()

    def btn_refresh(self):
        """Refresh the file list."""
        self.controller.refresh_file_list(invalidate=True)

    def btn_convert_assembly(self):
        """Convert all references to assemblies."""
        self.controller.convert_to_assembly()

    def btn_unlink_import_all(self):
        """Unlink and import all references."""
        self.controller.unlink_all()

    def btn_unreference_all(self):
        """Remove all references from the scene."""
        self.controller.unreference_all()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
