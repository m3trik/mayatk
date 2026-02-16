"""Interactive test for ChannelBox and WidgetInspector.

Run in Maya Script Editor (Python tab):
    exec(open(r"O:\Cloud\Code\_scripts\mayatk\test\temp_tests\test_devtools.py").read())

Select a mesh first, then run this script. It will:
  1) Dump the channel box widget tree
  2) List all item views inside the channel box
  3) Query currently selected attributes
  4) Programmatically select translateX and translateY
  5) Verify the selection took effect
"""

import mayatk as mtk


def run():
    print("\n" + "=" * 60)
    print("DevTools / ChannelBox / WidgetInspector Test")
    print("=" * 60)

    # -- 1. WidgetInspector: resolve channel box widget --
    print("\n--- Channel Box widget tree (depth=2) ---")
    mtk.ChannelBox.dump_tree(max_depth=2)

    # -- 2. List item views --
    views = mtk.ChannelBox.list_item_views()
    print(f"\n--- Item views found: {len(views)} ---")
    for i, v in enumerate(views):
        print(f"  [{i}] {type(v).__name__}  objectName={v.objectName()!r}")

    # -- 3. Query current selection --
    sel = mtk.ChannelBox.get_selected_attrs()
    print(f"\n--- Currently selected attrs: {sel} ---")

    plugs = mtk.ChannelBox.get_selected_plugs()
    print(f"--- Selected plugs: {plugs} ---")

    # -- 4. Programmatically select attrs --
    target = ["translateX", "translateY"]
    print(f"\n--- Selecting {target} via ChannelBox.select() ---")
    mtk.ChannelBox.select(target)

    # -- 5. Verify --
    verify = mtk.ChannelBox.get_selected_attrs()
    print(f"--- After select: {verify} ---")

    # -- 6. DevTools: find a MEL script --
    print("\n--- DevTools.find_mel('channelBoxCommand') ---")
    path = mtk.DevTools.find_mel("channelBoxCommand")
    print(f"  Result: {path}")

    # -- 7. WidgetInspector: dump model of main view --
    print("\n--- Channel Box model dump (first 10 rows) ---")
    mtk.ChannelBox.dump_model(max_rows=10)

    print("\n" + "=" * 60)
    print("Test complete.")
    print("=" * 60)


run()
