# coding=utf-8
"""CSV mapping resolver — facade over the engine implementation.

The mapping system (JSON mapping files → ``ColumnMap`` + audio/behavior
pipeline) lives once, DCC-agnostic, in
:mod:`pythontk.core_utils.engines.shots.manifest.mapping._mapping` (shared
with blendertk; built-ins ship with the engine and user mappings go under
``user_config_root()/shots/manifest_mappings/``).  This module re-exports it
so mayatk-internal imports — including the test seams on the private
``_audio_*`` / ``_build_*`` builders — keep working.
"""
from pythontk.core_utils.engines.shots.manifest.mapping._mapping import (  # noqa: F401
    DEFAULT_DIR,
    _AUDIO_BUILDERS,
    _audio_derive,
    _audio_map,
    _audio_prefix,
    _audio_regex,
    _build_audio_resolver,
    _build_column_map,
    _build_default_behaviors,
    _build_pipeline,
    _chain,
    discover,
    load_mapping,
    resolve,
    templates,
)
