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
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components as components
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

    @staticmethod
    def _get_uv_set_metrics(shape, uv_set: str, quiet: bool = True) -> dict:
        """Calculate comprehensive UV metrics for a UV set to determine quality/completeness.

        Parameters:
            shape: The mesh shape to check
            uv_set (str): Name of the UV set to analyze
            quiet (bool): If True, suppress debug output

        Returns:
            dict: UV metrics including actual face area, shell count, and area distribution
        """
        try:
            # Debug: Check what type of object we're working with
            if not quiet:
                print(f"    DEBUG: Analyzing shape: {shape} (type: {type(shape)})")

            # Ensure we have the right shape node using NodeUtils reliable method
            try:
                actual_shape = NodeUtils.get_shape_node(shape, returned_type="obj")
                if actual_shape:
                    if isinstance(actual_shape, list) and len(actual_shape) > 0:
                        shape = actual_shape[0]  # Get first shape if list returned
                    else:
                        shape = actual_shape
                    if not quiet:
                        print(f"    DEBUG: Got shape using NodeUtils: {shape}")
                else:
                    if not quiet:
                        print(f"    DEBUG: NodeUtils couldn't find shape node")
            except Exception as e:
                if not quiet:
                    print(f"    DEBUG: NodeUtils failed: {e}")

            # Verify this is a mesh
            if not isinstance(shape, pm.nt.Mesh):
                if not quiet:
                    print(f"    DEBUG: Not a mesh shape: {type(shape)}")
                return {
                    "total_face_area": 0.0,
                    "face_count": 0,
                    "shell_count": 0,
                    "shells_with_area": 0,
                    "avg_face_area": 0.0,
                    "area_variance": 0.0,
                }

            # Capture current UV set and normalize to a string
            original_current = pm.polyUVSet(shape, query=True, currentUVSet=True)
            if isinstance(original_current, (list, tuple)):
                original_current = original_current[0] if original_current else None

            # Switch to the target UV set for evaluation
            pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)

            if not quiet:
                print(f"    DEBUG: Set current UV to: {uv_set}")

            # Calculate total UV area for the entire shape in the current UV set
            total_face_area = 0.0
            face_count = 0

            try:
                # Get face count first
                face_count = pm.polyEvaluate(shape, face=True) or 0
                if not quiet:
                    print(f"    DEBUG: Face count: {face_count}")

                # Method 1: Use polyEvaluate uvArea on the specific UV set (preferred)
                try:
                    total_uv_area = pm.polyEvaluate(
                        shape, uvArea=True, uvSetName=uv_set
                    )
                    if not quiet:
                        print(
                            f"    DEBUG: uvArea returned: {total_uv_area} (type: {type(total_uv_area)})"
                        )

                    # When only one flag is requested, Maya returns a float
                    if isinstance(total_uv_area, (int, float)) and total_uv_area > 0:
                        total_face_area = float(total_uv_area)
                        if not quiet:
                            print(
                                f"    DEBUG: Total UV area (uvArea): {total_face_area}"
                            )
                except Exception as e:
                    if not quiet:
                        print(f"    DEBUG: uvArea evaluate failed: {e}")
                    pass

                # Method 2: If method 1 failed or returned 0, try face-by-face calculation
                if total_face_area <= 0:
                    if not quiet:
                        print(f"    DEBUG: Trying face-by-face UV area calculation...")

                    try:
                        # Get all faces and calculate individual UV areas
                        faces = pm.ls(
                            pm.polyListComponentConversion(shape, toFace=True),
                            flatten=True,
                        )
                        if not quiet:
                            print(f"    DEBUG: Found {len(faces)} faces to analyze")

                        valid_face_areas = []
                        for i, face in enumerate(
                            faces[:50]
                        ):  # Sample first 50 faces for performance
                            try:
                                # Try getting UV area for individual face
                                face_uv_area = pm.polyEvaluate(
                                    face, uvFaceArea=True, uvSetName=uv_set
                                )
                                # uvFaceArea on a single face can return a float or list
                                if isinstance(face_uv_area, list):
                                    for a in face_uv_area:
                                        if isinstance(a, (int, float)) and a > 0:
                                            valid_face_areas.append(float(a))
                                elif (
                                    isinstance(face_uv_area, (int, float))
                                    and face_uv_area > 0
                                ):
                                    valid_face_areas.append(float(face_uv_area))
                            except:
                                continue

                        if valid_face_areas:
                            # Calculate total based on average face area
                            avg_face_area = sum(valid_face_areas) / len(
                                valid_face_areas
                            )
                            total_face_area = avg_face_area * face_count
                            if not quiet:
                                print(
                                    f"    DEBUG: Face-by-face calc: {len(valid_face_areas)} valid faces, avg area: {avg_face_area}, total: {total_face_area}"
                                )
                        else:
                            if not quiet:
                                print(f"    DEBUG: No valid face areas found")

                    except Exception as e:
                        if not quiet:
                            print(f"    DEBUG: Face-by-face calculation failed: {e}")
                        pass

                # Method 3: If still 0, try UV 2D bounding box for the set
                if total_face_area <= 0:
                    if not quiet:
                        print(f"    DEBUG: Trying UV 2D bounding box...")
                    try:
                        bb2d = pm.polyEvaluate(
                            shape, boundingBox2d=True, uvSetName=uv_set
                        )
                        if bb2d and isinstance(bb2d, tuple) and len(bb2d) == 2:
                            (u_min, u_max), (v_min, v_max) = bb2d
                            u_range = (
                                (u_max - u_min)
                                if u_max is not None and u_min is not None
                                else 0.0
                            )
                            v_range = (
                                (v_max - v_min)
                                if v_max is not None and v_min is not None
                                else 0.0
                            )
                            if not quiet:
                                print(
                                    f"    DEBUG: BB2D U[{u_min:.6f},{u_max:.6f}] V[{v_min:.6f},{v_max:.6f}] -> ranges ({u_range:.6f}, {v_range:.6f})"
                                )
                            if u_range > 0 and v_range > 0:
                                total_face_area = max(
                                    0.0, float(u_range * v_range) * 0.6
                                )  # conservative factor
                                if not quiet:
                                    print(
                                        f"    DEBUG: BB2D estimated area: {total_face_area:.6f}"
                                    )
                    except Exception as e:
                        if not quiet:
                            print(f"    DEBUG: BB2D evaluate failed: {e}")
                        pass

                # Method 4: If still 0, try UV coordinate analysis for estimation
                if total_face_area <= 0:
                    if not quiet:
                        print(f"    DEBUG: Trying UV coordinate analysis...")

                    try:
                        # Get all UVs and check if they exist and have varied coordinates
                        uvs = pm.ls(
                            pm.polyListComponentConversion(shape, toUV=True),
                            flatten=True,
                        )
                        if (
                            uvs and len(uvs) >= 3
                        ):  # Need at least 3 UVs for a meaningful area
                            if not quiet:
                                print(f"    DEBUG: Found {len(uvs)} UV coordinates")

                            # Sample UV coordinates to estimate coverage
                            u_coords = []
                            v_coords = []
                            valid_uvs = 0

                            sample_count = min(
                                50, len(uvs)
                            )  # Sample more UVs for better estimate
                            for uv in uvs[:sample_count]:
                                try:
                                    coords = pm.polyEditUV(uv, query=True)
                                    if coords and len(coords) >= 2:
                                        u_coords.append(coords[0])
                                        v_coords.append(coords[1])
                                        valid_uvs += 1
                                except:
                                    continue

                            if valid_uvs >= 3 and u_coords and v_coords:
                                # Calculate UV bounding box area
                                u_min, u_max = min(u_coords), max(u_coords)
                                v_min, v_max = min(v_coords), max(v_coords)
                                u_range = u_max - u_min
                                v_range = v_max - v_min

                                if not quiet:
                                    print(
                                        f"    DEBUG: UV bounds - U: [{u_min:.6f}, {u_max:.6f}] ({u_range:.6f}), V: [{v_min:.6f}, {v_max:.6f}] ({v_range:.6f})"
                                    )

                                # If UVs are spread out, estimate area based on coverage
                                if u_range > 0.001 or v_range > 0.001:
                                    # Use bounding box area as a reasonable estimate
                                    bbox_area = u_range * v_range
                                    # Assume UVs cover roughly 60% of bounding box (typical for unwrapped meshes)
                                    total_face_area = bbox_area * 0.6
                                    if not quiet:
                                        print(
                                            f"    DEBUG: UV bbox area estimate: {bbox_area:.6f}, adjusted: {total_face_area:.6f}"
                                        )
                                else:
                                    if not quiet:
                                        print(
                                            f"    DEBUG: UV coordinates too tightly packed for area estimation"
                                        )
                            else:
                                if not quiet:
                                    print(
                                        f"    DEBUG: Insufficient valid UV coordinates ({valid_uvs})"
                                    )

                    except Exception as e:
                        if not quiet:
                            print(f"    DEBUG: UV coordinate analysis failed: {e}")
                        pass

                # Method 5: Final fallback - check if UVs exist and assign proportional value
                if total_face_area <= 0:
                    if not quiet:
                        print(f"    DEBUG: Using final fallback method...")

                    try:
                        uv_count = pm.polyEvaluate(shape, uvcoord=True) or 0
                        if not quiet:
                            print(f"    DEBUG: UV count for fallback: {uv_count}")

                        if uv_count > 0:
                            # If UVs exist but area calculation failed, use UV count as proxy
                            # Higher UV count generally indicates more detailed UV mapping
                            # Use a scaling factor based on face count and UV density
                            uv_density = (
                                uv_count / face_count if face_count > 0 else 1.0
                            )
                            total_face_area = (
                                uv_density * 0.001
                            )  # Small but meaningful value

                            if not quiet:
                                print(
                                    f"    DEBUG: Fallback area based on UV density ({uv_density:.3f}): {total_face_area:.6f}"
                                )
                        else:
                            if not quiet:
                                print(f"    DEBUG: No UVs found, area remains 0")
                    except Exception as e:
                        if not quiet:
                            print(f"    DEBUG: Fallback method failed: {e}")
                        pass

            except Exception:
                total_face_area = 0.0
                face_count = 0

            # Get UV shell information for additional quality metrics
            shell_count = 0
            shells_with_area = 0

            try:
                # Prefer querying uvShell directly on the specific set to avoid relying on current set
                shell_count = (
                    pm.polyEvaluate(shape, uvShell=True, uvSetName=uv_set) or 0
                )

                # For now, assume shells with area = shell count if we have total area
                shells_with_area = shell_count if total_face_area > 1e-6 else 0

            except Exception as e:
                shell_count = 0
                shells_with_area = 0

            # Restore original current set (do not let failures nuke computed metrics)
            try:
                if original_current:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=original_current)
            except Exception:
                if not quiet:
                    print(
                        "    DEBUG: Failed to restore original current UV set; ignoring"
                    )

            return {
                "total_face_area": total_face_area,  # Actual sum of UV face areas
                "face_count": face_count,
                "shell_count": shell_count,
                "shells_with_area": shells_with_area,
                "avg_face_area": total_face_area / face_count if face_count > 0 else 0,
                "area_variance": 0.0,  # Simplified for now
            }

        except Exception:
            return {
                "total_face_area": 0.0,
                "face_count": 0,
                "shell_count": 0,
                "shells_with_area": 0,
                "avg_face_area": 0.0,
                "area_variance": 0.0,
            }

    @staticmethod
    def _get_uv_set_area(shape, uv_set: str) -> float:
        """Calculate the total actual UV face area for a UV set (NOT bounding box area).

        Parameters:
            shape: The mesh shape to check
            uv_set (str): Name of the UV set to analyze

        Returns:
            float: Total actual UV face area (sum of all face areas in UV space)
        """
        metrics = UvUtils._get_uv_set_metrics(shape, uv_set, quiet=True)
        return metrics["total_face_area"]

    @staticmethod
    def _find_uv_set_with_data(
        shape, prefer_largest_area: bool = False, quiet: bool = True
    ) -> str:
        """Find the UV set that contains actual UV data (not empty).

        Parameters:
            shape: The mesh shape to check
            prefer_largest_area (bool): If True, prefer UV set with largest area coverage

        Returns:
            str: Name of the UV set with data, or None if all are empty
        """
        try:
            all_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
            original_current = pm.polyUVSet(shape, query=True, currentUVSet=True)
            # Normalize current UV set to a single string (Maya may return a list)
            if isinstance(original_current, (list, tuple)):
                original_current = original_current[0] if original_current else None

            # First pass: look for sets with UV coordinates
            sets_with_uvs = []

            for uv_set in all_sets:
                try:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                    uv_count = pm.polyEvaluate(shape, uvcoord=True)

                    if uv_count > 0:
                        sets_with_uvs.append((uv_set, uv_count))

                except RuntimeError:
                    continue

            # If no sets have UVs, return None
            if not sets_with_uvs:
                if original_current:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=original_current)
                return None

            # If only one set has UVs, return it
            if len(sets_with_uvs) == 1:
                if original_current:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=original_current)
                return sets_with_uvs[0][0]

            # Multiple sets have UVs - need to determine which is "primary"
            if prefer_largest_area:
                if not quiet:
                    print(
                        f"    DEBUG: Entering prefer_largest_area branch with {len(sets_with_uvs)} UV sets"
                    )
                # Use UV face area coverage as the primary criterion (NOT bounding box)
                uv_metrics = []
                for uv_set, count in sets_with_uvs:
                    metrics = UvUtils._get_uv_set_metrics(shape, uv_set, quiet)
                    uv_metrics.append((uv_set, count, metrics))

                # Sort by total face area (descending), then by shells with area, then by UV count
                uv_metrics.sort(
                    key=lambda x: (
                        x[2]["total_face_area"],
                        x[2]["shells_with_area"],
                        x[1],
                    ),
                    reverse=True,
                )

                if not quiet:
                    print(f"UV set analysis for {shape} (sorted by actual face area):")
                    for uv_set, count, metrics in uv_metrics:
                        print(
                            f"  {uv_set}: {count} UVs, {metrics['total_face_area']:.6f} face area, "
                            f"{metrics['face_count']} faces, {metrics['shells_with_area']}/{metrics['shell_count']} shells with area"
                        )

                    # If all areas are 0, provide debugging info
                    if all(
                        metrics["total_face_area"] <= 0 for _, _, metrics in uv_metrics
                    ):
                        print(f"  DEBUG: All UV sets show 0 area. This may indicate:")
                        print(f"    - UVs are not properly mapped to faces")
                        print(
                            f"    - UV coordinates are all at the same position (0,0)"
                        )
                        print(
                            f"    - Maya's polyEvaluate uvFaceArea is not working as expected"
                        )
                        print(
                            f"    - UV sets may be placeholder/empty despite having UV coordinates"
                        )

                best_set = (
                    uv_metrics[0][0],
                    uv_metrics[0][1],
                    uv_metrics[0][2]["total_face_area"],
                )  # (name, count, area)
                if not quiet:
                    print(f"    DEBUG: best_set from area analysis: {best_set}")
            else:
                # Prefer 'map1' if it exists and has UVs
                for uv_set, count in sets_with_uvs:
                    if uv_set == "map1":
                        if original_current:
                            pm.polyUVSet(
                                shape, currentUVSet=True, uvSet=original_current
                            )
                        return uv_set

                # If no 'map1', prefer the set with the most UVs
                best_set = max(sets_with_uvs, key=lambda x: x[1])
                if not quiet:
                    print(f"    DEBUG: best_set from UV count: {best_set}")

            # Restore original current set and return the chosen UV set
            try:
                if original_current:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=original_current)
            except Exception as e:
                if not quiet:
                    print(
                        f"    DEBUG: Failed to restore original UV set in success path: {e}"
                    )

            chosen_uv_set = best_set[0] if best_set else None
            if not quiet:
                print(f"    DEBUG: returning chosen UV set: '{chosen_uv_set}'")

            return chosen_uv_set

        except Exception as e:
            # Restore original current set on any error
            if not quiet:
                print(f"    DEBUG: Exception in _find_uv_set_with_data: {e}")
                import traceback

                traceback.print_exc()
            try:
                if original_current:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=original_current)
            except Exception:
                pass
            return None

    @classmethod
    @CoreUtils.undoable
    def cleanup_uv_sets(
        cls,
        objects,
        remove_empty: bool = True,
        keep_only_primary: bool = True,
        rename_primary_to_map1: bool = True,
        force_rename: bool = False,
        prefer_largest_area: bool = False,
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
            prefer_largest_area (bool): If True, choose UV set with largest area coverage as primary.
            quiet (bool): If True, suppress output messages.
        """
        # Ensure consistent object handling like in remove_empty_uv_sets
        objects = NodeUtils.get_transform_node(objects)

        for obj in objects:
            # Get the proper shape node using NodeUtils reliable method
            try:
                shape = NodeUtils.get_shape_node(obj, returned_type="obj")
                if isinstance(shape, list) and len(shape) > 0:
                    shape = shape[0]  # Get first shape if list returned
            except Exception as e:
                if not quiet:
                    print(f"{obj}: NodeUtils failed to get shape: {e}")
                continue

            # Ensure we have a valid mesh shape
            if not shape:
                if not quiet:
                    print(f"{obj}: no shape node found")
                continue

            if not isinstance(shape, pm.nt.Mesh):
                if not quiet:
                    print(f"{obj}: shape {shape} is not a mesh (type: {type(shape)})")
                continue

            if not shape.hasAttr("uvSet"):
                if not quiet:
                    print(f"{obj}: mesh {shape} has no uvSet attribute")
                continue

            try:
                # Get initial state
                uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                if not uv_sets:
                    continue

                current_uv_set = pm.polyUVSet(shape, query=True, currentUVSet=True)

                # Find the UV set with actual data - this should be our "primary" set
                uv_set_with_data = cls._find_uv_set_with_data(
                    shape, prefer_largest_area, quiet
                )

                if not quiet:
                    print(
                        f"{shape}: _find_uv_set_with_data returned: '{uv_set_with_data}' (prefer_largest_area={prefer_largest_area})"
                    )

                # If no specific UV set with data is found, use the first available set
                # This handles cases where objects have valid UVs but in default layouts
                if not uv_set_with_data:
                    # Check if any UV sets actually exist and have UV coordinates
                    has_any_uvs = False
                    for uv_set in uv_sets:
                        try:
                            pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
                            uv_count = pm.polyEvaluate(shape, uvcoord=True)
                            if uv_count > 0:
                                uv_set_with_data = uv_set
                                has_any_uvs = True
                                break
                        except RuntimeError:
                            continue

                    if not has_any_uvs:
                        if not quiet:
                            pm.warning(f"{shape}: no UV sets contain UV coordinates")
                        continue

                    if not uv_set_with_data:
                        # Fallback to first UV set if detection failed
                        uv_set_with_data = uv_sets[0]
                        if not quiet:
                            print(
                                f"{shape}: using first UV set '{uv_set_with_data}' as primary (detection inconclusive)"
                            )

                # Ensure the UV set with data is current
                try:
                    pm.polyUVSet(shape, currentUVSet=True, uvSet=uv_set_with_data)
                except RuntimeError as e:
                    if not quiet:
                        pm.warning(
                            f"{shape}: failed to set current UV set to '{uv_set_with_data}': {e}"
                        )
                    # Continue with processing using whatever is current

                # Phase 1: Remove empty UV sets if requested
                if remove_empty:
                    cls.remove_empty_uv_sets([obj], quiet=quiet)
                    # Refresh state after removing empty sets
                    uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                    if not uv_sets:
                        if not quiet:
                            pm.warning(
                                f"{shape}: no UV sets remain after removing empty sets"
                            )
                        continue

                # Phase 2: Remove secondary UV sets if requested
                if keep_only_primary and len(uv_sets) > 1:
                    # Keep only the chosen primary UV set regardless of list order
                    secondary_sets = [s for s in uv_sets if s != uv_set_with_data]

                    for uv_set in secondary_sets:
                        try:
                            pm.polyUVSet(shape, delete=True, uvSet=uv_set)
                            if not quiet:
                                print(f"{shape}: removed secondary UV set: {uv_set}")
                        except RuntimeError as e:
                            if not quiet:
                                pm.warning(
                                    f"{shape}: failed to delete UV set '{uv_set}': {e}"
                                )
                            continue

                    # Refresh UV sets list after deletions
                    uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                    if not uv_sets:
                        if not quiet:
                            pm.warning(f"{shape}: no UV sets remain after cleanup")
                        continue

                # Update primary after all operations
                # Prefer the chosen primary; fall back to first if something unexpected occurred
                primary_uv_set = (
                    uv_set_with_data
                    if uv_set_with_data in uv_sets
                    else (uv_sets[0] if uv_sets else None)
                )
                if not primary_uv_set:
                    continue

                # Phase 3: Rename primary UV set to 'map1' if requested
                if rename_primary_to_map1 and primary_uv_set != "map1":
                    # Refresh current state
                    current_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []

                    if "map1" in current_sets and primary_uv_set != "map1":
                        if force_rename:
                            try:
                                # Rename conflicting 'map1' to avoid collision
                                pm.polyUVSet(
                                    shape,
                                    rename=True,
                                    uvSet="map1",
                                    newUVSet="map1_conflict",
                                )
                                if not quiet:
                                    print(
                                        f"{shape}: renamed existing 'map1' → 'map1_conflict' to resolve conflict"
                                    )
                            except RuntimeError as e:
                                if not quiet:
                                    pm.warning(
                                        f"{shape}: failed to rename conflicting 'map1': {e}"
                                    )
                                continue  # Skip rename for this object
                        else:
                            if not quiet:
                                print(
                                    f"{shape}: 'map1' already exists — skipping rename (use force_rename=True to override)"
                                )
                            continue  # Skip rename for this object

                    # Now rename the primary UV set to 'map1'
                    try:
                        pm.polyUVSet(
                            shape, rename=True, uvSet=primary_uv_set, newUVSet="map1"
                        )
                        if not quiet:
                            print(
                                f"{shape}: renamed UV set '{primary_uv_set}' → 'map1'"
                            )

                        # Ensure 'map1' is set as current
                        pm.polyUVSet(shape, currentUVSet=True, uvSet="map1")

                    except RuntimeError as e:
                        if not quiet:
                            pm.warning(
                                f"{shape}: failed to rename '{primary_uv_set}' to 'map1': {e}"
                            )

                # Phase 4: Ensure primary UV set is first in order (using helper)
                try:
                    sets_after_rename = (
                        pm.polyUVSet(shape, query=True, allUVSets=True) or []
                    )
                    if sets_after_rename and sets_after_rename[0] != primary_uv_set:
                        new_order = [primary_uv_set] + [
                            s for s in sets_after_rename if s != primary_uv_set
                        ]
                        # Use helper that reorders safely by adjacent moves
                        UvUtils.reorder_uv_sets(obj, new_order)
                        if not quiet:
                            print(f"{shape}: reordered UV sets → {new_order}")
                        # Reassert current after reorder (Maya can flip it)
                        pm.polyUVSet(shape, currentUVSet=True, uvSet=primary_uv_set)
                        # Refresh local uv_sets cache
                        uv_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                except Exception as e:
                    if not quiet:
                        pm.warning(
                            f"{shape}: failed to reorder UV sets with '{primary_uv_set}' first: {e}"
                        )

                # Final step: Ensure the primary UV set is actually set as current
                try:
                    final_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                    if primary_uv_set in final_sets:
                        pm.polyUVSet(shape, currentUVSet=True, uvSet=primary_uv_set)
                        if not quiet:
                            print(
                                f"{shape}: explicitly set '{primary_uv_set}' as current UV set"
                            )
                    else:
                        if not quiet:
                            print(
                                f"{shape}: primary UV set '{primary_uv_set}' not found in final sets: {final_sets}"
                            )
                except RuntimeError as e:
                    if not quiet:
                        pm.warning(
                            f"{shape}: failed to set final current UV set to '{primary_uv_set}': {e}"
                        )

                # Final status report and validation
                if not quiet:
                    final_sets = pm.polyUVSet(shape, query=True, allUVSets=True) or []
                    final_current = pm.polyUVSet(shape, query=True, currentUVSet=True)
                    print(
                        f"{shape}: UV sets after cleanup: {final_sets} (current: {final_current})"
                    )

            except Exception as e:
                if not quiet:
                    pm.warning(f"{shape}: unexpected error during UV set cleanup: {e}")
                continue


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
