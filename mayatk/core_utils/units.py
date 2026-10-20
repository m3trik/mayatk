# !/usr/bin/python
# coding=utf-8
"""Maya's lengths and angles, converted by the kind of query that read them.

Maya answers a length in one of two units, by HOW it is asked:

* its INTERNAL unit, the centimetre, whatever the working unit: the API
  (``MFnMesh``, ``MTransformationMatrix``), ``xform -matrix``, ``getAttr`` of a
  matrix, and a distance a renderer reads straight off a plain float
  (``aiRadius``, an area light's local 2x2 plate);
* the WORKING unit (``cmds.currentUnit``): ``getAttr`` of a linear attribute,
  ``xform -translation``, ``exactWorldBoundingBox``.

Angles split the same way: radians inside, the working angle unit (degrees
unless the preferences say otherwise) through ``getAttr``. A value stored as a
plain number (a stamp, a record) holds whatever unit was current when it was
written, so it must say which.

The Scene Exporter switches the working unit (``set_linear_unit``) before it
produces the records, so a value and a unit read apart disagree: a metre export
once labelled every centimetre light position metres. Each conversion here
names the kind of query that produced the value and runs at the moment the
value is read. A deliverable record ships metres (``ptk.SceneRecords``).
"""
import contextlib
import math
from typing import Any, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk


class Units(ptk.HelpMixin):
    """Lengths and angles between Maya's units and the ones a deliverable ships.

    A *source* or *target* is ``"internal"`` (the centimetre; radians for an
    angle), ``"working"`` (the working unit now) or a unit name -- ``"cm"``,
    ``"m"``, ``"in"`` ... (:attr:`ptk.MathUtils.LENGTH_UNIT_FACTORS`), or
    ``"deg"``, ``"rad"``, ``"min"``, ``"sec"`` for an angle. Values are a
    number or a (nested) sequence of numbers; ``None`` passes through.
    """

    #: Metres in Maya's internal linear unit, the centimetre.
    INTERNAL_M: float = 0.01
    #: Radians in one of each angle unit Maya can work in.
    RADIANS_PER: dict = {
        "rad": 1.0,
        "deg": math.pi / 180.0,
        "min": math.pi / 10800.0,
        "sec": math.pi / 648000.0,
    }

    @staticmethod
    def working_as(
        linear: Optional[str] = "cm", angle: Optional[str] = "deg"
    ) -> "_WorkingAs":
        """Maya's working units set to *linear* and *angle* for a block or a
        call (a context manager and a decorator), and put back after; ``None``
        leaves that one as it is.

        Inside, Maya takes and answers every value in those units, and a MEL
        expression or a connection made inside fixes its conversion to them
        (measured: an expression made in metres evaluates in metres after a
        switch to centimetres). So a tool whose stored numbers are in fixed
        units -- the articulated rig's plan, in centimetres and degrees -- runs
        right in a scene that works in any. A unit switch is not undoable:
        undo and redo replay the block's edits and leave the user's units be.
        """
        return _WorkingAs(linear, angle)

    @staticmethod
    def working(kind: str = "linear") -> str:
        """The working unit now: ``"cm"``, ``"m"`` ... (*kind* ``"linear"``) or
        ``"deg"``, ``"rad"`` ... (``"angle"``)."""
        return str(cmds.currentUnit(query=True, **{kind: True}))

    @classmethod
    def metres_per(cls, unit: str = "internal") -> float:
        """Metres in one *unit* (``"internal"``, ``"working"`` or a unit name).

        Raises:
            ValueError: An unknown unit name.
        """
        if unit == "internal":
            return cls.INTERNAL_M
        name = cls.working() if unit == "working" else unit
        factor = ptk.MathUtils.metres_per_unit(name)
        if factor is None:
            raise ValueError(f"Unknown length unit {name!r}.")
        return factor

    @classmethod
    def convert(cls, value: Any, source: str = "internal", target: str = "m") -> Any:
        """A length (or lengths) from *source* units to *target* units.

        Example:
            Units.convert(m[12:15])  # a world matrix's translation, in metres
            Units.convert(1.0, "m", "working")  # a metre, as cmds.xform takes it
        """
        return cls._scaled(value, cls.metres_per(source) / cls.metres_per(target))

    @classmethod
    def angle(cls, value: Any, source: str = "working", target: str = "deg") -> Any:
        """An angle (or angles) from *source* units to *target* units.

        Example:
            Units.angle(cmds.getAttr(f"{spot}.coneAngle"), target="rad")
        """
        return cls._scaled(value, cls._radians_per(source) / cls._radians_per(target))

    @classmethod
    def _radians_per(cls, unit: str) -> float:
        name = "rad" if unit == "internal" else unit
        if name == "working":
            name = cls.working("angle")
        if name not in cls.RADIANS_PER:
            raise ValueError(f"Unknown angle unit {name!r}.")
        return cls.RADIANS_PER[name]

    @classmethod
    def _scaled(cls, value: Any, factor: float) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [cls._scaled(v, factor) for v in value]
        return float(value) * factor


class _WorkingAs(contextlib.ContextDecorator):
    """:meth:`Units.working_as`: re-entrant, a no-op when the units already are."""

    def __init__(self, linear: Optional[str], angle: Optional[str]):
        self.units = (linear, angle)
        self._saved = []

    def __enter__(self) -> "_WorkingAs":
        current = (Units.working("linear"), Units.working("angle"))
        wanted = tuple(w or c for w, c in zip(self.units, current))
        self._saved.append(current if current != wanted else None)
        if current != wanted:
            cmds.currentUnit(linear=wanted[0], angle=wanted[1])
        return self

    def __exit__(self, *exc) -> bool:
        saved = self._saved.pop()
        if saved is not None:
            cmds.currentUnit(linear=saved[0], angle=saved[1])
        return False
