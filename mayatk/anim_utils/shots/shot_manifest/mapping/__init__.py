# coding=utf-8
"""CSV mapping resolver — interprets JSON mapping files.

A mapping file is a ``.json`` file that declaratively specifies how
CSV columns map to :class:`BuilderStep` fields and how derived values
(e.g. audio objects) are resolved.

Example ``my_project.json``::

    {
        "columns": {
            "step_id": ["Step"],
            "description": ["Step Contents"],
            "assets": ["Asset Names"],
            "audio": ["Voice Support"],
            "exclude_steps": ["SETUP"],
            "exclude_values": {"assets": ["N/A"]},
            "metadata_pass": {"priority": ["Priority"]}
        },
        "audio_resolve": {
            "method": "prefix",
            "directory": "//server/project/audio",
            "extensions": [".wav", ".mp3"]
        },
        "default_behaviors": {
            "audio": ["set_clip"],
            "scene": ["fade_in"]
        }
    }

Supported ``audio_resolve`` methods:

- ``"prefix"``: Match files starting with ``{step_id}_``.
- ``"regex"``: Match files against a regex pattern with
  ``{step_id}`` placeholder.
- ``"map"``: Explicit ``step_id → clip_stem`` lookup table.
- ``"derive"``: Build clip name from ``step_id`` + first N words
  of the audio text (PascalCase).

API
---
:func:`discover` — list available mapping names in a directory.
:func:`load_mapping` — read a JSON mapping and return a callable.
:func:`resolve` — ``(csv_path, mapping_name) → List[BuilderStep]``.
"""
import json
import logging
import re as _re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderObject,
    BuilderStep,
    ColumnMap,
    parse_csv,
)

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DIR",
    "discover",
    "load_mapping",
    "resolve",
]

DEFAULT_DIR: Path = Path(__file__).parent
"""Default directory for mapping JSON files."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover(directory: Optional[str] = None) -> List[str]:
    """List available mapping names (without ``.json``) in *directory*.

    Excludes files starting with ``_``.
    """
    d = Path(directory) if directory else DEFAULT_DIR
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json") if not p.stem.startswith("_"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_mapping(
    name: str,
    directory: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a mapping JSON by *name* and return the parsed dict.

    Parameters:
        name: Mapping stem (e.g. ``"c5m_training"``).  Can also be a
            full path ending in ``.json``.
        directory: Folder containing mapping files.
            Defaults to :data:`DEFAULT_DIR`.

    Raises:
        FileNotFoundError: If no matching ``.json`` file exists.
    """
    if name.endswith(".json"):
        path = Path(name)
    else:
        d = Path(directory) if directory else DEFAULT_DIR
        path = d / f"{name}.json"

    if not path.is_file():
        raise FileNotFoundError(f"Mapping not found: {path}")

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Resolution  (JSON → List[BuilderStep])
# ---------------------------------------------------------------------------


def resolve(
    csv_path: str,
    mapping: Optional[Dict[str, Any]] = None,
    *,
    name: Optional[str] = None,
    directory: Optional[str] = None,
) -> List[BuilderStep]:
    """Parse a CSV through a mapping and return fully resolved steps.

    Provide *mapping* (already-loaded dict) **or** *name* (to load from
    disk).  If neither is given, uses default ``ColumnMap``.

    Parameters:
        csv_path: Path to the CSV file.
        mapping: Pre-loaded mapping dict.
        name: Mapping file stem to load via :func:`load_mapping`.
        directory: Search directory for :func:`load_mapping`.
    """
    if mapping is None and name is not None:
        mapping = load_mapping(name, directory)

    col_map, post = _build_pipeline(mapping or {})
    return parse_csv(csv_path, columns=col_map, post_process=post)


# ---------------------------------------------------------------------------
# Pipeline builder  (JSON dict → ColumnMap + post_process)
# ---------------------------------------------------------------------------


def _build_pipeline(
    mapping: Dict[str, Any],
) -> tuple:
    """Convert a mapping dict into ``(ColumnMap, post_process)``."""
    col_map = _build_column_map(mapping.get("columns", {}))

    processors: List[Callable[[BuilderStep], None]] = []

    audio_cfg = mapping.get("audio_resolve")
    if audio_cfg:
        processors.append(_build_audio_resolver(audio_cfg))

    behav_cfg = mapping.get("default_behaviors")
    if behav_cfg:
        processors.append(_build_default_behaviors(behav_cfg))

    post = _chain(processors) if processors else None
    return col_map, post


def _build_column_map(columns: Dict[str, Any]) -> ColumnMap:
    """Construct a :class:`ColumnMap` from the ``columns`` section."""
    if not columns:
        return ColumnMap()
    return ColumnMap.from_dict(columns)


# ---------------------------------------------------------------------------
# Audio resolvers  (JSON config → callable)
# ---------------------------------------------------------------------------


def _build_audio_resolver(
    cfg: Dict[str, Any],
) -> Callable[[BuilderStep], None]:
    """Dispatch to the right resolver based on ``cfg["method"]``."""
    method = cfg.get("method", "prefix")
    if method == "prefix":
        return _audio_prefix(
            cfg.get("directory", ""),
            cfg.get("extensions", (".wav", ".mp3")),
        )
    elif method == "regex":
        return _audio_regex(
            cfg.get("directory", ""),
            cfg["pattern"],
            cfg.get("extensions", (".wav", ".mp3")),
        )
    elif method == "map":
        return _audio_map(cfg.get("clips", {}))
    elif method == "derive":
        return _audio_derive(
            cfg.get("words", 3),
            cfg.get("separator", "_"),
            cfg.get("directory", ""),
            cfg.get("extensions", (".wav", ".mp3")),
        )
    else:
        raise ValueError(f"Unknown audio_resolve method: {method!r}")


def _audio_prefix(
    directory: str,
    extensions: Sequence[str] = (".wav", ".mp3"),
) -> Callable[[BuilderStep], None]:
    """Match audio files by ``{step_id}_*`` prefix."""
    audio_dir = Path(directory)
    ext_set = {e.lower() for e in extensions}

    def _resolve(step: BuilderStep) -> None:
        if not audio_dir.is_dir():
            return
        prefix = f"{step.step_id}_".lower()
        for f in audio_dir.iterdir():
            if f.suffix.lower() in ext_set and f.stem.lower().startswith(prefix):
                step.objects.append(
                    BuilderObject(
                        name=f.stem,
                        kind="audio",
                        source_path=str(f),
                    )
                )
                return

    return _resolve


def _audio_regex(
    directory: str,
    pattern: str,
    extensions: Sequence[str] = (".wav", ".mp3"),
) -> Callable[[BuilderStep], None]:
    """Match audio files by regex with ``{step_id}`` placeholder."""
    audio_dir = Path(directory)
    ext_set = {e.lower() for e in extensions}

    def _resolve(step: BuilderStep) -> None:
        if not audio_dir.is_dir():
            return
        compiled = _re.compile(
            pattern.format(step_id=_re.escape(step.step_id)),
            _re.IGNORECASE,
        )
        for f in audio_dir.iterdir():
            if f.suffix.lower() in ext_set and compiled.search(f.stem):
                step.objects.append(
                    BuilderObject(
                        name=f.stem,
                        kind="audio",
                        source_path=str(f),
                    )
                )
                return

    return _resolve


def _audio_map(
    clips: Dict[str, str],
) -> Callable[[BuilderStep], None]:
    """Set audio from an explicit ``{step_id: clip_stem}`` dict."""

    def _resolve(step: BuilderStep) -> None:
        clip = clips.get(step.step_id, "")
        if clip:
            step.objects.append(BuilderObject(name=clip, kind="audio"))

    return _resolve


def _audio_derive(
    words: int = 3,
    separator: str = "_",
    directory: str = "",
    extensions: Sequence[str] = (".wav", ".mp3"),
) -> Callable[[BuilderStep], None]:
    """Derive audio clip from ``step_id`` + first N words of ``audio``.

    Generates a PascalCase clip name like ``A01_WelcomeToThe`` from the
    step's voiceover text.  If *directory* is provided and a matching
    file exists, ``source_path`` is also set on the audio object.

    Parameters:
        words: Number of leading words to take from the audio text.
        separator: Join character between step_id and the word block.
        directory: Optional directory to scan for a matching file.
        extensions: File extensions to consider when scanning.
    """
    audio_dir = Path(directory) if directory else None
    ext_set = {e.lower() for e in extensions}
    # Strip non-alphanumeric except underscores for safe file names
    _clean_re = _re.compile(r"[^\w]+")

    def _resolve(step: BuilderStep) -> None:
        if not step.audio or step.audio.upper() == "N/A":
            return
        tokens = step.audio.split(None, words)[:words]
        if not tokens:
            return
        clean = [_clean_re.sub("", w).capitalize() for w in tokens]
        clip_name = step.step_id + separator + "".join(clean)
        source = ""

        # If a directory is configured, try to find the actual file
        if audio_dir and audio_dir.is_dir():
            target = clip_name.lower()
            for f in audio_dir.iterdir():
                if f.suffix.lower() in ext_set and f.stem.lower() == target:
                    source = str(f)
                    break

        step.objects.append(
            BuilderObject(name=clip_name, kind="audio", source_path=source)
        )

    return _resolve


# ---------------------------------------------------------------------------
# Default-behaviors applicator  (JSON config → callable)
# ---------------------------------------------------------------------------


def _build_default_behaviors(
    cfg: Dict[str, List[str]],
) -> Callable[[BuilderStep], None]:
    """Assign default behaviors to objects by *kind*.

    ``cfg`` maps object kinds to behavior name lists::

        {"audio": ["set_clip"], "scene": ["fade_in", "fade_out"]}

    Only behaviors not already present on the object are added.
    """

    def _apply(step: BuilderStep) -> None:
        for obj in step.objects:
            defaults = cfg.get(obj.kind, [])
            for b in defaults:
                if b not in obj.behaviors:
                    obj.behaviors.append(b)

    return _apply


# ---------------------------------------------------------------------------
# Internal combinators
# ---------------------------------------------------------------------------


def _chain(
    processors: List[Callable[[BuilderStep], None]],
) -> Callable[[BuilderStep], None]:
    """Chain multiple post-process callables into one."""
    if len(processors) == 1:
        return processors[0]

    def _chained(step: BuilderStep) -> None:
        for proc in processors:
            proc(step)

    return _chained
