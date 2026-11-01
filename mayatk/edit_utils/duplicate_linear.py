# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    pass
import math
import pythontk as ptk

# from this package:
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.xform_utils._xform_utils import XformUtils


class DuplicateLinear:
    @staticmethod
    def duplicate_linear(
        objects,
        num_copies,
        translate=(0, 0, 0),
        rotate=(0, 0, 0),
        scale=(1, 1, 1),
        weight_bias=0.5,
        weight_curve=4,
        pivot="object",
        calculation_mode="weighted",
        instance=True,
    ):
        originals_to_copies = {}

        for orig in CoreUtils.as_strings(objects):
            # Resolve to a full path for the scene ops below (instance/duplicate/
            # xform) so an ambiguous short name can't operate on the wrong
            # same-named object; the result stays keyed by the caller's ref.
            node = (cmds.ls(orig, long=True) or [orig])[0]
            copies = []

            # Get the pivot matrix (Orientation + Position) using the centralized utility
            mat_pivot = XformUtils.get_operation_axis_matrix(node, pivot)
            mat_pivot_inv = mat_pivot.inverse()

            for i in range(num_copies):
                if instance:
                    dup = cmds.instance(node)[0]
                else:
                    dup = cmds.duplicate(node, rr=True)[0]

                # Calculate the transformation factor using the selected method
                f_x = ptk.ProgressionCurves.calculate_progression_factor(
                    i, num_copies, weight_bias, weight_curve, calculation_mode
                )

                # Calculate transformations
                translation_vector = [translate[j] * f_x for j in range(3)]
                rotation_values = [rotate[j] * f_x for j in range(3)]
                scale_factors = [(abs(s) ** f_x) * (-1 if s < 0 else 1) for s in scale]

                # 1. Local Scale
                m_dup = om.MMatrix(
                    cmds.xform(dup, query=True, worldSpace=True, matrix=True)
                )
                tm_dup = om.MTransformationMatrix(m_dup)

                current_scale = tm_dup.scale(om.MSpace.kObject)
                new_scale = [current_scale[j] * scale_factors[j] for j in range(3)]
                tm_dup.setScale(new_scale, om.MSpace.kObject)

                m_scaled = tm_dup.asMatrix()

                # 2. Rotate around Pivot
                euler = om.MEulerRotation(
                    math.radians(rotation_values[0]),
                    math.radians(rotation_values[1]),
                    math.radians(rotation_values[2]),
                    om.MEulerRotation.kXYZ,
                )
                mat_rot = euler.asMatrix()

                # M_rot = M_piv_inv * R * M_piv
                # This orbits the object around the pivot frame (Pos + Ori)
                mat_orbit = mat_pivot_inv * mat_rot * mat_pivot

                m_rotated = m_scaled * mat_orbit

                # 3. Apply Translation (World Space, but respecting Pivot Orientation)
                # To support translating along the Pivot's axis (e.g. Manipulator Axis), we must rotate the translation vector.
                vec_trans_local = om.MVector(
                    translation_vector[0], translation_vector[1], translation_vector[2]
                )
                # Transforming MVector by MMatrix rotates/scales it but ignores translation (w=0)
                vec_trans_rotated = vec_trans_local * mat_pivot

                mat_trans_list = [
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
                    vec_trans_rotated.x,
                    vec_trans_rotated.y,
                    vec_trans_rotated.z,
                    1.0,
                ]
                mat_trans = om.MMatrix(mat_trans_list)

                m_final = m_rotated * mat_trans

                cmds.xform(dup, matrix=list(m_final), worldSpace=True)

                copies.append(dup)

            # One batched add per original rather than one per copy: the helper
            # resolves the isolated panels and opens an undo chunk per call.
            DisplayUtils.add_to_isolation_set(copies)
            originals_to_copies[orig] = copies

        return originals_to_copies


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
