# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


class UvUtils(ptk.HelpMixin):
    @staticmethod
    def calculate_uv_padding(
        map_size: int, normalize: bool = False, factor: int = 256
    ) -> float:
        """Calculate the UV padding for a given map size to ensure consistent texture padding across different resolutions.
        Optionally return the padding as a normalized value relative to the map size.

        Parameters:
        map_size (int): The size of the map for which to calculate UV padding, typically the width or height in pixels.
        normalize (bool): If True, returns the padding as a normalized value. Default is False.
        factor (int): The factor by which to divide the map size to calculate the padding. Default is 256.

        Returns:
        float: The calculated padding in pixels or normalized units.

        Expected Output:
        - For a 1024 pixel map: 4.0 pixels of padding or 0.00390625 if normalized
        - For a 2048 pixel map: 8.0 pixels of padding or 0.00390625 if normalized
        - For a 4096 pixel map: 16.0 pixels of padding or 0.00390625 if normalized
        - For a 8192 pixel map: 32.0 pixels of padding or 0.00390625 if normalized

        Example:
            calculate_uv_padding(4096, normalize=True)
        0.00390625
        """
        padding = map_size / factor
        if normalize:
            return padding / map_size
        return padding

    @staticmethod
    def orient_shells(objects):
        """Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.

        Parameters:
            objects (str/obj/list): Polygon mesh objects and/or components.
        """
        for obj in pm.ls(objects, objectsOnly=True):
            # filter components for only this object.
            obj_compts = [i for i in objects if obj in pm.ls(i, objectsOnly=1)]
            pm.polyLayoutUV(
                obj_compts,
                flipReversed=0,
                layout=0,
                layoutMethod=1,
                percentageSpace=0.2,
                rotateForBestFit=3,
                scale=0,
                separate=0,
            )

    @staticmethod
    def move_to_uv_space(objects, u, v, relative=True):
        """Move objects to the given u and v coordinates.

        Parameters:
            objects (str/obj/list): The object(s) to move.
            u (int): u coordinate.
            v (int): v coordinate.
            relative (bool): Move relative or absolute.
        """
        # Convert the objects to UVs
        uvs = pm.polyListComponentConversion(objects, fromFace=True, toUV=True)
        uvs = pm.ls(uvs, flatten=True)

        # Move the UVs to the given u and v coordinates
        pm.polyEditUV(uvs, u=u, v=v, relative=relative)

    @classmethod
    @CoreUtils.undoable
    def mirror_uvs(
        cls,
        objects,
        axis: str = "u",
        pivot: tuple | None = None,
        per_shell: bool = True,
        preserve_position: bool = True,
    ):
        """Mirror UVs across U or V.

        By default (`preserve_position=True`), this preserves the UV shell's *footprint*
        (the exact set of UV points) by reassigning UV components to the original
        point set via a one-to-one assignment. This is different from Maya's typical
        geometric flip/mirror which changes the shell's shape in UV space.

        The common failure mode when flipping UVs on a whole object is that multiple
        shells share one pivot, so some shells translate after the flip.
        By default this method flips *each UV shell* around its own center.

        Parameters:
            objects (str/obj/list): Object(s), faces, or UVs to flip.
            axis (str): 'u'/'horizontal' or 'v'/'vertical'. Default 'u'.
            pivot (tuple, optional): (u, v) pivot to use. If None, pivot is computed
                from each shell's UV bounds (or from the selection if per_shell=False).
            per_shell (bool): If True (default), flip each UV shell independently.
            preserve_position (bool): If True (default), preserves the UV shell's
                footprint (the exact set of UV points) by reassigning UV components
                to the original point set via a one-to-one assignment.
                If False, performs a geometric flip (mirrors UV coordinates around
                the pivot), which will mirror the shell shape in UV space.
        """
        axis_norm = (axis or "").lower()
        do_flip_u = axis_norm in ("u", "h", "horizontal")
        do_flip_v = axis_norm in ("v", "vert", "vertical")
        if not do_flip_u and not do_flip_v:
            raise ValueError(
                f"Invalid axis '{axis}'. Use 'u'/'horizontal' or 'v'/'vertical'."
            )

        uv_groups = []
        if per_shell:
            shell_face_sets = cls.get_uv_shell_sets(objects, returned_type="shell")
            for face_set in shell_face_sets:
                shell_uvs = pm.polyListComponentConversion(face_set, toUV=True)
                shell_uvs = pm.ls(shell_uvs, flatten=True)
                if shell_uvs:
                    uv_groups.append(shell_uvs)
        else:
            uvs = pm.polyListComponentConversion(objects, toUV=True)
            uvs = pm.ls(uvs, flatten=True)
            if uvs:
                uv_groups.append(uvs)

        if not uv_groups:
            pm.warning("No UVs found to flip.")
            return

        for uv_list in uv_groups:
            # 1. Get all UVs and coordinates
            coords_flat = pm.polyEditUV(uv_list, query=True)
            if not coords_flat:
                continue

            us = coords_flat[0::2]
            vs = coords_flat[1::2]

            orig_slots = list(zip(us, vs))

            # 2. Direct mapping approach
            # We want the final UV *positions* to remain exactly the same set of points
            # (preserve footprint), while the UV *assignment* is permuted as if the
            # shell was flipped. The reliable way to do this is:
            #   - compute where each UV would go if we did a geometric flip (targets)
            #   - solve a one-to-one assignment from targets -> original slots
            #   - move each UV to its assigned original slot
            # This avoids row/col inference, which breaks on tapered/tilted shells.

            # Calculate Center for Mirroring
            if pivot:
                center_u, center_v = pivot
            else:
                center_u = (min(us) + max(us)) / 2.0
                center_v = (min(vs) + max(vs)) / 2.0

            # Compute flipped target position for each UV (geometric flip, not applied)
            targets = []
            for u, v in orig_slots:
                if do_flip_u:
                    targets.append((center_u - (u - center_u), v))
                else:
                    targets.append((u, center_v - (v - center_v)))

            n = len(uv_list)
            if n > 350:
                pm.warning(
                    "Large UV shell detected; direct-mapping flip may take a moment."
                )

            if not preserve_position:
                # Geometric flip: actually mirrors UV coordinates around the pivot.
                for i, (u, v) in enumerate(targets):
                    pm.polyEditUV(uv_list[i], u=u, v=v, relative=False)
                continue

            # Footprint-preserving flip:
            # Solve a one-to-one assignment from flipped targets -> original slots,
            # then move each UV to the assigned original slot.
            cost = [[0.0] * n for _ in range(n)]
            for i in range(n):
                tu, tv = targets[i]
                row = cost[i]
                for j in range(n):
                    su, sv = orig_slots[j]
                    du = tu - su
                    dv = tv - sv
                    row[j] = du * du + dv * dv

            row_ind, col_ind = ptk.MathUtils.linear_sum_assignment(cost)
            assignment = {r: c for r, c in zip(row_ind, col_ind)}

            for uv_idx in range(n):
                slot_idx = assignment.get(uv_idx)
                if slot_idx is None:
                    continue
                u, v = orig_slots[slot_idx]
                pm.polyEditUV(uv_list[uv_idx], u=u, v=v, relative=False)

    @classmethod
    @CoreUtils.undoable
    def flip_uvs(
        cls,
        objects,
        axis: str = "u",
        pivot: tuple | None = None,
        per_shell: bool = True,
        preserve_position: bool = True,
    ):
        """Backward-compatible alias for :meth:`mirror_uvs`.

        Note: this operation is *not* a standard geometric flip when
        `preserve_position=True`.
        """
        try:
            pm.warning(
                "UvUtils.flip_uvs is deprecated; use UvUtils.mirror_uvs instead."
            )
        except Exception:
            pass
        return cls.mirror_uvs(
            objects,
            axis=axis,
            pivot=pivot,
            per_shell=per_shell,
            preserve_position=preserve_position,
        )

    @staticmethod
    def get_uv_shell_sets(objects=None, returned_type="shell"):
        """Get UV shells and their corresponding sets of faces.

        Parameters:
            objects (obj/list): Polygon object(s) or Polygon face(s).
            returned_type (str): The desired returned type. Valid values are: 'shell', 'id'. If None is given, the full dict will be returned.

        Returns:
            (list)(dict): Depending on the given returned_type arg.
            Example: {0: [MeshFace('pShape.f[0]'), MeshFace('pShape.f[1]')], 1: [MeshFace('pShape.f[2]'), MeshFace('pShape.f[3]')]}
        """
        faces = Components.get_components(objects, "faces", flatten=True)
        shells = {}

        for face in faces:
            try:
                # Attempt to get the UV shell ID, ensure it returns a non-empty list
                shell_Id = pm.polyEvaluate(face, uvShellIds=True)

                # Validate shell_Id
                if not isinstance(shell_Id, list) or not shell_Id:
                    pm.warning(f"Invalid UV shell ID for face: {face}")
                    continue

                # Use the shell ID to group faces
                shell_key = shell_Id[0]
                shells.setdefault(shell_key, []).append(face)

            except pm.MayaNodeError as e:
                pm.warning(f"Error evaluating UV shell ID for face {face}: {e}")
                continue

        if returned_type == "shell":
            return list(shells.values())
        elif returned_type == "id":
            return list(shells.keys())
        else:
            raise ValueError(
                f"Invalid returned_type: {returned_type}. Valid values are: 'shell', 'id'."
            )

    @staticmethod
    def get_uv_shell_border_edges(objects):
        """Get the edges that make up any UV islands of the given objects.

        Parameters:
            objects (str/obj/list): Polygon objects, mesh UVs, or Edges.

        Returns:
            (list): UV border edges.
        """
        uv_border_edges = []
        for obj in pm.ls(objects):
            # Get shape node using NodeUtils reliable method
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and len(shape) > 0:
                    obj = shape[0]
                elif shape:
                    obj = shape
            except Exception:
                # Fallback to original method if NodeUtils fails
                if isinstance(obj, pm.nt.Transform):
                    obj = obj.getShape()

            # If the obj is a mesh shape, get its UV borders
            if isinstance(obj, pm.nt.Mesh):
                # Get the connected edges to the selected UVs
                connected_edges = pm.polyListComponentConversion(
                    obj, fromUV=True, toEdge=True
                )
                connected_edges = pm.ls(connected_edges, flatten=True)
            elif isinstance(obj, pm.general.MeshEdge):
                # If the object is already an edge, no conversion is necessary
                connected_edges = pm.ls(obj, flatten=True)
            elif isinstance(obj, pm.general.MeshUV):
                # If the object is a UV, convert it to its connected edges
                connected_edges = pm.polyListComponentConversion(
                    obj, fromUV=True, toEdge=True
                )
                connected_edges = pm.ls(connected_edges, flatten=True)
            else:
                raise ValueError(f"Unsupported object type: {type(obj)}")

            for edge in connected_edges:
                edge_uvs = pm.ls(
                    pm.polyListComponentConversion(edge, tuv=True), fl=True
                )
                edge_faces = pm.ls(
                    pm.polyListComponentConversion(edge, tf=True), fl=True
                )
                if (
                    len(edge_uvs) > 2 or len(edge_faces) < 2
                ):  # If an edge has more than two uvs or less than 2 faces, it's a uv border edge.
                    uv_border_edges.append(edge)

        return uv_border_edges

    @staticmethod
    def get_texel_density(objects, map_size):
        """Calculate the texel density for the given objects' faces.

        Parameters:
            objects (str, obj, list): List of mesh objects or a single mesh object to calculate texel density for.
            map_size (int): Size of the map to calculate the texel density against.

        Returns (float):
            The texel density.
        """
        from math import sqrt

        area_3d_sum = 0.0
        area_uv_sum = 0.0

        # Convert objects to faces if they are not already
        if not isinstance(objects, list):
            objects = [objects]
        faces = pm.polyListComponentConversion(objects, toFace=True)
        faces = pm.filterExpand(
            faces, ex=True, sm=34
        )  # Now this will work, as faces are passed

        if not faces:
            pm.warning("No faces found in the input objects.")
            return 0

        # Calculate 3D and UV areas
        for f in faces:
            world_face_area = pm.polyEvaluate(f, worldFaceArea=True)
            uv_face_area = pm.polyEvaluate(f, uvFaceArea=True)
            if (
                world_face_area and uv_face_area
            ):  # Check if the area lists are not empty
                area_3d_sum += world_face_area[0]
                area_uv_sum += uv_face_area[0]

        # Avoid division by zero
        if area_3d_sum == 0 or area_uv_sum == 0:
            pm.warning("Cannot calculate texel density with zero area.")
            return 0

        # Calculate texel density
        texel_density = (sqrt(area_uv_sum) / sqrt(area_3d_sum)) * map_size
        return texel_density

    @classmethod
    @CoreUtils.undoable
    def set_texel_density(cls, objects=None, density=1.0, map_size=4096):
        """Set the texel density for the given objects.

        Parameters:
            objects (str, obj, list): List of objects or a single object to set texel density for.
                If None, the currently selected objects will be used.
            density (float): The desired texel density.
            map_size (int): Size of the map to calculate the texel density against.
        """
        # Get UV shell sets
        shells = cls.get_uv_shell_sets(objects or pm.selected(), returned_type="shell")

        for shell_faces in shells:
            # Convert face list to UVs
            shell_uvs = pm.polyListComponentConversion(shell_faces, toUV=True)
            shell_uvs = pm.ls(shell_uvs, flatten=True)  # Flatten the list of UVs

            # Calculate current density and scaling factor
            current_density = cls.get_texel_density(shell_faces, map_size)
            if current_density == 0:
                pm.warning(
                    f"Cannot set texel density for UV shell with zero area: {shell_faces}"
                )
                continue  # Skip this shell and continue with the next one

            scale = density / current_density

            # Calculate bounding box center for UVs
            bc = pm.polyEvaluate(shell_uvs, bc2=True)
            pU = (bc[0][0] + bc[1][0]) / 2
            pV = (bc[0][1] + bc[1][1]) / 2

            # Scale UVs
            pm.polyEditUV(shell_uvs, pu=pU, pv=pV, su=scale, sv=scale)

    @staticmethod
    @CoreUtils.undoable
    def transfer_uvs(
        source: Union[str, object, List[Union[str, object]]],
        target: Union[str, object, List[Union[str, object]]],
        tolerance: float = 0.1,
    ) -> None:
        """Transfers UVs from source meshes to target meshes based on geometric similarity. This method is
        topology-agnostic and can work with different mesh structures.

        Parameters:
            source (Union[str, pm.nt.Transform, List[Union[str, pm.nt.Transform]]]): The source mesh(es) from
                which to transfer UVs. Can be a string name, a PyNode object, or a list of these.
            target (Union[str, pm.nt.Transform, List[Union[str, pm.nt.Transform]]]): The target mesh(es) to
                which UVs will be transferred. Can be a string name, a PyNode object, or a list of these.
            tolerance (float): The geometric similarity tolerance within which UV transfer should occur.
                Defaults to 0.1.
        """
        mapping = CoreUtils.build_mesh_similarity_mapping(source, target, tolerance)
        for source_name, target_name in mapping.items():
            pm.transferAttributes(
                source_name,
                target_name,
                transferPositions=False,
                transferNormals=False,
                transferUVs=2,
                transferColors=0,
                sampleSpace=4,
                sourceUvSpace="map1",
                targetUvSpace="map1",
                searchMethod=3,
                flipUVs=False,
                colorBorders=True,
            )
            pm.delete(target_name, ch=True)  # Clean up history on target

    @staticmethod
    def reorder_uv_sets(obj: "pm.nt.Transform", new_order: list[str]) -> None:
        """Reorder UV sets of the given object to match the specified new order.
        This method will raise a ValueError if the new order does not match the existing UV sets.

        Parameters:
            obj (pm.nt.Transform): The object whose UV sets will be reordered.
            new_order (list[str]): The desired order of UV sets.
                This should be a list of strings representing the names of the UV sets.
                The order of the names in this list will be the new order of the UV sets.
                The first element in the list will be set as the current UV set.
        """
        # Get shape node using NodeUtils reliable method
        try:
            shape = NodeUtils.get_shape_node(obj, returned_type="obj")
            if isinstance(shape, list) and len(shape) > 0:
                shape = shape[0]
        except Exception:
            # Fallback to original method if NodeUtils fails
            shape = obj.getShape()
        existing = pm.polyUVSet(shape, query=True, allUVSets=True) or []

        if set(existing) != set(new_order):
            raise ValueError("new_order must match the set of existing UV sets")

        for i in range(1, len(new_order)):
            current = new_order[i]
            insert_after = new_order[i - 1]

            # Only reorder if order is incorrect
            if existing.index(current) < existing.index(insert_after):
                pm.polyUVSet(shape, reorder=True, uvSet=current, newUVSet=insert_after)
                existing = pm.polyUVSet(shape, query=True, allUVSets=True)

    @staticmethod
    @CoreUtils.undoable
    def remove_empty_uv_sets(objects, quiet: bool = False) -> None:
        """Remove empty UV sets from the given objects.
        This method checks each UV set of the objects and deletes any that are empty.
        It also prints a message for each deleted UV set unless quiet is set to True.

        Parameters:
            objects (str/obj/list): Polygon objects or components to check for empty UV sets.
            quiet (bool): If True, suppress output messages.
        """
        objects = NodeUtils.get_transform_node(objects)

        for obj in objects:
            # Get shape node using NodeUtils reliable method
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and len(shape) > 0:
                    shape = shape[0]
            except Exception:
                # Fallback to original method if NodeUtils fails
                shape = obj.getShape() if hasattr(obj, "getShape") else obj
            if not isinstance(shape, pm.nt.Shape) or not shape.hasAttr("uvSet"):
                continue

            deleted: list[str] = []
            all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
            current = pm.polyUVSet(shape, query=True, currentUVSet=True)

            for uv_set in list(all_sets):
                try:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                    uv_count = pm.polyEvaluate(shape, uvcoord=True)
                    if uv_count > 0:
                        continue

                    index = all_sets.index(uv_set)
                    if index == 0 and len(all_sets) > 1:
                        pm.polyUVSet(
                            shape, reorder=True, uvSet=all_sets[1], newUVSet=uv_set
                        )
                        all_sets = pm.polyUVSet(shape, query=True, allUVSets=True)

                    if uv_set == current:
                        fallback = next((s for s in all_sets if s != uv_set), None)
                        if fallback:
                            pm.polyUVSet(shape, currentUVSet=True, uvSet=fallback)

                    pm.polyUVSet(shape, delete=True, uvSet=uv_set)
                    deleted.append(uv_set)

                except RuntimeError:
                    continue

            remaining = pm.polyUVSet(shape, query=True, allUVSets=True) or []
            if current in remaining:
                pm.polyUVSet(shape, currentUVSet=True, uvSet=current)

            if deleted and not quiet:
                print(
                    f"{shape}: removed empty UV sets: {deleted} | remaining: {remaining}"
                )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
