# !/usr/bin/python
# coding=utf-8
"""Every boundary-mutating shots slot routes its refusal through the guard.

Headless and AST-only, deliberately. ``test_shots_panel.py`` owns this module's
behaviour but is GUI-only (``run_tests.GUI_REQUIRED``) because it builds a real
``MayaUiHandler``; the property checked here is structural, needs no Maya, and
is exactly the kind that regresses silently when a new slot is added by copying
an old one.

``ShotSequencer._reconcile_boundaries`` raises :class:`ShotBoundaryConflict`
BEFORE writing anything when two shots would have to share a sample whose two
poses disagree. Twenty-two public sequencer methods reach it.
``ShotsController._boundary_edit`` is the one place that turns that refusal into
a footer message and discards the restore point; a slot that brackets its edit
with a bare ``store.scene_edit(...)`` instead gets neither -- the exception
escapes to Qt and the restore point is left tagged, so the panel's undo offers
to "restore" a state the scene is already in.

Measured 2026-09-16: three slots did exactly that (``on_shot_end_changed``,
``on_trim_empty``, ``on_trim_all_shots``), and blendertk's twin panel had ten.
"""

import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SLOTS = os.path.join(REPO, "mayatk", "anim_utils", "shots", "shots_slots.py")

#: Public ``ShotSequencer`` methods whose call graph reaches
#: ``_reconcile_boundaries``. Derived once by walking the engine's call graph;
#: :meth:`TestRefusingCallsAreGuarded.test_the_refusing_set_is_still_accurate`
#: re-derives it so this list cannot silently go stale.
REFUSING_CALLS = frozenset(
    {
        "add_shot_space",
        "apply_gap",
        "delete_shot",
        "expand_shot",
        "extend_shot_to_fit",
        "fit_shot_to_content",
        "insert_shot",
        "move_object_in_shot",
        "move_sequences_to_shot",
        "move_shot",
        "move_shot_to_position",
        "resize_object",
        "resize_shot",
        "resize_shot_bounds",
        "respace",
        "ripple_downstream",
        "ripple_upstream",
        "set_shot_duration",
        "set_shot_start",
        "slide_shot",
        "split_shot",
        "trim_shot_to_content",
    }
)


def _tree(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _attr_calls(node):
    """Every ``x.name(...)`` attribute-call name inside *node*."""
    return {
        c.func.attr
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
    }


def _reaching(path, target):
    """Method names in *path* whose self-call graph reaches *target*."""
    tree = _tree(path)
    graph = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                graph[fn.name] = {
                    c.func.attr
                    for c in ast.walk(fn)
                    if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and isinstance(c.func.value, ast.Name)
                    and c.func.value.id == "self"
                }
    reach = {target}
    changed = True
    while changed:
        changed = False
        for name, calls in graph.items():
            if name not in reach and (calls & reach):
                reach.add(name)
                changed = True
    return reach


class TestRefusingCallsAreGuarded(unittest.TestCase):
    def test_the_refusing_set_is_still_accurate(self):
        """Re-derive REFUSING_CALLS from the engine so it cannot go stale."""
        engine = os.path.join(
            REPO,
            "mayatk",
            "anim_utils",
            "shots",
            "shot_sequencer",
            "_shot_sequencer.py",
        )
        if not os.path.isfile(engine):
            self.skipTest("engine module not found")
        derived = {
            n
            for n in _reaching(engine, "_reconcile_boundaries")
            if not n.startswith("_")
        }
        self.assertEqual(
            derived,
            set(REFUSING_CALLS),
            "the set of engine calls that can refuse has changed; update "
            "REFUSING_CALLS and re-check every slot that calls the new ones",
        )

    def test_every_slot_calling_a_refusing_method_uses_boundary_edit(self):
        tree = _tree(SLOTS)
        offenders = []
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name == "_boundary_edit":
                    continue
                calls = _attr_calls(fn)
                refusing = calls & REFUSING_CALLS
                if refusing and "_boundary_edit" not in calls:
                    offenders.append(
                        "%s (line %d) calls %s"
                        % (fn.name, fn.lineno, ", ".join(sorted(refusing)))
                    )
        self.assertEqual(
            [],
            offenders,
            "these slots can be refused but do not route through "
            "_boundary_edit, so the refusal escapes to Qt and strands the "
            "restore point: %s" % offenders,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
