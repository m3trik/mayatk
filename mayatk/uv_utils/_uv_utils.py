# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils, components
from mayatk.node_utils import NodeUtils


class UvUtils(ptk.HelpMixin):
    @staticmethod
    def calculate_uv_padding(
        map_size: int, normalize: bool = False, factor: int = 64
    ) -> float:
        """Calculate the UV padding for a given map size to ensure consistent texture padding across different resolutions.
        Optionally return the padding as a normalized value relative to the map size.

        Parameters:
        map_size (int): The size of the map for which to calculate UV padding, typically the width or height in pixels.
        normalize (bool): If True, returns the padding as a normalized value. Default is False.
        factor (int): The factor by which to divide the map size to calculate the padding. Default is 128.

        Returns:
        float: The calculated padding in pixels or normalized units. Ensures that a 4K (4096 pixels) map gets exactly 32 pixels of padding.

        Expected Output:
        - For a 1024 pixel map: 4.0 pixels of padding or 0.0078125 if normalized
        - For a 2048 pixel map: 8.0 pixels of padding or 0.0078125 if normalized
        - For a 4096 pixel map: 16.0 pixels of padding or 0.0078125 if normalized
        - For a 8192 pixel map: 32.0 pixels of padding or 0.0078125 if normalized

        Example:
            calculate_uv_padding(4096, normalize=True)
        0.0078125
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
        faces = components.Components.get_components(objects, "faces", flatten=True)
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
            # If the obj is a mesh object, get its shape
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
    def reorder_uv_sets(obj: pm.nt.Transform, new_order: list[str]) -> None:
        """Reorder UV sets of the given object to match the specified new order.
        This method will raise a ValueError if the new order does not match the existing UV sets.

        Parameters:
            obj (pm.nt.Transform): The object whose UV sets will be reordered.
            new_order (list[str]): The desired order of UV sets.
                This should be a list of strings representing the names of the UV sets.
                The order of the names in this list will be the new order of the UV sets.
                The first element in the list will be set as the current UV set.
        """
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

    @classmethod
    @CoreUtils.undoable
    def cleanup_uv_sets(
        cls,
        objects,
        remove_empty: bool = True,
        keep_only_primary: bool = True,
        rename_primary_to_map1: bool = True,
        force_rename: bool = False,
        quiet: bool = False,
    ) -> None:
        """Cleanup UV sets by removing empty sets, keeping only the primary set, and renaming the primary set to 'map1'.
        This method is useful for ensuring a consistent UV layout across multiple objects.

        Parameters:
            objects (str/obj/list): Polygon objects or components to clean up UV sets for.
            remove_empty (bool): If True, remove empty UV sets.
            keep_only_primary (bool): If True, keep only the primary UV set.
            rename_primary_to_map1 (bool): If True, rename the primary UV set to 'map1'.
            force_rename (bool): If True, force rename even if 'map1' already exists.
            quiet (bool): If True, suppress output messages.
        """
        objects = pm.ls(objects, flatten=True)

        if remove_empty:
            cls.remove_empty_uv_sets(objects, quiet=quiet)

        for obj in objects:
            shape = obj.getShape() if hasattr(obj, "getShape") else obj
            if not isinstance(shape, pm.nt.Shape) or not shape.hasAttr("uvSet"):
                continue

            uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
            if not uv_sets:
                continue

            primary_uv_set = uv_sets[0]

            if keep_only_primary:
                for uv_set in uv_sets[1:]:
                    try:
                        pm.polyUVSet(shape, delete=True, uvSet=uv_set)
                        if not quiet:
                            print(f"{shape}: removed secondary UV set: {uv_set}")
                    except RuntimeError:
                        continue

            if rename_primary_to_map1 and primary_uv_set != "map1":
                uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []

                if "map1" in uv_sets and primary_uv_set != "map1":
                    try:
                        if not keep_only_primary and force_rename:
                            pm.polyUVSet(
                                shape,
                                rename=True,
                                uvSet="map1",
                                newUVSet="map1_conflict",
                            )
                            if not quiet:
                                print(
                                    f"{shape}: renamed existing 'map1' → 'map1_conflict' to avoid conflict"
                                )
                        elif not quiet:
                            print(f"{shape}: 'map1' already exists — skipping rename")
                            continue
                    except RuntimeError:
                        if not quiet:
                            pm.warning(
                                f"{shape}: failed to resolve UV set name conflict with 'map1'"
                            )
                        continue

                try:
                    pm.polyUVSet(
                        shape, rename=True, uvSet=primary_uv_set, newUVSet="map1"
                    )
                    if not quiet:
                        print(f"{shape}: renamed UV set {primary_uv_set} → map1")
                except RuntimeError:
                    if not quiet:
                        pm.warning(
                            f"{shape}: failed to rename {primary_uv_set} to map1"
                        )

            if not quiet:
                final_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                print(f"{shape}: UV sets after cleanup: {final_sets}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
