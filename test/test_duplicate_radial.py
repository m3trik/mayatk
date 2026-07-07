# !/usr/bin/python
# coding=utf-8
"""Tests for edit_utils/duplicate_radial.py — the regroup-on-commit flow.

``DuplicateRadialSlots.regroup_copies`` is the Preview's ``finalize_func``:
it must regroup the committed copies under a fresh ``*_array`` group. It was
a silent no-op in every mode: ``duplicate_radial`` returned names that went
stale the moment ``_finalize_output`` grouped the copies (pre-group ``|name``
world paths) or the suffix pass renamed them (return value dropped), so the
``objExists`` guard always tripped. Fixing the staleness alone would have
exposed a worse defect in the old loop — it deleted the SHARED radial group
while sibling copies were still parented under it.
"""
import unittest

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase

import maya.cmds as cmds
from mayatk.edit_utils.duplicate_radial import DuplicateRadial, DuplicateRadialSlots


class TestRegroupCopies(MayaTkTestCase):
    def _duplicate_and_regroup(self, suffix):
        cube = cmds.polyCube(name="radSrc")[0]
        mapping = DuplicateRadial.duplicate_radial(
            [cube], num_copies=4, suffix=suffix
        )
        # Established stub pattern: the slot body only needs self.copies.
        slots = DuplicateRadialSlots.__new__(DuplicateRadialSlots)
        slots.copies = mapping
        slots.regroup_copies()
        return [c for lst in mapping.values() for c in lst]

    def _assert_regrouped(self, copies):
        # (The mapping entries themselves may legitimately change paths
        # during the regroup — the contract is the OUTCOME below, which can
        # only happen if the mapping was live when regroup_copies ran.)
        # All copies live under one fresh *_array group…
        arrays = [
            t for t in cmds.ls(type="transform") if t.split("|")[-1].endswith("_array")
        ]
        self.assertEqual(len(arrays), 1, f"expected one _array group, got {arrays}")
        children = cmds.listRelatives(arrays[0], children=True) or []
        self.assertEqual(len(children), len(copies), "every copy must be regrouped")
        # …no copy was destroyed (the old loop deleted the shared group while
        # sibling copies were still inside)…
        meshes = cmds.listRelatives(arrays[0], allDescendents=True, type="mesh") or []
        self.assertEqual(len(meshes), len(copies), "every copy's mesh must survive")
        # …and the shared radial group is gone.
        self.assertFalse(
            cmds.ls("*radialGroup*"), "shared radial group must be deleted"
        )

    def test_regroup_after_suffixed_duplicate(self):
        self._assert_regrouped(self._duplicate_and_regroup(suffix=True))

    def test_regroup_after_plain_duplicate(self):
        self._assert_regrouped(self._duplicate_and_regroup(suffix=False))


if __name__ == "__main__":
    unittest.main()
