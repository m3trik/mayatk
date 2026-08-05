# !/usr/bin/python
# coding=utf-8
"""Scene-stored bake-source set shared by the hand-off bridges.

:class:`BakeSourceSet` is the ONE cross-tool definition of "this scene's
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

Stored as a plain ``objectSet`` so it saves with the file, shows up in the
Outliner, and can't go stale against a scene it was never captured in. Same
pattern as :class:`mayatk.mat_utils.emissive_groups.EmissiveGroups` and
:class:`mayatk.display_utils.color_id.ColorId`.

Hidden members need no special treatment: Maya's FBX exporter writes hidden
geometry verbatim (verified on 2025), so the export never touches the scene.
"""

import os
from typing import List, Optional

try:
    from maya import cmds
except ImportError:
    pass


class BakeSourceSet:
    """The scene's bake source, stored as a plain ``objectSet``.

    Lived in ``substance_bridge`` originally (as ``HighPolySet``); promoted
    here once the Marmoset bake workflow needed the same concept — one scene,
    one bake-source definition, every bridge agrees on it. Renamed to
    source/target vocabulary because the set is frequently a texture-set
    donor at matching resolution, not a high-poly mesh. Scenes saved under
    either prior name resolve transparently and the next :meth:`define`
    migrates the scene to the canonical name.
    """

    SET_NAME = "bakeBridge_source"
    #: Prior set names, newest first. Read-only fallback; :meth:`define` /
    #: :meth:`clear` remove them so a scene never carries two competing
    #: definitions.
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

    @classmethod
    def _resolve_set(cls) -> Optional[str]:
        """The set node to read from: canonical first, then legacy names."""
        for name in (cls.SET_NAME,) + cls.LEGACY_SET_NAMES:
            if cmds.objExists(name):
                return name
        return None

    @classmethod
    def exists(cls) -> bool:
        """Whether a bake-source set node (canonical or legacy) is present."""
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
    def define(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Replace the set's contents with *objects* (default: the selection).

        Returns the resulting members. An empty input deletes the set --
        "no bake source" is the absence of the node, so a cleared set never
        lingers as a confusing empty container.
        """
        # ``None`` means "use the selection"; an explicit empty list means
        # "clear" -- collapsing the two would make ``define([])`` silently
        # capture whatever happened to be selected.
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        else:
            objects = cmds.ls(objects, long=True) or []
        if not objects:
            cls.clear()
            return []
        cls.clear()
        cmds.sets(objects, name=cls.SET_NAME)
        return cls.members()

    @classmethod
    def clear(cls) -> None:
        """Delete the set node(s) (members themselves are untouched)."""
        for name in (cls.SET_NAME,) + cls.LEGACY_SET_NAMES:
            if cmds.objExists(name):
                cmds.delete(name)


#: Back-compat alias -- the class shipped one release under the old name.
HighPolySet = BakeSourceSet
