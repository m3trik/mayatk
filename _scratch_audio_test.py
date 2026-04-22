"""Standalone mayapy test for audio scrub + snap-to-frame fixes.

Run via:
    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _scratch_audio_test.py
"""
import os
import sys
import traceback

SCENE = r"O:/Dropbox (Moth+Flame)/Moth+Flame Dropbox/Ryan Simpson/_tests/shots_test/shots_test.ma"
AUDIO_DIR = r"O:/Dropbox (Moth+Flame)/Moth+Flame Dropbox/Ryan Simpson/_tests/audio_files/C-130H FCR Audio Files"

FAILED = []


def check(label, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILED.append(label)


def main():
    import maya.standalone
    maya.standalone.initialize()

    import maya.cmds as cmds
    import pymel.core as pm  # noqa: F401 — ensures pymel is initialised

    # Open the scene
    if os.path.isfile(SCENE):
        cmds.file(SCENE, open=True, force=True, prompt=False)
        print(f"[INFO] Opened {SCENE}")
    else:
        cmds.file(new=True, force=True)
        print(f"[WARN] Scene not found; using empty scene")

    # --- 1. Snap-to-frame global + write_key ----------------------------------
    from mayatk.audio_utils._audio_utils import AudioUtils

    check("AudioUtils.get_snap_frames() default True",
          AudioUtils.get_snap_frames() is True)

    # Grab first few audio files to register as tracks
    files = []
    if os.path.isdir(AUDIO_DIR):
        files = sorted(
            os.path.join(AUDIO_DIR, f) for f in os.listdir(AUDIO_DIR)
            if f.lower().endswith((".mp3", ".wav"))
        )[:3]
    check("Found audio source files", len(files) >= 2, f"{len(files)} files")

    from mayatk.audio_utils.audio_clips._audio_clips import AudioClips

    if files:
        tids = AudioClips.load_tracks(files)
        print(f"[INFO] Registered tracks: {tids}")
        check("Load tracks", len(tids) >= 2, f"{len(tids)} tracks")

        # Report pre-existing scene state so "no keys" is visible, not
        # misdiagnosed as a bug in the changes under test.
        all_tracks = AudioUtils.list_tracks()
        keyed_existing = sum(1 for t in all_tracks if AudioUtils.read_keys(t))
        print(
            f"[INFO] Scene state: {len(all_tracks)} tracks, "
            f"{keyed_existing} have keys"
        )

        # Write a fractional-frame key with snap ON (default).
        tid = tids[0]
        AudioUtils.write_key(tid, 12.7, 1)
        keys = AudioUtils.read_keys(tid)
        snapped_ok = any(abs(f - 13.0) < 1e-6 for f, _ in keys)
        check("write_key(12.7) snaps to 13 when snap=True",
              snapped_ok, f"keys={keys}")

        # Clear and write with snap OFF via param
        AudioUtils.clear_keys(tid)
        AudioUtils.write_key(tid, 12.7, 1, snap=False)
        keys = AudioUtils.read_keys(tid)
        frac_ok = any(abs(f - 12.7) < 1e-3 for f, _ in keys)
        check("write_key(12.7, snap=False) keeps fractional",
              frac_ok, f"keys={keys}")

        # Toggle global OFF and confirm write_key respects it
        AudioUtils.clear_keys(tid)
        AudioUtils.set_snap_frames(False)
        AudioUtils.write_key(tid, 5.3, 1)
        keys = AudioUtils.read_keys(tid)
        frac_ok = any(abs(f - 5.3) < 1e-3 for f, _ in keys)
        check("Global snap_frames=False honoured by write_key",
              frac_ok, f"keys={keys}")
        AudioUtils.set_snap_frames(True)  # restore default

        # Write proper keys for composite test
        AudioUtils.clear_keys(tid)
        for i, t in enumerate(tids):
            AudioUtils.clear_keys(t)
            AudioUtils.write_key(t, 10 + i * 100, 1)
            AudioUtils.write_key(t, 10 + i * 100 + 50, 0)

    # --- 2. Composite build + resolution --------------------------------------
    if files:
        result = AudioClips.sync()
        print(f"[INFO] AudioClips.sync result: {result}")
        comp = AudioClips._find_composite_node()
        check("Composite node exists after sync", bool(comp), f"node={comp}")

        if comp:
            filename = cmds.getAttr(f"{comp}.filename") or ""
            check("Composite has filename", bool(filename),
                  f"path={filename}")
            check("Composite WAV file exists on disk",
                  os.path.isfile(filename))

    # --- 3. _resolve_preferred_audio_node -------------------------------------
    # Simulate the private helper directly; avoid full UI init.
    from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
        ShotSequencerController,
    )
    resolved = ShotSequencerController._resolve_preferred_audio_node()
    comp = AudioClips._find_composite_node()
    check("Resolved node prefers composite",
          resolved == comp if comp else True,
          f"resolved={resolved} composite={comp}")

    # --- 4. ScrubPlayer importable from uitk ----------------------------------
    try:
        from uitk.widgets.sequencer import ScrubPlayer
        check("uitk ScrubPlayer imports", True)
        check("ScrubPlayer is a class", isinstance(ScrubPlayer, type))
    except Exception as exc:
        check("uitk ScrubPlayer imports", False, str(exc))

    # Also confirm SequencerWidget exposes the audio API.
    try:
        from uitk.widgets.sequencer import SequencerWidget
        has_api = (
            hasattr(SequencerWidget, "set_audio_source")
            and hasattr(SequencerWidget, "clear_audio_source")
        )
        check("SequencerWidget exposes audio API", has_api)
    except Exception as exc:
        check("SequencerWidget exposes audio API", False, str(exc))

    # --- 5. Option-menu checkbox wiring is syntactic (can't run UI here) ------
    # Import the module to ensure no syntax regression in the UI hook.
    try:
        import mayatk.audio_utils.audio_clips.audio_clips_slots as _slots_mod
        check("audio_clips_slots importable", True)
    except Exception as exc:
        check("audio_clips_slots importable", False, str(exc))

    # --- Done -----------------------------------------------------------------
    print()
    if FAILED:
        print(f"FAILURES ({len(FAILED)}):")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
