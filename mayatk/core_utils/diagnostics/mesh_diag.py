# !/usr/bin/python
# coding=utf-8
"""Mesh diagnostics and repair helpers."""

from __future__ import annotations
from typing import Optional, Sequence, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # pragma: no cover - Maya runtime specific
    pass

# Type aliases keep Maya stubs optional during static analysis
NodeLike = Union[str, object]
NodeSeq = Union[NodeLike, Sequence[NodeLike]]


class MeshDiagnostics:
    """Operations for inspecting and fixing common mesh issues."""

    @staticmethod
    def clean_geometry(
        objects: NodeSeq,
        allMeshes: bool = False,
        repair: bool = False,
        quads: bool = False,
        nsided: bool = False,
        concave: bool = False,
        holed: bool = False,
        nonplanar: bool = False,
        zeroGeom: bool = False,
        zeroGeomTol: float = 0.000010,
        zeroEdge: bool = False,
        zeroEdgeTol: float = 0.000010,
        zeroMap: bool = False,
        zeroMapTol: float = 0.000010,
        sharedUVs: bool = False,
        nonmanifold: bool = False,
        lamina: bool = False,
        invalidComponents: bool = False,
        historyOn: bool = True,
        bakePartialHistory: bool = False,
    ) -> list:
        """Select or remove unwanted geometry from a mesh via ``polyCleanupArgList``.

        Returns the matched problem components in ``repair=False`` (select) mode — the selection
        ``polyCleanupArgList`` leaves behind — so callers can report or act on them. Repair mode
        replaces geometry rather than selecting it and returns ``[]``.
        """

        if allMeshes:
            objects = cmds.ls(geometry=True)
        elif not isinstance(objects, (list, tuple, set)):
            objects = [objects]

        objects = [obj for obj in objects if obj] if objects else []
        if not objects:
            raise ValueError(
                "Mesh cleanup requires one or more mesh objects. Select meshes and try again."
            )

        if bakePartialHistory:
            cmds.bakePartialHistory(objects, prePostDeformers=True)

        cmds.select(objects)

        options = [
            int(allMeshes),
            1 if repair else 2,
            int(historyOn),
            int(quads),
            int(nsided),
            int(concave),
            int(holed),
            int(nonplanar),
            int(zeroGeom),
            float(zeroGeomTol),
            int(zeroEdge),
            float(zeroEdgeTol),
            int(zeroMap),
            float(zeroMapTol),
            int(sharedUVs),
            int(nonmanifold),
            int(lamina),
            int(invalidComponents),
        ]

        arg_list = ",".join([f'"{option}"' for option in options])
        command = f"polyCleanupArgList 4 {{{arg_list}}}"

        mel.eval(command)
        if repair:
            # Repair replaces geometry; reselect the objects so downstream ops have a stable
            # selection, and there are no matched components to hand back.
            cmds.select(objects)
            return []
        # Select mode: polyCleanupArgList leaves the matched problem components selected. Keep that
        # selection — the whole point of "select only" — instead of clobbering it by reselecting the
        # objects (the prior behavior, which made Select mode a no-op), and return the components.
        return cmds.ls(selection=True, flatten=True) or []

    @staticmethod
    def get_ngons(objects: Optional[NodeSeq] = None, repair: bool = False) -> list:
        """Find N-gons and optionally convert them to quads.

        Parameters:
            objects: Mesh objects (or components) to inspect. None uses the
                current selection.
            repair: If True, quadrangulate the found N-gons via ``polyQuad``.

        Returns:
            list: The matched N-gon face components. They are left selected —
            same contract as :meth:`clean_geometry`'s select mode.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            raise ValueError(
                "N-gon check requires one or more mesh objects. Select meshes and try again."
            )

        cmds.select(objects)
        mel.eval("changeSelectMode 1")
        cmds.selectType(smp=0, sme=1, smf=0, smu=0, pv=0, pe=1, pf=0, puv=0)
        cmds.polySelectConstraint(mode=3, type=0x0008, size=3)
        # Flattened to individual face components — same granularity as
        # clean_geometry's select-mode return.
        n_gons = cmds.ls(sl=1, flatten=True)
        cmds.polySelectConstraint(disable=1)

        if repair:
            cmds.polyQuad(n_gons, angle=30, kgb=1, ktb=1, khe=1, ws=1)

        return n_gons

    @staticmethod
    def find_non_manifold_vertices(objects: NodeSeq) -> dict:
        """Map each mesh in *objects* to its non-manifold vertices, via ``polyInfo``.

        Native ``polyInfo`` is instant, unlike ``EditUtils.find_non_manifold_vertex``
        whose per-vertex Python scan is too slow for the heavy meshes that trip
        Unfold. The vertex twin of :meth:`UvDiagnostics.find_non_manifold_uvs`.

        Returns:
            dict: ``{mesh_shape: [vertex_components]}`` -- only meshes that have
            any; empty dict when there are none.
        """
        by_mesh = {}
        if not objects:
            return by_mesh
        if not isinstance(objects, (list, tuple, set)):
            objects = [objects]
        for shape in cmds.ls(objects, dag=True, type="mesh", noIntermediate=True) or []:
            verts = cmds.polyInfo(shape, nonManifoldVertices=True) or []
            if verts:
                by_mesh[shape] = cmds.ls(verts, flatten=True)
        return by_mesh

    @classmethod
    def select_non_manifold(cls, objects: NodeSeq) -> tuple:
        """Select what makes *objects* non-manifold: its vertices, else its UVs.

        Unfold rejects non-manifold *UVs* with the same error as bad geometry, so
        when no vertex is flagged the UV scan is what locates the problem. The
        component select mode is switched to match (vertex or UV), so the
        selection is visible.

        Returns:
            tuple: ``(kind, components)`` -- kind ``"vertices"`` or ``"uvs"`` for
            what was selected, or ``(None, [])`` when nothing is non-manifold (the
            selection is left alone).
        """
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        verts = [
            v for vs in cls.find_non_manifold_vertices(objects).values() for v in vs
        ]
        if verts:
            cmds.selectMode(component=True)
            cmds.selectType(vertex=True)
            cmds.select(verts, replace=True)
            return "vertices", verts
        uvs = [
            uv
            for us in UvDiagnostics.find_non_manifold_uvs(objects).values()
            for uv in us
        ]
        if uvs:
            cmds.selectMode(component=True)
            cmds.selectType(polymeshUV=True)
            cmds.select(uvs, replace=True)
            return "uvs", uvs
        return None, []

    @classmethod
    def repair_non_manifold(cls, objects: NodeSeq, quiet: bool = False) -> dict:
        """Auto-repair non-manifold geometry AND UVs on *objects*.

        Geometry goes through Mesh Cleanup (:meth:`clean_geometry`); non-manifold
        *UVs* -- which block Unfold with the same error, but which Cleanup cannot
        touch -- are repaired by re-mapping the affected faces
        (:meth:`UvDiagnostics.repair_non_manifold_uvs`). Either step failing is
        logged and survived: the caller's retry then reports what is left.

        Parameters:
            objects: The meshes to repair.
            quiet: Suppress the per-mesh console breakdown.

        Returns:
            dict: ``{"total", "fixed", "remaining"}`` counts of non-manifold
            components (vertices + UVs), for a one-line mention in a result.
        """
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        def log(message):
            if not quiet:
                print(f"# Repair non-manifold: {message} #")

        before_verts = cls.find_non_manifold_vertices(objects)
        before_uvs = UvDiagnostics.find_non_manifold_uvs(objects)
        total = sum(len(v) for v in before_verts.values()) + sum(
            len(v) for v in before_uvs.values()
        )
        for shape, verts in before_verts.items():
            log(f"{shape}: {len(verts)} non-manifold vertex(es)")
        for shape, uvs in before_uvs.items():
            log(f"{shape}: {len(uvs)} non-manifold UV(s)")

        try:
            cls.clean_geometry(objects, repair=True, nonmanifold=True)
        except (RuntimeError, ValueError) as exc:
            log(f"cleanup failed: {exc}")
        # Unconditional: it re-scans internally (no-op on clean meshes), and the
        # pre-scan above can't see UV corruption the Cleanup pass just exposed.
        try:
            UvDiagnostics.repair_non_manifold_uvs(objects)
        except (RuntimeError, ValueError) as exc:
            log(f"UV repair failed: {exc}")

        remaining = sum(
            len(v) for v in cls.find_non_manifold_vertices(objects).values()
        ) + sum(len(v) for v in UvDiagnostics.find_non_manifold_uvs(objects).values())
        fixed = total - remaining
        log(f"repaired {fixed} component(s), {remaining} remaining")
        return {"total": total, "fixed": fixed, "remaining": remaining}
