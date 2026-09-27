# !/usr/bin/python
# coding=utf-8
"""Scene-data sidecar -- Maya's scene hooks over ``ptk.SceneDataSidecarBase``.

The sidecar's file format, naming, migration, manifest I/O, diff report and
comparison are pythontk's (``core_utils/engines/scene_export/
scene_data_sidecar.py``), shared with blendertk; see that module for the v3
format.  What stays here is what reaches the Maya scene or spells its paths:

- :meth:`SceneDataSidecar.expand_to_descendants` -- ``cmds.listRelatives``
  (``allDescendents``), the scope ``cmds.file(exportSelected=True)`` writes;
- :meth:`SceneDataSidecar.drop_intermediate` -- intermediate shapes FBX never
  ships;
- :meth:`SceneDataSidecar.build_clean_path_set` -- DAG long paths with the
  leading ``|`` and namespaces stripped, closed under ancestors;
- :meth:`SceneDataSidecar._close` -- a recorded set closed under ancestors on
  comparison (:meth:`~pythontk.SceneDataSidecarBase.with_ancestors`): Maya's
  ``exportSelected`` ships every node's parent chain.
"""

import pythontk as ptk


class SceneDataSidecar(ptk.SceneDataSidecarBase):
    """The scene-data sidecar stored alongside Maya exports.

    Everything but the scene hooks is :class:`pythontk.SceneDataSidecarBase`
    (one sidecar per export stem, ``base_stem=True`` for a versioned series).
    """

    @classmethod
    def _close(cls, paths) -> set:
        """Both sides of a comparison closed under ancestors (see
        :meth:`with_ancestors`): manifests written before the rule carry
        leaves + shapes only, and a caller may hand over a raw set -- either
        way the ancestors shipped.
        """
        return cls.with_ancestors(paths)

    @classmethod
    def build_clean_path_set(cls, objects) -> set:
        """Build a set of namespace-stripped hierarchy paths from DAG long paths.

        Strips leading ``|`` and namespace prefixes from each component, then
        closes the set under ancestors (see :meth:`with_ancestors`) so the
        recorded hierarchy is the one that ships.
        """
        paths = set()
        for obj in objects:
            path = obj.lstrip("|")
            if ":" in path:
                path = "|".join(p.split(":")[-1] for p in path.split("|"))
            paths.add(path)
        return cls.with_ancestors(paths)

    @staticmethod
    def expand_to_descendants(objects) -> list:
        """Return *objects* plus all their DAG descendants (full paths).

        Uses ``maya.cmds.listRelatives(allDescendents=True)`` so the
        manifest captures the same scope that
        ``cmds.file(exportSelected=True)`` would export.
        """
        from maya import cmds

        all_paths = list(objects)
        for obj in objects:
            descendants = (
                cmds.listRelatives(obj, allDescendents=True, fullPath=True) or []
            )
            all_paths.extend(descendants)
        return all_paths

    @staticmethod
    def drop_intermediate(nodes) -> list:
        """*nodes* minus intermediate shapes (``…ShapeOrig`` and kin).

        An intermediate shape is construction data — the pre-history input
        Maya parks on the transform the moment a history-free mesh gets a
        deformer or poly op.  FBX writes the evaluated mesh only, never the
        intermediate as a node, so it is not part of the shipped hierarchy;
        recording it made the exporter's check fail with
        ``+ GRP|mesh|meshShapeOrig`` after an innocuous modelling edit.
        Applied to the WHOLE export set, not just descendants: ``all`` mode
        (``cmds.ls(transforms=True, geometry=True)``) lists intermediates as
        first-class objects.  Transforms pass through untouched.
        """
        from maya import cmds

        nodes = list(nodes)
        if not nodes:
            return nodes
        # ls(noIntermediate=True) filters shapes only; transforms are kept.
        # Order is irrelevant downstream (the result feeds a set).
        return cmds.ls(nodes, noIntermediate=True, long=True) or []
