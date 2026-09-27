# !/usr/bin/python
# coding=utf-8
"""Transform freezing behind :class:`mayatk.XformUtils`.

:meth:`XformUtils.freeze_transforms` and its variants: instanced groups frozen
as one, freezing into ``offsetParentMatrix`` (and back), and pushing a child's
local matrix up into its parent. Every freeze records its per-channel deltas in
the bake history ``_stored_transforms`` owns. Reached through
:class:`mayatk.XformUtils`; nothing here is called directly.
"""

from __future__ import annotations

import math
from typing import List, Tuple, Dict, Set

try:
    import maya.cmds as cmds
    from maya.api import OpenMaya as om
except Exception:
    cmds = om = None


from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.undo_recorder import UndoRecorder
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


class _FreezeInternal:
    """Private helpers and ``XformUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _apply_freeze_deltas(obj, axes_to_freeze, normal=0):
        """Apply freeze transformations using Maya's native makeIdentity.

        Maya's makeIdentity automatically preserves world-space pivot positions
        by adjusting rotatePivotTranslate/scalePivotTranslate as needed.

        Parameters:
            obj: The transform node to freeze.
            axes_to_freeze (set): Set of axes to freeze (e.g., {'tx', 'ty', 'tz', 'rx', ...}).
            normal (int/bool): ``makeIdentity -normal`` — 0 leave normals
                alone, 1 freeze them, 2 freeze only when the transform
                mirrors.  Matters for negatively-scaled geometry, whose
                normals invert when the scale is baked out.

        Returns:
            bool: True if successful, False if skipped due to error.
        """
        freeze_t = not axes_to_freeze.isdisjoint({"tx", "ty", "tz"})
        freeze_r = not axes_to_freeze.isdisjoint({"rx", "ry", "rz"})
        freeze_s = not axes_to_freeze.isdisjoint({"sx", "sy", "sz"})
        normal = int(normal)

        # Note: We let RuntimeError bubble up so freeze_transforms can handle
        # connection/locking strategies.
        try:
            cmds.makeIdentity(
                obj,
                apply=True,
                t=freeze_t,
                r=freeze_r,
                s=freeze_s,
                pn=True,
                normal=normal,
            )
        except RuntimeError:
            cmds.makeIdentity(
                obj,
                apply=True,
                t=freeze_t,
                r=freeze_r,
                s=freeze_s,
                pn=False,
                normal=normal,
            )
        return True

    #: Long names of the transform channels ``Attributes.get_lock_state``
    #: reads. ``listAttr -locked`` reports long names (measured).
    _LOCKABLE_TRS = frozenset(
        (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
            "scaleX",
            "scaleY",
            "scaleZ",
        )
    )

    @classmethod
    def _prune_to_locked(cls, nodes: List[str]) -> List[str]:
        """Keep only the nodes ``temporarily_unlock`` has actual work for.

        It reads and restores nine plugs on every node it is handed, which was
        48% of a 2000-node freeze -- and on a normal scene almost nothing is
        locked. One ``listAttr -locked`` per node answers the same question and
        agrees exactly with the per-plug ``getAttr(lock=True)`` sweep, including
        compound-locked and connected plugs (measured); it stays the cheaper
        call even on an attribute-heavy control.

        A LOCATOR is always kept: ``Attributes._resolve_lock_target`` redirects
        it to its first child transform, so the locator's own channels say
        nothing about what the unlock would touch.
        """
        if len(nodes) < 2:
            return nodes
        locator_shapes = (
            cmds.listRelatives(nodes, shapes=True, type="locator", fullPath=True) or []
        )
        # a full shape path's parent is its own prefix -- no extra query
        locators = {s.rsplit("|", 1)[0] for s in locator_shapes}
        kept = []
        for node in nodes:
            if node in locators:
                kept.append(node)
                continue
            locked = cmds.listAttr(node, locked=True)
            if locked and not cls._LOCKABLE_TRS.isdisjoint(locked):
                kept.append(node)
        return kept

    #: Channels a freeze rewrites — copied wholesale from the stand-in so the
    #: master ends up byte-identical to a real ``makeIdentity`` (pivots and
    #: rotateAxis included, not just TRS).
    _FREEZE_CHANNEL_ATTRS = (
        "translate",
        "rotate",
        "scale",
        "shear",
        "rotatePivot",
        "rotatePivotTranslate",
        "scalePivot",
        "scalePivotTranslate",
        "rotateAxis",
    )

    @staticmethod
    def _authoring_shapes(transform: str) -> List[str]:
        """Shapes under *transform* whose points a bake should be written to.

        Construction history (``polyCube1`` → ``inMesh``) is fine: point
        writes land on the shape's tweak and survive re-evaluation. A
        **deformer** is not — a shape downstream of a ``geometryFilter`` is
        evaluated output, so the deformer's orig (intermediate) shape is the
        one carrying the authored points and the visible shape is skipped
        to avoid baking the same delta twice.

        Empty shapes (no vertices) are skipped: there is nothing to
        transform and ``MFnMesh`` rejects them outright.
        """
        meshes = [
            s
            for s in cmds.listRelatives(transform, shapes=True, fullPath=True) or []
            if cmds.nodeType(s) == "mesh"
        ]

        def _has_vertices(shape):
            try:
                return bool(cmds.polyEvaluate(shape, vertex=True))
            except Exception:
                return False

        def _deformed(shape):
            return any(
                "geometryfilter" in NodeUtils.get_inherited_types(n)
                for n in cmds.listHistory(shape, pruneDagObjects=True) or []
            )

        visible = [s for s in meshes if not cmds.getAttr(f"{s}.intermediateObject")]
        # Only when a visible shape is deformer output does its orig shape
        # carry the authored points. Otherwise every intermediate present is
        # dead data (an orphaned history remnant — common in imported/scanned
        # assets) and baking it would be pointless work on a shape whose
        # member set may not even match the visible one.
        take_intermediates = any(_deformed(s) for s in visible)

        out = []
        for s in meshes:
            is_inter = bool(cmds.getAttr(f"{s}.intermediateObject"))
            if is_inter and not take_intermediates:
                continue
            if not is_inter and _deformed(s):
                continue  # evaluated output; its orig shape is baked instead
            if not _has_vertices(s):
                continue
            out.append(s)
        return out

    @classmethod
    def _instance_group_members(cls, transform: str):
        """``(members, shapes)`` for the instance group *transform* belongs
        to, resolved from the shapes a bake would actually write to.

        Single source of truth for "who is in this group": deriving it from
        :meth:`_authoring_shapes` rather than from every shared shape keeps
        the set that gets compensated identical to the set the caller
        considers handled — an orphaned intermediate can be shared with
        transforms that the visible shape is not, and treating those as
        group members silently drops them from the operation.

        Returns ``(None, None)`` when the shapes disagree about membership.
        """
        shapes = cls._authoring_shapes(transform)
        if not shapes:
            return None, None
        member_sets = {
            tuple(sorted(cmds.listRelatives(s, allParents=True, fullPath=True) or []))
            for s in shapes
        }
        if len(member_sets) != 1:
            return None, None
        return list(next(iter(member_sets))), shapes

    @staticmethod
    def _is_multi_path(transform: str) -> bool:
        """True when *transform* itself is instanced (several DAG paths)."""
        try:
            sel = om.MSelectionList()
            sel.add(transform)
            return sel.getDagPath(0).isInstanced()
        except Exception:
            return False

    @staticmethod
    def _transform_is_driven(
        transform: str, channels=("translate", "rotate", "scale", "shear")
    ) -> bool:
        """True when anything feeds *transform*'s *channels*.

        Compact yes/no twin of ``transform_diag._driving_connections``,
        which returns per-driver tags for its diagnosis dict; it can't be
        reused here because that module imports this one.

        *channels* defaults to TRS + shear (what a freeze has to bake).
        Narrow it to what a given caller actually writes — reporting a
        driven shear to a caller that only touches T/R/S is a false
        positive that costs a node its restore.
        """
        plugs = []
        for ch in channels:
            if cmds.attributeQuery(ch, node=transform, exists=True):
                plugs.append(f"{transform}.{ch}")
                plugs.extend(
                    f"{transform}.{c}"
                    for c in cmds.attributeQuery(ch, node=transform, listChildren=True)
                    or []
                )
        return bool(
            plugs and cmds.listConnections(plugs, source=True, destination=False)
        )

    @staticmethod
    def _set_matrix_plug(plug: str, mmatrix) -> None:
        """Write an ``om.MMatrix`` (or 16-flat iterable) to a matrix attribute plug."""
        if hasattr(mmatrix, "getElement"):
            flat = [mmatrix.getElement(r, c) for r in range(4) for c in range(4)]
        else:
            flat = list(mmatrix)
        cmds.setAttr(plug, *flat, type="matrix")

    @classmethod
    def _freeze_instanced_group(cls, master, translate, rotate, scale, quiet):
        """Body of :meth:`XformUtils.freeze_instanced_group`."""
        master = (cmds.ls(master, long=True) or [master])[0]
        members, shapes = cls._instance_group_members(master)
        if members is None:
            if not quiet:
                cmds.warning(
                    f"freeze_instanced_group: '{master}' shares shapes with "
                    "differing member sets — skipped."
                )
            return False
        siblings = [m for m in members if m != master]

        # Checked across EVERY member, not just the siblings: a driven
        # sibling would have its compensation overwritten on the next
        # evaluation (displaced against baked geometry), and a driven master
        # rejects the frozen channels outright ("child attribute … is locked
        # or connected"). Which member the caller happened to pass must not
        # change the answer. A member on several DAG paths cannot carry a
        # per-path compensation at all.
        for m in members:
            if cls._transform_is_driven(m):
                if not quiet:
                    cmds.warning(
                        f"freeze_instanced_group: '{CoreUtils.short_name(m)}' has "
                        "driven transform channels — group skipped."
                    )
                return False
            if m != master and cls._is_multi_path(m):
                if not quiet:
                    cmds.warning(
                        f"freeze_instanced_group: '{CoreUtils.short_name(m)}' is "
                        "itself instanced (multiple DAG paths) — group skipped."
                    )
                return False

        for node in members:
            try:
                if cmds.referenceQuery(node, isNodeReferenced=True):
                    if not quiet:
                        cmds.warning(
                            f"freeze_instanced_group: '{node}' is referenced — skipped."
                        )
                    return False
            except RuntimeError:
                pass

        pre_local = om.MMatrix(cmds.xform(master, q=True, os=True, matrix=True))
        sib_local = {
            m: om.MMatrix(cmds.xform(m, q=True, os=True, matrix=True)) for m in siblings
        }
        sib_pivots = {
            m: (
                cmds.xform(m, q=True, ws=True, rotatePivot=True),
                cmds.xform(m, q=True, ws=True, scalePivot=True),
            )
            for m in siblings
        }

        standin = cmds.duplicate(master, parentOnly=True)[0]
        try:
            with Attributes.temporarily_unlock([standin]):
                cmds.makeIdentity(
                    standin,
                    apply=True,
                    t=translate,
                    r=rotate,
                    s=scale,
                    n=False,
                    pn=True,
                )
            post_local = om.MMatrix(cmds.xform(standin, q=True, os=True, matrix=True))
            B = pre_local * post_local.inverse()
            if B.isEquivalent(om.MMatrix(), 1e-12):
                return False

            # A mirroring bake (negative determinant) reverses the handedness
            # of the point set, so the existing face winding now faces inward
            # — the whole group renders inside-out.  ``makeIdentity`` fixes
            # this for itself on the normal path; baking the points by hand
            # here does not, so the winding has to be reversed to match.
            mirrored = B.det3x3() < 0

            for shape in shapes:
                sel = om.MSelectionList()
                sel.add(shape)
                fn = om.MFnMesh(sel.getDagPath(0))
                # Recorded, and closed before the normal flip below edits the
                # same shape through cmds, so the queue keeps the two in order.
                with UndoRecorder.record() as recorder, recorder.points(fn):
                    fn.setPoints(
                        om.MPointArray(
                            [p * B for p in fn.getPoints(om.MSpace.kObject)]
                        ),
                        om.MSpace.kObject,
                    )
                if mirrored:
                    # Edit the SHARED shape once — every member sees it, which
                    # is exactly what the whole in-place design relies on.
                    cmds.polyNormal(
                        shape, normalMode=0, userNormalMode=0, constructionHistory=False
                    )

            with Attributes.temporarily_unlock([master]):
                for attr in cls._FREEZE_CHANNEL_ATTRS:
                    cmds.setAttr(
                        f"{master}.{attr}",
                        *cmds.getAttr(f"{standin}.{attr}")[0],
                        type="double3",
                    )
        finally:
            if cmds.objExists(standin):
                cmds.delete(standin)

        b_inv = B.inverse()
        for m in siblings:
            comp = b_inv * sib_local[m]
            with Attributes.temporarily_unlock([m]):
                cmds.xform(
                    m,
                    os=True,
                    matrix=[comp.getElement(r, c) for r in range(4) for c in range(4)],
                )
                rp, sp = sib_pivots[m]
                cmds.xform(m, ws=True, rotatePivot=rp)
                cmds.xform(m, ws=True, scalePivot=sp)
        return True

    @classmethod
    def _freeze_transforms(
        cls,
        objects,
        center_pivot,
        force,
        delete_history,
        freeze_children,
        unlock_children,
        connection_strategy,
        instance_strategy,
        from_channel_box,
        store,
        **kwargs,
    ):
        """Body of :meth:`XformUtils.freeze_transforms`."""
        if center_pivot is True:
            center_pivot = 2
        elif center_pivot is False:
            center_pivot = 0

        axes_to_freeze = set()

        channel_map = {
            "translate": ["tx", "ty", "tz"],
            "t": ["tx", "ty", "tz"],
            "translateX": ["tx"],
            "tx": ["tx"],
            "translateY": ["ty"],
            "ty": ["ty"],
            "translateZ": ["tz"],
            "tz": ["tz"],
            "rotate": ["rx", "ry", "rz"],
            "r": ["rx", "ry", "rz"],
            "rotateX": ["rx"],
            "rx": ["rx"],
            "rotateY": ["ry"],
            "ry": ["ry"],
            "rotateZ": ["rz"],
            "rz": ["rz"],
            "scale": ["sx", "sy", "sz"],
            "s": ["sx", "sy", "sz"],
            "scaleX": ["sx"],
            "sx": ["sx"],
            "scaleY": ["sy"],
            "sy": ["sy"],
            "scaleZ": ["sz"],
            "sz": ["sz"],
        }

        if from_channel_box:
            selected_channels = set(Attributes.get_selected_channels() or [])
            for ch in selected_channels:
                if "." in ch:
                    ch = ch.split(".")[-1]
                if ch in channel_map:
                    axes_to_freeze.update(channel_map[ch])
        else:
            # Detect whether the caller specified any per-channel flag.
            channel_keys = {
                "translate",
                "t",
                "rotate",
                "r",
                "scale",
                "s",
                "translateX",
                "tx",
                "translateY",
                "ty",
                "translateZ",
                "tz",
                "rotateX",
                "rx",
                "rotateY",
                "ry",
                "rotateZ",
                "rz",
                "scaleX",
                "sx",
                "scaleY",
                "sy",
                "scaleZ",
                "sz",
            }
            # ``normal`` is deliberately NOT in this set. It is a makeIdentity
            # MODIFIER (freeze vertex normals), not a channel selector —
            # counting it here made ``freeze_transforms(obj, normal=True)``
            # suppress the freeze-everything default while contributing no
            # axes of its own, so the call silently froze nothing at all.
            any_channel_flag = any(k in kwargs for k in channel_keys)

            if not any_channel_flag:
                # No explicit channels → freeze all (matches Maya's default
                # ``makeIdentity -apply true`` behaviour).
                axes_to_freeze.update(
                    ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]
                )
            else:
                if kwargs.get("translate") or kwargs.get("t"):
                    axes_to_freeze.update(["tx", "ty", "tz"])
                if kwargs.get("rotate") or kwargs.get("r"):
                    axes_to_freeze.update(["rx", "ry", "rz"])
                if kwargs.get("scale") or kwargs.get("s"):
                    axes_to_freeze.update(["sx", "sy", "sz"])
                # Per-axis flags (rare).
                for ch in (
                    "tx",
                    "ty",
                    "tz",
                    "rx",
                    "ry",
                    "rz",
                    "sx",
                    "sy",
                    "sz",
                ):
                    if kwargs.get(ch):
                        axes_to_freeze.add(ch)

        if not axes_to_freeze:
            return

        objects = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )

        strategy = (connection_strategy or "preserve").lower()
        valid_strategies = {"preserve", "disconnect", "delete"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"Invalid connection_strategy '{connection_strategy}'. "
                f"Valid options: {sorted(valid_strategies)}"
            )

        inst_strategy = (instance_strategy or "skip").lower()
        valid_inst_strategies = {"skip", "preserve", "uninstance"}
        if inst_strategy not in valid_inst_strategies:
            raise ValueError(
                f"Invalid instance_strategy '{instance_strategy}'. "
                f"Valid options: {sorted(valid_inst_strategies)}"
            )

        # ``makeIdentity -normal``: 0 leave alone, 1 freeze, 2 freeze only on
        # a mirroring transform. A modifier, not a channel — see the note on
        # ``channel_keys`` above.
        freeze_normals = int(kwargs.get("normal") or 0)

        freeze_channels: Set[str] = set()
        if not axes_to_freeze.isdisjoint({"tx", "ty", "tz"}):
            freeze_channels.add("translate")
        if not axes_to_freeze.isdisjoint({"rx", "ry", "rz"}):
            freeze_channels.add("rotate")
        if not axes_to_freeze.isdisjoint({"sx", "sy", "sz"}):
            freeze_channels.add("scale")

        # Snapshot the pre-freeze locals of every transform this call could
        # touch — the whole subtree, because ``makeIdentity`` on a group zeroes
        # every descendant's channels.  Read here, before the instance
        # strategies fork off; COMMITTED as bake history after the freeze, and
        # only for transforms that actually froze.  Stamping up front would
        # give every skipped object (instanced, connection-blocked) a bake
        # matching its untouched channels, so a later unfreeze would add a
        # transform that was never baked out and double it.
        pre_freeze: Dict[str, tuple] = {}
        if store:
            for obj in objects:
                for node in [obj] + (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                ):
                    if node not in pre_freeze:
                        pre_freeze[node] = cls._decompose_local(node)

        # Long paths of what actually froze. ``flattened`` freezes ran through
        # makeIdentity, which flattens the subtree, so their descendants are
        # stamped too; ``baked_in_place`` came from freeze_instanced_group,
        # which rewrites shape points and channels without touching the
        # subtree (and whose compensated siblings must NOT be stamped — their
        # channels were rewritten, not zeroed).
        flattened: List[str] = []
        baked_in_place: List[str] = []

        def _channels_zeroed(node) -> bool:
            """True when *node*'s freeze channels now sit at identity —
            proof ``makeIdentity`` actually flattened it.

            ``makeIdentity`` on a group is not all-or-nothing: a descendant
            whose shape is multiply-instanced is skipped with a *warning*
            (``Cannot freeze below transform X …``) while the rest of the
            subtree flattens (measured on a production module scene).
            Stamping such a skipped leaf would recreate exactly the stale
            bake this commit-after-freeze design exists to prevent.
            """
            if "translate" in freeze_channels and any(
                abs(v) > 1e-5 for v in cmds.getAttr(f"{node}.translate")[0]
            ):
                return False
            if "rotate" in freeze_channels and any(
                abs(v) > 1e-5 for v in cmds.getAttr(f"{node}.rotate")[0]
            ):
                return False
            if "scale" in freeze_channels and any(
                abs(v - 1.0) > 1e-5 for v in cmds.getAttr(f"{node}.scale")[0]
            ):
                return False
            return True

        def commit_bakes():
            if not store or not pre_freeze:
                return
            exact = set(flattened) | set(baked_in_place)
            flat_roots = set(flattened)
            for node, local in pre_freeze.items():
                # Ancestor lookup walks the path (O(depth)) rather than
                # testing every frozen root (O(roots)) — freeze_children over
                # a deep hierarchy puts every node in both collections.
                if node in exact:
                    cls._accumulate_bake(node, local, freeze_channels)
                elif (
                    cls._nearest_known_ancestor(node, flat_roots) is not None
                    and cmds.objExists(node)
                    and _channels_zeroed(node)
                ):
                    # Descendant of a flattened root: stamp only on PROOF the
                    # flatten reached it — see _channels_zeroed.
                    cls._accumulate_bake(node, local, freeze_channels)

        if inst_strategy != "skip" and objects:
            instanced = [o for o in objects if NodeUtils.get_instanced_shapes(o)]
            if instanced:
                if inst_strategy == "uninstance":
                    NodeUtils.uninstance(instanced, delete_history=delete_history)
                else:
                    # Preserve: bake the SHARED geometry in place and
                    # compensate the siblings — no fork, no DAG surgery, so
                    # per-instance shading survives (see
                    # freeze_instanced_group). One member per group does the
                    # work; the rest are dropped from this call's object list
                    # so they aren't frozen a second time.
                    handled: Set[str] = set()
                    frozen_groups = 0
                    for obj in instanced:
                        if obj in handled:
                            continue
                        # Claim exactly the members the bake compensates —
                        # NOT every transform sharing any shape. An orphaned
                        # intermediate can be shared more widely than the
                        # visible shape, and claiming those would drop them
                        # from this freeze without ever touching them.
                        members, _ = cls._instance_group_members(obj)
                        if cls.freeze_instanced_group(
                            obj,
                            translate=not axes_to_freeze.isdisjoint({"tx", "ty", "tz"}),
                            rotate=not axes_to_freeze.isdisjoint({"rx", "ry", "rz"}),
                            scale=not axes_to_freeze.isdisjoint({"sx", "sy", "sz"}),
                            quiet=False,
                        ):
                            # Claim the members only once the bake actually
                            # compensated them; a skipped group falls through
                            # to the normal loop, which reports it as an
                            # instanced skip instead of dropping it silently.
                            handled.update(members or [obj])
                            # Only the operated member ends at identity; the
                            # siblings absorbed a compensating matrix, so a
                            # bake of their pre-freeze local would be wrong.
                            baked_in_place.append(obj)
                            frozen_groups += 1
                    objects = [o for o in objects if o not in handled]
                    if frozen_groups:
                        print(
                            "XformUtils.freeze_transforms: "
                            f"{frozen_groups} instance group(s) frozen in place."
                        )
                    if not objects:
                        commit_bakes()
                        return

        if freeze_children:
            objects_set = set(objects)
            for obj in list(objects):
                descendants = (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                )
                for child in descendants:
                    if child not in objects_set:
                        objects.append(child)
                        objects_set.add(child)

        skipped_connections: List[Tuple[str, Dict[str, List[str]]]] = []
        instanced_skips: List[str] = []
        frozen_objects: List[str] = []

        def get_blockers(node: str) -> Dict[str, List[str]]:
            """Helper to find input connections on specified channels.

            Queries both the compound plug and its children — a compound
            ``listConnections`` does NOT see child-plug connections
            (``d.rotateZ -> c.rotateZ`` is invisible to a ``c.rotate`` query)
            and vice versa. Anim curves and constraints connect per-axis, so
            without the child plugs the disconnect/delete strategies found no
            blockers and silently skipped the node.

            Returns ``{dest_plug: [src_plug, ...]}``.
            """
            plugs = []
            for ch in freeze_channels:
                if cmds.attributeQuery(ch, node=node, exists=True):
                    plugs.append(f"{node}.{ch}")
                    children = (
                        cmds.attributeQuery(ch, node=node, listChildren=True) or []
                    )
                    plugs.extend(f"{node}.{child}" for child in children)
            if not plugs:
                return {}

            # cmds.listConnections with connections=True returns a flat list:
            # [dest, src, dest, src, ...] when plugs=True.
            connections = (
                cmds.listConnections(
                    plugs,
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                )
                or []
            )

            found_blockers: Dict[str, List[str]] = {}
            it = iter(connections)
            for dest, src in zip(it, it):
                found_blockers.setdefault(dest, []).append(src)
            return found_blockers

        for obj in objects:
            if not cmds.objExists(obj):
                continue

            if center_pivot == 2:
                cmds.xform(obj, centerPivots=True)
            elif center_pivot == 1:
                shapes = cmds.listRelatives(
                    obj, shapes=True, noIntermediate=True, type="mesh"
                )
                if shapes:
                    cmds.xform(obj, centerPivots=True)

            # Baking into a shared shape would rewrite every sibling instance's
            # geometry, so an instanced object is never frozen in place. Callers
            # that want it baked go through instance_strategy (or
            # NodeUtils.uninstance(freeze=True)), which forks first and then
            # calls back into here. Shared INTERMEDIATE shapes count — Maya's
            # makeIdentity refuses while any child shape is multiply-instanced,
            # so the test and the fork must span the same set.
            try:
                instanced = bool(NodeUtils.get_instanced_shapes(obj))
            except Exception:
                instanced = False

            if instanced:
                instanced_skips.append(CoreUtils.short_name(obj))
                continue

            nodes_to_unlock = []
            if force:
                nodes_to_unlock.append(obj)
                if unlock_children:
                    descendants = (
                        cmds.listRelatives(
                            obj, ad=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    nodes_to_unlock.extend(descendants)
                nodes_to_unlock = cls._prune_to_locked(nodes_to_unlock)

            with Attributes.temporarily_unlock(nodes_to_unlock):
                try:
                    if delete_history:
                        NodeUtils.delete_history(obj)

                    if cls._apply_freeze_deltas(
                        obj, axes_to_freeze, normal=freeze_normals
                    ):
                        frozen_objects.append(CoreUtils.short_name(obj))
                        flattened.append(obj)

                except RuntimeError as exc:
                    msg = str(exc).lower()
                    if "incoming connection" in msg or "locked" in msg:
                        blockers = get_blockers(obj)

                        if not blockers and "locked" not in msg:
                            skipped_connections.append((CoreUtils.short_name(obj), {}))
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' due to connection error: {exc}"
                            )
                            continue

                        if strategy == "preserve":
                            skipped_connections.append(
                                (CoreUtils.short_name(obj), blockers)
                            )
                            continue

                        nodes_to_delete: Set[str] = set()
                        for plug, sources in blockers.items():
                            for src in sources:
                                try:
                                    cmds.disconnectAttr(src, plug)
                                except Exception as disconnect_exc:
                                    raise RuntimeError(
                                        f"Failed to disconnect {src} -> {plug}: {disconnect_exc}"
                                    ) from disconnect_exc

                                if strategy == "delete":
                                    src_node = src.split(".")[0]
                                    if not src_node or src_node == obj:
                                        continue
                                    try:
                                        if cmds.referenceQuery(
                                            src_node, isNodeReferenced=True
                                        ):
                                            continue
                                    except Exception:
                                        pass
                                    nodes_to_delete.add(src_node)

                        if nodes_to_delete:
                            cmds.delete(list(nodes_to_delete))

                        try:
                            if cls._apply_freeze_deltas(
                                obj, axes_to_freeze, normal=freeze_normals
                            ):
                                frozen_objects.append(CoreUtils.short_name(obj))
                                flattened.append(obj)
                        except RuntimeError as retry_exc:
                            skipped_connections.append(
                                (CoreUtils.short_name(obj), blockers)
                            )
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' after clearing connections: {retry_exc}"
                            )

                    else:
                        raise

        commit_bakes()

        total_processed = (
            len(frozen_objects) + len(skipped_connections) + len(instanced_skips)
        )
        # The summary is a tool's confirmation line. Construction-time freezes
        # (``store=False``: rig controls, uninstancing) run in loops where it
        # is pure noise — 49 lines per tube-rig build; skips already warn.
        if total_processed and store:
            skipped_total = len(skipped_connections) + len(instanced_skips)
            print(
                "XformUtils.freeze_transforms: "
                f"{len(frozen_objects)} frozen, {skipped_total} skipped."
            )

    @classmethod
    def _freeze_to_opm(cls, objects, reset_rotate_axis, reset_joint_orient, store):
        """Body of :meth:`XformUtils.freeze_to_opm`."""
        transforms = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", flatten=True) or []
        )
        if not transforms:
            return

        identity_matrix = om.MMatrix()

        for obj in transforms:
            if not cmds.objExists(obj):
                continue

            if store:
                # An OPM bake and a geometry bake have different inverses, so
                # they must never share one history: composing them would send
                # the whole thing down the OPM path, putting the channels back
                # while the geometry stayed baked (the object doubles). A node
                # already carrying a non-OPM bake keeps it and this freeze goes
                # untracked — the honest option, since the alternative silently
                # corrupts a restore.
                if cls.get_stored_transforms(obj) is not None and not cls._has_opm_bake(
                    obj
                ):
                    cmds.warning(
                        f"freeze_to_opm: '{CoreUtils.short_name(obj)}' already "
                        "carries a geometry bake — the OPM freeze is not being "
                        "recorded (the two have different inverses). Un-freeze "
                        "first if you want it tracked."
                    )
                else:
                    # Before any mutation: store_transforms reads the CURRENT local.
                    cls.store_transforms(obj)
                    cls._mark_opm_bake(obj)

            with Attributes.temporarily_unlock([obj]):
                rotate_pivot_ws = cmds.xform(obj, q=True, ws=True, rp=True)
                scale_pivot_ws = cmds.xform(obj, q=True, ws=True, sp=True)

                rotate_pivot_translate = (
                    cmds.getAttr(f"{obj}.rotatePivotTranslate")[0]
                    if cmds.attributeQuery(
                        "rotatePivotTranslate", node=obj, exists=True
                    )
                    else None
                )
                scale_pivot_translate = (
                    cmds.getAttr(f"{obj}.scalePivotTranslate")[0]
                    if cmds.attributeQuery("scalePivotTranslate", node=obj, exists=True)
                    else None
                )

                original_local = om.MMatrix(
                    cmds.xform(obj, q=True, matrix=True, objectSpace=True)
                )

                temp = cmds.duplicate(obj, parentOnly=True)[0]
                try:
                    cls._set_matrix_plug(f"{temp}.offsetParentMatrix", identity_matrix)
                    cmds.setAttr(f"{temp}.translate", 0.0, 0.0, 0.0, type="double3")
                    cmds.setAttr(f"{temp}.rotate", 0.0, 0.0, 0.0, type="double3")
                    cmds.setAttr(f"{temp}.scale", 1.0, 1.0, 1.0, type="double3")
                    if cmds.attributeQuery("shear", node=temp, exists=True):
                        cmds.setAttr(f"{temp}.shear", 0.0, 0.0, 0.0, type="double3")

                    rest_matrix = om.MMatrix(
                        cmds.xform(temp, q=True, matrix=True, objectSpace=True)
                    )
                finally:
                    cmds.delete(temp)

                try:
                    compensation = rest_matrix.inverse()
                except RuntimeError:
                    cmds.warning(
                        f"XformUtils.freeze_to_opm: Skipping '{obj}' due to singular pivot matrix."
                    )
                    continue

                opm_matrix = compensation * original_local
                cls._set_matrix_plug(f"{obj}.offsetParentMatrix", opm_matrix)

                cmds.setAttr(f"{obj}.translate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{obj}.rotate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{obj}.scale", 1.0, 1.0, 1.0, type="double3")
                if cmds.attributeQuery("shear", node=obj, exists=True):
                    cmds.setAttr(f"{obj}.shear", 0.0, 0.0, 0.0, type="double3")

                cmds.xform(obj, ws=True, rp=rotate_pivot_ws, preserve=True)
                cmds.xform(obj, ws=True, sp=scale_pivot_ws, preserve=True)

                if rotate_pivot_translate is not None:
                    cmds.setAttr(
                        f"{obj}.rotatePivotTranslate",
                        *rotate_pivot_translate,
                        type="double3",
                    )
                if scale_pivot_translate is not None:
                    cmds.setAttr(
                        f"{obj}.scalePivotTranslate",
                        *scale_pivot_translate,
                        type="double3",
                    )

                if reset_rotate_axis and cmds.attributeQuery(
                    "rotateAxis", node=obj, exists=True
                ):
                    cmds.setAttr(f"{obj}.rotateAxis", 0.0, 0.0, 0.0, type="double3")

                if reset_joint_orient and cmds.attributeQuery(
                    "jointOrient", node=obj, exists=True
                ):
                    cmds.setAttr(f"{obj}.jointOrient", 0.0, 0.0, 0.0, type="double3")

    @classmethod
    def _unfreeze_from_opm(cls, objects, prefix, delete_attrs):
        """Body of :meth:`XformUtils.unfreeze_from_opm`."""
        restored: List[str] = []
        t_attr, r_attr, s_attr = cls._bake_attr_names(prefix)
        marker = cls._opm_marker_name(prefix)

        for obj in (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        ):
            stored = cls.get_stored_transforms(obj, prefix=prefix)
            if stored is None:
                continue
            if cmds.referenceQuery(obj, isNodeReferenced=True):
                cmds.warning(f"unfreeze_from_opm: skipping referenced node {obj}.")
                continue

            with Attributes.temporarily_unlock([obj]):
                # The world matrix is identical either side of an OPM freeze,
                # so re-pinning the world pivots after the channels go back
                # recomputes exactly the local pivot values the freeze
                # displaced — the mirror of what freeze_to_opm did on the way
                # in. (_apply_clean_local is the wrong tool here: it ZEROES
                # the pivots, which is right after a geometry bake and wrong
                # after a freeze that deliberately preserved them.)
                rotate_pivot_ws = cmds.xform(obj, q=True, ws=True, rp=True)
                scale_pivot_ws = cmds.xform(obj, q=True, ws=True, sp=True)

                cls._set_matrix_plug(f"{obj}.offsetParentMatrix", om.MMatrix())

                t_vec = stored["translate"]
                s_vec = stored["scale"]
                cmds.setAttr(f"{obj}.translate", *t_vec, type="double3")
                cmds.setAttr(f"{obj}.scale", *s_vec, type="double3")
                rot_order = cmds.getAttr(f"{obj}.rotateOrder") or 0
                euler = stored["rotate"].asEulerRotation()
                euler.reorderIt(rot_order)
                cmds.setAttr(
                    f"{obj}.rotate",
                    math.degrees(euler.x),
                    math.degrees(euler.y),
                    math.degrees(euler.z),
                    type="double3",
                )

                cmds.xform(obj, ws=True, rp=rotate_pivot_ws, preserve=True)
                cmds.xform(obj, ws=True, sp=scale_pivot_ws, preserve=True)

            if delete_attrs:
                for attr in (t_attr, r_attr, s_attr, marker):
                    if cmds.attributeQuery(attr, node=obj, exists=True):
                        cmds.deleteAttr(f"{obj}.{attr}")
            restored.append(obj)
        return restored

    @classmethod
    def _unfreeze_to_parent(cls, objects, traverse, preserve_root):
        """Body of :meth:`XformUtils.unfreeze_to_parent`."""
        if om is None or cmds is None:
            return []

        nodes = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        identity_matrix = om.MMatrix()
        modified_parents: List[str] = []
        root_set = set(nodes) if (traverse and preserve_root) else set()

        pairs: List[Tuple[str, str]] = []  # (parent, child)
        seen_children: Set[str] = set()

        for node in nodes:
            if not cmds.objExists(node):
                continue

            if traverse:
                locator_shapes = (
                    cmds.listRelatives(
                        node, allDescendents=True, type="locator", fullPath=True
                    )
                    or []
                )
                for shape in locator_shapes:
                    loc_xform_list = (
                        cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    )
                    if not loc_xform_list:
                        continue
                    child = loc_xform_list[0]
                    if child in seen_children:
                        continue
                    parent_list = (
                        cmds.listRelatives(child, parent=True, fullPath=True) or []
                    )
                    if not parent_list:
                        continue
                    parent = parent_list[0]
                    if parent in root_set:
                        continue
                    pairs.append((parent, child))
                    seen_children.add(child)
            else:
                child = node
                if child in seen_children:
                    continue
                parent_list = (
                    cmds.listRelatives(child, parent=True, fullPath=True) or []
                )
                if not parent_list:
                    cmds.warning(
                        f"XformUtils.unfreeze_to_parent: '{CoreUtils.short_name(child)}' "
                        "has no parent. Skipping."
                    )
                    continue
                pairs.append((parent_list[0], child))
                seen_children.add(child)

        for parent, child in pairs:
            child_local = om.MMatrix(
                cmds.xform(child, q=True, matrix=True, objectSpace=True)
            )
            parent_local = om.MMatrix(
                cmds.xform(parent, q=True, matrix=True, objectSpace=True)
            )

            # Maya row-vector convention: descendant.world = ... * child_local *
            # parent_local * grandparent_world. Absorbing child_local into
            # parent_local gives parent_new = child_local * parent_local.
            parent_new = child_local * parent_local

            with Attributes.temporarily_unlock([parent, child]):
                cls.set_object_matrix(parent, parent_new, world=False)
                cls.set_object_matrix(child, identity_matrix, world=False)

            modified_parents.append(CoreUtils.short_name(parent))

        if modified_parents:
            print(
                "XformUtils.unfreeze_to_parent: "
                f"{len(modified_parents)} parent(s) updated."
            )

        return modified_parents
