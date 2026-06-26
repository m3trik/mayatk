# !/usr/bin/python
# coding=utf-8
"""Maya hotkey collision checker for the uitk HotkeyEditor.

Hosts that integrate the editor inside Maya register
:func:`maya_collision_checker` via
``editor.add_collision_checker(...)``. The checker queries Maya's active
hotkey set and reports any runtime command bound to the same key
combination as a soft warning — Maya's bindings cannot be auto-cleared
from outside Maya's own Hotkey Editor, so users decide whether to
proceed.
"""
from typing import List, Optional

import maya.cmds as cmds


# Qt single-character keys come through as upper-case glyphs ("S"); Maya's
# keyShortcut wants lower-case for letter keys. Function keys, navigation
# keys, etc. are passed through. Fixups translate Qt-style names to the
# strings Maya's hotkey API expects.
_KEY_FIXUPS = {
    "Esc": "Escape",
    "Return": "Enter",
    "Del": "Delete",
    "Ins": "Insert",
    "PgUp": "Page_Up",
    "PgDown": "Page_Down",
    "Backspace": "Backspace",
    "Space": "Space",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "Tab": "Tab",
    "Home": "Home",
    "End": "End",
}


def parse_qt_sequence(sequence: str) -> Optional[dict]:
    """Convert a Qt key sequence string to ``cmds.hotkey`` query kwargs.

    Returns None when the sequence cannot be cleanly mapped — multi-step
    sequences like ``"Ctrl+K, Ctrl+S"``, Meta-modified keys (which Maya
    does not surface uniformly), or empty input. Callers treat None as
    "no Maya conflict reportable".
    """
    if not sequence or "," in sequence:
        return None

    parts = [p.strip() for p in sequence.split("+") if p.strip()]
    if not parts:
        return None

    key = parts[-1]
    modifiers = parts[:-1]

    kwargs: dict = {}
    for mod in modifiers:
        m = mod.lower()
        if m == "ctrl":
            kwargs["ctrlModifier"] = True
        elif m == "alt":
            kwargs["altModifier"] = True
        elif m == "shift":
            # Maya treats shift as part of the keyShortcut (e.g. "S" vs "s")
            # rather than a separate flag, but the modifier flag does exist
            # for non-printable keys.
            kwargs["shiftModifier"] = True
        elif m == "meta":
            return None  # Win key — Maya doesn't expose this consistently
        else:
            return None  # Unknown modifier

    if len(key) == 1:
        # If shift was specified for a letter, Maya wants the upper-case
        # form and no shiftModifier flag (it's redundant / inconsistent).
        if kwargs.get("shiftModifier"):
            key = key.upper()
            kwargs.pop("shiftModifier", None)
        else:
            key = key.lower()
    else:
        key = _KEY_FIXUPS.get(key, key)

    kwargs["keyShortcut"] = key
    return kwargs


def _ks_modifier(ks: list, mod: str) -> bool:
    """Read a modifier flag from an assignCommand keyString array.

    Maya 2025's keyString is a 7-element list:
        [key, alt, ctrl, ?, shift, release, ?]

    Older Maya versions report 6 elements with a slightly different order;
    this helper handles both common shapes. Modifier flags are stored as
    string ``"0"``/``"1"``.
    """
    if not ks:
        return False
    # 7-element layout (Maya 2025+)
    if len(ks) >= 7:
        idx = {"alt": 1, "ctrl": 2, "shift": 4}.get(mod)
    else:
        # 6-element legacy layout: [key, alt, ctrl, shift, ?, release]
        idx = {"alt": 1, "ctrl": 2, "shift": 3}.get(mod)
    if idx is None or idx >= len(ks):
        return False
    return ks[idx] == "1"


def keystring_to_token(ks: list) -> str:
    """Convert an ``assignCommand`` keyString array to a Maya hotkey token.

    e.g. ``["I", "0", "1", ...]`` (Ctrl+I) -> ``"ctl+i"``. A single upper-case
    letter is normalised to ``sht+`` + its lower-case form (Maya stores
    shift+letter as the upper-case glyph). Returns ``""`` for an empty array
    or a keyless entry — Maya reports the key as the literal string ``"NONE"``
    (case varies across versions) for a runtime/name command whose hotkey has
    been cleared.
    """
    if not ks or not ks[0] or str(ks[0]).strip().lower() == "none":
        return ""
    key = ks[0]
    ctrl = _ks_modifier(ks, "ctrl")
    alt = _ks_modifier(ks, "alt")
    shift = _ks_modifier(ks, "shift")
    if len(key) == 1 and key.isalpha() and key.isupper():
        shift = True
        key = key.lower()
    mods = []
    if ctrl:
        mods.append("ctl")
    if alt:
        mods.append("alt")
    if shift:
        mods.append("sht")
    return "+".join(mods + [key])


def live_hotkey_map() -> dict:
    """Return ``{runtime_command: maya_key_token}`` for the active hotkey set.

    Reads Maya's live ``assignCommand`` registry — the source of truth that
    reflects bindings made through the Macro Manager *and* Maya's own Hotkey
    Editor. Empty outside an interactive Maya (the registry is unavailable in
    ``mayapy`` standalone, where ``numElements`` is ``None``).
    """
    result: dict = {}
    try:
        count = cmds.assignCommand(query=True, numElements=True) or 0
    except Exception:
        return result
    for i in range(1, count + 1):
        try:
            ks = cmds.assignCommand(i, query=True, keyString=True)
            cmd = cmds.assignCommand(i, query=True, command=True) or ""
        except Exception:
            continue
        if not cmd or not ks:
            continue
        token = keystring_to_token(ks)
        if token:
            result[cmd] = token
    return result


def _find_bound_command(parsed: dict) -> str:
    """Return the runtime command bound to the parsed shortcut, or ''.

    Iterates the active hotkey set's ``assignCommand`` registry and
    matches by key + modifier flags. Returns the runtime command name
    (resolved from the bound name command) so messages name something
    a Maya user can find in their Hotkey Editor.
    """
    target_key = parsed.get("keyShortcut", "")
    target_alt = bool(parsed.get("altModifier"))
    target_ctrl = bool(parsed.get("ctrlModifier"))
    target_shift = bool(parsed.get("shiftModifier"))

    try:
        count = cmds.assignCommand(query=True, numElements=True) or 0
    except Exception:
        return ""

    for i in range(1, count + 1):
        try:
            ks = cmds.assignCommand(i, query=True, keyString=True)
        except Exception:
            continue
        if not ks or ks[0] != target_key:
            continue
        if (
            _ks_modifier(ks, "alt") != target_alt
            or _ks_modifier(ks, "ctrl") != target_ctrl
            or _ks_modifier(ks, "shift") != target_shift
        ):
            continue
        # Maya 2025's nameCommand doesn't support a query flag, so resolve
        # the wrapped runtime command via assignCommand directly. That's
        # the name a user sees in Maya's Hotkey Editor.
        try:
            rt_name = cmds.assignCommand(i, query=True, command=True) or ""
        except Exception:
            rt_name = ""
        if rt_name:
            return rt_name
        try:
            nc_name = cmds.assignCommand(i, query=True, name=True) or ""
        except Exception:
            nc_name = ""
        return nc_name
    return ""


def _current_hotkey_set() -> str:
    """Return the active hotkey set name, or '' if it can't be queried."""
    try:
        return cmds.hotkeySet(query=True, current=True) or ""
    except Exception:
        return ""


def maya_collision_checker(sequence, scope, ui_name, method_name):
    """Check a proposed binding against Maya's active hotkey set.

    Args:
        sequence: Qt key sequence string (e.g. ``"Ctrl+Alt+S"``).
        scope: ``"window"`` or ``"application"`` — informational.
        ui_name: UI being edited — informational.
        method_name: Slot being assigned — informational.

    Returns:
        A list of ``CollisionConflict`` entries (imported lazily so the
        module is still importable when uitk isn't installed).
    """
    from uitk.widgets.editors.hotkey_editor import CollisionConflict

    conflicts: List = []

    parsed = parse_qt_sequence(sequence)
    if parsed is None:
        return conflicts

    bound = _find_bound_command(parsed)
    if not bound:
        return conflicts

    set_name = _current_hotkey_set()
    desc = f"Maya runtime command '{bound}'"
    if set_name:
        desc += f" (hotkey set: {set_name})"

    conflicts.append(
        CollisionConflict(
            source="maya",
            description=desc,
            breaks_binding=False,  # external — cannot auto-clear Maya's binding
        )
    )
    return conflicts
