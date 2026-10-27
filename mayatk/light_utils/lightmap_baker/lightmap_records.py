# !/usr/bin/python
# coding=utf-8
"""The scene record a lightmap bake leaves in Maya: markers, manifest, and the files they name.

:class:`LightmapRecords` is everything about a baked lightmap that is not the
bake itself. :class:`~mayatk.LightmapBaker` renders the maps; this class
records them and answers for them afterwards:

* **Markers** -- a JSON ``lightmapInfo`` string on each baked TRANSFORM (per
  instance, so every copy of a shared shape carries its own atlas rect):
  :meth:`LightmapRecords.commit`, :meth:`LightmapRecords.revert`,
  :meth:`LightmapRecords.baked_objects`,
  :meth:`LightmapRecords.superseding` (what a re-bake leaves behind, set
  aside),
  and :meth:`LightmapRecords.migrate_legacy` for markers older than the
  rect-binding contract.
* **The manifest** -- the ``lightmaps`` record that rides the FBX on
  the ``data_export`` carrier, rebuilt from the markers:
  :meth:`LightmapRecords.export_record` (the ``FbxUtils.PRODUCERS`` entry) and
  :meth:`LightmapRecords.refresh_export_metadata`.
* **Dependencies** -- where the files the markers name are NOW, and rewriting
  the stored folders: :meth:`LightmapRecords.lightmap_dependencies`,
  :meth:`LightmapRecords.search_dirs`,
  :meth:`LightmapRecords.heal_lightmap_paths`,
  :meth:`LightmapRecords.relocate_lightmaps`,
  :meth:`LightmapRecords.repath_lightmaps`,
  :meth:`LightmapRecords.normalize_lightmap_paths`. The rules are pythontk's
  generic :class:`~pythontk.FileDependencies` (files a record names by name
  plus a recorded folder), which blendertk's twin uses too; this class hands
  it the markers and Maya's own ways of resolving a folder, walking a tree and
  copying a file.
* **The bake baseline** -- what each map was baked against, so a re-bake
  redoes only what changed (``LightmapBaker.rebake_changed``): every commit
  rolls the private ``ptk.SceneRecords.LIGHTMAP_BAKE_STATE`` record forward
  (``ptk.BakeBaseline.advance``) from :meth:`LightmapRecords.scene_state`;
  :meth:`LightmapRecords.bake_state` reads it back.

Every method is a classmethod: the record is scene state, so the Texture Path
Editor, the Scene Exporter and the FBX producer read it without building a
baker.
"""

import contextlib
import hashlib
import json
import os
import re
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

if TYPE_CHECKING:
    import numpy as np

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils


class LightmapRecords(ptk.LoggingMixin):
    """Maya's lightmap markers and manifest, and the files they name."""

    #: The identity binding: an object's 0-1 lightmap UVs cover its whole map.
    IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (1.0, 1.0, 0.0, 0.0)

    # Per-transform JSON marker for a lighting-only lightmap: which map, on
    # which UV set, at what intensity and rect -- never its folder, which is
    # build-time state (:meth:`_folder_hints`), while the marker rides every
    # FBX as a user property. Non-destructive
    # bookkeeping (the material and UVs are untouched) -- it records what the
    # engine should composite and what to republish into the export manifest.
    LIGHTMAP_INFO_ATTR: str = "lightmapInfo"

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only
    # lightmap, regenerated from the per-transform markers. Rides the FBX as a
    # user property (the ``ptk.SceneRecords.LIGHTMAPS`` record, whose key and
    # version these are) -- purely informational unless consumed; unitytk's
    # optional editor helper reads it to auto-bind Unity's *native* lightmap
    # slots ("sidecar benefits, no sidecar file").
    # Its key under its old name, retired here: read the declaration's
    # (``ptk.SceneRecords.LIGHTMAPS.key``).
    LIGHTMAP_METADATA = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.key,
        "ptk.SceneRecords.LIGHTMAPS.key",
        remove_in="0.29.0",
        since="2026-10-24",
    )
    #: The record's schema version -- the registry's
    #: (``ptk.SceneRecords.LIGHTMAPS.version``), retired here.
    LIGHTMAP_METADATA_VERSION = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.version,
        "ptk.SceneRecords.LIGHTMAPS.version",
        remove_in="0.29.0",
        since="2026-10-24",
    )

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------

    @staticmethod
    def _set_string_attr(node: str, attr: str, value: str) -> None:
        """Create (if missing) and set a string attr on *node*.

        ``Attributes.set_attributes`` can't be used here: it omits the
        ``-type "string"`` flag and Maya rejects a string ``setAttr`` without it.
        """
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr(f"{node}.{attr}", value, type="string")

    @classmethod
    def _write_marker(cls, node: str, info: Dict[str, Any]) -> None:
        """Store *info* as *node*'s marker."""
        cls._set_string_attr(node, cls.LIGHTMAP_INFO_ATTR, json.dumps(info))

    # ------------------------------------------------------------------
    # Folder hints -- private, never on a marker
    # ------------------------------------------------------------------

    @staticmethod
    def _hint_key(map_name: Any) -> str:
        """A map's key in the folder record: its lower-case file name."""
        return os.path.basename(str(map_name or "")).lower()

    @staticmethod
    def _decode_folder_hints(data: Any) -> Dict[str, str]:
        """A stored folder record as ``{key: folder}`` (``{}`` for anything else)."""
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}

    @classmethod
    def _folder_hints(cls) -> Dict[str, str]:
        """``{lower-case map name: stored folder}`` -- where each map was written.

        This scene's private ``ptk.SceneRecords.LIGHTMAP_DIRS`` record on
        ``data_internal``, not the marker: a marker is a node attribute and
        rides every FBX as a user property, so a folder on it put build-setup
        data on the deliverable. A REFERENCED module's maps are read from its
        own record as well (:meth:`_folder_hint`).
        """
        from mayatk.node_utils.data_nodes import DataNodes

        if cmds is None:
            return {}
        spec = ptk.SceneRecords.LIGHTMAP_DIRS
        return cls._decode_folder_hints(spec.load(DataNodes, {}))

    @classmethod
    def _module_folder_hints(cls, namespace: str) -> Dict[str, str]:
        """The folder record a referenced module keeps in ITS ``data_internal``
        (``NS:data_internal``) -- where a module baked in its own scene records
        its maps -- resolved ABSOLUTE: spelled from the MODULE's project, which
        this scene does not share. ``{}`` when it has none. Creates nothing."""
        from mayatk.node_utils.data_nodes import DataNodes

        node = DataNodes.carriers_in(namespace).get(ptk.Scope.PRIVATE)
        spec = ptk.SceneRecords.LIGHTMAP_DIRS
        plug = f"{node}.{spec.key}" if node else ""
        if not plug or not cmds.objExists(plug):
            return {}
        hints = cls._decode_folder_hints(spec.decode(cmds.getAttr(plug), {}))
        try:
            module_file = cmds.referenceQuery(
                node, filename=True, withoutCopyNumber=True
            )
        except RuntimeError:
            module_file = ""  # imported, not referenced: no file to read it from
        base = DataNodes.project_root_of(module_file)
        return {
            key: ptk.FileUtils.resolve_portable_path(folder, base)
            for key, folder in hints.items()
        }

    @classmethod
    def _save_folder_hints(cls, hints: Dict[str, str]) -> None:
        """Store *hints* as this scene's folder record (an empty one clears it)."""
        from mayatk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.LIGHTMAP_DIRS.save(DataNodes, dict(sorted(hints.items())))

    @classmethod
    def _folder_hint(
        cls,
        info: Dict[str, Any],
        hints: Dict[str, str],
        node: Optional[str] = None,
        modules: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> str:
        """The stored folder of the map *info* names.

        This scene's record first (a repath here speaks for every reader of the
        map), then the record of the reference *node* sits under -- innermost
        namespace first; *modules* caches them across one pass -- then a LEGACY
        marker's own ``dir``, read until :meth:`migrate_folder_hints` lifts it.
        """
        key = cls._hint_key(info.get("map"))
        folder = hints.get(key)
        namespace = str(node or "").rsplit("|", 1)[-1].rpartition(":")[0]
        modules = {} if modules is None else modules
        while not folder and namespace:
            if namespace not in modules:
                modules[namespace] = cls._module_folder_hints(namespace)
            folder = modules[namespace].get(key)
            namespace = namespace.rpartition(":")[0]
        return str(folder or info.get("dir") or "")

    @classmethod
    def _prune_folder_hints(cls) -> None:
        """Drop the folder entries no marker names any more (a revert), and the
        writer entries nobody's.

        A writer entry naming another scene file still on disk
        (:meth:`foreign_writer`) stays: it is what keeps this scene off a map
        it no longer reads (:meth:`claims`) -- pruned, a Save As copy's next
        bake took its source's map name back and wrote over it.

        A writer entry this scene wrote stays too: a file it no longer reads is
        still its own to bake over (:meth:`strangers`), where an
        unrecorded one is another scene's.
        """
        hints, writers = cls._folder_hints(), cls._writers()
        if not (hints or writers):
            return
        named = {cls._hint_key(i.get("map")) for _t, i in cls._marker_records()}
        probe = cls.probe()
        if probe:
            named.add(cls._hint_key(probe["map"]))
        kept = {k: v for k, v in hints.items() if k in named}
        if kept != hints:
            cls._save_folder_hints(kept)
        kept = {
            k: v
            for k, v in writers.items()
            if k in named
            or cls.foreign_writer(k, writers)
            or cls._data_nodes().written_here(v)
        }
        if kept != writers:
            cls._save_writers(kept)

    # ------------------------------------------------------------------
    # Writers -- which scene file wrote each map
    # ------------------------------------------------------------------

    @classmethod
    def _writers(cls) -> Dict[str, str]:
        """``{lower-case map name: stored scene file}`` -- who wrote each map.

        This scene's ``ptk.SceneRecords.LIGHTMAP_WRITERS`` record, stamped by
        :meth:`commit`. ``""`` is a map committed while the scene was unsaved
        -- its own while it still is; the first save stamps it with the file
        written (``DataNodes.install_path_rebase``). A map with no entry was
        committed before the record existed,
        or its folder was lifted off a legacy marker
        (:meth:`migrate_folder_hints`) -- another scene's, for all this scene
        can tell.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        if cmds is None:
            return {}
        data = ptk.SceneRecords.LIGHTMAP_WRITERS.load(DataNodes, {})
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v or "") for k, v in data.items()}

    @classmethod
    def _save_writers(cls, writers: Dict[str, str]) -> None:
        """Store *writers* as the writer record (an empty one clears it)."""
        from mayatk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.LIGHTMAP_WRITERS.save(DataNodes, dict(sorted(writers.items())))

    @classmethod
    def _marked_nodes(cls) -> set:
        """Every node in the scene carrying a :attr:`LIGHTMAP_INFO_ATTR` marker.

        One attribute-scoped ``ls`` in place of a per-node ``attributeQuery``
        over every transform and mesh: on a 3k-transform scene the walk costs
        seconds, this costs milliseconds. Verified to return the identical set
        across namespaces, references, intermediate shapes, DAG instances,
        duplicate short names and markers that exist but were never set.

        Two flags are load-bearing:

        - ``recursive=True`` -- without it the ``*`` pattern matches only the
          current namespace, so every namespaced or REFERENCED marker is
          silently dropped (measured: 3 of 513 markers lost on a probe scene).
        - ``long=True`` -- callers compare against ``cmds.ls(..., long=True)``
          names. Both listings name a node by its first DAG path, so an
          instanced node compares equal in both; short names would be
          ambiguous the moment two groups share a leaf name.

        Returns:
            set: Long names of the marked nodes (any node type).
        """
        return set(
            cmds.ls(
                f"*.{cls.LIGHTMAP_INFO_ATTR}",
                objectsOnly=True,
                long=True,
                recursive=True,
            )
            or []
        )

    @classmethod
    def _marked_dag_nodes(cls) -> List[str]:
        """The marked transforms and meshes -- the two homes a marker can have.

        Sorted, from the one attribute-scoped lookup, rather than listing every
        transform and mesh in the scene to test each against it: those two
        type-scoped walks are what a whole-scene revert used to cost. A marked
        node of any other type (a material a script stamped, a NURBS shape) is
        not a lightmap home and is left out.
        """
        return [
            node
            for node in sorted(cls._marked_nodes())
            if cmds.objectType(node, isAType="transform")
            or cmds.nodeType(node) == "mesh"
        ]

    @classmethod
    def _marker_node(cls, obj: str) -> Optional[str]:
        """The node carrying *obj*'s lightmap marker, or ``None``.

        Current commits stamp the TRANSFORM (a transform is per-instance, so
        every copy of a shared shape can hold its own atlas rect); commits
        that predate the move stamped the shape. Transform wins when both
        exist. *obj* may be either node.
        """
        transform = NodeUtils.get_transform_node(obj) or obj
        # Long form: the returned name feeds getAttr/deleteAttr later, and a
        # short name is ambiguous the moment two groups share a leaf name.
        transform = (cmds.ls(transform, long=True) or [transform])[0]
        if cmds.attributeQuery(cls.LIGHTMAP_INFO_ATTR, node=transform, exists=True):
            return transform
        shape = NodeUtils.get_shape(obj)
        if shape and cmds.attributeQuery(
            cls.LIGHTMAP_INFO_ATTR, node=shape, exists=True
        ):
            return shape
        return None

    @classmethod
    def _marker_info(cls, obj: str) -> Dict[str, Any]:
        """*obj*'s marker as a dict (``{}`` if absent or unparsable).

        Reads through :meth:`_marker_node`, so it finds the marker whether the
        commit stamped the transform (current) or the shape (legacy).
        """
        node = cls._marker_node(obj)
        if node:
            try:
                return json.loads(
                    cmds.getAttr(f"{node}.{cls.LIGHTMAP_INFO_ATTR}") or "{}"
                )
            except ValueError:
                pass
        return {}

    @classmethod
    def _marker_records(
        cls, objects: Optional[List[str]] = None
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """``[(transform, marker info)]`` for every marked transform in scope.

        A legacy shape marker reports under its first transform; a transform
        marked itself wins over its shape's legacy marker. *objects* scopes to
        those transforms AND their descendants (an export set names roots; the
        lightmapped meshes sit under them). ``None`` is the whole scene; an
        empty list is nothing. The one marker enumeration every reader shares
        -- claims, dependencies, the manifest.
        """
        if cmds is None:
            return []
        roots: Optional[List[str]] = None
        if objects is not None:
            names = [str(o) for o in objects]
            roots = (cmds.ls(names, long=True) or []) if names else []

        def in_scope(transform: str) -> bool:
            if roots is None:
                return True
            return any(
                transform == root or transform.startswith(root + "|") for root in roots
            )

        records: List[Tuple[str, Dict[str, Any]]] = []
        seen: set = set()
        # The two homes only: a marker a script left on a material or a NURBS
        # shape names no lightmap an engine could bind, and publishing it
        # would ship an entry for an object that has no lightmap UVs at all.
        for node in cls._marked_dag_nodes():
            # ``isAType``: a marker may sit on a transform SUBTYPE (a joint,
            # a locator's parent); an exact ``nodeType`` test would send it
            # down the shape branch and record its parent instead.
            if cmds.objectType(node, isAType="transform"):
                transform = node
            else:
                parents = cmds.listRelatives(node, allParents=True, fullPath=True) or []
                transform = parents[0] if parents else node
            transform = (cmds.ls(transform, long=True) or [transform])[0]
            if transform in seen or not in_scope(transform):
                continue
            info = cls._marker_info(transform)
            if not info or not info.get("map"):
                continue
            seen.add(transform)
            records.append((transform, info))
        return records

    @classmethod
    def baked_objects(cls, objects: Optional[List[str]] = None) -> List[str]:
        """The objects :meth:`revert` would take the lightmap from.

        Those of *objects* that carry a lightmap marker -- the given nodes
        themselves, as :meth:`revert` reads them -- or, with ``None``, every
        marked transform in the scene. What a confirmation states before a
        revert runs.
        """
        if cmds is None:
            return []
        if objects is None:
            return [transform for transform, _info in cls._marker_records()]
        return [o for o in objects if cls._marker_node(o)]

    @classmethod
    def lightmap_info(cls, obj: str) -> Dict[str, Any]:
        """*obj*'s committed lightmap -- its marker plus where the map is NOW.

        The marker's own keys (``map``, ``uv_set``, ``intensity``,
        ``scaleOffset`` -- the identity for a marker that predates it --
        ``mode``) plus ``path``: the map resolved on disk by
        :meth:`lightmap_dependencies`' rules, ``None`` when it is nowhere.
        ``{}`` when *obj* carries no marker. What a tool that carries a bake to
        another object reads (:meth:`transfer_lightmaps`) instead
        of parsing the marker itself. *obj* may be the transform or its shape
        (a legacy shape marker reads too).
        """
        if cmds is None:
            return {}
        info = dict(cls._marker_info(obj))
        if not info.get("map"):
            return {}
        info.setdefault("scaleOffset", list(cls.IDENTITY_SCALE_OFFSET))
        transform = NodeUtils.get_transform_node(obj) or obj
        transform = (cmds.ls(transform, long=True) or [transform])[0]
        info["path"] = next(
            (d["path"] for d in cls._resolve([transform]) if transform in d["owners"]),
            None,
        )
        return info

    # ------------------------------------------------------------------
    # Commit / revert
    # ------------------------------------------------------------------

    @classmethod
    def commit(
        cls,
        mapping: Dict[str, str],
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        intensity: float = 1.0,
        written: bool = True,
        cells: Optional[Dict[str, Dict[str, Any]]] = None,
        lighting: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (fully non-destructive).

        Nothing about the object's material or UV order changes: the full PBR
        material and texture UV0 are kept, and the lightmap stays a separate
        HDR on UV channel index 1 (where engines bind it), composited ``albedo
        x lightmap`` by the engine. Per object it stamps the marker on the
        TRANSFORM and records the map's folder and writer (this scene) in the
        private records. Then the bake baseline steps forward
        (:meth:`bake_state`): each recorded object as baked now, against the
        scene as it stands (:meth:`scene_state`) -- what a later re-bake of
        the changed objects diffs against; a baseline that cannot be read or
        written is logged and never costs the commit. Last, the scene
        manifest is republished onto the shared ``data_export`` carrier so it
        rides the FBX, its lights the ones the baseline says lit the maps.
        Files are never touched: a re-bake sets aside the maps it superseded
        around its commit (:meth:`superseding`).

        Parameters:
            mapping: ``{object: lightmap path}``.
            scale_offsets: ``{object: [scaleX, scaleY, offsetX, offsetY]}`` --
                THE atlas binding, the per-instance rect the engine applies
                when sampling (Unity ``Renderer.lightmapScaleOffset``; glTF
                ``KHR_texture_transform``). Absent entries are the identity (a
                map of its own).
            intensity: Recorded in the marker, informationally: whatever
                multiplier the maps' texels already carry (see
                :meth:`LightmapBaker.bake`'s ``intensity``). Unity's native
                lightmaps have no multiplier of their own to set.
            written: Whether THIS scene wrote the maps (a bake). ``False``
                binds objects to maps that already exist -- a lightmap carried
                to another object -- and leaves the writer record alone:
                stamping this scene their writer would let a later re-bake set
                aside a file another scene still reads (:meth:`superseding`).
            cells: ``{object: {"cell": [sx, sy, ox, oy], "extent": [w, h],
                "size": px, "packing": "atlas" | "per_object"}}`` -- where in
                its map each object was written: its atlas cell, the extent
                the cell was sized for and the map's size. Only an object
                whose cell is recorded can be written back into it later
                (``LightmapBaker._in_place_plan``); any key may be absent.
            lighting: The lighting the bake rendered with, and the rule that
                chose it (``LightmapBaker._lighting_state``); ``None`` records
                none, and a later lighting change then stales nothing (and
                the manifest ships every light that contributes for them).

        Returns:
            ``{object: lightmap path}`` for each object recorded.
        """
        if cmds is None:
            cls.logger.error("maya.cmds not available; commit aborted.")
            return {}

        scale_offsets = scale_offsets or {}
        cells = cells or {}
        recorded: Dict[str, str] = {}
        baked: Dict[str, Dict[str, Any]] = {}
        folders: Dict[str, str] = {}
        for obj, path in mapping.items():
            shape = NodeUtils.get_shape(obj)
            if not shape:
                cls.logger.warning("No shape for %s; skipping.", obj)
                continue
            transform = NodeUtils.get_transform_node(obj) or obj
            transform = (cmds.ls(transform, long=True) or [transform])[0]
            uv_set = (
                UvDiagnostics.find_lightmap_uv_set(shape)
                or UvDiagnostics.LIGHTMAP_UV_SET
            )
            so = scale_offsets.get(obj) or cls.IDENTITY_SCALE_OFFSET
            # Where the map lives goes in the PRIVATE folder record, never on
            # the marker: the marker is a node attribute that rides every FBX,
            # and a folder there is build-setup data on the deliverable. The
            # PORTABLE spelling -- workspace-relative when inside the project,
            # the rule textures follow -- read back through the scene's own
            # project (:meth:`search_dirs`) when a GLB build needs the file.
            folders[cls._hint_key(path)] = cls._portable_dir(path)
            info = {
                "map": os.path.basename(path),
                "uv_set": uv_set,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            # A LEGACY marker's UV remap (see :meth:`migrate_legacy`) is still
            # in the UVs until something restores it, so a re-commit carries
            # its record forward: dropped, the remap would be invisible to
            # revert and migration alike.
            rect = cls._marker_info(obj).get("uvRect")
            if rect and [float(v) for v in rect] != list(cls.IDENTITY_SCALE_OFFSET):
                info["uvRect"] = [float(v) for v in rect]
            # The TRANSFORM is the marker home: it is per-instance, so every
            # copy of a shared shape can carry its own rect. A leftover legacy
            # shape marker is cleared so the publisher can't double-count.
            cls._write_marker(transform, info)
            if cmds.attributeQuery(cls.LIGHTMAP_INFO_ATTR, node=shape, exists=True):
                try:
                    cmds.deleteAttr(f"{shape}.{cls.LIGHTMAP_INFO_ATTR}")
                except RuntimeError:
                    pass
            recorded[obj] = path
            baked[transform] = dict(cells.get(obj) or {}, map=info["map"])

        if recorded:
            hints = cls._folder_hints()
            hints.update(folders)
            cls._save_folder_hints(hints)
            # ...and that THIS scene wrote them: what lets a later re-bake
            # delete them once superseded (:meth:`superseding`).
            if written:
                from mayatk.node_utils.data_nodes import DataNodes

                writers = cls._writers()
                writers.update(dict.fromkeys(folders, DataNodes.writer_stamp()))
                cls._save_writers(writers)
            # The baseline first: the manifest's lights are the ones it says
            # each map was baked under (:meth:`_record`).
            cls._advance_bake_state(baked, lighting)
            cls._publish()
        return recorded

    # ------------------------------------------------------------------
    # The bake baseline -- what each map was baked against
    # ------------------------------------------------------------------

    @classmethod
    def bake_state(cls) -> Dict[str, Any]:
        """This scene's bake baseline, decoded (``ptk.BakeBaseline.decode``).

        ``{"version", "serial", "objects", "lightings"}``: per object (its
        long DAG path, per instance) the state its map was baked against,
        when it last baked, its atlas cell, and the lighting it saw -- the
        private ``ptk.SceneRecords.LIGHTMAP_BAKE_STATE`` record on
        ``data_internal``, rolled forward by every :meth:`commit`. Never on
        a marker: a marker rides the FBX, and this is build state. An empty
        record (serial 0) when the scene has none or it cannot be read.
        """
        stored = None
        if cmds is not None:
            stored = ptk.SceneRecords.LIGHTMAP_BAKE_STATE.load(cls._data_nodes(), None)
        return ptk.BakeBaseline.decode(stored)

    @classmethod
    def scene_state(cls, objects: Optional[List[str]] = None) -> Dict[str, Any]:
        """The scene as a bake sees it, for ``ptk.BakeBaseline``: every static
        participant's state, role and marker.

        The participants are what a bake renders as the room
        (``LightmapBaker._out_of_render``): every object carrying a marker,
        the :class:`~mayatk.LightmapExcludeSet`'s meshes and the
        :class:`~mayatk.LightmapIsolateSet`'s -- each keyed by its long DAG
        path, per instance. Per object (:meth:`_object_state`): its world
        matrix and box, fingerprints of its object-space points and of its
        lightmap UV set (``ptk.BakeBaseline.signature``), its material
        assignment and primary material (the atlas group), and whether
        Arnold renders it. Its role: ``excluded`` (in the Exclude set, which
        wins), ``isolated``, else ``baked``.

        Parameters:
            objects: Limit the read to these transforms and what lies under
                them; ``None`` reads every participant -- what a commit and
                the stale rule need, since a change anywhere may shade an
                object.

        Returns:
            ``{id: {"state", "role", "marker"}}`` (see ``ptk.BakeBaseline``).
        """
        if cmds is None:
            return {}
        from mayatk.mat_utils.bake_sets import LightmapExcludeSet, LightmapIsolateSet

        Role = ptk.BakeBaseline.Role
        markers = dict(cls._marker_records())
        excluded, isolated = (
            set(cmds.ls(meshes, long=True) or []) if meshes else set()
            for meshes in (LightmapExcludeSet.meshes(), LightmapIsolateSet.meshes())
        )
        ids = list(markers) + sorted((excluded | isolated) - set(markers))
        if objects is not None:
            roots = cmds.ls([str(o) for o in objects], long=True) or []
            ids = [
                i for i in ids if any(i == r or i.startswith(r + "|") for r in roots)
            ]
        scene: Dict[str, Any] = {}
        for node in ids:
            state = cls._object_state(node)
            if state is None:
                continue
            info = markers.get(node)
            scene[node] = {
                "state": state,
                "role": (
                    Role.EXCLUDED
                    if node in excluded
                    else Role.ISOLATED
                    if node in isolated
                    else Role.BAKED
                ).value,
                "marker": {
                    "map": info.get("map"),
                    "scaleOffset": info.get(
                        "scaleOffset", list(cls.IDENTITY_SCALE_OFFSET)
                    ),
                }
                if info
                else None,
            }
        return scene

    @classmethod
    def _object_state(cls, obj: str) -> Optional[Dict[str, Any]]:
        """*obj*'s state for :meth:`scene_state`, or ``None`` when it has no mesh.

        Read off the INSTANCE's shape path (an instanced mesh is one node
        under many transforms) through ``MFnMesh`` arrays: the object-space
        points and face-vertex lists (``ptk.BakeBaseline.signature``), the
        world box of those points under the instance's world matrix, and the
        lightmap UV set's UVs and face-UV lists. Moments, not a point hash:
        a ``.ma`` save keeps ~7 digits, and the baseline compares with
        tolerances that ride it out.
        """
        import numpy as np
        import maya.api.OpenMaya as om

        from mayatk.display_utils._display_utils import DisplayUtils

        try:
            shape = NodeUtils.get_shape(obj)
            if not shape:
                return None
            path = f"{obj}|{shape.rsplit('|', 1)[-1]}"
            selection = om.MSelectionList()
            selection.add(path)
            mesh = om.MFnMesh(selection.getDagPath(0))
            points = np.array(
                [(p.x, p.y, p.z) for p in mesh.getPoints(om.MSpace.kObject)],
                dtype=np.float64,
            ).reshape(-1, 3)
            counts, indices = mesh.getVertices()
            matrix = [
                float(v)
                for v in cmds.xform(obj, query=True, matrix=True, worldSpace=True)
            ]
            world = points @ np.array(matrix).reshape(4, 4)[:3, :3] + matrix[12:15]
            uv_set = UvDiagnostics.find_lightmap_uv_set(shape)
            layout = None
            if uv_set:
                us, vs = mesh.getUVs(uv_set)
                uv_counts, uv_ids = mesh.getAssignedUVs(uv_set)
                layout = ptk.BakeBaseline.signature(
                    np.column_stack([np.array(us), np.array(vs)]),
                    np.array(uv_counts),
                    np.array(uv_ids),
                    dims=2,
                )
            assigns = MatUtils.get_shading_assignments(obj) or {}
            materials = json.dumps(
                sorted([str(sg), faces] for sg, faces in assigns.items()),
                separators=(",", ":"),
            )
            return {
                "matrix": matrix,
                "bbox": [world.min(axis=0).tolist(), world.max(axis=0).tolist()]
                if len(world)
                else None,
                "geometry": ptk.BakeBaseline.signature(
                    points, np.array(counts), np.array(indices)
                ),
                "layout": layout,
                "materials": hashlib.blake2b(
                    materials.encode("utf-8"), digest_size=16
                ).hexdigest(),
                "group": cls._primary_material(obj, assigns) or "",
                "rendered": bool(DisplayUtils.is_visible(path)),
            }
        except Exception as error:  # a participant unread is gone, never a raise
            cls.logger.debug("Lightmap baseline: %s not read (%s).", obj, error)
            return None

    @staticmethod
    def _primary_material(
        obj: str, assigns: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """The shading group covering the most faces of *obj* -- its atlas group.

        A whole-object (single-material) assignment wins outright; otherwise
        the per-face group with the most faces. ``None`` when nothing is
        assigned. *assigns* is ``MatUtils.get_shading_assignments(obj)`` when
        the caller already read it. One rule for the atlas plan
        (``LightmapBaker._primary_material``) and the baseline's ``group``.
        """
        if assigns is None:
            assigns = MatUtils.get_shading_assignments(obj)
        if not assigns:
            return None
        return max(
            assigns.items(),
            key=lambda kv: float("inf") if kv[1] is None else len(kv[1]),
        )[0]

    @classmethod
    def _missing_maps(cls) -> List[str]:
        """The marked transforms whose map is on disk nowhere (:meth:`_reads`)."""
        return [transform for transform, _name, path in cls._reads() if not path]

    @classmethod
    def _advance_bake_state(
        cls,
        baked: Dict[str, Dict[str, Any]],
        lighting: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Roll the bake baseline forward over *baked* (``ptk.BakeBaseline.advance``).

        *baked*: ``{transform: {"map", "cell", "extent", "size", "packing"}}``
        -- what :meth:`commit` just recorded. Logged, never raised: the
        markers are committed by now, and a baseline that cannot be written
        costs only the next re-bake's precision. Returns whether it was written.
        """
        try:
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            store = cls._data_nodes()
            record = ptk.BakeBaseline.advance(
                spec.load(store, None),
                cls.scene_state(),
                baked=baked,
                lighting=lighting,
            )
            spec.save(store, record)
        except Exception as error:
            cls.logger.warning(
                "Lightmap bake baseline not recorded (%s): the Manage page will "
                "list this bake's objects as untracked, their changes unseen.",
                error,
            )
            return False
        return True

    @classmethod
    def untracked(cls, objects: Optional[List[str]] = None) -> List[str]:
        """The marked objects the bake baseline has never seen, sorted.

        Baked before change tracking, or brought in from another file
        (``ptk.BakeBaseline.rematch``): their changes are not detected, so no
        dry run counts them. *objects* (transforms) narrows it; default: every
        marked one.
        """
        if cmds is None:
            return []
        untracked = ptk.BakeBaseline.rematch(cls.bake_state(), cls.scene_state())[2]
        if objects is None:
            return untracked
        keep = set(cmds.ls(objects, long=True) or [])
        return [obj for obj in untracked if obj in keep]

    @classmethod
    def track(
        cls,
        objects: Optional[List[str]] = None,
        lighting: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Start tracking the :meth:`untracked` among *objects* -- no render.

        Each is recorded as baked now, against the scene as it stands and
        *lighting*: what vouches that its map matches both, so a change from
        here on reads as changed. Its cell stays unrecorded, so a later
        re-bake re-packs its atlas whole rather than write into a cell it
        cannot place. Every other participant rolls forward as any commit
        rolls it (:meth:`_advance_bake_state`), and the manifest is
        republished. One undo chunk.

        Parameters:
            objects: Default: every marked object.
            lighting: As :meth:`commit`'s (``LightmapBaker._lighting_state``).

        Returns:
            The long names of the transforms now tracked.
        """
        untracked = cls.untracked(objects)
        if not untracked:
            return []
        with CoreUtils.undo_chunk("Track Lightmaps"):
            if not cls._advance_bake_state({obj: {} for obj in untracked}, lighting):
                return []
            cls._publish()
        return untracked

    @classmethod
    def _keep_bake_state(cls, before: Dict[str, Any], objects: List[str]) -> None:
        """Give *objects* back how they were baked, across a commit that only
        re-packed them (``LightmapBaker.combine``): ``baked_at``,
        ``lighting`` (and the lighting state it names), ``stale`` and
        ``untracked`` from *before* (:meth:`bake_state`, read before that
        commit). One *before* never saw stays untracked: a re-pack vouches
        for no map. Logged, never raised."""
        kept = ("baked_at", "lighting", "stale", "untracked")
        try:
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            store = cls._data_nodes()
            record = ptk.BakeBaseline.decode(spec.load(store, None))
            for obj in objects:
                entry = record["objects"].get(obj)
                if entry is None:
                    continue
                was = before["objects"].get(obj)
                if was is None:
                    entry.update(baked_at=None, lighting=None, untracked=True)
                    continue
                entry.update({key: was[key] for key in kept})
                state = before["lightings"].get(was["lighting"] or "")
                if state is not None:
                    record["lightings"][was["lighting"]] = state
            spec.save(store, ptk.BakeBaseline.encode(record))
        except Exception as error:
            cls.logger.warning("Lightmap bake baseline not kept (%s).", error)

    @classmethod
    def _inherit_bake_state(cls, pairs: Dict[str, Tuple[str, bool]]) -> None:
        """Give each carried target its source's bake (:meth:`transfer_lightmaps`).

        *pairs*: ``{target: (source, rebound)}``. A target's map was baked
        when, and under the lighting, its source's was; a REBOUND one also
        reads the very cell its source owns, so it takes the cell with it --
        a resampled one has a map of its own.
        """
        try:
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            store = cls._data_nodes()
            record = ptk.BakeBaseline.decode(spec.load(store, None))
            entries = record["objects"]
            for target, (source, rebound) in pairs.items():
                target = (cmds.ls(target, long=True) or [target])[0]
                source = (cmds.ls(source, long=True) or [source])[0]
                if target not in entries or source not in entries:
                    continue
                inherited = ["baked_at", "lighting"]
                if rebound:
                    inherited += ["cell", "extent", "size", "packing"]
                else:
                    entries[target].update(
                        cell=None, extent=None, size=None, packing="per_object"
                    )
                entries[target].update({k: entries[source][k] for k in inherited})
            spec.save(store, ptk.BakeBaseline.encode(record))
        except Exception as error:
            cls.logger.warning("Lightmap bake baseline not carried over (%s).", error)

    @classmethod
    def revert(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Undo :meth:`commit` -- drop the markers and republish.

        The material and texture UV0 were never changed, so this removes the
        markers (the objects leave the export manifest) and, for a LEGACY
        atlas commit, restores the lightmap UV set to its original unit-square
        layout (inverting the recorded ``uvRect``); the baked texture and the
        UV set itself are left in place (harmless, reused by the next bake).
        Markers are cleared from BOTH possible homes -- the transform (current)
        and the shape (legacy). With ``objects=None`` it clears **every**
        marked object. One undo chunk, so a single Ctrl+Z puts every marker
        and the manifest back. When no marker is left, the reflection probe's
        record and the bake baseline (:meth:`bake_state`) go too; a partial
        revert leaves the baseline, which then reads the objects it took out
        as gone from the room (an unbaked mesh moves), so their neighbours
        read as changed.

        Returns the long names of the nodes cleared.
        """
        if cmds is None:
            return []
        with CoreUtils.undo_chunk("Revert Lightmaps"):
            return cls._revert_markers(objects)

    @classmethod
    def _revert_markers(cls, objects: Optional[List[str]]) -> List[str]:
        """:meth:`revert`'s work, inside its undo chunk."""
        candidates = cls._marked_dag_nodes() if objects is None else list(objects)
        cleared: List[str] = []
        for obj in candidates:
            marker = cls._marker_node(obj)
            if not marker:
                continue
            info = cls._marker_info(obj)
            # Clear every home the marker occupies: the resolved node, plus a
            # stale twin on the other node (a legacy shape marker superseded by
            # a transform re-commit, or vice versa).
            transform = NodeUtils.get_transform_node(obj) or obj
            shape = NodeUtils.get_shape(obj)
            failed = False
            for node in dict.fromkeys(n for n in (marker, transform, shape) if n):
                if not cmds.attributeQuery(
                    cls.LIGHTMAP_INFO_ATTR, node=node, exists=True
                ):
                    continue
                try:
                    cmds.deleteAttr(f"{node}.{cls.LIGHTMAP_INFO_ATTR}")
                    if node not in cleared:
                        cleared.append(node)
                except RuntimeError as e:
                    cls.logger.warning(
                        "Could not clear lightmap marker on %s: %s", node, e
                    )
                    failed = True
            if failed:
                continue  # marker intact -> leave the UV remap recorded too
            if shape:
                cls._restore_lightmap_uvs(shape, info)
        if cleared and not cls._marked_dag_nodes():
            # The probe was the bake's: with no bake left, nothing reads it --
            # nor any baseline: a bake from here is a first bake.
            ptk.SceneRecords.LIGHTMAP_PROBE.clear(cls._data_nodes())
            ptk.SceneRecords.LIGHTMAP_BAKE_STATE.clear(cls._data_nodes())
        if cleared:
            cls._publish()
        return cleared

    # ------------------------------------------------------------------
    # The reflection probe -- the room the bake lit, as an HDR
    # ------------------------------------------------------------------

    @staticmethod
    def _data_nodes():
        """The scene store the records live in (``DataNodes``), imported late."""
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes

    @classmethod
    def probe(cls) -> Optional[Dict[str, Any]]:
        """This scene's reflection probe, or ``None``.

        ``{"map", "position", "box", "unit_scale"}``: the HDR's file name, the
        point it was captured from and the box its reflections project onto
        (``[[min], [max]]``, or ``None`` for an open scene), in scene units
        with ``unit_scale`` metres per unit beside them -- what
        :meth:`commit_probe` recorded, the unit read at the moment the
        position was. The manifest publishes it in the file's unit
        (:meth:`_in_working_unit`).
        """
        if cmds is None:
            return None
        data = ptk.SceneRecords.LIGHTMAP_PROBE.load(cls._data_nodes(), None)
        if not isinstance(data, dict) or not data.get("map"):
            return None
        return {k: data.get(k) for k in ("map", "position", "box", "unit_scale")}

    @staticmethod
    def _in_working_unit(probe: Dict[str, Any]) -> Dict[str, Any]:
        """*probe* (:meth:`probe`, in the unit its bake read it in) in the
        working unit, with that unit's ``unit_scale`` -- the unit an export
        writes the file in, as every record it ships."""
        from mayatk.core_utils.units import Units

        unit = Units.metres_per("working")
        factor = float(probe.get("unit_scale") or unit) / unit
        out = dict(probe, unit_scale=unit)
        if probe.get("position") is not None:
            out["position"] = [float(c) * factor for c in probe["position"]]
        if probe.get("box"):
            out["box"] = [
                [float(c) * factor for c in corner] for corner in probe["box"]
            ]
        return out

    @classmethod
    def commit_probe(
        cls,
        path: str,
        position: Sequence[float],
        box: Optional[Sequence[Sequence[float]]] = None,
    ) -> Dict[str, Any]:
        """Record the reflection probe the bake wrote to *path*, and republish.

        The probe is the room the bake lit, captured as an HDR from
        *position* (``LightmapBaker.bake_probe``): what the lightmaps cannot
        hold -- the reflections on every baked surface, which are a metal's
        whole look, and the light on everything unbaked. Its file's folder and
        writer go in the private records like any map's (:meth:`search_dirs`
        then finds it for a GLB build); the scene's previous probe file, when
        it is another file -- another name, or the same name in another
        folder -- is held for the next save to set aside when this scene
        wrote it inside its project: the rule :meth:`superseding` applies to
        maps.

        Parameters:
            path: The probe's EXR.
            position: Where it was captured, in scene units.
            box: ``[[min], [max]]`` its reflections project onto, in scene
                units; ``None`` for an open scene, read as distant.

        Returns:
            The record (see :meth:`probe`).
        """
        previous = cls.probe()
        name = os.path.basename(path)
        record = {
            "map": name,
            "position": [float(c) for c in position],
            "box": [[float(c) for c in corner] for corner in box] if box else None,
            "unit_scale": ptk.MathUtils.metres_per_unit(
                cmds.currentUnit(query=True, linear=True), 1.0
            ),
        }
        data_nodes = cls._data_nodes()
        hints = cls._folder_hints()
        writers = cls._writers()
        # Resolved BEFORE this probe's records replace the previous one's: a
        # re-bake into another folder writes the same NAME, and its record
        # then names the new folder alone -- the old file was left behind,
        # read by nothing.
        superseded = cls._own_probe_file(previous, hints, writers) if previous else None
        ptk.SceneRecords.LIGHTMAP_PROBE.save(data_nodes, record)
        key = cls._hint_key(name)
        hints[key] = cls._portable_dir(path)
        cls._save_folder_hints(hints)
        writers[key] = data_nodes.writer_stamp()
        cls._save_writers(writers)
        if superseded:
            # Kept when it IS the new file, however spelled (the new probe
            # reads it, or will once the save puts it home); held for the save
            # when it is another.
            retired = ptk.FileDependencies.superseded(
                [superseded], [("", name, cls._home(path))]
            )
            if retired:
                cls._pending().retire(retired)
                cls.logger.info(
                    "The superseded reflection probe %s goes to the Recycle Bin "
                    "when the scene is saved.",
                    retired[0],
                )
        cls._publish()
        return record

    @classmethod
    def _own_probe_file(
        cls,
        probe: Dict[str, Any],
        hints: Dict[str, str],
        writers: Dict[str, str],
    ) -> Optional[str]:
        """*probe*'s file, when it is this scene's to set aside once superseded.

        Written by this scene (``DataNodes.written_here``: never a Save As
        copy's source, which still reads it), recorded in its folder record,
        on disk, and inside its project (:meth:`project_root`) -- the rules
        :meth:`superseding` holds a map to. ``None`` otherwise.
        """
        name = os.path.basename(str(probe["map"]))
        key = cls._hint_key(name)
        folder = hints.get(key)
        root = cls.project_root()
        if not (folder and root and cls._data_nodes().written_here(writers.get(key))):
            return None
        path = os.path.join(cls._resolved_dir(folder, name), name)
        if os.path.isfile(path) and ptk.FileUtils.is_under(os.path.abspath(path), root):
            return path
        return None

    @classmethod
    def _probe_ref(cls) -> Optional[Tuple[str, str, str]]:
        """The probe as a file reference for :meth:`_resolve`: no owner (it is
        the scene's, no object's), its file name and its recorded folder."""
        probe = cls.probe()
        if not probe:
            return None
        name = os.path.basename(str(probe["map"]))
        return ("", name, cls._folder_hints().get(cls._hint_key(name), ""))

    @staticmethod
    def project_root() -> Optional[str]:
        """The project this scene's lightmaps belong to; ``None`` without one
        (``DataNodes.scene_project_root``: the scene file's own project -- a
        foreign scene opened as new: its source's -- the session's while
        unsaved). A bake writes no map outside it
        (``LightmapBaker.beside_textures``) and sets none aside there
        (:meth:`superseding`).
        """
        return DataNodes.scene_project_root()

    @classmethod
    @contextlib.contextmanager
    def superseding(cls, objects: List[str]) -> Iterator[List[str]]:
        """Around a re-bake's :meth:`commit`: set aside the maps *objects* stop reading.

        A re-bake that changes where or how its maps are written -- another
        output folder, Beside Material Textures, another name affix,
        per-object maps folded into an atlas or back -- gives its objects new
        files and leaves the old ones behind, read by nobody. They are not
        just clutter: a same-named leftover is what a reader joining names
        against folders finds (the production room once bound a 17-day-old
        atlas that way), and beside the textures one takes its own name, so
        the next bake that returns to it writes ``_1``. The maps *objects*
        read on entry that no marker in the scene reads on a clean exit
        (:meth:`ptk.FileDependencies.superseded`) are held for the scene's
        next save (:meth:`_pending`): until then the scene file on disk still
        reads them. The save sets aside the ones still unread then
        (:meth:`_commit_pending`) -- to the Recycle Bin, else the
        ``superseded`` temp store, never deleted, since no scene can see
        another file's reads -- so an undo before it, or no save at all,
        loses none. A home file the scene now reads from the session is not
        superseded: the save puts that copy over it. A block that raises
        holds nothing.

        Only this scene's own maps are candidates: recorded in its folder
        record AND written by it (``DataNodes.written_here``) -- never a map
        another scene file still reads, such as a Save As copy's source, nor
        one committed before writers were recorded -- never one a
        REFERENCED object reads, which its own file may name too, and never
        one outside the scene's project (:meth:`project_root`): a soldering
        assembly's re-bake sent another project's room lightmap, which an older
        bake of it had written beside that project's textures, to the Recycle Bin
        (2026-10-03). A map an excluded, failed or out-of-scope object still
        reads is read, and stays.

        Parameters:
            objects: The transforms the block commits new maps for.

        Yields:
            A list, filled on exit with the paths the next save sets aside.
        """
        retired: List[str] = []
        root = cls.project_root()
        # Each read as its home, the file a save sets aside: a map re-baked in
        # place and not saved yet is read from its session copy, which no
        # project holds -- and only a home that is on disk.
        homes = dict.fromkeys(cls._home(p) for p in cls._written_reads(objects))
        before = [
            p
            for p in homes
            if os.path.isfile(p)
            and root
            and ptk.FileUtils.is_under(os.path.abspath(p), root)
        ]
        yield retired
        if not before:
            return
        reads = [(t, n, cls._home(p) if p else p) for t, n, p in cls._reads()]
        retired.extend(ptk.FileDependencies.superseded(before, reads))
        if retired:
            cls._pending().retire(retired)
            cls.logger.info(
                "%d superseded lightmap(s) nothing reads any more go to the "
                "Recycle Bin when the scene is saved: %s",
                len(retired),
                ", ".join(os.path.basename(p) for p in retired),
            )

    # ------------------------------------------------------------------
    # Pending -- maps written since the last save wait in the session
    # ------------------------------------------------------------------

    @classmethod
    def _pending(cls) -> "ptk.PendingFiles":
        """The session's ledger of files written for the open scene
        (``ptk.PendingFiles``), with :meth:`_commit_pending` as this class's
        save hook on it and the save hook that runs it installed
        (``DataNodes.ensure_path_rebase``)."""
        pending = ptk.PendingFiles.session()
        pending.on_save("lightmaps", cls._commit_pending)
        cls._data_nodes().ensure_path_rebase()
        return pending

    @classmethod
    def _stage(cls, path: str) -> str:
        """Where a write of the map *path* goes until the scene is saved: its
        name, in the session folder for its folder (``ptk.PendingFiles.stage``).

        A bake that wrote over a map the saved scene reads and was then not
        saved left that scene reading texels laid out for markers it never
        got: an unsaved Combine wrote one atlas over the production room's
        two, and the next session opened with a map missing and three objects
        lit from the wrong cells (2026-10-23). Written here instead, the map is
        what :meth:`commit` records -- its folder record names the session
        folder, so every reader (:meth:`_reads`, :meth:`lightmap_dependencies`,
        an export) finds the new file -- and the save moves it home under the
        name it was given (:meth:`_commit_pending`). A path already in the
        session is its own.
        """
        return cls._pending().stage(path)

    @staticmethod
    def _home(path: str) -> str:
        """Where a session file -- or folder -- goes at the save (its home);
        any other path is its own. What a decision about folders reads: a
        map's place is its home, wherever its bytes wait."""
        return ptk.PendingFiles.session().home(path)

    @classmethod
    def _commit_pending(cls, target: str, fork: bool) -> None:
        """The save's half of :meth:`_stage` (``ptk.PendingFiles.on_save``),
        just before the scene is written to *target*.

        Each map the scene reads from the session -- a marker's, the probe's
        -- goes home, and its folder record names its home folder again,
        spelled from the project the save's re-spelling reads it from; one it
        no longer reads from there (an undone bake) is dropped. Then the maps
        a write superseded (:meth:`superseding`) and nothing reads now are set
        aside. Runs after the save's re-spelling (``DataNodes``), so every
        record is spelled from *target*'s project.

        A *fork* -- a Save As -- leaves every home file as the file it came
        from reads it: a map whose home name is taken on disk is moved under
        the next free one, its readers here re-stamped
        (:meth:`fork_lightmap`), every map moved is the new file's, and
        nothing is set aside.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        pending = ptk.PendingFiles.session()
        reads = [p for _t, _n, p in cls._reads() if p]
        probe = cls._probe_ref()
        if probe and probe[2]:
            reads.append(os.path.join(cls._resolved_dir(probe[2], probe[1]), probe[1]))
        taken: set = set()

        def rename(home: str) -> str:
            if not fork or not os.path.exists(home):
                return home
            folder = os.path.dirname(home)
            stem, ext = os.path.splitext(os.path.basename(home))
            return ptk.FileUtils.unique_path(
                folder, stem, ext, taken, claims=os.listdir(folder)
            )

        moved = pending.commit(reads, rename)
        base = DataNodes._rebase_state().get("base")
        # The scene file's own stamp, spelled from ITS project: ``base`` keeps
        # the old one when *target* is in none (blendertk's twin reads it too).
        stamp = DataNodes.writer_stamp_of(target) if fork else None
        # The forks first: each re-keys the records the loop below then reads once.
        for staged, home in moved.items():
            if os.path.basename(staged) != os.path.basename(home):
                cls.fork_lightmap(os.path.basename(staged), os.path.basename(home))
        if moved:
            hints, writers = cls._folder_hints(), cls._writers()
            for home in moved.values():
                key = cls._hint_key(os.path.basename(home))
                hints[key] = ptk.FileUtils.portable_path(
                    os.path.dirname(os.path.abspath(home)), base
                )
                if stamp is not None:
                    writers[key] = stamp
            cls._save_folder_hints(hints)
            if stamp is not None:
                cls._save_writers(writers)
            cls.logger.info(
                "Saved with the scene: %s",
                ", ".join(sorted({os.path.basename(h) for h in moved.values()})),
            )
        retiring = pending.take_retiring()
        if retiring and not fork:
            retired = ptk.FileDependencies.remove_superseded(retiring, cls._reads())
            if retired:
                cls.logger.info(
                    "Set aside %d superseded lightmap(s) nothing reads any more -- "
                    "in the Recycle Bin, or a temp folder kept 30 days: %s",
                    len(retired),
                    ", ".join(os.path.basename(p) for p in retired),
                )

    @classmethod
    def _written_reads(cls, objects: List[str]) -> List[str]:
        """The map files *objects* read now that are this scene's to set aside
        once superseded (:meth:`superseding`)."""
        if cmds is None or not objects:
            return []
        from mayatk.node_utils.data_nodes import DataNodes

        hints, writers = cls._folder_hints(), cls._writers()
        readers = cls.claims()
        referenced = cls._referenced_nodes(
            o for names in readers.values() for o in names if o != cls.FOREIGN
        )
        own: Dict[Optional[str], bool] = {}
        paths: List[str] = []
        for _transform, info in cls._marker_records(list(objects)):
            name = os.path.basename(str(info.get("map") or ""))
            key = cls._hint_key(name)
            writer = writers.get(key)
            if writer not in own:
                own[writer] = DataNodes.written_here(writer)
            if key not in hints or not own[writer]:
                continue
            if referenced.intersection(readers.get(key, ())):
                continue
            path = os.path.join(cls._resolved_dir(hints[key], name), name)
            if os.path.isfile(path):
                paths.append(path)
        return paths

    @classmethod
    def _reads(cls) -> List[Tuple[str, str, Optional[str]]]:
        """``(transform, map name, path)`` per marker: the file each reads NOW.

        Per marker, not per map name -- one name read from two folders is two
        files: the marker's own recorded folder (this scene's record, a
        referenced module's, a legacy marker's) when the map is there, else
        where the texture search folders find it (:meth:`_resolve`, without
        the walk), else ``None``.
        """
        if cmds is None:
            return []
        hints = cls._folder_hints()
        modules: Dict[str, Dict[str, str]] = {}
        found = {d["name"].lower(): d["path"] for d in cls._resolve(walk=False)}
        reads: List[Tuple[str, str, Optional[str]]] = []
        for transform, info in cls._marker_records():
            name = os.path.basename(str(info.get("map") or ""))
            folder = cls._folder_hint(info, hints, transform, modules)
            path = os.path.join(cls._resolved_dir(folder, name), name) if folder else ""
            if not os.path.isfile(path):
                path = found.get(name.lower())
            reads.append((transform, name, path))
        return reads

    @staticmethod
    def _referenced_nodes(nodes) -> Set[str]:
        """Those of *nodes* (long names) that come from a referenced file --
        one ``ls`` for them all, where a ``referenceQuery`` per node was the
        cost of every claims read."""
        names = sorted({str(n) for n in nodes if n})
        if not names:
            return set()
        return set(cmds.ls(names, referencedNodes=True, long=True) or [])

    # ------------------------------------------------------------------
    # Legacy markers
    # ------------------------------------------------------------------

    @classmethod
    def migrate_legacy(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Bring markers older than the rect-binding contract up to date, losslessly.

        Two formats predate it. A commit before per-instance markers stamped
        the SHAPE; one before rect binding packed an atlas by squeezing the
        object's lightmap UVs into its cell and recording the remap as
        ``uvRect``. The migration moves the marker to the transform, restores
        the UVs to their 0-1 layout, and folds the rect into the object's
        ``scaleOffset`` (:meth:`ptk.ImgUtils.compose_rect`) -- so the object
        samples exactly the texels it sampled before, in the scene and in every
        export.

        Restoring WITHOUT the fold is what a bake used to do on its way in: the
        marker kept pointing at the old atlas with an identity binding over
        unsqueezed UVs, i.e. at the WHOLE atlas instead of its cell. That was
        harmless only while every bake reverted its objects first; an object
        the bake then failed on would have shipped another object's lighting.

        A rect whose recorded UV set the mesh no longer has (deleted, or
        renamed) is dropped with nothing restored or folded: no remap is left
        to undo, and the identity binding samples whatever UVs the object has
        exactly as its map was baked. Kept, it rode the next commit onto the
        set that bake builds, for a later migration to invert over a fresh
        unwrap.

        A marker's legacy folder moves to the private record on the way
        (:meth:`migrate_folder_hints`).

        A bake runs this over its own objects before it plans or renders
        anything; *objects* ``None`` migrates the whole scene. One undo chunk,
        idempotent. Returns the transforms whose marker changed.
        """
        if cmds is None:
            return []
        lifted = cls.migrate_folder_hints(objects)
        legacy = [
            (transform, info, home)
            for transform, info in cls._marker_records(objects)
            for home in (cls._marker_node(transform),)
            if home != transform or "uvRect" in info
        ]
        changed: List[str] = []
        if not legacy:  # the common case: no undo chunk for nothing
            return lifted
        with CoreUtils.undo_chunk("Migrate Lightmap Markers"):
            for transform, info, home in legacy:
                rect = info.get("uvRect")
                remapped = bool(rect) and [float(v) for v in rect] != list(
                    cls.IDENTITY_SCALE_OFFSET
                )
                shape = NodeUtils.get_shape(transform)
                recorded = info.get("uv_set")
                if (
                    remapped
                    and shape
                    and recorded
                    and not cls._has_uv_set(shape, recorded)
                ):
                    cls.logger.info(
                        "%s: the lightmap UV set %r its legacy atlas rect was "
                        "squeezed into is gone; the rect is dropped.",
                        transform.rsplit("|", 1)[-1],
                        recorded,
                    )
                elif remapped:
                    if not shape or not cls._restore_lightmap_uvs(shape, info):
                        cls.logger.warning(
                            "%s: legacy lightmap UVs could not be restored; its "
                            "marker is left as it was.",
                            transform.rsplit("|", 1)[-1],
                        )
                        continue
                    info["scaleOffset"] = ptk.ImgUtils.compose_rect(
                        info.get("scaleOffset"), rect
                    )
                info.pop("uvRect", None)
                cls._write_marker(transform, info)
                if home and home != transform:
                    try:
                        cmds.deleteAttr(f"{home}.{cls.LIGHTMAP_INFO_ATTR}")
                    except RuntimeError:
                        pass
                changed.append(transform)
            if changed:
                cls.logger.info(
                    "Migrated %d legacy lightmap marker(s) to the rect binding.",
                    len(changed),
                )
                cls._publish()
        return lifted + [t for t in changed if t not in lifted]

    @classmethod
    def migrate_folder_hints(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Lift a legacy marker's ``dir`` into the private folder record.

        Until 2026-09-23 every marker carried its map's folder, and a marker is
        a node attribute that rides every FBX as a user property: build-setup
        data on the deliverable. The folder now lives in
        ``ptk.SceneRecords.LIGHTMAP_DIRS`` (:meth:`_folder_hints`); this moves
        an old marker's folder there -- a folder the record already holds for
        that map wins -- and strips it from the marker. A REFERENCED marker's
        folder lands in this scene's record and the strip is a reference edit,
        as any host-side marker edit is. Lossless and idempotent, one undo
        chunk. A bake runs it through :meth:`migrate_legacy`, and every export
        bracket stages it (``FbxUtils.STAGERS``), so a scene baked before the
        move ships clean without a re-bake.

        Returns:
            The transforms whose marker changed.
        """
        if cmds is None:
            return []
        legacy = [(t, i) for t, i in cls._marker_records(objects) if "dir" in i]
        if not legacy:
            return []
        lifted: List[str] = []
        with CoreUtils.undo_chunk("Lift Lightmap Folders"):
            hints = cls._folder_hints()
            for transform, info in legacy:
                folder = str(info.pop("dir") or "")
                if folder:
                    hints.setdefault(cls._hint_key(info.get("map")), folder)
                home = cls._marker_node(transform) or transform
                try:
                    cls._write_marker(home, info)
                except RuntimeError as e:  # a locked reference, a locked attr
                    cls.logger.warning(
                        "%s: its lightmap marker keeps its folder (%s); it rides "
                        "the export until the marker can be written.",
                        transform.rsplit("|", 1)[-1],
                        e,
                    )
                    continue
                lifted.append(transform)
            cls._save_folder_hints(hints)
        cls.logger.info(
            "Moved %d lightmap marker folder(s) into the scene's private record.",
            len(lifted),
        )
        return lifted

    @staticmethod
    def _transform_lightmap_uvs(
        shape: str, uv_set: str, rect: List[float], invert: bool = False
    ) -> None:
        """Affine-transform *shape*'s *uv_set* by a ``[sx, sy, ox, oy]`` rect.

        The LEGACY primitive: current packs never edit UVs (the rect is an
        engine binding), so its callers are the ``uvRect`` restores --
        :meth:`migrate_legacy` and :meth:`revert`. Forward (default) maps the
        unit square into the rect (``uv' = uv * s + o``); ``invert=True``
        applies the exact inverse, restoring the original layout. Operates via
        a current-set swap (``polyEditUV`` edits the current UV set only) and
        restores the previous current set.

        Raises:
            RuntimeError: *shape* has no *uv_set*. ``polyUVSet -currentUVSet``
                ignores a set the shape lacks, so ``polyEditUV`` would edit
                whichever set IS current -- measured: a legacy rect inverted
                over the live lightmap set, its bounds from 0..1 to -2..1.8.
        """
        if not LightmapRecords._has_uv_set(shape, uv_set):
            raise RuntimeError(f"{shape} has no UV set {uv_set!r}")
        sx, sy, ox, oy = (float(v) for v in rect)
        prev = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_set)
        try:
            uvs = f"{shape}.map[*]"
            if invert:
                cmds.polyEditUV(uvs, uValue=-ox, vValue=-oy, relative=True)
                cmds.polyEditUV(
                    uvs,
                    pivotU=0.0,
                    pivotV=0.0,
                    scaleU=1.0 / sx,
                    scaleV=1.0 / sy,
                    scale=True,
                )
            else:
                cmds.polyEditUV(
                    uvs, pivotU=0.0, pivotV=0.0, scaleU=sx, scaleV=sy, scale=True
                )
                cmds.polyEditUV(uvs, uValue=ox, vValue=oy, relative=True)
        finally:
            if prev and prev != uv_set:
                cmds.polyUVSet(shape, currentUVSet=True, uvSet=prev)

    @staticmethod
    def _has_uv_set(shape: str, uv_set: str) -> bool:
        """Whether *shape* has a UV set named *uv_set*."""
        return uv_set in (cmds.polyUVSet(shape, query=True, allUVSets=True) or [])

    @classmethod
    def _restore_lightmap_uvs(cls, shape: str, info: Dict[str, Any]) -> bool:
        """Undo a LEGACY pack-time UV remap recorded on a marker (``uvRect``).

        Returns True when a non-identity rect was present and inverted.
        """
        rect = (info or {}).get("uvRect")
        if not rect or [float(v) for v in rect] == list(cls.IDENTITY_SCALE_OFFSET):
            return False
        uv_set = info.get("uv_set") or UvDiagnostics.find_lightmap_uv_set(shape)
        if not uv_set:
            return False
        try:
            cls._transform_lightmap_uvs(shape, uv_set, rect, invert=True)
        except Exception as e:
            cls.logger.warning(
                "Could not restore atlased lightmap UVs on %s: %s", shape, e
            )
            return False
        return True

    # ------------------------------------------------------------------
    # The export manifest
    # ------------------------------------------------------------------

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``lightmaps`` record for this scene, or ``None`` when no
        lightmapped mesh remains -- the ``ptk.SceneRecords.LIGHTMAPS`` producer
        (``FbxUtils.PRODUCERS``). Pure: it reads the markers and never writes.

        Parameters:
            ctx: The export's decisions; only its ``scope`` is read. A scoped
                export names the export set's objects alone (the record is
                ``export_scoped``): a marker outside it -- a hidden group of
                stale copies -- would ship its map with nothing to bind it to.
        """
        return cls._record(ctx.scope)

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Rebuild the ``lightmaps`` export channel from the scene's markers.

        The authoring-time publish of :meth:`export_record`: the record is
        committed through ``FbxUtils.publish_authored`` (a scene with no
        markers CLEARS the channel). An export pipeline runs the producer
        itself (``FbxUtils.PRODUCERS``).

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        return cls._publish()

    @classmethod
    def _publish(cls) -> Optional[str]:
        """Publish :meth:`_record` onto the shared ``data_export`` carrier.

        Regenerating from the markers (not the last bake) keeps incremental
        bakes additive and a revert subtractive. Clears the channel when no
        lightmapped meshes remain; never creates the carrier just to write an
        empty manifest. Returns the published JSON string, or ``None``.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        cls._prune_folder_hints()
        record = cls._record()
        FbxUtils.publish_authored({ptk.SceneRecords.LIGHTMAPS: record})
        return record.text if record is not None else None

    #: The most lights a manifest carries for their highlights: each is drawn
    #: into every probe copy a GLB build makes.
    SPECULAR_LIGHT_LIMIT = 16

    @classmethod
    def _record(cls, objects: Optional[List[str]] = None) -> Optional[ptk.Record]:
        """(Re)build the lightmap manifest record from the scene's markers.

        One entry per marked TRANSFORM (one per instance, each with its own
        atlas ``scaleOffset``), legacy shape markers included, in the one
        marker enumeration's order (:meth:`_marker_records`, sorted by path);
        *objects* scopes it like that enumeration (``None`` = the scene).
        Its shape -- one ``unit_scale`` for every length, the version stamped
        -- is the record's model's (``ptk.LightmapRecord.payload``), which
        blendertk's producer publishes through too. unitytk's optional editor
        helper reads it to auto-bind Unity's native lightmap slots
        (``renderer.lightmapScaleOffset`` per entry). Returns ``None`` when no
        lightmapped mesh remains (the publisher then clears the channel).
        """
        entries: List[Dict[str, Any]] = []
        for transform, info in cls._marker_records(objects):
            shape = NodeUtils.get_shape(transform)
            # The engine matches by the GameObject (transform) name, so publish
            # exactly what the export carries: the DAG path goes (no format
            # names a node by one -- it rides beside, as ``hierarchy``) but the
            # NAMESPACE stays. Measured end to end -- Maya writes `NS:leaf` as
            # the FBX Model name and FBX2glTF preserves the colon into the glTF
            # node name -- so stripping it did two kinds of damage on a
            # referenced scene: it invented duplicates between modules that
            # merely share leaf names (PROPS_DA:prop352 vs PROPS_RF:prop352 are
            # distinct everywhere downstream), and it broke the join outright,
            # since Unity's FindRenderer compares against `PROPS_DA:prop352`
            # while the manifest offered `prop352`. blendertk needs no
            # equivalent: Blender enforces scene-unique object names, so its
            # published name is already the exported one.
            name = transform.rsplit("|", 1)[-1]
            # Publish the lightmap set's REAL channel index. Unity's native
            # lightmaps only ever sample uv2 (index 1) -- anything else means
            # the export will sample the wrong channel, so warn loudly instead
            # of shipping a hardcoded 1 that hides the problem.
            uv_set = info.get("uv_set")
            sets = (
                list(
                    dict.fromkeys(
                        cmds.polyUVSet(shape, query=True, allUVSets=True) or []
                    )
                )
                if shape
                else []
            )
            uv_index = sets.index(uv_set) if uv_set in sets else 1
            if shape and uv_set and uv_set not in sets:
                cls.logger.warning(
                    "%s: committed lightmap set %r no longer exists; "
                    "publishing uvIndex 1 on faith. Re-run create_lightmap_uvs "
                    "if the set was renamed or removed.",
                    name,
                    uv_set,
                )
            if uv_index != 1:
                cls.logger.warning(
                    "%s: lightmap set %r sits at UV index %d, but Unity samples "
                    "uv2 (index 1). Re-run create_lightmap_uvs (it reorders to "
                    "index 1) before exporting.",
                    name,
                    uv_set,
                    uv_index,
                )
            # ``hierarchy`` is where the object SITS, root first, namespaces
            # kept like ``name``. FBX carries leaf names only, so two objects
            # that share one arrive as two same-named nodes -- the production
            # room's two machine bodies are both ``BODY``, and its GLB
            # bound one machine's lightmap onto both. FBX and glTF both keep
            # the node tree, so a reader tells the two apart by this
            # (``ptk.MeshConvert.apply_glb_lightmaps``).
            entries.append(
                {
                    # camelCase keys: Unity's JsonUtility matches C# field names
                    # exactly, so these mirror LightmapRecord in unitytk's
                    # LightmapMetadataController.cs.
                    "name": name,
                    "map": info.get("map"),
                    "uvIndex": uv_index,
                    # The object's rect into its (possibly shared) lightmap: the
                    # identity for a map of its own, or an atlas rect. Old
                    # markers predate the key -> identity.
                    "scaleOffset": info.get(
                        "scaleOffset", list(cls.IDENTITY_SCALE_OFFSET)
                    ),
                    "hierarchy": [part for part in transform.split("|") if part],
                }
            )

        names = [e["name"] for e in entries]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            # Not a warning: every record carries its hierarchy, and both
            # readers -- the GLB applier and unitytk's LightmapMetadataController
            # (since 2026-09-22) -- tell same-named objects apart by it. Only
            # an older Unity helper, which matched by name alone, needs the
            # objects renamed; said once, at info, for that reader.
            cls.logger.info(
                "Object name(s) recur in the lightmap manifest (%s); readers tell "
                "them apart by hierarchy. A unitytk LightmapMetadataController "
                "older than 2026-09-22 matches by name alone -- update it "
                "rather than renaming.",
                ", ".join(dupes),
            )

        if not entries:
            # ``None`` = nothing to publish: the publisher clears an existing
            # channel without creating data_export just to hold an empty one.
            return None

        # Objects only, no folder: wherever the manifest becomes a GLB the maps
        # are EMBEDDED, and the host hands that build where they live
        # (:meth:`search_dirs`, from the markers' own portable folders) -- a
        # build-time hint that does not belong in a deliverable. Before
        # 0.17.0 this published the ABSOLUTE authoring folders (``dir`` /
        # ``dirs``). The bake's reflection probe rides beside them, by file
        # name like a map (``ptk.MeshConvert.apply_glb_lightmaps`` embeds it).
        from mayatk.core_utils.units import Units

        probe = cls.probe()
        # The highlights a lightmap cannot hold: the lights, which the GLB
        # build draws into copies of the probe (LightUtils.specular_lights;
        # ptk.MeshConvert.highlight_probe_layers), the strongest at the middle
        # of what is baked when there are many.
        baked = [transform for transform, _info in cls._marker_records(objects)]
        near = None
        if baked:
            box = cmds.exactWorldBoundingBox(baked)
            near = [(box[i] + box[i + 3]) / 2.0 for i in range(3)]
        # The probe and the lights both in the working unit -- the one the
        # export writes the file in (the Scene Exporter switches it to its
        # target first) -- so one unit_scale states them all.
        payload = ptk.LightmapRecord.payload(
            entries,
            probe=cls._in_working_unit(probe) if probe else None,
            # The lights in the record's form: a light's name is the scene's
            # bookkeeping (``LightUtils.specular_lights``), which no reader of
            # the record uses.
            lights=[
                {k: v for k, v in light.items() if k != "name"}
                for light in cls._baked_lights(baked, near)
            ],
            unit_scale=Units.metres_per("working"),
        )
        return ptk.SceneRecords.LIGHTMAPS.make(payload) if payload else None

    @classmethod
    def _baked_lights(
        cls, baked: List[str], near: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """The manifest's ``lights``: the lights the bake of *baked* used.

        Each map's own, as the bake baseline recorded them
        (``ptk.BakeBaseline.lights_used``): a light Include Scene Lighting
        left out ships no highlight, and a hidden one Include Hidden Lights
        switched on for the bake -- hidden again by now -- ships one, read as
        lit. A map whose lights the baseline does not hold (baked before it
        did, or brought in) leaves it to every light that contributes now,
        as before. In ``LightUtils.specular_lights``' form, at most
        :attr:`SPECULAR_LIGHT_LIMIT` -- the strongest at *near*.
        """
        from mayatk.light_utils._light_utils import LightUtils

        used = ptk.BakeBaseline.lights_used(cls.bake_state(), baked)
        if used is None:
            return LightUtils.specular_lights(limit=cls.SPECULAR_LIGHT_LIMIT, near=near)
        ids = set(used)
        shapes = [
            shape
            for shape in LightUtils.all_lights()
            if (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[0]
            in ids
        ]
        # Every one lit the bake, whatever it shows now.
        return LightUtils.specular_lights(
            shapes, limit=cls.SPECULAR_LIGHT_LIMIT, near=near, lit=shapes
        )

    # ------------------------------------------------------------------
    # Dependencies -- the maps the markers name, on disk NOW
    # ------------------------------------------------------------------
    #
    # A committed lightmap is a texture dependency of the scene that lives
    # outside every file node: the marker records a basename plus the folder
    # the bake was COMMITTED from, and that folder is history, not a contract
    # (a reorganised project, a scene migrated to another module, another
    # machine). The texture tools -- the Texture Path Editor, the exporter's
    # path check, the GLB converter, the WebXR preview -- each answered "where
    # are my maps?" for file nodes only, so a migration that copied every
    # texture left the EXRs behind and the deliverable shipped unlit with one
    # log line nobody read. These methods are the lightmap-side answer those
    # tools consume, over pythontk's generic ``FileDependencies``.

    #: How a dependency was located. ``"hint"`` -- the marker's own recorded
    #: folder still holds the map; ``"search"`` -- found elsewhere (the
    #: workspace's texture folders, then a recursive walk of sourceimages),
    #: so the hint is stale and worth healing; ``None`` -- on disk nowhere.
    FOUND_BY_HINT: str = ptk.FileDependencies.FOUND_BY_HINT
    FOUND_BY_SEARCH: str = ptk.FileDependencies.FOUND_BY_SEARCH

    @classmethod
    def claims(cls, objects: Optional[List[str]] = None) -> Dict[str, FrozenSet[str]]:
        """``{file name: transforms}`` -- which objects read which map, by lower-case name.

        What a bake hands :meth:`ptk.FileUtils.unique_path` (``claims=``) with
        the objects it is writing as ``owners``. A map only the bake's own
        objects read is theirs to replace, so re-baking an object keeps its
        file name. A map anyone else reads is left alone: an object outside
        the bake, or one inside it the bake then fails on, goes on reading its
        own lighting instead of whatever landed on its file. Names, not paths,
        because a reader finds a map by joining its name onto a list of folders
        (:meth:`search_dirs`), so one name in two folders is a hazard of its own.

        A map another scene file wrote (:meth:`foreign_writer`) is claimed by
        :attr:`FOREIGN` too, whoever reads it here -- or whether anything here
        reads it at all: a Save As copy carries its source's markers, which
        name the source's maps as the copy's objects', and a re-bake of the
        copy replaced the file its source still reads. Once the copy baked
        under other names its markers named none of them, and a later bake
        back under the source's name wrote over it all the same. So is a map
        a referenced object reads that this scene did not write: its
        module's, which every scene referencing the module reads.
        """
        claims = dict(
            ptk.FileDependencies.claims(
                (transform, info.get("map"))
                for transform, info in cls._marker_records(objects)
            )
        )
        writers = cls._writers()  # read once: one record load, not one per map
        for key in set(claims) | set(writers):
            if cls.foreign_writer(key, writers):
                claims[key] = claims.get(key, frozenset()) | {cls.FOREIGN}
        # A map a referenced object reads is its module's -- every scene that
        # references the module reads it -- unless this scene wrote it: a
        # host's bake of the module's objects replaced the module's atlas.
        # One query for every reader, not a referenceQuery each.
        referenced = cls._referenced_nodes(
            o for readers in claims.values() for o in readers if o != cls.FOREIGN
        )
        for key, readers in claims.items():
            if cls.FOREIGN in readers or not referenced & readers:
                continue
            if not cls.wrote(key, writers):
                claims[key] = readers | {cls.FOREIGN}
        return claims

    #: The reader :meth:`claims` gives a map another scene file wrote: never
    #: one of a bake's owners, so the map is never theirs to replace.
    FOREIGN = "<another scene file>"

    @classmethod
    def wrote(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> bool:
        """Whether this scene wrote *map_name*: its writer record says so
        (``DataNodes.written_here``). A map with no entry is not known to be
        its own. *writers*: the record, when the caller has read it already."""
        writers = cls._writers() if writers is None else writers
        writer = writers.get(cls._hint_key(map_name))
        return writer is not None and cls._data_nodes().written_here(writer)

    @classmethod
    def strangers(
        cls,
        folder: str,
        stem: str,
        ext: str = ".exr",
        claims: Optional[Dict[str, FrozenSet[str]]] = None,
    ) -> Set[str]:
        """The files in *folder* a bake naming ``<stem>`` must not replace:
        ``<stem><ext>`` / ``<stem>_<k><ext>`` on disk that nothing here reads
        (*claims*, :meth:`claims` when ``None``) and this scene did not write
        (its writer record) -- another scene's.

        :meth:`claims` knows only this scene's readers: two scenes in one
        project whose materials share a texture set name the same atlas, and
        the second's bake replaced the first's (2026-10-21). A file this
        scene wrote and no longer reads (a revert) stays its own to replace.
        Absolute paths; empty when *folder* does not exist.
        """
        try:
            names = os.listdir(folder)
        except OSError:
            return set()
        claims = cls.claims() if claims is None else claims
        writers = cls._writers()
        pattern = re.compile(
            rf"{re.escape(stem)}(_\d+)?{re.escape(ext)}", re.IGNORECASE
        )
        out: Set[str] = set()
        for name in names:
            key = name.lower()
            if not pattern.fullmatch(name) or key in claims:
                continue
            if cls.wrote(key, writers):
                continue
            out.add(os.path.abspath(os.path.join(folder, name)))
        return out

    @classmethod
    def foreign_writer(
        cls, map_name: str, writers: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """The other scene file that wrote *map_name* (its writer record's
        entry), or ``None``: this scene wrote it, the scene that did is gone,
        or nothing recorded who did (a legacy map -- left as its markers say).
        *writers*: the record, when the caller has read it already."""
        writers = cls._writers() if writers is None else writers
        writer = writers.get(cls._hint_key(map_name))
        if not writer or cls._data_nodes().written_here(writer):
            return None
        return writer

    @classmethod
    def _resolve(
        cls,
        objects: Optional[List[str]] = None,
        search_dirs: Optional[List[str]] = None,
        walk: bool = True,
        maps: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The markers' maps through :meth:`ptk.FileDependencies.resolve`, Maya's way.

        *maps* narrows the read to those file names (any case), the probe's
        included -- a scope by FILE, where *objects* scopes by reader.
        """
        if cmds is None:
            return []
        hints: Dict[str, str] = cls._folder_hints()
        modules: Dict[str, Dict[str, str]] = {}
        refs = [
            (
                transform,
                str(info.get("map") or ""),
                cls._folder_hint(info, hints, transform, modules),
            )
            for transform, info in cls._marker_records(objects)
        ]
        # The probe is the scene's, no object's: a whole-scene read lists it,
        # and so does any read holding a baked object -- the manifest carries
        # the probe beside every set of entries, so what ships it must find it.
        probe = cls._probe_ref() if objects is None or refs else None
        if probe:
            refs.append(probe)
        if maps is not None:
            named = {os.path.basename(str(m)).lower() for m in maps}
            refs = [ref for ref in refs if ref[1].lower() in named]
        if not refs:
            return []
        deps = ptk.FileDependencies.resolve(
            refs,
            search_dirs=cls._texture_search_dirs()
            if search_dirs is None
            else search_dirs,
            walk_root=cls._walk_root() if walk else "",
            find_files=cls._find_files,
            resolve_hint=cls._resolved_dir,
        )
        for dep in deps:
            dep["owners"] = [o for o in dep["owners"] if o]
        return deps

    @staticmethod
    def _as_lightmap(dep: Dict[str, Any]) -> Dict[str, Any]:
        """A :class:`ptk.FileDependencies` record in the lightmap spelling
        (``map`` / ``objects``) the texture tools read."""
        return {
            "map": dep["name"],
            "dir": dep["dir"],
            "objects": dep["owners"],
            "path": dep["path"],
            "found_by": dep["found_by"],
            "note": dep["note"],
        }

    @classmethod
    def lightmap_dependencies(
        cls,
        objects: Optional[List[str]] = None,
        search_dirs: Optional[List[str]] = None,
        walk: bool = True,
    ) -> List[Dict[str, Any]]:
        """Every lightmap the scene's markers name, resolved on disk NOW.

        One record per unique map::

            {"map": basename, "dir": recorded folder, "objects": [transforms],
             "path": absolute path or None, "found_by": "hint" | "search" | None,
             "note": "" | why an unresolved map stayed unresolved}

        Resolution order is the GLB applier's (``ptk.MeshConvert.apply_glb_lightmaps``)
        so the two can never disagree about a map: the map's recorded folder
        (:meth:`_folder_hint`), then *search_dirs* (default :meth:`EnvUtils.texture_search_dirs`
        -- the workspace's texture folder and the scene's own folder), each a
        plain join. With *walk* a map still missing is looked for under the
        whole sourceimages tree; a UNIQUE hit resolves it (``found_by`` =
        ``"search"``), several same-named files leave it unresolved with the
        count in ``note`` rather than guessed at -- the exporter's own rule for
        rebinding a texture by name (:meth:`ptk.FileDependencies.resolve`).

        Parameters:
            objects: Transforms (roots) to scope to, descendants included;
                ``None`` for every marker in the scene. The scene's reflection
                probe comes along whenever the scope holds a baked object,
                as it does in the manifest.
            search_dirs: Folders to join the basename against after the hint.
            walk: Whether to fall back to the recursive sourceimages walk.
        """
        return [cls._as_lightmap(d) for d in cls._resolve(objects, search_dirs, walk)]

    @classmethod
    def search_dirs(cls, objects: Optional[List[str]] = None) -> List[str]:
        """Where this scene's lightmaps can be found NOW, for a consumer that joins.

        The folders the bake markers' maps resolve to FIRST -- most-named
        first (by the objects baked into each), ties broken on the path --
        then :meth:`EnvUtils.texture_search_dirs` (the order rule is
        :meth:`ptk.FileDependencies.search_dirs`). A consumer that can only
        join a basename against a list (the GLB applier's ``search_dirs``, the
        preview's ``lightmap_search_dirs`` hook) takes the first folder
        holding a file of the right name, so the order is a priority: the
        texture folders routinely hold a same-named atlas from an earlier bake,
        and reaching them first bound a 17-day-old map on the production room.
        The deliverable names no folder of its own (the GLB embeds the maps),
        so this is the one answer to where they are.
        """
        texture_dirs = cls._texture_search_dirs()
        return ptk.FileDependencies.search_dirs(
            cls._resolve(objects, search_dirs=texture_dirs), then=texture_dirs
        )

    @classmethod
    def heal_lightmap_paths(cls, objects: Optional[List[str]] = None) -> Dict[str, Any]:
        """Rewrite stale marker hints to where the maps actually are; republish.

        The lightmap half of the exporter's *Auto-Resolve Paths* task: a map
        found by search has a hint that resolves nowhere, so the scene's own
        answer to where its maps live (:meth:`search_dirs`, which every GLB
        build is handed) rests on a guess. The map's recorded folder becomes
        the one it was found in, in the portable spelling. Files are never
        touched.

        Returns:
            ``{"healed": [(map, old_dir, new_dir)], "missing": [records]}``.
        """
        deps = cls.lightmap_dependencies(objects)
        moves: Dict[str, str] = {}
        healed: List[Tuple[str, str, str]] = []
        for dep in deps:
            if dep["path"] and dep["found_by"] == cls.FOUND_BY_SEARCH:
                new_dir = os.path.dirname(dep["path"])
                moves[dep["map"].lower()] = new_dir
                healed.append((dep["map"], dep["dir"], new_dir))
        if moves:
            cls.repath_lightmaps(moves, objects)
        return {"healed": healed, "missing": [d for d in deps if not d["path"]]}

    @classmethod
    def normalize_lightmap_paths(
        cls, objects: Optional[List[str]] = None, relative: bool = True
    ) -> int:
        """Rewrite every in-scope marker's folder to its portable (or absolute) spelling.

        The lightmap half of the Texture Path Editor's *Normalize Paths* /
        *Make Paths Absolute*: files are never touched, the folder is
        re-spelled relative to the workspace when it lies inside the project
        (``relative=True``) or expanded to absolute (``relative=False``), and
        the manifest is republished. Returns how many markers changed.
        """
        dirs_by_map: Dict[str, str] = {}
        hints: Dict[str, str] = cls._folder_hints()
        modules: Dict[str, Dict[str, str]] = {}
        for transform, info in cls._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            folder = cls._resolved_dir(
                cls._folder_hint(info, hints, transform, modules), basename
            )
            if folder:
                dirs_by_map[basename.lower()] = folder
        if not dirs_by_map:
            return 0
        return cls.repath_lightmaps(dirs_by_map, objects, relative=relative)

    @classmethod
    def relocate_lightmaps(
        cls,
        dest_dir: str,
        source_dir: str = "",
        mode: str = "copy",
        objects: Optional[List[str]] = None,
        dry_run: bool = False,
        maps: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Gather the scene's lightmaps into *dest_dir* and repoint the markers.

        The lightmap half of the Texture Path Editor's *Find & Copy*: a map
        that resolves is its own source; one that does not is searched for
        under *source_dir* (recursively; the newest same-named file wins --
        the panel's rule for textures). A source already sitting in
        *dest_dir* needs no file operation and still gets its hint rewritten.
        *mode* is ``"copy"`` or ``"move"``, through the panel's own copier.
        With *dry_run* nothing is created, copied or written -- the plan comes
        back as it would run (:meth:`ptk.FileDependencies.relocate`). *maps*
        narrows it to those file names -- the reflection probe's too, which
        no object reads -- where *objects* narrows it to their readers.

        Returns::

            {"relocate": [(src, dst)], "in_place": [src], "missing": [records],
             "copied": [(src, dst)], "updated": markers rewritten}
        """
        plan = ptk.FileDependencies.relocate(
            cls._resolve(objects, maps=maps),
            dest_dir,
            source_dir=source_dir,
            mode=mode,
            dry_run=dry_run,
            find_files=cls._find_files,
            copy=cls._copy_files,
        )
        result: Dict[str, Any] = dict(
            plan, missing=[cls._as_lightmap(d) for d in plan["missing"]], updated=0
        )
        if dry_run:
            return result
        landed = {os.path.basename(dst).lower() for _src, dst in plan["copied"]}
        landed.update(os.path.basename(p).lower() for p in plan["in_place"])
        if landed:
            folder = dest_dir.replace("\\", "/")
            result["updated"] = cls.repath_lightmaps(
                {key: folder for key in landed}, objects
            )
        return result

    @staticmethod
    def _portable_dir(path: str) -> str:
        """The folder of *path* in the spelling the folder record STORES
        (``ptk.FileUtils.portable_path``): relative to the scene's own project
        (``DataNodes.project_root``) wherever a relative spelling reaches --
        ``sourceimages/lightmaps`` inside it, a ``../`` chain to a shared
        library beside it -- absolute on another drive. So a teammate's copy
        of the project resolves it, and the scene carries no machine's drive
        layout; a Save As into another project re-spells it.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        return ptk.FileUtils.portable_path(
            os.path.dirname(os.path.abspath(path)), DataNodes.project_root()
        )

    @classmethod
    def _resolved_dir(cls, folder: str, basename: str) -> str:
        """*folder* (a stored spelling) as an absolute folder on THIS machine:
        from the scene's own project (``ptk.FileUtils.resolve_portable_path``).
        A spelling written before that rule was relative to the SESSION's
        project and is read as a texture path is (:meth:`MatUtils.to_absolute`:
        the project root, then the sourceImages rule) when the first reading
        names no folder. ``""`` when nothing is recorded."""
        from mayatk.node_utils.data_nodes import DataNodes

        if not folder:
            return ""
        resolved = ptk.FileUtils.resolve_portable_path(folder, DataNodes.project_root())
        if os.path.isdir(resolved):
            return resolved
        return os.path.dirname(
            MatUtils.to_absolute(os.path.join(folder, basename or "_"))
        ).replace("\\", "/")

    @classmethod
    def _texture_search_dirs(cls) -> List[str]:
        """The workspace's texture folder and the scene's own folder."""
        return list(EnvUtils.texture_search_dirs())

    @classmethod
    def _walk_root(cls) -> str:
        """The project's sourceimages: where a map found nowhere else is walked for."""
        return EnvUtils.get_env_info("sourceimages") or ""

    @staticmethod
    def _find_files(names, root: str) -> List[str]:
        """The texture walk every Maya texture tool shares (its skip list, tile tokens)."""
        return MatUtils.find_texture_files(
            filenames=list(names), source_dir=root, recursive=True, quiet=True
        )

    @classmethod
    def _copy_files(
        cls, sources: List[str], dest_dir: str, mode: str
    ) -> List[Tuple[str, str]]:
        """Copy or move *sources* the way the Texture Path Editor moves textures.

        A move copies a map another scene reads (:attr:`FOREIGN` in
        :meth:`claims`: a Save As source's, a referenced module's) instead:
        only this scene's records follow a file, and that scene opened unlit.
        """
        shared: List[str] = []
        if mode == "move":
            claims = cls.claims()
            shared = [
                s
                for s in sources
                if cls.FOREIGN in claims.get(os.path.basename(s).lower(), ())
            ]
            if shared:
                cls.logger.info(
                    "Copied, not moved -- another scene reads them: %s",
                    ", ".join(os.path.basename(s) for s in shared),
                )
        out: List[Tuple[str, str]] = []
        for group, delete in (
            ([s for s in sources if s not in shared], mode == "move"),
            (shared, False),
        ):
            if group:
                out.extend(
                    MatUtils.move_texture_files(
                        found_files=group, new_dir=dest_dir, delete_old=delete
                    )
                )
        return out

    @classmethod
    def repath_lightmaps(
        cls,
        dirs_by_map: Dict[str, str],
        objects: Optional[List[str]] = None,
        relative: bool = True,
    ) -> int:
        """Point every in-scope marker naming a map in *dirs_by_map* at its new folder.

        Keys are lower-case basenames. The manual repath (the Texture Path
        Editor's Browse for File / typed path on a lightmap row) and the last
        step of :meth:`heal_lightmap_paths` and :meth:`relocate_lightmaps`.
        Files are never touched. The folder is stored -- in this scene's
        private folder record, per map (:meth:`_folder_hints`) -- in its
        portable spelling (workspace-relative when inside the project) unless
        ``relative=False`` -- the Make Paths Absolute case; a LEGACY marker's
        own ``dir`` naming the map is lifted off it on the way. One undo chunk.
        Returns how many markers now resolve to a different folder; one
        already there is untouched.
        """
        count = 0
        with CoreUtils.undo_chunk("Repath Lightmaps"):
            hints = cls._folder_hints()
            before = dict(hints)
            modules: Dict[str, Dict[str, str]] = {}
            for transform, info in cls._marker_records(objects):
                basename = os.path.basename(str(info.get("map") or ""))
                new_dir = dirs_by_map.get(basename.lower())
                if new_dir is None:
                    continue
                if relative:
                    spelling = cls._portable_dir(os.path.join(new_dir, basename))
                else:
                    spelling = os.path.abspath(new_dir).replace("\\", "/")
                current = cls._folder_hint(info, hints, transform, modules)
                if current.replace("\\", "/") != spelling:
                    count += 1
                hints[cls._hint_key(basename)] = spelling
                if "dir" in info:
                    info.pop("dir")
                    cls._write_marker(cls._marker_node(transform) or transform, info)
            # The probe has no marker and no objects: it follows when its file
            # is named, whatever the scope.
            probe = cls.probe()
            name = os.path.basename(str(probe["map"])) if probe else ""
            new_dir = dirs_by_map.get(name.lower()) if name else None
            if new_dir is not None:
                spelling = (
                    cls._portable_dir(os.path.join(new_dir, name))
                    if relative
                    else os.path.abspath(new_dir).replace("\\", "/")
                )
                if hints.get(cls._hint_key(name), "").replace("\\", "/") != spelling:
                    count += 1
                hints[cls._hint_key(name)] = spelling
            if hints != before:
                cls._save_folder_hints(hints)
            if count:
                cls._publish()
        return count

    @classmethod
    def rename_lightmap(cls, old_name: str, new_name: str) -> int:
        """Re-stamp every record naming map *old_name* with *new_name*.

        A lightmap is bound by its file NAME: the markers (``info["map"]``),
        this scene's folder and writer records (keyed by the lower-case name),
        the bake baseline's entries (:meth:`bake_state`), the reflection
        probe's record and the manifest the deliverable ships all name it, so
        a file renamed on disk read as a missing lightmap -- and a baseline
        left naming the old file refused the next re-bake in place ("recorded
        against another map").
        The rename follows the file -- which the caller renames; files are
        never touched here. Matched case-insensitively, scene-wide (a map is
        one file whoever reads it). One undo chunk.

        Returns:
            How many records now name *new_name*: the markers, plus one for
            the probe.
        """
        old_key = cls._hint_key(old_name)
        new_name = os.path.basename(str(new_name or ""))
        if not old_key or not new_name or os.path.basename(old_name) == new_name:
            return 0
        count = 0
        with CoreUtils.undo_chunk("Rename Lightmap"):
            for transform, info in cls._marker_records():
                if cls._hint_key(info.get("map")) != old_key:
                    continue
                info["map"] = new_name
                cls._write_marker(cls._marker_node(transform) or transform, info)
                count += 1
            probe = ptk.SceneRecords.LIGHTMAP_PROBE.load(cls._data_nodes(), None)
            if isinstance(probe, dict) and cls._hint_key(probe.get("map")) == old_key:
                probe["map"] = new_name
                ptk.SceneRecords.LIGHTMAP_PROBE.save(cls._data_nodes(), probe)
                count += 1
            new_key = cls._hint_key(new_name)
            for load, save in (
                (cls._folder_hints, cls._save_folder_hints),
                (cls._writers, cls._save_writers),
            ):
                record = load()
                if old_key in record:
                    record[new_key] = record.pop(old_key)
                    save(record)
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            state = cls.bake_state()
            entries = [e for e in state["objects"].values() if e["map"] == old_key]
            for entry in entries:
                entry["map"] = new_key
            if entries:
                spec.save(cls._data_nodes(), ptk.BakeBaseline.encode(state))
            if count:
                cls._publish()
        return count

    @classmethod
    def fork_lightmap(cls, old_name: str, new_name: str) -> int:
        """Make *new_name* -- this scene's copy of a map another scene file
        wrote (:meth:`foreign_writer`) -- the map its readers here read.

        :meth:`rename_lightmap`'s re-stamp, except that *old_name* keeps its
        writer entry: its source still reads it, and :meth:`claims` keeps this
        scene off it. *new_name* is this scene's (``DataNodes.writer_stamp``).
        How a Save As copy's first re-bake writes into its source's atlas
        without touching it (``LightmapBaker._bake_atlas``).

        Returns:
            :meth:`rename_lightmap`'s count.
        """
        old_key, new_key = cls._hint_key(old_name), cls._hint_key(new_name)
        writer = cls._writers().get(old_key)
        count = cls.rename_lightmap(old_name, new_name)
        writers = cls._writers()
        if writer:
            writers[old_key] = writer
        writers[new_key] = cls._data_nodes().writer_stamp()
        cls._save_writers(writers)
        return count

    # --------------------------------------------------------- transfer
    @classmethod
    def transfer_lightmaps(
        cls,
        targets,
        source,
        *,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        size: Optional[int] = None,
        supersample: int = 2,
        padding: int = -1,
    ) -> Dict[str, Dict[str, str]]:
        """Carry each source mesh's committed lightmap onto its paired target.

        A lightmap is not a material map, so :meth:`TextureTransfer.transfer`
        never sees it: it is a separate HDR the engine composites over the
        material, laid out in the object's own lightmap UV set and bound per
        object by its ``lightmapInfo`` marker (:meth:`commit`). It travels pair
        by pair, paired like :meth:`TextureTransfer.transfer` (matching leaf
        name, else order; identical topology required). It lives here, not on
        the transfer: the pass needs these records, and ``light_utils`` ranks
        above ``uv_utils``:

        * **Rebound** -- the target's lightmap layout IS the source's: the
          target is bound to the same map and atlas rect. Nothing is resampled
          or written, and a shared atlas stays shared.
        * **Resampled** -- the layouts differ: the source's texels, read
          through its rect, are re-mapped into the target's layout and
          written as a half-float EXR, ``<output_name>_Lightmap.exr`` (several
          targets append each one's name; without *output_name*, the
          target's name) -- :meth:`pythontk.UvTransfer.resample_lightmaps`,
          the half both hosts share.

        Either way the target is committed (:meth:`commit`)
        with the source's intensity, so the export manifest carries it, and
        takes its source's place in the bake baseline (:meth:`bake_state`):
        baked when and under the lighting its source was -- in its source's
        cell, when rebound. A
        target with no lightmap UV set is GIVEN the source's: the pair shares
        topology, so that layout fits it loop for loop
        (:meth:`UvUtils.export_uv_layout` -> :meth:`UvUtils.apply_uv_layout`)
        and the lightmap is then rebound -- where a fresh projection
        (``create_lightmap_uvs``) would force a lossy resample into a layout
        the source was never lit through. Its other UV sets are never written.
        A source with no committed lightmap is skipped quietly; one whose map
        or lightmap UV set is gone, with a warning.

        Parameters:
            targets: Target mesh(es).
            source: Source mesh(es) -- the meshes whose lightmaps travel.
            output_dir: Where resampled maps go; as :meth:`TextureTransfer.transfer`.
            output_name: Base name for resampled maps (see above).
            size: Resampled map resolution; default = the texels the object
                owned in its source map (see ``ptk.UvTransfer.remap_lightmap``).
            supersample / padding: As :meth:`TextureTransfer.transfer`.

        Returns:
            ``{target: {"path": lightmap path, "how": "rebound" | "resampled"}}``
            for each target now carrying its source's lightmap.

        Raises:
            ValueError: No targets or sources, or a pair whose topology differs.
        """
        try:
            import numpy  # noqa: F401 -- the resample's array math
        except ImportError:
            raise RuntimeError("numpy is required") from None
        from mayatk.uv_utils._uv_utils import UvUtils
        from mayatk.uv_utils.texture_transfer import TextureTransfer

        targets = [str(t) for t in ptk.make_iterable(targets)]
        sources = (
            [str(s) for s in ptk.make_iterable(source)] if source is not None else []
        )
        if not targets:
            raise ValueError("no target meshes")
        if not sources:
            raise ValueError("a lightmap travels between meshes: name the source(s)")
        if len(sources) > len(targets):
            # Each source's lightmap is its own object's lighting, bound per
            # object -- several cannot become one without a re-bake.
            raise ValueError(
                "a lightmap travels one mesh to one mesh; a target combined "
                "from several sources needs its own (Lightmap Baker)"
            )

        # ---- plan: what each pair carries, before anything is written ----
        checked: List[Tuple[str, str, Dict[str, Any], str, Optional[str]]] = []
        for tgt, src in TextureTransfer.pair_sources(targets, sources).items():
            info = cls.lightmap_info(src)
            leaf = CoreUtils.leaf_name(tgt)
            if not info:
                continue
            if not info.get("path"):
                cls.logger.warning(
                    f"{leaf}: the source's lightmap {info['map']!r} is not on "
                    "disk; skipped. Repair its path first (Texture Path Editor)."
                )
                continue
            ok, why = TextureTransfer.topology_matches(tgt, src)
            if not ok:
                raise ValueError(f"{tgt} / {src}: topology differs ({why})")
            if not TextureTransfer.positions_match(tgt, src):
                # Carried anyway -- asked for -- but a lightmap is the light AT
                # the source's place, so a copy standing elsewhere is lit wrong.
                cls.logger.warning(
                    f"{leaf}: target and source stand in different places; it "
                    "now carries the lighting baked where the source stands."
                )
            s_shape = NodeUtils.get_shape(src, no_intermediate=True, full_path=True)
            t_shape = NodeUtils.get_shape(tgt, no_intermediate=True, full_path=True)
            s_sets = cmds.polyUVSet(s_shape, query=True, allUVSets=True) or []
            s_set = info.get("uv_set")
            if s_set not in s_sets:
                s_set = UvDiagnostics.find_lightmap_uv_set(s_shape, s_sets)
            if not s_set:
                cls.logger.warning(
                    f"{leaf}: the source's lightmap UV set is gone; skipped. "
                    "Re-bake the source first (Lightmap Baker)."
                )
                continue
            checked.append(
                (tgt, src, info, s_set, UvDiagnostics.find_lightmap_uv_set(t_shape))
            )

        # ---- a target without lightmap UVs takes the source's layout ------
        # Only now, every pair having passed its checks: a run that raises
        # writes nothing.
        plan: List[Dict[str, Any]] = []
        for tgt, src, info, s_set, t_set in checked:
            leaf = CoreUtils.leaf_name(tgt)
            if not t_set:
                layout = UvUtils.export_uv_layout([src], uv_set=s_set).get(src)
                t_set = (
                    UvUtils.apply_uv_layout(
                        {tgt: layout}, uv_set=UvDiagnostics.LIGHTMAP_UV_SET, quiet=True
                    ).get(tgt)
                    if layout
                    else None
                )
                if not t_set:
                    cls.logger.warning(
                        f"{leaf}: could not be given the source's lightmap UVs; "
                        "skipped."
                    )
                    continue
                cls.logger.info(
                    f"{leaf}: no lightmap UV set -- given the source's "
                    f"({s_set} -> {t_set})."
                )
            corr = TextureTransfer.correspondence(
                tgt, src, source_uv_set=s_set, target_uv_set=t_set
            )
            if corr["dropped"]:
                cls.logger.warning(
                    f"{leaf}: {corr['dropped']} triangle(s) have no lightmap UVs "
                    "in one of the two sets and were skipped."
                )
            long_tgt = (cmds.ls(tgt, long=True) or [tgt])[0]
            plan.append(
                {
                    "owner": long_tgt,
                    "source": src,
                    "name": CoreUtils.leaf_name(long_tgt),
                    "path": info["path"],
                    "scale_offset": info["scaleOffset"],
                    "intensity": float(info.get("intensity", 1.0)),
                    "src": corr["src_tris"],
                    "dst": corr["dst_tris"],
                }
            )

        # ---- resample the pairs whose layouts differ (pythontk) -----------
        # Resolved only when something is written: the default output folder
        # and the claims scan cost nothing a pure rebind needs. Written to the
        # session until the scene is saved (_stage); the names, which claims
        # alone decide, are the folder's own.
        resample = [
            j for j in plan if not ptk.UvTransfer.layouts_match(j["src"], j["dst"])
        ]
        written = (
            ptk.UvTransfer.resample_lightmaps(
                resample,
                output_dir=cls._pending().folder(
                    TextureTransfer.resolve_output_dir(output_dir)
                ),
                read=cls._read_lightmap,
                write=cls._write_lightmap,
                output_name=output_name,
                claims=cls.claims(),
                size=size,
                supersample=supersample,
                padding=padding,
                log=cls.logger.info,
            )
            if resample
            else {}
        )

        # ---- bind: one commit per (intensity, written) group ---------------
        groups: Dict[Tuple[float, bool], Tuple[Dict[str, str], Dict[str, Any]]] = {}
        for job in plan:
            tgt, fresh = job["owner"], job["owner"] in written
            mapping, rects = groups.setdefault((job["intensity"], fresh), ({}, {}))
            mapping[tgt] = written[tgt] if fresh else job["path"]
            if not fresh:
                rects[tgt] = job["scale_offset"]
        results: Dict[str, Dict[str, str]] = {}
        for (intensity, fresh), (mapping, rects) in groups.items():
            recorded = cls.commit(mapping, rects, intensity=intensity, written=fresh)
            for tgt, path in recorded.items():
                results[tgt] = {
                    "path": path,
                    "how": "resampled" if fresh else "rebound",
                }
        # The bake baseline: a target's map was baked when its source's was --
        # and under its lights, which the manifest ships for it (:meth:`_record`).
        cls._inherit_bake_state(
            {
                job["owner"]: (job["source"], job["owner"] not in written)
                for job in plan
                if job["owner"] in results
            }
        )
        if results:
            cls._publish()
        return results

    # ----------------------------------------------------- lightmap IO
    @staticmethod
    def _read_lightmap(path: str) -> "np.ndarray":
        """*path*'s texels as top-down float RGB (file channel order kept)."""
        import numpy as np

        from mayatk.mat_utils.texture_baker import TextureBaker

        img = TextureBaker._read_exr(path)
        if img is None:
            raise ValueError(f"cannot read lightmap {path!r}")
        img = np.asarray(img, dtype=np.float32)
        if img.ndim == 2:
            img = img[..., None]
        return img[..., :3] if img.shape[2] >= 3 else np.repeat(img[..., :1], 3, 2)

    @staticmethod
    def _write_lightmap(path: str, image: "np.ndarray") -> None:
        """Write *image* (top-down float) the way the Lightmap Baker writes one."""
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        LightmapBaker._write_lightmap_exr(path, image)
