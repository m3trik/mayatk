# !/usr/bin/python
# coding=utf-8
"""Map image files to textured polygon planes in Maya.

Provides ``ImageToPlane`` — a batch-capable utility that creates a polygon
plane per image, sized to match the source aspect ratio, with a fully wired
material (Stingray PBS or standardSurface).
"""
import os
from typing import Dict, List, Optional

import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.mat_utils._mat_utils import MatUtils


class ImageToPlane(ptk.LoggingMixin):
    """Create textured polygon planes from image files.

    All public methods are class-level — no instance state required.

    Workflow
    --------
    1. Call :meth:`create` with one or more image paths.
    2. Each image produces a polygon plane whose width/height matches the
       source pixel ratio, a shader (Stingray PBS or standardSurface),
       a ``file`` texture node, and a ``place2dTexture``.
    3. Planes are created at the world origin.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        image_paths: List[str],
        mat_type: str = "stingray",
        suffix: str = "_MAT",
        prefix: str = "",
        plane_height: float = 10.0,
        axis: Optional[List[float]] = None,
        group: bool = False,
        group_name: str = "imagePlanes_GRP",
    ) -> Dict[str, object]:
        """Create textured planes for one or more images.

        Parameters:
            image_paths: Absolute paths to image files.
            mat_type: ``"stingray"`` for StingrayPBS or ``"standard"``
                for the preferred standard shader (standardSurface/lambert).
            suffix: Appended to the image stem for material naming
                (e.g. ``"_MAT"`` → ``myImage_MAT``).
            prefix: Prepended to the image stem for material naming
                (e.g. ``"MAT_"`` → ``MAT_myImage``).
            plane_height: Height of each plane in scene units.  Width is
                derived from the image aspect ratio.
            axis: Plane normal axis as ``[x, y, z]``.  Defaults to
                ``[0, 0, 1]`` (facing camera in front view).
            group: If *True*, parent all created planes under a
                single group node.
            group_name: Name of the group node when *group* is True.

        Returns:
            dict: ``{image_stem: plane_transform, ...}``
        """
        if axis is None:
            axis = [0, 0, 1]

        results: Dict[str, object] = {}
        for path in image_paths:
            path = os.path.normpath(path)
            if not os.path.isfile(path):
                cls.logger.warning("Image not found: %s", path)
                continue
            try:
                plane = cls._create_single(
                    path,
                    mat_type=mat_type,
                    suffix=suffix,
                    prefix=prefix,
                    plane_height=plane_height,
                    axis=axis,
                )
                stem = os.path.splitext(os.path.basename(path))[0]
                results[stem] = plane
            except Exception:
                cls.logger.error(
                    "Failed to create plane for %s",
                    path,
                    exc_info=True,
                )

        if group and results:
            grp = cmds.group(list(results.values()), name=group_name)
            results["__group__"] = grp

        return results

    @classmethod
    @CoreUtils.undoable
    def remove(cls, objects=None) -> int:
        """Remove planes and their materials created by this tool.

        Deletes the shading group, shader, file node, and place2dTexture
        upstream of each object, then deletes the object itself.

        Parameters:
            objects: Objects to remove.  If *None*, uses the current
                selection.

        Returns:
            int: Number of objects removed.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            return 0

        count = 0
        for obj in list(objects):
            try:
                obj_str = str(obj)
                # Collect all nodes to delete, then batch-delete
                to_delete = []

                shapes = cmds.listRelatives(
                    obj_str, shapes=True, noIntermediate=True, fullPath=True
                ) or []
                for shape in shapes:
                    sgs = cmds.listConnections(shape, type="shadingEngine") or []
                    for sg in sgs:
                        if sg.split("|")[-1].split(":")[-1] == "initialShadingGroup":
                            continue
                        # Shader connected to surfaceShader
                        shaders = cmds.listConnections(f"{sg}.surfaceShader") or []
                        for shader in shaders:
                            # File nodes upstream of the shader
                            files = cmds.listConnections(shader, type="file") or []
                            for f in files:
                                p2ds = cmds.listConnections(f, type="place2dTexture") or []
                                to_delete.extend(p2ds)
                                to_delete.append(f)
                            to_delete.append(shader)
                        to_delete.append(sg)

                # Delete upstream nodes first, then the object
                for node in to_delete:
                    try:
                        if cmds.objExists(node):
                            cmds.delete(node)
                    except Exception:
                        pass

                if cmds.objExists(obj_str):
                    cmds.delete(obj_str)
                count += 1
            except Exception:
                cls.logger.debug("Could not fully clean %s", obj, exc_info=True)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _create_single(cls, image_path, mat_type, suffix, plane_height, axis, prefix=""):
        """Create one plane + material from a single image path."""
        stem = os.path.splitext(os.path.basename(image_path))[0]

        # --- Dimensions via Maya file node ---
        w, h = cls._get_image_dimensions(image_path)
        aspect = w / h if h else 1.0
        plane_width = plane_height * aspect

        # --- Poly plane ---
        plane, _ = cmds.polyPlane(
            name=stem,
            width=plane_width,
            height=plane_height,
            subdivisionsX=1,
            subdivisionsY=1,
            axis=axis,
        )

        # --- Material ---
        mat_name = f"{prefix}{stem}{suffix}"
        shader = cls._create_shader(mat_name, mat_type)

        # --- File node (shared helper) ---
        file_node, _ = MatUtils.create_file_node(image_path, name=mat_name)

        # --- Connect texture → shader ---
        cls._connect_texture(shader, file_node, mat_type)

        # --- Shading group + assign (shared helper) ---
        MatUtils.create_shading_group(shader, name=f"{mat_name}_SG", assign_to=plane)

        return plane

    @classmethod
    def _create_shader(cls, name, mat_type):
        """Create either a StingrayPBS or standard shader."""
        if mat_type == "stingray":
            return MatUtils.create_stingray_shader(name, opacity=False)
        else:
            shader_name = MatUtils._create_standard_shader(
                name=name,
                return_type="shader",
            )
            return shader_name

    @staticmethod
    def _connect_texture(shader, file_node, mat_type):
        """Wire the file node colour output to the correct shader input."""
        if mat_type == "stingray":
            # StingrayPBS uses TEX_color_map and requires use_color_map=1
            try:
                cmds.connectAttr(
                    f"{file_node}.outColor", f"{shader}.TEX_color_map", force=True
                )
                cmds.setAttr(f"{shader}.use_color_map", 1)
            except Exception:
                try:
                    cmds.connectAttr(
                        f"{file_node}.outColor", f"{shader}.color", force=True
                    )
                except Exception:
                    pass
        else:
            # standardSurface / lambert
            color_attr = "baseColor" if cmds.attributeQuery("baseColor", node=shader, exists=True) else "color"
            cmds.connectAttr(
                f"{file_node}.outColor", f"{shader}.{color_attr}", force=True
            )

    @staticmethod
    def _get_image_dimensions(image_path):
        """Return ``(width, height)`` of an image using Maya's native query.

        Falls back to sensible defaults (1, 1) if the query fails.
        """
        try:
            # Create a temporary file node to query resolution
            tmp = cmds.shadingNode("file", asTexture=True)
            cmds.setAttr(f"{tmp}.fileTextureName", image_path, type="string")
            w = cmds.getAttr(f"{tmp}.outSizeX")
            h = cmds.getAttr(f"{tmp}.outSizeY")
            cmds.delete(tmp)
            if w and h:
                return w, h
        except Exception:
            pass
        return 1, 1
