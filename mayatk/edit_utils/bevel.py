# !/usr/bin/python
# coding=utf-8
# from this package:
import maya.cmds as cmds
from uitk.widgets.mixins.tooltip_mixin import TooltipFormat
from mayatk.core_utils.preview import Preview, OperationError
from mayatk.core_utils.components import Components


class Bevel:
    @staticmethod
    def bevel(
        edges,
        width=0.5,
        segments=1,
        autoFit=True,
        depth=1,
        mitering=0,
        miterAlong=0,
        chamfer=True,
        worldSpace=True,
        smoothingAngle=30,
        fillNgons=True,
        mergeVertices=True,
        mergeVertexTolerance=0.0001,
        miteringAngle=180,
        angleTolerance=180,
    ):
        """Bevels the given edges with highly customizable options for topology,
        bevel width, segments, and other attributes. Designed for production use,
        offering fine-grained control over the bevel operation.

        Parameters:
            edges (str/obj/list): List of edges to bevel
            width (float): Bevel width as a fraction between 0 and 1
            segments (int): Number of segments for the bevel
            autoFit (bool): Whether to compute a smooth roundness for new facets
            depth (): Depth of the bevel
            mitering (): Controls the topology at corners
            miterAlong (): Direction to offset new vertices
            chamfer (bool): Whether to smooth out the surface at bevels
            worldSpace (bool): Whether to use world space or object space for geometrical values
            smoothingAngle (): Angle for creating new hard edges
            fillNgons (bool): Whether to subdivide new faces with more than 4 edges
            mergeVertices (bool): Whether to merge vertices within a tolerance
            mergeVertexTolerance (float): Tolerance within which to merge vertices
            miteringAngle (): Miter faces that have angles less than this value
            angleTolerance (): Angular tolerance for creation of extra triangles

        Raises:
            OperationError: If polyBevel3 silently produced no topology change
                on one or more objects (it gives up without raising when the
                width/segment combination can't fit the adjacent geometry).
                Every object is still processed first, so healthy meshes in a
                multi-object selection keep their bevel.
        """

        mapped_edges = Components.map_components_to_objects(edges)

        def _counts(obj):
            return (
                cmds.polyEvaluate(obj, vertex=True),
                cmds.polyEvaluate(obj, edge=True),
                cmds.polyEvaluate(obj, face=True),
            )

        unchanged = []
        for obj, obj_edges in mapped_edges.items():
            before = _counts(obj)
            cmds.polyBevel3(
                obj_edges,
                fraction=width,
                segments=segments,
                autoFit=autoFit,
                depth=depth,
                mitering=mitering,
                miterAlong=miterAlong,
                chamfer=chamfer,
                worldSpace=worldSpace,
                smoothingAngle=smoothingAngle,
                fillNgons=fillNgons,
                mergeVertices=mergeVertices,
                mergeVertexTolerance=mergeVertexTolerance,
                miteringAngle=miteringAngle,
                angleTolerance=angleTolerance,
                offsetAsFraction=True,
                constructionHistory=True,
            )
            # polyBevel3 "succeeds" but changes nothing when the bevel can't
            # fit (thin/tiny adjacent faces, or width x segments overflowing
            # the neighborhood). Inside a Preview refresh that silence reads
            # as "my bevel was undone" and invites a work-destroying Ctrl+Z --
            # surface it as a real failure instead.
            if _counts(obj) == before:
                unchanged.append(obj)

        if unchanged:
            raise OperationError(
                f"Bevel produced no change on: <b>{', '.join(unchanged)}</b>.",
                causes=[
                    "The <b>Width</b> fraction is too large for the faces "
                    "adjacent to the selected edges at this <b>Segments</b> "
                    "count — polyBevel3 gives up silently instead of erroring.",
                    "Lower <b>Width</b> or <b>Segments</b>, or select edges "
                    "with more room around them.",
                ],
                title="Bevel failed",
            )


class BevelSlots:
    # polyBevel3 mutates the mesh in place with construction history, rewriting
    # its edge topology. Without a geometry snapshot the preview's node-diff
    # rollback leaves the bevel baked in (and drops the material), so each value
    # change stacks another bevel; worse, the captured edge indices (e.g. e[5])
    # shift after the first bevel, so the next refresh re-bevels *different*
    # edges. PRESERVE_GEOMETRY makes Preview snapshot the owning mesh and restore
    # it (topology + material) on rollback, so every refresh re-bevels the same
    # captured edges from a pristine mesh. Mirrors Bridge / Cut On Axis.
    PRESERVE_GEOMETRY = True

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bevel

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            TooltipFormat.fmt(
                title="Bevel",
                body="Add chamfer bevels to selected polygon edges.",
                steps=[
                    "Select one or more polygon edges.",
                    "Set <b>Width</b> (0–1 fraction) and <b>Segments</b> (1+).",
                    "Toggle <b>Preview</b> to iterate non-destructively, "
                    "or press <b>Bevel</b> to commit.",
                ],
                notes=[
                    "Mitering, smoothing, and vertex-merge tolerance use the "
                    "<i>Bevel.bevel</i> defaults (auto-fit, 30° smoothing). "
                    "Edit the slot if you need finer control.",
                ],
            )
        )

    def perform_operation(self, objects, contract):
        width = self.ui.s000.value()
        segments = self.ui.s001.value()
        Bevel.bevel(objects, width, segments)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("bevel", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
