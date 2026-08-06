# Substance Painter "reimport mesh" template.
#
# One-way update of the project currently open in a running Painter
# instance: Maya re-exports the FBX *over the same file the project was
# created from* (the path recorded in the scene's fileInfo by the last
# send -- REUSE_RECORDED_EXPORT), then asks Painter to reload it via the
# substance_rpc plugin's ``mesh.reload`` op
# (:func:`substance_painter.project.reload_mesh`). Painter preserves
# paint strokes where UVs / material names still match; Maya is
# unchanged by the operation.
#
# This is NOT a roundtrip -- no data comes back from Painter. The mode
# is ``send_to``, targeted at the existing instance: the bridge first
# checks its managed registry, then probes the default RPC port (8090),
# so a Painter left open from a previous Maya session -- or launched by
# hand -- is found too. It never launches a new instance.
#
# If no reachable Painter is found (plugin not yet enabled, Painter
# closed), the FBX overwrite has still happened and the bridge logs
# NO_CONNECTION_HINT below with the manual reload steps.
#
# Reimport replaces the project's *entire* mesh with this export -- so
# re-select the same object set the project was originally sent with.

"""Update the running Painter's open project from a fresh FBX export."""

# One-way send to an existing instance; not a "roundtrip" -- nothing
# comes back from Painter.
BRIDGE_MODES = ("send_to",)

# Host-side export scope (read by the bridge slots before launch; echoed here so the panel
# exposes the Scope combo): scope=__SCOPE__
#
# Claimed so the panel surfaces them; both are dispatched by the bridge as
# extra RPC ops (not from the static list below) and, on the already-open
# project this template targets, apply immediately:
#   __PAINTER_RESOLUTION__   -- re-resolve every texture set.
#   __PAINTER_HIGH_POLY__    -- re-export <name>_high.fbx beside the mesh
#   __BAKE_SOURCE_SET__      -- the panel's Set/Select/Clear action row
#                               and repoint the Hipoly Mesh at it.

# No launch -- reuse a running instance. The bridge enforces this via
# TARGET_INSTANCE below; LAUNCH_ARGS is consequently irrelevant.
LAUNCH_ARGS = []

# Structured RPC (substance_rpc plugin, {op, kwargs} wire). The modern
# Python API call behind Painter's Edit > Project Configuration reload;
# the legacy-JS ``alg.mesh.reimportMesh`` is not used.
RPC_OPS = [
    (
        "mesh.reload",
        {
            "mesh_path": "__FBX_PATH__",
            "preserve_strokes": True,
            "import_cameras": False,
        },
    ),
]

RPC_SCRIPT = ""

BUILD_MANIFEST = False

# Overwrite the FBX recorded by the previous send (scene fileInfo) so the
# reload hits the exact file Painter's project points at -- even from a
# fresh Maya session or with a different Output Dir resolution.
REUSE_RECORDED_EXPORT = True

# Requires a running Painter with the substance_rpc plugin; never
# launches. The bridge auto-installs the plugin on every send, but a
# Painter started before that install (or with the plugin disabled)
# can't be reached -- hence the manual fallback below.
TARGET_INSTANCE = "current"

NO_CONNECTION_HINT = (
    "The mesh was re-exported to __FBX_PATH__, but no running Painter "
    "answered on the RPC port -- reload it manually: in Painter, use "
    "Edit > Project Configuration > Select (pick that same file) > OK. "
    "For one-click reimport next time, activate the 'substance_rpc' "
    "plugin: in Painter use Python > Reload Plugins Folder (or relaunch "
    "Painter), then tick 'substance_rpc' in the Python menu -- Painter "
    "remembers it after the first time."
)
