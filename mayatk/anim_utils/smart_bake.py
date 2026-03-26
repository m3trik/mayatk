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
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.core_utils._core_utils import CoreUtils


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
    """Base-layer visibility animCurves created by inherited-vis bake.
    Maps ``{object_long_name: animCurve_node}`` so the caller can
    delete them after export to restore the scene."""

    visibility_originals: Dict[str, float] = field(default_factory=dict)
    """Original ``.visibility`` values before bake, for cleanup restoration.
    Maps ``{object_long_name: original_value}``."""

    backup_path: Optional[str] = None
    """Path to backup file saved (if backup_file was used)."""

    muted_drivers: List[str] = field(default_factory=list)
    """Driver nodes that were muted (if mute_drivers=True)."""

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

    # Attributes considered for baking (override in subclass to extend)
    TRANSFORM_ATTRS: Set[str] = {
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scaleX",
        "scaleY",
        "scaleZ",
        "translate",
        "rotate",
        "scale",
        "visibility",
    }

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
        optimize_keys: bool = False,
        bake_blend_shapes: bool = True,
        bake_inherited_visibility: bool = False,
        use_override_layer: bool = False,
        mute_drivers: bool = False,
        backup_file: Any = False,
    ):
        """Initialize SmartBake with configuration.

        Parameters:
            objects: Objects to analyze/bake. If None, uses all DAG transforms.
            sample_by: Keyframe sample interval (1 = every frame).
            preserve_outside_keys: Keep existing keys outside bake range.
            delete_inputs: Delete constraint/expression nodes after baking.
                Ignored when use_override_layer=True (use mute_drivers instead).
            optimize_keys: Run AnimUtils.optimize_keys() on baked objects to
                remove static curves and redundant flat keys.
            bake_blend_shapes: Analyze and bake driven blend shape weights.
                Required for Unity if blend shapes are driven by SDKs/expressions.
            bake_inherited_visibility: Walk ancestor transforms to detect
                inherited ``.visibility`` animation and bake it onto child
                mesh transforms.  Required for FBX/Unity when parent LOC
                nodes toggle visibility that child GEO inherits at runtime.
            use_override_layer: Bake to a new override animation layer instead
                of the base layer. Original constraints/expressions remain
                connected on base but are overridden by the baked layer.
                Toggle layer mute to compare baked vs. live results.
                FBX export will flatten layers when FBXExportBakeComplexAnimation=True.
            mute_drivers: Mute (disable) driver nodes after baking instead of
                deleting them. Useful with use_override_layer for better playback
                performance while keeping drivers recoverable. Set nodeState=2.
            backup_file: Save scene backup before any destructive operations.
                - False: No backup (default)
                - True: Save to scene directory as 'scenename_prebake.ma'
                - str: Custom file path for backup
        """
        self.objects = objects
        self.sample_by = sample_by
        self.preserve_outside_keys = preserve_outside_keys
        self.delete_inputs = delete_inputs
        self.optimize_keys = optimize_keys
        self.bake_blend_shapes = bake_blend_shapes
        self.bake_inherited_visibility = bake_inherited_visibility
        self.use_override_layer = use_override_layer
        self.mute_drivers = mute_drivers
        self.backup_file = backup_file

        # Cache for node type inheritance lookups
        self._type_cache: Dict[str, List[str]] = {}

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

        Includes both transforms and joints since joints are a separate Maya
        type but are essential for skeletal animation export.
        """
        if self.objects:
            return list(self.objects)
        # Get all transforms AND joints (joints are separate type in Maya)
        transforms = cmds.ls(type="transform", long=True) or []
        joints = cmds.ls(type="joint", long=True) or []
        return transforms + joints

    def analyze(self) -> Dict[str, BakeAnalysis]:
        """Analyze objects to determine what needs baking.

        Returns:
            Dict mapping object names to their BakeAnalysis results.
        """
        results: Dict[str, BakeAnalysis] = {}
        objects = self._get_objects()

        if not objects:
            return results

        # Batch query all connections for performance
        # Get all incoming connections to our objects
        for obj in objects:
            analysis = self._analyze_object(obj)
            if analysis.requires_bake or analysis.already_keyed:
                results[obj] = analysis

        # Detect inherited visibility from ancestor transforms.
        # Maya's FBX exporter only writes visibility curves for nodes
        # that have *direct* animation on .visibility. When a parent
        # LOC has keyed visibility, the child GEO inherits the
        # show/hide at runtime in Maya but the FBX file omits the
        # curve — Unity then never toggles the Renderer. This step
        # marks such children so bake() will sample the effective
        # visibility and key it directly on the mesh transform.
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

        Maya evaluates inherited visibility at runtime, but the FBX
        exporter only writes a visibility curve for nodes whose own
        ``.visibility`` attribute is keyed. By flagging such objects here,
        ``bake()`` can sample the evaluated ancestor visibility and key it
        directly on the mesh transform so that the FBX exporter (and Unity)
        see it.

        The analysis stores **all** ancestor ``.visibility`` plugs on
        ``source_nodes["inherited_visibility_plugs"]`` — including
        statically-set parents — so the bake phase can reuse them
        without re-walking the hierarchy.

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

    def _analyze_object(self, obj: str) -> BakeAnalysis:
        """Analyze a single object for bake requirements."""
        from mayatk.node_utils._node_utils import NodeUtils

        analysis = BakeAnalysis(object=obj)

        # Check for IK chain membership (joints in IK chains need rotation baking)
        ik_handles = self._get_ik_handles_for_joint(obj)
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

        # Process pairs: [dest_plug, src_plug, dest_plug, src_plug, ...]
        for i in range(0, len(connections), 2):
            dest_plug = connections[i]  # e.g., "pCube1.translateX"
            src_plug = connections[i + 1]

            # Extract attribute name
            if "." not in dest_plug:
                continue
            attr_long = dest_plug.split(".")[-1]

            # Handle compound attrs like .translate -> .translateX, .translateY, .translateZ
            base_attr = attr_long.split("[")[0]  # Handle indexed attrs
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
            return int(min(all_times)), int(max(all_times))

        # Fallback to playback range
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        return int(start), int(end)

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
    ) -> None:
        """Sample effective ancestor visibility and key it on each object.

        Keys are written directly on the **base layer** (no animation
        layer) because FBX ``BakeComplexAnimation`` does not evaluate
        visibility through animation-layer blend nodes — it only reads
        direct animCurve connections.  The caller is responsible for
        deleting the curves listed in ``result.visibility_curves`` after
        export to restore the scene.

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

        Parameters:
            objects: ``{obj: BakeAnalysis}`` for objects needing bake.
            start: First frame of the bake range.
            end: Last frame of the bake range (inclusive).
            result: Live ``BakeResult`` to update with baked/skipped info.
        """
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
                # Snapshot original visibility for cleanup restoration.
                original_vis = cmds.getAttr(f"{obj}.visibility")
                result.visibility_originals[obj] = float(original_vis)

                # Collect key times from ancestor animCurves — only
                # sample at those frames instead of the entire range.
                # Also include key times from the child's own vis curve
                # (if any) so we don't lose its independent transitions.
                sample_times: Set[float] = {float(start), float(end)}
                for curve in ancestor_curves:
                    if cmds.objExists(curve):
                        times = cmds.keyframe(curve, query=True, timeChange=True) or []
                        for t in times:
                            if start <= t <= end:
                                sample_times.add(t)

                # Include the child's own vis key times.
                child_vis_curves = (
                    cmds.listConnections(
                        f"{obj}.visibility",
                        source=True,
                        destination=False,
                        type="animCurve",
                    )
                    or []
                )
                for cvc in child_vis_curves:
                    times = cmds.keyframe(cvc, query=True, timeChange=True) or []
                    for t in times:
                        if start <= t <= end:
                            sample_times.add(t)

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

                # Track the created base-layer curve for cleanup.
                vis_curve = cmds.listConnections(
                    f"{obj}.visibility",
                    source=True,
                    destination=False,
                    type="animCurve",
                )
                if vis_curve:
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
                    f"SmartBake: Failed to bake inherited visibility " f"for {obj}: {e}"
                )

    def _create_override_layer(self, to_bake: Dict[str, Any]) -> str:
        """Create an override animation layer for baking.

        Delegates to AnimUtils.create_animation_layer() for layer creation.

        Parameters:
            to_bake: Dict of {object: BakeAnalysis} for objects being baked.

        Returns:
            Name of the created animation layer.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        # Collect all attributes that will be baked
        attributes = []
        for obj, data in to_bake.items():
            for channel in data.all_driven_channels:
                attributes.append(f"{obj}.{channel}")

        return AnimUtils.create_animation_layer(
            name="SmartBake_Override",
            override=True,
            attributes=attributes,
            preferred=True,
            timestamp_suffix=True,
            unique_name=True,
        )

    def _mute_driver_nodes(self, to_bake: Dict[str, Any]) -> List[str]:
        """Mute driver nodes by setting nodeState=2 (Blocking).

        Parameters:
            to_bake: Dict of {object: BakeAnalysis} for objects being baked.

        Returns:
            List of node names that were muted.
        """
        muted = []
        for obj, data in to_bake.items():
            for nodes in data.source_nodes.values():
                for node in nodes:
                    if cmds.objExists(node):
                        try:
                            if cmds.attributeQuery("nodeState", node=node, exists=True):
                                cmds.setAttr(f"{node}.nodeState", 2)  # Blocking
                                muted.append(node)
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

        # Create override layer for standard channels (excludes visibility)
        override_layer = None
        if self.use_override_layer and remaining_to_bake:
            override_layer = self._create_override_layer(remaining_to_bake)
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
        if inherited_vis_objects:
            self._bake_inherited_visibility(
                inherited_vis_objects,
                start,
                end,
                result,
            )

        # -----------------------------------------------------------
        # Phase 2: Standard channel bake via bakeResults.
        # -----------------------------------------------------------

        # Bake each object with its specific channels
        # Group by channels to use batched bake
        import collections

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
                        result.baked[obj] = list(channels)
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
                result.muted_drivers = self._mute_driver_nodes(to_bake)
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
        if self.optimize_keys and result.baked:
            from mayatk.anim_utils._anim_utils import AnimUtils

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
                    remove_flat_keys=True,
                    remove_static_curves=True,
                    simplify_keys=False,
                    recursive=False,
                    quiet=True,
                )
            result.optimized = list(result.baked.keys())

        return result

    def execute(self) -> BakeResult:
        """High-level entry point: analyze and bake in one call.

        Returns:
            BakeResult dataclass with bake operation results.
        """
        analysis = self.analyze()
        return self.bake(analysis)

    @classmethod
    def run(cls, **kwargs) -> BakeResult:
        """Class method for quick smart baking without explicit instantiation.

        Parameters:
            **kwargs: Forwarded to SmartBake.__init__:
                - objects: Objects to analyze/bake (default: all transforms/joints)
                - sample_by: Keyframe sample interval (default: 1)
                - preserve_outside_keys: Keep keys outside range (default: True)
                - delete_inputs: Delete driver nodes after bake (default: False)
                - optimize_keys: Remove redundant keys after bake (default: False)
                - bake_blend_shapes: Bake driven blend shape weights (default: True)
                - use_override_layer: Bake to override layer (default: False)
                - mute_drivers: Mute drivers instead of deleting (default: False)
                - backup_file: Save backup before baking (default: False)

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
