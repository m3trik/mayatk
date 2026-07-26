# coding=utf-8
"""CSV mapping resolver — facade over the engine implementation.

The mapping system (JSON mapping files → ``ColumnMap`` + audio/behavior
pipeline) lives once, DCC-agnostic, in
:mod:`pythontk.core_utils.engines.shots.manifest.mapping._mapping` (shared
with blendertk; built-ins ship with the engine and user mappings go under
``user_config_root()/shots/manifest_mappings/``).  This module re-exports the
:class:`Mapping` class; call the resolver methods as ``Mapping.resolve`` etc.
(private ``_audio_*`` / ``_build_*`` builders live on the engine class too).
"""

from pythontk.core_utils.engines.shots.manifest.mapping._mapping import (  # noqa: F401
    DEFAULT_DIR,
    Mapping,
    _AUDIO_BUILDERS,
)
