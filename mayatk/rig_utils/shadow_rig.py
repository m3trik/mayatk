# !/usr/bin/python
# coding=utf-8
import os
import numpy as np
import cv2
from typing import Optional, Tuple, Union

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om2
except ImportError as error:
    print(__file__, error)

import pythontk as ptk


class ShadowRig(ptk.LoggingMixin):
    """Projected shadow for Unity export.

    Creates a simple quad plane with the object's silhouette rendered as a
    PNG texture. The plane transforms (position, rotation, scale) are driven
    by an expression that can be baked to keyframes for FBX export.

    Workflow for Unity:
    1. Create shadow with ShadowRig.create()
    2. Bake simulation: Edit > Keys > Bake Simulation
    3. Export FBX (include shadow plane + texture)
    4. In Unity: Use Unlit/Transparent shader
    """

    def __init__(self, target=None, light=None, ground_height=0.0):
        self.target = pm.PyNode(target) if target else None
        self.light = pm.PyNode(light) if light else None
        self.shadow_plane = None
        self.contact_locator = None
        self.ground_height = ground_height
        self.shader = None
        self.opacity_mult = None
        self.texture_path = None

    def create_contact_locator(self):
        """Create a locator at the lowest point of the object to act as the shadow anchor."""
        bbox = pm.exactWorldBoundingBox(self.target)
        # BBox is [xmin, ymin, zmin, xmax, ymax, zmax]
        center_x = (bbox[0] + bbox[3]) / 2.0
        min_y = bbox[1]
        center_z = (bbox[2] + bbox[5]) / 2.0

        self.contact_locator = pm.spaceLocator(name=f"{self.target}_contact_loc")
        self.contact_locator.translate.set(center_x, min_y, center_z)
        self.contact_locator.localScale.set(0.2, 0.2, 0.2)

        # Parent to target so it moves/animates with it
        pm.parent(self.contact_locator, self.target)

        return self.contact_locator

    def get_or_create_shadow_source(self, position=(5, 10, 5), force_new=False):
        """Get existing shadow source or create a new one.

        Args:
            position: Initial position if creating new.
            force_new: If True, creates a unique source for this target.
                       If False, reuses 'shadow_source' if it exists.
        """
        source_name = "shadow_source"

        if not force_new and pm.objExists(source_name):
            self.light = pm.PyNode(source_name)
            print(f"Using existing shadow source: {self.light}")
        else:
            name = f"{self.target}_shadow_source" if force_new else source_name
            self.light = pm.spaceLocator(name=name)
            self.light.translate.set(position)
            self.light.localScale.set(1, 1, 1)

            # Yellow color
            shape = self.light.getShape()
            shape.overrideEnabled.set(True)
            shape.overrideColor.set(17)

        return self.light

    def create_shadow_plane(self):
        """Create a simple quad for the shadow with pivot at near edge."""
        if not self.target:
            raise ValueError("Target object required")

        # Get object footprint size
        bbox = pm.exactWorldBoundingBox(self.target)
        width = (bbox[3] - bbox[0]) * 1.5
        depth = (bbox[5] - bbox[2]) * 1.5
        self.plane_size = max(width, depth, 2.0)

        self.shadow_plane = pm.polyPlane(
            name=f"{self.target}_shadow",
            width=self.plane_size,
            height=self.plane_size,
            sx=1,
            sy=1,
            axis=(0, 1, 0),
        )[0]

        # Add custom attributes for controlling shadow
        if not pm.attributeQuery(
            "shadowIntensity", node=self.shadow_plane, exists=True
        ):
            pm.addAttr(
                self.shadow_plane,
                ln="shadowIntensity",
                at="float",
                min=0,
                max=1,
                dv=1.0,
                k=True,
            )
        if not pm.attributeQuery("falloffPower", node=self.shadow_plane, exists=True):
            pm.addAttr(
                self.shadow_plane,
                ln="falloffPower",
                at="float",
                min=0.0,
                max=5.0,
                dv=1.2,
                k=True,
            )
        if not pm.attributeQuery("scaleInfluence", node=self.shadow_plane, exists=True):
            pm.addAttr(
                self.shadow_plane,
                ln="scaleInfluence",
                at="float",
                min=0.0,
                max=1.0,
                dv=0.0,
                k=True,
            )

        # Move pivot to the near edge (negative Z edge in local space)
        pm.xform(
            self.shadow_plane, pivots=[0, 0, -self.plane_size / 2], objectSpace=True
        )

        # Bake the pivot offset into the vertices
        pm.select(self.shadow_plane.vtx[:])
        pm.move(0, 0, self.plane_size / 2, relative=True, objectSpace=True)
        pm.select(clear=True)

        # Reset pivot to origin
        pm.xform(self.shadow_plane, pivots=[0, 0, 0], objectSpace=True)

        # Position at target
        target_pos = self.target.getTranslation(space="world")
        self.shadow_plane.translate.set(
            target_pos[0], self.ground_height + 0.01, target_pos[2]
        )

        return self.shadow_plane

    def create_silhouette_texture(self, size=512, axis="auto", recursive=True):
        """Create silhouette texture using Maya API triangle rasterization.

        Args:
            size: Texture resolution
            axis: Projection axis - 'x', 'y', 'z', or 'auto' (default).
                  'auto' chooses the axis perpendicular to the widest dimension.
            recursive: If True, include descendant meshes (e.g. for groups/locators).
        """
        workspace = pm.workspace(q=True, rd=True)
        output_dir = os.path.join(workspace, "sourceimages")
        os.makedirs(output_dir, exist_ok=True)

        texture_name = f"{self.target}_shadow.png"
        self.texture_path = os.path.join(output_dir, texture_name)

        # Get mesh bounds
        bbox = pm.exactWorldBoundingBox(self.target)

        # Auto-detect best axis if requested
        if axis == "auto":
            width_x = bbox[3] - bbox[0]
            depth_z = bbox[5] - bbox[2]
            axis = "x" if depth_z > width_x else "z"

        # Determine projection axes based on view direction
        axis = axis.lower()
        if axis == "y":
            u_idx, v_idx = 0, 2  # XZ plane
        elif axis == "x":
            u_idx, v_idx = 2, 1  # ZY plane
        else:  # 'z' - default front/back view
            u_idx, v_idx = 0, 1  # XY plane

        # Calculate projection bounds
        u_min = bbox[u_idx]
        u_max = bbox[u_idx + 3]
        v_min = bbox[v_idx]
        v_max = bbox[v_idx + 3]

        u_extent = u_max - u_min
        v_extent = v_max - v_min
        extent = max(u_extent, v_extent) * 1.1
        u_center = (u_min + u_max) / 2
        v_center = (v_min + v_max) / 2

        # Create blank mask
        mask = np.zeros((size, size), dtype=np.uint8)

        def project_point(point):
            """Project 3D point to 2D image coordinates."""
            u = point[u_idx]
            v = point[v_idx]
            pu = int(((u - u_center) / extent + 0.5) * size)
            pv = int((1.0 - ((v - v_center) / extent + 0.5)) * size)
            return [np.clip(pu, 0, size - 1), np.clip(pv, 0, size - 1)]

        # Get all mesh shapes using Maya API for speed
        if recursive:
            shapes = (
                pm.listRelatives(self.target, shapes=True, ad=True, type="mesh") or []
            )
        else:
            shapes = pm.listRelatives(self.target, shapes=True, type="mesh") or []

        if not shapes:
            shape = self.target.getShape() if hasattr(self.target, "getShape") else None
            if shape:
                shapes = [shape]

        for shape in shapes:
            try:
                # Get the dag path for the shape
                sel_list = om2.MSelectionList()
                sel_list.add(str(shape))
                dag_path = sel_list.getDagPath(0)

                # Get MFnMesh for fast triangle access
                fn_mesh = om2.MFnMesh(dag_path)

                # Get all points in world space
                points = fn_mesh.getPoints(om2.MSpace.kWorld)

                # Get triangle data
                triangle_counts, triangle_vertices = fn_mesh.getTriangles()

                # Rasterize each triangle
                tri_idx = 0
                for face_idx, tri_count in enumerate(triangle_counts):
                    for t in range(tri_count):
                        # Get the 3 vertex indices for this triangle
                        v0_idx = triangle_vertices[tri_idx]
                        v1_idx = triangle_vertices[tri_idx + 1]
                        v2_idx = triangle_vertices[tri_idx + 2]
                        tri_idx += 3

                        # Get 3D points
                        p0 = points[v0_idx]
                        p1 = points[v1_idx]
                        p2 = points[v2_idx]

                        # Project to 2D
                        pt0 = project_point(p0)
                        pt1 = project_point(p1)
                        pt2 = project_point(p2)

                        # Draw filled triangle
                        triangle = np.array([pt0, pt1, pt2], dtype=np.int32)
                        cv2.fillPoly(mask, [triangle], 255)

            except Exception as e:
                print(f"Warning: Could not process shape {shape}: {e}")
                continue

        # Smooth the result slightly
        mask = cv2.GaussianBlur(mask, (3, 3), 0)

        # ---------------------------------------------------------
        # ANCHORING STEP: Shift silhouette to bottom edge of texture
        # ---------------------------------------------------------
        rows_with_content = np.where(mask.max(axis=1) > 0)[0]

        if len(rows_with_content) > 0:
            current_bottom = rows_with_content[-1]
            shift_down = (size - 1) - current_bottom

            if shift_down > 0:
                shifted_mask = np.zeros_like(mask)
                source_slice = mask[0 : current_bottom + 1]
                shifted_mask[shift_down:size] = source_slice
                mask = shifted_mask

                rows_with_content = np.where(mask.max(axis=1) > 0)[0]

        h, w = mask.shape

        # Gradient logic
        cols_with_content = np.where(mask.max(axis=0) > 0)[0]

        if len(rows_with_content) > 0 and len(cols_with_content) > 0:
            top_row = rows_with_content[0]
            bottom_row = rows_with_content[-1]
            center_col = (cols_with_content[0] + cols_with_content[-1]) // 2
            contact_point = (center_col, bottom_row)

            y, x = np.ogrid[:h, :w]
            dist_from_contact = np.sqrt(
                (x - contact_point[0]) ** 2 + (y - contact_point[1]) ** 2
            )
            max_dist = max(bottom_row - top_row, 1)
            radial = 1.0 - np.clip(dist_from_contact / max_dist, 0, 1) ** 0.8

            vertical = np.zeros((h, w), dtype=np.float32)
            for row in range(h):
                if row < top_row:
                    vertical[row, :] = 0.0
                elif row > bottom_row:
                    vertical[row, :] = 1.0
                else:
                    t = (row - top_row) / max(bottom_row - top_row, 1)
                    vertical[row, :] = t**0.6

            combined = radial * 0.7 + vertical * 0.3
        else:
            combined = np.ones((h, w), dtype=np.float32)

        alpha = (mask.astype(np.float32) / 255.0 * combined * 255).astype(np.uint8)
        alpha = np.flipud(alpha)

        result = np.zeros((h, w, 4), dtype=np.uint8)
        result[:, :, 3] = alpha

        cv2.imwrite(self.texture_path, result)
        print(f"Created silhouette texture: {self.texture_path} (axis={axis})")
        return self.texture_path

    def _create_silhouette_fallback(self, size, axis):
        """Fallback vertex-based silhouette if render fails."""
        bbox = pm.exactWorldBoundingBox(self.target)

        axis = axis.lower()
        if axis == "y":
            u_min, u_max = bbox[0], bbox[3]
            v_min, v_max = bbox[2], bbox[5]
            u_idx, v_idx = 0, 2
        elif axis == "x":
            u_min, u_max = bbox[2], bbox[5]
            v_min, v_max = bbox[1], bbox[4]
            u_idx, v_idx = 2, 1
        else:
            u_min, u_max = bbox[0], bbox[3]
            v_min, v_max = bbox[1], bbox[4]
            u_idx, v_idx = 0, 1

        extent = max(u_max - u_min, v_max - v_min) * 1.2
        u_center = (u_min + u_max) / 2
        v_center = (v_min + v_max) / 2

        img = np.zeros((size, size, 4), dtype=np.uint8)
        mask = np.zeros((size, size), dtype=np.uint8)

        vtx_count = pm.polyEvaluate(self.target, vertex=True)
        points = []
        for i in range(vtx_count):
            pos = pm.pointPosition(f"{self.target}.vtx[{i}]", w=True)
            pu = int(((pos[u_idx] - u_center) / extent + 0.5) * size)
            pv = int((1.0 - ((pos[v_idx] - v_center) / extent + 0.5)) * size)
            pu = np.clip(pu, 0, size - 1)
            pv = np.clip(pv, 0, size - 1)
            points.append([pu, pv])

        if points:
            points = np.array(points, dtype=np.int32)
            for p in points:
                cv2.circle(mask, tuple(p), 8, 255, -1)

            kernel = np.ones((15, 15), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            mask_filled = np.zeros_like(mask)
            cv2.drawContours(mask_filled, contours, -1, 255, -1)
            mask_filled = cv2.GaussianBlur(mask_filled, (7, 7), 0)

            h, w = mask_filled.shape
            rows_with_content = np.where(mask_filled.max(axis=1) > 0)[0]
            cols_with_content = np.where(mask_filled.max(axis=0) > 0)[0]

            if len(rows_with_content) > 0 and len(cols_with_content) > 0:
                top_row = rows_with_content[0]
                bottom_row = rows_with_content[-1]
                center_col = (cols_with_content[0] + cols_with_content[-1]) // 2

                y, x = np.ogrid[:h, :w]
                dist = np.sqrt((x - center_col) ** 2 + (y - bottom_row) ** 2)
                max_dist = bottom_row - top_row
                radial = 1.0 - np.clip(dist / max(max_dist, 1), 0, 1) ** 1.5

                vertical = np.zeros((h, w), dtype=np.float32)
                for row in range(h):
                    if row < top_row:
                        vertical[row, :] = 0.0
                    elif row > bottom_row:
                        vertical[row, :] = 1.0
                    else:
                        t = (row - top_row) / max(bottom_row - top_row, 1)
                        vertical[row, :] = t**0.5

                combined = vertical * 0.6 + radial * 0.4
                alpha = (
                    mask_filled.astype(np.float32) / 255.0 * combined * 255
                ).astype(np.uint8)
            else:
                alpha = mask_filled

            img[:, :, 3] = alpha

        cv2.imwrite(self.texture_path, img)
        return self.texture_path

    def create_material(self):
        """Create material with the silhouette texture."""
        if not self.texture_path:
            raise ValueError("Texture not created yet")

        self.shader = pm.shadingNode(
            "standardSurface", asShader=True, name=f"{self.shadow_plane}_mat"
        )
        self.shader.baseColor.set(0, 0, 0)

        file_node = pm.shadingNode(
            "file", asTexture=True, name=f"{self.shadow_plane}_tex"
        )
        file_node.fileTextureName.set(self.texture_path)

        place2d = pm.shadingNode(
            "place2dTexture", asUtility=True, name=f"{self.shadow_plane}_place2d"
        )
        place2d.outUV >> file_node.uv
        place2d.outUvFilterSize >> file_node.uvFilterSize

        # Create opacity multiplier controlled by expression
        self.opacity_mult = pm.shadingNode(
            "multiplyDivide", asUtility=True, name=f"{self.shadow_plane}_opacity_mult"
        )
        file_node.outAlpha >> self.opacity_mult.input1X
        file_node.outAlpha >> self.opacity_mult.input1Y
        file_node.outAlpha >> self.opacity_mult.input1Z

        # Connect to shader opacity
        self.opacity_mult.output >> self.shader.opacity

        sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{self.shader}_SG"
        )
        self.shader.outColor >> sg.surfaceShader
        pm.sets(sg, fe=self.shadow_plane)

        return self.shader

    def setup_expression(self):
        """Create expression to warp shadow based on light position."""
        expr_name = f"{self.shadow_plane}_expr"

        dm_name = f"{self.target}_contact_dm"
        if pm.objExists(dm_name):
            pm.delete(dm_name)
        dm_node = pm.createNode("decomposeMatrix", name=dm_name)

        source = self.contact_locator if self.contact_locator else self.target
        source.worldMatrix[0] >> dm_node.inputMatrix

        expr_code = f"""
// ----------------------------------------------------------------------
// Shadow Projection Logic
// ----------------------------------------------------------------------
// 1. Get World Positions
float $Lx = {self.light}.translateX;
float $Ly = {self.light}.translateY;
float $Lz = {self.light}.translateZ;

float $Cx = {dm_node}.outputTranslateX;
float $Cy = {dm_node}.outputTranslateY;
float $Cz = {dm_node}.outputTranslateZ;

float $Gy = {self.ground_height};

// 2. Project Contact Point to Ground
// Calculate intersection 't' where line L->C hits planar height G
// P = L + t * (C - L)
float $denom = $Cy - $Ly;
if (abs($denom) < 0.001) $denom = -0.001; // Safety check (light shouldn't be inside ground)

// t = (Ground - LightY) / (ContactY - LightY)
float $t = ($Gy - $Ly) / $denom;

// Projected Point P (Shadow Origin)
float $Px = $Lx + $t * ($Cx - $Lx);
float $Pz = $Lz + $t * ($Cz - $Lz);

// 3. Calculate Direction & Rotation
// Direction from Light to Contact (Ground Projected)
float $dx = $Px - $Lx;
float $dz = $Pz - $Lz;
float $angle = atan2d($dx, $dz);

// 4. Calculate Stretch
// Simple geometric stretch approximation based on angle of incidence
// Light Height relative to Contact Height
float $relLightHeight = max(0.1, $Ly - $Cy); 
// Horizontal distance from Light to Contact
float $hDist = sqrt( ($Cx-$Lx)*($Cx-$Lx) + ($Cz-$Lz)*($Cz-$Lz) );

// Stretch Factor: 1.0 = 45 degrees
float $stretch = 1.0 + ($hDist / $relLightHeight);
$stretch = clamp(0.5, 8.0, $stretch);


// ----------------------------------------------------------------------
// Apply Transforms
// ----------------------------------------------------------------------

// Position: Shadow pivot sits at the projected contact point on the ground
{self.shadow_plane}.translateX = $Px;
{self.shadow_plane}.translateZ = $Pz;
{self.shadow_plane}.translateY = $Gy + 0.005; // Bias to prevent z-fighting

// Rotation: Point away from light
{self.shadow_plane}.rotateY = $angle;

// Scale: Apply perspective scale ($t) combined with directional stretch
// Clamp $t to avoid infinite scaling when object approaches light
float $rawScale = clamp(0.01, 20.0, $t);
float $scaleInfluence = {self.shadow_plane}.scaleInfluence;

// Blend between 1.0 (Directional) and $rawScale (Perspective)
float $scale = 1.0 + ($rawScale - 1.0) * $scaleInfluence;

{self.shadow_plane}.scaleX = $scale;
{self.shadow_plane}.scaleZ = $scale * $stretch;


// ----------------------------------------------------------------------
// Opacity Falloff
// ----------------------------------------------------------------------
float $intensity = {self.shadow_plane}.shadowIntensity;
float $power = {self.shadow_plane}.falloffPower;

// 1. Distance Falloff: Fade out as shadow scales up (object moves away)
float $distOpacity = $intensity / max(0.001, pow($rawScale, $power));

// 2. Height Fade: Fade out if object goes above light source
// If ContactY ($Cy) approaches LightY ($Ly), opacity should drop to zero
float $heightDiff = $Ly - $Cy;
// Fade out over the last 1.0 unit before hitting light height
float $heightFade = clamp(0.0, 1.0, $heightDiff); 

float $opacity = $distOpacity * $heightFade;
$opacity = clamp(0.0, 1.0, $opacity);

{self.opacity_mult}.input2X = $opacity;
{self.opacity_mult}.input2Y = $opacity;
{self.opacity_mult}.input2Z = $opacity;
"""
        if pm.objExists(expr_name):
            pm.delete(expr_name)

        pm.expression(name=expr_name, string=expr_code, alwaysEvaluate=True)

    @classmethod
    def create(
        cls,
        target,
        light_pos=(5, 10, 5),
        texture_res=512,
        axis="auto",
        new_source=False,
        recursive=True,
    ):
        """Create a projected shadow for Unity export.

        Args:
            target: Object to cast shadow from
            light_pos: Initial position for light locator
            texture_res: Resolution of silhouette texture
            axis: Silhouette projection axis:
                  'auto' = best axis based on bounding box (default)
                  'z' = front/back view
                  'x' = side view
                  'y' = top-down view
            new_source: If True, creates a unique shadow source for this rig.
                        If False, reuses the global 'shadow_source'.
            recursive: If True, include descendant meshes in shadow.

        Returns:
            ShadowRig instance
        """
        shadow = cls(target=target)
        shadow.get_or_create_shadow_source(position=light_pos, force_new=new_source)
        shadow.create_contact_locator()
        shadow.create_shadow_plane()
        shadow.create_silhouette_texture(
            size=texture_res, axis=axis, recursive=recursive
        )
        shadow.create_material()
        shadow.setup_expression()

        grp = pm.group(empty=True, name=f"{target}_shadow_grp")
        pm.parent(shadow.shadow_plane, grp)

        # Only parent the source if it's unique to this rig
        if new_source:
            pm.parent(shadow.light, grp)
        # contact_locator stays on target

        print(f"\nCreated shadow for {target}")
        print(f"  Source: {shadow.light}")
        print(f"  Shadow: {shadow.shadow_plane}")
        print(f"  Texture: {shadow.texture_path}")
        print(f"  Axis: {axis}")
        print(f"\nMove the shadow source to stretch/warp the shadow!")
        print(f"\nFor Unity export:")
        print(f"  1. Bake: Edit > Keys > Bake Simulation")
        print(f"  2. Export FBX with shadow plane")
        print(f"  3. In Unity: Use Unlit/Transparent shader")

        return shadow


class ShadowRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (shadow_rig.ui)
        self.ui = self.sb.loaded_ui.shadow_rig

        # Connect UI elements
        self.ui.b000.clicked.connect(self.create_shadow)

    def create_shadow(self):
        """Create projected shadow for selected object."""
        sel = pm.selected()
        if not sel:
            self.sb.message_box("Please select a target object.")
            return

        target = sel[0]
        resolution = self.ui.s000.value()
        new_source = self.ui.chk_new_source.isChecked()
        recursive = self.ui.chk_combine.isChecked()

        # Axis combobox: 0=Auto, 1=X, 2=Y, 3=Z
        axis_idx = self.ui.cmb000.currentIndex()
        axis_map = {0: "auto", 1: "x", 2: "y", 3: "z"}
        axis = axis_map.get(axis_idx, "auto")

        try:
            ShadowRig.create(
                target,
                texture_res=resolution,
                axis=axis,
                new_source=new_source,
                recursive=recursive,
            )
            self.sb.message_box(f"Created shadow for {target}")
        except Exception as e:
            self.sb.message_box(f"Error creating shadow: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    sel = pm.selected()
    if not sel:
        print("Select an object first.")
    else:
        ShadowRig.create(sel[0])
