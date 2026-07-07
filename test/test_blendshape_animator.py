# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.blendshape_animator.

Each test case targets a specific known bug — see comments.
Tests are expected to fail before Phase 3 fixes land, and pass after.
"""
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.anim_utils.blendshape_animator.applicator import Applicator
from mayatk.anim_utils.blendshape_animator._blendshape_animator import BlendshapeAnimator
from mayatk.anim_utils.blendshape_animator.creator import Creator
from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.target import Target, Targets
from mayatk.anim_utils.blendshape_animator.weights import Weights


class TestBlendshapeAnimatorBugs(MayaTkTestCase):
    """Behavioral tests for known bugs. Each test name references a bug number."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from maya import standalone
            standalone.initialize(name="python")
        except (RuntimeError, TypeError):
            pass

    def _make_animator(self, n_keys=10):
        """Create a base+target sphere pair and a fully-set-up BlendshapeAnimator."""
        base = cmds.polySphere(name="base_mesh")[0]
        target = cmds.polySphere(name="target_mesh")[0]
        cmds.move(2, 0, 0, target)
        animator = BlendshapeAnimator()
        ok = animator.create(
            base_mesh=base,
            target_mesh=target,
            start_frame=1,
            end_frame=n_keys,
            name="test",
            test_setup=False,
        )
        self.assertTrue(ok, "setup precondition failed")
        return animator

    # -------------------------------------------------------------------------
    # Bug 1: find_all_targets double-counts (group children + scene scan)
    # -------------------------------------------------------------------------
    def test_bug1_find_all_targets_no_duplicates(self):
        animator = self._make_animator()
        animator.edit_weight_based(count=2)

        tweens = Targets.find_all_targets()
        mesh_names = [t.mesh for t in tweens]
        self.assertEqual(
            len(mesh_names),
            len(set(mesh_names)),
            f"find_all_targets returned duplicates: {mesh_names}",
        )

    # -------------------------------------------------------------------------
    # Bug 2: _validate_setup fails after _cleanup_target_mesh sets target=None
    # -------------------------------------------------------------------------
    def test_bug2_validate_setup_survives_target_cleanup(self):
        animator = self._make_animator()
        animator._cleanup_target_mesh()

        self.assertTrue(
            animator._validate_setup(),
            "_validate_setup should still pass after target mesh is cleaned up "
            "(blendshape + base mesh are sufficient)",
        )

    # -------------------------------------------------------------------------
    # Bug 3: create_weight_based_tweens crashes mid-batch on duplicate weights
    # -------------------------------------------------------------------------
    def test_bug3_create_weight_based_tweens_handles_existing(self):
        animator = self._make_animator()
        animator.tween_creator.create_weight_based_tweens([0.5])

        try:
            animator.tween_creator.create_weight_based_tweens([0.5, 0.7])
        except RuntimeError as e:
            self.fail(
                f"create_weight_based_tweens raised on duplicate weight 0.5: {e}"
            )

        weights = sorted({t.weight for t in Targets.find_all_targets()})
        self.assertIn(0.7, weights, "weight 0.7 should have been created")

    # -------------------------------------------------------------------------
    # Bug 4: apply_tweens default validate_topology should be False (no silent
    # filtering). Original behavior was to fail loudly per-tween.
    # -------------------------------------------------------------------------
    def test_bug4_apply_tweens_default_does_not_silently_filter(self):
        import inspect

        sig = inspect.signature(Applicator.apply_tweens)
        default = sig.parameters["validate_topology"].default
        self.assertFalse(
            default,
            "Applicator.apply_tweens default validate_topology should be False "
            "(do not silently mask topology mismatches)",
        )

    # -------------------------------------------------------------------------
    # Bug 5: _apply_single_tween returns False for skip AND for real errors
    # -------------------------------------------------------------------------
    def test_bug5_apply_single_tween_distinguishes_skip_from_error(self):
        animator = self._make_animator()
        animator.tween_creator.create_weight_based_tweens([0.5])
        tween = Targets.find_all_targets()[0]

        result = animator.tween_applicator._apply_single_tween(
            tween, skip_duplicates=True
        )
        self.assertNotEqual(
            result,
            False,
            "Skipped duplicate should not be indistinguishable from a real error. "
            "Expected a 3-state result (e.g. 'applied' / 'skipped' / 'error').",
        )

    # -------------------------------------------------------------------------
    # Bug 6: Weights.generate_weights is inconsistent across include_endpoints
    # -------------------------------------------------------------------------
    def test_bug6_generate_weights_consistent_count(self):
        n = 3
        without = Weights.generate_weights(n, include_endpoints=False)
        with_ends = Weights.generate_weights(n, include_endpoints=True)
        self.assertEqual(
            len(without),
            n,
            f"generate_weights({n}, include_endpoints=False) should yield {n} weights, got {len(without)}",
        )
        self.assertEqual(
            len(with_ends),
            n,
            f"generate_weights({n}, include_endpoints=True) should yield {n} weights, got {len(with_ends)}",
        )

    # -------------------------------------------------------------------------
    # Bug 8: _get_existing_weights swallows all errors via bare except
    # -------------------------------------------------------------------------
    def test_bug8_get_existing_weights_no_bare_except(self):
        import inspect
        from mayatk.anim_utils.blendshape_animator import creator

        src = inspect.getsource(creator)
        bare_count = sum(
            1 for line in src.splitlines() if line.strip() == "except:"
        )
        self.assertEqual(
            bare_count,
            0,
            f"creator.py contains {bare_count} bare 'except:' clauses",
        )

    # -------------------------------------------------------------------------
    # Bug 13: _tag_tween_mesh is not idempotent (re-tag raises)
    # -------------------------------------------------------------------------
    def test_bug13_tag_tween_mesh_idempotent(self):
        animator = self._make_animator()
        tweens = animator.tween_creator.create_weight_based_tweens([0.5])
        mesh = tweens[0].mesh

        try:
            animator.tween_creator.tag_tween_mesh(mesh, weight=0.5)
        except RuntimeError as e:
            self.fail(f"tag_tween_mesh should be idempotent, raised: {e}")

    # -------------------------------------------------------------------------
    # Bug 7: bare except clauses across the subpackage
    # -------------------------------------------------------------------------
    def test_bug7_no_bare_excepts_in_subpackage(self):
        import inspect
        from mayatk.anim_utils import blendshape_animator as pkg
        import pkgutil
        import importlib

        bare_locations = []
        for _, modname, _ in pkgutil.iter_modules(pkg.__path__):
            # Skip Designer-generated UI compilation output (depends on Qt at
            # module load and contains no logic the bug 7 rule applies to).
            if modname.endswith("_ui") or modname.endswith("_slots"):
                continue
            mod = importlib.import_module(f"{pkg.__name__}.{modname}")
            src = inspect.getsource(mod)
            for i, line in enumerate(src.splitlines(), 1):
                if line.strip() == "except:":
                    bare_locations.append(f"{modname}:{i}")
        self.assertEqual(
            bare_locations,
            [],
            f"Bare 'except:' clauses found: {bare_locations}",
        )


class TestAuditRegressionFixes(MayaTkTestCase):
    """Regression tests for the 2026-07 anim_utils audit fixes."""

    def _make_animator(self, n_keys=10):
        base = cmds.polySphere(name="audit_base_mesh")[0]
        target = cmds.polySphere(name="audit_target_mesh")[0]
        cmds.move(2, 0, 0, target)
        animator = BlendshapeAnimator()
        ok = animator.create(
            base_mesh=base,
            target_mesh=target,
            start_frame=1,
            end_frame=n_keys,
            name="audit",
            test_setup=False,
        )
        self.assertTrue(ok, "setup precondition failed")
        return animator

    def test_finalize_for_export_preserves_weight_animation(self):
        """finalize_for_export's history cleanup deleted the weight
        animCurve (proven live: keys went 2 -> 0 while it reported
        'EXPORT READY')."""
        animator = self._make_animator()
        bs = animator.blendshape
        keys_before = cmds.keyframe(
            f"{bs}.weight[0]", query=True, keyframeCount=True
        )
        self.assertGreater(keys_before, 0, "setup should key weight[0]")

        self.assertTrue(animator.finalize_for_export())

        self.assertTrue(cmds.objExists(bs), "blendShape must survive")
        keys_after = cmds.keyframe(
            f"{bs}.weight[0]", query=True, keyframeCount=True
        )
        self.assertEqual(
            keys_after,
            keys_before,
            "history cleanup must not delete the weight animCurve",
        )

    def test_create_none_frames_uses_defaults(self):
        """basic_workflow passes start/end=None into create(); None used to
        flow into create_keyframes and crash."""
        base = cmds.polySphere(name="none_base_mesh")[0]
        target = cmds.polySphere(name="none_target_mesh")[0]
        cmds.move(2, 0, 0, target)
        animator = BlendshapeAnimator()
        ok = animator.create(
            base_mesh=base,
            target_mesh=target,
            start_frame=None,
            end_frame=None,
            test_setup=False,
        )
        self.assertTrue(ok)
        times = cmds.keyframe(
            f"{animator.blendshape}.weight[0]", query=True, timeChange=True
        )
        self.assertTrue(times)
        self.assertEqual(min(times), float(BlendshapeAnimator.DEFAULT_START_FRAME))
        self.assertEqual(max(times), float(BlendshapeAnimator.DEFAULT_END_FRAME))

    def test_failed_create_clears_setup_state(self):
        """A failed create must not leave a half-bound animator (the UI
        gates edit/export groups on this state)."""
        animator = BlendshapeAnimator()
        # Invalid meshes -> validate fails before state is bound.
        ok = animator.create(
            base_mesh="does_not_exist_a", target_mesh="does_not_exist_b"
        )
        self.assertFalse(ok)
        self.assertIsNone(animator.base_mesh)
        self.assertIsNone(animator.blendshape)


if __name__ == "__main__":
    unittest.main()
