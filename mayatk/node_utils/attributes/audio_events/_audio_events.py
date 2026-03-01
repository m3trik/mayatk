# !/usr/bin/python
# coding=utf-8
"""Import and manage Maya audio nodes from keyed event triggers.

Reads the ``{cat}_trigger`` enum keyframes authored by
``EventTriggers`` and places matching audio clips on the Maya timeline
so animators can hear sound effects during playback and scrubbing.

Audio nodes are DG nodes (not DAG) so they cannot be parented under
transforms.  This module groups them in an ``objectSet`` named
``{category}_audio_set`` for easy selection and cleanup.

.. note::
    Maya only displays **one** audio waveform at a time — both the
    Time Slider and Graph Editor have a single sound slot.  Use
    ``set_active()`` to choose which clip is visible.

Typical workflow::

    # 1. Setup triggers (EventTriggers)
    EventTriggers.create(objs, category="audio", events=["Footstep", "Jump"])
    EventTriggers.set_key(obj, event="Footstep", time=12, category="audio")

    # 2. Import audio previews
    AudioEvents.sync(objs, search_dir=r"D:/project/audio", category="audio")

    # 3. Activate a clip for waveform display
    AudioEvents.set_active("Footstep_12")

    # 4. Cleanup
    AudioEvents.remove(category="audio")
"""
import logging
import os
from typing import Dict, List, Optional

import pythontk as ptk

try:
    import pymel.core as pm
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass

from mayatk.core_utils._core_utils import CoreUtils


class AudioEvents(ptk.LoggingMixin):
    """Creates and manages Maya audio nodes from keyed event triggers.

    Reads ``{category}_trigger`` enum keyframes via
    ``EventTriggers.iter_keyed_events`` and imports matching audio files
    from a search directory onto the timeline.

    Imported nodes are collected into a ``{cat}_audio_set`` objectSet.
    """

    PLAYABLE_EXTENSIONS = ptk.AudioUtils.PLAYABLE_EXTENSIONS
    """Maya timeline-playable audio file extensions."""

    SOURCE_EXTENSIONS = ptk.AudioUtils.SOURCE_EXTENSIONS
    """Importable source extensions (converted to WAV when needed)."""

    NODE_STEM_ATTR = "audio_event_stem"
    """String attr stamped on audio nodes storing the lowercase event stem."""

    NODE_TYPE_ATTR = "audio_node_type"
    """String attr stamped on audio nodes: ``"preview"``, ``"synced"``, or ``"composite"``."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def sync(
        cls,
        objects: Optional[List] = None,
        search_dir: str = "",
        audio_files: Optional[List[str]] = None,
        audio_file_map: Optional[Dict[str, str]] = None,
        category: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """Create or update audio nodes from keyed event triggers.

        Idempotent: clears existing audio nodes in the category's set
        and re-imports from scratch based on current keyframes.

        Provide *one* of:

        - ``audio_file_map`` — ``{stem: path}`` dict whose keys match
          the event labels.  **Preferred** when the caller already has
          the mapping (e.g. from ``_audio_files`` in the slots class).
        - ``audio_files`` — explicit list of paths (stems re-derived).
        - ``search_dir`` — scanned recursively.

        Parameters:
            objects: Transforms to scan.  Defaults to selection.
            search_dir: Root directory to recursively search for audio
                files.  File stems must match event names
                (e.g. ``Footstep.wav`` for event ``"Footstep"``).
            audio_files: Explicit list of audio file paths.  Stems are
                matched case-insensitively against event names.
            audio_file_map: ``{stem: path}`` mapping.  Keys are used
                directly (lowered) as lookup keys — no re-extraction
                from file paths.  Takes precedence over *audio_files*.
            category: Attribute prefix (default ``"event"``).

        Returns:
            Dict mapping object name -> list of created audio node names.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        if objects is None:
            objects = pm.selected()
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        cat = category or EventTriggers.DEFAULT_CATEGORY

        # 1. Build audio map from explicit files or directory scan
        if audio_file_map:
            audio_map = cls._build_audio_map_from_file_map(audio_file_map)
            first_path = next(iter(audio_file_map.values()), None)
            output_dir = (
                os.path.dirname(first_path).replace("\\", "/") if first_path else ""
            )
        elif audio_files:
            audio_map = cls._build_audio_map_from_files(audio_files)
            output_dir = os.path.dirname(audio_files[0]).replace("\\", "/")
        elif search_dir and os.path.isdir(search_dir):
            audio_map = cls._build_audio_map(search_dir)
            output_dir = search_dir
        else:
            cls.logger.error(
                "Provide audio_file_map, audio_files, or a valid search_dir."
            )
            return {}

        if not audio_map:
            cls.logger.warning("No audio files resolved.")
            return {}

        # Strip any trailing cache directory so the composite target
        # doesn't nest (e.g. ``…/_audio_cache/_maya_audio_cache/``).
        _CACHE_DIRS = {"_audio_cache", "_maya_audio_cache"}
        while os.path.basename(output_dir) in _CACHE_DIRS:
            output_dir = os.path.dirname(output_dir)

        # 2. Get or create the set — remove only previously-synced
        #    nodes (those with a digit suffix like ``label_42``) and
        #    old composite nodes.  Preview nodes from ``load_tracks``
        #    (plain stems at offset 0) are preserved.
        audio_set = cls._get_or_create_set(cat, clear=False)
        for member in list(audio_set.members()):
            name = str(member)
            if name.endswith("_composite"):
                # Clean up old composite WAV from disk
                fpath = cmds.getAttr(f"{name}.filename")
                if (
                    fpath
                    and os.path.isfile(fpath)
                    and "_composite_" in os.path.basename(fpath)
                ):
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
                pm.delete(member)
            else:
                # Check stamped type attr first (new-style)
                if cmds.attributeQuery(cls.NODE_TYPE_ATTR, node=name, exists=True):
                    ntype = cmds.getAttr(f"{name}.{cls.NODE_TYPE_ATTR}") or ""
                    if ntype == "synced":
                        pm.delete(member)
                else:
                    # Legacy fallback: ``{label}_{frame}`` pattern
                    parts = name.rsplit("_", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        pm.delete(member)

        # Re-fetch handle — Maya may have auto-deleted the set if it
        # became empty after the selective removal above.
        if not pm.objExists(audio_set.name()):
            audio_set = pm.sets(name=cls._preferred_set_name(cat), empty=True)

        # 3. Import individual audio nodes at keyed frames
        results: Dict[str, List[str]] = {}
        all_events: List[tuple] = []  # (frame, label) for composite

        for obj in pm.ls(objects):
            keyed = EventTriggers.iter_keyed_events(obj, category=category)
            if not keyed:
                continue

            obj_nodes: List[str] = []
            prev_label = None

            for t, label in keyed:
                # Skip consecutive duplicate labels (from auto_clear pattern)
                if label == prev_label:
                    prev_label = label
                    continue
                prev_label = label

                audio_path = audio_map.get(label.lower())
                if not audio_path:
                    cls.logger.warning(
                        f"No audio file for event '{label}' "
                        f"(frame {int(t)}) on {obj}. "
                        f"Map keys: {sorted(audio_map)[:8]}"
                    )
                    continue

                # Use createNode instead of pm.sound — the sound
                # command silently replaces nodes sharing the same
                # source file, making duplicates impossible.
                clean_name = f"{label}_{int(t)}"
                node_name = cmds.createNode("audio", name=clean_name, skipSelect=True)
                cls._configure_audio_node(node_name, audio_path, t)
                cls._stamp_event_attrs(node_name, label.lower(), "synced")
                cmds.sets(node_name, addElement=audio_set.name())
                obj_nodes.append(node_name)
                all_events.append((t, label))

                cls.logger.debug(f"Imported '{label}' at frame {int(t)} -> {node_name}")

            if obj_nodes:
                results[obj.name()] = obj_nodes

        total = sum(len(v) for v in results.values())
        if total:
            cls.logger.info(f"Imported {total} audio clip(s) into '{audio_set.name()}'")

            # 4. Build composite WAV so all clips play during scrub
            fps = mel.eval("currentTimeUnitToFPS")
            cache_dir = os.path.join(output_dir, "_maya_audio_cache").replace("\\", "/")
            os.makedirs(cache_dir, exist_ok=True)
            comp_path = ptk.AudioUtils.build_composite_wav(
                events=all_events,
                audio_map=audio_map,
                fps=fps,
                output_path=os.path.join(cache_dir, f"_composite_{cat}.wav"),
                logger=cls.logger,
            )
            if comp_path:
                comp_node = cmds.createNode(
                    "audio", name=f"{cat}_composite", skipSelect=True
                )
                cmds.setAttr(f"{comp_node}.filename", comp_path, type="string")
                cmds.setAttr(f"{comp_node}.offset", 0)
                cls._stamp_event_attrs(comp_node, "", "composite")
                cmds.sets(comp_node, addElement=audio_set.name())
                cls.set_active(comp_node)
                cls.logger.debug(
                    f"Composite '{comp_node}' set as active timeline sound"
                )
            else:
                # Composite unavailable (e.g. MP3 sources) — activate
                # the first synced clip so the user at least gets
                # waveform display and playback for that clip.
                first = list(results.values())[0][0]
                cls.set_active(first)
                cls.logger.debug(
                    f"No composite — '{first}' set as active timeline sound"
                )

        return results

    @classmethod
    @CoreUtils.undoable
    def remove(
        cls,
        category: Optional[str] = None,
    ) -> int:
        """Delete all imported audio nodes for a category.

        Removes the ``{cat}_audio_set`` and all its member audio nodes.
        Also clears the time slider / graph editor sound if the active
        node was in the set.

        Parameters:
            category: Attribute prefix (default ``"event"``).

        Returns:
            Number of audio nodes deleted.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        cat = category or EventTriggers.DEFAULT_CATEGORY
        audio_set = cls._find_audio_set(cat)
        if not audio_set:
            return 0

        set_name = audio_set.name()
        members = audio_set.members()
        count = len(members)

        # Clean up composite WAV file from disk before deleting nodes
        for m in members:
            name = str(m)
            if name.endswith("_composite"):
                filepath = cmds.getAttr(f"{name}.filename")
                if (
                    filepath
                    and os.path.isfile(filepath)
                    and "_composite_" in os.path.basename(filepath)
                ):
                    try:
                        os.remove(filepath)
                        cls.logger.debug(f"Deleted composite file: {filepath}")
                    except OSError:
                        pass

        # Clear time slider / graph editor if showing one of our nodes
        if members:
            member_names = {str(m) for m in members}
            cls._clear_active_if_member(member_names)

        # pm.delete(set) cascades: deletes members and the set itself.
        pm.delete(audio_set)
        cls.logger.info(f"Deleted {count} audio node(s) from '{set_name}'")
        return count

    @classmethod
    def set_active(
        cls,
        node_name: str,
        time_slider: bool = True,
        graph_editor: bool = False,
    ) -> None:
        """Set an audio node as the active waveform display.

        Parameters:
            node_name: Name of the audio node.
            time_slider: If True, assign to the time slider.
            graph_editor: If True, assign to the Graph Editor
                (panel must be open).
        """
        if not pm.objExists(node_name):
            cls.logger.warning(f"Audio node '{node_name}' not found.")
            return

        if time_slider:
            try:
                slider = mel.eval("$tmpVar=$gPlayBackSlider")
                if not slider:
                    cls.logger.warning("Could not resolve $gPlayBackSlider")
                    return

                mel.eval(
                    f'timeControl -e -displaySound true -sound "{node_name}" "{slider}"'
                )
                # Fallback via cmds wrapper for Maya builds where MEL may no-op
                cmds.timeControl(slider, e=True, sound=node_name, displaySound=True)

                # Verify it was actually set
                result = cmds.timeControl(slider, q=True, sound=True) or ""
                display = bool(cmds.timeControl(slider, q=True, displaySound=True))
                filepath = (
                    cmds.getAttr(f"{node_name}.filename")
                    if pm.objExists(node_name)
                    else ""
                )
                muted = None
                try:
                    if cmds.attributeQuery("mute", node=node_name, exists=True):
                        muted = bool(cmds.getAttr(f"{node_name}.mute"))
                except Exception:
                    pass
                if result != node_name:
                    cls.logger.warning(
                        f"Sound set to '{result}' instead of '{node_name}' "
                        f"(displaySound={display}, muted={muted}, slider='{slider}', file='{filepath}')"
                    )
                else:
                    cls.logger.debug(
                        f"Time slider sound: '{result}' "
                        f"(displaySound={display}, muted={muted}, slider='{slider}', file='{filepath}')"
                    )
            except Exception as e:
                cls.logger.warning(f"Could not set time slider sound: {e}")

    @classmethod
    def list_nodes(
        cls,
        category: Optional[str] = None,
    ) -> List[str]:
        """Return names of audio nodes in the category's set.

        Parameters:
            category: Attribute prefix (default ``"event"``).

        Returns:
            List of audio node names, or empty list.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        cat = category or EventTriggers.DEFAULT_CATEGORY
        audio_set = cls._find_audio_set(cat)
        if not audio_set:
            return []

        return [str(m) for m in audio_set.members()]

    @classmethod
    @CoreUtils.undoable
    def load_tracks(
        cls,
        audio_files: List[str],
        category: Optional[str] = None,
    ) -> List[str]:
        """Import audio files as preview nodes at offset 0.

        Creates one audio node per file (named after the stem) and adds
        it to the category's ``objectSet``.

        - **New stems** create a fresh audio node.
        - **Existing stems whose file path changed** are updated
          in-place (``_configure_audio_node`` is re-run on the
          existing node) so the preview reflects the new file
          without deleting/recreating the node.
        - **Existing stems with the same path** are skipped.

        Unlike ``sync()``, this does **not** require keyed events — it
        is intended to let the user preview clips before keying.

        Parameters:
            audio_files: Absolute paths to audio files.
            category: Attribute prefix (default ``"event"``).

        Returns:
            List of created *and* updated audio node names.
        """
        from mayatk.node_utils.attributes.event_triggers import EventTriggers

        if not audio_files:
            return []

        cat = category or EventTriggers.DEFAULT_CATEGORY
        audio_set = cls._get_or_create_set(cat, clear=False)

        # Collect existing plain-stem node names so we skip duplicates,
        # and build a map of preview stem -> node name for path-change detection.
        existing_stems: set = set()
        existing_preview_nodes: dict = {}  # {lower_stem: node_name}
        for member in audio_set.members():
            name = str(member)
            # Prefer stamped attr (new-style)
            if cmds.attributeQuery(cls.NODE_TYPE_ATTR, node=name, exists=True):
                ntype = cmds.getAttr(f"{name}.{cls.NODE_TYPE_ATTR}") or ""
                if ntype == "preview":
                    existing_stems.add(name.lower())
                    existing_preview_nodes[name.lower()] = name
            else:
                # Legacy fallback: exclude synced + composite
                parts = name.rsplit("_", 1)
                is_synced = len(parts) == 2 and parts[1].isdigit()
                if not is_synced and not name.endswith("_composite"):
                    existing_stems.add(name.lower())

        created: List[str] = []
        updated: List[str] = []
        for path in audio_files:
            path = path.replace("\\", "/")
            playable_path = cls._resolve_playable_path(path)
            if not playable_path:
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            key = stem.lower()

            if key in existing_stems:
                # Check whether the file path changed — update if so.
                existing_node = existing_preview_nodes.get(key)
                if existing_node:
                    cur_path = (
                        cmds.getAttr(f"{existing_node}.filename") or ""
                    ).replace("\\", "/")
                    if cur_path != playable_path:
                        cls._configure_audio_node(existing_node, playable_path, 0)
                        updated.append(existing_node)
                        cls.logger.debug(
                            f"Updated preview node '{existing_node}': {cur_path} → {playable_path}"
                        )
                    else:
                        cls.logger.debug(f"Preview node '{stem}' unchanged — skipping.")
                else:
                    cls.logger.debug(
                        f"Preview node '{stem}' already exists — skipping."
                    )
                continue
            node_name = cmds.createNode("audio", name=stem, skipSelect=True)
            cls._configure_audio_node(node_name, playable_path, 0)
            cls._stamp_event_attrs(node_name, stem.lower(), "preview")
            cmds.sets(node_name, addElement=audio_set.name())
            created.append(node_name)

        if created:
            cls.set_active(created[0])
            cls.logger.info(f"Loaded {len(created)} track(s). Active: {created[0]}")
        if updated:
            cls.logger.info(f"Updated {len(updated)} existing preview node(s).")

        return created + updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _CACHE_DIR_NAME = "_maya_audio_cache"
    """Sub-directory name used for converted audio caches."""

    @classmethod
    def _cache_dir_for(cls, source_path: str) -> str:
        """Return the cache directory path for a given source audio file."""
        return os.path.join(os.path.dirname(source_path), cls._CACHE_DIR_NAME).replace(
            "\\", "/"
        )

    @classmethod
    def _build_audio_map(cls, search_dir: str) -> Dict[str, str]:
        """Recursively scan a directory for audio files.

        Delegates to ``ptk.AudioUtils.build_audio_map``.
        """
        return ptk.AudioUtils.build_audio_map(
            search_dir,
            cache_dir=cls._cache_dir_for(os.path.join(search_dir, "_")),
            logger=cls.logger,
        )

    @classmethod
    def _build_audio_map_from_file_map(cls, file_map: Dict[str, str]) -> Dict[str, str]:
        """Build an audio map from a ``{stem: path}`` dict.

        Delegates to ``ptk.AudioUtils.build_audio_map_from_file_map``.
        """
        first_path = next(iter(file_map.values()), None)
        cache_dir = cls._cache_dir_for(first_path) if first_path else None
        return ptk.AudioUtils.build_audio_map_from_file_map(
            file_map, cache_dir=cache_dir, logger=cls.logger
        )

    @classmethod
    def _build_audio_map_from_files(cls, audio_files: List[str]) -> Dict[str, str]:
        """Build an audio map from an explicit list of file paths.

        Delegates to ``ptk.AudioUtils.build_audio_map_from_files``.
        """
        cache_dir = cls._cache_dir_for(audio_files[0]) if audio_files else None
        return ptk.AudioUtils.build_audio_map_from_files(
            audio_files, cache_dir=cache_dir, logger=cls.logger
        )

    @classmethod
    def _resolve_playable_path(cls, audio_path: str) -> Optional[str]:
        """Return a Maya-playable path, converting to WAV when required."""
        return ptk.AudioUtils.resolve_playable_path(
            audio_path,
            cache_dir=cls._cache_dir_for(audio_path),
            logger=cls.logger,
        )

    @classmethod
    def _stamp_event_attrs(cls, node_name: str, stem: str, node_type: str) -> None:
        """Stamp event metadata on an audio node.

        Parameters:
            node_name: Audio DG node.
            stem: Lowercase event stem (empty for composite).
            node_type: ``"preview"``, ``"synced"``, or ``"composite"``.
        """
        for attr_name, value in [
            (cls.NODE_STEM_ATTR, stem),
            (cls.NODE_TYPE_ATTR, node_type),
        ]:
            if not cmds.attributeQuery(attr_name, node=node_name, exists=True):
                cmds.addAttr(node_name, ln=attr_name, dt="string")
            cmds.setAttr(f"{node_name}.{attr_name}", value, type="string")

    @classmethod
    def _configure_audio_node(
        cls, node_name: str, audio_path: str, offset: float
    ) -> None:
        """Configure an audio node for reliable Maya playback.

        Using ``setAttr(filename)`` alone can leave some Maya builds with
        visible waveforms but silent playback. Running the ``sound`` command
        in edit mode forces Maya to initialize internal audio data.
        """
        path = audio_path.replace("\\", "/")

        # Basic attrs first
        cmds.setAttr(f"{node_name}.filename", path, type="string")
        cmds.setAttr(f"{node_name}.offset", offset)

        # Force Maya audio initialization via sound command.
        try:
            cmds.sound(node_name, e=True, file=path, offset=offset)
        except Exception as exc:
            cls.logger.warning(
                f"sound edit failed for '{node_name}': {exc}; using setAttr fallback"
            )

        # Ensure not muted if attr exists.
        try:
            if cmds.attributeQuery("mute", node=node_name, exists=True):
                cmds.setAttr(f"{node_name}.mute", 0)
        except Exception:
            pass

    @classmethod
    def _get_or_create_set(cls, category: str, clear: bool = False) -> "pm.PyNode":
        """Return the ``{cat}_audio_set``, creating if needed.

        Parameters:
            category: Category prefix.
            clear: If True, delete existing member nodes first.

        Returns:
            The objectSet PyNode.
        """
        set_name = cls._preferred_set_name(category)
        audio_set = cls._find_audio_set(category)

        if (
            audio_set
            and str(audio_set.name()) != set_name
            and not pm.objExists(set_name)
        ):
            audio_set = pm.rename(audio_set, set_name)

        if audio_set:
            if clear:
                # Maya auto-deletes an objectSet when its last member
                # is removed, and pm.delete(set) takes members with it.
                # Safest approach: clear display refs, delete the whole
                # set (members included), then recreate empty.
                members = audio_set.members()
                if members:
                    member_names = {str(m) for m in members}
                    cls._clear_active_if_member(member_names)
                pm.delete(audio_set)
                return pm.sets(name=set_name, empty=True)
            return audio_set

        return pm.sets(name=set_name, empty=True)

    @classmethod
    def _preferred_set_name(cls, category: str) -> str:
        """Return canonical objectSet name for a category."""
        cat = str(category)
        if cat == "audio":
            return "audio_set"
        return f"{cat}_audio_set"

    @classmethod
    def _find_audio_set(cls, category: str) -> Optional["pm.PyNode"]:
        """Find existing set by canonical or legacy name."""
        preferred = cls._preferred_set_name(category)
        if pm.objExists(preferred):
            return pm.PyNode(preferred)

        legacy = f"{category}_audio_set"
        if legacy != preferred and pm.objExists(legacy):
            return pm.PyNode(legacy)

        return None

    @classmethod
    def _clear_active_if_member(cls, member_names: set) -> None:
        """Clear time slider / graph editor sound if it's one of ours.

        Silently skips in batch/headless mode where UI elements don't exist.
        """
        if cmds.about(batch=True):
            return

        # Time slider
        try:
            slider = mel.eval("$tmpVar=$gPlayBackSlider")
            current = cmds.timeControl(slider, q=True, sound=True)
            if current and current in member_names:
                cmds.timeControl(slider, e=True, sound="", displaySound=False)
        except Exception:
            pass


AudioEvents.set_log_level(logging.INFO)
