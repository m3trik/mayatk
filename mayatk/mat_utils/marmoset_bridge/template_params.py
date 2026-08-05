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
    # Bake output. BAKE_PADDING / BAKE_BITS are deliberately absent: they are
    # managed values derived per send by :meth:`TemplateParams.derive_bake_values`
    # (padding from the map size via pythontk's UV-padding primitive; bit depth
    # from the per-map-type output templates) -- never user-tunable.
    "BAKE_SIZE": 4096,
    "BAKE_SAMPLES": 16,
    "OUTPUT_FORMAT": "png",
    # Bake maps to enable -- geometry maps
    "MAP_NORMAL": True,
    "MAP_AO": True,
    "MAP_CURVATURE": False,
    "MAP_THICKNESS": False,
    "MAP_POSITION": False,
    "MAP_MATID": False,
    # Bake maps to enable -- surface transfer (sample the source materials)
    "MAP_ALBEDO": True,
    "MAP_ROUGHNESS": True,
    "MAP_METALNESS": True,
    "MAP_EMISSIVE": False,
    # Source/target pairing. BAKE_SOURCE_SET is the panel's action row
    # (Set/Select/Clear the scene's bake-source objectSet) -- a command
    # widget, not a value; the empty default keeps its echo token
    # substitutable.
    "BAKE_SOURCE_SET": "",
    "HIGH_SUFFIX": "_source",
    "LOW_SUFFIX": "",
    # Whether a suffix on an ANCESTOR group classifies every mesh beneath it
    # (tag the group root once) or only a mesh's own name counts.
    "SUFFIX_INCLUDE_CHILDREN": True,
    "CAGE_OFFSET": 0.02,
    "IGNORE_BACKFACES": True,
    # Host-side: wire the roundtrip's baked maps into a StingrayPBS material
    # assigned to the bake-target meshes. Echo-referenced by bake.py.
    "ASSIGN_MATERIAL": True,
    # Look-dev
    "SKY_PRESET": "Marmoset Skies/Hangar.tbsky",
    "FRAME_SELECTION": True,
    # Host-side export scope. Not a Toolbag value -- the bridge slots resolve it
    # before launch to pick WHICH objects get exported. It is registered here so
    # the ``scope=__SCOPE__`` echo in each send template (which is what makes the
    # panel surface the Scope combo) substitutes like any other token instead of
    # leaking a raw placeholder into the rendered script.
    "SCOPE": "selected",
}


class TemplateParams:
    """TemplateParams — module namespace."""

    #: Constant filename stem the bake template writes under (its
    #: ``_output_stem``). Deliberately NOT the scene/model name -- the
    #: texture set (= material) is the identity the maps carry; the engine
    #: strips ``"<stem>_"`` on roundtrip relocation so production files land
    #: as ``<material>_<map>.<ext>``. One home for both sides of that
    #: contract (the rendered template quotes it; keep them in step).
    BAKE_OUTPUT_STEM = "bake"

    #: ``MAP_*`` toggle -> the MapFactory taxonomy name its baked file carries
    #: (mirrors ``_ENABLED_MAPS`` in ``templates/bake.py``); used to resolve
    #: each enabled map's output spec when deriving the bake bit depth.
    MAP_KEY_TYPES: Dict[str, str] = {
        "MAP_NORMAL": "Normal_OpenGL",
        "MAP_AO": "AO",
        "MAP_CURVATURE": "Curvature",
        "MAP_THICKNESS": "Thickness",
        "MAP_POSITION": "Position",
        "MAP_MATID": "MatID",
        "MAP_ALBEDO": "Base_Color",
        "MAP_ROUGHNESS": "Roughness",
        "MAP_METALNESS": "Metallic",
        "MAP_EMISSIVE": "Emissive",
    }

    @staticmethod
    def derive_bake_values(values: Dict[str, Any]) -> Dict[str, Any]:
        """Return the managed bake tokens derived from *values*.

        These are deliberately not user-tunable -- each has one in-house
        source of truth:

        * ``BAKE_PADDING``: pixels of edge bleed from the map size via
          :meth:`pythontk.MathUtils.calculate_uv_padding` -- the same
          primitive that drives shell/edge spacing everywhere else, so the
          bake's dilation always matches the UV layouts it fills.
        * ``BAKE_BITS``: the max per-channel bit depth the per-map-type
          :class:`pythontk.OutputTemplates` specs ask for across the enabled
          maps (Toolbag's ``outputBits`` is bake-wide, so the deepest map
          wins; the others cost disk, not correctness).
        """
        import pythontk as ptk

        size = int(values.get("BAKE_SIZE") or 4096)
        enabled = [
            map_type
            for key, map_type in TemplateParams.MAP_KEY_TYPES.items()
            if values.get(key)
        ]
        return {
            "BAKE_PADDING": ptk.MathUtils.calculate_uv_padding(size),
            "BAKE_BITS": max(
                (ptk.OutputTemplates.resolve(t).bit_depth for t in enabled),
                default=8,
            ),
        }

    @staticmethod
    def python_literal(value: Any) -> str:
        """Format *value* as a Python source literal for template substitution.

        ``repr`` covers every type the registry uses -- ``repr(True) == 'True'``,
        ``repr(4096) == '4096'``, ``repr('_high') == "'_high'"`` -- so a
        substituted token is valid Python when the template assigns it bare
        (e.g. ``SKY_PRESET = __SKY_PRESET__``).
        """
        return repr(value)

    @staticmethod
    def defaults() -> Dict[str, Any]:
        """Return a copy of :data:`DEFAULTS`."""
        return dict(DEFAULTS)

    @staticmethod
    def to_context(values: Dict[str, Any]) -> Dict[str, str]:
        """Map ``{KEY: value}`` to ``{KEY: python-literal-string}``.

        The result is suitable for ``StrUtils.replace_delimited``: every value
        becomes a Python source literal that can be substituted into a bare
        ``__KEY__`` token in a template.
        """
        return {
            key: TemplateParams.python_literal(value) for key, value in values.items()
        }
