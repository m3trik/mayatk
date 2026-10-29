# !/usr/bin/python
# coding=utf-8
"""Scene-data sidecar -- Maya's host adapter for ``ptk.SceneDataSidecarBase``.

The sidecar's file format, naming, migration, manifest I/O, diff report and
comparison are pythontk's (``core_utils/engines/scene_export/
scene_data_sidecar.py``), shared with blendertk; see that module for the v3
format.  What stays here is what reaches the Maya scene or spells its paths,
as :class:`MayaSidecarHost` (a :class:`pythontk.SidecarHost`):

- ``expand_to_descendants`` -- ``cmds.listRelatives`` (``allDescendents``),
  the scope ``cmds.file(exportSelected=True)`` writes;
- ``drop_intermediate`` -- intermediate shapes FBX never ships;
- ``clean_path`` -- a DAG long path with the leading ``|`` and namespaces
  stripped;
- ``ships_parent_chain`` -- true: Maya's ``exportSelected`` ships every node's
  parent chain, so a recorded set is closed under ancestors
  (:meth:`~pythontk.SceneDataSidecarBase.with_ancestors`) when built and on
  comparison.

:class:`SceneDataSidecar` is the shared class bound to it: configuration only.
"""

import pythontk as ptk


class MayaSidecarHost:
    """What the scene-data sidecar asks of Maya (:class:`pythontk.SidecarHost`)."""

    ships_parent_chain = True

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

    @staticmethod
    def clean_path(path: str) -> str:
        """A DAG long path in the recorded spelling: the leading ``|`` and
        each component's namespace prefix stripped."""
        path = path.lstrip("|")
        if ":" in path:
            path = "|".join(p.split(":")[-1] for p in path.split("|"))
        return path


class SceneDataSidecar(ptk.SceneDataSidecarBase):
    """The scene-data sidecar stored alongside Maya exports.

    :class:`pythontk.SceneDataSidecarBase` bound to :class:`MayaSidecarHost`
    (one sidecar per export stem, ``base_stem=True`` for a versioned series).
    """

    HOST = MayaSidecarHost()
