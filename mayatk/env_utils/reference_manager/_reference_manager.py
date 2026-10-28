# !/usr/bin/python
# coding=utf-8
"""Reference Manager engine — Maya file references, assemblies and workspaces.

:class:`ReferenceManager` (over :class:`~mayatk.env_utils.workspace_manager.WorkspaceManager`)
lists, adds, removes and converts scene references, and owns the scene-file
operations a browser runs (open / close / save / rename / delete; foreign rows baked
through the blender_bridge); :class:`AssemblyManager` handles assembly definitions.
Its panel is tentacle's shared Reference Manager (``slots/tools/reference_manager.py``),
which composes one and passes its progress, bake and report collaborators in;
blendertk's ``ReferenceManager`` mirrors it over linked libraries.
"""

import os
from typing import Callable, ContextManager, List, Optional

try:
    import maya.cmds as cmds
except ImportError:
    pass
import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.env_utils.usd import UsdReadRefused, UsdUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager

# Scratch twins of foreign scenes opened "as new" (see ReferenceManager.open_scene): one
# per source under the system temp dir, age-swept, discarded on close/next open while
# still untouched. Process-wide, like the open scene itself; lazily built (no temp-dir
# lookup at import). Mirror of blendertk's.
_OPENED_SCRATCH_PREFIX = "mtk_opened"
_opened_scratches = None


def _scratch_twins():
    """The ``ptk.ScratchTwins`` store (``<temp>/mtk_opened_<hash>/<stem>_<ext>.ma``)."""
    global _opened_scratches
    if _opened_scratches is None:
        _opened_scratches = ptk.ScratchTwins(_OPENED_SCRATCH_PREFIX, extension=".ma")
    return _opened_scratches


class _FileRef:
    """Lightweight cmds-based stand-in for pm.system.FileReference."""

    __slots__ = ("_ref_node",)

    def __init__(self, ref_node: str):
        self._ref_node = ref_node

    @property
    def namespace(self) -> str:
        ns = cmds.referenceQuery(self._ref_node, namespace=True) or ""
        return ns.lstrip(":")

    @property
    def path(self) -> str:
        return cmds.referenceQuery(
            self._ref_node, filename=True, withoutCopyNumber=True
        )

    @property
    def label(self) -> str:
        """Human name for a message — the namespace, or the node name when the
        reference is too broken to have one, which is exactly when something is
        being reported about it.
        """
        try:
            return self.namespace
        except RuntimeError:
            return self._ref_node

    def remove(self) -> list:
        """Remove the reference, and the foster parent it would leave behind.

        Returns:
            (list): What :meth:`EnvUtils.clear_orphan_foster_parents` moved to the
                world -- this reference's fostered nodes, and any an earlier
                removal left.
        """
        cmds.file(removeReference=True, referenceNode=self._ref_node)
        return EnvUtils.clear_orphan_foster_parents()

    def load(self):
        # An FBX (this one, or one this scene references) re-reads under the
        # importer's sticky take selector: an index past its takes loads nothing.
        FbxUtils._default_import_take(if_loaded=True)
        cmds.file(loadReference=self._ref_node)

    def importContents(self, removeNamespace: bool = True):
        # Capture namespace BEFORE import — the reference node is removed by
        # importReference, so querying after returns nothing and the namespace
        # strip would silently no-op, leaving every node prefixed.
        ns = self.namespace
        cmds.file(referenceNode=self._ref_node, importReference=True)
        if removeNamespace and ns:
            _ReferenceManagerInternal._merge_namespace_into_root(ns)


class AssemblyManager:
    @classmethod
    def current_references(cls):
        """Get the current scene references.

        Returns:
            list: A list of _FileRef objects representing the current scene references.
        """
        return _ReferenceManagerInternal._list_file_refs()

    @classmethod
    def create_assembly_definition(cls, namespace: str, file_path: str) -> str:
        """Create an assembly definition for the given file path.

        Parameters:
            namespace (str): The namespace to be used for the assembly.
            file_path (str): The file path of the scene to create the assembly from.

        Returns:
            str: The name of the created representation, or None if the creation failed.
        """
        return cls._create_definition(namespace, file_path)[1]

    @classmethod
    def _create_definition(cls, namespace: str, file_path: str):
        """``(assembly_node, representation)``, or ``(None, None)`` with a warning.

        The node is the name Maya actually gave it: ``<namespace>_assembly``
        when free, suffixed when that name is taken.
        """
        if not os.path.exists(file_path):
            cmds.warning(f"File does not exist: {file_path}")
            return None, None
        try:
            Plugins.load("sceneAssembly")  # mayapy never autoloads it
            assembly_node = cmds.assembly(
                name=f"{namespace}_assembly", type="assemblyDefinition"
            )
            cmds.assembly(
                assembly_node, edit=True, createRepresentation="Scene", input=file_path
            )
            representations = cmds.assembly(
                assembly_node, query=True, listRepresentations=True
            )
        except Exception:
            representations = None
        if not representations:
            cmds.warning(f"Failed to create assembly definition for {file_path}")
            return None, None
        return assembly_node, representations[0]

    @classmethod
    def set_active_representation(
        cls, assembly_node: str, representation_name: str
    ) -> bool:
        """Set the active representation for an assembly.

        Parameters:
            assembly_node (str): The name of the assembly node.
            representation_name (str): The name of the representation to set as active.

        Returns:
            bool: True if the representation was successfully set as active, False otherwise.
        """
        try:
            cmds.assembly(assembly_node, edit=True, active=representation_name)
            return True
        except Exception:
            cmds.warning(f"Failed to set active representation for {assembly_node}")
            return False

    @classmethod
    def convert_references_to_assemblies(cls):
        """Convert all current references to assembly definitions and references.

        Iterates through all current references, creates an assembly definition for each,
        sets the active representation, and optionally removes the original reference after conversion.
        """
        # Each helper warns on its own failure; the reference stays when either fails.
        for ref in cls.current_references():
            node, rep_name = cls._create_definition(ref.namespace, ref.path)
            if rep_name and cls.set_active_representation(node, rep_name):
                ref.remove()


class _ReferenceManagerInternal(object):
    """Internal helpers for ReferenceManager."""

    @classmethod
    def _holds_other_scenes(cls, folder, scene_path) -> bool:
        """True if *folder* holds a Maya scene, at any depth, other than *scene_path*
        and its own incremental saves -- a folder that is then not that scene's alone
        to move with it: a folder shared by versions, or the scenes root itself.

        Also True when *folder* cannot be read in full: leave what cannot be
        inspected alone, as Delete does. A folder that does not exist holds nothing.
        """
        scene = os.path.normcase(os.path.normpath(scene_path))
        own_increments = os.path.normcase(ptk.Workspace.backup_dir(scene_path))

        def _unreadable(error):
            if not isinstance(error, FileNotFoundError):
                raise error

        try:
            for root, dirs, files in os.walk(folder, onerror=_unreadable):
                if os.path.normcase(os.path.normpath(root)) == own_increments:
                    dirs[:] = []
                    continue
                for name in files:
                    if (
                        os.path.splitext(name)[1].lower()
                        not in EnvUtils.SCENE_SAVE_TYPES
                    ):
                        continue
                    path = os.path.normcase(os.path.normpath(os.path.join(root, name)))
                    if path != scene:
                        return True
        except OSError:
            return True
        return False

    @staticmethod
    def _is_usd(path) -> bool:
        """True if *path* is a USD layer or package -- read through mayaUsd's translator."""
        return (
            bool(path) and os.path.splitext(str(path))[1].lower() in UsdUtils.EXTENSIONS
        )

    @staticmethod
    def _is_fbx(path) -> bool:
        """True if *path* is an FBX -- read through the FBX plugin."""
        return bool(path) and os.path.splitext(str(path))[1].lower() == ".fbx"

    @staticmethod
    def bake_source(referenced_path) -> Optional[str]:
        """The scene *referenced_path* was baked from, or None.

        A foreign row, and an FBX no one take reads whole (:meth:`add_reference`),
        reference a cached ``.ma`` bake; mapping a live reference back to the row
        the user sees is the only way that row can read as referenced (and be
        un-referenced by a second click).
        """
        if not referenced_path:
            return None
        try:
            from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport
        except ImportError:  # bridge unavailable — no row can be bake-backed
            return None
        return BlenderSceneImport.bake_source(referenced_path)

    @classmethod
    def _bake_source_key(cls, referenced_path) -> Optional[str]:
        """:meth:`bake_source`, normalized for comparison."""
        source = cls.bake_source(referenced_path)
        return os.path.normcase(os.path.normpath(source)) if source else None

    @classmethod
    def row_key(cls, referenced_path) -> str:
        """Normalized path of the row a reference to *referenced_path* stands
        for: its bake's source (:meth:`_bake_source_key`), else the file itself."""
        return cls._bake_source_key(referenced_path) or os.path.normcase(
            os.path.normpath(referenced_path)
        )

    @staticmethod
    def _merge_namespace_into_root(namespace: str) -> bool:
        """Dissolve *namespace* into the root namespace; True if there was one to dissolve.

        ABSOLUTE (``:ns``) throughout, and that is the whole point of the helper:
        ``cmds.namespace`` resolves a BARE name against the session's CURRENT namespace,
        so a session left pointing anywhere but root (the Namespace Editor does this, and
        so does any sandbox import) made the existence check report False — the strip
        silently no-opped and an unlink that promised to remove the namespace kept it.
        """
        path = f":{namespace.strip(':')}"
        if not cmds.namespace(exists=path):
            return False
        cmds.namespace(removeNamespace=path, mergeNamespaceWithRoot=True)
        return True

    @staticmethod
    def _ensure_namespace(namespace: str) -> str:
        """Create *namespace* plus any missing parents; return its absolute path.

        ``namespace -add`` will not create intermediate levels, so a nested reference
        namespace (``parent:child``) has to be built one level at a time.
        """
        path = ""
        for part in namespace.strip(":").split(":"):
            path = f"{path}:{part}"
            if not cmds.namespace(exists=path):
                cmds.namespace(add=path)
        return path

    @staticmethod
    def _list_file_refs():
        """Return _FileRef objects for the references this panel can act on.

        The screen itself is :meth:`EnvUtils.list_reference_nodes` — it is not the
        panel's alone, and it is what keeps a node the panel cannot act on out of the
        table: every row here is one the user may select, unreference and unlink, and
        a nested or file-less reference node is none of those (reading
        :attr:`_FileRef.path` on the latter raises outright).
        """
        return [_FileRef(rn) for rn in EnvUtils.list_reference_nodes()]

    def _remove_promoted_file_less(self, standing) -> list:
        """Delete the file-less reference nodes an import promoted to top level.

        A reference Maya could not form -- a scene referencing the file that is
        ALREADY open -- survives inside its parent as a node with no file, and
        importing the parent promotes it to a top-level node that the scene then
        saves, and that Maya's Reference Editor shows as a broken reference. It is
        debris the import itself made, so the import removes it (reference nodes
        are locked, hence the unlock). *standing* are
        :meth:`CoreUtils.node_handles` for the file-less nodes that were already
        top level before the import; those are not its to touch. Called inside
        the import's undo chunk, so one undo brings the nodes back.

        Returns:
            (list): The names of the nodes removed.
        """
        keep = set(CoreUtils.resolve_handles(standing))
        removed = []
        for rn in EnvUtils.list_reference_nodes(file_less=True):
            if rn in keep:
                continue
            try:
                cmds.lockNode(rn, lock=False)
                cmds.delete(rn)
            except RuntimeError as e:
                self.logger.warning(
                    f"Could not remove broken reference node '{rn}': {e}"
                )
                continue
            removed.append(rn)
        if removed:
            self.logger.info(
                f"Removed {len(removed)} broken reference node(s) the import "
                f"promoted: {', '.join(removed)}"
            )
        return removed

    @staticmethod
    def _authored_name(name: str, namespace: str) -> str:
        """*name* (a referenced node's current long name) as the reference's
        own file spells it: *namespace* stripped from each path component."""
        if not name or not namespace:
            return name
        prefix = f"{namespace}:"
        return "|".join(
            part[len(prefix) :] if part.startswith(prefix) else part
            for part in name.split("|")
        )

    @staticmethod
    def _import_renames(authored, now):
        """``rename(name)`` for an import: a node's name in its own file (as
        *authored*) -> where the import put it (*now*, the same nodes in the
        same order), for each node the import renamed -- a clash digit, a kept
        or re-applied namespace.  A leaf spelling maps too where it named one
        node of the reference, and a plug (``node.attr``) through its node;
        ``None`` for a name the import left alone."""
        full: dict = {}
        leaves: dict = {}
        counts: dict = {}
        for before, after in zip(authored, now):
            if not before:
                continue
            leaf = before.rsplit("|", 1)[-1]
            counts[leaf] = counts.get(leaf, 0) + 1
            if after and after != before:
                full[before] = after
                leaves[leaf] = after
        leaves = {leaf: to for leaf, to in leaves.items() if counts[leaf] == 1}

        def rename(name: str) -> Optional[str]:
            hit = full.get(name) or leaves.get(name)
            if hit:
                return hit
            node, dot, attr = str(name).partition(".")
            hit = (full.get(node) or leaves.get(node)) if dot else None
            return f"{hit}.{attr}" if hit else None

        return rename


class ReferenceManager(
    WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin, _ReferenceManagerInternal
):
    """Maya's Reference Manager engine: the workspace's scene files, the scene's
    references, and the scene-file operations a browser runs on them.

    Features:
    - Add/remove references with namespace management; import them (unlink).
    - Update references from source files; display overrides.
    - Open / close / save / rename / delete scene files; foreign (Blender) rows baked
      through the blender_bridge, and opened as scratch twins.

    No UI: a panel composes one, handing it its collaborators (:meth:`__init__`), and
    calls its API -- tentacle's shared Reference Manager is that panel. blendertk's
    ``ReferenceManager`` is the same surface over linked libraries.
    """

    # Widen the workspace-file scan to every natively referenceable type -- FBX through the
    # FBX plugin, every USD spelling through mayaUsd's translator. A browser's Include Types
    # row filters this cached superset, so toggling a type never re-scans disk.
    SCENE_FILE_TYPES = ("*.ma", "*.mb", "*.fbx") + tuple(
        f"*{ext}" for ext in ptk.USD_EXTENSIONS
    )
    #: Extensions referenced natively -- exactly what the workspace scan lists.
    NATIVE_EXTENSIONS = tuple(t.lstrip("*") for t in SCENE_FILE_TYPES)
    #: Foreign (cross-DCC) rows: baked through the blender_bridge before they can be
    #: referenced or opened (:meth:`bake_scene`).
    FOREIGN_EXTENSIONS = (".blend",)
    #: Rows Unlink and Import brings in with no reference behind them: a foreign row
    #: converts; a USD row imports natively -- the one way in for a stage whose skins
    #: a live read refuses (``UsdUtils.live_read_options``).
    IMPORT_EXTENSIONS = (*FOREIGN_EXTENSIONS, *ptk.USD_EXTENSIONS)
    #: The Include Types a browser lists by default: Maya's own scene types.
    INCLUDE_DEFAULTS = (".ma", ".mb")
    #: What Save writes (:meth:`save_scene`).
    SCENE_SAVE_EXTENSION = ".ma"
    #: How a foreign scene's rig logic can travel (``bake_scene`` / ``import_scene``
    #: ``rig_mode``) -- the choices a browser offers when the scene has some. No
    #: ``"raw"``: both Blender exporters sample the EVALUATED scene, so on this
    #: direction raw converts exactly like bake.
    RIG_MODES = ("rig", "bake")
    #: Where a browser's header presets (naming / filter settings) are saved.
    PRESET_DIR = "mayatk/reference_manager"

    _progress = None
    _report = None

    def __init__(
        self,
        log_level="WARNING",
        *,
        progress: Optional[Callable[[str], ContextManager]] = None,
        bake_for_reference: Optional[Callable[..., Optional[str]]] = None,
        report: Optional[Callable[[str], None]] = None,
    ):
        """Build the engine; a panel passes its collaborators in (blendertk's takes
        the same, so a panel builds either host's engine one way).

        Parameters:
            log_level: The logger's level.
            progress: ``progress(text)`` -> a context manager yielding a
                ``progress(current, total, message)`` callback (or None): the scope
                the first read of an FBX's takes runs in (a large file's take layout
                takes seconds). A panel passes its footer progress; default, none.
            bake_for_reference: Replaces :meth:`_bake_for_reference` --
                ``(path, project=None) -> bake path or None`` -- the bake a multi-take
                FBX's reference and a foreign row's open read (a panel runs it in its
                progress, asking how a rig travels, and shows its errors).
            report: ``report(message)``: puts an outcome the user must see in front
                of them (a panel's message box) -- a USD row refused for a live read,
                a per-scene folder that kept its old name. Default: logged only.
        """
        super().__init__()
        self.set_log_level(log_level)
        self._progress = progress
        self._report = report
        if bake_for_reference is not None:
            self._bake_for_reference = bake_for_reference

    @property
    def current_references(self):
        """Get the current scene references.
        Returns a list of _FileRef objects.
        """
        return _ReferenceManagerInternal._list_file_refs()

    @staticmethod
    def sanitize_namespace(namespace: str) -> str:
        """Sanitize the namespace by replacing or removing illegal characters."""
        return EnvUtils.sanitize_namespace(namespace)

    def add_reference(self, namespace: str, file_path: str) -> bool:
        """Reference *file_path* under *namespace* (sanitized; Maya makes it unique).

        A USD layer reads through mayaUsd's translator, refused when it is not safe
        to read live. An FBX its last take reads whole is referenced natively, its
        take selector pinned; any other FBX (``FbxUtils._native_read_gap``: takes
        merged, one parked, a cover listed first) through a cached ``.ma`` bake,
        which maps back to the FBX (:meth:`_bake_source_key`) and follows it when
        it is re-exported (:meth:`follow_bake_sources`) -- a native FBX reference
        plays one take, whichever the session's selector names, at every load.

        Returns:
            (bool): True once *file_path* is referenced, also when it already was.
        """
        # Ensure the file exists before proceeding
        if not os.path.exists(file_path):
            file_not_found_error_msg = f"File not found: {file_path}"
            self.logger.error(file_not_found_error_msg)
            cmds.warning(file_not_found_error_msg)
            return False

        # Check if the file is fully accessible (not virtual)
        try:
            with open(file_path, "rb") as f:
                f.read(1)  # Try to read a byte to ensure the file is accessible
        except (OSError, IOError) as e:
            error_msg = (
                f"Could not open file: {file_path}\n"
                f"Possible reasons include:\n"
                f"- The file is virtual or not fully downloaded\n"
                f"- There is an issue accessing the file (ex. permissions)\n"
                f"Error details: {str(e)}"
            )
            cmds.warning(error_msg)
            return False

        # Already referenced -- the file itself, or through the bake that stands in
        # for it (either side may be a bake: a foreign row adds its bake's path).
        row = self.row_key(file_path)
        if any(self.row_key(ref.path) == row for ref in self.current_references):
            return True

        # A USD layer reads through mayaUsd's translator, NAMED -- left to pick one by
        # extension, Maya reads it with animation off -- and only once the stage is
        # proven safe to read live: a skin the reader crashes on takes Maya with it,
        # and a layer pxr cannot read leaves an empty reference node behind.
        read = {}
        if self._is_usd(file_path):
            try:
                read = UsdUtils.live_read_options(file_path)
            except RuntimeError as e:
                return self._refuse_live_read(file_path, e)

        # An FBX reads through the plugin, which plays the ONE take the session's
        # sticky selector names -- at this load and at every reload and reopen.
        # A file the per-file -1 reads whole is pinned to it; any other (takes
        # merged, one parked, a cover listed first) is referenced through a
        # cached .ma bake of what FbxUtils.import_scene makes of it. A bake cached
        # for the file as it is now answers without reading its takes again.
        target = file_path
        if self._is_fbx(file_path):
            baked = self._has_current_bake(file_path)
            gap = None if baked else self._read_fbx_takes(file_path)
            if baked or gap is not None:
                self.logger.info(
                    f"{os.path.basename(file_path)} ({gap or 'baked before'}): no "
                    "one take reads it whole, so the reference reads a baked .ma."
                )
                target = self._bake_for_reference(file_path)
                if not target:
                    return False
        else:  # a scene that references an FBX reads it under the selector too
            FbxUtils._default_import_take(if_loaded=True)

        # Sanitize the namespace to ensure it contains only valid characters
        sanitized_namespace = self.sanitize_namespace(namespace)

        # A bake (a foreign row's, a multi-take FBX's) is a project file: named
        # through the project, so the scene keeps it when the project moves.
        if self.bake_source(target):
            target = self._reference_spelling(target)
        try:
            cmds.file(target, reference=True, namespace=sanitized_namespace, **read)
            # Validate that a reference node was actually created
            rn = cmds.file(target, q=True, referenceNode=True)
            if not rn or cmds.nodeType(rn) != "reference":
                raise RuntimeError(
                    f"Failed to create reference for {file_path}. No valid reference node found."
                )
            if read:
                # A USD row in another unit or up axis (a Blender default export
                # is metre / Z-up; mayaUsd converts neither): its top nodes go
                # under a host-side conform group -- a parent edit Maya re-applies
                # on every reload, and an unlink keeps the group with its content.
                UsdUtils.conform_roots(
                    UsdUtils.top_transforms(
                        cmds.referenceQuery(rn, nodes=True, dagPath=True) or []
                    ),
                    UsdUtils.stage_conform(file_path),
                    f"{sanitized_namespace}_conform",
                )
            return True
        except RuntimeError as e:
            if "Could not open file" in str(e):
                cmds.warning(
                    f"Could not open file: {file_path} (Maya RuntimeError: {str(e)})"
                )
            else:
                raise
            return False

    def _refuse_live_read(self, file_path: str, error: Exception) -> bool:
        """Report why *file_path* cannot be read live -- referenced or opened -- and
        return False, the caller's result for it. A click did nothing, so the user
        must learn why (``report=``); Unlink and Import is named only for a stage the
        import CAN read (:class:`UsdReadRefused`), a damaged layer reads for neither."""
        cmds.warning(str(error))
        if self._report is not None:
            message = str(error)
            if isinstance(error, UsdReadRefused):
                message += (
                    "\n\nUnlink and Import brings it in as local data instead."
                )
            self._report(message)
        return False

    def _read_fbx_takes(self, file_path: str) -> Optional[str]:
        """What a native reference of the FBX *file_path* misses, its take
        selector pinned for the read (``FbxUtils._native_read``). The first read of
        a version runs in the panel's progress (``progress=``); a version read
        before answers at once."""
        if self._progress is None or FbxUtils._native_read_known(file_path):
            return FbxUtils._native_read(file_path, self.logger)
        with self._progress(f"Reading the takes of {os.path.basename(file_path)}"):
            return FbxUtils._native_read(file_path, self.logger)

    @staticmethod
    def _has_current_bake(file_path: str) -> bool:
        """True when the project's bake of the FBX *file_path* holds it as it is now."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport._current_bake(file_path) is not None

    @staticmethod
    def _reference_spelling(path: str) -> str:
        """*path* as a bake reference names it: relative to the project root
        when the bake's project -- the open scene's, where ``bake_scene`` keeps
        it -- IS the active one and holds it (Maya resolves a relative
        reference through the ACTIVE project, so the scene survives its project
        being moved or synced elsewhere), else as given. Spelled from another
        root, it would resolve to nothing once its own project is active."""
        try:
            root = cmds.workspace(q=True, rd=True) or ""
        except Exception:  # noqa: BLE001 - no Maya (mock tests)
            root = ""
        project = DataNodes.scene_project_root() or ""
        if (
            root
            and project
            and ptk.FileUtils.is_same_file(root, project)
            and ptk.FileUtils.is_under(path, root)
        ):
            return os.path.relpath(path, root).replace("\\", "/")
        return path

    def follow_bake_sources(self) -> list:
        """Keep every bake-backed reference in its project, on its source as the
        source is NOW.

        A native reference re-reads its file at every load; a baked one reads its
        project bake. So a reference whose FBX has been re-exported (or whose bake
        an older bake format made, ``BlenderSceneImport._current_bake``) is baked
        again -- into the same slot -- and reloaded IN PLACE
        (``file -loadReference``), keeping its reference edits. A reference to
        a bake in temp (made before 2026-10-13, which the temp sweep reclaims)
        is re-homed first: repointed onto the project's bake of its source
        (``BlenderSceneImport.rehome_bake``). A foreign (.blend) row's bake is
        only re-homed: following it means a Blender conversion.

        Returns:
            (list): The references repointed.
        """
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        baked_refs = []
        for ref in self.current_references:
            try:
                path = ref.path
            except RuntimeError:
                continue
            source = self.bake_source(path)
            if source:
                baked_refs.append((ref, path, source))
        # Source versions a refresh failed or was stopped on: retried once the
        # source moves, never at every list rebuild.
        unfollowed = self.__dict__.setdefault("_unfollowed_bakes", set())
        engine = BlenderSceneImport() if baked_refs else None
        moved = []
        for ref, path, source in baked_refs:
            target = None
            if ptk.TempArtifacts.contains(path):
                try:
                    home = engine.rehome_bake(path)
                except (OSError, RuntimeError, ValueError) as e:
                    # ValueError: a file the re-home cannot read or write in its
                    # codeset (UnicodeError) -- one old bake, never the refresh.
                    home = None
                    self.logger.warning(
                        f"Could not move the bake '{ref.label}' reads out of temp: {e}"
                    )
                # A project that itself lies in temp already reads its own slot.
                if home and not ptk.FileUtils.is_same_file(home, path):
                    target = home
            if self._is_fbx(source) and os.path.isfile(source):
                if BlenderSceneImport._current_bake(source) is None:
                    version = (
                        os.path.normcase(source),
                        BlenderSceneImport._bake_key(source),
                    )
                    if version not in unfollowed:
                        self.logger.info(
                            f"{os.path.basename(source)} changed since its "
                            "reference was baked: baking it again."
                        )
                        baked = self._bake_for_reference(source)
                        if baked:
                            target = baked
                        else:
                            # Failed or stopped (already reported): this version
                            # is not baked again at the next refresh.
                            unfollowed.add(version)
            if not target:
                continue
            spelled = self._reference_spelling(target)
            same = ptk.FileUtils.is_same_file(target, path)
            try:
                # Repointing loads it; one the user had unloaded stays unloaded.
                # A re-bake replaced the file it already reads: unloaded first,
                # so the load reads the new content.
                loaded = cmds.referenceQuery(ref._ref_node, isLoaded=True)
                if loaded and same:
                    cmds.file(unloadReference=ref._ref_node)
                cmds.file(spelled, loadReference=ref._ref_node)
                if not loaded:
                    cmds.file(unloadReference=ref._ref_node)
            except RuntimeError as e:
                self.logger.warning(
                    f"Could not repoint '{ref.label}' to {spelled}: {e}"
                )
                continue
            moved.append(ref)
        return moved

    def purge_unused_bakes(self, dry_run: bool = False) -> list:
        """Remove the bakes in the open scene's project that no scene references
        (``BlenderSceneImport.purge_unused_bakes``: the project's scenes are
        scanned as text, none is opened). Returns the paths removed."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport().purge_unused_bakes(dry_run=dry_run)

    def _bake_for_reference(self, file_path: str, project=None) -> Optional[str]:
        """The cached ``.ma`` a reference reads in place of the FBX *file_path*
        (or that a foreign row's open copies), or None with a warning.

        :meth:`bake_scene`, the foreign rows' bake: a fresh mayapy imports the
        file as :meth:`FbxUtils.import_scene` does -- its takes merged, an
        alternative parked in Key Stash, where it rides the reference and lands
        in this scene's on an import (:meth:`import_references`) -- and saves it,
        into *project*'s bake cache (default the open scene's). A hook: a panel
        runs it in its footer progress (``bake_for_reference=``).
        """
        try:
            return self.bake_scene(file_path, project=project)
        except Exception as e:  # noqa: BLE001 — reported; the reference is not made
            cmds.warning(f"Could not bake {file_path} for a reference: {e}")
            return None

    # What an unlink does with the imported reference's namespace.
    NAMESPACE_MODES = ("remove", "keep", "root")

    def _keep_namespace_on_roots(self, namespace: str, handles) -> bool:
        """Merge *namespace* into the root namespace, then move ONLY the nodes behind
        *handles* back under it. True if the namespace survived holding those nodes.

        Maya has no per-node namespace move, so the bulk strip goes through the very
        ``mergeNamespaceWithRoot`` the ``"remove"`` mode uses — identical clash and
        nested-namespace handling — and the kept roots are re-namespaced afterwards.

        A shape-bearing root carries its shape back in with it: Maya keeps a shape's
        name in step with its transform, so renaming ``root`` to ``ns:root`` renames
        ``rootShape`` to ``ns:rootShape`` too. Left as-is — the shape is the root's own
        data, and forcing it back out would only desync the pair Maya keeps together.
        """
        if not namespace or not self._merge_namespace_into_root(namespace):
            return False

        live = CoreUtils.resolve_handles(handles)
        if not live:
            self.logger.warning(
                f"Unlink: reference namespace {namespace!r} had no surviving top-level "
                "transform to keep it on — the namespace was removed instead."
            )
            return False

        ns_path = self._ensure_namespace(namespace)
        for path in live:
            try:
                cmds.rename(path, f"{ns_path}:{path.split('|')[-1]}")
            except RuntimeError as e:
                self.logger.warning(f"Failed to re-namespace {path}: {e}")
        return True

    # What an unlink does with a reference's scene data (see import_references).
    SCENE_DATA_MODES = ("merge", "discard")

    def import_references(
        self,
        namespaces=None,
        namespace_mode="remove",
        scene_data="merge",
    ):
        """Import referenced objects into the scene, making their data local.

        Parameters:
            namespaces (str/list/None): Restrict to these reference namespaces.
                None imports every reference in the scene.
            namespace_mode (str): What happens to each reference's namespace once
                imported — one of :attr:`NAMESPACE_MODES`:

                - ``"remove"``: merge the namespace into the root, so every imported
                  node loses its prefix (the long-standing behaviour).
                - ``"keep"``: leave every imported node namespaced.
                - ``"root"``: keep the prefix on the reference's top-level
                  transform(s) only and merge everything below into the root, so the
                  asset stays identifiable without prefixing the whole scene.
            scene_data (str/callable): What becomes of each reference's scene
                data -- the records its own tools kept on its ``data_internal`` /
                ``data_export`` (shots, parked keys, bake sessions, emissive
                groups). Once imported, a carrier of its own is read by nothing,
                so it never stays behind:

                - ``"merge"`` (default): each record merges into this scene's by
                  its declared rule (``DataNodes.merge_carriers``); nothing is
                  lost, and whatever arrives renamed or re-slotted is logged.
                - ``"discard"``: the records go with their carriers
                  (``DataNodes.discard_carriers``).
                - ``decide(summary, namespace) -> "merge" | "discard" | None``:
                  asked only for a reference that brings records a merge would
                  keep (*summary*: one line per record); ``None`` leaves that
                  reference referenced. A panel prompts here.

                Either way the deliverables are produced again from the merged
                scene.
        """
        if namespace_mode not in self.NAMESPACE_MODES:
            raise ValueError(
                f"Invalid namespace_mode {namespace_mode!r}; "
                f"expected one of {self.NAMESPACE_MODES}"
            )
        if not callable(scene_data) and scene_data not in self.SCENE_DATA_MODES:
            raise ValueError(
                f"Invalid scene_data {scene_data!r}; expected one of "
                f"{self.SCENE_DATA_MODES} or a callable"
            )

        all_references = self.current_references

        if namespaces is not None:
            all_references = [
                ref
                for ref in all_references
                if ref.namespace in ptk.make_iterable(namespaces)
            ]

        keep_on_root = namespace_mode == "root"
        if all_references:
            from mayatk.node_utils.data_nodes import DataNodes

            # What the scene's stores hold unwritten goes to ITS carrier now:
            # once a reference's nodes land, a scene without one adopts the
            # module's, and the idle write would land in that.
            DataNodes.flush_owners()
        with CoreUtils.undo_chunk():
            # File-less nodes already at the top level are not this import's to
            # touch; what the loop promotes there is (see the end of the chunk).
            standing = CoreUtils.node_handles(
                EnvUtils.list_reference_nodes(file_less=True)
            )
            for ref in all_references:
                # Everything the import renames is read BEFORE it: importReference
                # deletes the reference node, so the referenceQuery these come
                # from returns nothing afterwards -- the namespace and top
                # transforms 'root' needs, and the scene-data carriers.
                try:
                    ns = ref.namespace
                except RuntimeError:  # too broken to have one; nothing is prefixed
                    ns = ""
                decision, data = self._scene_data_before_import(ref, ns, scene_data)
                if decision is None:
                    self.logger.info(
                        f"Left '{ns}' referenced: its scene data was not settled."
                    )
                    continue
                roots = (
                    CoreUtils.node_handles(self.get_reference_top_transforms(ref))
                    if keep_on_root
                    else []
                )
                try:
                    ref.importContents(removeNamespace=namespace_mode == "remove")
                except RuntimeError as e:
                    self.logger.warning(f"Failed to import reference '{ns}': {e}")
                    continue
                if keep_on_root:
                    # Scoped so one asset Maya refuses to re-namespace (a locked node,
                    # a root that is itself still referenced) can't strand the rest —
                    # its contents are already imported by this point either way.
                    try:
                        self._keep_namespace_on_roots(ns, roots)
                    except RuntimeError as e:
                        self.logger.warning(
                            f"Imported '{ns}' but could not keep its namespace on the "
                            f"root(s): {e}"
                        )
                # After every rename (the namespace merge, 'root''s re-prefix):
                # the records respell to where the nodes actually landed.
                if data is not None:
                    self._settle_scene_data(decision, data, ns)
            self._remove_promoted_file_less(standing)

    def _scene_data_before_import(self, ref, namespace: str, scene_data):
        """What to do with *ref*'s scene data, and what the import will need to
        do it -- read while the reference still exists.

        Returns ``(decision, captured)``. *decision* is ``"merge"`` /
        ``"discard"``, or ``None`` when *scene_data*'s ``decide`` leaves the
        reference referenced -- asked only when a merge would keep something
        (``DataNodes.merge_plan``). *captured* is ``None`` for a reference
        with no carriers, else the carriers' and every node's handles plus
        the names those nodes have in the reference's own file.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        carriers = DataNodes.carriers_in(namespace)
        if not carriers:
            return "merge", None
        decision = scene_data
        if callable(scene_data):
            plan = DataNodes.merge_plan(carriers)
            if plan.is_empty:
                decision = "merge"  # nothing a merge keeps: no question to ask
            else:
                decision = scene_data(plan.summary(), namespace)
                if decision is None:
                    return None, None
                if decision not in self.SCENE_DATA_MODES:
                    raise ValueError(
                        f"scene_data decided {decision!r}; expected one of "
                        f"{self.SCENE_DATA_MODES} or None"
                    )
        try:
            nodes = cmds.referenceQuery(ref._ref_node, nodes=True, dagPath=True) or []
        except RuntimeError:
            nodes = []
        handles = CoreUtils.node_handles(nodes)
        try:
            ref_file = ref.path
        except RuntimeError:
            ref_file = ""
        return decision, {
            "carriers": {
                scope: CoreUtils.node_handles([node])
                for scope, node in carriers.items()
            },
            # The module's path records are spelled from ITS project: they
            # arrive re-spelled from this scene's (DataNodes.merge_carriers).
            "project": DataNodes.project_root_of(ref_file),
            "handles": handles,
            "authored": [
                self._authored_name(name, namespace)
                for name in CoreUtils.resolve_handles(handles, drop_dead=False)
            ],
        }

    def _settle_scene_data(self, decision: str, captured: dict, namespace: str):
        """Merge (or discard) an imported reference's carriers, its records
        respelled to where the import put each node; return the crossing's
        ``ptk.TransferContext`` (its notes are logged)."""
        from mayatk.node_utils.data_nodes import DataNodes

        carriers = {}
        for scope, handles in captured["carriers"].items():
            names = CoreUtils.resolve_handles(handles)
            if names:
                carriers[scope] = names[0]
        rename = self._import_renames(
            captured["authored"],
            CoreUtils.resolve_handles(captured["handles"], drop_dead=False),
        )
        settle = (
            DataNodes.merge_carriers
            if decision == "merge"
            else DataNodes.discard_carriers
        )
        ctx = settle(
            carriers,
            rename=rename,
            source=namespace,
            source_path_base=captured.get("project"),
        )
        self.logger.info(
            f"'{namespace}': scene data "
            + ("merged" if decision == "merge" else "discarded")
            + (f" ({len(ctx.notes)} note(s) logged)." if ctx.notes else ".")
        )
        return ctx

    def update_references(self):
        """Update all references to reflect the latest changes from the original files
        -- a bake-backed one through a fresh bake of its source (:meth:`follow_bake_sources`)."""
        moved = {ref._ref_node for ref in self.follow_bake_sources()}
        for ref in self.current_references:
            if ref._ref_node not in moved:
                ref.load()

    def get_reference_top_transforms(self, ref):
        """Return the reference's top-level transforms — those whose parent is outside it.

        Top-level is relative to the REFERENCE, not to the world: a referenced asset
        parented under a scene group is still that reference's root. Anchoring on
        parent-less-ness instead would return nothing the moment a user groups the
        asset, silently costing the caller (display overrides, ``"root"`` unlink) the
        very nodes it means to act on.
        """
        transforms = []
        nodes = []
        try:
            nodes = cmds.referenceQuery(ref._ref_node, nodes=True) or []
        except Exception as e:
            self.logger.debug(f"referenceQuery failed for {ref._ref_node}: {e}")

        candidates = []
        members = set()
        if nodes:
            try:
                # Members FIRST: if this throws, candidates stays empty and the
                # namespace fallback takes over — rather than leaving a populated
                # candidate list with no membership test, which would read every
                # one of them as top-level.
                members = set(cmds.ls(nodes, long=True) or [])
                candidates = cmds.ls(nodes, type="transform", long=True) or []
            except Exception as e:
                self.logger.debug(f"cmds.ls(transforms) failed: {e}")

        # Fallback: transforms living under the reference's namespace. Membership then
        # has to come from the namespace prefix too — keeping a node-list membership
        # set alongside namespace-derived candidates would test the two against each
        # other, so the set is dropped with the list that produced it.
        ns = ""
        if not candidates:
            members = set()
            try:
                ns = ref.namespace
            except Exception:
                pass
            if ns:
                try:
                    candidates = cmds.ls(f"{ns}:*", long=True, type="transform") or []
                except Exception as e:
                    self.logger.debug(f"namespace transform lookup failed: {e}")

        def _in_reference(path):
            if members:
                return path in members
            return bool(ns) and path.split("|")[-1].startswith(f"{ns}:")

        for t in candidates:
            parents = cmds.listRelatives(t, parent=True, fullPath=True) or []
            if not parents or not _in_reference(parents[0]):
                transforms.append(t)
        return transforms

    # Display mode constants — keys map to overrideDisplayType values.
    DISPLAY_MODES = ("off", "reference", "template")
    _DISPLAY_TYPE_VALUES = {"off": 0, "reference": 2, "template": 1}

    def get_reference_display_mode(self, ref) -> str:
        """Return the active display mode for the reference's top-level transforms.

        Returns one of ``"off"``, ``"reference"``, or ``"template"``. Mixed or
        partial states fall back to ``"off"`` (i.e. only reported as ``on``
        when *all* top transforms agree)."""
        transforms = self.get_reference_top_transforms(ref)
        if not transforms:
            return "off"
        seen = set()
        for t in transforms:
            try:
                if not cmds.getAttr(f"{t}.overrideEnabled"):
                    seen.add(0)
                else:
                    seen.add(cmds.getAttr(f"{t}.overrideDisplayType"))
            except Exception:
                seen.add(0)
        if seen == {1}:
            return "template"
        if seen == {2}:
            return "reference"
        return "off"

    def set_reference_display_mode(self, ref, mode: str) -> bool:
        """Set the display override mode on the reference's top-level transforms.

        ``mode`` must be one of :attr:`DISPLAY_MODES`. Returns True if at least
        one transform was successfully updated."""
        if mode not in self._DISPLAY_TYPE_VALUES:
            raise ValueError(
                f"Invalid display mode {mode!r}; expected one of {self.DISPLAY_MODES}"
            )
        dt = self._DISPLAY_TYPE_VALUES[mode]
        enable = 0 if mode == "off" else 1

        transforms = self.get_reference_top_transforms(ref)
        if not transforms:
            self.logger.warning(
                f"Display mode toggle: no top-level transforms found for reference "
                f"{ref.namespace!r} ({ref._ref_node})."
            )
            return False

        self.logger.debug(
            f"Display mode {mode!r} on {len(transforms)} transform(s) "
            f"under {ref.namespace!r}"
        )
        success = 0
        with CoreUtils.undo_chunk():
            for t in transforms:
                try:
                    cmds.setAttr(f"{t}.overrideEnabled", enable)
                    cmds.setAttr(f"{t}.overrideDisplayType", dt)
                    success += 1
                except Exception as e:
                    self.logger.warning(f"Failed to set display override on {t}: {e}")
        try:
            cmds.refresh()
        except Exception:
            pass
        return success > 0

    def remove_references(self, namespaces=None):
        """Remove references based on their namespaces.

        If no namespace is provided, all references will be removed.

        Parameters:
            namespaces (str, list of str, or None): The namespace(s) of the reference(s) to be removed.
                If None, all references will be removed. Default is None.

        Returns:
            (list): The references that could NOT be removed — empty when all went.
                A failure is per-reference rather than fatal, so one Maya refuses to
                drop cannot strand every reference behind it in the queue; returning
                them is what keeps that from being a SILENT partial success.
        """
        all_references = self.current_references

        if namespaces is None:  # Unreference all
            targets = all_references
        else:
            wanted = set(ptk.make_iterable(namespaces))
            targets = [ref for ref in all_references if ref.namespace in wanted]

        failed = []
        for ref in targets:
            try:
                namespace = ref.namespace
            except RuntimeError:
                namespace = ""
            try:
                released = ref.remove()
            except RuntimeError as e:
                self.logger.warning(
                    f"Failed to remove reference '{ref._ref_node}': {e}"
                )
                failed.append(ref)
                continue
            if released:
                self.logger.info(
                    f"Removing '{namespace or ref._ref_node}' moved local node(s) "
                    f"out of an orphaned foster parent to the world: "
                    f"{', '.join(released)}"
                )
            # The host-side group a USD reference was conformed under
            # (add_reference) goes with it once it holds nothing.
            group = f"{str(namespace).strip(':')}_conform"
            if (
                namespace
                and cmds.objExists(group)
                and not cmds.listRelatives(group, children=True)
            ):
                cmds.delete(group)
        return failed

    # ------------------------------------------------------------------ foreign rows
    @classmethod
    def is_foreign(cls, path) -> bool:
        """True if *path* is a foreign (Blender) scene -- a cross-DCC row baked through
        the blender_bridge before it can be referenced or opened."""
        return (
            bool(path)
            and os.path.splitext(str(path))[1].lower() in cls.FOREIGN_EXTENSIONS
        )

    @classmethod
    def needs_conversion(cls, path) -> bool:
        """True if bringing *path* in runs a conversion in the other DCC (a headless
        Blender for a ``.blend``) -- the rows whose conversion a browser asks about.
        A USD row imports natively: nothing to convert, nothing to ask."""
        return cls.is_foreign(path)

    @staticmethod
    def scene_has_complex_animation(path: str) -> bool:
        """True if the foreign scene *path* carries rig logic (constraints, IK,
        drivers) a Transfer-rig conversion could carry and a bake would flatten
        (``BlenderSceneImport.scene_has_complex_animation``)."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport.scene_has_complex_animation(path)

    @staticmethod
    def find_foreign_scenes(
        root_dir: str, recursive: bool = False, extensions=None
    ) -> List[str]:
        """The foreign scenes under *root_dir* (``BlenderSceneImport.find_scenes``)."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport.find_scenes(
            root_dir, recursive=recursive, extensions=extensions
        )

    def bake_scene(self, path: str, **kwargs) -> str:
        """Bake the foreign scene (or an FBX) *path* to a cached ``.ma`` and return its
        path (``BlenderSceneImport.bake_scene``: ``via``, ``rig_mode``, ``progress``,
        ``project``, ...)."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport().bake_scene(path, **kwargs)

    def import_scene(self, path: str, **kwargs) -> list:
        """Import *path* as local data with no reference behind it
        (``BlenderSceneImport.import_scene``: a ``.blend`` converts through a headless
        Blender; a USD layer imports natively, its skins neutralized for the read).
        Returns the nodes brought in."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        return BlenderSceneImport().import_scene(path, **kwargs)

    def bake_project_root(self) -> Optional[str]:
        """The project a reference's bake goes in: the open scene's
        (``DataNodes.scene_project_root``: an unsaved scene's is the active
        project) -- what :meth:`bake_scene` defaults to."""
        try:
            return DataNodes.scene_project_root()
        except Exception:  # noqa: BLE001 - no Maya (mock tests)
            return None

    @staticmethod
    def foreign_scratch_path(path: str) -> str:
        """The deterministic scratch ``.ma`` a foreign row opens as
        (``<temp>/mtk_opened_<hash>/<stem>_<ext>.ma``: ``scene.blend`` opens as
        ``scene_blend.ma``, so the title bar and the Save-As default say what it was
        converted from, and it can never shadow a sibling ``scene.ma``; the hash of
        the source's full path keeps same-named scenes in different projects apart).
        ``ptk.ScratchTwins`` owns the naming, the age sweep and the discard."""
        return _scratch_twins().path_for(path)

    def discard_stale_scratches(self):
        """Discard every untouched scratch twin that is no longer the open scene (one
        the user saved into is kept: ``ptk.ScratchTwins.discard_except``)."""
        _scratch_twins().discard_except(self.current_scene())

    # ------------------------------------------------------------------ scene files
    def current_scene(self) -> str:
        """The open scene's path; '' when it has never been saved
        (``EnvUtils.saved_scene_path``: batch Maya's phantom ``untitled`` is none)."""
        try:
            return EnvUtils.saved_scene_path()
        except Exception:  # noqa: BLE001 - no Maya (mock tests)
            return ""

    def scene_modified(self) -> bool:
        """True if the open scene has unsaved changes (``file -q -modified``)."""
        try:
            return bool(cmds.file(q=True, modified=True))
        except Exception:  # noqa: BLE001 - no Maya (mock tests)
            return False

    def save_open_scene(self) -> Optional[bool]:
        """Flush the open scene to its own file so its unsaved edits reach disk.

        Returns True when the file on disk is up to date (nothing to save, or
        saved), and None when the scene cannot be written where it came from: never
        saved, or opened from a format Maya only reads (an ``.fbx`` or USD row keeps
        that file as its scene name, which ``EnvUtils.SCENE_SAVE_TYPES`` has no save
        type for) -- a browser then saves it under a new name instead.

        Raises:
            RuntimeError: The save failed.
        """
        path = self.current_scene()
        file_type = EnvUtils.SCENE_SAVE_TYPES.get(os.path.splitext(path)[1].lower())
        if not file_type:
            return None
        if not self.scene_modified():
            return True
        cmds.file(save=True, type=file_type)
        self.logger.info(f"Saved the open scene: {path}")
        return True

    def save_scene(self, path: str) -> str:
        """Save the open scene as *path* in Maya's own format, by its extension (an
        unknown one saves ASCII); the scene is renamed to it. Returns the path;
        raises RuntimeError on failure."""
        file_type = EnvUtils.SCENE_SAVE_TYPES.get(
            os.path.splitext(path)[1].lower(), "mayaAscii"
        )
        cmds.file(rename=path)
        cmds.file(save=True, type=file_type)
        self.logger.info(f"Saved scene to: {path}")
        return path

    def new_scene(self) -> bool:
        """Discard the current file and start an empty scene (Maya's ``file -new``).

        The 'close' counterpart of :meth:`open_scene` -- Maya has no null document,
        so closing a scene means replacing it with a fresh empty one. Callers guard
        unsaved changes first; ``force`` then skips Maya's own prompt. An untouched
        scratch twin the scene was goes with it. True on success. Mirror of
        blendertk's.
        """
        try:
            cmds.file(new=True, force=True)
        except Exception as e:  # noqa: BLE001 - reported to the caller as False
            self.logger.error(f"Failed to start a new scene: {e}")
            return False
        self.logger.info("Started a new (empty) scene.")
        self.discard_stale_scratches()
        return True

    def is_current(self, path, current=None) -> bool:
        """True if *path*'s scene is the one open: the file itself, or -- a foreign row
        -- its scratch twin (so a second Open click on either closes it). Pass
        *current* (normalized) to reuse one query across a listing. Mirror of
        blendertk's."""
        if not path:
            return False
        if current is None:
            scene = self.current_scene()
            current = os.path.normcase(os.path.normpath(scene)) if scene else ""
        if not current:
            return False
        target = self.foreign_scratch_path(path) if self.is_foreign(path) else path
        return os.path.normcase(os.path.normpath(target)) == current

    def open_scene(self, file_path: str, set_workspace: bool = True) -> bool:
        """Open a scene file, optionally setting the workspace to match the file.

        A USD row opens through mayaUsd's translator, read the way ``add_reference``
        reads it (:meth:`UsdUtils.live_read_options`) -- a stage the reader would
        crash on is refused before the session is touched (reported, False).

        A foreign (Blender) row has no Maya file to open: its bake
        (:meth:`_bake_for_reference`, baked into the SOURCE's project -- the scene
        opened next is the one that reads it) is copied to a scratch twin
        (:meth:`foreign_scratch_path`) opened as a new scene standing in for its
        source (``DataNodes.set_scene_source``) -- never the bake itself, which a save
        would write over for every scene referencing the row.

        Parameters:
            file_path (str): Path to the scene file to open.
            set_workspace (bool): Set Maya's project to the workspace containing the
                file -- for a foreign row, its SOURCE (the scratch lives in temp).

        Returns:
            (bool): True once opened; False when it was refused or a foreign row's
            bake did not come (both reported).

        Raises:
            FileNotFoundError: The scene file does not exist.
            OSError: A foreign row's scratch copy could not be written.
            RuntimeError: Maya could not open it.
        """
        self.logger.debug(f"Opening scene: {file_path}")
        workspace_source = file_path
        if self.is_foreign(file_path):
            # Baked into the SOURCE's project, not the open scene's: the scene opened
            # next is the one that reads it (the browsed folder decides an unmarked
            # source's, as it does the notes').
            home = ptk.Workspace.for_scene(
                file_path,
                session_root=getattr(self, "_current_working_dir", None) or None,
            )
            baked = self._bake_for_reference(
                file_path, project=home.root if home is not None else None
            )
            if not baked:
                return False
            try:
                scratch = _scratch_twins().create(file_path, baked)
            except OSError as e:
                raise OSError(
                    f"the working copy of its bake could not be written ({e})"
                ) from e
            file_path = scratch

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Scene file not found: {file_path}")

        read = {}
        if self._is_usd(file_path):
            try:
                read = UsdUtils.live_read_options(file_path)
            except RuntimeError as e:
                return self._refuse_live_read(file_path, e)
        elif self._is_fbx(file_path):
            # The plugin opens the ONE take the session's sticky selector names:
            # pinned to the per-file -1 (an index past the file's takes opened an
            # empty scene). A file that take does not read whole opens in part.
            gap = self._read_fbx_takes(file_path)
            if gap:
                self.logger.warning(
                    f"{os.path.basename(file_path)} ({gap}) opens with its last take "
                    "only; reference the row to read every take."
                )
        else:  # a scene that references an FBX reads it under the selector too
            FbxUtils._default_import_take(if_loaded=True)

        cmds.file(file_path, open=True, force=True, **read)
        conform = UsdUtils.stage_conform(file_path) if read else None
        if conform:  # a USD row in another unit or up axis
            roots = [
                r
                for r in UsdUtils.top_transforms(cmds.ls(assemblies=True))
                if not any(
                    cmds.camera(s, q=True, startupCamera=True)
                    for s in cmds.listRelatives(r, shapes=True, type="camera") or []
                )
            ]
            UsdUtils.conform_roots(
                roots,
                conform,
                f"{os.path.splitext(os.path.basename(file_path))[0]}_conform",
            )
        # Loading a scene that carries references resolves/applies reference edits during
        # the open, which leaves Maya's 'modified' flag set though the user made no edits;
        # that stale flag made an immediate close/reference toggle falsely prompt about
        # unsaved changes. Interactive Maya applies some edits deferred, so a second clear
        # is scheduled on idle below. A genuine user edit re-sets the flag.
        cmds.file(modified=False)
        self.logger.info(f"Opened scene: {file_path}")
        # The previous scene is gone: drop any untouched foreign scratch twin it was.
        _scratch_twins().discard_except(file_path)
        if file_path != workspace_source:
            # The scratch stands in for its source: the scene's project is the
            # source's, not the temp folder (DataNodes.scene_project_root).
            DataNodes.set_scene_source(file_path, workspace_source)

        if set_workspace:
            try:
                new_workspace = EnvUtils.find_workspace_using_path(workspace_source)
                if new_workspace:
                    current_workspace = cmds.workspace(q=True, rd=True)
                    if os.path.normcase(
                        os.path.normpath(current_workspace)
                    ) != os.path.normcase(os.path.normpath(new_workspace)):
                        cmds.workspace(new_workspace, openWorkspace=True)
                        self.logger.info(f"Set workspace to: {new_workspace}")
                else:
                    self.logger.warning(f"No workspace found for: {workspace_source}")
            except Exception as e:  # noqa: BLE001 - the open itself stands
                self.logger.error(f"Failed to set workspace: {e}")

        # Second, deferred clear: interactive Maya applies deferred reference edits (and
        # fires a browser's SceneOpened refresh) AFTER this returns, both of which re-set
        # the flag on the just-loaded scene. Lowest priority, so it runs after them.
        # No-op in batch/standalone (no idle loop), where the clear above suffices.
        try:
            cmds.evalDeferred(lambda: cmds.file(modified=False), lowestPriority=True)
        except Exception as e:  # noqa: BLE001 — best-effort; the sync clear stands
            self.logger.debug(f"Deferred modified-clear could not be scheduled: {e}")
        return True

    def _is_workspace_structure(self, folder) -> bool:
        """True if *folder* is the browsed workspace or its scenes folder, or holds
        either: the project's own structure, never one scene's folder however it is
        named, so a rename never moves it and a delete never removes it. The name test
        alone let a scene named for its folder claim it -- ``scenes_final.ma`` loose in
        the scenes root, ``proj_v01.ma`` at the root of project ``proj``."""
        workspace = self.current_working_dir
        if not workspace:
            return False
        folder = os.path.normcase(os.path.abspath(folder))
        inside = os.path.join(folder, "")
        for root in (workspace, ptk.Workspace.load(workspace).scene_dir):
            root = os.path.normcase(os.path.abspath(root))
            if root == folder or root.startswith(inside):
                return True
        return False

    def rename_scene_file(self, old_path: str, new_path: str, folder=None):
        """Rename a scene file on disk, carrying everything keyed to its old name: the
        metadata sidecar, Maya's incremental-save folder, and -- with *folder* -- the
        per-scene folder that holds it. Returns the final path, or None if the rename
        was aborted.

        Renaming the **open** scene is save-then-reopen: unsaved edits are flushed to
        the old file first (a save afterwards would just re-create the old name), and
        the renamed file is re-opened at the end, so the session never points at a
        filename that no longer exists.

        *folder* is the new name for the per-scene folder (``{name}`` in a browser's
        folder structure); it is applied only when the folder is the scene's alone:
        named for the file's base, not the project's own structure
        (:meth:`_is_workspace_structure`) and holding no other scene
        (:meth:`_holds_other_scenes`). A folder that cannot follow is reported
        (``report=``), since the scene itself was renamed. ``OSError`` from the file
        rename and ``RuntimeError`` from the open scene's save propagate; a failed
        sidecar / increments move is logged and the file rename stands.
        """
        # Only Maya's own scene types round-trip through save-then-reopen. An .fbx or
        # USD row can also be the open scene (Maya opens both through their
        # translators), but saving it would write Maya scene data over it -- those
        # rename on disk only.
        ext = os.path.splitext(old_path)[1].lower()
        is_open = ext in EnvUtils.SCENE_SAVE_TYPES and self.is_current(old_path)
        if is_open and not self.save_open_scene():
            return None

        os.rename(old_path, new_path)
        self.logger.info(f"Renamed {old_path} to {new_path}")

        old_sidecar = old_path + ".metadata.json"
        if os.path.exists(old_sidecar):
            new_sidecar = new_path + ".metadata.json"
            try:
                os.rename(old_sidecar, new_sidecar)
                self.logger.info(f"Renamed sidecar {old_sidecar} to {new_sidecar}")
            except OSError as e:
                self.logger.warning(f"Failed to rename sidecar: {e}")

        # Maya's Incremental Save keeps its versions in a folder beside the scene, keyed
        # by the scene's FILENAME. Left behind it detaches from the scene it belongs to
        # -- and a later scene that reuses the old name silently inherits that history.
        old_increments = ptk.Workspace.backup_dir(old_path)
        if os.path.isdir(old_increments):
            new_increments = ptk.Workspace.backup_dir(new_path)
            try:
                os.rename(old_increments, new_increments)
                self.logger.info(
                    f"Moved incremental saves {old_increments} to {new_increments}"
                )
            except OSError as e:
                self.logger.warning(f"Failed to move the incremental-save folder: {e}")

        final_path = new_path
        if folder:
            old_dir = os.path.dirname(old_path)
            parent_dir_name = os.path.basename(old_dir)
            old_base = os.path.splitext(os.path.basename(old_path))[0]
            # The folder is the scene's own when it is named for it -- case-
            # insensitively ("hero_v01.ma" in "Hero") -- is not the project's own
            # structure, AND holds no other scene. The file (and its increments)
            # already moved.
            if (
                old_base.lower().startswith(parent_dir_name.lower())
                and not self._is_workspace_structure(old_dir)
                and not self._holds_other_scenes(old_dir, new_path)
            ):
                new_folder_path = os.path.join(os.path.dirname(old_dir), folder)
                skipped = None
                if os.path.exists(new_folder_path) and not ptk.FileUtils.is_same_file(
                    new_folder_path, old_dir
                ):
                    skipped = f"{new_folder_path} already exists"
                else:
                    try:
                        os.rename(old_dir, new_folder_path)
                        self.logger.info(
                            f"Renamed folder {old_dir} to {new_folder_path}"
                        )
                        final_path = os.path.join(
                            new_folder_path, os.path.basename(new_path)
                        )
                    except OSError as e:
                        skipped = str(e)
                if skipped:
                    # Report it: the scene itself WAS renamed, so staying silent leaves
                    # the file under the old scene's folder with nothing to explain it.
                    self.logger.warning(f"Cannot rename folder {old_dir}: {skipped}")
                    if self._report is not None:
                        self._report(
                            "Renamed the scene, but its folder kept the old name:\n"
                            f"{skipped}"
                        )
            else:
                self.logger.debug(
                    f"Folder '{parent_dir_name}' is not '{old_base}''s alone (named "
                    "for another scene, or holds others); skipping its rename"
                )

        if is_open:
            # Re-open last, after any folder move, so the session lands on the final path.
            self.open_scene(final_path)
        return final_path

    @staticmethod
    def _discard(path: str, permanent: bool) -> Optional[bool]:
        """*path* to the trash, or -- *permanent* -- deleted. True when it is gone,
        ``None`` when the trash would not take it (it is untouched)."""
        if permanent:
            os.remove(path)
            return True
        return None if ptk.FileUtils.move_to_trash(path) is None else True

    def remove_scene_file(
        self, path: str, permanent: bool = False, use_folder: bool = False
    ) -> Optional[bool]:
        """Remove one scene with its metadata sidecar -- to the trash, or *permanent*
        -- and, with *use_folder* (``{name}`` in a browser's folder structure), its
        per-scene folder once that is empty.

        Returns:
            True when it is gone; ``None`` when the trash would not take it (nothing
            touched); False when it could not be removed (logged).
        """
        try:
            if self._discard(path, permanent) is None:
                return None
            where = (
                "Deleted" if permanent else f"Moved to the {ptk.FileUtils.trash_name()}"
            )
            self.logger.info(f"{where}: {path}")

            # The file's metadata sidecar goes with it (ptk.Metadata's
            # "<file>.metadata.json" convention), the same way, so it neither
            # outlives the scene it describes nor misses a restore of it.
            sidecar = path + ".metadata.json"
            if os.path.exists(sidecar):
                try:
                    if self._discard(sidecar, permanent) is None:
                        self.logger.warning(
                            f"Kept sidecar {sidecar}: no trash takes it."
                        )
                except OSError as e:
                    self.logger.warning(f"Could not remove sidecar {sidecar}: {e}")

            if use_folder:
                parent_dir = os.path.dirname(path)
                # The per-scene folder goes only when ALL of these hold (a permanent
                # delete cannot be taken back, and a restore from the trash puts the
                # scene back where it was):
                # 1. Ownership: the folder is named for this scene (case-insensitive:
                #    Windows filenames -- "Env_v1.ma" in folder "env" is scene-owned).
                # 2. Not the project's own structure: "scenes_final.ma" loose in a
                #    scenes root named "scenes" passes (1), and must never take the
                #    root (or the workspace) with it.
                # 3. Empty, now the scene and its sidecar are gone. Anything left keeps
                #    it -- another version, a subfolder, and every file that is not a
                #    Maya scene: the prompt named one file, so rmdir, never rmtree.
                file_base = os.path.splitext(os.path.basename(path))[0].lower()
                parent_name = os.path.basename(os.path.normpath(parent_dir)).lower()
                owns_folder = bool(parent_name) and file_base.startswith(parent_name)
                try:
                    leftovers = os.listdir(parent_dir)
                except OSError:
                    leftovers = None  # cannot inspect: leave the folder alone

                if (
                    owns_folder
                    and leftovers == []
                    and not self._is_workspace_structure(parent_dir)
                ):
                    try:
                        os.rmdir(parent_dir)
                        self.logger.info(f"Deleted empty scene folder: {parent_dir}")
                    except OSError as e:
                        self.logger.warning(
                            f"Could not remove scene folder {parent_dir}: {e}"
                        )
                else:
                    self.logger.debug(
                        f"Keeping folder '{parent_dir}'; not scene-owned, the "
                        f"project's own structure, not empty, or it could not "
                        f"be inspected (owns={owns_folder}, left={leftovers})."
                    )
        except Exception as e:  # noqa: BLE001 - reported to the caller as False
            self.logger.error(f"Delete failed for {path}: {e}")
            return False
        return True
