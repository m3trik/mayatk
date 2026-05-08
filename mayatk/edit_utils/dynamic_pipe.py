try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from typing import List, Optional, Sequence


class DynamicPipe:
    """Build a pipe-style mesh by lofting NURBS circles parented to a chain of locators.

    Each input locator drives a cross-section circle; when the user moves a
    locator in the scene the circle (and any lofted surface that includes it)
    follows. Optional in-between locators are inserted by linear interpolation
    so the curve resolution can be increased without manually placing extra
    nodes.

    Public attributes:
        locators (list[str]): Final ordered list of driving locators
            (originals + any inserted in-betweens).
        circles (list[str]): Per-locator NURBS-circle transforms, parented
            under their corresponding locator.
        curve (str): NURBS curve transform fitted through the locators.
        pipe_segments (list[str]): Loft surface transforms produced by
            ``create_pipe_geometry``.
    """

    def __init__(
        self,
        locators: Sequence[str],
        num_inbetween: int = 0,
        radius: float = 1.0,
        normal: Sequence[float] = (1, 0, 0),
    ):
        if cmds is None:
            raise RuntimeError("DynamicPipe requires maya.cmds.")
        if len(locators) < 2:
            raise ValueError("At least two locators are required.")

        self.radius = float(radius)
        self.normal = tuple(normal)
        self.locators: List[str] = self._with_inbetweens(list(locators), num_inbetween)
        self.curve: str = self._build_curve(self.locators)
        self.circles: List[str] = self._build_circles(self.locators)
        self.pipe_segments: List[str] = []

    # ------------------------------------------------------------------ build

    @staticmethod
    def _world_pos(node: str) -> List[float]:
        return cmds.xform(node, query=True, worldSpace=True, translation=True)

    def _with_inbetweens(self, base: List[str], n: int) -> List[str]:
        """Insert ``n`` linearly-interpolated locators between each pair."""
        if n <= 0:
            return base
        result: List[str] = []
        for i, loc in enumerate(base):
            result.append(loc)
            if i == len(base) - 1:
                break
            start = self._world_pos(loc)
            end = self._world_pos(base[i + 1])
            for k in range(1, n + 1):
                t = k / float(n + 1)
                pos = [start[a] + t * (end[a] - start[a]) for a in range(3)]
                inserted = cmds.spaceLocator()[0]
                cmds.xform(inserted, worldSpace=True, translation=pos)
                result.append(inserted)
        return result

    def _build_curve(self, locators: Sequence[str]) -> str:
        points = [self._world_pos(loc) for loc in locators]
        # Degree must be < num points; fall back to linear for the 2-point case.
        degree = 3 if len(points) >= 4 else max(1, len(points) - 1)
        return cmds.curve(point=points, degree=degree)

    def _build_circles(self, locators: Sequence[str]) -> List[str]:
        circles: List[str] = []
        for loc in locators:
            circle = cmds.circle(
                normal=self.normal,
                radius=self.radius,
                constructionHistory=False,
            )[0]
            cmds.matchTransform(circle, loc)
            cmds.parent(circle, loc)
            # Make the cross-section non-selectable in the viewport so the
            # user can keep grabbing the locator instead of the circle.
            cmds.setAttr(f"{circle}.overrideEnabled", 1)
            cmds.setAttr(f"{circle}.overrideDisplayType", 2)  # 2 = reference
            circles.append(circle)
        return circles

    # ----------------------------------------------------------------- public

    def create_pipe_geometry(
        self, segments_to_loft: Optional[Sequence[int]] = None
    ) -> List[str]:
        """Loft consecutive circle pairs to produce pipe segments.

        Parameters:
            segments_to_loft: Indices of circle pairs to loft. ``i`` lofts
                ``circles[i]`` to ``circles[i+1]``. ``None`` lofts every pair.

        Returns:
            The resulting loft transform names.
        """
        if segments_to_loft is None:
            segments_to_loft = range(len(self.circles) - 1)
        else:
            for i in segments_to_loft:
                if not isinstance(i, int):
                    raise ValueError(
                        "segments_to_loft must be a sequence of integers."
                    )

        new_segments: List[str] = []
        for i in segments_to_loft:
            if i < 0 or i >= len(self.circles) - 1:
                continue
            pair = self.circles[i : i + 2]
            surface = cmds.loft(
                pair,
                constructionHistory=True,
                uniform=True,
                close=False,
                autoReverse=True,
                degree=3,
                sectionSpans=1,
                range=False,
                polygon=0,
                reverseSurfaceNormals=True,
            )[0]
            new_segments.append(surface)

        self.pipe_segments.extend(new_segments)
        return new_segments


class DynamicPipeSlots:
    """Switchboard slot wiring for the dynamic_pipe UI."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.dynamic_pipe
        self.pipe: Optional[DynamicPipe] = None

    def header_init(self, widget):
        """Configure header menu with tool instructions."""
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Dynamic Pipe — Create pipe geometry from locators.\n\n"
                "• Place locators to define the pipe path.\n"
                "• Select locators in order, then press Initialize Pipe.\n"
                "• Each locator drives a cross-section circle;\n"
                "  consecutive circles are lofted into pipe segments."
            ),
        )

    def b000(self):
        """Initialize Pipe — build pipe from the current ordered selection."""
        locators = cmds.ls(orderedSelection=True, exactType="transform") or []
        if len(locators) < 2:
            self.sb.message_box(
                "Select at least two transforms (locators) in order, "
                "then press Initialize Pipe."
            )
            return

        cmds.undoInfo(openChunk=True)
        try:
            self.pipe = DynamicPipe(locators)
            self.pipe.create_pipe_geometry()
        finally:
            cmds.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("dynamic_pipe", reload=True)
    ui.show(pos="screen", app_exec=True)
