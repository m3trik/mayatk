# !/usr/bin/python
# coding=utf-8
"""Hermetic preview with replay-on-commit (H1 design).

The preview phase runs ``perform_operation`` with Maya's undo recording
suppressed. A :class:`CleanupContract` records what was created (and
optionally which attrs were mutated, which files were written) so rollback
can reverse it without touching Maya's undo stack. On commit, the work is
replayed inside an ``openChunk``/``closeChunk`` pair so the user gets a
single Ctrl+Z-able undo entry.

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
    place and may run on historyless (frozen/imported) meshes -- where a
    poly op bakes its result when its auto-created orig-shape is deleted --
    set ``PRESERVE_GEOMETRY = True`` on the operation instance. The contract
    then snapshots the operated objects and, on rollback, restores any that
    survived but diverged from the snapshot (identity/UUID preserved).
  - Do not delete pre-existing nodes inside ``perform_operation`` (diff is
    one-way; deletions cannot be reversed).
  - Mutating an attribute on a pre-existing node requires
    ``contract.record_modification(node, attr)`` before the ``setAttr``.
  - Disk writes that should be cleaned on rollback require
    ``contract.add_file(path)``.
  - ``contract`` is ``None`` during the commit replay; guard with
    ``if contract: contract.add_file(...)``.

Verification: see ``test/temp_tests/verify_preview_*.py`` for the 60+
empirical tests this design passes.
"""
import logging
import weakref
from pathlib import Path
from typing import Any, Callable, List, Optional, Set, Tuple
from functools import wraps

import maya.cmds as cmds
import maya.api.OpenMaya as om

# From this package:
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


def _format_op_error(err: Exception) -> str:
    """Build a clean, readable message-box string from an exception.

    The full traceback is logged to the console separately; this is only the
    popup text. :class:`OperationError` contributes its curated message and
    bullet causes; any other exception is reduced to its first non-empty line
    so multi-line driver errors don't leak into the popup. Falls back to plain
    text if the ``uitk`` rich-text helper is unavailable.
    """
    if isinstance(err, OperationError):
        # Author-controlled text -- inline HTML (e.g. <b>) is intentional.
        title, body, bullets = err.title, err.user_message, (err.causes or None)
    else:
        from html import escape

        title = "Operation failed"
        lines = [ln.strip() for ln in str(err).splitlines() if ln.strip()]
        # Untrusted text -> escape so a stray '<' (e.g. "'<' not supported")
        # renders literally instead of being swallowed as an HTML tag.
        body = escape(lines[0]) if lines else type(err).__name__
        bullets = None

    try:
        from uitk.widgets.mixins.tooltip_mixin import fmt

        return fmt(title=title, body=body, bullets=bullets)
    except Exception:  # uitk unavailable -- degrade to plain text
        tail = (" " + " ".join(bullets)) if bullets else ""
        return f"{title}: {body}{tail}"


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
                    rel = d[len(orig_long):]  # "" for root
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

    def __exit__(self, *exc):
        try:
            dag_after = frozenset(cmds.ls(long=True, allPaths=True) or [])
            dg_after = frozenset(cmds.ls() or [])
            self.created = (dag_after - self._before[0]) | (
                dg_after - self._before[1]
            )
        finally:
            cmds.undoInfo(stateWithoutFlush=self._prev_undo_state)
        return False  # don't suppress exceptions

    def add_file(self, path) -> None:
        self.files.append(Path(path))

    def record_modification(self, node: str, attr: str) -> None:
        if cmds.objExists(node):
            try:
                value = cmds.getAttr(f"{node}.{attr}")
                self.attr_snapshots.append((node, attr, value))
            except Exception:
                pass

    # ----------------------------------------------- in-place mesh restore
    @classmethod
    def _mesh_signature(cls, transform: str):
        """Cheap topology + world-extent signature used to decide whether a
        surviving original still matches its pristine duplicate.

        Returns ``None`` for non-mesh (or shape-less) transforms, which the
        divergence check treats as "not diverged" -- so the in-place restore
        only ever runs on genuine meshes.
        """
        shape = NodeUtils.get_shape(transform, no_intermediate=True)
        if not shape:
            return None
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
        try:
            bbox = tuple(round(v, 5) for v in cmds.exactWorldBoundingBox(transform))
        except Exception:
            bbox = None
        return sig + (bbox,)

    @classmethod
    def _mesh_diverged(cls, transform: str, dup: str) -> bool:
        a = cls._mesh_signature(transform)
        b = cls._mesh_signature(dup)
        if a is None or b is None:
            return False
        return a != b

    @classmethod
    def _restore_mesh_in_place(cls, transform: str, dup: str) -> None:
        """Overwrite *transform*'s mesh with the pristine *dup*'s mesh while
        keeping the original shape node (and thus its identity/UUID) intact.

        Feeds the duplicate's ``outMesh`` into the original ``inMesh`` then
        bakes it via ``delete(ch=True)`` -- which evaluates the pristine mesh
        into the shape and severs the temporary connection.
        """
        orig_shape = NodeUtils.get_shape(transform, no_intermediate=True)
        dup_shape = NodeUtils.get_shape(dup, no_intermediate=True)
        if not orig_shape or not dup_shape:
            return
        cmds.connectAttr(f"{dup_shape}.outMesh", f"{orig_shape}.inMesh", force=True)
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
                    if (
                        isinstance(value, (list, tuple))
                        and len(value) == 1
                        and isinstance(value[0], (list, tuple))
                    ):
                        cmds.setAttr(f"{node}.{attr}", *value[0], type="double3")
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
                        # Original survived. Node-diff deletion may still have
                        # BAKED an in-place mesh mutation: ops like polyCut run
                        # on a HISTORYLESS mesh create an intermediate orig
                        # shape that holds the only pristine copy, and deleting
                        # it (counted as a "created" node) leaves the mutated
                        # result baked into the visible shape. Detect that by
                        # comparing the live mesh to the pristine duplicate; if
                        # they diverged, restore the mesh in place -- preserving
                        # the original shape's identity/UUID -- rather than
                        # leaving the bake. When they match (node-diff already
                        # reverted, e.g. a mesh WITH upstream history), skip the
                        # restore so legitimate construction history is kept.
                        try:
                            orig_path = (
                                cmds.ls(p["orig_uuid"], long=True) or [None]
                            )[0]
                            if orig_path and self._mesh_diverged(orig_path, p["dup"]):
                                self._restore_mesh_in_place(orig_path, p["dup"])
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


def _safe(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            self.logger.exception(f"Error in {func.__name__}: {e}")
            if self.message_func:
                self.message_func(_format_op_error(e))
            # Reentry guard: if disable() itself raises, _safe(disable) would
            # otherwise call self.disable() again -> infinite recursion.
            if not getattr(self, "_in_recovery", False):
                self._in_recovery = True
                try:
                    self.disable()
                except Exception as inner:
                    self.logger.exception(
                        f"disable() during recovery raised: {inner}"
                    )
                finally:
                    self._in_recovery = False
            raise

    return wrapper


class Preview:
    """Hermetic preview orchestrator (H1).

    The constructor signature matches the legacy :class:`preview_old.Preview`
    for drop-in instantiation, but ``perform_operation`` on the operation
    instance must now accept ``(objects, contract)``.
    """

    _instances: Set["Preview"] = set()

    @classmethod
    def cleanup_all_instances(cls) -> None:
        for inst in list(cls._instances):
            try:
                inst.cleanup()
            except Exception:
                pass
        cls._instances.clear()

    def __init__(
        self,
        operation_instance,
        preview_checkbox,
        create_button,
        finalize_func: Optional[Callable] = None,
        message_func: Optional[Callable] = print,
        enable_on_show: bool = False,
        disable_on_hide: bool = True,
        validation_func: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None,
    ):
        if not hasattr(operation_instance, "perform_operation"):
            raise ValueError(
                "operation_instance must implement perform_operation(objects, contract)"
            )
        if preview_checkbox is None or create_button is None:
            raise ValueError("preview_checkbox and create_button cannot be None")

        self.logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        self.operation_instance = operation_instance
        self.preview_checkbox = preview_checkbox
        # Previews must start disabled on UI load; uitk state-restore skips
        # widgets tagged this way.
        self.preview_checkbox.exclude_from_reset = True
        self.preview_checkbox.restore_state = False
        self.create_button = create_button
        self.finalize_func = finalize_func
        self.message_func = message_func or self.logger.info
        self.validation_func = validation_func
        self.progress_callback = progress_callback

        self.operated_objects: Set[str] = set()
        self.operation_instance.operated_objects = self.operated_objects
        self._contract: Optional[CleanupContract] = None
        self._captured_objects: List[str] = []
        self.is_enabled: bool = False
        # Global re-entry guard: if perform_operation emits a signal that
        # fires refresh() (e.g. cmds.select -> SelectionChanged ->
        # PivotWatcher -> refresh), the inner call would corrupt the
        # contract state we're mid-recording. Single Python flag is
        # sufficient because Maya is single-threaded.
        self._refresh_in_progress: bool = False

        self.window = self._find_window()
        self._setup_ui_connections()
        self.init_show_hide_behavior(enable_on_show, disable_on_hide)

        Preview._instances.add(self)

    # ------------------------------------------------------------------ setup
    def _find_window(self):
        try:
            if hasattr(self.create_button, "window") and self.create_button.window():
                return weakref.ref(self.create_button.window())
            if (
                hasattr(self.preview_checkbox, "window")
                and self.preview_checkbox.window()
            ):
                return weakref.ref(self.preview_checkbox.window())
        except Exception as e:
            self.logger.warning(f"Could not weakref window: {e}")
        return None

    def _setup_ui_connections(self) -> None:
        try:
            self.preview_checkbox.toggled.connect(self.toggle)
            self.create_button.clicked.connect(self.finalize_changes)
        except Exception as e:
            self.logger.error(f"Failed to setup UI connections: {e}")
            raise

    def init_show_hide_behavior(
        self, enable_on_show: bool, disable_on_hide: bool
    ) -> None:
        self.enable_on_show = enable_on_show
        self.disable_on_hide = disable_on_hide
        window = self.window() if self.window else None
        if window:
            try:
                if hasattr(window, "on_show"):
                    window.on_show.connect(self.conditionally_enable)
                if hasattr(window, "on_hide"):
                    window.on_hide.connect(self.conditionally_disable)
            except Exception as e:
                self.logger.warning(f"Show/hide signal setup failed: {e}")

    def conditionally_enable(self) -> None:
        if self.enable_on_show:
            self.enable()

    def conditionally_disable(self) -> None:
        if self.disable_on_hide:
            self.disable()

    # -------------------------------------------------------- public lifecycle
    def toggle(self, state: bool) -> None:
        if state:
            self.enable()
        else:
            self.disable()

    def validate_operation(self, objects: List[Any]) -> bool:
        if self.validation_func:
            try:
                return self.validation_func(objects)
            except Exception as e:
                self.logger.warning(f"Validation function failed: {e}")
                return False
        return True

    @_safe
    def enable(self) -> None:
        # Idempotent guard: if a previous enable already built a contract,
        # calling again would overwrite self._contract and orphan the old
        # one's created nodes. Trigger paths include redundant on_show
        # firings (enable_on_show=True) or programmatic double-calls.
        if self.is_enabled and self._contract is not None:
            return

        sel = cmds.ls(selection=True) or []
        if not sel:
            self.message_func("No objects selected.")
            self._set_checkbox(False)
            return

        if not self.validate_operation(sel):
            self.message_func("Operation validation failed.")
            self._set_checkbox(False)
            return

        self._captured_objects = list(sel)
        self.operated_objects.clear()
        self.operated_objects.update(str(s) for s in sel)

        self._set_checkbox(True)
        self.create_button.setEnabled(True)
        self.is_enabled = True

        # Guard around the preview phase. enable() is user-initiated so
        # _refresh_in_progress should be False; if a signal fired inside
        # _run_preview_phase re-enters refresh, the inner call short-circuits.
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            self._run_preview_phase()
        finally:
            self._refresh_in_progress = False

    def _run_preview_phase(self) -> None:
        """Build a fresh contract and run perform_operation under it.

        If the operation_instance declares ``MUTATES_SELECTION = True`` or
        ``PRESERVE_GEOMETRY = True``, the contract preserves (duplicates+hides)
        the captured selection's owning transform(s) so rollback can restore
        originals that perform_operation deletes or bakes in place. The
        selection is resolved to transforms because component selections (e.g.
        Bridge's edges) can't be duplicated. Default is opt-in to avoid paying
        duplication cost for ops that don't need it (e.g. ShadowRig on a 10k-node
        hierarchy).

        Caller must hold ``_refresh_in_progress`` for the duration --
        ``enable`` and ``refresh`` are the only callers. The flag covers
        ``refresh``'s pre-phase rollback as well, so any *synchronous*
        signal cascade fired during rollback (e.g. Qt connections that
        valueChange a slider already wired to refresh) finds the flag
        held and short-circuits. Maya scriptJob events (SelectionChanged,
        etc.) fire on idle and are handled separately by PivotWatcher's
        signature dedup -- those don't reach the flag.
        """
        preserve = None
        if getattr(self.operation_instance, "MUTATES_SELECTION", False) or getattr(
            self.operation_instance, "PRESERVE_GEOMETRY", False
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
        try:
            with self._contract:
                self.operation_instance.perform_operation(
                    self._captured_objects, self._contract
                )
                # Isolation-set membership is a `cmds.sets` connection,
                # which Maya records on the undo queue. It MUST run inside
                # the contract (under suppressed undo) -- otherwise every
                # refresh leaks one entry into the user's queue and the
                # first few Ctrl+Z presses after commit pop those instead
                # of the operation. Membership is a connection (not a node
                # creation), so rollback's node-diff doesn't track or
                # reverse it -- which is what we want: the user-initiated
                # isolation persists across preview cycles.
                #
                # For MUTATES_SELECTION ops (Mirror), the captured names may
                # have been deleted by perform_operation. Combine the
                # captured names with the post-op selection so we add
                # whichever still exist -- add_to_isolation_set filters by
                # objExists internally, so missing names are no-ops.
                iso_targets = list(self._captured_objects)
                if getattr(self.operation_instance, "MUTATES_SELECTION", False):
                    try:
                        iso_targets.extend(cmds.ls(selection=True) or [])
                    except Exception:
                        pass
                try:
                    DisplayUtils.add_to_isolation_set(iso_targets)
                except Exception:
                    pass
        except Exception as e:
            self.logger.exception(f"perform_operation raised: {e}")
            self.message_func(_format_op_error(e))

    @_safe
    def refresh(self, *args) -> None:
        """Roll back the previous preview and re-run perform_operation.

        Both rollback and the new preview phase run inside one
        ``_refresh_in_progress`` critical section. The Maya scriptJob path
        (cmds.delete -> SelectionChanged -> PivotWatcher) is deferred to
        idle and absorbed by PivotWatcher's signature dedup, not by this
        flag. The flag handles the *synchronous* case: any Qt cross-wire
        where rollback's scene mutations cause a connected widget to emit
        a signal already bound to refresh, which would otherwise build a
        fresh contract that the outer call overwrites and abandons (orphan
        nodes + double perform_operation).
        """
        if not self.is_enabled or self._contract is None:
            return
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            self._contract.rollback()
            self._run_preview_phase()
        finally:
            self._refresh_in_progress = False

    @_safe
    def disable(self) -> None:
        """Roll back the preview without committing.

        Guarded the same way as refresh so signal-fired re-entry during
        rollback can't trigger a phantom refresh on an already-disabling
        preview.
        """
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            if self._contract is not None:
                self._contract.rollback()
                self._contract = None
            self.operated_objects.clear()
            self._set_checkbox(False)
            self.create_button.setEnabled(False)
            self.is_enabled = False
        finally:
            self._refresh_in_progress = False

    @_safe
    def finalize_changes(self) -> None:
        """Commit: rollback the hermetic version, then replay under undo.

        The flag is held across rollback AND the replay chunk so a signal
        fired by rollback (or the replay itself) can't re-enter refresh
        and corrupt the chunk we're building.
        """
        if not self.is_enabled or self._contract is None:
            return
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            self._contract.rollback()
            self._contract = None

            chunk_name = type(self.operation_instance).__name__ or "PreviewCommit"
            cmds.undoInfo(openChunk=True, chunkName=chunk_name)
            try:
                # Pass None as contract; replay shouldn't record (no rollback path).
                self.operation_instance.perform_operation(
                    self._captured_objects, None
                )
            finally:
                cmds.undoInfo(closeChunk=True)

            self.operated_objects.clear()
            self._set_checkbox(False)
            self.create_button.setEnabled(False)
            self.is_enabled = False
        finally:
            self._refresh_in_progress = False

        if self.finalize_func:
            try:
                self.finalize_func()
            except Exception as e:
                self.logger.exception(f"finalize_func raised: {e}")

    # ------------------------------------------------------------- internals
    def _set_checkbox(self, checked: bool) -> None:
        """Set checkbox state without triggering toggle()."""
        self.preview_checkbox.blockSignals(True)
        try:
            self.preview_checkbox.setChecked(checked)
        finally:
            self.preview_checkbox.blockSignals(False)

    def cleanup(self) -> None:
        try:
            Preview._instances.discard(self)
            if self.is_enabled:
                self.disable()
        except Exception as e:
            self.logger.error(f"Cleanup error: {e}")

    def __del__(self):
        self.cleanup()

    # ----------------------------------------------------------- read-only API
    @property
    def enabled(self) -> bool:
        return self.is_enabled

    @property
    def operated_object_count(self) -> int:
        return len(self.operated_objects)

    def get_operated_objects(self) -> List[str]:
        return list(self.operated_objects)


def cleanup_all_previews() -> None:
    Preview.cleanup_all_instances()
