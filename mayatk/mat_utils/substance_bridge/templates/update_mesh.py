"""Update Mesh: reload the sent mesh into the Painter project made from it, paint kept where UVs and materials still match."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``) on the
# project whose mesh is the sent file: the export overwrites the mesh the
# previous send recorded on the scene, so the project's own path is hit even
# from a later host session.
WORKS_ON = "mesh"
REUSE_RECORDED_EXPORT = True

# Rows: __SCOPE__ __CARRIER__ __PAINTER_RESOLUTION__ __BAKE_SOURCE_SET__ __PAINTER__

from substance_rpc.ops import project_ops, setup_ops  # noqa: E402

# The setup first: the reload is asynchronous, so texture sets are changed on
# a settled project, and the baking settings survive the reload that follows.
if int(args.get("PAINTER_RESOLUTION") or 0) > 0:
    setup_ops.set_resolution(size=int(args["PAINTER_RESOLUTION"]))
    args.report(f"Resolution: {args['PAINTER_RESOLUTION']}.")
if args.get("HIGH_POLY"):
    setup_ops.set_high_poly(mesh_path=args["HIGH_POLY"])
    args.report(f"Baking's high poly: {args['HIGH_POLY']}.")

# Painter reloads asynchronously (its callback ends it after this script): the
# job ends here, and the reload's outcome is Painter's mesh.reload_status.
project_ops.mesh_reload(mesh_path=args["MESH"], preserve_strokes=True, import_cameras=False)
args.report(f"Reloading the mesh from {args['MESH']} (Painter keeps the paint where UVs and materials match).")
