# !/usr/bin/python
# coding=utf-8
"""Smart bake module for intelligent pre-bake animation processing.

Analyzes scene objects to detect what requires baking:
- Constraints (parent, point, orient, scale, aim)
- Set Driven Keys (animCurveU* with input connections)
- Expressions
- IK chains (joints driven by ikHandle/ikEffector)
- Motion paths
- Animation layers (anim blend nodes)
- Blend shape weights driven by SDKs/expressions

Auto-detects optimal time range from driver animation.
Designed for Unity/game engine export workflows.
"""

import math
import collections
from contextlib import contextmanager
from typing import Dict, List, Optional, Set, Tuple, Union, TYPE_CHECKING
from dataclasses import dataclass, field

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

if TYPE_CHECKING:
    # Resolves the ``restore()`` return annotation for type-checkers only; the
    # real import is done lazily inside the method, keeping bake_session out of
    # module load like the other deferred imports here.
    from mayatk.anim_utils.smart_bake.bake_session import RestoreResult

import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS


@dataclass
class BakeAnalysis:
    """Analysis result for a single object's bake requirements."""

    object: str
    """The object name being analyzed."""

    driven_channels: Dict[str, List[str]] = field(default_factory=dict)
    """Channels driven by non-keyframe sources. {source_type: [channel_names]}"""

    source_nodes: Dict[str, List[str]] = field(default_factory=dict)
    """Source nodes driving this object. {source_type: [node_names]}"""

    already_keyed: List[str] = field(default_factory=list)
    """Channels that already have direct time-based keyframes."""

    @property
    def requires_bake(self) -> bool:
        """Return True if this object has any driven channels needing bake."""
        return bool(self.driven_channels)

    @property
    def all_driven_channels(self) -> List[str]:
        """Return flat list of all channels that need baking."""
        channels = []
        for ch_list in self.driven_channels.values():
            channels.extend(ch_list)
        return list(set(channels))


@dataclass
class BakeResult:
    """Result container for SmartBake.bake() operation."""

    baked: Dict[str, List[str]] = field(default_factory=dict)
    """Objects that were baked. {object: [channels]}"""

    skipped: List[str] = field(default_factory=list)
    """Objects skipped (no driven channels or bake failed)."""

    time_range: Tuple[int, int] = (0, 0)
    """Time range used for baking (start, end)."""

    deleted: List[str] = field(default_factory=list)
    """Source nodes deleted (if delete_inputs=True)."""

    optimized: List[str] = field(default_factory=list)
    """Objects that had keys optimized (if optimize_keys=True)."""

    override_layer: Optional[str] = None
    """Name of override layer created (if use_override_layer=True)."""

    visibility_curves: Dict[str, str] = field(default_factory=dict)
    """Base-layer visibility animCurves **created** by the inherited-vis bake.
    Maps ``{object_long_name: animCurve_node}`` so the caller can
    delete them after export to restore the scene.

    Deliberately excludes objects that already had their own ``.visibility``
    curve: there the baked keys MERGED into the artist's curve, so deleting
    it would destroy authored animation.  Those objects still appear in
    ``baked``; reverse them with ``SmartBake.restore()``, never by deletion
    (and a non-restorable session refuses to bake them at all)."""

    visibility_originals: Dict[str, float] = field(default_factory=dict)
    """Original ``.visibility`` values before bake, for cleanup restoration.
    Maps ``{object_long_name: original_value}``."""

    backup_path: Optional[str] = None
    """Path to backup file saved (if backup_file was used)."""

    muted_drivers: List[str] = field(default_factory=list)
    """Driver nodes that were muted (if mute_drivers=True)."""

    session_id: Optional[str] = None
    """Id of the restore-manifest session recorded for this bake (if
    restorable=True). Pass to ``SmartBake.restore()`` to reverse the bake —
    the manifest persists on the ``data_internal`` node, so restore works
    even after scene save/reopen."""

    @property
    def baked_count(self) -> int:
        """Number of objects successfully baked."""
        return len(self.baked)

    @property
    def success(self) -> bool:
        """Return True if any objects were baked."""
        return bool(self.baked)


class SmartBake:
    """Intelligent baking with automatic detection of what needs to be baked.

    Analyzes objects to find:
    - Constraint-driven channels (parentConstraint, pointConstraint, etc.)
    - Set Driven Key channels (animCurveU* with input connections)
    - Expression-driven channels
    - IK-driven joint rotations

    Only bakes the specific channels that are driven, leaving already-keyed
    channels untouched. Auto-detects optimal time range from driver animation.

    Example:
        >>> baker = SmartBake()
        >>> result = baker.execute()
        >>> print(result.baked)  # Objects that were baked
        >>> print(result.time_range)  # Time range used
    """

    # Attributes considered for baking (override in subclass to extend).
    # Extends the shared per-axis constant with compound names so that
    # compound plugs like ".translate" are also recognised.
    TRANSFORM_ATTRS: Set[str] = set(STANDARD_TRANSFORM_ATTRS) | {
        "translate",
        "rotate",
        "scale",
    }

    #: Matrix inputs that displace a transform WITHOUT touching its scalar
    #: t/r/s plugs. A rig that places joints through ``offsetParentMatrix``
    #: (a multMatrix network -- standard since Maya 2020) leaves every t/r/s
    #: plug unconnected, so a TRANSFORM_ATTRS-only scan sees nothing and the
    #: object reports ``requires_bake=False`` while moving tens of units.
    MATRIX_ATTRS: Set[str] = {"offsetParentMatrix"}

    #: Scalar channels a matrix drive resolves onto once baked.
    MATRIX_BAKE_CHANNELS: List[str] = [
        "tx",
        "ty",
        "tz",
        "rx",
        "ry",
        "rz",
        "sx",
        "sy",
        "sz",
    ]

    #: Neutralises a baked-away ``offsetParentMatrix``.
    IDENTITY_MATRIX: List[float] = [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]

    # Intermediate node types to trace through when finding drivers
    # These are utility nodes that pass values through without being true "drivers"
    PASSTHROUGH_TYPES: Set[str] = {
        # Blend nodes
        "pairBlend",
        "blendWeighted",
        "blendColors",
        "blendTwoAttr",
        # Unit/type conversion
        "unitConversion",
        "unitToTimeConversion",
        "timeToUnitConversion",
        # Math utility nodes
        "reverse",
        "multiplyDivide",
        "plusMinusAverage",
        "addDoubleLinear",
        "multDoubleLinear",
        # Conditional/remapping
        "condition",
        "remapValue",
        "clamp",
        "setRange",
        # Animation layer blend nodes
        "animBlendNodeAdditive",
        "animBlendNodeAdditiveDA",
        "animBlendNodeAdditiveRotation",
        "animBlendNodeAdditiveScale",
        "animBlendNodeAdditiveDL",
        "animBlendNodeBase",
    }

    def __init__(
        self,
        objects: Optional[List[str]] = None,
        sample_by: int = 1,
        preserve_outside_keys: bool = True,
        delete_inputs: bool = False,
        optimize_keys: Union[bool, str, None] = False,
        bake_blend_shapes: bool = True,
        bake_inherited_visibility: bool = False,
        use_override_layer: bool = True,
        mute_drivers: bool = False,
        backup_file: Union[bool, str, None] = None,
        restorable: bool = True,
    ):
        """Initialize SmartBake with configuration.

        Parameters:
            objects: Objects to analyze/bake. If None, uses all DAG transforms.
            sample_by: Keyframe sample interval (1 = every frame).
            preserve_outside_keys: Keep existing keys outside bake range.
            delete_inputs: Delete constraint/expression nodes after baking.
                Destructive — the restore manifest cannot rebuild deleted
                drivers, so the session is marked non-restorable and a scene
                backup is saved by default (see backup_file).
                Ignored when use_override_layer=True (use mute_drivers instead).
            optimize_keys: Optimization level for the baked output, run
                through ``AnimUtils.optimize_keys()``. A key of
                ``AnimUtils.OPTIMIZE_LEVELS`` (``"static"``, ``"flat"``,
                ``"simplify"``, ``"extremes"``); ``True`` selects the default
                level and anything falsy is OFF. An unknown level raises
                here, before the scene is touched, rather than mid-bake.
                ``"extremes"`` is the one worth knowing about: this bake writes
                a key per frame, which is exactly the input the other levels
                have nothing to delete from.
            bake_blend_shapes: Analyze and bake driven blend shape weights.
                Required for Unity if blend shapes are driven by SDKs/expressions.
            bake_inherited_visibility: Walk ancestor transforms to detect
                inherited ``.visibility`` animation and bake it onto child
                mesh transforms.  API-only — deliberately NOT exposed in the
                Smart Bake panel; see the CAUTION below before enabling it.

                NOT needed for FBX/Unity.  Measured live (Unity 6000.3.10f1):
                Maya's FBX exporter already RESOLVES ancestor visibility onto
                renderable descendants, and Unity binds the result to
                ``m_Enabled@Renderer`` on the child.  A production asset with
                27 keyed ``_LOC`` parents exported 78 visibility curves, every
                one of them on a ``_GEO`` child and none on a ``_LOC``.  A
                statically hidden ancestor resolves at import too.  The only
                gap is a child carrying its OWN ``.visibility`` keys under a
                keyed ancestor: there the exporter writes the child's curve
                alone and drops the ancestor's contribution.

                CAUTION — that gap is also the shape a RenderOpacity fade has.
                ``RenderOpacity.key_fade`` encodes a fade as the GAP between
                two opposite-value ``.visibility`` keys; a key written inside
                it splits one ramp into several.  Measured: an authored 10f
                fade-in + 60f fade-out under an ancestor keyed mid-fade
                reconstructed in Unity as FOUR ramps instead of two — the
                object flickered.  The bake now refuses any object carrying an
                ``opacity`` attribute, no longer keys the bake-range
                boundaries, and refuses a child with its own ``.visibility``
                curve when ``restorable=False`` (nothing could reverse the
                merge).  Ancestor key times can still land inside an
                UNMARKED fade gap, so keep ``opacity`` on faded objects.

                Its one real consumer is the Maya->Blender bridge, which needs
                it for an unrelated reason: Blender's FBX importer drops
                visibility animation entirely, so the values travel in the
                conversion manifest instead.
            use_override_layer: Bake to a new override animation layer instead
                of the base layer (default: True — nondestructive). Original
                constraints/expressions remain connected on base but are
                overridden by the baked layer. Toggle layer mute to compare
                baked vs. live results. FBX export will flatten layers when
                FBXExportBakeComplexAnimation=True. Base-layer mode
                (use_override_layer=False) converts SDK curves in place and
                disconnects drivers — recoverable only via the restore
                manifest (restorable=True) or a backup.
            mute_drivers: Mute (disable) driver nodes after baking instead of
                deleting them. Useful with use_override_layer for better playback
                performance while keeping drivers recoverable. Sets nodeState=2;
                prior states are recorded in the restore manifest.
            backup_file: Save scene backup before any destructive operations.
                - None (default): auto — backup only when delete_inputs=True
                  in base-layer mode (the one non-restorable path).
                - False: never back up.
                - True: Save to scene directory as 'scenename_prebake.ma'.
                - str: Custom file path for backup.
            restorable: Record a restore-manifest session for this bake
                (default: True). The manifest persists on the data_internal
                node; ``SmartBake.restore()`` reverses the bake — deletes the
                override layer, unmutes drivers, re-enables IK handles,
                restores visibility, and rebuilds base-layer driver networks
                from stashed curves. Costs a few small nodes/attrs per bake.
        """
        self.objects = objects
        self.sample_by = sample_by
        self.preserve_outside_keys = preserve_outside_keys
        self.delete_inputs = delete_inputs
        self.optimize_keys = optimize_keys
        # Resolve NOW, not at the call site: an unknown level is a config
        # error and must fail before the first scene mutation, not after N
        # objects have been baked.  Falsy resolves to None, which is what
        # the optimization pass tests to decide whether to run at all.
        from mayatk.anim_utils._anim_utils import AnimUtils

        self._optimize_kwargs = AnimUtils.resolve_optimize_level(optimize_keys)
        self.bake_blend_shapes = bake_blend_shapes
        self.bake_inherited_visibility = bake_inherited_visibility
        self.use_override_layer = use_override_layer
        self.mute_drivers = mute_drivers
        if backup_file is None:
            backup_file = bool(delete_inputs and not use_override_layer)
        self.backup_file = backup_file
        self.restorable = restorable

    # -------------------------------------------------------------------------
    # Connection Tracing
    # -------------------------------------------------------------------------

    def _trace_upstream_driver(
        self, plug: str, visited: Optional[Set[str]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Trace upstream through passthrough nodes to find the true driver.

        Delegates to Attributes.trace_upstream() for the actual
        tracing logic.

        Returns:
            Tuple of (driver_node, driver_type) or (None, None) if not found.
        """
        from mayatk.node_utils.attributes._attributes import (
            Attributes,
        )

        return Attributes.trace_upstream(
            plug, passthrough_types=self.PASSTHROUGH_TYPES, visited=visited
        )

    def _get_attr_short_name(self, long_name: str) -> str:
        """Convert long attribute name to short name for bakeResults."""
        from mayatk.node_utils.attributes._attributes import (
            Attributes,
        )

        return Attributes.attr_short_name(long_name)

    # -------------------------------------------------------------------------
    # Analysis
    # -------------------------------------------------------------------------

    def _get_objects(self) -> List[str]:
        """Get objects to analyze, defaulting to all transforms and joints.

        ``ls(type="transform")`` already includes joints (joint derives from
        transform); the explicit joint query is kept as a safety net for any
        Maya version where it doesn't, with duplicates removed.
        """
        if self.objects:
            return list(self.objects)
        transforms = cmds.ls(type="transform", long=True) or []
        joints = cmds.ls(type="joint", long=True) or []
        return ptk.remove_duplicates(transforms + joints)

    def analyze(self) -> Dict[str, BakeAnalysis]:
        """Analyze objects to determine what needs baking.

        Returns:
            Dict mapping object names to their BakeAnalysis results.
        """
        results: Dict[str, BakeAnalysis] = {}
        objects = self._get_objects()

        if not objects:
            return results

        # Skip the per-joint IK-chain scan entirely when the scene has no
        # IK handles — it is O(joints x handles) otherwise.
        check_ik = bool(cmds.ls(type="ikHandle"))

        for obj in objects:
            analysis = self._analyze_object(obj, check_ik=check_ik)
            if analysis.requires_bake or analysis.already_keyed:
                results[obj] = analysis

        # Detect inherited visibility from ancestor transforms.
        # NOTE: the FBX exporter already resolves ancestor visibility onto
        # renderable descendants on its own (measured — see the CAUTION on
        # bake_inherited_visibility in __init__), so this pass is NOT needed
        # for a Maya->Unity export. It exists for the Maya->Blender bridge,
        # and to cover the one case the exporter drops: a child carrying its
        # OWN .visibility keys under a keyed ancestor.
        if self.bake_inherited_visibility:
            inherited = self._analyze_inherited_visibility(objects, results)
            for obj, analysis in inherited.items():
                if obj in results:
                    # Merge into existing analysis
                    existing = results[obj]
                    for k, v in analysis.driven_channels.items():
                        if k not in existing.driven_channels:
                            existing.driven_channels[k] = v
                    for k, v in analysis.source_nodes.items():
                        if k not in existing.source_nodes:
                            existing.source_nodes[k] = v
                else:
                    results[obj] = analysis

        # Analyze blend shapes separately (they're on deformers, not transforms)
        if self.bake_blend_shapes:
            blendshape_results = self._analyze_blend_shapes(objects)
            for bs, analysis in blendshape_results.items():
                if analysis.requires_bake:
                    results[bs] = analysis

        return results

    def _analyze_blend_shapes(self, objects: List[str]) -> Dict[str, BakeAnalysis]:
        """Analyze blend shape deformers for driven weights.

        Unity can import blend shapes (morph targets) but needs the weights
        baked if driven by expressions or SDKs.

        Returns:
            Dict mapping blendShape node names to their BakeAnalysis.
        """
        results: Dict[str, BakeAnalysis] = {}

        # Find blend shapes connected to our objects
        blend_shapes = set()
        for obj in objects:
            # Get shapes under transform (fullPath avoids ambiguous short names)
            shapes = (
                cmds.listRelatives(obj, shapes=True, noIntermediate=True, fullPath=True)
                or []
            )
            for shape in shapes:
                # Find blend shape deformers
                bs_nodes = (
                    cmds.listConnections(
                        shape, type="blendShape", source=True, destination=False
                    )
                    or []
                )
                blend_shapes.update(bs_nodes)

        for bs in blend_shapes:
            analysis = BakeAnalysis(object=bs)

            # Get weight aliases (target names)
            aliases = cmds.aliasAttr(bs, query=True) or []
            weight_attrs = [aliases[i] for i in range(0, len(aliases), 2)]

            for weight_attr in weight_attrs:
                plug = f"{bs}.{weight_attr}"
                driver_node, driver_type = self._trace_upstream_driver(plug)

                if driver_type and driver_type != "keyframe":
                    if driver_type not in analysis.driven_channels:
                        analysis.driven_channels[driver_type] = []
                    analysis.driven_channels[driver_type].append(weight_attr)

                    if driver_type not in analysis.source_nodes:
                        analysis.source_nodes[driver_type] = []
                    if driver_node not in analysis.source_nodes[driver_type]:
                        analysis.source_nodes[driver_type].append(driver_node)
                elif driver_type == "keyframe":
                    analysis.already_keyed.append(weight_attr)

            if analysis.requires_bake:
                results[bs] = analysis

        return results

    def _analyze_inherited_visibility(
        self,
        objects: List[str],
        existing_results: Dict[str, BakeAnalysis],
    ) -> Dict[str, BakeAnalysis]:
        """Detect visibility animation on ancestor transforms.

        For each export object, walk up the DAG hierarchy. If any
        ancestor's ``.visibility`` plug has incoming animation (animCurve,
        expression, constraint, driven key, etc.) the effective visibility
        of the export object depends on something outside itself.

        Maya evaluates inherited visibility at runtime. The FBX exporter
        resolves it onto renderable descendants by itself EXCEPT when the
        child carries its own ``.visibility`` keys — then the child's curve
        is written alone and the ancestor's contribution is lost. Flagging
        such objects here lets ``bake()`` sample the effective (ancestor x
        self) visibility and key it directly on the mesh transform.

        The analysis stores **all** ancestor ``.visibility`` plugs on
        ``source_nodes["inherited_visibility_plugs"]`` — including
        statically-set parents — so the bake phase can reuse them
        without re-walking the hierarchy.

        Flagging is not a promise to bake: ``_bake_inherited_visibility``
        refuses objects carrying an ``opacity`` attribute, and (in a
        non-restorable session) objects with their own ``.visibility``
        curve.  Those land in ``BakeResult.skipped``.

        Parameters:
            objects: The list of export objects (typically mesh transforms).
            existing_results: Already-analysed results from ``_analyze_object``.

        Returns:
            Dict of *new* ``BakeAnalysis`` entries for objects that need
            inherited-visibility baking. Does not include objects whose
            own ``.visibility`` is already keyed or driven (handled by
            the normal analysis path).
        """
        results: Dict[str, BakeAnalysis] = {}

        for obj in objects:
            # Skip only if visibility is already driven by a non-keyframe
            # source (constraint, expression, etc.) — those are handled
            # by the normal bake path.  Do NOT skip objects whose own
            # .visibility is merely keyed: their keys may represent only
            # the object's *own* show/hide state and not account for an
            # ancestor being hidden.  We need to multiply ancestor
            # visibility into the bake.
            if obj in existing_results:
                existing = existing_results[obj]
                vis_driven = any(
                    "v" in ch_list for ch_list in existing.driven_channels.values()
                )
                if vis_driven:
                    continue

            # Walk up the DAG hierarchy collecting ALL ancestor vis
            # plugs and any animCurve source nodes.
            ancestor_curves: List[str] = []
            ancestor_plugs: List[str] = []
            current = obj
            while True:
                parents = cmds.listRelatives(current, parent=True, fullPath=True)
                if not parents:
                    break
                parent = parents[0]

                # Always track the plug — even statically-set parents
                # affect inherited visibility.
                ancestor_plugs.append(f"{parent}.visibility")

                # Check if parent has animated visibility
                vis_conns = (
                    cmds.listConnections(
                        f"{parent}.visibility",
                        source=True,
                        destination=False,
                        type="animCurve",
                    )
                    or []
                )
                if vis_conns:
                    ancestor_curves.extend(vis_conns)

                # Also check for non-animCurve drivers (expressions, etc.)
                if not vis_conns:
                    any_driver = (
                        cmds.listConnections(
                            f"{parent}.visibility",
                            source=True,
                            destination=False,
                        )
                        or []
                    )
                    if any_driver:
                        ancestor_curves.extend(any_driver)

                current = parent

            if ancestor_curves:
                analysis = BakeAnalysis(object=obj)
                analysis.driven_channels["inherited_visibility"] = ["v"]
                analysis.source_nodes["inherited_visibility"] = ancestor_curves
                # Store all ancestor plugs for the bake phase to reuse.
                analysis.source_nodes["inherited_visibility_plugs"] = ancestor_plugs
                results[obj] = analysis

        return results

    #: Driver types the taxonomy names. Anything else is a raw ``nodeType``
    #: string returned by ``trace_upstream`` as a last-resort fallback.
    SEMANTIC_DRIVER_TYPES: Set[str] = {
        "constraint",
        "expression",
        "driven_key",
        "keyframe",
        "ik",
        "motion_path",
    }

    def _plug_has_upstream_animation(self, plug: str, depth: int = 6) -> bool:
        """Return True if any animCurve feeds *plug*, however indirectly.

        Plug-precise by necessity: a node-level walk conflates every attribute
        on a shared node, so a rig's ``settings_CTRL`` -- keyless display
        switches sitting beside a keyed transform -- would report
        ``controlsVis`` as animated because some *other* attribute has a curve.
        """
        from mayatk.node_utils.attributes._attributes import Attributes

        return bool(
            Attributes.upstream_anim_curves(plug, plug_precise=True, depth=depth)
        )

    def _analyze_object(self, obj: str, check_ik: bool = True) -> BakeAnalysis:
        """Analyze a single object for bake requirements."""
        from mayatk.node_utils._node_utils import NodeUtils

        analysis = BakeAnalysis(object=obj)

        # An ikEffector is IK plumbing, never an animation target. Maya wires
        # ``effector.translate`` straight from the chain's last joint, which
        # the trace below would otherwise report as a joint-driven channel and
        # queue for bake -- keying an effector accomplishes nothing and writes
        # onto a node no exporter reads.
        if cmds.objExists(obj) and cmds.nodeType(obj) == "ikEffector":
            return analysis

        # Check for IK chain membership (joints in IK chains need rotation baking)
        ik_handles = self._get_ik_handles_for_joint(obj) if check_ik else []
        if ik_handles:
            # Joint is part of an IK chain - rotations need baking
            analysis.driven_channels["ik"] = ["rx", "ry", "rz"]
            analysis.source_nodes["ik"] = ik_handles

        # Get all incoming connections with plugs
        connections = (
            cmds.listConnections(
                obj,
                source=True,
                destination=False,
                connections=True,
                plugs=True,
                skipConversionNodes=False,
            )
            or []
        )

        # Process pairs: [dest_plug, src_plug, dest_plug, src_plug, ...].
        # We only need the destination plug; the driver is traced upstream from
        # it via ``_trace_upstream_driver`` below, so the paired source is skipped.
        for i in range(0, len(connections), 2):
            dest_plug = connections[i]  # e.g., "pCube1.translateX"

            # Extract attribute name
            if "." not in dest_plug:
                continue
            attr_long = dest_plug.split(".")[-1]

            # Handle compound attrs like .translate -> .translateX, .translateY, .translateZ
            base_attr = attr_long.split("[")[0]  # Handle indexed attrs

            # A matrix input displaces the object without ever touching a
            # scalar t/r/s plug, so it needs its own detection pass and its
            # own bake (see _bake_matrix_drivers).
            if base_attr in self.MATRIX_ATTRS:
                driver_node = (
                    cmds.listConnections(dest_plug, source=True, destination=False)
                    or [None]
                )[0]
                if driver_node:
                    channels = analysis.driven_channels.setdefault("matrix", [])
                    for channel in self.MATRIX_BAKE_CHANNELS:
                        if channel not in channels:
                            channels.append(channel)
                    sources = analysis.source_nodes.setdefault("matrix", [])
                    if driver_node not in sources:
                        sources.append(driver_node)
                continue

            if base_attr not in self.TRANSFORM_ATTRS:
                continue

            # Trace to find actual driver
            driver_node, driver_type = self._trace_upstream_driver(dest_plug)

            if not driver_node or not driver_type:
                continue

            # Skip muted nodes
            if driver_type in ("constraint", "expression") and NodeUtils.is_muted(
                driver_node
            ):
                continue

            attr_short = self._get_attr_short_name(attr_long)

            # A .visibility wired straight off another node's attribute -- a
            # rig's ``settings_CTRL.controlsVis`` display switch -- is a plain
            # scalar copy with no parent contribution. When nothing upstream
            # carries a key it is a CONSTANT, and baking it writes a flat value
            # across the whole range onto controls that never export.
            #
            # Deliberately narrow: it does NOT generalise to constraints. A
            # constraint whose targets own no curves can still move, because
            # the targets are driven by animated PARENTS -- measured on a
            # production scene, 217 constraint drivers reported no animation
            # while the rig they drive travelled tens of units. "Driver owns no
            # animCurve" is not a proxy for "produces no motion" anywhere but
            # this direct-connect case.
            if (
                attr_short == "v"
                and driver_type not in self.SEMANTIC_DRIVER_TYPES
                and not self._plug_has_upstream_animation(dest_plug)
            ):
                continue

            if driver_type == "keyframe":
                # Already has time-based keyframes
                if attr_short not in analysis.already_keyed:
                    analysis.already_keyed.append(attr_short)
            else:
                # Needs baking - constraint, driven key, expression, or IK
                if driver_type not in analysis.driven_channels:
                    analysis.driven_channels[driver_type] = []
                if attr_short not in analysis.driven_channels[driver_type]:
                    analysis.driven_channels[driver_type].append(attr_short)

                if driver_type not in analysis.source_nodes:
                    analysis.source_nodes[driver_type] = []
                if driver_node not in analysis.source_nodes[driver_type]:
                    analysis.source_nodes[driver_type].append(driver_node)

        return analysis

    def _get_ik_handles_for_joint(self, joint: str) -> List[str]:
        """Find IK handles that control a given joint.

        Delegates to RigUtils.get_ik_handles_for_joint() for the actual logic.

        Returns:
            List of ikHandle names affecting this joint, or empty list.
        """
        from mayatk.rig_utils._rig_utils import RigUtils

        return RigUtils.get_ik_handles_for_joint(joint)

    # -------------------------------------------------------------------------
    # Time Range Detection
    # -------------------------------------------------------------------------

    def get_time_range(
        self, analysis: Optional[Dict[str, BakeAnalysis]] = None
    ) -> Tuple[int, int]:
        """Determine optimal bake time range from driver animation.

        Traces constraint targets and driven key drivers to find their
        animation range. Falls back to playback range if no animation found.

        Parameters:
            analysis: Pre-computed analysis dict. If None, runs analyze().

        Returns:
            Tuple of (start_frame, end_frame) as integers.
        """
        if analysis is None:
            analysis = self.analyze()

        all_times: List[float] = []

        for obj, data in analysis.items():
            for source_type, nodes in data.source_nodes.items():
                for node in nodes:
                    times = self._get_driver_time_range(node, source_type)
                    all_times.extend(times)

        if all_times:
            # floor/ceil — int() truncates toward zero and would drop
            # fractional driver keys at the range boundaries.
            return math.floor(min(all_times)), math.ceil(max(all_times))

        # Fallback to playback range
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        return math.floor(start), math.ceil(end)

    def _get_driver_time_range(self, node: str, source_type: str) -> List[float]:
        """Get keyframe times from a driver node's animation curves.

        Delegates to AnimUtils.get_driver_animation_range() for the actual logic.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return AnimUtils.get_driver_animation_range(node, driver_type=source_type)

    # -------------------------------------------------------------------------
    # Baking
    # -------------------------------------------------------------------------

    def _save_backup(self) -> Optional[str]:
        """Save a backup of the current scene before baking.

        Delegates to EnvUtils.save_scene_backup() for the actual operation.

        Returns:
            Path to the saved backup file, or None if backup was skipped/failed.
        """
        if not self.backup_file:
            return None

        from mayatk.env_utils._env_utils import EnvUtils

        # Determine suffix based on backup type
        if isinstance(self.backup_file, str):
            return EnvUtils.save_scene_backup(backup_path=self.backup_file)
        else:
            return EnvUtils.save_scene_backup(
                backup_path=True,
                suffix="_prebake",
            )

    def _bake_inherited_visibility(
        self,
        objects: Dict[str, "BakeAnalysis"],
        start: int,
        end: int,
        result: "BakeResult",
        session: Optional[dict] = None,
    ) -> Set[str]:
        """Sample effective ancestor visibility and key it on each object.

        Keys are written directly on the **base layer** (no animation
        layer) because FBX ``BakeComplexAnimation`` does not evaluate
        visibility through animation-layer blend nodes — it only reads
        direct animCurve connections.  The caller is responsible for
        deleting the curves listed in ``result.visibility_curves`` after
        export to restore the scene — that dict therefore lists only
        curves this bake CREATED (see below).

        The effective visibility is the product of **all** ancestor
        ``.visibility`` values (including statically-set parents) **and**
        the child's own ``.visibility`` at each frame.  This ensures
        that:

        - A child under a statically-hidden parent is never made visible.
        - A child with its own independent show/hide keys retains them
          (merged with ancestor state) rather than being overwritten.

        Ancestor plugs are reused from the analysis phase stored on
        ``data.source_nodes["inherited_visibility_plugs"]`` to avoid
        re-walking the hierarchy.

        Uses stepped tangents since visibility is boolean.

        Two refusals protect authored data (BACKLOG 2026-08-02):

        - **``opacity`` attribute** — ``RenderOpacity`` encodes an opacity
          fade as the GAP between two opposite-value ``.visibility`` keys.
          Any key written inside that gap splits one ramp into several
          (measured in Unity: two authored ramps reconstructed as four).
          Objects carrying the attribute are warned about and skipped.
        - **Own visibility curve in a non-restorable session** — keying
          merges into the child's ORIGINAL curve.  Without a session there
          is no pristine stash to reverse it, and the curve would be listed
          for deletion, destroying the artist's keys.  Such objects are
          skipped; run with ``restorable=True`` to bake them reversibly.

        Sampling is limited to the child's own key times and the ancestor
        key times.  The bake-range boundaries are deliberately NOT keyed:
        step tangents already hold the first/last sampled value outward, so
        a boundary key only invents a transition — inside a fade gap, a
        wrong one.  An ancestor driven by something other than an animCurve
        (an expression, say) therefore contributes no sample times; such an
        object is skipped rather than keyed from two boundary samples that
        never described its motion.

        Parameters:
            objects: ``{obj: BakeAnalysis}`` for objects needing bake.
            start: First frame of the bake range.
            end: Last frame of the bake range (inclusive).
            result: Live ``BakeResult`` to update with baked/skipped info.
            session: Restorable-session manifest to record visibility
                stashes into, or ``None`` to skip stash bookkeeping (the
                caller passes None for non-restorable sessions — restore()
                refuses those, so their stash nodes could never be
                reclaimed).

        Returns:
            The objects whose baked keys were MERGED into a pre-existing
            (artist-authored) visibility curve — never listed in
            ``result.visibility_curves`` and never optimized.
        """
        merged: Set[str] = set()

        for obj, data in objects.items():
            # Reuse plugs from analysis; fall back to source_nodes curves.
            ancestor_plugs: List[str] = data.source_nodes.get(
                "inherited_visibility_plugs", []
            )
            ancestor_curves: List[str] = data.source_nodes.get(
                "inherited_visibility", []
            )

            if not ancestor_plugs:
                result.skipped.append(obj)
                continue

            try:
                if cmds.attributeQuery("opacity", node=obj, exists=True):
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: {obj} carries an 'opacity' attribute - "
                        f"its .visibility keys encode a RenderOpacity fade as "
                        f"the gap between them. Refusing to bake inherited "
                        f"visibility onto it (any inserted key splits the fade)."
                    )
                    continue

                child_vis_curves = (
                    cmds.listConnections(
                        f"{obj}.visibility",
                        source=True,
                        destination=False,
                        type="animCurve",
                    )
                    or []
                )

                if child_vis_curves and session is None:
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: {obj} has its own .visibility animation and "
                        f"this session is not restorable - baking would merge "
                        f"into the artist's curve irreversibly. Skipped; use "
                        f"restorable=True to bake it."
                    )
                    continue

                # Snapshot original visibility for cleanup restoration.
                original_vis = cmds.getAttr(f"{obj}.visibility")
                result.visibility_originals[obj] = float(original_vis)

                # Sample ONLY at the ancestor key times and the child's own
                # key times — never at the bake-range boundaries.
                sample_times: Set[float] = set()
                for curve in ancestor_curves:
                    if cmds.objExists(curve):
                        times = cmds.keyframe(curve, query=True, timeChange=True) or []
                        for t in times:
                            if start <= t <= end:
                                sample_times.add(t)

                # Include the child's own vis key times.
                for cvc in child_vis_curves:
                    times = cmds.keyframe(cvc, query=True, timeChange=True) or []
                    for t in times:
                        if start <= t <= end:
                            sample_times.add(t)

                if not sample_times:
                    result.visibility_originals.pop(obj, None)
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: nothing to sample for {obj} - no ancestor "
                        f"visibility keys fall inside {start}-{end} (an ancestor "
                        f"driven by something other than an animCurve "
                        f"contributes no key times)."
                    )
                    continue

                # Keying below mutates the child's OWN vis curve in place —
                # stash a pristine duplicate first so restore can bring the
                # original animation back instead of deleting it with the
                # baked keys.
                if session is not None:
                    from mayatk.anim_utils.smart_bake import bake_session

                    vis_stash = (
                        bake_session.BakeSessionStore.stash_curve(child_vis_curves[0])
                        if child_vis_curves
                        else None
                    )
                    session["visibility"].append(
                        {
                            "object": bake_session.BakeSessionStore.node_ref(obj),
                            "had_curve": bool(child_vis_curves),
                            "stash": vis_stash,
                            "original_value": float(original_vis),
                        }
                    )

                sorted_times = sorted(sample_times)

                # Snapshot the child's own visibility at ALL sample
                # times BEFORE writing any keys.  Once we start keying
                # the curve, later getAttr reads would return values
                # from the modified curve rather than the original.
                child_vis_snapshot = {
                    frame: float(cmds.getAttr(f"{obj}.visibility", time=frame))
                    for frame in sorted_times
                }

                for frame in sorted_times:
                    # Start with the child's original visibility.
                    effective = child_vis_snapshot[frame]
                    if effective == 0:
                        pass  # Already 0, skip ancestor evaluation.
                    else:
                        for plug in ancestor_plugs:
                            val = cmds.getAttr(plug, time=frame)
                            if val == 0:
                                effective = 0.0
                                break
                            effective *= val

                    cmds.setKeyframe(
                        obj,
                        attribute="visibility",
                        time=frame,
                        value=effective,
                        shape=False,
                    )

                # Track the created base-layer curve for cleanup.  A curve
                # that pre-existed this bake is the ARTIST's — the baked keys
                # merged into it, so it must NOT be advertised under the
                # delete-after-export contract; ``restore()`` reverses that
                # merge from the pristine stash instead.
                vis_curve = cmds.listConnections(
                    f"{obj}.visibility",
                    source=True,
                    destination=False,
                    type="animCurve",
                )
                if vis_curve:
                    if child_vis_curves:
                        merged.add(obj)
                    else:
                        result.visibility_curves[obj] = vis_curve[0]
                    cmds.keyTangent(vis_curve[0], outTangentType="step")
                    result.baked[obj] = ["v"]
                else:
                    cmds.warning(
                        f"SmartBake: No animCurve found on "
                        f"{obj}.visibility after keying — "
                        f"curve may have been renamed."
                    )
                    result.skipped.append(obj)
            except Exception as e:
                result.skipped.append(obj)
                cmds.warning(
                    f"SmartBake: Failed to bake inherited visibility for {obj}: {e}"
                )

        return merged

    def _writable_matrix_channels(self, obj: str) -> List[str]:
        """Return the t/r/s channels of *obj* the matrix bake will key.

        Only LOCKED channels are excluded. Channels driven by a live
        network are included on purpose: the folded local
        (``TRS x offsetParentMatrix``) is only consistent when EVERY
        channel lands, so the bake severs those inputs (recorded;
        restore reconnects). The previous design left network-driven
        channels to their drivers -- the production _01 wire looms'
        curveInfo-driven scale then dropped the OPM's own scale content
        entirely, and the folded-R/T-beside-unfolded-S hybrid drifted
        worlds up to 3.1 cm at the chain tip (probe-pinned).
        """
        writable: List[str] = []
        for channel in self.MATRIX_BAKE_CHANNELS:
            plug = f"{obj}.{channel}"
            try:
                if cmds.getAttr(plug, lock=True):
                    continue
            except (RuntimeError, ValueError):
                continue
            writable.append(channel)
        return writable

    def _bake_matrix_drivers(
        self,
        objects: Dict[str, BakeAnalysis],
        start: int,
        end: int,
        result: BakeResult,
        session: Optional[dict] = None,
    ) -> None:
        """Bake ``offsetParentMatrix``-driven objects onto their t/r/s channels.

        ``bakeResults`` cannot do this. It samples the scalar t/r/s plugs, and
        a matrix-driven object's local TRS is identity, so it writes zeros --
        the animation reads correct only while the matrix network is still
        connected, and is lost the instant it isn't.

        Samples the EFFECTIVE local matrix (``localTRS * offsetParentMatrix``)
        at every frame first, then disconnects the network, resets the plug to
        identity, and writes the sampled transforms as keys. Verified against a
        matrix-driven joint to reproduce the driven motion exactly.

        Runs in BOTH layer and base modes. A layer cannot hold it (no matrix
        blend node exists), and leaving the network live for the FBX exporter
        ships FROZEN motion whenever the matrix upstream does not translate
        to FBX -- BakeComplexAnimation then samples the plug once at the
        export-time frame (``TestFbxMatrixOpmExport`` pins this).

        Parameters:
            objects: ``{object: BakeAnalysis}`` carrying a "matrix" drive.
            start: First frame to sample.
            end: Last frame to sample.
            result: Mutated in place -- baked/skipped are recorded here.
            session: Restore manifest to append to, or None when the bake is
                not restorable.
        """
        import maya.api.OpenMaya as om

        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        step = max(1, int(self.sample_by))
        frames = list(range(int(start), int(end) + 1, step))
        if frames and frames[-1] != int(end):
            frames.append(int(end))

        # Resolve the bakeable set up front:
        # (object, matrix plug, source plug, writable channels).
        targets: List[Tuple[str, str, str, List[str]]] = []
        for obj in objects:
            if not cmds.objExists(obj):
                result.skipped.append(obj)
                continue
            plug = f"{obj}.offsetParentMatrix"
            sources = (
                cmds.listConnections(plug, source=True, destination=False, plugs=True)
                or []
            )
            if not sources:  # disconnected between analyze() and bake()
                result.skipped.append(obj)
                continue
            channels = self._writable_matrix_channels(obj)
            if not channels:
                result.skipped.append(obj)
                continue
            targets.append((obj, plug, sources[0], channels))

        if not targets:
            return

        # Snapshot what the t/r/s channels held BEFORE the bake overwrites
        # them, so restore can put it back: an existing curve is stashed, a
        # static value recorded verbatim, and any non-curve driver captured as
        # a connection (writing keys below would sever it).
        pending: Dict[str, dict] = {}
        if session is not None:
            for obj, _, source_plug, channels in targets:
                originals: Dict[str, float] = {}
                stashes: List[dict] = []
                connections: List[List[dict]] = []
                for channel in channels:
                    channel_plug = f"{obj}.{channel}"
                    curves = (
                        cmds.listConnections(
                            channel_plug,
                            type="animCurve",
                            source=True,
                            destination=False,
                        )
                        or []
                    )
                    if curves:
                        stashes.append(BakeSessionStore.stash_curve(curves[0]))
                    else:
                        originals[channel] = cmds.getAttr(channel_plug)
                    connections.extend(
                        BakeSessionStore.snapshot_connections(channel_plug)
                    )
                pending[obj] = {
                    "object": BakeSessionStore.node_ref(obj),
                    "source": BakeSessionStore.plug_ref(source_plug),
                    "channels": list(channels),
                    "originals": originals,
                    "stashes": stashes,
                    "_connections": connections,
                }

        restore_time = cmds.currentTime(query=True)

        # Sample with the TIMELINE outermost: one scene evaluation per frame
        # for the whole set, not one per object per frame. Every currentTime
        # forces a full DG evaluation, so the object-outer form cost
        # objects x frames of them -- on the production rig that found this bug
        # (182 matrix-driven joints over 1134 frames) roughly 206,000
        # evaluations instead of 1,134.
        sampled: Dict[str, Dict[int, List[float]]] = {
            obj: {} for obj, _, _, _ in targets
        }
        for frame in frames:
            cmds.currentTime(frame)
            for obj, plug, _, _ in targets:
                offset = om.MMatrix(cmds.getAttr(plug))
                local = om.MMatrix(cmds.xform(obj, query=True, matrix=True))
                sampled[obj][frame] = list(local * offset)

        # Neutralise every drive before writing any keys -- a half-disconnected
        # set would sample-and-write against a moving target.
        surviving: List[Tuple[str, str, str, List[str]]] = []
        for obj, plug, source_plug, channels in targets:
            try:
                cmds.disconnectAttr(source_plug, plug)
                cmds.setAttr(plug, self.IDENTITY_MATRIX, type="matrix")
                # The folded local is the COMPLETE transform: any channel a
                # live network keeps driving would stay unfolded beside it.
                # Sever every non-animCurve input on the bake channels (the
                # pairs are already in the session via snapshot_connections;
                # restore reconnects them). animCurve inputs stay -- the
                # stash mechanism owns those.
                cut_pairs = set()
                for channel in channels:
                    child = f"{obj}.{channel}"
                    probe_plugs = [child]
                    try:
                        parents = (
                            cmds.attributeQuery(channel, node=obj, listParent=True)
                            or []
                        )
                    except RuntimeError:
                        parents = []
                    if parents:
                        probe_plugs.append(f"{obj}.{parents[0]}")
                    for probe in probe_plugs:
                        conns = (
                            cmds.listConnections(
                                probe,
                                source=True,
                                destination=False,
                                plugs=True,
                                connections=True,
                            )
                            or []
                        )
                        for k in range(0, len(conns), 2):
                            dst, src = conns[k], conns[k + 1]
                            if (dst, src) in cut_pairs:
                                continue
                            if cmds.nodeType(src.partition(".")[0]).startswith(
                                "animCurve"
                            ):
                                continue
                            try:
                                cmds.disconnectAttr(src, dst)
                                cut_pairs.add((dst, src))
                            except RuntimeError:
                                pass  # locked/refused: setKeyframe will skip it
                # segmentScaleCompensate is part of the drive being
                # neutralised: with SSC live, xform(matrix=) folds the
                # inverseScale compensation into the SHEAR channel -- which
                # is never keyed, so the last-written value sticks and
                # shears the local at every other frame (0.10-0.35 residue
                # on the production wire looms, re-blocking the export the
                # flatten had just fixed). The sampled effective local
                # already contains the compensation, so keys written with
                # SSC off reproduce the same worlds exactly.
                if cmds.attributeQuery("segmentScaleCompensate", node=obj, exists=True):
                    prior_ssc = cmds.getAttr(f"{obj}.segmentScaleCompensate")
                    if prior_ssc:
                        cmds.setAttr(f"{obj}.segmentScaleCompensate", False)
                        record = pending.get(obj)
                        if record is not None:
                            record["ssc"] = int(prior_ssc)
                if cmds.attributeQuery("shear", node=obj, exists=True):
                    prior_shear = cmds.getAttr(f"{obj}.shear")[0]
                    if any(abs(v) > 1e-9 for v in prior_shear):
                        record = pending.get(obj)
                        if record is not None:
                            record["shear_was"] = list(prior_shear)
                surviving.append((obj, plug, source_plug, channels))
            except RuntimeError as e:
                cmds.warning(f"SmartBake: could not neutralise '{plug}': {e}")
                for record in pending.pop(obj, {}).get("stashes", []):
                    BakeSessionStore.discard_stash(record)
                result.skipped.append(obj)

        if session is not None:
            for obj, _, _, _ in surviving:
                record = pending.get(obj)
                if record is None:
                    continue
                session["connections"].extend(record.pop("_connections", []))
                session["matrix"].append(record)

        for frame in frames:
            cmds.currentTime(frame)
            for obj, _, _, channels in surviving:
                # xform applies the whole matrix (jointOrient/rotateAxis
                # included -- probe-verified identity on orient-carrying
                # joints); every bake channel's live input was severed
                # above, so the complete folded local lands. A LOCKED
                # channel still refuses and keeps its value.
                cmds.xform(obj, matrix=sampled[obj][frame])
                cmds.setKeyframe(obj, attribute=channels, time=frame)

        for obj, _, _, channels in surviving:
            prior = result.baked.get(obj, [])
            result.baked[obj] = sorted(set(prior) | set(channels))
            # Whatever shear the per-frame xform writes, only the LAST value
            # survives (shear is not keyed) -- and FBX/glTF drop shear
            # anyway. Zero it so the static leftover cannot skew the local
            # at other frames; the pre-bake value is in the session record.
            try:
                cmds.setAttr(f"{obj}.shear", 0.0, 0.0, 0.0)
            except RuntimeError:
                pass  # locked/connected shear keeps its own value

        cmds.currentTime(restore_time)

    def _create_override_layer(self) -> str:
        """Create an empty override animation layer for baking.

        Delegates to AnimUtils.create_animation_layer() for layer creation.
        Deliberately does NOT pre-register attributes onto the layer (no
        ``attributes=`` kwarg): pre-registering via ``cmds.animLayer(edit=True,
        attribute=...)`` and then ``bakeResults(destinationLayer=...)`` onto
        the SAME freshly-created layer corrupts the bake — every sampled key
        comes back as one flat constant (whatever value was live at
        registration time) instead of the true per-frame curve. Proven live:
        a locator animated 0->5 baked through a pre-registered layer read
        back flat at every frame; handing bakeResults the empty layer and
        letting it wire the attributes itself reproduces the original motion
        exactly, including non-linear ("auto") tangent shape.

        Returns:
            Name of the created (empty) animation layer.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return AnimUtils.create_animation_layer(
            name="SmartBake_Override",
            override=True,
            preferred=True,
            timestamp_suffix=True,
            unique_name=True,
        )

    def _mute_driver_nodes(
        self, to_bake: Dict[str, BakeAnalysis]
    ) -> List[Tuple[str, int]]:
        """Mute driver nodes by setting nodeState=2 (Blocking).

        Parameters:
            to_bake: Dict of {object: BakeAnalysis} for objects being baked.

        Returns:
            List of ``(node, prior_nodeState)`` tuples for the restore manifest.
        """
        muted: List[Tuple[str, int]] = []
        seen: Set[str] = set()
        for obj, data in to_bake.items():
            for source_type, nodes in data.source_nodes.items():
                if source_type.startswith("inherited_visibility"):
                    continue  # ancestor curves/plugs, not driver nodes
                if source_type == "matrix":
                    # The direct matrix bake (both modes) has already
                    # disconnected this object's offsetParentMatrix, so there
                    # is nothing left to mute -- and the multMatrix may still
                    # feed OTHER consumers, which muting would freeze.
                    continue
                for node in nodes:
                    if node in seen or not cmds.objExists(node):
                        continue
                    seen.add(node)
                    try:
                        if cmds.attributeQuery("nodeState", node=node, exists=True):
                            prior = cmds.getAttr(f"{node}.nodeState")
                            cmds.setAttr(f"{node}.nodeState", 2)  # Blocking
                            muted.append((node, int(prior)))
                    except RuntimeError:
                        pass
        return muted

    @CoreUtils.undoable
    def bake(
        self,
        analysis: Optional[Dict[str, BakeAnalysis]] = None,
        time_range: Optional[Tuple[int, int]] = None,
    ) -> BakeResult:
        """Execute baking on analyzed objects.

        Parameters:
            analysis: Pre-computed analysis. If None, runs analyze().
            time_range: Custom time range. If None, auto-detects from drivers.

        Returns:
            BakeResult dataclass with baked, skipped, time_range, deleted,
            override_layer, backup_path, and muted_drivers.
        """
        if analysis is None:
            analysis = self.analyze()

        if time_range is None:
            time_range = self.get_time_range(analysis)

        result = BakeResult(time_range=time_range)

        # Collect objects that need baking
        to_bake = {obj: data for obj, data in analysis.items() if data.requires_bake}

        if not to_bake:
            result.skipped = list(analysis.keys())
            return result

        # Warn about conflicting options
        if self.use_override_layer and self.delete_inputs:
            cmds.warning(
                "SmartBake: delete_inputs is ignored when use_override_layer=True. "
                "Use mute_drivers=True instead to disable drivers without deleting."
            )

        # Save backup before any destructive operations
        result.backup_path = self._save_backup()

        # Restore-manifest session: records everything this bake changes so
        # SmartBake.restore() can reverse it (persisted on data_internal).
        from mayatk.anim_utils.smart_bake import bake_session

        session: Optional[dict] = None
        if self.restorable:
            # delete_inputs removes the driver nodes themselves — nothing to
            # reconnect afterwards, so the session is recorded but flagged
            # non-restorable (restore() then points at the backup instead).
            # mute_drivers takes precedence over delete_inputs at cleanup
            # time, so drivers survive (and the session stays restorable).
            will_delete = (
                self.delete_inputs
                and not self.use_override_layer
                and not self.mute_drivers
            )
            session = {
                "version": bake_session.BakeSessionStore.SCHEMA_VERSION,
                "id": bake_session.BakeSessionStore.new_session_id(),
                "restorable": not will_delete,
                "time_range": list(time_range),
                "override_layer": None,
                "baked_plugs": [],
                "layer_conversions": [],
                "connections": [],
                "stashed_curves": [],
                "visibility": [],
                "matrix": [],
                "ik_handles": [],
                "muted_drivers": [],
                "backup_path": result.backup_path,
            }

            # bakeResults(disableImplicitControl=True) zeroes ikBlend on the
            # handles EVEN when baking to an override layer — record the
            # pre-bake state so restore can re-enable IK.
            if session["restorable"]:
                seen_handles: Set[str] = set()
                for obj, data in to_bake.items():
                    for handle in data.source_nodes.get("ik", []):
                        if handle in seen_handles or not cmds.objExists(handle):
                            continue
                        seen_handles.add(handle)
                        if not cmds.attributeQuery("ikBlend", node=handle, exists=True):
                            continue
                        had_incoming = bool(
                            cmds.listConnections(
                                f"{handle}.ikBlend",
                                source=True,
                                destination=False,
                                type="animCurve",
                            )
                        )
                        session["ik_handles"].append(
                            {
                                "ref": bake_session.BakeSessionStore.node_ref(handle),
                                "ik_blend": float(cmds.getAttr(f"{handle}.ikBlend")),
                                "had_incoming": had_incoming,
                            }
                        )

        # Split inherited-visibility objects from standard driven channels.
        # These get their own dedicated layer and frame-by-frame sampling.
        inherited_vis_objects = {}
        remaining_to_bake = {}

        for obj, data in to_bake.items():
            if "inherited_visibility" in data.driven_channels:
                inherited_vis_objects[obj] = data
                # If the object also has other driven channels, include
                # it in the standard bake pass for those channels.
                other_channels = {
                    k: v
                    for k, v in data.driven_channels.items()
                    if k != "inherited_visibility"
                }
                if other_channels:
                    other_analysis = BakeAnalysis(object=obj)
                    other_analysis.driven_channels = other_channels
                    other_analysis.source_nodes = {
                        k: v
                        for k, v in data.source_nodes.items()
                        if not k.startswith("inherited_visibility")
                    }
                    other_analysis.already_keyed = list(data.already_keyed)
                    remaining_to_bake[obj] = other_analysis
            else:
                remaining_to_bake[obj] = data

        # Split matrix-driven objects out of the standard pass. bakeResults
        # samples the scalar t/r/s plugs, and for a matrix drive those are
        # identity -- it would write nine channels of zeros and the motion
        # would vanish the moment the matrix network was disconnected. Only an
        # explicit effective-local-matrix sample can bake these, and that
        # requires neutralising offsetParentMatrix (see _bake_matrix_drivers).
        matrix_objects: Dict[str, BakeAnalysis] = {}
        for obj in list(remaining_to_bake):
            data = remaining_to_bake[obj]
            if "matrix" not in data.driven_channels:
                continue
            matrix_objects[obj] = data
            # The object may ALSO be constraint- or IK-driven; those channels
            # still belong in the standard bakeResults pass.
            other_channels = {
                k: v for k, v in data.driven_channels.items() if k != "matrix"
            }
            if other_channels:
                other_analysis = BakeAnalysis(object=obj)
                other_analysis.driven_channels = other_channels
                other_analysis.source_nodes = {
                    k: v for k, v in data.source_nodes.items() if k != "matrix"
                }
                other_analysis.already_keyed = list(data.already_keyed)
                remaining_to_bake[obj] = other_analysis
            else:
                del remaining_to_bake[obj]

        # Create override layer for standard channels (excludes visibility)
        override_layer = None
        if self.use_override_layer and remaining_to_bake:
            override_layer = self._create_override_layer()
            result.override_layer = override_layer

        start, end = time_range

        from mayatk.anim_utils._anim_utils import AnimUtils

        # -----------------------------------------------------------
        # Phase 1: Bake inherited visibility via frame-by-frame sampling.
        #
        # bakeResults cannot resolve ancestor-inherited visibility; it
        # only evaluates the attribute's own value at each time.  We
        # manually sample the effective visibility (product of all
        # ancestor .visibility values) and key it on the mesh transform.
        # Keys are written on the BASE LAYER (not an override layer)
        # because FBX BakeComplexAnimation does not evaluate visibility
        # through animation-layer blend nodes.
        # -----------------------------------------------------------
        merged_vis_objects: Set[str] = set()
        if inherited_vis_objects:
            # Only record/stash for restorable sessions: restore() refuses
            # non-restorable (delete_inputs) sessions outright, so any stash
            # created for one could never be reclaimed and would leak locked
            # nodes into the scene.
            merged_vis_objects = self._bake_inherited_visibility(
                inherited_vis_objects,
                start,
                end,
                result,
                session=session if session and session["restorable"] else None,
            )

        # -----------------------------------------------------------
        # Phase 1b: Matrix drives (offsetParentMatrix).
        #
        # Baked DIRECTLY (base level) in BOTH modes. An animation layer
        # blends keyable scalars -- Maya has no matrix blend node -- so a
        # layer can never neutralise a matrix plug. And leaving the network
        # live for FBX does NOT work: FBXExportBakeComplexAnimation samples
        # the t/r/s plugs per frame but evaluates a CONNECTED
        # offsetParentMatrix only when its whole upstream translates to FBX.
        # A plain animCurve network bakes; anything constraint- or IK-driven
        # upstream (constraints are stripped on export) is sampled ONCE at
        # the export-time frame. Verified: a minimal repro shipped worldX
        # 0/0 for a live 0/25 (test_unbaked_opm_freezes_through_fbx), and
        # the production wire looms shipped 15.9 cm off exactly while their shot
        # animated. The direct bake is recorded in the session manifest and
        # reversed with the rest of the restore.
        # -----------------------------------------------------------
        if matrix_objects:
            self._bake_matrix_drivers(
                matrix_objects,
                start,
                end,
                result,
                session=session if session and session["restorable"] else None,
            )

        # -----------------------------------------------------------
        # Phase 2: Standard channel bake via bakeResults.
        # -----------------------------------------------------------

        # Bake each object with its specific channels
        # Group by channels to use batched bake
        grouped_by_channels = collections.defaultdict(
            list
        )  # tuple(channels) -> list[objects]

        for obj, data in remaining_to_bake.items():
            channels = data.all_driven_channels
            if not channels:
                result.skipped.append(obj)
                continue

            # SmartBake logic: explicit channel lists derived from analysis
            key = tuple(sorted(channels))
            grouped_by_channels[key].append(obj)

        # Base-layer mode is destructive: bakeResults converts SDK curves in
        # place (the original animCurveU node is DELETED and replaced by a
        # same-named animCurveT) and disconnects driver networks.  Before
        # baking, snapshot each plug's incoming connections and stash a
        # locked duplicate of every animCurve feeding it (directly or
        # through passthrough nodes) so restore can rebuild the network.
        # Layer mode keeps the original connections live under the layer's
        # blend node, so there is nothing to reconnect — but DELETING that layer
        # makes Maya rebuild the direct link itself and re-derive any implicit
        # unitConversion from the working unit in force at that moment. The
        # exporter's is metres while the scene authored them in centimetres, so
        # record each plug's factor for restore_session to re-pin (this is what
        # scaled the wire-loom auto-bend channels by 100).
        if session is not None and session["restorable"] and self.use_override_layer:
            for obj, data in remaining_to_bake.items():
                if not data.all_driven_channels:
                    continue
                session["layer_conversions"].append(
                    bake_session.BakeSessionStore.snapshot_conversions(
                        obj, data.all_driven_channels
                    )
                )

        if (
            session is not None
            and session["restorable"]
            and not self.use_override_layer
        ):
            stashed_curve_nodes: Set[str] = set()
            for obj, data in remaining_to_bake.items():
                channels = data.all_driven_channels
                if not channels:
                    continue
                session["baked_plugs"].append(
                    {
                        "ref": bake_session.BakeSessionStore.node_ref(obj),
                        "channels": channels,
                    }
                )
                for channel in channels:
                    plug = f"{obj}.{channel}"
                    session["connections"].extend(
                        bake_session.BakeSessionStore.snapshot_connections(plug)
                    )
                    for curve in bake_session.BakeSessionStore.collect_upstream_curves(
                        plug, self.PASSTHROUGH_TYPES
                    ):
                        if curve not in stashed_curve_nodes:
                            stashed_curve_nodes.add(curve)
                            session["stashed_curves"].append(
                                bake_session.BakeSessionStore.stash_curve(curve)
                            )

        for channels, objects in grouped_by_channels.items():
            try:
                dest_layer = None
                if self.use_override_layer and override_layer:
                    dest_layer = override_layer

                # Using the unified bake command
                baked = AnimUtils.bake(
                    objects,
                    attributes=list(channels),
                    time_range=(start, end),
                    sample_by=self.sample_by,
                    preserve_outside_keys=self.preserve_outside_keys,
                    simulation=False,
                    destination_layer=dest_layer,
                    remove_baked_attr_from_layer=False,
                    bake_on_override_layer=False,
                    sparse_anim_curve_bake=False,
                    minimize_rotation=True,
                    disable_implicit_control=True,
                    control_points=False,
                    shape=False,
                    only_keyed=False,  # SmartBake analysis already determined driven channels
                )

                if baked:
                    for obj in objects:
                        # Merge, don't assign: the inherited-visibility pass
                        # may already have recorded ["v"] for this object.
                        prior = result.baked.get(obj, [])
                        result.baked[obj] = sorted(set(prior) | set(channels))
                else:
                    for obj in objects:
                        result.skipped.append(obj)

            except Exception as e:
                for obj in objects:
                    result.skipped.append(obj)
                cmds.warning(f"SmartBake: Failed to batch bake {channels}: {e}")

        # Handle driver node cleanup after all baking is complete
        if result.baked:
            if self.mute_drivers:
                # Mute drivers (set nodeState=2) - keeps them recoverable
                muted_with_states = self._mute_driver_nodes(to_bake)
                result.muted_drivers = [node for node, _ in muted_with_states]
                if session is not None:
                    session["muted_drivers"] = [
                        {
                            "ref": bake_session.BakeSessionStore.node_ref(node),
                            "prior_state": prior,
                        }
                        for node, prior in muted_with_states
                    ]
            elif self.delete_inputs and not self.use_override_layer:
                # Delete drivers (destructive).
                # IMPORTANT: bakeResults converts SDK curves (animCurveU*)
                # in-place to time-based curves (animCurveT*), reusing the
                # same node.  We must NOT delete nodes that are now the
                # baked result.  Check the current nodeType before deleting.
                for obj, data in to_bake.items():
                    if obj not in result.baked:
                        continue
                    for source_type, nodes in data.source_nodes.items():
                        # Ancestor vis curves/plugs are NOT driver inputs to
                        # this object — the parent's own animation was never
                        # baked away and must survive.
                        if source_type.startswith("inherited_visibility"):
                            continue
                        # The matrix bake already disconnected
                        # offsetParentMatrix; the multMatrix may still feed
                        # other consumers, so leave the network standing.
                        if source_type == "matrix":
                            continue
                        for node in nodes:
                            if not cmds.objExists(node):
                                continue
                            # Skip SDK curves that bakeResults converted
                            # in-place from animCurveU* to animCurveT*.
                            if source_type == "driven_key":
                                node_type = cmds.nodeType(node)
                                if node_type.startswith("animCurveT"):
                                    # bakeResults converted this SDK
                                    # curve — it's now the baked result.
                                    continue
                            try:
                                cmds.delete(node)
                                result.deleted.append(node)
                            except RuntimeError:
                                pass  # Node already deleted or protected

        # Optimize keys if requested — only on baked channels, not the
        # entire object.  Passing whole objects would let optimize_keys
        # delete pre-existing curves (e.g. stepped keys the user placed
        # manually) that happen to be constant-valued.
        if self._optimize_kwargs and result.baked:
            baked_curves = []
            # When an override layer exists, query its curves directly.
            # listConnections(plug) won't traverse animBlendNodes.
            if override_layer and cmds.objExists(override_layer):
                layer_curves = (
                    cmds.animLayer(override_layer, query=True, animCurves=True) or []
                )
                baked_curves = list(set(layer_curves))

            # Also include base-layer visibility curves.
            if result.visibility_curves:
                baked_curves.extend(result.visibility_curves.values())
                baked_curves = list(set(baked_curves))

            if not baked_curves:
                for obj, channels in result.baked.items():
                    for ch in channels:
                        if ch == "v" and obj in merged_vis_objects:
                            # The artist's own curve, merged into — optimizing
                            # it would delete keys this bake never wrote.
                            continue
                        plug = f"{obj}.{ch}"
                        curves = cmds.listConnections(
                            plug,
                            type="animCurve",
                            source=True,
                            destination=False,
                        )
                        if curves:
                            baked_curves.extend(curves)
                baked_curves = list(set(baked_curves))

            if baked_curves:
                AnimUtils.optimize_keys(
                    baked_curves,
                    recursive=False,
                    quiet=True,
                    **self._optimize_kwargs,
                )
            result.optimized = list(result.baked.keys())

        # Persist the restore manifest — only when the bake actually
        # changed something worth reversing.
        if session is not None:
            if result.baked or result.visibility_curves:
                if override_layer and cmds.objExists(override_layer):
                    session["override_layer"] = bake_session.BakeSessionStore.node_ref(
                        override_layer
                    )
                bake_session.BakeSessionStore.push(session)
                result.session_id = session["id"]
            else:
                # Bake was a no-op — discard any stashes created for it.
                for record in session["stashed_curves"]:
                    bake_session.BakeSessionStore.discard_stash(record)
                for entry in session["visibility"]:
                    if entry.get("stash"):
                        bake_session.BakeSessionStore.discard_stash(entry["stash"])

        # An object can be skipped by more than one phase — report it once.
        result.skipped = ptk.remove_duplicates(result.skipped)

        return result

    def execute(self) -> BakeResult:
        """High-level entry point: analyze and bake in one call.

        Returns:
            BakeResult dataclass with bake operation results.
        """
        analysis = self.analyze()
        return self.bake(analysis)

    # -------------------------------------------------------------------------
    # Restore
    # -------------------------------------------------------------------------

    @classmethod
    def list_sessions(cls) -> List[str]:
        """Return ids of restorable bake sessions recorded in this scene,
        oldest first."""
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        return BakeSessionStore.list_ids()

    @classmethod
    @CoreUtils.undoable
    def restore(cls, session_id: Optional[str] = None) -> "RestoreResult":
        """Reverse a bake session recorded by ``bake(restorable=True)``.

        Restores from the manifest persisted on the ``data_internal`` node,
        so this works in a later Maya session after scene save/reopen:

        - Deletes the override animation layer (drivers resume).
        - Unmutes drivers to their recorded nodeState values.
        - Re-enables IK handles (``disableImplicitControl`` zeroes ikBlend
          even when baking to a layer).
        - Restores visibility: deletes baked curves, reconnects the stashed
          original curve or resets the recorded static value.
        - Base-layer bakes: deletes the baked curves and rebuilds the driver
          network — reconnects recorded constraint/expression/motion-path
          plugs and unstashes SDK / blended key curves.

        Known limitation: if bake deleted an intermediate blend node (e.g. a
        pairBlend), its wiring cannot be rebuilt — the stashed curve is
        reconnected directly to the channel where possible and a warning is
        reported. Restore never raises on missing nodes; per-item issues are
        collected in ``RestoreResult.warnings``.

        Parameters:
            session_id: Session to restore. None (default) restores the most
                recent session (LIFO). The session is removed from the
                manifest either way — including non-restorable
                (delete_inputs) sessions, so older sessions stay reachable.

        Returns:
            RestoreResult with success flag, per-category restore lists, and
            warnings. ``success=False`` means the session was missing or
            recorded as non-restorable.
        """
        from mayatk.anim_utils.smart_bake.bake_session import (
            BakeSessionStore,
            RestoreResult,
        )

        session = BakeSessionStore.peek(session_id)
        if session is None:
            result = RestoreResult(session_id=session_id)
            result.warnings.append(
                "No bake session found to restore."
                if session_id is None
                else f"Bake session '{session_id}' not found."
            )
            cmds.warning(f"SmartBake: {result.warnings[0]}")
            return result

        result = BakeSessionStore.restore_session(session)
        # Pop only after the restore pass completes — an unexpected failure
        # mid-restore leaves the session in place so it can be retried.
        BakeSessionStore.pop(session.get("id"))
        for warning in result.warnings:
            cmds.warning(f"SmartBake restore: {warning}")
        return result

    @classmethod
    def restore_matrix_wiring(cls, session_id: Optional[str] = None) -> "RestoreResult":
        """Restore ONLY the matrix (offsetParentMatrix) bakes of a session.

        The keep-bake consumer: the scene exporter's "Scene Keys (In Place)"
        mode keeps the override layer and every scalar bake -- but baked
        matrix channels cannot stay. Their keys were written in whatever
        parent space the flatten task staged, and the deferred flatten
        restore reinstates the original offsetParentMatrix wiring, which
        would then compose ON TOP of the baked keys (a double transform).
        This hands exactly those channels back to their live drivers:
        deletes the baked t/r/s curves, unstashes what the channels held,
        and reconnects the recorded matrix source and driver plugs.

        The session manifest is left in place (not popped) and unmodified: a
        later full :meth:`restore` re-applies these sections harmlessly --
        curve deletion is a no-op, an already-made connection is skipped,
        and a since-deleted matrix source only logs a warning while the
        plug keeps the wiring the flatten restore gave it.

        Parameters:
            session_id: Session to slice. None restores from the most recent.

        Returns:
            RestoreResult for the slice; ``success=False`` when the session
            was missing or recorded as non-restorable.
        """
        from mayatk.anim_utils.smart_bake.bake_session import (
            BakeSessionStore,
            RestoreResult,
        )

        session = BakeSessionStore.peek(session_id)
        if session is None:
            result = RestoreResult(session_id=session_id)
            result.warnings.append(
                "No bake session found to restore."
                if session_id is None
                else f"Bake session '{session_id}' not found."
            )
            cmds.warning(f"SmartBake: {result.warnings[0]}")
            return result

        result = BakeSessionStore.restore_session(
            {
                "version": session.get("version"),
                "id": session.get("id"),
                "restorable": session.get("restorable", True),
                "matrix": session.get("matrix", []),
                "connections": session.get("connections", []),
            }
        )
        for warning in result.warnings:
            cmds.warning(f"SmartBake matrix-wiring restore: {warning}")
        return result

    @classmethod
    @contextmanager
    def session(cls, **kwargs):
        """Context manager: bake on enter, restore on exit.

        The scene is returned to its pre-bake state even if the body raises —
        made for export workflows::

            with SmartBake.session(objects=meshes) as result:
                export_fbx(...)
            # layer deleted, drivers unmuted, IK re-enabled

        Parameters:
            **kwargs: Forwarded to SmartBake.__init__ (restorable is forced
                True — the exit restore depends on the manifest).

        Yields:
            BakeResult from the enter-time bake.
        """
        kwargs["restorable"] = True
        result = cls(**kwargs).execute()
        try:
            yield result
        finally:
            if result.session_id:
                cls.restore(result.session_id)

    @classmethod
    def run(cls, **kwargs) -> BakeResult:
        """Class method for quick smart baking without explicit instantiation.

        Parameters:
            **kwargs: Forwarded to SmartBake.__init__:
                - objects: Objects to analyze/bake (default: all transforms/joints)
                - sample_by: Keyframe sample interval (default: 1)
                - preserve_outside_keys: Keep keys outside range (default: True)
                - delete_inputs: Delete driver nodes after bake (default: False)
                - optimize_keys: Optimization level for the baked output —
                  an AnimUtils.OPTIMIZE_LEVELS key, True for the default
                  level, falsy for OFF (default: False)
                - bake_blend_shapes: Bake driven blend shape weights (default: True)
                - use_override_layer: Bake to override layer (default: True)
                - mute_drivers: Mute drivers instead of deleting (default: False)
                - backup_file: Save backup before baking (default: None = auto)
                - restorable: Record a restore-manifest session (default: True)

        Returns:
            BakeResult dataclass with bake operation results.

        Example:
            >>> result = SmartBake.run()
            >>> result = SmartBake.run(objects=["pCube1"], delete_inputs=True)
            >>> # Non-destructive bake to layer with backup:
            >>> result = SmartBake.run(use_override_layer=True, backup_file=True)
            >>> if result.success:
            ...     print(f"Baked {result.baked_count} objects")
            ...     if result.override_layer:
            ...         print(f"Baked to layer: {result.override_layer}")
        """
        return cls(**kwargs).execute()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
