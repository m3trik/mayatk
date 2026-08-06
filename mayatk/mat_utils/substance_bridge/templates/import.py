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

# Host-side export scope (read by the bridge slots before launch; echoed here so the panel
# exposes the Scope combo): scope=__SCOPE__

# Painter command-line args. Internal tokens (__FBX_PATH__) and user
# PARAMS are substituted by the bridge before launch. Keep this list to
# flags the *currently shipping* Painter accepts -- a single unknown flag
# makes Painter print a help popup and exit without opening.
#
# ``__PAINTER_INCLUDE_TEXTURES__``, ``__PAINTER_TEXTURE_PREFIX__``,
# ``__PAINTER_UNPACK_MAPS__`` and ``__PAINTER_SPLIT_BY_UDIM__`` are
# referenced here purely so the slot panel surfaces the matching widgets --
# their values do not land in this static list. The bridge expands them
# into argv after rendering: ``--mesh-map <path>`` per staged texture that
# is genuinely a mesh map (when INCLUDE_TEXTURES is on, with each filename
# optionally prefixed by TEXTURE_PREFIX, and channel-packed sources split
# into components first when UNPACK_MAPS is on) and a bare
# ``--split-by-udim`` presence flag (when SPLIT_BY_UDIM is on).
#
# Material channels (base color, roughness, ...) are deliberately NOT
# passed as ``--mesh-map``: Painter has no mesh-map slot for them. They
# reach the project through the FBX's embedded textures and the manifest.
#
# ``__PAINTER_RESOLUTION__``, ``__PAINTER_HIGH_POLY__`` (and the panel's
# ``__BAKE_SOURCE_SET__`` action row) are claimed the
# same way, but reach Painter over the substance_rpc plugin rather than
# argv -- neither has a CLI flag any more. The bridge appends
# ``project.set_resolution`` / ``bake.set_high_poly`` to the RPC dispatch
# only when the user actually asked for them, so a plain send still
# launches Painter without waiting on the plugin's endpoint. Both are
# applied when the New Project wizard finishes (the plugin holds them
# until then).
LAUNCH_ARGS = [
    "--mesh", "__FBX_PATH__",
]

# No RPC dispatch; the new project is created via Painter's startup wizard.
RPC_SCRIPT = ""

# Build a material manifest mapping FBX material slots back to Maya
# shaders + their map files. Its ``mesh_maps`` section is what the
# substance_rpc plugin's ``textures.apply_mesh_maps`` op consumes to put
# each material's AO/normal on ITS OWN texture set -- the thing
# ``--mesh-map`` cannot do, since that flag applies a map to every set.
BUILD_MANIFEST = True

# Embed every Maya-referenced texture into the FBX so Painter's New
# Project wizard pre-populates each texture set's base color.
FBX_OPTIONS = {
    "FBXExportEmbeddedTextures": True,
}

# Painter only honours these flags during new-project creation; reusing
# a running Painter wouldn't apply them. Force a fresh launch.
TARGET_INSTANCE = "new"
