# coding=utf-8
"""Retired: the CSV mapping resolver lives in pythontk.

This package was a pure re-export of
``pythontk.core_utils.engines.shots.manifest.mapping`` (``Mapping``,
``MappingSpec``, ``DEFAULT_DIR``, ``AUDIO_METHODS``); import them from there.
The old names resolve here, with a deprecation warning, for one release.
"""

import pythontk as ptk

_ENGINE = "pythontk.core_utils.engines.shots.manifest.mapping"
ptk.Deprecation.attributes(
    globals(),
    {
        "Mapping": f"{_ENGINE}.Mapping",
        "MappingSpec": f"{_ENGINE}.MappingSpec",
        "DEFAULT_DIR": f"{_ENGINE}.DEFAULT_DIR",
        "AUDIO_METHODS": f"{_ENGINE}.AUDIO_METHODS",
    },
    remove_in="0.22.0",
    since="2026-09-26",
)
