# Substance Painter "render via Iray" template.
#
# Targets the project currently open in a running Painter instance and
# asks Painter to render the active viewport with the Iray path tracer,
# writing the resulting image to disk. No Maya FBX export happens --
# this template purely orchestrates a Painter-side operation.
#
# ============================================================================
# STATUS: BLOCKED on Painter-side plugin (same wall as reimport.py)
# ============================================================================
# Stock Substance 3D Painter does not auto-bind a JSON-RPC port on launch
# (verified empirically 2026-05-18). Until a Painter Python plugin under
# ``%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins``
# stands up an HTTP JSON-RPC server, sending this template will hang on
# ``PainterRpcClient.wait_until_ready`` and then surface a "Painter RPC
# didn't respond" error.
#
# Once the plugin exists, the RPC_SCRIPT below dispatches Painter's
# ``alg.imageExporter.exportRenderImage`` (canonical JS API for an
# Iray-quality viewport render). API surface details (exact field
# names, return shape) are documented best-effort and may need to be
# adjusted against the plugin's real JS shim.
# ============================================================================

"""Render the current Painter project via Iray (BLOCKED -- needs Painter plugin)."""

# One-way send to the existing instance; nothing comes back to Maya
# beyond the RPC return value (the saved image path on success).
BRIDGE_MODES = ("send_to",)

# No launch -- reuse the running Painter instance.
LAUNCH_ARGS = []

# Maya doesn't need to export anything; the project is already loaded
# in Painter. EXPORT_FBX=False also relaxes the slot's "nothing
# selected" guard so the user can fire a render with no Maya selection.
EXPORT_FBX = False
FBX_OPTIONS = {}

# JS body sent over the (currently unavailable) JSON-RPC endpoint.
# ``alg.imageExporter.exportRenderImage`` is the documented JS API
# method for triggering an Iray render to file.
#
# Quoting convention:
#  * User PARAMS (``__PAINTER_RENDER_OUTPUT_PATH__``) go through
#    :func:`uitk.bridge.js_literal` and arrive as JS literals --
#    already quoted.
#  * Internal tokens (``__OUTPUT_DIR__``) substitute as raw strings, so
#    the template body wraps them in manual double quotes to land a
#    valid JS string literal.
# Empty ``__PAINTER_RENDER_OUTPUT_PATH__`` renders as ``""`` and the
# ``||`` falls back to ``<output_dir>/painter_render.png``.
RPC_SCRIPT = (
    'alg.imageExporter.exportRenderImage({'
    'path: __PAINTER_RENDER_OUTPUT_PATH__ || ("__OUTPUT_DIR__" + "/painter_render.png"),'
    'width: __PAINTER_RENDER_WIDTH__,'
    'height: __PAINTER_RENDER_HEIGHT__,'
    'samples: __PAINTER_RENDER_SAMPLES__'
    '});'
)

BUILD_MANIFEST = False

# Refuses to run without a managed Painter instance whose RPC pings live.
TARGET_INSTANCE = "current"
