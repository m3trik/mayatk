# !/usr/bin/python
# coding=utf-8
"""Arnold HDR environment manager.

Provides :class:`HdrManager` — a thin wrapper around an ``aiSkyDomeLight``
+ ``file`` texture network for image-based lighting, and
:class:`HdrManagerSlots` — the matching Switchboard slots class for the
``hdr_manager.ui`` panel.

Knobs the manager owns on the skydome:

* ``hdr_env``    — file texture (``.exr`` / ``.hdr``) wired into ``color``.
* ``intensity``  — linear multiplier on the light output.
* ``exposure``   — photographic stops (log2) via Arnold's ``aiExposure``;
  returns 0.0 / no-ops on older mtoa builds that don't expose it.
* ``rotation``   — Y rotation on the transform parent.
* ``visibility`` — primary-ray (``camera``) flag controlling whether the
  HDR is visible *as a backdrop* rather than just as lighting.
* ``resolution`` — importance-sampling resolution of the HDR (``resolution``).
* ``samples``    — light samples (``aiSamples``) governing IBL noise.
* ``diffuse`` / ``specular`` — per-component contribution scales
  (``aiDiffuse`` / ``aiSpecular``).

The skydome's scalar attributes (``intensity``, ``exposure``, ``resolution``,
``samples``, ``diffuse``, ``specular``) are all read/written through
:meth:`_get_light_attr` / :meth:`_set_light_attr`, which fall back to the
Arnold default (and no-op the write) when the attribute is absent — so the
manager stays robust across mtoa versions. (``rotation`` lives on the
transform and ``visibility`` is the ``camera`` flag, so those go direct.)

Maya 2025+ / Arnold (``mtoa``) plugin required for any network mutation.
"""
import os
import shutil
import subprocess
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class HdrManager(ptk.LoggingMixin, ptk.HelpMixin):
    """Manage a single ``aiSkyDomeLight`` + connected ``file`` texture.

    The class is intentionally stateless beyond a class-level node name:
    every method/property re-queries the scene, so external edits to the
    skydome are picked up on the next read.
    """

    # Trailing underscore preserved for backward compatibility with
    # existing scenes and tests that look up the node by this exact name.
    hdr_env_name: str = "aiSkyDomeLight_"

    # ------------------------------------------------------------------
    # Plugin
    # ------------------------------------------------------------------

    @staticmethod
    def arnold_loaded() -> bool:
        """True if ``mtoa`` is *already* loaded — cheap, side-effect-free query.

        Unlike :meth:`arnold_available` this never triggers ``loadPlugin``,
        so it's safe for read-only UI gating (e.g. on panel open). Loading
        ``mtoa`` boots the whole Arnold renderer and costs seconds — defer
        that to the first mutating action, not merely showing the panel.
        """
        if cmds is None:
            return False
        try:
            return bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
        except Exception:
            return False

    @staticmethod
    def arnold_available() -> bool:
        """True if the ``mtoa`` plugin can be loaded right now.

        Loads ``mtoa`` if it isn't already (use :meth:`arnold_loaded` for a
        non-loading check). Never raises — returns ``False`` on any failure.
        """
        if HdrManager.arnold_loaded():
            return True
        if cmds is None:
            return False
        try:
            cmds.loadPlugin("mtoa", quiet=True)
        except Exception:
            return False
        return HdrManager.arnold_loaded()

    @classmethod
    def ensure_plugin_loaded(cls) -> bool:
        """Backward-compat alias for :meth:`arnold_available`."""
        return cls.arnold_available()

    # ------------------------------------------------------------------
    # Skydome accessors
    # ------------------------------------------------------------------

    @property
    def hdr_env(self) -> Optional[str]:
        """The skydome shape node, or ``None`` if not present."""
        # Without mtoa loaded the aiSkyDomeLight type isn't registered and no
        # such node can exist — short-circuit to skip the ls (and the
        # "Unknown object type" warning it emits) on every UI sync. This keeps
        # panel open / refresh cheap now that Arnold is no longer force-loaded.
        if not self.arnold_loaded():
            return None
        node = cmds.ls(self.hdr_env_name, exactType="aiSkyDomeLight") or []
        return node[0] if node else None

    @hdr_env.setter
    def hdr_env(self, tex: Optional[str]) -> None:
        """Set (and lazily create) the skydome's HDR file texture.

        Passing ``None`` or an empty string is a no-op — use
        :meth:`clear` to remove the network.
        """
        if not tex:
            return
        if not self.arnold_available():
            self.logger.warning("Arnold (mtoa) plugin not available — cannot set HDR env.")
            return

        tex = str(tex)
        node = self.hdr_env or self._create_skydome()
        file_node = self._connected_file_node(node)
        if file_node is None:
            # Fresh network — create_file_node wires path + colorSpace.
            self._attach_file_node(node, tex)
        else:
            # Existing file node — swap path in place, keep wiring intact.
            cmds.setAttr(f"{file_node}.fileTextureName", tex, type="string")
            # HDR/EXR data is scene-linear; tag colorSpace so Maya
            # doesn't double-apply an sRGB → linear conversion.
            try:
                cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
            except Exception:
                pass

    @property
    def hdr_env_transform(self) -> Optional[str]:
        """Transform parent of the skydome shape, or ``None``."""
        shape = self.hdr_env
        if not shape:
            return None
        return NodeUtils.get_transform_node(shape)

    @property
    def hdr_file_node(self) -> Optional[str]:
        """The ``file`` node currently driving ``color`` on the skydome."""
        node = self.hdr_env
        return self._connected_file_node(node) if node else None

    @property
    def hdr_file_path(self) -> Optional[str]:
        """Current HDR file path on disk, or ``None``."""
        file_node = self.hdr_file_node
        if not file_node:
            return None
        path = cmds.getAttr(f"{file_node}.fileTextureName")
        return path or None

    # ------------------------------------------------------------------
    # Continuous attributes
    # ------------------------------------------------------------------

    @property
    def visibility(self) -> bool:
        """Primary-ray visibility of the HDR (skydome as backdrop)."""
        node = self.hdr_env
        return bool(cmds.getAttr(f"{node}.camera")) if node else False

    @visibility.setter
    def visibility(self, state: bool) -> None:
        node = self.hdr_env
        if node:
            cmds.setAttr(f"{node}.camera", bool(state))

    def set_hdr_map_visibility(self, state: bool) -> None:
        """Backward-compat shim for :attr:`visibility`."""
        if not self.arnold_available():
            return
        self.visibility = state

    @property
    def rotation(self) -> float:
        """Y rotation (degrees) of the skydome transform; 0 if absent."""
        transform = self.hdr_env_transform
        return float(cmds.getAttr(f"{transform}.rotateY")) if transform else 0.0

    @rotation.setter
    def rotation(self, degrees: float) -> None:
        transform = self.hdr_env_transform
        if not transform:
            return
        cmds.rotate(
            transform,
            float(degrees),
            rotateY=True,
            forceOrderXYZ=True,
            objectSpace=True,
            absolute=True,
        )

    @property
    def intensity(self) -> float:
        """Linear light-output multiplier on the skydome; 1.0 if absent."""
        return self._get_light_attr("intensity", 1.0)

    @intensity.setter
    def intensity(self, value: float) -> None:
        self._set_light_attr("intensity", float(value))

    @property
    def exposure(self) -> float:
        """Photographic stops (log2) on the skydome's ``aiExposure``.

        Returns 0.0 when the skydome or attribute is absent (older mtoa).
        """
        return self._get_light_attr("aiExposure", 0.0)

    @exposure.setter
    def exposure(self, stops: float) -> None:
        self._set_light_attr("aiExposure", float(stops))

    @property
    def resolution(self) -> int:
        """Importance-sampling resolution of the HDR (``resolution``); 1000 if absent.

        Higher = cleaner light and shadows from bright spots (e.g. a sun
        baked into the HDR), at the cost of a longer sampling precompute.
        """
        return self._get_light_attr("resolution", 1000, cast=int)

    @resolution.setter
    def resolution(self, value: int) -> None:
        self._set_light_attr("resolution", int(value))

    @property
    def samples(self) -> int:
        """Light samples (``aiSamples``) — soft-IBL noise control; 1 if absent."""
        return self._get_light_attr("aiSamples", 1, cast=int)

    @samples.setter
    def samples(self, value: int) -> None:
        self._set_light_attr("aiSamples", int(value))

    @property
    def diffuse(self) -> float:
        """Diffuse contribution scale (``aiDiffuse``); 1.0 if absent."""
        return self._get_light_attr("aiDiffuse", 1.0)

    @diffuse.setter
    def diffuse(self, value: float) -> None:
        self._set_light_attr("aiDiffuse", float(value))

    @property
    def specular(self) -> float:
        """Specular contribution scale (``aiSpecular``); 1.0 if absent."""
        return self._get_light_attr("aiSpecular", 1.0)

    @specular.setter
    def specular(self, value: float) -> None:
        self._set_light_attr("aiSpecular", float(value))

    # ------------------------------------------------------------------
    # Network lifecycle
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_network(
        self,
        hdrMap: str = "",
        hdrMapVisibility: bool = False,
        intensity: Optional[float] = None,
        exposure: Optional[float] = None,
        rotation: Optional[float] = None,
        resolution: Optional[int] = None,
        samples: Optional[int] = None,
        diffuse: Optional[float] = None,
        specular: Optional[float] = None,
    ) -> Optional[str]:
        """Apply settings to the (lazily-created) skydome network.

        Only non-``None`` knobs are written, so callers can update a subset
        without disturbing the rest. Returns the skydome shape node, or
        ``None`` if Arnold is unavailable.
        """
        if not self.arnold_available():
            self.logger.warning("Arnold (mtoa) not available — create_network skipped.")
            return None

        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)
        for attr, value in (
            ("intensity", intensity),
            ("exposure", exposure),
            ("rotation", rotation),
            ("resolution", resolution),
            ("samples", samples),
            ("diffuse", diffuse),
            ("specular", specular),
        ):
            if value is not None:
                setattr(self, attr, value)
        return self.hdr_env

    @CoreUtils.undoable
    def clear(self) -> None:
        """Remove the skydome and its connected file/place2d nodes."""
        node = self.hdr_env
        if not node:
            return
        file_node = self._connected_file_node(node)
        place2d = (
            cmds.listConnections(file_node, type="place2dTexture") or []
            if file_node else []
        )
        transform = NodeUtils.get_transform_node(node)
        for n in [*place2d, file_node, transform or node]:
            if n and cmds.objExists(n):
                try:
                    cmds.delete(n)
                except Exception as e:
                    self.logger.debug("Could not delete %s: %s", n, e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_light_attr(self, attr: str, default, cast=float):
        """Read numeric *attr* off the skydome shape.

        Returns *default* (cast left untouched) when the skydome or the
        attribute is absent — keeps the manager robust across mtoa builds
        that may not expose a given knob.
        """
        node = self.hdr_env
        if not node or not cmds.attributeQuery(attr, node=node, exists=True):
            return default
        return cast(cmds.getAttr(f"{node}.{attr}"))

    def _set_light_attr(self, attr: str, value) -> None:
        """Set numeric *attr* on the skydome shape; no-op if it's absent."""
        node = self.hdr_env
        if not node:
            return
        if cmds.attributeQuery(attr, node=node, exists=True):
            cmds.setAttr(f"{node}.{attr}", value)
        else:
            self.logger.debug("%s not present on %s; skipping.", attr, node)

    def _create_skydome(self) -> str:
        """Create the named ``aiSkyDomeLight`` and stash its transform."""
        node = NodeUtils.create_render_node(
            "aiSkyDomeLight",
            "asLight",
            name=self.hdr_env_name,
            camera=0,        # hidden from primary rays by default
            skyRadius=0,     # hide the sphere preview in viewport
        )
        transform = NodeUtils.get_transform_node(node)
        if transform:
            try:
                cmds.setAttr(f"{transform}.hiddenInOutliner", 1)
            except Exception:
                pass
            # Best-effort outliner refresh — silently no-op under mayapy
            # / batch where the panel doesn't exist.
            try:
                cmds.outlinerEditor("outlinerPanel1", edit=True, refresh=True)
            except Exception:
                pass
        return node

    @staticmethod
    def _connected_file_node(skydome: str) -> Optional[str]:
        """Return the file node feeding ``skydome.color``, if any."""
        files = cmds.listConnections(
            f"{skydome}.color", source=True, destination=False, type="file"
        ) or []
        return files[0] if files else None

    @staticmethod
    def _attach_file_node(skydome: str, path: str) -> str:
        """Create a file+place2d pair and wire it into ``skydome.color``.

        Naming uses the image stem so the file node mirrors the texture
        (avoids the ``X_file_file`` collision that ``create_file_node``
        would otherwise produce when fed a name already ending in ``_file``).
        """
        stem = os.path.splitext(os.path.basename(path))[0] or "hdr"
        file_node, _ = MatUtils.create_file_node(
            path, name=stem, color_space="Raw"
        )
        cmds.connectAttr(f"{file_node}.outColor", f"{skydome}.color", force=True)
        return file_node


class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the HDR Manager UI.

    Composition over inheritance: routes events through ``self.manager``
    rather than carrying business logic. Combobox auto-refreshes when
    the Maya scene changes (``SceneOpened``) so newly-opened projects
    pick up their own sourceimages set.
    """

    # File-dialog filter for the browse button.
    HDR_FILTER: str = "HDR Images (*.exr *.hdr);;All Files (*.*)"

    # Add-HDR mode picker — index → (label, token). Index is the source of
    # truth so reordering the labels can't silently rename the dispatch token.
    _ADD_MODES: tuple = (
        ("Copy to sourceimages", "copy"),
        ("Move to sourceimages", "move"),
        ("Link to original location", "link"),
    )

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hdr_manager
        self.manager = HdrManager()

        # Auto-refresh the HDR list when the user opens a new scene —
        # otherwise the combo silently lists sourceimages from the
        # previous workspace.
        mgr = ScriptJobManager.instance()
        mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
        mgr.subscribe("NewSceneOpened", self._on_scene_changed, owner=self)
        mgr.connect_cleanup(self.ui, owner=self)

        # Initial population is deferred to the next event-loop tick. The
        # switchboard constructs this slots instance *mid-load* — child
        # widgets (footer, spinboxes, slider) aren't wired onto self.ui until
        # register_children runs after __init__ returns, so touching them now
        # hits AttributeError on None. By the next tick the UI is fully wired.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Populate the combobox and sync widgets from the scene.

        Deferred from __init__ (see there) so the full UI is registered
        before any ``self.ui.<widget>`` access.
        """
        self._refresh_combo()
        self._sync_ui_to_scene()

        # Gate UI affordances on plugin state — but do NOT force-load Arnold
        # here. loadPlugin("mtoa") boots the whole renderer (seconds) and was
        # the cause of the panel's slow open. Only report when it isn't loaded
        # yet; the real load happens lazily on the first mutating action.
        if not self.manager.arnold_loaded():
            self.ui.footer.setText("Arnold (mtoa) not loaded — loads on first use.")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure header menu and refresh button."""
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._refresh_combo)
        widget.menu.add("Separator", setTitle="Sourceimages")
        widget.menu.add(
            "QPushButton",
            setText="Open Sourceimages Folder",
            setObjectName="open_sourceimages",
            setToolTip="Open the workspace's sourceimages folder in Explorer.",
        )
        widget.menu.add("Separator", setTitle="Network")
        widget.menu.add(
            "QPushButton",
            setText="Clear Network",
            setObjectName="clear_network",
            setToolTip="Delete the skydome and its connected file / place2d nodes.",
        )
        widget.set_help_text(
            fmt(
                title="HDR Manager",
                body="Manage the scene's Arnold HDR environment lighting "
                "(aiSkyDomeLight + file + place2dTexture network).",
                steps=[
                    "Pick an HDR / EXR from the dropdown (lists files in "
                    "<i>sourceimages</i>).",
                    "Use the folder button on the dropdown to add a new HDR; "
                    "the option-box menu (▸) beside it picks the import mode.",
                    "Adjust <b>Intensity</b> (linear), <b>Exposure</b> (stops), "
                    "and <b>Resolution</b> (HDR importance-sampling res).",
                    "Drag the rotation slider to spin the environment around Y.",
                    "Toggle <b>Visible</b> to show the HDR as a viewport backdrop.",
                    "Press <b>Set HDR</b> to create or refresh the skydome network.",
                ],
                sections=[
                    ("Advanced Options (collapsible)", [
                        "<b>Samples</b> — light samples; raise to clean up "
                        "soft-IBL noise (<i>aiSamples</i>).",
                        "<b>Diffuse</b> / <b>Specular</b> — scale the dome's "
                        "diffuse vs specular contribution independently "
                        "(<i>aiDiffuse</i> / <i>aiSpecular</i>).",
                    ]),
                    ("Add HDR modes (option-box menu ▸)", [
                        "<b>Copy</b> — duplicate the file into sourceimages "
                        "(default; keeps scenes portable).",
                        "<b>Move</b> — relocate into sourceimages.",
                        "<b>Link</b> — wire the file in at its original path.",
                    ]),
                    ("Dropdown right-click", [
                        "Select skydome / file / transform nodes.",
                        "Reveal the texture in Explorer.",
                    ]),
                    ("Header menu", [
                        "<b>Open Sourceimages Folder</b> — Explorer shortcut.",
                        "<b>Clear Network</b> — delete the skydome and its "
                        "file / place2d nodes.",
                    ]),
                ],
                notes=[
                    "Requires the Arnold (mtoa) plugin to be loaded — the "
                    "footer reports if it's missing.",
                ],
            )
        )

    def cmb000_init(self, widget) -> None:
        """Wire the HDR dropdown: option-box plugins, context menu, auto-refresh."""
        # Auto-refresh from disk every time the user opens the dropdown,
        # so newly-saved HDRs appear without hitting the header refresh.
        widget.before_popup_shown.connect(self._refresh_combo)

        # Right-click → context menu (MenuMixin on uitk ComboBox). Kept
        # separate from the option-box menu below (different Menu instance).
        widget.configure_menu(trigger_button="right")
        widget.menu.add(
            "QPushButton",
            setText="Select Skydome",
            setObjectName="ctx_select_skydome",
            setToolTip="Select the aiSkyDomeLight shape node in the scene.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select Transform",
            setObjectName="ctx_select_transform",
            setToolTip="Select the skydome's transform parent.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select File Node",
            setObjectName="ctx_select_file_node",
            setToolTip="Select the file texture node driving the skydome color.",
        )
        widget.menu.add("Separator")
        widget.menu.add(
            "QPushButton",
            setText="Reveal in Explorer",
            setObjectName="ctx_reveal_in_explorer",
            setToolTip="Open the HDR file's containing folder in Explorer.",
        )

        # Option-box icon buttons on the combobox (replace the old standalone
        # "…" button): a folder/browse button that launches the Add-HDR file
        # dialog, and an option-box menu button that picks the import mode.
        widget.option_box.browse(
            file_types=self.HDR_FILTER,
            title="Add HDR / EXR",
            start_dir=lambda: EnvUtils.get_env_info("sourceimages") or "",
            icon="folder",
            tooltip=(
                "Add an HDR/EXR — runs the mode picked in the option menu (▸):\n"
                "  • Copy — duplicate it into sourceimages (default, scenes stay portable).\n"
                "  • Move — relocate it into sourceimages (original is removed).\n"
                "  • Link — leave it in place and wire the original path directly\n"
                "    (the file won't appear in the dropdown afterward)."
            ),
            callback=self._add_hdr,
        )
        widget.option_box.menu.setTitle("Add HDR Mode")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_add_mode",
            setToolTip=(
                "Choose what the folder (browse) button does with the picked file:\n"
                "  • Copy — duplicate it into sourceimages (default, scenes stay portable).\n"
                "  • Move — relocate it into sourceimages (original is removed).\n"
                "  • Link — leave it in place and wire the original path directly\n"
                "    (the file won't appear in the dropdown afterward)."
            ),
            addItems=[label for label, _token in self._ADD_MODES],
        )

    # ------------------------------------------------------------------
    # Scene → UI sync
    # ------------------------------------------------------------------

    def _on_scene_changed(self) -> None:
        self._refresh_combo()
        self._sync_ui_to_scene()

    def _refresh_combo(self) -> None:
        """Repopulate the HDR combobox from the workspace sourceimages.

        Preserves the user's current selection across rebuilds (by data,
        not index) so refreshing on dropdown-open doesn't snap them back
        to the first entry.
        """
        previous_data = self.ui.cmb000.currentData()
        src = EnvUtils.get_env_info("sourceimages")

        # Block signals so the rebuild doesn't fire cmb000 → set hdr_env
        # → re-trigger refresh while we're still rebuilding.
        self.ui.cmb000.blockSignals(True)
        try:
            if not src or not os.path.isdir(src):
                self.ui.cmb000.clear()
                self.ui.cmb000.addItem("<HDR Map>")
                self.ui.footer.setText("No sourceimages directory in workspace.")
                return

            hdr_info = ptk.get_dir_contents(
                src,
                ["filename", "filepath"],
                inc_files=["*.exr", "*.hdr"],
                group_by_type=True,
            )
            # ComboBox.add() drives both userData and visible text.
            self.ui.cmb000.add(
                zip(hdr_info["filename"], hdr_info["filepath"]),
                ascending=False,
                clear=True,
            )

            # Restore prior selection by data, not index.
            if previous_data:
                idx = self.ui.cmb000.findData(previous_data)
                if idx >= 0:
                    self.ui.cmb000.setCurrentIndex(idx)

            count = len(hdr_info["filename"])
            self.ui.footer.setText(
                f"{count} HDR{'s' if count != 1 else ''} in sourceimages."
            )
        finally:
            self.ui.cmb000.blockSignals(False)

    def _sync_ui_to_scene(self) -> None:
        """Pull live scene state into the UI widgets.

        When no skydome exists (fresh scene, after :meth:`clear_network`),
        falls back to neutral defaults so the controls don't display
        stale values from a deleted node.
        """
        has_env = bool(self.manager.hdr_env)
        rotation = int(round(self.manager.rotation)) % 360 if has_env else 0
        intensity = self.manager.intensity if has_env else 1.0
        exposure = self.manager.exposure if has_env else 0.0
        visible = self.manager.visibility if has_env else False
        resolution = self.manager.resolution if has_env else 1000
        samples = self.manager.samples if has_env else 1
        diffuse = self.manager.diffuse if has_env else 1.0
        specular = self.manager.specular if has_env else 1.0

        for widget, setter, value in (
            (self.ui.slider000, "setSliderPosition", rotation),
            (self.ui.spn_intensity, "setValue", intensity),
            (self.ui.spn_exposure, "setValue", exposure),
            (self.ui.chk000, "setChecked", visible),
            (self.ui.spn_resolution, "setValue", resolution),
            (self.ui.spn_samples, "setValue", samples),
            (self.ui.spn_diffuse, "setValue", diffuse),
            (self.ui.spn_specular, "setValue", specular),
        ):
            widget.blockSignals(True)
            try:
                getattr(widget, setter)(value)
            finally:
                widget.blockSignals(False)

    # ------------------------------------------------------------------
    # Read-only convenience
    # ------------------------------------------------------------------

    @property
    def hdr_map(self) -> Optional[str]:
        """Selected HDR file path from the combobox."""
        return self.ui.cmb000.currentData()

    @property
    def hdr_map_visibility(self) -> bool:
        return self.ui.chk000.isChecked()

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def cmb000(self, index, widget) -> None:
        """HDR map selection — apply immediately."""
        path = widget.currentData()
        if not path:
            return
        self.manager.hdr_env = path
        self._sync_ui_to_scene()
        self.ui.footer.setText(f"HDR: {os.path.basename(path)}")

    def chk000(self, state, widget) -> None:
        """Toggle skydome primary-ray visibility."""
        self.manager.set_hdr_map_visibility(bool(state))

    def slider000(self, value, widget) -> None:
        """Rotate the HDR around Y."""
        self.manager.rotation = value

    def spn_intensity(self, value) -> None:
        self.manager.intensity = value

    def spn_exposure(self, value) -> None:
        self.manager.exposure = value

    def spn_resolution(self, value) -> None:
        self.manager.resolution = value

    def spn_samples(self, value) -> None:
        self.manager.samples = value

    def spn_diffuse(self, value) -> None:
        self.manager.diffuse = value

    def spn_specular(self, value) -> None:
        self.manager.specular = value

    def b000(self) -> None:
        """Create / refresh the skydome network from current UI state."""
        if not self.manager.arnold_available():
            self.ui.footer.setText("Arnold (mtoa) plugin not loaded.")
            return
        path = self.hdr_map
        if not path:
            self.ui.footer.setText("Pick or browse for an HDR first.")
            return
        self.manager.create_network(
            hdrMap=path,
            hdrMapVisibility=self.hdr_map_visibility,
            intensity=self.ui.spn_intensity.value(),
            exposure=self.ui.spn_exposure.value(),
            rotation=self.ui.slider000.sliderPosition(),
            resolution=self.ui.spn_resolution.value(),
            samples=self.ui.spn_samples.value(),
            diffuse=self.ui.spn_diffuse.value(),
            specular=self.ui.spn_specular.value(),
        )
        self.ui.footer.setText(f"Applied: {os.path.basename(path)}")

    def _add_hdr(self, result) -> None:
        """Browse-plugin callback — import the picked HDR per the option mode.

        Wired as the ``cmb000`` option-box ``browse`` callback; *result* is
        the path the file dialog returned. Three modes (set via the option-box
        menu button next to the dropdown):
          - **Copy** — duplicate into sourceimages, wire it up.
          - **Move** — relocate into sourceimages, wire it up.
          - **Link** — wire the original path directly; file stays put
            and won't appear in the combobox dropdown (combo lists
            sourceimages only).

        Copy / Move require a workspace sourceimages directory. Link
        works anywhere.
        """
        path = result if isinstance(result, str) else (result[0] if result else None)
        if not path:
            return

        mode = self._add_mode()
        if mode == "link":
            self.manager.hdr_env = path
            self._sync_ui_to_scene()
            self.ui.footer.setText(f"Linked: {path}")
            return

        # Copy / Move both require sourceimages.
        src = EnvUtils.get_env_info("sourceimages")
        if not src or not os.path.isdir(src):
            self.ui.footer.setText(
                "No sourceimages directory — set a Maya project first."
            )
            return

        imported = self._import_into_sourceimages(path, src, mode=mode)
        if imported is None:
            return  # cancelled or failed (footer already set)
        final_path, did_io = imported

        self._refresh_combo()
        idx = self.ui.cmb000.findData(final_path)
        if idx >= 0:
            self.ui.cmb000.setCurrentIndex(idx)
        self.manager.hdr_env = final_path
        self._sync_ui_to_scene()
        name = os.path.basename(final_path)
        if did_io:
            verb = {"copy": "Copied", "move": "Moved"}[mode]
            self.ui.footer.setText(f"{verb} & set: {name}")
        else:
            self.ui.footer.setText(f"Set: {name}")

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def open_sourceimages(self) -> None:
        """Open the workspace's sourceimages folder in Explorer."""
        src = EnvUtils.get_env_info("sourceimages")
        if not src or not os.path.isdir(src):
            self.ui.footer.setText("No sourceimages directory in workspace.")
            return
        os.startfile(src)

    def clear_network(self) -> None:
        """Delete the skydome network and reset the UI to defaults."""
        if not self.manager.hdr_env:
            self.ui.footer.setText("No HDR network in scene.")
            return
        self.manager.clear()
        self._sync_ui_to_scene()
        self.ui.footer.setText("HDR network cleared.")

    # ------------------------------------------------------------------
    # Context-menu actions (right-click on cmb000)
    # ------------------------------------------------------------------

    def ctx_select_skydome(self) -> None:
        node = self.manager.hdr_env
        if not node:
            self.ui.footer.setText("No skydome in scene.")
            return
        cmds.select(node, replace=True)
        self.ui.footer.setText(f"Selected: {node}")

    def ctx_select_transform(self) -> None:
        node = self.manager.hdr_env_transform
        if not node:
            self.ui.footer.setText("No skydome transform in scene.")
            return
        cmds.select(node, replace=True)
        self.ui.footer.setText(f"Selected: {node}")

    def ctx_select_file_node(self) -> None:
        node = self.manager.hdr_file_node
        if not node:
            self.ui.footer.setText("No file node connected to the skydome.")
            return
        cmds.select(node, replace=True)
        self.ui.footer.setText(f"Selected: {node}")

    def ctx_reveal_in_explorer(self) -> None:
        path = self.manager.hdr_file_path
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        # Fall back to the workspace sourceimages folder so the user can
        # still navigate from there when the texture is missing/unsaved.
        src = EnvUtils.get_env_info("sourceimages")
        if src and os.path.isdir(src):
            os.startfile(src)
        else:
            self.ui.footer.setText("HDR file not found and no sourceimages folder.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_mode(self) -> str:
        """Return the active Add-HDR mode token: ``copy`` / ``move`` / ``link``.

        Falls back to ``copy`` if the option-box widget hasn't been
        attached yet (e.g. during early init or tests).
        """
        try:
            idx = self.ui.cmb000.option_box.menu.cmb_add_mode.currentIndex()
        except AttributeError:
            return "copy"
        if 0 <= idx < len(self._ADD_MODES):
            return self._ADD_MODES[idx][1]
        return "copy"

    def _import_into_sourceimages(
        self, path: str, src: str, mode: str
    ) -> Optional[tuple]:
        """Copy or move *path* into sourceimages.

        Returns ``(final_path, did_io)`` — ``did_io`` is ``True`` when an
        actual copy/move ran, ``False`` when *path* was already inside
        *src* and we short-circuited. Returns ``None`` if the user
        cancelled the overwrite prompt or the I/O failed (footer already
        set in that case).
        """
        src_norm = os.path.normpath(src).lower()
        path_norm = os.path.normpath(path).lower()
        if path_norm.startswith(src_norm + os.sep):
            return os.path.normpath(path), False

        final_path = os.path.normpath(os.path.join(src, os.path.basename(path)))
        if os.path.exists(final_path):
            if not self._confirm_overwrite(final_path):
                self.ui.footer.setText(f"{mode.capitalize()} cancelled.")
                return None
        op = {"copy": shutil.copy2, "move": shutil.move}[mode]
        try:
            op(path, final_path)
        except OSError as e:
            self.logger.error("%s failed: %s", mode, e)
            self.ui.footer.setText(f"{mode.capitalize()} failed: {e}")
            return None
        return final_path, True

    def _confirm_overwrite(self, target_path: str) -> bool:
        """Ask the user before overwriting an existing file in sourceimages."""
        QtWidgets = self.sb.QtWidgets
        reply = QtWidgets.QMessageBox.question(
            self.ui,
            "Overwrite existing HDR?",
            f"{os.path.basename(target_path)} already exists in sourceimages.\n"
            "Overwrite it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return reply == QtWidgets.QMessageBox.Yes


# ----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("hdr_manager", reload=True)
    ui.show(pos="screen", app_exec=True)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
