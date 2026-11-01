# !/usr/bin/python
# coding=utf-8
"""The Maya ``ShotSequencer`` supplies every scene hook pythontk leaves empty.

Headless and structural: it imports the engine, which needs no Maya. A hook
this adapter stops overriding falls back to pythontk's empty-scene default
without an error anywhere -- the edits silently move bounds only. blendertk's
twin is its ``test_shot_sequencer_hooks.py``. The Shots panel and its guard
(every edit that can be refused goes through ``_boundary_edit``, the refusing
engine calls derived from this engine) are tentacle's
(``test/test_shots_panel.py``).
"""

import unittest


class TestSequencerAdapterHooks(unittest.TestCase):
    """The Maya ``ShotSequencer`` supplies the scene hooks pythontk leaves empty.

    ``pythontk.ShotSequencer`` reaches the scene only through the hooks on
    ``_ShotSequencerHooks``, and each default describes an EMPTY scene: no
    keys, no audio, nothing to hold. A hook this adapter does not override is
    therefore no error anywhere -- the panel's edits silently move bounds only.
    blendertk's twin guard is its ``test_shot_sequencer_hooks.py``.
    """

    #: Hooks inherited on purpose. ``_follow_unresolved_claims``: a Maya ledger
    #: key is the anim curve's node name, which renaming its owner leaves as it
    #: is (measured, 2025: ``box`` -> ``crate`` keeps ``box_translateX``), so
    #: there is nothing to follow; blendertk's keys embed the owner's name.
    INHERITED = frozenset({"_follow_unresolved_claims"})

    @staticmethod
    def _classes():
        """``(adapter, pythontk's ShotSequencer, its hook defaults)``."""
        import pythontk as ptk
        from pythontk.core_utils.engines.shots.shot_sequencer import (
            _ShotSequencerHooks,
        )

        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        return ShotSequencer, ptk.ShotSequencer, _ShotSequencerHooks

    def test_every_hook_default_is_overridden(self):
        adapter, core, defaults = self._classes()
        hooks = {
            n for n in vars(defaults) if not (n.startswith("__") and n.endswith("__"))
        } - {"STORE_CLASS"}
        self.assertTrue(hooks, "no hooks read off _ShotSequencerHooks")
        mro = adapter.__mro__
        ahead = mro[: mro.index(core)]
        inherited = {n for n in hooks if not any(n in vars(c) for c in ahead)}
        self.assertEqual(
            [],
            sorted(inherited - self.INHERITED),
            "these hooks resolve to pythontk's empty-scene default, so every "
            "operation reaching them edits bounds only",
        )
        self.assertEqual(
            [],
            sorted(self.INHERITED - inherited),
            "listed in INHERITED but overridden now (or no longer a hook): "
            "drop them from INHERITED",
        )

    def test_no_maya_base_sits_behind_the_hook_defaults(self):
        """A helper base listed after pythontk's class is shadowed by it: a hook
        implemented on an ``_<Class>Internal`` base (the house home for
        helpers) would resolve to the pythontk no-op instead. blendertk's
        bases had exactly that order until 2026-09-27."""
        adapter, core, _ = self._classes()
        mro = adapter.__mro__
        behind = [
            c.__qualname__
            for c in mro[mro.index(core) :]
            if c.__module__.partition(".")[0] == "mayatk"
        ]
        self.assertEqual(
            [],
            behind,
            "these mayatk bases come after pythontk's ShotSequencer in the "
            "MRO, so its hook defaults shadow anything they define",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
