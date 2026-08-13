# !/usr/bin/python
# coding=utf-8
"""Scene-data sidecar manifest management.

An export's sidecar ``.{stem}.scene_data.json`` is an externalized copy of
what shipped in the FBX: the exported hierarchy (which the FBX carries
natively as its node tree) and the ``data_export`` carrier channels (which
ride into the FBX as user properties).  Consumers get the export's metadata
without parsing FBX — and for GLB-only deliverables the sidecar is the one
guaranteed-readable copy.

Two sections with distinct semantics::

    {
      "format": 2,
      "hierarchy":   {"paths": [...], "object_count": N, "hash": "..."},
      "data_export": {"shot_metadata": {...}, "fbx_takes": [...], ...}
    }

- ``hierarchy`` — change-detection baseline: sorted namespace-stripped DAG
  paths from the last successful export plus a SHA-256 hash.  Subsequent
  exports compare against it to detect accidental structural changes
  (missing/extra nodes, reparenting).  :meth:`SceneDataSidecar.compare`
  reads ONLY this section, and the hash covers only the paths — so metadata
  churn can never false-positive the hierarchy check.
- ``data_export`` — snapshot of the ``data_export`` carrier at export time
  (``DataNodes.dump``), decoded to nested JSON.  Never includes
  ``data_internal``: that node's contract is scene-private state that must
  not export, and a sidecar next to the deliverable is a form of export.
  Omitted entirely when the carrier shipped nothing.

Format v1 (a flat ``{"paths": ..., "object_count": ..., "hash": ...}``
written by the former ``HierarchySidecar`` under the name
``.{stem}.hierarchy.json``) is still readable — v1 content is treated as the
hierarchy section, and :meth:`migrate_legacy` promotes v1-named files to the
current name.
"""
import hashlib
import json
import logging
import os
import re
from typing import Optional, Set, Tuple

import pythontk as ptk


class SceneDataSidecar:
    """Manages scene-data sidecar files stored alongside export files.

    Sidecar files:
        - ``.{stem}.scene_data.json`` — manifest of hierarchy paths plus the
          ``data_export`` channel snapshot (see module docstring).
        - ``.{stem}.hierarchy_diff.txt`` — human-readable hierarchy diff report.

    When ``base_stem=True`` is passed to the path helpers, a trailing
    ``_v\\d+`` suffix is stripped so that all versioned exports of a
    series share a single sidecar (the record rolls forward to the
    most recent export, with ``.prev`` holding the one before it).
    Used by the SceneExporter ``version`` task.
    """

    # Anchored to end-of-stem so it only matches genuine version suffixes,
    # not mid-name occurrences like 'arch_v2_proxy'.
    VERSION_SUFFIX_RE = re.compile(r"_v\d+$", re.IGNORECASE)

    MANIFEST_SUFFIX = "scene_data.json"
    # Format-v1 manifest name, still recognized for read/migration.
    LEGACY_MANIFEST_SUFFIX = "hierarchy.json"

    # ------------------------------------------------------------------
    # Path derivation
    # ------------------------------------------------------------------

    @classmethod
    def base_stem(cls, export_path: str) -> str:
        """Return the export stem with any trailing ``_vNN`` suffix stripped."""
        stem = os.path.splitext(os.path.basename(export_path))[0]
        return cls.VERSION_SUFFIX_RE.sub("", stem)

    @classmethod
    def _stem_for(cls, export_path: str, base_stem: bool) -> str:
        return (
            cls.base_stem(export_path)
            if base_stem
            else os.path.splitext(os.path.basename(export_path))[0]
        )

    @classmethod
    def manifest_path_for(cls, export_path: str, *, base_stem: bool = False) -> str:
        """Return the sidecar manifest path for an export file.

        Parameters:
            export_path: The export file path (e.g. ``shot_v003.fbx``).
            base_stem: If True, strip the version suffix so all versions of a
                series share one manifest. Opt-in: callers without an active
                versioning context should leave this False to preserve
                per-file sidecar behavior.
        """
        directory = os.path.dirname(export_path)
        stem = cls._stem_for(export_path, base_stem)
        return os.path.join(directory, f".{stem}.{cls.MANIFEST_SUFFIX}")

    @classmethod
    def _legacy_manifest_path_for(
        cls, export_path: str, *, base_stem: bool = False
    ) -> str:
        """Return the format-v1 (``.hierarchy.json``) manifest path."""
        directory = os.path.dirname(export_path)
        stem = cls._stem_for(export_path, base_stem)
        return os.path.join(directory, f".{stem}.{cls.LEGACY_MANIFEST_SUFFIX}")

    @classmethod
    def diff_report_path_for(cls, export_path: str, *, base_stem: bool = False) -> str:
        """Return the sidecar diff report path for an export file."""
        directory = os.path.dirname(export_path)
        stem = cls._stem_for(export_path, base_stem)
        return os.path.join(directory, f".{stem}.hierarchy_diff.txt")

    # ------------------------------------------------------------------
    # Legacy sidecar migration
    # ------------------------------------------------------------------

    @classmethod
    def find_legacy_manifest(cls, export_path: str) -> Optional[str]:
        """Return the path of a legacy per-version sidecar to migrate from.

        Scans the export directory for ``.{base}_v<N>.scene_data.json`` and
        ``.{base}_v<N>.hierarchy.json`` files and returns the one with the
        highest version number (compared as integers, so unpadded ``_v10``
        ranks above ``_v2``; on a version tie the current naming wins).
        Returns None if no legacy sidecars exist.
        """
        directory = os.path.dirname(export_path)
        base = cls.base_stem(export_path)
        if not directory or not os.path.isdir(directory):
            return None
        # Group 2 ranks the current naming above the v1 naming on a tie.
        legacy_re = re.compile(
            rf"^\.{re.escape(base)}_v(\d+)\."
            rf"(?:({re.escape(cls.MANIFEST_SUFFIX)})|{re.escape(cls.LEGACY_MANIFEST_SUFFIX)})$",
            re.IGNORECASE,
        )
        matches = []
        for f in os.listdir(directory):
            m = legacy_re.match(f)
            if m:
                matches.append((int(m.group(1)), bool(m.group(2)), f))
        if not matches:
            return None
        _, _, name = max(matches, key=lambda t: (t[0], t[1]))
        return os.path.join(directory, name)

    @staticmethod
    def _safe_replace(src: str, dst: str) -> bool:
        """``os.replace`` that survives transient locks (cloud-sync, AV scans).

        A failed sidecar rename must never abort the export that triggered
        the migration — a locked file here used to propagate up and mislabel
        a *successful* FBX write as "Failed to export objects".  The caller
        keeps working with whichever file still exists; returns True when
        the rename actually happened.
        """
        try:
            os.replace(src, dst)
            return True
        except OSError as e:
            logging.getLogger(__name__).warning(
                "Sidecar rename failed (%s -> %s): %s", src, dst, e
            )
            return False

    @classmethod
    def _promote_stem(cls, export_path: str, *, base_stem: bool) -> Optional[str]:
        """Rename a same-stem v1-named manifest (and its ``.prev``) to the
        current name.  Returns the current-name path if a manifest exists
        after promotion, else None.  Idempotent.
        """
        new_path = cls.manifest_path_for(export_path, base_stem=base_stem)
        old_path = cls._legacy_manifest_path_for(export_path, base_stem=base_stem)
        if not os.path.exists(new_path) and os.path.exists(old_path):
            cls._safe_replace(old_path, new_path)
        # Carry the .prev backup too — even when orphaned (manifest deleted,
        # backup intact): compare() falls back to .prev, and that protection
        # must survive the name migration.
        if os.path.exists(old_path + ".prev") and not os.path.exists(
            new_path + ".prev"
        ):
            cls._safe_replace(old_path + ".prev", new_path + ".prev")
        return new_path if os.path.exists(new_path) else None

    @classmethod
    def ensure_base_name(cls, export_path: str) -> Optional[str]:
        """Migrate a legacy per-version manifest to the base-stem name.

        Called by callers that opt into ``base_stem=True``.  If a base-stem
        manifest already exists (either naming — a v1-named one is renamed),
        returns its path.  Otherwise, if a legacy ``_v\\d+`` manifest exists,
        renames it (and its ``.prev``) to the base-stem path and returns the
        new path.  Returns None if nothing was found.

        Idempotent: callers can invoke this on every check without harm.
        """
        promoted = cls._promote_stem(export_path, base_stem=True)
        if promoted:
            return promoted
        legacy = cls.find_legacy_manifest(export_path)
        if not legacy:
            return None
        new_path = cls.manifest_path_for(export_path, base_stem=True)
        if not cls._safe_replace(legacy, new_path):
            # Locked mid-migration — behave as if the legacy manifest wasn't
            # found (the check reports "no manifest" and a fresh one is
            # written after export) rather than raising into the exporter.
            return None
        if os.path.exists(legacy + ".prev"):
            cls._safe_replace(legacy + ".prev", new_path + ".prev")
        return new_path

    @classmethod
    def migrate_legacy(
        cls, export_path: str, *, base_stem: bool = False
    ) -> Optional[str]:
        """Idempotently bring on-disk sidecars up to the current naming.

        Promotes a same-stem v1-named ``.hierarchy.json`` manifest to
        ``.scene_data.json``; with ``base_stem=True`` additionally promotes
        the newest per-version manifest of either naming to the shared
        base-stem name (the version-series promotion).

        Returns the manifest path if one exists after migration, else None.
        The single migration entry point for exporter callers — invoke before
        every manifest read/write.
        """
        if base_stem:
            return cls.ensure_base_name(export_path)
        return cls._promote_stem(export_path, base_stem=False)

    # ------------------------------------------------------------------
    # Rename / move
    # ------------------------------------------------------------------

    @classmethod
    def rename(cls, old_export_path: str, new_export_path: str) -> list:
        """Rename sidecar files to match a renamed export file.

        Moves the ``.scene_data.json`` manifest and ``.hierarchy_diff.txt``
        report (if they exist) so that subsequent hierarchy checks find
        the baseline data under the new export name.  Any v1-named sidecars
        are promoted to the current naming first, so one pass covers them.

        Parameters:
            old_export_path: The previous export path whose sidecars exist.
            new_export_path: The new export path to rename them to.

        Returns:
            A list of ``(old, new)`` tuples for each file that was renamed.
        """
        cls._promote_stem(old_export_path, base_stem=False)
        cls._promote_stem(old_export_path, base_stem=True)

        renamed = []
        for path_fn in (cls.manifest_path_for, cls.diff_report_path_for):
            # Cover both per-file and base-stem sidecar variants.
            pairs = list(
                dict.fromkeys(
                    [
                        (path_fn(old_export_path), path_fn(new_export_path)),
                        (
                            path_fn(old_export_path, base_stem=True),
                            path_fn(new_export_path, base_stem=True),
                        ),
                    ]
                )
            )
            for old, new in pairs:
                if old == new:
                    continue
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


    @staticmethod
    def _hierarchy_section(manifest: dict) -> dict:
        """Return the hierarchy section of a loaded manifest.

        Format v2 nests it under ``"hierarchy"``; a v1 manifest IS the
        hierarchy section (flat ``paths``/``object_count``/``hash``).
        """
        section = manifest.get("hierarchy")
        return section if isinstance(section, dict) else manifest

    @classmethod
    def write_manifest(
        cls,
        export_path: str,
        paths,
        *,
        data: Optional[dict] = None,
        base_stem: bool = False,
    ) -> Optional[str]:
        """Write the sidecar manifest for *export_path*.

        Before overwriting, the existing manifest (if any) is preserved
        as a ``.prev`` file — so the previous export's full record stays
        available — unless neither the hierarchy nor the data section
        changed.

        Parameters:
            export_path: The export file the manifest accompanies.
            paths: Iterable of cleaned DAG path strings.
            data: Decoded ``data_export`` channel snapshot to record
                (``DataNodes.dump(decode=True)`` shape).  Omitted from the
                manifest when empty.
            base_stem: If True, strip the version suffix from the path
                derivation so all versions share one manifest.

        Returns:
            The manifest file path on success, ``None`` on failure.
        """
        manifest_path = cls.manifest_path_for(export_path, base_stem=base_stem)
        sorted_paths = sorted(paths)
        path_hash = cls._paths_hash(sorted_paths)

        payload = {
            "format": 2,
            "hierarchy": {
                "paths": sorted_paths,
                "object_count": len(sorted_paths),
                "hash": path_hash,
            },
        }
        if data:
            # A sidecar next to the deliverable is a form of export, so it
            # records no authoring-machine paths (see the pythontk twin, which
            # scrubs the GLB the same way).
            payload["data_export"] = ptk.MeshConvert.without_locate_hints(data)

        # Write the new manifest to a temp file FIRST — moving the old one
        # to .prev before a failed write would leave no manifest at all
        # (compare() then silently passes).
        tmp_path = manifest_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                # default=str guards the rare non-JSON-native channel value
                # (mirrors DataNodes.format_dump).
                json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        except OSError:
            return None

        # Preserve previous manifest as .prev (skip only when neither
        # section changed — the hash covers just the hierarchy paths).
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                old_hash = cls._hierarchy_section(old_data).get("hash")
                old_payload = old_data.get("data_export")
                if old_hash != path_hash or old_payload != payload.get("data_export"):
                    os.replace(manifest_path, manifest_path + ".prev")
            except (OSError, json.JSONDecodeError):
                pass  # Can't read old manifest — just overwrite

        try:
            os.replace(tmp_path, manifest_path)
            return manifest_path
        except OSError:
            # Don't leave the orphaned .tmp behind — it would sit next to
            # the deliverable forever (nothing else ever cleans it up).
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return None

    @classmethod
    def _load_manifest(cls, manifest_path: str) -> Optional[dict]:
        """Load manifest JSON, falling back to its ``.prev`` backup.

        Accidental deletion or a corrupt write usually leaves the ``.prev``
        backup intact — comparing against the last-known-good baseline
        beats silently passing.  Returns None when neither file is
        readable (no baseline yet).
        """
        for path in (manifest_path, manifest_path + ".prev"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
        return None

    @classmethod
    def read_manifest(
        cls, export_path: str, *, base_stem: bool = False
    ) -> Optional[Set[str]]:
        """Read the hierarchy paths from the manifest for *export_path*.

        Falls back to the ``.prev`` backup when the manifest itself is
        missing or unreadable.  Reads both format v2 and flat v1 manifests.

        Returns:
            A set of DAG path strings, or ``None`` if neither the
            manifest nor its backup can be read.
        """
        data = cls._load_manifest(
            cls.manifest_path_for(export_path, base_stem=base_stem)
        )
        if data is None:
            return None
        return set(cls._hierarchy_section(data).get("paths", []))

    @classmethod
    def read_data(
        cls, export_path: str, *, base_stem: bool = False
    ) -> Optional[dict]:
        """Read the ``data_export`` snapshot from the manifest for *export_path*.

        Returns:
            The decoded channel dict, or ``None`` when no manifest exists or
            it carries no data section (v1 manifests never do).
        """
        data = cls._load_manifest(
            cls.manifest_path_for(export_path, base_stem=base_stem)
        )
        if data is None:
            return None
        section = data.get("data_export")
        return section if isinstance(section, dict) else None

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
    def _format_top_level_section(cls, prefix: str, all_paths: list) -> list:
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
        *,
        base_stem: bool = False,
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
        diff_path = cls.diff_report_path_for(export_path, base_stem=base_stem)
        try:
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write("Hierarchy Diff Report\n")
                f.write("=" * 60 + "\n\n")

                # Summary
                f.write("Summary\n")
                f.write("-" * 40 + "\n")
                f.write(f"  Missing:  {len(missing)}\n")
                f.write(f"  Extra:    {len(extra)}\n")
                f.write(f"  Total:    {len(missing) + len(extra)}\n")
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
                    for line in cls._format_top_level_section("Missing", missing):
                        f.write(line)
                if extra:
                    for line in cls._format_top_level_section("Extra", extra):
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
    def clean_stale_diff(cls, export_path: str, *, base_stem: bool = False) -> None:
        """Remove a stale diff report left over from a previous failure."""
        diff_path = cls.diff_report_path_for(export_path, base_stem=base_stem)
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
        *,
        base_stem: bool = False,
    ) -> Tuple[bool, list, list]:
        """Compare *current_paths* against the stored hierarchy baseline.

        Uses the stored hash for a fast-path equality check before
        falling back to a full set diff.  When the manifest is missing or
        unreadable its ``.prev`` backup is used as the baseline; with
        neither present the check passes (no baseline yet).  Reads both
        format v2 and flat v1 manifests; the data section is ignored.

        Parameters:
            export_path: The export file whose manifest to compare against.
            current_paths: Set of cleaned DAG paths from the current scene.
            base_stem: If True, route manifest lookup through the version
                base-stem so the diff baseline rolls forward across versions.

        Returns:
            ``(match, missing, extra)`` where *match* is ``True`` when
            the hierarchies are identical.
        """
        manifest_path = cls.manifest_path_for(export_path, base_stem=base_stem)
        data = cls._load_manifest(manifest_path)
        if data is None:
            return True, [], []
        section = cls._hierarchy_section(data)

        # Fast-path: compare hashes before doing the full set diff
        stored_hash = section.get("hash")
        if stored_hash:
            current_hash = cls._paths_hash(sorted(current_paths))
            if stored_hash == current_hash:
                return True, [], []

        previous = set(section.get("paths", []))
        missing = sorted(previous - current_paths)
        extra = sorted(current_paths - previous)
        return (not missing and not extra), missing, extra
