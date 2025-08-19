# !/usr/bin/python
# coding=utf-8
from typing import List, Dict, Optional, Tuple, Any
import pymel.core as pm
import pythontk as ptk


class MatchingEngine(ptk.LoggingMixin):
    """Core engine for object matching with fuzzy logic and container searches.

    This class consolidates all matching logic to eliminate duplication between
    ObjectMatcher and DryRunAnalyzer.
    """

    def __init__(self, import_manager, fuzzy_matching: bool = True):
        super().__init__()
        self.import_manager = import_manager
        self.fuzzy_matching = fuzzy_matching

    def _clean_namespace_name(self, namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def find_matches(
        self,
        target_objects: List[str],
        imported_transforms: List,
        dry_run: bool = False,
    ) -> Tuple[List, Dict]:
        """Find matching objects using exact and fuzzy matching.

        Args:
            target_objects: List of target object names to find
            imported_transforms: List of imported transform nodes
            dry_run: Whether this is a dry-run analysis

        Returns:
            Tuple of (found_objects, fuzzy_match_map)
        """
        found_objects = []
        fuzzy_match_map = {}

        for target_name in target_objects:
            # Try exact match first
            exact_matches = self._find_exact_matches(target_name, imported_transforms)
            if exact_matches:
                found_objects.extend(exact_matches)
                log_prefix = "[DRY-RUN] " if dry_run else ""
                self.logger.notice(f"{log_prefix}Exact match found: {target_name}")
                continue

            # Log debug info
            self._log_debug_info(target_name, imported_transforms, dry_run)

            # Try fuzzy matching if enabled
            if self.fuzzy_matching:
                match_result = self._find_fuzzy_match(
                    target_name, imported_transforms, dry_run
                )
                if match_result:
                    matching_node, fuzzy_target_name = match_result
                    found_objects.append(matching_node)
                    fuzzy_match_map[matching_node] = fuzzy_target_name

        return found_objects, fuzzy_match_map

    def _find_exact_matches(self, target_name: str, imported_transforms: List) -> List:
        """Find exact name matches."""
        return [
            node
            for node in imported_transforms
            if self._clean_namespace_name(node.nodeName()) == target_name
        ]

    def _find_fuzzy_match(
        self, target_name: str, imported_transforms: List, dry_run: bool = False
    ) -> Optional[Tuple[Any, str]]:
        """Find fuzzy match for target object."""
        # Allow fuzzy matching even if target exists in current scene
        # This is needed for object replacement scenarios
        if pm.objExists(target_name):
            log_prefix = "[DRY-RUN] " if dry_run else ""
            self.logger.debug(
                f"{log_prefix}Target '{target_name}' exists in current scene - will attempt fuzzy match for replacement"
            )

        # Get clean names for fuzzy matching
        import_names = [
            self._clean_namespace_name(node.nodeName()) for node in imported_transforms
        ]

        # Try fuzzy matching with standard threshold
        matches = ptk.FuzzyMatcher.find_all_matches(
            [target_name], import_names, score_threshold=0.7
        )

        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}Fuzzy matching for '{target_name}' with threshold 0.7: {len(matches)} matches found"
        )

        # If no matches found, log debugging info and return None
        if target_name not in matches:
            # Try with lower threshold for debugging purposes only
            lower_matches = ptk.FuzzyMatcher.find_all_matches(
                [target_name], import_names, score_threshold=0.5
            )
            if target_name in lower_matches and lower_matches[target_name]:
                matched_name, score = lower_matches[target_name]
                self.logger.debug(
                    f"{log_prefix}Best fuzzy match for '{target_name}': '{matched_name}' (score: {score:.3f}) - below threshold 0.7"
                )
            else:
                self.logger.debug(
                    f"{log_prefix}No fuzzy matches found even with threshold 0.5"
                )
            return None  # Get potential matches
        potential_matches = self._get_potential_matches(target_name, import_names)

        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}Trying {len(potential_matches)} potential matches for {target_name}"
        )

        # Try each match in order of score (best first)
        for matched_name, score in potential_matches:
            match_result = self._evaluate_potential_match(
                target_name, matched_name, score, imported_transforms, dry_run
            )
            if match_result:
                return match_result

        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.warning(
            f"{log_prefix}No suitable fuzzy match found for {target_name}"
        )
        return None

    def _get_potential_matches(
        self, target_name: str, import_names: List[str]
    ) -> List[Tuple[str, float]]:
        """Get list of potential matches sorted by score."""
        matches = ptk.FuzzyMatcher.find_all_matches(
            [target_name], import_names, score_threshold=0.7
        )

        if target_name not in matches:
            return []

        potential_matches = [(matches[target_name][0], matches[target_name][1])]

        # Try to get additional matches if supported
        try:
            all_matches = ptk.FuzzyMatcher.find_all_matches(
                [target_name],
                import_names,
                score_threshold=0.6,
                return_all=True,
            )
            if target_name in all_matches and isinstance(
                all_matches[target_name], list
            ):
                potential_matches = all_matches[target_name]
        except (TypeError, AttributeError):
            # Fallback if return_all parameter not supported
            pass

        return potential_matches

    def _evaluate_potential_match(
        self,
        target_name: str,
        matched_name: str,
        score: float,
        imported_transforms: List,
        dry_run: bool = False,
    ) -> Optional[Tuple[Any, str]]:
        """Evaluate a potential match and return result if suitable."""
        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}Evaluating match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
        )

        # Find the actual node
        try:
            matching_node = next(
                node
                for node in imported_transforms
                if self._clean_namespace_name(node.nodeName()) == matched_name
            )
        except StopIteration:
            return None

        # Search inside containers for better matches
        better_match_result = self._search_container_for_better_match(
            matching_node, target_name, matched_name, score, dry_run
        )

        if better_match_result:
            matching_node, matched_name, score, should_use_match = better_match_result

            if should_use_match:
                self.logger.notice(
                    f"{log_prefix}Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                )
                return matching_node, target_name

        return None

    def _search_container_for_better_match(
        self,
        matching_node: Any,
        target_name: str,
        matched_name: str,
        score: float,
        dry_run: bool = False,
    ) -> Optional[Tuple[Any, str, float, bool]]:
        """Search inside containers for better matches."""
        better_match_found = False
        log_prefix = "[DRY-RUN] " if dry_run else ""

        # Check if the matched object has children that might be better matches
        if hasattr(matching_node, "getChildren"):
            try:
                better_child, child_has_shapes = self._find_better_match_recursive(
                    matching_node, target_name, dry_run
                )

                if better_child:
                    # Found a better match inside the container
                    original_matched_name = matched_name
                    matching_node = better_child
                    matched_name = self._clean_namespace_name(better_child.nodeName())
                    score = 0.95  # High score for exact match found inside container
                    better_match_found = True

                    self.logger.info(
                        f"{log_prefix}Found better match inside container: '{original_matched_name}' -> '{matched_name}' (has_shapes: {child_has_shapes})"
                    )
                else:
                    self.logger.debug(
                        f"{log_prefix}No better match found inside container '{matched_name}'"
                    )

            except Exception as search_error:
                self.logger.debug(
                    f"{log_prefix}Could not search inside container: {search_error}"
                )

        # Decide whether to use this match
        should_use_match = self._should_use_match(
            matching_node, better_match_found, score, dry_run
        )

        return matching_node, matched_name, score, should_use_match

    def _find_better_match_recursive(
        self,
        node: Any,
        target_name: str,
        dry_run: bool = False,
        depth: int = 0,
        max_depth: int = 3,
    ) -> Tuple[Optional[Any], bool]:
        """Recursively search for better matches inside containers."""
        if depth > max_depth:
            return None, False

        log_prefix = "[DRY-RUN] " if dry_run else ""
        children = node.getChildren()

        for child in children:
            child_clean_name = self._clean_namespace_name(child.nodeName())
            child_has_shapes = (
                bool(child.getShapes()) if hasattr(child, "getShapes") else False
            )

            # Check if this child is a better match
            is_exact_match = child_clean_name == target_name
            is_better_match = False

            if is_exact_match:
                is_better_match = True
                self.logger.debug(
                    f"{log_prefix}Found exact name match: {child_clean_name}"
                )
            elif child_has_shapes and target_name in child_clean_name:
                is_better_match = True
                self.logger.debug(
                    f"{log_prefix}Found geometry match: {child_clean_name} (has shapes)"
                )
            elif (
                len(child_clean_name) > len(node.nodeName())
                and target_name in child_clean_name
            ):
                is_better_match = True
                self.logger.debug(
                    f"{log_prefix}Found more specific match: {child_clean_name}"
                )

            if is_better_match:
                return child, child_has_shapes

            # If this child doesn't match but has children, search recursively
            if child.getChildren():
                recursive_result, recursive_has_shapes = (
                    self._find_better_match_recursive(
                        child, target_name, dry_run, depth + 1, max_depth
                    )
                )
                if recursive_result:
                    return recursive_result, recursive_has_shapes

        return None, False

    def _should_use_match(
        self,
        matching_node: Any,
        better_match_found: bool,
        score: float,
        dry_run: bool = False,
    ) -> bool:
        """Determine if a match should be used based on quality criteria."""
        container_has_shapes = (
            bool(matching_node.getShapes())
            if hasattr(matching_node, "getShapes")
            else False
        )

        # Only skip the match if:
        # 1. We searched inside a container (has children)
        # 2. Found no better match inside
        # 3. The container itself has no geometry/content
        # 4. The match quality isn't very high
        if (
            hasattr(matching_node, "getChildren")
            and matching_node.getChildren()
            and not better_match_found
            and not container_has_shapes
            and score < 0.9
        ):
            log_prefix = "[DRY-RUN] " if dry_run else ""
            matched_name = self._clean_namespace_name(matching_node.nodeName())
            self.logger.warning(
                f"{log_prefix}Skipping low-quality container match: score: {score:.2f}, empty container)"
            )
            return False

        return True

    def _log_debug_info(
        self, target_name: str, imported_transforms: List, dry_run: bool = False
    ) -> None:
        """Log debug information about available objects."""
        log_prefix = "[DRY-RUN] " if dry_run else ""

        # Show available objects that contain the target name
        available_containing_target = [
            self._clean_namespace_name(node.nodeName())
            for node in imported_transforms
            if target_name in self._clean_namespace_name(node.nodeName())
        ]

        if available_containing_target:
            self.logger.debug(
                f"{log_prefix}Objects containing '{target_name}': {available_containing_target[:10]}"
            )
        else:
            self.logger.debug(
                f"{log_prefix}No objects found containing '{target_name}'"
            )

        # Show a sample of all available objects for context
        sample_objects = [
            self._clean_namespace_name(node.nodeName())
            for node in imported_transforms[:20]
        ]
        self.logger.debug(f"{log_prefix}Sample available objects: {sample_objects}")
