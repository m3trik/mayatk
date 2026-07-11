# !/usr/bin/python
# coding=utf-8
import os
import math

import numpy as np

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# From this package:
from mayatk import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.core_utils.preview import Preview


class ShadowRig(ptk.LoggingMixin):
    """Projected shadow for Unity export.

    Creates a simple quad plane with the object's silhouette rendered as a
    PNG texture. The plane transforms (position, rotation, scale) are driven
    by an expression that can be baked to keyframes for FBX export.

    Modes:
        - "orbit": Plane rotates around the target to face away from light.
                   Correct for animated lights (the silhouette never mirrors).
        - "stretch": Plane stays axis-aligned; uses scale + compensatory
                     translation to warp shadow. Bake-friendly, but the
                     silhouette mirrors if the light crosses to the opposite
                     side of the target — prefer orbit for orbiting lights.

    Shadow behavior (both modes):
        - The plane is anchored at the *projected* ground contact — where the
          light ray through the contact point hits the ground — so the shadow
          slides away from the light as the target leaves the ground.
        - Stretch amount is proportional to the target's measured height
          (``objectHeight``): tall objects cast long shadows, flat ones don't.
        - Opacity fades with stretch (``falloffPower``), with the light
          dropping toward the contact, and with the target rising off the
          ground (``fadeHeight`` = rise at which the shadow is fully gone).

    Workflow for Unity:
    1. Create shadow with ShadowRig.create()
    2. Bake with ShadowRig.bake() (or the panel's Bake to Keyframes button)
    3. Export through the Scene Exporter — the rig publishes a
       ``shadow_metadata`` channel on the ``data_export`` carrier
       (refreshed at export time via FbxUtils.run_export_preparers), and
       the silhouette PNG goes into the Unity project alongside.
    4. In Unity: with unitytk's ShadowPlaneController.cs deployed, the
       import is automatic (unlit-transparent material bound to the
       silhouette, shadow casting/receiving + probes off). Without it,
       assign an Unlit/Transparent shader with the PNG by hand.

    Note: the opacity falloff is shader-side (Maya preview only) — FBX does
    not carry material animation into Unity. The baked plane transform does.
    """

    MODES = ("orbit", "stretch")
    # Lift above the ground plane to avoid z-fighting (build + expression).
    GROUND_OFFSET = 0.01
    # Channels the mode expressions drive (and bake() keys).
    BAKE_CHANNELS = ("translateX", "translateY", "translateZ", "rotateY", "scaleX", "scaleZ")
    # data_export carrier channel (see refresh_export_metadata).
    SHADOW_METADATA = "shadow_metadata"

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
        self.group = None
        self.plane_size = 1.0
        self.object_height = 0.0
        self.mode = mode if mode in self.MODES else "stretch"

        # For naming, use first target or "combined" — uniquified against
        # existing rigs: every rig node/texture is named off this base, and a
        # collision (two multi-target "combined" rigs, or re-creating a
        # target's rig) would delete the older rig's decomposeMatrix nodes
        # out from under its expression and overwrite its silhouette PNG.
        base = str(self.targets[0]) if len(self.targets) == 1 else "combined"
        i, unique = 0, base
        while cmds.objExists(f"{unique}_shadow_grp"):
            i += 1
            unique = f"{base}{i}"
        self._name_base = unique

    def _world_bbox(self):
        """``exactWorldBoundingBox`` over the targets' MESH geometry only.

        Helper shapes parented under a target — this rig's own contact
        locator, most notably — must not pollute the measurement (the
        locator sits at min-Y, so including it inflates ``objectHeight``
        by its display size). Mirrors blendertk's empty-skipping bounds.
        """
        shapes = []
        for t in self.targets:
            shapes += cmds.ls(t, type="mesh") or []  # target may BE a shape
            shapes += (
                cmds.listRelatives(t, ad=True, type="mesh", fullPath=True) or []
            )
        # Intermediate (Orig) shapes hold pre-deformation geometry — a skinned
        # target posed away from bind pose would union its bind pose into the
        # measurement, skewing objectHeight / plane size / center.
        shapes = cmds.ls(shapes, noIntermediate=True) or []
        if not shapes:
            return cmds.exactWorldBoundingBox(self.targets)
        return cmds.exactWorldBoundingBox(shapes)

    def create_contact_locator(self):
        """Create a locator at the lowest point of the combined objects to act as the shadow anchor."""
        bbox = self._world_bbox()
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
            self.logger.info(f"Using existing shadow source: {self.light}")
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

    def _ensure_plane_attr(self, ln, dv, min_val=None, max_val=None, keyable=True):
        """Add a float attr to the shadow plane if it doesn't exist yet."""
        if not cmds.attributeQuery(ln, node=self.shadow_plane, exists=True):
            kwargs = {"ln": ln, "at": "float", "dv": dv, "k": keyable}
            if min_val is not None:
                kwargs["min"] = min_val
            if max_val is not None:
                kwargs["max"] = max_val
            cmds.addAttr(self.shadow_plane, **kwargs)

    def create_shadow_plane(self):
        """Create a simple quad for the shadow with the keyable shadow attrs."""
        if not self.targets:
            raise ValueError("Target object(s) required")

        # Get combined footprint size + height from all targets
        bbox = self._world_bbox()
        width = (bbox[3] - bbox[0]) * 1.1
        depth = (bbox[5] - bbox[2]) * 1.1
        self.plane_size = max(width, depth, 1.0)
        self.object_height = max(bbox[4] - bbox[1], 0.001)

        self.shadow_plane = cmds.polyPlane(
            name=f"{self._name_base}_shadow",
            width=self.plane_size,
            height=self.plane_size,
            sx=1,
            sy=1,
            axis=(0, 1, 0),
        )[0]

        # Art-direction attrs (keyable) + measured constants the expression reads.
        self._ensure_plane_attr("shadowIntensity", 1.0, 0.0, 1.0)
        self._ensure_plane_attr("falloffPower", 1.2, 0.0, 5.0)
        self._ensure_plane_attr("scaleInfluence", 0.0, 0.0, 1.0)
        self._ensure_plane_attr("maxStretch", 4.0, 0.0, 10.0)
        # Rise above the ground at which the shadow has fully faded out.
        self._ensure_plane_attr("fadeHeight", max(2.0 * self.object_height, 0.001), 0.0)
        self._ensure_plane_attr("basePlaneSize", self.plane_size, keyable=False)
        self._ensure_plane_attr("objectHeight", self.object_height, keyable=False)
        # Measured constants are always restamped to this build's values.
        cmds.setAttr(f"{self.shadow_plane}.basePlaneSize", self.plane_size)
        cmds.setAttr(f"{self.shadow_plane}.objectHeight", self.object_height)

        # Keep plane centered - pivot at center, vertices centered around origin
        # The expression handles positioning based on light direction
        cmds.xform(self.shadow_plane, pivots=[0, 0, 0], objectSpace=True)

        # Position at combined targets center
        center_x = (bbox[0] + bbox[3]) / 2.0
        center_z = (bbox[2] + bbox[5]) / 2.0
        cmds.setAttr(
            f"{self.shadow_plane}.translate",
            center_x,
            self.ground_height + self.GROUND_OFFSET,
            center_z,
            type="double3",
        )

        return self.shadow_plane

    def _gather_world_meshes(self, recursive=True):
        """``[(points, tris)]`` world-space arrays for every target mesh shape.

        Walks transforms first so instanced shapes yield one path per parent
        (otherwise listRelatives dedupes by node and we miss instance copies).
        """
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
                # noIntermediate: an Orig shape holds pre-deformation geometry
                # — rasterizing it draws the bind pose into the silhouette of
                # a posed/skinned target (mirrors _world_bbox's filter).
                tx_shapes = (
                    cmds.listRelatives(
                        tx, shapes=True, type="mesh", fullPath=True,
                        noIntermediate=True,
                    )
                    or []
                )
                target_shapes.extend(tx_shapes)

            if not target_shapes:
                direct_shapes = NodeUtils.get_shapes(target, no_intermediate=True)
                if direct_shapes:
                    target_shapes = direct_shapes

            shapes.extend(target_shapes)

        meshes = []
        for shape in shapes:
            try:
                sel_list = om2.MSelectionList()
                sel_list.add(str(shape))
                fn_mesh = om2.MFnMesh(sel_list.getDagPath(0))
                points = fn_mesh.getPoints(om2.MSpace.kWorld)
                _, tri_verts = fn_mesh.getTriangles()
                pts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
                tris = np.array(tri_verts, dtype=np.int64).reshape(-1, 3)
                if len(pts) and len(tris):
                    meshes.append((pts, tris))
            except Exception as e:
                self.logger.warning(f"Could not process shape {shape}: {e}")
        return meshes

    def _light_view_basis(self):
        """Horizontal unit bearing ``(dx, dz)`` from the light to the targets'
        center, or None when the light is absent or (near) directly overhead."""
        if not self.light or not cmds.objExists(self.light):
            return None
        bbox = self._world_bbox()
        cx = (bbox[0] + bbox[3]) / 2.0
        cz = (bbox[2] + bbox[5]) / 2.0
        lp = cmds.xform(self.light, q=True, ws=True, t=True)
        dx, dz = cx - lp[0], cz - lp[2]
        d = math.hypot(dx, dz)
        if d < 1e-4:
            return None
        return dx / d, dz / d

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
        """Create the silhouette texture via ``pythontk.ImgUtils.rasterize_silhouette``.

        Args:
            size: Texture resolution.
            axis: Projection axis - 'light', 'x', 'y', 'z', or 'auto' (default).
                  'light' projects the silhouette as seen from the light's
                  horizontal bearing — the physically correct shape for a
                  ground shadow. 'auto' = 'light' when a source exists (falling
                  back to the axis perpendicular to the widest dimension when
                  the light is directly overhead or not yet created).
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
        from PIL import Image

        workspace = cmds.workspace(q=True, rd=True)
        output_dir = os.path.join(workspace, "sourceimages")
        os.makedirs(output_dir, exist_ok=True)
        self.texture_path = os.path.join(output_dir, f"{self._name_base}_shadow.png")

        meshes = self._gather_world_meshes(recursive)
        if not meshes:
            raise ValueError("No mesh geometry found on the target(s).")

        axis = str(axis).lower()
        raster_axis = axis
        if axis in ("auto", "light"):
            basis = self._light_view_basis()
            if basis is not None:
                # Rebase points into the light's view frame: u = the horizontal
                # coordinate perpendicular to the bearing (preserved by the
                # ground projection — u_axis = (dz, 0, -dx)), v = world up.
                # Rasterize as a front ('z') view of that frame.
                dx_n, dz_n = basis
                meshes = [
                    (
                        np.column_stack(
                            [
                                pts[:, 0] * dz_n - pts[:, 2] * dx_n,
                                pts[:, 1],
                                np.zeros(len(pts)),
                            ]
                        ),
                        tris,
                    )
                    for pts, tris in meshes
                ]
                raster_axis = "z"
            else:
                raster_axis = "auto"  # widest-dimension fallback

        rgba = ptk.ImgUtils.rasterize_silhouette(
            meshes,
            size=size,
            axis=raster_axis,
            uniform_alpha=uniform_alpha,
            falloff_source=falloff_source,
            falloff_power=falloff_power,
            vertical_weight=vertical_weight,
            blur_amount=blur_amount,
        )
        Image.fromarray(rgba, "RGBA").save(self.texture_path)

        self.logger.info(
            f"Created silhouette texture: {self.texture_path} (axis={axis})"
        )
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

            # Route the alpha THROUGH the fade multiplier — feeding the file's
            # outAlpha straight into the shader left opacity_mult.output
            # dangling, so the expression-driven rise fade (input2X) had no
            # visible effect on the default material.
            self.opacity_mult = cmds.shadingNode(
                "multiplyDivide",
                asUtility=True,
                name=f"{self.shadow_plane}_opacity_mult",
            )
            cmds.connectAttr(f"{file_node}.outAlpha", f"{self.opacity_mult}.input1X")
            cmds.setAttr(f"{self.opacity_mult}.input2X", 1.0)
            if stingray_opacity_mode == "masked":
                # TEX_mask_map is color3 — fan the scalar alpha into all three.
                for ch in ("X", "Y", "Z"):
                    cmds.connectAttr(
                        f"{self.opacity_mult}.outputX",
                        f"{self.shader}.TEX_mask_map{ch}",
                        force=True,
                    )
            else:
                cmds.connectAttr(
                    f"{self.opacity_mult}.outputX",
                    f"{self.shader}.opacity",
                    force=True,
                )
            self.logger.info(f"Created StingrayPBS material ({stingray_opacity_mode})")
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
            self.logger.info("Created standardSurface material")

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

    def _make_world_decompose(self, node, suffix):
        """A ``decomposeMatrix`` on ``node.worldMatrix`` — world position even
        when the node is parented/grouped (raw ``.translate`` is local)."""
        name = f"{self._name_base}_{suffix}_dm"
        if cmds.objExists(name):
            cmds.delete(name)
        dm = cmds.createNode("decomposeMatrix", name=name)
        cmds.connectAttr(f"{node}.worldMatrix[0]", f"{dm}.inputMatrix")
        return dm

    def _expr_common(self, light_dm, contact_dm):
        """Shared expression prologue: world positions, light->contact
        direction, and the projected ground anchor the plane hangs off."""
        return f"""
// 1. World Positions (decomposeMatrix — correct even when parented/grouped)
float $Lx = {light_dm}.outputTranslateX;
float $Ly = {light_dm}.outputTranslateY;
float $Lz = {light_dm}.outputTranslateZ;

float $Cx = {contact_dm}.outputTranslateX;
float $Cy = {contact_dm}.outputTranslateY;
float $Cz = {contact_dm}.outputTranslateZ;
float $Gy = {self.ground_height};

// 2. Light->Contact Direction and Elevation
float $dx = $Cx - $Lx;
float $dz = $Cz - $Lz;
float $relHeight = max(0.1, $Ly - $Gy);

// 3. Projected Ground Anchor: where the light ray through the contact hits
// the ground. Equals the contact while the target is grounded; slides away
// from the light as the target rises (clamped against blowup when the light
// drops to the contact's height).
float $k = clamp(0.0, 10.0, ($Ly - $Gy) / max(0.1, $Ly - $Cy));
float $Sx = $Lx + $dx * $k;
float $Sz = $Lz + $dz * $k;

// 4. Plane Constants
float $size = {self.shadow_plane}.basePlaneSize;
float $objH = {self.shadow_plane}.objectHeight;
float $lim = {self.shadow_plane}.maxStretch;
float $radius = $size * 0.5;

// Manual scale influence (art-directed grow)
float $si = {self.shadow_plane}.scaleInfluence;
float $baseScale = 1.0;
if ($si > 0) {{
    float $hDiff = $Ly - $Cy;
    if ($hDiff > 0.1)
        $baseScale = 1.0 + (($Ly / $hDiff) - 1.0) * $si;
}}
$baseScale = clamp(0.5, 3.0, $baseScale);
"""

    def _expr_opacity(self, stretch_term):
        """Shared expression epilogue: opacity = distance falloff x light-height
        fade x rise fade (target leaving the ground)."""
        return f"""
// Opacity Falloff
float $intensity = {self.shadow_plane}.shadowIntensity;
float $power = {self.shadow_plane}.falloffPower;
float $fadeH = {self.shadow_plane}.fadeHeight;

float $distOpacity = $intensity / max(0.001, pow({stretch_term}, $power));
float $heightFade = clamp(0.0, 1.0, $Ly - $Cy);
float $riseFade = clamp(0.0, 1.0, 1.0 - max(0.0, $Cy - $Gy) / max(0.001, $fadeH));

float $opacity = clamp(0.0, 1.0, $distOpacity * $heightFade * $riseFade);

{self.opacity_mult}.input2X = $opacity;
{self.opacity_mult}.input2Y = $opacity;
{self.opacity_mult}.input2Z = $opacity;
"""

    def _build_expression(self, body):
        """(Re)create the plane's expression node from the mode body."""
        expr_name = f"{self.shadow_plane}_expr"
        if cmds.objExists(expr_name):
            cmds.delete(expr_name)
        cmds.expression(name=expr_name, string=body, alwaysEvaluate=True)

    def _expr_orbit(self):
        """Orbit mode: plane rotates around target to face away from light.

        The shadow plane pivots around the projected anchor, always pointing
        away from the light source. Scale stretches based on light angle.
        """
        contact = self.contact_locator if self.contact_locator else self.targets[0]
        contact_dm = self._make_world_decompose(contact, "contact")
        light_dm = self._make_world_decompose(self.light, "light")

        expr_code = self._expr_common(light_dm, contact_dm) + f"""
// 5. Rotation (plane faces away from light)
float $angleDeg = rad_to_deg(atan2($dx, $dz));

// 6. Depth Stretch (shadow length ~ objectHeight x horizontal-offset/light-height)
float $dist2D = sqrt($dx * $dx + $dz * $dz);
float $sz = (1.0 + clamp(0.0, $lim, ($objH * ($dist2D / $relHeight)) / $size)) * $baseScale;

// 7. Position (anchored at the projected contact, extending away from light)
float $offsetDist = $radius * ($sz - 1.0);
float $normX = ($dist2D > 0.001) ? ($dx / $dist2D) : 0;
float $normZ = ($dist2D > 0.001) ? ($dz / $dist2D) : 1;

{self.shadow_plane}.translateX = $Sx + $normX * $offsetDist;
{self.shadow_plane}.translateZ = $Sz + $normZ * $offsetDist;
{self.shadow_plane}.translateY = $Gy + {self.GROUND_OFFSET};
{self.shadow_plane}.rotateY = $angleDeg;
{self.shadow_plane}.scaleX = 1.0;
{self.shadow_plane}.scaleZ = $sz;
""" + self._expr_opacity("$sz")
        self._build_expression(expr_code)

    def _expr_stretch(self):
        """Stretch mode: plane stays axis-aligned, uses scale + translation.

        The shadow plane never rotates; instead it scales along X and Z
        independently, with compensatory translation to anchor the "heel"
        (the edge facing the light) at the projected contact point.
        """
        contact = self.contact_locator if self.contact_locator else self.targets[0]
        contact_dm = self._make_world_decompose(contact, "contact")
        light_dm = self._make_world_decompose(self.light, "light")

        expr_code = self._expr_common(light_dm, contact_dm) + f"""
// 5. Axis Stretch (shadow length ~ objectHeight x horizontal-offset/light-height)
float $rx = abs($dx) / $relHeight;
float $rz = abs($dz) / $relHeight;
float $sx = (1.0 + clamp(0.0, $lim, ($objH * $rx) / $size)) * $baseScale;
float $sz = (1.0 + clamp(0.0, $lim, ($objH * $rz) / $size)) * $baseScale;

// 6. Compensatory Translation (heel anchored at the projected contact)
float $px = ($dx > 0) ? -$radius : $radius;
float $pz = ($dz > 0) ? -$radius : $radius;

{self.shadow_plane}.translateX = $Sx + $px * (1.0 - $sx);
{self.shadow_plane}.translateZ = $Sz + $pz * (1.0 - $sz);
{self.shadow_plane}.translateY = $Gy + {self.GROUND_OFFSET};
{self.shadow_plane}.rotateY = 0;
{self.shadow_plane}.scaleX = $sx;
{self.shadow_plane}.scaleZ = $sz;
""" + self._expr_opacity("max($sx, $sz)")
        self._build_expression(expr_code)

    # ------------------------------------------------------------------ bake
    def bake(self, start=None, end=None):
        """Bake this rig's driven channels to keyframes and remove the live
        expression (FBX-ready). See :meth:`bake_planes`."""
        return self.bake_planes([self.shadow_plane], start=start, end=end)

    # ------------------------------------------------------------------ export metadata
    @staticmethod
    def _plane_texture_path(plane):
        """Full path of the plane's silhouette texture — the file node driving
        the assigned material's OPACITY chain (SSoT; survives retexturing).

        Walks the opacity plugs only (stingray ``.opacity`` /
        ``TEX_mask_mapX``, or standardSurface ``.opacity`` via the
        ``*_opacity_mult`` multiplyDivide) rather than the whole network:
        ``listHistory`` doesn't traverse a ShaderFX (StingrayPBS) node's
        inputs, and its loaded graph carries three stock IBL preset file
        nodes a material-wide search would wrongly match.
        """
        shapes = cmds.listRelatives(plane, shapes=True, fullPath=True) or []
        for shape in shapes:
            for sg in cmds.listConnections(shape, type="shadingEngine") or []:
                shaders = (
                    cmds.listConnections(f"{sg}.surfaceShader", source=True) or []
                )
                for shader in shaders:
                    queue = [
                        src
                        for attr in ("opacity", "TEX_mask_mapX")
                        if cmds.attributeQuery(attr, node=shader, exists=True)
                        for src in (
                            cmds.listConnections(
                                f"{shader}.{attr}", source=True, destination=False
                            )
                            or []
                        )
                    ]
                    seen = set()
                    while queue:
                        node = queue.pop(0)
                        if node in seen:
                            continue
                        seen.add(node)
                        if cmds.nodeType(node) == "file":
                            path = cmds.getAttr(f"{node}.fileTextureName")
                            if path:
                                return path
                        else:
                            queue += (
                                cmds.listConnections(
                                    node, source=True, destination=False
                                )
                                or []
                            )
        return None

    @classmethod
    def refresh_export_metadata(cls):
        """Republish the ``shadow_metadata`` channel on the ``data_export``
        carrier from the scene's shadow planes.

        The canonical, no-arg pre-export refresh for the shadow rig — wired
        into ``FbxUtils._KNOWN_PRODUCERS`` so the Scene Exporter (and any
        ``run_export_preparers`` caller) ships a current channel. The payload
        joins Unity-side by GameObject name (unitytk's
        ``ShadowPlaneController.cs``):

        ``{"version": 1, "planes": [{"name", "texture", "intensity"}]}``

        Clears the channel when the scene has no shadow planes (no empty
        carrier left behind).

        Returns:
            The published JSON string, or None when cleared.
        """
        import json

        from mayatk.core_utils._core_utils import leaf_name
        from mayatk.node_utils.data_nodes import DataNodes

        planes = cls.find_shadow_planes()
        if not planes:
            DataNodes.set_export_string(cls.SHADOW_METADATA, "")
            return None
        records = []
        for plane in planes:
            tex = cls._plane_texture_path(plane)
            intensity = (
                cmds.getAttr(f"{plane}.shadowIntensity")
                if cmds.attributeQuery("shadowIntensity", node=plane, exists=True)
                else 1.0
            )
            records.append(
                {
                    "name": leaf_name(plane),
                    "texture": os.path.basename(tex) if tex else "",
                    "intensity": round(float(intensity), 4),
                }
            )
        payload = json.dumps({"version": 1, "planes": records})
        DataNodes.set_export_string(cls.SHADOW_METADATA, payload)
        return payload

    @classmethod
    def find_shadow_planes(cls, nodes=None):
        """Shadow planes = transforms carrying the stamped ``basePlaneSize``
        attr. ``nodes`` limits the search (their descendants included, so a
        selected ``*_shadow_grp`` finds its plane); None scans the scene."""
        if nodes:
            # Selections can carry non-DAG nodes (shaders, sets) — filter to
            # transforms before walking descendants.
            pool = cmds.ls([str(n) for n in nodes], transforms=True) or []
            if pool:
                # fullPath: bare leaf names are ambiguous under duplicate
                # transform names and crash attributeQuery; ls() normalizes
                # back to shortest-unique (matching the scene-scan branch).
                kids = (
                    cmds.listRelatives(
                        pool, ad=True, type="transform", fullPath=True
                    )
                    or []
                )
                pool = cmds.ls(pool + kids) or []
        else:
            pool = cmds.ls(type="transform") or []
        return [
            n
            for n in dict.fromkeys(pool)
            if cmds.attributeQuery("basePlaneSize", node=n, exists=True)
        ]

    @classmethod
    def bake_planes(cls, planes=None, start=None, end=None):
        """Bake shadow planes' expression-driven channels to keyframes and
        delete the live rig nodes (expression + decomposeMatrix) so the
        result exports cleanly to FBX.

        Args:
            planes: Shadow plane transform(s); None bakes every shadow plane
                in the scene that still has a live expression.
            start/end: Frame range; defaults to the playback range.

        Returns:
            The list of planes that were baked.

        Note: the shader-side opacity fade freezes at its last evaluated
        value (FBX carries no material animation into Unity anyway).
        """
        planes = cls.find_shadow_planes(planes)
        if start is None:
            start = cmds.playbackOptions(q=True, min=True)
        if end is None:
            end = cmds.playbackOptions(q=True, max=True)

        baked = []
        for plane in planes:
            # Resolve the driving expression via connections, not by name —
            # robust to path-qualified plane names and suffixed expr nodes.
            exprs = set(
                cmds.listConnections(
                    plane, source=True, destination=False, type="expression"
                )
                or []
            )
            if not exprs:
                continue  # already baked / hand-keyed
            dm_nodes = set()
            for expr in exprs:
                dm_nodes.update(
                    cmds.listConnections(
                        expr, source=True, destination=False, type="decomposeMatrix"
                    )
                    or []
                )
            plugs = [f"{plane}.{ch}" for ch in cls.BAKE_CHANNELS]
            cmds.bakeResults(
                plugs,
                time=(start, end),
                simulation=True,
                sampleBy=1,
                disableImplicitControl=True,
                preserveOutsideKeys=False,
            )
            for node in exprs | dm_nodes:
                if cmds.objExists(node):
                    cmds.delete(node)
            baked.append(plane)
        if baked:
            cls.refresh_export_metadata()
        return baked

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
        ground_height=0.0,
    ):
        """Create a projected shadow for Unity export.

        Args:
            targets: Object(s) to cast shadow from. Can be a single object
                     or a list of objects for a combined shadow.
            light_pos: Initial position for light locator
            texture_res: Resolution of silhouette texture
            axis: Silhouette projection axis:
                  'auto' / 'light' = as seen from the light's horizontal
                           bearing (default; physically correct shape — both
                           fall back to the widest-dimension heuristic when
                           the light is directly overhead)
                  'z' = front/back view
                  'x' = side view
                  'y' = top-down view
            source_name: Name for the shadow source locator. Use different
                         names to create separate sources.
            recursive: If True, include descendant meshes in shadow.
            mode: Rig behavior mode:
                  'orbit' = plane rotates around target to face away from light
                  'stretch' = plane stays axis-aligned, scales + translates (default)
            ground_height: World Y of the ground plane the shadow lies on.

        Returns:
            ShadowRig instance
        """
        shadow = cls(targets=targets, mode=mode, ground_height=ground_height)
        shadow.get_or_create_shadow_source(position=light_pos, source_name=source_name)
        shadow.create_contact_locator()
        shadow.create_shadow_plane()
        shadow.create_silhouette_texture(
            size=texture_res, axis=axis, recursive=recursive
        )
        shadow.create_material()
        shadow.setup_expression()

        shadow.group = cmds.group(empty=True, name=f"{shadow._name_base}_shadow_grp")
        # Re-capture: parenting can path-qualify the name under a collision
        # (same lesson as Controls.create — the stale ref stops resolving).
        shadow.shadow_plane = cmds.parent(shadow.shadow_plane, shadow.group)[0]
        # contact_locator stays on first target

        # Publish the engine hand-off record onto the data_export carrier (the
        # Scene Exporter re-refreshes it at export time via run_export_preparers).
        cls.refresh_export_metadata()

        target_names = ", ".join(str(t) for t in shadow.targets)
        shadow.logger.success(
            f"Shadow rig for {target_names} ({shadow.mode}) — plane "
            f"{shadow.shadow_plane}, source {shadow.light}, "
            f"texture {shadow.texture_path}"
        )
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
        # b001/b002 are auto-wired by the switchboard (method name ==
        # objectName); a raw connect here stacked a second connection →
        # double-fire.

        self._init_tooltips()

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Shadow Rig",
                body="Create a projected-shadow plane rig that exports cleanly "
                "for game engines (Unity, etc.). The plane carries a baked "
                "silhouette PNG (rendered as seen from the light); its "
                "transform is driven by an expression and anchored at the "
                "light ray's projected ground contact, so the shadow slides, "
                "stretches, and fades realistically as the target or light "
                "moves.",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> to build the rig live.",
                    "Tweak <b>Mode</b>, <b>Resolution</b>, <b>Axis</b>, and "
                    "<b>Combine</b>. The preview refreshes on each change.",
                    "Press <b>Create Shadow</b> to commit, or disable Preview "
                    "to discard.",
                    "Press <b>Bake to Keyframes</b> to bake the expression to "
                    "keys over the playback range (FBX-ready).",
                    "Export through the <b>Scene Exporter</b> — the rig's "
                    "<i>shadow_metadata</i> rides the data_export carrier "
                    "automatically.",
                ],
                sections=[
                    ("Modes", [
                        "<b>Orbit</b> — Plane rotates around the target to face "
                        "away from the light. Correct for animated/orbiting "
                        "lights.",
                        "<b>Stretch</b> — Plane stays axis-aligned; uses scale "
                        "and compensatory translation to warp the shadow. Bake-"
                        "friendly, but the silhouette mirrors if the light "
                        "crosses to the target's opposite side.",
                    ]),
                    ("Plane attributes", [
                        "<b>shadowIntensity</b> / <b>falloffPower</b> — overall "
                        "strength and distance falloff.",
                        "<b>maxStretch</b> — clamp on shadow elongation.",
                        "<b>fadeHeight</b> — rise off the ground at which the "
                        "shadow has fully faded.",
                        "<b>scaleInfluence</b> — art-directed extra grow.",
                    ]),
                ],
                notes=[
                    "Unity plug-and-play: deploy unitytk's C# templates once "
                    "(<i>unitytk.deploy_templates</i>), export via the Scene "
                    "Exporter, and copy the silhouette PNG into Assets — the "
                    "import sets up the unlit-transparent material and shadow "
                    "flags automatically. Other engines: assign an "
                    "unlit/transparent shader with the PNG by hand.",
                    "The opacity fade is shader-side (preview only) — FBX "
                    "carries the plane's transform animation, not material "
                    "animation.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        ui = self.ui

        ui.cmb_mode.setToolTip(
            fmt(
                title="Rig Mode",
                body="How the shadow plane reacts to the light's position.",
                sections=[
                    (
                        "Stretch",
                        [
                            "Plane stays axis-aligned; scale and compensatory "
                            "translation warp the shadow.",
                            "Bake-friendly.",
                            "Silhouette mirrors if the light crosses to the "
                            "target's opposite side.",
                        ],
                    ),
                    (
                        "Orbit",
                        [
                            "Plane rotates around the target to face away from "
                            "the light.",
                            "Correct for animated / orbiting lights.",
                        ],
                    ),
                ],
            )
        )
        ui.chk_combine.setToolTip(
            fmt(
                title="Include Children",
                body="Include the selected objects' descendant meshes in the "
                "baked silhouette.",
                notes=[
                    "The selection always shares a single combined shadow "
                    "plane.",
                    "Off — only the selected meshes themselves are "
                    "rasterized.",
                ],
            )
        )
        ui.txt_source.setToolTip(
            fmt(
                title="Source Name",
                body="Name for the shadow-source locator that anchors the "
                "projection.",
                notes=[
                    "Reuse a name to share one source; use distinct names for "
                    "separate shadow sources.",
                ],
            )
        )
        ui.s000.setToolTip(
            fmt(
                title="Texture Resolution",
                body="Pixel resolution of the baked silhouette PNG carried by "
                "the shadow plane.",
                notes=[
                    "Higher = crisper shadow edge, but a larger texture on disk.",
                ],
            )
        )
        ui.cmb000.setToolTip(
            fmt(
                title="Projection Axis",
                body="Viewing axis the silhouette is rendered along.",
                rows=[
                    ("Auto", "chooses the projection axis automatically"),
                    ("X / Y / Z", "force side / top / front projection"),
                ],
            )
        )
        ui.chk_preview.setToolTip(
            fmt(
                title="Preview",
                body="Builds the shadow rig live so you can judge it before "
                "committing.",
                notes=[
                    "Tweaking any option refreshes the preview.",
                    "<b>Create Shadow</b> commits it; disabling Preview "
                    "discards it.",
                ],
            )
        )
        ui.b000.setToolTip(
            fmt(
                title="Create Shadow",
                body="Commits the previewed shadow rig for the selected "
                "target(s).",
                steps=[
                    "Select one or more target objects.",
                    "Enable <b>Preview</b> and dial in the options.",
                    "Press <b>Create Shadow</b>.",
                ],
                notes=[
                    "Only commits an active preview — enable <b>Preview</b> "
                    "first, or this does nothing.",
                ],
            )
        )
        ui.b001.setToolTip(
            fmt(
                title="Reset to Defaults",
                body="Restores every option on this panel to its default value.",
            )
        )
        ui.b002.setToolTip(
            fmt(
                title="Bake to Keyframes",
                body="Bakes the shadow plane's driven motion to keyframes over "
                "the playback range and removes the live rig — leaving an "
                "FBX-ready plane.",
                notes=[
                    "Applies to selected shadow planes, or all planes if none "
                    "are selected.",
                    "Bake before exporting to Unity / a game engine.",
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def b002(self):
        """Bake to Keyframes: bake selected (or all) shadow planes' expressions
        to keys over the playback range and remove the live rig."""
        sel = cmds.ls(selection=True) or None
        planes = ShadowRig.find_shadow_planes(sel)
        if sel and not planes:
            # A non-empty selection with no shadow planes must NOT silently
            # fall back to baking (destructively de-rigging) every plane in
            # the scene — that's only the documented behavior for an empty
            # selection.
            self.sb.message_box(
                "Selection contains no shadow planes. Select the plane(s) "
                "to bake, or clear the selection to bake all."
            )
            return
        baked = ShadowRig.bake_planes(planes)
        if baked:
            self.sb.message_box(f"Baked {len(baked)} shadow plane(s) to keyframes.")
        else:
            self.sb.message_box("No shadow planes with a live expression found.")

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
        try:
            resolution = int(res_text.replace("Resolution: ", "").strip())
        except (ValueError, AttributeError):
            resolution = 512
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

        if contract is not None:
            # create() republishes shadow_metadata on the data_export carrier.
            # A brand-new carrier is rolled back as a created node, but a
            # PRE-EXISTING one (other producers' channels) only gets its attr
            # mutated — snapshot it so canceling the preview can't leave a
            # stale channel behind.
            from mayatk.node_utils.data_nodes import DataNodes

            if cmds.objExists(DataNodes.EXPORT):
                contract.record_modification(
                    DataNodes.EXPORT, ShadowRig.SHADOW_METADATA
                )

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
