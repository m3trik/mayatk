# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None

import os
from functools import partial

from pythontk.img_utils._img_utils import ImgUtils
from pythontk.file_utils._file_utils import FileUtils
from pythontk.core_utils.engines.textures.map_factory import MapFactory
from pythontk.str_utils.fuzzy_matcher import FuzzyMatcher
from uitk.widgets.footer import FooterStatusController

# From this package:
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes


class TexturePathEditorSlots:
    _ROW_SELECTION_COLUMNS = {
        "shader": 0,
        "path": 1,
        "file_node": 2,
    }

    #: Shader-column label of a lightmap dependency row (no shader, no file
    #: node behind it -- the bake markers of the objects in the third column).
    _LIGHTMAP_ROW_LABEL = "<lightmap>"

    # Read-only class defaults for the lightmap state ``__init__`` sets: every
    # refresh / scope capture REASSIGNS them (never mutates in place), so a
    # driver built without ``__init__`` (the test harnesses) reads "none".
    _lightmap_rows: dict = {}
    _find_copy_lightmaps: tuple = ()

    # Resolve-Missing cascade order: safest first. Same order is used in the
    # header-menu checkbox listing so the visual matches the run order.
    _RESOLVE_STRATEGY_ORDER = ("stem", "texture", "fuzzy")

    # Displayed length of a texture path while the header's "Truncate Texture
    # Paths" toggle is on. Cut with ``mode="path"``, which drops whole middle
    # components: the drive/root stays readable at the front, the filename and
    # as many of its parents as fit at the back. ``_PATH_TRUNCATE_HEAD`` caps
    # the front to that root — what identifies a texture is the end of its
    # path, so the whole budget goes there.
    _PATH_TRUNCATE_LENGTH = 67
    _PATH_TRUNCATE_HEAD = 1

    # Normalize-Paths combobox items. Order is the contract: the menu's
    # combobox is populated in this order, and ``_read_normalize_external_mode``
    # maps ``currentIndex()`` back to the mode key. Reordering breaks the read.
    _NORMALIZE_MODE_ITEMS = (
        ("Leave external textures untouched", "rewrite"),
        ("Copy external textures to sourceimages", "copy"),
        ("Move external textures to sourceimages", "move"),
    )

    # Set-Directory / Find-&-Copy relocate combobox items.
    _RELOCATE_MODE_ITEMS = (
        ("Leave textures in place (path only)", "rewrite"),
        ("Copy textures to new directory", "copy"),
        ("Move textures to new directory", "move"),
    )

    # Colour markers leading the Find & Copy source / destination rows, and the
    # Set Directory picker's caption. The native Windows picker draws its
    # caption through the shell — plain text, no rich text — but it renders
    # emoji in colour, so the marker is the one channel that carries colour
    # without giving up the shell browser (Quick Access, OneDrive, network,
    # recent). Blue/amber rather than green/red: it survives the common
    # colour-vision deficiencies, and the words carry the meaning anyway — the
    # glyph is redundancy, never the only signal.
    _DIALOG_MARK_SOURCE = "🔵"
    _DIALOG_MARK_DEST = "🟠"

    # Find-&-Copy relocate combobox items (no "rewrite" — the operation
    # always relocates files; the only choice is copy vs. move).
    _FIND_MODE_ITEMS = (
        ("Copy", "copy"),
        ("Move", "move"),
    )

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._refresh_pending = False
        self._footer_controller = self._create_footer_controller()
        self._previous_paths = {}  # node_name -> path before last in-session repath
        self._browse_in_progress = False  # re-entry guard
        # Find & Copy is a tool window that stays open while it works, so the
        # panel is KEPT: reopening it re-seeds the rows from live scene state
        # rather than throwing away the log the user is still reading and the
        # size they set. ``_find_copy_nodes`` is the scope that panel is
        # currently pointed at, captured when it opens (the row selection can
        # change behind a modeless window; the button that names a count must
        # not disagree with what it relocates).
        self._find_copy_panel = None
        self._find_copy_nodes = []
        self._find_copy_mode = "copy"
        self._find_copy_scope_label = ""
        # Lightmap dependencies: the baked maps the bake markers name, which
        # no file node references. ``_lightmap_rows`` maps a row's path text
        # to its dependency record (rebuilt with the table); the Find & Copy
        # scope carries its own list, captured together with the file nodes.
        self._lightmap_rows = {}
        self._find_copy_lightmaps = []
        # Set for the duration of a panel-driven command; ``_log`` routes the
        # report into that panel's pane instead of Maya's channels.
        self._active_logger = None

    # ------------------------------------------------------------------
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Initialize the header menu.

        Plain action items are QPushButtons wired via ``clicked.connect``.
        The two items with per-button option-box flyouts (Normalize Paths,
        Resolve Missing Textures) are uitk ``PushButton`` (``tb_*``)
        auto-wired by name; their flyout contents are populated by matching
        ``_init`` methods.
        """
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh_texture_table)

        widget.menu.add("Separator", setTitle="General")
        btn_open_si = widget.menu.add(
            "QPushButton",
            setText="Open Source Images",
            setObjectName="btn_open_source_images",
            setToolTip="Open the project's sourceimages directory in the file explorer.",
        )
        btn_open_si.clicked.connect(self.open_source_images)

        btn_reload = widget.menu.add(
            "QPushButton",
            setText="Reload Scene Textures",
            setObjectName="btn_reload_scene_textures",
            setToolTip=(
                "Force Maya to re-read every scene texture from disk "
                "(file / aiImage / pxrTexture / imagePlane). Useful after "
                "editing textures externally or after Find & Copy / Normalize "
                "Paths relocates them."
            ),
        )
        btn_reload.clicked.connect(self.reload_scene_textures)

        chk_truncate = widget.menu.add(
            "QCheckBox",
            setText="Truncate Texture Paths",
            setObjectName="chk_truncate_paths",
            setChecked=False,
            setToolTip=(
                "Shorten long paths in the Texture Path column by dropping "
                "whole middle folders — the drive and its first directories "
                "stay readable at the front, the filename at the back.\n"
                "Display only — the cell still holds the full path, so edits, "
                "path commands and the tooltip are unaffected."
            ),
        )
        chk_truncate.toggled.connect(lambda *_: self._apply_path_truncation())

        chk_warn_len = widget.menu.add(
            "QCheckBox",
            setText="Warn On Over-Long Paths",
            setObjectName="chk_warn_path_length",
            setChecked=True,
            setToolTip=(
                "Flag rows whose resolved path is longer than this OS accepts "
                f"({FileUtils.path_length_limit()} characters).\n"
                "Over-long paths fail late and opaquely — a texture the FBX "
                "plug-in silently cannot embed, a copy that reports success "
                "and produced nothing — and a path that fits here still breaks "
                "on a machine without long paths enabled (260 characters)."
            ),
        )
        chk_warn_len.toggled.connect(lambda *_: self.refresh_texture_table())

        chk_exc_arnold = widget.menu.add(
            "QCheckBox",
            setText="Exclude Arnold Nodes",
            setObjectName="chk_exclude_arnold",
            setChecked=False,
            setToolTip=(
                "Hide rows whose texture is used only by an Arnold shader.\n"
                "An Arnold preview shader (Materials ▸ Arnold Preview Shader) "
                "owns a dedicated file node per texture, so every bridged "
                "material contributes a duplicate row for the same image.\n"
                "A texture shared with a non-Arnold shader is always shown."
            ),
        )
        chk_exc_arnold.toggled.connect(lambda *_: self.refresh_texture_table())

        chk_lightmaps = widget.menu.add(
            "QCheckBox",
            setText="Show Lightmap Dependencies",
            setObjectName="chk_show_lightmaps",
            setChecked=True,
            setToolTip=(
                "List the baked lightmaps the scene's bake markers name.\n"
                "A committed lightmap is a texture dependency with no file "
                "node: the marker records the map and the folder it was baked "
                "into, and that folder goes stale when the project is "
                "reorganised or the scene is migrated — the export then ships "
                "unlit. Rows read red when the map is nowhere on disk and "
                "amber when it was found somewhere other than the recorded "
                "folder.\n"
                "Find & Copy relocates them with the textures and rewrites "
                "the markers; Normalize Paths / Make Paths Absolute re-spell "
                "the recorded folder (workspace-relative inside the project, "
                "so a teammate's copy on another drive resolves it); Select "
                "Broken Paths, Browse for File and a typed path apply too. "
                "Set Directory and Resolve Missing are file-node only."
            ),
        )
        chk_lightmaps.toggled.connect(lambda *_: self.refresh_texture_table())

        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Set Directory…",
            setObjectName="tb_set_texture_directory",
            setToolTip=(
                "Repath every (selected, or all) file node so its texture lives "
                "under the chosen directory. Subdirectories are flattened. Paths "
                "become relative when the chosen directory is inside sourceimages."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Find && Copy Textures…",
            setObjectName="tb_find_and_copy_textures",
            setToolTip=self.sb.tooltip.fmt(
                title="Find &amp; Copy Textures",
                body="Gather the textures used by (selected, or all) file "
                "nodes, relocate them into one destination, and repath. Paths "
                "become relative when the destination is inside sourceimages.",
                bullets=[
                    "Opens a panel with both folders on screen at once — "
                    "<b>Search in</b> and <b>Copy into</b>, each labelled — so "
                    "there is no order to remember and nothing to mistake one "
                    "for the other. Copy vs Move lives there too.",
                    "A path that already resolves is its own source, so the "
                    "search folder is only used for what is unresolved — "
                    "leave it empty and only the resolving paths relocate.",
                    "The panel stays open and reports into its own log, so a "
                    "second run with one value changed is one click away.",
                ],
                notes=[
                    "Lightmap dependencies (rows the header toggle shows) ride "
                    "along: searched, copied and their bake markers repointed.",
                    "Arnold texture nodes are not supported.",
                ],
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Normalize Paths",
            setObjectName="tb_normalize_paths",
            setToolTip=(
                "Rewrite (selected, or all) absolute paths inside the project "
                "to relative. A texture under sourceimages becomes relative to "
                "it — the one form Maya keeps: a path it can resolve against "
                "the project root is expanded back to absolute on the next "
                "open and saved that way. UDIM tokens are preserved."
            ),
        )
        btn_make_abs = widget.menu.add(
            "QPushButton",
            setText="Make Paths Absolute",
            setObjectName="btn_make_paths_absolute",
            setToolTip=(
                "Rewrite (selected, or all) relative paths to absolute, "
                "resolved against the project root. Inverse of Normalize "
                "Paths — needed e.g. for FBX Embed Media, which cannot "
                "consume relative paths."
            ),
        )
        btn_make_abs.clicked.connect(self.make_paths_absolute)
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Resolve Missing Textures",
            setObjectName="tb_resolve_missing_textures",
            setToolTip=(
                "Search sourceimages (recursively, all subfolders) for "
                "replacement files for missing (selected, or all) textures. "
                "Enabled strategies run in order: Stem → Texture → Fuzzy "
                "(safest first); stops at first hit."
            ),
        )

        widget.menu.add("Separator", setTitle="Selection")
        btn_sel_for_obj = widget.menu.add(
            "QPushButton",
            setText="Select Textures for Selected Objects",
            setObjectName="btn_select_textures_for_objects",
            setToolTip=(
                "Highlight the texture-path cells for textures used by the "
                "currently selected scene objects."
            ),
        )
        btn_sel_for_obj.clicked.connect(self.select_textures_for_objects)

        btn_sel_broken = widget.menu.add(
            "QPushButton",
            setText="Select Broken Paths",
            setObjectName="btn_select_broken_paths",
            setToolTip="Highlight rows whose texture file is missing.",
        )
        btn_sel_broken.clicked.connect(self.select_broken_paths)

        btn_sel_abs = widget.menu.add(
            "QPushButton",
            setText="Select Absolute Paths",
            setObjectName="btn_select_absolute_paths",
            setToolTip=(
                "Highlight rows whose path is absolute (regardless of validity). "
                "These are candidates for Normalize Paths."
            ),
        )
        btn_sel_abs.clicked.connect(self.select_absolute_paths)

        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Texture Path Editor",
                body="Inspect and fix file-node texture paths. Path commands "
                "operate on selected rows if any, otherwise on all file "
                "nodes in the scene.",
                sections=[
                    (
                        "Path management (header menu)",
                        [
                            "<b>Set Directory…</b> — repath to a chosen folder "
                            "(subdirs flatten). Option box (▸) chooses leave / "
                            "copy / move.",
                            "<b>Find &amp; Copy Textures…</b> — gather every "
                            "texture the file nodes use and relocate them into one "
                            "destination. Opens a panel carrying every option: "
                            "Copy / Move, the folder to search, the folder to "
                            "write into, and whether to search for every texture "
                            "or only the unresolved ones.",
                            "<b>Normalize Paths</b> — rewrite absolute paths "
                            "inside the project to relative. Option box (▸) "
                            "controls textures outside it: leave / copy / move "
                            "into sourceimages.",
                            "<b>Make Paths Absolute</b> — rewrite relative paths "
                            "to absolute (resolved against the project root). "
                            "Inverse of Normalize Paths.",
                            "<b>Resolve Missing Textures</b> — search sourceimages "
                            "using strategy cascade <i>Stem → Texture → Fuzzy</i> "
                            "(safest first; stops at first hit). Option box (▸) "
                            "enables/disables individual strategies.",
                        ],
                    ),
                    (
                        "General (header menu)",
                        [
                            "<b>Open Source Images</b> — Explorer shortcut.",
                            "<b>Reload Scene Textures</b> — force Maya to re-read "
                            "all textures from disk (useful after relocations).",
                            "<b>Truncate Texture Paths</b> — shorten the path "
                            "column's display by dropping whole middle folders "
                            "(drive and filename stay readable). The cell keeps "
                            "the full path (edits, commands and the tooltip "
                            "always use it).",
                            "<b>Exclude Arnold Nodes</b> — hide rows whose texture "
                            "is used only by an Arnold shader (a preview shader "
                            "owns a duplicate file node per texture). Also narrows "
                            "the <i>all</i> scope, so path commands skip them too.",
                            "<b>Show Lightmap Dependencies</b> — rows for the baked "
                            "maps the bake markers name (no file node). Red = "
                            "nowhere on disk; amber = found outside the recorded "
                            "folder. Find &amp; Copy relocates them and rewrites "
                            "the markers; Normalize Paths / Make Paths Absolute "
                            "re-spell the recorded folder; hiding them also keeps "
                            "them out of the <i>all</i> scope.",
                        ],
                    ),
                    (
                        "Selection helpers (header menu)",
                        [
                            "<b>Select Textures for Selected Objects</b> — "
                            "highlight rows for textures used by the current "
                            "scene selection.",
                            "<b>Select Broken Paths</b> — rows whose file is "
                            "missing on disk.",
                            "<b>Select Absolute Paths</b> — rows with absolute "
                            "paths (candidates for Normalize Paths).",
                        ],
                    ),
                ],
                notes=[
                    "Find &amp; Copy runs inside its own panel and reports "
                    "there — the pane at the bottom is the whole record of what "
                    "it found, relocated and repathed.",
                    "<b>Right-click</b> any row for per-texture actions: "
                    "Browse for File, scene selection, Hypershade graph, "
                    "delete.",
                    "Collision policy on Copy / Move: same-name + same-size "
                    "files rebind without overwriting; different-size hits "
                    "skip with a warning (never silently rebinds to a wrong "
                    "texture, never destroys the external).",
                ],
            )
        )

    def tb_set_texture_directory_init(self, widget):
        """Populate the Set Directory option-box with the relocate-mode combobox."""
        widget.option_box.menu.setTitle("Set Directory")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_relocate_mode",
            setToolTip=(
                "Behavior for texture files when the directory changes:\n\n"
                "• Leave in place — only rewrite the file-node path.\n"
                "• Copy — duplicate each texture into the chosen directory.\n"
                "• Move — relocate each texture into the chosen directory.\n\n"
                "Collision policy: same-name + same-size at destination is a "
                "safe rebind (no overwrite). Different size is skipped + "
                "warned — never silently rebind to a wrong texture."
            ),
            addItems=[label for label, _key in self._RELOCATE_MODE_ITEMS],
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setObjectName="chk_allow_missing",
            setText="Allow Missing Targets",
            setChecked=False,
            setToolTip=(
                "Repath a file node even when the chosen directory holds no "
                "texture of that name.\n\nOff (the default), such nodes keep "
                "the path they have: rewriting them would only spell the "
                "breakage differently. Tick it to point a batch at a folder "
                "you are about to fill."
            ),
        )

    def tb_normalize_paths_init(self, widget):
        """Populate the Normalize Paths option-box with the external-mode combobox."""
        widget.option_box.menu.setTitle("Normalize Paths")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_external_mode",
            setToolTip=(
                "Behavior for external textures (absolute paths outside the "
                "project) whose file exists on disk:\n\n"
                "• Leave untouched — only rewrite paths already inside the "
                "project.\n"
                "• Copy to sourceimages — duplicate the file in (a UDIM set's "
                "every tile), then rebind.\n"
                "• Move to sourceimages — relocate the file in, then rebind.\n\n"
                "Collision policy: a same-named sourceimages file is reused "
                "only when its content provably matches; a different file "
                "under that name is staged alongside it as an _N variant, "
                "loudly — never silently rebind to a wrong texture, never "
                "overwrite."
            ),
            addItems=[label for label, _key in self._NORMALIZE_MODE_ITEMS],
        )

    def tb_resolve_missing_textures_init(self, widget):
        """Populate the Resolve Missing option-box with the strategy checkboxes."""
        widget.option_box.menu.setTitle("Resolve Missing Textures")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Stem  — exact name, different extension",
            setObjectName="chk_stem",
            setChecked=True,
            setToolTip=(
                "Match files in sourceimages whose stem equals the missing "
                "texture's stem (extension may differ)."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Texture  — same map type + base name (safest fuzzy)",
            setObjectName="chk_texture",
            setChecked=True,
            setToolTip=(
                "Restrict candidates to files of the same map type "
                "(AO/DIFF/NORM/SPEC/…) and fuzzy-match on the map-stripped "
                "base name."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Fuzzy  — similar name (loose; may mismatch)",
            setObjectName="chk_fuzzy",
            setChecked=True,
            setToolTip=(
                "Loose name matching across all candidates. May mismatch on "
                "map-type boundaries."
            ),
        )

    # ------------------------------------------------------------------
    # Table context menu
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton",
                setText="Browse for File...",
                setObjectName="row_browse_for_file",
                setToolTip=(
                    "Open a file browser and pick a texture file to repath this "
                    "row to. Single selection only."
                ),
            )

            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton",
                setText="Select In Scene",
                setObjectName="select_material",
                setToolTip=(
                    "Select all scene objects currently assigned to this material."
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setToolTip="Select the file node in Maya.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show in Hypershade",
                setObjectName="row_show_in_hypershade",
                setToolTip="Graph the selected file node in the Hypershade editor.",
            )

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Delete File Node",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected file node from Maya.",
            )

            def _bind_menu_action(action_name, method, columns=None):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=columns or self._ROW_SELECTION_COLUMNS,
                )

            _bind_menu_action("row_browse_for_file", self.row_browse_for_file)
            _bind_menu_action("select_material", self.select_material)
            _bind_menu_action("select_file_node", self.select_file_node)
            _bind_menu_action("row_show_in_hypershade", self.row_show_in_hypershade)
            _bind_menu_action("delete_file_node", self.delete_file_node)

            self._setup_scene_change_callback(widget)

        self._refresh_table_content(widget)

    # ------------------------------------------------------------------
    # Smart scope
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Path validity — one token-aware rule for the whole panel
    # ------------------------------------------------------------------

    @staticmethod
    def _texture_on_disk(path: str) -> bool:
        """Is there a file behind *path*? Tile/frame tokens resolved first.

        Every "does this texture exist?" in this panel goes through here.
        ``os.path.exists`` is the wrong question for a stored ``.ftn`` value:
        a ``rock.<UDIM>.png`` names no literal file, so a perfectly good tile
        set painted red in the table, was swept up by Select Broken Paths, and
        was fed to Resolve Missing Textures — which repaths, so a healthy set
        could be rebound to whichever single file matched its stem.

        It asks ``MatUtils.texture_tiles`` and not the ``1001``-probing
        ``_texture_exists``: the probe is pinned to the exporter's
        representative tile, so a set running 1002-1005 reads as missing
        through it — and this same verdict gates Set Texture Directory's
        repath, which would then leave a node behind on tiles it had just
        copied.

        Parameters:
            path: An ABSOLUTE path — resolve a stored value through
                ``MatUtils.to_absolute`` first.
        """
        return bool(MatUtils.texture_tiles(path))

    # ------------------------------------------------------------------
    # Menu state
    # ------------------------------------------------------------------

    @staticmethod
    def _menu_flag(menu, name: str, default: bool) -> bool:
        """State of a checkbox on ``menu``; ``default`` when it isn't there.

        Every toggle in this panel is read from a menu that may not exist yet —
        a refresh can fire before ``header_init`` builds it, and a workflow can
        be driven without an option box at all (programmatic calls, tests). One
        lookup for all of them, so the per-toggle readers carry only the thing
        that actually differs: the default.
        """
        chk = getattr(menu, name, None) if menu is not None else None
        try:
            return bool(chk.isChecked())
        except AttributeError:
            return default

    def _header_menu(self):
        """The header's menu, or None while it is still unbuilt."""
        return getattr(getattr(self.ui, "header", None), "menu", None)

    def _exclude_arnold_pattern(self):
        """Classification pattern behind the header's "Exclude Arnold Nodes" toggle.

        Returns None (no filtering) when the toggle is off or the header menu
        hasn't been built yet, so an early refresh is safe.
        """
        on = self._menu_flag(self._header_menu(), "chk_exclude_arnold", False)
        return "rendernode/arnold*" if on else None

    def _get_scope_nodes(self):
        """Return (nodes, scope_label).

        Selection-aware: returns selected rows' file nodes if any, otherwise
        all file nodes in the scene. ``scope_label`` is a human-readable
        descriptor used in dialog titles and info logs.

        The "all" scope honors the header's Exclude Arnold Nodes toggle, so a
        path command never touches rows the panel is hiding.

        Distinguishes "no selection" (fall through to all) from "selection
        with no valid file nodes" (warn + return empty) so a user with a
        broken selection isn't silently escalated to scene-wide scope.
        """
        contexts = self._get_selected_contexts(
            warn_on_empty=False,
            require_file_nodes=False,
        )
        if contexts:
            selected_nodes = []
            for ctx in contexts:
                selected_nodes.extend(ctx.get("file_nodes") or [])
            selected_nodes = list(dict.fromkeys(selected_nodes))
            if selected_nodes:
                return selected_nodes, f"{len(selected_nodes)} selected row(s)"
            if any(ctx.get("lightmap") for ctx in contexts):
                # A lightmap-only selection is a valid scope for the commands
                # that take one (Find & Copy); the file-node commands get an
                # empty list and say "nothing to do" themselves.
                return [], f"{len(contexts)} selected lightmap row(s)"
            cmds.warning("Selected row(s) contain no valid file nodes; nothing to do.")
            return [], "selected (no valid file nodes)"

        exc_classification = self._exclude_arnold_pattern()
        if exc_classification:
            all_nodes = MatUtils.get_file_nodes(
                return_type="fileNode", exc_classification=exc_classification
            )
        else:
            all_nodes = cmds.ls(type="file") or []
        return all_nodes, f"all {len(all_nodes)} file node(s)"

    # ------------------------------------------------------------------
    # Lightmap dependencies -- rows with no file node behind them
    # ------------------------------------------------------------------
    #
    # A committed lightmap is referenced by a bake marker (map basename + the
    # folder it was baked into), never by a file node, so every file-node
    # command here was blind to it: a migration that copied every texture
    # left the EXRs behind and the export shipped unlit. The engine is
    # ``LightmapBaker`` (list / heal / relocate + repoint); the panel shows the
    # records as rows and hands the relocation half to Find & Copy.

    @staticmethod
    def _lightmap_baker():
        """The lightmap engine, imported on use -- it drags the Arnold texture
        baker in, which a panel listing file nodes should not pay for."""
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker()

    def _show_lightmaps_enabled(self) -> bool:
        """The header's "Show Lightmap Dependencies" toggle (default on)."""
        return self._menu_flag(self._header_menu(), "chk_show_lightmaps", True)

    def _lightmap_dependencies(self):
        """The scene's lightmap dependencies, or ``[]`` when the toggle hides
        them, the scene has none, or the engine cannot list them (a listing
        must never cost the table)."""
        if not self._show_lightmaps_enabled():
            return []
        try:
            return self._lightmap_baker().lightmap_dependencies()
        except Exception as e:  # noqa: BLE001
            cmds.warning(f"Lightmap dependencies not listed: {e}")
            return []

    @staticmethod
    def _lightmap_row_path(dep) -> str:
        """The path a lightmap row shows: the marker's recorded folder + map.

        The STORED value, as the file-node rows show theirs -- where the map
        was actually found, when that is elsewhere, is the tooltip's job.
        """
        folder = str(dep.get("dir") or "").replace("\\", "/").rstrip("/")
        return f"{folder}/{dep['map']}" if folder else dep["map"]

    def _lightmap_row_labels(self, dep):
        """``(shader, node)`` labels for a lightmap row, read like a texture row.

        Shader column: the material(s) the lightmapped objects wear -- an
        atlas by material is named after its material, and a per-object map
        still belongs to one. Node column: the map's stem, what a file node
        for it would be called (``OFFICE_ENV_LightMap``), since none exists.
        The objects themselves are the tooltip's: a row reading
        ``BASEBOARD_A (+45)`` identified nothing (reported 2026-08-26).
        """
        try:
            mats = MatUtils.get_mats(
                [o for o in dep.get("objects") or [] if cmds.objExists(o)]
            )
        except Exception:  # noqa: BLE001 — a label must never cost the table
            mats = []
        mats = list(dict.fromkeys(str(m) for m in mats))
        shader = ", ".join(mats[:2])
        if len(mats) > 2:
            shader += f" (+{len(mats) - 2})"
        return shader or self._LIGHTMAP_ROW_LABEL, os.path.splitext(dep["map"])[0]

    def _get_scope_lightmaps(self):
        """Lightmap dependencies in scope, for the commands that take them.

        Mirrors :meth:`_get_scope_nodes`: the selected lightmap rows when the
        selection holds any (a selection of file-node rows alone takes no
        lightmaps along), otherwise every lightmap row the table shows -- so
        the header toggle that hides them also keeps them out of an "all"
        scope.
        """
        contexts = self._get_selected_contexts(
            warn_on_empty=False, require_file_nodes=False
        )
        if contexts:
            return [ctx["lightmap"] for ctx in contexts if ctx.get("lightmap")]
        return list(self._lightmap_rows.values())

    def _normalize_lightmaps(self, lightmaps, relative: bool) -> int:
        """Re-spell the scoped lightmap markers' folders (Normalize / Make Absolute).

        Files never move: inside the project the recorded folder becomes
        workspace-relative (``relative=True``) -- what lets a teammate's copy
        of the project, mounted on another drive, still resolve it -- or is
        expanded to absolute (``relative=False``). Reports through the panel's
        channels; a scope with no lightmaps is a silent no-op.
        """
        if not lightmaps:
            return 0
        try:
            count = self._lightmap_baker().normalize_lightmap_paths(
                self._lightmap_objects(lightmaps), relative=relative
            )
        except Exception as e:  # noqa: BLE001 — the texture half already ran
            cmds.warning(f"Lightmap folders not rewritten: {e}")
            return 0
        if count:
            om.MGlobal.displayInfo(
                f"{count} lightmap marker(s) now record their folder "
                f"{'relative to the project' if relative else 'as an absolute path'}."
            )
        return count

    def _repath_lightmap(self, dep, folder: str) -> bool:
        """Point every marker naming *dep*'s map at *folder*, then refresh.

        The manual counterpart of Find & Copy's relocation (Browse for File
        and a typed path on a lightmap row): files are not touched, the
        markers' recorded folder changes and the FBX manifest is republished.
        """
        folder = str(folder or "").replace("\\", "/").rstrip("/")
        if not folder:
            cmds.warning("A lightmap path needs a folder.")
            return False
        try:
            count = self._lightmap_baker().repath_lightmaps(
                {dep["map"].lower(): folder}, dep.get("objects")
            )
        except Exception as e:  # noqa: BLE001
            cmds.warning(f"Failed to repath lightmap {dep['map']}: {e}")
            return False
        om.MGlobal.displayInfo(
            f"{dep['map']}: lightmap folder -> '{folder}' ({count} marker(s))"
        )
        return True

    # ------------------------------------------------------------------
    # Header slots — General
    # ------------------------------------------------------------------

    def open_source_images(self):
        """Open the project's sourceimages directory."""
        path = EnvUtils.get_env_info("sourceimages")
        if path and os.path.exists(path):
            FileUtils.open_explorer(path)
        else:
            cmds.warning(f"Source images directory not found: {path}")

    def reload_scene_textures(self):
        """Force Maya to re-read all scene textures from disk."""
        MatUtils.reload_textures(refresh_viewport=True)
        om.MGlobal.displayInfo("Reloaded scene textures from disk.")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------
    # Header slots — Path Management
    # ------------------------------------------------------------------

    def tb_set_texture_directory(self, widget=None):
        """Repath file nodes (selection or all) under a chosen directory.

        The option-box combobox selects whether files are also relocated to
        the new directory (copy / move) or only the path attribute changes
        (rewrite, default).
        """
        nodes, scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return

        relocate_mode = self._read_relocate_mode(widget, self._RELOCATE_MODE_ITEMS)

        # Surface the active mode in the dialog title — last interaction before
        # any file ops fire. Matches the dynamic-text intent in Find & Copy.
        mode_hint = {
            "rewrite": "path only",
            "copy": "copy files",
            "move": "move files",
        }.get(relocate_mode, relocate_mode)

        # Same marker as Find & Copy's destination picker: this is the panel's
        # third directory dialog and it is a TARGET too, so it must not read as
        # "pick a folder to look in".
        target_dir = self.sb.dir_dialog(
            title=(
                f"{self._DIALOG_MARK_DEST} DESTINATION — Set Texture Directory "
                f"({mode_hint}) for {scope_label}"
            ),
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not target_dir:
            return

        om.MGlobal.displayInfo(
            f"Setting texture paths to: {target_dir} (mode: {relocate_mode})"
        )
        count = self._set_texture_dir_flat(
            nodes,
            target_dir,
            relocate_mode=relocate_mode,
            allow_missing=self._read_option_flag(widget, "chk_allow_missing", False),
        )
        om.MGlobal.displayInfo(f"Updated {count}/{len(nodes)} file nodes.")
        self.ui.tbl000.init_slot()

    def tb_find_and_copy_textures(self, widget=None):
        """Open the Find & Copy panel over the current scope.

        Every option lives on that one panel, so there is no option box: the
        mode, the search folder and the destination are read together, next to
        each other, at the moment they are used — and the panel stays up while
        it works, so what it did is on screen instead of in a script editor.
        """
        nodes, scope_label = self._get_scope_nodes()
        lightmaps = self._get_scope_lightmaps()
        if not nodes and not lightmaps:
            cmds.warning("No file nodes to process.")
            return
        if lightmaps:
            scope_label = (f"{scope_label} + " if nodes else "") + (
                f"{len(lightmaps)} lightmap(s)"
            )
        self._find_and_copy_workflow(
            nodes, scope_label=scope_label, lightmaps=lightmaps
        )

    @classmethod
    def _read_option_flag(cls, button, name: str, default: bool) -> bool:
        """State of a checkbox in ``button``'s option box; ``default`` if absent."""
        return cls._menu_flag(
            getattr(getattr(button, "option_box", None), "menu", None), name, default
        )

    def _read_relocate_mode(self, button, mode_items) -> str:
        """Read a relocate combobox (``cmb_relocate_mode``) via index lookup."""
        combo = button.option_box.menu.cmb_relocate_mode
        idx = combo.currentIndex()
        if 0 <= idx < len(mode_items):
            return mode_items[idx][1]
        return mode_items[0][1]  # safe default

    def tb_normalize_paths(self, widget=None):
        """Rewrite paths inside the project to relative.

        External-mode is read from this button's own option_box combobox.
        ``widget`` is the button itself, passed by the switchboard auto-wire.
        """
        nodes, _scope_label = self._get_scope_nodes()
        lightmaps = self._get_scope_lightmaps()
        if not nodes and not lightmaps:
            cmds.warning("No file nodes to process.")
            return

        if nodes:
            external_mode = self._read_normalize_external_mode(widget)
            self._normalize_to_relative(nodes, external_mode=external_mode)
        # Lightmap rows: the marker's recorded folder takes the same portable
        # spelling (workspace-relative inside the project). Files never move.
        self._normalize_lightmaps(lightmaps, relative=True)
        self.ui.tbl000.init_slot()

    def _read_normalize_external_mode(self, button) -> str:
        """Read the Normalize Paths external-mode combobox via index lookup."""
        combo = button.option_box.menu.cmb_external_mode
        idx = combo.currentIndex()
        if 0 <= idx < len(self._NORMALIZE_MODE_ITEMS):
            return self._NORMALIZE_MODE_ITEMS[idx][1]
        return self._NORMALIZE_MODE_ITEMS[0][1]  # safe default

    def make_paths_absolute(self):
        """Rewrite relative paths (selection or all) to absolute."""
        nodes, _scope_label = self._get_scope_nodes()
        lightmaps = self._get_scope_lightmaps()
        if not nodes and not lightmaps:
            cmds.warning("No file nodes to process.")
            return
        if nodes:
            self._make_paths_absolute(nodes)
        self._normalize_lightmaps(lightmaps, relative=False)
        self.ui.tbl000.init_slot()

    def _make_paths_absolute(self, file_nodes) -> None:
        """Rewrite relative texture paths to absolute — inverse of Normalize Paths.

        Each relative path is resolved against the project root (the same
        rule Maya and the table's validity check use) and written back
        absolute. Pure path rewrite — no files move; UDIM tokens live in
        the basename, so they survive the prefix join. Missing files are
        still rewritten (the absolute form points where Maya would have
        looked); Resolve Missing Textures is the command for finding them.

        "Relative" is judged on the value with environment variables
        EXPANDED, so a ``$TEXDIR/foo.png`` counts as already absolute and is
        left intact. Judged on the raw string it read as relative and was
        rewritten to ``<proj>/$TEXDIR/foo.png`` — the variable pasted under
        the project, resolving nowhere, and re-running could not undo it.
        """
        workspace = EnvUtils.get_env_info("workspace") or ""
        if not workspace:
            cmds.warning("Project workspace not set; cannot resolve relative paths.")
            return
        source_images = self._resolve_source_images_path()

        rewritten = 0
        already_absolute = 0
        cmds.undoInfo(openChunk=True, chunkName="Make Texture Paths Absolute")
        try:
            for node in [str(n) for n in file_nodes]:
                try:
                    path = cmds.getAttr(f"{node}.fileTextureName") or ""
                except Exception:
                    continue
                if not path:
                    continue
                expanded = os.path.expandvars(path)
                if os.path.isabs(expanded) or os.path.splitdrive(expanded)[0]:
                    already_absolute += 1
                    continue
                new_path = MatUtils.to_absolute(path, workspace, source_images)
                try:
                    Attributes.set_plug_literal(f"{node}.fileTextureName", new_path)
                    self._previous_paths[node] = path
                    rewritten += 1
                except Exception as e:
                    cmds.warning(f"{node}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        om.MGlobal.displayInfo(
            f"Make Paths Absolute — rewritten: {rewritten}; "
            f"already absolute: {already_absolute}."
        )

    def tb_resolve_missing_textures(self, widget=None):
        """Resolve missing textures with configurable cascade strategies.

        Strategy selection is read from this button's own option_box
        checkboxes. ``widget`` is the button itself, passed by auto-wire.
        """
        nodes, _scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return

        modes = self._read_resolve_modes(widget)
        if not modes:
            cmds.warning("No Resolve Missing strategies enabled in the option-box.")
            return

        self._resolve_missing_textures(modes=modes, file_nodes=nodes)

    def _read_resolve_modes(self, button):
        """Read the Resolve Missing strategy checkboxes; preserve safest-first order."""
        menu = button.option_box.menu
        attr_by_mode = {
            "stem": "chk_stem",
            "texture": "chk_texture",
            "fuzzy": "chk_fuzzy",
        }
        return [
            mode
            for mode in self._RESOLVE_STRATEGY_ORDER
            if getattr(menu, attr_by_mode[mode]).isChecked()
        ]

    # ------------------------------------------------------------------
    # Header slots — Selection
    # ------------------------------------------------------------------

    def select_textures_for_objects(self):
        """Select table rows whose textures are used by the scene selection."""
        selection = cmds.ls(sl=True, flatten=True)
        if not selection:
            self.sb.message_box("Select object(s) first.")
            return

        mats = MatUtils.get_mats(selection)
        if not mats:
            cmds.warning("No materials found on selected objects.")
            return

        target_node_names = set()
        for mat in mats:
            try:
                file_nodes = cmds.ls(cmds.listHistory(mat) or [], type="file") or []
                for fn in file_nodes:
                    target_node_names.add(fn.split("|")[-1].split(":")[-1])
            except Exception:
                pass

        if not target_node_names:
            cmds.warning("No file nodes found for selected objects.")
            return

        table = self.ui.tbl000
        table.clearSelection()
        selected_count = 0
        for row in range(table.rowCount()):
            node_data = table.item_data(row, 2)
            if not node_data:
                continue
            node_name = str(node_data).split("|")[-1].split(":")[-1]
            if node_name in target_node_names:
                path_item = table.item(row, 1)
                if path_item:
                    path_item.setSelected(True)
                    selected_count += 1
                    if selected_count == 1:
                        table.scrollToItem(path_item)
        if selected_count > 0:
            om.MGlobal.displayInfo(f"Selected {selected_count} rows in the table.")

    def select_broken_paths(self):
        """Select rows whose texture file is missing."""
        self._select_rows_by_predicate(
            # A lightmap row is broken when the engine found its map nowhere
            # (a stale-but-found hint is amber, not broken -- it ships).
            predicate=lambda path, abs_path: (
                not self._lightmap_rows[path].get("path")
                if path in self._lightmap_rows
                else not self._texture_on_disk(abs_path)
            ),
            empty_message="No broken paths found.",
            count_message="broken paths",
        )

    def select_absolute_paths(self):
        """Select rows whose path is absolute (regardless of validity)."""
        self._select_rows_by_predicate(
            # Lightmap rows are absolute by design (the marker's hint is read
            # by consumers with no workspace) and never Normalize candidates.
            predicate=lambda path, abs_path: (
                path not in self._lightmap_rows and os.path.isabs(path)
            ),
            empty_message="No absolute paths found.",
            count_message="absolute paths",
        )

    def _select_rows_by_predicate(self, predicate, empty_message, count_message):
        """Select rows whose ``(path, abs_path)`` satisfies the predicate."""
        widget = self.ui.tbl000
        source_root, source_images = self._project_roots()
        widget.clearSelection()

        selection_mode = widget.selectionMode()
        widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)
        rows_to_select = []
        try:
            for row in range(widget.rowCount()):
                item = widget.item(row, 1)
                if not item:
                    continue
                path = str(item.text()).strip()
                if not path:
                    continue
                abs_path = MatUtils.to_absolute(path, source_root, source_images)
                if predicate(path, abs_path):
                    rows_to_select.append(row)

            for row in rows_to_select:
                path_item = widget.item(row, 1)
                if path_item:
                    path_item.setSelected(True)

            if rows_to_select:
                widget.scrollToItem(widget.item(rows_to_select[0], 1))
                om.MGlobal.displayInfo(
                    f"Selected {len(rows_to_select)} {count_message}."
                )
            else:
                om.MGlobal.displayInfo(empty_message)
        finally:
            widget.setSelectionMode(selection_mode)

    # ------------------------------------------------------------------
    # Row-only context slots
    # ------------------------------------------------------------------

    def row_browse_for_file(self, selection=None):
        """Open a file dialog and repath the selected row's file node."""
        if getattr(self, "_browse_in_progress", False):
            return
        self._browse_in_progress = True
        try:
            self._do_browse_for_file(selection)
        finally:
            from qtpy.QtCore import QTimer

            QTimer.singleShot(250, lambda: setattr(self, "_browse_in_progress", False))

    def _do_browse_for_file(self, selection):
        contexts = self._get_selected_contexts(selection, require_file_nodes=False)
        lightmaps = [ctx["lightmap"] for ctx in contexts if ctx.get("lightmap")]
        if lightmaps:
            # A lightmap row: the file picked names the folder the markers
            # should record (the map itself is what the bake committed, so a
            # different basename is refused rather than silently rebound).
            if len(contexts) > 1:
                cmds.warning("Browse for File: select a single row.")
                return
            self._browse_for_lightmap(lightmaps[0])
            return

        nodes = self._file_nodes_from_selection(selection)
        if not nodes:
            return
        if len(nodes) > 1:
            cmds.warning("Browse for File: select a single row.")
            return

        node_name = nodes[0]
        sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        try:
            current = cmds.getAttr(f"{node_name}.fileTextureName") or ""
        except Exception:
            current = ""

        start_dir = sourceimages
        if current:
            workspace = EnvUtils.get_env_info("workspace") or ""
            current_dir = os.path.dirname(
                MatUtils.to_absolute(current, workspace, sourceimages)
            )
            if current_dir and os.path.isdir(current_dir):
                start_dir = current_dir

        chosen = self.sb.file_dialog(
            file_types=[
                "*.png",
                "*.jpg",
                "*.jpeg",
                "*.tga",
                "*.tif",
                "*.tiff",
                "*.exr",
                "*.hdr",
                "*.bmp",
                "*.psd",
                "*.iff",
                "*.tx",
                "*.*",
            ],
            title=f"Select texture file for {node_name}",
            start_dir=start_dir,
            filter_description="Texture Files",
            allow_multiple=False,
        )
        if not chosen:
            return

        new_path = MatUtils.to_project_relative(chosen)
        cmds.undoInfo(openChunk=True, chunkName="Browse Texture File")
        try:
            Attributes.set_plug_literal(f"{node_name}.fileTextureName", new_path)
            if current and current != new_path:
                self._previous_paths[node_name] = current
            om.MGlobal.displayInfo(f"{node_name}: '{current}' -> '{new_path}'")
        except Exception as e:
            cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)
        self.ui.tbl000.init_slot()

    def _browse_for_lightmap(self, dep) -> None:
        """Pick the file a lightmap row's markers should point at; repath them."""
        start_dir = dep.get("dir") or EnvUtils.get_env_info("sourceimages") or ""
        if dep.get("path"):
            start_dir = os.path.dirname(dep["path"])
        if not (start_dir and os.path.isdir(start_dir)):
            start_dir = EnvUtils.get_env_info("sourceimages") or ""
        chosen = self.sb.file_dialog(
            file_types=["*.exr", "*.hdr", "*.png", "*.tif", "*.tiff", "*.*"],
            title=f"Select lightmap file {dep['map']}",
            start_dir=start_dir,
            filter_description="Lightmap Files",
            allow_multiple=False,
        )
        if not chosen:
            return
        if os.path.basename(chosen).lower() != dep["map"].lower():
            cmds.warning(
                f"Browse for File: the markers name {dep['map']!r}; pick that "
                f"file (chose {os.path.basename(chosen)!r}). A different map "
                "is a re-bake, not a repath."
            )
            return
        if self._repath_lightmap(dep, os.path.dirname(chosen)):
            self.ui.tbl000.init_slot()

    def select_material(self, selection=None):
        """Select scene objects assigned to the materials of selected rows."""
        contexts = self._get_selected_contexts(selection, require_file_nodes=False)
        if not contexts:
            return

        all_assigned_objects = []
        for context in contexts:
            lightmap = context.get("lightmap")
            if lightmap is not None:
                # A lightmap row selects the objects carrying its bake markers.
                all_assigned_objects.extend(
                    o for o in lightmap.get("objects") or [] if cmds.objExists(o)
                )
                continue
            shader_name = context.get("shader_name")
            if not shader_name:
                continue
            try:
                assigned = MatUtils.find_by_mat_id(shader_name, shell=True)
                if assigned:
                    all_assigned_objects.extend(assigned)
            except Exception as e:
                print(f"Failed to query objects for '{shader_name}': {e}")

        if not all_assigned_objects:
            cmds.warning("No scene objects found for the selected materials.")
            return

        try:
            cmds.select(all_assigned_objects, r=True)
            om.MGlobal.displayInfo(f"Selected objects for {len(contexts)} material(s).")
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select objects: {str(e)}")

    def select_file_node(self, selection=None):
        """Select the file nodes from the selected rows."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_select = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_select.append(file_node)
        if not nodes_to_select:
            return

        try:
            cmds.select(nodes_to_select, r=True)
            om.MGlobal.displayInfo(f"Selected {len(nodes_to_select)} file node(s).")
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select file nodes: {str(e)}")

    def row_show_in_hypershade(self, selection=None):
        """Graph the selected file node(s) in Hypershade."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_graph = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_graph.append(file_node)
        if not nodes_to_graph:
            return
        MatUtils.graph_materials(nodes_to_graph)

    def delete_file_node(self, selection=None):
        """Delete the selected file node(s)."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_delete = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_delete.append(file_node)
        if not nodes_to_delete:
            return

        nodes_to_delete = list(set(nodes_to_delete))
        node_names = [n.split("|")[-1].split(":")[-1] for n in nodes_to_delete]

        count = len(nodes_to_delete)
        msg = f"Are you sure you want to delete {count} file node(s)?"
        if count == 1:
            msg = f"Are you sure you want to delete the file node '{node_names[0]}'?"

        reply = self.sb.message_box(msg, "Yes", "No")
        if reply == "Yes":
            try:
                cmds.delete(nodes_to_delete)
                om.MGlobal.displayInfo(f"Deleted {count} file node(s).")
                self.ui.tbl000.init_slot()
            except Exception as e:
                om.MGlobal.displayError(f"Failed to delete file nodes: {str(e)}")

    # ------------------------------------------------------------------
    # Set-Directory workflow
    # ------------------------------------------------------------------

    def _set_texture_dir_flat(
        self,
        file_nodes,
        target_dir: str,
        relocate_mode: str = "rewrite",
        allow_missing: bool = False,
    ) -> int:
        """Repath each file node so its texture lives directly under target_dir.

        ``relocate_mode`` controls disk behavior for nodes whose current path
        resolves to an existing file:
          - ``"rewrite"`` — path-only (no file movement).
          - ``"copy"`` — copy the file to ``target_dir`` then rebind.
          - ``"move"`` — move the file to ``target_dir`` then rebind.

        A node is repathed only when the texture is ACTUALLY at the
        destination afterwards — tile/frame tokens resolved, so a UDIM set
        counts as present when its tiles are. Every mode used to rewrite the
        plug regardless, which is how picking sourceimages in the default
        (path-only) mode filled the table with red rows naming files that were
        never at the destination; in copy/move it was worse, because a token
        path matched no ``os.path.exists`` and so relocated NOTHING while the
        node was repathed anyway. ``allow_missing`` restores the blind rewrite
        for the one case that wants it: pointing a batch at a folder you are
        about to fill.

        Tile sets relocate per tile (``MatUtils.texture_tiles``) — the same
        rule ``MatUtils.stage_textures_relative`` stages them by.

        Collision policy: same-name + same-size at destination is a safe
        rebind (no overwrite); different size is skipped + warned. (A size
        proxy, kept deliberately: the target here is an arbitrary flat
        directory, not the engine-managed sourceimages staging that
        ``_normalize_to_relative`` now delegates to.) Records prior paths in
        ``self._previous_paths`` so the table tooltip can show the original.
        Returns the number of file nodes actually updated.
        """
        # Derive valid modes from the combobox items — SSoT for the mode keys.
        valid_modes = {key for _label, key in self._RELOCATE_MODE_ITEMS}
        if relocate_mode not in valid_modes:
            raise ValueError(
                f"Unknown relocate_mode {relocate_mode!r}; expected one of {sorted(valid_modes)}."
            )
        if not file_nodes:
            return 0
        node_names = [str(n) for n in file_nodes]
        target_dir_norm = os.path.normpath(target_dir).replace("\\", "/")
        workspace, source_images = self._project_roots()
        to_relative = lambda p: MatUtils.to_project_relative(  # noqa: E731
            p, workspace, source_images
        )

        # Phase 1 — collect (node, old_path, new_path, source tiles, new_abs).
        plan = []
        for node_name in node_names:
            try:
                old_path = cmds.getAttr(f"{node_name}.fileTextureName") or ""
            except Exception:
                continue
            if not old_path:
                continue
            new_abs = os.path.normpath(
                os.path.join(target_dir_norm, os.path.basename(old_path))
            ).replace("\\", "/")
            new_path = to_relative(new_abs)
            if new_path == old_path:
                continue

            # The whole set, not one name: a token path denotes every tile on
            # disk, and each has to travel for the repathed pattern to resolve.
            sources = (
                MatUtils.texture_tiles(
                    MatUtils.to_absolute(old_path, workspace, source_images)
                )
                if relocate_mode != "rewrite"
                else []
            )
            plan.append((node_name, old_path, new_path, sources, new_abs))

        # Phase 2 — perform relocations outside the undo chunk (disk ops aren't undoable).
        relocated = 0
        collision_skipped = 0
        skipped_nodes = set()
        if relocate_mode in ("copy", "move"):
            import shutil

            for node_name, _old_path, _new_path, sources, new_abs in plan:
                dst_dir = os.path.dirname(new_abs)
                for src in sources:
                    dst = os.path.join(dst_dir, os.path.basename(src))
                    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(
                        os.path.abspath(dst)
                    ):
                        continue  # already at the destination — nothing to do
                    try:
                        if os.path.exists(dst):
                            try:
                                same = os.path.getsize(src) == os.path.getsize(dst)
                            except OSError:
                                same = False
                            if not same:
                                cmds.warning(
                                    f"{node_name}: '{os.path.basename(dst)}' already "
                                    f"exists at destination with different size; "
                                    f"skipping to avoid wrong-file rebind."
                                )
                                collision_skipped += 1
                                skipped_nodes.add(node_name)
                                break
                            if relocate_mode == "move":
                                try:
                                    os.remove(src)
                                except OSError as e:
                                    cmds.warning(
                                        f"{node_name}: equivalent at dst, but could "
                                        f"not remove '{src}': {e}"
                                    )
                        else:
                            os.makedirs(dst_dir, exist_ok=True)
                            if relocate_mode == "move":
                                shutil.move(src, dst)
                            else:
                                shutil.copy2(src, dst)
                        relocated += 1
                    except Exception as e:
                        cmds.warning(f"{node_name}: failed to {relocate_mode}: {e}")
                        skipped_nodes.add(node_name)
                        break

        # Phase 3 — apply path updates (undoable).
        count = 0
        missing_at_target = 0
        cmds.undoInfo(openChunk=True, chunkName="Set Texture Directory")
        try:
            for node_name, old_path, new_path, _sources, new_abs in plan:
                if node_name in skipped_nodes:
                    continue
                # The destination is what the node will READ from, so it is
                # what has to hold a file — never "did a copy run?" (a texture
                # already sitting in the target needs no copy and must still
                # be rebound to the shorter form).
                if not (allow_missing or self._texture_on_disk(new_abs)):
                    missing_at_target += 1
                    continue
                try:
                    Attributes.set_plug_literal(
                        f"{node_name}.fileTextureName", new_path
                    )
                    self._previous_paths[node_name] = old_path
                    count += 1
                except Exception as e:
                    cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        if relocate_mode != "rewrite":
            om.MGlobal.displayInfo(
                f"Set Directory — relocated: {relocated} file(s); "
                f"collision skipped: {collision_skipped}; "
                f"mode: {relocate_mode}."
            )
        if missing_at_target:
            cmds.warning(
                f"Set Directory: {missing_at_target} file node(s) left on their "
                f"current path — '{target_dir_norm}' holds no texture of that "
                "name, and repathing them would only spell the breakage "
                "differently. Use copy/move to bring the files along, or tick "
                "'Allow Missing Targets' to point them there anyway."
            )
        return count

    # ------------------------------------------------------------------
    # Find-and-Copy workflow
    # ------------------------------------------------------------------

    def _find_and_copy_workflow(
        self,
        file_nodes,
        relocate_mode: str = "copy",
        scope_label: str = "",
        lightmaps=None,
    ):
        """Open the Find & Copy panel over *file_nodes*, or re-seed and raise it.

        *lightmaps* are the lightmap dependency records in scope
        (:meth:`_get_scope_lightmaps`); they get their own opt-out row on the
        form and are relocated after the textures, by the same folders.

        The panel is kept between invocations rather than rebuilt. It is a
        tool window that stays open while the operation runs — closing and
        recreating it would throw away the report the user is reading and the
        size they set it to — but every invocation re-seeds the rows, so the
        counts and hints always describe the scope the command was just
        issued for.

        The scope is captured HERE, not re-read when Run is pressed. The row
        selection can change behind a modeless window, and a button reading
        "Copy 12 texture(s)" that relocates three is the exact class of
        mismatch this panel exists to remove. Re-issuing the command re-seeds
        it; the footer names the scope so which one is loaded is visible.
        """
        self._find_copy_nodes = [str(n) for n in file_nodes]
        self._find_copy_mode = relocate_mode
        self._find_copy_scope_label = scope_label
        self._find_copy_lightmaps = list(lightmaps or [])

        panel = self._find_copy_panel
        if panel is None:
            panel = self.sb.form_panel(
                self._find_and_copy_scope_fields(),
                title="Find & Copy Textures",
                parent=getattr(self, "ui", None),
                # Callable: the mode lives ON the form, so a fixed verb here
                # would contradict the combo the moment it is changed.
                ok_text=self._find_and_copy_ok_text,
                validate=self._validate_find_and_copy,
                help_text=self._find_and_copy_help_text(),
                on_run=self._run_find_and_copy,
                # A tool window the user sizes once and reopens all week: the
                # size it comes back at is part of being a panel rather than
                # a dialog.
                settings=self._find_copy_settings(),
            )
            self._find_copy_panel = panel
        else:
            panel.set_fields(self._find_and_copy_scope_fields())

        panel.footer.setDefaultStatusText(
            f"Scope: {scope_label}" if scope_label else ""
        )
        panel.present()
        return panel

    @staticmethod
    def _stored_path(node) -> str:
        """The node's stored ``.ftn``, or "" when the plug cannot be read.

        Shared by the source partition and the form's "what is missing" line
        so the two cannot disagree about a node whose plug has gone away
        mid-command (a referenced file node unloaded under it).
        """
        try:
            return cmds.getAttr(f"{node}.fileTextureName") or ""
        except Exception:
            return ""

    def _partition_resolved_sources(self, node_names):
        """Split nodes into ``({basename: source path}, [unresolved nodes])``.

        A node whose stored path resolves to a file on disk already *is* its
        own source: a recursive walk can only rediscover the same bytes, more
        slowly, with a form in front of it. Only what does not resolve has
        anything to find.

        A token path (``<UDIM>``, ``<uvtile>``, ``<f>``…) is never a literal
        file, so those nodes always land in ``unresolved`` and go through the
        search, which expands the token. It used to test the literal string
        ``"<udim>"``, so every other spelling fell through as a plain path;
        ``MatUtils.has_path_token`` is the one classifier now.
        """
        workspace, source_images = self._project_roots()
        resolved = {}
        unresolved = []
        for node in node_names:
            path = self._stored_path(node)
            abs_path = (
                MatUtils.to_absolute(path, workspace, source_images) if path else ""
            )
            if (
                abs_path
                and not MatUtils.has_path_token(abs_path)
                and os.path.isfile(abs_path)
            ):
                resolved.setdefault(os.path.basename(abs_path).lower(), abs_path)
            else:
                unresolved.append(node)
        return resolved, unresolved

    def _find_copy_settings(self):
        """The panel's geometry store, or None when the switchboard has none.

        ``getattr``: the slot is constructed against a real Switchboard in
        production and against a stand-in in tests, and a panel that cannot
        remember its size is a smaller loss than a tool that will not open.
        """
        store = getattr(self.sb, "settings", None)
        if store is None:
            return None
        return store.branch("find_and_copy_textures")

    def _find_and_copy_scope_fields(self):
        """Field specs for the scope the panel is currently pointed at.

        Re-partitions the nodes on every call rather than caching: reopening
        the panel after a Resolve Missing pass must not still claim the
        textures it just fixed are unresolved.
        """
        return self._find_and_copy_fields(
            self._find_copy_nodes,
            self._partition_resolved_sources(self._find_copy_nodes)[1],
            EnvUtils.get_env_info("sourceimages") or "",
            self._find_copy_mode,
            lightmaps=self._find_copy_lightmaps,
        )

    def _find_and_copy_fields(
        self,
        node_names,
        unresolved,
        sourceimages,
        relocate_mode="copy",
        lightmaps=None,
    ):
        """The Find & Copy rows: operation, search folder, destination, dry run.

        *lightmaps* (dependency records) count toward the search hint: a
        missing lightmap is as much "something to find" as a broken file
        node, and the search folder must switch on for it. They get no row
        of their own — the scope already says what this command was issued
        over, and an opt-out for what was just selected is a second place
        to answer a question already answered.

        Both folders on screen at once, each labelled and each carrying — as
        its tooltip — what will happen to it, which is the whole point and
        what two identical native pickers in a variable order could not do.

        The source row is DISABLED, not hidden, when every path already
        resolves: the reason ("nothing needs finding") is the answer to the
        question a missing row would raise, and its placeholder says the
        same where the empty field is.

        Pure: no Qt, no scene writes. The panel renders whatever this
        returns, so what the form SAYS is testable on its own.

        Returns:
            list[dict]: specs for ``sb.form_panel`` — ``mode``, ``source_dir``,
            ``dest_dir``, ``dry_run``.
        """
        # Counted from the NODES, never from ``resolved``: that dict is keyed
        # by basename, so two file nodes reading the same texture collapse into
        # one entry and the panel would promise to copy fewer than it will.
        total = len(node_names)
        resolved_count = total - len(unresolved)
        mode_labels = {key: label for label, key in self._FIND_MODE_ITEMS}
        initial_mode = mode_labels.get(relocate_mode, self._FIND_MODE_ITEMS[0][0])

        lightmaps = list(lightmaps or [])
        missing_lightmaps = [d for d in lightmaps if not d.get("path")]
        # What the search folder is FOR: the file nodes that do not resolve
        # and the lightmaps found nowhere -- one list, since one folder serves
        # both and the row must switch on for either.
        wanted = [os.path.basename(self._stored_path(n)) or str(n) for n in unresolved]
        wanted.extend(d["map"] for d in missing_lightmaps)

        # The placeholder says WHY this field would be filled, in as few words
        # as fit: a line edit elides what overruns it, and a cropped sentence
        # is worse than none. Counted off ``wanted``, never off ``total`` — a
        # lightmap-only scope has no file nodes, and a count of 0 in the one
        # line that is always on screen makes the rest of the form untrusted.
        # What leaving it empty costs is spelled out in the hint, which has
        # room for it.
        if wanted:
            listed = ", ".join(wanted[:3])
            if len(wanted) > 3:
                listed += f", +{len(wanted) - 3} more"
            skipped = "Leave it empty to skip them"
            if resolved_count:
                skipped += f" and relocate the {resolved_count} that already resolve"
            source_hint = (
                f"Searched recursively for {len(wanted)} unresolved "
                f"texture(s): {listed}. {skipped}. A path that already "
                "resolves is its own source and is never searched for — it "
                "is the file the scene is rendering."
            )
            source_placeholder = f"{len(wanted)} path(s) require a search dir"
        else:
            source_hint = (
                f"All {total} path(s) resolve — every texture is its own source, "
                "so nothing needs finding."
                if total
                else "Nothing in scope needs finding — every path already resolves."
            )
            source_placeholder = "No path requires a search dir"

        # Order is the reading order of the decision: what to do, where to
        # look, where it lands, and whether to commit.
        return [
            {
                "name": "mode",
                "kind": "choice",
                "label": "Operation",
                "items": [label for label, _key in self._FIND_MODE_ITEMS],
                "value": initial_mode,
                "hint": (
                    "Copy duplicates each texture into the destination; "
                    "Move removes the original after a successful copy."
                ),
            },
            {
                "name": "source_dir",
                "kind": "dir",
                "label": f"{self._DIALOG_MARK_SOURCE} Search in",
                "hint": source_hint,
                "placeholder": source_placeholder,
                "enabled": bool(wanted),
            },
            {
                "name": "dest_dir",
                "kind": "dir",
                "label": f"{self._DIALOG_MARK_DEST} Copy into",
                "value": sourceimages,
                "hint": (
                    f"The {total} texture(s) land HERE and the file nodes "
                    "are repointed at them. Paths become relative when "
                    "this folder is inside the project."
                ),
            },
            {
                "name": "dry_run",
                "kind": "check",
                "label": "Dry run (preview only)",
                "hint": (
                    "Report exactly what would be relocated and repathed "
                    "without touching a file or a plug, then arm <b>Apply</b> "
                    "to commit the report on screen. The preview and the "
                    "commit derive every destination path through the same "
                    "call, so what Apply writes is what was previewed."
                ),
            },
        ]

    def _find_and_copy_ok_text(self, values) -> str:
        """Accept-button text — the verb chosen ON the form, and the real count.

        Re-evaluated on every edit by the panel, so switching the Operation
        combo retitles the button that commits it — and ticking Dry Run
        retitles it to what it will actually do, which is not copy anything.
        """
        total = len(self._find_copy_nodes)
        lightmaps = len(self._find_copy_lightmaps)
        what = f"{total} texture(s)"
        if lightmaps:
            what = (
                f"{what} + {lightmaps} lightmap(s)"
                if total
                else f"{lightmaps} lightmap(s)"
            )
        if values.get("dry_run"):
            return f"Preview {what}"
        mode = values.get("mode") or self._FIND_MODE_ITEMS[0][0]
        return f"{mode} {what}"

    def _find_and_copy_help_text(self) -> str:
        """Rich text behind the panel header's ``?``."""
        return self.sb.tooltip.fmt(
            title="Find &amp; Copy Textures",
            body="Gather the textures the scoped file nodes use, relocate them "
            "into one destination, and repoint the nodes at them.",
            sections=[
                (
                    "The rows",
                    [
                        "<b>Operation</b> — Copy duplicates each texture; Move "
                        "removes the original after a successful copy.",
                        "<b>Search in</b> — searched recursively, and only for "
                        "paths that do not already resolve. Leave it empty to "
                        "skip those and relocate the rest.",
                        "<b>Copy into</b> — where the textures land. Created if "
                        "it does not exist; paths become relative when it is "
                        "inside the project.",
                        "<b>Dry run</b> — report what would happen without "
                        "touching anything, then press <b>Apply</b> in the "
                        "footer to commit exactly what was reported.",
                    ],
                ),
            ],
            notes=[
                "Lightmaps in the scope ride along — there is no row for them, "
                "because the selection that opened this panel already said so. "
                "No file node references a baked map, so this is the one "
                "command that relocates them: searched and copied like the "
                "textures, then every marker is repointed at the destination "
                "and the FBX manifest republished.",
                "Source and destination may not be the same folder — nothing "
                "would move, and the run would report success.",
                "What the search did not find is named at the end of the "
                "report; those nodes keep their current path.",
                "Collision policy: same-name + same-size files rebind without "
                "overwriting; different-size hits skip with a warning.",
            ],
        )

    def _run_find_and_copy(self, values):
        """The panel's Run — over whatever scope the panel is pointed at."""
        return self._run_find_and_copy_over(list(self._find_copy_nodes), values)

    def _run_find_and_copy_over(self, node_names, values):
        """Preview or commit *node_names*, reporting into the panel.

        Returns the call that WOULD commit when this pass was a preview, which
        is the panel's contract for arming its Apply button. That call is this
        same method with the preview switched off, over the nodes and answers
        AS THEY WERE PREVIEWED — so Apply commits the report on screen even if
        the row selection or the form has moved on since.
        """
        panel = self._find_copy_panel
        dry_run = bool(values.get("dry_run"))
        self._active_logger = getattr(panel, "logger", None)
        try:
            planned = self._execute_find_and_copy(
                node_names, values, progress_host=panel, dry_run=dry_run
            )
        finally:
            self._active_logger = None
        self.ui.tbl000.init_slot()
        if not (dry_run and planned):
            return None
        return partial(
            self._run_find_and_copy_over, node_names, dict(values, dry_run=False)
        )

    @staticmethod
    def _validate_find_and_copy(values):
        """Refuse a form that cannot do what it says, and say why.

        The reported mistake IS the first rule: aiming the destination at the
        folder being searched relocates nothing (every source is already
        there), reports success, and leaves the user believing the textures
        moved.
        """
        dest = values.get("dest_dir") or ""
        source = values.get("source_dir") or ""
        if not dest:
            return "Pick a destination — this is where the textures will land."
        if source and os.path.normcase(os.path.abspath(source)) == os.path.normcase(
            os.path.abspath(dest)
        ):
            return (
                "Search folder and destination are the same — nothing would "
                "move. The destination is where the textures LAND."
            )
        return ""

    def _execute_find_and_copy(
        self, file_nodes, answers, progress_host=None, dry_run=False
    ):
        """Collect sources, relocate them into one directory, repath the nodes.

        The answers arrive as a plain dict — from the panel, from a test, from
        anything that can name the four values — so the work is reachable and
        checkable without a window in front of it.

        ONE form, with both folders on it at the same time. It used to be two
        native directory pickers shown back to back — the same widget twice,
        one meaning "search here" and the other "write here", with the
        direction carried only by the window caption. Reported 2026-08-25:
        users pick their texture folder in the DESTINATION picker, believing
        they are answering "where do I find these". Colour-marking the
        captions (2026-08-18) did not fix it, and could not: a shell caption
        is the lowest-salience text on screen, and worse, the two option-box
        toggles that skipped a picker made the ORDER variable — with every
        path already valid the FIRST and only dialog was the destination, so
        the position the user was navigating by had moved.

        Side by side and labelled, there is nothing to tell apart and no order
        to remember; the accept button names the operation and the count, and
        source == destination is refused inline, BEFORE any file is touched.
        Both toggles are gone with the sequence that needed them: the
        destination pre-fills to sourceimages (what "Always Relocate To
        sourceimages" bought, minus the hidden state), and "search
        everything, not just what is broken" went with them — a resolving
        path is the file the scene is rendering, so the search fills the
        gaps and the empty field says what that means.

        A source row with no folder in it skips the unresolved nodes and
        relocates the rest — with 48 of 50 paths valid, those 48 must not be
        lost to the two that are broken.

        ``dry_run`` reports the same decision without acting on it: the
        search still runs (reading the disk is the only way to know what would
        move), but no folder is created, no file is relocated and no plug is
        written. Both passes derive the destination paths through the one
        :meth:`_plan_remap` call, so a preview cannot promise a path the
        commit would not write.

        Parameters:
            file_nodes: The nodes to relocate textures for.
            answers: ``source_dir``, ``dest_dir``, ``mode``, ``dry_run``.
            progress_host: Widget whose footer carries the progress bar —
                the panel running the command, so the feedback is on the
                window being watched rather than the one behind it.
            dry_run: Report the plan; change nothing.

        Returns:
            bool: whether there was anything to do — the signal the panel's
            Apply button is armed from, so a preview that found nothing
            offers nothing to commit.
        """
        node_names = [str(n) for n in file_nodes]
        source_dir = answers.get("source_dir") or ""
        dest_dir = answers.get("dest_dir") or ""
        # Normalized HERE rather than on the way out of the form: the same
        # dict arrives from the panel and from a test, and a mode read
        # straight off the combo ("Copy" / "Move") has to mean the same thing
        # on every path in.
        relocate_mode = (
            "move" if str(answers.get("mode", "")).lower() == "move" else "copy"
        )

        resolved, unresolved = self._partition_resolved_sources(node_names)
        # The lightmaps captured with this scope. No opt-out: the selection
        # that opened the panel already said what this runs over, and a row
        # asking again could only contradict it.
        lightmaps = list(self._find_copy_lightmaps)

        found_textures = []
        if unresolved and source_dir:
            # find_texture_files ticks per directory so the marquee advances
            # during the walk. The walk itself isn't interruptible
            # mid-directory, but Esc cancels via the ProgressBar filter.
            with self.sb.progress(
                progress_host or self.ui, text=f"Searching {source_dir}…"
            ) as update:
                found_textures = MatUtils.find_texture_files(
                    file_nodes=unresolved,
                    source_dir=source_dir,
                    recursive=True,
                    progress_callback=self.sb.progress_adapter(update),
                )
        elif unresolved:
            self._log(
                f"No search folder — skipping {len(unresolved)} unresolved "
                f"texture(s); continuing with {len(resolved)} valid path(s)."
            )

        if unresolved and source_dir:
            # Name what the search did NOT find, by node. Reported 2026-08-26:
            # a run that found 46 of 48 read as a success -- the counts were
            # right, but nothing said WHICH two kept their broken path -- and
            # the export then failed on textures the panel had "just copied".
            found_names = {os.path.basename(p).lower() for p in found_textures}
            not_found = [
                node
                for node in unresolved
                if not self._landed(
                    os.path.basename(self._stored_path(node)), found_names
                )
            ]
            if not_found:
                self._log(
                    f"{len(not_found)} of {len(unresolved)} unresolved "
                    f"texture(s) not found under {source_dir} — their file "
                    "nodes keep their current path",
                    "warning",
                    items=[
                        f"{node}:  "
                        f"{os.path.basename(self._stored_path(node)) or '<no path>'}"
                        for node in not_found
                    ],
                )

        if not resolved and not found_textures and not lightmaps:
            self._log("No textures found.", "warning")
            return False

        # Dedup by basename, newest mtime wins. Walks can return multiple
        # matches per target filename (versioned/archived copies, sync-client
        # conflict copies); feeding all of them into the threaded copy pool
        # would have two workers racing on the same destination — a deadlock
        # pattern on Windows against a cloud-sync client. A valid path outranks
        # every walk hit of that name: it is the file the scene is rendering.
        by_basename = {}
        for fpath in found_textures:
            bn = os.path.basename(fpath).lower()
            if bn in resolved:
                continue
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                mtime = 0.0
            existing = by_basename.get(bn)
            if existing is None or mtime > existing[0]:
                by_basename[bn] = (mtime, fpath)
        if found_textures and len(by_basename) < len(found_textures):
            self._log(
                f"{len(found_textures)} candidates found, deduped to "
                f"{len(by_basename)} unique basenames (newest wins)."
            )
        deduped = list(resolved.values()) + [v[1] for v in by_basename.values()]

        # --- Destination -----------------------------------------------------
        # Already answered, on the same form as the source. Created if
        # missing: the destination is typed as often as it is browsed, and a
        # folder that does not exist yet is a normal answer to "put them here".
        if not dry_run:
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                self._log(f"Cannot create '{dest_dir}': {e}", "error")
                return False

        # Sources already sitting in the destination need no file op — and a
        # Move would be a self-copy, which shutil rejects as SameFileError, so
        # the file drops out of the copied set and its node never repaths.
        # They skip straight to the repath, which is the half that still
        # applies: a texture already in place still normalizes to relative.
        dest_key = os.path.normcase(os.path.abspath(dest_dir))
        in_place, to_relocate = [], []
        for src in deduped:
            same_dir = (
                os.path.normcase(os.path.dirname(os.path.abspath(src))) == dest_key
            )
            (in_place if same_dir else to_relocate).append(src)

        if dry_run:
            return self._report_find_and_copy_plan(
                node_names,
                dest_dir,
                relocate_mode,
                in_place,
                to_relocate,
                lightmap_plan=self._plan_lightmaps(
                    lightmaps, source_dir, dest_dir, relocate_mode
                ),
            )

        copied = []
        if to_relocate:
            relocate_verb = "Moving" if relocate_mode == "move" else "Copying"
            with self.sb.progress(
                progress_host or self.ui, text=f"{relocate_verb} textures…"
            ) as update:
                copied = MatUtils.move_texture_files(
                    found_files=to_relocate,
                    new_dir=dest_dir,
                    delete_old=(relocate_mode == "move"),
                    progress_callback=self.sb.progress_adapter(update),
                )
            if not copied and not in_place and not lightmaps:
                self._log(f"No textures {relocate_verb.lower()}.", "warning")
                return False

        if deduped:
            self._log(
                f"Find & {relocate_mode.title()} — sources: {len(resolved)} from "
                f"valid path(s), {len(by_basename)} found by search; "
                f"{len(copied)} relocated, {len(in_place)} already at destination."
            )

        copied_basenames = {os.path.basename(dst).lower() for _src, dst in copied}
        copied_basenames.update(os.path.basename(p).lower() for p in in_place)
        remap = self._plan_remap(node_names, dest_dir, copied_basenames)

        if remap:
            cmds.undoInfo(openChunk=True, chunkName="Remap Found Textures")
            try:
                count = 0
                unchanged = 0
                for node_name, path, final_path in remap:
                    if final_path == path:
                        # Already the stored form — re-running the command over
                        # textures that never left the destination (the normal
                        # case once the destination is pinned to sourceimages)
                        # would otherwise dirty every plug, reload every
                        # texture, and report them all as remapped.
                        unchanged += 1
                        continue

                    Attributes.set_plug_literal(
                        f"{node_name}.fileTextureName", final_path
                    )
                    # Every other path command records this; the panel's
                    # "Previous:" tooltip line is built from it.
                    self._previous_paths[node_name] = path
                    count += 1
                self._log(
                    f"Remapped {count} file nodes.",
                    "success" if count else "info",
                )
                if unchanged:
                    self._log(f"{unchanged} already pointed at the destination.")
            finally:
                cmds.undoInfo(closeChunk=True)
        elif deduped:
            self._log("No file nodes matched the copied textures.", "warning")

        if lightmaps:
            self._relocate_lightmaps(lightmaps, source_dir, dest_dir, relocate_mode)

        # The honest close: what is STILL broken after this run, by node.
        # Re-partitioned from the live plugs, so a copy that failed or a
        # search that missed shows up here whatever the counts above said.
        if node_names:
            _still_ok, still_unresolved = self._partition_resolved_sources(node_names)
            if still_unresolved:
                self._log(
                    f"{len(still_unresolved)} texture(s) still unresolved after "
                    "this run — the export's path check will fail on them",
                    "warning",
                    items=[
                        f"{node}:  {self._stored_path(node) or '<no path>'}"
                        for node in still_unresolved
                    ],
                )
        return True

    def _plan_remap(self, node_names, dest_dir, basenames):
        """``[(node, stored path, final path)]`` for every node a relocation repaths.

        The ONE place a destination path is derived, so a preview cannot
        promise a path the committed pass would not write.

        The path is flattened deliberately: the files land in ``dest_dir``'s
        root, and ``MatUtils.remap_texture_paths`` would try to preserve the
        original relative depth, which no longer corresponds to disk layout.
        """
        workspace, source_images = self._project_roots()
        plan = []
        for node_name in node_names:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
            except Exception:
                continue
            if not path or not self._landed(os.path.basename(path), basenames):
                continue
            final_path = MatUtils.to_project_relative(
                os.path.join(dest_dir, os.path.basename(path)),
                workspace,
                source_images,
            )
            plan.append((node_name, path, final_path))
        return plan

    @staticmethod
    def _landed(basename: str, landed: set) -> bool:
        """Did a file for *basename* land -- literally, or as tiles of its set?

        *landed* holds lower-case basenames of the files copied or already at
        the destination. A tokened stored name (``rock.<UDIM>.png``) is never
        among them literally -- its TILES are -- so it is matched as the
        pattern the token table spells (:meth:`MatUtils.token_wildcard`). A
        plain membership test left every tiled node unrepathed after its
        tiles had been copied.
        """
        lower = basename.lower()
        if not MatUtils.has_path_token(lower):
            return lower in landed
        import fnmatch

        pattern = MatUtils.token_wildcard(lower, None).lower()
        return any(fnmatch.fnmatchcase(name, pattern) for name in landed)

    #: Rows of a dry-run listing shown in full before it collapses to a count.
    #: Long enough to recognise the operation, short enough that the plan
    #: stays one screenful next to the form that produced it.
    _PLAN_PREVIEW_ROWS = 12

    def _report_find_and_copy_plan(
        self,
        node_names,
        dest_dir,
        relocate_mode,
        in_place,
        to_relocate,
        lightmap_plan=None,
    ):
        """Report what a live pass WOULD do, having written nothing.

        Every line comes from the same values the commit acts on — the
        partitioned source lists, :meth:`_plan_remap`, and the lightmap plan
        the engine's own dry run returned — so this is the plan itself being
        described, not a second guess at it.
        """
        verb = "Move" if relocate_mode == "move" else "Copy"
        basenames = {os.path.basename(p).lower() for p in (*in_place, *to_relocate)}
        remap = self._plan_remap(node_names, dest_dir, basenames)
        changed = [row for row in remap if row[2] != row[1]]
        lightmaps_change = bool(
            lightmap_plan and (lightmap_plan["relocate"] or lightmap_plan["in_place"])
        )

        if not to_relocate and not changed and not lightmaps_change:
            self._log(
                "Dry run — nothing would change: every texture is already at "
                "the destination and every path already stored in its final "
                "form.",
                "warning",
            )
            self._report_lightmap_plan(lightmap_plan, dest_dir, verb)
            return False

        if to_relocate:
            self._log(
                f"Dry run — would {verb.lower()} {len(to_relocate)} texture(s) "
                f"into {dest_dir}"
                + (f" ({len(in_place)} already there)" if in_place else ""),
                items=self._plan_lines(
                    f"{os.path.basename(p)}  ←  {os.path.dirname(p)}"
                    for p in to_relocate
                ),
            )
        elif in_place:
            self._log(
                f"Dry run — all {len(in_place)} texture(s) are already at the "
                "destination; only the stored paths would change."
            )

        if changed:
            self._log(
                f"Would repath {len(changed)} file node(s)",
                items=self._plan_lines(
                    f"{node}:  {old}  →  {new}" for node, old, new in changed
                ),
            )
        unchanged = len(remap) - len(changed)
        if unchanged:
            self._log(f"{unchanged} node(s) already point at the destination.")

        self._report_lightmap_plan(lightmap_plan, dest_dir, verb)

        self._log(
            f"Nothing has been written — press Apply to {verb.lower()} and "
            "repath exactly this.",
            "warning",
        )
        return True

    # -- lightmaps through Find & Copy ------------------------------------
    # The engine does the work (LightmapBaker.relocate_lightmaps: search,
    # copy, repoint the markers, republish the manifest); the panel scopes it
    # to the captured records and reports through the same pane.

    @staticmethod
    def _lightmap_objects(lightmaps):
        """The transforms the records name -- the engine's scope argument."""
        return list(
            dict.fromkeys(o for dep in lightmaps for o in (dep.get("objects") or []))
        )

    def _plan_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """The engine's dry run over *lightmaps*, or ``None`` when none are in scope."""
        if not lightmaps:
            return None
        try:
            return self._lightmap_baker().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir,
                mode=relocate_mode,
                objects=self._lightmap_objects(lightmaps),
                dry_run=True,
            )
        except Exception as e:  # noqa: BLE001 — a preview must not raise
            self._log(f"Lightmaps not planned: {e}", "error")
            return None

    def _report_lightmap_plan(self, plan, dest_dir, verb) -> None:
        """The dry-run lines for the lightmaps -- same shape as the texture ones."""
        if not plan:
            return
        if plan["relocate"]:
            self._log(
                f"Dry run — would {verb.lower()} {len(plan['relocate'])} "
                f"lightmap(s) into {dest_dir} and repoint their bake markers"
                + (
                    f" ({len(plan['in_place'])} already there)"
                    if plan["in_place"]
                    else ""
                ),
                items=self._plan_lines(
                    f"{os.path.basename(src)}  ←  {os.path.dirname(src)}"
                    for src, _dst in plan["relocate"]
                ),
            )
        elif plan["in_place"]:
            self._log(
                f"Dry run — all {len(plan['in_place'])} lightmap(s) are already "
                "at the destination; only the bake markers would change."
            )
        if plan["missing"]:
            self._log(
                f"{len(plan['missing'])} lightmap(s) found nowhere — their "
                "markers would keep pointing at the recorded folder",
                "warning",
                items=[
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in plan["missing"]
                ],
            )

    def _relocate_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """Relocate *lightmaps* for real and report; returns whether any landed."""
        try:
            result = self._lightmap_baker().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir,
                mode=relocate_mode,
                objects=self._lightmap_objects(lightmaps),
            )
        except Exception as e:  # noqa: BLE001 — the textures already landed
            self._log(f"Lightmaps not relocated: {e}", "error")
            return False
        landed = len(result["copied"]) + len(result["in_place"])
        if landed:
            self._log(
                f"Lightmaps — {len(result['copied'])} relocated, "
                f"{len(result['in_place'])} already at destination; "
                f"{result['updated']} bake marker(s) repointed, manifest "
                "republished.",
                "success",
            )
        failed = len(result["relocate"]) - len(result["copied"])
        if failed:
            self._log(
                f"{failed} lightmap(s) did not copy — see the script editor.",
                "warning",
            )
        if result["missing"]:
            self._log(
                f"{len(result['missing'])} lightmap(s) found nowhere — the "
                "export's path check will fail on them",
                "warning",
                items=[
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in result["missing"]
                ],
            )
        return bool(landed)

    @classmethod
    def _plan_lines(cls, lines):
        """*lines* capped at :attr:`_PLAN_PREVIEW_ROWS`, with the remainder counted.

        A truncated listing that does not SAY it was truncated reads as the
        whole plan, which is the one thing a preview must never do.
        """
        listed = list(lines)
        if len(listed) <= cls._PLAN_PREVIEW_ROWS:
            return listed
        hidden = len(listed) - cls._PLAN_PREVIEW_ROWS
        return listed[: cls._PLAN_PREVIEW_ROWS] + [f"… and {hidden} more"]

    # ------------------------------------------------------------------
    # Normalize workflow
    # ------------------------------------------------------------------

    def _normalize_to_relative(
        self, file_nodes, external_mode: str = "rewrite"
    ) -> None:
        """Rewrite (selected) paths inside the project to relative.

        A thin driver over :meth:`MatUtils.stage_textures_relative`
        (``scope="project"``) — the same engine behind the Scene Exporter's
        Convert To Relative Paths task, so the panel and the exporter cannot
        drift on what "relative" means. This method owns only the UI-mode
        mapping, the ``_previous_paths`` bookkeeping the table tooltip reads,
        and the summary line.

        "Inside" means under the project ROOT, not under sourceimages: a
        texture already in the project is portable where it sits — it needs no
        copy to become so — and the option-box modes read on "external"
        textures only. What it is made relative TO is a separate question the
        converter owns: under sourceimages, relative to that rule (the form
        Maya keeps across a reload); elsewhere in the project, relative to the
        root (which Maya expands again on load — portable in the file it was
        saved into, not beyond it).

        Per node (engine semantics):
          - already relative → no-op, unless it is in the legacy
            ``sourceimages/…`` spelling, which is upgraded in place.
          - absolute inside the project → rewritten relative. UDIM/frame
            tokens ride the basename and survive (the engine relativizes a
            token path in place; the old local pass skipped them entirely).
          - absolute inside the project, file missing → left untouched, same
            as the external case below. Relativizing it would only spell the
            breakage differently while reporting it as a rewrite.
          - absolute outside the project, by mode:
              external_mode="rewrite" → left untouched.
              external_mode="copy"    → staged into sourceimages (a token
                                        set stages every tile), then rebound
                                        relative.
              external_mode="move"    → as copy, and the external original
                                        is removed once staged.
          - absolute outside the project, file missing → left untouched
            (Resolve Missing Textures is the command for that case).

        Collision policy (the engine's): a same-named sourceimages resident
        is reused only when its CONTENT provably matches (size + partial
        hash); a different file under the same name is staged alongside it
        under an ``_N`` index on its base name, loudly — the node is neither
        rebound to a same-named-but-different texture nor abandoned on its
        absolute path. (Replaced this method's old size-only proxy, which
        called any two same-length files "the same file" — the exact
        wrong-file rebind the check exists to prevent.)
        """
        # Derive valid modes from the combobox items — SSoT for the mode keys.
        valid_modes = {key for _label, key in self._NORMALIZE_MODE_ITEMS}
        if external_mode not in valid_modes:
            raise ValueError(
                f"Unknown external_mode {external_mode!r}; "
                f"expected one of {sorted(valid_modes)}."
            )

        nodes = [str(n) for n in file_nodes]
        before = {}
        for node in nodes:
            try:
                before[node] = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                before[node] = ""

        # The engine is ``@CoreUtils.undoable`` — one undo step, no chunk here.
        results = MatUtils.stage_textures_relative(
            nodes,
            external_mode={"rewrite": "skip"}.get(external_mode, external_mode),
            scope="project",
        )

        # Every path command feeds the table tooltip's "Previous:" line.
        for node in nodes:
            try:
                after = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            if before.get(node) and after != before[node]:
                self._previous_paths[node] = before[node]

        rewritten = already_relative = 0
        external_relocated = variant_staged = 0
        external_left = missing_left = 0
        for status in results.values():
            if status == "relativized":
                rewritten += 1
            elif status == "already-relative":
                already_relative += 1
            elif status in ("copied+relativized", "moved+relativized"):
                external_relocated += 1
            elif status == "variant+relativized":
                external_relocated += 1
                variant_staged += 1
            elif status == "skipped:external":
                external_left += 1
            elif status == "skipped:missing-source":
                missing_left += 1
            # Any other skipped:* was already warned about by the engine.

        relocate_label = {
            "rewrite": "—",
            "copy": "copied",
            "move": "moved",
        }[external_mode]
        om.MGlobal.displayInfo(
            f"Normalize Paths — rewritten: {rewritten}; "
            f"already relative: {already_relative}; "
            f"external {relocate_label}: {external_relocated}"
            + (f" ({variant_staged} as _N variants)" if variant_staged else "")
            + f"; external left as-is: {external_left}; "
            f"missing left as-is: {missing_left}."
        )

    # ------------------------------------------------------------------
    # Resolve Missing
    # ------------------------------------------------------------------

    def _strategies_for_modes(self, modes, index_stems):
        """Concatenate strategy lists for each enabled mode, dedup-preserving order.

        ``exact`` is the first tier of every mode and gets deduplicated so it
        only runs once at the head of the pipeline.
        """
        pipeline = []
        seen = set()
        for mode in modes:
            for s in self._strategies_for_mode(mode, index_stems):
                key = id(s) if callable(s) else s
                if key in seen:
                    continue
                seen.add(key)
                pipeline.append(s)
        return pipeline

    def _strategies_for_mode(self, mode: str, index_stems):
        if mode == "stem":
            return ["exact"]
        if mode == "fuzzy":
            # use_base_name is intentionally NOT in the pipeline: numbered
            # variants like texture_001 / texture_002 should not auto-fuse.
            return ["exact", "substring", "ratio"]
        if mode == "texture":
            return [
                "exact",
                self._texture_aware_strategy(index_stems),
                "substring",
                "ratio",
            ]
        raise ValueError(f"Unknown resolve mode: {mode!r}")

    def _texture_aware_strategy(self, index_stems):
        """Custom strategy: filter by map type, then fuzzy-match base name.

        For a missing ``asset01_..._ao``, this restricts candidates to other
        ``_AO`` files, so an ``_AO`` file node can never get repathed to a
        ``_DIFF`` / ``_NORM`` / ``_SPEC`` file.
        """
        candidate_meta = []
        for stem in index_stems:
            try:
                map_type = MapFactory.resolve_map_type(stem + ".png", key=True)
            except Exception:
                map_type = None
            try:
                base = ImgUtils.get_base_texture_name(stem + ".png").lower()
            except Exception:
                base = stem
            candidate_meta.append((stem, base, map_type))

        def texture(target, candidates):
            try:
                target_map = MapFactory.resolve_map_type(target + ".png", key=True)
            except Exception:
                target_map = None
            if not target_map:
                return None, 0.0, "no_match"
            try:
                target_base = ImgUtils.get_base_texture_name(target + ".png").lower()
            except Exception:
                target_base = target

            same_map_stems = []
            same_map_bases = []
            for stem, base, map_type in candidate_meta:
                if map_type == target_map:
                    same_map_stems.append(stem)
                    same_map_bases.append(base)

            if not same_map_bases:
                return None, 0.0, "no_match"

            base_match, score, status = FuzzyMatcher.find_unique_match(
                target_base,
                same_map_bases,
                score_threshold=0.5,
                ambiguity_delta=0.05,
                use_base_name=False,
                use_substring=True,
                use_prefix=False,
                use_ratio=False,
            )
            if status == "no_match":
                return None, 0.0, "no_match"
            idx = same_map_bases.index(base_match)
            return same_map_stems[idx], score, status

        return texture

    def _resolve_missing_textures(self, modes, file_nodes=None):
        """Resolve missing textures using the given strategy modes (in cascade order).

        Parameters:
            modes: List of mode names drawn from ``_RESOLVE_STRATEGY_ORDER``.
            file_nodes: Optional list of file nodes to restrict to. If None,
                       processes all ``cmds.ls(type="file")``.
        """
        if not modes:
            raise ValueError("At least one mode is required.")
        unknown = set(modes) - set(self._RESOLVE_STRATEGY_ORDER)
        if unknown:
            raise ValueError(f"Unknown resolve mode(s): {sorted(unknown)}")

        sourceimages = EnvUtils.get_env_info("sourceimages")
        if not sourceimages or not os.path.isdir(sourceimages):
            cmds.warning(f"sourceimages directory not found: {sourceimages}")
            return

        workspace = EnvUtils.get_env_info("workspace") or ""
        if file_nodes is None:
            all_file_nodes = cmds.ls(type="file") or []
        else:
            # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
            all_file_nodes = [str(n) for n in file_nodes]
        if not all_file_nodes:
            cmds.warning("No file nodes to process.")
            return

        missing = []
        for node in all_file_nodes:
            try:
                path = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            # A token path is skipped whether or not its tiles are there:
            # this command rebinds to ONE concrete file, which would flatten a
            # tile set to a single tile. It used to test the literal
            # ``"<udim>"``, so every other token spelling fell through to
            # exactly that.
            if not path or MatUtils.has_path_token(path):
                continue
            if self._texture_on_disk(
                MatUtils.to_absolute(path, workspace, sourceimages)
            ):
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem:
                missing.append((node, path, stem))

        if not missing:
            om.MGlobal.displayInfo("No missing textures to resolve.")
            return

        index = []
        for root, _, files in os.walk(sourceimages):
            for f in files:
                stem = os.path.splitext(f)[0]
                if stem:
                    index.append((stem.lower(), os.path.join(root, f)))
        if not index:
            cmds.warning("No files in sourceimages to match against.")
            return

        to_project_relative = lambda p: MatUtils.to_project_relative(  # noqa: E731
            p, workspace, sourceimages
        )
        by_stem = {}
        for stem_key, abs_path in index:
            by_stem.setdefault(stem_key, []).append(abs_path)
        index_stems = list(by_stem.keys())

        strategies = self._strategies_for_modes(modes, index_stems)

        resolved = 0
        ambiguous = 0
        no_match = 0
        cmds.undoInfo(openChunk=True, chunkName="Resolve Missing Textures")
        try:
            for node, current_path, stem in missing:
                stem_lower = stem.lower()
                match_name, _score, status, strat_name = (
                    FuzzyMatcher.find_with_fallbacks(
                        stem_lower,
                        index_stems,
                        strategies=strategies,
                        score_threshold=0.6,
                        ambiguity_delta=0.05,
                    )
                )
                if status == "no_match":
                    no_match += 1
                    continue
                if status == "ambiguous":
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: ambiguous {strat_name} match for '{stem}', skipped."
                    )
                    continue
                matches = by_stem.get(match_name) or []
                if not matches:
                    no_match += 1
                    continue
                if len(matches) > 1:
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: '{match_name}' resolves to {len(matches)} files, skipped."
                    )
                    continue
                final_abs = matches[0]
                new_path = to_project_relative(final_abs)
                try:
                    Attributes.set_plug_literal(f"{node}.fileTextureName", new_path)
                    self._previous_paths[node] = current_path
                    resolved += 1
                    om.MGlobal.displayInfo(f"{node}: '{current_path}' -> '{new_path}'")
                except Exception as e:
                    cmds.warning(f"{node}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        om.MGlobal.displayInfo(
            f"Resolved {resolved}/{len(missing)} missing "
            f"(no match: {no_match}, ambiguous: {ambiguous}); "
            f"strategies: {', '.join(modes)}."
        )
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------
    # Table refresh / scene callbacks
    # ------------------------------------------------------------------

    def refresh_texture_table(self):
        """Manual refresh trigger from the header refresh button."""
        table = getattr(self.ui, "tbl000", None)
        if not table:
            return
        table.init_slot()

    def _setup_scene_change_callback(self, widget):
        """Subscribe to scene-change events via ScriptJobManager."""
        mgr = ScriptJobManager.instance()
        for event in (
            "SceneOpened",
            "NewSceneOpened",
            "SceneImported",
            "workspaceChanged",
        ):
            mgr.subscribe(
                event,
                lambda w=widget: self._on_scene_change(w),
                owner=self,
            )
        mgr.connect_cleanup(widget, owner=self)

    def _on_scene_change(self, widget):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def do_refresh():
            self._refresh_pending = False
            self._previous_paths.clear()
            try:
                try:
                    if not widget.isVisible():
                        pass
                except RuntimeError:
                    # Widget has been deleted (C++ object gone).
                    self.cleanup_scene_callbacks()
                    return
                print(
                    "TexturePathEditor: Scene changed, refreshing texture path table..."
                )
                self._refresh_table_content(widget)
            except Exception as e:
                print(f"TexturePathEditor: Error refreshing table on scene change: {e}")

        cmds.evalDeferred(do_refresh)

    def _refresh_table_content(self, widget):
        """Refresh the table content with current scene data."""
        cmds.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.clear()
            # Stored .ftn verbatim (no relativized display): Make Paths
            # Absolute / Select Absolute Paths and cell-edit write-back all
            # depend on the cell showing the path the node actually holds.
            rows = MatUtils.get_file_nodes(
                return_type="shaderName|path|fileNodeName",
                exc_classification=self._exclude_arnold_pattern(),
            )
            # Lightmap rows: the maps the bake markers name. Keyed by the
            # path text (the one column every selection payload carries), so
            # the row helpers can tell them from file-node rows; an EMPTY
            # UserRole on the name cells keeps the file-node commands from
            # mistaking the object label for a node.
            self._lightmap_rows = {
                self._lightmap_row_path(dep): dep
                for dep in self._lightmap_dependencies()
            }
            if not rows and not self._lightmap_rows:
                rows = [("", "", "No file nodes found")]

            formatted = []
            for shader_name, path, file_node_name in rows:
                # Stash node names in UserRole so handle_cell_edit can recover
                # the old name after editing.
                formatted.append(
                    [
                        (shader_name, shader_name),
                        path,
                        (file_node_name, file_node_name),
                    ]
                )
            for path, dep in self._lightmap_rows.items():
                shader_label, node_label = self._lightmap_row_labels(dep)
                # The path cell keeps its path in UserRole too: an edit
                # replaces the text, and the cell-edit handler needs the row's
                # identity to reach the record it repoints.
                formatted.append(
                    [
                        (shader_label, ""),
                        (path, path),
                        (node_label, ""),
                    ]
                )

            widget.add(formatted, headers=["Shader", "Texture Path", "File Node"])

            header = widget.horizontalHeader()
            header.setSectionsMovable(False)
            header.setSectionResizeMode(0, self.sb.QtWidgets.QHeaderView.Interactive)
            header.setSectionResizeMode(1, self.sb.QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(2, self.sb.QtWidgets.QHeaderView.Interactive)
            widget.setColumnWidth(0, 200)
            widget.setColumnWidth(2, 200)

            self.setup_formatting(widget)
            widget.apply_formatting()
            self._apply_path_truncation(widget)
        finally:
            widget.setUpdatesEnabled(True)
            cmds.waitCursor(state=False)

        # After apply_formatting — that pass is what fills the set.
        over_long = getattr(self, "_over_long_paths", None)
        if over_long:
            cmds.warning(
                f"Texture Path Editor: {len(over_long)} path(s) exceed this OS's "
                f"{FileUtils.path_length_limit()}-character path limit."
            )

        if self._footer_controller:
            self._footer_controller.update()

    def _warn_path_length_enabled(self) -> bool:
        """State of the header's "Warn On Over-Long Paths" toggle.

        Defaults to True when the header menu hasn't been built yet — an
        early refresh should warn, not silently skip the check (same
        defensive lookup as ``_truncate_paths_enabled``, opposite default
        because this one is on by default).
        """
        return self._menu_flag(self._header_menu(), "chk_warn_path_length", True)

    def _truncate_paths_enabled(self) -> bool:
        """State of the header's "Truncate Texture Paths" toggle.

        Returns False when the header menu hasn't been built yet, so an early
        refresh is safe (same defensive lookup as ``_exclude_arnold_pattern``).
        """
        return self._menu_flag(self._header_menu(), "chk_truncate_paths", False)

    def _apply_path_truncation(self, widget=None):
        """Push the Truncate Texture Paths toggle onto the path column.

        Display-only: ``set_column_truncation`` shortens what the delegate
        paints, never the item's data — the cell still holds (and edits back)
        the full path, and ``setup_formatting``'s tooltip still resolves it.
        Re-applied on every table rebuild so a refresh can't drop it.
        """
        widget = widget if widget is not None else getattr(self.ui, "tbl000", None)
        if widget is None:  # toggled before the table exists
            return
        widget.set_column_truncation(
            1,
            length=(
                self._PATH_TRUNCATE_LENGTH if self._truncate_paths_enabled() else None
            ),
            mode="path",
            # An ellipsis, not the primitive's default "..", which in a path
            # column reads as a parent-directory segment.
            insert="…",
            head=self._PATH_TRUNCATE_HEAD,
        )

    def cleanup_scene_callbacks(self):
        """Clean up scene-change subscriptions via ScriptJobManager."""
        ScriptJobManager.instance().unsubscribe_all(self)

    def setup_formatting(self, widget):
        # Resolved HERE, on the main thread: ``resolve_and_check`` below runs
        # on a thread pool, and ``cmds`` must not be touched from one.
        source_root, source_images = self._project_roots()
        path_cache = {}
        unique_paths = set()
        for row in range(widget.rowCount()):
            item = widget.item(row, 1)
            if item:
                path = str(item.text()).strip()
                if path:
                    unique_paths.add(path)

        def resolve_and_check(path):
            abs_path = MatUtils.to_absolute(path, source_root, source_images)
            return path, self._texture_on_disk(abs_path), abs_path

        if len(unique_paths) > 50:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(resolve_and_check, p): p for p in unique_paths
                }
                for future in as_completed(futures):
                    try:
                        path, exists, abs_path = future.result(timeout=5)
                        path_cache[path] = (exists, abs_path)
                    except Exception:
                        path = futures[future]
                        path_cache[path] = (
                            False,
                            MatUtils.to_absolute(path, source_root, source_images),
                        )
        else:
            for path in unique_paths:
                _, exists, abs_path = resolve_and_check(path)
                path_cache[path] = (exists, abs_path)

        warn_long = self._warn_path_length_enabled()
        length_limit = FileUtils.path_length_limit()
        # Reset per rebuild; the formatter fills it as it paints, so the caller
        # reads it only after apply_formatting.
        self._over_long_paths = set()

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()
            dep = self._lightmap_rows.get(path)
            if dep is not None:
                # A lightmap row carries its own verdict: the engine already
                # resolved it the way the export will. Red = nowhere; amber =
                # found, but not where the marker says (stale hint -- the
                # export ships it and Auto-Resolve / Find & Copy heal it).
                found = dep.get("path")
                stale = bool(found) and dep.get("found_by") != "hint"
                widget.format_item(
                    item,
                    key="invalid" if not found else ("warning" if stale else "reset"),
                )
                objects = ", ".join(o.rsplit("|", 1)[-1] for o in dep["objects"])
                if not found:
                    line = f"Missing lightmap:\n{path}"
                    if dep.get("note"):
                        line += f"\n{dep['note']}"
                elif stale:
                    line = f"Recorded folder no longer holds it; found at:\n{found}"
                else:
                    line = found
                item.setToolTip(
                    f"{line}\n\nBake marker on {len(dep['objects'])} object(s): "
                    f"{objects}"
                )
                return
            if path in path_cache:
                exists, abs_path = path_cache[path]
            else:
                abs_path = MatUtils.to_absolute(path, source_root, source_images)
                exists = self._texture_on_disk(abs_path)
                path_cache[path] = (exists, abs_path)
            # A missing file outranks an over-long one: it's the harder failure,
            # and an over-long path is usually WHY it went missing.
            over_long = warn_long and len(abs_path) > length_limit
            if over_long:
                self._over_long_paths.add(path)
            widget.format_item(
                item,
                key="invalid" if not exists else ("warning" if over_long else "reset"),
            )
            tooltip_lines = [abs_path if exists else f"Missing file:\n{abs_path}"]
            if over_long:
                tooltip_lines.append(
                    f"Path is {len(abs_path)} characters — over this OS's "
                    f"{length_limit}-character limit."
                )
            fn_item = widget.item(row, 2)
            fn_name = str(fn_item.text()).strip() if fn_item else ""
            previous = self._previous_paths.get(fn_name) if fn_name else None
            if previous and previous != path:
                tooltip_lines.append(f"Previous: {previous}")
            item.setToolTip("\n\n".join(tooltip_lines))

        widget.set_column_formatter(1, format_if_invalid)

    # ------------------------------------------------------------------
    # Context resolution helpers
    # ------------------------------------------------------------------

    def _file_nodes_from_selection(self, selection):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return []
        nodes = []
        for ctx in contexts:
            nodes.extend(ctx.get("file_nodes") or [])
        return list(dict.fromkeys(nodes))

    # Path primitives: ``MatUtils.to_absolute`` / ``MatUtils.to_project_relative``
    # (promoted from this class 2026-08-20 — the Scene Exporter's relative-path
    # task needed the same rule). They own WHICH relative form is emitted and
    # the order it resolves in; both were rebuilt 2026-08-25 around the only
    # form Maya keeps across a scene reload.

    def _resolve_context(self, shader_name, file_node_data):
        shader_name = str(shader_name).strip() if shader_name else ""
        shader_node = shader_name if shader_name else None

        if isinstance(file_node_data, (list, tuple)):
            file_node_data = next(
                (v for v in file_node_data if v and cmds.objExists(str(v))),
                None,
            )

        material_file_nodes = []
        if file_node_data:
            material_file_nodes = [file_node_data]
        elif shader_node:
            # listHistory directly on the shader is much faster than
            # rebuilding the entire scene-wide mapping via get_file_nodes.
            try:
                history = (
                    cmds.ls(cmds.listHistory(shader_node) or [], type="file") or []
                )
                material_file_nodes = list(dict.fromkeys(history))
            except Exception:
                material_file_nodes = []

        return {
            "shader_name": shader_name,
            "shader_node": shader_node,
            "file_node": file_node_data,
            "file_nodes": material_file_nodes,
        }

    def _get_selected_contexts(
        self,
        selection=None,
        require_file_nodes: bool = True,
        warn_on_empty: bool = False,
    ):
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return []

        if selection is None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS,
                include_current=True,
            )

        if not selection:
            if warn_on_empty:
                cmds.warning("No row selected.")
            return []

        contexts = []
        for entry in selection:
            # A lightmap row: no shader, no file node -- the record itself is
            # the context. Commands that require file nodes skip it (and say
            # so through the existing "no valid file nodes" path).
            path_value = str(self._selection_value(entry, "path") or "").strip()
            lightmap = self._lightmap_rows.get(path_value)
            if lightmap is not None:
                if require_file_nodes:
                    continue
                contexts.append(
                    {
                        "shader_name": "",
                        "shader_node": None,
                        "file_node": None,
                        "file_nodes": [],
                        "lightmap": lightmap,
                    }
                )
                continue
            shader_value = self._selection_value(entry, "shader")
            file_node_value = self._selection_value(entry, "file_node")
            context = self._resolve_context(shader_value, file_node_value)
            if require_file_nodes and not context["file_nodes"]:
                continue
            contexts.append(context)

        if require_file_nodes and not contexts:
            if warn_on_empty:
                cmds.warning("No valid file nodes found in the selected row(s).")
            return []
        return contexts

    def _selection_value(self, entry, key: str):
        if hasattr(entry, "values"):
            return entry.values.get(key)
        if hasattr(entry, "get"):
            try:
                value = entry.get(key)
                if value is not None:
                    return value
            except TypeError:
                pass
        column = self._ROW_SELECTION_COLUMNS.get(key)
        if column is not None and isinstance(entry, dict):
            return entry.get(column)
        return None

    # ------------------------------------------------------------------
    # Cell editing
    # ------------------------------------------------------------------

    def handle_cell_edit(self, row: int, col: int):
        tbl = self.ui.tbl000
        item = tbl.item(row, col)
        if not item:
            return
        new_value = item.text()
        UserRole = self.sb.QtCore.Qt.UserRole

        def _restore_text(target_item, original):
            tbl.blockSignals(True)
            try:
                target_item.setText(original)
            finally:
                tbl.blockSignals(False)

        def _rename_node(label):
            old_name = item.data(UserRole)
            if not old_name:
                _restore_text(item, new_value)
                return
            if new_value == old_name:
                return
            if not cmds.objExists(old_name):
                cmds.warning(f"{label} '{old_name}' no longer exists; cannot rename.")
                _restore_text(item, old_name)
                return
            try:
                actual = cmds.rename(old_name, new_value)
            except Exception as e:
                cmds.warning(f"Failed to rename {label}: {e}")
                _restore_text(item, old_name)
                return
            item.setData(UserRole, actual)
            if actual != new_value:
                _restore_text(item, actual)
            om.MGlobal.displayInfo(f"Renamed {label} '{old_name}' -> '{actual}'")

        # A lightmap row: the path cell repoints the bake markers (folder only
        # -- the map is what the bake committed); the name cells are labels,
        # not nodes, and cannot be renamed. Identified through the path cell's
        # UserRole, which still holds the row's path after the text was edited.
        path_item = tbl.item(row, 1)
        row_key = (
            str(path_item.data(UserRole) or path_item.text()).strip()
            if path_item is not None
            else ""
        )
        lightmap = self._lightmap_rows.get(row_key)
        if lightmap is not None:
            if col != 1:
                cmds.warning("Lightmap rows carry no node to rename.")
                # Deferred: this runs inside cellChanged, and rebuilding the
                # table (clear + add) from within its own signal would delete
                # the item mid-dispatch.
                self.sb.QtCore.QTimer.singleShot(0, self.refresh_texture_table)
                return
            typed = new_value.strip().replace("\\", "/")
            if typed and os.path.basename(typed).lower() != lightmap["map"].lower():
                cmds.warning(
                    f"The bake markers name {lightmap['map']!r}; a path to a "
                    "different map is a re-bake, not a repath."
                )
                _restore_text(item, self._lightmap_row_path(lightmap))
                return
            if self._repath_lightmap(lightmap, os.path.dirname(typed)):
                self.sb.QtCore.QTimer.singleShot(0, self.refresh_texture_table)
            else:
                _restore_text(item, self._lightmap_row_path(lightmap))
            return

        if col == 0:
            _rename_node("shader")
        elif col == 1:
            fn_item = tbl.item(row, 2)
            file_node = fn_item.data(UserRole) if fn_item else None
            if not file_node:
                cmds.warning("No file node associated with this row.")
                return
            if not cmds.objExists(file_node):
                cmds.warning(f"File node '{file_node}' no longer exists.")
                return
            try:
                Attributes.set_plug_literal(f"{file_node}.fileTextureName", new_value)
                om.MGlobal.displayInfo(f"{file_node}: texture path -> '{new_value}'")
                tbl.apply_formatting()
                if self._footer_controller:
                    self._footer_controller.update()
            except Exception as e:
                cmds.warning(f"Failed to update texture path: {e}")
        elif col == 2:
            _rename_node("file node")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    #: ``_log`` level -> the Maya channel that carries it when no panel is
    #: driving the command. ``cmds.warning`` / ``displayError`` are what put a
    #: line in front of a user who is not looking at the script editor, so a
    #: warning must not quietly degrade to an info line off-panel.
    _MAYA_LOG_CHANNELS = {
        "warning": "warning",
        "error": "error",
    }

    @staticmethod
    def _joined(message: str, items) -> str:
        """*message* with its *items* indented under it, as one plain string.

        The fallback for a sink with no ``log_group``: a stdlib logger, or
        Maya's own channels, where the whole report has to arrive as one
        string or arrive as N separate lines that read as N separate events.
        """
        return "\n".join([message, *(f"  {item}" for item in items)])

    def _log(self, message: str, level: str = "info", items=None) -> None:
        """Report one line — or one titled list — of a command's result.

        Goes to the driving panel's output pane when there is one — that pane
        IS the report, and the whole point of having it is not having to look
        somewhere else for what just happened (its logger keeps a stream
        handler, so the script editor still sees the same lines) — and to
        Maya's own channels otherwise, so the same call site works headless
        and from a test.

        ``items`` is a list belonging under *message*. It goes through
        ``log_group`` so the whole thing is ONE record: a widget handler
        appends a QTextBlock per record, so a line-per-item loop renders as N
        blank-line-separated paragraphs instead of a list.
        """
        logger = self._active_logger
        items = list(items) if items else []
        if logger is not None:
            if items and hasattr(logger, "log_group"):
                logger.log_group(message, items, level=level)
                return
            if items:
                message = self._joined(message, items)
            getattr(logger, level, logger.info)(message)
            return
        if items:
            message = self._joined(message, items)
        if cmds is None:  # headless / unit test outside Maya
            return
        channel = self._MAYA_LOG_CHANNELS.get(level)
        if channel == "warning":
            cmds.warning(message)
        elif channel == "error":
            om.MGlobal.displayError(message)
        else:
            om.MGlobal.displayInfo(message)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._footer_status_text,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _footer_status_text(self) -> str:
        """Footer line: the texture directory every path command resolves against.

        Labelled, because a bare path in a status strip reads as "some path" —
        the label is what makes a wrong project obvious at a glance.

        The label is the resolved folder's own name, not a fixed word: the
        ``sourceImages`` rule may map anywhere (a blendertk-promoted project
        maps it to ``textures``), and a footer reading SOURCEIMAGES over a
        path ending in ``/textures`` would be the panel disagreeing with
        itself. Whatever the project calls it is what the footer says.
        """
        path = self._resolve_source_images_path()
        if not path:
            return ""
        label = FileUtils.format_path(path, "dir").upper()
        return f"{label}: {path}" if label else path

    def _resolve_source_images_path(self) -> str:
        return EnvUtils.get_env_info("sourceimages") or ""

    def _project_roots(self):
        """``(workspace, sourceimages)`` — both roots the path converters take.

        ``MatUtils.to_absolute`` / ``to_project_relative`` look either one up
        themselves when it is not handed over, and that lookup goes through
        ``cmds.workspace``. Resolving the pair ONCE, here, is what keeps a
        per-row Maya call out of the table refresh and keeps ``cmds`` off the
        thread pool in :meth:`setup_formatting` — so callers that loop pass
        both down rather than letting the converters ask per path.
        """
        return (
            EnvUtils.get_env_info("workspace") or "",
            self._resolve_source_images_path(),
        )


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
