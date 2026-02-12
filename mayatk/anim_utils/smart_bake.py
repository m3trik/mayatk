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

    # Long-to-short attribute name mappings for bakeResults
    ATTR_SHORT_NAMES: Dict[str, str] = {
        "translateX": "tx",
        "translateY": "ty",
        "translateZ": "tz",
        "rotateX": "rx",
        "rotateY": "ry",
        "rotateZ": "rz",
        "scaleX": "sx",
        "scaleY": "sy",
        "scaleZ": "sz",
        "visibility": "v",
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

        Delegates to NodeUtils.trace_upstream_connection() for the actual
        tracing logic.

        Returns:
            Tuple of (driver_node, driver_type) or (None, None) if not found.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        return NodeUtils.trace_upstream_connection(
            plug, passthrough_types=self.PASSTHROUGH_TYPES, visited=visited
        )

    def _get_attr_short_name(self, long_name: str) -> str:
        """Convert long attribute name to short name for bakeResults."""
        return self.ATTR_SHORT_NAMES.get(long_name, long_name)

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
            # Get shapes under transform
            shapes = cmds.listRelatives(obj, shapes=True, noIntermediate=True) or []
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

        # Create override layer if requested
        override_layer = None
        if self.use_override_layer:
            override_layer = self._create_override_layer(to_bake)
            result.override_layer = override_layer

        start, end = time_range

        from mayatk.anim_utils._anim_utils import AnimUtils

        # Bake each object with its specific channels
        # Group by channels to use batched bake
        import collections

        grouped_by_channels = collections.defaultdict(
            list
        )  # tuple(channels) -> list[objects]

        for obj, data in to_bake.items():
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
                # Delete drivers (destructive)
                for obj, data in to_bake.items():
                    if obj not in result.baked:
                        continue
                    for nodes in data.source_nodes.values():
                        for node in nodes:
                            if cmds.objExists(node):
                                try:
                                    cmds.delete(node)
                                    result.deleted.append(node)
                                except RuntimeError:
                                    pass  # Node already deleted or protected

        # Optimize keys if requested
        if self.optimize_keys and result.baked:
            from mayatk.anim_utils._anim_utils import AnimUtils

            baked_objects = list(result.baked.keys())
            AnimUtils.optimize_keys(
                baked_objects,
                remove_flat_keys=True,
                remove_static_curves=True,
                simplify_keys=False,
                quiet=True,
            )
            result.optimized = baked_objects

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
