# !/usr/bin/python
# coding=utf-8
"""Hermetic preview with replay-on-commit (H1 design): Maya's preview session.

The widgets and the lifecycle -- the enable checkbox, the commit button, the
window's show / hide hooks, Select Result, the error popup -- are uitk's
``PreviewController``, shared with blendertk; :class:`Preview` is it bound to
:class:`MayaPreviewSession`, the scene half written here.

The preview phase runs ``perform_operation`` with Maya's undo recording
suppressed. A :class:`CleanupContract` records what was created (and
optionally which attrs were mutated, which files were written) so rollback
can reverse it without touching Maya's undo stack. On commit, the work is
replayed inside an ``openChunk``/``closeChunk`` pair so the user gets a
single Ctrl+Z-able undo entry.

The preview is **optional**: the Create button is live from the start and a
click with the preview off runs the operation once on the current selection,
inside the same undo chunk the commit replay uses. Panels that must not commit
blind pass ``require_preview=True`` to restore the old gating (button dead
until Preview is checked).

Why this shape:
  - No ``Undo`` scriptJob -> no false-positive disables from plugin/internal
    undo events.
  - No undo chunk during preview -> user Ctrl+Z mid-preview navigates their
    own pre-preview history rather than tearing through ours.
  - Rollback is a snapshot/diff over ``cmds.ls`` -> deterministic cleanup,
    no dependency on Maya's undo stack.
  - Replay on commit -> committed work is user-undoable like any other op.

Constraints on ``perform_operation`` authors:
  - Signature is ``perform_operation(self, objects, contract)``.
  - Mesh ops must use ``constructionHistory=True`` so rollback can revert
    geometry by deleting the history node. This is sufficient only when the
    mesh has upstream history to fall back to. For ops that mutate a mesh in
    place (a poly op on a historyless mesh that bakes its result when its
    auto-created orig-shape is deleted, or Curve to Tube resampling its source
    curve in place), set ``PRESERVE_GEOMETRY = True`` on the operation instance.
    The contract then snapshots the operated objects and, on rollback, restores
    any mesh or curve that survived but diverged from the snapshot -- geometry,
    identity/UUID, and per-face (multi-material) shading, which the geometry pipe
    alone doesn't carry.
  - Do not delete pre-existing nodes inside ``perform_operation`` (diff is
    one-way; deletions cannot be reversed).  This includes *replacing* one --
    un-instancing swaps a transform's shape, so the rollback deletes the new
    shape as a created node and leaves the transform empty.  Preconditions
    like that belong in the optional ``prepare_operation(objects)`` hook,
    which runs once at ``enable`` outside any contract (see below).
  - Mutating an attribute on a pre-existing node requires
    ``contract.record_modification(node, attr)`` before the ``setAttr``.
  - Disk writes that should be cleaned on rollback require
    ``contract.add_file(path)``.
  - ``contract`` is ``None`` during the commit replay; guard with
    ``if contract: contract.add_file(...)``.
  - State no rollback reaches -- a record a shared node carries, republished
    from the scene; a file a pass wrote -- can instead be repaired by
    ``restore_func``: called once after the rollback that DISCARDS the
    preview (an un-check or a failed pass; never a commit), recorded nowhere
    on the undo queue. blendertk's Preview takes the same hook.

Verification: see ``test/temp_tests/verify_preview_*.py`` for the 60+
empirical tests this design passes.
"""

import logging
from pathlib import Path
from typing import Any, Callable, List, Optional, Set, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception:
    cmds = om = None

import uitk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.node_utils._node_utils import NodeUtils


class OperationError(Exception):
    """User-facing operation failure for the Preview message box.

    Raise from ``perform_operation`` -- chained with ``from`` so the console
    keeps the original traceback -- to replace a raw, multi-line driver error
    (e.g. Maya's ``polyBridgeEdge`` wall of text + help URLs) with a short,
    readable popup. ``causes`` render as bullet points to help the user
    self-diagnose; inline HTML (e.g. ``<b>...</b>``) is supported.
    """

    def __init__(self, message: str, causes=None, title: str = "Operation failed"):
        super().__init__(message)
        self.user_message = message
        self.causes = list(causes) if causes else []
        self.title = title


class CleanupContract:
    """Captures and reverses side effects of a previewed operation.

    Use as a context manager. ``__enter__`` snapshots the scene and
    suppresses undo recording; ``__exit__`` re-enables undo and records the
    diff in :attr:`created`. :meth:`rollback` reverses everything.

    ``preserve`` (optional): list of node paths to duplicate+hide before
    snapshotting. If perform_operation deletes any of them, rollback
    restores from the duplicate. Required for ops like Mirror that delete
    the original mesh as part of their workflow.
    """

    def __init__(self, preserve: Optional[List[str]] = None):
        self.created: Set[str] = set()
        self.files: List[Path] = []
        self.attr_snapshots: List[Tuple[str, str, Any]] = []
        self._preserve_objects: List[str] = list(preserve or [])
        self._preserved: List[dict] = []
        self._prev_undo_state: Optional[bool] = None
        self._before: Optional[Tuple[frozenset, frozenset]] = None

    def __enter__(self):
        # stateWithoutFlush=False disables undo recording WITHOUT clearing
        # the user's existing undo queue. state=False FLUSHES the queue --
        # destroys pre-preview history. Never use state= here.
        self._prev_undo_state = cmds.undoInfo(q=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            self._enter_body()
        except Exception:
            # Restore undo state on any unexpected failure -- otherwise
            # an exception in __enter__ (e.g. cmds.ls failing) would leave
            # recording permanently disabled for the rest of the session
            # because the `with` block never calls __exit__ when __enter__
            # raises.
            cmds.undoInfo(stateWithoutFlush=self._prev_undo_state)
            raise
        return self

    def _enter_body(self) -> None:
        # Preserve originals (before snapshot, so duplicates land in _before
        # and are NOT counted as `created`).
        # We capture UUIDs for the entire tree under each preserved root so
        # rollback can re-assign them via MFnDependencyNode.setUuid -- pipeline
        # tooling that tracks by UUID won't see the rollback as identity loss.
        for obj in self._preserve_objects:
            try:
                if not cmds.objExists(obj):
                    continue
                orig_long_list = cmds.ls(obj, long=True) or []
                if not orig_long_list:
                    continue
                orig_long = orig_long_list[0]
                uuid_list = cmds.ls(obj, uuid=True) or []
                if not uuid_list:
                    continue
                # Map relative path under root -> uuid string, for every
                # descendant. cmds.duplicate preserves the relative-path
                # structure, so this lets us re-pair UUIDs on restore.
                descendants = [orig_long] + (
                    cmds.listRelatives(obj, ad=True, fullPath=True) or []
                )
                uuid_map = {}
                for d in descendants:
                    rel = d[len(orig_long) :]  # "" for root
                    d_uuid = cmds.ls(d, uuid=True) or [None]
                    if d_uuid[0]:
                        uuid_map[rel] = d_uuid[0]
                short = obj.split("|")[-1]
                parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
                orig_parent = parents[0] if parents else None
                # Duplicate without upstream/input connections so we don't
                # carry hooks into the live network.
                dup = cmds.duplicate(
                    obj,
                    name=f"_preview_preserve_{short}_",
                    upstreamNodes=False,
                    inputConnections=False,
                    returnRootsOnly=True,
                )[0]
                try:
                    cmds.setAttr(f"{dup}.visibility", 0)
                except Exception:
                    pass
                self._preserved.append(
                    {
                        "orig_short": short,
                        "orig_uuid": uuid_list[0],
                        "dup": dup,
                        "orig_parent": orig_parent,
                        "uuid_map": uuid_map,
                    }
                )
            except Exception:
                # If preserve fails for one object, continue with others.
                # Op may still succeed; rollback for this object won't.
                continue

        self._before = (
            frozenset(cmds.ls(long=True, allPaths=True) or []),
            frozenset(cmds.ls() or []),
        )

    def snapshot_created(self) -> Set[str]:
        """Diff the scene against the entry snapshot: what exists now that
        didn't at ``__enter__``.

        ``__exit__`` calls this to populate :attr:`created`. It is public so a
        caller that must act on the created nodes *while still inside* the
        contract can -- Preview's isolation update has to run under suppressed
        undo, and by the time :attr:`created` is populated ``__exit__`` has
        already restored undo recording.
        """
        if self._before is None:  # __enter__ raised before snapshotting
            return set()
        dag_after = frozenset(cmds.ls(long=True, allPaths=True) or [])
        dg_after = frozenset(cmds.ls() or [])
        return (dag_after - self._before[0]) | (dg_after - self._before[1])

    def __exit__(self, *exc):
        try:
            self.created = self.snapshot_created()
        finally:
            cmds.undoInfo(stateWithoutFlush=self._prev_undo_state)
        return False  # don't suppress exceptions

    def add_file(self, path) -> None:
        self.files.append(Path(path))

    #: Sentinel snapshot value: the attr did not exist at record time, so
    #: rollback must DELETE it (the op added it onto a pre-existing node)
    #: rather than restore a value. Without it the getAttr raised and the
    #: snapshot was silently skipped — canceling a preview left the freshly
    #: added channel (e.g. shadows on an existing data_export
    #: carrier) behind with stale data.
    _ATTR_ABSENT = object()

    def record_modification(self, node: str, attr: str) -> None:
        if cmds.objExists(node):
            try:
                if not cmds.attributeQuery(attr, node=node, exists=True):
                    self.attr_snapshots.append((node, attr, self._ATTR_ABSENT))
                    return
                value = cmds.getAttr(f"{node}.{attr}")
                self.attr_snapshots.append((node, attr, value))
            except Exception:
                pass

    # ------------------------------------------- in-place geometry restore
    @classmethod
    def _geo_signature(cls, transform: str):
        """Cheap signature used to decide whether a surviving original still
        matches its pristine duplicate.

        Handles polygon meshes (topology + world extent) and NURBS curves
        (spans / degree / form + world extent). Returns ``None`` for shapes of
        neither kind, which the divergence check treats as "not diverged" -- so
        the in-place restore only ever runs on geometry it knows how to rebuild.
        """
        shape = NodeUtils.get_shape(transform, no_intermediate=True)
        if not shape:
            return None
        try:
            bbox = tuple(round(v, 5) for v in cmds.exactWorldBoundingBox(transform))
        except Exception:
            bbox = None
        stype = cmds.objectType(shape)
        if stype == "mesh":
            try:
                sig = (
                    cmds.polyEvaluate(shape, vertex=True),
                    cmds.polyEvaluate(shape, edge=True),
                    cmds.polyEvaluate(shape, face=True),
                )
            except Exception:
                return None
            # polyEvaluate returns a str/dict on non-mesh or error shapes.
            if not all(isinstance(n, int) for n in sig):
                return None
            return ("mesh",) + sig + (bbox,)
        if stype == "nurbsCurve":
            try:
                # CV positions (not just count/bbox): a resample can keep the
                # span count and world extent while moving the control points,
                # which spans+bbox alone would miss.
                cvs = cmds.xform(
                    f"{shape}.cv[*]", query=True, objectSpace=True, translation=True
                )
                sig = (
                    cmds.getAttr(f"{shape}.degree"),
                    cmds.getAttr(f"{shape}.form"),
                    tuple(round(c, 5) for c in cvs),
                )
            except Exception:
                return None
            return ("curve",) + sig
        return None

    @classmethod
    def _geo_diverged(cls, transform: str, dup: str) -> bool:
        a = cls._geo_signature(transform)
        b = cls._geo_signature(dup)
        if a is None or b is None:
            return False
        return a != b

    @classmethod
    def _restore_geo_in_place(cls, transform: str, dup: str) -> None:
        """Overwrite *transform*'s geometry with the pristine *dup*'s while
        keeping the original shape node (and thus its identity/UUID) intact.

        Feeds the duplicate's object-space output into the original's input
        (mesh: ``outMesh -> inMesh``; curve: ``local -> create``) then bakes it
        via ``delete(ch=True)`` -- which evaluates the pristine geometry into the
        shape and severs the temporary connection.
        """
        orig_shape = NodeUtils.get_shape(transform, no_intermediate=True)
        dup_shape = NodeUtils.get_shape(dup, no_intermediate=True)
        if not orig_shape or not dup_shape:
            return
        stype = cmds.objectType(orig_shape)
        if stype == "mesh":
            cmds.connectAttr(f"{dup_shape}.outMesh", f"{orig_shape}.inMesh", force=True)
        elif stype == "nurbsCurve":
            cmds.connectAttr(f"{dup_shape}.local", f"{orig_shape}.create", force=True)
        else:
            return
        cmds.delete(orig_shape, constructionHistory=True)

    @staticmethod
    def _shares_surviving_instance(path: str, created: Set[str]) -> bool:
        """True if *path* is a shape node that is multiply-instanced and at least
        one of its instance paths is NOT in *created* -- i.e. it's shared with a
        surviving original. Deleting such a shape by its created path would orphan
        the survivor's geometry (instance-mode DuplicateLinear/Radial share the
        original's shape); deleting the created instance transform removes the
        extra instance safely instead.
        """
        try:
            sel = om.MSelectionList()
            sel.add(path)
            if not sel.getDependNode(0).hasFn(om.MFn.kShape):
                return False
            node = sel.getDagPath(0).node()
            all_paths = [p.fullPathName() for p in om.MDagPath.getAllPathsTo(node)]
            return len(all_paths) > 1 and any(p not in created for p in all_paths)
        except Exception:
            return False

    def rollback(self) -> None:
        prev = cmds.undoInfo(q=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            # 1. Restore mutated attrs before their owners might be deleted.
            for node, attr, value in reversed(self.attr_snapshots):
                if not cmds.objExists(node):
                    continue
                try:
                    if value is self._ATTR_ABSENT:
                        if cmds.attributeQuery(attr, node=node, exists=True):
                            cmds.deleteAttr(f"{node}.{attr}")
                    elif (
                        isinstance(value, (list, tuple))
                        and len(value) == 1
                        and isinstance(value[0], (list, tuple))
                    ):
                        cmds.setAttr(f"{node}.{attr}", *value[0], type="double3")
                    elif isinstance(value, str) or (
                        value is None
                        and cmds.getAttr(f"{node}.{attr}", type=True) == "string"
                    ):
                        # Bare setAttr raises on string attrs (they need the
                        # type flag) — without this branch a snapshotted
                        # string channel (e.g. a data_export producer attr)
                        # silently failed to restore into the except below.
                        # An empty one snapshots as the None getAttr reads and
                        # writes back empty (Attributes.set_plug's rule; this
                        # layer cannot import it).
                        cmds.setAttr(f"{node}.{attr}", value or "", type="string")
                    else:
                        cmds.setAttr(f"{node}.{attr}", value)
                except Exception:
                    pass
            # 2. Delete created nodes -- expressions first to avoid eval errors
            # on still-dangling references. Skip multiply-instanced shape paths
            # shared with a surviving (non-created) instance: instance-mode ops
            # (e.g. DuplicateLinear) share the ORIGINAL's shape, and deleting it
            # by the created path destroys the original's geometry. Deleting the
            # created instance *transforms* removes the extra instance safely and
            # leaves the shared shape on the survivor.
            existing = [
                n
                for n in self.created
                if cmds.objExists(n)
                and not self._shares_surviving_instance(n, self.created)
            ]
            exprs = [n for n in existing if cmds.nodeType(n) == "expression"]
            rest = [n for n in existing if n not in exprs]
            if exprs:
                cmds.delete(exprs)
            remaining = [n for n in rest if cmds.objExists(n)]
            if remaining:
                cmds.delete(remaining)
            # 3. Restore preserved originals if perform_operation deleted them.
            # Identity check is by UUID, not name -- a new node with the same
            # name is not the same node.
            for p in self._preserved:
                try:
                    orig_alive = bool(cmds.ls(p["orig_uuid"]))
                    if not orig_alive and cmds.objExists(p["dup"]):
                        # Reparent dup back to where the original lived.
                        if p["orig_parent"] and cmds.objExists(p["orig_parent"]):
                            try:
                                cmds.parent(p["dup"], p["orig_parent"])
                            except RuntimeError:
                                pass  # already there or parent is invalid
                        # Rename back to original short name.
                        restored = cmds.rename(p["dup"], p["orig_short"])
                        try:
                            cmds.setAttr(f"{restored}.visibility", 1)
                        except Exception:
                            pass
                        # Re-assign UUIDs across the restored tree so pipeline
                        # tooling (UUID-keyed asset trackers, animation refs,
                        # set membership) sees identity continuity.
                        restored_long_list = cmds.ls(restored, long=True) or []
                        if restored_long_list and p.get("uuid_map"):
                            restored_long = restored_long_list[0]
                            for rel, target_uuid in p["uuid_map"].items():
                                target_path = restored_long + rel
                                if not cmds.objExists(target_path):
                                    continue
                                try:
                                    sel = om.MSelectionList()
                                    sel.add(target_path)
                                    mobj = sel.getDependNode(0)
                                    om.MFnDependencyNode(mobj).setUuid(
                                        om.MUuid(target_uuid)
                                    )
                                except Exception:
                                    pass  # collision or invalid; leave dup uuid
                    elif cmds.objExists(p["dup"]):
                        # Original survived but may have been mutated in place:
                        # ops like polyCut on a HISTORYLESS mesh create an
                        # intermediate orig shape that holds the only pristine
                        # copy, and deleting it (counted as "created") leaves the
                        # mutated result baked in; Curve to Tube resamples the
                        # source curve in place. Detect divergence against the
                        # pristine duplicate and restore in place -- preserving
                        # the original shape's identity/UUID -- rather than
                        # leaving the mutation. When they match (node-diff already
                        # reverted, e.g. geometry WITH upstream history), skip the
                        # restore so legitimate construction history is kept.
                        try:
                            orig_path = (cmds.ls(p["orig_uuid"], long=True) or [None])[
                                0
                            ]
                            if orig_path and self._geo_diverged(orig_path, p["dup"]):
                                self._restore_geo_in_place(orig_path, p["dup"])
                        finally:
                            # Always remove the snapshot, even if restore raised.
                            if cmds.objExists(p["dup"]):
                                cmds.delete(p["dup"])
                except Exception:
                    continue
            # 4. Files
            for f in self.files:
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
        finally:
            cmds.undoInfo(stateWithoutFlush=prev)


class MayaPreviewSession:
    """Maya's scene half of a preview (:class:`uitk.PreviewSession`).

    Each pass runs under a fresh :class:`CleanupContract` with undo recording
    suppressed, and a rollback reverses it from the contract's diff; a commit
    rolls the pass back (recorded nowhere) and replays the operation inside
    the commit's undo chunk. Per-face shading the in-place geometry rollback
    drops is reasserted from an enable-time snapshot after every clean
    operation; Isolate Select viewports get the result added.

    Parameters:
        operation: The operation, ``perform_operation(objects, contract)``
            (``contract`` is None on a commit); ``MUTATES_SELECTION`` /
            ``PRESERVE_GEOMETRY`` make each pass's contract preserve the
            captured objects' transforms.
    """

    def __init__(self, operation):
        self.operation = operation
        self.logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        self._contract: Optional[CleanupContract] = None
        self._captured_objects: List[str] = []
        # {transform_uuid: shading-assignments} captured pristine at enable.
        # Per-face (multi-material) shading is dropped by the hermetic preview's
        # in-place geometry rollback and CANNOT be reliably restored in place
        # (Maya leaves malformed shading groups that the next poly op collapses),
        # so it is reasserted from this snapshot AFTER each clean op -- the
        # forward preview op and the commit replay -- where assignment sticks.
        # See _reassert_shading_snapshot.
        self._shading_snapshot: dict = {}

    @property
    def captured_objects(self) -> List[str]:
        """The objects the preview (or the commit) captured, components
        included."""
        return list(self._captured_objects)

    # ------------------------------------------------------ PreviewSession
    @staticmethod
    def selection() -> List[str]:
        """The current selection, components included."""
        return cmds.ls(selection=True) or []

    def begin(self, objects) -> None:
        """Capture *objects* and their pristine per-face shading."""
        self._captured_objects = list(objects)
        self._shading_snapshot = self._capture_shading_snapshot(objects)

    def run(self) -> None:
        """One hermetic pass under a fresh contract.

        If the operation declares ``MUTATES_SELECTION = True`` or
        ``PRESERVE_GEOMETRY = True``, the contract preserves (duplicates+hides)
        the captured selection's owning transform(s) so rollback can restore
        originals that perform_operation deletes or bakes in place. The
        selection is resolved to transforms because component selections (e.g.
        Bridge's edges) can't be duplicated. Default is opt-in to avoid paying
        duplication cost for ops that don't need it (e.g. ShadowRig on a 10k-node
        hierarchy).
        """
        preserve = None
        if getattr(self.operation, "MUTATES_SELECTION", False) or getattr(
            self.operation, "PRESERVE_GEOMETRY", False
        ):
            # Resolve the captured selection to its owning transform(s).
            # Component selections (e.g. Bridge's edges) can't be
            # duplicated/UUID'd, and on a historyless mesh rollback's node-diff
            # bakes the op's result when it deletes the auto-created orig-shape
            # -- so the preserved geometry is what restores it. get_transform_node
            # is idempotent for transform selections (Mirror, CutOnAxis), so this
            # is safe for every preserve user.
            resolved = NodeUtils.get_transform_node(
                self._captured_objects, returned_type="str"
            )
            if resolved:
                preserve = (
                    list(resolved)
                    if isinstance(resolved, (list, tuple))
                    else [resolved]
                )
        self._contract = CleanupContract(preserve=preserve)
        with self._contract:
            self.operation.perform_operation(self._captured_objects, self._contract)
            # Reassert per-face shading on the freshly-built preview mesh so
            # a multi-material object doesn't flash bright green. Must run
            # here (after the op, clean groups) not in rollback (bare mesh
            # rebuild leaves malformed groups the next op would collapse).
            self._reassert_shading_snapshot()
            # Add the inputs AND everything the op just created to the
            # isolation set. Must stay inside the contract -- see
            # _update_isolation for why.
            self._update_isolation(self._contract.snapshot_created)

    def rollback(self) -> None:
        """Reverse the last pass from its contract (recorded nowhere)."""
        contract, self._contract = self._contract, None
        if contract is not None:
            contract.rollback()

    def discard(self, reselect: bool) -> None:
        """After the discarding rollback: the per-face shading the geometry
        rollback dropped, and with *reselect* the enable-time selection
        (components included), recorded nowhere -- that selection already
        sits at the top of the user's history."""
        # No op follows a cancel, so the restored (in-place) shading is what
        # the user is left looking at; it renders correctly even though a
        # future op could collapse it -- acceptable for a cancelled preview.
        self._reassert_shading_snapshot()
        if reselect:
            self._reselect_captured()

    def commit(self) -> None:
        """Roll the hermetic pass back (recorded nowhere, even inside the open
        chunk) and replay it, recorded."""
        self.rollback()
        self._replay()

    def apply(self, objects) -> None:
        """Capture *objects* and run the operation once, recorded: the same
        replay the previewed commit uses, so its result and undo entry are
        identical."""
        self.begin(objects)
        self._replay()

    @staticmethod
    def undo_step(name: str):
        """One undo chunk named *name* (``CoreUtils.undo_chunk``)."""
        return CoreUtils.undo_chunk(name)

    @staticmethod
    def unrecorded():
        """Undo recording off, the queue kept (``CoreUtils.undo_disabled``)."""
        return CoreUtils.undo_disabled()

    def select_result(self, checkbox, results, committed: bool) -> None:
        """:meth:`Preview.apply_result_selection` in object mode; re-asserted
        on idle after a commit."""
        Preview.apply_result_selection(
            checkbox, results, object_mode=True, defer=committed
        )

    # ------------------------------------------------------------ internals
    def _replay(self) -> None:
        """Run the operation on the captured objects, inside the commit's undo
        chunk. ``contract`` is ``None`` -- a commit has no rollback path, so
        nothing is recorded."""
        # Isolate Select: a commit has no contract to diff against, so the
        # before-set is taken here -- and only when the user is actually
        # isolated (the gate is one getPanel query; the snapshot is a full
        # cmds.ls). Inside the chunk, so the membership commits and undoes
        # atomically with the operation.
        iso_before = (
            frozenset(cmds.ls(long=True, allPaths=True) or [])
            if DisplayUtils.get_isolated_panels()
            else None
        )
        self.operation.perform_operation(self._captured_objects, None)
        # Reassert per-face material on the freshly-built (clean) mesh so the
        # COMMITTED result keeps every material -- inside the chunk so it
        # commits/undoes atomically with the operation.
        self._reassert_shading_snapshot()
        if iso_before is not None:
            self._update_isolation(
                lambda: (frozenset(cmds.ls(long=True, allPaths=True) or []) - iso_before)
            )

    @staticmethod
    def _capture_shading_snapshot(objects) -> dict:
        """Snapshot the operated meshes' per-face shading as data, keyed by the
        owning transform's UUID. Captured pristine at enable and reasserted after
        each clean op because the hermetic preview's in-place rollback drops
        per-face (multi-material) shading. Only multi-material meshes are stored
        -- object-level shading survives the preview on its own, so single-
        material tools pay nothing. Lazy import: mat_utils -> core."""
        snap: dict = {}
        try:
            from mayatk.mat_utils._mat_utils import MatUtils

            resolved = NodeUtils.get_transform_node(objects, returned_type="str")
            tfs = resolved if isinstance(resolved, (list, tuple)) else [resolved]
            for tf in tfs:
                if not tf:
                    continue
                assigns = MatUtils.get_shading_assignments(tf)
                if not any(faces is not None for faces in assigns.values()):
                    continue
                uuid = (cmds.ls(tf, uuid=True) or [None])[0]
                if uuid:
                    snap[uuid] = assigns
        except Exception:
            pass
        return snap

    def _reassert_shading_snapshot(self) -> None:
        """Reapply the pristine enable-time shading snapshot to its meshes (by
        UUID). MUST run on a freshly-built mesh -- right after the forward
        preview op or the commit replay, both of which leave clean shading
        groups -- never on a bare in-place-rolled-back mesh, where the per-face
        assignment doesn't stick (Maya collapses the malformed groups on the
        next op)."""
        if not self._shading_snapshot:
            return
        try:
            from mayatk.mat_utils._mat_utils import MatUtils

            for uuid, assigns in self._shading_snapshot.items():
                tf = (cmds.ls(uuid, long=True) or [None])[0]
                if tf:
                    MatUtils.apply_shading_assignments(tf, assigns)
        except Exception:
            pass

    def _reselect_captured(self) -> None:
        """Re-select the enable-time selection after a cancel (see discard)."""
        alive = [o for o in self._captured_objects if cmds.objExists(o)]
        if not alive:
            return
        with CoreUtils.undo_disabled():
            try:
                cmds.select(alive, replace=True)
            except Exception:
                self.logger.debug("reselect on disable failed", exc_info=True)

    # -------------------------------------------------------- isolate select
    @staticmethod
    def _dag_roots(created) -> List[str]:
        """Shallowest created DAG paths.

        ``created`` mixes long DAG paths (``|grp|pCube1``) with short DG names,
        and lists every descendant of a created group. Isolate Select draws a
        member's whole subtree, so only the roots need adding: drop anything
        without a leading ``|`` (DG nodes -- shaders, sets, history -- can't be
        isolated, and the DG half of the diff also duplicates every DAG node
        under its short name) and anything sitting under another created path.
        Sorting puts a parent immediately before its children, so one pass with
        a prefix test is enough.
        """
        roots: List[str] = []
        for path in sorted(p for p in (created or ()) if p.startswith("|")):
            if roots and path.startswith(f"{roots[-1]}|"):
                continue
            roots.append(path)
        return roots

    def _update_isolation(self, created_provider: Callable[[], Any]) -> None:
        """Add the operation's inputs *and its newly created nodes* to every
        isolated viewport, so a previewed or committed result is visible to a
        user working in "view selected".

        MUST be called from inside the contract (preview) or inside the undo
        chunk (commit): membership is a ``cmds.sets`` connection that Maya
        records, so run bare it leaks one entry per preview refresh and the
        first Ctrl+Z presses after a commit pop those instead of the operation.

        The created nodes come from a scene node diff rather than from
        ``result_provider`` because most panels have none (ShadowRig,
        DuplicateRadial's combine path) and a provider is Slots-owned code that
        can raise or return names from the previous cycle -- which Maya may have
        since reused for an unrelated node. The diff costs a full-scene
        ``cmds.ls``, so it is only *requested* when isolate mode is actually on;
        with isolate off (the normal case) this is one ``getPanel`` query.

        Membership is a connection, not a node, so the contract's node diff
        neither tracks nor reverses it -- which is what we want: the
        user-initiated isolation persists across preview cycles, and a member
        that rollback deletes drops out of the set on its own (Maya destroys the
        membership with the node), so rebuild cycles cannot accumulate dead
        members.
        """
        if not DisplayUtils.get_isolated_panels():
            return

        targets = list(self._captured_objects)
        # MUTATES_SELECTION ops (Mirror) may have deleted the captured names;
        # their post-op selection is the surviving result. add_to_isolation_set
        # filters by objExists, so dead names are no-ops.
        if getattr(self.operation, "MUTATES_SELECTION", False):
            try:
                targets.extend(cmds.ls(selection=True) or [])
            except Exception:
                pass
        try:
            targets.extend(self._dag_roots(created_provider()))
        except Exception:
            # The diff is the only step here that can raise -- the two
            # DisplayUtils calls own that contract themselves. Losing it just
            # means the inputs get isolated without the result.
            self.logger.debug("isolation node-diff failed", exc_info=True)
        DisplayUtils.add_to_isolation_set(targets)


class Preview(uitk.PreviewController):
    """Hermetic preview orchestrator (H1): ``Preview(slot, ui.chk000, ui.b000, ...)``.

    :class:`uitk.PreviewController` bound to :class:`MayaPreviewSession`
    (its session, plus the host's Select Result primitive; the signature and
    every lifecycle method are the controller's). ``perform_operation`` on the operation instance must accept
    ``(objects, contract)``. Mirror of blendertk's ``Preview``.
    """

    SESSION_CLASS = MayaPreviewSession

    @staticmethod
    def apply_result_selection(
        widget, results, *, object_mode: bool = False, defer: bool = False
    ) -> None:
        """Select the operation's result(s) — or explicitly deselect them — per a
        "Select Result" checkbox, the way a Preview panel wants it on preview/commit.

        With *widget* checked the results are selected so the user can see them; with
        it unchecked they are explicitly **deselected** (an op can leave its result
        selected, so "off" must actively clear it). The widget read is defensive: a
        missing/dead checkbox falls back to selecting (the prior behavior). The
        checked state is re-read on each (immediate and deferred) pass so the settled
        UI value wins — interactive state-restore can settle the toggle just after a
        commit returns.

        Parameters:
            widget: the "Select Result" QCheckBox (queried for its checked state).
            results (str | Iterable[str]): the result node name(s).
            object_mode (bool): for ops that can leave the result in a component
                selection (e.g. a UV cut's map components, polySoftEdge's edges)
                that a transform-only deselect can't clear — switches to object
                mode and collapses any such selection onto the objects (replace)
                before (de)selecting.
            defer (bool): also re-assert the choice on idle (``evalDeferred``,
                lowest priority). Only the commit path needs it — committing can
                leave Maya restoring the pre-op selection (or settling the toggle)
                *after* this returns; the idle re-read then wins. A live toggle is a
                direct user action with nothing to clobber it, so it passes False.
                The re-assert lands outside the commit's undo chunk, so it is
                recorded nowhere: recorded, it was the first Ctrl+Z after a
                commit, one that seemed to do nothing.
        """
        nodes = [results] if isinstance(results, str) else list(results or [])

        def _apply():
            alive = [n for n in nodes if n and cmds.objExists(n)]
            if not alive:
                return
            try:
                select = widget.isChecked()
            except Exception:
                select = True
            if object_mode:
                try:
                    cmds.selectMode(object=True)
                except Exception:
                    pass
            if select:
                cmds.select(alive, replace=True)
            else:
                # An op can leave the result in COMPONENT selection (e.g.
                # polySoftEdge leaves the mesh's edges selected, a UV cut its map
                # components); a transform-only deselect won't clear those, so under
                # object_mode collapse any such selection onto the objects (replace)
                # before deselecting. (selectMode alone doesn't collapse an existing
                # component selection.)
                if object_mode:
                    cmds.select(alive, replace=True)
                cmds.select(alive, deselect=True)

        def _reassert():
            with CoreUtils.undo_disabled():
                _apply()

        _apply()  # immediate
        if defer:
            try:
                cmds.evalDeferred(_reassert, lowestPriority=True)
            except Exception:
                pass
