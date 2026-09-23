# !/usr/bin/python
# coding=utf-8
"""Scene-stored bake sets: named object sets the bake tools read.

:class:`BakeSet` is the storage: a plain ``objectSet`` under a fixed name, so
it saves with the file, shows up in the Outliner, and can't go stale against a
scene it was never captured in. Same pattern as
:class:`mayatk.mat_utils.emissive_groups.EmissiveGroups` and
:class:`mayatk.display_utils.color_id.ColorId`. Each subclass is one question
the scene answers:

* :class:`BakeSourceSet` -- the ONE cross-tool definition of "this scene's
  bake source" (the *bake from* geometry -- often just the texture-set donor,
  not necessarily a high-poly mesh). Both texture bridges consume it:

  * **Substance**: the set exports as a companion ``<name>_source.fbx`` and is
    wired as Painter's *Hipoly Mesh* (Painter's name for the slot, not ours).
  * **Marmoset**: the set exports as a companion ``<name>_source.fbx`` that the
    bake template parents into the baker's *High* container (Toolbag's name),
    while the scoped selection becomes the bake *target*. This replaces
    suffix-based classification, which cannot work when source and target
    hierarchies use identical mesh names (the common retopo / UV-transfer /
    atlas-consolidation scene layout).

* :class:`LightmapExcludeSet` -- the objects a lightmap bake gives no map of
  their own, while they stay in the render and light the rest.

Hidden members need no special treatment: Maya's FBX exporter writes hidden
geometry verbatim (verified on 2025), so the export never touches the scene.
"""

import os
from typing import List, Optional, Tuple

try:
    from maya import cmds
except ImportError:
    pass


class BakeSet:
    """A scene's named object set, stored as a plain ``objectSet``.

    Subclasses name the set (:attr:`SET_NAME`) and may list names it was saved
    under before (:attr:`LEGACY_SET_NAMES`); reading resolves those
    transparently and the next :meth:`define` migrates the scene to the
    canonical name.
    """

    SET_NAME: str = ""
    #: Prior set names, newest first. Read-only fallback; :meth:`define` /
    #: :meth:`clear` remove them so a scene never carries two competing
    #: definitions.
    LEGACY_SET_NAMES: Tuple[str, ...] = ()

    @classmethod
    def _resolve_set(cls) -> Optional[str]:
        """The set node to read from: canonical first, then legacy names."""
        for name in (cls.SET_NAME,) + cls.LEGACY_SET_NAMES:
            if cmds.objExists(name):
                return name
        return None

    @classmethod
    def exists(cls) -> bool:
        """Whether the set node (canonical or legacy) is present."""
        return cls._resolve_set() is not None

    @classmethod
    def members(cls) -> List[str]:
        """Long names of the set's surviving members (deleted nodes drop out)."""
        node = cls._resolve_set()
        if node is None:
            return []
        members = cmds.sets(node, query=True) or []
        return cmds.ls(members, long=True) or []

    @classmethod
    def meshes(cls) -> List[str]:
        """The mesh transforms the set covers -- a group member counts its descendants.

        A member is whatever was selected when the set was defined: a mesh, a
        group of them, a shape or faces. Each resolves through
        :meth:`mayatk.TextureBaker.resolve_meshes` (the one definition of a
        bakeable mesh), after a group has been expanded to what lies under it.
        """
        from mayatk.mat_utils.texture_baker import TextureBaker

        members = cls.members()
        if not members:
            return []
        # Only a TRANSFORM member stands for what is under it; faces of a mesh
        # name that mesh alone, not the children parented below it.
        groups = [
            m
            for m in members
            if "." not in m and cmds.objectType(m, isAType="transform")
        ]
        below = (
            cmds.listRelatives(
                groups, allDescendents=True, fullPath=True, type="transform"
            )
            if groups
            else None
        ) or []
        return TextureBaker.resolve_meshes(members + below)

    @classmethod
    def define(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Replace the set's contents with *objects* (default: the selection).

        Returns the resulting members. An empty input deletes the set --
        "no set" is the absence of the node, so a cleared set never lingers as
        a confusing empty container.
        """
        # ``None`` means "use the selection"; an explicit empty list means
        # "clear" -- collapsing the two would make ``define([])`` silently
        # capture whatever happened to be selected.
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        else:
            objects = cmds.ls(objects, long=True) or []
        # ONE undo step: cleared and rebuilt as two, a single Ctrl+Z after
        # "Set From Selection" restored nothing and left NO set -- and the next
        # bake re-baked whatever the old set had excluded.
        from mayatk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_chunk(f"Define {cls.SET_NAME}"):
            cls.clear()
            if objects:
                cmds.sets(objects, name=cls.SET_NAME)
        return cls.members() if objects else []

    @classmethod
    def clear(cls) -> None:
        """Delete the set node(s) (members themselves are untouched)."""
        for name in (cls.SET_NAME,) + cls.LEGACY_SET_NAMES:
            if cmds.objExists(name):
                cmds.delete(name)


class BakeSourceSet(BakeSet):
    """The scene's bake source, shared by the Substance and Marmoset bridges.

    Lived in ``substance_bridge`` originally (as ``HighPolySet``); promoted
    here once the Marmoset bake workflow needed the same concept — one scene,
    one bake-source definition, every bridge agrees on it. Renamed to
    source/target vocabulary because the set is frequently a texture-set
    donor at matching resolution, not a high-poly mesh.
    """

    SET_NAME = "bakeBridge_source"
    LEGACY_SET_NAMES = ("bakeBridge_highPoly", "substanceBridge_highPoly")
    #: Suffix appended to an export stem for the companion bake-source file.
    #: One convention across every bridge (see :meth:`companion_path`).
    FILE_SUFFIX = "_source"

    @classmethod
    def companion_path(cls, export_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx``.

        The single source of truth for where a bridge's bake-source companion
        export lands relative to its main export -- substance and marmoset
        both derive their paths here so the convention can't drift.
        """
        stem, ext = os.path.splitext(export_path)
        return f"{stem}{cls.FILE_SUFFIX}{ext}"


class LightmapExcludeSet(BakeSet):
    """Objects every lightmap bake of the scene gives no map of their own.

    Excluded objects stay in the render: Arnold still traces them, so they
    keep casting shadows and bouncing light onto the objects that DO bake.
    Only their own bake is skipped, and a bake reverts nothing first, so a
    map baked earlier (a hero prop at a higher preset) survives a room
    re-bake. Read by :meth:`mayatk.LightmapBaker.bake_targets`, so the
    panel and a headless bake of the same scene skip the same objects.
    """

    SET_NAME = "lightmapBaker_exclude"
