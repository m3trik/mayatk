# !/usr/bin/python
# coding=utf-8
"""Tube Rig build strategies and the bundle they return.

Each :class:`TubeStrategy` is a thin composition of :class:`TubeRig` step
methods (the same ones the panel's step-by-step buttons call); the engine's
``TubeRig.STRATEGIES`` registry maps a strategy name to its class.
:data:`RIG_MODES` (``TubeRig.MODES``) is the modes tentacle's Tube Rig panel
offers: each one's strategy, option defaults and supported options.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from mayatk.rig_utils.tube_rig._tube_rig import TubeRig


# ======================================================================
# Data Containers
# ======================================================================


@dataclass
class TubeRigBundle:
    rig_group: str
    joints: List[str]
    ik_handle: Optional[str] = None
    curve: Optional[str] = None
    anchors: Optional[List[str]] = None
    controls: Optional[List[str]] = None
    #: Secondary finesse layer (spline builds). Deliberately NOT merged into
    #: ``controls`` — end-control resolution and control-spacing checks read
    #: ``controls[0]/[-1]`` and must keep meaning the DRIVER controls.
    tweak_controls: Optional[List[str]] = None


# ======================================================================
# Rig modes (what the panel offers per strategy)
# ======================================================================


@dataclass
class RigModeConfig:
    """A rig mode: its strategy, its option defaults, and which options it
    supports (an option a strategy implements no system for is not editable).
    Mirrored by blendertk's."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # Which options the mode lets the user change (default: all).
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


#: The modes, in the panel's Mode combo order (the combo saves an index).
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=True,  # hoses bow on compression, not accordion
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=False,  # AnchorStrategy implements no volume system
        squash_editable=False,  # ...nor squash
        auto_bend_editable=False,  # ...nor a mid control to bend
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        # A handful of controls, each spreading its rotation across the
        # joints it owns. One control per joint is technically FK and
        # practically unanimatable: an Auto build puts a joint on every edge
        # loop, so a single curve costs twenty-odd keys in lockstep and one
        # missed control corners the tube.
        num_controls=5,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
    ),
]


# ======================================================================
# Build Strategies (orchestrate TubeRig methods)
# ======================================================================


class TubeStrategy(ABC):
    @abstractmethod
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        pass


class FKChainStrategy(TubeStrategy):
    """Joints → nested FK controls → parametric skin.

    Thin composition of the same ``TubeRig`` step methods the UI's
    step-by-step buttons call — one-click and step-by-step run identical
    code with identical parameters by construction.
    """

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building FK chain: reading the tube's centerline…")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report(f"Building FK chain: creating {num_joints} joints…")
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        rig._report("Building FK chain: creating controls…")
        controls = rig.create_fk_controls(
            joints, size=size, num_controls=kwargs.get("num_controls", 5)
        )
        rig._report("Building FK chain: binding skin…")
        rig.skin_mesh(joints, centerline=centerline)

        rig._report("FK chain build complete.")
        return TubeRigBundle(rig_group=rig.rig_group, joints=joints, controls=controls)


class SplineIKStrategy(TubeStrategy):
    """Joints → spline-IK control rig → parametric skin along the IK curve."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building spline IK: reading the tube's centerline…")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            # Reverse before anything derives from the path so the joints,
            # the IK curve, and the skin solve all share one direction.
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report(f"Building spline IK: creating {num_joints} joints…")
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        rig._report("Building spline IK: creating curve, IK and controls…")
        controls, ik_handle, curve = rig.create_spline_controls(
            joints,
            centerline=centerline,
            size=size,
            num_controls=kwargs.get("num_controls", 3),
            enable_stretch=kwargs.get("enable_stretch", True),
            enable_squash=kwargs.get("enable_squash", True),
            enable_volume=kwargs.get("enable_volume", True),
            enable_twist=kwargs.get("enable_twist", True),
            enable_auto_bend=kwargs.get("enable_auto_bend", False),
            enable_tweaks=kwargs.get("enable_tweaks", True),
        )
        rig._report("Building spline IK: binding skin…")
        rig.skin_mesh(joints, curve=curve)

        rig._report("Spline IK build complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            ik_handle=ik_handle,
            curve=curve,
            controls=controls,
            tweak_controls=rig.tweak_controls,
        )


class AnchorStrategy(TubeStrategy):
    """Two end joints → anchor controls with distance stretch → parametric skin."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building anchor rig: reading the tube's centerline…")
        centerline, _ = rig.resolve_centerline(2, edges=kwargs.get("edges"))
        if len(centerline) < 2:
            raise ValueError("Could not determine centerline")
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report("Building anchor rig: creating end joints…")
        joints = rig.create_anchor_joints(centerline, radius=joint_radius)
        rig._report("Building anchor rig: creating controls…")
        controls = rig.create_anchor_controls(
            joints, size=size, enable_stretch=kwargs.get("enable_stretch", True)
        )
        rig._report("Building anchor rig: binding skin…")
        rig.skin_mesh(joints, centerline=centerline)

        rig._report("Anchor rig build complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            anchors=None,
            controls=controls,
        )
