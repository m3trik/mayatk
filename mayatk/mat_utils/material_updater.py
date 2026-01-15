# !/usr/bin/python
# coding=utf-8
import os
import logging
from qtpy import QtCore
from typing import List, Dict, Any, Union, Callable

try:
    import pymel.core as pm
except ImportError:
    pass
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils.game_shader import GameShader
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class MaterialUpdater(ptk.LoggingMixin):
    """Updates existing materials with processed textures."""

    @classmethod
    @CoreUtils.undoable
    def update_materials(
        cls,
        materials: List[Any] = None,
        config: Union[str, Dict[str, Any]] = None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Update materials with processed textures.

        Args:
            materials: List of materials to update. If None, finds all StingrayPBS and standardSurface materials.
            config: Configuration preset name (str) or dictionary.
                    If dict, can contain 'preset' key to inherit from a workflow preset.
            verbose: Print verbose output.

        Returns:
            Dict[str, Any]: Results keyed by material name.
        """
        # Configure Logger
        cls.set_log_level(logging.INFO if verbose else logging.WARNING)
        cls.logger.hide_logger_name(True)
        # Workaround for stale cache in LoggingMixin/Maya logging
        if hasattr(cls.logger, "_cache"):
            cls.logger._cache.clear()

        # try:
        if True:
            if materials is None:
                materials = MatUtils.get_scene_mats(
                    node_type=["StingrayPBS", "standardSurface", "aiStandardSurface"]
                )

            if not materials:
                cls.logger.info("No supported materials found.")
                return {}

            # Resolve Config
            cfg_kwargs = ptk.MapRegistry().resolve_config(config)

            # Extract move_to_folder from config
            move_to_folder = cfg_kwargs.get("move_to_folder")

            # Resolve relative paths to sourceimages
            if move_to_folder and not os.path.isabs(move_to_folder):
                try:
                    source_images = EnvUtils.get_env_info("sourceimages")
                    if source_images:
                        move_to_folder = os.path.join(source_images, move_to_folder)
                        cfg_kwargs["move_to_folder"] = move_to_folder
                except Exception as e:
                    cls.logger.warning(f"Could not resolve sourceimages path: {e}")

            # Create Config Object
            config_obj = cfg_kwargs

            # Log Settings
            cls.logger.info("Run Settings", preset="header")
            cls.logger.log_divider()
            for k, v in sorted(config_obj.items()):
                cls.logger.info(f"{k:<25}: {v}")
            cls.logger.log_divider()

            results = {}
            texture_cache = {}

            # Pre-resolve materials
            materials = MatUtils.get_mats(materials)

            cls.logger.info(f"Processing {len(materials)} material(s)...")

            # --- BATCH PROCESSING ---
            run_factory = (
                config_obj.get("convert", True)
                or config_obj.get("optimize", True)
                or config_obj.get("convert_format", True)
                or config_obj.get("convert_type", True)
                or config_obj.get("resize", True)
                or config_obj.get("pack", True)
            )
            processed_sets = {}
            mat_to_files = {}

            # Track globally moved files to prevent "File not found" errors when multiple materials share textures
            globally_moved_files = set()

            if run_factory:
                # 1. Collect all files
                all_files = set()

                for mat in materials:
                    # Get source files
                    file_nodes = pm.listHistory(mat, type="file")
                    files = []
                    for f in file_nodes:
                        try:
                            path = f.fileTextureName.get()
                            resolved = MatUtils.resolve_path(path)
                            if resolved and os.path.isfile(resolved):
                                files.append(resolved)
                            elif resolved:
                                cls.logger.warning(
                                    f"Resolved path is not a file: '{resolved}' for node '{f.name()}'"
                                )
                            elif path:
                                cls.logger.info(
                                    f"Could not resolve path: '{path}' for node '{f.name()}'"
                                )
                        except Exception:
                            continue

                    # Ensure unique paths
                    files = sorted(list(set(files)))

                    if files:
                        mat_to_files[mat] = files
                        all_files.update(files)

                # 2. Batch Process
                if all_files:
                    cls.logger.info("Batch Processing", preset="header")
                    cls.logger.log_divider()
                    cls.logger.info(f"{len(all_files)} unique textures found")
                    cls.logger.info("Starting conversion...")

                    try:
                        # Extract max_workers to avoid double argument error
                        batch_config = config_obj.copy()
                        max_workers = batch_config.pop("max_workers", 1)

                        processed_sets = ptk.MapFactory.prepare_maps(
                            list(all_files),
                            output_dir=move_to_folder,
                            max_workers=max_workers,
                            **batch_config,
                        )
                    except Exception as e:
                        cls.logger.error(f"Batch processing failed: {e}")
                        processed_sets = {}

            if move_to_folder:
                cls.logger.notice(f"Output Folder: {move_to_folder}")

            for mat in materials:
                mat_name = mat.name()
                cls.logger.log_divider()
                cls.logger.info(f"Material: {mat_name}")

                # Get source files
                if run_factory and mat in mat_to_files:
                    files = mat_to_files[mat]
                else:
                    file_nodes = pm.listHistory(mat, type="file")
                    if not file_nodes:
                        cls.logger.info(f"No file nodes found connected to {mat_name}.")
                        continue

                    files = []
                    for f in file_nodes:
                        try:
                            path = f.fileTextureName.get()
                            resolved = MatUtils.resolve_path(path)
                            if resolved and os.path.isfile(resolved):
                                files.append(resolved)
                            elif resolved:
                                cls.logger.warning(
                                    f"Resolved path is not a file: '{resolved}' for node '{f.name()}'"
                                )
                            elif path:
                                cls.logger.info(
                                    f"Could not resolve path: '{path}' for node '{f.name()}'"
                                )
                        except Exception:
                            continue

                    # Ensure unique paths
                    files = sorted(list(set(files)))

                    if not files:
                        cls.logger.warning(
                            f"Found {len(file_nodes)} file nodes on {mat_name}, but no valid paths could be resolved."
                        )
                        continue

                # Determine if we need to run the factory
                processed_files = []

                if run_factory:
                    cache_key = tuple(sorted(files))

                    # 1. Check Cache
                    if cache_key in texture_cache:
                        cls.logger.log_raw(f"  Using cached maps for {mat_name}")
                        processed_files = texture_cache[cache_key]

                    else:
                        # 2. Try Batch Lookup
                        # We only use batch results if the material's files belong to a SINGLE set.
                        # If they span multiple sets, we must re-process to allow cross-set packing.
                        batch_success = False

                        # Filter out common environment maps that shouldn't be treated as texture sets
                        # These are often connected to slots but aren't part of the material's identity
                        IGNORED_ENV_SETS = {
                            "ibl_brdf_lut",
                            "diffuse_cube",
                            "specular_cube",
                        }

                        local_sets = {}

                        if processed_sets and isinstance(processed_sets, dict):
                            raw_sets = ptk.MapFactory.group_textures_by_set(files)
                            local_sets = {
                                k: v
                                for k, v in raw_sets.items()
                                if k not in IGNORED_ENV_SETS
                            }

                            # If filtering removed everything (e.g. material only has env maps),
                            # restore raw sets to avoid having 0 sets (though this is a weird edge case)
                            if not local_sets and raw_sets:
                                local_sets = raw_sets

                            if len(local_sets) == 1:
                                base_name = list(local_sets.keys())[0]
                                if base_name in processed_sets:
                                    processed_files = processed_sets[base_name]
                                    batch_success = True
                            elif len(local_sets) > 1:
                                # Attempt to find a common root set (detecting unknown maps like Curvature as part of main set)
                                keys = sorted(local_sets.keys(), key=len)
                                root = keys[0]
                                is_subset = True
                                for k in keys[1:]:
                                    if not k.startswith(root):
                                        is_subset = False
                                        break

                                if is_subset and root in processed_sets:
                                    processed_files = processed_sets[root]
                                    batch_success = True
                                    cls.logger.info(
                                        f"Merged {len(local_sets)} sets into '{root}' (ignoring unknown suffixes)."
                                    )

                        # 3. Manual Process (Re-process)
                        if not batch_success:
                            if len(local_sets) > 1:
                                cls.logger.info(
                                    f"Material uses textures from {len(local_sets)} different sets. Re-processing as single set."
                                )
                                for s_name, s_files in local_sets.items():
                                    file_names = [os.path.basename(f) for f in s_files]
                                    cls.logger.info(f"  - Set '{s_name}': {file_names}")
                            else:
                                cls.logger.info(f"Preparing maps...")

                            try:
                                # Extract max_workers to avoid collision with kwargs
                                manual_config = config_obj.copy()
                                max_workers = manual_config.pop("max_workers", 1)

                                processed_files = ptk.MapFactory.prepare_maps(
                                    files,
                                    output_dir=move_to_folder,
                                    group_by_set=False,  # Always force single set for per-material context
                                    max_workers=max_workers,
                                    **manual_config,
                                )
                                texture_cache[cache_key] = processed_files
                            except Exception as e:
                                cls.logger.error(f"Error preparing maps: {e}")
                                continue
                else:
                    cls.logger.info(f"Skipping factory (using existing textures)")
                    processed_files = files

                if not processed_files:
                    continue

                # Move files if requested
                if move_to_folder:
                    import shutil

                    target_folder = move_to_folder
                    transfer_mode = config_obj.get("transfer_mode", "move")

                    files_to_move = []
                    files_to_copy = []
                    files_to_keep = []

                    copy_all = config_obj.get("copy_all", False)
                    target_folder_norm = (
                        os.path.normpath(target_folder) if target_folder else None
                    )

                    # Determine action for each file
                    source_set = set(os.path.normpath(f) for f in files if f)

                    for f in processed_files:
                        if not f:
                            continue

                        # Check existence (if not found, we can't move/copy it)
                        if not os.path.exists(f):
                            # It might have been moved already?
                            # If so, we hope it's handled by globally_moved_files logic or kept as is?
                            # For now, stick to existing patterns.
                            if f in globally_moved_files:
                                # It was moved to target already (presumably)
                                # But we need the NEW path.
                                # Wait, globally_moved_files stores the SOURCE path that was moved.
                                # This is getting complicated.
                                pass
                            # Just continue if it doesn't exist
                            # cls.logger.warning(f"File not found: {f}")
                            # files_to_keep.append(f) # Keep the broken path?
                            continue

                        f_norm = os.path.normpath(f)

                        # Check if already in target folder
                        if (
                            target_folder_norm
                            and os.path.normpath(os.path.dirname(f))
                            == target_folder_norm
                        ):
                            files_to_keep.append(f)
                            continue

                        # Determine if this is a Source File or a Generated/New File
                        is_source = f_norm in source_set

                        # Decision Logic
                        if transfer_mode == "copy":
                            if is_source:
                                files_to_copy.append(f)  # Copy source file
                            else:
                                files_to_move.append(f)  # Move generated file
                        elif transfer_mode == "move":
                            files_to_move.append(f)  # Move everything
                        else:
                            # Fallback / Default (treat as move)
                            files_to_move.append(f)

                    # --- EXECUTE MOVES ---
                    # Filter out files that have already been moved in this session
                    files_to_move = [
                        f for f in files_to_move if f not in globally_moved_files
                    ]

                    # Filter out system files
                    maya_location = os.environ.get("MAYA_LOCATION", "").replace(
                        "\\", "/"
                    )
                    if maya_location:
                        files_to_move = [
                            f
                            for f in files_to_move
                            if not os.path.normpath(f)
                            .replace("\\", "/")
                            .startswith(maya_location)
                        ]

                    moved_files = []
                    if files_to_move:
                        try:
                            # Double check existence before call
                            valid_moves = [
                                f for f in files_to_move if os.path.exists(f)
                            ]
                            if valid_moves:
                                res = ptk.FileUtils.move_file(
                                    valid_moves,
                                    target_folder,
                                    overwrite=True,
                                    create_dir=True,
                                )
                                if isinstance(res, str):
                                    moved_files = [res]
                                else:
                                    moved_files = res

                                globally_moved_files.update(valid_moves)
                        except Exception as e:
                            cls.logger.error(f"Error moving files: {e}")
                            # Fallback: assume they didn't move
                            files_to_keep.extend(files_to_move)

                    # --- EXECUTE COPIES ---
                    copied_files = []
                    if files_to_copy:
                        try:
                            # Ensure target exists
                            if not os.path.exists(target_folder):
                                os.makedirs(target_folder)

                            for f in files_to_copy:
                                if not os.path.exists(f):
                                    continue

                                # Use ptk.FileUtils.copy_file (preferred) or shutil fallback
                                # Note: ptk copy_file returns the new path
                                try:
                                    if hasattr(ptk.FileUtils, "copy_file"):
                                        new_path = ptk.FileUtils.copy_file(
                                            f,
                                            target_folder,
                                            overwrite=True,
                                            create_dir=False,
                                        )
                                    else:
                                        # Fallback to manual shutil if method missing
                                        dest = os.path.join(
                                            target_folder, os.path.basename(f)
                                        )
                                        shutil.copy2(f, dest)
                                        new_path = dest

                                    copied_files.append(new_path)
                                except Exception as ie:
                                    cls.logger.error(f"Failed to copy {f}: {ie}")
                                    files_to_keep.append(
                                        f
                                    )  # Fallback to keeping old path

                        except Exception as e:
                            cls.logger.error(f"Error copying files: {e}")

                    # Reconstruct processed_files
                    processed_files = files_to_keep + moved_files + copied_files

                # Disconnect existing attributes driven by these files to prevent stale connections
                cls.disconnect_associated_attributes(mat, files, config=config_obj)

                # Update network
                connected_maps = cls.update_network(mat, processed_files, config_obj)

                results[mat_name] = {
                    "textures": processed_files,
                    "connected": connected_maps,
                }

            return results

    @classmethod
    def disconnect_associated_attributes(cls, material, file_paths, config=None):
        """Disconnects PBR attributes if they are driven by the specified files.

        This ensures that if a file's map type changes (e.g. Base Color -> Emissive),
        the old connection (Base Color) is removed.
        """
        if config and config.get("dry_run", False):
            cls.logger.info("[Dry Run] Skipping disconnection of attributes.")
            return

        target_paths = set(os.path.normpath(p) for p in file_paths if p)

        # Identify file nodes that match our paths
        matching_nodes = set()
        for node in pm.listHistory(material, type="file"):
            try:
                path = MatUtils.resolve_path(node.fileTextureName.get())
                if path and os.path.normpath(path) in target_paths:
                    matching_nodes.add(node)
            except Exception:
                continue

        if not matching_nodes:
            return

        # Define attributes to check
        node_type = material.nodeType()
        attributes = []
        if node_type == "standardSurface":
            attributes = [
                "baseColor",
                "metalness",
                "specularRoughness",
                "normalCamera",
                "emissionColor",
                "opacity",
                "transmission",
                "specularColor",
            ]
        elif node_type == "StingrayPBS":
            attributes = [
                "TEX_color_map",
                "TEX_metallic_map",
                "TEX_roughness_map",
                "TEX_normal_map",
                "TEX_emissive_map",
                "TEX_ao_map",
                "TEX_specular_map",
                "TEX_glossiness_map",
                "opacity",
            ]

        for attr_name in attributes:
            if not material.hasAttr(attr_name):
                continue

            attr = material.attr(attr_name)
            inputs = pm.listConnections(attr, source=True, destination=False)
            if inputs:
                input_node = inputs[0]
                # Check if input_node is one of our matching nodes OR driven by them
                # We check history of input_node (including itself)
                history = pm.listHistory(input_node)
                if any(n in matching_nodes for n in history):
                    # Disconnect
                    cls.logger.info(
                        f"Disconnecting {attr_name} (driven by updated file)"
                    )
                    # Get the plug
                    input_plugs = pm.listConnections(
                        attr, source=True, plugs=True, destination=False
                    )
                    if input_plugs:
                        pm.disconnectAttr(input_plugs[0], attr)

    @classmethod
    def update_network(cls, material, texture_paths, config) -> Dict[str, str]:
        """Connect processed textures to the material.

        Returns:
            Dict[str, str]: Map of connected map types to file paths.
        """
        # Build inventory: Map Type -> Path
        inventory = {}
        for path in texture_paths:
            map_type = ptk.MapFactory.resolve_map_type(path)
            cls.logger.info(f"  Resolving {os.path.basename(path)} -> {map_type}")

            if map_type:
                inventory[map_type] = path

        # Filter redundant maps (in-place)
        ptk.MapFactory.filter_redundant_maps(inventory)

        if config.get("dry_run", False):
            cls.logger.info("[Dry Run] Skipping connection.")
            return inventory

        # Use GameShader for connections to avoid duplication
        gs = GameShader()
        node_type = material.nodeType()

        for map_type, path in inventory.items():
            try:
                if node_type == "standardSurface":
                    gs.connect_standard_surface_nodes(path, map_type, material)
                elif node_type == "StingrayPBS":
                    gs.connect_stingray_nodes(path, map_type, material)
            except Exception as e:
                msg = f"  Error connecting {map_type}: {e}"
                cls.logger.error(msg)

        return inventory


class MaterialUpdaterSlots(MaterialUpdater):
    msg_intro = "Update existing materials with processed textures."
    msg_completed = '<br><hl style="color:rgb(0, 255, 255);"><b>COMPLETED.</b></hl>'

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.material_updater

        # Setup logging
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        try:
            sourceimages = EnvUtils.get_env_info("sourceimages")
            info = ptk.truncate(
                f"<br><font color='#888'>Source Images: {sourceimages}</font><br>",
                "middle",
            )
            self.ui.txt001.setText(self.msg_intro + info)
        except Exception:
            self.ui.txt001.setText(self.msg_intro)

    def header_init(self, widget):
        """Format global options in the header menu."""
        widget.menu.setTitle("Global Settings:")

        # Selection Mode
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_selection_mode",
            addItems=["Selected Materials", "All Scene Materials"],
            setToolTip="Choose which materials to process.",
        )
        widget.menu.add("Separator", setTitle="Processing")
        # Convert Format
        cmb_format = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_convert_format",
            setToolTip="Convert texture file formats.",
        )
        cmb_format.addItem("Convert Format: None", None)
        for ext in ptk.ImgUtils.texture_file_types:
            cmb_format.addItem(f"Convert Format: {ext}", ext)

        # Max Size
        cmb_size = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_max_size",
            setToolTip="Maximum texture size.",
        )
        cmb_size.addItem("Max Size: None", None)
        for size in [512, 1024, 2048, 4096, 8192]:
            cmb_size.addItem(f"Max Size: {size}", size)

        # Mask Map Scale
        cmb_scale = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_mask_scale",
            setToolTip="Scale factor for Mask Maps.",
        )
        for scale in [1.0, 0.5, 0.25, 0.125]:
            cmb_scale.addItem(f"Mask Scale: {scale}", scale)
        # Force Packed Maps
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_force_packed",
            setText="Force Packed Maps",
            setToolTip="Force generation of packed maps (ORM, MSAO) even if some source maps are missing.",
        )
        # Use Input Fallbacks
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_input_fallbacks",
            setText="Use Input Fallbacks",
            setChecked=True,
            setToolTip="Allow generating maps from alternative inputs (e.g. create Base Color from Existing Diffuse).",
        )
        # Use Output Fallbacks
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_output_fallbacks",
            setText="Use Output Fallbacks",
            setChecked=True,
            setToolTip="Allow substituting missing output maps with alternatives (e.g. use AO map alone if Mask Map cannot be generated). Ignored if Force Packed Maps is enabled.",
        )
        # Connect Force Packed to disable Output Fallbacks
        widget.menu.chk_force_packed.toggled.connect(
            lambda state: widget.menu.chk_output_fallbacks.setDisabled(state)
        )
        # Dry Run
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_dry_run",
            setText="Dry Run",
            setToolTip="Simulate the process without making changes.",
        )
        widget.menu.add("Separator", setTitle="File Management")
        # File Transfer Mode
        cmb_transfer = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_transfer_mode",
            setToolTip="How to handle files when an Output Folder is specified.",
        )
        cmb_transfer.addItem("Copy All to Output", "copy")
        cmb_transfer.addItem("Move All to Output", "move")
        cmb_transfer.addItem("Use Existing Folders", "none")

        # Output Folder
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_move_to",
            setPlaceholderText="Output Folder (Optional)",
            setToolTip="Folder to move/copy processed files to.",
        )

        # Disable Move To field when "Use Existing" is selected
        def _update_move_to_state():
            mode = cmb_transfer.currentData()
            widget.menu.txt_move_to.setEnabled(mode != "none")

        cmb_transfer.currentIndexChanged.connect(_update_move_to_state)
        # Initialize state
        _update_move_to_state()

        # Archive Folder
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_old_files",
            setPlaceholderText="Archive To (Optional)",
            setToolTip="Optional: Folder to move original files to.",
        )

    @property
    def selection_mode(self):
        return self.ui.cmb_selection_mode.currentText()

    @property
    def move_to_folder(self):
        return self.ui.txt_move_to.text() or None

    @property
    def max_size(self):
        return self.ui.cmb_max_size.currentData()

    @property
    def mask_map_scale(self):
        return self.ui.cmb_mask_scale.currentData()

    @property
    def output_extension(self):
        return self.ui.cmb_convert_format.currentData()

    @property
    def old_files_folder(self):
        return self.ui.txt_old_files.text() or None

    def cmb001_init(self, widget):
        """Initialize Presets"""
        if not widget.is_initialized:
            widget.restore_state = True
            # Populate presets
            presets = ptk.MapRegistry().get_workflow_presets()
            widget.clear()
            for name, settings in presets.items():
                widget.addItem(name)
                description = settings.get("description")
                if description:
                    widget.setItemData(
                        widget.count() - 1, description, QtCore.Qt.ToolTipRole
                    )

    def b001(self, widget):
        """Update Materials"""
        config_name = self.ui.cmb001.currentText()

        menu = self.ui.header.menu
        dry_run = menu.chk_dry_run.isChecked()
        transfer_mode = menu.cmb_transfer_mode.currentData()
        force_packed = menu.chk_force_packed.isChecked()
        use_input_fallbacks = menu.chk_input_fallbacks.isChecked()
        use_output_fallbacks = menu.chk_output_fallbacks.isChecked()

        max_size = self.max_size

        materials = None
        if self.selection_mode == "Selected Materials":
            materials = pm.selected()
            if not materials:
                self.ui.txt001.append("No materials selected.")
                return

        self.ui.txt001.clear()
        self.ui.txt001.append(f"Starting update with preset: {config_name}...")

        try:
            # Build config dictionary
            config = {
                "preset": config_name,
                "max_size": max_size,
                "mask_map_scale": self.mask_map_scale,
                "output_extension": self.output_extension,
                "move_to_folder": (
                    self.move_to_folder if transfer_mode != "none" else None
                ),
                "transfer_mode": transfer_mode,
                "old_files_folder": self.old_files_folder,
                "force_packed_maps": force_packed,
                "use_input_fallbacks": use_input_fallbacks,
                "use_output_fallbacks": use_output_fallbacks,
                "dry_run": dry_run,
            }

            self.update_materials(
                materials=materials,
                config=config,
                verbose=True,
            )
            self.ui.txt001.append(self.msg_completed)
        except Exception as e:
            self.ui.txt001.append(f"<br><font color='red'>ERROR: {e}</font>")
            import traceback

            self.ui.txt001.append(traceback.format_exc())


if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("material_updater", reload=True)
    ui.show(pos="screen", app_exec=True)
