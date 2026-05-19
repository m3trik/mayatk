# Substance Painter "new project" template.
#
# Mirrors the New Project dialog in Painter: hand off the FBX with all
# Maya-referenced textures embedded, plus user-tunable Document Resolution
# / Normal Map Format / UV Tile Mode / Project Template / Tangent Mode /
# Import Cameras / Auto-Unwrap / Import Baked Maps.
#
# The bridge surfaces each ``__KEY__`` token below as a UI widget in the
# Maya panel; see ``parameters.py`` for the full spec of each PARAM.
# Painter ignores CLI flags it doesn't recognize, so additions here are
# safe to experiment with even on older Painter versions.

"""Send the FBX to Painter as a new project."""

BRIDGE_MODES = ("send_to",)

# Painter command-line args. Internal tokens (__FBX_PATH__) and user
# PARAMS are substituted by the bridge before launch.
#
# Empty optional values are skipped: an entry like ``--template`` followed
# by an empty rendered value gets dropped entirely by the bridge, so the
# user can leave PAINTER_PROJECT_TEMPLATE blank without producing a broken
# ``--template ""`` argv pair.
#
# ``__PAINTER_BAKED_MAPS__`` is referenced here purely so the slot panel
# surfaces the multi-file picker widget -- file_list values are NOT
# substituted into LAUNCH_ARGS. The bridge stages the selected files into
# the FBX output folder out-of-band and records them in the manifest.
LAUNCH_ARGS = [
    "--mesh", "__FBX_PATH__",
    "--resolution", "__PAINTER_RESOLUTION__",
    "--normal-map-format", "__PAINTER_NORMAL_FORMAT__",
    "--uvtile-mode", "__PAINTER_UV_TILE_MODE__",
    "--template", "__PAINTER_PROJECT_TEMPLATE__",
]

# No RPC dispatch; the new project is created via Painter's startup wizard.
RPC_SCRIPT = ""

# Build a material manifest so a future Painter plugin can map FBX
# material slots back to Maya shaders + baked map files.
BUILD_MANIFEST = True

# Embed every Maya-referenced texture into the FBX so Painter's New
# Project wizard pre-populates each texture set's base color.
FBX_OPTIONS = {
    "FBXExportEmbeddedTextures": True,
}

# Painter only honours these flags during new-project creation; reusing
# a running Painter wouldn't apply them. Force a fresh launch.
TARGET_INSTANCE = "new"
