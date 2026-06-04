# !/usr/bin/python
# coding=utf-8
"""Plain default values + literal formatting for Marmoset template tokens.

DCC- and UI-agnostic. This module is the single source of truth for the
*values* a template's ``__KEY__`` tokens default to; it deliberately has
no knowledge of Qt or widget specs. UI layers (the extapps panel, the
mayatk slots) build their own ``AttributeSpec`` widget registries on top
of these keys and pass user-edited values back to
:meth:`MarmosetEngine.send` as a plain dict.

``MarmosetEngine.render_template`` merges :data:`DEFAULTS` with the
caller's overrides and feeds the result through :func:`to_context`, which
turns each value into a Python source literal for
``StrUtils.replace_delimited`` substitution into ``templates/*.py``.
"""
from __future__ import annotations

from typing import Any, Dict


# The value each registered template token defaults to. Keys are bare
# token names (no ``__`` delimiters); the delimiters are added by
# ``StrUtils.replace_delimited`` at substitution time.
DEFAULTS: Dict[str, Any] = {
    # Bake output
    "BAKE_SIZE": 4096,
    "BAKE_SAMPLES": 16,
    "BAKE_PADDING": 16,
    "BAKE_BITS": 8,
    # Bake maps to enable
    "MAP_NORMAL": True,
    "MAP_AO": True,
    "MAP_CURVATURE": True,
    "MAP_THICKNESS": False,
    "MAP_POSITION": False,
    "MAP_MATID": True,
    # High/Low pairing (suffix convention)
    "HIGH_SUFFIX": "_high",
    "LOW_SUFFIX": "",
    "CAGE_OFFSET": 0.02,
    "IGNORE_BACKFACES": True,
    # Look-dev
    "SKY_PRESET": "Marmoset Skies/Hangar.tbsky",
    "FRAME_SELECTION": True,
}


def python_literal(value: Any) -> str:
    """Format *value* as a Python source literal for template substitution.

    ``repr`` covers every type the registry uses -- ``repr(True) == 'True'``,
    ``repr(4096) == '4096'``, ``repr('_high') == "'_high'"`` -- so a
    substituted token is valid Python when the template assigns it bare
    (e.g. ``SKY_PRESET = __SKY_PRESET__``).
    """
    return repr(value)


def defaults() -> Dict[str, Any]:
    """Return a copy of :data:`DEFAULTS`."""
    return dict(DEFAULTS)


def to_context(values: Dict[str, Any]) -> Dict[str, str]:
    """Map ``{KEY: value}`` to ``{KEY: python-literal-string}``.

    The result is suitable for ``StrUtils.replace_delimited``: every value
    becomes a Python source literal that can be substituted into a bare
    ``__KEY__`` token in a template.
    """
    return {key: python_literal(value) for key, value in values.items()}
