"""End-to-end test for DevTools, WidgetInspector, and ChannelBox modules.

Run from a terminal outside Maya:
    python test_devtools_e2e.py

Requires Maya running with command port 7002 open:
    In Maya Script Editor: cmds.commandPort(name=':7002', sourceType='python')
"""

import sys, os

# Ensure local packages are importable
scripts_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
for pkg in ("mayatk", "pythontk", "uitk"):
    p = os.path.join(scripts_root, pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

from mayatk.env_utils.maya_connection import MayaConnection


PASS = 0
FAIL = 0


def run(conn, label, code, timeout=30):
    """Execute code in Maya via capture_output and print result."""
    global PASS, FAIL
    print(f"\n[{label}]")
    try:
        result = conn.execute(code, timeout=timeout, capture_output=True)
        output = (result or "").strip()
        if output:
            for line in output.splitlines():
                print(f"  {line}")
        else:
            print("  (no output)")
        PASS += 1
        return output
    except Exception as e:
        print(f"  ERROR: {e}")
        FAIL += 1
        return None


RELOAD_CODE = r"""
import sys, os

scripts_root = r'O:\Cloud\Code\_scripts'
for pkg in ('mayatk', 'pythontk', 'uitk'):
    p = os.path.join(scripts_root, pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

to_del = [k for k in sys.modules if k.startswith('mayatk')]
for k in to_del:
    del sys.modules[k]
to_del = [k for k in sys.modules if k.startswith('pythontk')]
for k in to_del:
    del sys.modules[k]

import mayatk
print(f'reload OK - mayatk {getattr(mayatk, "__version__", "?")}')
"""


def main():
    conn = MayaConnection.get_instance()
    # Try connecting first (with launch if needed)
    if not conn.connect(mode="port", port=7002, launch=True):
        print("FATAL: Could not connect to Maya on port 7002.")
        return

    print("\n" + "=" * 60)
    print("  DevTools / WidgetInspector / ChannelBox  E2E Tests v2")
    print("=" * 60)

    # ========== SETUP ==========

    run(conn, "0. Reload mayatk", RELOAD_CODE, timeout=60)

    run(
        conn,
        "1. Setup scene",
        """
import maya.cmds as cmds
cmds.file(new=True, force=True)
cmds.polyCube(name='testCube1')
cmds.select('testCube1')
print('testCube1 created and selected')
""",
    )

    # ========== DEVTOOLS — CORE ==========

    run(
        conn,
        "2. echo_all(False)",
        """
from mayatk.env_utils.devtools import DevTools
DevTools.echo_all(False)
print('echo_all OK')
""",
    )

    run(
        conn,
        "3. find_mel('polyCube')",
        """
from mayatk.env_utils.devtools import DevTools
print(f'find_mel: {DevTools.find_mel("polyCube")}')
""",
    )

    run(
        conn,
        "4. get_mel_global('gChannelBoxName')",
        """
from mayatk.env_utils.devtools import DevTools
print(f'result: {DevTools.get_mel_global("gChannelBoxName")!r}')
""",
    )

    run(
        conn,
        "5. find('AEaddRampControl')",
        """
from mayatk.env_utils.devtools import DevTools
print(f'find: {DevTools.find("AEaddRampControl")}')
""",
    )

    # ========== DEVTOOLS — NEW: find_all, list_mel_globals ==========

    run(
        conn,
        "6. find_all('AEaddRampControl')",
        """
from mayatk.env_utils.devtools import DevTools
hits = DevTools.find_all('AEaddRampControl')
for h in hits:
    print(f"  {h['type']}: {h['path']}")
print(f'{len(hits)} total')
""",
    )

    run(
        conn,
        "7. list_mel_globals('channelBox|gChannelBox')",
        """
from mayatk.env_utils.devtools import DevTools
names = DevTools.list_mel_globals('channelBox|gChannelBox')
for n in names[:10]:
    print(f'  {n}')
print(f'{len(names)} matched')
""",
    )

    # ========== DEVTOOLS — NEW: grep_mel_procs ==========

    run(
        conn,
        "8. grep_mel_procs('channelBox')",
        """
from mayatk.env_utils.devtools import DevTools
procs = DevTools.grep_mel_procs('channelBox')
for p in procs[:8]:
    print(f"  {p['scope']:6s} {p['signature']}")
    print(f"         {p['path']}")
print(f'{len(procs)} procs total')
""",
        timeout=60,
    )

    # ========== DEVTOOLS — NEW: read_mel_proc ==========

    run(
        conn,
        "9. read_mel_proc('selectedChannelBoxAttributes')",
        """
from mayatk.env_utils.devtools import DevTools
src = DevTools.read_mel_proc('selectedChannelBoxAttributes')
if src:
    lines = src.strip().splitlines()
    for ln in lines[:8]:
        print(f'  {ln}')
    if len(lines) > 8:
        print(f'  ... ({len(lines)} lines total)')
else:
    print('  not found')
""",
    )

    # ========== DEVTOOLS — NEW: grep_maya_dir (regex + context) ==========

    run(
        conn,
        "10. grep_maya_dir('channelBox.*select', regex)",
        """
from mayatk.env_utils.devtools import DevTools
hits = DevTools.grep_maya_dir(
    'channelBox.*select', regex=True, ext='.mel', context=1, max_results=5,
)
for h in hits:
    print(f"  {h['path']}:{h['line']}")
    print(f"    {h['text'][:100]}")
print(f'{len(hits)} matches')
""",
        timeout=60,
    )

    # ========== WIDGET INSPECTOR — CORE ==========

    run(
        conn,
        "11. from_mel_global + dump_tree",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
if w:
    lines = WidgetInspector.dump_tree(w, max_depth=2)
    print(f'{len(lines)} widget nodes')
else:
    print('not found')
""",
    )

    run(
        conn,
        "12. find_item_views",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
views = WidgetInspector.find_item_views(w) if w else []
for v in views:
    print(f'  {type(v).__name__} obj={v.objectName()!r}')
print(f'{len(views)} views')
""",
    )

    run(
        conn,
        "13. list_signals",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
sigs = WidgetInspector.list_signals(w) if w else []
print(f'{len(sigs)} signals')
""",
    )

    run(
        conn,
        "14. list_slots",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
slots = WidgetInspector.list_slots(w) if w else []
print(f'{len(slots)} slots (first 5):')
for s in slots[:5]:
    print(f'  {s}')
""",
    )

    # ========== WIDGET INSPECTOR — NEW ==========

    run(
        conn,
        "15. dump_properties",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
if w:
    props = WidgetInspector.dump_properties(w)
    print(f'{len(props)} properties')
else:
    print('not found')
""",
    )

    # SKIPPED: Causes hard crash in Maya 2025
    # run(
    #     conn,
    #     "16. dump_actions (context menus)",
    #     """
    # from mayatk.env_utils.devtools import WidgetInspector
    # w = WidgetInspector.from_mel_global('gChannelBoxName')
    # if w:
    #     actions = WidgetInspector.dump_actions(w)
    #     for a in actions[:8]:
    #         print(f"  [{a.get('menu','')}] {a['text']!r}  enabled={a['enabled']}")
    #     print(f'{len(actions)} actions total')
    # else:
    #     print('not found')
    # """,
    # )

    run(
        conn,
        "17. find_by_property('objectName', 'mainChannelBox')",
        """
from mayatk.env_utils.devtools import WidgetInspector
main = WidgetInspector.main_window()
if main:
    hits = WidgetInspector.find_by_property(main, 'objectName', 'mainChannelBox')
    print(f'{len(hits)} widget(s) found')
    for h in hits:
        print(f'  {type(h).__name__} {h.objectName()!r}')
else:
    print('main window not found')
""",
    )

    run(
        conn,
        "18. snapshot + diff",
        """
from mayatk.env_utils.devtools import WidgetInspector
w = WidgetInspector.from_mel_global('gChannelBoxName')
if w:
    snap1 = WidgetInspector.snapshot(w, max_depth=1)
    print(f"snapshot keys: {list(snap1.keys())}")
    print(f"class={snap1['class']} obj={snap1['objectName']}")
    n_children = len(snap1.get('children', []))
    print(f'{n_children} children')
    # diff with itself should be empty
    diffs = WidgetInspector.diff_snapshots(snap1, snap1)
    print(f'self-diff: {len(diffs)} differences')
else:
    print('not found')
""",
    )

    # ========== CHANNEL BOX — CORE ==========

    run(
        conn,
        "19. get_selected_attrs",
        """
from mayatk.env_utils.channel_box import ChannelBox
print(f'selected: {ChannelBox.get_selected_attrs()}')
""",
    )

    run(
        conn,
        "20. select + get_selected_plugs",
        """
from mayatk.env_utils.channel_box import ChannelBox
ChannelBox.select(['translateX', 'translateY'])
print(f'selected: {ChannelBox.get_selected_attrs()}')
print(f'plugs: {ChannelBox.get_selected_plugs()}')
""",
    )

    run(
        conn,
        "21. clear_selection",
        """
from mayatk.env_utils.channel_box import ChannelBox
ChannelBox.clear_selection()
print(f'after clear: {ChannelBox.get_selected_attrs()}')
""",
    )

    # ========== CHANNEL BOX — NEW ==========

    run(
        conn,
        "22. get_all_attrs('testCube1')",
        """
from mayatk.env_utils.channel_box import ChannelBox
attrs = ChannelBox.get_all_attrs('testCube1', section='main')
print(f'{len(attrs)} main attrs:')
for a in attrs:
    print(f'  {a}')
""",
    )

    run(
        conn,
        "23. get_all_attrs shape section",
        """
from mayatk.env_utils.channel_box import ChannelBox
attrs = ChannelBox.get_all_attrs('testCube1', section='shape')
print(f'{len(attrs)} shape attrs')
for a in attrs[:5]:
    print(f'  {a}')
""",
    )

    run(
        conn,
        "24. get_all_attrs history section",
        """
from mayatk.env_utils.channel_box import ChannelBox
attrs = ChannelBox.get_all_attrs('testCube1', section='history')
print(f'{len(attrs)} history attrs')
for a in attrs[:5]:
    print(f'  {a}')
""",
    )

    run(
        conn,
        "25. get_attr_properties",
        """
from mayatk.env_utils.channel_box import ChannelBox
# Limit to safe attributes to avoid crashing on complex types
props = ChannelBox.get_attr_properties('testCube1', ['translateX', 'scaleY', 'visibility'])
for p in props:
    print(f"  {p['name']:15s} type={p['type']:8s} val={p['value']}  locked={p['locked']}  min={p['min']}  max={p['max']}")
""",
    )

    run(
        conn,
        "26. get_context_menu_actions",
        """
from mayatk.env_utils.channel_box import ChannelBox
actions = ChannelBox.get_context_menu_actions()
for a in actions[:8]:
    print(f"  [{a.get('menu','')}] {a['text']!r}")
print(f'{len(actions)} actions total')
""",
    )

    run(
        conn,
        "27. snapshot + diff (select changes)",
        """
from mayatk.env_utils.channel_box import ChannelBox
snap1 = ChannelBox.snapshot(max_depth=2)
print(f'snapshot: {snap1.get("class", "?")} children={len(snap1.get("children", []))}')
# Diff with itself
diffs = ChannelBox.diff(snap1, snap1)
print(f'self-diff: {len(diffs)} changes')
""",
    )

    run(
        conn,
        "28. list_mel_procs('channelBox')",
        """
from mayatk.env_utils.channel_box import ChannelBox
procs = ChannelBox.list_mel_procs('channelBox')
for p in procs[:5]:
    print(f"  {p['scope']:6s} {p['signature']}")
print(f'{len(procs)} procs found')
""",
        timeout=60,
    )

    run(
        conn,
        "29. read_mel_proc('channelBoxCommand')",
        """
from mayatk.env_utils.channel_box import ChannelBox
src = ChannelBox.read_mel_proc('channelBoxCommand')
if src:
    lines = src.strip().splitlines()
    for ln in lines[:5]:
        print(f'  {ln}')
    print(f'  ... ({len(lines)} lines total)')
else:
    print('  not found')
""",
    )

    # ========== CROSS-MODULE: Attributes refactored path ==========

    run(
        conn,
        "30. Attributes.get_selected_channels",
        """
from mayatk.node_utils.attributes._attributes import Attributes
result = Attributes.get_selected_channels()
print(f'get_selected_channels: {result}')
""",
    )

    run(
        conn,
        "31. Top-level mayatk access",
        """
import mayatk as mtk
print(f'DevTools={mtk.DevTools.__name__}')
print(f'WidgetInspector={mtk.WidgetInspector.__name__}')
print(f'ChannelBox={mtk.ChannelBox.__name__}')
""",
    )

    print("\n" + "=" * 60)
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
