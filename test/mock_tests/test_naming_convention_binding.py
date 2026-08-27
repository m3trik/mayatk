# !/usr/bin/python
# coding=utf-8
"""Naming engine <-> shared convention binding (no Maya, no Qt).

``Naming.SUFFIX_BINDINGS`` is the one host-specific fact -- which Maya node type
each convention entry names. Everything else (the affix spelling, the placement,
the label) comes from ``pythontk.NamingConvention``, so these tests are pure
Python: they cover the join (:meth:`Naming.affix_rules`), the compatibility view
(``SUFFIX_TYPES``), and the property the whole design rests on -- that a
convention change reaches the engine without anyone editing the engine.

Scene behaviour (``suffix_by_type`` against real nodes) is covered by
test_naming.py under mayapy.
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MONO = os.path.dirname(REPO)
for _p in (REPO, os.path.join(MONO, "pythontk")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pythontk.core_utils.naming_convention import NamingConvention  # noqa: E402
from pythontk.core_utils.user_config import CONFIG_ROOT_ENV_VAR  # noqa: E402
from mayatk.edit_utils.naming._naming import Naming  # noqa: E402


class TestConventionBinding(unittest.TestCase):
    """The engine reads the convention; it no longer carries its own copy."""

    def setUp(self):
        # Redirect the config root so no test writes the developer's real doc.
        self.tmp = tempfile.mkdtemp()
        self._prev = os.environ.get(CONFIG_ROOT_ENV_VAR)
        os.environ[CONFIG_ROOT_ENV_VAR] = self.tmp
        NamingConvention.reload()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop(CONFIG_ROOT_ENV_VAR, None)
        else:
            os.environ[CONFIG_ROOT_ENV_VAR] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)
        NamingConvention.reload()

    # ------------------------------------------------------------- bindings
    def test_every_binding_names_a_real_convention_entry(self):
        """A typo in the middle column would silently disable a whole type."""
        for _kw, ck, _tk in Naming.SUFFIX_BINDINGS:
            with self.subTest(convention_key=ck):
                self.assertIn(ck, NamingConvention.DEFAULTS)

    def test_edit_only_panel_rows_name_real_convention_entries(self):
        """A typo in CONVENTION_GROUPS would render an empty row that writes a
        junk entry into the shared convention on first edit."""
        from mayatk.edit_utils.naming.naming_slots import NamingSlots

        for _group, rows in NamingSlots.CONVENTION_GROUPS:
            for convention_key, _object_name in rows:
                with self.subTest(convention_key=convention_key):
                    self.assertIn(convention_key, NamingConvention.DEFAULTS)

    def test_every_entry_is_reachable_from_the_editor(self):
        """Nothing in the convention may be uneditable.

        A node-type entry is edited through its SUFFIX_BINDINGS row; everything
        else needs an explicit CONVENTION_GROUPS row. Adding an entry to
        ``ARTIFACT_KEYS`` and forgetting the panel row would leave it
        settable only by hand-editing JSON — which is the failure this asserts
        against, in both directions.
        """
        from mayatk.edit_utils.naming.naming_slots import NamingSlots

        bound = {ck for _kw, ck, _tk in Naming.SUFFIX_BINDINGS}
        edit_only = {
            ck for _g, rows in NamingSlots.CONVENTION_GROUPS for ck, _n in rows
        }
        self.assertEqual(
            edit_only,
            set(NamingConvention.ARTIFACT_KEYS),
            "the panel's edit-only rows and ARTIFACT_KEYS must agree",
        )
        self.assertEqual(
            set(NamingConvention.DEFAULTS) - bound - edit_only,
            set(),
            "every shipped entry needs an editor row",
        )

    def test_panel_row_object_names_are_unique(self):
        """They are persisted user settings; a collision silently shares state."""
        from mayatk.edit_utils.naming.naming_slots import NamingSlots

        names = list(NamingSlots.SUFFIX_FIELDS.values()) + [
            n for _g, rows in NamingSlots.CONVENTION_GROUPS for _k, n in rows
        ]
        self.assertEqual(len(names), len(set(names)))

    def test_bindings_cover_the_published_keyword_set(self):
        self.assertEqual(len(Naming.SUFFIX_BINDINGS), 19)
        keywords = [kw for kw, _ck, _tk in Naming.SUFFIX_BINDINGS]
        self.assertEqual(len(set(keywords)), 19, "keywords must be unique")

    # -------------------------------------------------- compatibility view
    def test_suffix_types_keeps_its_published_four_column_shape(self):
        row = dict((r[0], r) for r in Naming.SUFFIX_TYPES)["mesh_suffix"]
        self.assertEqual(row, ("mesh_suffix", "_GEO", "Mesh", "mesh"))

    def test_suffix_types_is_live_not_frozen_at_import(self):
        """The whole point: edit the convention, every reader follows."""
        NamingConvention.set("mesh", "_MSH")
        row = dict((r[0], r) for r in Naming.SUFFIX_TYPES)["mesh_suffix"]
        self.assertEqual(row[1], "_MSH")

    # ----------------------------------------------------------- the join
    def test_affix_rules_are_keyed_by_maya_type(self):
        rules = Naming.affix_rules()
        self.assertEqual(rules["mesh"].text, "_GEO")
        self.assertEqual(rules["material"].text, "_MAT")
        self.assertEqual(rules["mesh"].apply("body"), "body_GEO")

    def test_affix_rules_follow_a_convention_edit(self):
        NamingConvention.set("mesh", "_MSH")
        self.assertEqual(Naming.affix_rules()["mesh"].apply("body"), "body_MSH")

    def test_a_prefix_convention_lands_on_the_front(self):
        """An affix, not merely a suffix — the reason for the whole rework."""
        NamingConvention.set("mesh", "GEO_")
        self.assertEqual(Naming.affix_rules()["mesh"].apply("body"), "GEO_body")

    def test_overrides_accept_either_key_form(self):
        """Callers pass suffix_by_type's keywords straight through."""
        by_keyword = Naming.affix_rules({"mesh_suffix": "_A"})["mesh"].text
        by_type_key = Naming.affix_rules({"mesh": "_A"})["mesh"].text
        self.assertEqual((by_keyword, by_type_key), ("_A", "_A"))

    def test_mode_override_forces_placement(self):
        rules = Naming.affix_rules({"mesh_suffix": "GEO_"}, {"mesh_suffix": "prefix"})
        self.assertEqual(rules["mesh"].apply("body"), "GEO_body")

    def test_an_affix_carries_its_own_separator(self):
        """Documented contract (``StrUtils.apply_affix`` concatenates verbatim):
        the separator is part of the affix, not something the engine inserts.
        A UI that lets users type a bare token normalises it before it gets
        here — the engine must not guess."""
        rules = Naming.affix_rules({"mesh_suffix": "GEO"}, {"mesh_suffix": "prefix"})
        self.assertEqual(rules["mesh"].apply("body"), "GEObody")

    def test_an_empty_override_disables_the_type(self):
        rules = Naming.affix_rules({"mesh_suffix": ""})
        self.assertEqual(rules["mesh"].apply("body"), "body")

    def test_labels_survive_the_join(self):
        self.assertEqual(Naming.affix_rules()["nurbsCurve"].label, "Nurbs Curve")


class TestHostParity(unittest.TestCase):
    """blendertk mirrors mayatk's keywords and convention keys by contract."""

    def test_blendertk_shares_the_keywords_and_convention_keys(self):
        btk_naming = os.path.join(
            MONO, "blendertk", "blendertk", "edit_utils", "naming", "_naming.py"
        )
        if not os.path.isfile(btk_naming):
            self.skipTest("blendertk not present beside mayatk")
        if os.path.join(MONO, "blendertk") not in sys.path:
            sys.path.insert(0, os.path.join(MONO, "blendertk"))
        from blendertk.edit_utils.naming._naming import Naming as BNaming

        self.assertEqual(
            [(kw, ck) for kw, ck, _tk in Naming.SUFFIX_BINDINGS],
            [(kw, ck) for kw, ck, _tk in BNaming.SUFFIX_BINDINGS],
            "only the host TYPE column may differ between the toolkits",
        )

    def test_both_panels_offer_the_same_edit_only_rows(self):
        """The convention is shared, so its editor must be too — a row present
        in one host and not the other makes an entry uneditable there."""
        btk_slots = os.path.join(
            MONO, "blendertk", "blendertk", "edit_utils", "naming", "naming_slots.py"
        )
        if not os.path.isfile(btk_slots):
            self.skipTest("blendertk not present beside mayatk")
        if os.path.join(MONO, "blendertk") not in sys.path:
            sys.path.insert(0, os.path.join(MONO, "blendertk"))
        from mayatk.edit_utils.naming.naming_slots import NamingSlots
        from blendertk.edit_utils.naming.naming_slots import (
            NamingSlots as BNamingSlots,
        )

        self.assertEqual(NamingSlots.CONVENTION_GROUPS, BNamingSlots.CONVENTION_GROUPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
