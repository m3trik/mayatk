# coding=utf-8
"""Diagnostic test to understand split_static + overlap_groups behavior."""
import sys

# Clear cached modules
modules_to_clear = [k for k in list(sys.modules.keys()) if "mayatk" in k.lower()]
for mod in modules_to_clear:
    del sys.modules[mod]

import pymel.core as pm
from mayatk.anim_utils.scale_keys import ScaleKeys
from mayatk.anim_utils._anim_utils import KeyframeGrouper

print("=" * 70)
print("DIAGNOSTIC: Split Static + Overlap Groups Scaling")
print("=" * 70)

# Get selected objects
selected = pm.selected()
if not selected:
    print("ERROR: Please select objects with animation")
else:
    print(f"\nSelected objects: {len(selected)}")

    # Collect segments with split_static
    segments = KeyframeGrouper.collect_segments(
        selected,
        split_static=True,
        ignore="visibility",
    )

    print(f"\nSegments found: {len(segments)}")
    for i, seg in enumerate(segments[:20]):  # Show first 20
        print(
            f"  {i}: obj={seg['obj'].name()}, range=({seg['start']:.1f}, {seg['end']:.1f}), keys={len(seg['keyframes'])}"
        )

    if len(segments) > 20:
        print(f"  ... and {len(segments) - 20} more segments")

    # Group by overlap
    groups = KeyframeGrouper.group_segments(segments, mode="overlap_groups")

    print(f"\nOverlap groups: {len(groups)}")
    for i, grp in enumerate(groups[:10]):  # Show first 10
        print(
            f"  Group {i}: start={grp['start']:.1f}, end={grp['end']:.1f}, objects={len(grp['objects'])}, sub_groups={len(grp.get('sub_groups', []))}"
        )

    if len(groups) > 10:
        print(f"  ... and {len(groups) - 10} more groups")

print("\n" + "=" * 70)
print("To test scaling, UNDO first then run:")
print(
    "  ScaleKeys.scale_keys(factor=2.0, group_mode='overlap_groups', split_static=True, ignore='visibility')"
)
print("=" * 70)
