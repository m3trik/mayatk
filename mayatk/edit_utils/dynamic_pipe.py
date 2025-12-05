from typing import List, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# From this package
from mayatk.core_utils._core_utils import CoreUtils


class Curve:
    def __init__(self, **kwargs):
        self.node = pm.curve(**kwargs)
        self.shape_node = self.node.getShape()

    def __getattr__(self, attr):
        """Delegate attribute access to the underlying PyNode when not found in Curve."""
        try:
            return getattr(super(), attr)
        except AttributeError:
            return getattr(self.node, attr)

    def __setattr__(self, attr, value):
        """Delegate attribute setting to the underlying PyNode unless specifically handled."""
        if attr in ["node", "shape_node"]:
            super().__setattr__(attr, value)
        else:
            setattr(self.node, attr, value)

    @property
    def length(self):
        """Get the length of the curve."""
        return pm.arclen(self.shape_node)

    @length.setter
    def length(self, new_length):
        """Set the curve to a new length, adjusting its scale proportionally."""
        current_length = self.length
        scale_factor = new_length / current_length
        pm.scale(self.node.cv[:], scale_factor, scale_factor, scale_factor, r=True)

    def add_point(self, position):
        """Dynamically add a point to the curve."""
        pm.curve(self.node, a=True, p=[position])

    def update_point(self, index, new_position):
        """Update the position of a point on the curve."""
        cvs = pm.ls(self.node.cv, fl=True)
        if 0 <= index < len(cvs):
            pm.xform(cvs[index], ws=True, t=new_position)

    def update_curve_cv(self, index, position):
        """Update the position of a curve control vertex."""
        cv_index = index * 3  # Adjust as needed based on the curve's structure
        pm.xform(self.shape_node.cv[cv_index], ws=True, t=position)

    def get_param_at_point(self, point):
        """Get the curve parameter at a given point."""
        return self.shape_node.getParamAtPoint(pm.datatypes.Point(point), space="world")

    def get_point_at_param(self, param):
        """Get the point on the curve at a given parameter."""
        return self.shape_node.getPointAtParam(param, space="world")

    def get_adjacent_points(self, u, delta):
        """Return points adjacent to a parameter u on the curve, adjusted by delta."""
        min_param, max_param = self.shape_node.getKnotDomain()
        u_delta = min(max(u + delta, min_param), max_param)
        return self.get_point_at_param(u), self.get_point_at_param(u_delta)


class BezierCurve(Curve):
    def __init__(self, **kwargs):
        # Ensure the curve created is a bezier curve by setting the 'bezier' argument
        kwargs.update({"bezier": True})
        super().__init__(**kwargs)

    def add_control_point(
        self, position, handle1_offset=(1, 0, 0), handle2_offset=(-1, 0, 0)
    ):
        """
        Adds a control point and its handles to the bezier curve.
        'position' is the main anchor point,
        'handle1_offset' and 'handle2_offset' are offsets from the anchor point to handles.
        """
        # Add main control point
        super().add_point(position)

        # Calculate handle positions based on the provided offsets
        handle1_position = (
            position[0] + handle1_offset[0],
            position[1] + handle1_offset[1],
            position[2] + handle1_offset[2],
        )
        handle2_position = (
            position[0] + handle2_offset[0],
            position[1] + handle2_offset[1],
            position[2] + handle2_offset[2],
        )

        # Add handles
        super().add_point(handle1_position)
        super().add_point(handle2_position)

    def update_control_point(self, index, new_position):
        """
        Updates the position of a control point and optionally adjusts its handles.
        'index' should be the index of the main control point.
        """
        # Update main control point
        super().update_point(
            index * 3, new_position
        )  # Assuming each control point has two handles

    def adjust_handles(
        self, index, handle1_new_position=None, handle2_new_position=None
    ):
        """
        Adjust the positions of the handles for a specific control point.
        """
        if handle1_new_position:
            super().update_point(index * 3 + 1, handle1_new_position)
        if handle2_new_position:
            super().update_point(index * 3 + 2, handle2_new_position)


class CurveRig:
    def __init__(self, locators, delta=0.001):

        self.validate_locators(locators)
        self.locators = locators
        self.components = {"pathLocators": [], "clusters": [], "circles": []}
        self.delta = delta

        self.create_inbetween_locators(locators)
        self.orient_locators()
        self.curve = self.create_curve_from_locators(locators)
        self.setup_clusters()

    def validate_locators(self, locators):
        if len(locators) < 2:
            raise ValueError("At least two locators are required.")

    def create_inbetween_locators(self, locators, num_inbetween=2):
        result = []
        for i in range(len(locators) - 1):
            self.locators.append(locators[i])
            start_pos = pm.xform(locators[i], q=True, ws=True, t=True)
            end_pos = pm.xform(locators[i + 1], q=True, ws=True, t=True)
            step_vector = [
                (end_pos[j] - start_pos[j]) / (num_inbetween + 1) for j in range(3)
            ]

            for n in range(1, num_inbetween + 1):
                inbetween_pos = [start_pos[j] + n * step_vector[j] for j in range(3)]
                inbetween_locator = self.rig.create_locator_at_position(inbetween_pos)
                self.locators.append(inbetween_locator)
                result.append(inbetween_locator)

        self.locators.append(locators[-1])
        return result

    def create_curve_from_locators(self, locators):
        points = [pm.xform(loc, q=True, ws=True, t=True) for loc in locators]
        return BezierCurve(p=points, bezier=True)

    def setup_clusters(self):
        cvs = pm.ls(self.curve.shape_node.cv, fl=True)
        for i, cv in enumerate(cvs):
            cluster = pm.cluster(cv)[1]
            self.components["clusters"].append(cluster)
            if i < len(self.locators):
                pm.pointConstraint(self.locators[i], cluster, mo=False)

    def orient_locators(self):
        for i, locator in enumerate(self.locators):
            if i == 0 or i == len(self.locators) - 1:
                self.orient_end_locators(i)
            else:
                self.orient_middle_locators(i)

    def orient_end_locators(self, index):
        if index == 0:
            next_loc = self.locators[index + 1]
        else:
            next_loc = self.locators[index - 1]

        direction = (
            next_loc.getTranslation(space="world")
            - self.locators[index].getTranslation(space="world")
        ).normal()
        aim_locator = pm.spaceLocator()
        pm.xform(
            aim_locator,
            ws=True,
            t=self.locators[index].getTranslation(space="world") + direction,
        )
        constraint = pm.aimConstraint(
            aim_locator,
            self.locators[index],
            aimVector=[1, 0, 0],
            upVector=[0, 1, 0],
            worldUpType="vector",
            worldUpVector=direction,
        )
        pm.delete(constraint, aim_locator)

    def orient_middle_locators(self, index):
        prev_dir = (
            self.locators[index].getTranslation(space="world")
            - self.locators[index - 1].getTranslation(space="world")
        ).normal()
        next_dir = (
            self.locators[index + 1].getTranslation(space="world")
            - self.locators[index].getTranslation(space="world")
        ).normal()
        average_dir = (prev_dir + next_dir).normal()
        up_vector = next_dir.cross(prev_dir).normal()
        aim_locator = pm.spaceLocator()
        pm.xform(
            aim_locator,
            ws=True,
            t=self.locators[index].getTranslation(space="world") + average_dir,
        )
        constraint = pm.aimConstraint(
            aim_locator,
            self.locators[index],
            aimVector=[1, 0, 0],
            upVector=[0, 1, 0],
            worldUpType="vector",
            worldUpVector=up_vector,
        )
        pm.delete(constraint, aim_locator)

    def create_inbetween_locators(self, num_inbetween):
        new_locators = []
        for i in range(len(self.locators) - 1):
            start_pos = self.locators[i].getTranslation(space="world")
            end_pos = self.locators[i + 1].getTranslation(space="world")
            step_vector = [
                (end_pos[j] - start_pos[j]) / (num_inbetween + 1) for j in range(3)
            ]

            for n in range(1, num_inbetween + 1):
                inbetween_pos = [start_pos[j] + n * step_vector[j] for j in range(3)]
                locator = self.create_locator_at_position(inbetween_pos)
                new_locators.append(locator)
                self.locators.insert(i + n, locator)
        return new_locators

    def create_nurbs_circles_at_locators(self):
        for locator in self.locators:
            circle = pm.circle(normal=(1, 0, 0), constructionHistory=False)[0]
            pm.matchTransform(circle, locator)
            circle.overrideEnabled.set(1)
            circle.overrideDisplayType.set(2)
            pm.parent(circle, locator)
            self.components["circles"].append(circle)

    def add_circles_between_points(self, start_parameter, end_parameter, num_circles):
        circle = pm.circle(normal=(1, 0, 0), constructionHistory=False)[0]
        new_circles = self.curve.add_along_curve(
            circle, start_parameter, end_parameter, num_circles
        )
        pm.delete(circle)
        self.components["circles"].extend(new_circles)

    def update_locator_position(self, index, new_position):
        if index < len(self.locators):
            locator = self.locators[index]
            pm.xform(locator, ws=True, t=new_position)
            self.update_curve_cv(index, new_position)

    def update_curve_cv(self, index, position):
        cv_index = index * 3
        pm.xform(self.curve.shape_node.cv[cv_index], ws=True, t=position)

    def add_locator(self, position):
        locator = self.create_locator_at_position(position)
        self.locators.append(locator)
        self.curve.add_anchor_point(position)
        self.setup_clusters()

    def create_locator_at_position(self, position):
        locator = pm.spaceLocator(p=position)
        pm.xform(locator, ws=True, t=position, piv=position)
        return locator

    def remove_locator(self, locator_index):
        if 0 <= locator_index < len(self.locators):
            pm.delete(self.locators.pop(locator_index))
            self.curve.node.deleteCV(locator_index * 3)
            self.curve.rebuild_curve()
            self.setup_clusters()

    def position_and_orient_object(self, obj, locator):
        pos = pm.xform(locator, q=True, ws=True, t=True)
        self.align_and_orient_to_curve(obj, pos)

    def align_and_orient_to_curve(self, obj, position):
        u = self.curve.get_param_at_point(position)
        if u is None:
            return

        point1, point2 = self.get_adjacent_curve_points(u)
        self.orient_object(obj, point1, point2)

    def get_adjacent_curve_points(self, u):
        min_param, max_param = self.curve.shape_node.getKnotDomain()
        u_delta = min(max(u + self.delta, min_param), max_param)
        return self.curve.get_point_at_param(u), self.curve.get_point_at_param(u_delta)

    def orient_object(self, obj, point1, point2):
        direction = point2 - point1
        if direction.length() > 0:
            direction.normalize()
            obj.setRotation(
                pm.datatypes.Vector(direction).rotateTo(pm.datatypes.Vector(1, 0, 0))
            )

    def add_path_locators(self, locators_per_segment):
        for i, count in enumerate(locators_per_segment):
            start_param, end_param = self.get_segment_param_range(i, i + 1)
            self.place_locators_on_segment(i, count, start_param, end_param)

    def place_locators_on_segment(
        self, segment_index, num_locators, start_param, end_param
    ):
        for i in range(1, num_locators + 1):
            u = (end_param - start_param) * i / (num_locators + 1) + start_param
            locator_name = f"pathLocator_segment{segment_index}_num{i}"
            locator = self.create_locator_at_position(self.curve.get_point_at_param(u))
            locator.rename(locator_name)
            self.components["pathLocators"].append(locator)

    def get_segment_param_range(self, start_index, end_index):
        start_pos = pm.xform(self.locators[start_index], q=True, ws=True, t=True)
        end_pos = pm.xform(self.locators[end_index], q=True, ws=True, t=True)
        return self.curve.get_param_at_point(start_pos), self.curve.get


class DynamicPipe:
    def __init__(self, locators, num_inbetween=2):

        # self.segment_locators = self.create_inbetween_locators(locators, num_inbetween)
        self.rig = CurveRig(locators)
        self.circles = self.create_nurbs_circles_at_locators(self.segment_locators)
        self.pipe_segments = []
        self.add_circles_between_points(0, 1, len(self.locators) - 1)

    def create_nurbs_circles_at_locators(self, locators):
        circles = []
        for locator in locators:
            circle = pm.circle(normal=(1, 0, 0), constructionHistory=False)[0]
            pm.matchTransform(circle, locator)
            # Make the circle not selectable
            circle.overrideEnabled.set(1)
            circle.overrideDisplayType.set(2)
            # Parent the circle to the locator
            pm.parent(circle, locator)
            circles.append(circle)
        return circles

    def add_circles_between_points(self, start_parameter, end_parameter, num_circles):
        # Create a circle to be duplicated
        circle = pm.circle(normal=(1, 0, 0), constructionHistory=False)[0]
        # Add circles along the curve
        self.rig.add_along_curve(circle, start_parameter, end_parameter, num_circles)
        # Delete the original circle
        pm.delete(circle)

    def create_pipe_geometry(self, segments_to_loft):
        if not all(isinstance(item, int) for item in segments_to_loft):
            raise ValueError("segments_to_loft must be a list of integers.")
        for segment_index in segments_to_loft:
            if segment_index < len(self.circles) - 1:
                circle_pair = self.circles[segment_index : segment_index + 2]
                pipe_segment = self.loft_between_circles(circle_pair)
                self.pipe_segments.append(pipe_segment)

    def loft_between_circles(self, circles):
        lofted_surface = pm.loft(
            circles,
            ch=True,
            u=True,
            c=False,
            ar=True,
            d=3,
            ss=1,
            rn=False,
            po=0,
            rsn=True,
        )[0]
        return lofted_surface


class DynamicPipeSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.dynamic_pipe

        self.pipe = None

    def b000(self):
        """Create Dynamic Pipe"""
        pm.undoInfo(openChunk=True)
        locators = pm.ls(orderedSelection=True, exactType="transform")
        self.pipe = DynamicPipe(locators)
        segments_to_loft = list(range(len(self.pipe.circles) - 1))
        self.pipe.create_pipe_geometry(segments_to_loft)
        pm.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("dynamic_pipe", reload=True)
    ui.show(pos="screen", app_exec=True)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
