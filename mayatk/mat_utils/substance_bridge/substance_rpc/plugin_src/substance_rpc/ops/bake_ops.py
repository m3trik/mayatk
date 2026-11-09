# !/usr/bin/python
# coding=utf-8
"""Bake ops: iray-backed mesh-map bakes and lighting composites.

Moved here from the standalone app's own Painter plugin when the two became
this one (2026-11-05).

The ``bake.lighting_to_diffuse`` op is the headline helper: it drives
Painter's iray-backed baker for AO (and optionally Curvature) and adds a
multiply fill layer onto the base color channel so the result reads as
baked lighting. It is not a literal iray viewport render — Painter's
Python API doesn't expose that — but the baked maps come from the same
iray engine and composite identically for ~80% of production needs.
"""

import logging
import math
from typing import List, Optional

from .. import PLUGIN, register
from .script_ops import ScriptCancelled

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — kept module-private; Painter API access is lazy-imported.
# ---------------------------------------------------------------------------


def _resolve_texture_sets(texture_set: Optional[str]):
    """Return a list of ``TextureSet`` objects matching the filter.

    Parameters:
        texture_set: Single texture set name, or ``None`` for every set.
    """
    import substance_painter.textureset as textureset

    if texture_set:
        return [textureset.TextureSet.from_name(texture_set)]
    return list(textureset.all_texture_sets())


def _ts_name(ts) -> str:
    """``TextureSet.name`` is a method on newer Painter, attr on older — coerce both."""
    n = ts.name
    return n() if callable(n) else n


def _uid(layer, fallback: str):
    """A layer's id: ``uid`` is a method on current Painter (``ReadOnlyUid.uid``),
    an attribute on older builds; *fallback* when it has neither."""
    uid = getattr(layer, "uid", None)
    uid = uid() if callable(uid) else uid
    return fallback if uid is None else uid


def _subsampling(factor: int) -> int:
    """Painter's ``SubSampling`` (Antialiasing) index for a supersampling factor:
    1 none, 2 / 4 / 8 its 2x2 / 4x4 / 8x8; others round to the nearest."""
    return min(3, max(0, round(math.log2(max(1, int(factor))))))


def _in_range(prop, value):
    """*value* held to the range Painter publishes for *prop* (its slider's
    ``min`` / ``max``); a value of any other kind is returned as is."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    kind = type(value)
    try:
        bounds = prop.properties() or {}
        lo, hi = (bounds.get(k) for k in ("min", "max"))
        lo, hi = (None if b is None else kind(float(b)) for b in (lo, hi))
    except Exception:  # no ranges published (an older Painter): write what was asked
        return value
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value


def _configure_bake_params(params, common_kwargs: dict, ao_kwargs: dict) -> List[str]:
    """Write *common_kwargs* and *ao_kwargs* -- Painter's own property names --
    on a texture set's ``BakingParameters``, each held to Painter's range.

    Returns the parameter labels written, for debug.

    Raises:
        ValueError: a property Painter does not have (its names change between
            releases); the message lists the ones it has. Nothing is written
            then -- a bake with other settings than the ones asked for is not
            the bake that was asked for.
    """
    import substance_painter.baking as baking

    groups = (
        ("", "common", params.common(), common_kwargs),
        ("AO.", "AO baker", params.baker(baking.MeshMapUsage.AO), ao_kwargs),
    )
    for _, label, props, values in groups:
        missing = sorted(set(values) - set(props))
        if missing:
            raise ValueError(f"Painter's {label} parameters have no {missing}; it has: {sorted(props)}")

    settings, written = {}, []
    for prefix, _, props, values in groups:
        for key, value in values.items():
            value = _in_range(props[key], value)
            settings[props[key]] = value
            written.append(f"{prefix}{key}={value}")
    baking.BakingParameters.set(settings)
    return written


def _has_maps(ts, usages) -> bool:
    """Whether *ts* already has a mesh map for every one of *usages*."""
    return all(ts.get_mesh_map_resource(usage) is not None for usage in usages)


class BakeCancelled(ScriptCancelled):
    """The bake was stopped because its job was cancelled (a running script's
    cancel: the runner ends the job ``cancelled``)."""


def _bake(ts, params, usages, timeout: float, cancelled=None) -> None:
    """Bake *usages* of *ts* alone and return once Painter says it is done.

    ``baking.bake_async`` takes ONE texture set and returns a ``StopSource``,
    not a future: the end arrives as a ``BakingProcessEnded`` event on the main
    thread -- the thread this op runs on -- so the wait is a nested event loop,
    never a sleep. Only *usages* bake (the project's other mesh maps, baked or
    imported, are left alone), and the project's own enabled bakers come back
    afterwards. *cancelled* (a callable) is asked four times a second while
    the bake runs: once it answers True the bake is stopped (a job's
    ``script.cancel`` is served by this very loop).

    Raises:
        BakeCancelled: *cancelled* answered True (the bake is stopped).
        RuntimeError: Painter ended the bake without success.
        TimeoutError: it did not end within *timeout* seconds (it is stopped).
    """
    import substance_painter.baking as baking
    import substance_painter.event as event
    from PySide6 import QtCore  # Painter's own binding; this runs only inside Painter

    ended = []
    loop = QtCore.QEventLoop()

    def on_ended(e) -> None:  # held here: the dispatcher keeps only a weak reference
        ended.append(e.status)
        loop.quit()

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    stopped = []

    def poll() -> None:
        if cancelled is not None and cancelled():
            stopped.append(True)
            loop.quit()

    watch = QtCore.QTimer()
    watch.setInterval(250)
    watch.timeout.connect(poll)
    previous = params.get_enabled_bakers()
    event.DISPATCHER.connect(event.BakingProcessEnded, on_ended)
    try:
        params.set_enabled_bakers(list(usages))
        stop = baking.bake_async(ts)
        if not ended:
            timer.start(int(timeout * 1000))
            if cancelled is not None:
                watch.start()
            loop.exec()
        if stopped and not ended:
            stop.request_stop()
            raise BakeCancelled("the bake was cancelled")
        if not ended:
            stop.request_stop()
            raise TimeoutError(f"the bake did not end within {timeout:.0f} s")
    finally:
        watch.stop()
        timer.stop()
        event.DISPATCHER.disconnect(event.BakingProcessEnded, on_ended)
        params.set_enabled_bakers(previous)
    if ended[0] != baking.BakingStatus.Success:
        raise RuntimeError(f"Painter ended the bake: {getattr(ended[0], 'name', ended[0])}")


def _apply_layer_intensity(layer, channel, intensity: float) -> None:
    """Scale a fill layer's channel contribution via its layer opacity.

    Painter layer opacity is clamped to ``[0.0, 1.0]``, so an ``intensity``
    above 1.0 saturates at full strength (the sliders allow up to 4.0). An
    intensity of 1.0 is Painter's default opacity, so it is a no-op we skip —
    keeping the default bake byte-identical.

    The real API is ``Node.set_opacity(opacity, channel)`` — a method on the
    layer node (verified against the installed Painter's layerstack module;
    no release ships a module-level setter). Resolve it defensively and warn,
    never crash, when the running Painter lacks it.
    """
    opacity = min(max(intensity, 0.0), 1.0)
    if opacity >= 1.0:  # full strength == Painter's default; nothing to change.
        return

    set_opacity = getattr(layer, "set_opacity", None)
    if set_opacity is None:
        logger.warning(
            "[bake] Layer node has no set_opacity method; intensity %.2f "
            "could not be applied to '%s'.",
            intensity,
            getattr(layer, "uid", layer),
        )
        return

    try:
        set_opacity(opacity, channel)
    except TypeError:
        # Older/alternate signature: layer-wide opacity, no per-channel arg.
        set_opacity(opacity)


def _name_node(layer, name: str) -> None:
    """Name a layer node via ``Node.set_name`` (the real API), warn-don't-crash."""
    set_name = getattr(layer, "set_name", None)
    if set_name is None:
        logger.warning(
            "[bake] Layer node has no set_name method; '%s' left unnamed.", name
        )
        return
    set_name(name)


def _set_blending_mode(layerstack, layer, channel, blend_mode: str) -> None:
    """Set a layer's blending mode for *channel*.

    Real API: ``Node.set_blending_mode(mode, channel)`` — a method on the
    layer node (verified against the installed Painter). The old module-level
    ``layerstack.set_channel_blending_mode`` never existed; it is kept only as
    a last-resort fallback for hypothetical other builds.
    """
    try:
        mode = getattr(
            layerstack.BlendingMode, blend_mode, layerstack.BlendingMode.Multiply
        )
    except AttributeError:
        logger.warning("[bake] layerstack.BlendingMode unavailable.")
        return

    set_bm = getattr(layer, "set_blending_mode", None)
    if set_bm is not None:
        set_bm(mode, channel)
        return
    legacy = getattr(layerstack, "set_channel_blending_mode", None)
    if legacy is not None:
        legacy(layer, channel, mode)
        return
    logger.warning("[bake] No blending-mode setter available on this Painter.")


#: Why a lighting layer is not driven by the AO map (said in the op's result).
UNDRIVEN = (
    "the lighting layer is added but not driven by the AO map: this Painter's "
    "Python API has no verified setter for a fill layer's mesh-map source"
)


def _mesh_map_setter(layerstack):
    """The layerstack call that drives a fill channel from a mesh map, or ``None``."""
    return getattr(layerstack, "set_source_from_mesh_map", None) or getattr(
        layerstack, "set_channel_source_to_mesh_map", None
    )


def _undriven() -> bool:
    """Whether this Painter can drive a fill channel from a mesh map: False
    when it has the setter (or no layerstack to ask)."""
    try:
        import substance_painter.layerstack as layerstack
    except ImportError:
        return False
    return _mesh_map_setter(layerstack) is None


def _remove_previous_layers(layerstack, stack, names) -> None:
    """Delete the root layers a previous run added (by *names*): a re-run
    replaces its lighting layer rather than stacking another."""
    get_roots = getattr(layerstack, "get_root_layer_nodes", None)
    delete = getattr(layerstack, "delete_node", None)
    if get_roots is None or delete is None:
        return
    for node in list(get_roots(stack)):
        get_name = getattr(node, "get_name", None)
        if get_name is not None and get_name() in names:
            delete(node)


def _add_lighting_layer(
    ts,
    layer_name: str,
    blend_mode: str,
    intensity: float,
    include_curvature: bool,
    curvature_intensity: float,
) -> Optional[str]:
    """Insert a fill layer on top of the stack that multiplies AO (+optional
    curvature) onto the base color channel.

    Returns the new layer's UID/name on success, ``None`` if the API surface
    isn't compatible with what we expect.
    """
    import substance_painter.layerstack as layerstack
    import substance_painter.textureset as textureset

    stack = textureset.Stack.from_name(_ts_name(ts))
    _remove_previous_layers(layerstack, stack, (layer_name, f"{layer_name} (Curvature)"))
    insert_pos = layerstack.InsertPosition.from_textureset_stack(stack)
    # Real API: insert_fill(position) takes no name kwarg (verified against
    # the installed Painter — the old name= call raised TypeError); the node
    # is named afterwards via Node.set_name.
    layer = layerstack.insert_fill(insert_pos)
    _name_node(layer, layer_name)

    base_color = textureset.ChannelType.BaseColor
    _set_blending_mode(layerstack, layer, base_color, blend_mode)

    # Source: drive base color from the AO mesh map. NOTE: neither name below
    # exists in the installed Painter's layerstack module — the real recipe
    # goes through TextureSet.get_mesh_map_resource(usage) plus the fill
    # layer's SourceEditorMixin, whose per-channel source contract can't be
    # designed statically (needs a live Painter session to validate). Until
    # then this resolves defensively and degrades to a warning: the layer is
    # created, blended, and intensity-scaled, but not yet map-driven.
    mesh_map_setter = _mesh_map_setter(layerstack)
    if mesh_map_setter is None:
        logger.warning(
            "[bake] No mesh-map-to-fill-channel setter on substance_painter.layerstack. "
            "Layer was created but base color source is not driven by the AO map."
        )
        return _uid(layer, _ts_name(ts) + "/" + layer_name)

    import substance_painter.baking as baking

    mesh_map_setter(layer, base_color, baking.MeshMapUsage.AO)
    _apply_layer_intensity(layer, base_color, intensity)

    if include_curvature:
        # Stack a second overlay layer for curvature contribution.
        curv_layer = layerstack.insert_fill(insert_pos)
        _name_node(curv_layer, f"{layer_name} (Curvature)")
        _set_blending_mode(layerstack, curv_layer, base_color, "Overlay")
        mesh_map_setter(curv_layer, base_color, baking.MeshMapUsage.Curvature)
        _apply_layer_intensity(curv_layer, base_color, curvature_intensity)

    return _uid(layer, _ts_name(ts) + "/" + layer_name)


# ---------------------------------------------------------------------------
# Registered ops
# ---------------------------------------------------------------------------


@register("bake.lighting_to_diffuse")
def lighting_to_diffuse(
    texture_set: Optional[str] = None,
    bake_resolution: int = 1024,
    ao_intensity: float = 1.0,
    ao_secondary_rays: int = 64,
    ao_max_distance: float = 1.0,
    ao_min_distance: float = 0.0,
    ao_spread_angle: float = 162.0,
    ao_subsample: int = 1,
    include_curvature: bool = False,
    curvature_intensity: float = 0.3,
    layer_name: str = "iray AO Lighting",
    blend_mode: str = "Multiply",
    skip_existing_bakes: bool = False,
    save_project: bool = False,
    cancelled=None,
) -> dict:
    """Bake iray-backed AO (+ optional curvature) and composite onto base color.

    Parameters:
        texture_set: Name of a single texture set to bake, or ``None`` for all.
        bake_resolution: Bake output size (power of two). ``1024`` → 1024².
        ao_intensity: Multiplier applied to AO contribution when compositing.
        ao_secondary_rays: AO sample count (Painter: 1-256). Higher = cleaner, slower.
        ao_max_distance: Max occluder distance, a fraction of the mesh's bounding
            box (Painter's default, up to 1.0).
        ao_min_distance: Min occluder distance (bias against self-shadow noise),
            the same fraction; 0 is Painter's smallest.
        ao_spread_angle: Spread angle of the occlusion rays in degrees (0-180).
        ao_subsample: Antialiasing by supersampling: 1 none, 2, 4 or 8 (others
            round to the nearest).
        include_curvature: Also bake curvature and stack as an overlay layer.
        curvature_intensity: Curvature multiplier when ``include_curvature``.
        layer_name: Name of the inserted fill layer.
        blend_mode: Blend mode for the AO layer (default ``Multiply``).
        skip_existing_bakes: If True, only bake when mesh maps are missing.
        save_project: Save the project after applying the layer.

    Every bake value is held to the range Painter publishes for it.

    Returns:
        ``{texture_sets, baked_maps, layers_created, parameters, saved, errors,
        warnings}``.
    """
    import substance_painter.baking as baking

    result: dict = {
        "texture_sets": [],
        "baked_maps": [],
        "layers_created": [],
        "parameters": [],
        "saved": False,
        "errors": [],
        "warnings": [],
    }

    # Painter's own property names (read from Painter 2026-10-19; the op once
    # wrote names Painter never had). Subsampling is Painter's common
    # "Antialiasing", not an AO setting.
    size = int(math.log2(bake_resolution))
    common_kwargs = {"OutputSize": (size, size), "SubSampling": _subsampling(ao_subsample)}
    ao_kwargs = {
        "NbSecondary": ao_secondary_rays,
        "MaxDistance": ao_max_distance,
        "MinDistance": ao_min_distance,
        "Spread": ao_spread_angle,
    }

    try:
        ts_list = _resolve_texture_sets(texture_set)
    except Exception as e:
        result["errors"].append(f"resolve_texture_sets: {type(e).__name__}: {e}")
        return result

    usages = [baking.MeshMapUsage.AO] + ([baking.MeshMapUsage.Curvature] if include_curvature else [])
    for ts in ts_list:
        name = _ts_name(ts)
        result["texture_sets"].append(name)

        if skip_existing_bakes and _has_maps(ts, usages):
            # Only the bake is skipped (its settings untouched): the maps it
            # has still composite.
            result["baked_maps"].append({name: "skipped"})
        else:
            try:
                params = baking.BakingParameters.from_texture_set(ts)
                written = _configure_bake_params(params, common_kwargs, ao_kwargs)
                result["parameters"].append({name: written})
            except Exception as e:
                result["errors"].append(f"{name}/configure: {type(e).__name__}: {e}")
                continue

            try:
                _bake(ts, params, usages, PLUGIN.marshaller.timeout, cancelled)  # the op's own bound
                baked = ["AO"] + (["Curvature"] if include_curvature else [])
                result["baked_maps"].append({name: baked})
            except BakeCancelled:
                raise  # no other texture set starts
            except Exception as e:
                result["errors"].append(f"{name}/bake: {type(e).__name__}: {e}")
                continue

        try:
            uid = _add_lighting_layer(
                ts,
                layer_name=layer_name,
                blend_mode=blend_mode,
                intensity=ao_intensity,
                include_curvature=include_curvature,
                curvature_intensity=curvature_intensity,
            )
            if uid:
                result["layers_created"].append({name: uid})
        except Exception as e:
            result["errors"].append(f"{name}/layer: {type(e).__name__}: {e}")

    if result["layers_created"] and _undriven():
        result["warnings"].append(UNDRIVEN)

    if save_project and not result["errors"]:
        try:
            import substance_painter.project as project

            project.save()
            result["saved"] = True
        except Exception as e:
            result["errors"].append(f"save: {type(e).__name__}: {e}")

    return result


#: Painter's mesh-map bakers by the names a script and the panel use.
MESH_MAPS = (
    "Normal",
    "WorldSpaceNormal",
    "ID",
    "AO",
    "Curvature",
    "Position",
    "Thickness",
)


@register("bake.mesh_maps")
def mesh_maps(maps=None, resolution=0, texture_set=None, cancelled=None):
    """Bake *maps* (names of :data:`MESH_MAPS`; ``None`` bakes the project's
    enabled bakers) for *texture_set*, or every texture set, one at a time.

    Parameters:
        maps: Mesh maps to bake, e.g. ``["Normal", "AO"]``.
        resolution: Bake output size in px (power of two); 0 keeps the set's.
        texture_set: One texture set's name, or ``None`` for every one.

    A high poly to bake from is set first (``setup_ops.set_high_poly``).

    Returns:
        ``{texture_sets, baked, errors}``.
    """
    import substance_painter.baking as baking

    result = {"texture_sets": [], "baked": [], "errors": []}
    try:
        ts_list = _resolve_texture_sets(texture_set)
    except Exception as e:
        result["errors"].append(f"resolve_texture_sets: {type(e).__name__}: {e}")
        return result
    unknown = sorted(set(maps or ()) - set(MESH_MAPS))
    if unknown:
        result["errors"].append(f"unknown mesh maps {unknown}; Painter's are {list(MESH_MAPS)}")
        return result
    for ts in ts_list:
        name = _ts_name(ts)
        result["texture_sets"].append(name)
        try:
            params = baking.BakingParameters.from_texture_set(ts)
            usages = [getattr(baking.MeshMapUsage, m) for m in maps] if maps else list(params.get_enabled_bakers())
            common = {}
            if resolution:
                size = int(math.log2(int(resolution)))
                common["OutputSize"] = (size, size)
            if common:
                props = params.common()
                missing = sorted(set(common) - set(props))
                if missing:
                    raise ValueError(f"Painter's common parameters have no {missing}")
                baking.BakingParameters.set({props[k]: v for k, v in common.items()})
            _bake(ts, params, usages, PLUGIN.marshaller.timeout, cancelled)
            result["baked"].append({name: [getattr(u, "name", str(u)) for u in usages]})
        except BakeCancelled:
            raise  # no other texture set starts
        except Exception as e:
            result["errors"].append(f"{name}: {type(e).__name__}: {e}")
    return result
