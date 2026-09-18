# !/usr/bin/python
# coding=utf-8
"""The scene's hierarchy baseline, stored in the scene.

ONE baseline per scene, on the ``data_internal`` carrier (``network`` node --
stays with the file, never exports), beside the other scene-private records
that live there (``shot_store``, ``key_stash``, SmartBake's restore manifest).

It used to live in the export's ``.scene_data.json`` sidecar, keyed by the
deliverable's file stem.  That made the baseline a property of the NAME rather
than of the scene: renaming the Output Filename -- a prefix, a regex, a literal
name -- pointed the next export at a different sidecar and silently started a
fresh baseline, so the first export after any rename passed no matter what had
changed.  The ``_v<N>`` stripping patched exactly one renaming axis and the
naming report asked the user to end names in ``_v<N>`` to keep the key working.
Here there is no key: the scene holds its own record, which survives the output
being renamed, the scene being renamed or moved, and the file being handed to
another machine.

The set algebra is ``ptk.HierarchyBaseline`` (shared with blendertk); this class
is only its storage and its migration.  Scope is derived at compare time from
the roots being exported, so one record serves every export a scene makes:
exporting asset B never reports asset A as missing, and recording B never
forgets A.
"""

import os
from typing import List, Optional, Sequence, Set, Tuple

import pythontk as ptk

from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class HierarchyBaseline:
    """Read, compare and roll forward the scene's hierarchy baseline."""

    #: Channel on ``data_internal`` holding the record.
    ATTR_NAME = "hierarchy_baseline"

    @classmethod
    def read(cls) -> Set[str]:
        """Every path the scene has recorded, across all scopes.

        Empty when there is no record, the channel is unreadable, or the schema
        is not recognised -- all of which mean the same thing to a caller:
        nothing to diff against.

        Closed under ancestors on the way out, matching the set the check builds
        from the scene (``SceneDataSidecar.with_ancestors``): Maya's
        ``exportSelected`` ships a node's parent chain, so a leaves-only record
        -- every v1 sidecar, and anything adopted from one -- describes the same
        hierarchy as a group-selected one and must not read as a different one.
        """
        try:
            paths = ptk.HierarchyBaseline.decode(
                DataNodes.get_internal_json(cls.ATTR_NAME)
            )
            return SceneDataSidecar.with_ancestors(paths) if paths else set()
        except Exception:  # a check must never break the scene it inspects
            return set()

    @classmethod
    def is_unreadable(cls) -> bool:
        """The channel holds something, but no baseline could be read from it.

        "No record" and "a record nothing can be read from" both leave the check
        with nothing to diff, but they are not the same event: the second means
        a baseline was LOST, and the scene should be told rather than quietly
        given a fresh one. Same rule the sidecar-era check applied to an
        unreadable manifest.
        """
        try:
            raw = DataNodes.get_internal_string(cls.ATTR_NAME)
        except Exception:
            return False
        # is_record, not read(): a valid record that happens to hold no paths
        # decodes to an empty set exactly as a corrupt one does, and calling
        # that "unreadable" would warn about a baseline nothing had lost.
        return bool(raw) and not ptk.HierarchyBaseline.is_record(raw)

    @classmethod
    def compare(
        cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None
    ) -> Tuple[bool, List[str], List[str], bool]:
        """Diff *current_paths* against the baseline, scoped to what is exporting.

        Returns ``(match, missing, extra, is_new_scope)`` -- see
        :meth:`pythontk.HierarchyBaseline.compare`.
        """
        return ptk.HierarchyBaseline.compare(cls.read(), current_paths, roots)

    @classmethod
    def write(
        cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None
    ) -> bool:
        """Roll the exported scope forward, leaving every other scope intact.

        Returns True when the record was written.  Never raises: a baseline the
        scene could not record must not fail the export that produced it -- the
        caller warns instead, because a silently stale baseline is what corrupts
        the NEXT run's diff.
        """
        try:
            merged = ptk.HierarchyBaseline.merge(cls.read(), current_paths, roots)
            if not merged:
                # Nothing to record. Writing an empty record would create a
                # channel that says "baseline, no paths" -- indistinguishable
                # from a real one to every reader, and pointless to keep.
                return True
            DataNodes.set_internal_json(
                cls.ATTR_NAME, ptk.HierarchyBaseline.encode(merged)
            )
            return True
        except Exception:
            return False

    #: The sidecar names the baseline used to live under, per export stem --
    #: the current one and the v1 spelling, because a scene that never
    #: re-exported since v1 still has its history under the old name and must
    #: not lose it on the way in.
    _LEGACY_SUFFIXES = (".scene_data.json", ".hierarchy.json")

    @classmethod
    def migrate_from_sidecar(cls, export_dir: str) -> int:
        """Adopt any on-disk baselines in *export_dir* into the scene, once.

        Reads every ``.{stem}.scene_data.json`` in the folder -- and the v1
        ``.hierarchy.json`` spelling -- and merges its
        ``hierarchy.paths`` into the scene's record, so upgrading does not throw
        away the history a user already has.  The sidecars are NOT deleted: they
        remain the consumer-facing payload, they simply stop being read as a
        baseline.

        Runs only when the scene has no record yet -- a merge of stale per-name
        sidecars into a live baseline would resurrect paths the scene has since
        dropped.  Returns the number of sidecars adopted.
        """
        if cls.read():
            return 0
        try:
            names = [
                n
                for n in os.listdir(export_dir)
                if n.startswith(".") and n.endswith(cls._LEGACY_SUFFIXES)
            ]
        except OSError:
            return 0

        adopted, paths = 0, set()
        for name in names:
            record = ptk.FileUtils.read_json(os.path.join(export_dir, name))
            if not isinstance(record, dict):
                continue
            section = record.get("hierarchy")
            if not isinstance(section, dict):
                # v1 sidecars are flat: the paths sit at the top level.
                section = record
            found = section.get("paths")
            if isinstance(found, list):
                paths.update(p for p in found if isinstance(p, str))
                adopted += 1
        if paths:
            DataNodes.set_internal_json(
                cls.ATTR_NAME, ptk.HierarchyBaseline.encode(paths)
            )
        return adopted
