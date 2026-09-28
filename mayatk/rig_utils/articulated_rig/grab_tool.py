# !/usr/bin/python
# coding=utf-8
"""The viewport grab for articulated rigs: press on a rigged part, drag, let go.

What a hand does to the rig in Unity or a headset, an animator does here: the
held point follows the cursor across the camera-facing plane through it, and
the rig is solved by the same ``ptk.ArticulationModel`` the runtimes port --
so the pose keyed in Maya is the pose a grab would have made. The controls
are written, never the joints, so every drag is keyable like any other edit:
one undo step per drag, keyed on release when Auto Key is on.

:class:`ArticulatedRigGrab` is a thin ``draggerContext`` over plain-value
steps (:meth:`~ArticulatedRigGrab.press` / :meth:`~ArticulatedRigGrab.drag` /
:meth:`~ArticulatedRigGrab.release` on world rays), which is what a test
drives -- a dragger needs a user's mouse.
"""

from typing import Any, Optional, Sequence, Tuple

try:
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaUI as omui
    import maya.cmds as cmds
except ImportError:  # the surface imports without Maya (registry, docs, mock tests)
    cmds = om = omui = None

import pythontk as ptk

from mayatk.rig_utils.articulated_rig._articulated_rig import ArticulatedRig


class ArticulatedRigGrab(ptk.LoggingMixin):
    """Grab and drag the parts of articulated rigs in the viewport."""

    #: The dragger context's name; :meth:`activate` rebuilds it each time.
    CONTEXT = "articulatedRigGrabCtx"
    #: How far along the view an orthographic pick ray starts behind its point.
    ORTHO_BACK = 1.0e6
    #: The live tool, kept so its callbacks outlive :meth:`activate`.
    _active: Optional["ArticulatedRigGrab"] = None

    def __init__(self):
        self.rig: Optional[ArticulatedRig] = None
        self.hold: Optional[dict] = None
        self.plane: Optional[Tuple["om.MPoint", "om.MVector"]] = None
        self._chunk = False

    @classmethod
    def activate(cls) -> str:
        """Make the grab the current tool. Returns the context's name."""
        tool = cls()
        if cmds.draggerContext(cls.CONTEXT, exists=True):
            cmds.deleteUI(cls.CONTEXT)
        cmds.draggerContext(
            cls.CONTEXT,
            pressCommand=tool._on_press,
            dragCommand=tool._on_drag,
            releaseCommand=tool._on_release,
            projection="viewPlane",
            cursor="hand",
            undoMode="step",
            space="world",
        )
        cls._active = tool
        cmds.setToolTo(cls.CONTEXT)
        return cls.CONTEXT

    # ============================================================ plain values
    @staticmethod
    def pick(
        origin: Sequence[float], direction: Sequence[float]
    ) -> Optional[Tuple[ArticulatedRig, str, Tuple[float, float, float]]]:
        """The rigged part a world ray hits first: ``(rig, part, point)``, or
        None. Only parts riding a joint count -- the root link stays put."""
        best = None
        source = om.MFloatPoint(*origin)
        ray = om.MFloatVector(*direction).normal()
        for rig in ArticulatedRig.scene_rigs():
            spec = rig.spec
            for joint in spec["joints"]:
                for member in spec["links"][joint["link"]]:
                    part = rig._by_uuid(member["uuid"])
                    if not part:
                        continue
                    shapes = (
                        cmds.listRelatives(
                            part, allDescendents=True, type="mesh", fullPath=True
                        )
                        or []
                    )
                    for shape in shapes:
                        if cmds.getAttr(f"{shape}.intermediateObject"):
                            continue
                        sel = om.MSelectionList()
                        sel.add(shape)
                        hit = om.MFnMesh(sel.getDagPath(0)).closestIntersection(
                            source, ray, om.MSpace.kWorld, 1.0e9, False
                        )
                        # API 2.0 answers a miss with a face index of -1.
                        if not hit or hit[2] < 0:
                            continue
                        if best is None or hit[1] < best[0]:
                            p = hit[0]
                            best = (hit[1], rig, part, (p.x, p.y, p.z))
        return None if best is None else best[1:]

    def press(self, origin: Sequence[float], direction: Sequence[float]) -> bool:
        """Take hold of whatever rigged part the ray hits. Opens the drag's
        undo chunk. Returns whether something was taken."""
        found = self.pick(origin, direction)
        if found is None:
            return False
        rig, part, point = found
        self.rig = rig
        self.hold = rig.grab_begin(part, point)
        self.plane = (om.MPoint(*point), om.MVector(*direction).normal())
        cmds.undoInfo(openChunk=True, chunkName="Articulated Rig: Grab")
        self._chunk = True
        return True

    def drag(
        self, origin: Sequence[float], direction: Sequence[float]
    ) -> Optional[list]:
        """Move the held point to where the ray crosses the camera-facing plane
        through it, and pose the rig there (unkeyed until release)."""
        if not self.hold:
            return None
        anchor, normal = self.plane
        ray = om.MVector(*direction).normal()
        facing = ray * normal
        if abs(facing) < 1.0e-9:
            return None  # a ray along the plane: nowhere to put the point
        t = ((anchor - om.MPoint(*origin)) * normal) / facing
        target = om.MPoint(*origin) + ray * t
        return self.rig.grab_to(self.hold, (target.x, target.y, target.z), key=False)

    def release(self) -> None:
        """Let go: key the rig's controls when Auto Key is on, and close the
        drag's undo chunk."""
        try:
            if self.hold and cmds.autoKeyframe(query=True, state=True):
                slots = self.hold["slots"]
                self.rig.set_state(self.rig.state(slots), key=True, slots=slots)
        finally:
            self.rig = self.hold = self.plane = None
            if self._chunk:
                cmds.undoInfo(closeChunk=True)
                self._chunk = False

    # =============================================================== dragger
    @classmethod
    def ray(
        cls, point: Sequence[float]
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """The world pick ray through a dragger point, from the active view's
        camera: from the eye for a perspective view, along the view for an
        orthographic one."""
        camera = om.MFnCamera(omui.M3dView.active3dView().getCamera())
        view = camera.viewDirection(om.MSpace.kWorld).normal()
        at = om.MPoint(*point)
        if camera.isOrtho():
            origin = at - view * cls.ORTHO_BACK
            return (origin.x, origin.y, origin.z), (view.x, view.y, view.z)
        eye = camera.eyePoint(om.MSpace.kWorld)
        direction = (at - eye).normal()
        return (eye.x, eye.y, eye.z), (direction.x, direction.y, direction.z)

    def _on_press(self, *_: Any) -> None:
        point = cmds.draggerContext(self.CONTEXT, query=True, anchorPoint=True)
        if not self.press(*self.ray(point)):
            om.MGlobal.displayInfo(
                "Articulated Rig Grab: nothing rigged under the cursor."
            )

    def _on_drag(self, *_: Any) -> None:
        if not self.hold:
            return
        point = cmds.draggerContext(self.CONTEXT, query=True, dragPoint=True)
        self.drag(*self.ray(point))
        cmds.refresh(currentView=True)

    def _on_release(self, *_: Any) -> None:
        self.release()
