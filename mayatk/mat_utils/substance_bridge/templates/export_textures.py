"""Export Textures: write every texture set's maps through an export preset, to the Export Dir."""

# Runs inside Painter (the substance_rpc plugin's ``script.start``) on the
# project named in Project, else the one open in Painter.
WORKS_ON = "project"

# Rows: __PAINTER_PROJECT__ __PAINTER__
# Row: __EXPORT_PRESET__ str "PBR Metallic Roughness" "Export Preset" tooltip="A Painter export preset by name: a predefined one (PBR Metallic Roughness, Unity HD Render Pipeline (Metallic Standard), ...) or one on your shelf."
# Row: __EXPORT_DIR__ path "" "Export Dir" placeholder="Painter's export folder" tooltip="Where the maps are written; empty is Painter's own export folder for the project (beside it), never a source-texture folder."
# Row: __EXPORT_FORMAT__ choice png "File Format" choices=png|tga|tif|exr|jpeg
# Row: __EXPORT_BIT_DEPTH__ choice 8 "Bit Depth" choices=8|16|16f|32f tooltip="16f and 32f are float, for exr and tif."
# Row: __EXPORT_SIZE__ choice Project "Size" choices=Project|512|1024|2048|4096|8192 tooltip="Project writes each texture set at its own resolution."
# Row: __EXPORT_PADDING__ choice infinite "Padding" choices=infinite|passthrough|transparent|color|diffusion
# Row: __EXPORT_DILATION__ int 16 "Dilation" min=0 max=256 tooltip="Pixels the padding reaches past each UV island (transparent, color and diffusion padding)."

import os  # noqa: E402

import substance_painter.export as export  # noqa: E402
import substance_painter.textureset as textureset  # noqa: E402

name = args.get("EXPORT_PRESET") or "PBR Metallic Roughness"
# A predefined preset is named by its url, one on a shelf by its resource's.
presets = {p.name: p.url for p in export.list_predefined_export_presets()}
shelf = {p.resource_id.name: p.resource_id.url() for p in export.list_resource_export_presets()}
preset = presets.get(name) or shelf.get(name)
if preset is None:
    raise RuntimeError(f"No export preset named {name!r}. Painter has: {', '.join(sorted({*presets, *shelf}))}")

folder = args.get("EXPORT_DIR") or export.get_default_export_path()
if not folder:
    raise RuntimeError("Set an Export Dir to write the maps to.")

parameters = {
    "fileFormat": args.get("EXPORT_FORMAT") or "png",
    "bitDepth": str(args.get("EXPORT_BIT_DEPTH") or "8"),
    "paddingAlgorithm": args.get("EXPORT_PADDING") or "infinite",
}
if parameters["paddingAlgorithm"] in ("transparent", "color", "diffusion"):
    parameters["dilationDistance"] = int(args.get("EXPORT_DILATION", 16))
size = str(args.get("EXPORT_SIZE") or "Project")
if size.isdigit():
    parameters["sizeLog2"] = int(size).bit_length() - 1  # Painter's: 2048 is 11


def _set_name(texture_set):
    """Its name on any Painter: a property since 10.1, a method before."""
    name = texture_set.name
    return str(name) if isinstance(name, str) else name()


def _export():
    os.makedirs(folder, exist_ok=True)
    result = export.export_project_textures(
        {
            "exportShaderParams": False,
            "exportPath": folder,
            "defaultExportPreset": preset,
            "exportList": [{"rootPath": _set_name(ts)} for ts in textureset.all_texture_sets()],
            "exportParameters": [{"parameters": parameters}],
        }
    )
    written = [path for files in result.textures.values() for path in files]
    status = getattr(result.status, "name", str(result.status))
    if status == "Cancelled":
        raise RuntimeError("The export was cancelled in Painter.")
    if status == "Error" or not written:
        raise RuntimeError(f"Painter wrote {len(written)} map(s) to {folder}: {result.message or status}")
    args.report(f"{len(written)} map(s) written to {folder}.")
    if status != "Success":
        args.report(f"Note: {result.message}")


# Outside an undo entry: Painter computes the layer stack only once one is
# closed, so inside one this would write the maps of before the last edit.
try:
    args.when_idle(_export, undo=False)
except TypeError:  # a plugin loaded before steps could opt out of the undo entry
    raise RuntimeError(
        "This Painter still serves an older substance_rpc plugin: in Painter, "
        "Python > Reload Plugins Folder, then run this again."
    ) from None
