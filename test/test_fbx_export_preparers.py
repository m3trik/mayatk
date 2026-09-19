# !/usr/bin/python
# coding=utf-8
"""Tests for the FBX export-metadata registry on ``FbxUtils``.

Producers RETURN scene records (``ptk.SceneRecords``) and ``FbxUtils.publish``
commits them once, in dependency order, with the exporter's decisions as
input; stagers mutate the scene for a write and undo it after; the session
hook runs whatever a subsystem opted in to for ANY FBX export.  Covers the
hook lifecycle (install, reload safety, ref-counted teardown, fault
isolation), the producer contract, the handoff block the commit stamps, and
the real Audio + Shots composition reaching one ASCII FBX.
"""

import json
import os
import sys
import tempfile
import unittest
import warnings
from unittest import mock

import pythontk as ptk
from base_test import MayaTkTestCase

import maya.cmds as cmds
from maya import mel

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.audio_utils._audio_utils import AudioUtils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips

SR = ptk.SceneRecords


def _export_selected_ascii(nodes, fname="mtk_preparers.fbx"):
    out = os.path.join(tempfile.gettempdir(), fname)
    try:
        mel.eval("FBXExportInAscii -v true")
        cmds.select(list(nodes), replace=True)
        cmds.file(
            out, force=True, options="v=0;", type="FBX export", exportSelected=True
        )
        with open(out, encoding="utf-8", errors="ignore") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.remove(out)


def _clear_export_state():
    for key in list(FbxUtils._session_producers):
        FbxUtils.disable_export_producer(key)
    for name in list(FbxUtils._session_stagers):
        FbxUtils.unregister_export_stager(name)
    FbxUtils.disable_auto_takes()
    FbxUtils.reset_takes()


def _stub_producers(table):
    """Patch the resolved producer table for one test (the real table imports
    every subsystem; a stub keeps a test about ordering about ordering)."""
    return mock.patch.object(
        FbxUtils, "producers", staticmethod(lambda only=None: dict(table))
    )


class TestSessionHook(MayaTkTestCase):
    """The any-export hook: lifecycle and composition with stub stagers."""

    def setUp(self):
        super().setUp()
        _clear_export_state()

    def tearDown(self):
        _clear_export_state()
        super().tearDown()

    def test_a_session_stager_installs_the_hook_and_runs_once(self):
        cube = self.create_test_cube("prepCube")
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=1)

        ran = []
        FbxUtils.register_export_stager(
            "stub",
            prepare=lambda: ran.append("prepare"),
            finish=lambda: ran.append("finish"),
        )
        self.assertTrue(FbxUtils.is_auto_takes_enabled())

        _export_selected_ascii([cube])
        self.assertEqual(ran, ["prepare", "finish"], "exactly once each, in order")

    def test_scratch_export_stands_the_hook_down(self):
        """A throwaway write (a UV round-trip's duplicates) inside
        ``scratch_export`` stages nothing and leaves the depth counter
        balanced; the very next plain export stages again. Added:
        2026-09-05 -- the RizomUV round-trip left ``data_export`` behind
        whenever a session subsystem was armed."""
        cube = self.create_test_cube("scratchCube")
        ran = []
        FbxUtils.register_export_stager("stub", prepare=lambda: ran.append(True))

        with FbxUtils.scratch_export():
            _export_selected_ascii([cube])
        self.assertEqual(ran, [], "nothing may stage inside a scratch bracket")
        self.assertEqual(FbxUtils._export_depth, 0, "bracket must balance")

        _export_selected_ascii([cube])
        self.assertEqual(ran, [True], "the session hook is back after the bracket")

    def test_bracket_depth_is_one_counter_across_a_module_reload(self):
        """A reload rebinds ``FbxUtils`` to a NEW class while every module that
        imported the name keeps the OLD one; a bracket opened on one copy must
        be visible from the other, or a hook fires inside a scratch export
        (measured in the 2026-09-05 GUI pass)."""
        import importlib
        import mayatk.env_utils.fbx_utils as fu

        old = fu.FbxUtils
        self.addCleanup(setattr, fu, "FbxUtils", old)
        with old.scratch_export():
            new = importlib.reload(fu).FbxUtils
            self.assertIsNot(new, old)
            self.assertGreater(new._export_depth, 0, "the new copy sees the bracket")
            with new.export_prepared():  # nested on the other copy: still one
                self.assertEqual(old._export_depth, new._export_depth)
        self.assertEqual(old._export_depth, 0)
        self.assertEqual(new._export_depth, 0)

    def test_a_reload_does_not_leave_the_previous_copys_hook_armed(self):
        """A live dev reload rebinds the manager too; the previous copy's
        callback must be removed by id, or every export runs both copies."""
        import importlib
        import mayatk.core_utils.script_job_manager as sjm
        import mayatk.env_utils.fbx_utils as fu

        old_fu, old_sjm = fu.FbxUtils, sjm.ScriptJobManager
        self.addCleanup(setattr, fu, "FbxUtils", old_fu)
        self.addCleanup(setattr, sjm, "ScriptJobManager", old_sjm)

        cube = self.create_test_cube("reloadHookCube")
        ran = []
        old_fu.register_export_stager("stub", prepare=lambda: ran.append("old"))

        importlib.reload(sjm)
        new = importlib.reload(fu).FbxUtils
        self.assertIsNot(new, old_fu)
        self.addCleanup(new.disable_auto_takes)
        self.addCleanup(new.unregister_export_stager, "stub")
        new.register_export_stager("stub", prepare=lambda: ran.append("new"))

        _export_selected_ascii([cube])
        self.assertEqual(ran, ["new"], f"the previous copy's hook is armed: {ran}")

    def test_a_PURGED_reimport_rearms_the_hook_on_the_new_copy(self):
        """A purge-and-reimport (what the harness does between modules) gives
        the new copy its own dict; the callback Maya holds must be re-armed on
        it, or the hook runs a purged copy's registries forever."""
        import importlib

        name = "mayatk.env_utils.fbx_utils"
        old_mod = sys.modules[name]
        old_fu = old_mod.FbxUtils
        import mayatk.env_utils as env_pkg

        self.addCleanup(setattr, env_pkg, "fbx_utils", old_mod)
        self.addCleanup(sys.modules.__setitem__, name, old_mod)

        cube = self.create_test_cube("purgeHookCube")
        ran = []
        old_fu.register_export_stager("stub", prepare=lambda: ran.append("old"))

        del sys.modules[name]
        new = importlib.import_module(name).FbxUtils
        self.assertIsNot(new, old_fu, "the purge must yield a genuinely new class")
        self.addCleanup(new.disable_auto_takes)
        self.addCleanup(new.unregister_export_stager, "stub")
        new.register_export_stager("stub", prepare=lambda: ran.append("new"))

        _export_selected_ascii([cube])
        self.assertEqual(ran, ["new"], f"the hook is bound to the purged copy: {ran}")

    def test_stagers_prepare_in_registration_order_and_finish_in_reverse(self):
        cube = self.create_test_cube("prepCube2")
        order = []
        for name in ("a", "b"):
            FbxUtils.register_export_stager(
                name,
                prepare=lambda n=name: order.append(f"+{n}"),
                finish=lambda n=name: order.append(f"-{n}"),
            )
        _export_selected_ascii([cube])
        self.assertEqual(order, ["+a", "+b", "-b", "-a"])

    def test_a_bracket_that_fails_to_open_finishes_what_it_staged(self):
        """``begin_export`` stages, then publishes. A publish that raises
        leaves the caller no bracket to ``end_export``, so the stagers finish
        on the way out and the depth is left as it was found."""
        ran = []
        FbxUtils.register_export_stager(
            "stub",
            prepare=lambda: ran.append("prepare"),
            finish=lambda: ran.append("finish"),
        )
        with mock.patch.object(FbxUtils, "publish", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                FbxUtils.begin_export(FbxUtils.export_context(), stagers=())
        self.assertEqual(ran, ["prepare", "finish"])
        self.assertEqual(FbxUtils._export_depth, 0)

    def test_the_hook_holds_while_anything_needs_it(self):
        FbxUtils.enable_export_producer(SR.SHOTS)
        FbxUtils.register_export_stager("b", prepare=lambda: None)
        self.assertTrue(FbxUtils.is_auto_takes_enabled())
        FbxUtils.disable_export_producer(SR.SHOTS)
        self.assertTrue(FbxUtils.is_auto_takes_enabled(), "the stager still holds it")
        FbxUtils.unregister_export_stager("b")
        self.assertFalse(FbxUtils.is_auto_takes_enabled(), "last holder gone")

    def test_one_stager_failure_does_not_abort_the_export(self):
        cube = self.create_test_cube("prepCube3")
        ran = []

        def boom():
            raise RuntimeError("stager blew up")

        FbxUtils.register_export_stager("bad", prepare=boom)
        FbxUtils.register_export_stager("good", prepare=lambda: ran.append(True))

        text = _export_selected_ascii([cube])  # must still produce the FBX
        self.assertTrue(ran)
        self.assertIn("prepCube3", text)

    def test_explicit_enable_is_independent_and_shares_the_hook(self):
        self.assertFalse(FbxUtils.is_auto_takes_enabled())
        FbxUtils.enable_auto_takes()
        self.assertTrue(FbxUtils.is_auto_takes_enabled())
        FbxUtils.register_export_stager("a", prepare=lambda: None)
        FbxUtils.disable_auto_takes()  # explicit off, but a stager remains
        self.assertTrue(FbxUtils.is_auto_takes_enabled())
        FbxUtils.unregister_export_stager("a")
        self.assertFalse(FbxUtils.is_auto_takes_enabled())

    def test_the_hook_publishes_only_the_opted_in_producers(self):
        """A File > Export runs the producers a subsystem enabled -- in
        dependency order, the reader handed the record it reads -- and no
        other: the session hook is opt-in per subsystem."""
        cube = self.create_test_cube("hookCube")
        seen = []

        def shots(ctx):
            seen.append("shots")
            return SR.SHOTS.make(
                {"shots": [{"clip": "A", "start": 1, "end": 5, "objects": []}]}
            )

        def audio(ctx):
            seen.append(("audio", SR.declared_takes(ctx.record)[0]["name"]))
            return None

        def lightmaps(ctx):
            seen.append("lightmaps")
            return None

        FbxUtils.enable_export_producer(SR.AUDIO)
        FbxUtils.enable_export_producer(SR.SHOTS)
        table = {SR.AUDIO: audio, SR.SHOTS: shots, SR.LIGHTMAPS: lightmaps}
        with mock.patch.object(
            FbxUtils,
            "producers",
            staticmethod(
                lambda only=None: {
                    s: f
                    for s, f in table.items()
                    if only is None or s.key in {SR.resolve(k).key for k in only}
                }
            ),
        ):
            _export_selected_ascii([cube])
        self.assertEqual(seen, ["shots", ("audio", "A")])


class TestProducerContract(MayaTkTestCase):
    """What the export pipeline relies on from the producer table."""

    def setUp(self):
        super().setUp()
        _clear_export_state()

    def tearDown(self):
        _clear_export_state()
        super().tearDown()

    def test_every_producer_is_a_declared_deliverable_record(self):
        specs = SR.check_producers(FbxUtils.PRODUCERS)
        self.assertEqual(len(specs), len(FbxUtils.PRODUCERS))
        resolved = FbxUtils.producers()
        self.assertEqual(
            set(resolved), set(FbxUtils.PRODUCERS), "every producer imports"
        )

    def test_producers_are_pure_on_an_empty_scene(self):
        """A producer RETURNS its record and never writes: on an empty scene
        each returns nothing and no carrier appears."""
        ctx = FbxUtils.export_context()
        for spec, produce in FbxUtils.producers().items():
            self.assertIsNone(produce(ctx), spec.key)
        self.assertIsNone(DataNodes.get_export_node(create=False))
        self.assertIsNone(DataNodes.get_internal_node(create=False))

    def test_a_decision_is_an_input_so_a_second_publish_cannot_undo_it(self):
        """The clip mode rides the shot record because the context carries it
        -- and publishing again with the same context produces the same record.
        The old pipeline patched it on after the producers, and the bracket's
        second producer run overwrote the patch (three exports shipped the
        wrong clip origin while logging the right one)."""
        cube = self.create_test_cube("modeCube")
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=20, v=1)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("Intro", 1, 20)

        ctx = FbxUtils.export_context(clip_mode="full")
        first = FbxUtils.publish(ctx)
        second = FbxUtils.publish(FbxUtils.export_context(clip_mode="full"))
        meta = SR.SHOTS.load(DataNodes)
        self.assertEqual(meta["clip_mode"], "full")
        self.assertEqual(
            [(s["clip"], s["start"], s["end"]) for s in meta["shots"]],
            [("Intro", 1, 20)],
        )
        self.assertEqual(
            first.written[SR.SHOTS.key], second.written[SR.SHOTS.key], "idempotent"
        )

    def test_a_handoff_publish_refreshes_only_derived_records(self):
        """A bridge that merely ships the carrier is not the authority on an
        authored record: a lightmap manifest the scene's markers no longer
        describe survives a HANDOFF publish (measured: a full refresh from a
        preview push wiped it and previewed the asset unlit)."""
        SR.LIGHTMAPS.save(DataNodes, {"objects": [{"name": "kept"}]})
        FbxUtils.publish(FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF))
        self.assertEqual(SR.LIGHTMAPS.load(DataNodes)["objects"], [{"name": "kept"}])
        FbxUtils.publish(FbxUtils.export_context())  # a pipeline IS the authority
        self.assertIsNone(SR.LIGHTMAPS.load(DataNodes))

    def test_a_session_stager_stages_before_the_producers_read(self):
        """Producers see the staged scene even outside a bracket: a preview
        that must detach before its record is read registers a stager, and a
        pipeline publish runs its prepare first."""
        order = []
        FbxUtils.register_export_stager("detach", prepare=lambda: order.append("stage"))

        def shots(ctx):
            order.append("produce")
            return None

        with _stub_producers({SR.SHOTS: shots}):
            FbxUtils.publish()
        self.assertEqual(order, ["stage", "produce"])

    def test_the_retired_names_still_work_and_warn(self):
        ran = []
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FbxUtils.register_export_preparer("legacy", lambda: ran.append(True))
            FbxUtils.run_export_preparers(include_known=False)
            FbxUtils.unregister_export_preparer("legacy")
        self.assertEqual(ran, [True])
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertNotIn("legacy", FbxUtils._session_stagers)

    def test_the_retired_preparer_names_still_select_what_they_named(self):
        """``run_export_preparers(only=...)`` and ``export_prepared(only=...)``
        took the retired preparer names ("shots", "lightmap", "render_effects"
        ...); resolved as record keys they raised KeyError, and a bracket
        opened with no context ignored them. They map onto the records and
        stagers they named, and the shim publishes inside a bracket, so a
        session stager it prepares is finished too -- a preview it stood down
        came back only if the caller also ran the retired finalizers.
        Added: 2026-09-18
        """
        order = []
        FbxUtils.register_export_stager(
            "detach",
            prepare=lambda: order.append("stage"),
            finish=lambda: order.append("finish"),
        )

        def shots(ctx):
            order.append("shots")
            return None

        def lightmaps(ctx):
            order.append("lightmaps")
            return None

        table = {SR.SHOTS: shots, SR.LIGHTMAPS: lightmaps}
        narrowed = staticmethod(
            lambda only=None: {
                s: f
                for s, f in table.items()
                if only is None or s.key in {SR.resolve(k).key for k in only}
            }
        )
        with mock.patch.object(FbxUtils, "producers", narrowed):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                FbxUtils.run_export_preparers(only=["shots", "render_effects"])
                self.assertEqual(order, ["stage", "shots", "finish"])
                del order[:]
                with FbxUtils.export_prepared(only=["lightmap"]):
                    order.append("write")
                self.assertEqual(order, ["stage", "lightmaps", "write", "finish"])
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertEqual(FbxUtils._export_depth, 0)


class TestExportHandoffBlock(MayaTkTestCase):
    """The standalone-reader contract every commit stamps onto ``data_export``."""

    def setUp(self):
        super().setUp()
        _clear_export_state()

    def tearDown(self):
        _clear_export_state()
        super().tearDown()

    def test_never_manufactures_a_carrier(self):
        """An absent ``data_export`` means the scene has no in-band metadata;
        stamping a description of nothing would put a stray node in every
        deliverable."""
        self.assertIsNone(DataNodes.get_export_node(create=False))
        FbxUtils.publish()
        self.assertIsNone(DataNodes.get_export_node(create=False))

    def test_describes_what_the_producers_actually_wrote(self):
        """Stamped by the commit AFTER every record is written, from the
        channels the carrier then holds."""
        cube = cmds.polyCube(name="handoffBox")[0]
        cmds.addAttr(cube, longName="lightmapInfo", dataType="string")
        cmds.setAttr(
            f"{cube}.lightmapInfo",
            json.dumps(
                {
                    "map": "handoffBox_LightMap.exr",
                    "uv_set": "lightmap",
                    "intensity": 1.0,
                    "scaleOffset": [1.0, 1.0, 0.0, 0.0],
                }
            ),
            type="string",
        )
        FbxUtils.publish()

        block = SR.HANDOFF.load(DataNodes)
        self.assertTrue(block, "the carrier has channels, so it must describe them")
        self.assertEqual(block["version"], SR.HANDOFF.version)
        self.assertEqual(block["source"]["application"], "maya")
        self.assertIn("data_export.lightmap_metadata", block["reads"])
        self.assertNotIn("data_export.handoff", block["reads"], "not itself")
        # The sentence the whole block exists for.
        self.assertIn("NOT", block["instructions"])
        self.assertIn("lightmapInfo", block["instructions"])

    def test_the_session_hook_path_stamps_it_too(self):
        """File > Export and the Game Exporter run the session hook, which
        always publishes -- even with no producer opted in -- so every FBX
        written outside the Scene Exporter describes the channels it carries."""
        DataNodes.write(ptk.Scope.DELIVERABLE, "audio_manifest", "1:beep")
        FbxUtils._on_before_export()
        FbxUtils._on_after_export()
        block = SR.HANDOFF.load(DataNodes)
        self.assertTrue(block, "the session-hook path must describe the carrier too")
        self.assertIn("data_export.audio_manifest", block["reads"])

    def test_a_producer_that_clears_its_record_leaves_no_stale_claim(self):
        """A scene whose bake was reverted must not still advertise a lightmap."""
        cmds.polyCube(name="handoffBoxB")
        SR.LIGHTMAPS.save(DataNodes, {"objects": []})
        FbxUtils.publish()  # the producer clears the unbacked record
        reads = (SR.HANDOFF.load(DataNodes) or {}).get("reads") or {}
        self.assertNotIn("data_export.lightmap_metadata", reads)


class TestAudioShotsAutoExportCompose(MayaTkTestCase):
    """Audio + Shots opt-ins compose: one export, both records fresh."""

    def setUp(self):
        super().setUp()
        _clear_export_state()
        ShotStore.clear_active()

    def tearDown(self):
        AudioClips.disable_auto_export()
        ShotStore.disable_auto_export()
        ShotStore.clear_active()
        _clear_export_state()
        super().tearDown()

    def test_both_systems_bake_on_one_export(self):
        cube = self.create_test_cube("anim_host")
        for t, v in ((1, 0.0), (50, 5.0), (100, 0.0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, value=v)
        cmds.playbackOptions(min=1, max=100)

        # Author audio + shots but DO NOT publish by hand -- the session hook
        # must do it inside the before-export callback.
        AudioUtils.write_key("footstep", frame=10, value=1)
        AudioUtils.write_key("footstep", frame=15, value=0)

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        store.define_shot("Outro", 51, 100)

        # Pre-create the carrier so it can be in the export selection; the hook
        # populates its records during the export (mirrors File > Export All,
        # where the carrier is included automatically).
        DataNodes.ensure_export()

        AudioClips.enable_auto_export()
        ShotStore.enable_auto_export()
        self.assertTrue(FbxUtils.is_auto_takes_enabled())

        text = _export_selected_ascii([cube, DataNodes.EXPORT], "mtk_both.fbx")

        # Shots: metadata + both named takes.
        self.assertIn("shot_metadata", text)
        self.assertIn("Intro", text)
        self.assertIn("Outro", text)
        # Audio: manifest record + the track label, scoped to its clip.
        self.assertIn("audio_manifest", text)
        self.assertIn("footstep", text)
        manifest = SR.AUDIO.load(DataNodes)
        self.assertEqual({e["clip"] for e in manifest["events"]}, {"Intro"})

        # Distinct records on the one carrier node -- and no take list beside
        # the shot record: the clips carry their ranges, so a separate list
        # is no longer written.
        attrs = cmds.listAttr(DataNodes.EXPORT, userDefined=True) or []
        for key in ("shot_metadata", "audio_manifest", "handoff"):
            self.assertIn(key, attrs)
        self.assertNotIn(SR.FBX_TAKES.key, attrs)


if __name__ == "__main__":
    unittest.main()
