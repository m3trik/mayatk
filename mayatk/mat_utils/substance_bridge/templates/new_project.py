"""New Project: create a Painter project from the sent mesh, its mesh maps on its texture sets."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``), with
# ``args`` holding the panel's rows and what the send made.
WORKS_ON = "new"
# The manifest's mesh_maps put each material's AO / normal on its own texture
# set (setup_ops.apply_mesh_maps), which project.create's mesh maps cannot.
BUILD_MANIFEST = True
# Embed every scene-referenced texture into the FBX, so each texture set
# starts with its base color.
FBX_OPTIONS = {
    "FBXExportEmbeddedTextures": True,
}

# Rows: __SCOPE__ __CARRIER__ __PAINTER_RESOLUTION__ __PAINTER_INCLUDE_TEXTURES__
# Rows: __PAINTER_TEXTURE_AFFIX__ __PAINTER_SPLIT_BY_UDIM__ __BAKE_SOURCE_SET__
# Rows: __PAINTER_PROJECT__ __PAINTER_SAVE__ __PAINTER__

import substance_painter.project as project  # noqa: E402
import substance_painter.textureset as textureset  # noqa: E402

from substance_rpc.ops import project_ops, setup_ops  # noqa: E402

if args.get("PAINTER_SAVE") and not args.get("PROJECT"):
    raise RuntimeError("Save When Done needs a Project path to save the new project as.")
if project.is_open():
    raise RuntimeError(
        f"Painter has {project.name() or 'a project'} open: a new project needs a Painter "
        "with none (save and close it, or pick New Painter)."
    )

info = project_ops.project_create(
    mesh_path=args["MESH"],
    mesh_map_paths=args["MESH_MAP_FILES"] if args.get("PAINTER_INCLUDE_TEXTURES", True) else None,
    resolution=int(args.get("PAINTER_RESOLUTION") or 0),
    split_by_udim=bool(args.get("PAINTER_SPLIT_BY_UDIM")),
)
args.report(f"Created {info.get('name') or 'the project'} from {args['MESH']}.")


# Painter goes on loading the project after project.create returns (a save
# then is refused: "Painter is busy"), so the rest waits until it is idle.
def _set_up():
    if not textureset.all_texture_sets():
        # Painter's importer read no mesh from the file ("No meshes in scene").
        raise RuntimeError(
            f"Painter read no mesh from {args['MESH']} (see Painter's log): "
            "try another format or FBX version."
        )
    if args.get("HIGH_POLY"):
        setup_ops.set_high_poly(mesh_path=args["HIGH_POLY"])
        args.report(f"Baking's high poly: {args['HIGH_POLY']}.")
    if args.get("MANIFEST") and args.get("MESH_MAPS"):
        setup_ops.apply_mesh_maps(manifest_path=args["MANIFEST"])
        args.report(f"Mesh maps wired for {len(args['MESH_MAPS'])} material(s).")


def _save():
    project_ops.project_save_as(path=args["PROJECT"])
    args.report(f"Saved as {args['PROJECT']}.")


args.when_idle(_set_up)
if args.get("PAINTER_SAVE"):
    args.when_idle(_save)
