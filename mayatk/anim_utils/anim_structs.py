# coding=utf-8
"""Shared data structures for animation utilities."""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Union


@dataclass
class ScaleOperation:
    """Represents a scaleKey command."""

    curves: List[Any]
    pivot: float
    factor: float
    time_range: Optional[Tuple[float, float]] = None


@dataclass
class MoveOperation:
    """Represents a move (cut/paste or explicit set) operation."""

    curve: Any
    time_pairs: List[Tuple[float, float]]  # List of (old_time, new_time)


@dataclass
class ShiftOperation:
    """Represents a relative time shift."""

    curves: List[Any]
    offset: float
    time_range: Optional[Tuple[float, float]] = None


@dataclass
class AnimPlan:
    """Base class for animation operation plans."""

    operations: List[Union[ScaleOperation, MoveOperation, ShiftOperation]] = field(
        default_factory=list
    )
    keys_affected: int = 0
    processed_objects: int = 0
    description: str = ""
