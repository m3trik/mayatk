# !/usr/bin/python
# coding=utf-8
"""Reference Manager engine — Maya file references, assemblies and workspaces.

:class:`ReferenceManager` (over :class:`~mayatk.env_utils.workspace_manager.WorkspaceManager`)
lists, adds, removes and converts scene references; :class:`AssemblyManager`
handles assembly definitions. The panel's controller and slots live beside it
(``reference_manager_controller.py``, ``reference_manager_slots.py``).
"""

import os
import re
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.usd import UsdUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager


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

    def remove(self):
        cmds.file(removeReference=True, referenceNode=self._ref_node)

    def load(self):
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
        try:
            if not os.path.exists(file_path):
                cmds.warning(f"File does not exist: {file_path}")
                return None

            assembly_name = f"{namespace}_assembly"
            assembly_node = cmds.assembly(name=assembly_name, type="assemblyDefinition")

            cmds.assembly(
                assembly_node, edit=True, createRepresentation="Scene", input=file_path
            )
            representations = cmds.assembly(
                assembly_node, query=True, listRepresentations=True
            )
            return representations[0] if representations else None
        except Exception:
            cmds.warning(f"Failed to create assembly definition for {file_path}")
            return None

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
        for ref in cls.current_references():
            namespace = ref.namespace
            file_path = ref.path

            rep_name = cls.create_assembly_definition(namespace, file_path)
            if rep_name:
                assembly_name = f"{namespace}_assembly"
                if cls.set_active_representation(assembly_name, rep_name):
                    # Optionally remove the original reference after conversion
                    ref.remove()
                else:
                    cmds.warning(
                        f"Failed to set active representation for {assembly_name}"
                    )
            else:
                cmds.warning(f"Failed to create assembly definition for {file_path}")


class _ReferenceManagerInternal(object):
    """Internal helpers for ReferenceManager."""

    @staticmethod
    def _increments_dir(path):
        """Maya's Incremental Save folder for *path* — ``<scene dir>/incrementalSave/<filename>``
        (may not exist). Mirrors Maya's own ``incrementalSaveProcessPath.mel``, which roots the
        folder at the scene's directory and names it after the scene's full filename.
        """
        return os.path.join(
            os.path.dirname(path), "incrementalSave", os.path.basename(path)
        )

    @classmethod
    def _holds_other_scenes(cls, folder, scene_path) -> bool:
        """True if *folder* holds a Maya scene, at any depth, other than *scene_path*
        and its own incremental saves -- a folder that is then not that scene's alone
        to move with it: a folder shared by versions, or the scenes root itself.

        Also True when *folder* cannot be read in full: leave what cannot be
        inspected alone, as Delete does. A folder that does not exist holds nothing.
        """
        scene = os.path.normcase(os.path.normpath(scene_path))
        own_increments = os.path.normcase(
            os.path.normpath(cls._increments_dir(scene_path))
        )

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
    def _display_name(path: str, hide_extension: bool, hide_suffix: str) -> str:
        """The table label for *path* — its basename, less the extension and/or a
        TRAILING ``hide_suffix``.

        End-anchored on the stem, never a substring pass: the suffix field holds a
        naming token (``_LOC``), and removing it wherever it appears rewrites the
        middle of a name — ``ITA_LOCKHANDLE.ma`` listed as ``ITAKHANDLE``. The
        extension is split off first so the token is still at the end when the
        column is showing it, and ``endswith`` matches the filter above it
        (``chk_filter_suffix``), so a row shown for its suffix hides that same one.
        """
        stem, ext = os.path.splitext(os.path.basename(path))
        if hide_suffix and stem.endswith(hide_suffix):
            stem = stem[: -len(hide_suffix)]
        return stem if hide_extension else f"{stem}{ext}"

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
    """Core Maya scene reference management functionality.

    Features:
    - Add/remove references with namespace management
    - Import references into the scene
    - Update references from source files
    - Convert references to assemblies

    This class provides the core Maya reference functionality without any UI dependencies.
    For UI integration, use ReferenceManagerController and ReferenceManagerSlots.
    """

    # Widen the workspace-file scan to every natively referenceable type -- FBX through the
    # FBX plugin, every USD spelling through mayaUsd's translator. The panel's Include Types
    # row filters this cached superset, so toggling a type never re-scans disk.
    SCENE_FILE_TYPES = ("*.ma", "*.mb", "*.fbx") + tuple(
        f"*{ext}" for ext in ptk.USD_EXTENSIONS
    )

    def __init__(self, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self._filter_text = ""
        self._filter_enabled = True
        self.prefilter_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")

    @property
    def current_references(self):
        """Get the current scene references.
        Returns a list of _FileRef objects.
        """
        return _ReferenceManagerInternal._list_file_refs()

    def _matches_prefilter_regex(self, filename):
        """Check if a file is an auto-save file based on its name."""
        return bool(self.prefilter_regex.match(filename))

    def _extract_strip_patterns(self, filter_text: str, delimiter=(",", ";")) -> list:
        """Extract the core patterns to strip from wildcard filter text.

        For example:
        - '*_v001*' -> ['_v001']
        - 'character_*' -> ['character_']
        - '*' -> []
        - '*_module.ma;JET*' -> ['_module.ma', 'JET']
        - 'test_*_rig' -> ['test_'] (takes the longest contiguous part)

        Parameters:
            filter_text (str): The filter text possibly containing multiple patterns.
            delimiter (str or tuple): Delimiter(s) used to split patterns.

        Returns:
            list: List of core patterns to strip from filenames.
        """
        import re

        if not filter_text:
            return []

        # Split by delimiters first
        if isinstance(delimiter, tuple):
            pattern = "|".join(re.escape(d) for d in delimiter)
            patterns = [p.strip() for p in re.split(pattern, filter_text) if p.strip()]
        elif delimiter in filter_text:
            patterns = [p.strip() for p in filter_text.split(delimiter) if p.strip()]
        else:
            patterns = [filter_text]

        strip_patterns = []
        for pattern in patterns:
            # If pattern is just wildcards, skip
            if pattern.replace("*", "").replace("?", "") == "":
                continue

            # Remove leading wildcards
            while pattern.startswith("*") or pattern.startswith("?"):
                pattern = pattern[1:]

            # Remove trailing wildcards
            while pattern.endswith("*") or pattern.endswith("?"):
                pattern = pattern[:-1]

            # If there are still wildcards in the middle, take the longest contiguous part
            if "*" in pattern or "?" in pattern:
                parts = [part for part in pattern.replace("?", "*").split("*") if part]
                if parts:
                    pattern = max(parts, key=len)
                else:
                    pattern = ""

            if pattern:
                strip_patterns.append(pattern)

        return strip_patterns

    @staticmethod
    def _text_matches_filter(
        text: str, filter_patterns: list, ignore_case: bool = True
    ) -> bool:
        """Check if text matches ANY of the pre-split filter patterns via fnmatch.

        Parameters:
            text: The string to test (e.g. a filename).
            filter_patterns: Already-split list of glob patterns.
            ignore_case: Case-insensitive matching.

        Returns:
            True if the text matches at least one pattern.
        """
        from fnmatch import fnmatchcase

        if not text or not filter_patterns:
            return False
        t = text.lower() if ignore_case else text
        for pattern in filter_patterns:
            p = pattern.lower() if ignore_case else pattern
            if fnmatchcase(t, p):
                return True
        return False

    @staticmethod
    def _matches_notes_filter(
        notes_text: str, filter_text: str, ignore_case: bool = True
    ) -> bool:
        """Check if notes/comments text matches the filter patterns.

        Notes are often comma or semicolon-delimited (e.g. "Layout, Speedrun"),
        so the filter is checked against the full notes string as well as each
        individual delimited segment.

        Parameters:
            notes_text: The notes/comments string from the file metadata.
            filter_text: The filter string, possibly with wildcards and delimiters.
            ignore_case: Whether to match case-insensitively.

        Returns:
            True if any filter pattern matches the notes text.
        """
        from fnmatch import fnmatchcase

        if not notes_text or not filter_text:
            return False

        # Split filter_text into individual patterns (same delimiters as filter_list)
        filter_patterns = [filter_text]
        for delim in (",", ";"):
            expanded = []
            for p in filter_patterns:
                expanded.extend(s.strip() for s in p.split(delim) if s.strip())
            filter_patterns = expanded

        # Split notes into individual segments for matching
        note_segments = [notes_text]
        for delim in (",", ";"):
            expanded = []
            for seg in note_segments:
                expanded.extend(s.strip() for s in seg.split(delim) if s.strip())
            note_segments = expanded

        # Match: each filter pattern against the full notes string AND each segment
        candidates = [notes_text] + note_segments
        for pattern in filter_patterns:
            p = pattern.lower() if ignore_case else pattern
            for candidate in candidates:
                c = candidate.lower() if ignore_case else candidate
                if fnmatchcase(c, p):
                    return True
        return False

    @staticmethod
    def sanitize_namespace(namespace: str) -> str:
        """Sanitize the namespace by replacing or removing illegal characters."""
        return EnvUtils.sanitize_namespace(namespace)

    def add_reference(self, namespace: str, file_path: str) -> bool:
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

        # Normalize the file path to ensure consistent comparison
        normalized_file_path = os.path.normcase(os.path.normpath(file_path))

        # Check if the file is already referenced
        for ref in self.current_references:
            if os.path.normcase(os.path.normpath(ref.path)) == normalized_file_path:
                return True  # Exit the method if the file is already referenced

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

        # Sanitize the namespace to ensure it contains only valid characters
        sanitized_namespace = self.sanitize_namespace(namespace)

        try:
            cmds.file(file_path, reference=True, namespace=sanitized_namespace, **read)
            # Validate that a reference node was actually created
            rn = cmds.file(file_path, q=True, referenceNode=True)
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
        return False, the caller's result for it. A hook: the panel's controller also
        puts *error* in front of the user."""
        cmds.warning(str(error))
        return False

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
        """Update all references to reflect the latest changes from the original files."""
        for ref in self.current_references:
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
                ref.remove()
            except RuntimeError as e:
                self.logger.warning(
                    f"Failed to remove reference '{ref._ref_node}': {e}"
                )
                failed.append(ref)
                continue
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
