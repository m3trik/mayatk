# !/usr/bin/python
# coding=utf-8
"""Hierarchy sidecar manifest management.

Provides file-based hierarchy tracking alongside exported assets (e.g. FBX).
A ``.hierarchy.json`` manifest records namespace-stripped DAG paths from
the last successful export.  Subsequent exports compare against the manifest
to detect accidental structural changes (missing/extra nodes, reparenting).
"""
import hashlib
import json
import os
from typing import Optional, Set, Tuple


class HierarchySidecar:
    """Manages hierarchy sidecar files stored alongside export files.

    Sidecar files:
        - ``.{stem}.hierarchy.json`` — manifest of DAG paths.
        - ``.{stem}.hierarchy_diff.txt`` — human-readable diff report.
    """

    # ------------------------------------------------------------------
    # Path derivation
    # ------------------------------------------------------------------

    @staticmethod
    def manifest_path_for(export_path: str) -> str:
        """Return the sidecar manifest path for an export file."""
        directory = os.path.dirname(export_path)
        stem = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(directory, f".{stem}.hierarchy.json")

    @staticmethod
    def diff_report_path_for(export_path: str) -> str:
        """Return the sidecar diff report path for an export file."""
        directory = os.path.dirname(export_path)
        stem = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(directory, f".{stem}.hierarchy_diff.txt")

    # ------------------------------------------------------------------
    # Rename / move
    # ------------------------------------------------------------------

    @classmethod
    def rename(cls, old_export_path: str, new_export_path: str) -> list:
        """Rename sidecar files to match a renamed export file.

        Moves the ``.hierarchy.json`` manifest and ``.hierarchy_diff.txt``
        report (if they exist) so that subsequent hierarchy checks find
        the baseline data under the new export name.

        Parameters:
            old_export_path: The previous export path whose sidecars exist.
            new_export_path: The new export path to rename them to.

        Returns:
            A list of ``(old, new)`` tuples for each file that was renamed.
        """
        renamed = []
        for path_fn in (cls.manifest_path_for, cls.diff_report_path_for):
            old = path_fn(old_export_path)
            new = path_fn(new_export_path)
            if os.path.exists(old):
                os.replace(old, new)
                renamed.append((old, new))
            # Also rename .prev backup if present
            old_prev = old + ".prev"
            new_prev = new + ".prev"
            if os.path.exists(old_prev):
                os.replace(old_prev, new_prev)
                renamed.append((old_prev, new_prev))
        return renamed

    # ------------------------------------------------------------------
    # Path utilities
    # ------------------------------------------------------------------

    @staticmethod
    def build_clean_path_set(objects) -> set:
        """Build a set of namespace-stripped hierarchy paths from DAG long paths.

        Strips leading ``|`` and namespace prefixes from each component.
        """
        paths = set()
        for obj in objects:
            path = obj.lstrip("|")
            if ":" in path:
                path = "|".join(p.split(":")[-1] for p in path.split("|"))
            paths.add(path)
        return paths

    @staticmethod
    def expand_to_descendants(objects) -> list:
        """Return *objects* plus all their DAG descendants (full paths).

        Uses ``maya.cmds.listRelatives(allDescendents=True)`` so the
        manifest captures the same scope that
        ``cmds.file(exportSelected=True)`` would export.
        """
        from maya import cmds

        all_paths = list(objects)
        for obj in objects:
            descendants = (
                cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            )
            all_paths.extend(descendants)
        return all_paths

    @staticmethod
    def get_top_level(paths) -> list:
        """Return only paths whose ancestor is *not* also in the set.

        Given ``|``-delimited DAG paths, keeps only the shallowest entries.
        """
        result = []
        for p in sorted(paths, key=lambda x: x.count("|")):
            if not any(p.startswith(r + "|") for r in result):
                result.append(p)
        return result

    @staticmethod
    def detect_reparenting(missing: list, extra: list) -> list:
        """Detect nodes that were reparented rather than added/removed.

        When a subtree is moved under a new parent every original path
        appears in *missing* and the same paths prefixed with the new
        parent appear in *extra*.  This method finds those patterns and
        returns a list of ``(root_missing, new_parent, count)`` tuples
        describing each reparenting.  Unmatched paths are ignored.
        """
        if not missing or not extra:
            return []

        extra_set = set(extra)
        missing_by_root = {}
        for p in missing:
            root = p.split("|")[0]
            missing_by_root.setdefault(root, []).append(p)

        # Unique top-level roots present in extra (avoids redundant checks)
        extra_roots = sorted({e.split("|")[0] for e in extra})

        results = []
        for root, paths in missing_by_root.items():
            for candidate in extra_roots:
                if all(f"{candidate}|{p}" in extra_set for p in paths):
                    results.append((root, candidate, len(paths)))
                    break

        return results

    # ------------------------------------------------------------------
    # Manifest I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _paths_hash(sorted_paths: list) -> str:
        """Return a stable SHA-256 hex digest for a sorted path list."""
        payload = "\n".join(sorted_paths).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def write_manifest(cls, export_path: str, paths) -> Optional[str]:
        """Write *paths* to the sidecar manifest for *export_path*.

        Before overwriting, the existing manifest (if any) is preserved
        as a ``.prev`` file so the last-known-good baseline is always
        available.

        Parameters:
            export_path: The export file the manifest accompanies.
            paths: Iterable of cleaned DAG path strings.

        Returns:
            The manifest file path on success, ``None`` on failure.
        """
        manifest_path = cls.manifest_path_for(export_path)
        sorted_paths = sorted(paths)
        path_hash = cls._paths_hash(sorted_paths)

        # Preserve previous manifest as .prev (skip if hash is identical)
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                if old_data.get("hash") != path_hash:
                    prev_path = manifest_path + ".prev"
                    os.replace(manifest_path, prev_path)
            except (OSError, json.JSONDecodeError):
                pass  # Can't read old manifest — just overwrite

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "paths": sorted_paths,
                        "object_count": len(sorted_paths),
                        "hash": path_hash,
                    },
                    f,
                    indent=2,
                )
            return manifest_path
        except OSError:
            return None

    @classmethod
    def read_manifest(cls, export_path: str) -> Optional[Set[str]]:
        """Read the manifest for *export_path*.

        Returns:
            A set of DAG path strings, or ``None`` if the manifest does
            not exist or cannot be read.
        """
        manifest_path = cls.manifest_path_for(export_path)
        if not os.path.exists(manifest_path):
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("paths", []))
        except (OSError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # Diff report
    # ------------------------------------------------------------------

    @staticmethod
    def count_descendants(top_path: str, all_paths) -> int:
        """Count *top_path* plus its descendants in *all_paths*."""
        path_set = all_paths if isinstance(all_paths, set) else set(all_paths)
        return sum(
            1 for p in path_set if p == top_path or p.startswith(top_path + "|")
        )

    @classmethod
    def _format_top_level_section(
        cls, prefix: str, all_paths: list
    ) -> list:
        """Build lines showing top-level parents with descendant counts.

        Returns a list of formatted strings for a single report section.
        """
        top = cls.get_top_level(all_paths)
        path_set = set(all_paths)
        lines = [f"{prefix} ({len(all_paths)} nodes, {len(top)} top-level):\n"]
        for t in top:
            count = cls.count_descendants(t, path_set)
            if count > 1:
                lines.append(f"  {t}  ({count} nodes)\n")
            else:
                lines.append(f"  {t}\n")
        lines.append("\n")
        return lines

    @classmethod
    def write_diff_report(
        cls,
        export_path: str,
        missing: list,
        extra: list,
        reparented: list = None,
    ) -> Optional[str]:
        """Write a human-readable diff report to the sidecar text file.

        The report contains a summary with top-level rollups followed by
        a full path listing.  Reparenting patterns are called out at the
        top.

        Parameters:
            export_path: The export file the report accompanies.
            missing: Paths present in manifest but absent in current scene.
            extra: Paths in current scene but absent from manifest.
            reparented: Pre-computed reparenting tuples from
                ``detect_reparenting``.  Computed on-demand if ``None``.

        Returns:
            The diff report path on success, ``None`` on failure.
        """
        diff_path = cls.diff_report_path_for(export_path)
        try:
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write("Hierarchy Diff Report\n")
                f.write("=" * 60 + "\n\n")

                # Summary
                f.write("Summary\n")
                f.write("-" * 40 + "\n")
                f.write(f"  Missing:  {len(missing)}\n")
                f.write(f"  Extra:    {len(extra)}\n")
                f.write(
                    f"  Total:    {len(missing) + len(extra)}\n"
                )
                f.write("\n")

                if reparented is None:
                    reparented = cls.detect_reparenting(missing, extra)
                if reparented:
                    for root, parent, count in reparented:
                        f.write(
                            f"Reparented: '{root}' moved under "
                            f"'{parent}' ({count} nodes)\n"
                        )
                    f.write("\n")

                # Top-level rollup
                if missing:
                    for line in cls._format_top_level_section(
                        "Missing", missing
                    ):
                        f.write(line)
                if extra:
                    for line in cls._format_top_level_section(
                        "Extra", extra
                    ):
                        f.write(line)

                # Full path listing
                if missing or extra:
                    f.write("-" * 60 + "\n")
                    f.write("Full Path Listing\n")
                    f.write("-" * 60 + "\n\n")
                if missing:
                    f.write(f"All missing ({len(missing)}):\n")
                    for p in missing:
                        f.write(f"  - {p}\n")
                    f.write("\n")
                if extra:
                    f.write(f"All extra ({len(extra)}):\n")
                    for p in extra:
                        f.write(f"  + {p}\n")
            return diff_path
        except OSError:
            return None

    @classmethod
    def clean_stale_diff(cls, export_path: str) -> None:
        """Remove a stale diff report left over from a previous failure."""
        diff_path = cls.diff_report_path_for(export_path)
        if os.path.exists(diff_path):
            try:
                os.remove(diff_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # High-level: full build + compare
    # ------------------------------------------------------------------

    @classmethod
    def build_full_path_set(cls, objects) -> set:
        """Expand *objects* to descendants, then clean and deduplicate."""
        return cls.build_clean_path_set(cls.expand_to_descendants(objects))

    @classmethod
    def compare(
        cls,
        export_path: str,
        current_paths: set,
    ) -> Tuple[bool, list, list]:
        """Compare *current_paths* against the stored manifest.

        Uses the stored hash for a fast-path equality check before
        falling back to a full set diff.

        Parameters:
            export_path: The export file whose manifest to compare against.
            current_paths: Set of cleaned DAG paths from the current scene.

        Returns:
            ``(match, missing, extra)`` where *match* is ``True`` when
            the hierarchies are identical.
        """
        manifest_path = cls.manifest_path_for(export_path)
        if not os.path.exists(manifest_path):
            return True, [], []

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return True, [], []

        # Fast-path: compare hashes before doing the full set diff
        stored_hash = data.get("hash")
        if stored_hash:
            current_hash = cls._paths_hash(sorted(current_paths))
            if stored_hash == current_hash:
                return True, [], []

        previous = set(data.get("paths", []))
        missing = sorted(previous - current_paths)
        extra = sorted(current_paths - previous)
        return (not missing and not extra), missing, extra
