# !/usr/bin/python
# coding=utf-8
"""Material assignment behind :class:`mayatk.MatUtils`.

Which geometry carries which material: assigning one, telling whether one is
assigned at all, snapshotting a mesh's per-face shading membership as plain
data and putting it back after a rebuild, and the reverse lookups -- objects
or faces by material, objects with none, objects grouped by material.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

from typing import List, Dict, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils


class _AssignmentInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _cluster_objects_by_distance(objects, threshold):
        """Clusters objects by spatial proximity (flood-fill, threshold-linked).

        Delegates the proximity flood-fill to the DCC-agnostic
        ``ptk.PointCloud.cluster_by_distance`` (shared with the Blender port);
        this only supplies each object's bounding-box centre and maps the
        returned index-clusters back to objects.
        """
        obj_list = list(objects)
        if not obj_list:
            return []
        if len(obj_list) == 1:
            return [obj_list]

        positions = []
        for obj in obj_list:
            xmin, ymin, zmin, xmax, ymax, zmax = cmds.xform(
                obj, q=True, ws=True, bb=True
            )
            positions.append(
                ((xmin + xmax) * 0.5, (ymin + ymax) * 0.5, (zmin + zmax) * 0.5)
            )

        index_clusters = ptk.PointCloud.cluster_by_distance(positions, threshold)
        return [[obj_list[i] for i in cluster] for cluster in index_clusters]

    @staticmethod
    def _materials_by_object(objects: List[str]) -> Dict[str, List[str]]:
        """Map each object to its assigned material(s) in a single scene pass.

        Batched equivalent of calling :meth:`get_mats` once per object: resolves
        shading-engine membership for the whole scene once (a handful of cmds
        calls) instead of issuing ~5 calls per object. Returns
        ``{obj_long_name: [material, ...]}`` for every input object.
        """
        objects = cmds.ls(objects, long=True) or []
        if not objects:
            return {}

        # Resolve each input transform to its shape(s) and build a reverse map.
        shape_to_obj: Dict[str, str] = {}
        for obj in objects:
            if cmds.nodeType(obj) in NodeUtils.SURFACE_TYPES:
                shapes = [obj]
            else:
                shapes = (
                    cmds.listRelatives(
                        obj, shapes=True, fullPath=True, noIntermediate=True
                    )
                    or []
                )
            for shape in shapes:
                shape_to_obj[shape] = obj

        result: Dict[str, set] = {obj: set() for obj in objects}
        obj_set = set(objects)
        if not shape_to_obj:
            return {obj: [] for obj in objects}

        # One pass over the scene's shading engines: for each, find which of our
        # shapes it touches (whole-object or per-face), then attribute its
        # surface shader to those objects.
        for sg in cmds.ls(type="shadingEngine") or []:
            members = cmds.sets(sg, q=True) or []
            if not members:
                continue

            touched = set()
            for member in cmds.ls(members, long=True) or []:
                base = member.split(".")[0]  # strip any .f[...] component
                if base in shape_to_obj:
                    touched.add(shape_to_obj[base])
                elif base in obj_set:  # member is a transform we were given
                    touched.add(base)

            if not touched:
                continue

            mats = (
                cmds.listConnections(
                    f"{sg}.surfaceShader", source=True, destination=False
                )
                or []
            )
            for obj in touched:
                result[obj].update(mats)

        return {obj: list(mats) for obj, mats in result.items()}

    @classmethod
    def _group_objects_by_material(cls, objects, cluster_by_distance, threshold):
        """Body of :meth:`MatUtils.group_objects_by_material`."""
        groups = {}

        objects = cmds.ls(cls._to_strs(objects), long=True) or []
        mats_by_obj = cls._materials_by_object(objects)

        for obj in objects:
            mats = mats_by_obj.get(obj, [])

            if not mats:
                key = "None"
            elif len(mats) > 1:
                key = tuple(sorted(mats))
            else:
                key = mats[0]

            if key not in groups:
                groups[key] = []
            groups[key].append(obj)

        if cluster_by_distance:
            clustered_groups = {}
            for mat_key, objs in groups.items():
                clusters = cls._cluster_objects_by_distance(objs, threshold)
                for i, cluster in enumerate(clusters):
                    new_key = (mat_key, i) if len(clusters) > 1 else mat_key
                    clustered_groups[new_key] = cluster
            return clustered_groups
        return groups

    @staticmethod
    def _is_mat_assigned(mat):
        """Body of :meth:`MatUtils.is_mat_assigned`."""
        mat_str = str(mat)
        try:
            shading_engines = cmds.listConnections(mat_str, type="shadingEngine") or []
        except Exception:
            return False
        for sg in set(shading_engines):
            try:
                members = cmds.sets(sg, query=True) or []
            except Exception:
                continue
            if members:
                return True
        return False

    @staticmethod
    def _is_connected(mat, delete):
        """Body of :meth:`MatUtils.is_connected`."""
        try:
            mat_list = cmds.ls(str(mat), type="shadingDependNode", flatten=True) or []
            mat = mat_list[0]
        except (IndexError, TypeError):
            print(f"Error: Material {mat} not found or invalid.")
            return False

        connected_shading_groups = cmds.listConnections(
            f"{mat}.outColor", type="shadingEngine"
        )
        if not connected_shading_groups:
            if delete:
                cmds.delete(mat)
            return True

        return False

    @classmethod
    def _assign_mat(cls, objects, mat_name):
        """Body of :meth:`MatUtils.assign_mat`."""
        if not objects:
            raise ValueError("No objects provided to assign material.")

        mat_name = str(mat_name)

        if cmds.objExists(mat_name):
            mat = mat_name
        else:
            preferred_type = cls._create_standard_shader(return_type="type")
            mat = cmds.shadingNode(preferred_type, name=mat_name, asShader=True)

        shading_groups = cmds.listConnections(mat, type="shadingEngine")
        if not shading_groups:
            shading_group = cmds.sets(
                name=f"{mat_name}SG", renderable=True, noSurfaceShader=True, empty=True
            )
            cmds.connectAttr(
                f"{mat}.outColor", f"{shading_group}.surfaceShader", force=True
            )
        else:
            shading_group = shading_groups[0]

        objects = cls._to_strs(objects)
        valid_objects = cmds.ls(objects, flatten=True) or []
        if valid_objects:
            cmds.sets(valid_objects, edit=True, forceElement=shading_group)

    @staticmethod
    def _get_shading_assignments(obj):
        """Body of :meth:`MatUtils.get_shading_assignments`."""
        shape = NodeUtils.get_shape(obj, no_intermediate=True)
        if not shape:
            return {}
        # Long paths the set members may be expressed under: component sets
        # reference the transform, whole-object sets the shape.
        owners = set(cmds.ls(shape, long=True) or [])
        owners.update(cmds.listRelatives(shape, parent=True, fullPath=True) or [])

        result: Dict[str, Optional[List[int]]] = {}
        for sg in set(cmds.listConnections(shape, type="shadingEngine") or []):
            whole = False
            faces: List[int] = []
            for m in (
                cmds.ls(cmds.sets(sg, q=True) or [], long=True, flatten=True) or []
            ):
                if m.split(".f[")[0] not in owners:
                    continue  # a different object that shares this shading group
                if ".f[" in m:
                    faces.append(int(m.split(".f[", 1)[1].rstrip("]")))
                else:
                    whole = True
            if whole and not faces:
                result[sg] = None
            elif faces:
                result[sg] = faces
        return result

    @staticmethod
    def _apply_shading_assignments(obj, assignments):
        """Body of :meth:`MatUtils.apply_shading_assignments`."""
        if not assignments:
            return
        shape = NodeUtils.get_shape(obj, no_intermediate=True)
        if not shape:
            return
        tf = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]

        per_face = {sg: f for sg, f in assignments.items() if f and cmds.objExists(sg)}
        whole = [
            sg for sg, f in assignments.items() if f is None and cmds.objExists(sg)
        ]

        # Pure single-material (no per-face overrides): a whole-object assignment
        # is the natural, cleanest form -- set it directly and return.
        if not per_face:
            for sg in whole:
                try:
                    cmds.sets(shape, edit=True, forceElement=sg)
                except Exception:
                    pass
            return

        if not tf:
            return

        # Multi-material: assign EVERY material as face components, never as a
        # whole-object set. After an in-place geometry rebuild (outMesh->inMesh +
        # delete(ch=True)) a whole-object forceElement followed by a per-face
        # split does NOT convert the whole assignment to a remainder -- it leaves
        # the object in BOTH the whole-object set AND the component sets at once.
        # That overlap is silently tolerated until the next poly op, which
        # resolves it to the whole-object material and drops every other one (a
        # multi-material mesh loses its extra materials -- the "neon green"
        # regression). Driving everything through components keeps the assignment
        # unambiguous so it survives the op.
        try:
            total = cmds.polyEvaluate(shape, face=True)
        except Exception:
            total = 0
        # The base material covers faces the snapshot doesn't (e.g. a bevel's new
        # chamfer faces, indexed past the originals): the whole-object SG if the
        # snapshot had one, else the per-face SG covering the most faces.
        base = whole[0] if whole else max(per_face, key=lambda s: len(per_face[s]))
        covered = set().union(*per_face.values())
        uncovered = [i for i in range(total) if i not in covered]

        def _force(faces, sg):
            try:
                cmds.sets([f"{tf}.f[{i}]" for i in faces], edit=True, forceElement=sg)
            except Exception:
                pass

        # Clean slate: park ALL faces on a neutral SG (as components) first, so
        # any stale/overlapping groups left by the rebuild are collapsed before
        # the real assignments land. A single range expression keeps this O(1) in
        # command size -- this runs on every preview refresh, so building one
        # string per face would lag a value-drag on a dense mesh.
        if total:
            try:
                cmds.sets(
                    f"{tf}.f[0:{total - 1}]",
                    edit=True,
                    forceElement="initialShadingGroup",
                )
            except Exception:
                pass
        for sg, faces in per_face.items():
            _force(faces, sg)
        if uncovered:
            _force(uncovered, base)

    @classmethod
    def _find_by_mat_id(cls, material, objects, shell):
        """Body of :meth:`MatUtils.find_by_mat_id`."""
        material = str(material)

        if not cmds.objExists(material):
            print(f"Material '{material}' does not exist.")
            return []

        if cmds.nodeType(material) == "VRayMultiSubTex":
            raise TypeError(
                "Invalid material type. If material is a multimaterial, please select a submaterial."
            )

        shading_groups = cmds.listConnections(material, type="shadingEngine")
        if not shading_groups:
            print(f"No shading groups found for material '{material}'.")
            return []

        objs_with_material = []

        target_transforms = set()
        if objects:
            objects = cls._to_strs(objects)
            objects = cmds.ls(objects, long=True) or []

            for obj in objects:
                if cmds.objExists(obj):
                    if cmds.nodeType(obj) == "transform":
                        target_transforms.add(obj)
                    else:
                        parents = cmds.listRelatives(obj, parent=True, fullPath=True)
                        if parents:
                            target_transforms.add(parents[0])

        for sg in shading_groups:
            members = cmds.sets(sg, query=True, noIntermediate=True) or []
            members = cmds.ls(members, long=True) or []

            for member in members:
                node = member.split(".")[0] if "." in member else member

                if cmds.nodeType(node) == "transform":
                    transform = node
                else:
                    parents = cmds.listRelatives(node, parent=True, fullPath=True)
                    transform = parents[0] if parents else node

                if objects and transform not in target_transforms:
                    continue

                if shell:
                    if transform not in objs_with_material:
                        objs_with_material.append(transform)
                else:
                    objs_with_material.append(member)

        return objs_with_material

    @classmethod
    def _find_unassigned(cls, objects, include_default):
        """Body of :meth:`MatUtils.find_unassigned`."""
        if objects:
            # ``objectsOnly`` first so a component selection resolves to its object
            # (like find_by_mat_id, which accepts components); ``dag=True`` then
            # walks at-or-below each input, so groups reach their shapes.
            objs = cmds.ls([str(o) for o in objects], objectsOnly=True, long=True) or []
            if not objs:  # scoped to nothing — an argless ls would scan the scene
                return []
            shapes = (
                cmds.ls(objs, dag=True, type="mesh", noIntermediate=True, long=True)
                or []
            )
        else:
            shapes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []

        defaults = cls._default_material_names()
        unassigned = []
        for shape in shapes:
            shading_groups = set(
                cmds.listConnections(shape, type="shadingEngine") or []
            )
            if shading_groups and not include_default:
                continue
            mats = cls.get_mats(shape)
            # No materials at all -> orphaned. Otherwise unassigned only when every
            # material found is one of Maya's built-in defaults.
            if mats and not all(CoreUtils.short_name(m) in defaults for m in mats):
                continue
            parents = cmds.listRelatives(shape, parent=True, fullPath=True)
            transform = parents[0] if parents else shape
            if transform not in unassigned:
                unassigned.append(transform)

        return unassigned
