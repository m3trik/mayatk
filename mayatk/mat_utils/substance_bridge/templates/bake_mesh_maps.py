"""Bake Mesh Maps: bake the chosen mesh maps of every texture set, from the bake source when one was sent."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``) on the
# project named in Project, else the one open in Painter.
WORKS_ON = "project"

# Rows: __PAINTER_PROJECT__ __PAINTER_SAVE__ __PAINTER__
# Row: __MAP_BAKE_RESOLUTION__ choice 2048 "Map Resolution" choices=512|1024|2048|4096 tooltip="Bake output size, square."
# Row: __BAKE_NORMAL__ bool on "Normal"
# Row: __BAKE_WORLD_NORMAL__ bool on "World Space Normal"
# Row: __BAKE_ID__ bool on "ID"
# Row: __BAKE_AO__ bool on "Ambient Occlusion"
# Row: __BAKE_CURVATURE__ bool on "Curvature"
# Row: __BAKE_POSITION__ bool on "Position"
# Row: __BAKE_THICKNESS__ bool on "Thickness"

import substance_painter.project as project  # noqa: E402

from substance_rpc.ops import bake_ops  # noqa: E402

chosen = [
    name
    for key, name in (
        ("BAKE_NORMAL", "Normal"),
        ("BAKE_WORLD_NORMAL", "WorldSpaceNormal"),
        ("BAKE_ID", "ID"),
        ("BAKE_AO", "AO"),
        ("BAKE_CURVATURE", "Curvature"),
        ("BAKE_POSITION", "Position"),
        ("BAKE_THICKNESS", "Thickness"),
    )
    if args.get(key, True)
]
if not chosen:
    raise RuntimeError("No mesh map is ticked to bake.")

result = bake_ops.mesh_maps(maps=chosen, resolution=int(args.get("MAP_BAKE_RESOLUTION", 2048)), cancelled=args.cancelled)
for entry in result["baked"]:
    args.report(f"Baked: {entry}")
if result["errors"]:
    raise RuntimeError("; ".join(result["errors"]))
def _save():
    project.save()
    args.report("Project saved.")


if args.get("PAINTER_SAVE"):
    args.when_idle(_save)  # a save while Painter computes the new maps is refused
