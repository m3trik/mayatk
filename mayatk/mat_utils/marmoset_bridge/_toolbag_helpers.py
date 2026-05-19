# !/usr/bin/python
# coding=utf-8
"""Shared helpers for Marmoset Toolbag template scripts.

These run inside Toolbag's bundled Python (where ``mset`` is available) --
NOT inside Maya. They must not import mayatk or anything else from the
host Maya install.

Template scripts pick this module up via a ``sys.path`` insert pointing at
this package directory; the path itself is substituted into the rendered
script via the ``__TOOLBAG_HELPERS_DIR__`` token (see render_template).

The module lives at the package root (not inside ``templates/``) so that
``list_templates()`` does not list it as a selectable template.

Diagnostics
-----------
Toolbag's ``send_to`` mode runs the script in a GUI process whose stdout is
not captured by the Maya-side bridge. To make these runs debuggable, every
helper that prints also tees the message into a ``<base>.toolbag.log``
file next to the manifest. Call :func:`begin_log` once at the top of a
template (after the manifest path is known) to enable file logging.
"""
import json
import os

try:
    import mset
except ImportError:
    mset = None


# --------------------------------------------------------------------------
# Tee logger -- prints AND appends to a file alongside the manifest, so the
# user can open ``<base>.toolbag.log`` after a send_to run and see exactly
# what the template did (sky load, mat name matches, slot wiring, etc.).
# --------------------------------------------------------------------------
_LOG_PATH = None


def derive_per_run_log_path(manifest_path):
    """Return the ``<base>.toolbag.log`` path next to *manifest_path*.

    Pure path math, no I/O. Lives here (Toolbag-side helper module) rather
    than in the Maya-side bridge so it can be the single source of truth
    even though the helper writes the file from inside Toolbag and the
    bridge surfaces the path from outside.
    """
    if not manifest_path:
        return ""
    stem, _ext = os.path.splitext(manifest_path)
    return stem.replace(".materials", "") + ".toolbag.log"


def begin_log(reference_path):
    """Start a fresh log file alongside *reference_path*.

    *reference_path* is typically the manifest path. The log file is
    truncated each run so users can read the latest send without scrolling.
    """
    global _LOG_PATH
    if not reference_path:
        _LOG_PATH = None
        return None
    try:
        _LOG_PATH = derive_per_run_log_path(reference_path)
        # Truncate so each send produces a fresh log.
        with open(_LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write("")
    except Exception:
        _LOG_PATH = None
    return _LOG_PATH


def log(msg):
    """Print *msg* and (best-effort) append it to the active log file."""
    print(msg)
    if _LOG_PATH is None:
        return
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


# Maya shader slot -> (Toolbag subroutine attr, [candidate field names]).
#
# Toolbag's subroutines (albedo, microsurface, reflectivity, etc.) have
# multiple variants, and each variant's field is named after itself. The
# microsurface module, for example, exposes "Gloss Map" when set to the
# Gloss subroutine and "Roughness Map" when set to the Roughness Map
# subroutine -- there is no universal "Microsurface Map" field.
#
# We try each candidate against ``subroutine.getFieldNames()`` and use the
# first match, falling back to the first listed name if discovery fails.
SLOT_MAP = {
    "baseColor": ("albedo", ["Albedo Map"]),
    "normal": ("surface", ["Normal Map"]),
    "roughness": ("microsurface", ["Roughness Map", "Gloss Map", "Microsurface Map"]),
    "metallic": ("reflectivity", ["Metalness Map"]),
    "ambientOcclusion": ("occlusion", ["Occlusion Map"]),
    "emission": ("emissive", ["Emissive Map"]),
    "opacity": ("transparency", ["Transparency Map"]),
}


def _pick_field_name(sub, candidates):
    """Return the first candidate field name that the subroutine exposes.

    Falls back to the first available field if no candidate matches (most
    subroutines expose exactly one texture-map field). Returns None if the
    subroutine has no fields at all, which usually means the active
    variant is "None" / disabled.
    """
    try:
        available = list(sub.getFieldNames())
    except Exception:
        # Older API or unexpected stub -- assume the first candidate works.
        return candidates[0] if candidates else None
    for name in candidates:
        if name in available:
            return name
    return available[0] if available else None


def find_material(name, scene_mats):
    """Return the Toolbag material whose name matches *name*.

    FBX importers sometimes append suffixes (``_ncl1_1``, ``(Instance)``,
    etc.), so we try an exact match first, then fall back to substring.
    """
    for m in scene_mats:
        if m.name == name:
            return m
    for m in scene_mats:
        if m.name.startswith(name) or name in m.name:
            return m
    return None


def load_manifest(manifest_path):
    """Return the ``materials`` dict from a MatManifest JSON sidecar.

    Missing/unreadable file -> ``{}``. Callers can treat an empty dict as
    "nothing to wire" without distinguishing absent from empty.
    """
    if not manifest_path or not os.path.isfile(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        log(f"[toolbag_helpers] Could not read manifest {manifest_path}: {exc}")
        return {}
    return data.get("materials", {}) or {}


def wire_materials_from_manifest(manifest_path, verbose=True):
    """Wire every texture slot in *manifest_path* onto matching Toolbag mats.

    Returns the number of slots successfully wired. Best-effort: per-slot
    failures are logged when *verbose* but never raised, so one bad field
    doesn't abort the whole pass.
    """
    if mset is None:
        if verbose:
            log("[toolbag_helpers] mset not available; cannot wire materials.")
        return 0

    mat_map = load_manifest(manifest_path)
    if not mat_map:
        if verbose:
            log(f"[toolbag_helpers] Manifest empty or missing at: {manifest_path}")
            log("[toolbag_helpers] Nothing to wire -- check Maya-side MatManifest.build().")
        return 0

    # Toolbag 5 API: getAllMaterials() is the documented entry point. The
    # earlier ``getAllObjects() + isinstance(mset.MaterialObject)`` filter
    # raised AttributeError -- ``MaterialObject`` does not exist; the class
    # is ``mset.Material`` and there is no need to filter manually.
    scene_mats = list(mset.getAllMaterials())
    if verbose:
        log(f"[toolbag_helpers] Scene contains {len(scene_mats)} material(s).")
        log(f"[toolbag_helpers] Scene mat names: {[m.name for m in scene_mats]}")
        log(f"[toolbag_helpers] Manifest mat names: {list(mat_map.keys())}")

    wired = 0
    for mat_name, slots in mat_map.items():
        tb_mat = find_material(mat_name, scene_mats)
        if tb_mat is None:
            if verbose:
                log(f"  SKIP  '{mat_name}' -- no matching Toolbag material.")
            continue
        if verbose:
            log(f"  Wiring '{mat_name}' -> '{tb_mat.name}'")

        for slot_key, tex_path in slots.items():
            mapping = SLOT_MAP.get(slot_key)
            if not mapping:
                if verbose:
                    log(f"    ? No Toolbag mapping for slot '{slot_key}', skipping.")
                continue

            # Pre-flight: if the texture file is missing on disk Toolbag
            # accepts the path silently and the slot appears empty in the
            # UI. Surface this clearly so the user knows it's a *data*
            # problem (e.g. an unresolved Dropbox path), not a wire bug.
            if not os.path.isfile(tex_path):
                if verbose:
                    log(f"    ! {slot_key}: file not found on disk -> {tex_path}")
                continue

            module_attr, candidates = mapping
            sub = getattr(tb_mat, module_attr, None)
            if sub is None:
                if verbose:
                    log(f"    ? Material has no '{module_attr}' module.")
                continue

            # Toolbag's subroutine variants each expose a differently-named
            # field. Discover the actual field name from the live module
            # rather than hardcoding a guess that breaks when a project
            # uses (e.g.) the Gloss subroutine instead of Roughness Map.
            field_name = _pick_field_name(sub, candidates)
            if field_name is None:
                if verbose:
                    log(
                        f"    ! {slot_key}: subroutine '{module_attr}' exposes "
                        f"no fields -- variant may be disabled."
                    )
                continue

            try:
                sub.setField(field_name, tex_path)
                wired += 1
                if verbose:
                    log(
                        f"    + {slot_key} -> '{field_name}' = "
                        f"{os.path.basename(tex_path)}"
                    )
            except Exception as exc:
                if verbose:
                    log(f"    ! {slot_key}: {exc}")

    if verbose:
        log(f"[toolbag_helpers] Wired {wired} texture slot(s).")
    return wired


def _classify_by_chain(o, high_suffix, low_suffix):
    """Walk *o* and its ancestors via ``.parent``; return the suffix match
    at the first level encountered.

    Returns ``("high", node)``, ``("low", node)``, or ``(None, None)``.
    ``node`` is the actual object whose name carried the suffix -- useful
    for diagnostics when a parent group decides the classification.

    The walk visits *o* first, then *o.parent*, then *o.parent.parent*,
    etc. This means a mesh's own suffix always wins over an ancestor's;
    only when *o* itself has no suffix does the parent group decide.
    """
    cur = o
    visited = 0  # cheap loop-cycle guard for malformed scene graphs
    while cur is not None and visited < 64:
        name = getattr(cur, "name", "")
        # Only string names are meaningful for suffix comparison. A non-
        # string ancestor name (rare in real Toolbag scenes, common when
        # something hands us a stubbed object) is skipped rather than
        # halting the walk -- a real parent further up may still match.
        if isinstance(name, str) and name:
            stem = name.rsplit(".", 1)[0] if "." in name else name
            if high_suffix and stem.endswith(high_suffix):
                return "high", cur
            if low_suffix and stem.endswith(low_suffix):
                return "low", cur
        cur = getattr(cur, "parent", None)
        visited += 1
    return None, None


def split_high_low(objects, high_suffix, low_suffix, pre_classified=None):
    """Group *objects* into ``(highs, lows, others)`` by name suffix.

    Each object is classified in priority order:

    1. *pre_classified* (if supplied) -- an explicit
       ``{mesh_short_name: 'high' | 'low'}`` map. Wins over everything.
       The Maya bridge builds this from the Maya parent chain BEFORE
       FBX export, because Toolbag's importer flattens parent transforms
       and we need a way to carry the classification across that wall.
    2. Walking the object's parent chain (self first, then ``.parent``,
       then ``.parent.parent``, ...). First name to match a suffix wins.

    Without *pre_classified* the chain walker handles three tagging styles:

    * every mesh tagged individually -- ``cube_high``, ``cube_low``; or
    * parent group tagged once -- ``engine_high`` containing
      ``engine_block``, ``engine_pipes``, ...; or
    * mix of the two -- a child's own suffix always wins over an ancestor.

    (Note: parent-group tagging only survives the round trip through
    Toolbag's FBX importer when *pre_classified* is provided. Toolbag
    flattens empty parent transforms on import regardless of FBX
    contents.)

    Resolution rules (after chain classification):

    +-----------+----------+-----------------------------------------------+
    | HIGH      | LOW      | Behaviour                                     |
    +===========+==========+===============================================+
    | set       | set      | both matched explicitly; non-matches go       |
    |           |          | to *others* and stay unpaired.                |
    +-----------+----------+-----------------------------------------------+
    | set       | empty    | matching meshes -> highs; everything else     |
    |           |          | -> lows (common workflow: only suffix the     |
    |           |          | high-poly source).                            |
    +-----------+----------+-----------------------------------------------+
    | empty     | set      | matching meshes -> lows; everything else      |
    |           |          | -> highs.                                     |
    +-----------+----------+-----------------------------------------------+
    | empty     | empty    | nothing can be inferred; all -> others.       |
    +-----------+----------+-----------------------------------------------+

    A mesh whose own name ends in BOTH suffixes (rare: ``cube_high_low``)
    goes to highs; HIGH is checked before LOW at each chain level.

    FBX importers sometimes append a ``.001`` duplicate-suffix; we strip
    it before the suffix check so ``cube_high.001`` and a parent group
    named ``engine_high.001`` still resolve as high-poly.
    """
    high_set = bool(high_suffix)
    low_set = bool(low_suffix)
    pre_classified = pre_classified or {}

    highs, lows, others = [], [], []
    for o in objects:
        # 1. Pre-classified hint wins over everything else.
        name = getattr(o, "name", "") or ""
        pre = pre_classified.get(name)
        if pre == "high":
            highs.append(o)
            continue
        if pre == "low":
            lows.append(o)
            continue

        # 2. Walk this object's own parent chain.
        match, _node = _classify_by_chain(o, high_suffix, low_suffix)

        if match == "high":
            highs.append(o)
        elif match == "low":
            lows.append(o)
        elif high_set and not low_set:
            lows.append(o)            # rest-is-low (HIGH-driven workflow)
        elif low_set and not high_set:
            highs.append(o)           # rest-is-high (LOW-driven workflow)
        else:
            others.append(o)
    return highs, lows, others


def collect_mesh_objects(root):
    """Recursively gather ``mset.MeshObject`` descendants of *root*.

    ``mset.importModel()`` returns an ``mset.ExternalObject`` wrapper
    around the imported file (Toolbag 5+), not a flat list of meshes.
    Walking ``getChildren()`` recursively gives back the actual mesh
    transforms the baker needs -- the wrapper itself, animation
    containers, and any non-mesh hierarchy nodes are filtered out.

    Accepts: the ExternalObject from ``importModel``, a single MeshObject,
    a non-mesh transform with mesh descendants, or a flat list (callers
    that already pre-flattened the tree). Returns ``[]`` on anything else.
    """
    if root is None or mset is None:
        return []

    # Single mesh -- check before getChildren because MeshObject inherits
    # SceneObject's getChildren and we don't want to descend into it.
    if isinstance(root, mset.MeshObject):
        return [root]

    # Transform / ExternalObject node: walk children.
    if hasattr(root, "getChildren"):
        try:
            children = root.getChildren() or []
        except Exception:  # noqa: BLE001 -- Toolbag API can raise opaque errors.
            return []
        out = []
        for c in children:
            out.extend(collect_mesh_objects(c))
        return out

    # Last resort: caller may have handed us a Python list/iterable of
    # objects (mixed). Filter to MeshObject in that case.
    try:
        return [o for o in root if isinstance(o, mset.MeshObject)]
    except TypeError:
        return []


def apply_sky_preset(preset_path):
    """Load a ``.tbsky`` preset onto the scene's existing SkyObject.

    Returns True on success, False otherwise. Failures are logged but
    never raise so callers can chain this before other setup steps.
    """
    if mset is None or not preset_path:
        return False
    try:
        # Toolbag 5: class is SkyBoxObject (not SkyObject); the method to
        # apply a .tbsky preset is loadSky(path) (not loadPreset).
        skies = [
            o for o in mset.getAllObjects() if isinstance(o, mset.SkyBoxObject)
        ]
        if not skies:
            log("[toolbag_helpers] No SkyBoxObject in scene; skipping sky preset.")
            return False
        skies[0].loadSky(preset_path)
        return True
    except Exception as exc:
        log(f"[toolbag_helpers] Sky preset load failed: {exc}")
        return False


def frame_in_viewport():
    """Frame the imported scene in the viewport (best-effort).

    Toolbag 4/5 doesn't have a documented Python "frame scene" call, so we
    try the menu route first and fall back to ``frameInView`` on any
    object that exposes it. Failure is harmless.
    """
    if mset is None:
        return False
    try:
        mset.callMenuItem("View/Frame Selection")
        return True
    except Exception:
        pass
    try:
        for o in mset.getAllObjects():
            if hasattr(o, "frameInView"):
                o.frameInView()
                return True
    except Exception:
        pass
    return False
