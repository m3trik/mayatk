# !/usr/bin/python
# coding=utf-8
"""The end control's solve as a Maya expression: a MEL port of
``ptk.ArticulationModel.solve``, written out for one rig.

The end control has to move the stand in ANY Maya that opens the scene --
nothing to install, nothing to trust, no prompt -- so its solve is plain MEL in
an ``expression`` node, the one evaluator every Maya carries. The text is
generated from the rig's joint model: the chain from the root to the held
joint is unrolled with its rest frames, rotate orders, limits and weights
inlined, and the model's damped least squares runs as written (same steps,
same constants, the same wrist split for a ball-mounted end), so a pose keyed
on the end control is the pose the Grab Tool and the Unity and WebXR hand grab
make. Like every port, it is pinned against the model by golden cases
(``test_articulated_rig.py``).

The expression reads the end control in rig space (a ``decomposeMatrix``), its
``ikBlend`` and ``followRotation``, and the FK controls' channels -- the
solve's seed -- and writes, per channel of the chain, how far the solve moves
it from FK::

    offset = blend * (solve(seed=FK, target) - FK)

Measured on the production magnifier the whole solve runs in about a
millisecond (MEL expressions evaluate at roughly 0.06 us a statement).
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

import pythontk as ptk


class _MelText:
    """Lines of MEL, and every variable they use, declared once at the top (MEL
    refuses a second declaration in one scope; a loop body is one)."""

    def __init__(self):
        self._lines: List[str] = []
        self._kinds: Dict[str, str] = {}
        self._depth = 0

    def var(self, name: str, kind: str = "float") -> str:
        """``$name``, declared as *kind* (``float`` / ``vector`` / ``int``)."""
        known = self._kinds.setdefault(name, kind)
        if known != kind:
            raise ValueError(f"${name} is a {known}, not a {kind}.")
        return f"${name}"

    def __call__(self, line: str) -> None:
        self._lines.append("    " * self._depth + line)

    @contextmanager
    def block(self, head: str) -> Iterator[None]:
        self(head + " {")
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            self("}")

    def text(self) -> str:
        declared = [f"{kind} ${name};" for name, kind in self._kinds.items()]
        return "\n".join(declared + self._lines) + "\n"


class _SolverExpressionInternal:
    """MEL literals and the model's quaternion algebra, written as statements.

    A quaternion is a vector (its ``x, y, z``) and a float (its ``w``); ``a
    (x) b`` applies ``b`` first, as in the model.
    """

    #: ``math.degrees(1.0)`` and ``math.radians(1.0)``: the model's units.
    DEGREES = math.degrees(1.0)
    RADIANS = math.radians(1.0)

    @staticmethod
    def _num(value: float) -> str:
        """*value* as a MEL float literal that reads back exactly: always with
        a point (``1`` is an int, and ``1 / 2`` is 0), a negative in brackets
        (``a - -1`` is a parse error)."""
        text = repr(float(value))
        if "e" in text and "." not in text:
            text = text.replace("e", ".0e")
        elif "." not in text and "e" not in text:
            text += ".0"
        return f"({text})" if text.startswith("-") else text

    @classmethod
    def _vec(cls, v: Sequence[float]) -> str:
        return "<<" + ", ".join(cls._num(c) for c in v) + ">>"

    @staticmethod
    def _axis(qv: str, qw: str, axis: str) -> str:
        """The unit *axis* (``x`` / ``y`` / ``z``) rotated by the quaternion
        ``(qv, qw)``: that column of its matrix."""
        x, y, z, w = f"{qv}.x", f"{qv}.y", f"{qv}.z", qw
        return {
            "x": f"<<1.0 - 2.0 * ({y} * {y} + {z} * {z}), "
            f"2.0 * ({x} * {y} + {z} * {w}), 2.0 * ({x} * {z} - {y} * {w})>>",
            "y": f"<<2.0 * ({x} * {y} - {z} * {w}), "
            f"1.0 - 2.0 * ({x} * {x} + {z} * {z}), 2.0 * ({y} * {z} + {x} * {w})>>",
            "z": f"<<2.0 * ({x} * {z} + {y} * {w}), "
            f"2.0 * ({y} * {z} - {x} * {w}), 1.0 - 2.0 * ({x} * {x} + {y} * {y})>>",
        }[axis]

    @staticmethod
    def _entry(qv: str, qw: str, row: int, col: int) -> str:
        """One entry of the quaternion ``(qv, qw)``'s rotation matrix (the
        model's ``_qmatrix``)."""
        x, y, z, w = f"{qv}.x", f"{qv}.y", f"{qv}.z", qw
        return [
            [
                f"1.0 - 2.0 * ({y} * {y} + {z} * {z})",
                f"2.0 * ({x} * {y} - {z} * {w})",
                f"2.0 * ({x} * {z} + {y} * {w})",
            ],
            [
                f"2.0 * ({x} * {y} + {z} * {w})",
                f"1.0 - 2.0 * ({x} * {x} + {z} * {z})",
                f"2.0 * ({y} * {z} - {x} * {w})",
            ],
            [
                f"2.0 * ({x} * {z} - {y} * {w})",
                f"2.0 * ({y} * {z} + {x} * {w})",
                f"1.0 - 2.0 * ({x} * {x} + {y} * {y})",
            ],
        ][row][col]

    @staticmethod
    def _qmul(mel: _MelText, out: str, a: Sequence[str], b: Sequence[str]) -> None:
        """``out = a (x) b`` (*out* a name stem: ``$<out>v``, ``$<out>w``)."""
        (av, aw), (bv, bw) = a, b
        ov, ow = mel.var(out + "v", "vector"), mel.var(out + "w")
        tmp = mel.var("qmw")
        mel(f"{tmp} = {aw} * {bw} - ({av} * {bv});")
        mel(f"{ov} = {aw} * {bv} + {bw} * {av} + ({av} ^ {bv});")
        mel(f"{ow} = {tmp};")

    @staticmethod
    def _qrot(mel: _MelText, out: str, qv: str, qw: str, v: str) -> None:
        """``out = v`` rotated by the unit quaternion ``(qv, qw)``."""
        t = mel.var("qrt", "vector")
        mel(f"{t} = 2.0 * ({qv} ^ {v});")
        mel(f"{out} = {v} + {qw} * {t} + ({qv} ^ {t});")


class SolverExpression(_SolverExpressionInternal):
    """The MEL text of one rig's end-control solve.

    Parameters:
        rig: The rig's joints as the ``articulation`` record ships them
            (``{"name", "joints": [...]}``, see ``ptk.ArticulationModel``).
        end: The index of the joint whose link the end control holds.
        pivot: The held point -- the end control's pivot -- in that joint's
            frame.
        turn: The held link's rotation in the end control's frame, ``(x, y,
            z, w)``: a ball-mounted link following the control turns to
            ``control (x) turn``.
        plugs: Where the expression reads and writes: ``"translate"`` (three
            plugs) and ``"quat"`` (four) -- the end control in rig space;
            ``"blend"`` and ``"follow"`` (None: no follow switch, a hinged
            end); ``"seeds"``, one plug per model slot (the FK channel);
            ``"outputs"``, a list of plugs per model slot (what takes its
            offset).
    """

    def __init__(
        self,
        rig: Mapping[str, Any],
        end: int,
        pivot: Sequence[float],
        turn: Sequence[float],
        plugs: Mapping[str, Any],
    ):
        self.model = ptk.ArticulationModel(rig)
        if not 0 <= end < len(self.model.joints):
            raise ValueError(f"No joint {end} in rig {self.model.name!r}.")
        self.end = end
        self.pivot = tuple(float(v) for v in pivot)
        self.turn = tuple(float(v) for v in turn)
        self.plugs = plugs
        # Per slot, the record's weight as the model reads it (every port
        # parses the record itself).
        self.weights: List[float] = []
        for joint in rig.get("joints") or []:
            for entry in joint.get("channels") or []:
                weight = entry.get("weight")
                self.weights.append(max(0.0, 1.0 if weight is None else float(weight)))
        self._slots: Dict[int, List[int]] = {}
        for slot, (joint, _channel) in enumerate(self.model.channels):
            self._slots.setdefault(joint, []).append(slot)

    @classmethod
    def text_for(
        cls,
        rig: Mapping[str, Any],
        end: int,
        pivot: Sequence[float],
        turn: Sequence[float],
        plugs: Mapping[str, Any],
    ) -> str:
        """The expression text (see the class for the parameters)."""
        return cls(rig, end, pivot, turn, plugs).text()

    # ----------------------------------------------------------------- model
    def _chain_slots(self, joint: int) -> List[int]:
        return [s for j in self.model.chain(joint) for s in self._slots.get(j, [])]

    def _channel(self, slot: int) -> str:
        return self.model.channels[slot][1]

    def _bone_length(self, chain: Sequence[int]) -> float:
        """The model's ``_reach`` without the held point's lever."""
        return sum(
            math.sqrt(sum(c * c for c in self.model.joints[j]["t"])) for j in chain[1:]
        )

    def _clamped(self, slot: int, value: str) -> str:
        lo, hi = self.model.limits(slot)
        if lo is not None and hi is not None:
            return f"clamp({self._num(lo)}, {self._num(hi)}, {value})"
        if lo is not None:
            return f"max({self._num(lo)}, {value})"
        if hi is not None:
            return f"min({self._num(hi)}, {value})"
        return value

    # ------------------------------------------------------------------ text
    def text(self) -> str:
        mel = _MelText()
        plugs = self.plugs
        chain = self._chain_slots(self.end)
        mel("// The articulated rig's end control: a MEL port of")
        mel("// pythontk ArticulationModel.solve, generated by mayatk. Edit the")
        mel("// rig, not this text: every rebuild writes it again.")
        blend = mel.var("bl")
        mel(f"{blend} = clamp(0.0, 1.0, {plugs['blend']});")
        for slot in chain:
            mel(f"{mel.var(f'f{slot}')} = {plugs['seeds'][slot]};")
            mel(f"{mel.var(f'o{slot}')} = 0.0;")
        target = mel.var("tg", "vector")
        tx, ty, tz = plugs["translate"]
        mel(f"{target} = <<{tx}, {ty}, {tz}>>;")
        qx, qy, qz, qw = plugs["quat"]
        cqv, cqw = mel.var("cqv", "vector"), mel.var("cqw")
        mel(f"{cqv} = <<{qx}, {qy}, {qz}>>;")
        mel(f"{cqw} = {qw};")
        follow = plugs.get("follow")
        if follow:
            mel(f"{mel.var('fr', 'int')} = {follow};")
        with mel.block(f"if ({blend} > 0.0)"):
            for slot in chain:
                mel(f"$v{slot} = {self._clamped(slot, f'$f{slot}')};")
                mel.var(f"v{slot}")
            self._solve(mel, bool(follow))
            for slot in chain:
                mel(f"$o{slot} = {blend} * ($v{slot} - $f{slot});")
        for slot in chain:
            for plug in plugs["outputs"][slot]:
                mel(f"{plug} = $o{slot};")
        return mel.text()

    def _solve(self, mel: _MelText, follow: bool) -> None:
        """The end control's solve, by the end link's joint: a ball following
        the control takes the model's wrist split (:meth:`_wrist`), a ball
        keeping its own turn is carried by its parent's link (:meth:`_carry`),
        and any other end is placed by position alone (the model ignores a
        rotation it cannot take)."""
        spec = self.model.joints[self.end]
        turns = {self._channel(s) for s in self._slots.get(self.end, [])}
        ball = {"rx", "ry", "rz"} <= turns
        pivot = mel.var("piv", "vector")
        mel(f"{pivot} = {self._vec(self.pivot)};")
        reach = self._num(
            max(
                math.sqrt(sum(c * c for c in self.pivot))
                + self._bone_length(self.model.chain(self.end)),
                self.model.EPS,
            )
        )
        if not ball:
            self._dls(mel, self._chain_slots(self.end), self.end, pivot, "$tg", reach)
            return
        if follow:
            with mel.block("if ($fr)"):
                self._wrist(mel, pivot, reach)
            with mel.block("else"):
                self._carry(mel, spec["parent"], pivot)
        else:
            self._wrist(mel, pivot, reach)

    def _wrist(self, mel: _MelText, pivot: str, reach: str) -> None:
        """The wrist split: the ball takes the control's rotation, the chain
        above places its centre, and a last position solve closes the rest."""
        model = self.model
        rv, rw = mel.var("Rv", "vector"), mel.var("Rw")
        self._qmul(
            mel,
            "R",
            ("$cqv", "$cqw"),
            (self._vec(self.turn[:3]), self._num(self.turn[3])),
        )
        norm = mel.var("rn")
        mel(f"{norm} = sqrt({rv} * {rv} + {rw} * {rw});")
        with mel.block(f"if ({norm} < 1.0e-12)"):
            mel(f"{rv} = <<0.0, 0.0, 0.0>>;")
            mel(f"{rw} = 1.0;")
        with mel.block("else"):
            mel(f"{rv} = {rv} * (1.0 / {norm});")
            mel(f"{rw} = {rw} / {norm};")
        hv, hw = mel.var("Hv", "vector"), mel.var("Hw")
        mel(f"{hv} = {rv};")
        mel(f"{hw} = {rw};")
        rotations = {
            s for s in self._slots.get(self.end, []) if self._channel(s).startswith("r")
        }
        placed = [s for s in self._chain_slots(self.end) if s not in rotations]
        centre, lever = mel.var("cen", "vector"), mel.var("lev", "vector")
        with mel.block(
            f"for ({mel.var('ps', 'int')} = 0; $ps < {model.PASSES}; $ps++)"
        ):
            self._qrot(mel, lever, hv, hw, pivot)
            mel(f"{centre} = $tg - {lever};")
            self._dls(mel, placed, self.end, None, centre, reach)
            self._turn(mel)
            self._fk(mel, self.end, self._values("v"), axes=False)
            mel(f"{hv} = $Q{self.end}v;")
            mel(f"{hw} = $Q{self.end}w;")
        self._dls(mel, self._chain_slots(self.end), self.end, pivot, "$tg", reach)

    def _carry(self, mel: _MelText, parent: Optional[int], pivot: str) -> None:
        """The end link keeps its own (FK) turn and the chain above carries
        it: the held point solved on the parent's link, where it is rigid --
        read off the FK pose, as the model's ``point`` / ``to_local`` do."""
        if parent is None:
            reach = self._num(
                max(math.sqrt(sum(c * c for c in self.pivot)), self.model.EPS)
            )
            self._dls(mel, self._slots.get(self.end, []), self.end, pivot, "$tg", reach)
            return
        self._fk(mel, self.end, self._values("f"), axes=False)
        held = mel.var("hp", "vector")
        self._qrot(mel, held, f"$Q{self.end}v", f"$Q{self.end}w", pivot)
        mel(f"{held} = $P{self.end} + {held};")
        local, back = mel.var("lp", "vector"), mel.var("bkv", "vector")
        mel(f"{back} = -1.0 * $Q{parent}v;")
        mel(f"{local} = {held} - $P{parent};")
        self._qrot(mel, local, back, f"$Q{parent}w", local)
        reach = mel.var("rc")
        mel(
            f"{reach} = max(mag({local}) + "
            f"{self._num(self._bone_length(self.model.chain(parent)))}, "
            f"{self._num(self.model.EPS)});"
        )
        self._dls(mel, self._chain_slots(parent), parent, local, "$tg", reach)

    # -------------------------------------------------------------- forward
    @staticmethod
    def _values(prefix: str) -> Callable[[int], str]:
        """The MEL variable holding each slot's value: ``$<prefix><slot>``."""
        return lambda slot: f"${prefix}{slot}"

    def _fk(
        self, mel: _MelText, upto: int, value: Callable[[int], str], axes: bool
    ) -> None:
        """World position ``$P<j>`` and rotation ``$Q<j>v`` / ``$Q<j>w`` of
        every joint from the root down to *upto*, each channel at
        ``value(slot)``; with *axes*, each channel's world axis ``$A<slot>``
        too (a rotation's axis turns with the channels applied after it)."""
        fv, fw = mel.var("Fv", "vector"), mel.var("Fw")
        for j in self.model.chain(upto):
            spec = self.model.joints[j]
            parent = spec["parent"]
            t, q = self._vec(spec["t"]), spec["q"]
            qv, qw = self._vec(q[:3]), self._num(q[3])
            pos = mel.var(f"P{j}", "vector")
            if parent is None:
                mel(f"{fv} = {qv};")
                mel(f"{fw} = {qw};")
                mel(f"{pos} = {t};")
            else:
                self._qmul(mel, "F", (f"$Q{parent}v", f"$Q{parent}w"), (qv, qw))
                self._qrot(mel, pos, f"$Q{parent}v", f"$Q{parent}w", t)
                mel(f"{pos} = $P{parent} + {pos};")
            slots = self._slots.get(j, [])
            for slot in slots:
                channel = self._channel(slot)
                if channel.startswith("t"):
                    axis = mel.var(f"A{slot}", "vector")
                    mel(f"{axis} = {self._axis(fv, fw, channel[1])};")
                    mel(f"{pos} = {pos} + {value(slot)} * {axis};")
            turns = {
                self._channel(s)[1]: s
                for s in slots
                if self._channel(s).startswith("r")
            }
            for c in reversed(spec["rotate_order"]):
                slot = turns.get(c)
                if slot is None:
                    continue
                if axes:
                    mel(f"{mel.var(f'A{slot}', 'vector')} = {self._axis(fv, fw, c)};")
                half, sn, cs, nw = (mel.var(n) for n in ("hh", "sn", "cs", "nw"))
                mel(f"{half} = ({value(slot)} * {self._num(self.RADIANS)}) * 0.5;")
                mel(f"{sn} = sin({half});")
                mel(f"{cs} = cos({half});")
                # frame (x) axis-angle, the old frame on both sides
                cross = {
                    "x": f"<<{fw}, {fv}.z, -{fv}.y>>",
                    "y": f"<<-{fv}.z, {fw}, {fv}.x>>",
                    "z": f"<<{fv}.y, -{fv}.x, {fw}>>",
                }[c]
                mel(f"{nw} = {fw} * {cs} - {sn} * {fv}.{c};")
                mel(f"{fv} = {cs} * {fv} + {sn} * {cross};")
                mel(f"{fw} = {nw};")
            mel(f"{mel.var(f'Q{j}v', 'vector')} = {fv};")
            mel(f"{mel.var(f'Q{j}w')} = {fw};")

    # ---------------------------------------------------------------- solve
    def _dls(
        self,
        mel: _MelText,
        slots: Sequence[int],
        joint: int,
        point: Optional[str],
        target: str,
        reach: str,
    ) -> None:
        """The model's ``_dls``: move *slots* to bring *point* (a MEL vector
        in *joint*'s frame; None its origin) to *target*, *reach* the length
        its damping, tolerance and step scale by -- each step halved until it
        brings the point nearer, and none doing so the end. A slot of weight
        0 never moves (the model's step gives it nothing), so it is left
        out."""
        model = self.model
        active = [s for s in slots if self.weights[s] > 0.0]
        if not active:
            return
        damp, tol, step = mel.var("dmp"), mel.var("tol"), mel.var("stp")
        mel(
            f"{damp} = ({self._num(model.DAMPING)} * {reach}) * ({self._num(model.DAMPING)} * {reach});"
        )
        mel(f"{tol} = {self._num(model.TOLERANCE)} * {reach};")
        mel(f"{step} = {self._num(model.MAX_STEP)} * {reach};")
        it, halving, nearer = (mel.var(n, "int") for n in ("it", "hn", "ok"))
        pt, err, dist = (
            mel.var("pt", "vector"),
            mel.var("err", "vector"),
            mel.var("dst"),
        )
        tpt, tdist, fraction = (
            mel.var("tpt", "vector"),
            mel.var("tdst"),
            mel.var("frac"),
        )
        moving = set(active)
        trial = lambda slot: f"$t{slot}" if slot in moving else f"$v{slot}"  # noqa: E731
        self._fk(mel, joint, self._values("v"), axes=True)
        self._point(mel, pt, joint, point)
        mel(f"{dist} = mag({target} - {pt});")
        with mel.block(f"for ({it} = 0; {it} < {model.ITERATIONS}; {it}++)"):
            mel(f"if ({dist} <= {tol}) break;")
            mel(f"{err} = {target} - {pt};")
            mel(f"if ({dist} > {step}) {err} = {err} * ({step} / {dist});")
            for slot in active:
                column = mel.var(f"C{slot}", "vector")
                owner = model.channels[slot][0]
                if self._channel(slot).startswith("r"):
                    mel(f"{column} = $A{slot} ^ ({pt} - $P{owner});")
                else:
                    mel(f"{column} = {reach} * $A{slot};")
                mel(f"{mel.var(f'W{slot}')} = {self._num(self.weights[slot])};")
            self._step(mel, active)
            limited = [s for s in active if model.limits(s) != (None, None)]
            if limited:
                blocked = mel.var("blk", "int")
                mel(f"{blocked} = 0;")
                for slot in limited:
                    lo, hi = model.limits(slot)
                    sides = []
                    if lo is not None:
                        sides.append(f"($v{slot} <= {self._num(lo)} && $d{slot} < 0.0)")
                    if hi is not None:
                        sides.append(f"($v{slot} >= {self._num(hi)} && $d{slot} > 0.0)")
                    with mel.block(f"if ({' || '.join(sides)})"):
                        mel(f"$W{slot} = 0.0;")
                        mel(f"{blocked} = 1;")
                with mel.block(f"if ({blocked})"):
                    self._step(mel, active)
            # halved until it brings the point nearer (the trial's pose is
            # the next iteration's, kept in $P / $Q / $A when it is taken)
            mel(f"{fraction} = 1.0;")
            mel(f"{nearer} = 0;")
            with mel.block(
                f"for ({halving} = 0; {halving} <= {model.HALVINGS}; {halving}++)"
            ):
                for slot in active:
                    unit = (
                        self._num(self.DEGREES)
                        if self._channel(slot).startswith("r")
                        else reach
                    )
                    moved = f"$v{slot} + {fraction} * $d{slot} * {unit}"
                    mel(f"{mel.var(f't{slot}')} = {self._clamped(slot, moved)};")
                self._fk(mel, joint, trial, axes=True)
                self._point(mel, tpt, joint, point)
                mel(f"{tdist} = mag({target} - {tpt});")
                with mel.block(f"if ({tdist} < {dist})"):
                    mel(f"{nearer} = 1;")
                    mel("break;")
                mel(f"{fraction} = {fraction} * 0.5;")
            mel(f"if (!{nearer}) break;")
            for slot in active:
                mel(f"$v{slot} = $t{slot};")
            mel(f"{pt} = {tpt};")
            mel(f"{dist} = {tdist};")

    def _point(self, mel: _MelText, out: str, joint: int, point: Optional[str]) -> None:
        """``out`` = *point* (a MEL vector in *joint*'s frame; None its
        origin) in rig space, at the pose ``$P`` / ``$Q`` hold."""
        if point is None:
            mel(f"{out} = $P{joint};")
            return
        self._qrot(mel, out, f"$Q{joint}v", f"$Q{joint}w", point)
        mel(f"{out} = $P{joint} + {out};")

    def _step(self, mel: _MelText, active: Sequence[int]) -> None:
        """One weighted damped-least-squares step, ``du = W J^T (J W J^T +
        lambda^2 I)^-1 e``, into ``$d<slot>``: the normal matrix by its rows,
        inverted by cross products (the model's cofactors)."""
        r0, r1, r2 = (mel.var(n, "vector") for n in ("r0", "r1", "r2"))
        wc, y, det = mel.var("wc", "vector"), mel.var("y", "vector"), mel.var("det")
        mel(f"{r0} = <<$dmp, 0.0, 0.0>>;")
        mel(f"{r1} = <<0.0, $dmp, 0.0>>;")
        mel(f"{r2} = <<0.0, 0.0, $dmp>>;")
        for slot in active:
            mel(f"{wc} = $W{slot} * $C{slot};")
            mel(f"{r0} = {r0} + {wc}.x * $C{slot};")
            mel(f"{r1} = {r1} + {wc}.y * $C{slot};")
            mel(f"{r2} = {r2} + {wc}.z * $C{slot};")
        mel(f"{det} = {r0} * ({r1} ^ {r2});")
        mel(f"if (abs({det}) < 1.0e-300) {y} = <<0.0, 0.0, 0.0>>;")
        mel(
            f"else {y} = (({r1} ^ {r2}) * $err.x + ({r2} ^ {r0}) * $err.y "
            f"+ ({r0} ^ {r1}) * $err.z) * (1.0 / {det});"
        )
        for slot in active:
            mel(f"{mel.var(f'd{slot}')} = $W{slot} * ($C{slot} * {y});")

    def _turn(self, mel: _MelText) -> None:
        """The model's ``_turn``: the ball's channels set so its link's
        rotation is ``$R``, read as the Euler triple nearer the ball's values
        (``_euler_for``), each clamped."""
        model = self.model
        spec = model.joints[self.end]
        parent = spec["parent"]
        q = spec["q"]
        if parent is None:
            gv, gw = self._vec(q[:3]), self._num(q[3])
        else:
            self._fk(mel, parent, self._values("v"), axes=False)
            self._qmul(
                mel,
                "G",
                (f"$Q{parent}v", f"$Q{parent}w"),
                (self._vec(q[:3]), self._num(q[3])),
            )
            gv, gw = "$Gv", "$Gw"
        # local = conj(frame) (x) R
        lv, lw = mel.var("Lv", "vector"), mel.var("Lw")
        mel(f"{lv} = {gw} * $Rv - $Rw * {gv} - ({gv} ^ $Rv);")
        mel(f"{lw} = {gw} * $Rw + ({gv} * $Rv);")
        order = spec["rotate_order"]
        axis = {"x": 0, "y": 1, "z": 2}
        a, b, c = axis[order[2]], axis[order[1]], axis[order[0]]
        s = 1.0 if (b - a) % 3 == 1 else -1.0
        m = {}
        for row, col in {(a, c), (b, c), (c, c), (a, b), (a, a), (c, b), (b, b)}:
            m[(row, col)] = mel.var(f"m{row}{col}")
            mel(f"{m[(row, col)]} = {self._entry(lv, lw, row, col)};")
        sb, ab, aa, ac = (mel.var(n) for n in ("sb", "ab", "aa", "ac"))
        mel(f"{sb} = clamp(-1.0, 1.0, {self._num(s)} * {m[(a, c)]});")
        mel(f"{ab} = asin({sb});")
        with mel.block(f"if (cos({ab}) > 1.0e-6)"):
            mel(f"{aa} = atan2({self._num(-s)} * {m[(b, c)]}, {m[(c, c)]});")
            mel(f"{ac} = atan2({self._num(-s)} * {m[(a, b)]}, {m[(a, a)]});")
        with mel.block("else"):
            mel(f"{ac} = 0.0;")
            mel(f"{aa} = atan2({self._num(s)} * {m[(c, b)]}, {m[(b, b)]});")
        slots = {self._channel(s_): s_ for s_ in self._slots.get(self.end, [])}
        deg = self._num(self.DEGREES)
        first = {
            order[2]: f"{aa} * {deg}",
            order[1]: f"{ab} * {deg}",
            order[0]: f"{ac} * {deg}",
        }
        second = {
            order[2]: f"{aa} * {deg} + 180.0",
            order[1]: f"180.0 - {ab} * {deg}",
            order[0]: f"{ac} * {deg} + 180.0",
        }
        d1, d2 = mel.var("d1"), mel.var("d2")
        mel(f"{d1} = 0.0;")
        mel(f"{d2} = 0.0;")
        for axis_name in "xyz":
            slot = slots["r" + axis_name]
            for tag, triple, drift in (("a", first, d1), ("b", second, d2)):
                raw = mel.var(f"e{tag}{axis_name}")
                mel(f"{raw} = {triple[axis_name]};")
                # unwrap toward the ball's value: a half turn rounds up
                mel(f"{raw} = {raw} + 360.0 * floor(($v{slot} - {raw}) / 360.0 + 0.5);")
                mel(f"{drift} = {drift} + abs({raw} - $v{slot});")
        with mel.block(f"if ({d2} < {d1})"):
            for axis_name in "xyz":
                slot = slots["r" + axis_name]
                mel(f"$v{slot} = {self._clamped(slot, f'$eb{axis_name}')};")
        with mel.block("else"):
            for axis_name in "xyz":
                slot = slots["r" + axis_name]
                mel(f"$v{slot} = {self._clamped(slot, f'$ea{axis_name}')};")
