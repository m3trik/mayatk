# !/usr/bin/python
# coding=utf-8
import tempfile
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pythontk as ptk
import pymel.core as pm


class FBXImporter:
    """Handles FBX-specific import operations (.fbx files)."""

    # FBX Import Settings - these can be customized based on needs
    FBX_IMPORT_SETTINGS = {
        "FBXImportMode": "add",  # "add", "merge", or "exmerge"
        "FBXImportConvertDeformingNullsToJoint": True,  # Convert deforming nulls to joints
        "FBXImportConvertNullsToJoint": False,  # Don't convert all nulls to joints
        "FBXImportMergeAnimationLayers": True,  # Merge animation layers
        "FBXImportProtectDrivenKeys": True,  # Protect driven keys
        "FBXImportResamplingRateSource": "Scene",  # Use scene's frame rate
        "FBXImportSetLockedAttribute": False,  # Don't lock attributes
        "FBXImportConstraints": True,  # Import constraints
        "FBXImportCameras": True,  # Import cameras
        "FBXImportLights": True,  # Import lights
        "FBXImportAnimatedRootMotion": True,  # Import root motion
        "FBXImportBakeComplexAnimation": False,  # Don't bake complex animation
        "FBXImportDeleteOriginalTakeOnSplitAnimation": True,  # Clean up takes
        "FBXImportGenerateLog": False,  # Don't generate verbose logs
        # Material and texture settings
        "FBXImportMaterials": True,  # Import materials
        "FBXImportTextures": True,  # Import textures
        "FBXImportEmbeddedTextures": True,  # Extract embedded textures
        # Units and axis conversion
        "FBXImportConvertUnitString": "",  # Let Maya handle unit conversion
        "FBXImportForcedFileAxis": "None",  # Don't force axis conversion
        "FBXImportUpAxis": "Y",  # Use Y-up (Maya default)
    }

    def __init__(self, logger, dry_run: bool = True):
        self.logger = logger
        self.dry_run = dry_run

    def is_supported_file(self, file_path: Union[str, Path]) -> bool:
        """Check if the file is an FBX file."""
        return Path(file_path).suffix.lower() == ".fbx"

    def import_with_namespace(
        self,
        source_file: Path,
        namespace: str,
        temp_namespace_prefix: str,
        force_complete_import: bool = False,
    ) -> Optional[Dict]:
        """Import FBX file with namespace - handles the complete import process.

        Args:
            source_file: Path to the FBX file
            namespace: Target namespace for import
            temp_namespace_prefix: Prefix for temporary namespace
            force_complete_import: If True, handle name conflicts to ensure complete import.
                                 If False, let Maya handle conflicts naturally.
        """
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import FBX: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Import the FBX file
            imported_transforms = self._import_fbx_file(
                source_file, namespace, force_complete_import
            )

            # Check if import was successful - None means error, empty list means no objects
            if imported_transforms is None:
                self.logger.error(f"Failed to import FBX file: {source_file}")
                return None

            # Even if no transforms, we should still return success info
            # The warning about no transforms will be logged by _import_fbx_file if needed
            self.logger.debug(
                f"FBX import completed: {len(imported_transforms)} transforms in namespace {namespace}"
            )

            return {
                "namespace": namespace,
                "transforms": imported_transforms,
                "all_nodes": imported_transforms,  # For FBX, transforms are the main nodes
                "requested_namespace": namespace,
            }

        except Exception as e:
            self.logger.error(f"Failed to import FBX file with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import FBX file for analysis purposes."""
        try:
            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            pm.namespace(add=namespace)

            # Import FBX for analysis
            imported_transforms = self._import_fbx_file(source_file, namespace)

            self.logger.debug(
                f"Imported {len(imported_transforms) if imported_transforms else 0} FBX transforms for analysis"
            )
            return imported_transforms

        except Exception as e:
            self.logger.error(f"Failed to import FBX for analysis: {e}")
            return None

    def _setup_fbx_import_settings(self) -> None:
        """Configure FBX import settings for consistent imports including materials."""
        try:
            # Only set FBX settings if FBX plugin is loaded
            if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
                try:
                    pm.loadPlugin("fbxmaya", quiet=True)
                    self.logger.debug("Loaded FBX plugin")
                except Exception as e:
                    self.logger.warning(f"Could not load FBX plugin: {e}")
                    return

            # Check if FBX commands are available before using them
            fbx_commands_available = True
            try:
                # Test if FBXImportMaterials command exists (suppress error output)
                try:
                    pm.mel.eval("help FBXImportMaterials")
                except RuntimeError:
                    # Expected if command doesn't exist
                    raise Exception("FBX commands not available")
            except:
                fbx_commands_available = False
                self.logger.debug(
                    "FBX MEL commands not available - using alternative approach"
                )

            # Apply FBX import settings using proper MEL commands
            if fbx_commands_available:
                try:
                    # Set import mode
                    pm.mel.eval('FBXImportMode -v "add"')

                    # Enable material and texture import - CRITICAL for preserving materials
                    pm.mel.eval("FBXImportMaterials -v true")
                    pm.mel.eval("FBXImportTextures -v true")
                    pm.mel.eval("FBXImportEmbeddedTextures -v true")

                    # Other important settings
                    pm.mel.eval("FBXImportConvertDeformingNullsToJoint -v true")
                    pm.mel.eval("FBXImportConvertNullsToJoint -v false")
                    pm.mel.eval("FBXImportMergeAnimationLayers -v true")
                    pm.mel.eval("FBXImportConstraints -v true")
                    pm.mel.eval("FBXImportCameras -v true")
                    pm.mel.eval("FBXImportLights -v true")
                    pm.mel.eval("FBXImportGenerateLog -v false")

                    # Units and axis
                    pm.mel.eval('FBXImportUpAxis -v "Y"')

                    self.logger.debug(
                        "Applied FBX import settings including material preservation"
                    )

                except Exception as e:
                    self.logger.warning(f"Could not set some FBX settings: {e}")
                    # Try basic material import at minimum
                    try:
                        pm.mel.eval("FBXImportMaterials -v true")
                        pm.mel.eval("FBXImportTextures -v true")
                        self.logger.debug("Applied basic material import settings")
                    except Exception as fallback_error:
                        self.logger.warning(
                            f"Could not set even basic material settings: {fallback_error}"
                        )
            else:
                # Alternative approach when FBX MEL commands aren't available
                self.logger.debug(
                    "Using alternative FBX import approach without MEL commands"
                )
                # Set general import options that should work regardless
                try:
                    # These are more universally available
                    pm.mel.eval(
                        'FBXImport -file ""'
                    )  # This will fail but may initialize FBX
                except:
                    pass  # Expected to fail, but may load FBX procedures

        except Exception as e:
            self.logger.warning(f"Failed to setup FBX import settings: {e}")

    def _import_fbx_file(
        self, source_file: Path, namespace: str, force_complete_import: bool = False
    ) -> Optional[List[Any]]:
        """Import FBX file with proper settings and namespace handling.

        Args:
            source_file: Path to the FBX file
            namespace: Target namespace
            force_complete_import: If True, handle name conflicts to ensure complete import
        """
        try:
            # Setup FBX import settings
            self._setup_fbx_import_settings()

            # Get objects before import to detect new ones
            objects_before = set(pm.ls(assemblies=True))
            transforms_before = set(pm.ls(type="transform"))

            # Handle name conflicts - temporarily rename root namespace objects
            # that might conflict with FBX import to ensure complete import
            # Only apply this when we want to force complete import (e.g., for tree display)
            renamed_objects = {}
            if force_complete_import:
                renamed_objects = self._handle_import_name_conflicts(source_file)

            # FBX import doesn't directly support namespaces like Maya ASCII
            # We need to import first, then move objects to namespace

            self.logger.debug(f"Importing FBX file: {source_file}")

            # Try FBX import with MEL command first, fallback to file command if needed
            import_success = False
            fbx_path = str(source_file).replace("\\", "/")

            try:
                # Primary method: Use FBX MEL command
                pm.mel.eval(f'FBXImport -file "{fbx_path}"')
                import_success = True
                self.logger.debug("FBX import completed using MEL command")
            except Exception as mel_error:
                self.logger.debug(f"FBX MEL import failed: {mel_error}")

                # Fallback method: Use pm.mel.eval with file command
                try:
                    # Use Maya's file command with FBX type via MEL
                    pm.mel.eval(
                        f'file -i -type "FBX" -ignoreVersion -ra true -mergeNamespacesOnClash false -rpr "temp_import" -options "fbx" -pr "{fbx_path}"'
                    )
                    import_success = True
                    self.logger.debug(
                        "FBX import completed using file command fallback"
                    )
                except Exception as file_error:
                    self.logger.debug(f"File command import failed: {file_error}")

                    # Final fallback: Use PyMEL's importFile directly
                    try:
                        pm.importFile(fbx_path, i=True, type="FBX")
                        import_success = True
                        self.logger.debug("FBX import completed using PyMEL importFile")
                    except Exception as pymel_error:
                        self.logger.error(
                            f"All FBX import methods failed. MEL: {mel_error}, file: {file_error}, PyMEL: {pymel_error}"
                        )
                        return None

            if not import_success:
                self.logger.error("FBX import failed")
                return None

            # Find newly imported objects
            objects_after = set(pm.ls(assemblies=True))
            transforms_after = set(pm.ls(type="transform"))

            new_assemblies = list(objects_after - objects_before)
            new_transforms = list(transforms_after - transforms_before)

            self.logger.debug(
                f"Import detection: {len(new_assemblies)} new assemblies, {len(new_transforms)} new transforms"
            )

            if not new_transforms:
                self.logger.warning("No new transforms found after FBX import")
                return []

            # DEBUG: Show what was actually imported before namespace operations
            self.logger.debug("=== FBX IMPORT DEBUG: PRE-NAMESPACE ANALYSIS ===")
            self.logger.debug(
                f"New assemblies: {[a.nodeName() for a in new_assemblies]}"
            )

            # Show hierarchy structure of imported objects
            root_imports = []
            child_imports = []
            for transform in new_transforms:
                parent = transform.getParent()
                if parent is None:
                    root_imports.append(transform)
                else:
                    child_imports.append(transform)

            self.logger.debug(
                f"Pre-namespace: {len(root_imports)} roots, {len(child_imports)} children"
            )
            self.logger.debug(f"Root imports: {[r.nodeName() for r in root_imports]}")

            # Show some examples of the hierarchy
            for root in root_imports[:2]:  # Show first 2 roots
                children = root.getChildren(type="transform")
                self.logger.debug(
                    f"  {root.nodeName()} has {len(children)} direct children"
                )
                if children:
                    child_names = [c.nodeName() for c in children[:5]]
                    self.logger.debug(
                        f"    Children: {child_names}{'...' if len(children) > 5 else ''}"
                    )

            self.logger.debug("=== END PRE-NAMESPACE ANALYSIS ===")

            # Move imported objects to namespace
            if namespace and namespace != ":":
                # Store the original transform names before moving
                original_transform_names = [t.nodeName() for t in new_transforms]

                # For FBX imports, we need to move ALL transforms to namespace, not just assemblies
                # The previous approach of only moving assemblies breaks the hierarchy
                self.logger.debug(
                    f"Moving ALL {len(new_transforms)} transforms to namespace {namespace}"
                )

                # Filter out any problematic transforms before moving to namespace
                filtered_transforms = []
                for transform in new_transforms:
                    try:
                        # Skip objects with malformed names (duplicated path elements)
                        name = transform.nodeName()
                        long_name = transform.longName()

                        # Check for malformed FBX paths with duplicate elements
                        if "|" in long_name:
                            path_parts = long_name.split("|")
                            # Check if any path element appears multiple times consecutively
                            has_duplicates = False
                            for i in range(len(path_parts) - 1):
                                if path_parts[i] == path_parts[i + 1]:
                                    has_duplicates = True
                                    break

                            if has_duplicates:
                                self.logger.debug(
                                    f"Skipping malformed FBX object with duplicate path elements: {long_name}"
                                )
                                continue

                        # Check for excessive path depth (indicates malformed FBX structure)
                        if "|" in long_name and long_name.count("|") > 10:
                            self.logger.debug(
                                f"Skipping object with excessive path depth: {long_name}"
                            )
                            continue

                        # Verify object actually exists and is valid
                        if pm.objExists(transform.nodeName()):
                            filtered_transforms.append(transform)
                        else:
                            self.logger.debug(
                                f"Skipping non-existent object: {transform.nodeName()}"
                            )
                    except Exception as e:
                        self.logger.debug(
                            f"Skipping problematic transform {transform}: {e}"
                        )
                        continue

                # Move filtered transforms to namespace to preserve hierarchy
                self._move_objects_to_namespace(filtered_transforms, namespace)

                # Log what we actually moved
                moved_names = [t.nodeName() for t in filtered_transforms[:10]]
                self.logger.debug(
                    f"Moved transforms (first 10): {moved_names}{'...' if len(filtered_transforms) > 10 else ''}"
                )

                # Re-query transforms in the new namespace - this is the critical part
                # We need to get ALL transforms, not just assemblies
                if len(filtered_transforms) != len(new_transforms):
                    self.logger.debug(
                        f"Filtered {len(new_transforms) - len(filtered_transforms)} problematic transforms, moving {len(filtered_transforms)} valid transforms to namespace {namespace}"
                    )
                else:
                    self.logger.debug(
                        f"FBX import: {len(filtered_transforms)} transforms moved to namespace {namespace}"
                    )

                # Try multiple namespace query approaches to get all transforms
                namespaced_transforms = []

                # Method 1: Query all transform nodes in namespace
                try:
                    query_result = pm.ls(f"{namespace}:*", type="transform")
                    namespaced_transforms.extend(query_result)
                    self.logger.debug(
                        f"Method 1 - Found {len(query_result)} transforms in namespace query"
                    )
                except Exception as e:
                    self.logger.debug(f"Method 1 namespace query failed: {e}")

                # Method 2: Query all objects in namespace and filter for transforms
                if not namespaced_transforms:
                    try:
                        all_namespace_objects = pm.ls(f"{namespace}:*")
                        filtered_transforms = [
                            obj
                            for obj in all_namespace_objects
                            if pm.nodeType(obj) == "transform"
                        ]
                        namespaced_transforms.extend(filtered_transforms)
                        self.logger.debug(
                            f"Method 2 - Found {len(filtered_transforms)} transforms via object filtering (from {len(all_namespace_objects)} total objects)"
                        )
                    except Exception as e:
                        self.logger.debug(f"Method 2 namespace query failed: {e}")

                # Method 3: Try to reconstruct from original names
                if not namespaced_transforms:
                    try:
                        reconstructed_transforms = []
                        for original_name in original_transform_names:
                            clean_name = original_name.split(":")[
                                -1
                            ]  # Remove any existing namespace
                            namespaced_name = f"{namespace}:{clean_name}"
                            if pm.objExists(namespaced_name):
                                try:
                                    reconstructed_transforms.append(
                                        pm.PyNode(namespaced_name)
                                    )
                                    self.logger.debug(
                                        f"Reconstructed: {namespaced_name}"
                                    )
                                except Exception as e:
                                    self.logger.debug(
                                        f"Could not create PyNode for {namespaced_name}: {e}"
                                    )

                        namespaced_transforms.extend(reconstructed_transforms)
                        self.logger.debug(
                            f"Method 3 - Reconstructed {len(reconstructed_transforms)} transforms from original names"
                        )
                    except Exception as e:
                        self.logger.debug(f"Method 3 reconstruction failed: {e}")

                # Method 4: Direct namespace listing with recursive search
                if not namespaced_transforms:
                    try:
                        # List all objects under this namespace recursively
                        all_ns_objects = pm.ls(f"{namespace}:*", recursive=True)
                        recursive_transforms = [
                            obj
                            for obj in all_ns_objects
                            if hasattr(obj, "type") and obj.type() == "transform"
                        ]
                        namespaced_transforms.extend(recursive_transforms)
                        self.logger.debug(
                            f"Method 4 - Found {len(recursive_transforms)} transforms via recursive namespace search"
                        )
                    except Exception as e:
                        self.logger.debug(f"Method 4 recursive search failed: {e}")

                # Method 5: For FBX imports, also search for ALL transforms and filter by namespace
                if not namespaced_transforms:
                    try:
                        # Get ALL transforms in the scene and filter for our namespace
                        all_transforms = pm.ls(type="transform")
                        namespace_filtered = [
                            t
                            for t in all_transforms
                            if t.nodeName().startswith(f"{namespace}:")
                        ]
                        namespaced_transforms.extend(namespace_filtered)
                        self.logger.debug(
                            f"Method 5 - Found {len(namespace_filtered)} transforms via scene-wide namespace filtering"
                        )
                    except Exception as e:
                        self.logger.debug(f"Method 5 scene-wide filtering failed: {e}")

                # Method 6: Try to find hierarchical objects based on the pattern we see in setAttr commands
                if not namespaced_transforms and len(new_transforms) > 2:
                    try:
                        # Look for objects that match the hierarchical patterns we expect
                        pattern_objects = []
                        for root_name in ["INTERACTIVE", "STATIC"]:
                            namespaced_root = f"{namespace}:{root_name}"
                            if pm.objExists(namespaced_root):
                                root_obj = pm.PyNode(namespaced_root)
                                pattern_objects.append(root_obj)

                                # Recursively get ALL descendants
                                all_descendants = root_obj.getChildren(
                                    allDescendents=True, type="transform"
                                )
                                pattern_objects.extend(all_descendants)

                                self.logger.debug(
                                    f"Found {len(all_descendants)} descendants under {namespaced_root}"
                                )

                        namespaced_transforms.extend(pattern_objects)
                        self.logger.debug(
                            f"Method 6 - Found {len(pattern_objects)} transforms via hierarchical pattern search"
                        )
                    except Exception as e:
                        self.logger.debug(
                            f"Method 6 hierarchical pattern search failed: {e}"
                        )

                # Remove duplicates if any methods found overlapping results
                if namespaced_transforms:
                    unique_transforms = []
                    seen_names = set()
                    for transform in namespaced_transforms:
                        name = transform.nodeName()
                        if name not in seen_names:
                            unique_transforms.append(transform)
                            seen_names.add(name)

                    namespaced_transforms = unique_transforms
                    self.logger.debug(
                        f"After deduplication: {len(namespaced_transforms)} unique transforms"
                    )

                self.logger.debug(
                    f"Final result: Found {len(namespaced_transforms)} transforms in namespace {namespace}"
                )

                if namespaced_transforms:
                    # Log some examples of what we found
                    example_names = [t.nodeName() for t in namespaced_transforms[:5]]
                    self.logger.debug(
                        f"Example transforms: {example_names}{'...' if len(namespaced_transforms) > 5 else ''}"
                    )

                    # DEBUG: Analyze the final hierarchy structure
                    self.logger.debug(
                        "=== FBX IMPORT DEBUG: POST-NAMESPACE ANALYSIS ==="
                    )
                    final_roots = []
                    final_children = []
                    for transform in namespaced_transforms:
                        parent = transform.getParent()
                        if parent is None:
                            final_roots.append(transform)
                        else:
                            final_children.append(transform)

                    self.logger.debug(
                        f"Post-namespace: {len(final_roots)} roots, {len(final_children)} children"
                    )
                    self.logger.debug(
                        f"Final root objects: {[r.nodeName() for r in final_roots]}"
                    )

                    # Check if we lost the hierarchy structure during namespace operations
                    if len(final_children) == 0 and len(child_imports) > 0:
                        self.logger.warning(
                            f"HIERARCHY LOST: Had {len(child_imports)} children before namespace, now have 0"
                        )
                        self.logger.warning(
                            "This suggests namespace moving broke the hierarchy relationships"
                        )

                        # Try to find the missing children by looking for objects with expected names
                        self.logger.debug(
                            "Searching for missing children in namespace..."
                        )
                        missing_found = 0
                        for child_import in child_imports[:10]:  # Check first 10
                            expected_name = f"{namespace}:{child_import.nodeName()}"
                            if pm.objExists(expected_name):
                                try:
                                    missing_child = pm.PyNode(expected_name)
                                    if missing_child not in namespaced_transforms:
                                        namespaced_transforms.append(missing_child)
                                        missing_found += 1
                                        self.logger.debug(
                                            f"Recovered missing child: {expected_name}"
                                        )
                                except Exception as e:
                                    self.logger.debug(
                                        f"Could not recover {expected_name}: {e}"
                                    )

                        if missing_found > 0:
                            self.logger.debug(
                                f"Recovered {missing_found} missing children from namespace"
                            )

                    self.logger.debug("=== END POST-NAMESPACE ANALYSIS ===")

                    # Restore any temporarily renamed objects before returning successful result
                    self._restore_renamed_objects(renamed_objects)
                    return namespaced_transforms
                else:
                    # Final fallback: return empty list but log detailed info
                    try:
                        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
                        namespace_exists = namespace in all_namespaces
                        self.logger.warning(
                            f"Could not find transforms in namespace {namespace} (exists: {namespace_exists}). "
                            f"Original transforms: {len(original_transform_names)}, "
                            f"Assemblies moved: {len(new_assemblies) if new_assemblies else 0}"
                        )

                        # Debug: Show what namespaces we do have
                        temp_namespaces = [
                            ns for ns in all_namespaces if "temp_import_" in ns
                        ]
                        self.logger.debug(
                            f"Available temp namespaces: {temp_namespaces}"
                        )

                        # Debug: Try to list what's actually in the namespace
                        if namespace_exists:
                            try:
                                ns_contents = pm.ls(f"{namespace}:*")
                                self.logger.debug(
                                    f"Namespace {namespace} contains {len(ns_contents)} objects"
                                )
                                # Show types of objects in namespace
                                object_types = {}
                                for obj in ns_contents[
                                    :20
                                ]:  # Limit to first 20 for brevity
                                    obj_type = pm.nodeType(obj)
                                    object_types[obj_type] = (
                                        object_types.get(obj_type, 0) + 1
                                    )
                                self.logger.debug(
                                    f"Object types in namespace: {object_types}"
                                )
                            except Exception as debug_error:
                                self.logger.debug(
                                    f"Could not debug namespace contents: {debug_error}"
                                )

                    except Exception as debug_error:
                        self.logger.debug(f"Final fallback debug failed: {debug_error}")

                    # Restore any temporarily renamed objects before returning empty result
                    self._restore_renamed_objects(renamed_objects)
                    return []
            else:
                self.logger.debug(
                    f"FBX import: {len(new_transforms)} transforms imported"
                )
                # Restore any temporarily renamed objects before returning result
                self._restore_renamed_objects(renamed_objects)
                return new_transforms

        except Exception as e:
            self.logger.error(f"Failed to import FBX file {source_file}: {e}")
            # Restore any temporarily renamed objects in case of error
            self._restore_renamed_objects(renamed_objects)
            return None

    def _move_objects_to_namespace(self, objects: List[Any], namespace: str) -> None:
        """Move objects and their materials to the specified namespace."""
        try:
            if not objects:
                self.logger.debug("No objects to move to namespace")
                return

            # Ensure namespace exists
            if not pm.namespace(exists=namespace):
                pm.namespace(add=namespace)

            # Collect all materials and shading engines connected to these objects
            materials_to_move = set()
            shading_engines_to_move = set()

            # First pass: collect all materials and shading engines
            for obj in objects:
                try:
                    obj_node = pm.PyNode(obj) if not isinstance(obj, pm.PyNode) else obj

                    # Get all shapes under this transform (including children)
                    shapes = obj_node.getShapes(allDescendents=True)

                    for shape in shapes:
                        try:
                            # Get shading engines connected to this shape
                            shading_groups = shape.outputs(type="shadingEngine")
                            for sg in shading_groups:
                                shading_engines_to_move.add(sg)

                                # Get materials connected to this shading engine
                                materials = sg.surfaceShader.inputs()
                                materials.extend(sg.displacementShader.inputs())
                                materials.extend(sg.volumeShader.inputs())

                                for mat in materials:
                                    if mat and hasattr(mat, "nodeType"):
                                        materials_to_move.add(mat)

                                        # Also get textures connected to the material
                                        file_textures = mat.inputs(type="file")
                                        for tex in file_textures:
                                            materials_to_move.add(tex)

                                        # Get other connected nodes (like bump2d, etc.)
                                        connected_nodes = mat.inputs()
                                        for node in connected_nodes:
                                            if node and hasattr(node, "nodeType"):
                                                node_type = node.nodeType()
                                                if node_type in [
                                                    "bump2d",
                                                    "place2dTexture",
                                                    "samplerInfo",
                                                ]:
                                                    materials_to_move.add(node)

                        except Exception as e:
                            self.logger.debug(
                                f"Could not get materials for shape {shape}: {e}"
                            )

                except Exception as e:
                    self.logger.debug(
                        f"Could not process object {obj} for materials: {e}"
                    )

            self.logger.debug(
                f"Found {len(materials_to_move)} materials and {len(shading_engines_to_move)} shading engines to move"
            )

            moved_count = 0

            # Move materials first (but skip default materials)
            for material in materials_to_move:
                try:
                    mat_name = material.nodeName()
                    # Skip default Maya materials
                    if mat_name in ["lambert1", "particleCloud1", "shaderGlow1"]:
                        continue

                    if ":" not in mat_name:  # Only move if not already namespaced
                        new_mat_name = f"{namespace}:{mat_name}"
                        if not pm.objExists(new_mat_name):
                            material.rename(new_mat_name)
                            self.logger.debug(
                                f"Moved material {mat_name} to {new_mat_name}"
                            )
                except Exception as e:
                    self.logger.debug(f"Could not move material {material}: {e}")

            # Move shading engines (but skip default ones)
            for sg in shading_engines_to_move:
                try:
                    sg_name = sg.nodeName()
                    # Skip default Maya shading engines
                    if sg_name in ["initialShadingGroup", "initialParticleSE"]:
                        continue

                    if ":" not in sg_name:  # Only move if not already namespaced
                        new_sg_name = f"{namespace}:{sg_name}"
                        if not pm.objExists(new_sg_name):
                            sg.rename(new_sg_name)
                            self.logger.debug(
                                f"Moved shading engine {sg_name} to {new_sg_name}"
                            )
                except Exception as e:
                    self.logger.debug(f"Could not move shading engine {sg}: {e}")

            # Move geometry objects
            for obj in objects:
                try:
                    # Get the object as PyNode for reliable operations
                    obj_node = pm.PyNode(obj) if not isinstance(obj, pm.PyNode) else obj

                    # Rename to move to namespace
                    original_name = obj_node.nodeName()
                    new_name = f"{namespace}:{original_name}"

                    # Check if target name already exists
                    if pm.objExists(new_name):
                        # Generate unique name
                        counter = 1
                        while pm.objExists(f"{new_name}_{counter}"):
                            counter += 1
                        new_name = f"{new_name}_{counter}"

                    obj_node.rename(new_name)
                    moved_count += 1
                    self.logger.debug(f"Moved geometry {original_name} to {new_name}")

                except Exception as e:
                    self.logger.warning(
                        f"Failed to move object {obj} to namespace: {e}"
                    )

            self.logger.debug(
                f"Successfully moved {moved_count}/{len(objects)} objects to namespace {namespace} with materials preserved"
            )

        except Exception as e:
            self.logger.error(f"Failed to move objects to namespace {namespace}: {e}")

    def _handle_import_name_conflicts(self, source_file: Path) -> Dict[str, str]:
        """
        Handle name conflicts by temporarily renaming root namespace objects
        that might conflict with FBX import.

        Returns:
            Dict mapping temporary names to original names for restoration
        """
        renamed_objects = {}

        try:
            # Only handle this for FBX files during repopulation scenarios
            if not self.is_supported_file(source_file):
                return renamed_objects

            # Get current root namespace transform objects
            root_transforms = pm.ls(assemblies=True, type="transform")

            if not root_transforms:
                return renamed_objects

            # Log what we're checking for conflicts
            root_names = [
                t.nodeName().split(":")[-1]
                for t in root_transforms
                if ":" not in t.nodeName()
            ]

            if root_names:
                self.logger.debug(
                    f"Checking for name conflicts with {len(root_names)} root objects: {root_names[:5]}{'...' if len(root_names) > 5 else ''}"
                )

                # Temporarily rename root objects to avoid conflicts during FBX import
                for transform in root_transforms:
                    try:
                        original_name = transform.nodeName()

                        # Only rename objects in root namespace (no ":" in name)
                        if ":" in original_name:
                            continue

                        # Create a temporary unique name
                        temp_name = f"_temp_import_conflict_{original_name}"

                        # Make sure temp name is unique
                        counter = 1
                        while pm.objExists(temp_name):
                            temp_name = (
                                f"_temp_import_conflict_{original_name}_{counter}"
                            )
                            counter += 1

                        # Rename the object temporarily
                        transform.rename(temp_name)
                        renamed_objects[temp_name] = original_name

                        self.logger.debug(
                            f"Temporarily renamed {original_name} → {temp_name}"
                        )

                    except Exception as e:
                        self.logger.debug(f"Could not rename {transform}: {e}")

            if renamed_objects:
                self.logger.info(
                    f"Temporarily renamed {len(renamed_objects)} objects to avoid FBX import name conflicts"
                )

        except Exception as e:
            self.logger.warning(f"Error handling import name conflicts: {e}")

        return renamed_objects

    def _restore_renamed_objects(self, renamed_objects: Dict[str, str]) -> None:
        """
        Restore objects that were temporarily renamed to avoid import conflicts.

        Args:
            renamed_objects: Dict mapping temporary names to original names
        """
        if not renamed_objects:
            return

        try:
            restored_count = 0

            for temp_name, original_name in renamed_objects.items():
                try:
                    if pm.objExists(temp_name):
                        # Check if original name is now available
                        if not pm.objExists(original_name):
                            pm.rename(temp_name, original_name)
                            restored_count += 1
                            self.logger.debug(f"Restored {temp_name} → {original_name}")
                        else:
                            # Original name exists - this means the new import created a new object
                            # with that name, so we can safely delete the old temporary object
                            self.logger.info(
                                f"Deleting superseded temporary object: {temp_name} (new {original_name} imported)"
                            )
                            temp_obj = pm.PyNode(temp_name)
                            pm.delete(temp_obj)
                            restored_count += 1
                    else:
                        self.logger.debug(
                            f"Temp object {temp_name} no longer exists (already cleaned up)"
                        )

                except Exception as e:
                    self.logger.warning(
                        f"Error restoring {temp_name} to {original_name}: {e}"
                    )

            if restored_count > 0:
                self.logger.info(
                    f"Restored {restored_count} temporarily renamed objects"
                )

        except Exception as e:
            self.logger.error(f"Error restoring renamed objects: {e}")


class MayaImporter:
    """Handles Maya-specific import operations (.ma/.mb files)."""

    def __init__(self, logger, dry_run: bool = True):
        self.logger = logger
        self.dry_run = dry_run

    def is_supported_file(self, file_path: Union[str, Path]) -> bool:
        """Check if the file is a Maya file (.ma or .mb)."""
        suffix = Path(file_path).suffix.lower()
        return suffix in [".ma", ".mb"]

    def import_with_namespace(
        self,
        source_file: Path,
        namespace: str,
        temp_namespace_prefix: str,
        force_complete_import: bool = False,
    ) -> Optional[Dict]:
        """Import Maya file with namespace - original logic."""
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import Maya: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                self.logger.debug(f"Cleaning up existing namespace: {namespace}")
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            # Create the namespace
            self.logger.debug(f"Creating namespace: {namespace}")
            pm.namespace(add=namespace)

            # Get list of namespaces before import to detect what Maya actually creates
            namespaces_before = set(pm.namespaceInfo(listOnlyNamespaces=True))

            # Get list of transform nodes before import
            transforms_before = set(pm.ls(type="transform"))

            # Import the file into the namespace to avoid name clashes
            # Use options to reduce warnings and conflicts
            imported_nodes = pm.importFile(
                str(source_file),
                namespace=namespace,
                returnNewNodes=True,
                mergeNamespacesOnClash=False,  # Don't merge namespaces
                preserveReferences=False,  # Don't preserve external references
            )

            if not imported_nodes:
                self.logger.warning("No nodes were imported from source file")
                return None

            # Find newly imported transform nodes
            transforms_after = set(pm.ls(type="transform"))
            new_transforms = list(transforms_after - transforms_before)

            # Get list of namespaces after import to detect what Maya actually created
            namespaces_after = set(pm.namespaceInfo(listOnlyNamespaces=True))
            new_namespaces = namespaces_after - namespaces_before

            # Find temp namespaces that were actually created
            temp_namespaces_created = [
                ns for ns in new_namespaces if ns.startswith(temp_namespace_prefix)
            ]

            # Detect the actual namespace used by Maya
            actual_namespace = namespace

            # Method 1: Check what temp namespaces were actually created by Maya
            if temp_namespaces_created and len(temp_namespaces_created) == 1:
                actual_namespace = temp_namespaces_created[0]
                if actual_namespace != namespace:
                    self.logger.debug(
                        f"Maya created namespace '{actual_namespace}' instead of requested '{namespace}'"
                    )
            elif len(temp_namespaces_created) > 1:
                self.logger.warning(
                    f"Multiple temp namespaces created: {temp_namespaces_created}"
                )
                # Use the one with the most objects
                namespace_object_counts = {}
                for ns in temp_namespaces_created:
                    try:
                        objects = pm.ls(f"{ns}:*")
                        namespace_object_counts[ns] = len(objects)
                    except:
                        namespace_object_counts[ns] = 0

                if namespace_object_counts:
                    actual_namespace = max(
                        namespace_object_counts, key=namespace_object_counts.get
                    )
                    self.logger.debug(
                        f"Selected namespace with most objects: {actual_namespace}"
                    )

            # Method 2: Double-check with transform namespace detection
            if new_transforms:
                # Check the namespace of the first transform to verify
                first_transform_name = new_transforms[0].nodeName()
                if ":" in first_transform_name:
                    transform_namespace = first_transform_name.split(":")[0]
                    if transform_namespace != actual_namespace:
                        self.logger.warning(
                            f"Namespace mismatch! Detected from namespaces: {actual_namespace}, "
                            f"but transform says: {transform_namespace}"
                        )
                        # Trust the transform namespace as it's more reliable
                        actual_namespace = transform_namespace

                # Debug: Check all transforms to see their namespaces
                namespaces_found = set()
                for transform in new_transforms[:5]:  # Check first 5
                    transform_name = transform.nodeName()
                    if ":" in transform_name:
                        ns = transform_name.split(":")[0]
                        namespaces_found.add(ns)

                if len(namespaces_found) > 1:
                    self.logger.warning(
                        f"Multiple namespaces found in imported transforms: {namespaces_found}"
                    )
                elif namespaces_found:
                    confirmed_namespace = list(namespaces_found)[0]
                    if confirmed_namespace != actual_namespace:
                        self.logger.warning(
                            f"Final namespace correction: {actual_namespace} -> {confirmed_namespace}"
                        )
                        actual_namespace = confirmed_namespace

            self.logger.debug(
                f"Maya import {actual_namespace}: {len(imported_nodes)} total nodes, {len(new_transforms)} transforms"
            )

            return {
                "namespace": actual_namespace,
                "transforms": new_transforms,
                "all_nodes": imported_nodes,
                "requested_namespace": namespace,  # Track original request for cleanup
            }

        except Exception as e:
            self.logger.error(f"Failed to import Maya file with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import Maya file for analysis purposes."""
        try:
            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            pm.namespace(add=namespace)

            # Import for analysis with consistent options
            imported_nodes = pm.importFile(
                str(source_file),
                namespace=namespace,
                returnNewNodes=True,
                mergeNamespacesOnClash=False,
                preserveReferences=False,
            )

            self.logger.debug(
                f"Imported {len(imported_nodes) if imported_nodes else 0} Maya nodes for analysis"
            )
            return imported_nodes

        except Exception as e:
            self.logger.error(f"Failed to import Maya file for analysis: {e}")
            return None


class CameraTracker(ptk.LoggingMixin):
    """Tracks cameras before and after import operations for proper cleanup."""

    def __init__(self, logger=None):
        if logger:
            self.logger = logger
        else:
            super().__init__()
        self.pre_import_cameras = set()
        self.post_import_cameras = set()
        self.new_cameras = set()

    def capture_pre_import_state(self):
        """Capture camera state before import."""
        self.logger.debug("Capturing pre-import camera state...")
        self.pre_import_cameras = self._get_scene_cameras()
        self.logger.debug(f"Pre-import cameras: {sorted(self.pre_import_cameras)}")
        return self.pre_import_cameras

    def capture_post_import_state(self):
        """Capture camera state after import."""
        self.logger.debug("Capturing post-import camera state...")
        self.post_import_cameras = self._get_scene_cameras()
        self.new_cameras = self.post_import_cameras - self.pre_import_cameras
        self.logger.debug(f"Post-import cameras: {sorted(self.post_import_cameras)}")
        self.logger.debug(f"New cameras detected: {sorted(self.new_cameras)}")
        return self.new_cameras

    def get_imported_cameras(self, namespace_filter=None):
        """Get cameras that were imported (optionally filtered by namespace)."""
        imported = self.new_cameras
        if namespace_filter:
            imported = {cam for cam in imported if namespace_filter in cam}
        self.logger.debug(
            f"Imported cameras (filtered={namespace_filter}): {sorted(imported)}"
        )
        return imported

    def cleanup_imported_cameras(
        self, namespace_filter=None, preserve_user_cameras=True
    ):
        """Clean up imported cameras with optional preservation of user cameras."""
        self.logger.debug("Cleaning up imported cameras...")

        cameras_to_delete = self.get_imported_cameras(namespace_filter)

        if preserve_user_cameras:
            # Define Maya default cameras that should be deleted (exact matches only)
            MAYA_DEFAULT_CAMERAS = {"persp", "top", "front", "side"}

            # Filter to only delete exact default cameras
            filtered_cameras = set()
            for camera in cameras_to_delete:
                base_name = camera.split(":")[-1] if ":" in camera else camera
                if base_name in MAYA_DEFAULT_CAMERAS:  # Exact match only
                    filtered_cameras.add(camera)

            cameras_to_delete = filtered_cameras

        if cameras_to_delete:
            self.logger.info(
                f"Deleting {len(cameras_to_delete)} imported cameras: {sorted(cameras_to_delete)}"
            )
            try:
                # Get camera transforms and delete them
                camera_transforms = []
                for camera_name in cameras_to_delete:
                    try:
                        # camera_name is already the transform name
                        camera_transform = pm.PyNode(camera_name)
                        camera_transforms.append(camera_transform)
                    except Exception as e:
                        self.logger.warning(
                            f"Could not access camera transform {camera_name}: {e}"
                        )

                if camera_transforms:
                    pm.delete(camera_transforms)
                    self.logger.debug(
                        f"Successfully deleted {len(camera_transforms)} camera transforms"
                    )

                return list(cameras_to_delete)
            except Exception as e:
                self.logger.error(f"Failed to delete imported cameras: {e}")
                return []
        else:
            self.logger.debug("No imported cameras to clean up")
            return []

    def _get_scene_cameras(self):
        """Get all camera transform names in the scene."""
        try:
            cameras = pm.ls(type="camera")
            # Get the transform nodes (parents) of the camera shapes
            camera_transforms = []
            for cam in cameras:
                try:
                    transform = cam.getParent()
                    if transform:
                        camera_transforms.append(transform.nodeName())
                except:
                    # If we can't get the parent, use the camera name directly
                    camera_transforms.append(cam.nodeName())
            return set(camera_transforms)
        except Exception as e:
            self.logger.error(f"Failed to get scene cameras: {e}")
            return set()

    def reset(self):
        """Reset tracking state."""
        self.pre_import_cameras = set()
        self.post_import_cameras = set()
        self.new_cameras = set()


class NamespaceSandbox(ptk.LoggingMixin):
    """Handles temporary importing and namespace management for Maya scenes.

    Manages temporary imports with namespace isolation for safe cleanup.
    Automatically detects and handles Maya ASCII/Binary and FBX files.

    Uses specialized importers for different file types:
    - MayaImporter: Handles .ma/.mb files
    - FBXImporter: Handles .fbx files
    """

    # Constants
    TEMP_NAMESPACE_PREFIX = "temp_import_"
    DRY_RUN_NAMESPACE = "dry_run_temp"

    def __init__(self, dry_run: bool = True, fuzzy_matching: bool = True):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self._active_namespaces = []  # Track only OUR namespaces for cleanup

        # Initialize camera tracking
        self.camera_tracker = CameraTracker(self.logger)

        # Initialize specialized importers
        self._maya_importer = MayaImporter(self.logger, dry_run)
        self._fbx_importer = FBXImporter(self.logger, dry_run)

    # =============================================================================
    # PUBLIC API METHODS
    # =============================================================================

    def import_with_namespace(
        self,
        source_file: Union[str, Path],
        namespace_prefix: str = None,
        force_complete_import: bool = False,
    ) -> Optional[Dict]:
        """Import file and return import information.

        Automatically detects file type and uses appropriate importer:
        - .ma/.mb files → MayaImporter
        - .fbx files → FBXImporter

        Returns:
            Dictionary with namespace and imported transform nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            # Get the appropriate importer and delegate
            importer = self._get_importer_for_file(source_file)
            if not importer:
                self.logger.error(
                    f"No importer available for file type: {source_file.suffix}"
                )
                return None

            # Create unique namespace for tracking
            namespace = self._create_unique_namespace(namespace_prefix)

            # Track cameras before import
            self.camera_tracker.capture_pre_import_state()

            # Delegate to the appropriate importer
            import_result = importer.import_with_namespace(
                source_file,
                namespace,
                self.TEMP_NAMESPACE_PREFIX,
                force_complete_import=force_complete_import,
            )

            if import_result:
                # Track cameras after import
                self.camera_tracker.capture_post_import_state()

                # Track the namespace(s) for cleanup
                actual_namespace = import_result["namespace"]
                requested_namespace = import_result.get(
                    "requested_namespace", namespace
                )

                self._track_namespace(actual_namespace)

                # Also track the original requested namespace if different (Maya-specific logic)
                if (
                    actual_namespace != requested_namespace
                    and pm.namespace(exists=requested_namespace)
                    and requested_namespace not in self._active_namespaces
                ):
                    self._track_namespace(requested_namespace)
                    self.logger.debug(
                        f"Also tracking requested namespace: {requested_namespace}"
                    )

                self.logger.debug(f"Import successful: {actual_namespace}")
                self.logger.debug(f"Active namespaces: {self._active_namespaces}")

            return import_result

        except Exception as e:
            self.logger.error(f"Failed to import with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Union[str, Path], namespace: str = None
    ) -> Optional[List[Any]]:
        """Import file into temporary namespace for analysis (dry-run mode).

        Automatically detects file type and uses appropriate importer.

        Returns:
            List of imported nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            # Get the appropriate importer and delegate
            importer = self._get_importer_for_file(source_file)
            if not importer:
                self.logger.error(
                    f"No importer available for file type: {source_file.suffix}"
                )
                return None

            # Set up analysis namespace
            if namespace is None:
                if self._fbx_importer.is_supported_file(source_file):
                    namespace = f"{self.DRY_RUN_NAMESPACE}_fbx"
                else:
                    namespace = self.DRY_RUN_NAMESPACE

            # Delegate to the appropriate importer
            return importer.import_for_analysis(source_file, namespace)

        except Exception as e:
            self.logger.error(f"Failed to import for analysis: {e}")
            return None

    # =============================================================================
    # IMPORTER COORDINATION METHODS
    # =============================================================================

    def _get_importer_for_file(self, file_path: Union[str, Path]):
        """Get the appropriate importer for the given file type."""
        if self._fbx_importer.is_supported_file(file_path):
            return self._fbx_importer
        elif self._maya_importer.is_supported_file(file_path):
            return self._maya_importer
        else:
            return None

    def get_supported_formats(self) -> List[str]:
        """Get list of supported file formats from all importers."""
        return [".ma", ".mb", ".fbx"]

    def _track_namespace(self, namespace: str) -> None:
        """Track a namespace for cleanup."""
        if namespace not in self._active_namespaces:
            self._active_namespaces.append(namespace)
            self.logger.debug(f"Tracking namespace: {namespace}")

    # =============================================================================
    # UTILITY METHODS
    # =============================================================================

    def find_objects_in_namespace(
        self, namespace: str, target_objects: List[str]
    ) -> List[Any]:
        """Find objects in the specified namespace with optional fuzzy matching."""
        try:
            # Get all transform nodes in the namespace
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform")
            else:
                nodes = pm.ls(type="transform")

            available_transforms = {}
            for node in nodes:
                base_name = self._clean_namespace_name(node.nodeName())
                available_transforms[base_name] = node

            found_objects = []
            target_set = set(target_objects)

            # First pass: exact matches only
            for target_name in target_objects:
                if target_name in available_transforms:
                    found_objects.append(available_transforms[target_name])

            # Second pass: fuzzy matching (only if enabled)
            if self.fuzzy_matching:
                found_names = [
                    self._clean_namespace_name(obj.nodeName()) for obj in found_objects
                ]
                unmatched_targets = target_set - set(found_names)

                if unmatched_targets:
                    # Use batch fuzzy matching for better performance
                    matches = ptk.FuzzyMatcher.find_all_matches(
                        list(unmatched_targets),
                        list(available_transforms.keys()),
                        score_threshold=0.7,
                    )

                    for target_name, (matched_name, score) in matches.items():
                        found_objects.append(available_transforms[matched_name])
                        self.logger.info(
                            f"Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                        )

            return found_objects

        except Exception as e:
            self.logger.error(f"Failed to find objects in namespace {namespace}: {e}")
            return []

    def find_objects_with_hierarchy_matching(
        self, namespace: str, target_objects: List[str]
    ) -> List[Any]:
        """Find objects using hierarchical path matching (only if fuzzy_matching enabled)."""
        if not self.fuzzy_matching:
            self.logger.debug("Hierarchical matching disabled (fuzzy_matching=False)")
            return []

        try:
            # Get full hierarchy paths
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = pm.ls(type="transform", long=True)

            # Convert to hierarchy paths (using | separator like Maya)
            available_paths = []
            path_to_node = {}

            for node in nodes:
                # Convert Maya long name to hierarchy path
                hierarchy_path = node.replace("|", "|").replace(f"{namespace}:", "")
                available_paths.append(hierarchy_path)
                path_to_node[hierarchy_path] = pm.PyNode(node)

            # Create target paths (assume they're just node names for now)
            target_paths = [f"|{name}" for name in target_objects]

            # Use hierarchical fuzzy matching
            fuzzy_matches, _, _ = ptk.FuzzyMatcher.find_trailing_digit_matches(
                target_paths, available_paths, path_separator="|"
            )

            found_objects = []
            for match in fuzzy_matches:
                node = path_to_node[match["current_path"]]
                found_objects.append(node)
                self.logger.info(
                    f"Hierarchy match: {match['current_name']} -> {match['target_name']}"
                )

            return found_objects

        except Exception as e:
            self.logger.error(f"Hierarchical matching failed: {e}")
            return []

    def get_namespace_hierarchy(self, namespace: str) -> Dict[str, Any]:
        """Get complete hierarchy information for objects in namespace.

        Returns:
            Dictionary mapping object names to their hierarchy data
        """
        try:
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = pm.ls(type="transform", long=True)

            hierarchy_data = {}

            for node in nodes:
                node_obj = pm.PyNode(node)
                clean_name = self._clean_namespace_name(node_obj.nodeName())

                hierarchy_data[clean_name] = {
                    "node": node_obj,
                    "parent": node_obj.getParent(),
                    "children": node_obj.getChildren(type="transform"),
                    "world_matrix": node_obj.getMatrix(worldSpace=True),
                    "local_matrix": node_obj.getMatrix(worldSpace=False),
                    "full_path": node,
                }

            return hierarchy_data

        except Exception as e:
            self.logger.error(f"Failed to get namespace hierarchy: {e}")
            return {}

    def cleanup_import(
        self, namespace: str, imported_objects: List[Any] = None
    ) -> bool:
        """Safely remove imported objects and cleanup namespace tracking."""
        try:
            # Verify this is one of our managed namespaces
            if namespace not in self._active_namespaces:
                self.logger.debug(
                    f"Namespace not managed by this instance: {namespace}"
                )
                return False

            if self.dry_run:
                self.logger.notice(f"[DRY-RUN] Would cleanup import: {namespace}")
                return True

            # Check if namespace actually exists in Maya
            if not pm.namespace(exists=namespace):
                self.logger.debug(f"Namespace {namespace} does not exist in Maya")
                # Remove from tracking anyway
                self._active_namespaces = [
                    ns for ns in self._active_namespaces if ns != namespace
                ]
                return True

            # Delete the imported objects if provided
            if imported_objects:
                existing_objects = [
                    obj for obj in imported_objects if pm.objExists(obj)
                ]
                if existing_objects:
                    pm.delete(existing_objects)
                    self.logger.debug(
                        f"Deleted {len(existing_objects)} imported objects"
                    )

            # Clean up namespace if it exists and has objects
            try:
                # List objects in namespace before deleting
                namespace_objects = pm.ls(f"{namespace}:*")
                self.logger.debug(
                    f"Removing namespace {namespace} with {len(namespace_objects)} objects"
                )

                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
                self.logger.debug(f"Removed namespace: {namespace}")
            except Exception as e:
                self.logger.debug(f"Namespace cleanup handled gracefully: {e}")

            # Remove from active namespaces tracking
            self._active_namespaces = [
                ns for ns in self._active_namespaces if ns != namespace
            ]

            return True

        except Exception as e:
            self.logger.debug(f"Import cleanup handled gracefully: {e}")
            # Still remove from tracking even if cleanup failed
            if namespace in self._active_namespaces:
                self._active_namespaces = [
                    ns for ns in self._active_namespaces if ns != namespace
                ]
            return False

    def cleanup_namespace(self, namespace: str) -> bool:
        """Backward compatibility alias for cleanup_import."""
        return self.cleanup_import(namespace)

    def cleanup_all_namespaces(self) -> None:
        """Clean up all temp imports managed by this instance."""
        if not self._active_namespaces:
            self.logger.debug("No temporary imports to clean up")
            # Still clean up any imported cameras that might remain (including root namespace)
            self.camera_tracker.cleanup_imported_cameras(
                preserve_user_cameras=False
            )  # Clean up everything
            # Also clean up any cameras imported to root namespace
            self._cleanup_root_namespace_cameras()
            self.camera_tracker.reset()
            return

        self.logger.debug(f"Cleaning up namespaces: {self._active_namespaces}")

        # Clean up imported cameras for each namespace
        total_deleted_cameras = []
        for namespace in self._active_namespaces:
            # For temporary imports, clean up ALL imported cameras (not just defaults)
            deleted = self.camera_tracker.cleanup_imported_cameras(
                namespace_filter=namespace,
                preserve_user_cameras=False,  # Clean up everything
            )
            total_deleted_cameras.extend(deleted)

        # Also clean up any cameras that were imported to root namespace
        root_deleted = self._cleanup_root_namespace_cameras()
        total_deleted_cameras.extend(root_deleted)

        if total_deleted_cameras:
            self.logger.info(
                f"Cleaned up {len(total_deleted_cameras)} imported cameras"
            )

        # Also check what temp namespaces actually exist in Maya
        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        temp_namespaces_in_maya = [
            ns for ns in all_namespaces if ns.startswith("temp_import_")
        ]

        if temp_namespaces_in_maya:
            self.logger.debug(f"Temp namespaces in Maya: {temp_namespaces_in_maya}")

        temp_namespaces_to_cleanup = self._active_namespaces.copy()
        cleaned_count = 0

        for namespace in temp_namespaces_to_cleanup:
            self.logger.debug(f"Attempting to cleanup namespace: {namespace}")
            if self.cleanup_import(namespace):
                cleaned_count += 1

        self._active_namespaces.clear()

        # Reset camera tracking after cleanup
        self.camera_tracker.reset()

        if cleaned_count > 0:
            self.logger.debug(f"Cleaned up {cleaned_count} temporary imports")
        else:
            self.logger.debug("All temporary imports were already cleaned up")

    def _cleanup_root_namespace_cameras(self):
        """Clean up cameras that were imported to root namespace."""
        # Get cameras that were imported but don't have namespace prefixes
        root_imported_cameras = {
            cam
            for cam in self.camera_tracker.new_cameras
            if ":" not in cam  # No namespace prefix
        }

        if root_imported_cameras:
            self.logger.warning(
                f"Found cameras imported to root namespace: {sorted(root_imported_cameras)}"
            )

            # For temporary imports, clean up ALL imported cameras (aggressive cleanup)
            cameras_to_delete = list(root_imported_cameras)

            if cameras_to_delete:
                self.logger.info(
                    f"Cleaning up ALL root namespace imported cameras: {cameras_to_delete}"
                )
                try:
                    camera_transforms = []
                    for camera_name in cameras_to_delete:
                        try:
                            camera_transform = pm.PyNode(camera_name)
                            camera_transforms.append(camera_transform)
                        except Exception as e:
                            self.logger.warning(
                                f"Could not access camera transform {camera_name}: {e}"
                            )

                    if camera_transforms:
                        pm.delete(camera_transforms)
                        self.logger.debug(
                            f"Successfully deleted {len(camera_transforms)} root namespace cameras"
                        )

                    return cameras_to_delete
                except Exception as e:
                    self.logger.error(f"Failed to delete root namespace cameras: {e}")
                    return []

        return []

    def get_imported_cameras(self, namespace_filter=None):
        """Get cameras that were imported during the last import operation.

        Args:
            namespace_filter: Optional namespace to filter cameras by

        Returns:
            Set of imported camera names
        """
        return self.camera_tracker.get_imported_cameras(namespace_filter)

    def cleanup_imported_cameras(
        self, namespace_filter=None, preserve_user_cameras=True
    ):
        """Clean up imported cameras for a specific namespace.

        Args:
            namespace_filter: Optional namespace to filter cameras by
            preserve_user_cameras: If True, only delete Maya default cameras

        Returns:
            List of deleted camera names
        """
        return self.camera_tracker.cleanup_imported_cameras(
            namespace_filter, preserve_user_cameras
        )

        # Final check - see if any temp namespaces remain
        remaining_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        remaining_temp_namespaces = [
            ns for ns in remaining_namespaces if ns.startswith("temp_import_")
        ]

        if remaining_temp_namespaces:
            self.logger.warning(
                f"WARNING: Temp namespaces still exist after cleanup: {remaining_temp_namespaces}"
            )

    def cleanup_all_temp_namespaces_force(self) -> None:
        """Force cleanup of ALL temp namespaces in Maya, not just tracked ones.

        This is a nuclear option for cleaning up orphaned temp namespaces
        from previous failed runs.
        """
        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        all_temp_namespaces = [
            ns for ns in all_namespaces if ns.startswith("temp_import_")
        ]

        if not all_temp_namespaces:
            self.logger.debug("No temp namespaces found in Maya")
            return

        self.logger.warning(
            f"Force cleaning up ALL temp namespaces: {all_temp_namespaces}"
        )

        cleaned_count = 0
        for namespace in all_temp_namespaces:
            try:
                if pm.namespace(exists=namespace):
                    objects = pm.ls(f"{namespace}:*")
                    self.logger.debug(
                        f"Force removing namespace {namespace} with {len(objects)} objects"
                    )
                    pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
                    cleaned_count += 1
                    self.logger.debug(f"Force removed namespace: {namespace}")
            except Exception as e:
                self.logger.warning(
                    f"Failed to force remove namespace {namespace}: {e}"
                )

        # Clear our tracking since we've cleaned everything
        self._active_namespaces.clear()

        self.logger.warning(f"Force cleaned up {cleaned_count} temp namespaces")

    def export_objects_to_temp(self, target_objects: List[str]) -> Optional[Path]:
        """Export objects to temporary file using pm.ls() for robust object handling."""
        try:
            # pm.ls() handles the input normalization automatically
            objects_to_export = pm.ls(target_objects, type="transform")

            if not objects_to_export:
                self.logger.error("No valid transform objects to export")
                return None

            temp_dir = Path(tempfile.mkdtemp(prefix="maya_cross_scene_"))
            temp_file = temp_dir / "exported_objects.ma"

            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would export {len(objects_to_export)} objects to {temp_file}"
                )
                return temp_file

            pm.select(objects_to_export)
            pm.exportSelected(str(temp_file), type="mayaAscii", force=True)

            self.logger.info(
                f"Exported {len(objects_to_export)} objects to temporary file"
            )
            return temp_file

        except Exception as e:
            self.logger.error(f"Failed to export objects to temp file: {e}")
            return None

    def import_objects_for_swapping(
        self, source_file: Union[str, Path]
    ) -> Optional[Dict]:
        """Import objects from source scene for object swapping operations.

        Automatically detects file type and uses appropriate importer.
        This method handles the import logic that was previously in SceneManager.
        Returns import info with normalized key names for compatibility.
        """
        try:
            source_file = Path(source_file)

            # For dry-run, we still need to import to analyze, but we'll clean up afterwards
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Importing from {source_file} for analysis"
                )

            # Use the existing import_with_namespace method (which auto-detects file type)
            import_info = self.import_with_namespace(
                source_file, force_complete_import=True
            )

            if import_info:
                # Normalize the key name to match what the rest of the code expects
                import_info["imported_transforms"] = import_info.get("transforms", [])

            return import_info

        except Exception as e:
            self.logger.error(f"Failed to import source objects: {e}")
            return None

    def import_to_target_scene(
        self,
        temp_file: Union[str, Path],
        target_scene: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Import objects into target scene.

        This method handles the push operation logic that was in SceneManager.
        """
        try:
            temp_file = Path(temp_file)
            target_scene = Path(target_scene)

            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import {temp_file} to {target_scene}"
                )
                return True

            # Implementation for actual import would go here
            # This would involve opening target_scene, importing temp_file, then saving
            self.logger.info(f"Importing {temp_file} to {target_scene}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to import to target scene: {e}")
            return False

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup only our temp namespaces."""
        self.cleanup_all_namespaces()
        self.cleanup_analysis_namespace()

    # =============================================================================
    # PRIVATE/INTERNAL METHODS
    # =============================================================================

    def cleanup_analysis_namespace(self, namespace: str = None) -> bool:
        """Clean up analysis namespace and its contents."""
        try:
            if namespace is None:
                namespace = self.DRY_RUN_NAMESPACE

            if self.dry_run:
                self.logger.notice(f"[DRY-RUN] Would clean up namespace: {namespace}")
                return True

            if not pm.namespace(exists=namespace):
                return True

            pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
            self.logger.debug(f"Cleaned up analysis namespace: {namespace}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to cleanup namespace {namespace}: {e}")
            return False

    def _create_unique_namespace(self, prefix: str = None) -> str:
        """Create a unique namespace for importing."""
        import time

        if prefix is None:
            prefix = self.TEMP_NAMESPACE_PREFIX

        return f"{prefix}{int(time.time())}"

    @staticmethod
    def _clean_namespace_name(namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
