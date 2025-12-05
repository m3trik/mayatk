# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Optional, Union

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import pymel.core as pm
except ImportError:
    pm = None

# From this package:
from mayatk.core_utils._core_utils import CoreUtils


class ImageTracer(object):
    """A class to trace images into Maya NURBS curves and generate geometry."""

    def __init__(
        self,
        image_path,
        scale=0.1,
        simplify=1.0,
    ):
        # type: (str, float, Optional[float]) -> None
        self.image_path = image_path
        self.scale = scale
        self.simplify = simplify
        self._check_dependencies()

    def _check_dependencies(self):
        if cv2 is None:
            raise ImportError("OpenCV (cv2) is not installed.")
        if pm is None:
            raise ImportError("PyMEL is not available. Run this inside Maya.")
        if not os.path.exists(self.image_path):
            raise FileNotFoundError("Image not found: {}".format(self.image_path))

    @CoreUtils.undoable
    def trace_curves(self):
        # type: () -> List[pm.nt.NurbsCurve]
        """Traces the image and returns a list of created NURBS curves."""
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
                    curve = pm.curve(p=points, d=1)
                    created_curves.append(curve)
                except Exception as e:
                    print("Failed to create curve: {}".format(e))

        return created_curves

    @CoreUtils.undoable
    def create_mesh(
        self,
        curves=None,  # type: Optional[List[pm.nt.NurbsCurve]]
        combine=True,  # type: bool
        name="traced_mesh",  # type: str
        group_output=True,  # type: bool
    ):
        # type: (...) -> Union[pm.nt.Transform, List[pm.nt.Transform]]
        """Creates a polygon mesh from the traced curves (positive space)."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return []

        # Grouping logic
        parent_grp = None
        if group_output:
            parent_grp = pm.group(em=True, name="{}_grp".format(name))
            curves_grp = pm.group(curves, name="curves_grp")
            pm.parent(curves_grp, parent_grp)

        # Planar Surface
        planar_surfaces = pm.planarSrf(
            curves, d=3, keepOutside=0, tolerance=0.01, polygon=0
        )
        nurbs_surfaces = [x for x in planar_surfaces if pm.nodeType(x) == "transform"]

        if group_output and nurbs_surfaces:
            srf_grp = pm.group(nurbs_surfaces, name="nurbs_surfaces_grp")
            pm.parent(srf_grp, parent_grp)
            pm.hide(srf_grp)

        # Convert to Poly
        polygons = []
        for srf in nurbs_surfaces:
            poly = pm.nurbsToPoly(
                srf, format=1, uType=3, vType=3, uNumber=1, vNumber=1, mnd=1, ch=1
            )
            if poly:
                polygons.append(poly[0])

        if group_output and combine and len(polygons) > 1:
            inter_poly_grp = pm.group(polygons, name="intermediate_polygons_grp")
            pm.parent(inter_poly_grp, parent_grp)
            pm.hide(inter_poly_grp)

        result = polygons
        if combine and len(polygons) > 1:
            result = pm.polyUnite(polygons, ch=1, mergeUVSets=1, name=name)[0]
        elif len(polygons) == 1:
            result = polygons[0]
            result = pm.rename(result, name)

        if group_output:
            if isinstance(result, list):
                for r in result:
                    pm.parent(r, parent_grp)
            else:
                pm.parent(result, parent_grp)
            return parent_grp

        return result

    @CoreUtils.undoable
    def create_negative_space_mesh(
        self,
        curves=None,  # type: Optional[List[pm.nt.NurbsCurve]]
        margin_scale=0.1,  # type: float
        name="negative_space_mesh",  # type: str
        group_output=True,  # type: bool
    ):
        # type: (...) -> pm.nt.Transform
        """Creates a mesh representing the negative space (plane with holes)."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return None

        parent_grp = None
        if group_output:
            parent_grp = pm.group(em=True, name="{}_grp".format(name))
            curves_grp = pm.group(curves, name="curves_grp")
            pm.parent(curves_grp, parent_grp)

        # Calculate bounds
        min_x = min_z = float("inf")
        max_x = max_z = float("-inf")
        for curve in curves:
            bb = pm.xform(curve, q=True, bb=True, ws=True)
            min_x = min(min_x, bb[0])
            max_x = max(max_x, bb[3])
            min_z = min(min_z, bb[2])
            max_z = max(max_z, bb[5])

        margin = max((max_x - min_x), (max_z - min_z)) * margin_scale
        min_x -= margin
        max_x += margin
        min_z -= margin
        max_z += margin

        boundary_curve = pm.curve(
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
            pm.parent(boundary_curve, parent_grp)

        all_curves = [boundary_curve] + curves
        planar_surfaces = pm.planarSrf(
            all_curves, d=3, keepOutside=0, tolerance=0.01, polygon=0
        )
        nurbs_surfaces = [x for x in planar_surfaces if pm.nodeType(x) == "transform"]

        if group_output and nurbs_surfaces:
            srf_grp = pm.group(nurbs_surfaces, name="nurbs_surfaces_grp")
            pm.parent(srf_grp, parent_grp)
            pm.hide(srf_grp)

        polygons = []
        for srf in nurbs_surfaces:
            poly = pm.nurbsToPoly(
                srf, format=1, uType=3, vType=3, uNumber=1, vNumber=1, mnd=1, ch=1
            )
            if poly:
                polygons.append(poly[0])

        if group_output and len(polygons) > 1:
            inter_poly_grp = pm.group(polygons, name="intermediate_polygons_grp")
            pm.parent(inter_poly_grp, parent_grp)
            pm.hide(inter_poly_grp)

        if len(polygons) > 1:
            result = pm.polyUnite(polygons, ch=1, mergeUVSets=1, name=name)[0]
        elif polygons:
            result = polygons[0]
            result = pm.rename(result, name)
        else:
            return None

        if group_output:
            pm.parent(result, parent_grp)
            return parent_grp

        return result

    @CoreUtils.undoable
    def project_on_plane(
        self,
        curves=None,  # type: Optional[List[pm.nt.NurbsCurve]]
        name="projected_curves",  # type: str
        group_output=True,  # type: bool
    ):
        # type: (...) -> pm.nt.Transform
        """Projects curves onto a plane."""
        if curves is None:
            curves = self.trace_curves()

        if not curves:
            return None

        parent_grp = None
        if group_output:
            parent_grp = pm.group(em=True, name="{}_grp".format(name))
            curves_grp = pm.group(curves, name="source_curves_grp")
            pm.parent(curves_grp, parent_grp)

        # Calculate bounds
        min_x = min_z = float("inf")
        max_x = max_z = float("-inf")
        for curve in curves:
            bb = pm.xform(curve, q=True, bb=True, ws=True)
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

        plane = pm.nurbsPlane(
            w=width * 1.5, lr=height / width, ax=(0, 1, 0), name="projection_plane"
        )[0]
        pm.move(center_x, -1.0, center_z, plane)

        if group_output:
            pm.parent(plane, parent_grp)

        projected_curves = []
        for curve in curves:
            res = pm.projectCurve(curve, plane, d=(0, 1, 0))
            if res:
                transforms = [x for x in res if pm.nodeType(x) == "transform"]
                projected_curves.extend(transforms)

        if group_output and projected_curves:
            proj_grp = pm.group(projected_curves, name="projected_curves_grp")
            pm.parent(proj_grp, parent_grp)

        return parent_grp if group_output else projected_curves


class ImageTracerSlots:
    """UI slots for the Image Tracer tool."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.image_tracer

    def txt000_init(self, widget):
        # Configure option box for file dialog
        self.ui.txt000.option_box.set_action(self.browse_image)

        # Set icon
        from uitk.widgets.mixins.icon_manager import IconManager

        IconManager.set_icon(self.ui.txt000.option_box.widget, "folder_minimal")

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
        image_path = self.ui.txt000.text()
        if not image_path:
            pm.warning("Please select an image first.")
            return None

        scale = self.ui.s000.value()
        simplify_epsilon = self.ui.s001.value()
        simplify = simplify_epsilon if simplify_epsilon > 0 else None

        try:
            return ImageTracer(
                image_path,
                scale=scale,
                simplify=simplify,
            )
        except Exception as e:
            pm.error("Error initializing ImageTracer: {}".format(e))
            return None

    def b002(self):
        tracer = self._get_tracer()
        if tracer:
            tracer.trace_curves()

    def b003(self):
        tracer = self._get_tracer()
        if tracer:
            tracer.create_mesh()

    def b004(self):
        tracer = self._get_tracer()
        if tracer:
            tracer.create_negative_space_mesh()

    def b005(self):
        tracer = self._get_tracer()
        if tracer:
            tracer.project_on_plane()
