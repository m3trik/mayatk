# Substance Painter "reimport mesh" template.
#
# One-way update of the project currently open in a running Painter
# instance: Maya re-exports the FBX, then tells Painter to replay
# ``alg.mesh.reimportMesh`` against it. Painter preserves texture-set
# work where UVs match; Maya is unchanged by the operation.
#
# This is NOT a roundtrip -- no data comes back from Painter. The mode
# is ``send_to`` (interactive Painter), targeted at the existing
# instance rather than a fresh one.
#
# ============================================================================
# STATUS: BLOCKED on Painter-side plugin
# ============================================================================
# Empirical finding (verified 2026-05-18 against Adobe Substance 3D Painter
# installed at ``C:\Program Files\Adobe\Adobe Substance 3D Painter``):
#
#   Stock Painter does not auto-bind a JSON-RPC port on launch. The
#   ``--enable-remote-scripting`` CLI flag has no observable effect --
#   port scans over the standard candidate range during a 75-second
#   startup window returned zero listeners. The bundled
#   ``qrc:/plugins/pythonjsonserver.qml`` plugin loads but does not open
#   a TCP socket.
#
# To make this template usable, a Painter-side Python plugin must be
# installed under
# ``%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins``
# that:
#
#   1. Starts an HTTP JSON-RPC server on a known port (default 8090 here).
#   2. Routes the ``eval`` method to either ``substance_painter.*`` calls
#      or a JS shim, depending on what API surface you want.
#   3. Specifically handles a ``substance_painter.project`` or
#      ``alg.mesh.reimportMesh``-equivalent call to swap the project mesh.
#
# Until that plugin exists, sending this template will hang on
# ``PainterRpcClient.wait_until_ready`` for the configured timeout and
# then surface a "Painter RPC didn't respond" error.
# ============================================================================

"""Update the running Painter's active project from a fresh FBX export."""

# One-way send to an existing instance; not a "roundtrip" -- nothing
# comes back from Painter.
BRIDGE_MODES = ("send_to",)

# No launch -- reuse a running instance. The bridge enforces this via
# TARGET_INSTANCE below; LAUNCH_ARGS is consequently irrelevant.
LAUNCH_ARGS = []

# JS body delivered via the (currently unavailable) JSON-RPC endpoint.
# ``alg.mesh.reimportMesh(<path>)`` is the documented JS API method;
# behaviour is unverified until a real endpoint exists to send it to.
RPC_SCRIPT = 'alg.mesh.reimportMesh("__FBX_PATH__")'

BUILD_MANIFEST = False

# Refuses to run without a managed Painter instance whose RPC pings live.
# In practice this will never succeed against stock Painter -- the
# Painter-side plugin described above must be installed first.
TARGET_INSTANCE = "current"
