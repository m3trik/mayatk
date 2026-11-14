"""Bake Lighting to Diffuse: bake AO (and curvature) per texture set and lay it over Base Color, as a Multiply layer on top of the stack."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``) on the
# project named in Project, else the one open in Painter. Its settings are its
# own rows (the Row: lines below), saved with its presets.
WORKS_ON = "project"

# Rows: __PAINTER_PROJECT__ __PAINTER_SAVE__ __PAINTER__
# Row: __BAKE_RESOLUTION__ choice 1024 "Bake Resolution" choices=512|1024|2048|4096 tooltip="Bake output size, square."
# Row: __AO_RAYS__ int 64 "AO Rays" min=1 max=256 tooltip="Secondary rays per sample: more is cleaner and slower."
# Row: __AO_MAX_DISTANCE__ float 1.0 "AO Max Distance" min=0 max=1 decimals=3 tooltip="Farthest occluder, a fraction of the mesh's bounding box."
# Row: __AO_MIN_DISTANCE__ float 0.0 "AO Min Distance" min=0 max=1 decimals=4 tooltip="Nearest occluder: a bias against self-shadow noise."
# Row: __AO_SPREAD__ float 162 "AO Spread" min=0 max=180 decimals=1 tooltip="Spread angle of the occlusion rays, in degrees."
# Row: __AO_SUBSAMPLE__ choice 1 "Antialiasing" choices=1|2|4|8 tooltip="Supersampling: 1 is none."
# Row: __AO_INTENSITY__ float 1.0 "AO Intensity" min=0 max=1 decimals=2 tooltip="The lighting layer's opacity: 1 darkens Base Color by the full occlusion."
# Row: __INCLUDE_CURVATURE__ bool off "Curvature" tooltip="Also bake curvature, laid over as an Overlay layer above the lighting (edges lighten, creases darken)."
# Row: __SKIP_EXISTING__ bool off "Skip Existing Bakes" tooltip="Bake only texture sets missing the maps; the rest still composite."

from substance_rpc.ops import bake_ops  # noqa: E402

result = bake_ops.lighting_to_diffuse(
    bake_resolution=int(args.get("BAKE_RESOLUTION", 1024)),
    ao_intensity=float(args.get("AO_INTENSITY", 1.0)),
    ao_secondary_rays=int(args.get("AO_RAYS", 64)),
    ao_max_distance=float(args.get("AO_MAX_DISTANCE", 1.0)),
    ao_min_distance=float(args.get("AO_MIN_DISTANCE", 0.0)),
    ao_spread_angle=float(args.get("AO_SPREAD", 162.0)),
    ao_subsample=int(args.get("AO_SUBSAMPLE", 1)),
    include_curvature=bool(args.get("INCLUDE_CURVATURE", False)),
    skip_existing_bakes=bool(args.get("SKIP_EXISTING", False)),
    save_project=False,  # once Painter is idle, below
    cancelled=args.cancelled,  # a cancel stops the bake under way
)
for entry in result["baked_maps"]:
    for name, maps in entry.items():
        if maps == "skipped":
            args.report(f"{name}: kept the maps it has (Skip Existing Bakes).")
        else:
            args.report(f"{name}: baked {', '.join(maps)}.")
for entry in result["layers_created"]:
    for name in entry:
        args.report(f"{name}: lighting laid over Base Color (the layer on top of its stack).")
if result["errors"]:
    raise RuntimeError("; ".join(result["errors"]))


def _save():
    import substance_painter.project as project

    project.save()
    args.report("Project saved.")


if args.get("PAINTER_SAVE"):
    args.when_idle(_save)  # a save while Painter computes the layers is refused
