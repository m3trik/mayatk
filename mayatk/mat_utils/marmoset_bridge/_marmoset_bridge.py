# !/usr/bin/python
# coding=utf-8
"""Maya-side glue for the Marmoset Toolbag engine.

:class:`MarmosetBridge` is the Maya half of the split: a
:class:`pythontk.HandoffBridge` whose ``_produce`` exports the current
selection to FBX, builds a :class:`MatManifest` material sidecar and a
Maya-DAG-classified source/target bake-pairs sidecar, and whose **deliverer** is the
DCC-agnostic :class:`._marmoset_engine.MarmosetEngine` (a
:class:`pythontk.Deliverer`) that renders the Toolbag template and launches /
round-trips Toolbag.

Everything Marmoset-specific but DCC-agnostic (Toolbag discovery/launch,
log handling, template rendering, the in-Toolbag helpers, the RPC client)
is bundled alongside this module in the ``marmoset_bridge`` subpackage:
the Toolbag SDK glue is not a generic pythontk utility, so it lives with
its consumer (mirroring ``substance_bridge``). This module owns only what
genuinely needs Maya. The standalone extapps ``marmoset_workflow`` panel
keeps its own copy of the same engine, since it cannot import mayatk.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk

# DCC-agnostic engine (bundled in this subpackage) + the names the slots
# import from this module.
from mayatk.mat_utils.marmoset_bridge._marmoset_engine import MarmosetEngine, ROUND_TRIP

# Re-exported so the slots/tests can ``from ._marmoset_bridge import SEND_TO, _TEMPLATE_DIR``.
from mayatk.mat_utils.marmoset_bridge._marmoset_engine import (  # noqa: F401
    SEND_TO,
    _TEMPLATE_DIR,
)

# Sibling module, imported relatively (as the engine does): this module is
# what the subpackage ``__init__`` imports, so an absolute import here would
# re-enter that partially-initialized package.
from . import template_params

from mayatk.core_utils.components import Components
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils.bake_sets import BakeSourceSet
from mayatk.mat_utils.mat_manifest import MatManifest

logger = logging.getLogger(__name__)

# FBX options tuned for Marmoset Toolbag.
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportBakeComplexAnimation": False,
}


class _MarmosetBridgeInternal(object):
    """Internal helpers for MarmosetBridge."""

    #: Packed map type -> the :class:`pythontk.MapFactory` unpacker for it,
    #: with the kwargs that turn its components into what Toolbag's material
    #: slots expect (smoothness inverts into roughness on the way out).
    _UNPACKERS: Dict[str, Tuple[str, Dict[str, Any]]] = {
        "ORM": ("unpack_orm_texture", {}),
        "MRAO": ("unpack_mrao_texture", {}),
        "MSAO": (
            "unpack_msao_texture",
            {"invert_smoothness": True, "smoothness_suffix": "_Roughness"},
        ),
        "Metallic_Smoothness": (
            "unpack_metallic_smoothness",
            {"invert_smoothness": True, "smoothness_suffix": "_Roughness"},
        ),
        "Albedo_Transparency": ("unpack_albedo_transparency", {}),
    }

    #: Manifest slot -> map-type names (lowercased) an unpacked component may
    #: resolve to and satisfy that slot.
    _SLOT_ACCEPTS: Dict[str, Tuple[str, ...]] = {
        "baseColor": ("base_color", "basecolor", "albedo", "diffuse"),
        "metallic": ("metallic", "metalness"),
        "roughness": ("roughness",),
        "ambientOcclusion": ("ambient_occlusion", "ao", "mixed_ao"),
        "opacity": ("opacity", "alpha", "transparency"),
    }

    @classmethod
    def _stage_manifest_textures(
        cls, manifest: Dict[str, Any], staging_dir: str, log
    ) -> Dict[str, str]:
        """Retarget packed-map manifest slots at unpacked component files.

        The Maya materials read specific channels out of packed maps (MSAO /
        MetallicSmoothness / ORM); Toolbag's material fields sample whole
        images, so wiring the packed file verbatim feeds the wrong data to
        the surface-transfer bake. Each packed source is split once into
        *staging_dir* (smoothness channels invert into roughness) and every
        slot that referenced it is re-pointed at the matching component.

        Returns ``{material: packed_map_type}`` for every material whose
        slots read from a packed source -- the texture-map template the
        post-bake rewire restores (the baked components repack into the
        same layout the source material shipped with). Failures leave the
        original path in place -- a packed file in the slot still beats an
        empty one.
        """
        unpack_cache: Dict[str, List[str]] = {}
        packing: Dict[str, str] = {}
        retargeted = 0
        for mat_name, slots in (manifest.get("materials") or {}).items():
            for slot, path in list(slots.items()):
                accepts = cls._SLOT_ACCEPTS.get(slot)
                if not accepts or not path:
                    continue
                try:
                    map_type = ptk.MapFactory.resolve_map_type(path)
                except Exception:  # noqa: BLE001
                    continue
                spec = cls._UNPACKERS.get(map_type)
                if spec is None:
                    continue
                packing.setdefault(mat_name, map_type)
                if path not in unpack_cache:
                    unpacker, kwargs = spec
                    try:
                        produced = getattr(ptk.MapFactory, unpacker)(
                            path, output_dir=staging_dir, save=True, **kwargs
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            f"Could not unpack {map_type} map "
                            f"{os.path.basename(path)}: {e}"
                        )
                        produced = ()
                    unpack_cache[path] = [
                        str(p) for p in produced or () if p and os.path.isfile(str(p))
                    ]
                for component in unpack_cache[path]:
                    try:
                        ctype = (
                            ptk.MapFactory.resolve_map_type(component) or ""
                        ).lower()
                    except Exception:  # noqa: BLE001
                        continue
                    if ctype in accepts:
                        slots[slot] = component.replace("\\", "/")
                        retargeted += 1
                        break
        if retargeted:
            log.info(
                f"Unpacked packed maps: {retargeted} material slot(s) now "
                f"point at single-channel components."
            )
        return packing

    @staticmethod
    def _classify_maya_chain(
        dag_path: str,
        high_suffix: str,
        low_suffix: str,
        include_children: bool = True,
    ) -> Optional[str]:
        """Walk *dag_path* leaf-to-root in Maya, return ``'source'``/``'target'``/None.

        Mirrors the Toolbag-side ``_classify_by_chain`` in
        :mod:`._toolbag_helpers`, but operates on Maya
        DAG paths via ``cmds.listRelatives`` -- so we can run it BEFORE the FBX
        export flattens the hierarchy. *include_children* off stops the walk at
        the node itself, so a suffixed group no longer tags its descendants.
        """
        cur = dag_path
        visited = 0
        while cur and visited < 64:
            leaf = cur.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
            if high_suffix and stem.endswith(high_suffix):
                return "source"
            if low_suffix and stem.endswith(low_suffix):
                return "target"
            if not include_children:
                break
            parents = cmds.listRelatives(cur, parent=True, fullPath=True) or []
            cur = parents[0] if parents else None
            visited += 1
        return None

    @staticmethod
    def _split_by_pairs(
        objects: Sequence[str], bake_pairs: Dict[str, str]
    ) -> Tuple[List[str], List[str]]:
        """``(sources, targets)`` mesh transforms under *objects*, per *bake_pairs*.

        The single-file flow has no separate source export to classify by, but
        it does have the pairs sidecar -- the same leaf-name classification the
        Toolbag side will use. Reusing it here keeps one answer to "which of
        these is the source", rather than a second opinion that could disagree
        with the bake groups it is measured for.
        """
        sources: List[str] = []
        targets: List[str] = []
        for mesh in Components.get_mesh_transforms(objects):
            leaf = mesh.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            side = bake_pairs.get(leaf)
            if side == "source":
                sources.append(mesh)
            elif side == "target":
                targets.append(mesh)
        return sources, targets


class MarmosetBridge(ptk.HandoffBridge, _MarmosetBridgeInternal):
    """Export the Maya selection to Marmoset Toolbag with templated automation.

    A :class:`pythontk.HandoffBridge` whose ``_produce`` exports the selection to
    FBX with a :class:`MatManifest` sidecar and a bake-pairs sidecar, and whose
    deliverer is the DCC-agnostic :class:`MarmosetEngine` (renders the Toolbag
    template + launches / round-trips). The public ``send()`` is the shared
    skeleton; its app-specific knobs (``output_dir`` / ``output_name`` /
    ``toolbag_exe`` / ``fbx_options`` / ``preset_file``) ride as keyword extras.

    Usage::

        MarmosetBridge().send(template="bake", mode="round_trip")
        MarmosetBridge().send(template="lookdev")  # mode defaults to send_to
    """

    def __init__(self, toolbag_path: Optional[str] = None):
        super().__init__()
        # The Toolbag-side launch/roundtrip Strategy (also usable standalone).
        self.deliverer = MarmosetEngine(toolbag_path)
        # The panel redirects only the bridge's logger (`BridgeSlotsBase`); route
        # the engine's delivery-phase output (Toolbag launch, output links,
        # roundtrip results) through the SAME logger so it reaches the log panel.
        # `LoggingMixin.logger` is a non-data ClassProperty, so this instance
        # attribute shadows it for this engine only (standalone engines keep
        # their own logger).
        self.deliverer.logger = self.logger

    # Back-compat: expose the engine's resolved Toolbag path on the bridge.
    @property
    def toolbag_path(self) -> Optional[str]:
        return self.deliverer.toolbag_path

    @toolbag_path.setter
    def toolbag_path(self, value: Optional[str]) -> None:
        self.deliverer.toolbag_path = value

    def params_defaults(self) -> Dict[str, Any]:
        from mayatk.mat_utils.marmoset_bridge import parameters as _params

        return _params.Parameters.defaults()

    def render_template(self, *args, **kwargs) -> Optional[str]:
        """Render a Toolbag script body (delegates to the engine deliverer)."""
        return self.deliverer.render_template(*args, **kwargs)

    # ------------------------------------------------------------------ hooks
    def _resolve_objects(self, objects):
        """Return the objects to export; ``None`` -> current selection."""
        if not objects:
            objects = cmds.ls(selection=True, long=True)
        return objects or []

    @classmethod
    def source_model_path_for(cls, fbx_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx`` (shared convention)."""
        return BakeSourceSet.companion_path(fbx_path)

    #: Back-compat alias -- shipped one release under the high-poly name.
    high_poly_path_for = source_model_path_for

    def _split_bake_objects(self, objects) -> Tuple[List[str], List[str]]:
        """Split the export scope into (targets, sources) via the scene's Bake Source set.

        The scoped selection is the bake *target*; the scene's
        :class:`BakeSourceSet` members are the bake *source* and ride along
        whether or not they were selected. Any scoped object that is a
        source member (or sits under one) moves to the source side rather
        than exporting twice.
        """
        source_members = BakeSourceSet.members()
        if not source_members:
            return list(objects), []
        source_prefixes = tuple(f"{m}|" for m in source_members)
        targets: List[str] = []
        for obj in objects:
            if obj in source_members or str(obj).startswith(source_prefixes):
                continue
            # A scoped ancestor of a source member would smuggle the source
            # geometry into the target export; surface it rather than
            # silently double-exporting.
            if any(str(m).startswith(f"{obj}|") for m in source_members):
                self.logger.warning(
                    f"'{obj}' contains Bake Source set members; excluding it "
                    f"from the bake-target export. Select the target "
                    f"geometry itself."
                )
                continue
            targets.append(obj)
        return targets, source_members

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX(es) + material manifest (+ sidecars) into ``output_dir``.

        Resolves ``output_dir`` / ``output_name`` (stamping them back into
        ``request.extras`` so the engine deliverer writes its script alongside),
        then returns a :class:`pythontk.Payload` carrying the FBX + sidecar paths.

        For the bake template, the scene's :class:`BakeSourceSet` splits the
        export in two: the scoped selection becomes ``<base>.fbx`` (bake
        target) and the set members a companion ``<base>_source.fbx`` (bake
        source) -- explicit classification that survives identical mesh
        names on both sides. Without the set, the legacy single-file
        suffix-pairing flow applies.
        """
        output_dir = request.get("output_dir")
        if not output_dir:
            # Detached policy: Toolbag reads the artifacts after we return;
            # allocation sweeps stale leftovers instead of deleting live ones.
            output_dir = ptk.TempArtifacts(
                "maya_marmoset_bridge", policy="detached"
            ).dir_path(name="handoff")
        os.makedirs(output_dir, exist_ok=True)
        base = request.get("output_name") or self._scene_base_name()
        # Keep produce + deliver on the same dir/name.
        request.extras["output_dir"] = output_dir
        request.extras["output_name"] = base

        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        pairs_path = os.path.join(output_dir, f"{base}.bake_pairs.json")

        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if request.get("fbx_options"):
            merged_options.update(request.get("fbx_options"))

        # Bake sends split the export scope via the scene's Bake Source set.
        is_bake = request.template == "bake"
        # Fall back to the registry defaults for any key a programmatic caller
        # left out -- one source of truth for what "_source" is, and for
        # whether the cage is auto-sized.
        pairing = {**template_params.DEFAULTS, **request.params}
        source_objects: List[str] = []
        target_objects = list(objects)
        if is_bake:
            target_objects, source_objects = self._split_bake_objects(objects)
            if source_objects and not target_objects:
                self.logger.error(
                    "The export scope resolved to Bake Source set members only "
                    "-- there is no bake target. Select the target "
                    "geometry (the source set rides along automatically)."
                )
                return None

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting
        # so we get a clear FBX-export error instead of "Invalid file type".
        FbxUtils.load_plugin()

        self.logger.info("Exporting FBX ...")
        try:
            FbxUtils.export(
                file_path=fbx_path,
                objects=target_objects,
                preset_file=request.get("preset_file"),
                options=merged_options,
                selection_only=True,
            )
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None
        self.logger.info(
            f'FBX written: <a href="action://open?path={fbx_path}">{fbx_path}</a>'
        )

        source_model_path: Optional[str] = None
        if source_objects:
            source_model_path = self.source_model_path_for(fbx_path)
            self.logger.info(
                f"Exporting bake source ({len(source_objects)} Bake Source set "
                f"object(s)) ..."
            )
            # ``FbxUtils.export`` selects what it exports; restore the user's
            # selection so the companion pass leaves no visible trace.
            restore = cmds.ls(selection=True, long=True) or []
            try:
                FbxUtils.export(
                    file_path=source_model_path,
                    objects=source_objects,
                    options=merged_options,
                    selection_only=True,
                )
            except Exception as e:
                self.logger.error(f"Bake-source FBX export failed: {e}")
                return None
            finally:
                if restore:
                    cmds.select(restore, replace=True)
                else:
                    cmds.select(clear=True)
            self.logger.info(
                f"Bake source written: "
                f'<a href="action://open?path={source_model_path}">{source_model_path}</a>'
            )

        self.logger.info("Building material manifest ...")
        manifest = MatManifest.build(target_objects + source_objects)
        if is_bake:
            # Toolbag samples whole images per material field; packed maps
            # must be split before the surface-transfer bake reads them.
            # Staged OUTSIDE output_dir: the roundtrip collects every image
            # under output_dir as a bake result, and these are inputs.
            staging_dir = ptk.TempArtifacts(
                "maya_marmoset_bridge", policy="detached"
            ).dir_path(name=f"{ptk.StrUtils.sanitize(base, preserve_case=True)}_staging")
            # The recorded packing templates let the post-bake rewire pack
            # the baked components back into the layout each source shipped.
            request.extras["source_packing"] = self._stage_manifest_textures(
                manifest, staging_dir, self.logger
            )
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(
            f"Manifest written: "
            f'<a href="action://open?path={manifest_path}">{manifest_path}</a>'
        )

        # Record which target objects carry which material so the roundtrip
        # can assign each baked texture set back onto the right meshes.
        if is_bake:
            request.extras["bake_assignments"] = self._material_assignments(
                target_objects
            )

        # Bake-pairs sidecar (single-file fallback only): Maya-side
        # parent-chain classification, written while we still have the full
        # DAG (Toolbag's FBX importer flattens empty parent transforms).
        actual_pairs_path: Optional[str] = None
        cage_sources, cage_targets = source_objects, target_objects
        if is_bake and not source_objects:
            bake_pairs = MarmosetBridge.build_bake_pairs_manifest(
                target_objects,
                pairing.get("HIGH_SUFFIX") or "",
                pairing.get("LOW_SUFFIX") or "",
                include_children=bool(pairing.get("SUFFIX_INCLUDE_CHILDREN", True)),
            )
            if bake_pairs:
                with open(pairs_path, "w", encoding="utf-8") as fh:
                    json.dump(bake_pairs, fh, indent=2)
                self.logger.info(
                    f"Bake-pairs sidecar written ({len(bake_pairs)} mesh(es) "
                    f"pre-classified): "
                    f'<a href="action://open?path={pairs_path}">{pairs_path}</a>'
                )
                actual_pairs_path = pairs_path
            # Same classification the Toolbag side will group by, so the cage
            # is measured for the pairs it actually gets applied to.
            cage_sources, cage_targets = self._split_by_pairs(
                target_objects, bake_pairs
            )

        # Measured cage input, passed as template params. Only for AUTO_CAGE:
        # with a hand-typed offset these would go unread, and the measurement
        # walks every source mesh's points.
        if is_bake and pairing.get("AUTO_CAGE"):
            request.params.update(self._cage_measurements(cage_sources, cage_targets))

        return ptk.Payload(
            primary=fbx_path,
            extras={
                "manifest": manifest_path,
                "pairs": actual_pairs_path,
                "source_model": source_model_path,
            },
        )

    def _cage_measurements(
        self, sources: Sequence[str], targets: Sequence[str]
    ) -> Dict[str, Any]:
        """Measure what the auto cage needs, as ``{CAGE_STANDOFFS, CAGE_HOST_DIAGONAL}``.

        The cage has to travel from the bake target out past the source's
        FURTHEST point, and only a closest-point query can say how far that is
        -- a source standing off an INTERIOR target surface (a light fixture
        under a ceiling, a door inset in its opening) sits wholly inside the
        target's bounding box, so every box-derived estimate reads zero for it.
        Maya has the acceleration structure for the real query; Toolbag exposes
        no such call, which is why it happens here and ships with the send.

        The diagonal rides along so the Toolbag side can convert these host-unit
        distances into its own units by comparing the two measurements of the
        same target (see ``_unit_scale`` in ``templates/bake.py``).

        Returns an empty mapping when there is nothing to measure; the template
        falls back to its bounds estimate.
        """
        if not (sources and targets):
            return {}
        try:
            distances = Components.get_standoff_distances(sources, targets)
            bbox = cmds.exactWorldBoundingBox(list(targets))
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Could not measure the bake cage ({e}); Toolbag will estimate "
                f"it from the imported bounds instead."
            )
            return {}
        if not distances:
            return {}

        diagonal = sum((bbox[i + 3] - bbox[i]) ** 2 for i in range(3)) ** 0.5
        # Keyed by leaf short name -- the same key the bake-pairs sidecar uses,
        # and what survives the FBX round-trip into Toolbag's mesh names.
        standoffs = {}
        for mesh, distance in distances.items():
            leaf = mesh.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            standoffs[leaf] = max(distance, standoffs.get(leaf, 0.0))
        furthest = max(standoffs.items(), key=lambda kv: kv[1])
        self.logger.info(
            f"Cage measured over {len(standoffs)} source mesh(es): the furthest "
            f"stands {furthest[1]:.4g} off the bake target ({furthest[0]})."
        )
        return {"CAGE_STANDOFFS": standoffs, "CAGE_HOST_DIAGONAL": diagonal}

    @staticmethod
    def _material_assignments(objects: Sequence[str]) -> Dict[str, List[str]]:
        """``{material: [mesh transforms]}`` over *objects* and their descendants."""
        from mayatk.mat_utils._mat_utils import MatUtils

        out: Dict[str, List[str]] = {}
        visited = set()
        for obj in objects:
            descendants = (
                cmds.listRelatives(
                    obj, allDescendents=True, type="transform", fullPath=True
                )
                or []
            )
            for x in [obj] + descendants:
                if x in visited:
                    continue
                visited.add(x)
                if not cmds.listRelatives(x, shapes=True, type="mesh", fullPath=True):
                    continue
                for mat in MatUtils.get_mats([x], as_strings=True) or []:
                    out.setdefault(str(mat), []).append(x)
        return out

    # ------------------------------------------------------------ deliver
    def _deliver(self, payload, request) -> Optional[Dict[str, Any]]:
        """Run the engine hand-off, then wire bake results back into Maya.

        A bake roundtrip's outputs are production maps; with
        ``ASSIGN_MATERIAL`` on (the default), each baked texture set becomes a
        shader network -- of the type those meshes already wore -- assigned to
        the bake-target meshes that carried the matching source material: the
        full loop the panel promises.
        """
        result = super()._deliver(payload, request)
        if (
            result
            and request.template == "bake"
            and request.mode == ROUND_TRIP
            and result.get("outputs")
            and request.params.get("ASSIGN_MATERIAL", True)
        ):
            try:
                created = self._assign_baked_materials(
                    result["outputs"],
                    request.extras.get("bake_assignments") or {},
                    strip_prefix=request.extras.get("output_name") or "",
                    source_packing=request.extras.get("source_packing") or {},
                    output_dir=result.get("output_dir") or "",
                )
                if created:
                    result["materials"] = created
            except Exception:  # noqa: BLE001 -- the bake itself succeeded
                import traceback

                self.logger.error(
                    "Baked-material assignment failed:\n" + traceback.format_exc()
                )
        return result

    #: Packed map type (as recorded from the source materials) -> the
    #: GameShader.create_network flags that rebuild that layout from the
    #: baked single-channel components. Types without an entry (e.g. MRAO)
    #: fall back to wiring the separates.
    _PACKING_FLAGS: Dict[str, Dict[str, bool]] = {
        "MSAO": {"mask_map": True},
        "ORM": {"orm_map": True},
        "Metallic_Smoothness": {"metallic_smoothness": True},
        "Albedo_Transparency": {"albedo_transparency": True},
    }

    #: Suffix marking a material this bridge built from a bake.
    BAKED_SUFFIX = "_BAKED"

    @classmethod
    def baked_material_name(cls, mat_name: str) -> str:
        """``<source material>_BAKED``, idempotent across re-bakes.

        A re-bake reads its texture-set names off the meshes' CURRENT
        materials, which after the first roundtrip are already ``<mat>_BAKED``
        -- so appending unconditionally grew a new material (and a new set of
        map files) per bake: ``mat_BAKED_BAKED_BAKED``. Every trailing
        ``_BAKED`` is stripped before one is re-applied, so the second bake
        overwrites the first material and its maps instead of stacking beside
        them.
        """
        base = ptk.StrUtils.sanitize(str(mat_name), preserve_case=True)
        while base.endswith(cls.BAKED_SUFFIX):
            base = base[: -len(cls.BAKED_SUFFIX)]
        return f"{base}{cls.BAKED_SUFFIX}"

    @staticmethod
    def _shader_type_of(mat_name: str, default: str = "stingray") -> str:
        """The ``GameShader`` shader type to rebuild *mat_name* as.

        The bake's job is to hand the scene back the way it found it, so a
        target that wore a StingrayPBS gets a StingrayPBS and one that wore a
        standardSurface gets a standardSurface -- rather than every bake
        silently retyping the scene to the bridge's own default. A material
        Maya no longer has (deleted between export and roundtrip) or a type
        ``GameShader`` cannot build falls back to *default*, which is also
        what the game-bound hand-off wants when there is nothing to preserve.

        The node-type -> vocabulary pairing is inverted from
        :attr:`ShaderConverter.TARGETS` (its SSoT) rather than restated here,
        so a shader family added there is understood by this path too.
        """
        from mayatk.mat_utils.shader_converter import ShaderConverter

        by_node_type = {v: k for k, v in ShaderConverter.TARGETS.items()}
        try:
            if cmds.objExists(mat_name):
                return by_node_type.get(cmds.nodeType(mat_name), default)
        except RuntimeError:
            pass
        return default

    def _retire_previous_network(
        self, previous: str, shading_group: str, wanted_name: str
    ) -> str:
        """Delete the earlier bake's *previous* shader; give its name to the rebuild.

        Maya uniquifies the rebuild to ``<name>1`` while the old material still
        holds the name, so without this the scene accumulates one dead
        material per bake -- the meshes get the new one, the old one lingers
        wired to the previous maps. Called only after the rebuild is assigned,
        so the meshes are never briefly material-less.

        Only the shader and its shading engine go; the file nodes are left for
        Maya's own cleanup, so a texture shared with another material is never
        yanked out from under it. A shader that refuses to go (referenced,
        locked) is reported and left alone -- the rebuild then keeps its
        uniquified name, which is the pre-existing behaviour: degraded, not a
        reason to abandon the remaining texture sets.

        Reclaiming the freed name is :meth:`MatUtils.claim_material_name` --
        the same primitive the Blender scene import uses for the same reason,
        and it carries the shading group's name along so the ``<mat>SG``
        pairing doesn't drift to ``<mat>1SG``.

        Returns the rebuilt shader's name, renamed when the retirement freed it.
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        rebuilt = self._surface_shader(shading_group)
        if not cmds.objExists(previous):
            return rebuilt
        doomed = [previous]
        for sg in cmds.listConnections(previous, type="shadingEngine") or []:
            if sg not in doomed:
                doomed.append(sg)
        try:
            cmds.delete(doomed)
        except RuntimeError as e:
            self.logger.warning(
                f"Could not remove the previous '{previous}' ({e}); the "
                f"rebuild stays under its uniquified name '{rebuilt}'."
            )
            return rebuilt

        self.logger.info(
            f"Replaced the previous bake's '{previous}' "
            f"(re-bakes overwrite rather than accumulate)."
        )
        MatUtils.claim_material_name(shading_group, wanted_name)
        return self._surface_shader(shading_group)

    @staticmethod
    def _surface_shader(shading_group: str) -> str:
        """The surface shader behind *shading_group*, or the group itself."""
        shaders = (
            cmds.listConnections(
                f"{shading_group}.surfaceShader", source=True, destination=False
            )
            or []
        )
        return str(shaders[0]) if shaders else str(shading_group)

    def _assign_baked_materials(
        self,
        outputs: List[str],
        assignments: Dict[str, List[str]],
        strip_prefix: str = "",
        source_packing: Optional[Dict[str, str]] = None,
        output_dir: str = "",
    ) -> Dict[str, str]:
        """Create + assign one shader network per baked texture set.

        Each set is rebuilt as the shader type its meshes were already wearing
        (:meth:`_shader_type_of`) under a name that survives re-baking
        (:meth:`baked_material_name`), replacing the previous bake's material
        rather than stacking a new one beside it -- the scene comes back the
        way the bake found it, just with the baked maps in the slots.

        *outputs* are the map files the roundtrip generated; *assignments*
        maps each source material to the bake-target meshes that wore it
        (captured at export time). Toolbag names per-material outputs after
        the texture set (= the material), so files bucket to materials by
        name; a bake with a single texture set assigns everything to every
        recorded target. *strip_prefix* is the output stem (the scene/base
        name) removed from filenames before matching, so a scene name that
        happens to contain a material name can't swallow every file.

        *source_packing* (``{material: packed_map_type}``, recorded while
        unpacking the sources for the transfer bake) restores each source's
        texture-map template on the way back in: the baked components repack
        into the same layout (MSAO / ORM / MetallicSmoothness / ...) before
        wiring. A bucket with no recorded source (the new-material single-set
        case) inherits the dominant packing across the sources.

        *output_dir* is the run's production folder; a map that did not land
        there is a scratch-copy fallback (an unverifiable copy keeps its
        original -- see ``MarmosetEngine._relocate_outputs``) and is flagged
        before it is wired, because that scratch store is age-swept on a later
        bake and the material would lose the texture with no further warning.

        Returns ``{source_material: created_shader}``.
        """
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.game_shader import GameShader

        # Maya's stored texture paths are forward-slashed everywhere else in
        # this package (manifest build, sourceimages copy, remap); these come
        # straight off ``os.path.join`` on Windows, so normalize before they
        # reach a ``fileTextureName`` and start mismatching every path compare
        # that follows.
        outputs = [str(p).replace("\\", "/") for p in outputs]

        buckets = self._group_baked_outputs(
            outputs, list(assignments), strip_prefix=strip_prefix
        )
        if not buckets:
            self.logger.warning(
                "No baked maps could be matched to source materials; "
                "skipping material assignment."
            )
            return {}

        # Dominant packing = the fallback template for buckets whose material
        # has no recorded source of its own.
        source_packing = source_packing or {}
        default_packing: Optional[str] = None
        if source_packing:
            counts: Dict[str, int] = {}
            for ptype in source_packing.values():
                counts[ptype] = counts.get(ptype, 0) + 1
            default_packing = max(counts, key=counts.get)

        production_root = (
            os.path.normcase(os.path.abspath(output_dir)) if output_dir else ""
        )

        shader_builder = GameShader()
        shader_builder.logger.setLevel("WARNING")  # keep the panel log tight
        created: Dict[str, str] = {}
        for mat_name, textures in buckets.items():
            targets = assignments.get(mat_name) or []
            targets = [t for t in targets if cmds.objExists(t)]
            if not targets:
                self.logger.warning(
                    f"Baked set '{mat_name}': no surviving target meshes; "
                    f"maps left on disk unassigned."
                )
                continue
            if production_root:
                stranded = [
                    t
                    for t in textures
                    if not os.path.normcase(os.path.abspath(t)).startswith(
                        production_root + os.sep
                    )
                ]
                if stranded:
                    self.logger.warning(
                        f"Baked set '{mat_name}': {len(stranded)} map(s) are "
                        f"being wired from the bake scratch folder, not "
                        f"'{output_dir}' -- their copy could not be verified. "
                        f"That folder is swept on a later bake; copy them into "
                        f"the project and re-point the file nodes:\n  "
                        + "\n  ".join(stranded)
                    )
            shader_name = self.baked_material_name(mat_name)
            # A re-bake's material is named off the PREVIOUS bake's output
            # (``mat_BAKED``), so the packing recorded against the original
            # source (``mat``) is found under the canonical name too.
            pack_type = source_packing.get(
                mat_name,
                source_packing.get(
                    shader_name[: -len(self.BAKED_SUFFIX)], default_packing
                ),
            )
            pack_flags = self._PACKING_FLAGS.get(pack_type or "", {})
            if pack_flags:
                self.logger.info(
                    f"Restoring source texture template '{pack_type}' for "
                    f"'{shader_name}' (baked components repack on wire)."
                )
            # Rebuild as whatever the meshes were already wearing, so a
            # Stingray scene comes back Stingray.
            shader_type = self._shader_type_of(mat_name)
            # The earlier bake's material, retired only once its replacement
            # is built AND assigned. Clearing it first would leave the target
            # meshes with no shading group at all if the rebuild then failed
            # -- worse than the stale material it was removing. Matched as a
            # MATERIAL, not merely by name: a transform that happens to be
            # called 'mat_BAKED' is not a previous bake, and deleting it
            # because it shares the name would be destructive.
            previous = next(iter(cmds.ls(shader_name, materials=True) or []), None)
            self.logger.info(
                f"Building {shader_type} '{shader_name}' from "
                f"{len(textures)} baked map(s) ..."
            )
            node = shader_builder.create_network(
                textures,
                name=shader_name,
                shader_type=shader_type,
                normal_type="OpenGL",
                ambient_occlusion=True,
                **pack_flags,
            )
            # Batch-shaped returns (a list) collapse to their first entry --
            # a named create_network builds exactly one network.
            if isinstance(node, (list, tuple)):
                node = node[0] if node else None
            if node is None:
                self.logger.error(f"Shader network failed for '{mat_name}'.")
                continue
            # ``create_network`` returns the shading ENGINE when one exists
            # (so Hypershade selection lands on the group); normalize to the
            # surface shader + assign via the set either way.
            node_s = str(node)
            shading_group = ""
            if cmds.nodeType(node_s) == "shadingEngine":
                shading_group = node_s
                cmds.sets(targets, edit=True, forceElement=node_s)
                node_s = self._surface_shader(node_s)
            else:
                MatUtils.assign_mat(targets, node_s)
            # The meshes now wear the new network, so the earlier bake's is
            # safe to retire -- and its name is free for the rebuild to claim,
            # which is what keeps a re-bake at 'mat_BAKED' instead of walking
            # 'mat_BAKED1', 'mat_BAKED2', ... with a dead material each time.
            # Reclaiming works off the shading GROUP (that is what carries the
            # <mat>/<mat>SG pair), so it only applies on that branch.
            if previous and shading_group and previous != node_s:
                node_s = self._retire_previous_network(
                    previous, shading_group, shader_name
                )
            created[mat_name] = node_s
            self.logger.info(
                f"Assigned '{node_s}' to {len(targets)} mesh(es) "
                f"(was '{mat_name}')."
            )
        return created

    @staticmethod
    def _group_baked_outputs(
        outputs: List[str], materials: List[str], strip_prefix: str = ""
    ) -> Dict[str, List[str]]:
        """Bucket baked map files to source materials by texture-set naming.

        Longest material name wins so ``mat`` never swallows ``mat_metal``'s
        files. With a single recorded material every output goes to it (the
        single-texture-set bake carries no set token in its filenames).
        *strip_prefix* (the output stem) is removed from each filename before
        matching so a scene name containing a material name stays inert.
        """
        if not outputs:
            return {}
        if len(materials) == 1:
            return {materials[0]: list(outputs)}
        buckets: Dict[str, List[str]] = {}
        unmatched: List[str] = []
        by_len = sorted(materials, key=len, reverse=True)
        prefix = strip_prefix.lower()
        for path in outputs:
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            if prefix and stem.startswith(prefix):
                stem = stem[len(prefix):]
            for mat in by_len:
                if mat.lower() in stem:
                    buckets.setdefault(mat, []).append(path)
                    break
            else:
                unmatched.append(path)
        if unmatched:
            logger.warning(
                "Baked outputs with no matching material name: %s",
                ", ".join(os.path.basename(p) for p in unmatched),
            )
        return buckets

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"

    @staticmethod
    def build_bake_pairs_manifest(
        objects: Sequence[str],
        high_suffix: str,
        low_suffix: str,
        include_children: bool = True,
    ) -> Dict[str, str]:
        """Build the ``{mesh_short_name: 'source'|'target'}`` sidecar for the bake.

        Toolbag's FBX importer flattens parent transforms on the way in, so
        a ``bake_source`` group that the user named in Maya doesn't survive
        long enough for the Toolbag-side chain classifier to see it. We
        compute the classification HERE -- while we still have the full
        Maya parent chain -- and ship the result as a JSON sidecar that the
        rendered bake template reads after import. This is what makes
        *include_children* (group-root tagging) usable at all: without the
        sidecar the ancestor names are already gone by the time Toolbag looks.

        For each selected object, finds every mesh-transform descendant
        (and the object itself if it has a mesh shape), walks each one's
        Maya parent chain, and records a classification if any ancestor (or
        the mesh itself) carries *high_suffix* or *low_suffix*. With
        *include_children* off only the mesh's own name is consulted. Meshes
        with no match are simply omitted -- ``split_source_target`` will fall
        through to its own chain walk / "rest is X" rules for them.
        """
        if not (high_suffix or low_suffix):
            return {}

        visited = set()
        mesh_xforms: List[str] = []
        for obj in objects:
            try:
                descendants = (
                    cmds.listRelatives(
                        obj, allDescendents=True, type="transform", fullPath=True
                    )
                    or []
                )
            except Exception:
                descendants = []
            for x in [obj] + descendants:
                if x in visited:
                    continue
                visited.add(x)
                shapes = (
                    cmds.listRelatives(x, shapes=True, type="mesh", fullPath=True) or []
                )
                if shapes:
                    mesh_xforms.append(x)

        out: Dict[str, str] = {}
        conflicted: set = set()
        for mesh_path in mesh_xforms:
            cls = _MarmosetBridgeInternal._classify_maya_chain(
                mesh_path, high_suffix, low_suffix, include_children
            )
            if cls:
                leaf = mesh_path.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
                if leaf in out and out[leaf] != cls:
                    # The sidecar is keyed by leaf short name; a scene whose
                    # source and target hierarchies reuse the same mesh names
                    # (|STATIC|HAMMER vs |HIGH|HAMMER) is ambiguous here.
                    # Dropping the key beats letting one side silently win.
                    conflicted.add(leaf)
                    continue
                out[leaf] = cls
        for leaf in conflicted:
            out.pop(leaf, None)
        if conflicted:
            logger.warning(
                "Bake-pairs sidecar: %d mesh name(s) appear on BOTH the source "
                "and target side and were dropped (%s). Name-based pairing "
                "cannot disambiguate them -- define the Bake Source set in the "
                "panel's Bake Source row instead.",
                len(conflicted),
                ", ".join(sorted(conflicted)[:8]),
            )
        return out


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MarmosetBridge()
    bridge.send(template="bake", mode=ROUND_TRIP)
