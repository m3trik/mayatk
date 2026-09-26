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
  :meth:`LightmapRecords.superseding` (what a re-bake leaves behind, deleted),
  and :meth:`LightmapRecords.migrate_legacy` for markers older than the
  rect-binding contract.
* **The manifest** -- the ``lightmap_metadata`` record that rides the FBX on
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

Every method is a classmethod: the record is scene state, so the Texture Path
Editor, the Scene Exporter and the FBX producer read it without building a
baker.
"""

import contextlib
import json
import os
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    cmds = None
    print(__file__, error)

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics
from mayatk.env_utils._env_utils import EnvUtils
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
    LIGHTMAP_METADATA: str = ptk.SceneRecords.LIGHTMAPS.key
    LIGHTMAP_METADATA_VERSION: int = ptk.SceneRecords.LIGHTMAPS.version

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
        """Drop the folder and writer entries no marker names any more (a revert)."""
        hints, writers = cls._folder_hints(), cls._writers()
        if not (hints or writers):
            return
        named = {cls._hint_key(i.get("map")) for _t, i in cls._marker_records()}
        kept = {k: v for k, v in hints.items() if k in named}
        if kept != hints:
            cls._save_folder_hints(kept)
        kept = {k: v for k, v in writers.items() if k in named}
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
        -- its own only while it still is (a Save As copy carries the same
        ``""``). A map with no entry was committed before the record existed,
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

    # ------------------------------------------------------------------
    # Commit / revert
    # ------------------------------------------------------------------

    @classmethod
    def commit(
        cls,
        mapping: Dict[str, str],
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        intensity: float = 1.0,
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (fully non-destructive).

        Nothing about the object's material or UV order changes: the full PBR
        material and texture UV0 are kept, and the lightmap stays a separate
        HDR on UV channel index 1 (where engines bind it), composited ``albedo
        x lightmap`` by the engine. Per object it stamps the marker on the
        TRANSFORM and records the map's folder and writer (this scene) in the
        private records, then republishes the scene manifest onto the shared
        ``data_export`` carrier so it rides the FBX. Files are never touched:
        a re-bake deletes the maps it superseded around its commit
        (:meth:`superseding`).

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

        Returns:
            ``{object: lightmap path}`` for each object recorded.
        """
        if cmds is None:
            cls.logger.error("maya.cmds not available; commit aborted.")
            return {}

        scale_offsets = scale_offsets or {}
        recorded: Dict[str, str] = {}
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

        if recorded:
            hints = cls._folder_hints()
            hints.update(folders)
            cls._save_folder_hints(hints)
            # ...and that THIS scene wrote them: what lets a later re-bake
            # delete them once superseded (:meth:`superseding`).
            from mayatk.node_utils.data_nodes import DataNodes

            writers = cls._writers()
            writers.update(dict.fromkeys(folders, DataNodes.writer_stamp()))
            cls._save_writers(writers)
            cls._publish()
        return recorded

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
        and the manifest back.

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
        if cleared:
            cls._publish()
        return cleared

    @classmethod
    @contextlib.contextmanager
    def superseding(cls, objects: List[str]) -> Iterator[List[str]]:
        """Around a re-bake's :meth:`commit`: delete the maps *objects* stop reading.

        A re-bake that changes where or how its maps are written -- another
        output folder, Beside Material Textures, another name affix,
        per-object maps folded into an atlas or back -- gives its objects new
        files and leaves the old ones behind, read by nobody. They are not
        just clutter: a same-named leftover is what a reader joining names
        against folders finds (the production room once bound a 17-day-old
        atlas that way), and beside the textures one takes its own name, so
        the next bake that returns to it writes ``_1``. The maps *objects*
        read on entry are deleted on a clean exit once no marker in the scene
        reads them (:meth:`ptk.FileDependencies.remove_superseded`); a block
        that raises deletes nothing.

        Only this scene's own maps are candidates: recorded in its folder
        record AND written by it (``DataNodes.written_here``) -- never a map
        another scene file still reads, such as a Save As copy's source, nor
        one committed before writers were recorded -- and never one a
        REFERENCED object reads, which its own file may name too. A map an
        excluded, failed or out-of-scope object still reads is read, and
        stays. The deletion cannot be undone; the commit's markers can.

        Parameters:
            objects: The transforms the block commits new maps for.

        Yields:
            A list, filled with the deleted paths on exit.
        """
        retired: List[str] = []
        before = cls._written_reads(objects)
        yield retired
        if not before:
            return
        retired.extend(ptk.FileDependencies.remove_superseded(before, cls._reads()))
        if retired:
            cls.logger.info(
                "Deleted %d superseded lightmap(s) nothing reads any more: %s",
                len(retired),
                ", ".join(os.path.basename(p) for p in retired),
            )

    @classmethod
    def _written_reads(cls, objects: List[str]) -> List[str]:
        """The map files *objects* read now that are this scene's to delete once
        superseded (:meth:`superseding`)."""
        if cmds is None or not objects:
            return []
        from mayatk.node_utils.data_nodes import DataNodes

        hints, writers = cls._folder_hints(), cls._writers()
        readers = cls.claims()
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
            if any(cls._referenced(o) for o in readers.get(key, ())):
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
    def _referenced(node: str) -> bool:
        """Whether *node* comes from a referenced file."""
        try:
            return bool(cmds.referenceQuery(node, isNodeReferenced=True))
        except RuntimeError:
            return False

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

    @classmethod
    def _stamp_uv_rect(cls, obj: str, rect: Optional[List[float]]) -> None:
        """Record (or, for the identity, drop) a legacy ``uvRect`` on *obj*'s marker.

        Only :meth:`LightmapBaker.commit_lightmap`'s deprecated ``uv_rects``
        writes one: a caller asserting that it already squeezed these UVs.
        """
        info = cls._marker_info(obj)
        node = cls._marker_node(obj)
        if not info or not node:
            return
        if rect and [float(v) for v in rect] != list(cls.IDENTITY_SCALE_OFFSET):
            info["uvRect"] = [float(v) for v in rect]
        else:
            info.pop("uvRect", None)
        cls._write_marker(node, info)

    # ------------------------------------------------------------------
    # The export manifest
    # ------------------------------------------------------------------

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``lightmap_metadata`` record for this scene, or ``None`` when no
        lightmapped mesh remains -- the ``ptk.SceneRecords.LIGHTMAPS`` producer
        (``FbxUtils.PRODUCERS``). Pure: it reads the markers and never writes.

        Parameters:
            ctx: The export's decisions (unused: the manifest is a function of
                the markers alone).
        """
        return cls._record()

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Rebuild the ``lightmap_metadata`` export channel from the scene's markers.

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

    @classmethod
    def _record(cls) -> Optional[ptk.Record]:
        """(Re)build the lightmap manifest record from the scene's markers.

        One entry per marked TRANSFORM (one per instance, each with its own
        atlas ``scaleOffset``), legacy shape markers included, in the one
        marker enumeration's order (:meth:`_marker_records`, sorted by path).
        The ``version`` is stamped by the ``ptk.SceneRecords.LIGHTMAPS``
        declaration. unitytk's optional editor helper reads it to auto-bind
        Unity's native lightmap slots (``renderer.lightmapScaleOffset`` per
        entry). Returns ``None`` when no lightmapped mesh remains (the
        publisher then clears the channel).
        """
        entries: List[Dict[str, Any]] = []
        for transform, info in cls._marker_records():
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
                    "intensity": info.get("intensity", 1.0),
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
        # ``dirs``); a manifest carrying them still reads.
        return ptk.SceneRecords.LIGHTMAPS.make({"objects": entries})

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
        """
        return ptk.FileDependencies.claims(
            (transform, info.get("map"))
            for transform, info in cls._marker_records(objects)
        )

    @classmethod
    def _resolve(
        cls,
        objects: Optional[List[str]] = None,
        search_dirs: Optional[List[str]] = None,
        walk: bool = True,
    ) -> List[Dict[str, Any]]:
        """The markers' maps through :meth:`ptk.FileDependencies.resolve`, Maya's way."""
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
        if not refs:
            return []
        return ptk.FileDependencies.resolve(
            refs,
            search_dirs=cls._texture_search_dirs()
            if search_dirs is None
            else search_dirs,
            walk_root=cls._walk_root() if walk else "",
            find_files=cls._find_files,
            resolve_hint=cls._resolved_dir,
        )

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
                ``None`` for every marker in the scene.
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
    ) -> Dict[str, Any]:
        """Gather the scene's lightmaps into *dest_dir* and repoint the markers.

        The lightmap half of the Texture Path Editor's *Find & Copy*: a map
        that resolves is its own source; one that does not is searched for
        under *source_dir* (recursively; the newest same-named file wins --
        the panel's rule for textures). A source already sitting in
        *dest_dir* needs no file operation and still gets its hint rewritten.
        *mode* is ``"copy"`` or ``"move"``, through the panel's own copier.
        With *dry_run* nothing is created, copied or written -- the plan comes
        back as it would run (:meth:`ptk.FileDependencies.relocate`).

        Returns::

            {"relocate": [(src, dst)], "in_place": [src], "missing": [records],
             "copied": [(src, dst)], "updated": markers rewritten}
        """
        plan = ptk.FileDependencies.relocate(
            cls._resolve(objects),
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

    @staticmethod
    def _copy_files(
        sources: List[str], dest_dir: str, mode: str
    ) -> List[Tuple[str, str]]:
        """Copy or move *sources* the way the Texture Path Editor moves textures."""
        return list(
            MatUtils.move_texture_files(
                found_files=list(sources),
                new_dir=dest_dir,
                delete_old=(mode == "move"),
            )
        )

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
            if hints != before:
                cls._save_folder_hints(hints)
            if count:
                cls._publish()
        return count
