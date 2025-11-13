# !/usr/bin/python
# coding=utf-8
"""Facade utilities for repair operations."""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Union

from .anim_curve_repair import AnimCurveRepair
from .mesh_repair import MeshRepair

PyNodeLike = Union[str, Any]  # Forward-compatible alias without Maya stubs
PyNodeSeq = Union[PyNodeLike, Sequence[PyNodeLike]]


class Repair:
    """High-level access point for all repair helpers."""

    def repair_corrupted_curves(
        self,
        objects: Optional[Union[PyNodeLike, Sequence[PyNodeLike]]] = None,
        recursive: bool = True,
        delete_corrupted: bool = False,
        fix_infinite: bool = True,
        fix_invalid_times: bool = True,
        time_range_threshold: float = 1e6,
        value_threshold: float = 1e6,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Delegate to :class:`AnimCurveRepair` for curve corruption fixes."""

        return AnimCurveRepair.repair_corrupted_curves(
            objects=objects,
            recursive=recursive,
            delete_corrupted=delete_corrupted,
            fix_infinite=fix_infinite,
            fix_invalid_times=fix_invalid_times,
            time_range_threshold=time_range_threshold,
            value_threshold=value_threshold,
            quiet=quiet,
        )

    def clean_geometry(
        self,
        objects: PyNodeSeq,
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
        MeshRepair.clean_geometry(
            objects=objects,
            allMeshes=allMeshes,
            repair=repair,
            quads=quads,
            nsided=nsided,
            concave=concave,
            holed=holed,
            nonplanar=nonplanar,
            zeroGeom=zeroGeom,
            zeroGeomTol=zeroGeomTol,
            zeroEdge=zeroEdge,
            zeroEdgeTol=zeroEdgeTol,
            zeroMap=zeroMap,
            zeroMapTol=zeroMapTol,
            sharedUVs=sharedUVs,
            nonmanifold=nonmanifold,
            lamina=lamina,
            invalidComponents=invalidComponents,
            historyOn=historyOn,
            bakePartialHistory=bakePartialHistory,
        )

    def get_ngons(
        self,
        objects: Optional[PyNodeSeq],
        repair: bool = False,
    ):
        return MeshRepair.get_ngons(objects, repair=repair)


repair = Repair()
