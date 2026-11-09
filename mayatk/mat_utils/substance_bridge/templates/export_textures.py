"""Export Textures: write every texture set's maps through an export preset, to the Output Dir."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``) on the
# project named in Project, else the one open in Painter.
WORKS_ON = "project"

# Rows: __PAINTER_PROJECT__ __PAINTER__
# Row: __EXPORT_PRESET__ str "PBR Metallic Roughness" "Export Preset" tooltip="A Painter export preset by name: a predefined one (PBR Metallic Roughness, Unity HD Render Pipeline (Metallic Standard), ...) or one on your shelf."
# Row: __EXPORT_DIR__ path "" "Export Dir" placeholder="Painter's export folder" tooltip="Where the maps are written; empty is Painter's own export folder for the project (beside it), never a source-texture folder."
# Row: __EXPORT_FORMAT__ choice png "File Format" choices=png|tga|tif|exr|jpeg
# Row: __EXPORT_BIT_DEPTH__ choice 8 "Bit Depth" choices=8|16|32 tooltip="32 is float (exr, tif)."
# Row: __EXPORT_PADDING__ choice infinite "Padding" choices=infinite|passthrough|transparent|color|diffusion

import os  # noqa: E402

import substance_painter.export as export  # noqa: E402
import substance_painter.textureset as textureset  # noqa: E402

name = args.get("EXPORT_PRESET") or "PBR Metallic Roughness"
preset = next((p.url for p in export.list_predefined_export_presets() if p.name == name), None)
if preset is None:
    preset = next(
        (p.resource_id.url() for p in export.list_resource_export_presets() if p.resource_id.name == name),
        None,
    )
if preset is None:
    names = sorted(p.name for p in export.list_predefined_export_presets())
    raise RuntimeError(f"No export preset named {name!r}. Predefined: {names}")

folder = args.get("EXPORT_DIR") or export.get_default_export_path()
if not folder:
    raise RuntimeError("Set an Export Dir to write the maps to.")
os.makedirs(folder, exist_ok=True)
config = {
    "exportShaderParams": False,
    "exportPath": folder,
    "defaultExportPreset": preset,
    "exportList": [{"rootPath": str(ts.name() if callable(ts.name) else ts.name)} for ts in textureset.all_texture_sets()],
    "exportParameters": [
        {
            "parameters": {
                "fileFormat": args.get("EXPORT_FORMAT") or "png",
                "bitDepth": str(args.get("EXPORT_BIT_DEPTH") or "8"),
                "paddingAlgorithm": args.get("EXPORT_PADDING") or "infinite",
            }
        }
    ],
}
result = export.export_project_textures(config)
written = [path for files in result.textures.values() for path in files]
args.report(f"{len(written)} map(s) written to {folder}.")
status = getattr(result.status, "name", str(result.status))
if status == "Cancelled":
    raise RuntimeError("The export was cancelled in Painter.")
if status != "Success":
    args.report(f"Note: the export ended {status}: {result.message}")
