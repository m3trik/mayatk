"""Qt-only check for the Shell Xform move-scope combobox (``uv_utils/shell_xform.ui``).

Requires **Qt but not Maya**, which is the inverse of the rest of ``mayatk/test``:
mayapy + ``maya.standalone`` + offscreen Qt segfaults building any QMainWindow, so
this cannot run in the standard runner. The non-``test_`` name keeps it out of that
runner's discovery. Run it under the workspace ``.venv``::

    QT_QPA_PLATFORM=offscreen python mayatk/test/shell_xform_ui_check.py

``shell_xform.py`` guards its ``maya.cmds`` import, so the slots class imports fine
without Maya; only the op methods need it, and this check never calls them.

What it covers that ``test_shell_xform.py`` cannot: the combo ships **no static item
list** in the ``.ui`` — ``cmb_move_scope_init`` builds the items from
``_MOVE_SCOPES`` with the step as item data. If that init stops running or stops
carrying data, every arrow silently falls back to the derived-bounds branch. This
also guards the option-box reparent, which has historically invalidated the
wrappers of a widget's grid siblings (see the option-box wrap-before-defer note in
``uitk``).

The Blender twin's panel is kept in step by the parity sweep
(``m3trik/scripts/compare_panel_surface.py --panel shell_xform``), and its own
``_MOVE_SCOPES`` shape is asserted in ``blendertk/test/shell_xform_slot_check.py``.
"""
import sys

from qtpy import QtWidgets

from uitk import Switchboard
from mayatk.uv_utils.shell_xform import ShellXformSlots

UI_DIR = r"o:\Cloud\Code\_scripts\mayatk\mayatk\uv_utils"

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


def main():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    sb = Switchboard(ui_source=UI_DIR)
    ui = sb.get_ui("shell_xform")
    combo = ui.cmb_move_scope

    check(".ui ships no static items (the table is the SSoT)", combo.count() == 0)

    slots = ShellXformSlots.__new__(ShellXformSlots)
    combo.is_initialized = False
    ShellXformSlots.cmb_move_scope_init(slots, combo)
    slots.ui = ui

    items = [combo.itemText(i) for i in range(combo.count())]
    data = [combo.itemData(i) for i in range(combo.count())]
    check(
        "init populates the combo from _MOVE_SCOPES",
        items == list(ShellXformSlots._MOVE_SCOPES),
        f"{items}",
    )
    check(
        "each item carries its step as data",
        data == list(ShellXformSlots._MOVE_SCOPES.values()),
        f"{data}",
    )

    combo.setCurrentIndex(0)
    check(
        "default scope is a whole tile",
        combo.currentText() == "Tile" and slots._move_step(None) == (1.0, 1.0),
        f"{combo.currentText()!r} -> {slots._move_step(None)}",
    )
    combo.setCurrentText("Half Tile")
    check("Half Tile -> 0.5 step", slots._move_step(None) == (0.5, 0.5))
    combo.setCurrentText("Quarter Tile")
    check("Quarter Tile -> 0.25 step", slots._move_step(None) == (0.25, 0.25))

    combo.setCurrentText("Selection Bounds")
    derived = slots._move_step((0.2, 0.5, 0.6, 0.9))  # a 0.4 x 0.4 box
    check(
        "Selection Bounds -> the selection's own extent",
        max(abs(d - 0.4) for d in derived) < 1e-9,
        f"{derived}",
    )
    collapsed = slots._move_step((0.2, 0.5, 0.2, 0.9))  # zero width, 0.4 height
    check(
        "a collapsed axis falls back to a whole tile, the other is unaffected",
        abs(collapsed[0] - 1.0) < 1e-9 and abs(collapsed[1] - 0.4) < 1e-9,
        f"{collapsed}",
    )

    # ---- tri-state snap button (off / grid / shell)
    # `cmb_move_scope_init` installs on the panel's real settings key, so the
    # restored value is whatever the user last left the panel on. Read that the
    # button exists, then re-install on a throwaway key before touching state:
    # asserting "defaults off" against the live key would fail for anyone who
    # had left it on shell, and cycling it would overwrite their choice. (uitk's
    # own suite sandboxes QSettings from a conftest, which a standalone script
    # like this never loads.)
    check("snap button installed", getattr(slots, "_snap_action", None) is not None)
    check(
        "the restored mode is a valid cycle position",
        slots._snap_mode() in (ShellXformSlots._SNAP_OFF, ShellXformSlots._SNAP_GRID,
                               ShellXformSlots._SNAP_SHELL),
        f"{slots._snap_mode()}",
    )

    # `settings_key=False` opts out of persistence entirely, so this writes
    # nothing at all — not even a stray probe key.
    action = combo.option_box.set_action(
        states=slots._snap_states(), settings_key=False
    )
    slots._snap_action = action
    check(
        "an unpersisted install defaults off",
        slots._snap_mode() == ShellXformSlots._SNAP_OFF,
        f"{slots._snap_mode()}",
    )

    # Clicking cycles off -> grid -> shell -> off, and `_snap_mode` must track
    # it: the cycle index IS the mode the move path reads.
    seen = []
    for _ in range(4):
        action.current_state = (slots._snap_mode() + 1) % 3
        seen.append(slots._snap_mode())
    check(
        "cycling the button walks the three modes and wraps",
        seen == [
            ShellXformSlots._SNAP_GRID,
            ShellXformSlots._SNAP_SHELL,
            ShellXformSlots._SNAP_OFF,
            ShellXformSlots._SNAP_GRID,
        ],
        f"{seen}",
    )

    states = slots._snap_states()
    colors = [s.get("color") for s in states]
    check("three states, each tinted, all tints distinct",
          len(states) == 3 and all(colors) and len(set(colors)) == 3, f"{colors}")
    check("shell snap carries its own icon",
          states[ShellXformSlots._SNAP_SHELL]["icon"]
          != states[ShellXformSlots._SNAP_GRID]["icon"],
          f"{[s['icon'] for s in states]}")
    check("every state has a tooltip", all(s.get("tooltip") for s in states))

    # The option-box wrap reparents the combo; its grid siblings must survive.
    check(
        "move arrows survive the option-box reparent",
        all(getattr(ui, n, None) is not None and getattr(ui, n).objectName() == n
            for n in ("b023", "b024", "b025", "b026")),
    )

    print("\n".join(lines))
    ok = bool(lines) and all(line.startswith("OK") for line in lines)
    print(
        f"===RESULT: {'PASS' if ok else 'FAIL'}=== "
        f"({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
