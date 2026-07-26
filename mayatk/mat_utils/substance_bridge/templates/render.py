# Substance Painter "render via Iray" template.
#
# Targets the project currently open in a running Painter instance and
# asks Painter to render the active viewport with the Iray path tracer,
# writing the resulting image to disk. No Maya FBX export happens --
# this template purely orchestrates a Painter-side operation.
#
# ============================================================================
# STATUS: transport OK (substance_rpc plugin); JS body unverified
# ============================================================================
# The bridge now ships a Painter-side ``substance_rpc`` plugin
# (auto-installed on send) whose ``js.evaluate`` op routes this
# RPC_SCRIPT through :func:`substance_painter.js.evaluate` -- so the
# script actually reaches Painter's JS engine. The
# ``alg.imageExporter.exportRenderImage`` symbol + field names below are
# still best-effort against Painter's published legacy-JS surface and
# need verification against a live Painter; adjust here if the call
# errors (the RPC response will carry Painter's exception text).
# ============================================================================

"""Render the current Painter project via Iray (JS body unverified)."""

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
