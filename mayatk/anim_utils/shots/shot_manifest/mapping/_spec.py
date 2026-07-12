# coding=utf-8
"""Mapping-file schema — facade over the engine spec.

:class:`MappingSpec` (the self-validating, self-documenting schema for one
CSV-mapping file) and the ``AUDIO_METHODS`` registry live once in
:mod:`pythontk.core_utils.engines.shots.manifest.mapping._spec`; re-exported
here so mayatk-internal imports keep working.
"""
from pythontk.core_utils.engines.shots.manifest.mapping._spec import (  # noqa: F401
    AUDIO_METHODS,
    AudioMethod,
    MappingSpec,
    format_markdown,
    validate_audio_resolve,
    validate_default_behaviors,
)
