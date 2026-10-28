# !/usr/bin/python
# coding=utf-8
# from this package:
try:
    import maya.cmds as cmds
except Exception:
    cmds = None
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils._edit_utils import EditUtils


class CutOnAxis:
    @staticmethod
    @CoreUtils.undoable
    def perform_cut_on_axis(
        objects,
        axis="-x",
        cuts=0,
        cut_offset=0,
        cut_spacing=0.0,
        distribution="linear",
        weight_bias=0.5,
        weight_curve=2.0,
        delete=False,
        mirror=False,
        pivot="manip",
        axis_frame=None,
    ):
        """Iterates over provided objects and performs cut or delete operations based on the axis specified.

        Parameters:
            objects (list): The list of mesh objects to be processed.
            axis (str): The axis to cut or delete along ('x', '-x', 'y', '-y', 'z', '-z'). Default is '-x'.
            cuts (int): The number of cuts to make. Default is 0.
            cut_offset (float): Offset amount from the center for the cut. Default is 0.
            cut_spacing (float): Distance between adjacent cuts. ``0`` (default)
                auto-fills the axis; ``>0`` fixes the span between cuts.
            distribution (str): Progression curve distributing the cuts across
                the span ("linear", "ease_in", "ease_out", "weighted", …).
            weight_bias (float): Bias for the "weighted" distribution (0..1).
            weight_curve (float): Curve strength for non-linear distributions.
            delete (bool): If True, delete the faces on the specified axis. Default is False.
            mirror (bool): After deleting, mirror the object(s).
            pivot (str): Pivot type string ("manip", "object", "world", "center"). Default is "manip".
            axis_frame (str): Which axes to cut along -- ``None``/``"auto"``
                (default) derives it from the pivot, ``"world"`` forces world
                axes, a frame name forces that frame. See
                ``EditUtils.cut_along_axis``.
        """
        if cuts:
            axis = axis.lower()  # Assure lower case.

            EditUtils.cut_along_axis(
                objects,
                axis=axis,
                pivot=pivot,
                amount=cuts,
                mirror=mirror,
                offset=cut_offset,
                spacing=cut_spacing,
                distribution=distribution,
                weight_bias=weight_bias,
                weight_curve=weight_curve,
                delete=delete,
                axis_frame=axis_frame,
            )

            cmds.select(objects)


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
