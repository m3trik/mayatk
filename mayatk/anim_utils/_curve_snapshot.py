# !/usr/bin/python
# coding=utf-8
"""Curve snapshots behind :class:`mayatk.AnimUtils`.

:meth:`AnimUtils.snapshot_curves` stashes every curve driving a set of objects;
:meth:`AnimUtils.restore_curves` swaps the stash back in, UUIDs and all.
Reached through :class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Dict, List, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None


from mayatk.core_utils._core_utils import CoreUtils


class _CurveSnapshotInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @classmethod
    def _snapshot_curves(cls, objects, recursive):
        """Body of :meth:`AnimUtils.snapshot_curves`."""
        curves = [
            c
            for c in cls.objects_to_curves(objects, recursive=recursive)
            if cmds.objExists(c)
        ]
        records: List[Dict[str, Any]] = []
        if not curves:
            return {"records": records}
        # ONE duplicate for the whole set: the command's per-call overhead
        # dominated a production snapshot (2,000 curves, 19 s after a bake
        # left them dense); a batch is the same nodes for a fraction of it.
        # Each duplicate is renamed to the ``__snapshot`` convention so a
        # leaked stash still names itself.
        try:
            stashes = cmds.duplicate(
                curves, inputConnections=False, upstreamNodes=False
            )
        except RuntimeError as error:  # pragma: no cover - defensive
            cmds.warning(f"Could not snapshot the curves: {error}")
            return {"records": records}
        # Where each curve plugs in, so a curve DELETED by the caller (the
        # optimize pass drops static ones outright) can be put back rather than
        # merely restored in place -- and what drives it: a set-driven-key
        # curve is fed by another attribute, and a stash that lost its input
        # would restore as a curve driven by time. One query per side for the
        # whole set, not two per curve.
        wired: Dict[str, Dict[str, List[str]]] = {"output": {}, "input": {}}
        for attr, is_input in (("output", False), ("input", True)):
            pairs = (
                cmds.listConnections(
                    [f"{curve}.{attr}" for curve in curves],
                    plugs=True,
                    connections=True,
                    source=is_input,
                    destination=not is_input,
                )
                or []
            )
            for own, other in zip(pairs[::2], pairs[1::2]):
                wired[attr].setdefault(own.rsplit(".", 1)[0], []).append(other)
        for curve, stash in zip(curves, stashes):
            try:
                stash = cmds.rename(stash, f"{CoreUtils.short_name(curve)}__snapshot")
            except RuntimeError:  # pragma: no cover - a name is a nicety
                pass
            records.append(
                {
                    "curve": curve,
                    # So a curve the caller DELETES comes back as the same node
                    # to anything holding its UUID, not only under its name.
                    "uuid": (cmds.ls(curve, uuid=True) or [None])[0],
                    "stash": stash,
                    "targets": wired["output"].get(curve, []),
                    "drivers": wired["input"].get(curve, []),
                }
            )
        return {"records": records}

    @classmethod
    def _restore_curves(cls, snapshot):
        """Body of :meth:`AnimUtils.restore_curves`."""
        records = (snapshot or {}).get("records") or []
        restored = 0
        for record in records:
            curve, stash = record.get("curve"), record.get("stash")
            try:
                if not stash or not cmds.objExists(stash):
                    continue
                if cmds.objExists(curve) and cls._swap_in_stash(
                    curve, stash, record.get("uuid")
                ):
                    # The stash IS the curve now (same name, same plugs);
                    # nothing left to delete.
                    stash = None
                elif cmds.objExists(curve):
                    # Content-only replacement: `replaceCompletely` swaps the
                    # whole curve (keys AND tangents) while the node, its name
                    # and its connections stay put.
                    cmds.copyKey(stash)
                    cmds.pasteKey(curve, option="replaceCompletely")
                    weighted = cmds.keyTangent(stash, query=True, weightedTangents=True)
                    if weighted:
                        cmds.keyTangent(
                            curve, edit=True, weightedTangents=bool(weighted[0])
                        )
                    pre = cmds.setInfinity(stash, query=True, preInfinite=True)
                    post = cmds.setInfinity(stash, query=True, postInfinite=True)
                    if pre and post:
                        cmds.setInfinity(
                            curve, preInfinite=pre[0], postInfinite=post[0]
                        )
                    # The node kept may be a same-named REBUILD (a pass that
                    # cleared the channel and keyed it again) rather than the
                    # curve the snapshot took: hand it the recorded identity.
                    try:
                        cls._take_back_uuid(curve, record.get("uuid"))
                    except RuntimeError as error:
                        cmds.warning(
                            f"Restored {curve!r} but not under its UUID: {error}"
                        )
                else:
                    # The caller deleted it (optimize drops static curves), so
                    # the stash BECOMES the curve: wire it where the original
                    # sat and give it the original's name back.
                    for driver in record.get("drivers") or []:
                        cmds.connectAttr(driver, f"{stash}.input", force=True)
                    for target in record.get("targets") or []:
                        cmds.connectAttr(f"{stash}.output", target, force=True)
                    # Consumed the moment it is WIRED IN, before the rename:
                    # the rename is cosmetic and the connections are the
                    # restore, so a rename that raises must not send this
                    # through the cleanup below -- that would delete the curve
                    # just put back, and the original is already gone.
                    wired, stash = stash, None
                    try:
                        wired = cmds.rename(wired, CoreUtils.short_name(curve))
                        cls._take_back_uuid(wired, record.get("uuid"))
                    except RuntimeError as error:
                        cmds.warning(
                            f"Restored {curve!r} as {wired!r}; it could not take "
                            f"its name and UUID back: {error}"
                        )
                restored += 1
            except RuntimeError as error:
                cmds.warning(f"Could not restore the curve {curve!r}: {error}")
            finally:
                if stash and cmds.objExists(stash):
                    try:
                        cmds.delete(stash)
                    except RuntimeError:  # pragma: no cover - defensive
                        pass
        return restored

    @classmethod
    def _swap_in_stash(cls, curve: str, stash: str, uuid: Optional[str] = None) -> bool:
        """Put *stash* where *curve* is wired and delete *curve* -- the O(1)
        restore. The stash takes the curve's name, plugs AND the UUID the
        snapshot recorded (*uuid*; the live node's own only when another node
        still holds that one), so to anything holding the curve it is the same
        node: the scene exporter's flatten deletes its fitted curves by UUID
        after this runs, and under a new UUID they stayed wired to the rig --
        while a live node that is a same-named rebuild carries a UUID that was
        never the curve's (both 2026-09-14). ``False`` (nothing
        changed) when the live curve is not an ordinary node: referenced or
        locked (cannot be deleted), carrying any connection but its ``input``
        and ``output`` (a membership the swap would drop), or driving a locked
        plug (which refuses the stash's connection).

        Why not always paste in place: ``pasteKey replaceCompletely`` onto a
        live curve is per-key work on the LIVE curve's keys, and after a bake
        every one is dense -- measured 47 ms a curve, 94 s of a production
        export's restore, for two connections' worth of change.
        """
        try:
            if cmds.referenceQuery(curve, isNodeReferenced=True):
                return False
            if (cmds.lockNode(curve, query=True, lock=True) or [False])[0]:
                return False
            wired = cmds.listConnections(curve, plugs=True, connections=True) or []
            own = wired[::2]
            if any(plug.split(".", 1)[1] not in ("input", "output") for plug in own):
                return False
            targets = [
                dst for src, dst in zip(own, wired[1::2]) if src.endswith(".output")
            ]
            drivers = [
                src
                for own_plug, src in zip(own, wired[1::2])
                if own_plug.endswith(".input")
            ]
            # Every refusal is decided before the first connection: a locked
            # destination raised partway, and the plugs already moved were
            # left on a stash the caller deletes (2026-09-14).
            if any(cmds.getAttr(target, lock=True) for target in targets):
                return False
            moved: List[str] = []
            try:
                for target in targets:
                    cmds.connectAttr(f"{stash}.output", target, force=True)
                    moved.append(target)
                for driver in drivers:
                    cmds.connectAttr(driver, f"{stash}.input", force=True)
            except RuntimeError:
                for target in moved:  # back on the live curve for the paste
                    cmds.connectAttr(f"{curve}.output", target, force=True)
                raise
            name = CoreUtils.short_name(curve)
            live = cmds.ls(curve, uuid=True)[0]
            cmds.delete(curve)
        except RuntimeError as error:
            cmds.warning(f"Could not swap the curve {curve!r} back: {error}")
            return False
        # Committed: the stash drives the plugs now, so a failed rename costs
        # the curve its name or UUID, never the restore.
        try:
            cls._take_back_uuid(cmds.rename(stash, name), uuid, fallback=live)
        except RuntimeError as error:
            cmds.warning(f"Restored {curve!r} but not under its name/UUID: {error}")
        return True

    @staticmethod
    def _take_back_uuid(
        node: str, recorded: Optional[str], fallback: Optional[str] = None
    ) -> None:
        """Give *node* the UUID a snapshot *recorded* for its curve.

        The recorded UUID is the curve's identity to everything that held it,
        so it wins -- unless a node still carries it, when *fallback* (or
        nothing) is applied instead. The node found under a curve's name is
        not necessarily that curve: a pass that cleared the channel and keyed
        it again leaves a rebuild with a UUID of its own, and a production
        restore put the curve back under that one (2026-09-14).
        """
        current = (cmds.ls(node, uuid=True) or [None])[0]
        target = recorded if recorded and not cmds.ls(recorded) else fallback
        if target and target != current:
            cmds.rename(node, target, uuid=True)
