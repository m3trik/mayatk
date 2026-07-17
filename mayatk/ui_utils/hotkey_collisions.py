# !/usr/bin/python
# coding=utf-8
"""Maya hotkey collision checker for the uitk ShortcutEditor.

Hosts that integrate the editor inside Maya register
:func:`maya_collision_checker` via
``editor.add_collision_checker(...)``. The checker queries the active hotkey
set's **global (viewport) context** and reports the runtime command bound to
the same key there — the only binding that competes with an application-wide
Qt shortcut. The conflict carries a clear-action that unbinds Maya's hotkey
when the active set is editable (a user set, not the locked ``Maya_Default``),
so the editor can offer to free the key; on a locked set the conflict is
reported read-only.

Deliberately ignored: editor/tool-scoped bindings (Time Editor, Profiler, …)
that share the key in their own hotkey context. They fire only while that
editor is focused, never shadow a viewport shortcut, and can't be cleared from
here — surfacing them produced an unresolvable conflict the editor re-prompted
to "free" every session (see :func:`_find_bound_command`).
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

    Maya 2025's keyString is a 7-element list with shift LAST:
        [key, alt, ctrl, ?, ?, ?, shift]

    Probe-verified against live dumps: ``ctl+alt+sht+F9`` reports
    ``["F9","1","1","0","0","0","1"]`` and ``sht+i`` reports
    ``["I","0","0","0","0","0","1"]`` (shift+letter also upper-cases the
    glyph). Reading shift from index 4 — the old assumption — silently
    dropped the modifier for non-letter keys, so ``live_hotkey_map`` returned
    a shift-less token and a rebind/clear released the wrong chord.

    Older Maya versions report 6 elements with a different order; this helper
    handles both common shapes. Modifier flags are stored as string
    ``"0"``/``"1"``.
    """
    if not ks:
        return False
    # 7-element layout (Maya 2025+): shift is the final element.
    if len(ks) >= 7:
        idx = {"alt": 1, "ctrl": 2, "shift": 6}.get(mod)
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


def _hotkey_mod_kwargs(parsed: dict) -> dict:
    """Translate ``parse_qt_sequence`` modifier flags to ``cmds.hotkey`` kwargs.

    Emits only the modifiers that are set, using the short ``ctl``/``alt``/
    ``sht`` aliases Maya's hotkey query matches on.
    """
    kwargs: dict = {}
    if parsed.get("ctrlModifier"):
        kwargs["ctl"] = True
    if parsed.get("altModifier"):
        kwargs["alt"] = True
    if parsed.get("shiftModifier"):
        kwargs["sht"] = True
    return kwargs


def _runtime_command_for(name_command: str) -> str:
    """Resolve a name command to its wrapped runtime command, or ''.

    The global hotkey query returns the *name command*; the friendlier runtime
    command (what a user sees in Maya's Hotkey Editor) is read from the
    ``assignCommand`` registry entry carrying that same name command. Used only
    to label an already-confirmed conflict, so the registry scan is paid once
    per user edit, not per query.
    """
    if not name_command:
        return ""
    try:
        count = cmds.assignCommand(query=True, numElements=True) or 0
    except Exception:
        return ""
    for i in range(1, count + 1):
        try:
            if (cmds.assignCommand(i, query=True, name=True) or "") == name_command:
                return cmds.assignCommand(i, query=True, command=True) or ""
        except Exception:
            continue
    return ""


def _find_bound_command(parsed: dict) -> str:
    """Return the runtime command bound to *parsed* in Maya's GLOBAL context, or ''.

    Queries the global (viewport) hotkey context via
    ``cmds.hotkey(..., query=True, name=True)`` — the only binding that actually
    competes with an application-wide Qt shortcut, and the only one
    :func:`_unbind_maya_hotkey` can clear.

    Editor/tool-scoped bindings (Time Editor, Profiler, …) that share the key
    live in their own hotkey context: they fire only while that editor is
    focused, never shadow a viewport shortcut, and can't be cleared from here.
    The previous implementation scanned the whole ``assignCommand`` registry and
    so surfaced those as collisions — an *unresolvable* conflict the editor
    offered to "free" but couldn't, so the same key re-prompted to unbind Maya
    every session and the user's command stayed dead (Maya kept the key). This
    queries the global context only: once the viewport binding is cleared, the
    query returns '' and the conflict is gone for good. Empirically verified in
    Maya — ``cmds.hotkey('n', q, name)`` returns the viewport binding and ''
    after a clear, while ``assignCommand`` still lists the Time Editor binding.

    Returns the friendly runtime-command name (resolved from the name command),
    falling back to the name command itself. Empty outside an interactive Maya.
    """
    key = parsed.get("keyShortcut", "")
    if not key:
        return ""
    try:
        name_command = (
            cmds.hotkey(key, query=True, name=True, **_hotkey_mod_kwargs(parsed))
            or ""
        )
    except Exception:
        return ""
    if not name_command or name_command.strip().lower() == "none":
        return ""
    return _runtime_command_for(name_command) or name_command


def _current_hotkey_set() -> str:
    """Return the active hotkey set name, or '' if it can't be queried."""
    try:
        return cmds.hotkeySet(query=True, current=True) or ""
    except Exception:
        return ""


def _is_hotkey_set_editable() -> bool:
    """True when the active Maya hotkey set can be edited from here.

    Maya's factory ``Maya_Default`` set is read-only — bindings can only be
    changed in a user-created set. Maya exposes no ``-locked`` query, so the
    known factory set name is treated as locked and any other (user) set as
    editable. Returns False when the set can't be queried (e.g. headless).
    """
    current = _current_hotkey_set()
    return bool(current) and current != "Maya_Default"


# Name of the user hotkey set created when an editable set is needed while the
# locked factory set is active. One well-known name so repeated calls (and
# every mayatk tool) converge on the same set instead of accumulating copies.
MACRO_HOTKEY_SET = "mayatk"


def ensure_editable_hotkey_set(name: str = MACRO_HOTKEY_SET) -> str:
    """Make the *current* hotkey set editable; return the resulting set name.

    Maya refuses hotkey edits while the locked factory set (``Maya_Default``)
    is active — ``cmds.hotkey`` raises — which is why a binding assigned on a
    fresh Maya silently never lands ("the hotkey does nothing"). Maya's own
    Hotkey Editor resolves this by prompting to duplicate the factory set;
    this is the scripted equivalent: when the active set is locked, switch to
    the user set *name* (created on first use, sourced from the current set so
    every default binding carries over). No-op when the active set is already
    editable.

    Raises:
        RuntimeError: when hotkey sets can't be managed at all (headless).
    """
    if _is_hotkey_set_editable():
        return _current_hotkey_set()
    if cmds.hotkeySet(name, query=True, exists=True):
        cmds.hotkeySet(name, edit=True, current=True)
    else:
        cmds.hotkeySet(
            name, source=_current_hotkey_set() or "Maya_Default", current=True
        )
    return name


def _unbind_maya_hotkey(parsed: dict) -> None:
    """Clear Maya's press (and release) binding for the parsed key combo.

    Requires the active hotkey set to be editable (see
    :func:`_is_hotkey_set_editable`) — Maya raises on a locked set. ``parsed``
    is the :func:`parse_qt_sequence` output (``keyShortcut`` + modifier flags).
    """
    mods = {k: v for k, v in parsed.items() if k != "keyShortcut"}
    key = parsed["keyShortcut"]
    # Clear press + release in one atomic call (mirrors Macros.clear_hotkey) so a
    # key can never be left half-cleared. The long-form modifier flags produced by
    # parse_qt_sequence (ctrlModifier/altModifier/shiftModifier) are valid
    # cmds.hotkey aliases of ctl/alt/sht. This clears the global (viewport)
    # binding — the one that shadows a Qt shortcut; see _find_bound_command for
    # why editor/tool-context bindings are left alone.
    cmds.hotkey(keyShortcut=key, name="", releaseName="", **mods)
    # Persist immediately. A cmds.hotkey edit lives only in the in-memory set;
    # Maya flushes hotkeys to disk on a *clean* exit, so a crash or hard-kill
    # would otherwise lose the freed key and Maya's binding would be back next
    # launch — re-eating the key and re-prompting the editor to free it. Making
    # the user's deliberate "free this key" durable right away closes that gap.
    try:
        cmds.savePrefs(hotkeys=True)
    except Exception:
        pass


def maya_collision_checker(sequence, scope, ui_name, method_name, ignore=None):
    """Check a proposed binding against Maya's active hotkey set.

    Args:
        sequence: Qt key sequence string (e.g. ``"Ctrl+Alt+S"``).
        scope: ``"window"`` or ``"application"`` — informational.
        ui_name: UI being edited — informational.
        method_name: Slot being assigned — informational.
        ignore: Optional predicate ``(runtime_command_name) -> bool``; a bound
            command it returns True for is not reported. Lets a caller whose
            editor already reports its own managed bindings (e.g. the Macro
            Manager's built-in macro-vs-macro check) suppress the duplicate
            listing of the same conflict.

    Returns:
        A list of ``CollisionConflict`` entries (imported lazily so the
        module is still importable when uitk isn't installed).
    """
    from uitk.widgets.editors.shortcut_editor.registry_editor import CollisionConflict

    conflicts: List = []

    parsed = parse_qt_sequence(sequence)
    if parsed is None:
        return conflicts

    bound = _find_bound_command(parsed)
    if not bound or (ignore is not None and ignore(bound)):
        return conflicts

    set_name = _current_hotkey_set()
    editable = _is_hotkey_set_editable()
    desc = f"Maya runtime command '{bound}'"
    if set_name:
        desc += f" (hotkey set: {set_name})"

    # Maya's binding can be cleared, but only in an editable (user) set. On the
    # locked factory set we leave clear_action None and say why, so the editor
    # disables its "free Maya binding" option rather than no-opping.
    clear = None
    if editable:
        clear = lambda p=dict(parsed): _unbind_maya_hotkey(p)
    else:
        desc += " — locked set; switch to a custom Maya hotkey set to clear it"

    conflicts.append(
        CollisionConflict(
            source="maya",
            description=desc,
            breaks_binding=False,  # external — coexists unless explicitly cleared
            clear_action=clear,
        )
    )
    return conflicts
