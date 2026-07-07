# !/usr/bin/python
# coding=utf-8
"""Main workflow facade for blendShape morph-animation creation, editing, and export."""
from typing import List, Optional, Tuple, Union

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.blendshape_animator.applicator import Applicator, ApplyStatus
from mayatk.anim_utils.blendshape_animator.creator import Creator
from mayatk.anim_utils.blendshape_animator.helpers import list_history
from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.recovery import Recovery
from mayatk.anim_utils.blendshape_animator.target import Target, Targets
from mayatk.anim_utils.blendshape_animator.validator import Validator
from mayatk.anim_utils.blendshape_animator.weights import Weights


class BlendshapeAnimator(ptk.LoggingMixin):
    """Main workflow facade for blendShape animations.

    Holds references to the four sub-components:
      * ``keyframes`` (:class:`Keyframes`)   — keyframe authoring on the bs weight
      * ``tween_creator`` (:class:`Creator`) — duplicate-mesh in-between creation
      * ``tween_applicator`` (:class:`Applicator`) — apply tween edits back to bs
      * (Recovery is a stateless utility class; not stored.)
    """

    def __init__(self):
        super().__init__()
        self.base_mesh: Optional[str] = None
        self.target_mesh: Optional[str] = None
        self.blendshape: Optional[str] = None
        self.keyframes: Optional[Keyframes] = None
        self.tween_creator: Optional[Creator] = None
        self.tween_applicator: Optional[Applicator] = None

    # =============================================================================
    # CREATE
    # =============================================================================

    DEFAULT_START_FRAME = 5500
    DEFAULT_END_FRAME = 5800

    @CoreUtils.undoable
    def create(
        self,
        base_mesh: Optional[str] = None,
        target_mesh: Optional[str] = None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        name: str = "morph",
        test_setup: bool = True,
    ) -> bool:
        """Set up basic morph animation between two meshes."""
        self.logger.info("=== CREATE PHASE: Setting up morph animation ===")

        # Coalesce here rather than in the signature so callers that pass
        # None through (e.g. basic_workflow) still get the defaults.
        if start_frame is None:
            start_frame = self.DEFAULT_START_FRAME
        if end_frame is None:
            end_frame = self.DEFAULT_END_FRAME

        if base_mesh is None or target_mesh is None:
            selection = cmds.ls(selection=True)
            if len(selection) != 2:
                self.logger.error(
                    "Please select exactly 2 meshes (source first, target second)"
                )
                return False
            base_mesh, target_mesh = selection[0], selection[1]

        if not Validator.validate_meshes(base_mesh, target_mesh):
            return False

        self.base_mesh = base_mesh
        self.target_mesh = target_mesh

        try:
            history = list_history(base_mesh, type_filter="blendShape")
            if history:
                self.blendshape = history[0]
                self.logger.info(f"Found existing blendShape: {self.blendshape}")
            else:
                blendshape_name = f"{name}_BS"
                self.blendshape = cmds.blendShape(
                    target_mesh,
                    base_mesh,
                    name=blendshape_name,
                    frontOfChain=True,
                    origin="world",
                )[0]
                self.logger.info(f"Created blendShape: {self.blendshape}")

            cmds.setAttr(f"{self.blendshape}.weight[0]", keyable=True, lock=False)
            cmds.setAttr(f"{self.blendshape}.envelope", 1.0)

            self.keyframes = Keyframes(self.base_mesh, self.target_mesh, self.blendshape)
            self.tween_creator = Creator(self.keyframes)
            self.tween_applicator = Applicator(self.keyframes)

            if not self.keyframes.create_keyframes(start_frame, end_frame):
                self._clear_setup_state()
                return False

            if test_setup:
                self.logger.info("Testing blendShape setup...")
                self.keyframes.test_morph()

            self.logger.info(f"CREATE phase complete: {base_mesh} -> {target_mesh}")
            self.logger.info(f"Animation range: {start_frame} to {end_frame}")
            return True

        except RuntimeError as e:
            self.logger.error(f"in CREATE phase: {e}")
            self._clear_setup_state()
            return False

    def _clear_setup_state(self) -> None:
        """Reset the bound setup after a failed create so the animator (and
        any UI gating on it) doesn't report a half-initialized setup."""
        self.base_mesh = None
        self.target_mesh = None
        self.blendshape = None
        self.keyframes = None
        self.tween_creator = None
        self.tween_applicator = None

    # =============================================================================
    # EDIT — three explicit methods (no string dispatch)
    # =============================================================================

    def edit_weight_based(
        self,
        weights: Optional[List[float]] = None,
        count: int = 3,
        weight_range: Tuple[float, float] = (0.0, 1.0),
    ) -> List[Target]:
        """Create tweens at specific weights or evenly spaced."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Creating weight-based tweens ===")

        if weights is None:
            weights = Weights.generate_weights(count, weight_range)
        else:
            weights = [Weights.round_weight(w) for w in weights]

        tweens = self.tween_creator.create_weight_based_tweens(weights)

        if tweens:
            self.logger.info(
                f"Edit these {len(tweens)} meshes to customize the morph curve"
            )
            self.logger.info("When done editing, call: edit_apply_tweens()")

        return tweens

    def edit_frame_based(
        self,
        frames: Optional[List[int]] = None,
        target_frame: Optional[int] = None,
    ) -> List[Target]:
        """Create tweens at specific animation frames."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Creating frame-based tweens ===")

        created_tweens: List[Target] = []

        if target_frame is not None:
            tween = self.tween_creator.create_frame_based_tween(target_frame)
            if tween:
                created_tweens.append(tween)

        if frames:
            for frame in frames:
                tween = self.tween_creator.create_frame_based_tween(frame)
                if tween:
                    created_tweens.append(tween)

        if created_tweens:
            self.logger.info(
                f"Edit these {len(created_tweens)} meshes to customize specific frames"
            )
            self.logger.info("When done editing, call: edit_apply_tweens()")

        return created_tweens

    def edit_apply_tweens(
        self, tweens: Optional[List[Target]] = None
    ) -> List[Target]:
        """Apply tween mesh edits back to blendShape."""
        if not self._validate_setup():
            return []
        self.logger.info("=== EDIT PHASE: Applying tween edits ===")

        results = self.tween_applicator.apply_tweens(tweens)
        applied = [t for t, s in results if s is ApplyStatus.APPLIED]

        if applied:
            self.logger.info("Tween edits applied! Scrub timeline to see custom curve")

        return applied

    # =============================================================================
    # INTERNAL
    # =============================================================================

    def _validate_setup(self) -> bool:
        """Return True if base mesh + blendShape + keyframes engine are bound.

        ``target_mesh`` is intentionally NOT required: workflows like
        ``_cleanup_target_mesh`` and ``remove_target_for_export`` legitimately
        clear it once the blendShape is established (Bug 2).
        """
        if not all([self.base_mesh, self.blendshape, self.keyframes]):
            self.logger.error("Setup not complete. Run create() first.")
            return False
        return True

    def _process_existing_inbetweens(self, inbetween_meshes: List[str]) -> None:
        """Add pre-existing in-between meshes to the blendShape."""
        if not self._validate_setup():
            return

        self.logger.info(
            f"Processing {len(inbetween_meshes)} existing in-between meshes..."
        )

        count = len(inbetween_meshes)
        weights = Weights.generate_weights(count, (0.0, 1.0))

        for mesh, weight in zip(inbetween_meshes, weights):
            try:
                cmds.blendShape(
                    self.blendshape,
                    edit=True,
                    inBetween=True,
                    target=(self.base_mesh, 0, mesh, weight),
                )
                self.tween_creator.tag_tween_mesh(mesh, weight)
                self.logger.info(f"  Added {mesh} as in-between at weight {weight:.3f}")
            except RuntimeError as e:
                self.logger.error(f"  Failed to add {mesh}: {e}")

        self.logger.info("Existing in-between meshes processed.")

    # =============================================================================
    # WORKFLOW CONVENIENCE METHODS
    # =============================================================================

    @classmethod
    def basic_workflow(
        cls,
        base_mesh: Optional[str] = None,
        target_mesh: Optional[str] = None,
        inbetween_meshes: Optional[List[str]] = None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        frame_range: Optional[Union[Tuple[int, int], List[int]]] = None,
        name: str = "morph",
    ) -> Optional["BlendshapeAnimator"]:
        """Complete basic workflow: create setup with targets ready for editing."""
        cls.logger.info("=== BASIC WORKFLOW ===")

        if frame_range is not None:
            if isinstance(frame_range, (tuple, list)) and len(frame_range) == 2:
                start_frame, end_frame = frame_range
            else:
                cls.logger.error(
                    "frame_range must be a tuple/list of (start_frame, end_frame)"
                )
                return None

        animator = cls()
        success = animator.create(
            base_mesh=base_mesh,
            target_mesh=target_mesh,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            test_setup=True,
        )

        if not success:
            cls.logger.error("Setup failed. Check your mesh objects or selection.")
            return None

        if inbetween_meshes:
            cls.logger.info(
                f"Processing {len(inbetween_meshes)} existing in-between meshes..."
            )
            animator._process_existing_inbetweens(inbetween_meshes)
        else:
            cls.logger.info("Creating target meshes for custom animation curve...")
            targets = animator.edit_weight_based(count=3)

            if targets:
                cls.logger.info(f"Created {len(targets)} target meshes")
                cls.logger.info(
                    "Now edit these meshes in Maya to customize your animation curve"
                )
                cls.logger.info(
                    "When done editing, call: animator.apply_all_edits()"
                )

        return animator

    @CoreUtils.undoable
    def apply_all_edits(self) -> bool:
        """Apply all target edits to the current setup."""
        self.logger.info("=== APPLYING ALL TARGET EDITS ===")

        if not self._validate_setup():
            return False

        applied = self.edit_apply_tweens()

        if applied:
            self.logger.info(f"Applied {len(applied)} target edits")
            self.logger.info(
                "Check your timeline - animation should now show custom curve"
            )
            return True
        self.logger.warning("No target edits found to apply")
        return False

    @CoreUtils.undoable
    def finalize_for_export(
        self,
        cleanup_scene: bool = True,
        delete_construction_history: bool = True,
        hide_target_mesh: bool = True,
        delete_inbetween_meshes: bool = True,
    ) -> bool:
        """Finalize the morph animation and clean up the scene for baking/export."""
        self.logger.info("=== FINALIZING FOR EXPORT ===")

        if not self._validate_setup():
            return False

        self.logger.info("Step 1: Applying all in-between edits...")
        applied = self.edit_apply_tweens()

        if not applied:
            self.logger.info("No edits to apply - continuing with cleanup...")
        else:
            self.logger.info(f"Applied {len(applied)} in-between edits")

        if cleanup_scene:
            self.logger.info("Step 2: Cleaning up scene...")

            if hide_target_mesh and self.target_mesh:
                try:
                    cmds.setAttr(f"{self.target_mesh}.visibility", False)
                    self.logger.info(f"  Hidden target mesh: {self.target_mesh}")
                except RuntimeError as e:
                    self.logger.warning(
                        f"  Could not hide target mesh: {self.target_mesh} ({e})"
                    )

            if delete_inbetween_meshes:
                tweens = Targets.find_all_targets()
                deleted_count = 0
                for tween in tweens:
                    try:
                        cmds.delete(tween.mesh)
                        deleted_count += 1
                    except RuntimeError as e:
                        self.logger.warning(
                            f"  Could not delete: {tween.mesh} ({e})"
                        )

                if deleted_count > 0:
                    self.logger.info(
                        f"  Deleted {deleted_count} in-between mesh objects"
                    )

                for group_name in Targets.DEFAULT_GROUPS:
                    if cmds.objExists(group_name):
                        children = cmds.listRelatives(group_name, children=True) or []
                        if not children:
                            try:
                                cmds.delete(group_name)
                                self.logger.info(
                                    f"  Deleted empty group: {group_name}"
                                )
                            except RuntimeError:
                                pass
            else:
                for group_name in Targets.DEFAULT_GROUPS:
                    if cmds.objExists(group_name):
                        try:
                            cmds.setAttr(f"{group_name}.visibility", False)
                            self.logger.info(f"  Hidden group: {group_name}")
                        except RuntimeError:
                            pass

        if delete_construction_history and self.base_mesh:
            self.logger.info("Step 3: Cleaning construction history...")
            try:
                # Maya's canonical non-deformer history bake: removes
                # construction nodes while preserving deformers (blendShape,
                # skinCluster) and the animation curves driving them.  A
                # type-filtered cmds.delete over listHistory would also
                # delete the weight animCurve (nodeType 'animCurveTU'),
                # destroying the morph animation this function exports.
                before = set(list_history(self.base_mesh))
                cmds.bakePartialHistory(self.base_mesh, prePostDeformers=True)
                removed = len(before - set(list_history(self.base_mesh)))
                if removed:
                    self.logger.info(
                        f"  Cleaned {removed} history nodes "
                        "(preserved blendShape + animation)"
                    )
                else:
                    self.logger.info("  No unnecessary history to clean")
            except RuntimeError as e:
                self.logger.warning(f"  Could not clean history completely: {e}")

        self.logger.info("Step 4: Final validation...")
        try:
            original_weight = cmds.getAttr(f"{self.blendshape}.weight[0]")
            cmds.setAttr(f"{self.blendshape}.weight[0]", 0.5)
            cmds.refresh()
            cmds.setAttr(f"{self.blendshape}.weight[0]", original_weight)
            self.logger.info("  BlendShape validation passed")
        except RuntimeError as e:
            self.logger.warning(f"  BlendShape validation warning: {e}")

        self.logger.info("=== EXPORT READY ===")
        self.logger.info(f"Base mesh: {self.base_mesh}")
        self.logger.info(f"BlendShape: {self.blendshape}")
        self.logger.info(
            f"Animation keyframes: {len(cmds.keyframe(f'{self.blendshape}.weight[0]', query=True) or [])} keys"
        )
        self.logger.info("Scene cleaned and ready for baking/export")
        return True

    @classmethod
    def from_existing(
        cls, base_mesh: Optional[str] = None
    ) -> Optional["BlendshapeAnimator"]:
        """Create animator from existing blendShape setup on ``base_mesh``."""
        cls.logger.info("=== LOADING EXISTING SETUP ===")

        if base_mesh is None:
            selection = cmds.ls(selection=True)
            if selection:
                base_mesh = selection[0]
            else:
                cls.logger.error("No base mesh provided and nothing selected.")
                return None

        history = list_history(base_mesh, type_filter="blendShape")
        if not history:
            cls.logger.error(f"No blendShape found on {base_mesh}")
            return None

        blendshape = history[0]

        targets = cmds.blendShape(blendshape, query=True, target=True)
        if not targets:
            cls.logger.error(f"No targets found in blendShape {blendshape}")
            return None

        # Heuristic: pick the first target whose name doesn't look like a tween
        # mesh. Brittle — relies on naming patterns set by Creator (Bug 12).
        # If the convention changes, update both this list and Creator.
        TWEEN_NAME_PATTERNS = ("tween_f", "_w0", "_ib_")
        target_mesh = next(
            (t for t in targets if not any(p in t for p in TWEEN_NAME_PATTERNS)),
            None,
        )

        if target_mesh is None:
            target_mesh = targets[0]
            cls.logger.warning(
                f"Using {target_mesh} as target - might be an in-between mesh"
            )

        animator = cls()
        animator.base_mesh = base_mesh
        animator.target_mesh = target_mesh
        animator.blendshape = blendshape
        animator.keyframes = Keyframes(base_mesh, target_mesh, blendshape)
        animator.tween_creator = Creator(animator.keyframes)
        animator.tween_applicator = Applicator(animator.keyframes)

        existing_keys = cmds.keyframe(f"{blendshape}.weight[0]", query=True) or []
        if existing_keys:
            cls.logger.info(f"Found {len(existing_keys)} existing keyframes")
        else:
            cls.logger.warning("No animation keyframes found")

        cls.logger.info(f"Loaded existing setup: {base_mesh} -> {target_mesh}")
        return animator

    def recover_animation(self) -> bool:
        """Recover lost animation keyframes and validate setup."""
        self.logger.info("=== RECOVERING ANIMATION ===")

        if not self._validate_setup():
            return False

        current_keys = cmds.keyframe(f"{self.blendshape}.weight[0]", query=True) or []

        if len(current_keys) >= 2:
            self.logger.info(
                f"Animation already exists with {len(current_keys)} keyframes"
            )
            return True

        self.logger.warning("No animation keyframes found - attempting recovery...")

        tweens = Targets.find_all_targets()
        if tweens:
            frames = [t.target_frame for t in tweens if t.target_frame]

            if len(frames) >= 2:
                start_frame = min(frames)
                end_frame = max(frames)
                self.logger.info(
                    f"Recovered frame range from tweens: {start_frame} to {end_frame}"
                )

                if self.keyframes.create_keyframes(start_frame, end_frame):
                    self.logger.info("Animation keyframes recovered")
                    return True

        self.logger.warning(
            "Could not recover original range - creating default animation (frames 1-100)"
        )
        if self.keyframes.create_keyframes(1, 100):
            self.logger.info("Default animation created")
            return True

        self.logger.error("Failed to recover animation")
        return False

    def diagnose_topology_issues(self) -> bool:
        """Diagnose topology mismatches between base mesh and in-between meshes."""
        self.logger.info("=== TOPOLOGY DIAGNOSIS ===")

        if not self._validate_setup():
            return False

        base_vert_count = cmds.polyEvaluate(self.base_mesh, vertex=True)
        base_face_count = cmds.polyEvaluate(self.base_mesh, face=True)

        self.logger.info(
            f"Base mesh '{self.base_mesh}': {base_vert_count} vertices, {base_face_count} faces"
        )

        try:
            target_vert_count = cmds.polyEvaluate(self.target_mesh, vertex=True)
            target_face_count = cmds.polyEvaluate(self.target_mesh, face=True)
            self.logger.info(
                f"Target mesh '{self.target_mesh}': {target_vert_count} vertices, {target_face_count} faces"
            )
            if target_vert_count != base_vert_count:
                self.logger.warning("Target mesh topology mismatch!")
        except RuntimeError as e:
            self.logger.error(f"Cannot read target mesh topology ({e})")

        tweens = Targets.find_all_targets()
        if not tweens:
            self.logger.info("No in-between meshes found")
            return True

        self.logger.info(f"Checking {len(tweens)} in-between meshes:")

        mismatched_count = 0
        for tween in tweens:
            try:
                tween_vert_count = cmds.polyEvaluate(tween.mesh, vertex=True)
                tween_face_count = cmds.polyEvaluate(tween.mesh, face=True)
            except RuntimeError as e:
                self.logger.error(f"  {tween.mesh}: Error - {e}")
                mismatched_count += 1
                continue

            if (
                tween_vert_count == base_vert_count
                and tween_face_count == base_face_count
            ):
                self.logger.info(
                    f"  {tween.mesh}: {tween_vert_count}v, {tween_face_count}f (MATCH)"
                )
            else:
                self.logger.error(
                    f"  {tween.mesh}: {tween_vert_count}v, {tween_face_count}f (MISMATCH)"
                )
                mismatched_count += 1

        if mismatched_count > 0:
            self.logger.warning(
                f"{mismatched_count} meshes have topology mismatches"
            )
            self.logger.info(
                "Possible solutions: delete + recreate / Transfer Attributes / "
                "manually fix vertex counts / start over with matching topology"
            )
            return False

        self.logger.info("All meshes have matching topology")
        return True

    def cleanup_topology_mismatches(
        self,
        delete_mismatched: bool = True,
        apply_valid_only: bool = True,
    ) -> bool:
        """Clean up topology mismatches by deleting bad meshes and applying good ones."""
        self.logger.info("=== CLEANING UP TOPOLOGY MISMATCHES ===")

        if not self._validate_setup():
            return False

        base_vert_count = cmds.polyEvaluate(self.base_mesh, vertex=True)
        target_topology_mismatch = False

        try:
            target_vert_count = cmds.polyEvaluate(self.target_mesh, vertex=True)
            if target_vert_count != base_vert_count:
                self.logger.warning(
                    f"Target mesh topology mismatch: {target_vert_count}v vs {base_vert_count}v"
                )
                target_topology_mismatch = True
            else:
                self.logger.info(f"Target mesh topology OK: {target_vert_count}v")
        except RuntimeError as e:
            self.logger.warning(f"Cannot validate target mesh topology ({e})")
            target_topology_mismatch = True

        all_tweens = Targets.find_all_targets()
        if not all_tweens:
            self.logger.info("No in-between meshes found")
            if target_topology_mismatch and delete_mismatched:
                self._cleanup_target_mesh()
            return True

        valid_tweens = self.tween_applicator.validate_topology(all_tweens)
        invalid_tweens = [t for t in all_tweens if t not in valid_tweens]

        self.logger.info(
            f"Found {len(valid_tweens)} valid and {len(invalid_tweens)} invalid in-between meshes"
        )

        if apply_valid_only and valid_tweens:
            self.logger.info(f"Applying {len(valid_tweens)} valid meshes...")
            results = self.tween_applicator.apply_tweens(
                valid_tweens, validate_topology=False
            )
            applied_count = sum(
                1 for _, status in results if status is ApplyStatus.APPLIED
            )
            self.logger.info(f"Successfully applied {applied_count} valid meshes")

        if delete_mismatched and invalid_tweens:
            self.logger.info(f"Deleting {len(invalid_tweens)} mismatched meshes...")
            deleted_count = 0

            for tween in invalid_tweens:
                try:
                    mesh_name = tween.mesh
                    cmds.delete(tween.mesh)
                    self.logger.info(f"  Deleted: {mesh_name}")
                    deleted_count += 1
                except RuntimeError as e:
                    self.logger.error(f"  Failed to delete {tween.mesh}: {e}")

            for group_name in Targets.DEFAULT_GROUPS:
                if cmds.objExists(group_name):
                    children = cmds.listRelatives(group_name, children=True) or []
                    if not children:
                        try:
                            cmds.delete(group_name)
                            self.logger.info(f"  Deleted empty group: {group_name}")
                        except RuntimeError:
                            pass

            self.logger.info(f"Deleted {deleted_count} mismatched meshes")

        if target_topology_mismatch and delete_mismatched:
            self._cleanup_target_mesh()

        remaining_tweens = Targets.find_all_targets()
        self.logger.info("Cleanup complete")
        self.logger.info(f"  Remaining in-between meshes: {len(remaining_tweens)}")
        self.logger.info(
            f"  Applied valid meshes: {len(valid_tweens) if apply_valid_only else 0}"
        )
        if target_topology_mismatch and delete_mismatched:
            self.logger.info("  Target mesh: Updated/cleaned")

        return True

    def _cleanup_target_mesh(self) -> None:
        """Hide problematic target mesh and clear the local reference."""
        try:
            old_target_name = self.target_mesh
            cmds.setAttr(f"{self.target_mesh}.visibility", False)
            self.logger.info(
                f"  Hidden problematic target mesh: {old_target_name}"
            )
            self.target_mesh = None
            self.logger.info("  Updated target reference to None")
        except RuntimeError as e:
            self.logger.warning(f"  Could not clean up target mesh: {e}")

    def remove_target_for_export(self) -> bool:
        """Remove target mesh for clean export."""
        self.logger.info("=== REMOVING TARGET MESH FOR EXPORT ===")

        if not self._validate_setup():
            return False

        if self.target_mesh and cmds.objExists(self.target_mesh):
            try:
                target_name = self.target_mesh
                cmds.delete(self.target_mesh)
                self.logger.info(f"Removed target mesh: {target_name}")
                self.target_mesh = None

                if self.blendshape and cmds.objExists(self.blendshape):
                    self.logger.info(
                        f"BlendShape {self.blendshape} preserved - animation intact"
                    )
                else:
                    self.logger.warning(
                        "BlendShape not found - animation may be lost"
                    )

                self.logger.info(
                    "Export cleanup complete - scene contains only base mesh with animation"
                )
                return True
            except RuntimeError as e:
                self.logger.error(f"Failed to remove target mesh: {e}")
                return False

        self.logger.info("No target mesh to remove - scene already clean for export")
        return True

    @classmethod
    def recover_setup(
        cls,
        base_mesh: Optional[str] = None,
        target_mesh: Optional[str] = None,
    ) -> Optional["BlendshapeAnimator"]:
        """Recover corrupted blendShape setup."""
        cls.logger.info("=== RECOVERY MODE ===")

        if base_mesh is None or target_mesh is None:
            selection = cmds.ls(selection=True)
            if len(selection) >= 2:
                base_mesh = selection[0] if base_mesh is None else base_mesh
                target_mesh = selection[1] if target_mesh is None else target_mesh
            else:
                cls.logger.error(
                    "Need base_mesh and target_mesh parameters or select 2 meshes"
                )
                return None

        success = Recovery.recover_with_targets(base_mesh, target_mesh)

        if success:
            cls.logger.info("Recovery complete. Loading new animator...")
            return cls.from_existing(base_mesh)

        cls.logger.error("Recovery failed. Check the console for details.")
        return None
