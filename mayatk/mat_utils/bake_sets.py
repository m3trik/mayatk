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
* :class:`LightmapIsolateSet` -- the objects (moving parts) that bake in a
  pass of their own and stay out of every other object's bake.
* :class:`LightmapBakeSet` -- the objects the last lightmap bake acted on, so
  the bake can be re-selected and re-run.

A set may be stored under its plain name or under the naming convention's
spelling of it (the ``objectSet`` affix, ``_SET`` by default --
:meth:`BakeSet.conventional_name`); either spelling reads, and :meth:`BakeSet.define`
writes the one asked for.

Hidden members need no special treatment: Maya's FBX exporter writes hidden
geometry verbatim (verified on 2025), so the export never touches the scene.
"""

import os
from typing import List, Optional, Tuple

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk


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
    #: The ``pythontk.NamingConvention`` entry whose affix a conventional
    #: spelling carries (the Naming panel's Set suffix).
    CONVENTION_KEY: str = "objectSet"

    @classmethod
    def conventional_name(cls) -> str:
        """:attr:`SET_NAME` spelled by the naming convention (``lightmapBaker_baked_SET``).

        The plain name when the convention's set entry is empty.
        """
        return ptk.NamingConvention.apply(cls.SET_NAME, cls.CONVENTION_KEY)

    @classmethod
    def _spellings(cls) -> Tuple[str, ...]:
        """Every name the set may be stored under: plain, conventional, legacy.

        The conventional spelling twice over -- the convention as it stands
        and as it shipped -- so a set saved before a studio changed its set
        affix still reads.
        """
        shipped = ptk.NamingConvention.DEFAULTS.get(cls.CONVENTION_KEY)
        names = (cls.SET_NAME, cls.conventional_name())
        if shipped is not None:
            names += (shipped.apply(cls.SET_NAME),)
        return tuple(dict.fromkeys(names + cls.LEGACY_SET_NAMES))

    @classmethod
    def _resolve_set(cls) -> Optional[str]:
        """The set node to read from: plain, then conventional, then legacy names."""
        for name in cls._spellings():
            if cmds.objExists(name):
                return name
        return None

    @classmethod
    def name(cls) -> Optional[str]:
        """The name the set is stored under in this scene, or ``None``."""
        return cls._resolve_set()

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
        return TextureBaker.resolve_meshes(members, descendants=True)

    @classmethod
    def define(
        cls, objects: Optional[List[str]] = None, conventional: bool = False
    ) -> List[str]:
        """Replace the set's contents with *objects* (default: the selection).

        Returns the resulting members. An empty input deletes the set --
        "no set" is the absence of the node, so a cleared set never lingers as
        a confusing empty container.

        Parameters:
            objects: The members; ``None`` for the selection, ``[]`` to clear.
            conventional: Store the set under :meth:`conventional_name` (the
                naming convention's Set affix) rather than :attr:`SET_NAME`.
                Either way every other spelling goes, so a scene carries one
                definition.
        """
        # ``None`` means "use the selection"; an explicit empty list means
        # "clear" -- collapsing the two would make ``define([])`` silently
        # capture whatever happened to be selected.
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        else:
            objects = cmds.ls(objects, long=True) or []
        name = cls.conventional_name() if conventional else cls.SET_NAME
        # ONE undo step: cleared and rebuilt as two, a single Ctrl+Z after
        # "Set From Selection" restored nothing and left NO set -- and the next
        # bake re-baked whatever the old set had excluded.
        from mayatk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_chunk(f"Define {name}"):
            cls.clear()
            if objects:
                cmds.sets(objects, name=name)
        return cls.members() if objects else []

    @classmethod
    def respell(cls, conventional: bool) -> Optional[str]:
        """Rename an existing set to its plain or conventional spelling; the name it ends under.

        ``None`` when the scene has no such set. One undo step.
        """
        current = cls._resolve_set()
        if current is None:
            return None
        wanted = cls.conventional_name() if conventional else cls.SET_NAME
        if current == wanted:
            return current
        from mayatk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_chunk(f"Rename {current}"):
            for name in cls._spellings():
                if name not in (current, wanted) and cmds.objExists(name):
                    cmds.delete(name)
            if cmds.objExists(wanted):
                cmds.delete(wanted)
            return cmds.rename(current, wanted)

    @classmethod
    def clear(cls) -> None:
        """Delete the set node under every spelling (members themselves are untouched)."""
        for name in cls._spellings():
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
    keep casting shadows and bouncing light onto the objects that DO bake --
    the way to keep a static object's shadow without baking it, since a mesh
    neither baked nor excluded moves and is out of the render
    (``LightmapBaker._out_of_render``).
    Only their own bake is skipped, and a bake reverts nothing first, so a
    map baked earlier (a hero prop at a higher preset) survives a room
    re-bake. Read by :meth:`mayatk.LightmapBaker.bake_targets`, so the
    panel and a headless bake of the same scene skip the same objects.
    """

    SET_NAME = "lightmapBaker_exclude"


class LightmapIsolateSet(BakeSet):
    """Objects that bake in a pass of their own, out of every other bake's render.

    For a part that moves against what is baked around it -- a locker door on
    its cabinet, a lid, a drawer: it still gets a map, lit by the static scene
    at its authored pose, but it casts no shadow or bounce into another
    object's map (the cabinet's would keep a door-shaped shadow wherever the
    door swung), and no other isolated object casts into its own. Each member
    -- with the meshes under it (:meth:`groups`) -- bakes alone
    (:meth:`LightmapBaker._bake_to_lightmap_uvs`) and still packs
    into its material's atlas. A member of :class:`LightmapExcludeSet` as well
    gets no map, and is out of the render like any other isolated object.
    """

    SET_NAME = "lightmapBaker_isolate"

    @classmethod
    def groups(cls) -> List[List[str]]:
        """The set's meshes by member: what lies under each one moves together.

        A door stored as its mesh brings the glass parented under it along
        (unlike :meth:`BakeSet.meshes`, where a mesh member stands for itself
        alone), so the two are out of other bakes' render together and see
        each other in their own. A mesh under two members counts with the
        outermost (the glass stored beside its door).
        """
        from mayatk.mat_utils.texture_baker import TextureBaker

        seen: set = set()
        groups: List[List[str]] = []
        # Outermost first: a set's member order is Maya's, not the define's.
        for member in sorted(cls.members(), key=lambda m: m.count("|")):
            below = (
                []
                if "." in member  # faces stand for their mesh alone
                else cmds.listRelatives(
                    member, allDescendents=True, fullPath=True, type="transform"
                )
                or []
            )
            meshes = [
                m
                for m in TextureBaker.resolve_meshes([member] + below)
                if m not in seen
            ]
            seen.update(meshes)
            if meshes:
                groups.append(meshes)
        return groups

    @classmethod
    def meshes(cls) -> List[str]:
        """Every mesh :meth:`groups` holds: each member with all it carries."""
        return [mesh for group in cls.groups() for mesh in group]


class LightmapBakeSet(BakeSet):
    """The objects the last lightmap bake acted on.

    Written by the Lightmap Baker panel after a bake when its *Save After
    Bake* switch is on: every object the bake was asked to bake -- the Scope
    minus the Exclude set and what Arnold renders nothing of -- whether or not
    it finished, so selecting the set and baking again re-runs that bake.
    Each bake replaces it; the panel names it plain or by the naming
    convention (:meth:`BakeSet.conventional_name`). A script reads it the
    same way: ``LightmapBakeSet.define(result.maps)`` after
    :meth:`mayatk.LightmapBaker.bake`.
    """

    SET_NAME = "lightmapBaker_baked"
