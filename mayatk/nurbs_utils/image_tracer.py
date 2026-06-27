# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

import os
from typing import List, Optional, Union

from uitk.widgets.mixins.tooltip_mixin import fmt

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = None
    mel = None

# From this package:
from mayatk.core_utils._core_utils import CoreUtils


class BluePencilMixin(object):
    """Mixin for handling Blue Pencil operations."""

    def get_blue_pencil_curves(self):
        """Converts active Blue Pencil strokes to NURBS curves."""
        if cmds is None:
            return []

        # Ensure plugin is loaded
        try:
            if not cmds.pluginInfo("bluePencil", query=True, loaded=True):
                cmds.loadPlugin("bluePencil", quiet=True)
        except Exception:
            pass

        if cv2 is None:
            cmds.warning(
                "OpenCV (cv2) is required for Blue Pencil tracing in this version of Maya."
            )
            return []

        import tempfile
        import zipfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "bp_export.zip").replace("\\", "/")

        created_curves = []

        try:
            # Export Archive
            # Note: This exports the current frame/view to a zip file
            cmds.bluePencilFrame(exportArchive=zip_path)

            if not os.path.exists(zip_path):
                cmds.warning("Blue Pencil export failed: Archive not created.")
                return []

            # Extract
            extract_dir = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            # Find Images
            png_files = []
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(".png"):
                        png_files.append(os.path.join(root, file))

            if not png_files:
                cmds.warning("No Blue Pencil frames found in export.")
                return []

            # Trace each image
            # Save state
            original_image_path = self.image_path
            original_use_bp = self.use_blue_pencil

            try:
                self.use_blue_pencil = False
                for png_file in png_files:
                    self.image_path = png_file
                    curves = self.trace_curves()
                    created_curves.extend(curves)
            finally:
                self.image_path = original_image_path
                self.use_blue_pencil = original_use_bp

        except Exception as e:
            cmds.warning("Failed to trace Blue Pencil frames: {}".format(e))
        finally:
            # Cleanup
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

        # Return the created curve transforms
        return created_curves


class ImageTracer(BluePencilMixin):
    """A class to trace images into Maya NURBS curves and generate geometry."""

    def __init__(
        self,
        image_path: Optional[str] = None,
        scale: float = 0.1,
        simplify: Optional[float] = 1.0,
        use_blue_pencil: bool = False,
    ) -> None:
        self.image_path = image_path
        self.scale = scale
        self.simplify = simplify
        self.use_blue_pencil = use_blue_pencil
        self._check_dependencies()

    def _check_dependencies(self):
        if cmds is None:
            raise ImportError("maya.cmds is not available. Run this inside Maya.")

        if self.use_blue_pencil:
            return

        if cv2 is None:
            raise ImportError("OpenCV (cv2) is not installed.")
        if not self.image_path or not os.path.exists(self.image_path):
            raise FileNotFoundError("Image not found: {}".format(self.image_path))

    @CoreUtils.undoable
    def trace_curves(self) -> List[str]:
        """Traces the image and returns a list of created NURBS curves."""
        if self.use_blue_pencil:
            return self.get_blue_pencil_curves()

        img = cv2.imread(self.image_path)
        if img is None:
            raise ValueError("Failed to read image: {}".format(self.image_path))

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ret, thresh = cv2.threshold(gray, 127, 255, 0)
        contours, hierarchy = cv2.findContours(
            thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )

        created_curves = []
        for contour in contours:
            if self.simplify is not None:
                epsilon = self.simplify
                contour = cv2.approxPolyDP(contour, epsilon, True)

            points = []
            for point in contour:
                x = point[0][0] * self.scale
                y = point[0][1] * self.scale
                points.append((x, 0, y))

            if len(points) > 2:
                points.append(points[0])  # Close loop
                try:
                    curve = cmds.curve(p=points, d=1)
                    # Explicitly close the curve to ensure planarSrf works
                    cmds.closeCurve(curve, ch=0, ps=0, rpo=1, bb=0.5, bki=0, p=0.1)
                    created_curves.append(curve)
                except Exception as e:
                    print("Failed to create curve: {}".format(e))

        return created_curves

    @CoreUtils.undoable
    def create_mesh(
        self,
        curves: Optional[List[str]] = None,
        combine: bool = True,
        name: str = "traced_mesh",
        group_output: bool = True,
    ) -> Union[str, List[str]]:
        """Creates a polygon mesh from the traced curves (positive space)."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return []

        # Grouping logic
        parent_grp = None
        if group_output:
            parent_grp = cmds.group(em=True, name="{}_grp".format(name))
            curves_grp = cmds.group(curves, name="curves_grp")
            cmds.parent(curves_grp, parent_grp)

        # Planar Surface
        planar_surfaces = cmds.planarSrf(
            curves, d=3, keepOutside=0, tolerance=0.01, polygon=0
        ) or []
        nurbs_surfaces = [x for x in planar_surfaces if cmds.nodeType(x) == "transform"]

        if group_output and nurbs_surfaces:
            srf_grp = cmds.group(nurbs_surfaces, name="nurbs_surfaces_grp")
            # cmds.parent returns the new full path; the input ``srf_grp``
            # path becomes stale after re-parenting.
            srf_grp = cmds.parent(srf_grp, parent_grp)[0]
            cmds.hide(srf_grp)

        # Convert to Poly
        polygons = []
        for srf in nurbs_surfaces:
            poly = cmds.nurbsToPoly(
                srf, format=1, uType=3, vType=3, uNumber=1, vNumber=1, mnd=1, ch=1
            )
            if poly:
                polygons.append(poly[0])

        if group_output and combine and len(polygons) > 1:
            inter_poly_grp = cmds.group(polygons, name="intermediate_polygons_grp")
            inter_poly_grp = cmds.parent(inter_poly_grp, parent_grp)[0]
            cmds.hide(inter_poly_grp)

        result = polygons
        if combine and len(polygons) > 1:
            result = cmds.polyUnite(polygons, ch=1, mergeUVSets=1, name=name)[0]
        elif len(polygons) == 1:
            result = polygons[0]
            result = cmds.rename(result, name)

        if group_output:
            if isinstance(result, list):
                for r in result:
                    cmds.parent(r, parent_grp)
            else:
                cmds.parent(result, parent_grp)
            return parent_grp

        return result

    @CoreUtils.undoable
    def create_negative_space_mesh(
        self,
        curves: Optional[List[str]] = None,
        margin_scale: float = 0.1,
        name: str = "negative_space_mesh",
        group_output: bool = True,
    ) -> Optional[str]:
        """Creates a mesh representing the negative space (plane with holes)."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return None

        parent_grp = None
        if group_output:
            parent_grp = cmds.group(em=True, name="{}_grp".format(name))
            curves_grp = cmds.group(curves, name="curves_grp")
            cmds.parent(curves_grp, parent_grp)

        # Calculate bounds
        min_x = min_z = float("inf")
        max_x = max_z = float("-inf")
        for curve in curves:
            bb = cmds.xform(curve, q=True, bb=True, ws=True)
            min_x = min(min_x, bb[0])
            max_x = max(max_x, bb[3])
            min_z = min(min_z, bb[2])
            max_z = max(max_z, bb[5])

        margin = max((max_x - min_x), (max_z - min_z)) * margin_scale
        min_x -= margin
        max_x += margin
        min_z -= margin
        max_z += margin

        boundary_curve = cmds.curve(
            d=1,
            p=[
                (min_x, 0, min_z),
                (max_x, 0, min_z),
                (max_x, 0, max_z),
                (min_x, 0, max_z),
                (min_x, 0, min_z),
            ],
            name="boundary_curve",
        )

        if group_output:
            cmds.parent(boundary_curve, parent_grp)

        all_curves = [boundary_curve] + list(curves)
        planar_surfaces = cmds.planarSrf(
            all_curves, d=3, keepOutside=0, tolerance=0.01, polygon=0
        ) or []
        nurbs_surfaces = [x for x in planar_surfaces if cmds.nodeType(x) == "transform"]

        if group_output and nurbs_surfaces:
            srf_grp = cmds.group(nurbs_surfaces, name="nurbs_surfaces_grp")
            # cmds.parent returns the new full path; the input ``srf_grp``
            # path becomes stale after re-parenting.
            srf_grp = cmds.parent(srf_grp, parent_grp)[0]
            cmds.hide(srf_grp)

        polygons = []
        for srf in nurbs_surfaces:
            poly = cmds.nurbsToPoly(
                srf, format=1, uType=3, vType=3, uNumber=1, vNumber=1, mnd=1, ch=1
            )
            if poly:
                polygons.append(poly[0])

        if group_output and len(polygons) > 1:
            inter_poly_grp = cmds.group(polygons, name="intermediate_polygons_grp")
            inter_poly_grp = cmds.parent(inter_poly_grp, parent_grp)[0]
            cmds.hide(inter_poly_grp)

        if len(polygons) > 1:
            result = cmds.polyUnite(polygons, ch=1, mergeUVSets=1, name=name)[0]
        elif polygons:
            result = polygons[0]
            result = cmds.rename(result, name)
        else:
            return None

        if group_output:
            cmds.parent(result, parent_grp)
            return parent_grp

        return result

    @CoreUtils.undoable
    def project_on_plane(
        self,
        curves: Optional[List[str]] = None,
        name: str = "projected_curves",
        group_output: bool = True,
    ) -> Union[str, List[str], None]:
        """Projects curves onto a plane."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return None

        parent_grp = None
        if group_output:
            parent_grp = cmds.group(em=True, name="{}_grp".format(name))
            curves_grp = cmds.group(curves, name="source_curves_grp")
            cmds.parent(curves_grp, parent_grp)

        # Calculate bounds
        min_x = min_z = float("inf")
        max_x = max_z = float("-inf")
        for curve in curves:
            bb = cmds.xform(curve, q=True, bb=True, ws=True)
            min_x = min(min_x, bb[0])
            max_x = max(max_x, bb[3])
            min_z = min(min_z, bb[2])
            max_z = max(max_z, bb[5])

        width = max_x - min_x
        height = max_z - min_z
        center_x = (min_x + max_x) / 2.0
        center_z = (min_z + max_z) / 2.0

        width = max(width, 1.0)
        height = max(height, 1.0)

        plane = cmds.nurbsPlane(
            w=width * 1.5, lr=height / width, ax=(0, 1, 0), name="projection_plane"
        )[0]
        cmds.move(center_x, -1.0, center_z, plane)

        if group_output:
            cmds.parent(plane, parent_grp)

        projected_curves = []
        for curve in curves:
            # Project down (0, -1, 0) since plane is at -1.0
            res = cmds.projectCurve(curve, plane, d=(0, -1, 0))
            if res:
                transforms = [x for x in res if cmds.nodeType(x) == "transform"]
                projected_curves.extend(transforms)

        if group_output and projected_curves:
            proj_grp = cmds.group(projected_curves, name="projected_curves_grp")
            cmds.parent(proj_grp, parent_grp)

        return parent_grp if group_output else projected_curves


class ImageTracerSlots:
    """UI slots for the Image Tracer tool."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.image_tracer
        # Sync UI state after initialization/restore
        try:
            from qtpy import QtCore

            QtCore.QTimer.singleShot(200, self._sync_ui)
        except ImportError:
            pass

    def _sync_ui(self):
        """Synchronize UI state."""
        try:
            from qtpy import QtCore

            chk000 = self.ui.findChild(QtCore.QObject, "chk000")
            if chk000:
                self.chk000(chk000.isChecked())
        except Exception:
            pass

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.add(
            "QCheckBox",
            setText="Use Blue Pencil",
            setObjectName="chk000",
            setChecked=False,
            setToolTip="Use Blue Pencil strokes instead of an image.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Open Blue Pencil",
            setObjectName="blue_pencil_button",
            setToolTip="Open the Blue Pencil tool in Maya.",
            clicked=lambda: mel.eval("OpenBluePencil"),
        )
        widget.set_help_text(
            fmt(
                title="Image Tracer",
                body="Trace contours from a raster image (or Blue Pencil "
                "strokes) into editable NURBS curves.",
                steps=[
                    "Browse (▸) to an image file, or check <b>Use Blue Pencil</b> "
                    "in the header menu to trace Blue Pencil strokes "
                    "(<b>Open Blue Pencil</b> launches the Maya tool).",
                    "Adjust the tracing parameters (threshold, smoothness, "
                    "min-area, etc.) for the desired level of detail.",
                    "Press <b>Trace</b> to generate NURBS curves.",
                ],
                notes=[
                    "Curves are created at the world origin and can be "
                    "extruded, lofted, or used as construction history "
                    "drivers like any other NURBS curve.",
                ],
            )
        )

    def txt000_init(self, widget):
        # Configure option box for file browsing
        widget.option_box.browse(
            file_types="Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
            title="Select Image",
        )

    def browse_image(self):
        file_path = self.sb.file_dialog(
            title="Select Image",
            file_types=["*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tif", "*.tiff"],
            filter_description="Images",
            allow_multiple=False,
        )
        if file_path:
            self.ui.txt000.setText(file_path)

    def _get_tracer(self):
        use_bp = self.ui.chk000.isChecked()
        image_path = self.ui.txt000.text()

        if not use_bp and not image_path:
            cmds.warning("Please select an image first.")
            return None

        scale = self.ui.s000.value()
        simplify_epsilon = self.ui.s001.value()
        simplify = simplify_epsilon if simplify_epsilon > 0 else None

        try:
            return ImageTracer(
                image_path,
                scale=scale,
                simplify=simplify,
                use_blue_pencil=use_bp,
            )
        except Exception as e:
            cmds.error("Error initializing ImageTracer: {}".format(e))
            return None

    def chk000(self, state):
        """Use Blue Pencil"""
        self.ui.txt000.setEnabled(not state)

    def b002(self):
        """Trace the source image into curves."""
        tracer = self._get_tracer()
        if tracer:
            tracer.trace_curves()

    def b003(self):
        """Build a mesh from the traced curves."""
        tracer = self._get_tracer()
        if tracer:
            tracer.create_mesh()

    def b004(self):
        """Build a mesh from the traced negative space."""
        tracer = self._get_tracer()
        if tracer:
            tracer.create_negative_space_mesh()

    def b005(self):
        """Project the traced result onto a plane."""
        tracer = self._get_tracer()
        if tracer:
            tracer.project_on_plane()


if __name__ == "__main__":
    tracer = ImageTracer(
        image_path="O:\\Cloud\\Code\\_scripts\\mayatk\\test\\test_assets\\prod_AO.png",
        scale=0.1,
        simplify=2.0,
        use_blue_pencil=False,
    )
