# Substance Painter "import + bake lighting to diffuse" template.
#
# Builds a new Painter project from the Maya selection (FBX + embedded
# textures, same as import.py), then queues a Painter-side workflow:
#
#   1. Switch the viewport renderer to Iray.
#   2. Render the loaded scene to a PNG on disk.
#   3. Import the rendered PNG as a fill-layer texture in the texture
#      set's Base Color (diffuse) channel.
#
# Net effect: the lit appearance the artist saw in Maya is baked into
# the diffuse channel of every texture set, so Painter's viewport (any
# renderer) starts off looking already-lit. The user can then add
# further work on top.
#
# ============================================================================
# STATUS: BLOCKED on Painter-side plugin (same wall as reimport / render)
# ============================================================================
# Stock Substance 3D Painter doesn't auto-bind a JSON-RPC port on launch.
# Until a custom Painter Python plugin under
# ``%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins``
# stands up an HTTP JSON-RPC server, sending this template will hang on
# ``PainterRpcClient.wait_until_ready`` and surface a timeout error.
#
# The JS API symbol names below (``alg.shaders.setCurrent``,
# ``alg.imageExporter.exportRenderImage``, ``alg.resources.importResource``,
# ``alg.layers.insertLayerInstance``) are best-effort guesses against
# Painter's published JS surface; the plugin shim should map them to the
# concrete ``substance_painter.*`` Python equivalents and may need to
# adjust field names. The Maya side is right; only the Painter shim
# needs verification.
#
# Plugin-shim responsibilities not expressed in the JS below:
#
#   * **Async coordination** -- ``exportRenderImage`` is likely async;
#     the ``importResource`` call must wait for the PNG to actually
#     land on disk. The shim should await/poll before proceeding.
#   * **Multi texture set iteration** -- the JS targets the project's
#     default texture set; meshes with multiple materials / UDIMs need
#     the shim to loop ``alg.project.textureSets()`` (or equivalent)
#     and insert one fill layer per set.
# ============================================================================
#
# DRY note for maintainers:
#   LAUNCH_ARGS / FBX_OPTIONS duplicate ``import.py`` verbatim. This is
#   structural -- :func:`parse_template` uses ``ast.literal_eval`` so a
#   syntax error in one template can't crash others, which means
#   templates can't share constants via ``from . import _common``.
#   When changing the import line, update both files together.

"""Import the FBX as a new project, then bake Iray lighting into diffuse."""

BRIDGE_MODES = ("send_to",)

# Same launch line as import.py -- the project is created from the FBX.
# Resolution / normal-map format / project template etc. are no longer
# CLI-tunable in current Painter; the user sets them in the New Project
# dialog. See import.py for the full reasoning behind the trimmed list.
LAUNCH_ARGS = [
    "--mesh", "__FBX_PATH__",
]

# Multi-step JS body delivered to Painter after the project is open and
# RPC is reachable. Quoting follows the convention:
#  * User PARAMS arrive JS-quoted via :func:`uitk.bridge.js_literal`.
#  * Internal tokens (``__OUTPUT_DIR__``) substitute raw, so the template
#    body wraps them in manual double quotes.
RPC_SCRIPT = """
// 1. Pick Iray as the viewport renderer so the next render call uses it.
alg.shaders.setCurrent("iray");

// 2. Render the loaded scene to disk. The ``||`` fallback uses the
//    bridge's output dir when PAINTER_RENDER_OUTPUT_PATH is empty.
var bakePath = __PAINTER_RENDER_OUTPUT_PATH__ || ("__OUTPUT_DIR__" + "/baked_lighting.png");
alg.imageExporter.exportRenderImage({
    path: bakePath,
    width: __PAINTER_RENDER_WIDTH__,
    height: __PAINTER_RENDER_HEIGHT__,
    samples: __PAINTER_RENDER_SAMPLES__
});

// 3. Pull the rendered PNG back in as a project resource and create a
//    fill-layer instance against the base-color (diffuse) channel.
//    The plugin shim must wait for step 2 to actually write the file,
//    and iterate texture sets if the mesh has more than one.
var bakeRes = alg.resources.importResource(bakePath, "texture");
alg.layers.insertLayerInstance({
    name: "Baked Iray Lighting",
    channel: "baseColor",
    resource: bakeRes
});
"""

BUILD_MANIFEST = True

# Embed every Maya-referenced texture so the new project starts already
# textured -- the Iray render then captures lighting *on top of* the
# user's look-dev.
FBX_OPTIONS = {
    "FBXExportEmbeddedTextures": True,
}

# Fresh launch (we're creating a new project, not updating one).
TARGET_INSTANCE = "new"
