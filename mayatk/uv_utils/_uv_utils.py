# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Union

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils, as_strings
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

        objects = as_strings(objects)
        for obj in cmds.ls(objects, objectsOnly=True) or []:
            # filter components for only this object.
            obj_compts = [i for i in objects if obj in (cmds.ls(i, objectsOnly=True) or [])]
            cmds.polyLayoutUV(
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

        objects = as_strings(objects)
        # Convert the objects to UVs
        uvs = cmds.polyListComponentConversion(objects, fromFace=True, toUV=True) or []
        uvs = cmds.ls(uvs, flatten=True) or []

        # Move the UVs to the given u and v coordinates
        cmds.polyEditUV(uvs, u=u, v=v, relative=relative)

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
                shell_uvs = cmds.polyListComponentConversion(face_set, toUV=True) or []
                shell_uvs = cmds.ls(shell_uvs, flatten=True) or []
                if shell_uvs:
                    uv_groups.append(shell_uvs)
        else:
            uvs = cmds.polyListComponentConversion(objects, toUV=True) or []
            uvs = cmds.ls(uvs, flatten=True) or []
            if uvs:
                uv_groups.append(uvs)

        if not uv_groups:
            cmds.warning("No UVs found to flip.")
            return

        for uv_list in uv_groups:
            # 1. Get all UVs and coordinates
            coords_flat = cmds.polyEditUV(uv_list, query=True)
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
                cmds.warning(
                    "Large UV shell detected; direct-mapping flip may take a moment."
                )

            if not preserve_position:
                # Geometric flip: actually mirrors UV coordinates around the pivot.
                for i, (u, v) in enumerate(targets):
                    cmds.polyEditUV(uv_list[i], u=u, v=v, relative=False)
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
                cmds.polyEditUV(uv_list[uv_idx], u=u, v=v, relative=False)

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
            cmds.warning(
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

        Optimized to use the Maya API (OpenMaya) for performance and reliability,
        avoiding selection changes.

        Parameters:
            objects (obj/list): Polygon object(s) or Polygon face(s). If None,
                uses the current selection.
            returned_type (str): The desired returned type. Valid values are:
                'shell', 'id'.

        Returns:
            (list)(dict): Depending on the given returned_type arg.
        """
        import maya.api.OpenMaya as om

        if objects is None:
            objects = cmds.ls(selection=True) or []

        # Expand inputs to faces
        faces = Components.get_components(objects, "faces", flatten=True)
        if not faces:
            return [] if returned_type in ("shell", "id") else {}

        # Group faces by their shape node to batch API calls
        # Use str() on node components to get cmds-compatible strings
        mesh_faces_map = {}
        for f in faces:
            f_str = str(f)
            node_str = f_str.split(".")[0]
            # Resolve to shape node
            if cmds.objectType(node_str, isAType="transform"):
                shapes = cmds.listRelatives(node_str, shapes=True, fullPath=True) or []
                shape_str = shapes[0] if shapes else None
            else:
                shape_str = node_str
            if shape_str is None:
                continue
            if shape_str not in mesh_faces_map:
                mesh_faces_map[shape_str] = []
            mesh_faces_map[shape_str].append(f)

        shells = {}
        shell_count = 0

        for shape_str, shape_faces in mesh_faces_map.items():
            if cmds.objectType(shape_str) != "mesh":
                continue

            try:
                # Retrieve MFnMesh
                sel = om.MSelectionList()
                sel.add(shape_str)
                dag_path = sel.getDagPath(0)
                mfn_mesh = om.MFnMesh(dag_path)
                current_uv_set = mfn_mesh.currentUVSetName()

                # Get shell IDs for all UVs on the mesh
                _, uv_shell_ids = mfn_mesh.getUvShellsIds(current_uv_set)

                # Store faces by local shell ID
                local_shells = {}

                for f in shape_faces:
                    # Parse face index from string representation
                    f_str = str(f)
                    try:
                        face_idx = int(f_str.split("[")[1].rstrip("]"))
                    except (IndexError, ValueError):
                        continue
                    try:
                        # Get the UV index of the first vertex of the face.
                        if mfn_mesh.polygonVertexCount(face_idx) > 0:
                            uv_id = mfn_mesh.getPolygonUVid(face_idx, 0, current_uv_set)

                            # Look up shell ID for this UV
                            sid = uv_shell_ids[uv_id]
                            local_shells.setdefault(sid, []).append(f)
                    except Exception:
                        # Case: Face has no UVs projected
                        pass

                # Add to main results
                for sid in local_shells:
                    shells[shell_count] = local_shells[sid]
                    shell_count += 1

            except Exception as e:
                cmds.warning(f"Error processing UV shells for {shape_str}: {e}")
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

        objects = as_strings(objects)
        uv_border_edges = []
        for obj in cmds.ls(objects) or []:
            obj_str = str(obj)
            # Resolve transform to its shape
            if "." not in obj_str:
                try:
                    shapes = cmds.listRelatives(obj_str, shapes=True, fullPath=True) or []
                    if shapes:
                        obj_str = shapes[0]
                except Exception:
                    pass

            # Determine component or node type and get connected edges
            if "." not in obj_str and cmds.objectType(obj_str) == "mesh":
                # Mesh shape — get UV border edges
                connected_edges = cmds.polyListComponentConversion(
                    obj_str, fromUV=True, toEdge=True
                ) or []
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            elif ".e[" in obj_str:
                # Edge component — already an edge
                connected_edges = cmds.ls(obj_str, flatten=True) or []
            elif ".map[" in obj_str or ".uv[" in obj_str:
                # UV component — convert to edges
                connected_edges = cmds.polyListComponentConversion(
                    obj_str, fromUV=True, toEdge=True
                ) or []
                connected_edges = cmds.ls(connected_edges, flatten=True) or []
            else:
                raise ValueError(f"Unsupported object type: {obj_str}")

            for edge in connected_edges:
                edge_uvs = cmds.ls(
                    cmds.polyListComponentConversion(edge, tuv=True) or [], fl=True
                ) or []
                edge_faces = cmds.ls(
                    cmds.polyListComponentConversion(edge, tf=True) or [], fl=True
                ) or []
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
        faces = cmds.polyListComponentConversion(objects, toFace=True) or []
        faces = cmds.filterExpand(
            faces, ex=True, sm=34
        )  # Now this will work, as faces are passed

        if not faces:
            cmds.warning("No faces found in the input objects.")
            return 0

        # Calculate 3D and UV areas
        for f in faces:
            world_face_area = cmds.polyEvaluate(f, worldFaceArea=True)
            uv_face_area = cmds.polyEvaluate(f, uvFaceArea=True)
            if (
                world_face_area and uv_face_area
            ):  # Check if the area lists are not empty
                area_3d_sum += world_face_area[0]
                area_uv_sum += uv_face_area[0]

        # Avoid division by zero
        if area_3d_sum == 0 or area_uv_sum == 0:
            cmds.warning("Cannot calculate texel density with zero area.")
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
        shells = cls.get_uv_shell_sets(
            objects or (cmds.ls(selection=True) or []), returned_type="shell"
        )

        for shell_faces in shells:
            # Convert face list to UVs
            shell_uvs = cmds.polyListComponentConversion(shell_faces, toUV=True) or []
            shell_uvs = cmds.ls(shell_uvs, flatten=True) or []  # Flatten the list of UVs

            # Calculate current density and scaling factor
            current_density = cls.get_texel_density(shell_faces, map_size)
            if current_density == 0:
                cmds.warning(
                    f"Cannot set texel density for UV shell with zero area: {shell_faces}"
                )
                continue  # Skip this shell and continue with the next one

            scale = density / current_density

            # Calculate bounding box center for UVs
            bc = cmds.polyEvaluate(shell_uvs, bc2=True)
            pU = (bc[0][0] + bc[1][0]) / 2
            pV = (bc[0][1] + bc[1][1]) / 2

            # Scale UVs
            cmds.polyEditUV(shell_uvs, pu=pU, pv=pV, su=scale, sv=scale)

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
            source (Union[str, object, List]): The source mesh(es) from which to transfer UVs.
            target (Union[str, object, List]): The target mesh(es) to which UVs will be transferred.
            tolerance (float): The geometric similarity tolerance. Defaults to 0.1.
        """
        mapping = CoreUtils.build_mesh_similarity_mapping(source, target, tolerance)
        for source_name, target_name in mapping.items():
            cmds.transferAttributes(
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
            cmds.delete(target_name, ch=True)  # Clean up history on target

    @staticmethod
    def reorder_uv_sets(obj: str, new_order: list[str]) -> None:
        """Reorder UV sets of the given object to match the specified new order.
        This method will raise a ValueError if the new order does not match the existing UV sets.

        Parameters:
            obj (str): The object whose UV sets will be reordered.
            new_order (list[str]): The desired order of UV sets.
        """
        # Get shape node
        try:
            shape = NodeUtils.get_shape_node(obj, returned_type="obj")
            if isinstance(shape, list) and len(shape) > 0:
                shape = shape[0]
        except Exception:
            shapes = cmds.listRelatives(str(obj), shapes=True, fullPath=True) or []
            shape = shapes[0] if shapes else obj
        shape = str(shape)
        existing = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

        if set(existing) != set(new_order):
            raise ValueError("new_order must match the set of existing UV sets")

        for i in range(1, len(new_order)):
            current = new_order[i]
            insert_after = new_order[i - 1]

            # Only reorder if order is incorrect
            if existing.index(current) < existing.index(insert_after):
                cmds.polyUVSet(shape, reorder=True, uvSet=current, newUVSet=insert_after)
                existing = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

    @staticmethod
    @CoreUtils.undoable
    def remove_empty_uv_sets(objects, quiet: bool = False) -> None:
        """Remove empty UV sets from the given objects.

        Parameters:
            objects (str/obj/list): Polygon objects or components to check for empty UV sets.
            quiet (bool): If True, suppress output messages.
        """
        objects = NodeUtils.get_transform_node(objects)

        for obj in objects:
            # Get shape node
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and len(shape) > 0:
                    shape = shape[0]
            except Exception:
                shapes = cmds.listRelatives(str(obj), shapes=True, fullPath=True) or []
                shape = shapes[0] if shapes else None
            if shape is None:
                continue
            shape = str(shape)
            if not cmds.attributeQuery("uvSet", node=shape, exists=True):
                continue

            deleted: list[str] = []
            all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            current = cmds.polyUVSet(shape, query=True, currentUVSet=True)

            for uv_set in list(all_sets):
                try:
                    cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                    uv_count = cmds.polyEvaluate(shape, uvcoord=True)
                    if uv_count > 0:
                        continue

                    index = all_sets.index(uv_set)
                    if index == 0 and len(all_sets) > 1:
                        cmds.polyUVSet(
                            shape, reorder=True, uvSet=all_sets[1], newUVSet=uv_set
                        )
                        all_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []

                    if uv_set == current:
                        fallback = next((s for s in all_sets if s != uv_set), None)
                        if fallback:
                            cmds.polyUVSet(shape, currentUVSet=True, uvSet=fallback)

                    cmds.polyUVSet(shape, delete=True, uvSet=uv_set)
                    deleted.append(uv_set)

                except RuntimeError:
                    continue

            remaining = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
            if current in remaining:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=current)

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
