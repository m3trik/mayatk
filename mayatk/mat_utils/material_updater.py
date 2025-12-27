# !/usr/bin/python
# coding=utf-8
import os
import re
from typing import List, Dict, Any, Union, Optional

try:
    import pymel.core as pm
except ImportError:
    pass
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils.game_shader import GameShader


class MaterialUpdater:
    """Updates existing materials with processed textures."""

    @classmethod
    @CoreUtils.undoable
    def update_materials(
        cls,
        materials: List[Any] = None,
        config: Union[str, Dict[str, Any]] = None,
        move_to_folder: str = None,
        verbose: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update materials with processed textures.

        Args:
            materials: List of materials to update. If None, finds all StingrayPBS and standardSurface materials.
            config: Configuration preset name (str) or dictionary.
            move_to_folder: Folder to move final textures to.
            verbose: Print verbose output.
            **kwargs: Overrides for configuration (e.g. max_size, convert, optimize, dry_run).

        Returns:
            Dict[str, Any]: Results keyed by material name.
        """

        # Define callback
        def log(msg, *args, **kwargs):
            if verbose:
                # Strip HTML tags
                clean_msg = re.sub(r"<[^>]+>", "", str(msg))
                print(clean_msg)

        if materials is None:
            materials = pm.ls(
                type=["StingrayPBS", "standardSurface", "aiStandardSurface"]
            )

        if not materials:
            log("No supported materials found.")
            return {}

        # Resolve Config
        cfg_kwargs = {}
        if isinstance(config, str):
            presets = ptk.MapRegistry().get_workflow_presets()
            if config in presets:
                cfg_kwargs = presets[config].copy()
        elif isinstance(config, dict):
            cfg_kwargs = config.copy()

        # Handle aliases
        if "output_type" in kwargs:
            kwargs["output_extension"] = kwargs.pop("output_type")

        # Apply overrides
        cfg_kwargs.update(kwargs)

        # Extract move_to_folder from config if not provided explicitly
        if move_to_folder is None:
            move_to_folder = cfg_kwargs.get("move_to_folder")

        # Create Config Object
        config_obj = cfg_kwargs

        results = {}
        texture_cache = {}

        # Pre-resolve materials
        resolved_materials = []
        for mat in materials:
            if isinstance(mat, str):
                try:
                    mat = pm.PyNode(mat)
                except Exception:
                    continue
            resolved_materials.append(mat)
        materials = resolved_materials

        log(f"Processing {len(materials)} material(s)...")

        # --- BATCH PROCESSING ---
        run_factory = config_obj.get("convert", True) or config_obj.get(
            "optimize", True
        )
        processed_sets = {}
        mat_to_files = {}

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
                        if path and os.path.exists(path):
                            files.append(path)
                    except Exception:
                        continue

                # Ensure unique paths
                files = sorted(list(set(files)))

                if files:
                    mat_to_files[mat] = files
                    all_files.update(files)

            # 2. Batch Process
            if all_files:
                log(f"Batch processing {len(all_files)} unique textures...")
                try:
                    # Extract max_workers to avoid double argument error
                    batch_config = config_obj.copy()
                    max_workers = batch_config.pop("max_workers", 1)

                    processed_sets = ptk.TextureMapFactory.prepare_maps(
                        list(all_files),
                        callback=log,
                        max_workers=max_workers,
                        **batch_config,
                    )
                except Exception as e:
                    log(f"Batch processing failed: {e}", "error")
                    processed_sets = {}

        for mat in materials:
            mat_name = mat.name()
            log(f"\n{'='*60}")
            log(f"Processing material: {mat_name}")
            log(f"{'='*60}")

            # Get source files
            if run_factory and mat in mat_to_files:
                files = mat_to_files[mat]
            else:
                file_nodes = pm.listHistory(mat, type="file")
                files = []
                for f in file_nodes:
                    try:
                        path = f.fileTextureName.get()
                        if path and os.path.exists(path):
                            files.append(path)
                    except Exception:
                        continue

                # Ensure unique paths
                files = sorted(list(set(files)))

            if not files:
                log(f"  No valid texture paths for {mat_name}, skipping.")
                continue

            # Determine if we need to run the factory
            processed_files = []

            if run_factory:
                cache_key = tuple(sorted(files))

                # 1. Check Cache
                if cache_key in texture_cache:
                    log(f"  Using cached maps for {mat_name}")
                    processed_files = texture_cache[cache_key]

                else:
                    # 2. Try Batch Lookup
                    # We only use batch results if the material's files belong to a SINGLE set.
                    # If they span multiple sets, we must re-process to allow cross-set packing.
                    batch_success = False
                    local_sets = {}

                    if processed_sets and isinstance(processed_sets, dict):
                        local_sets = ptk.TextureMapFactory.group_textures_by_set(files)

                        if len(local_sets) == 1:
                            base_name = list(local_sets.keys())[0]
                            if base_name in processed_sets:
                                processed_files = processed_sets[base_name]
                                batch_success = True

                    # 3. Manual Process (Re-process)
                    if not batch_success:
                        if len(local_sets) > 1:
                            log(
                                f"  Material uses textures from {len(local_sets)} different sets. Re-processing as single set."
                            )
                        else:
                            log(f"  Preparing maps...")

                        try:
                            # Extract max_workers to avoid collision with kwargs
                            manual_config = config_obj.copy()
                            max_workers = manual_config.pop("max_workers", 1)

                            processed_files = ptk.TextureMapFactory.prepare_maps(
                                files,
                                callback=log,
                                group_by_set=False,  # Always force single set for per-material context
                                max_workers=max_workers,
                                **manual_config,
                            )
                            texture_cache[cache_key] = processed_files
                        except Exception as e:
                            log(f"  Error preparing maps: {e}")
                            continue
            else:
                log(f"  Skipping factory (using existing textures)")
                processed_files = files

            if not processed_files:
                continue

            # Move files if requested
            if move_to_folder:
                log(f"  Moving textures to: {move_to_folder}")
                try:
                    processed_files = ptk.FileUtils.move_file(
                        processed_files,
                        move_to_folder,
                        overwrite=True,
                        create_dir=True,
                    )
                    # Ensure list
                    if isinstance(processed_files, str):
                        processed_files = [processed_files]
                except Exception as e:
                    log(f"  Error moving files: {e}")

            # Update network
            log(f"  Updating network...")
            connected_maps = cls.update_network(
                mat, processed_files, config_obj, callback=log
            )

            results[mat_name] = {
                "textures": processed_files,
                "connected": connected_maps,
            }

        return results

    @staticmethod
    def update_network(
        material, texture_paths, config, callback=None
    ) -> Dict[str, str]:
        """Connect processed textures to the material.

        Returns:
            Dict[str, str]: Map of connected map types to file paths.
        """
        # Build inventory: Map Type -> Path
        inventory = {}
        for path in texture_paths:
            map_type = ptk.TextureMapFactory.resolve_map_type(path)
            if callback:
                callback(f"  Resolving {os.path.basename(path)} -> {map_type}")
            if map_type:
                inventory[map_type] = path

        # Filter redundant maps (in-place)
        ptk.TextureMapFactory.filter_redundant_maps(inventory, callback=callback)

        if config.get("dry_run", False):
            if callback:
                callback("[Dry Run] Skipping connection.")
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
                if callback:
                    callback(f"  Error connecting {map_type}: {e}")

        return inventory


if __name__ == "__main__":
    from mayatk.ui_utils._ui_utils import UiUtils

    UiUtils.clear_scrollfield_reporters()
    # Use the top-level alias for reliability
    config_name = "Unity HDRP"

    print(f"Running MaterialUpdater with config: {config_name}...")
    results = MaterialUpdater.update_materials(
        materials=None,  # Process all StingrayPBS materials in scene
        config=config_name,
        max_size=4096,
        mask_map_scale=0.5,  # Downscale Mask Maps to 2048px
        output_type="png",
        old_files_folder=None,  # Archive original files
        optimize=True,
        convert=True,
        verbose=1,
        dry_run=0,
    )

    # Print summary
    print("\n" + "=" * 80)
    print(f"MATERIAL UPDATE SUMMARY ({config_name})")
    print("=" * 80)

    # Define expected maps for verification
    expected_maps = {
        "Unity HDRP": ["Base_Color", "MaskMap", "Normal"],
        "Unity URP": ["Base_Color", "Metallic_Smoothness", "Normal"],
        "Unreal Engine": ["Base_Color", "ORM", "Normal"],
    }
    required = expected_maps.get(config_name, [])

    for mat_name, data in sorted(results.items()):
        print(f"\nMaterial: {mat_name}")
        print(f"  Total Textures Processed: {len(data['textures'])}")

        connected = data.get("connected", {})
        if connected:
            print("  Connected Maps:")
            found_types = set()
            for map_type, path in sorted(connected.items()):
                print(f"    - {map_type:<20}: {os.path.basename(path)}")
                found_types.add(map_type)

            # Verification
            print("  Verification:")
            for req in required:
                # Handle aliases/variations
                is_present = False
                if req == "MaskMap":
                    is_present = any(
                        k in found_types for k in ["MaskMap", "MSAO", "Mask"]
                    )
                elif req == "Normal":
                    is_present = any(
                        k in found_types
                        for k in ["Normal", "Normal_OpenGL", "Normal_DirectX"]
                    )
                else:
                    is_present = req in found_types

                status = "OK" if is_present else "MISSING"
                print(f"    - {req:<20}: {status}")

        else:
            print("  No maps connected.")
    print("\n" + "=" * 80)
