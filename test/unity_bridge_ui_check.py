# !/usr/bin/python
# coding=utf-8
"""Qt-only check for the Unity Bridge panel's optional-engine behavior.

Requires **Qt but not Maya** (``unity_bridge_slots`` guards its ``maya.cmds``
import), which is the inverse of the rest of ``mayatk/test`` — the non-``test_``
name keeps it out of that runner's discovery. Run it under the workspace venv::

    QT_QPA_PLATFORM=offscreen python mayatk/test/unity_bridge_ui_check.py

What it covers that the unit tests cannot: FULL panel construction through the
real Switchboard, in both engine states. This is the regression surface of the
2026-07-29 live-Maya failure — ``make_bridge`` prompted from ``__init__`` (via
the log wiring), construction then failed, and the modal was stranded with no
way to dismiss it:

1. Engine present: the panel builds, the template combo carries Copy to
   Project + Manage Unity Scripts, selecting the manage template shows the
   SCRIPTS checklist + SCRIPTS_ACTION, and the checklist populated itself from
   the installed unitytk release (all rows checked).
2. Engine missing (unitytk import blocked): the panel must STILL build, with
   zero dialogs (any modal here would hang this offscreen script — hanging IS
   the failure signal), ``peek_bridge()`` None, an empty script checklist that
   falls back to the full set, and the manage template's Status action
   reporting through ``panel_log`` instead of raising.
"""
import importlib
import os
import sys

from qtpy import QtWidgets

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


class _BlockUnitytk:
    """Meta-path hook simulating the broken environment (unitytk absent)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unitytk" or fullname.startswith("unitytk."):
            raise ImportError("unitytk blocked for the missing-engine check")
        return None


def _build_panel(sb_cls, ui_dir):
    sb = sb_cls(ui_source=ui_dir, slot_source=ui_dir)
    ui = sb.get_ui("unity_bridge")
    return sb, ui


def main():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    from uitk import Switchboard

    ui_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mayatk",
        "env_utils",
        "unity_bridge",
    )

    # ---- 1. Engine present -------------------------------------------------
    sb, ui = _build_panel(Switchboard, ui_dir)
    slots = sb.get_slots_instance(ui)
    check("panel constructs with the engine present", slots is not None)
    check("bridge builds", slots.peek_bridge() is not None)

    combo = ui.cmb000
    # cmb000 populates lazily on first show; drive its init directly (the
    # same pattern shell_xform_ui_check uses for its combo).
    slots._populate_template_combo(combo)
    labels = [combo.itemText(i) for i in range(combo.count())]
    check(
        "combo carries both templates",
        labels == ["Copy to Project", "Manage Unity Scripts"],
        f"{labels}",
    )

    combo.setCurrentIndex(1)
    keys = slots._relevant_param_keys()
    check(
        "manage template shows the script params",
        keys == {"SCRIPTS", "SCRIPTS_ACTION"},
        f"{sorted(keys)}",
    )
    combo.setCurrentIndex(0)
    keys = slots._relevant_param_keys()
    check(
        "copy template hides the script params",
        not (keys & {"SCRIPTS", "SCRIPTS_ACTION"}) and "SCOPE" in keys,
        f"{sorted(keys)}",
    )

    # The checklist is runtime-populated from the installed unitytk release,
    # every row checked (== the historical full-set deploy).
    from unitytk import TemplateDeployer

    expected = [c for c in TemplateDeployer.components() if not c.required]
    lw = slots._param_widgets["SCRIPTS"]
    rows = [lw.item(i).text() for i in range(lw.count())]
    check(
        "script checklist lists the optional components",
        rows == [c.label for c in expected],
        f"{rows}",
    )
    check(
        "every script checked by default",
        slots._script_selection() == [c.key for c in expected],
        f"{slots._script_selection()}",
    )
    check(
        "rows carry their per-script tooltip",
        all(lw.item(i).toolTip() for i in range(lw.count())),
    )
    # The row must not be squashed to the one-line height the scalar params use.
    check(
        "checklist row is not clamped to 19px",
        slots._param_rows["SCRIPTS"].maximumHeight() > 19,
        f"{slots._param_rows['SCRIPTS'].maximumHeight()}",
    )

    # ---- 2. Engine missing -------------------------------------------------
    blocker = _BlockUnitytk()
    sys.meta_path.insert(0, blocker)
    for mod in [m for m in sys.modules if m == "unitytk" or m.startswith("unitytk.")]:
        del sys.modules[mod]
    # The slots module holds no top-level unitytk import, but reload defensively
    # so a cached engine class can't mask the block.
    import mayatk.env_utils.unity_bridge.unity_bridge_slots as _slots_mod

    importlib.reload(_slots_mod)
    try:
        sb2, ui2 = _build_panel(Switchboard, ui_dir)
        slots2 = sb2.get_slots_instance(ui2)
        check("panel constructs with the engine MISSING (no dialog)", slots2 is not None)
        check("peek_bridge is None", slots2.peek_bridge() is None)
        check(
            "empty checklist falls back to the full set",
            slots2._param_widgets["SCRIPTS"].count() == 0
            and slots2._script_selection() is None,
        )

        # The manage template's Status action must report, not raise.
        before = ui2.txt000.toPlainText()
        slots2._manage_unity_scripts("status")
        after = ui2.txt000.toPlainText()
        check(
            "status action reports via panel_log without an engine",
            "not installed" in after.lower() or after != before,
            repr(after[-120:]),
        )
    finally:
        sys.meta_path.remove(blocker)
        importlib.reload(_slots_mod)

    print("\n".join(lines))
    failed = [ln for ln in lines if ln.startswith("FAIL")]
    print(f"\n{len(lines) - len(failed)}/{len(lines)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
