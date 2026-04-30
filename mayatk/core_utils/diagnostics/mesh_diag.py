# !/usr/bin/python
# coding=utf-8
"""Mesh diagnostics and repair helpers."""
from __future__ import annotations
from typing import Optional, Sequence, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)

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
    ) -> None:
        """Select or remove unwanted geometry from a mesh via ``polyCleanupArgList``."""

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
        cmds.select(objects)

    @staticmethod
    def get_ngons(objects: Optional[NodeSeq], repair: bool = False):
        """Find N-gons and optionally convert them to quads."""

        cmds.select(objects)
        mel.eval("changeSelectMode 1")
        cmds.selectType(smp=0, sme=1, smf=0, smu=0, pv=0, pe=1, pf=0, puv=0)
        cmds.polySelectConstraint(mode=3, type=0x0008, size=3)
        n_gons = cmds.ls(sl=1)
        cmds.polySelectConstraint(disable=1)

        if repair:
            cmds.polyQuad(n_gons, angle=30, kgb=1, ktb=1, khe=1, ws=1)

        return n_gons
