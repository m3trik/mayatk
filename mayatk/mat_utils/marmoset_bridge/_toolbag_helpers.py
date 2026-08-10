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


class _ToolbagHelpersInternal(object):
    """Internal helpers for ToolbagHelpers."""

    @staticmethod
    def _set_texture_srgb(sub, field_name, srgb, verbose=True):
        """Pin the just-wired texture's colour-space (sRGB vs Linear).

        Toolbag's ``setField(name, path)`` loads every texture Linear
        (``Texture.sRGB = False``); colour maps then render washed-out. We read
        the field's live Texture back and set its ``sRGB`` flag (verified to
        persist on the material). Best-effort: a subroutine/stub whose
        ``getField`` returns no Texture-like object is left untouched.
        """
        try:
            tex = sub.getField(field_name)
        except Exception:
            return
        if tex is None or not hasattr(tex, "sRGB"):
            return
        try:
            tex.sRGB = srgb
        except Exception as exc:
            if verbose:
                ToolbagHelpers.log(
                    f"      (could not set sRGB={srgb} on '{field_name}': {exc})"
                )

    @staticmethod
    def _ensure_subroutine(tb_mat, fix, verbose=True):
        """Switch *tb_mat*'s subroutine so the wanted texture field exists.

        *fix* is a ``(module_attr, subroutine_slot, shader_name,
        wanted_field)`` tuple from :data:`SUBROUTINE_FIXES`. No-op when the
        module already exposes *wanted_field*. Best-effort: a Toolbag build
        with different shader names just logs and leaves the variant alone.
        """
        module_attr, subroutine_slot, shader_name, wanted_field = fix
        sub = getattr(tb_mat, module_attr, None)
        have = []
        if sub is not None:
            try:
                have = list(sub.getFieldNames())
            except Exception:  # noqa: BLE001
                have = []
        if wanted_field in have:
            return
        try:
            tb_mat.setSubroutine(subroutine_slot, shader_name)
            if verbose:
                ToolbagHelpers.log(
                    f"    ~ {subroutine_slot} subroutine -> '{shader_name}'"
                )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                ToolbagHelpers.log(
                    f"    ! could not switch {subroutine_slot} to "
                    f"'{shader_name}': {exc}"
                )

    @staticmethod
    def _neutralize_module_scalars(sub, module_attr, verbose=True):
        """Reset a module's map-multiplier scalars to identity after wiring.

        Toolbag's FBX import leaves non-identity multipliers on the modules
        (verified on 5.02: ``microsurface.Roughness = 0.3``,
        ``reflectivity.Metalness = 0.0``) and ``setSubroutine`` preserves
        them -- so a freshly wired roughness map bakes at 30% and a metalness
        map bakes black. Once a texture drives the module, the map is the
        authority; the scalars must scale it by exactly 1.

        Only fields the live module actually exposes are touched (guarded via
        ``getFieldNames``), so unknown Toolbag builds/variants stay safe.
        """
        wanted = MODULE_NEUTRAL_FIELDS.get(module_attr)
        if not wanted:
            return
        try:
            available = set(sub.getFieldNames())
        except Exception:  # noqa: BLE001
            return
        for field, value in wanted.items():
            if field not in available:
                continue
            try:
                current = sub.getField(field)
            except Exception:  # noqa: BLE001
                current = None
            if current == value:
                continue
            try:
                sub.setField(field, value)
                if verbose:
                    ToolbagHelpers.log(
                        f"      = {field}: {current!r} -> {value!r} "
                        f"(map is now the authority)"
                    )
            except Exception as exc:  # noqa: BLE001
                if verbose:
                    ToolbagHelpers.log(f"      ! could not reset '{field}': {exc}")

    @staticmethod
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

    @staticmethod
    def _classify_by_chain(o, high_suffix, low_suffix, include_children=True):
        """Walk *o* and its ancestors via ``.parent``; return the suffix match
        at the first level encountered.

        Returns ``("source", node)``, ``("target", node)``, or ``(None, None)``.
        ``node`` is the actual object whose name carried the suffix -- useful
        for diagnostics when a parent group decides the classification.

        The walk visits *o* first, then *o.parent*, then *o.parent.parent*,
        etc. This means a mesh's own suffix always wins over an ancestor's;
        only when *o* itself has no suffix does the parent group decide.

        *include_children* is what makes "name the group root once" work: with
        it off the walk stops at *o* itself, so a suffixed group no longer
        adopts its descendants and every mesh must carry the suffix in its own
        name.
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
                    return "source", cur
                if low_suffix and stem.endswith(low_suffix):
                    return "target", cur
            if not include_children:
                break  # own name only -- ancestors don't adopt their children
            cur = getattr(cur, "parent", None)
            visited += 1
        return None, None


class ToolbagHelpers(_ToolbagHelpersInternal):
    """ToolbagHelpers — module namespace."""

    @staticmethod
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

    @staticmethod
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
            _LOG_PATH = ToolbagHelpers.derive_per_run_log_path(reference_path)
            # Truncate so each send produces a fresh log.
            with open(_LOG_PATH, "w", encoding="utf-8") as fh:
                fh.write("")
        except Exception:
            _LOG_PATH = None
        return _LOG_PATH

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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
            ToolbagHelpers.log(
                f"[toolbag_helpers] Could not read manifest {manifest_path}: {exc}"
            )
            return {}
        return data.get("materials", {}) or {}

    @staticmethod
    def wire_materials_from_manifest(manifest_path, verbose=True, srgb_colors=True):
        """Wire every texture slot in *manifest_path* onto matching Toolbag mats.

        *srgb_colors* controls whether color maps (albedo/emissive) are
        flagged sRGB. True is correct for RENDERING (lookdev/import: colors
        wash out otherwise). Pass False for a surface-TRANSFER bake: the bake
        writes the sampled values straight back to an 8-bit file, so loading
        the source linear makes the round trip an identity copy -- flagged
        sRGB, the output comes back linearized (visibly darkened).

        Returns the number of slots successfully wired. Best-effort: per-slot
        failures are logged when *verbose* but never raised, so one bad field
        doesn't abort the whole pass.
        """
        if mset is None:
            if verbose:
                ToolbagHelpers.log(
                    "[toolbag_helpers] mset not available; cannot wire materials."
                )
            return 0

        mat_map = ToolbagHelpers.load_manifest(manifest_path)
        if not mat_map:
            if verbose:
                ToolbagHelpers.log(
                    f"[toolbag_helpers] Manifest empty or missing at: {manifest_path}"
                )
                ToolbagHelpers.log(
                    "[toolbag_helpers] Nothing to wire -- check Maya-side MatManifest.build()."
                )
            return 0

        # Toolbag 5 API: getAllMaterials() is the documented entry point. The
        # earlier ``getAllObjects() + isinstance(mset.MaterialObject)`` filter
        # raised AttributeError -- ``MaterialObject`` does not exist; the class
        # is ``mset.Material`` and there is no need to filter manually.
        scene_mats = list(mset.getAllMaterials())
        if verbose:
            ToolbagHelpers.log(
                f"[toolbag_helpers] Scene contains {len(scene_mats)} material(s)."
            )
            ToolbagHelpers.log(
                f"[toolbag_helpers] Scene mat names: {[m.name for m in scene_mats]}"
            )
            ToolbagHelpers.log(
                f"[toolbag_helpers] Manifest mat names: {list(mat_map.keys())}"
            )

        wired = 0
        for mat_name, slots in mat_map.items():
            tb_mat = ToolbagHelpers.find_material(mat_name, scene_mats)
            if tb_mat is None:
                if verbose:
                    ToolbagHelpers.log(
                        f"  SKIP  '{mat_name}' -- no matching Toolbag material."
                    )
                continue
            if verbose:
                ToolbagHelpers.log(f"  Wiring '{mat_name}' -> '{tb_mat.name}'")

            # Pass 1 -- PBR-normalize every needed subroutine BEFORE any
            # texture lands: Toolbag's FBX import defaults materials to the
            # Gloss/Specular variants (or no occlusion/emissive module at
            # all), and wiring a roughness map into a 'Gloss Map' field
            # inverts its meaning. Must be a separate pass: setSubroutine
            # rebuilds the material, and bindings applied before a rebuild
            # come back colorspace-shifted (same failure as re-serializing).
            for slot_key in slots:
                fix = SUBROUTINE_FIXES.get(slot_key)
                if fix is not None:
                    _ToolbagHelpersInternal._ensure_subroutine(
                        tb_mat, fix, verbose=verbose
                    )

            # Pass 2 -- wire the textures.
            for slot_key, tex_path in slots.items():
                mapping = SLOT_MAP.get(slot_key)
                if not mapping:
                    if verbose:
                        ToolbagHelpers.log(
                            f"    ? No Toolbag mapping for slot '{slot_key}', skipping."
                        )
                    continue

                # Pre-flight: if the texture file is missing on disk Toolbag
                # accepts the path silently and the slot appears empty in the
                # UI. Surface this clearly so the user knows it's a *data*
                # problem (e.g. an unresolved cloud-synced path), not a wire bug.
                if (
                    not tex_path
                    or not isinstance(tex_path, str)
                    or not os.path.isfile(tex_path)
                ):
                    if verbose:
                        ToolbagHelpers.log(
                            f"    ! {slot_key}: file not found on disk -> {tex_path}"
                        )
                    continue

                module_attr, candidates, is_srgb = mapping
                is_srgb = bool(is_srgb and srgb_colors)
                sub = getattr(tb_mat, module_attr, None)
                if sub is None:
                    if verbose:
                        ToolbagHelpers.log(
                            f"    ? Material has no '{module_attr}' module."
                        )
                    continue

                # Toolbag's subroutine variants each expose a differently-named
                # field. Discover the actual field name from the live module
                # rather than hardcoding a guess that breaks when a project
                # uses (e.g.) the Gloss subroutine instead of Roughness Map.
                field_name = _ToolbagHelpersInternal._pick_field_name(sub, candidates)
                if field_name is None:
                    if verbose:
                        ToolbagHelpers.log(
                            f"    ! {slot_key}: subroutine '{module_attr}' exposes "
                            f"no fields -- variant may be disabled."
                        )
                    continue

                try:
                    # Bind the colorspace at construction: mutating ``sRGB``
                    # on a texture already bound to the field severs the
                    # binding (verified on Toolbag 5.02 -- the albedo field
                    # read back empty and baked flat). Path-based setField +
                    # post-mutation remains only as a fallback for builds
                    # without the Texture constructor.
                    tex_obj = None
                    try:
                        tex_obj = mset.Texture(tex_path)
                        tex_obj.sRGB = is_srgb
                    except Exception:  # noqa: BLE001
                        tex_obj = None
                    if tex_obj is not None:
                        sub.setField(field_name, tex_obj)
                    else:
                        sub.setField(field_name, tex_path)
                        _ToolbagHelpersInternal._set_texture_srgb(
                            sub, field_name, is_srgb, verbose
                        )
                    # The map is now the authority: reset the module's scalar
                    # multipliers (imports leave Roughness=0.3, Metalness=0.0
                    # -- a wired map otherwise bakes darkened or black).
                    _ToolbagHelpersInternal._neutralize_module_scalars(
                        sub, module_attr, verbose
                    )
                    wired += 1
                    if verbose:
                        ToolbagHelpers.log(
                            f"    + {slot_key} -> '{field_name}' = "
                            f"{os.path.basename(tex_path)}"
                        )
                except Exception as exc:
                    if verbose:
                        ToolbagHelpers.log(f"    ! {slot_key}: {exc}")

        if verbose:
            ToolbagHelpers.log(f"[toolbag_helpers] Wired {wired} texture slot(s).")
        return wired

    @staticmethod
    def split_source_target(
        objects, high_suffix, low_suffix, pre_classified=None, include_children=True
    ):
        """Group *objects* into ``(sources, targets, others)`` by name suffix.

        Each object is classified in priority order:

        1. *pre_classified* (if supplied) -- an explicit
           ``{mesh_short_name: 'source' | 'target'}`` map. Wins over everything.
           The Maya bridge builds this from the Maya parent chain BEFORE
           FBX export, because Toolbag's importer flattens parent transforms
           and we need a way to carry the classification across that wall.
        2. Walking the object's parent chain (self first, then ``.parent``,
           then ``.parent.parent``, ...). First name to match a suffix wins.

        Without *pre_classified* the chain walker handles three tagging styles:

        * every mesh tagged individually -- ``cube_source``, ``cube_target``; or
        * parent group tagged once -- ``engine_source`` containing
          ``engine_block``, ``engine_pipes``, ...; or
        * mix of the two -- a child's own suffix always wins over an ancestor.

        *include_children* (default True) is the group-root style above: pass
        False to require every mesh to carry the suffix in its own name.

        (Note: parent-group tagging only survives the round trip through
        Toolbag's FBX importer when *pre_classified* is provided. Toolbag
        flattens empty parent transforms on import regardless of FBX
        contents.)

        Resolution rules (after chain classification):

        +-----------+----------+-----------------------------------------------+
        | SRC sfx   | TGT sfx  | Behaviour                                     |
        +===========+==========+===============================================+
        | set       | set      | both matched explicitly; non-matches go       |
        |           |          | to *others* and stay unpaired.                |
        +-----------+----------+-----------------------------------------------+
        | set       | empty    | matching meshes -> sources; everything else   |
        |           |          | -> targets (common workflow: only suffix the  |
        |           |          | bake source).                                 |
        +-----------+----------+-----------------------------------------------+
        | empty     | set      | matching meshes -> targets; everything else   |
        |           |          | -> sources.                                   |
        +-----------+----------+-----------------------------------------------+
        | empty     | empty    | nothing can be inferred; all -> others.       |
        +-----------+----------+-----------------------------------------------+

        A mesh whose own name ends in BOTH suffixes (rare:
        ``cube_target_source``) goes to sources; the source suffix is checked
        first at each level.

        FBX importers sometimes append a ``.001`` duplicate-suffix; we strip
        it before the suffix check so ``cube_source.001`` and a parent group
        named ``engine_source.001`` still resolve as bake sources.
        """
        source_set = bool(high_suffix)
        target_set = bool(low_suffix)
        pre_classified = pre_classified or {}

        sources, targets, others = [], [], []
        for o in objects:
            # 1. Pre-classified hint wins over everything else. The sidecar
            # is keyed by the Maya short name, so strip the '.001'-style
            # duplicate suffix FBX importers append before looking it up
            # (the chain walker below already strips it for suffix checks).
            name = getattr(o, "name", "") or ""
            stem = name.rsplit(".", 1)[0] if "." in name else name
            pre = pre_classified.get(name) or pre_classified.get(stem)
            if pre == "source":
                sources.append(o)
                continue
            if pre == "target":
                targets.append(o)
                continue

            # 2. Walk this object's own parent chain.
            match, _node = _ToolbagHelpersInternal._classify_by_chain(
                o, high_suffix, low_suffix, include_children
            )

            if match == "source":
                sources.append(o)
            elif match == "target":
                targets.append(o)
            elif source_set and not target_set:
                targets.append(o)  # rest-is-target (source-suffix workflow)
            elif target_set and not source_set:
                sources.append(o)  # rest-is-source (target-suffix workflow)
            else:
                others.append(o)
        return sources, targets, others

    #: Back-compat alias -- shipped one release under the high/low name.
    split_high_low = split_source_target

    @staticmethod
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
                out.extend(ToolbagHelpers.collect_mesh_objects(c))
            return out

        # Last resort: caller may have handed us a Python list/iterable of
        # objects (mixed). Filter to MeshObject in that case.
        try:
            return [o for o in root if isinstance(o, mset.MeshObject)]
        except TypeError:
            return []

    @staticmethod
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
                ToolbagHelpers.log(
                    "[toolbag_helpers] No SkyBoxObject in scene; skipping sky preset."
                )
                return False
            skies[0].loadSky(preset_path)
            return True
        except Exception as exc:
            ToolbagHelpers.log(f"[toolbag_helpers] Sky preset load failed: {exc}")
            return False

    @staticmethod
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


# Maya shader slot -> (Toolbag subroutine attr, [candidate field names], sRGB?).
#
# Toolbag's subroutines (albedo, microsurface, reflectivity, etc.) have
# multiple variants, and each variant's field is named after itself. The
# microsurface module, for example, exposes "Gloss Map" when set to the
# Gloss subroutine and "Roughness Map" when set to the Roughness Map
# subroutine -- there is no universal "Microsurface Map" field.
#
# We try each candidate against ``subroutine.getFieldNames()`` and use the
# first match, falling back to the first listed name if discovery fails.
#
# The trailing bool is the texture colour-space: colour maps (albedo,
# emissive) must load sRGB or they wash out; data maps stay Linear. Toolbag's
# ``setField`` loads every texture Linear, so we re-tag per slot after wiring.
SLOT_MAP = {
    "baseColor": ("albedo", ["Albedo Map"], True),
    "normal": ("surface", ["Normal Map"], False),
    "roughness": (
        "microsurface",
        ["Roughness Map", "Gloss Map", "Microsurface Map"],
        False,
    ),
    "metallic": ("reflectivity", ["Metalness Map"], False),
    "ambientOcclusion": ("occlusion", ["Occlusion Map"], False),
    # NOTE: the Material attribute is ``emission`` (the subroutine SLOT is
    # named 'emissive'); the old 'emissive' attr never resolved.
    "emission": ("emission", ["Emissive Map"], True),
    "opacity": ("transparency", ["Transparency Map"], False),
}

# Module scalar multipliers that scale a bound map, with their identity
# values. Applied after a texture is wired into the module (guarded by the
# live module's getFieldNames, so absent fields and unknown variants are
# skipped). Field names verified on Toolbag 5.02: an FBX import leaves
# 'Roughness'=0.3 and 'Metalness'=0.0, silently scaling any map wired later.
# 'Invert*' flags are cleared too -- manifests always carry roughness-
# convention maps (smoothness sources are inverted during unpacking).
MODULE_NEUTRAL_FIELDS = {
    "albedo": {"Color": [1.0, 1.0, 1.0]},
    "microsurface": {"Roughness": 1.0, "Gloss": 1.0, "Invert;roughness": False},
    "reflectivity": {"Metalness": 1.0, "Invert": False},
    "occlusion": {"Occlusion": 1.0},
    "emission": {"Color": [1.0, 1.0, 1.0], "Intensity": 1.0},
}

# Manifest slot -> (material attr, setSubroutine slot, shader variant, field
# that variant exposes). Applied before wiring when the wanted field is
# absent: Toolbag's FBX importer defaults materials to Gloss/Specular (and
# creates no occlusion/emissive module), so without the switch a roughness
# map lands in a 'Gloss Map' field -- same data, inverted meaning.
SUBROUTINE_FIXES = {
    "roughness": ("microsurface", "microsurface", "Roughness", "Roughness Map"),
    "metallic": ("reflectivity", "reflectivity", "Metalness", "Metalness Map"),
    "ambientOcclusion": ("occlusion", "occlusion", "Occlusion", "Occlusion Map"),
    "emission": ("emission", "emissive", "Emissive", "Emissive Map"),
}
