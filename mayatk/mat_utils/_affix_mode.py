# !/usr/bin/python
# coding=utf-8
"""Shared affix-mode option-box helper for mat_utils slot files.

Pairs an affix text field (QLineEdit or uitk LineEdit) with a 3-option
combobox in its ``option_box.menu`` so slot files can offer a uniform
"Auto / Suffix / Prefix" affix picker without duplicating the wiring.
"""
from typing import Tuple

import pythontk as ptk


# Indices map to AFFIX_MODE_VALUES one-for-one.
AFFIX_MODE_LABELS = ("Auto (by _ placement)", "Suffix", "Prefix")
AFFIX_MODE_VALUES = ("auto", "suffix", "prefix")
AFFIX_MODE_TOOLTIP = (
    "How the affix text is applied to the base name:\n"
    "  Auto — leading '_' (e.g. '_MAT') is treated as a suffix;\n"
    "         trailing '_' (e.g. 'MAT_') is treated as a prefix.\n"
    "  Suffix — always appended (e.g. 'brick' + '_MAT' → 'brick_MAT').\n"
    "  Prefix — always prepended (e.g. 'MAT_' + 'brick' → 'MAT_brick')."
)


def add_affix_mode_menu(widget, default_mode: str = "auto", on_change=None):
    """Wire a 3-option affix-mode combobox onto ``widget.option_box.menu``.

    The combobox is added under the object name ``cmb_affix_mode`` and
    seeded to *default_mode*. Use :func:`current_affix_mode` or
    :func:`resolve_affix` to read state back.

    Parameters:
        widget: An affix text field exposing ``option_box.menu``
            (QLineEdit, uitk LineEdit, or any patched text widget).
        default_mode: Initial selection — one of ``"auto"``, ``"suffix"``,
            ``"prefix"``.
        on_change: Optional callable invoked with the new mode string
            whenever the user changes the combobox.
    """
    widget.option_box.menu.add(
        "QComboBox",
        setObjectName="cmb_affix_mode",
        addItems=list(AFFIX_MODE_LABELS),
        setToolTip=AFFIX_MODE_TOOLTIP,
    )
    cmb = widget.option_box.menu.cmb_affix_mode
    cmb.setCurrentIndex(AFFIX_MODE_VALUES.index(default_mode))

    if on_change is not None:
        cmb.currentIndexChanged.connect(
            lambda _idx, w=widget: on_change(current_affix_mode(w))
        )


def current_affix_mode(widget) -> str:
    """Return the currently selected affix mode ('auto'/'suffix'/'prefix')."""
    cmb = getattr(widget.option_box.menu, "cmb_affix_mode", None)
    if cmb is None:
        return "auto"
    idx = max(0, cmb.currentIndex())
    return AFFIX_MODE_VALUES[idx] if idx < len(AFFIX_MODE_VALUES) else "auto"


def resolve_affix(widget, default: str = "prefix") -> Tuple[str, str]:
    """Read widget text + mode and return ``(prefix, suffix)`` per the picker.

    *default* is the fallback mode used when the user selected Auto but the
    text has no boundary delimiter. Matches the ``StrUtils.split_affix``
    library default of ``"prefix"``.
    """
    return ptk.StrUtils.split_affix(
        widget.text(), mode=current_affix_mode(widget), default=default
    )
