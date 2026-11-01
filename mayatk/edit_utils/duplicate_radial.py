# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

import re
from typing import List, Dict, Tuple, Union
import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    pass
# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk import DisplayUtils
from mayatk import XformUtils
from mayatk.edit_utils.naming._naming import Naming


class DuplicateRadial(ptk.LoggingMixin):
    @staticmethod
    def duplicate_radial(
        objects: List[str],
        num_copies: int,
        start_angle: float = 0,
        end_angle: float = 360,
        weight_bias: float = 0.5,
        weight_curve: float = 0.5,
        rotate_axis: str = "y",
        offset: Tuple[float, float, float] = (0, 0, 0),
        translate: Tuple[float, float, float] = (0, 0, 0),
        rotate: Tuple[float, float, float] = (0, 0, 0),
        scale: Tuple[float, float, float] = (1, 1, 1),
        pivot: Union[str, Tuple[float, float, float]] = "object",
        keep_original: bool = False,
        instance: bool = False,
        combine: bool = False,
        suffix: bool = True,
    ) -> Dict[str, List[str]]:
        """Duplicate objects in a radial pattern.

        Parameters:
            objects (List[str]): List of objects to duplicate.
            num_copies (int): Number of copies to create.
            start_angle (float): Starting angle for duplication.
            end_angle (float): Ending angle for duplication.
            weight_bias (float): Bias for the weight curve.
            weight_curve (float): Weight curve value.
            rotate_axis (str): Axis of rotation ('x', 'y', or 'z').
            offset (Tuple[float, float, float]): Offset for the pivot point.
            translate (Tuple[float, float, float]): Translation vector.
            rotate (Tuple[float, float, float]): Rotation vector.
            scale (Tuple[float, float, float]): Scale vector.
            pivot (Union[str, Tuple[float, float, float]]): Pivot point type or position.
            keep_original (bool): Whether to keep the original object.
            instance (bool): Whether to create instances of the duplicates.
            combine (bool): Whether to combine the duplicates into one mesh.
            suffix (bool): Whether to add a suffix to the duplicated objects.

        Returns:
            Dict[str, List[str]]: Mapping of original objects to their duplicates.

        Raises:
            ValueError: If invalid parameters are provided.
        """
        DuplicateRadial._validate_inputs(rotate_axis, weight_bias, weight_curve)
        originals_to_copies = {}

        for orig in CoreUtils.as_strings(objects):
            # Resolve to a full path for the scene ops below — especially
            # _cleanup_original's delete when keep_original=False — so an
            # ambiguous short name can't hit the wrong same-named object. The
            # result stays keyed by the caller's original reference.
            node = (cmds.ls(orig, long=True) or [orig])[0]
            print(f"\n[duplicate radial] Processing node: {node} ..")

            driven_group, driven_node, pivot_pos = (
                DuplicateRadial._prepare_driven_group(
                    node, rotate, scale, translate, offset, pivot, instance
                )
            )

            copies = DuplicateRadial._create_and_transform_instances(
                driven_group,
                num_copies,
                rotate_axis,
                start_angle,
                end_angle,
                translate,
                weight_bias,
                weight_curve,
                instance,
            )

            cmds.delete(driven_group)
            DuplicateRadial._cleanup_original(node, keep_original)

            finalized = DuplicateRadial._finalize_output(
                node, copies, keep_original, combine
            )

            if suffix:
                # The suffix pass RENAMES the copies — keep the returned
                # final names, or the mapping goes stale (regroup_copies'
                # objExists guard then silently no-ops the whole commit).
                finalized = (
                    Naming.append_location_based_suffix(
                        finalized, first_obj_as_ref=True, alphabetical=True
                    )
                    or finalized
                )

            originals_to_copies[orig] = finalized
            print(
                f"[duplicate radial] [{node}] Created {len(finalized)} total instances"
            )

        return originals_to_copies

    @classmethod
    def _finalize_output(
        cls,
        node: str,
        copies: List[str],
        keep_original: bool,
        combine: bool,
    ) -> List[str]:
        if combine:
            combined = cmds.polyUnite(copies, ch=False, mergeUVSets=True)[0]
            combined = cmds.rename(combined, f"{node}_radialCombined")
            transforms_under = cmds.listRelatives(
                combined, shapes=True, noIntermediate=True, type="transform"
            )
            if transforms_under:
                cmds.delete(transforms_under)
            cls.logger.debug(f"Combined all instances into: {combined}")
            # polyUnite consumed the per-copy members added at the copy loop --
            # the united result is a new node nothing has registered yet.
            DisplayUtils.add_to_isolation_set(combined)
            return [combined]

        clean_copies = []
        for copy in copies:
            parent = cmds.listRelatives(copy, parent=True, fullPath=True)
            if parent:
                copy = cmds.parent(copy, world=True)[0]
                if not keep_original:
                    cmds.delete(parent[0])
            clean_copies.append(copy)

        group_name = f"{node}_radialGroup"
        container_group = cmds.group(clean_copies, name=group_name)
        cls.logger.debug(f"Grouped all instances under: {container_group}")

        # Re-resolve after grouping — the pre-group names (world paths like
        # '|copy1') went stale the moment the copies were reparented.
        return (
            cmds.listRelatives(
                container_group, children=True, fullPath=True, type="transform"
            )
            or clean_copies
        )

    @classmethod
    def _cleanup_original(cls, node: str, keep_original: bool) -> None:
        if not keep_original:
            cls.logger.debug(f"Deleting original node: {node}")
            cmds.delete(node)

    @classmethod
    def _prepare_driven_group(
        cls,
        node: str,
        rotate: Tuple[float, float, float],
        scale: Tuple[float, float, float],
        translate: Tuple[float, float, float],
        offset: Tuple[float, float, float],
        pivot: Union[str, Tuple[float, float, float]],
        instance: bool = False,
    ) -> Tuple[str, str, Tuple[float, float, float]]:
        driven_node = cmds.duplicate(node, rr=True, instanceLeaf=instance)[0]
        cls.logger.debug(f"[{node}] Duplicated original → driven node: {driven_node}")

        cls._apply_initial_transformations(driven_node, rotate, scale, translate)

        pivot_pos = XformUtils.get_operation_axis_pos(driven_node, pivot)
        cls.logger.debug(f"[{driven_node}] Rotation pivot (world-space): {pivot_pos}")

        group_node = cmds.group(em=True)
        cmds.xform(group_node, ws=True, t=(0, 0, 0))

        pivot_offset_pos = [pivot_pos[i] + offset[i] for i in range(3)]
        cls.logger.debug(f"Setting rotate and scale pivot to: {pivot_offset_pos}")
        cmds.xform(group_node, ws=True, rp=pivot_offset_pos, sp=pivot_offset_pos)

        driven_node = cmds.parent(driven_node, group_node)[0]
        cls.logger.debug(f"[{driven_node}] Wrapped in group: {group_node}")

        return group_node, driven_node, pivot_pos

    @staticmethod
    def _validate_inputs(
        rotate_axis: str, weight_bias: float, weight_curve: float
    ) -> None:
        if rotate_axis not in ["x", "y", "z"]:
            raise ValueError("Invalid rotation axis, expected 'x', 'y', or 'z'")
        if not (0.0 <= weight_bias <= 1.0):
            raise ValueError("weight_bias must be between 0.0 and 1.0")
        if not (0.0 <= weight_curve <= 1.0):
            raise ValueError("weight_curve must be between 0.0 and 1.0")

    @classmethod
    def _apply_initial_transformations(
        cls,
        node: str,
        rotate: Tuple[float, float, float],
        scale: Tuple[float, float, float],
        translate: Tuple[float, float, float],
    ) -> None:
        cls.logger.debug(f"Applying initial rotation to {node}: {rotate}")
        cmds.rotate(rotate[0], rotate[1], rotate[2], node, r=True)
        cls.logger.debug(f"Applying scale to {node}: {scale}")
        cmds.scale(scale[0], scale[1], scale[2], node, relative=True)
        cls.logger.debug(f"Applying translation to {node}: {translate}")
        cmds.move(translate[0], translate[1], translate[2], node, relative=True)

    @classmethod
    def _create_and_transform_instances(
        cls,
        group_node: str,
        num_copies: int,
        rotate_axis: str,
        start_angle: float,
        end_angle: float,
        translate: Tuple[float, float, float],
        weight_bias: float,
        weight_curve: float,
        instance: bool,
    ) -> List[str]:
        rotation_index = {"x": 0, "y": 1, "z": 2}[rotate_axis]
        total_rotation = end_angle - start_angle
        weight_factor = 2 * abs(weight_bias - 0.5)

        # Spacing divisor: an arc keeps both endpoints (num_copies - 1), but a
        # whole revolution makes the end angle coincide with the start, so the
        # inclusive endpoint stacks the last copy on the first. Drop the shared
        # endpoint (num_copies) when the sweep is a multiple of 360. The min()
        # is the distance to the nearest multiple of 360 (handles a span that
        # falls just shy of one from float error, e.g. 359.9999999).
        remainder = abs(total_rotation) % 360.0
        is_full_revolution = (
            abs(total_rotation) > 1e-6 and min(remainder, 360.0 - remainder) < 1e-6
        )
        span_divisor = num_copies if is_full_revolution else num_copies - 1
        copies = []

        for i in range(num_copies):
            if instance:
                copy_group = cmds.instance(group_node, leaf=True)[0]
            else:
                copy_group = cmds.duplicate(group_node, rr=True)[0]
            children = (
                cmds.listRelatives(copy_group, children=True, fullPath=True) or []
            )
            copy = children[0]
            copies.append(copy)
            cls.logger.debug(
                f"Creating {'instance' if instance else 'duplicate'} {i}: {copy}"
            )

            x = i / span_divisor if num_copies > 1 else 0.0
            # Clamp the denominator so weight_curve == 1.0 (a valid input per
            # _validate_inputs, and the spinbox max) stays finite instead of
            # raising ZeroDivisionError; the clamp approximates the sharp-curve limit.
            exponent = 1.0 / (1.0 - min(weight_curve, 0.9999))
            curve_value = x**exponent if weight_bias >= 0.5 else 1 - (1 - x) ** exponent

            f_x = (1 - weight_factor) * x + weight_factor * curve_value
            current_rotation = [0, 0, 0]
            current_rotation[rotation_index] = start_angle + total_rotation * f_x
            cls.logger.debug(f"Rotation factor for instance {i}: {f_x}")
            cls.logger.debug(f"Applying rotation to instance {i}: {current_rotation}")
            cmds.rotate(
                current_rotation[0],
                current_rotation[1],
                current_rotation[2],
                copy_group,
                r=True,
                os=True,
                fo=True,
            )

            t = [translate[j] * f_x for j in range(3)]
            cls.logger.debug(f"Applying translation to instance {i}: {t}")
            cmds.move(t[0], t[1], t[2], copy_group)
            DisplayUtils.add_to_isolation_set(copy)
            cls.logger.debug(
                f"{'Instance' if instance else 'Duplicate'} {i} added to isolation set: {copy}"
            )

        return copies

    @staticmethod
    def regroup_copies(copies: Dict[str, List[str]]) -> None:
        """Regroup each source's committed copies under a fresh ``*_array`` group.

        The finalize step after :meth:`duplicate_radial` commits: that parents
        every copy under ONE shared radial group, and the ``*_array`` group
        takes its place in the hierarchy. All copies are unparented in a single
        call and the shared group is deleted only once it is empty -- deleting
        it while sibling copies were still inside destroyed them. One undo
        chunk.

        Parameters:
            copies: :meth:`duplicate_radial`'s return, ``{source: [copies]}``.
        """
        with CoreUtils.undo_chunk():
            for group_copies in copies.values():
                group_copies = [c for c in group_copies if cmds.objExists(c)]
                if not group_copies:
                    continue

                first_obj_name = CoreUtils.short_name(group_copies[0])
                name = re.sub(r"\d+$", "", first_obj_name) + "_array"
                unique_name = Naming.generate_unique_name(name)

                # The shared group's parent (if any) hosts the new group.
                shared_group = NodeUtils.get_parent(group_copies[0])
                original_parent = (
                    NodeUtils.get_parent(shared_group) if shared_group else None
                )

                if shared_group:
                    group_copies = [
                        str(c) for c in cmds.parent(group_copies, world=True)
                    ]
                    if not cmds.listRelatives(shared_group, children=True):
                        cmds.delete(shared_group)

                new_group = cmds.group(group_copies, n=unique_name)

                if original_parent is not None:
                    cmds.parent(new_group, original_parent)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
