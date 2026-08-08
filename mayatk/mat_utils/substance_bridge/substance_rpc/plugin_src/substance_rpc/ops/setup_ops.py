# !/usr/bin/python
# coding=utf-8
"""Project-setup ops: resolution, the baking high poly, and mesh maps.

These are the knobs Painter's command line cannot express: it dropped
``--resolution``, never had a high-poly flag, and its ``--mesh-map``
applies a map to *every* texture set at once (so a multi-material asset
gets one material's AO smeared across all of them). The DCC bridges
route all three here instead.

Both share one problem: the bridge dispatches them right after launching
Painter, when the user still has the New Project wizard in front of them
and ``project.is_open()`` is False. Erroring there would make the
knobs work only on already-open projects -- exactly the case the
bridges care least about. So each op **applies now if it can, and
otherwise arms itself**: the pending value is stored and replayed the
moment a project opens, via Painter's event dispatcher.

Pending values are last-write-wins and are consumed on apply, so a
second send before the wizard closes supersedes the first rather than
queueing, and a value can never leak into a later, unrelated project.
"""
import os

from .. import run_on_main_thread
from .. import register


# Values awaiting a project. ``None`` = nothing pending for that knob.
_pending = {"resolution": None, "high_poly": None, "mesh_maps": None}

# True once the project-opened listener is connected (idempotent arming).
_listener_connected = False

# The (event, callback) pair actually subscribed, so :func:`teardown` can
# undo it. Painter's dispatcher has no "disconnect everything" call, and a
# callback left live across a plugin disable would keep mutating projects.
_subscription = None


# -- Deferral plumbing -----------------------------------------------------


def _project_is_open():
    """True when Painter has a project open (False outside Painter too)."""
    try:
        import substance_painter.project as project  # noqa: PLC0415
    except ImportError:
        return False
    try:
        return bool(project.is_open())
    except Exception:  # noqa: BLE001
        return False


def _connect_listener():
    """Subscribe to the project-ready event once; no-op outside Painter.

    ``ProjectEditionEntered`` is preferred over ``ProjectOpened``: it fires
    when the project is actually editable, which is when texture sets exist
    and baking parameters can be written. Older Painter APIs that lack it
    fall back to ``ProjectOpened``.
    """
    global _listener_connected, _subscription
    if _listener_connected:
        return
    try:
        import substance_painter.event as spevent  # noqa: PLC0415
    except ImportError:
        return
    event = getattr(spevent, "ProjectEditionEntered", None) or getattr(
        spevent, "ProjectOpened", None
    )
    dispatcher = getattr(spevent, "DISPATCHER", None)
    if event is None or dispatcher is None:
        print("[substance_rpc] no project event available; cannot defer setup.")
        return
    dispatcher.connect(event, _on_project_ready)
    _subscription = (dispatcher, event)
    _listener_connected = True


def teardown():
    """Drop any pending values and unsubscribe (plugin-disable hook).

    Without this, disabling the plugin would leave a live callback that
    still rewrites a project's resolution / high poly the next time one
    opens -- long after the user turned the bridge off. Safe to call when
    nothing was ever armed.
    """
    global _listener_connected, _subscription
    _pending.update({key: None for key in _pending})
    subscription, _subscription = _subscription, None
    _listener_connected = False
    if subscription is None:
        return
    dispatcher, event = subscription
    try:
        dispatcher.disconnect(event, _on_project_ready)
    except Exception as exc:  # noqa: BLE001 -- teardown must never raise
        print(f"[substance_rpc] could not disconnect project listener: {exc}")


def _on_project_ready(*_args):
    """Apply (and consume) whatever was pending when the project opened.

    Each knob is isolated: one that fails must not swallow the others, and
    none of them may propagate out of a Painter event callback.
    """
    for slot, apply, label in (
        ("resolution", _apply_resolution, "resolution"),
        ("high_poly", _apply_high_poly, "high poly"),
        ("mesh_maps", _apply_mesh_maps, "mesh maps"),
    ):
        value, _pending[slot] = _pending[slot], None
        if not value:
            continue
        try:
            apply(value)
            print(f"[substance_rpc] applied deferred {label}: {value}")
        except Exception as exc:  # noqa: BLE001 -- never break Painter's event
            print(f"[substance_rpc] deferred {label} FAILED: {exc}")


def _defer(slot, value, label):
    """Stash *value* for replay on project-open and report that back."""
    _pending[slot] = value
    _connect_listener()
    return {
        "applied": False,
        "deferred": bool(_listener_connected),
        slot: value,
        "detail": (
            f"No project open; {label} will be applied when one opens."
            if _listener_connected
            else f"No project open and no event dispatcher; {label} was dropped."
        ),
    }


# -- Document resolution ---------------------------------------------------


def _apply_resolution(size):
    """Set every texture set's resolution to *size* x *size*."""
    import substance_painter.textureset as textureset  # noqa: PLC0415

    resolution = textureset.Resolution(int(size), int(size))
    names = []
    for texture_set in textureset.all_texture_sets():
        texture_set.set_resolution(resolution)
        names.append(texture_set.name())
    return names


@register("project.set_resolution")
def set_resolution(size=0):
    """Set the document resolution of every texture set to *size* px square.

    Painter's dropped ``--resolution`` flag, re-exposed. A *size* of 0
    (or falsy) clears any pending value and does nothing -- the panel's
    "Project default" choice.

    Returns ``{"applied": bool, ...}``; ``applied`` is False when the
    value was deferred to the next project-open.
    """
    size = int(size or 0)
    if size <= 0:
        _pending["resolution"] = None
        return {"applied": False, "skipped": True, "detail": "No resolution requested."}

    if not _project_is_open():
        return _defer("resolution", size, "the resolution")

    names = run_on_main_thread(_apply_resolution, size)
    return {"applied": True, "size": size, "texture_sets": names}


# -- Baking high-poly mesh -------------------------------------------------

# Painter has never documented a stable spelling for the high-poly entry in
# ``BakingParameters.common()``. Match case-insensitively against these
# rather than hard-coding one, and report what Painter actually offers if
# none of them is present -- a wrong guess otherwise fails as a bare KeyError.
_HIGH_POLY_KEYS = ("hipolymesh", "highpolymesh", "hipoly_mesh", "highdefinitionmesh")


def _as_file_url(path):
    """Painter's mesh-valued baking properties take a ``file:///`` URL."""
    path = str(path).replace("\\", "/")
    if "://" in path:
        return path
    return "file:///" + path.lstrip("/")


def _apply_high_poly(mesh_path):
    """Point every texture set's baking parameters at *mesh_path*."""
    import substance_painter.baking as baking  # noqa: PLC0415
    import substance_painter.textureset as textureset  # noqa: PLC0415

    url = _as_file_url(mesh_path)
    applied = []
    for texture_set in textureset.all_texture_sets():
        params = baking.BakingParameters.from_texture_set(texture_set)
        common = params.common()
        match = next(
            (v for k, v in common.items() if k.lower() in _HIGH_POLY_KEYS), None
        )
        if match is None:
            raise KeyError(
                "No high-poly parameter in Painter's common baking "
                f"parameters. Available: {sorted(common)}"
            )
        baking.BakingParameters.set({match: url})
        applied.append(texture_set.name())
    return applied


@register("bake.set_high_poly")
def set_high_poly(mesh_path=""):
    """Set the Hipoly Mesh of every texture set's baking parameters.

    *mesh_path* is a local path or a ``file:///`` URL; an empty value
    clears any pending high poly and does nothing.

    Raises:
        ValueError: *mesh_path* is not a file on disk.
    """
    if not mesh_path:
        _pending["high_poly"] = None
        return {"applied": False, "skipped": True, "detail": "No high poly requested."}
    if "://" not in str(mesh_path) and not os.path.isfile(str(mesh_path)):
        raise ValueError(f"High-poly mesh not found on disk: {mesh_path!r}")

    if not _project_is_open():
        return _defer("high_poly", mesh_path, "the high poly")

    applied = run_on_main_thread(_apply_high_poly, mesh_path)
    return {"applied": True, "mesh_path": mesh_path, "texture_sets": applied}


# -- Per-texture-set mesh maps ---------------------------------------------

# Painter's ``MeshMapUsage`` member names have varied across releases, so
# resolve each of our usage keys against whatever this build exposes rather
# than importing a name that may not be there. First match wins. Keys are
# exactly the values the bridge emits (``SubstanceBridge.MESH_MAP_TYPES``);
# an unlisted key still resolves by its own name.
_MESH_MAP_USAGES = {
    "ambient_occlusion": ("ambientocclusion", "ao", "ambient_occlusion"),
    "normal": ("normal", "normalbase", "normal_base"),
    "thickness": ("thickness",),
}


def _enum_members(enum):
    """``{normalized_name: member}`` for a Painter enum class."""
    return {
        member.lower().replace("_", ""): getattr(enum, member)
        for member in dir(enum)
        if not member.startswith("_")
    }


def _resolve_usage(name):
    """Map one of our usage keys onto this Painter's ``MeshMapUsage`` member.

    Returns ``None`` when the build has no such usage -- the caller skips
    that map rather than failing the whole assignment.
    """
    import substance_painter.textureset as textureset  # noqa: PLC0415

    usage_enum = getattr(textureset, "MeshMapUsage", None)
    if usage_enum is None:
        return None
    members = _enum_members(usage_enum)
    for candidate in _MESH_MAP_USAGES.get(name, (name,)):
        member = members.get(candidate.lower().replace("_", ""))
        if member is not None:
            return member
    return None


def _resolve_resource_usage(resource):
    """The ``resource.Usage`` member to import a mesh-map image under.

    ``TEXTURE`` is the documented one, but the enum has been reshaped
    before -- resolve it rather than hard-coding an attribute that would
    turn a missing name into an AttributeError mid-assignment.

    Raises:
        AttributeError: the build exposes no usable member (message lists
            what it does have, so the miss is diagnosable from the log).
    """
    usage_enum = getattr(resource, "Usage", None)
    members = _enum_members(usage_enum) if usage_enum is not None else {}
    for candidate in ("texture", "meshmap", "baked"):
        member = members.get(candidate)
        if member is not None:
            return member
    raise AttributeError(
        f"No usable resource.Usage member for a mesh map. Available: {sorted(members)}"
    )


def _apply_mesh_maps(manifest_path):
    """Assign each material's mesh maps to the matching texture set.

    The manifest's ``mesh_maps`` section is ``{material: {usage: path}}``,
    and Painter names each texture set after the FBX material -- so the
    match is by name. A texture set with no entry is left alone, which is
    what makes this safe to run against a project the user has already
    reorganised.

    Returns ``{texture_set: [usage, ...]}`` for what was actually assigned.
    """
    import json  # noqa: PLC0415

    import substance_painter.resource as resource  # noqa: PLC0415
    import substance_painter.textureset as textureset  # noqa: PLC0415

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    wiring = manifest.get("mesh_maps") or {}
    if not wiring:
        return {}

    resource_usage = _resolve_resource_usage(resource)
    applied = {}
    for texture_set in textureset.all_texture_sets():
        name = texture_set.name()
        # Sorted so a multi-map material assigns in a stable order; the
        # manifest is JSON, whose object order is whatever json.load gives.
        for usage_name, path in sorted((wiring.get(name) or {}).items()):
            if not os.path.isfile(path):
                print(f"[substance_rpc] mesh map missing on disk: {path}")
                continue
            usage = _resolve_usage(usage_name)
            if usage is None:
                print(f"[substance_rpc] this Painter has no '{usage_name}' mesh map.")
                continue
            imported = resource.import_project_resource(
                path, resource_usage, os.path.basename(path)
            )
            texture_set.set_mesh_map_resource(usage, imported.identifier())
            applied.setdefault(name, []).append(usage_name)
    return applied


@register("textures.apply_mesh_maps")
def apply_mesh_maps(manifest_path=""):
    """Wire each material's baked maps onto its own texture set.

    The per-texture-set answer to ``--mesh-map``, which can only apply a
    map to every set at once -- on a multi-material asset that smears one
    material's AO across all of them.

    *manifest_path* is the ``<name>.materials.json`` the bridge wrote; an
    empty value clears any pending assignment and does nothing.

    Raises:
        ValueError: *manifest_path* is not a file on disk.
    """
    if not manifest_path:
        _pending["mesh_maps"] = None
        return {"applied": False, "skipped": True, "detail": "No manifest given."}
    if not os.path.isfile(manifest_path):
        raise ValueError(f"Manifest not found on disk: {manifest_path!r}")

    if not _project_is_open():
        return _defer("mesh_maps", manifest_path, "the mesh maps")

    applied = run_on_main_thread(_apply_mesh_maps, manifest_path)
    return {"applied": bool(applied), "texture_sets": applied}


@register("bake.pending_setup")
def pending_setup():
    """Return what is queued for the next project-open (diagnostics)."""
    return {"listener": _listener_connected, **_pending}
