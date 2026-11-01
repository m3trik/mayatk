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

# Eager: ``CoreUtils.undoable`` decorates ``cleanup_meshes`` while the class body runs.
from mayatk.core_utils._core_utils import CoreUtils

# Type aliases keep Maya stubs optional during static analysis
NodeLike = Union[str, object]
NodeSeq = Union[NodeLike, Sequence[NodeLike]]


class _MeshDiagnosticsInternal(object):
    """Internal helpers for MeshDiagnostics."""

    #: Mesh Cleanup's check names (``cleanup_meshes(checks=...)``) -> the
    #: :meth:`MeshDiagnostics.clean_geometry` (``polyCleanupArgList``) flag that
    #: runs each. Twin of blendertk's ``_CLEANUP_CHECKS``.
    _CLEANUP_FLAGS = {
        "ngons": "nsided",
        "nonplanar": "nonplanar",
        "quads": "quads",
        "concave": "concave",
        "holed": "holed",
        "zero_area_faces": "zeroGeom",
        "zero_length_edges": "zeroEdge",
        "zero_uv_area": "zeroMap",
        "shared_uvs": "sharedUVs",
        "nonmanifold": "nonmanifold",
        "lamina": "lamina",
    }

    @staticmethod
    def _cleanup_pool(scope: str) -> list:
        """The mesh transforms Mesh Cleanup's *scope* names: ``"selected"`` the
        selected transforms; ``"visible"`` / ``"all"`` the transforms of every
        visible / every mesh shape, intermediate shapes skipped, each once."""
        if scope == "selected":
            return cmds.ls(selection=True, transforms=True) or []
        kwargs = {"type": "mesh"}
        if scope == "visible":
            kwargs["visible"] = True
        shapes = [
            s
            for s in (cmds.ls(**kwargs) or [])
            if not cmds.getAttr(f"{s}.intermediateObject")
        ]
        if not shapes:
            return []
        transforms = cmds.listRelatives(shapes, parent=True, fullPath=True) or []
        return list(dict.fromkeys(transforms))  # each once, in order

    @staticmethod
    def _poly_counts(objects) -> tuple:
        """``(vertices, faces)`` across *objects* (``polyEvaluate``); an empty or
        incompatible set counts 0 (``polyEvaluate`` answers it with a string)."""

        def _count(**kw):
            try:
                value = cmds.polyEvaluate(objects, **kw)
            except (RuntimeError, ValueError):
                return 0
            return value if isinstance(value, int) else 0

        return _count(vertex=True), _count(face=True)


class MeshDiagnostics(_MeshDiagnosticsInternal):
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

    @classmethod
    @CoreUtils.undoable
    def cleanup_meshes(
        cls,
        scope: str = "selected",
        repair: bool = False,
        *,
        checks=(),
        area_tolerance: float = 0.00001,
        edge_length_tolerance: float = 0.00001,
        uv_area_tolerance: float = 0.00001,
        merge_vertices: bool = False,
        merge_distance: float = 0.0001,
        delete_history: bool = False,
        delete_loose: bool = False,
        dissolve_degenerate: bool = False,
        recalculate_normals: bool = False,
        fill_holes: bool = False,
        overlapping_faces: bool = False,
        overlapping_objects: bool = False,
        omit_selected: bool = False,
    ) -> dict:
        """Mesh Cleanup: select, or repair, common topology problems on the meshes
        *scope* names -- one undo step. Twin of blendertk's
        ``MeshDiagnostics.cleanup_meshes``: the call a panel shared by both hosts
        makes, wording the report itself.

        Parameters:
            scope (str): ``"selected"``, ``"visible"`` or ``"all"`` meshes.
            repair (bool): Repair in place; False selects the matches.
            checks (iterable): The checks to run, Maya's Mesh Cleanup options:
                ``ngons``, ``nonplanar``, ``quads``, ``concave``, ``holed``,
                ``zero_area_faces``, ``zero_length_edges``, ``zero_uv_area``,
                ``shared_uvs``, ``nonmanifold``, ``lamina``.
            area_tolerance / edge_length_tolerance / uv_area_tolerance (float):
                The zero-area, zero-length and zero-UV-area thresholds.
            merge_vertices (bool): Repair: first merge vertices closer than
                *merge_distance*.
            delete_history (bool): First bake the non-deformer history.
            delete_loose / dissolve_degenerate / recalculate_normals / fill_holes
                (bool): Blender's repair passes, taken for the shared panel;
                ``polyCleanup`` repairs what the checks find instead.
            overlapping_faces (bool): Also find faces coincident with another:
                repair deletes them, select adds them to the selection.
            overlapping_objects (bool): Find duplicate objects overlapping in the
                scene instead (the scope is not read): repair deletes them, select
                selects them; with a selection, only its duplicates.
            omit_selected (bool): With *overlapping_objects*, keep the selected
                objects (report their duplicates only).

        Returns:
            dict: ``objects`` (the meshes in scope; empty: nothing done), ``checks``
            (the checks run), ``duplicates`` (how many overlapping objects; None
            unless searched), ``overlapping_faces`` (how many), ``problems``
            (matches per check: ``{}``, ``polyCleanup`` selects them together),
            ``selected`` (the matched components selected), ``repairs`` (the
            passes run), ``vertices`` / ``faces`` (``(before, after)``, repair).
            Select with no check and no *overlapping_faces* does nothing.

        Raises:
            RuntimeError / ValueError: Maya's report from a pass.
        """
        from mayatk.edit_utils._edit_utils import EditUtils

        report = {
            "objects": [],
            "checks": [],
            "duplicates": None,
            "overlapping_faces": 0,
            "problems": {},
            "selected": 0,
            "repairs": [],
            "vertices": (0, 0),
            "faces": (0, 0),
        }
        if overlapping_objects:
            found = sorted(
                EditUtils.get_overlapping_duplicates(
                    retain_given_objects=omit_selected, select=not repair, verbose=True
                )
            )
            if repair and found:
                cmds.delete(found)
            report["duplicates"] = len(found)
            return report

        pool = cls._cleanup_pool(scope)
        wanted = set(checks)
        report["objects"] = pool
        report["checks"] = [name for name in cls._CLEANUP_FLAGS if name in wanted]
        if not pool or not (repair or report["checks"] or overlapping_faces):
            return report

        faces = (EditUtils.get_overlapping_faces(pool) or []) if overlapping_faces else []
        report["overlapping_faces"] = len(faces)
        if repair and faces:
            cmds.delete(faces)  # reported apart, so counted before ``before``
        if not (repair or report["checks"]):  # overlapping faces alone
            cmds.select(faces, replace=True) if faces else cmds.select(clear=True)
            return report

        before = cls._poly_counts(pool) if repair else (0, 0)
        if repair and merge_vertices:
            EditUtils.merge_vertices(pool, tolerance=merge_distance)
        matched = []
        if report["checks"] or delete_history:  # else polyCleanup has nothing to do
            matched = cls.clean_geometry(
                pool,
                repair=repair,
                zeroGeomTol=area_tolerance,
                zeroEdgeTol=edge_length_tolerance,
                zeroMapTol=uv_area_tolerance,
                bakePartialHistory=delete_history,
                **{
                    flag: name in report["checks"]
                    for name, flag in cls._CLEANUP_FLAGS.items()
                },
            )
        if repair:
            after = cls._poly_counts(pool)
            report["vertices"], report["faces"] = (
                (before[0], after[0]),
                (before[1], after[1]),
            )
            report["repairs"] = [
                label
                for on, label in (
                    (delete_history, "delete history"),
                    (merge_vertices, "merge vertices"),
                    (bool(report["checks"]), "polygon cleanup"),
                )
                if on
            ]
            return report
        # Select: polyCleanup left its matches selected; the overlapping faces join them.
        report["selected"] = len(matched or [])
        if faces:
            cmds.select(faces, add=True)
        return report

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
