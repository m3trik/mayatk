# !/usr/bin/python
# coding=utf-8
"""UV diagnostics and repair helpers."""
from __future__ import annotations
from typing import Optional, Sequence, Union
from dataclasses import dataclass, field

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)

# Type aliases keep Maya stubs optional during static analysis
PyNodeLike = Union[str, object]
PyNodeSeq = Union[PyNodeLike, Sequence[PyNodeLike]]


@dataclass
class UvSetCleanupResult:
    """Result of a UV set cleanup operation for a single mesh."""

    shape: str
    initial_sets: list[str] = field(default_factory=list)
    primary_set: Optional[str] = None
    sets_to_delete: list[str] = field(default_factory=list)
    final_name: str = "map1"
    success: bool = False
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.error:
            return f"{self.shape}: ERROR - {self.error}"
        delete_str = ", ".join(self.sets_to_delete) if self.sets_to_delete else "none"
        return (
            f"{self.shape}: {self.initial_sets} → ['{self.final_name}'] "
            f"(primary: '{self.primary_set}', delete: [{delete_str}])"
        )


class UvDiagnostics:
    """Operations for inspecting and fixing common UV issues."""

    @classmethod
    def cleanup_uv_sets(
        cls,
        objects: PyNodeSeq,
        remove_empty: bool = True,
        keep_only_primary: bool = True,
        rename_to_map1: bool = True,
        force_rename: bool = False,
        prefer_largest_area: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
    ) -> list[UvSetCleanupResult]:
        """Cleanup UV sets by removing empty/secondary sets and renaming the primary to 'map1'.

        The cleanup process:
        1. Find the UV set with actual UV data (the "primary" set)
        2. Delete all other UV sets (empty or secondary)
        3. Rename the primary set to 'map1'

        Parameters:
            objects: Polygon objects or components to clean up UV sets for.
            remove_empty: If True, remove empty UV sets (sets with no UV coordinates).
            keep_only_primary: If True, keep only the primary UV set and delete all others.
            rename_to_map1: If True, rename the primary UV set to 'map1'.
            force_rename: If True, force rename even if another 'map1' already exists.
            prefer_largest_area: If True, choose UV set with largest area coverage as primary.
            dry_run: If True, only report what would be done without making changes.
            quiet: If True, suppress output messages.

        Returns:
            List of UvSetCleanupResult objects describing the cleanup for each mesh.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        objects = NodeUtils.get_transform_node(objects)
        results: list[UvSetCleanupResult] = []

        for obj in objects:
            # Get the mesh shape node
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and shape:
                    shape = shape[0]
            except Exception as e:
                results.append(
                    UvSetCleanupResult(
                        shape=str(obj), error=f"failed to get shape: {e}"
                    )
                )
                continue

            if not shape or not isinstance(shape, pm.nt.Mesh):
                continue

            if not shape.hasAttr("uvSet"):
                continue

            result = UvSetCleanupResult(shape=str(shape))

            try:
                # Get initial state
                all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                result.initial_sets = list(all_sets)

                # Check if all UV sets are ghosts - if so, try deleting history first
                # This can "materialize" real UV sets from ghost sets
                real_sets = cls._get_real_uv_sets(shape)
                if not real_sets and all_sets:
                    # All UV sets are ghosts - try deleting construction history
                    try:
                        pm.delete(obj, constructionHistory=True)
                        # Re-query after history deletion
                        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                        real_sets = cls._get_real_uv_sets(shape)
                    except Exception:
                        pass  # History deletion failed, continue anyway

                # Step 1: Identify the primary UV set
                primary_uv_set = cls._find_primary_uv_set(shape, prefer_largest_area)
                result.primary_set = primary_uv_set

                if not primary_uv_set:
                    result.error = "no UV set with data found"
                    results.append(result)
                    continue

                # Determine which sets to delete
                if keep_only_primary:
                    result.sets_to_delete = [s for s in all_sets if s != primary_uv_set]
                elif remove_empty:
                    result.sets_to_delete = cls._get_empty_uv_sets(
                        shape, primary_uv_set
                    )

                # Final name: keep original primary, don't rename
                # (Renaming UV sets affects ALL objects globally in Maya!)
                result.final_name = primary_uv_set

                # Execute if not dry run
                if not dry_run:
                    cleanup_ok = cls._execute_cleanup(
                        shape,
                        primary_uv_set,
                        result.sets_to_delete,
                        result.final_name,
                        force_rename,
                        quiet,
                    )
                    result.success = cleanup_ok
                    if not cleanup_ok:
                        # Verify final state
                        final_sets = (
                            pm.polyUVSet(shape, query=True, allUVSets=True) or []
                        )
                        result.error = f"cleanup incomplete, final sets: {final_sets}"
                else:
                    result.success = True  # Dry run is always "successful"

            except Exception as e:
                result.error = str(e)

            results.append(result)

            if not quiet:
                prefix = "[DRY RUN] " if dry_run else ""
                print(f"{prefix}{result}")

        return results

    @staticmethod
    def _get_real_uv_sets(shape) -> set[str]:
        """Get UV sets that actually exist on this mesh (not ghost/inherited sets).

        Ghost UV sets are those that Maya reports via polyUVSet query but cannot
        be deleted because they're inherited from construction history, instances,
        or other sources. Real UV sets have a valid 'perInstance' attribute.

        Parameters:
            shape: The mesh shape to check.

        Returns:
            Set of UV set names that are "real" (deletable) on this mesh.
        """
        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        real_sets = set()

        for uv_set in dict.fromkeys(all_sets):  # unique
            try:
                per_inst = pm.polyUVSet(
                    shape, query=True, perInstance=True, uvSet=uv_set
                )
                if per_inst:  # Not None/empty = real UV set
                    real_sets.add(uv_set)
            except RuntimeError:
                pass  # Can't query = probably not real

        return real_sets

    @staticmethod
    def _analyze_uv_set(shape, uv_set: str) -> dict:
        """Analyze a UV set and return quality metrics.

        Parameters:
            shape: The mesh shape node
            uv_set: Name of the UV set to analyze

        Returns:
            Dictionary with quality metrics:
            - uv_count: Number of UV coordinates
            - area: Total UV area (coverage in UV space)
            - bounds: (umin, vmin, umax, vmax) bounding box
            - in_bounds: True if UVs are within reasonable range (0-10 for UDIM support)
            - is_valid: True if this is usable UV data
        """
        result = {
            "uv_count": 0,
            "area": 0.0,
            "bounds": (0, 0, 0, 0),
            "in_bounds": False,
            "is_valid": False,
        }

        try:
            pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
            uv_count = pm.polyEvaluate(shape, uvcoord=True) or 0
            result["uv_count"] = uv_count

            if uv_count > 0:
                # Get UV bounding box
                try:
                    bbox = pm.polyEvaluate(shape, boundingBox2d=True)
                    if bbox and len(bbox) == 2:
                        umin, umax = bbox[0]
                        vmin, vmax = bbox[1]
                        result["bounds"] = (umin, vmin, umax, vmax)

                        # Check if UVs are in reasonable range (0-10 for UDIM, allow some overflow)
                        result["in_bounds"] = (
                            umin >= -1 and umax <= 11 and vmin >= -1 and vmax <= 11
                        )
                except:
                    pass

                # Get UV area
                try:
                    area = pm.polyEvaluate(shape, uvArea=True) or 0.0
                    if isinstance(area, (list, tuple)):
                        area = area[0] if area else 0.0
                    result["area"] = float(area)
                except:
                    pass

                # Valid if has UVs, has area, and is in reasonable bounds
                result["is_valid"] = (
                    uv_count > 0
                    and result["area"] > 0.001  # Non-degenerate
                    and result["in_bounds"]
                )
        except RuntimeError:
            pass

        return result

    @staticmethod
    def _find_primary_uv_set(shape, prefer_largest_area: bool = True) -> Optional[str]:
        """Find the best UV set to keep based on UV data quality.

        Selection is based on DATA QUALITY, not naming conventions. Names are
        only used as a tiebreaker when multiple sets have equal quality.

        Quality criteria (in order of importance):
        1. Has valid UV data (non-empty, non-degenerate, within bounds)
        2. UV coverage area (larger = better quality mapping)
        3. UV count (more coordinates = more detailed mapping)
        4. Name preference (standard names as tiebreaker only)

        Sets with '___delete___' prefix are EXCLUDED entirely - this is an
        explicit user directive indicating the set should be removed.

        Parameters:
            shape: The mesh shape to check
            prefer_largest_area: If True, prefer sets with largest UV area.
                If False, prefer sets with most UV coordinates.

        Returns:
            Name of the best UV set, or None if no valid sets exist.
        """
        DELETE_MARKER = "___delete___"
        # Standard names used ONLY as tiebreaker for equal-quality sets
        STANDARD_NAMES = ("map1", "UVChannel_1", "UVMap", "Default", "uvSet")

        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        if not all_sets:
            return None

        # Only consider "real" UV sets (those that can actually be deleted)
        real_sets = UvDiagnostics._get_real_uv_sets(shape)

        # Analyze each unique UV set
        candidates = []  # List of (name, metrics, is_real)
        checked = set()

        for uv_set in all_sets:
            if uv_set in checked:
                continue
            checked.add(uv_set)

            # EXCLUDE delete-marked sets entirely - explicit user directive
            if uv_set.startswith(DELETE_MARKER):
                continue

            is_real = uv_set in real_sets
            metrics = UvDiagnostics._analyze_uv_set(shape, uv_set)
            candidates.append((uv_set, metrics, is_real))

        if not candidates:
            # No non-delete-marked sets found - fall back to any set with data
            for uv_set in dict.fromkeys(all_sets):
                is_real = uv_set in real_sets
                metrics = UvDiagnostics._analyze_uv_set(shape, uv_set)
                if metrics["uv_count"] > 0:
                    candidates.append((uv_set, metrics, is_real))

            if not candidates:
                return None

        # Prefer real UV sets if available
        real_candidates = [c for c in candidates if c[2]]
        if real_candidates:
            candidates = real_candidates

        if len(candidates) == 1:
            return candidates[0][0]

        # Score by quality
        def quality_score(item):
            """Score UV set by data quality. Higher = better."""
            name, metrics, is_real = item

            # Primary: Valid UV data is mandatory for top tier
            valid_bonus = 1000 if metrics["is_valid"] else 0

            # Secondary: Coverage area or UV count
            if prefer_largest_area:
                coverage = metrics["area"]
            else:
                coverage = metrics["uv_count"]

            # Tertiary: Standard name as tiebreaker only
            if name in STANDARD_NAMES:
                name_bonus = 10 - STANDARD_NAMES.index(name)  # 10-5
            else:
                name_bonus = 0

            return (valid_bonus, coverage, name_bonus, metrics["uv_count"])

        best = max(candidates, key=quality_score)
        return best[0]

    @staticmethod
    def _get_empty_uv_sets(shape, primary_uv_set: str) -> list[str]:
        """Get list of empty UV sets (excluding primary)."""
        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        empty_sets: list[str] = []

        for uv_set in all_sets:
            if uv_set == primary_uv_set:
                continue
            try:
                pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                uv_count = pm.polyEvaluate(shape, uvcoord=True) or 0
                if uv_count == 0:
                    empty_sets.append(uv_set)
            except RuntimeError:
                empty_sets.append(uv_set)  # Can't query, probably broken

        return empty_sets

    @staticmethod
    def _execute_cleanup(
        shape,
        primary_uv_set: str,
        sets_to_delete: list[str],
        final_name: str,
        force_rename: bool,
        quiet: bool,
    ) -> bool:
        """Execute the actual UV set cleanup operations.

        Only attempts to delete "real" UV sets (those with perInstance data).
        Ghost UV sets (inherited from history, instances, etc.) are skipped
        as they cannot be deleted via normal Maya commands.

        IMPORTANT: Maya protects the UV set at index 0 (the "default" set).
        To delete it, we first reorder the primary UV set to index 0,
        making all other sets deletable.

        Returns:
            True if cleanup was successful (only primary real UV set remains),
            False otherwise.
        """
        import maya.mel as mel

        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        shape_name = str(shape)  # Get shape name for MEL commands

        # Get the set of "real" UV sets that can actually be deleted
        real_sets = UvDiagnostics._get_real_uv_sets(shape)

        # Ensure primary is set as current before any operations
        if primary_uv_set in all_sets:
            pm.polyUVSet(shape, currentUVSet=True, uvSet=primary_uv_set)

        # CRITICAL: Reorder primary UV set to index 0 FIRST
        # Maya protects the UV set at index 0 from deletion. By moving our
        # primary to index 0, we make all other UV sets deletable.
        if all_sets and all_sets[0] != primary_uv_set and primary_uv_set in all_sets:
            try:
                pm.polyUVSet(
                    shape, reorder=True, uvSet=primary_uv_set, newUVSet=all_sets[0]
                )
                all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                real_sets = UvDiagnostics._get_real_uv_sets(shape)
            except RuntimeError:
                pass  # Reorder failed, continue anyway

        # Delete duplicate UV sets (same name appearing multiple times)
        # But NEVER delete the primary set, and only delete "real" sets
        seen_names = set()
        for uv_set in all_sets:
            if uv_set == primary_uv_set:
                # Skip deleting primary, but mark it as seen
                seen_names.add(uv_set)
                continue
            if uv_set not in real_sets:
                # Skip ghost UV sets - they can't be deleted
                seen_names.add(uv_set)
                continue
            if uv_set in seen_names:
                # This is a duplicate of a non-primary set - delete it using MEL with shape
                try:
                    mel.eval(f'polyUVSet -delete -uvSet "{uv_set}" "{shape_name}";')
                except RuntimeError:
                    pass  # May fail if it's the only one with that name now
            else:
                seen_names.add(uv_set)

        # Re-query after removing duplicates
        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        real_sets = UvDiagnostics._get_real_uv_sets(shape)  # Re-query real sets

        # Delete non-primary REAL sets - keep deleting until only primary remains
        max_iterations = 50  # Safety limit (some meshes have many UV sets)
        failed_sets = set()  # Track sets that fail to delete to avoid retrying
        deleted_count = 0
        ghost_count = 0  # Track ghost sets we skip

        for _ in range(max_iterations):
            current_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []

            # De-duplicate current sets for comparison (Maya reports duplicates)
            unique_current = list(dict.fromkeys(current_sets))

            # Find a non-primary REAL set to delete (skip ghosts and already-failed)
            set_to_delete = None
            for uv_set in unique_current:
                if uv_set == primary_uv_set:
                    continue
                if uv_set in failed_sets:
                    continue
                if uv_set not in real_sets:
                    # This is a ghost UV set - skip it
                    if uv_set not in failed_sets:
                        ghost_count += 1
                        failed_sets.add(uv_set)  # Mark as "handled"
                    continue
                set_to_delete = uv_set
                break

            if not set_to_delete:
                break  # Only primary remains (or all others are ghosts/failed)

            try:
                # If set_to_delete is the current UV set, switch to primary first
                current_uv = pm.polyUVSet(shape, query=True, currentUVSet=True)
                if current_uv == set_to_delete or (
                    isinstance(current_uv, list) and set_to_delete in current_uv
                ):
                    if primary_uv_set in current_sets:
                        pm.polyUVSet(shape, currentUVSet=True, uvSet=primary_uv_set)
                    else:
                        # Find any other real set to switch to
                        other_set = next(
                            (
                                s
                                for s in current_sets
                                if s != set_to_delete and s in real_sets
                            ),
                            None,
                        )
                        if other_set:
                            pm.polyUVSet(shape, currentUVSet=True, uvSet=other_set)

                # Use MEL with shape name directly - this is the only reliable method
                mel.eval(f'polyUVSet -delete -uvSet "{set_to_delete}" "{shape_name}";')

                # Verify deletion actually happened
                new_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                if set_to_delete in new_sets:
                    # Deletion silently failed - mark as failed
                    if not quiet:
                        pm.warning(
                            f"{shape}: deletion of '{set_to_delete}' silently failed"
                        )
                    failed_sets.add(set_to_delete)
                else:
                    deleted_count += 1
                    # Update real_sets after successful deletion
                    real_sets = UvDiagnostics._get_real_uv_sets(shape)
            except Exception as e:
                if not quiet:
                    pm.warning(f"{shape}: failed to delete '{set_to_delete}': {e}")
                failed_sets.add(set_to_delete)
                # Continue trying other sets instead of breaking

        # NOTE: We deliberately do NOT rename UV sets here because Maya's polyUVSet rename
        # command affects ALL objects in the scene with that UV set name, not just
        # the targeted mesh. This causes cross-object corruption.

        # Ensure primary is current
        final_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
        if primary_uv_set in final_sets:
            pm.polyUVSet(shape, currentUVSet=True, uvSet=primary_uv_set)

        # Success if only real UV set remaining is the primary
        # (Ghost UV sets don't count against success)
        final_real_sets = UvDiagnostics._get_real_uv_sets(shape)
        return len(final_real_sets) == 1 and primary_uv_set in final_real_sets
