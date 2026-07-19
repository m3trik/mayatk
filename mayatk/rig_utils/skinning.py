# !/usr/bin/python
# coding=utf-8
"""Skinning utilities: binding, batch weight I/O, transfer, procedural weights.

Influence-index contract
------------------------
All weight arrays in this module are ordered by **physical influence index** —
the position in ``MFnSkinCluster.influenceObjects()`` — which is also what the
API 2.0 ``getWeights``/``setWeights`` consume. Never use
``indexForInfluenceObject()`` (that returns the *logical* ``matrix[]`` plug
index, which diverges from physical order once an influence has been removed)
and never assume ``cmds.skinCluster -q -influence`` order.
``SkinUtils.get_influences()`` is the single source of truth for ordering.

Weight data shapes
------------------
- Dense: ``(weights, influences)`` — flat vertex-major floats,
  ``weights[v * len(influences) + i]``.
- Sparse: ``{vertex_index: {influence_name: weight}}``.
"""
import os
import bisect
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils, leaf_name
from mayatk.nurbs_utils._nurbs_utils import NurbsUtils

# DCC-agnostic (name/callable -> f(t) falloff curve); lives upstream so the
# blendertk mirror of this module shares one implementation.
resolve_falloff_profile = ptk.MathUtils.resolve_falloff_profile


class CurveWeights(ptk.HelpMixin):
    """Analytic, ring-uniform skin weights for a joint chain along a curve.

    Projects every vertex onto the curve, converts the parameter to arc
    length, and evaluates a clamped B-spline basis over the joints'
    arc-length stations (cubic by default — C2-continuous weights spanning
    up to ``degree + 1`` joints, so bends and twists spread smoothly across
    neighboring joints instead of hinging at each station). Vertices in the
    same cross-section ring project to the same curve point, so they receive
    identical weights — twist pinching (candy-wrapper) is impossible by
    construction. Vertices projecting before/after the end joint stations
    (tube caps) weight fully to the end joints.
    """

    @staticmethod
    def _temp_curve_from_centerline(centerline: Sequence) -> str:
        """Build a temp curve through *centerline* edit points (deg 3, or 1 if < 4 pts)."""
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in centerline]
        degree = 3 if len(points) >= 4 else 1
        return cmds.curve(ep=points, d=degree)

    @staticmethod
    def _mesh_points(mesh) -> "om.MPointArray":
        """World-space vertex positions via one MFnMesh.getPoints call."""
        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        if not dag.hasFn(om.MFn.kMesh):
            dag.extendToShape()
        return om.MFnMesh(dag).getPoints(om.MSpace.kWorld)

    @staticmethod
    def effective_degree(degree: int, num_joints: int) -> int:
        """The basis degree actually solvable: *degree* clamped to [1, num_joints - 1]."""
        return max(1, min(int(degree), num_joints - 1))

    @classmethod
    def joint_stations(cls, joints: List[str], curve) -> List[float]:
        """Arc length of each joint's closest curve point, in input order.

        Raises:
            ValueError: If the stations are not strictly increasing along the
                curve (joints out of order, or two joints projecting to the
                same point).
        """
        positions = [cmds.xform(str(j), q=True, ws=True, t=True) for j in joints]
        stations = NurbsUtils.get_arc_lengths(curve, positions)
        for a, b, ja, jb in zip(stations, stations[1:], joints, joints[1:]):
            if b - a <= 1e-6:
                raise ValueError(
                    f"Joint stations are not strictly increasing along the curve: "
                    f"{ja} ({a:.6f}) -> {jb} ({b:.6f}). Joints must be ordered "
                    "start-to-end along the curve and may not overlap."
                )
        return stations

    @classmethod
    def solve(
        cls,
        mesh,
        joints: List[str],
        curve: Optional[str] = None,
        centerline: Optional[Sequence] = None,
        profile: Union[str, Callable] = "smoothstep",
        degree: int = 3,
    ) -> Tuple[List[float], List[str]]:
        """Compute per-vertex weights from arc-length stations along a curve.

        Parameters:
            mesh (str/obj): The mesh (transform or shape) to weight.
            joints (List[str]): Joint chain ordered start-to-end along the tube.
            curve (str): A NURBS curve following the tube centerline. Mutually
                exclusive with *centerline*.
            centerline (List): Ordered centerline points (MPoint or xyz). A temp
                curve is built through them and deleted afterwards.
            profile (str/callable): Blend profile between bracketing joints
                (see resolve_falloff_profile). Only applies at degree 1;
                higher degrees define the blend by the basis itself.
            degree (int): Smoothness of the weight field, clamped to
                ``len(joints) - 1``. 1 = pairwise blend between the two
                bracketing joints (each ring at a joint station rigidly
                follows that joint — visible hinge creases on tight bends).
                2/3 = clamped B-spline basis over the stations (C1/C2
                continuous, up to ``degree + 1`` influences) — deformation
                spreads smoothly across neighboring joints, matching how a
                spline curve deforms. Default 3 (cubic).

        Returns:
            (tuple) (weights, influences): flat vertex-major weights over the
                *joints* input order — ready for SkinUtils.set_weights.
                Guarantees: each row sums to 1.0; at most ``degree + 1``
                non-zero influences per vertex; cap vertices clamp to the
                end joints.
        """
        if (curve is None) == (centerline is None):
            raise ValueError("Provide exactly one of 'curve' or 'centerline'.")
        joints = [str(j) for j in joints]
        if len(joints) < 2:
            raise ValueError("At least 2 joints are required.")
        profile_fn = resolve_falloff_profile(profile)
        degree = cls.effective_degree(degree, len(joints))

        temp_curve = None
        try:
            if centerline is not None:
                curve = temp_curve = cls._temp_curve_from_centerline(centerline)
            curve = str(curve)

            points = cls._mesh_points(mesh)
            stations = cls.joint_stations(joints, curve)
            vertex_s = NurbsUtils.get_arc_lengths(curve, points)
        finally:
            if temp_curve and cmds.objExists(temp_curve):
                cmds.delete(temp_curve)

        n_inf = len(joints)
        s_first, s_last = stations[0], stations[-1]
        knots = (
            ptk.MathUtils.bspline_clamped_knots(stations, degree)
            if degree > 1
            else None
        )
        weights = [0.0] * (len(points) * n_inf)
        for v, s in enumerate(vertex_s):
            base = v * n_inf
            if s <= s_first:  # start cap
                weights[base] = 1.0
            elif s >= s_last:  # end cap
                weights[base + n_inf - 1] = 1.0
            elif knots is None:  # degree 1: pairwise blend, shaped by *profile*
                i = min(bisect.bisect_right(stations, s) - 1, n_inf - 2)
                t = (s - stations[i]) / (stations[i + 1] - stations[i])
                w = min(max(float(profile_fn(t)), 0.0), 1.0)
                # Both terms derive from the same float: rows sum to exactly 1.
                weights[base + i] = 1.0 - w
                weights[base + i + 1] = w
            else:
                span = min(max(bisect.bisect_right(knots, s) - 1, degree), n_inf - 1)
                basis = ptk.MathUtils.bspline_basis(knots, span, degree, s)
                total = sum(basis)  # 1.0 to machine precision; pin it exactly
                for r, w in enumerate(basis):
                    weights[base + span - degree + r] = w / total
        return weights, joints


class SkinUtils(ptk.HelpMixin):
    """Skinning: binding, batch weight I/O, transfer, falloffs, delta mush."""

    BIND_METHODS = {"closest": 0, "hierarchy": 1, "heatmap": 2, "geodesic": 3}
    SKINNING_METHODS = {
        "classic": 0,
        "linear": 0,
        "dqs": 1,
        "dual_quaternion": 1,
        "blended": 2,
    }

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @staticmethod
    def get_skin_cluster(mesh) -> Optional[str]:
        """Return the first skinCluster in the mesh's history, or None."""
        mesh = str(mesh)
        if not cmds.objExists(mesh):
            return None
        history = cmds.listHistory(mesh, pruneDagObjects=True) or []
        clusters = cmds.ls(history, type="skinCluster") or []
        return clusters[0] if clusters else None

    @classmethod
    def get_influences(cls, skin_cluster, long_names: bool = False) -> List[str]:
        """Influence names in PHYSICAL order (``MFnSkinCluster.influenceObjects()``).

        This order indexes every weight array in this module.
        """
        fn = cls._skin_fn(skin_cluster)
        paths = fn.influenceObjects()
        return [p.fullPathName() if long_names else p.partialPathName() for p in paths]

    @staticmethod
    def _skin_fn(skin_cluster) -> "oma.MFnSkinCluster":
        sel = om.MSelectionList()
        sel.add(str(skin_cluster))
        return oma.MFnSkinCluster(sel.getDependNode(0))

    @staticmethod
    def _resolve_geometry(skin_cluster) -> str:
        """The output geometry (shape) driven by *skin_cluster*."""
        geo = (cmds.skinCluster(str(skin_cluster), q=True, geometry=True) or [None])[0]
        if not geo:
            raise RuntimeError(f"No geometry on skinCluster: {skin_cluster}")
        return geo

    @classmethod
    def _get_skin_fn(
        cls, skin_cluster, vertices: Optional[Sequence[int]] = None
    ) -> Tuple["oma.MFnSkinCluster", "om.MDagPath", "om.MObject"]:
        """Resolve (MFnSkinCluster, geo dagPath, vertex component) for weight I/O."""
        fn = cls._skin_fn(skin_cluster)
        geo = cls._resolve_geometry(skin_cluster)
        sel = om.MSelectionList()
        sel.add(geo)
        dag = sel.getDagPath(0)
        if not dag.hasFn(om.MFn.kMesh):
            dag.extendToShape()
        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        if vertices is None:
            comp_fn.setCompleteData(om.MFnMesh(dag).numVertices)
        else:
            comp_fn.addElements(list(vertices))
        return fn, dag, comp

    @classmethod
    def _influence_index_map(cls, skin_cluster) -> Dict[str, int]:
        """Map full, partial, and leaf influence names -> physical index."""
        index_map: Dict[str, int] = {}
        for i, path in enumerate(cls._skin_fn(skin_cluster).influenceObjects()):
            index_map[path.fullPathName()] = i
            index_map[path.partialPathName()] = i
            index_map.setdefault(path.fullPathName().split("|")[-1], i)
        return index_map

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def bind(
        cls,
        mesh,
        joints,
        bind_method: str = "closest",
        skinning_method: str = "classic",
        max_influences: int = 4,
        dropoff_rate: float = 4.0,
        weight_distribution: float = 0.5,
        remove_unused_influences: bool = False,
        heatmap_falloff: float = 0.68,
        bind_fallback: bool = True,
        name: Optional[str] = None,
    ) -> str:
        """Smooth-bind *mesh* to *joints* with the full skinCluster arg surface.

        Parameters:
            mesh (str/obj): Mesh transform or shape to bind.
            joints (str/list): Influence joints.
            bind_method (str): "closest" | "hierarchy" | "heatmap" | "geodesic"
                (bindMethod 0/1/2/3). Geodesic voxel gives the best generic
                results on tube-like/overlapping geometry.
            skinning_method (str): "classic" (linear) | "dqs" (dual quaternion)
                | "blended". DQS preserves volume on bend/twist.
            max_influences (int): Maximum influences per vertex (obeyed).
            dropoff_rate (float): Weight falloff rate (closest-distance bind).
            weight_distribution (float): skinCluster weightDistribution.
            remove_unused_influences (bool): Strip zero-weight influences.
            heatmap_falloff (float): Heatmap falloff (heatmap bind only).
            bind_fallback (bool): Heatmap/geodesic can fail without a GPU/GL
                context (e.g. headless). When True, fall back to a
                closest-distance bind with a warning instead of raising.
            name (str): Optional skinCluster node name.

        Returns:
            (str) The skinCluster name.

        Raises:
            ValueError: On unknown method names, invalid joints, or when the
                mesh is already bound (unbind first — see ``unbind``).
        """
        mesh = str(mesh)
        if not isinstance(joints, (list, tuple)):
            joints = [joints]
        joints = [str(j) for j in joints]
        if bind_method not in cls.BIND_METHODS:
            raise ValueError(
                f"Invalid bind_method: {bind_method!r}. Expected one of {sorted(cls.BIND_METHODS)}."
            )
        if skinning_method not in cls.SKINNING_METHODS:
            raise ValueError(
                f"Invalid skinning_method: {skinning_method!r}. Expected one of {sorted(cls.SKINNING_METHODS)}."
            )
        missing = [j for j in joints if not cmds.objExists(j)]
        if not joints or missing:
            raise ValueError(f"Invalid joints: {missing or joints}")
        existing = cls.get_skin_cluster(mesh)
        if existing:
            raise ValueError(f"{mesh} is already bound to {existing}; unbind first.")

        kwargs = dict(
            toSelectedBones=True,
            bindMethod=cls.BIND_METHODS[bind_method],
            skinMethod=cls.SKINNING_METHODS[skinning_method],
            maximumInfluences=max_influences,
            obeyMaxInfluences=True,
            dropoffRate=dropoff_rate,
            weightDistribution=weight_distribution,
            removeUnusedInfluence=remove_unused_influences,
            normalizeWeights=1,
        )
        if name:
            kwargs["name"] = name
        if bind_method == "heatmap":
            kwargs["heatmapFalloff"] = heatmap_falloff

        try:
            result = cmds.skinCluster(joints, mesh, **kwargs)
        except RuntimeError as e:
            if not (bind_fallback and bind_method in ("heatmap", "geodesic")):
                raise
            om.MGlobal.displayWarning(
                f"{bind_method} bind failed ({e}); falling back to closest-distance."
            )
            # A failed bind attempt may leave a partial cluster behind.
            partial = cls.get_skin_cluster(mesh)
            if partial:
                cmds.delete(partial)
            kwargs["bindMethod"] = cls.BIND_METHODS["closest"]
            kwargs.pop("heatmapFalloff", None)
            result = cmds.skinCluster(joints, mesh, **kwargs)
        skin_cluster = result[0] if isinstance(result, (list, tuple)) else result
        if name:
            cls.name_bind_pose(skin_cluster, f"{name}_pose")
        return skin_cluster

    @staticmethod
    def name_bind_pose(skin_cluster, name: str) -> Optional[str]:
        """Rename *skin_cluster*'s dagPose to *name*.

        ``cmds.skinCluster`` honors ``name`` for the cluster but always
        leaves the dagPose it creates default-named ('bindPose1') — debris
        that name-based cleanup sweeps can't attribute in multi-rig scenes.

        Returns:
            (str) The new pose name, or None when the cluster has no pose.
        """
        poses = (
            cmds.listConnections(
                f"{skin_cluster}.bindPose", source=True, destination=False
            )
            or []
        )
        if not poses:
            return None
        return cmds.rename(poses[0], name)

    @classmethod
    @CoreUtils.undoable
    def unbind(cls, mesh) -> bool:
        """Remove the mesh's skinCluster (restores the pre-bind shape).

        Returns:
            (bool) True if a skinCluster was removed.
        """
        skin_cluster = cls.get_skin_cluster(mesh)
        if not skin_cluster:
            return False
        cmds.skinCluster(skin_cluster, edit=True, unbind=True)
        return True

    # ------------------------------------------------------------------
    # Batch weight I/O
    # ------------------------------------------------------------------

    @classmethod
    def get_weights(
        cls, skin_cluster, vertices: Optional[Sequence[int]] = None
    ) -> Tuple[List[float], List[str]]:
        """Read weights in one batched API call.

        Returns:
            (tuple) (weights, influences): flat vertex-major weights and the
                influence names in physical order. With a *vertices* subset,
                rows follow the order of that sequence.
        """
        fn, dag, comp = cls._get_skin_fn(skin_cluster, vertices)
        weights, _ = fn.getWeights(dag, comp)
        return list(weights), [p.partialPathName() for p in fn.influenceObjects()]

    @classmethod
    def set_weights(
        cls,
        skin_cluster,
        weights: Sequence[float],
        influences: Optional[List[str]] = None,
        vertices: Optional[Sequence[int]] = None,
        normalize: bool = True,
        undoable: bool = False,
    ) -> List[float]:
        """Write weights in one batched call. Returns the previous weights.

        Parameters:
            skin_cluster (str): The skinCluster.
            weights (list): Flat vertex-major weights; length must equal
                n_vertices * n_influences (over *influences* order when given,
                else all influences in physical order).
            influences (List[str]): Optional influence-name subset; columns of
                *weights* follow this order. Default: all, physical order.
            vertices (List[int]): Optional vertex-index subset; rows of
                *weights* follow the order of this sequence.
            normalize (bool): Normalize after setting.
            undoable (bool): False (default) writes via a single
                MFnSkinCluster.setWeights — fast and exact but NOT in Maya's
                undo queue (safe when the cluster was created inside the same
                undo chunk: undoing the chunk deletes the deformer entirely).
                True routes through cmds.skinPercent per vertex inside an undo
                chunk (slower; for interactive edits of existing clusters).

        Returns:
            (list) The previous weights of the affected vertices across ALL
                influences in physical order (regardless of any *influences*
                subset) — pass back through set_weights to restore.
        """
        skin_cluster = str(skin_cluster)
        all_influences = cls.get_influences(skin_cluster)
        if influences is None:
            influence_indices = list(range(len(all_influences)))
            influence_names = list(all_influences)
        else:
            index_map = cls._influence_index_map(skin_cluster)
            influence_indices, influence_names = [], []
            for inf in influences:
                key = str(inf)
                if key not in index_map:
                    key = key.split("|")[-1]
                if key not in index_map:
                    raise ValueError(f"Influence not in skinCluster: {inf}")
                influence_indices.append(index_map[key])
                influence_names.append(key)

        n_inf = len(influence_indices)
        fn, dag, comp = cls._get_skin_fn(skin_cluster, vertices)
        n_verts = om.MFnSingleIndexedComponent(comp).elementCount
        if len(weights) != n_verts * n_inf:
            raise ValueError(
                f"Weight count mismatch: got {len(weights)}, expected "
                f"{n_verts} vertices x {n_inf} influences = {n_verts * n_inf}."
            )

        if not undoable:
            if influences is None:
                old = fn.setWeights(
                    dag,
                    comp,
                    om.MIntArray(influence_indices),
                    om.MDoubleArray([float(w) for w in weights]),
                    normalize,
                    True,  # returnOldWeights
                )
                return list(old)
            # Influence subset: setWeights' returnOldWeights covers only the
            # subset columns, which would violate the documented all-influence
            # restore contract. Snapshot every influence before writing.
            old_weights, _ = cls.get_weights(skin_cluster, vertices)
            fn.setWeights(
                dag,
                comp,
                om.MIntArray(influence_indices),
                om.MDoubleArray([float(w) for w in weights]),
                normalize,
                False,
            )
            return old_weights

        # Undo-safe route: per-vertex skinPercent inside one undo chunk.
        old_weights, _ = cls.get_weights(skin_cluster, vertices)
        geo = cls._resolve_geometry(skin_cluster)
        vertex_ids = (
            list(vertices) if vertices is not None else list(range(n_verts))
        )
        with CoreUtils.undo_chunk():
            for k, v in enumerate(vertex_ids):
                row = weights[k * n_inf : (k + 1) * n_inf]
                cmds.skinPercent(
                    skin_cluster,
                    f"{geo}.vtx[{v}]",
                    transformValue=list(zip(influence_names, map(float, row))),
                    normalize=normalize,
                )
        return old_weights

    @classmethod
    def set_vertex_weights(
        cls,
        skin_cluster,
        vertex_weights: Dict[int, Dict[str, float]],
        undoable: bool = True,
    ) -> None:
        """Sparse per-vertex write with skinPercent semantics.

        Named influences receive their exact values; the remainder
        (1 - sum(specified)) redistributes across the unspecified influences
        proportionally to their current weights. Specified values summing past
        1 are renormalized to 1 (unspecified influences drop to 0), matching
        skinPercent.

        Parameters:
            vertex_weights (dict): ``{vertex_index: {influence_name: weight}}``.
            undoable (bool): True (default) applies via cmds.skinPercent
                (native semantics, undo-safe). False computes the
                redistribution in Python and writes one batched setWeights.
        """
        skin_cluster = str(skin_cluster)
        if not vertex_weights:
            return
        if undoable:
            geo = cls._resolve_geometry(skin_cluster)
            with CoreUtils.undo_chunk():
                for v, row in vertex_weights.items():
                    cmds.skinPercent(
                        skin_cluster,
                        f"{geo}.vtx[{v}]",
                        transformValue=[(str(i), float(w)) for i, w in row.items()],
                    )
            return

        index_map = cls._influence_index_map(skin_cluster)
        vertex_ids = sorted(vertex_weights)
        flat, all_influences = cls.get_weights(skin_cluster, vertices=vertex_ids)
        n_inf = len(all_influences)
        for k, v in enumerate(vertex_ids):
            specified: Dict[int, float] = {}
            for inf, w in vertex_weights[v].items():
                key = str(inf)
                if key not in index_map:
                    key = key.split("|")[-1]
                if key not in index_map:
                    raise ValueError(f"Influence not in skinCluster: {inf}")
                specified[index_map[key]] = float(w)
            row = flat[k * n_inf : (k + 1) * n_inf]
            specified_sum = sum(specified.values())
            remaining = max(0.0, 1.0 - specified_sum)
            unspecified_sum = sum(
                row[i] for i in range(n_inf) if i not in specified
            )
            for i in range(n_inf):
                if i in specified:
                    row[i] = specified[i]
                elif unspecified_sum > 1e-9:
                    row[i] = row[i] / unspecified_sum * remaining
                else:
                    row[i] = 0.0
            # No unspecified weight to absorb the remainder, or the specified
            # values overshoot 1: rescale the specified weights to sum 1
            # (matching skinPercent's normalization).
            if specified_sum > 0 and (unspecified_sum <= 1e-9 or specified_sum > 1.0):
                scale = 1.0 / specified_sum
                for i in specified:
                    row[i] *= scale
            flat[k * n_inf : (k + 1) * n_inf] = row
        cls.set_weights(
            skin_cluster, flat, vertices=vertex_ids, normalize=False, undoable=False
        )

    # ------------------------------------------------------------------
    # Weight operations
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def prune_weights(cls, skin_cluster, below: float = 0.001) -> None:
        """Zero weights below the threshold and renormalize."""
        geo = cls._resolve_geometry(skin_cluster)
        cmds.skinPercent(str(skin_cluster), f"{geo}.vtx[*]", pruneWeights=below)

    @classmethod
    @CoreUtils.undoable
    def normalize_weights(cls, skin_cluster) -> None:
        """Normalize all weights to sum 1 per vertex."""
        geo = cls._resolve_geometry(skin_cluster)
        cmds.skinPercent(str(skin_cluster), f"{geo}.vtx[*]", normalize=True)

    @classmethod
    @CoreUtils.undoable
    def set_max_influences(
        cls, skin_cluster, max_influences: int, enforce: bool = True
    ) -> None:
        """Set the influence cap; optionally re-weight existing vertices to obey it.

        Maya does not re-weight when ``.maxInfluences`` changes — with
        *enforce* True, each vertex keeps its top-N weights (renormalized) via
        one batched write.
        """
        skin_cluster = str(skin_cluster)
        cmds.setAttr(f"{skin_cluster}.maxInfluences", max_influences)
        cmds.setAttr(f"{skin_cluster}.maintainMaxInfluences", True)
        if not enforce:
            return
        flat, influences = cls.get_weights(skin_cluster)
        n_inf = len(influences)
        changed = False
        for base in range(0, len(flat), n_inf):
            row = flat[base : base + n_inf]
            ranked = sorted(range(n_inf), key=lambda i: row[i], reverse=True)
            keep = set(ranked[:max_influences])
            kept_sum = sum(row[i] for i in keep)
            if len([w for w in row if w > 1e-9]) <= max_influences or kept_sum <= 0:
                continue
            changed = True
            for i in range(n_inf):
                flat[base + i] = row[i] / kept_sum if i in keep else 0.0
        if changed:
            cls.set_weights(skin_cluster, flat, normalize=False, undoable=False)

    @classmethod
    @CoreUtils.undoable
    def set_skinning_method(cls, skin_cluster, method: str = "dqs") -> None:
        """Set the blend method: "classic" | "dqs" | "blended"."""
        if method not in cls.SKINNING_METHODS:
            raise ValueError(
                f"Invalid skinning method: {method!r}. Expected one of {sorted(cls.SKINNING_METHODS)}."
            )
        cmds.setAttr(f"{str(skin_cluster)}.skinningMethod", cls.SKINNING_METHODS[method])

    # ------------------------------------------------------------------
    # Transfer
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def copy_weights(
        cls,
        source_mesh,
        target_mesh,
        surface_association: str = "closestPoint",
        influence_association: Sequence[str] = ("label", "oneToOne", "closestJoint"),
        bind_target_if_needed: bool = True,
    ) -> str:
        """Copy skin weights between meshes; binds the target first if needed.

        Returns:
            (str) The target skinCluster.
        """
        source_sc = cls.get_skin_cluster(source_mesh)
        if not source_sc:
            raise ValueError(f"No skinCluster on source mesh: {source_mesh}")
        target_sc = cls.get_skin_cluster(target_mesh)
        if not target_sc:
            if not bind_target_if_needed:
                raise ValueError(f"No skinCluster on target mesh: {target_mesh}")
            method_idx = cmds.getAttr(f"{source_sc}.skinningMethod")
            method = {0: "classic", 1: "dqs", 2: "blended"}.get(method_idx, "classic")
            target_sc = cls.bind(
                target_mesh,
                cls.get_influences(source_sc),
                skinning_method=method,
                max_influences=cmds.getAttr(f"{source_sc}.maxInfluences"),
            )
        cmds.copySkinWeights(
            sourceSkin=source_sc,
            destinationSkin=target_sc,
            noMirror=True,
            surfaceAssociation=surface_association,
            influenceAssociation=list(influence_association),
        )
        return target_sc

    @classmethod
    @CoreUtils.undoable
    def mirror_weights(
        cls,
        mesh,
        axis: str = "YZ",
        positive_to_negative: bool = True,
        surface_association: str = "closestPoint",
        influence_association: Sequence[str] = ("label", "closestJoint", "oneToOne"),
    ) -> None:
        """Mirror weights across a plane ("YZ" | "XY" | "XZ") on the same mesh."""
        skin_cluster = cls.get_skin_cluster(mesh)
        if not skin_cluster:
            raise ValueError(f"No skinCluster on mesh: {mesh}")
        cmds.copySkinWeights(
            sourceSkin=skin_cluster,
            destinationSkin=skin_cluster,
            mirrorMode=axis,
            mirrorInverse=not positive_to_negative,
            surfaceAssociation=surface_association,
            influenceAssociation=list(influence_association),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def export_weights(cls, mesh, file_path: Optional[str] = None) -> str:
        """Export skin weights to XML (cmds.deformerWeights).

        Default path: ``<maya user tmp>/skin_weights/<mesh>.xml``. Returns the
        full file path.

        Note: RigUtils.rebind_skin_clusters uses the same mechanism and should
        delegate here eventually.
        """
        skin_cluster = cls.get_skin_cluster(mesh)
        if not skin_cluster:
            raise ValueError(f"No skinCluster on mesh: {mesh}")
        if file_path is None:
            directory = os.path.join(
                cmds.internalVar(userTmpDir=True), "skin_weights"
            )
            file_path = os.path.join(directory, f"{leaf_name(mesh)}.xml")
        directory, filename = os.path.split(file_path)
        os.makedirs(directory, exist_ok=True)
        cmds.deformerWeights(
            filename,
            path=directory,
            export=True,
            deformer=skin_cluster,
            vertexConnections=True,
        )
        return file_path

    @classmethod
    @CoreUtils.undoable
    def import_weights(cls, mesh, file_path: str, method: str = "index") -> None:
        """Import skin weights from XML and renormalize.

        Parameters:
            method (str): "index" (exact topology) | "nearest" | "bilinear".
        """
        skin_cluster = cls.get_skin_cluster(mesh)
        if not skin_cluster:
            raise ValueError(f"No skinCluster on mesh: {mesh}")
        directory, filename = os.path.split(str(file_path))
        cmds.deformerWeights(
            filename,
            path=directory,
            im=True,
            deformer=skin_cluster,
            method=method,
        )
        cmds.skinCluster(skin_cluster, edit=True, forceNormalizeWeights=True)

    # ------------------------------------------------------------------
    # Procedural weighting
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def apply_falloff(
        cls,
        skin_cluster,
        target_influence,
        center,
        radius: float = 5.0,
        profile: Union[str, Callable] = "linear",
        source_influence: Optional[str] = None,
        add_influence: bool = True,
        undoable: bool = True,
    ) -> int:
        """Distance-based weight falloff around *center*.

        For each vertex within *radius* of center: ``t = 1 - d/radius``,
        ``w = profile(t)``. With *source_influence* the vertex is weighted
        ``{target: w, source: 1 - w}``; without it, the target receives ``w``
        and the remainder redistributes across the other influences
        (skinPercent semantics). Vertex positions are fetched in one batched
        MFnMesh.getPoints call. ``profile="linear"`` reproduces the classic
        ``w = 1 - d/radius`` falloff exactly.

        Parameters:
            target_influence (str): Influence receiving the falloff weight
                (added as a 0-weight influence if missing and *add_influence*).
            center (str/tuple): World-space point or a node whose position to use.
            radius (float): World-space falloff distance.
            source_influence (str): Optional influence to blend against 1:1.
            undoable (bool): Route the write through skinPercent (undo-safe).

        Returns:
            (int) The number of affected vertices.
        """
        skin_cluster = str(skin_cluster)
        target_influence = str(target_influence)
        profile_fn = resolve_falloff_profile(profile)
        if isinstance(center, str):
            center = cmds.xform(center, q=True, ws=True, t=True)
        center_v = om.MVector(center[0], center[1], center[2])

        influences = cls.get_influences(skin_cluster)
        influence_leaves = {i.split("|")[-1] for i in influences}
        if target_influence.split("|")[-1] not in influence_leaves:
            if not add_influence:
                raise ValueError(
                    f"Influence not in skinCluster: {target_influence}"
                )
            cmds.skinCluster(
                skin_cluster, edit=True, addInfluence=target_influence, weight=0.0
            )

        geo = cls._resolve_geometry(skin_cluster)
        points = CurveWeights._mesh_points(geo)
        affected: Dict[int, Dict[str, float]] = {}
        for i, p in enumerate(points):
            d = (om.MVector(p.x, p.y, p.z) - center_v).length()
            if d > radius:
                continue
            t = 1.0 - (d / radius)
            w = min(max(float(profile_fn(t)), 0.0), 1.0)
            if source_influence:
                affected[i] = {
                    target_influence: w,
                    str(source_influence): 1.0 - w,
                }
            else:
                affected[i] = {target_influence: w}
        if affected:
            cls.set_vertex_weights(skin_cluster, affected, undoable=undoable)
        return len(affected)

    # ------------------------------------------------------------------
    # Deformers / conveniences
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def add_delta_mush(
        cls,
        mesh,
        smoothing_iterations: int = 10,
        smoothing_step: float = 0.5,
        pin_border_vertices: bool = True,
        name: Optional[str] = None,
    ) -> str:
        """Add a deltaMush finishing pass (softens residual skinning artifacts).

        Returns:
            (str) The deltaMush node.
        """
        kwargs = dict(
            smoothingIterations=smoothing_iterations,
            smoothingStep=smoothing_step,
            pinBorderVertices=pin_border_vertices,
        )
        if name:
            kwargs["name"] = name
        result = cmds.deltaMush(str(mesh), **kwargs)
        return result[0] if isinstance(result, (list, tuple)) else result

    @classmethod
    @CoreUtils.undoable
    def bind_to_curve(
        cls,
        mesh,
        joints,
        curve: Optional[str] = None,
        centerline: Optional[Sequence] = None,
        profile: Union[str, Callable] = "smoothstep",
        degree: int = 3,
        skinning_method: str = "dqs",
        max_influences: Optional[int] = None,
        name: Optional[str] = None,
        **bind_kwargs,
    ) -> str:
        """One-call precision bind for tube-like meshes.

        Solves analytic arc-length weights along the curve/centerline
        (CurveWeights.solve — ring-uniform, a C2-smooth cubic basis by
        default; see *degree*), binds, and writes the solved weights in one
        batched call. Defaults to dual quaternion skinning for
        volume-preserving bends and twist. *max_influences* defaults to
        ``degree + 1`` (what the solve produces).

        Returns:
            (str) The skinCluster name.
        """
        joints = [str(j) for j in (joints if isinstance(joints, (list, tuple)) else [joints])]
        # Solve first: fails fast (bad joints/curve) before creating the cluster.
        weights, influences = CurveWeights.solve(
            mesh, joints, curve=curve, centerline=centerline, profile=profile, degree=degree
        )
        if max_influences is None:
            max_influences = CurveWeights.effective_degree(degree, len(joints)) + 1
        skin_cluster = cls.bind(
            mesh,
            joints,
            skinning_method=skinning_method,
            max_influences=max_influences,
            name=name,
            **bind_kwargs,
        )
        # Cluster was created in this same undo chunk: the non-undoable batch
        # write is safe (undoing the chunk deletes the deformer entirely).
        cls.set_weights(
            skin_cluster, weights, influences=influences, normalize=True, undoable=False
        )
        return skin_cluster
