# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import os
import logging
from typing import List, Dict, Any, Optional, Union, Callable, Tuple

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils.game_shader import GameShader
from mayatk.mat_utils.shader_converter import ShaderConverter
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class MatUpdater(ptk.LoggingMixin):
    """Updates existing materials with processed textures."""

    #: Everything this tool knows about a material node type, in ONE place:
    #: ``connect`` is the :class:`GameShader` method that wires a map in,
    #: ``shader`` names the shader family for
    #: :meth:`GameShader.resolve_opacity_sources` (StingrayPBS takes its
    #: opacity in the colour map's alpha; the others take a separate map), and
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
    #:
    #: The physically-based surfaces take their teardown from
    #: ``ShaderAttributeMap.slot_attrs`` -- the same table their connector wires
    #: the lobe maps through -- so a lobe cannot be wired and left out of the
    #: teardown. openPBRSurface was missing outright: a retype to OpenPBR
    #: converted every material, deleted the sources, and then skipped each
    #: one as "no connector", so the texture update never ran.
    CONNECTORS = {
        "standardSurface": {
            "connect": "connect_standard_surface_nodes",
            "shader": "standard_surface",
            "attrs": ShaderAttributeMap.slot_attrs("standardSurface"),
        },
        "openPBRSurface": {
            "connect": "connect_open_pbr_nodes",
            "shader": "open_pbr",
            "attrs": ShaderAttributeMap.slot_attrs("openPBRSurface"),
        },
        "StingrayPBS": {
            "connect": "connect_stingray_nodes",
            "shader": "stingray",
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

    #: Environment maps a material's slots carry (the StingrayPBS IBL cubes and
    #: LUT) that are no part of its texture set -- never a map to rebuild.
    IGNORED_ENV_SETS = frozenset({"ibl_brdf_lut", "diffuse_cube", "specular_cube"})

    #: The ``config["transfer_mode"]`` values a run applies once
    #: ``move_to_folder`` is set -- what becomes of a material's SOURCE maps:
    #: ``"copy"``: copied there (generated maps move), ``"move"``: moved there.
    #: Leaving them where they are is no mode: pass no ``move_to_folder``.
    #: Twin of blendertk's, which has no such step (empty).
    TRANSFER_MODES = ("copy", "move")

    @classmethod
    @CoreUtils.undoable
    def update_materials(
        cls,
        materials: List[Any] = None,
        config: Union[str, Dict[str, Any]] = None,
        verbose: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        shader_type: Optional[str] = None,
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
            shader_type: Retype the materials first -- one of
                :attr:`ShaderConverter.TARGETS` (``"stingray"``,
                ``"standard_surface"``, ``"open_pbr"``). ``None`` (default)
                leaves every material the type it already is. See
                :meth:`_retype_materials` for why it runs before anything else.

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
                types = set(cls.SUPPORTED_MAT_TYPES)
                if shader_type:
                    # A scene-wide retype has to SEE the legacy shaders: they
                    # are the ones with no connector, which is the whole point
                    # of asking for one. Arnold stays out even here -- those
                    # materials are generated previews owned by ArnoldBridge
                    # (see CONNECTORS) -- but one passed in explicitly still
                    # converts.
                    types |= set(ShaderConverter.CONVERTIBLE) - {"aiStandardSurface"}
                materials = MatUtils.get_scene_mats(node_type=sorted(types))

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
                        move_to_folder, cls.texture_root()
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
                    source_images = cls.texture_root()
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

            # Pre-resolve materials (get_mats always returns name strings, which
            # the downstream cmds.* calls need in Maya 2025).
            materials = MatUtils.get_mats(materials)

            # Retype FIRST, before the connector filter below: a legacy
            # blinn/phong has no connector at all, so converting first is what
            # lets one run bring a scene's shaders AND its textures onto the
            # target workflow instead of skipping every shader that most needs
            # it.
            if shader_type:
                materials = cls._retype_materials(
                    materials,
                    shader_type,
                    dry_run=bool(config_obj.get("dry_run")),
                    verbose=verbose,
                )

            # Drop what cannot be wired BEFORE any file is touched. The per
            # material guard in ``update_network`` keeps the report honest, but
            # by then the textures have already been processed and -- with an
            # output folder set -- MOVED, out from under file nodes this tool
            # will never repath. Caller-supplied materials are the path that
            # reaches here unfiltered (``selected_materials`` and
            # ``materials_using_textures`` filter their own).
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

                # A rebuild wires back only the maps it classifies BY NAME, so
                # a material none of whose own textures names a map type would
                # come out of it with nothing: a projected shadow's silhouette
                # (``BODY_shadow.png``) left its plane untextured and opaque in
                # an export (2026-10-15). Nothing to rebuild -- left as authored.
                own = [
                    f
                    for f in files
                    if os.path.splitext(os.path.basename(f))[0].lower()
                    not in cls.IGNORED_ENV_SETS
                ]
                if own and not any(ptk.MapFactory.resolve_map_type(f) for f in own):
                    cls.logger.info(
                        f"{mat_link}: left as authored -- none of its textures "
                        "names a map type: "
                        + ", ".join(os.path.basename(f) for f in own)
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
                        local_sets = {}

                        if processed_sets and isinstance(processed_sets, dict):
                            raw_sets = ptk.MapFactory.group_textures_by_set(files)
                            local_sets = {
                                k: v
                                for k, v in raw_sets.items()
                                if k not in cls.IGNORED_ENV_SETS
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
                    mat_log.append(
                        f"[Dry Run] Skipping move/copy to '{move_to_folder}'."
                    )
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
    def _retype_materials(
        cls,
        materials: List[str],
        shader_type: str,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> List[str]:
        """Retype *materials* to *shader_type*, returning what to carry forward.

        The retype itself is :meth:`ShaderConverter.convert`'s -- it reads each
        channel through ``ShaderAttributeMap`` (the SSoT for what every shader
        type calls each logical channel), rebuilds the network on the target
        type and moves the shading-group membership across, so this only picks
        the materials and reconciles the names afterwards.

        A material already of the target type is left out of the call rather
        than converted and skipped: nothing is lost either way, but the run
        report should not list a no-op per material.

        Returns:
            The list to update: each converted material replaced by its new
            node, everything else (already the target type, or of a type the
            converter cannot read) left exactly as it was.
        """
        if shader_type not in ShaderConverter.TARGETS:
            # Rejected here rather than at the conversion: a DRY RUN never
            # reaches that call, and reporting a plan that could not have run
            # is worse than no plan.
            raise ValueError(
                f"Unknown shader_type {shader_type!r}. "
                f"Expected one of {sorted(ShaderConverter.TARGETS)}."
            )
        node_type = ShaderConverter.TARGETS[shader_type]
        pending = [m for m in materials if cmds.nodeType(m) != node_type]
        if not pending:
            return materials
        if dry_run:
            cls.logger.info(
                f"Dry run: would retype {len(pending)} material(s) to {node_type} -- "
                + ", ".join(sorted(CoreUtils.short_name(m) for m in pending))
            )
            return materials
        converted = ShaderConverter.convert(
            pending, target=shader_type, verbose=verbose
        )
        # `convert` reports {source: new | None}; None is a skip (unreadable
        # type) and an unlisted material was never a candidate -- both keep the
        # node the caller came in with.
        return [converted.get(m) or m for m in materials]

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
        # Build inventory: Map Type -> Path. The material's maps read as ONE
        # batch, so a set named for a lobe (`Hero_Coat`) keeps its base maps.
        inventory = {}
        unresolved = []
        map_types = ptk.MapFactory.resolve_map_types(texture_paths)
        for path in texture_paths:
            map_type = map_types[path]
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

        dropped = report.get("dropped") or {}

        # Resolve the connector BEFORE the dry-run return: a material this tool
        # cannot wire has nothing to report in either mode. It also names the
        # shader for the opacity pass below, which has to run before anything
        # is reported OR connected.
        node_type = cmds.nodeType(material)
        spec = cls.CONNECTORS.get(node_type)
        if spec is None:
            cls.logger.warning(
                f"Cannot update {CoreUtils.short_name(material)}: no connector "
                f"for node type '{node_type}' "
                f"(supported: {', '.join(cls.CONNECTORS)})."
            )
            return {}

        shader = GameShader()

        # Settle where the opacity lives, exactly as a fresh build does. A
        # StingrayPBS graph has no sampler for a separate opacity map (the
        # alpha rides the colour map), so without this the factory's own
        # Base_Color + Opacity split — what every preset with
        # ``albedo_transparency`` off produces — arrives as a map with nowhere
        # to go: the material's working alpha was disconnected for the rewire
        # and its replacement reported as "no slot for Opacity; skipped".
        # Reported through the same group as the redundancy drops below.
        type_cache = {path: map_type for map_type, path in inventory.items()}
        textures, _, opacity_rows = shader.resolve_opacity_sources(
            list(inventory.values()),
            type_cache,
            config,
            shader_type=spec["shader"],
            name=CoreUtils.short_name(material),
        )
        if opacity_rows:
            inventory = {
                map_type: path
                for path in textures
                if (map_type := type_cache.get(path))
            }
            for path, map_type, why in opacity_rows:
                dropped[map_type] = why

        # A map in the folder that never reaches the material reads as a bug
        # unless the reason is stated — the reported case was a set carrying
        # both an ORM and an MSAO. Same grouping and naming rationale as the
        # unresolved block below: one record, named for the material because
        # it lands before the caller's per-material chunk.
        if dropped and cls.logger.isEnabledFor(logging.INFO):
            cls.logger.log_group(
                f"Superseded map on {CoreUtils.short_name(material)} "
                "(not connected as-is)",
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

        if config.get("dry_run", False):
            return inventory

        # Use GameShader for connections to avoid duplication
        connect = getattr(shader, spec["connect"])

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

    @staticmethod
    def shader_type_choices() -> List[Tuple[str, str]]:
        """The retype targets ``update_materials(shader_type=...)`` takes.

        Returns:
            ``(label, shader_type)`` pairs in :attr:`ShaderConverter.TARGETS`
            order (a panel's combo saves its INDEX: a target added there lands
            at the END). Twin of blendertk's, which has none.
        """
        return [
            (ShaderConverter.TARGET_LABELS.get(name, node_type), name)
            for name, node_type in ShaderConverter.TARGETS.items()
        ]

    @staticmethod
    def texture_root() -> str:
        """Where a run resolves a relative ``move_to_folder`` and discovers
        sibling maps (``discover_sourceimages``): the project's sourceimages.
        Twin of blendertk's (its current workspace).

        Returns:
            The folder, or '' when the scene has none.
        """
        return EnvUtils.get_env_info("sourceimages") or ""

    @classmethod
    def selected_materials(cls, shader_type: Optional[str] = None) -> List[str]:
        """The materials a run over the selection acts on.

        The materials on the selected geometry (groups descend) and any
        material node selected directly (Hypershade, Outliner), kept to the
        types the run can act on. The Material Updater panel's *Selected
        Objects* scope. Twin of blendertk's.

        Parameters:
            shader_type: The run's retype target (see :meth:`update_materials`).
                With one, the legacy types the converter reads count too: the
                retype is what brings them into the run.

        Returns:
            The materials, or ``[]`` with a warning naming the fix: nothing
            selected, nothing resolved from it, or only unsupported types.
        """
        sel = cmds.ls(selection=True, long=True) or []
        if not sel:
            cls.logger.warning("Nothing selected.")
            return []
        # get_mats passes selected material nodes straight through, so a
        # Hypershade/Outliner selection works as well as geometry, and it
        # descends into groups.
        resolved = MatUtils.get_mats(sel) or []
        materials = cls._filter_supported(resolved, shader_type)
        if materials:
            return materials
        # Name what WAS resolved. "No supported materials found" reads as a
        # tool failure when the scene visibly has a Stingray material -- the
        # two cases (selection resolved nothing at all vs. resolved only
        # unsupported shaders) need different fixes from the artist, so they
        # get different messages.
        if resolved:
            found = ", ".join(
                sorted(
                    f"{CoreUtils.short_name(m)} ({cmds.nodeType(m)})" for m in resolved
                )
            )
            cls.logger.warning(
                f"Selection resolved {len(resolved)} material(s), none of a "
                f"supported type ({', '.join(cls._acceptable_types(shader_type))}): "
                f"{found}"
            )
        else:
            cls.logger.warning(
                "No materials could be resolved from the selection "
                f"({len(sel)} node(s)). Select geometry, a group "
                "containing geometry, or the material itself."
            )
        return []

    @classmethod
    def materials_using_textures(
        cls, paths: List[str], shader_type: Optional[str] = None
    ) -> List[str]:
        """The scene materials whose file nodes read any of *paths*, kept to the
        types the run can act on (see :meth:`selected_materials`).

        The Material Updater panel's *Browse...* scope. Twin of blendertk's.
        """
        if not paths:
            return []
        target = set()
        for p in paths:
            try:
                target.add(cls._normalize_path(p))
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
            if resolved and cls._normalize_path(resolved) in target:
                matching_nodes.append(node)

        if not matching_nodes:
            return []
        return cls._filter_supported(
            MatUtils.get_connected_shaders(matching_nodes), shader_type
        )

    @staticmethod
    def _normalize_path(p):
        """Normalize for case-insensitive comparison on case-insensitive filesystems."""
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))

    @classmethod
    def _acceptable_types(cls, shader_type: Optional[str] = None) -> Tuple[str, ...]:
        """Node types a run can act on -- what it can wire, plus what it can retype.

        A legacy blinn has no connector, so the plain run drops it; with a
        *shader_type* the retype gives it one, and refusing it here would
        refuse exactly the material that option exists for.
        """
        types = set(cls.SUPPORTED_MAT_TYPES)
        if shader_type:
            types |= set(ShaderConverter.CONVERTIBLE)
        return tuple(sorted(types))

    @classmethod
    def _filter_supported(cls, materials, shader_type: Optional[str] = None):
        """Drop materials a run cannot act on (see :meth:`_acceptable_types`)."""
        types = cls._acceptable_types(shader_type)  # once, not per material
        return [m for m in materials if cmds.nodeType(m) in types]
