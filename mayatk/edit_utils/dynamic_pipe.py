from typing import List, Union
import pymel.core as pm


class CurveRig:
    def __init__(self, locators):
        if len(locators) < 2:
            raise ValueError(
                f"{self.__class__.__name__} requires at least two locators."
            )

        self.components = {
            "pathLocators": [],
            "clusters": [],
        }

        self.locators = locators
        self.curve = None
        self.create_curve_and_constrain(locators)

    def create_curve_and_constrain(self, locators, degree=1):
        positions = [pm.xform(loc, q=True, ws=True, t=True) for loc in locators]
        self.curve = pm.curve(d=degree, p=positions, n="curveRig")
        for i, loc in enumerate(locators):
            cv = self.curve.cv[i]
            cluster = pm.cluster(cv)
            clusterHandle = cluster[1]
            self.components["clusters"].append(clusterHandle)
            pm.pointConstraint(loc, clusterHandle, mo=False)

    def add_path_locators(self, locators_per_segment):
        for segment_index, num_locators in enumerate(locators_per_segment):
            self.place_locators_on_segment(segment_index, num_locators)

    def place_locators_on_segment(self, segment_index, num_locators, curveShape=None):
        if curveShape is None:
            curveShape = self.curve.getShape() if self.curve.getShape() else self.curve
        startParam, endParam = self.get_segment_param_range(segment_index, curveShape)
        for i in range(1, num_locators + 1):
            u = self.calculate_param_for_locator(i, startParam, endParam, num_locators)
            self.create_and_attach_locator(segment_index, i, u)

    def create_and_attach_locator(self, segment_index, locator_index, u):
        locator_name = f"pathLocator_segment{segment_index}_num{locator_index}"
        locator = pm.spaceLocator(name=locator_name)
        motionPath = pm.pathAnimation(
            locator,
            c=self.curve,
            follow=False,
            fractionMode=False,
            followAxis="x",
            upAxis="y",
            worldUpType="vector",
            worldUpVector=(0, 1, 0),
        )
        pm.cutKey(motionPath, time=(0,), attribute="uValue", option="keys")
        pm.setAttr(f"{motionPath}.uValue", u)
        self.components["pathLocators"].append(locator)

    def get_segment_param_range(self, segment_index, curveShape):
        startPos = pm.xform(self.locators[segment_index], q=True, ws=True, t=True)
        endPos = pm.xform(self.locators[segment_index + 1], q=True, ws=True, t=True)
        startParam = curveShape.getParamAtPoint(startPos, space="world")
        endParam = curveShape.getParamAtPoint(endPos, space="world")
        return startParam, endParam

    def calculate_param_for_locator(
        self, locator_index, startParam, endParam, num_locators
    ):
        return (
            (endParam - startParam) * locator_index / (num_locators + 1)
        ) + startParam

    def add_objects_to_locators(self, object_creator, **creator_kwargs):
        for locator in self.components["pathLocators"]:
            self.add_object_to_locator(locator, object_creator, **creator_kwargs)

    def add_object_to_locator(
        self, locator, object_creator, storage_key="attachedObjects", **creator_kwargs
    ):
        if storage_key not in self.components:
            self.components[storage_key] = {}

        locator_key = locator.name()
        if locator_key not in self.components[storage_key]:
            self.components[storage_key][locator_key] = []

        obj, _ = object_creator(**creator_kwargs)
        obj_name = f"{locator.name()}_object"
        pm.rename(obj, obj_name)
        self.components[storage_key][locator_key].append(obj)

        self.position_and_orient_object(obj, locator)

    def position_and_orient_object(self, object, locator):
        locator_pos = pm.xform(locator, q=True, ws=True, t=True)
        pm.xform(object, ws=True, t=locator_pos)

        self.align_with_curve_direction(object, locator, locator_pos)
        self.adjust_orientation_to_locator(object, locator)

    def align_with_curve_direction(self, object, locator, locator_pos):
        curve_shape = self.curve.getShape()
        if curve_shape:
            u = curve_shape.getParamAtPoint(
                pm.datatypes.Point(locator_pos), space="world"
            )
            # Get the minimum and maximum parameter values of the curve
            minParam, maxParam = curve_shape.getKnotDomain()

            # Clamp u + delta to stay within the curve's valid parameter range
            delta = 0.001
            u_delta = min(max(u + delta, minParam), maxParam)

            point1 = curve_shape.getPointAtParam(u, space="world")
            point2 = curve_shape.getPointAtParam(u_delta, space="world")
            direction = point2 - point1
            if direction.length() == 0:
                return
            direction.normalize()
            object.setRotation(
                pm.datatypes.Vector(direction).rotateTo(pm.datatypes.Vector(1, 0, 0))
            )

    def adjust_orientation_to_locator(self, object, locator):
        locator_rot = pm.xform(locator, q=True, ws=True, ro=True)
        pm.xform(object, ws=True, ro=locator_rot)


class DynamicPipe:
    def __init__(self, locators):
        self.curve_rig = CurveRig(locators)
        self.curve_rig.add_path_locators([2] * (len(locators) - 1))
        # Initialize circles list here for clarity and ensure it's always defined
        self.circles = []
        # Call create_nurbs_circles_at_locators with the original definition locators
        self.create_nurbs_circles_at_locators(self.curve_rig.components["pathLocators"])
        self.create_nurbs_circles_at_locators(self.curve_rig.locators)
        self.pipe_segments = []
        # self.create_pipe_geometry(range(5))

    def create_nurbs_circles_at_locators(self, locators):
        """
        Creates NURBS circles at the specified locators and ensures they move with the locators.
        This method replaces the original create_nurbs_circles_at_locators method and
        is designed to be more flexible by taking a list of locators as an argument.
        """
        self.circles = []  # Ensure the list is reset or initialized here
        for loc in locators:
            # Assuming the circle's normal is aligned with the Y-axis
            circle = pm.circle(nr=(1, 0, 0), c=(0, 0, 0), r=2, ch=False)[0]
            self.curve_rig.position_and_orient_object(circle, loc)
            pm.parent(circle, loc)
            self.circles.append(circle)

    def create_pipe_geometry(self, segments_to_loft):
        # Ensure segments_to_loft is a list of integers
        if not all(isinstance(item, int) for item in segments_to_loft):
            raise ValueError("segments_to_loft must be a list of integers.")

        # Iterate through the defined segments
        for segment_index in segments_to_loft:
            if segment_index < len(self.circles) - 1:  # Ensure index is within range
                circle_pair = self.circles[segment_index : segment_index + 2]
                pipe_segment = self.loft_between_circles(circle_pair)
                self.pipe_segments.append(pipe_segment)

    def loft_between_circles(self, circles):
        # Use the loft command to create a surface between the circles
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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pm.undoInfo(openChunk=True)
    pm.select(clear=True)
    for i in range(1, 5):
        pm.select(f"locator{i}", add=True)
    locators = pm.ls(orderedSelection=True, exactType="transform")
    pipe = DynamicPipe(locators)
    pm.undoInfo(closeChunk=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
