# Substance Painter "new project" template.
#
# Hands off the FBX (with Maya-referenced textures embedded) so Painter
# opens it as a new project. Document Resolution / Normal Map Format /
# Project Template / Tangent Mode used to be CLI-tunable here, but current
# Painter rejects every one of those flags on launch -- the user picks
# them in Painter's New Project dialog instead.
#
# The bridge surfaces each ``__KEY__`` token below as a UI widget in the
# Maya panel; see ``parameters.py`` for the full spec of each PARAM.

"""Send the FBX to Painter as a new project."""

BRIDGE_MODES = ("send_to",)

# Painter command-line args. Internal tokens (__FBX_PATH__) and user
# PARAMS are substituted by the bridge before launch. Keep this list to
# flags the *currently shipping* Painter accepts -- a single unknown flag
# makes Painter print a help popup and exit without opening.
#
# ``__PAINTER_INCLUDE_TEXTURES__``, ``__PAINTER_TEXTURE_PREFIX__`` and
# ``__PAINTER_SPLIT_BY_UDIM__`` are referenced here purely so the slot
# panel surfaces the matching widgets -- their values do not land in
# this static list. The bridge expands them into argv after rendering:
# ``--mesh-map <path>`` per staged texture (when INCLUDE_TEXTURES is on,
# with each filename optionally prefixed by TEXTURE_PREFIX) and a bare
# ``--split-by-udim`` presence flag (when SPLIT_BY_UDIM is on).
LAUNCH_ARGS = [
    "--mesh", "__FBX_PATH__",
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
