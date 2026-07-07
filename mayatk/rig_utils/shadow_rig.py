# !/usr/bin/python
# coding=utf-8
import os
import numpy as np
import cv2
from typing import Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# From this package:
from mayatk import CoreUtils, NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.preview import Preview


class ShadowRig(ptk.LoggingMixin):
    """Projected shadow for Unity export.

    Creates a simple quad plane with the object's silhouette rendered as a
    PNG texture. The plane transforms (position, rotation, scale) are driven
    by an expression that can be baked to keyframes for FBX export.

    Modes:
        - "orbit": Plane rotates around the target to face away from light.
                   Simple and intuitive; shadow always points away from source.
        - "stretch": Plane stays axis-aligned; uses scale + compensatory
                     translation to warp shadow. Better for baking but
                     silhouette may appear mirrored at extreme angles.

    Workflow for Unity:
    1. Create shadow with ShadowRig.create()
    2. Bake simulation: Edit > Keys > Bake Simulation
    3. Export FBX (include shadow plane + texture)
    4. In Unity: Use Unlit/Transparent shader
    """

    MODES = ("orbit", "stretch")

    def __init__(self, targets=None, light=None, ground_height=0.0, mode="stretch"):
        # Accept single target or list of targets
        if targets is None:
            self.targets = []
        elif isinstance(targets, (list, tuple)):
            self.targets = [str(t) for t in targets]
        else:
            self.targets = [str(targets)]

        self.light = str(light) if light else None
        self.shadow_plane = None
        self.contact_locator = None
        self.ground_height = ground_height
        self.shader = None
        self.opacity_mult = None
        self.texture_path = None
        self.mode = mode if mode in self.MODES else "stretch"

        # For naming, use first target or "combined"
        self._name_base = str(self.targets[0]) if len(self.targets) == 1 else "combined"

    def create_contact_locator(self):
        """Create a locator at the lowest point of the combined objects to act as the shadow anchor."""
        bbox = cmds.exactWorldBoundingBox(self.targets)
        # BBox is [xmin, ymin, zmin, xmax, ymax, zmax]
        center_x = (bbox[0] + bbox[3]) / 2.0
        min_y = bbox[1]
        center_z = (bbox[2] + bbox[5]) / 2.0

        self.contact_locator = cmds.spaceLocator(
            name=f"{self._name_base}_contact_loc"
        )[0]
        cmds.setAttr(
            f"{self.contact_locator}.translate",
            center_x,
            min_y,
            center_z,
            type="double3",
        )
        cmds.setAttr(
            f"{self.contact_locator}.localScale", 0.2, 0.2, 0.2, type="double3"
        )

        # Parent to first target so it moves/animates with it
        self.contact_locator = cmds.parent(self.contact_locator, self.targets[0])[0]

        return self.contact_locator

    def get_or_create_shadow_source(
        self, position=(5, 10, 5), source_name="shadow_source"
    ):
        """Get existing shadow source or create a new one.

        Args:
            position: Initial position if creating new.
            source_name: Name for the shadow source locator.
        """
        if cmds.objExists(source_name):
            self.light = source_name
            print(f"Using existing shadow source: {self.light}")
        else:
            self.light = cmds.spaceLocator(name=source_name)[0]
            cmds.setAttr(
                f"{self.light}.translate",
                position[0],
                position[1],
                position[2],
                type="double3",
            )
            cmds.setAttr(f"{self.light}.localScale", 1, 1, 1, type="double3")

            # Yellow color
            shapes = NodeUtils.get_shapes(self.light, no_intermediate=False)
            if shapes:
                shape = shapes[0]
                cmds.setAttr(f"{shape}.overrideEnabled", True)
                cmds.setAttr(f"{shape}.overrideColor", 17)

        return self.light

    def create_shadow_plane(self):
        """Create a simple quad for the shadow with pivot at near edge."""
        if not self.targets:
            raise ValueError("Target object(s) required")

        # Get combined footprint size from all targets
        bbox = cmds.exactWorldBoundingBox(self.targets)
        width = (bbox[3] - bbox[0]) * 1.1
        depth = (bbox[5] - bbox[2]) * 1.1
        self.plane_size = max(width, depth, 1.0)

        self.shadow_plane = cmds.polyPlane(
            name=f"{self._name_base}_shadow",
            width=self.plane_size,
            height=self.plane_size,
            sx=1,
            sy=1,
            axis=(0, 1, 0),
        )[0]

        # Add custom attributes for controlling shadow
        if not cmds.attributeQuery(
            "shadowIntensity", node=self.shadow_plane, exists=True
        ):
            cmds.addAttr(
                self.shadow_plane,
                ln="shadowIntensity",
                at="float",
                min=0,
                max=1,
                dv=1.0,
                k=True,
            )
        if not cmds.attributeQuery("falloffPower", node=self.shadow_plane, exists=True):
            cmds.addAttr(
                self.shadow_plane,
                ln="falloffPower",
                at="float",
                min=0.0,
                max=5.0,
                dv=1.2,
                k=True,
            )
        if not cmds.attributeQuery("scaleInfluence", node=self.shadow_plane, exists=True):
            cmds.addAttr(
                self.shadow_plane,
                ln="scaleInfluence",
                at="float",
                min=0.0,
                max=1.0,
                dv=0.0,
                k=True,
            )
        # Store base plane size for expression calculations
        if not cmds.attributeQuery("basePlaneSize", node=self.shadow_plane, exists=True):
            cmds.addAttr(
                self.shadow_plane,
                ln="basePlaneSize",
                at="float",
                dv=self.plane_size,
                k=False,
            )
        cmds.setAttr(f"{self.shadow_plane}.basePlaneSize", self.plane_size)

        # Keep plane centered - pivot at center, vertices centered around origin
        # The expression handles positioning based on light direction
        cmds.xform(self.shadow_plane, pivots=[0, 0, 0], objectSpace=True)

        # Position at combined targets center
        bbox = cmds.exactWorldBoundingBox(self.targets)
        center_x = (bbox[0] + bbox[3]) / 2.0
        center_z = (bbox[2] + bbox[5]) / 2.0
        cmds.setAttr(
            f"{self.shadow_plane}.translate",
            center_x,
            self.ground_height + 0.01,
            center_z,
            type="double3",
        )

        return self.shadow_plane

    def create_silhouette_texture(
        self,
        size=512,
        axis="auto",
        recursive=True,
        *,
        uniform_alpha=False,
        falloff_source=None,
        falloff_power=0.8,
        vertical_weight=0.3,
        blur_amount=1.5,
    ):
        """Create silhouette texture using Maya API triangle rasterization.

        Args:
            size: Texture resolution.
            axis: Projection axis - 'x', 'y', 'z', or 'auto' (default).
                  'auto' chooses the axis perpendicular to the widest dimension.
            recursive: If True, include descendant meshes (e.g. for groups/locators).
            uniform_alpha: If True, the silhouette alpha is uniform across the
                shape (no contact-point falloff). Useful for top-down shadows
                or stylised cases where you want a flat shadow.
            falloff_source: Override the auto-detected contact point — the
                (u, v) origin from which alpha falls off — in *saved-PNG
                image coords* (open the texture file: (0, 0) is top-left,
                (1, 1) is bottom-right). For typical ground shadows the
                contact appears at the **top** of the saved file (it's
                flipped before save so Maya's V-up UV reads it correctly),
                so the auto-default works out to ≈ ``(0.5, 0.0)``.
                Ignored when ``uniform_alpha``.
            falloff_power: Radial falloff exponent. ``1.0`` = linear,
                ``<1`` = sharper drop near the source, ``>1`` = lingers.
                Ignored when ``uniform_alpha``.
            vertical_weight: Blend weight of the vertical-gradient term into
                the falloff (``0.0`` = pure radial, ``1.0`` = pure vertical).
                Ignored when ``uniform_alpha``.
            blur_amount: Gaussian blur radius (in pixels) applied to the
                silhouette mask. ``0`` = sharp edges; typical 1-4.
        """
        import pythontk as ptk

        workspace = cmds.workspace(q=True, rd=True)
        output_dir = os.path.join(workspace, "sourceimages")
        os.makedirs(output_dir, exist_ok=True)

        texture_name = f"{self._name_base}_shadow.png"
        self.texture_path = os.path.join(output_dir, texture_name)

        # Get combined mesh bounds from all targets
        bbox = cmds.exactWorldBoundingBox(self.targets)

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

        # Gather all mesh-shape DAG paths from all targets.
        # Walk transforms first so instanced shapes yield one path per parent
        # (otherwise listRelatives dedupes by node and we miss instance copies).
        shapes = []
        for target in self.targets:
            if recursive:
                transforms = [target] + (
                    cmds.listRelatives(
                        target, ad=True, type="transform", fullPath=True
                    )
                    or []
                )
            else:
                transforms = [target]

            target_shapes = []
            for tx in transforms:
                tx_shapes = (
                    cmds.listRelatives(
                        tx, shapes=True, type="mesh", fullPath=True
                    )
                    or []
                )
                target_shapes.extend(tx_shapes)

            if not target_shapes:
                direct_shapes = NodeUtils.get_shapes(target, no_intermediate=True)
                if direct_shapes:
                    target_shapes = direct_shapes

            shapes.extend(target_shapes)

        for shape in shapes:
            try:
                shape_path = str(shape)

                # Get the dag path for the shape
                sel_list = om2.MSelectionList()
                sel_list.add(shape_path)
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

        # Smooth the silhouette edges
        if blur_amount and blur_amount > 0:
            mask = ptk.ImgUtils.gaussian_blur(mask, radius=blur_amount)

        h, w = mask.shape

        if uniform_alpha:
            combined = np.ones((h, w), dtype=np.float32)
        else:
            rows_with_content = np.where(mask.max(axis=1) > 0)[0]
            cols_with_content = np.where(mask.max(axis=0) > 0)[0]

            if len(rows_with_content) and len(cols_with_content):
                top_row = rows_with_content[0]
                bottom_row = rows_with_content[-1]
                center_col = (cols_with_content[0] + cols_with_content[-1]) // 2

                # Resolve contact point. ``falloff_source`` is interpreted in
                # *saved-PNG image coords* — (0, 0) top-left, (1, 1) bottom-
                # right of the file the user opens on disk. The gradient is
                # computed before the final ``np.flipud`` (which exists so
                # the texture aligns with Maya's V-up UV convention), so the
                # v coord is mirrored when passed to the gradient.
                if falloff_source is not None:
                    src_u = float(falloff_source[0])
                    src_v_saved = float(falloff_source[1])
                    src_uv_norm = (src_u, 1.0 - src_v_saved)
                else:
                    src_uv_norm = (
                        center_col / max(w - 1, 1),
                        bottom_row / max(h - 1, 1),
                    )

                # Radial falloff (1 at source → 0 at silhouette height away)
                max_dist = max(bottom_row - top_row, 1)
                radial_w = max(1.0 - vertical_weight, 0.0)
                vertical_w = max(min(vertical_weight, 1.0), 0.0)

                radial = ptk.ImgUtils.radial_gradient(
                    (w, h),
                    center=src_uv_norm,
                    max_radius=max_dist,
                    falloff_power=falloff_power,
                )

                # Vertical term — only contributes where mask has content
                # vertically; outside that band it falls off in the same
                # direction as the silhouette.
                vertical = np.zeros((h, w), dtype=np.float32)
                rows = np.arange(h)
                span = max(bottom_row - top_row, 1)
                t = np.clip((rows - top_row) / span, 0.0, 1.0) ** 0.6
                vertical[:, :] = t[:, None]
                vertical[rows < top_row, :] = 0.0
                vertical[rows > bottom_row, :] = 1.0

                combined = radial * radial_w + vertical * vertical_w
            else:
                combined = np.ones((h, w), dtype=np.float32)

        alpha = (mask.astype(np.float32) / 255.0 * combined * 255).astype(np.uint8)
        alpha = np.flipud(
            alpha
        )  # Re-enabled flip, as Texture coordinate Y=0 is usually Bottom

        result = np.zeros((h, w, 4), dtype=np.uint8)
        result[:, :, 3] = alpha

        cv2.imwrite(self.texture_path, result)
        print(f"Created silhouette texture: {self.texture_path} (axis={axis})")
        return self.texture_path

    def _create_silhouette_fallback(self, size, axis):
        """Fallback vertex-based silhouette if render fails."""
        bbox = cmds.exactWorldBoundingBox(self.target)

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

        vtx_count = cmds.polyEvaluate(self.target, vertex=True)
        points = []
        for i in range(vtx_count):
            pos = cmds.pointPosition(f"{self.target}.vtx[{i}]", w=True)
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

    def create_material(self, shader_type="stingray", stingray_opacity_mode="transparent"):
        """Create material with the silhouette texture.

        Parameters:
            shader_type: ``"stingray"`` (Unity-friendly StingrayPBS) or
                ``"standard"`` (standardSurface — cleanest VP2.0 preview but
                less direct mapping to Unity materials).
            stingray_opacity_mode: When ``shader_type="stingray"``:
                ``"transparent"`` (alpha blend; soft edges; faint preview tint)
                or ``"masked"`` (alpha test; hard edges; no preview tint).

        Material properties:
        - Base color: Black (shadow color)
        - Opacity: From texture alpha
        - Metallic: 0, Roughness: 1 (no reflections)
        """
        if not self.texture_path:
            raise ValueError("Texture not created yet")

        # Shared file/place2d setup
        file_node = cmds.shadingNode(
            "file", asTexture=True, name=f"{self.shadow_plane}_tex"
        )
        cmds.setAttr(f"{file_node}.fileTextureName", self.texture_path, type="string")
        place2d = cmds.shadingNode(
            "place2dTexture", asUtility=True, name=f"{self.shadow_plane}_place2d"
        )
        cmds.connectAttr(f"{place2d}.outUV", f"{file_node}.uv")
        cmds.connectAttr(f"{place2d}.outUvFilterSize", f"{file_node}.uvFilterSize")

        if shader_type == "stingray":
            # Always load a graph — a bare StingrayPBS node exposes none of
            # the attrs (base_color/use_opacity_map/etc.) the old code tried
            # to set, which silently fell back to standardSurface.
            self.shader = MatUtils.create_stingray_shader(
                f"{self.shadow_plane}_mat",
                opacity_mode=stingray_opacity_mode,
            )
            cmds.setAttr(f"{self.shader}.base_color", 0, 0, 0, type="double3")
            if cmds.attributeQuery("metallic", node=self.shader, exists=True):
                cmds.setAttr(f"{self.shader}.metallic", 0)
            if cmds.attributeQuery("roughness", node=self.shader, exists=True):
                cmds.setAttr(f"{self.shader}.roughness", 1)
            cmds.setAttr(f"{self.shader}.use_opacity_map", True)
            if stingray_opacity_mode == "masked":
                # TEX_mask_map is color3 — fan the scalar alpha into all three.
                for ch in ("X", "Y", "Z"):
                    cmds.connectAttr(
                        f"{file_node}.outAlpha",
                        f"{self.shader}.TEX_mask_map{ch}",
                        force=True,
                    )
            else:
                cmds.connectAttr(
                    f"{file_node}.outAlpha", f"{self.shader}.opacity", force=True
                )

            self.opacity_mult = cmds.shadingNode(
                "multiplyDivide",
                asUtility=True,
                name=f"{self.shadow_plane}_opacity_mult",
            )
            cmds.connectAttr(f"{file_node}.outAlpha", f"{self.opacity_mult}.input1X")
            cmds.setAttr(f"{self.opacity_mult}.input2X", 1.0)
            print(f"Created StingrayPBS material ({stingray_opacity_mode})")
        else:
            # standardSurface path (cleanest VP2.0 preview; Arnold's PBR).
            self.shader = cmds.shadingNode(
                "standardSurface", asShader=True, name=f"{self.shadow_plane}_mat"
            )
            cmds.setAttr(f"{self.shader}.baseColor", 0, 0, 0, type="double3")
            cmds.setAttr(f"{self.shader}.specular", 0)
            cmds.setAttr(f"{self.shader}.metalness", 0)
            cmds.setAttr(f"{self.shader}.specularRoughness", 1)

            self.opacity_mult = cmds.shadingNode(
                "multiplyDivide",
                asUtility=True,
                name=f"{self.shadow_plane}_opacity_mult",
            )
            for chan in ("X", "Y", "Z"):
                cmds.connectAttr(
                    f"{file_node}.outAlpha", f"{self.opacity_mult}.input1{chan}"
                )
            cmds.connectAttr(f"{self.opacity_mult}.output", f"{self.shader}.opacity")
            print("Created standardSurface material")

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{self.shader}_SG"
        )
        cmds.connectAttr(f"{self.shader}.outColor", f"{sg}.surfaceShader")
        cmds.sets(self.shadow_plane, fe=sg)

        return self.shader

    def setup_expression(self):
        """Create expression to warp shadow based on light position.

        Dispatches to mode-specific expression builder.
        """
        if self.mode == "orbit":
            self._expr_orbit()
        else:
            self._expr_stretch()

    def _expr_orbit(self):
        """Orbit mode: plane rotates around target to face away from light.

        The shadow plane pivots around the contact point, always pointing
        away from the light source. Scale stretches based on light angle.
        """
        expr_name = f"{self.shadow_plane}_expr"

        dm_name = f"{self._name_base}_contact_dm"
        if cmds.objExists(dm_name):
            cmds.delete(dm_name)
        dm_node = cmds.createNode("decomposeMatrix", name=dm_name)

        source = self.contact_locator if self.contact_locator else self.targets[0]
        cmds.connectAttr(f"{source}.worldMatrix[0]", f"{dm_node}.inputMatrix")

        expr_code = f"""
// ----------------------------------------------------------------------
// Shadow Projection Logic (Orbit Mode - Rotating Plane)
// ----------------------------------------------------------------------

// 1. Get World Positions
float $Lx = {self.light}.translateX;
float $Ly = {self.light}.translateY;
float $Lz = {self.light}.translateZ;

float $Cx = {dm_node}.outputTranslateX;
float $Cy = {dm_node}.outputTranslateY;
float $Cz = {dm_node}.outputTranslateZ;
float $Gy = {self.ground_height};

// 2. Calculate Direction from Light to Contact
float $dx = $Cx - $Lx;
float $dz = $Cz - $Lz;
float $dist2D = sqrt($dx * $dx + $dz * $dz);
float $relHeight = max(0.1, $Ly - $Gy);

// 3. Calculate Rotation (plane faces away from light)
float $angle = atan2($dx, $dz);
float $angleDeg = rad_to_deg($angle);

// 4. Calculate Scale (stretch based on light angle)
float $ratio = $dist2D / $relHeight;
float $limit = 4.0;
float $sz = 1.0 + clamp(0, $limit, $ratio);

// Apply manual scale influence
float $si = {self.shadow_plane}.scaleInfluence;
float $baseScale = 1.0;
if ($si > 0) {{
    float $hDiff = $Ly - $Cy;
    if ($hDiff > 0.1)
        $baseScale = 1.0 + (($Ly / $hDiff) - 1.0) * $si;
}}
$baseScale = clamp(0.5, 3.0, $baseScale);
$sz = $sz * $baseScale;

// 5. Calculate Position (pivot at contact, extend away from light)
float $size = {self.shadow_plane}.basePlaneSize;
float $radius = $size * 0.5;

// Offset along the direction away from light
float $offsetDist = $radius * ($sz - 1.0);
float $normX = ($dist2D > 0.001) ? ($dx / $dist2D) : 0;
float $normZ = ($dist2D > 0.001) ? ($dz / $dist2D) : 1;

float $tx = $Cx + $normX * $offsetDist;
float $tz = $Cz + $normZ * $offsetDist;

// 6. Apply Transforms
{self.shadow_plane}.translateX = $tx;
{self.shadow_plane}.translateZ = $tz;
{self.shadow_plane}.translateY = $Gy + 0.005;
{self.shadow_plane}.rotateY = $angleDeg;
{self.shadow_plane}.scaleX = 1.0;
{self.shadow_plane}.scaleZ = $sz;

// 7. Opacity Falloff
float $intensity = {self.shadow_plane}.shadowIntensity;
float $power = {self.shadow_plane}.falloffPower;

float $distOpacity = $intensity / max(0.001, pow($sz, $power));

float $heightDiff = $Ly - $Cy;
float $heightFade = clamp(0.0, 1.0, $heightDiff);

float $opacity = $distOpacity * $heightFade;
$opacity = clamp(0.0, 1.0, $opacity);

{self.opacity_mult}.input2X = $opacity;
{self.opacity_mult}.input2Y = $opacity;
{self.opacity_mult}.input2Z = $opacity;
"""
        if cmds.objExists(expr_name):
            cmds.delete(expr_name)

        cmds.expression(name=expr_name, string=expr_code, alwaysEvaluate=True)

    def _expr_stretch(self):
        """Stretch mode: plane stays axis-aligned, uses scale + translation.

        The shadow plane never rotates; instead it scales along X and Z
        independently, with compensatory translation to anchor the "heel"
        (the edge facing the light) at the contact point.
        """
        expr_name = f"{self.shadow_plane}_expr"

        dm_name = f"{self._name_base}_contact_dm"
        if cmds.objExists(dm_name):
            cmds.delete(dm_name)
        dm_node = cmds.createNode("decomposeMatrix", name=dm_name)

        source = self.contact_locator if self.contact_locator else self.targets[0]
        cmds.connectAttr(f"{source}.worldMatrix[0]", f"{dm_node}.inputMatrix")

        expr_code = f"""
// ----------------------------------------------------------------------
// Shadow Projection Logic (Stretch Mode - Axis-Aligned, Compensatory Translation)
// ----------------------------------------------------------------------

// 1. Get World Positions
float $Lx = {self.light}.translateX;
float $Ly = {self.light}.translateY;
float $Lz = {self.light}.translateZ;

float $Cx = {dm_node}.outputTranslateX;
float $Cy = {dm_node}.outputTranslateY;
float $Cz = {dm_node}.outputTranslateZ;
float $Gy = {self.ground_height};

// 2. Calculate Directions and Ratios
float $dx = $Cx - $Lx;
float $dz = $Cz - $Lz;
float $relHeight = max(0.1, $Ly - $Gy);

float $rx = abs($dx) / $relHeight;
float $rz = abs($dz) / $relHeight;

// 3. Calculate Scales
float $limit = 4.0;
float $sx = 1.0 + clamp(0, $limit, $rx);
float $sz = 1.0 + clamp(0, $limit, $rz);

// Apply manual scale influence
float $si = {self.shadow_plane}.scaleInfluence;
float $baseScale = 1.0;
if ($si > 0) {{
    float $hDiff = $Ly - $Cy;
    if ($hDiff > 0.1)
        $baseScale = 1.0 + (($Ly / $hDiff) - 1.0) * $si;
}}
$baseScale = clamp(0.5, 3.0, $baseScale);

$sx = $sx * $baseScale;
$sz = $sz * $baseScale;

// 4. Compensatory Translation (Virtual Pivot Logic)
float $size = {self.shadow_plane}.basePlaneSize;
float $radius = $size * 0.5;

float $px = ($dx > 0) ? -$radius : $radius;
float $pz = ($dz > 0) ? -$radius : $radius;

float $tx = $px * (1.0 - $sx);
float $tz = $pz * (1.0 - $sz);

// 5. Apply Transforms
{self.shadow_plane}.translateX = $Cx + $tx;
{self.shadow_plane}.translateZ = $Cz + $tz;
{self.shadow_plane}.translateY = $Gy + 0.005;
{self.shadow_plane}.rotateY = 0;
{self.shadow_plane}.scaleX = $sx;
{self.shadow_plane}.scaleZ = $sz;

// 6. Opacity Falloff
float $intensity = {self.shadow_plane}.shadowIntensity;
float $power = {self.shadow_plane}.falloffPower;
float $maxStretch = max($sx, $sz);

float $distOpacity = $intensity / max(0.001, pow($maxStretch, $power));

float $heightDiff = $Ly - $Cy;
float $heightFade = clamp(0.0, 1.0, $heightDiff);

float $opacity = $distOpacity * $heightFade;
$opacity = clamp(0.0, 1.0, $opacity);

{self.opacity_mult}.input2X = $opacity;
{self.opacity_mult}.input2Y = $opacity;
{self.opacity_mult}.input2Z = $opacity;
"""
        if cmds.objExists(expr_name):
            cmds.delete(expr_name)

        cmds.expression(name=expr_name, string=expr_code, alwaysEvaluate=True)

    @classmethod
    def create(
        cls,
        targets,
        light_pos=(5, 10, 5),
        texture_res=512,
        axis="auto",
        source_name="shadow_source",
        recursive=True,
        mode="stretch",
    ):
        """Create a projected shadow for Unity export.

        Args:
            targets: Object(s) to cast shadow from. Can be a single object
                     or a list of objects for a combined shadow.
            light_pos: Initial position for light locator
            texture_res: Resolution of silhouette texture
            axis: Silhouette projection axis:
                  'auto' = best axis based on bounding box (default)
                  'z' = front/back view
                  'x' = side view
                  'y' = top-down view
            source_name: Name for the shadow source locator. Use different
                         names to create separate sources.
            recursive: If True, include descendant meshes in shadow.
            mode: Rig behavior mode:
                  'orbit' = plane rotates around target to face away from light
                  'stretch' = plane stays axis-aligned, scales + translates (default)

        Returns:
            ShadowRig instance
        """
        shadow = cls(targets=targets, mode=mode)
        shadow.get_or_create_shadow_source(position=light_pos, source_name=source_name)
        shadow.create_contact_locator()
        shadow.create_shadow_plane()
        shadow.create_silhouette_texture(
            size=texture_res, axis=axis, recursive=recursive
        )
        shadow.create_material()
        shadow.setup_expression()

        grp = cmds.group(empty=True, name=f"{shadow._name_base}_shadow_grp")
        cmds.parent(shadow.shadow_plane, grp)
        # contact_locator stays on first target

        target_names = ", ".join(str(t) for t in shadow.targets)
        print(f"\nCreated shadow for: {target_names}")
        print(f"  Mode: {shadow.mode}")
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

        # Preview wraps perform_operation in an undo chunk so toggling the
        # checkbox builds the rig, tweaking any option refreshes it, and
        # clicking b000 (Create Shadow) commits.
        self.preview = Preview(
            self, self.ui.chk_preview, self.ui.b000, message_func=self.sb.message_box
        )

        # Any option change should re-bake the previewed rig.
        self.ui.cmb_mode.currentIndexChanged.connect(self.preview.refresh)
        self.ui.chk_combine.toggled.connect(self.preview.refresh)
        self.ui.txt_source.editingFinished.connect(self.preview.refresh)
        self.ui.s000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        # b001 is auto-wired by the switchboard (method name == objectName);
        # a raw connect here stacked a second connection → double-fire.

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly "
                "for game engines (Unity, etc.). The plane carries a baked "
                "silhouette PNG; its transform is driven by an expression "
                "you can bake to keyframes before FBX export.",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> to build the rig live.",
                    "Tweak <b>Mode</b>, <b>Resolution</b>, <b>Axis</b>, and "
                    "<b>Combine</b>. The preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview "
                    "to discard.",
                ],
                sections=[
                    ("Modes", [
                        "<b>Orbit</b> — Plane rotates around the target to face "
                        "away from the light. Simple, shadow always points away.",
                        "<b>Stretch</b> — Plane stays axis-aligned; uses scale "
                        "and compensatory translation to warp the shadow. Better "
                        "for baking, but the silhouette may appear mirrored at "
                        "extreme light angles.",
                    ]),
                ],
                notes=[
                    "For Unity export: bake the simulation (Edit ▸ Keys ▸ Bake "
                    "Simulation), export FBX with the shadow plane + texture, "
                    "and use an Unlit/Transparent shader in Unity.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects, contract):
        """Build the shadow rig for the given targets.

        Called by Preview during the hermetic preview phase (contract is a
        CleanupContract) and again during commit (contract is None).
        """
        targets = list(objects) if objects else []
        if not targets:
            return

        # Resolution combobox: extract numeric value from text like "Resolution: 512"
        res_text = self.ui.s000.currentText()
        resolution = int(res_text.replace("Resolution: ", ""))
        source_name = self.ui.txt_source.text().strip() or "shadow_source"
        recursive = self.ui.chk_combine.isChecked()

        # Axis combobox: 0=Auto, 1=X, 2=Y, 3=Z
        axis_idx = self.ui.cmb000.currentIndex()
        axis_map = {0: "auto", 1: "x", 2: "y", 3: "z"}
        axis = axis_map.get(axis_idx, "auto")

        # Mode combobox: 0=Stretch, 1=Orbit
        mode_idx = self.ui.cmb_mode.currentIndex()
        mode_map = {0: "stretch", 1: "orbit"}
        mode = mode_map.get(mode_idx, "stretch")

        rig = ShadowRig.create(
            targets,
            texture_res=resolution,
            axis=axis,
            source_name=source_name,
            recursive=recursive,
            mode=mode,
        )
        if contract is not None and rig.texture_path:
            contract.add_file(rig.texture_path)


if __name__ == "__main__":
    sel = cmds.ls(selection=True) or []
    if not sel:
        print("Select object(s) first.")
    else:
        ShadowRig.create(sel)
