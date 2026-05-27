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
    def arnold_available() -> bool:
        """True if the ``mtoa`` plugin can be loaded right now."""
        if cmds is None:
            return False
        try:
            if cmds.pluginInfo("mtoa", query=True, loaded=True):
                return True
            cmds.loadPlugin("mtoa", quiet=True)
            return bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
        except Exception:
            return False

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
        node = self.hdr_env
        return float(cmds.getAttr(f"{node}.intensity")) if node else 1.0

    @intensity.setter
    def intensity(self, value: float) -> None:
        node = self.hdr_env
        if node:
            cmds.setAttr(f"{node}.intensity", float(value))

    @property
    def exposure(self) -> float:
        """Photographic stops (log2) on the skydome's ``aiExposure``.

        Returns 0.0 when the skydome or attribute is absent (older mtoa).
        """
        node = self.hdr_env
        if not node or not cmds.attributeQuery("aiExposure", node=node, exists=True):
            return 0.0
        return float(cmds.getAttr(f"{node}.aiExposure"))

    @exposure.setter
    def exposure(self, stops: float) -> None:
        node = self.hdr_env
        if not node:
            return
        if cmds.attributeQuery("aiExposure", node=node, exists=True):
            cmds.setAttr(f"{node}.aiExposure", float(stops))
        else:
            self.logger.debug("aiExposure not present on %s; skipping.", node)

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
    ) -> Optional[str]:
        """Apply settings to the (lazily-created) skydome network.

        Returns the skydome shape node, or ``None`` if Arnold is unavailable.
        """
        if not self.arnold_available():
            self.logger.warning("Arnold (mtoa) not available — create_network skipped.")
            return None

        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)
        if intensity is not None:
            self.intensity = intensity
        if exposure is not None:
            self.exposure = exposure
        if rotation is not None:
            self.rotation = rotation
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

        self._refresh_combo()
        self._sync_ui_to_scene()

        # Auto-refresh the HDR list when the user opens a new scene —
        # otherwise the combo silently lists sourceimages from the
        # previous workspace.
        mgr = ScriptJobManager.instance()
        mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
        mgr.subscribe("NewSceneOpened", self._on_scene_changed, owner=self)
        mgr.connect_cleanup(self.ui, owner=self)

        # Gate UI affordances on plugin availability.
        if not self.manager.arnold_available():
            self.ui.footer.setText("Arnold (mtoa) plugin not loaded.")

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
                    "Use <b>…</b> to add a new HDR; its option box (▸) picks "
                    "the import mode.",
                    "Adjust <b>Intensity</b> (linear) and <b>Exposure</b> (stops).",
                    "Drag the rotation slider to spin the environment around Y.",
                    "Toggle <b>Visible</b> to show the HDR as a viewport backdrop.",
                    "Press <b>Set HDR</b> to create or refresh the skydome network.",
                ],
                sections=[
                    ("Add HDR modes (… option box)", [
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

    def b001_init(self, widget) -> None:
        """Attach the Add-HDR mode selector to the button's option box."""
        widget.option_box.menu.setTitle("Add HDR Mode")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_add_mode",
            setToolTip=(
                "Choose what the … button does with the picked file:\n"
                "  • Copy — duplicate it into sourceimages (default, scenes stay portable).\n"
                "  • Move — relocate it into sourceimages (original is removed).\n"
                "  • Link — leave it in place and wire the original path directly\n"
                "    (the file won't appear in the dropdown afterward)."
            ),
            addItems=[label for label, _token in self._ADD_MODES],
        )

    def cmb000_init(self, widget) -> None:
        """Wire right-click context menu + auto-refresh on dropdown."""
        # Auto-refresh from disk every time the user opens the dropdown,
        # so newly-saved HDRs appear without hitting the header refresh.
        widget.before_popup_shown.connect(self._refresh_combo)

        # Right-click → context menu (MenuMixin on uitk ComboBox).
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

        for widget, setter, value in (
            (self.ui.slider000, "setSliderPosition", rotation),
            (self.ui.spn_intensity, "setValue", intensity),
            (self.ui.spn_exposure, "setValue", exposure),
            (self.ui.chk000, "setChecked", visible),
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
        )
        self.ui.footer.setText(f"Applied: {os.path.basename(path)}")

    def b001(self) -> None:
        """Add an HDR using the mode selected in the option box.

        Three modes (set via the gear icon next to ``…``):
          - **Copy** — duplicate into sourceimages, wire it up.
          - **Move** — relocate into sourceimages, wire it up.
          - **Link** — wire the original path directly; file stays put
            and won't appear in the combobox dropdown (combo lists
            sourceimages only).

        Copy / Move require a workspace sourceimages directory. Link
        works anywhere.
        """
        path = self._browse_for_hdr(title="Add HDR / EXR")
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

        result = self._import_into_sourceimages(path, src, mode=mode)
        if result is None:
            return  # cancelled or failed (footer already set)
        final_path, did_io = result

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

    def _browse_for_hdr(self, title: str) -> Optional[str]:
        """Run a file-open dialog rooted at sourceimages; return the picked path."""
        QtWidgets = self.sb.QtWidgets
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.ui,
            title,
            EnvUtils.get_env_info("sourceimages") or "",
            self.HDR_FILTER,
        )
        return path or None

    def _add_mode(self) -> str:
        """Return the active Add-HDR mode token: ``copy`` / ``move`` / ``link``.

        Falls back to ``copy`` if the option-box widget hasn't been
        attached yet (e.g. during early init or tests).
        """
        try:
            idx = self.ui.b001.option_box.menu.cmb_add_mode.currentIndex()
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
