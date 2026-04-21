"""Reproduce user's Build-audio-misalignment bug using their actual scene."""
import os
import sys
import unittest

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
test_dir = os.path.dirname(os.path.abspath(__file__))
if test_dir not in sys.path:
    sys.path.insert(0, test_dir)

import maya.cmds as cmds  # noqa: E402
import pymel.core as pm  # noqa: E402

from base_test import MayaTkTestCase  # noqa: E402

SCENE = (
    r"O:/Dropbox (Moth+Flame)/Moth+Flame Dropbox/Ryan Simpson/_tests/"
    r"audio_files/audio_clips_not_aligning.ma"
)
CSV = (
    r"O:/Dropbox (Moth+Flame)/Moth+Flame Dropbox/Ryan Simpson/_tests/"
    r"seq_doc/Speed_Run_C-130H Rigging Verification - Sequence Doc.csv"
)

LOG = os.path.join(test_dir, "temp_tests", "repro_output.txt")


def _log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


class TestAudioReproFromScene(MayaTkTestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(LOG):
            os.remove(LOG)

    def test_repro(self):
        from mayatk.anim_utils.shots._shots import (
            ShotStore,
            detect_shot_regions,
        )
        from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
            ShotManifest,
        )
        from mayatk.anim_utils.shots.shot_manifest.mapping import (
            resolve as mapping_resolve,
        )
        from mayatk.anim_utils.shots.shot_manifest.range_resolver import (
            resolve_ranges,
        )
        from mayatk.audio_utils._audio_utils import AudioUtils as au

        pm.mel.file(SCENE, open=True, force=True)
        _log(f"opened {os.path.basename(SCENE)}")

        store = ShotStore.active()
        _log(f"store has {len(store.shots)} shots")

        track_names = [
            "A01_WelcomeToThe",
            "A02_TodaysTrainingConsists",
            "A03_AtTheEnd",
            "A04_LookUpTo",
            "A05_BeforeVerifyingThe",
            "A06_ContourClampsMust",
            "A07_TheClampsHave",
            "A08_TheresCurrentlyWarning",
        ]

        _log("\n--- BEFORE build ---")
        for s in store.sorted_shots()[:10]:
            _log(f"  shot {s.name}: start={s.start} end={s.end}")
        for tn in track_names:
            tid = au.normalize_track_id(tn)
            keys = au.read_keys(tid) or []
            _log(f"  track {tn}: keys={keys[:4]} path={au.get_path(tid)}")

        steps = mapping_resolve(CSV, name="speedrun")
        n_aud = sum(1 for st in steps for o in st.objects if o.kind == "audio")
        _log(f"parsed {len(steps)} steps with mapping=speedrun "
             f"(audio objects: {n_aud})")

        builder = ShotManifest(store)

        det_threshold = store.detection_threshold if store else 5.0
        regions = detect_shot_regions(gap_threshold=det_threshold)
        gap_starts = [r["start"] for r in regions] if regions else []
        gap_end_map = {
            r["start"]: r["end"] for r in regions if r.get("end") is not None
        } if regions else {}
        _log(f"detected {len(gap_starts)} animation regions in scene: "
             f"{gap_starts[:8]}{'...' if len(gap_starts) > 8 else ''}")

        default_dur = 200.0 if not gap_starts else 0

        range_map = {s.name: (s.start, s.end) for s in store.sorted_shots()}
        resolved = resolve_ranges(
            steps=steps, user_ranges={}, gap_starts=gap_starts,
            gap_end_map=gap_end_map, gap=(store.gap if store else 0.0),
            use_selected_keys=False, last_resolved=[],
            default_duration=default_dur,
        )
        for sid, s, e, _ in resolved:
            if sid not in range_map and e is not None:
                range_map[sid] = (s, e)

        with store.batch_update():
            actions, beh, _ = builder.sync(
                steps, ranges=range_map, remove_missing=True,
                zero_duration_fallback=True,
            )

        _log(f"\nactions: created={sum(1 for v in actions.values() if v=='created')} "
             f"patched={sum(1 for v in actions.values() if v=='patched')} "
             f"skipped={sum(1 for v in actions.values() if v=='skipped')}")
        _log(f"behaviors applied: {len(beh.get('applied', []))}")

        _log("\n--- AFTER build ---")
        for s in store.sorted_shots()[:10]:
            _log(f"  shot {s.name}: start={s.start} end={s.end}")
        for tn in track_names:
            tid = au.normalize_track_id(tn)
            keys = au.read_keys(tid) or []
            _log(f"  track {tn}: keys={keys[:4]}")

        # Misalignment report
        _log("\n--- MISALIGNMENT ---")
        mis = 0
        for s in store.sorted_shots():
            for entry in s.metadata.get("behaviors", []):
                if entry.get("kind") != "audio":
                    continue
                name = entry["name"]
                tid = au.normalize_track_id(name)
                keys = au.read_keys(tid) or []
                on = [f for f, v in keys if int(round(v)) == 1]
                if not on:
                    _log(f"  {s.name}/{name}: NO ON-KEY")
                    mis += 1
                elif abs(on[0] - s.start) > 0.5:
                    _log(f"  {s.name}/{name}: on={on[0]} shot.start={s.start} "
                         f"D={on[0]-s.start:+.1f}")
                    mis += 1
        _log(f"\nTOTAL MISALIGNED: {mis}")


if __name__ == "__main__":
    unittest.main()
