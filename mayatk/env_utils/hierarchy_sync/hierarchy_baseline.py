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

What it must NOT survive is a Save As into another module: the copy carries the
record verbatim, and its first export was diffed against what the SOURCE
exported (2026-09-24: a module saved as a new one failed with the source's
groups "missing" and its own "new").  So the record is stamped with the scene
file that recorded it (``DataNodes.writer_stamp``), and a record whose writer is
another scene file still on disk is that scene's (``DataNodes.written_here``):
set aside, replaced by this scene's own at its first export.  The stamp is a
declared path (``RecordSpec.paths``), so a save into another project re-spells
it and a copy there still names its source.  A renamed or moved scene keeps its
record -- nothing can open the file it names any more.  Where the scene's own
record holds nothing of the deliverable being exported, that deliverable stands
in (:meth:`HierarchyBaseline.adopt_sidecar`): its sidecar records what it last
shipped, so a version-up (``_v001`` kept, ``_v002`` saved) still diffs every
deliverable it continues -- each one's scope adopted at its own first export.

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

    #: Channel on ``data_internal`` holding the record -- the key of
    #: ``ptk.SceneRecords.HIERARCHY_BASELINE``, which every read and write
    #: below goes through.
    ATTR_NAME = ptk.SceneRecords.HIERARCHY_BASELINE.key

    @classmethod
    def read(cls) -> Set[str]:
        """Every path the scene has recorded, across all scopes.

        Empty when there is no record, the channel is unreadable, the schema is
        not recognised, or the record is not this scene's own
        (:meth:`inherited_from`) -- all of which mean the same thing to a
        caller: nothing to diff against.

        Closed under ancestors on the way out, matching the set the check builds
        from the scene (``SceneDataSidecar.with_ancestors``): Maya's
        ``exportSelected`` ships a node's parent chain, so a leaves-only record
        -- every v1 sidecar, and anything adopted from one -- describes the same
        hierarchy as a group-selected one and must not read as a different one.
        """
        try:
            record, own = cls._record()
            paths = ptk.HierarchyBaseline.decode(record) if own else set()
            return SceneDataSidecar.with_ancestors(paths) if paths else set()
        except Exception:  # a check must never break the scene it inspects
            return set()

    @classmethod
    def inherited_from(cls) -> Optional[str]:
        """Who recorded the baseline this scene holds but does not own.

        The record's writer stamp (``DataNodes.writer_stamp``) when it names
        another scene file still on disk -- the source of a Save As copy --
        and ``""`` when it names no scene file at all: recorded before records
        were stamped, or while its scene was unsaved, so a copy's cannot be
        told from its source's.  ``None`` when the record is this scene's own,
        or there is no readable record.  :meth:`read` sets such a record aside.
        """
        try:
            record, own = cls._record()
            if own or not ptk.HierarchyBaseline.is_record(record):
                return None
            return ptk.HierarchyBaseline.recorded_by(record) or ""
        except Exception:  # a check must never break the scene it inspects
            return None

    @classmethod
    def _record(cls) -> Tuple[object, bool]:
        """``(record, own)``: the stored record, and whether this scene owns it
        (``DataNodes.written_here`` of its writer stamp)."""
        record = ptk.SceneRecords.HIERARCHY_BASELINE.load(DataNodes)
        stamp = ptk.HierarchyBaseline.recorded_by(record)
        return record, DataNodes.written_here(stamp)

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
            raw = ptk.SceneRecords.HIERARCHY_BASELINE.read_text(DataNodes)
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

        Stamped with this scene (``DataNodes.writer_stamp``).  A record the
        scene does not own reads empty, so it is replaced rather than merged:
        a Save As copy's first export starts the copy's own record.

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
            cls._save(merged)
            return True
        except Exception:
            return False

    @classmethod
    def _save(cls, paths: Set[str]) -> None:
        """Store *paths* as this scene's own record."""
        ptk.SceneRecords.HIERARCHY_BASELINE.save(
            DataNodes,
            ptk.HierarchyBaseline.encode(paths, scene=DataNodes.writer_stamp()),
        )

    @classmethod
    def adopt_sidecar(cls, export_path: str, *, base_stem: bool = False) -> bool:
        """Give the scene what *export_path* last shipped, where its own
        baseline holds nothing of that deliverable.

        The deliverable's ``.scene_data.json`` sidecar records the hierarchy
        its last export shipped, whichever scene made it: the history of a
        scene exported before the baseline moved into the scene, of a
        version-up whose record is its predecessor's, and of a copy
        re-exporting its source's deliverable.  THIS deliverable's only --
        every module of a production can export into one folder, and another
        deliverable's sidecar there is another module's history (adopting all
        of them diffed a new module against its neighbours).  The sidecar is
        brought up to the current naming first (``SceneDataSidecar
        .migrate_legacy``) and is NOT deleted: it remains the consumer-facing
        payload.

        Per scope (``ptk.HierarchyBaseline.adopt``): adopted beside whatever
        the record holds of other deliverables -- a scene whose baseline was
        set aside records its first deliverable at that one's export, and
        each other deliverable's history must still come from its own
        sidecar -- and never over a scope the record already holds, where a
        stale sidecar would resurrect paths the scene has since dropped.

        Parameters:
            export_path: The deliverable being exported.
            base_stem: The Output Filename carries a version counter, so every
                version of the deliverable shares one sidecar.

        Returns:
            bool: True when a sidecar was adopted.
        """
        try:
            SceneDataSidecar.migrate_legacy(export_path, base_stem=base_stem)
            adopted = ptk.HierarchyBaseline.adopt(
                cls.read(),
                SceneDataSidecar.read_manifest(export_path, base_stem=base_stem),
            )
            if adopted is None:
                return False
            cls._save(adopted)
            return True
        except Exception:  # a check must never break the scene it inspects
            return False

    #: The sidecar names the baseline used to live under, per export stem --
    #: the current one and the v1 spelling, because a scene that never
    #: re-exported since v1 still has its history under the old name and must
    #: not lose it on the way in.
    _LEGACY_SUFFIXES = (".scene_data.json", ".hierarchy.json")

    @classmethod
    @ptk.Deprecation.symbol(
        "HierarchyBaseline.adopt_sidecar", remove_in="0.21.0", since="2026-09-24"
    )
    def migrate_from_sidecar(cls, export_dir: str) -> int:
        """Adopt every on-disk baseline in *export_dir* into the scene, once.

        Superseded by :meth:`adopt_sidecar`, which adopts only the deliverable
        being exported: a folder several modules export into holds the other
        modules' histories too.  Runs only when the scene has no record of its
        own.  Returns the number of sidecars adopted.
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
            cls._save(paths)
        return adopted
