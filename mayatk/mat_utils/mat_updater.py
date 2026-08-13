# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import os
import logging
from qtpy import QtCore
from typing import List, Dict, Any, Optional, Union, Callable

import pythontk as ptk
from uitk.switchboard import Cancelable
# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils.game_shader import GameShader
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class MatUpdater(ptk.LoggingMixin):
    """Updates existing materials with processed textures."""

    #: Everything this tool knows about a material node type, in ONE place:
    #: ``connect`` is the :class:`GameShader` method that wires a map in, and
    #: ``attrs`` are the plugs :meth:`disconnect_associated_attributes` clears
    #: before the rewire. Keeping them together is the point -- they were two
    #: structures keyed by the same thing, and that is precisely how the bug
    #: this replaces arose: ``aiStandardSurface`` sat in the supported-type
    #: list with no branch in ``update_network``, so every map it reported as
    #: connected was a silent no-op, and with an output folder set the textures
    #: were MOVED out from under file nodes that were never repathed. A type
    #: added here now brings its wiring AND its teardown, or neither. Arnold
    #: shaders are ``ArnoldBridge``'s alone (they are generated bridges, which
    #: is why it excludes them from its own material queries).
    CONNECTORS = {
        "standardSurface": {
            "connect": "connect_standard_surface_nodes",
            "attrs": (
                "baseColor",
                "metalness",
                "specularRoughness",
                "normalCamera",
                "emissionColor",
                "opacity",
                "transmission",
                "specularColor",
            ),
        },
        "StingrayPBS": {
            "connect": "connect_stingray_nodes",
            "attrs": (
                "TEX_color_map",
                "TEX_metallic_map",
                "TEX_roughness_map",
                "TEX_normal_map",
                "TEX_emissive_map",
                "TEX_ao_map",
                "TEX_specular_map",
                "TEX_glossiness_map",
                "opacity",
            ),
        },
    }
    SUPPORTED_MAT_TYPES = tuple(CONNECTORS)

    @classmethod
    @CoreUtils.undoable
    def update_materials(
        cls,
        materials: List[Any] = None,
        config: Union[str, Dict[str, Any]] = None,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Update materials with processed textures.

        Args:
            materials: List of materials to update. If None, finds every scene
                material of a type in :attr:`CONNECTORS`.
            config: Configuration preset name (str) or dictionary.
                    If dict, can contain 'preset' key to inherit from a workflow preset.
            verbose: Print verbose output.
            progress_callback: ``cb(current, total, message)`` — invoked
                per material in the apply loop.

        Returns:
            Dict[str, Any]: Results keyed by material name.
        """
        # Configure Logger
        cls.set_log_level(logging.INFO if verbose else logging.WARNING)
        cls.logger.hide_logger_name(True)
        # Workaround for stale cache in LoggingMixin/Maya logging
        if hasattr(cls.logger, "_cache"):
            cls.logger._cache.clear()

        # ``log_box`` / ``log_group`` write through ``log_raw``, which bypasses
        # level filtering BY DESIGN — so the run report has to gate itself or a
        # non-verbose caller gets it anyway. That is not hypothetical: both
        # DCCs' Scene Exporter runs this as its ``convert_textures`` task with
        # the default ``verbose=False``, and an export would otherwise carry a
        # banner, the whole settings dump and a block per material in its log.
        report = cls.logger.isEnabledFor(logging.INFO)

        # try:
        if True:
            if materials is None:
                materials = MatUtils.get_scene_mats(
                    node_type=list(cls.SUPPORTED_MAT_TYPES)
                )

            if not materials:
                cls.logger.info("No supported materials found.")
                return {}

            # Resolve Config
            cfg_kwargs = ptk.MapRegistry().resolve_config(config)

            # Extract move_to_folder from config
            move_to_folder = cfg_kwargs.get("move_to_folder")

            # Resolve a subdirectory entry to sourceimages. Through the shared
            # primitive, so a driveless "/optimized" in a preset stays a
            # SUBDIRECTORY rather than resolving to the current drive's root --
            # which is what os.path.isabs would have called it on Windows.
            # Guarded on a set value: an absent move_to_folder must stay absent,
            # not become sourceimages itself (that would move every map there).
            if move_to_folder:
                try:
                    resolved = ptk.FileUtils.resolve_output_dir(
                        move_to_folder, EnvUtils.get_env_info("sourceimages")
                    )
                except Exception as e:
                    cls.logger.warning(f"Could not resolve sourceimages path: {e}")
                    resolved = None
                if resolved:
                    move_to_folder = resolved
                    cfg_kwargs["move_to_folder"] = move_to_folder

            # Resolve opt-in sibling discovery into a concrete directory for the
            # factory. The mayatk-level flag ``discover_sourceimages`` maps to the
            # generic ``discover_dir`` param understood by MapFactory.prepare_maps.
            if cfg_kwargs.pop("discover_sourceimages", False) and not cfg_kwargs.get(
                "discover_dir"
            ):
                try:
                    source_images = EnvUtils.get_env_info("sourceimages")
                    if source_images:
                        cfg_kwargs["discover_dir"] = source_images
                    else:
                        cls.logger.warning(
                            "Discovery enabled but no sourceimages folder was found."
                        )
                except Exception as e:
                    cls.logger.warning(
                        f"Could not resolve sourceimages for discovery: {e}"
                    )

            # Create Config Object
            config_obj = cfg_kwargs

            results = {}
            texture_cache = {}

            # Pre-resolve materials. Force string returns — un-migrated
            # ``MatUtils.get_mats`` defaults to node wrapping which the
            # downstream cmds.* calls don't accept in Maya 2025.
            materials = MatUtils.get_mats(materials, as_strings=True)

            # Drop what cannot be wired BEFORE any file is touched. The per
            # material guard in ``update_network`` keeps the report honest, but
            # by then the textures have already been processed and -- with an
            # output folder set -- MOVED, out from under file nodes this tool
            # will never repath. Caller-supplied materials are the path that
            # reaches here unfiltered (the panel filters its own selection).
            wireable, unwireable = [], []
            for m in materials:
                bucket = wireable if cmds.nodeType(m) in cls.CONNECTORS else unwireable
                bucket.append(m)
            if unwireable:
                materials = wireable
                cls.logger.warning(
                    "Skipped "
                    + ", ".join(
                        sorted(
                            f"{CoreUtils.short_name(m)} ({cmds.nodeType(m)})"
                            for m in unwireable
                        )
                    )
                    + f" -- no connector (supported: {', '.join(cls.CONNECTORS)})."
                )
                if not materials:
                    return {}

            # Run banner + settings. Every log record renders as its own
            # paragraph in the output panel, so a line-per-setting dump reads
            # as ~20 blank-line-separated sections. Both of these go out as a
            # single record — a box for the header, one group for the settings
            # list — so the vertical space marks *sections*, not values.
            if report:
                # ``resolve_config`` consumes the 'preset' key, so read the name
                # off the caller's config for the banner.
                preset_name = (
                    config
                    if isinstance(config, str)
                    else (config or {}).get("preset") or "custom"
                )
                cls.logger.log_box(
                    "MATERIAL UPDATE",
                    [
                        f"Preset     : {preset_name}",
                        f"Materials  : {len(materials)}",
                        f"Output     : {move_to_folder or 'in place'}",
                    ]
                    + (
                        ["Mode       : DRY RUN (nothing written)"]
                        if config_obj.get("dry_run")
                        else []
                    ),
                )
                cls.logger.log_group(
                    "Run Settings",
                    [
                        f"{k:<24}: {v}"  # 24 == longest key in a resolved preset
                        for k, v in sorted(config_obj.items())
                        if k != "description"
                    ],
                )

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
                    # Get source files. ``cmds.listHistory`` has no ``type``
                    # flag; filter manually.
                    history = cmds.listHistory(mat) or []
                    file_nodes = [h for h in history if cmds.nodeType(h) == "file"]
                    files = []
                    for f in file_nodes:
                        try:
                            path = cmds.getAttr(f"{f}.fileTextureName")
                            resolved = MatUtils.resolve_path(path)
                            if resolved and os.path.isfile(resolved):
                                files.append(resolved)
                            elif resolved:
                                cls.logger.warning(
                                    f"Resolved path is not a file: '{resolved}' for node '{f}'"
                                )
                            elif path:
                                cls.logger.info(
                                    f"Could not resolve path: '{path}' for node '{f}'"
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
                    if report:
                        cls.logger.log_group(
                            "Batch Processing",
                            [
                                f"{len(all_files)} unique texture(s) across "
                                f"{len(mat_to_files)} material(s)",
                                "Preparing maps...",
                            ],
                        )

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

            total_mats = len(materials)
            connected_total = 0
            for mat_index, mat in enumerate(materials):
                if progress_callback:
                    progress_callback(mat_index, total_mats, f"Updating: {mat}")
                mat_name = CoreUtils.short_name(mat)
                mat_link = cls.logger.log_link(mat_name, "select", node=str(mat))
                # Per-material detail accumulates here and is emitted as ONE
                # grouped record when the material is done — line-by-line
                # logging puts a paragraph break between every texture.
                mat_log: List[str] = []

                # Get source files
                if run_factory and mat in mat_to_files:
                    files = mat_to_files[mat]
                else:
                    history = cmds.listHistory(mat) or []
                    file_nodes = [h for h in history if cmds.nodeType(h) == "file"]
                    if not file_nodes:
                        cls.logger.info(f"No file nodes found connected to {mat_link}.")
                        continue

                    files = []
                    for f in file_nodes:
                        try:
                            path = cmds.getAttr(f"{f}.fileTextureName")
                            f_name = CoreUtils.short_name(f)
                            resolved = MatUtils.resolve_path(path)
                            if resolved and os.path.isfile(resolved):
                                files.append(resolved)
                            elif resolved:
                                node_link = cls.logger.log_link(
                                    f_name, "select", node=f_name
                                )
                                cls.logger.warning(
                                    f"Resolved path is not a file: '{resolved}' for node {node_link}"
                                )
                            elif path:
                                node_link = cls.logger.log_link(
                                    f_name, "select", node=f_name
                                )
                                cls.logger.info(
                                    f"Could not resolve path: '{path}' for node {node_link}"
                                )
                        except Exception:
                            continue

                    # Ensure unique paths
                    files = sorted(list(set(files)))

                    if not files:
                        cls.logger.warning(
                            f"Found {len(file_nodes)} file nodes on {mat_link}, but no valid paths could be resolved."
                        )
                        continue

                # Determine if we need to run the factory
                processed_files = []

                if run_factory:
                    cache_key = tuple(sorted(files))

                    # 1. Check Cache
                    if cache_key in texture_cache:
                        mat_log.append("Using cached maps (shared texture set).")
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
                                    mat_log.append(
                                        f"Merged {len(local_sets)} sets into '{root}' (ignoring unknown suffixes)."
                                    )

                        # 3. Manual Process (Re-process)
                        if not batch_success:
                            if len(local_sets) > 1:
                                mat_log.append(
                                    f"Textures span {len(local_sets)} sets — re-processing as one:"
                                )
                                mat_log.extend(
                                    f"  {s_name}: "
                                    + ", ".join(
                                        sorted(os.path.basename(f) for f in s_files)
                                    )
                                    for s_name, s_files in local_sets.items()
                                )
                            else:
                                # Distinct from the batch pass's "Preparing
                                # maps..." — this set wasn't reusable from it.
                                mat_log.append("Re-processed for this material.")

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
                    mat_log.append("Skipping factory (using existing textures).")
                    processed_files = files

                if not processed_files:
                    continue

                # Move files if requested
                if move_to_folder and config_obj.get("dry_run", False):
                    mat_log.append(f"[Dry Run] Skipping move/copy to '{move_to_folder}'.")
                elif move_to_folder:
                    import shutil

                    target_folder = move_to_folder
                    # ``copy_all`` is the legacy spelling of transfer_mode="copy"
                    # (pre-combo configs/presets may still carry it); explicit
                    # transfer_mode wins.
                    transfer_mode = config_obj.get(
                        "transfer_mode",
                        "copy" if config_obj.get("copy_all") else "move",
                    )

                    files_to_move = []
                    files_to_copy = []
                    files_to_keep = []

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
                disconnected = cls.disconnect_associated_attributes(
                    mat, files, config=config_obj
                )
                if disconnected:
                    mat_log.append(f"Disconnected: {', '.join(disconnected)}")

                # Update network
                connected_maps = cls.update_network(mat, processed_files, config_obj)

                results[mat_name] = {
                    "textures": processed_files,
                    "connected": connected_maps,
                }

                # One record per material: the header plus every map it ended
                # up with, as a single chunk.
                if report:
                    mat_log.extend(
                        f"{map_type:<24} {os.path.basename(path)}"
                        for map_type, path in connected_maps.items()
                    )
                    cls.logger.log_group(
                        f"Material: {mat_link}", mat_log or ["No maps connected."]
                    )
                connected_total += len(connected_maps)

            if progress_callback and total_mats:
                progress_callback(total_mats, total_mats, "Done")

            if report:
                cls.logger.log_box(
                    "UPDATE COMPLETE",
                    [
                        f"Materials updated : {len(results)}/{total_mats}",
                        f"Maps connected    : {connected_total}",
                    ],
                    level="SUCCESS",
                )
            return results

    @classmethod
    def disconnect_associated_attributes(cls, material, file_paths, config=None):
        """Disconnects PBR attributes if they are driven by the specified files.

        This ensures that if a file's map type changes (e.g. Base Color -> Emissive),
        the old connection (Base Color) is removed.

        Returns:
            List[str]: Names of the attributes that were disconnected. Reported
                by the caller as one line in the material's log chunk rather
                than logged here per attribute.
        """
        if config and config.get("dry_run", False):
            return []

        target_paths = set(os.path.normpath(p) for p in file_paths if p)

        # Identify file nodes that match our paths
        matching_nodes = set()
        history = cmds.listHistory(material) or []
        for node in [h for h in history if cmds.nodeType(h) == "file"]:
            try:
                path = MatUtils.resolve_path(cmds.getAttr(f"{node}.fileTextureName"))
                if path and os.path.normpath(path) in target_paths:
                    matching_nodes.add(node)
            except Exception:
                continue

        if not matching_nodes:
            return []

        # Define attributes to check
        # Same registry the rewire uses, so a node type cannot be wired by one
        # and left un-torn-down by the other.
        spec = cls.CONNECTORS.get(cmds.nodeType(material)) or {}
        attributes = spec.get("attrs", ())

        disconnected_attrs = []
        for attr_name in attributes:
            if not cmds.attributeQuery(attr_name, node=material, exists=True):
                continue

            # Packed maps (MSAO/ORM) wire their unpacked channels into the
            # compound's *child* plugs (e.g. TEX_ao_mapR/G/B), and a parent-level
            # query won't report those (so the stale packed connection survives
            # the rewire). Gather the parent plug plus any R/G/B/X/Y/Z children so
            # every incoming connection is caught.
            plug_names = [attr_name]
            for suffix in ("R", "G", "B", "X", "Y", "Z"):
                child = f"{attr_name}{suffix}"
                if cmds.attributeQuery(child, node=material, exists=True):
                    plug_names.append(child)

            # Collect unique (src_plug, dest_plug) pairs; a parent-compound query
            # may also echo child connections, so dedupe before disconnecting.
            pairs = set()
            for plug_name in plug_names:
                conns = (
                    cmds.listConnections(
                        f"{material}.{plug_name}",
                        source=True,
                        destination=False,
                        plugs=True,
                        connections=True,
                    )
                    or []
                )
                # Flat [destPlug, srcPlug, destPlug, srcPlug, ...].
                for i in range(0, len(conns), 2):
                    pairs.add((conns[i + 1], conns[i]))

            disconnected = False
            for src_plug, dest_plug in pairs:
                # Disconnect only plugs whose source traces back to an updated
                # file — directly, or through an intermediate node such as the
                # reverse used to invert smoothness into roughness.
                src_node = src_plug.split(".")[0]
                history = cmds.listHistory(src_node) or []
                if any(n in matching_nodes for n in history) and cmds.isConnected(
                    src_plug, dest_plug
                ):
                    cmds.disconnectAttr(src_plug, dest_plug)
                    disconnected = True

            if disconnected:  # one entry per attribute, not per child plug
                disconnected_attrs.append(attr_name)

        return disconnected_attrs

    @classmethod
    def update_network(cls, material, texture_paths, config) -> Dict[str, str]:
        """Connect processed textures to the material.

        Returns:
            Dict[str, str]: Map of connected map types to file paths. The
                caller lists these as the material's log chunk — resolution is
                not logged per file here, which would put a paragraph break
                between every texture in a set.
        """
        # Build inventory: Map Type -> Path
        inventory = {}
        unresolved = []
        for path in texture_paths:
            map_type = ptk.MapFactory.resolve_map_type(path)
            if map_type:
                inventory[map_type] = path
            else:
                unresolved.append(os.path.basename(path))

        # Filter redundant maps (in-place). Pass the config so packed/loose
        # redundancy follows the target preset: an unpacked preset (e.g. PBR
        # Metallic/Roughness, mask_map=False) drops a redundant MSAO/ORM/MRAO
        # in favor of the separate Metallic/Roughness/AO maps, rather than the
        # packed map superseding them. It also picks between RIVAL packings —
        # a glTF 2.0 pass keeps the ORM and retires an HDRP mask map.
        report = ptk.MapFactory.filter_redundant_maps(inventory, config=config)

        # A map in the folder that never reaches the material reads as a bug
        # unless the reason is stated — the reported case was a set carrying
        # both an ORM and an MSAO. Same grouping and naming rationale as the
        # unresolved block below: one record, named for the material because
        # it lands before the caller's per-material chunk.
        dropped = report.get("dropped") or {}
        if dropped and cls.logger.isEnabledFor(logging.INFO):
            cls.logger.log_group(
                f"Redundant map on {CoreUtils.short_name(material)} (not connected)",
                [f"{t:<24} {why}" for t, why in sorted(dropped.items())],
            )

        # One grouped record, not one warning per file. ``log_group`` writes
        # through ``log_raw``, which bypasses level filtering, so the WARNING
        # gate is applied here.
        if unresolved and cls.logger.isEnabledFor(logging.WARNING):
            # Named for the material: this lands *before* the caller's
            # per-material chunk, so it has to identify itself.
            cls.logger.log_group(
                f"Unrecognized map type on {CoreUtils.short_name(material)} "
                "(not connected)",
                sorted(unresolved),
                level="WARNING",
            )

        # Resolve the connector BEFORE the dry-run return: a material this tool
        # cannot wire has nothing to report in either mode.
        node_type = cmds.nodeType(material)
        spec = cls.CONNECTORS.get(node_type)
        if spec is None:
            cls.logger.warning(
                f"Cannot update {CoreUtils.short_name(material)}: no connector "
                f"for node type '{node_type}' "
                f"(supported: {', '.join(cls.CONNECTORS)})."
            )
            return {}

        if config.get("dry_run", False):
            return inventory

        # Use GameShader for connections to avoid duplication
        connect = getattr(GameShader(), spec["connect"])

        # Only what actually landed. The connectors return False for a slot the
        # material's graph does not have -- Stingray slots are graph-dependent
        # and a probe-and-skip is by design -- and the caller both counts this
        # and lists it as the material's connected maps, so returning the
        # planned inventory reported skipped maps as connected.
        connected: Dict[str, str] = {}
        for map_type, path in inventory.items():
            try:
                if connect(path, map_type, material):
                    connected[map_type] = path
                else:
                    cls.logger.info(
                        f"{CoreUtils.short_name(material)} has no slot for "
                        f"{map_type}; skipped."
                    )
            except Exception as e:
                # Also material-named — same ordering reason as the group above.
                cls.logger.error(
                    f"Error connecting {map_type} on "
                    f"{CoreUtils.short_name(material)}: {e}"
                )

        return connected


class MatUpdaterSlots(MatUpdater):
    msg_intro = "Reconfigure existing materials for a target workflow preset."
    # SUPPORTED_MAT_TYPES is inherited -- derived from MatUpdater.CONNECTORS so
    # the panel's filter cannot drift from what update_network can actually wire.

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.mat_updater

        # Setup logging
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt001, "anchorClicked"):
            self.ui.txt001.anchorClicked.connect(self._on_log_link_clicked)

        try:
            sourceimages = EnvUtils.get_env_info("sourceimages")
            info = ptk.truncate(
                f"<br><font color='#888'>Source Images: {sourceimages}</font><br>",
                mode="middle",
            )
            self.ui.txt001.setText(self.msg_intro + info)
        except Exception:
            self.ui.txt001.setText(self.msg_intro)

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Format global options in the header menu."""
        # Selection Mode
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_selection_mode",
            addItems=["Selected Objects", "All Scene Materials", "Browse..."],
            setToolTip=(
                "Choose the texture/material source:\n"
                "• Selected Objects — materials on the current selection; "
                "material nodes selected directly (Hypershade, Outliner) count too.\n"
                "• All Scene Materials — every supported material in the scene.\n"
                "• Browse… — pick texture files; updates materials that reference them."
            ),
        )
        # Dry Run — kept at the top so it's the first thing reached under the
        # selection mode; simulate the run without writing files or editing nodes.
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_dry_run",
            setText="Dry Run",
            setToolTip="Simulate the process without making changes.",
        )
        # Reconfiguration only — file format, max size, mask/secondary scale
        # and bit depth are NOT offered here. They duplicate the Map Converter's
        # Optimize tool, which owns image optimization for the whole pipeline;
        # this panel decides *which* maps a material gets and wires them up.
        widget.menu.add("Separator", setTitle="Processing")
        # Missing Maps — the policy for a packed map (ORM / MSAO) the preset
        # calls for whose source channels aren't all resolvable. Same three
        # rules, wording and prefixed-combo presentation as the Map Packer's
        # control, so one vocabulary covers both ends of the pipeline.
        cmb_missing = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_missing_maps",
            setToolTip=(
                "What to do when a packed map (ORM, MSAO) the preset calls for "
                "is missing one or more of its source maps (and it can't be "
                "derived from the maps that are present).\n"
                "Skip Map (default): the packed map isn't written. A gap whose "
                "fill is harmless still packs - an absent AO fills white - so "
                "this is about the ones that would bake in a wrong value, like "
                "black roughness or white (mirror) smoothness.\n"
                "Pack If 2+ Maps: pack once at least two source channels "
                "resolved - enough that the result is still a useful packed map "
                "rather than a single map wearing a packed name.\n"
                "Pack Anyway: always pack; missing channels are filled with "
                "their default value."
            ),
        )
        cmb_missing.add(
            [
                ("Skip Map", ptk.MapRegistry.MISSING_SKIP),
                ("Pack If 2+ Maps", ptk.MapRegistry.MISSING_MULTI),
                ("Pack Anyway", ptk.MapRegistry.MISSING_FORCE),
            ],
            prefix="Missing Maps:",
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
            setToolTip="Allow substituting missing output maps with alternatives (e.g. use AO map alone if Mask Map cannot be generated). Ignored when Missing Maps is set to Pack Anyway.",
        )

        # Output fallbacks only have something to do while a packed map can
        # still fail to be written: 'Pack Anyway' always emits one, so there is
        # never a missing output left to substitute for.
        def _update_output_fallbacks_state():
            widget.menu.chk_output_fallbacks.setDisabled(
                cmb_missing.currentData() == ptk.MapRegistry.MISSING_FORCE
            )

        cmb_missing.currentIndexChanged.connect(
            lambda *_: _update_output_fallbacks_state()
        )
        _update_output_fallbacks_state()
        # Discover Maps in sourceimages
        widget.menu.add(
            "QCheckBox",
            setObjectName="chk_discover_sourceimages",
            setText="Discover Maps in sourceimages",
            setChecked=True,
            setToolTip=(
                "Pull in same-base-name textures found in the project's "
                "sourceimages folder that aren't wired into the material "
                "(e.g. a Normal sitting on disk but never connected).\n"
                "Only map types missing from the material are added; "
                "connected textures are never replaced."
            ),
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

        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Material Updater",
                body="Reconfigure scene materials for a target workflow — "
                "resolve each material's texture set, generate the packed maps "
                "the preset calls for (ORM / MSAO), and rewire the shading "
                "network to the result.<br>"
                "Image <i>optimization</i> — file format, resolution clamp, "
                "secondary scale, bit depth, archiving originals — is not done "
                "here: run the <b>Map Converter</b>'s <b>Optimize</b> tool for that.",
                steps=[
                    "Pick a <b>Selection Mode</b> (Selected materials / All "
                    "scene materials).",
                    "Open the header menu (▸) and configure the processing "
                    "and file-management options below.",
                    "Press <b>Update</b> to run.",
                ],
                sections=[
                    (
                        "Processing options",
                        [
                            "<b>Missing Maps</b> — what an ORM / MSAO does when a "
                            "source channel can't be resolved: <i>Skip Map</i> "
                            "writes nothing unless the gap is harmless (an absent "
                            "AO fills white), <i>Pack If 2+ Maps</i> packs once at "
                            "least two channels resolved, <i>Pack Anyway</i> packs "
                            "regardless (absent channels take their default fill).",
                            "<b>Use Input Fallbacks</b> — generate missing inputs "
                            "from related ones (e.g. Base Color from Diffuse).",
                            "<b>Use Output Fallbacks</b> — substitute missing "
                            "outputs (e.g. AO alone for Mask Map). Disabled when "
                            "Missing Maps is set to Pack Anyway.",
                            "<b>Discover Maps in sourceimages</b> — gap-fill each "
                            "material with same-base-name textures sitting in "
                            "sourceimages that were never connected. Only missing "
                            "map types are added; connected textures are kept.",
                            "<b>Dry Run</b> — preview the plan without writing files.",
                        ],
                    ),
                    (
                        "File management",
                        [
                            "<b>Transfer Mode</b> — Copy / Move / Use Existing.",
                            "<b>Output Folder</b> — destination (disabled when "
                            "Use Existing is selected).",
                        ],
                    ),
                ],
            )
        )

    @property
    def selection_mode(self):
        return self.ui.cmb_selection_mode.currentText()

    @property
    def move_to_folder(self):
        return self.ui.txt_move_to.text() or None

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

    @staticmethod
    def _normalize_path(p):
        """Normalize for case-insensitive comparison on case-insensitive filesystems."""
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))

    def _filter_supported(self, materials):
        """Drop materials whose node type ``update_network`` doesn't know how to wire."""
        return [m for m in materials if cmds.nodeType(m) in self.SUPPORTED_MAT_TYPES]

    def _materials_from_texture_paths(self, paths):
        """Find scene materials that reference any of the given texture paths."""
        if not paths:
            return []
        target = set()
        for p in paths:
            try:
                target.add(self._normalize_path(p))
            except Exception:
                continue

        matching_nodes = []
        for node in cmds.ls(type="file") or []:
            try:
                resolved = MatUtils.resolve_path(
                    cmds.getAttr(f"{node}.fileTextureName")
                )
            except Exception:
                continue
            if resolved and self._normalize_path(resolved) in target:
                matching_nodes.append(node)

        if not matching_nodes:
            return []
        return self._filter_supported(MatUtils.get_connected_shaders(matching_nodes))

    @Cancelable(300)
    def b001(self, widget):
        """Update Materials"""
        config_name = self.ui.cmb001.currentText()

        menu = self.ui.header.menu
        dry_run = menu.chk_dry_run.isChecked()
        transfer_mode = menu.cmb_transfer_mode.currentData()
        missing_map_rule = menu.cmb_missing_maps.currentData()
        use_input_fallbacks = menu.chk_input_fallbacks.isChecked()
        use_output_fallbacks = menu.chk_output_fallbacks.isChecked()
        discover_sourceimages = menu.chk_discover_sourceimages.isChecked()

        # Resolve target materials from the header selection mode.
        # `None` means "let update_materials default to all scene materials".
        mode = self.selection_mode
        materials = None

        if mode == "Selected Objects":
            sel = cmds.ls(selection=True, long=True) or []
            if not sel:
                self.logger.warning("Nothing selected.")
                return
            # get_mats passes selected material nodes straight through, so a
            # Hypershade/Outliner selection works as well as geometry, and it
            # descends into groups.
            resolved = MatUtils.get_mats(sel, as_strings=True) or []
            materials = self._filter_supported(resolved)
            if not materials:
                # Name what WAS resolved. "No supported materials found" reads
                # as a tool failure when the scene visibly has a Stingray
                # material -- the two cases (selection resolved nothing at all
                # vs. resolved only unsupported shaders) need different fixes
                # from the artist, so they get different messages.
                if resolved:
                    found = ", ".join(
                        sorted(
                            f"{CoreUtils.short_name(m)} ({cmds.nodeType(m)})"
                            for m in resolved
                        )
                    )
                    self.logger.warning(
                        f"Selection resolved {len(resolved)} material(s), none of a "
                        f"supported type ({', '.join(self.SUPPORTED_MAT_TYPES)}): "
                        f"{found}"
                    )
                else:
                    self.logger.warning(
                        "No materials could be resolved from the selection "
                        f"({len(sel)} node(s)). Select geometry, a group "
                        "containing geometry, or the material itself."
                    )
                return
        elif mode == "Browse...":
            try:
                start_dir = EnvUtils.get_env_info("sourceimages") or ""
            except Exception:
                start_dir = ""
            paths = self.sb.file_dialog(
                file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
                title="Select textures whose materials should be updated:",
                start_dir=start_dir,
                allow_multiple=True,
            )
            if not paths:
                return
            materials = self._materials_from_texture_paths(paths)
            if not materials:
                self.logger.warning(
                    "No supported materials reference the selected textures."
                )
                return

        self.ui.txt001.clear()

        try:
            # Reconfiguration keys only. Image-optimization keys (max_size,
            # mask_map_scale, output_extension, old_files_folder) are
            # deliberately absent — the factory then leaves resolution, format
            # and bit depth alone, and the Map Converter's Optimize tool owns
            # that pass. ``update_materials`` still accepts them, so a script
            # can drive both in one call; the panel does not offer them.
            config = {
                "preset": config_name,
                "move_to_folder": (
                    self.move_to_folder if transfer_mode != "none" else None
                ),
                "transfer_mode": transfer_mode,
                "missing_map_rule": missing_map_rule,
                "use_input_fallbacks": use_input_fallbacks,
                "use_output_fallbacks": use_output_fallbacks,
                "discover_sourceimages": discover_sourceimages,
                "dry_run": dry_run,
            }

            # No ``total=`` needed — :func:`SwitchboardUtilsMixin.progress_adapter`
            # auto-syncs the bar from ``update_materials``'s callback total
            # on the first tick. Also avoids ``len(None)`` when materials
            # defaults to "all scene materials".
            with self.sb.progress(text="Updating Materials") as update:
                self.update_materials(
                    materials=materials,
                    config=config,
                    verbose=True,
                    progress_callback=self.sb.progress_adapter(update),
                )
            # No completion line appended here — update_materials closes the
            # run with its own summary box (mirrors the scene exporter).
        except Exception as e:
            # Through the logger, not txt001.append: the handler is already
            # pointed at txt001, so this lands in the same place *and* picks up
            # ERROR colouring plus any file/ring sink the session attached.
            # Message and traceback go out as ONE record — a formatted
            # traceback is already a single multi-line string, and the widget
            # handler renders it with ``white-space:pre-wrap``, so it needs no
            # ``log_group`` to avoid per-frame paragraphs. Plain ``error``
            # also keeps the whole thing level-filtered, which ``log_group``
            # (via ``log_raw``) would not be.
            import traceback

            self.logger.error(
                f"Material update failed: {e}\n{traceback.format_exc()}"
            )


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("material_updater", reload=True)
    ui.show(pos="screen", app_exec=True)
