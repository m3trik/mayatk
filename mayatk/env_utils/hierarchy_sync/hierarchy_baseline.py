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

import pythontk as ptk

from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar


class HierarchyBaseline(ptk.HierarchyBaselineStore):
    """Read, compare and roll forward the scene's hierarchy baseline.

    The storage (``read`` / ``inherited_from`` / ``is_unreadable`` /
    ``compare`` / ``write`` / ``adopt_sidecar``) is
    :class:`pythontk.HierarchyBaselineStore`'s, shared with blendertk; this
    class is configuration: Maya's scene store and sidecar.  A recorded set is
    closed under ancestors on the way out because the sidecar's host says the
    export ships every node's parent chain (``MayaSidecarHost``).
    """

    STORE = DataNodes
    SIDECAR = SceneDataSidecar
