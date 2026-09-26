# !/usr/bin/python
# coding=utf-8
"""The pre-export validation checks -- each returns ``(passed, messages)``
and is hoisted to the earliest point its ``ptk.ExportProfile.CHECK_DEPENDENCIES``
entry allows.
"""

import os
import re
import math
import logging
from typing import Optional, Dict, Any, List

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None
import pythontk as ptk

# From this package:
from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import SceneDataSidecar
from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import HierarchyBaseline
from mayatk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _TaskChecksMixin(_TaskDataMixin):
    """The pre-export validation checks -- each returns ``(passed, messages)``
    and is hoisted to the earliest point its ``ptk.ExportProfile.CHECK_DEPENDENCIES`` entry allows."""

    _LOD_SUFFIX_REGEX = re.compile(r"_lod\d*$", re.IGNORECASE)
    _MAX_LISTED_OBJECTS = 25
    _DEFAULT_FLOOR_TOLERANCE = 0.5

    def _obj_link(
        self, node: str, action: str = "reveal", label: Optional[str] = None
    ) -> str:
        """Return a clickable log link for a Maya scene node.

        Parameters:
            node:   Full or short DAG path (the link's param, and its label
                    when *label* is omitted).
            action: ``"select"`` or ``"reveal"`` (default).
            label:  Visible text, for a report where the leaf name is not
                    enough — a same-name collision, where every link would
                    otherwise read the same word.
        """
        return self.logger.log_link(label or node.rsplit("|", 1)[-1], action, node=node)

    def _truncate_obj_entries(
        self, entries: List[str], limit: Optional[int] = None
    ) -> List[str]:
        """Cap per-object log entries with a summary tail when the list is long.

        Returns the entries unchanged when ``len(entries) <= limit``; otherwise
        returns the first ``limit`` entries followed by ``"... and N more (omitted)"``.
        """
        cap = self._MAX_LISTED_OBJECTS if limit is None else limit
        if len(entries) <= cap:
            return entries
        remaining = len(entries) - cap
        return entries[:cap] + [f"... and {remaining} more (omitted)"]

    def _texture_node_links(self, nodes: List[str]) -> str:
        """Comma-joined select links for texture *nodes* that outlive the run.

        Texture checks run between ``convert_textures``' staged rewire and its
        deferred restore, so a node they read can be one the conversion
        created -- which the restore deletes on every exit, a blocked export
        included, before anyone clicks the report (production: the size check
        listed ``ROOM_ENV_Base_color1``, a link that selected nothing). Each
        node is linked through :meth:`MatSnapshot.surviving_node` instead: the
        original node of the same slot, else its material. Nodes resolving to
        one survivor link it once; a node with none is plain text.
        """
        from mayatk.mat_utils.mat_snapshot import MatSnapshot

        snapshot = self._texture_network_snapshot
        links: Dict[str, str] = {}
        for node in sorted(nodes):
            target = MatSnapshot.surviving_node(snapshot, node) if snapshot else node
            if (target or node) not in links:
                links[target or node] = (
                    self._obj_link(target, "select") if target else node
                )
        return ", ".join(links.values())

    def _texture_size_limit_remedy(self) -> str:
        """What would bring an over-limit map under :meth:`check_texture_file_size`.

        Keyed on the Optimize Textures dial this run used (stamped by
        ``perform_export``): it is the export's only fix for an oversized map,
        and a failure right after an "Optimize" run otherwise reads as if the
        pass never ran -- without a ceiling it never resamples, by design, so
        it cannot bring a map under a byte limit. blendertk mirrors it.
        """
        if not self.run.optimize_textures:
            return (
                "Optimize Textures is OFF: an 'Optimize + Max …' ceiling "
                "downsamples the maps this export ships. Or raise this limit."
            )
        # The ceiling in pixels, 0 for none: the resolution the GLB pass takes,
        # which also reads the budget sentinel under an unbudgeted template as
        # no ceiling at all.
        ceiling = self.run.glb_max_size(logger=self.logger)
        if not ceiling:
            return (
                "Optimize Textures ran with no size ceiling, and without one the "
                "pass never resamples. Choose an 'Optimize + Max …' ceiling (or "
                "'Optimize + Template Budget' with a budgeted Textures template), "
                "or raise this limit."
            )
        return (
            f"Still over the limit with Optimize Textures clamped to {ceiling} px. "
            "Lower the ceiling, or raise this limit."
        )

    def check_geometry_lod_suffix(self) -> tuple:
        """Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.

        Returns:
            tuple: (status: bool, messages: list)

        Notes:
            - This check is informational. It returns True regardless, and lists any matches.
            - Suffix examples matched: '_LOD', '_LOD0', '_LOD1', '_LOD02', etc. (case-insensitive)
        """
        messages: List[str] = []

        matches = {}
        for obj in self._live_objects():
            # Check if geometry (has shapes)
            # Use cmds for speed
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            name = obj.split("|")[-1]
            if self._LOD_SUFFIX_REGEX.search(name):
                matches.setdefault(name, obj)

        if matches:
            items = [
                f"  - {self._obj_link(matches[n], 'reveal')}" for n in sorted(matches)
            ]
            messages.append("Geometry with LOD suffix detected (informational):")
            messages.extend(items)
            # The runner only surfaces messages from FAILING checks; this one
            # always passes, so its listing must be logged directly or the
            # check is a silent no-op. As ONE grouped record: every log record
            # is its own paragraph in the export panel, so a line per match
            # rendered the listing as N blank-line-separated sections.
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.log_group(f"LOD suffixes detected ({len(matches)})", items)

        return True, messages

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms.

        A frozen root reads identity on every channel, so the live values alone
        cannot tell "authored at identity" from "identity because someone froze
        it" — and the second case still carries a pre-freeze transform in its
        bake history that a downstream un-freeze would reinstate. Those roots
        are reported (with the transform the freeze consumed) but do NOT fail
        the check: the scene as it stands really is at identity, which is what
        the exporter needs.
        """
        log_messages = []
        box_logged = False
        frozen_box_logged = False
        tolerance = 1e-5
        has_non_default_transforms = False
        frozen_messages = []

        # self.objects contains only geometry transforms (never assemblies),
        # so we walk up each object's DAG path to find the root ancestor.
        root_groups = set()
        for obj in self.objects:
            # Long path: "|root|child|...|geo" — the root is segment [1]
            parts = obj.split("|")
            if len(parts) > 2:
                root_long = "|" + parts[1]
                root_groups.add(root_long)

        root_nodes = cmds.ls(list(root_groups), long=True) or []

        for node in root_nodes:
            if not NodeUtils.is_group(node):
                continue

            translate = cmds.getAttr(f"{node}.translate")[0]
            rotate = cmds.getAttr(f"{node}.rotate")[0]
            scale = cmds.getAttr(f"{node}.scale")[0]

            if (
                not all(abs(val) < tolerance for val in translate)
                or not all(abs(val) < tolerance for val in rotate)
                or not all(abs(val - 1) < tolerance for val in scale)
            ):
                if not box_logged:
                    log_messages.append(
                        "Root level group nodes found with non-default transforms:"
                    )
                    box_logged = True

                has_non_default_transforms = True
                link = self._obj_link(node)
                log_messages.append(
                    f"Node: {link}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
                )
                continue

            # Reads default — but a freeze is one of the ways a node gets here.
            stored = XformUtils.get_stored_transforms(node)
            if stored is not None:
                if not frozen_box_logged:
                    frozen_messages.append(
                        "Root level group nodes at default transforms because "
                        "they were FROZEN (not authored at identity):"
                    )
                    frozen_box_logged = True
                frozen_messages.append(
                    f"Node: {self._obj_link(node)}, baked Translate: "
                    f"{tuple(round(v, 6) for v in stored['translate'])}, "
                    f"baked Scale: {tuple(round(v, 6) for v in stored['scale'])}"
                )

        # Frozen roots are reported after any real failures, and never change
        # the verdict — the exported scene is at identity either way.
        log_messages.extend(frozen_messages)

        if has_non_default_transforms:
            return (
                False,
                log_messages,
            )  # Failed, log the nodes with non-default transforms

        return True, log_messages  # All checks passed, no non-default transforms

    def check_material_compatibility(self, template) -> tuple:
        """Every mask map matches the chosen texture template (post-conversion).

        The check half of the Texture Template combobox: armed only when a
        template is selected, alongside :meth:`convert_textures`. Checks run
        after the task phase, so this validates the **converted** state -- it
        fails only for a mask map the conversion could not bring to the
        template (unreadable source, missing inputs, an unsupported material
        type), naming the residuals rather than blocking the fix. The override
        button skips it like any other check.

        The judgement is pythontk's (``MeshConvert.sidecar_foreign_packings``
        -> ``MapFactory.foreign_packings``), read off the same sidecar the GLB
        conversion will carry and keyed by the registry workflow the combobox
        named -- so no engine name or channel layout is spelled out here and
        blendertk's twin cannot drift from it.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        from mayatk.env_utils.scene_state import SceneState

        log_messages = []
        try:
            sections = SceneState.read(self._live_objects())
        except Exception:  # noqa: BLE001 — a read failure must not block an export
            self.logger.warning("Material compatibility check skipped.", exc_info=True)
            return True, log_messages

        foreign = ptk.MeshConvert.sidecar_foreign_packings(
            {"sections": sections}, workflow=template
        )
        if not foreign:
            return True, log_messages

        # Count header then indented offenders, as check_path_length does.
        log_messages.append(
            f"{len(foreign)} mask map(s) do not match the {template!r} template "
            "after conversion:"
        )
        log_messages.extend(
            f"  - {map_type}: {os.path.basename(path)}"
            for path, map_type in sorted(foreign.items())
        )
        log_messages.append(
            "See the Map Updater log above for why these did not convert, or "
            "set Textures back to 'As Authored' to ship them as they are."
        )
        return False, log_messages

    def check_texture_optimization(self, template) -> tuple:
        """Every shipping texture is optimized for its map type (post-task).

        The check half of the Optimize Textures checkbox: armed alongside
        :meth:`optimize_textures` by the same setting, judged through the same
        :meth:`_assess_optimization`, and — because checks run after tasks —
        validating the **staged/written** state the export will actually
        read. It FAILS only for a texture the task should have optimized but
        could not (a per-texture failure), naming the residuals rather than
        blocking the fix.

        Everything the pass deliberately does not touch is reported without
        failing: tiled/UDIM sets (measured via their 1001 tile — the task is
        single-file), and the active template's ``DeliveryBudget`` advisories
        — advisory means REPORTED, not resampled, and never a blocked export.
        With a size ceiling set the resize IS part of the pass, so
        an over-size residual the task could not shrink fails here like any
        other unoptimized map. Those notes are logged directly (the runner only surfaces
        messages from failing checks). Unreadable or missing files are
        :meth:`check_valid_paths`' domain and are skipped here.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        tpl = template if isinstance(template, str) else None

        offenders: List[str] = []
        notes: List[str] = []
        advisories: Dict[str, List[str]] = {}  # warning text -> texture names
        # include_tiled: the task cannot TOUCH a tiled set, but the gate
        # should still measure it (via its 1001 tile) so an unoptimized one
        # is at least reported instead of slipping past the scan.
        for _key, entry in sorted(
            self._export_texture_sources(include_tiled=True).items()
        ):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict is None:
                continue
            name = os.path.basename(entry["path"])
            if verdict["needed"]:
                links = self._texture_node_links(entry["nodes"])
                line = f"  - {links} -> {name}: {'; '.join(verdict['reasons'])}"
                if entry["tiled"]:
                    notes.append(line + " (tiled set — not auto-optimized)")
                else:
                    offenders.append(line)
            for warning in verdict["warnings"]:
                advisories.setdefault(warning, []).append(name)

        # One line per advisory, not one per texture: a 4K set over a 2K budget
        # printed the same sentence 49 times (measured 2026-09-13), burying the
        # tiled-set notes it shared the group with.
        limit = 8
        for warning, names in advisories.items():
            more = len(names) - limit
            notes.append(
                f"  - {warning} -- {len(names)} texture(s): {', '.join(names[:limit])}"
                + (f" (+{more} more)" if more > 0 else "")
            )

        # Advisory tier: budget notes and untouchable residuals inform, never
        # gate. Logged directly — the runner only surfaces messages from
        # FAILING checks, so returning them on a pass would be a silent no-op.
        if notes and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(f"Texture optimization notes ({len(notes)})", notes)

        if offenders:
            pass_desc = f"the {tpl!r} template" if tpl else "their map type"
            header = [
                f"{len(offenders)} texture(s) are not optimized for "
                f"{pass_desc} after the optimization task:"
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    def check_path_length(self, max_length: Optional[int] = None) -> tuple:
        """Check that no export path exceeds the OS path-length limit.

        Covers the export destination and every texture feeding the export
        materials, measured in ABSOLUTE form — that is the string the
        filesystem, the FBX plug-in and the receiving pipeline all see.  An
        over-long path fails late and opaquely (a write that reports success
        but produced nothing, a texture the plug-in silently can't embed), and
        a path that fits here still breaks on the next machine: the Windows
        260-character cap applies unless that machine opted into long paths.

        Sidecars written beside the export (``.scene_data.json``, ``.fbm``
        folders) are longer than the export path itself, so leave headroom by
        setting a budget below the OS limit.

        Parameters:
            max_length: Maximum allowed path length — the spin box's value.
                ``None`` uses this OS's limit
                (``ptk.FileUtils.path_length_limit``); ``0`` (the spin box's
                "OFF" position) or ``"OFF"`` disables the check.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if max_length is not None:
            if not max_length or str(max_length).upper() == "OFF":
                return True, []
            try:
                limit = int(max_length)
            except (TypeError, ValueError):
                self.logger.warning(
                    f"Invalid max path length '{max_length}'. Skipping length check."
                )
                return True, []
        else:
            limit = ptk.FileUtils.path_length_limit()

        # A relative texture path must be measured the way MAYA resolves it —
        # against the project root. os.path.abspath would resolve it against
        # the process CWD, which is only the same directory when the
        # set_workspace task happened to run.
        project_root = cmds.workspace(query=True, rootDirectory=True) or ""

        def absolute(path: str) -> str:
            expanded = os.path.expandvars(path)
            if not os.path.isabs(expanded):
                # With no project open there is no better base than the CWD
                # (abspath's own) — resolve here either way, so the length the
                # message reports is the length the verdict was made on.
                expanded = (
                    os.path.join(project_root, expanded)
                    if project_root
                    else os.path.abspath(expanded)
                )
            return os.path.normpath(expanded).replace("\\", "/")

        offenders = []

        export_path = self.export_path
        if export_path:
            resolved = absolute(export_path)
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                offenders.append(
                    f"  - export path ({len(resolved)} chars) -> {resolved}"
                )

        seen_paths = set()
        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue
            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)

            # Measure what the pipeline will see: the stored path resolved the
            # way Maya resolves it, falling back to a project-root join when it
            # doesn't resolve (a missing texture is check_valid_paths' domain,
            # but its path length is still this check's).
            resolved = absolute(MatUtils.resolve_path(path, search=False) or path)
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                link = self._texture_node_links([node])
                offenders.append(f"  - {link} ({len(resolved)} chars) -> {resolved}")

        if offenders:
            header = [
                f"{len(offenders)} path(s) exceed the {limit}-character limit:",
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    def _deliverable_paths(self) -> List[str]:
        """The files this run will write, in the order it writes them.

        A GLB-only run writes its FBX to a throwaway temp dir, so only the
        ``.glb`` is a destination there; every other mode writes the export
        path itself, plus a sibling ``.glb`` when one is produced.
        """
        export_path = self.export_path or ""
        if not export_path:
            return []
        glb_only = bool(self.run.glb_only)
        paths = [] if glb_only else [export_path]
        if glb_only or self.run.create_glb:
            paths.append(os.path.splitext(export_path)[0] + ".glb")
        return paths

    def check_output_writable(self) -> tuple:
        """Check that every file this run will write can actually be replaced.

        Windows refuses to delete a file, or rename onto it, while another
        process holds it open -- so a deliverable someone is previewing fails
        the write with ``[WinError 32]``. The cost is not the failure but its
        TIMING: the write is the last thing an export does, so a file handle
        that was there all along discards the entire pipeline's work, and
        (for GLB-only) a finished conversion with it.

        Which is why this check declares no task dependencies: it is decidable
        before the first mutation, the scheduler hoists it ahead of everything,
        and a locked destination stops the run in milliseconds with the name of
        the process to close rather than in minutes with an errno.

        A path that does not exist yet cannot be held, and passes.

        Returns:
            tuple: (status: bool, messages: list)
        """
        # Resolved rather than called directly: mayatk and pythontk update
        # independently, and a Maya running an older pythontk would raise
        # AttributeError HERE -- aborting the very export this check exists to
        # protect. Measured in mayapy against the installed pythontk.
        describe = getattr(ptk.FileUtils, "describe_lock", None)
        if describe is None:
            self.logger.warning(
                "Output-writability check skipped: this pythontk predates "
                "FileUtils.describe_lock. A destination held open by another "
                "process will not be caught until the write fails."
            )
            return True, []
        blocked = [(path, describe(path)) for path in self._deliverable_paths()]
        blocked = [(path, why) for path, why in blocked if why]
        if not blocked:
            return True, []
        return False, [
            f"{len(blocked)} destination file(s) cannot be replaced:",
        ] + [
            f"  - {os.path.basename(path)} is {why} -> {path}" for path, why in blocked
        ] + [
            "Close whatever holds the file (a viewer, the WebXR preview, an "
            "engine import) and re-run.",
        ]

    def check_valid_paths(self) -> tuple:
        """Check that every export texture and scene reference resolves on disk
        — the way Maya resolves it AND the way the FBX plugin will locate it
        at write time.

        Texture scope is the ``file`` nodes feeding the export materials
        (``_get_export_file_nodes``), not every ``file`` node in the scene.
        Scene-wide scanning flagged maps that never ship: the Arnold skydome's
        HDR (already dropped from the export set by ``exclude_hdr``) and the
        orphaned file nodes left behind when ``reassign_duplicate_materials``
        deletes a duplicate shader — the file nodes outlive the shader they fed.

        Maya-side resolution goes through ``MatUtils.resolve_path(search=False)``:
        env vars and ``workspace(expandName=...)`` — i.e. exactly how Maya
        itself resolves the stored path — plus ``<UDIM>`` expansion, which the
        previous hand-rolled lookup lacked entirely (every tiled texture read
        as missing).  ``search=False`` is load-bearing: the default hunt would
        match any same-named file under ``sourceimages``, so a node pointing at
        a stale directory would pass validation and still ship broken.

        A Maya-resolvable path is then re-probed the way the **fbxmaya plugin**
        locates media when it writes: plain OS resolution — absolute paths
        as-is, relative paths against the process CWD; the workspace is never
        consulted (probe-proven 2026-08-04).  Without this second gate the
        check passed workspace-relative paths that the plugin then reported as
        "The following texture(s) will not be embedded" after the export — or,
        in batch (or with embedding off), shipped silently broken with no
        console warning at all.  The ``set_workspace`` task aligns the CWD
        with the workspace root, so in the default pipeline both probes agree.

        Three texture verdicts, because they have three different remedies:
        *Missing Texture* (Maya resolves nothing — repoint the node),
        *Unresolved tile/frame pattern* (same, but the stored value carries a
        token, so the useful question is whether the tile exists rather than
        whether the filename does) and *Not locatable at write time* (Maya
        resolves it through the workspace, the plugin's CWD resolution will
        not — enable Auto Set Workspace).  The token split belongs to the
        FIRST gate: anything reaching the second has already resolved, so a
        token there means only that the CWD is wrong.

        Entries are grouped by path so a texture shared by several file nodes
        logs once.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        all_valid = True

        # 1. Texture paths — scoped to the maps that will actually ship.
        missing_textures: Dict[str, List[str]] = {}
        fbx_unlocatable: Dict[str, List[str]] = {}
        unresolved_tokens: Dict[str, List[str]] = {}
        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                # Some empty file nodes might exist?
                continue

            # Tile/frame tokens collapse through MatUtils' single token table
            # rather than a local <UDIM>-only replace: a <uvtile>, <u>_<v>,
            # <f> or <frame> path failed that narrower probe and was then
            # reported as an FBX working-directory problem, so the remedy
            # offered ("Auto Set Workspace") could not fix it. A token-free
            # path probes back unchanged, so the comparison IS the "did this
            # carry a token" test -- no second match. (``None`` means a glob
            # token found nothing, which is also a token.)
            expanded = os.path.expandvars(path)
            probe = MatUtils.probe_texture_path(expanded)
            carries_token = probe != expanded

            if not MatUtils.resolve_path(path, search=False):
                # Neither the env-expanded nor the workspace-expanded form
                # names a file, so no working directory can rescue it. The
                # token split happens HERE, at the resolution gate, and not at
                # the FBX gate below: a tokened path that reaches the FBX gate
                # has by definition already resolved somewhere, so classifying
                # it there sent every RELATIVE tiled set -- the normal storage
                # form in a Maya project, resolvable only through the
                # workspace -- to "no workspace setting fixes it", negating
                # the one remedy that does.
                if carries_token:
                    unresolved_tokens.setdefault(path, []).append(node)
                else:
                    missing_textures.setdefault(path, []).append(node)
                continue

            # Maya resolves it — now probe it the way the FBX plugin will at
            # write time (os.path.abspath resolves relative paths against the
            # CWD, never the workspace).
            if probe is None or not os.path.isfile(os.path.abspath(probe)):
                fbx_unlocatable.setdefault(path, []).append(node)

        if missing_textures:
            all_valid = False
            entries = []
            for path in sorted(missing_textures):
                links = self._texture_node_links(missing_textures[path])
                entries.append(f"Missing Texture: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        if fbx_unlocatable:
            all_valid = False
            log_messages.append(
                f"{len(fbx_unlocatable)} texture path(s) resolve in Maya but the "
                "FBX plug-in will not locate them at write time (it resolves "
                "relative paths against the process working directory, not the "
                "workspace) — the export would end with 'The following "
                "texture(s) will not be embedded'. Enable the 'Auto Set "
                "Workspace' task to align the working directory."
            )
            entries = []
            for path in sorted(fbx_unlocatable):
                links = self._texture_node_links(fbx_unlocatable[path])
                entries.append(f"Not locatable at write time: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        if unresolved_tokens:
            all_valid = False
            log_messages.append(
                f"{len(unresolved_tokens)} texture path(s) carry a tile/frame "
                "token (<UDIM>, <uvtile>, <u>_<v>, <f>, <frame>) that matches no "
                "file on disk. This is NOT the FBX working-directory case — the "
                "pattern itself resolves to nothing, so no workspace setting "
                "fixes it. Check the tile/frame actually exists, or repoint the "
                "file node."
            )
            entries = []
            for path in sorted(unresolved_tokens):
                links = self._texture_node_links(unresolved_tokens[path])
                entries.append(f"Unresolved tile/frame pattern: {links} -> {path}")
            log_messages.extend(self._truncate_obj_entries(entries))

        # 2. Reference Paths
        references = cmds.ls(references=True) or []
        for ref in references:
            try:
                # withoutCopyNumber=True gets actual file path
                path = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
                if path:
                    expanded_path = os.path.expandvars(path)
                    if not os.path.exists(expanded_path):
                        all_valid = False
                        link = self._obj_link(ref, "select")
                        log_messages.append(f"Missing Reference: {link} -> {path}")
            except Exception:
                continue

        # 3. Lightmap dependencies -- baked maps the markers name. They live
        # outside every file node (the marker records a basename and the
        # folder the bake was COMMITTED from), so the two gates above never
        # see them, and a scene migrated with its textures ships its GLB
        # unlit and its FBX manifest pointing at nothing -- with one converter
        # warning nobody reads. Resolved the way the GLB applier resolves
        # them (hint, then the live texture folders, then the sourceimages
        # walk); a map found only by search still ships -- the conversion is
        # handed the folder it was found in -- but says so, since the FBX
        # manifest's hint is stale until the Auto-Resolve task rewrites it.
        missing_lightmaps = []
        stale_lightmaps = []
        for dep in self._lightmap_dependencies():
            if not dep["path"]:
                missing_lightmaps.append(dep)
            elif dep["found_by"] != "hint":
                stale_lightmaps.append(dep)

        if missing_lightmaps:
            all_valid = False
            log_messages.append(
                f"{len(missing_lightmaps)} lightmap(s) the bake markers name are "
                "not on disk. The GLB would ship unlit and the FBX manifest would "
                "point at nothing. Relocate them (Texture Path Editor ▸ Find & "
                "Copy Textures, lightmaps included) or revert the bake (Lightmap "
                "Baker ▸ Revert)."
            )
            entries = []
            for dep in missing_lightmaps:
                links = ", ".join(
                    self._obj_link(o, "select") for o in sorted(dep["objects"])
                )
                where = f"{dep['dir']}/{dep['map']}" if dep["dir"] else dep["map"]
                note = f" ({dep['note']})" if dep.get("note") else ""
                entries.append(f"Missing Lightmap: {links} -> {where}{note}")
            log_messages.extend(self._truncate_obj_entries(entries))

        for dep in stale_lightmaps:
            log_messages.append(
                f"Lightmap {dep['map']}: the recorded folder "
                f"{dep['dir'] or '<none>'} no longer holds it; found at "
                f"{dep['path']} (shipped from there; enable the Resolve Invalid "
                "Texture Paths task to rewrite the marker)."
            )

        if all_valid:
            log_messages.append("All checked paths exist on disk.")

        return all_valid, log_messages

    def check_texture_file_size(self, max_size_mb: Optional[float] = 16.0) -> tuple:
        """Check that no export texture exceeds a maximum on-disk file size.

        Oversized source textures bloat the exported asset and usually signal an
        un-downsized authoring map (e.g. an 8K master) that shouldn't ship to a
        game engine.  Scoped to the textures feeding the export materials, so it
        only flags maps that will actually travel with the FBX.

        Measures what the deliverable carries. A GLB-only export ships no scene
        map -- its GLB pass resizes and re-encodes every one -- so the check
        passes there, and hands its limit to the post-write
        ``glb_image_bytes`` gate (:meth:`verify_deliverables`), which measures
        the images the GLB actually holds. For FBX + GLB the failure names the
        FBX as the file carrying the maps.

        Measured after the texture tasks (``CHECK_DEPENDENCIES``), so on the
        staged copies the write reads. One file reached through several paths
        -- a staged conversion's byte-identical copy beside the scene's own
        map -- is one offender with every consumer linked
        (:meth:`_texture_node_links`, which also keeps links off nodes the
        staged restore deletes). A failure ends with the remedy for the
        Optimize Textures dial the run used (:meth:`_texture_size_limit_remedy`).

        Parameters:
            max_size_mb: Maximum allowed texture size in megabytes — the spin
                box's value, or any numeric-ish string (e.g. ``"16"``).
                ``None``, ``0`` (the spin box's "OFF" position), ``""``, or
                ``"OFF"`` disables the check (returns pass); a non-numeric
                value logs a warning and skips.  Defaults to 16 MB.

        Returns:
            tuple: (status: bool, messages: list)
        """
        import filecmp

        limit_bytes = ptk.ExportProfile.texture_size_limit_bytes(max_size_mb)
        if limit_bytes is None:
            if max_size_mb and str(max_size_mb).strip().upper() != "OFF":
                self.logger.warning(
                    f"Invalid max texture size '{max_size_mb}'. Skipping size check."
                )
            return True, []
        limit_mb = limit_bytes / (1024 * 1024)
        if self.run.glb_only:
            # A GLB-only export ships no scene map: the GLB pass resizes and
            # re-encodes each one (a production 57 MB PNG shipped as a 3.12 MB
            # KTX2), so these bytes reach nothing a consumer receives.
            self.logger.info(
                "Texture size check skipped: a GLB-only export ships its own "
                "re-encoded copies; 'Verify The Written File' measures those "
                f"against {limit_mb:g} MB."
            )
            return True, []

        # Offenders grouped by the FILE they measured, not by path: a staged
        # conversion ships an untouched map as a byte-identical copy under the
        # same name, while the scene's own file can still feed another export
        # material -- one map reached this check through two paths and was
        # listed twice. Only a same-name, same-size pair is ever compared.
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        sizes: Dict[str, int] = {}

        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            # Resolve to the on-disk file via MatUtils.resolve_path so
            # project-relative paths still resolve — the default-on
            # convert_to_relative_paths task runs before checks and rewrites
            # texture paths to workspace-relative form.  Missing files are the
            # domain of check_valid_paths, so an unresolved path is skipped.
            # search=False for the same reason that check applies it: the
            # basename hunt would size-probe a same-named file the node does
            # not actually point at.
            resolved = MatUtils.resolve_path(path, search=False)
            if not resolved:
                continue

            # Collapse the tile/frame token to a concrete file for the size
            # probe (resolve_path deliberately returns a path that still
            # carries it). Through the shared table, not a <UDIM>-only
            # replace: a <uvtile>/<u>_<v>/<f>/<frame> path did not collapse,
            # so os.path.isfile failed and the texture was skipped -- an
            # oversized frame sequence escaped the size limit entirely.
            probe = MatUtils.probe_texture_path(resolved)
            if probe is None:  # frame pattern with nothing on disk
                continue
            if probe not in sizes:
                sizes[probe] = os.path.getsize(probe) if os.path.isfile(probe) else -1
            size = sizes[probe]
            if size <= limit_bytes:  # a probe that is not a file is -1
                continue
            name = os.path.basename(probe)
            same_key = groups.setdefault((name.lower(), size), [])
            for group in same_key:
                if group["path"] == probe or filecmp.cmp(
                    group["path"], probe, shallow=False
                ):
                    group["nodes"].append(node)
                    break
            else:
                same_key.append(
                    {"path": probe, "name": name, "size": size, "nodes": [node]}
                )

        offenders = [
            f"  - {self._texture_node_links(g['nodes'])} -> {g['name']} "
            f"({g['size'] / (1024 * 1024):.2f} MB)"
            for same_key in groups.values()
            for g in same_key
        ]
        if offenders:
            # FBX + GLB: only the FBX carries these files; naming it keeps the
            # failure from reading as the GLB's.
            carrier = " the FBX carries" if self.run.create_glb else ""
            header = [
                f"{len(offenders)} texture(s){carrier} exceed the {limit_mb:g} MB "
                "limit" + (" (the GLB re-encodes its own copies):" if carrier else ":")
            ]
            return False, (
                header
                + self._truncate_obj_entries(offenders)
                + [self._texture_size_limit_remedy()]
            )

        return True, []

    # Scratch/mangled name signatures — the diagnostics repair
    # (SceneDiagnostics.repair_mangled_names) owns the definition; this
    # check and that repair must always agree on what "mangled" means.
    MANGLED_NAME_RE = SceneDiagnostics.MANGLED_NAME_RE

    def check_mangled_names(self) -> tuple:
        """Check the export set (including shapes) for scratch/mangled names.

        Catches names no tool should ever ship: uninstance ``__uninst_tmp``
        scratch tokens, Rizom ``__RZTMP`` round-trip suffixes, ``FBXASC###``
        import escapes, and underscore runs.  Instanced shapes make one bad
        node fan out across every instance path in the exported hierarchy
        (and its scene_data.json sidecar), so a single offender is worth
        failing on.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        nodes = self._live_objects()
        if not nodes:  # listRelatives([]) would fall back to the selection
            return True, log_messages
        nodes += cmds.listRelatives(nodes, allDescendents=True, fullPath=True) or []

        offenders = []
        seen = set()
        for node in nodes:
            if node in seen:
                continue
            seen.add(node)
            leaf = node.split("|")[-1].split(":")[-1]
            if self.MANGLED_NAME_RE.search(leaf):
                offenders.append(leaf)

        if not offenders:
            return True, log_messages

        log_messages.append(f"{len(offenders)} node(s) carry scratch/mangled names:")
        for leaf in offenders[:20]:
            link = self._obj_link(leaf, "select")
            log_messages.append(f"  - {link}")
        if len(offenders) > 20:
            log_messages.append(f"  … and {len(offenders) - 20} more")
        log_messages.append(
            "Repair via the 'Fix Mangled Names' task "
            "(or SceneDiagnostics.repair_mangled_names)."
        )
        return False, log_messages

    #: Duplicate Names — how wide the short-name scan casts, narrowest first;
    #: each tier is a superset of the one above it.  Keys are the combo's
    #: labels, values the scope token :meth:`check_duplicate_names` resolves
    #: (``None`` = OFF, which the panel's falsy filter drops before the check
    #: is ever dispatched).
    #:
    #: The tiers answer "whose name is load-bearing downstream?".  FBX keeps
    #: the hierarchy, so a short-name collision only bites where a consumer
    #: resolves nodes by their FLAT name: sockets, skeleton bones, and the
    #: animation/metadata that is re-bound by node name.  Plain groups collide
    #: harmlessly all the time, which is why they arrive only under the
    #: explicitly strictest option and never in a middle tier.
    _duplicate_name_options: Dict[str, Any] = {
        "OFF": None,
        "Locators": "locators",
        "Locators & Joints": "joints",
        "Connected & Animated": "connected",
        "All Export Objects": "all",
    }

    #: Scope token -> its combo label, for the failure report's header.
    _duplicate_name_labels: Dict[str, str] = {
        v: k for k, v in _duplicate_name_options.items() if v
    }

    #: Transform channels whose INCOMING connections make a node's name
    #: load-bearing: a constraint, an anim curve, a driver, an expression or an
    #: IK solver writes here, and whatever rebuilds that plumbing downstream
    #: re-resolves it by name.  Compounds AND their children: a point
    #: constraint drives ``.translateX/Y/Z`` while a float3 output drives
    #: ``.translate`` itself, and ``listConnections`` on a compound does not
    #: report its children's connections.
    _CONNECTED_CHANNELS = (
        "translate",
        "translateX",
        "translateY",
        "translateZ",
        "rotate",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scale",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    )

    @staticmethod
    def _ambiguous_leaf_names(nodes: List[str]) -> List[str]:
        """*nodes* whose leaf name is shared with another entry in the list.

        A node nobody shares a name with cannot be half of a collision, and
        every scope tier draws from the export set — so resolving a tier
        against this pool instead of the whole set is exactly equivalent, and
        turns the per-node connection probe from "every object in the export"
        into "the handful that were already ambiguous".  Pure string work.
        """
        leaves = [n.rsplit("|", 1)[-1] for n in nodes]
        counts: Dict[str, int] = {}
        for leaf in leaves:
            counts[leaf] = counts.get(leaf, 0) + 1
        return [n for n, leaf in zip(nodes, leaves) if counts[leaf] > 1]

    def _duplicate_name_scope(self, scope: str) -> List[str]:
        """The export-set nodes *scope* puts in front of the duplicate scan.

        *scope* is validated by :meth:`check_duplicate_names`; anything it did
        not recognize never reaches here (the widest branch is the fallthrough,
        so an unvalidated typo would silently scan a NARROWER tier and pass).
        """
        objects = self._live_objects()
        if not objects:  # listRelatives([]) would fall back to the selection
            return []
        objects = self._ambiguous_leaf_names(objects)
        if not objects:
            return []
        if scope == "all":
            # Transforms only (joints included — cmds.ls matches derived
            # types): the 'all' export scope puts SHAPES in the set too, and
            # two cubes both named CRATE also carry two CRATEShape nodes, so
            # reporting shapes doubles every row with a name the FBX consumer
            # never resolves against.  Shape-name hygiene is conform_shape_names'.
            return cmds.ls(objects, type="transform", long=True) or []

        # Locator TRANSFORMS: the set holds transforms and the type lives on
        # the shape, so this is a shape query with a hop back up to the parent.
        locator_shapes = (
            cmds.listRelatives(objects, shapes=True, type="locator", fullPath=True)
            or []
        )
        nodes = set(
            cmds.listRelatives(locator_shapes, parent=True, fullPath=True) or []
            if locator_shapes
            else []
        )
        if scope == "locators":
            return sorted(nodes)

        nodes.update(cmds.ls(objects, type="joint", long=True) or [])
        if scope == "joints":
            return sorted(nodes)

        nodes.update(self._connected_transforms(objects))
        return sorted(nodes)

    def _connected_transforms(self, objects: List[str]) -> List[str]:
        """*objects* carrying an incoming connection on a transform channel."""
        connected = []
        for obj in objects:
            # Shapes reach the set too ('all' scope lists geometry); they have
            # none of these channels, and their transform is what gets named.
            if not cmds.objectType(obj, isAType="transform"):
                continue
            plugs = [f"{obj}.{attr}" for attr in self._CONNECTED_CHANNELS]
            if cmds.listConnections(
                plugs, source=True, destination=False, skipConversionNodes=True
            ):
                connected.append(obj)
        return connected

    def check_duplicate_names(self, scope: Optional[str] = None) -> tuple:
        """Check for duplicate short names within the export set.

        Parameters:
            scope: One of :attr:`_duplicate_name_options`' values —
                ``"locators"``, ``"joints"``, ``"connected"`` or ``"all"``.
                Falsy (or ``"OFF"``) skips the check; ``True`` is read as
                ``"locators"``, the scope the pre-dial checkbox had.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages: List[str] = []
        if not scope or str(scope).upper() == "OFF":
            return True, log_messages
        scope = "locators" if scope is True else str(scope).lower()
        if scope not in self._duplicate_name_labels:
            # Loud, not a fallthrough: the resolver's widest branch is its
            # default, so a typo'd scope would quietly scan a NARROWER tier
            # than the caller asked for and PASS the export on that basis.
            valid = ", ".join(sorted(self._duplicate_name_labels))
            return False, [f"Unknown duplicate-name scope {scope!r}. Valid: {valid}."]

        nodes = self._duplicate_name_scope(scope)
        if not nodes:
            return True, log_messages

        seen: Dict[str, str] = {}
        collisions: Dict[str, List[str]] = {}
        for node in nodes:
            name = node.rsplit("|", 1)[-1]
            if name in seen:
                collisions.setdefault(name, [seen[name]]).append(node)
            else:
                seen[name] = node

        if not collisions:
            return True, log_messages

        label = self._duplicate_name_labels.get(scope, scope)
        log_messages.append(
            f"{len(collisions)} duplicate short name(s) in scope '{label}':"
        )
        entries = [
            f"  - {name} (x{len(paths)}): "
            # FULL path as the link label: every link in a collision row would
            # otherwise read the same leaf name and tell the user nothing
            # about WHICH pair collided.
            + ", ".join(self._obj_link(p, "reveal", label=p) for p in paths)
            for name, paths in sorted(collisions.items())
        ]
        return False, log_messages + self._truncate_obj_entries(entries)

    def check_duplicate_materials(self) -> tuple:
        """Check if any duplicate materials are present in the scene."""
        log_messages = []

        materials = self._get_all_materials()
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(materials)

        if duplicate_mapping:
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    dup_link = self._obj_link(str(duplicate), "select")
                    orig_link = self._obj_link(str(original), "select")
                    log_messages.append(f"Duplicate: {dup_link} -> {orig_link}")
            return False, log_messages  # Failed, log the duplicates

        return True, log_messages  # All checks passed, no duplicates found

    #: Shading groups that mean "nobody assigned this one". ``initialShadingGroup``
    #: is Maya's own fallback; a shape wired to neither it nor anything else is the
    #: same story one step earlier.
    DEFAULT_SHADING_GROUPS = ("initialShadingGroup", "initialParticleSE")

    def check_default_materials(self) -> tuple:
        """Geometry shipping on Maya's fallback shader instead of an authored one.

        A mesh nobody assigned a material to still exports: Maya hands it
        ``lambert1`` through ``initialShadingGroup``, FBX carries it, and
        FBX2glTF writes it out as ``Default_Material`` -- an untextured grey
        with no base colour and **no normal map**. It is invisible in the
        exporter's other checks because nothing about it is missing or
        malformed; the object simply renders wrong, and only in the deliverable.

        Measured on the production assembly this was added for: 54 of its 55
        GLB materials carried their normal map and the 55th was
        ``Default_Material`` -- found only by auditing the shipped file, which
        is exactly the work this check exists to make unnecessary. Two separate
        causes put geometry there, and only the first is the obvious one; see
        the intermediate-shape paragraph below for the one that actually
        shipped.

        Scoped to the export set (``_live_objects``) rather than the scene: an
        unassigned mesh that never ships is not this export's problem. Reports
        per SHAPE, because a per-face assignment can leave part of one mesh on
        the default while the rest is authored.

        ``descend=True`` is load-bearing. ``_live_objects`` returns the export
        ROOTS re-resolved, not the hierarchy under them, so a walk of direct
        shapes finds nothing at all on a scene exported by its top groups --
        measured against the assembly this was written for, where the check
        passed clean while the deliverable carried the very material it is
        looking for.

        Intermediate shapes are read too, and this is the case that actually
        shipped. An orig shape belongs to the mesh it deforms and never exports
        -- unless it has been parented under a transform that is not that mesh,
        where it reaches the FBX carrying no shading group (an orig shape is in
        none) and WINS over that transform's real shape. Measured:
        ``|STATIC|DA2|INSTRUMENTS|prop533`` shipped its 20-vertex orig cage on
        ``Default_Material`` while its DA1 twin shipped the authored 45-vertex
        mesh -- the wrong geometry, untextured, and invisible to every other
        check because the transform's own assignment is perfectly correct.

        Having several parents is NOT the test: instancing a deformed mesh
        legitimately shares its orig across every instance, and on the scene
        this was written against 5 of the 6 multi-parent orig shapes were
        exactly that (one at 276 parents). The test is how many DISTINCT real
        shapes those parents carry -- one means ordinary instancing, more than
        one means the orig is riding geometry it does not belong to. That
        separated the single genuine offender from the five false ones.
        """
        offenders = []
        for shape in NodeUtils.get_shapes(
            self._live_objects(), descend=True, type="mesh", no_intermediate=False
        ):
            if NodeUtils.is_intermediate(shape):
                parents = (
                    cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                )
                if len(parents) < 2:
                    continue  # an ordinary orig shape never leaves Maya
                # By UUID, not by path: an instanced shape is ONE node with many
                # paths, and every path is the same geometry.
                real = {
                    uuid
                    for parent in parents
                    for sibling in (
                        cmds.listRelatives(
                            parent, shapes=True, fullPath=True, noIntermediate=True
                        )
                        or []
                    )
                    for uuid in cmds.ls(sibling, uuid=True) or []
                }
                if len(real) > 1:
                    offenders.append(
                        (shape, f"orig shape riding {len(real)} different meshes")
                    )
                continue
            engines = set(cmds.listConnections(shape, type="shadingEngine") or [])
            if not engines:
                offenders.append((shape, "no shading group"))
            elif engines & set(self.DEFAULT_SHADING_GROUPS):
                # Named so a partially-assigned mesh reads as such rather than
                # as a wholly unassigned one.
                offenders.append(
                    (
                        shape,
                        "default shader" + (" (partial)" if len(engines) > 1 else ""),
                    )
                )

        if offenders:
            return False, [
                f"Default material ({reason}): {self._obj_link(shape)}"
                for shape, reason in offenders
            ]
        return True, []

    def check_referenced_objects(self) -> tuple:
        """Check if any referenced objects are present in the scene."""
        log_messages = []
        # Check all referenced objects in the scene, not just the selected objects
        referenced_objects = cmds.ls(references=True) or []

        if referenced_objects:
            for ref in referenced_objects:
                link = self._obj_link(ref, "select")
                log_messages.append(f"Referenced Object: {link}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not target_framerate or str(target_framerate).upper() == "OFF":
            return True, []

        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True, []

        current_time_unit = cmds.currentUnit(query=True, time=True)
        if current_time_unit != target_framerate:
            return False, [
                f"Framerate mismatch: Current time unit is {current_time_unit}, expected {target_framerate}."
            ]

        return True, []

    def check_objects_below_floor(
        self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE
    ) -> tuple:
        """Fail when surface geometry reaches deeper than *tolerance* below Y=0.

        Parameters:
            tolerance: How far beneath the plane geometry may reach, in scene
                units, before failing -- the panel's **Max Depth Below Floor**
                spin box. ``0`` (the box's OFF), ``None`` or ``False`` disable
                the check; a strict check is a small depth, not zero. ``True``
                -- a template or caller from the checkbox era -- means the
                default depth rather than ``float(True)``.
        """
        offenders: List[str] = []

        # ``True`` (checkbox enabled) is a bool, not a real distance — honor the
        # documented default instead of float(True) == 1.0.
        if tolerance is True:
            tolerance = self._DEFAULT_FLOOR_TOLERANCE
        if not tolerance or float(tolerance) <= 0.0:
            return True, []  # OFF
        tolerance = float(tolerance)
        limit = -tolerance

        geometry_types = NodeUtils.SURFACE_TYPES
        for obj in self._live_objects():
            # Surface geometry only — the check is named "geometry below
            # floor", but the old any-shape guard also failed control curves
            # and locators dipping under Y=0 in 'selected'/'all' modes.
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue
            if not any(cmds.nodeType(s) in geometry_types for s in shapes):
                continue

            bbox = cmds.xform(obj, query=True, ws=True, bb=True)
            if not bbox:
                continue

            ymin = bbox[1]
            if ymin < limit:
                link = self._obj_link(obj)
                offenders.append(
                    f"Object: {link} - Below Floor: True (Y-min: {ymin:.3f})"
                )

        if offenders:
            header = [
                f"{len(offenders)} object(s) below floor "
                f"(deeper than {tolerance:.3f} unit{'s' if tolerance != 1 else ''})"
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []  # All checks passed, no objects below the floor

    def check_overlapping_duplicate_mesh(self) -> tuple:
        """Check for duplicate overlapping geometry among the export objects.

        Returns:
            tuple: (status: bool, messages: list)
        """
        duplicates = EditUtils.get_overlapping_duplicates(objects=self._live_objects())
        if duplicates:
            messages = [
                f"Overlapping duplicate object: {self._obj_link(obj)}"
                for obj in duplicates
            ]
            return False, messages  # Failed, duplicates found
        return True, []  # Passed, no duplicates

    def check_hidden_geometry(self) -> tuple:
        """Check for geometry that will ship in the FBX while hidden.

        Beyond the plain ``.visibility`` flag this also reads DISPLAY-LAYER
        hiding (the old check's blind spot — layer-hidden geometry shipped
        unflagged in every mode).  Objects whose visibility has an incoming
        connection are deliberately NOT flagged: the 'visible' export mode
        includes animated-visibility objects on purpose (the animation is
        baked and ships), so flagging them made the check fail exactly the
        content that mode exists to carry.
        """
        hidden_objects = []
        geometry_types = NodeUtils.SURFACE_TYPES

        for obj in self._live_objects():
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue

            # Check if any shape is actually geometry
            is_geometry = False
            for shape in shapes:
                if cmds.nodeType(shape) in geometry_types:
                    is_geometry = True
                    break

            if not is_geometry:
                continue

            # Animated/driven visibility is intentional export content, not
            # hidden geometry — skip regardless of the current-frame value.
            if cmds.listConnections(
                f"{obj}.visibility", source=True, destination=False
            ):
                continue

            layer_hidden = False
            for layer in set(cmds.listConnections(obj, type="displayLayer") or []):
                if layer == "defaultLayer":
                    continue
                try:
                    if not cmds.getAttr(f"{layer}.visibility"):
                        layer_hidden = True
                        break
                except ValueError:
                    continue

            if layer_hidden:
                hidden_objects.append((obj, "display layer"))
            elif not cmds.getAttr(f"{obj}.visibility"):
                hidden_objects.append((obj, "visibility off"))

        if hidden_objects:
            return False, [
                f"Hidden geometry detected ({reason}): {self._obj_link(obj)}"
                for obj, reason in hidden_objects
            ]
        return True, []

    def check_uv_snapshots(self) -> tuple:
        """Report the auto-unwrap backup UV sets left on the export meshes.

        ``UvUtils.snapshot_uv_sets`` backs a mesh's UVs up into a ``_uv_snap_*``
        set for the length of one unwrap; a run that dies mid-mesh leaves it for
        good, and the FBX writes it as a real UV set -- the second one is
        ``TEXCOORD_1``, the lightmap channel, which a consumer cannot tell from a
        lightmap UV (measured: 183 of 757 meshes on a production scene).
        Reported, never removed: an export does not edit what it reads, and the
        next ``UvUtils.auto_unwrap`` of a mesh sweeps an earlier session's.
        """
        found = UvUtils.find_uv_snapshots(self._live_objects())
        if not found:
            return True, []
        messages = []
        for shape, _current, snapshot in found:
            owner = (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[
                0
            ]
            messages.append(
                f"Leftover UV snapshot '{snapshot}' on {self._obj_link(owner)} -- "
                "it ships as a real UV set."
            )
        messages.append(
            "Remove them with mtk.UvUtils.discard_uv_snapshot("
            "mtk.UvUtils.find_uv_snapshots(objects)), or re-run Auto Unwrap."
        )
        return False, messages

    def check_untied_keyframes(self) -> tuple:
        """Check if there are any untied keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping untied keyframe check.")
            return True, []

        log_messages = []
        untied_keyframes_found = False

        # Optimization: Get all connections at once to avoid N calls to listConnections
        # connections=True returns [source, dest, source, dest...]
        # plugs=True returns [obj.plug, curve.output, ...]
        connections = (
            cmds.listConnections(
                self._live_objects(),
                type="animCurve",
                source=True,
                destination=False,
                connections=True,
                plugs=True,
            )
            or []
        )

        # Drop unitless (set-driven-key) curves: their first/last "keys" are
        # driver values, not frames, so comparing them against the object's
        # time range false-positives on every SDK-rigged object.
        pairs = list(zip(connections[::2], connections[1::2]))
        time_curves = set(
            cmds.ls(
                [c.split(".")[0] for _, c in pairs],
                type=list(AnimUtils.TIME_CURVE_TYPES),
            )
            or []
        )

        # Parse into a dict: obj_name -> set(curves)
        obj_curves = {}
        for obj_plug, curve_plug in pairs:
            obj_name = obj_plug.split(".")[0]  # e.g. "pCube1.translateX"
            curve_name = curve_plug.split(".")[0]  # e.g. "animCurveTL1.output"
            if curve_name not in time_curves:
                continue

            if obj_name not in obj_curves:
                obj_curves[obj_name] = set()
            obj_curves[obj_name].add(curve_name)

        for obj, curves in obj_curves.items():
            if not curves:
                continue

            # Get start/end for each curve
            curve_data = []
            min_start = float("inf")
            max_end = float("-inf")

            for curve in curves:
                # findKeyframe on a curve is fast
                s = cmds.findKeyframe(curve, which="first")
                e = cmds.findKeyframe(curve, which="last")
                curve_data.append((curve, s, e))

                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e

            # Check for mismatches
            obj_link = self._obj_link(obj)
            for curve, s, e in curve_data:
                if s > min_start:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (Start {s} != {min_start})"
                    )
                if e < max_end:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (End {e} != {max_end})"
                    )

        if untied_keyframes_found:
            return False, log_messages  # Failed, log untied keyframes

        return True, log_messages  # All checks passed, no untied keyframes

    def check_sheared_local_transforms(self, tolerance: float = 0.05) -> tuple:
        """Fail when a node's LOCAL matrix is sheared past *tolerance*.

        FBX and glTF store an animated node as translation/rotation/scale.
        A sheared local matrix has no TRS form, so the exporter drops the
        shear -- and in a chain the residual compounds joint by joint.

        A clean scene reaches this state without any authored shear. A
        spline-IK squash/stretch rig gives every joint the SAME non-uniform
        world scale ``S`` (an ``offsetParentMatrix`` cancels the cascade), so
        each world matrix is perfectly orthogonal. The LOCAL matrix between
        two of them is ``S . R_child . R_parent^-1 . S^-1`` -- a rotation
        conjugated by a non-uniform scale, which is sheared whenever the two
        joints differ in orientation. Checking world matrices finds nothing;
        the loss is entirely in the local ones.

        Measured on a production wire-loom rig: a chain carrying 47%
        stretch reached 0.307 local shear and landed its last joint 7.5 cm
        from Maya's answer -- four times its true distance from the plug it is
        anchored to. A sibling chain at 11% stretch (0.104 shear) stayed
        within 0.5 cm, and one at 0.2% (0.003) was exact.

        Parameters:
            tolerance: Maximum |cos(angle)| between normalised local axes.
                0 is perfectly orthogonal. Falsy skips the check.

        Returns:
            tuple: (status: bool, messages: list)
        """

        if tolerance is True:
            # The pre-dial QCheckBox delivers its raw value; True == 1.0
            # would pass every scene (see flatten_sheared_chains).
            tolerance = 0.05
        log_messages: List[str] = []
        if not tolerance:
            return True, log_messages

        offenders = self._sheared_offenders_after_flatten(tolerance)
        if not offenders:
            return True, log_messages

        # Report per rig group, not per node: a sheared chain lists every joint,
        # and a 20-deep DAG path per joint buries the one fact that matters --
        # which rig is bad, and by how much.
        worst_by_group: Dict[str, tuple] = {}
        counts_by_group: Dict[str, int] = {}
        for node, skew in offenders.items():
            parts = node.split("|")
            group = next(
                (p for p in reversed(parts[:-1]) if p.endswith("_GRP")),
                parts[1] if len(parts) > 1 else node,
            )
            counts_by_group[group] = counts_by_group.get(group, 0) + 1
            current = worst_by_group.get(group)
            if current is None or skew > current[0]:
                worst_by_group[group] = (skew, node.rsplit("|", 1)[-1])

        log_messages.append(
            f"{len(offenders)} node(s) across {len(worst_by_group)} group(s) "
            f"have a local transform FBX/glTF cannot represent (> "
            f"{tolerance:g}): a sheared local has no TRS form, and a "
            "segment-scale-compensated joint under a scaling parent "
            "recomposes with the parent scale the rig cancels. Either way "
            "the error compounds along a chain:"
        )
        for group, (skew, leaf) in sorted(
            worst_by_group.items(), key=lambda kv: -kv[1][0]
        ):
            log_messages.append(
                f"    {skew:.4f}  {group}  "
                f"({counts_by_group[group]} node(s), worst at {leaf})"
            )
        log_messages.append(
            "    Usual cause: squash/stretch scale on a joint chain "
            "(sheared locals, or segmentScaleCompensate the formats lack). "
            "The Flatten Sheared Chains task re-anchors these "
            "automatically; otherwise reduce the stretch at the source."
        )
        return False, log_messages

    def check_floating_point_keys(self) -> tuple:
        """Check if there are any floating point keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping floating point key check.")
            return True, []

        log_messages = []
        offenders = []

        # Optimization: Iterate curves instead of objects
        # This is much faster than querying keyframes per object
        all_curves = (
            cmds.listConnections(
                self._live_objects(), type="animCurve", source=True, destination=False
            )
            or []
        )
        # Time-driven curves only: a set-driven key's inbetweens (driver
        # values like 0.25/0.5) would otherwise read as "floating point keys".
        all_curves = (
            cmds.ls(list(set(all_curves)), type=list(AnimUtils.TIME_CURVE_TYPES)) or []
        )

        for curve in all_curves:
            times = cmds.keyframe(curve, query=True, timeChange=True)
            if not times:
                continue

            for t in times:
                if not math.isclose(t, round(t), abs_tol=1e-4):
                    # Find object name
                    conn = cmds.listConnections(
                        curve, plugs=True, destination=True, source=False
                    )
                    obj_name = conn[0].split(".")[0] if conn else curve
                    offenders.append(f"{obj_name} (frame {t:.3f})")
                    break

        # Remove duplicates
        offenders = sorted(list(set(offenders)))

        if offenders:
            log_messages.append("Floating point keys found on:")
            for offender in offenders:
                # offender format: "objName (frame N.NNN)" — link the object part
                name = offender.split(" (frame")[0]
                link = self._obj_link(name, "select")
                detail = offender[len(name) :]
                log_messages.append(f"  - {link}{detail}")
            return False, log_messages

        return True, log_messages

    def check_hierarchy_vs_existing_fbx(self) -> tuple:
        """Check export objects against the hierarchy manifest of the previous export.

        Compares namespace-stripped DAG paths of the current export objects
        against the scene's own hierarchy baseline (``HierarchyBaseline``:
        recorded on the scene at every export, scoped to the roots exporting).
        A baseline another scene file recorded -- the source of a Save As
        copy -- is set aside with a warning; where its own record holds nothing
        of what this deliverable ships, the scene takes what the deliverable
        last shipped (its sidecar), one deliverable at a time.  Detects
        missing or extra nodes that would indicate accidental structural
        changes.  A mismatch is stashed for the post-export sidecar write
        (``hierarchy.last_diff``) and its full report goes to a temp artifact
        linked from the log — never into the export folder.
        """
        self._hierarchy_check_ran = True
        self._hierarchy_last_diff = None

        export_path = self.export_path
        if not export_path:
            return True, []

        messages = []
        current_paths = self._build_full_hierarchy_set()
        roots = ptk.HierarchyBaseline.top_level(current_paths)

        # Where the scene's own record holds nothing of what THIS deliverable
        # ships -- no record yet, one a Save As copy carries from its source, or
        # one kept for other deliverables -- it takes what this one last shipped.
        # Asked first: the adoption replaces the record it would name.
        inherited = HierarchyBaseline.inherited_from()
        adopted = HierarchyBaseline.adopt_sidecar(export_path, **self._sidecar_kwargs())
        set_aside = inherited is not None and not adopted
        if adopted:
            messages.append(
                "Adopted this deliverable's on-disk hierarchy baseline into the "
                "scene; the baseline now follows the scene rather than the "
                "output name."
            )
        elif set_aside:
            # Nothing to diff against, and the user must SEE why: a PASSING
            # check's messages never reach them, so log it directly.
            source = (
                f"by '{inherited}', which is still on disk -- this scene is a "
                "copy of it"
                if inherited
                else "before baselines named their scene (or while unsaved), so "
                "a Save As source's cannot be told from this scene's"
            )
            message = (
                f"Hierarchy baseline set aside: it was recorded {source}. This "
                "export records the scene's own."
            )
            self.logger.warning(message)
            messages.append(message)

        if HierarchyBaseline.is_unreadable():
            # The baseline is lost either way, but the user must SEE that this
            # export went structurally unchecked rather than have a fresh
            # baseline written silently over the one that broke. A PASSING
            # check's messages never reach the user, so log it directly.
            message = (
                "The scene's hierarchy baseline is unreadable — the hierarchy "
                "check was skipped. A fresh baseline will be recorded after "
                "this export."
            )
            self.logger.warning(message)
            return True, [message]

        match, missing, extra, new_scope = HierarchyBaseline.compare(
            current_paths, roots
        )

        if new_scope:
            # Nothing recorded under these roots: a first export of this scope
            # has nothing to be diffed against, exactly as a missing manifest
            # had nothing to be diffed against before. Recorded after the export.
            # Said out loud when the deliverable ALREADY exists -- that is the
            # case where "passed" would otherwise read as "checked and clean"
            # rather than "nothing to check it against yet" (the sidecar-era
            # check said the same thing about a missing manifest). A baseline
            # set aside above has said so already.
            if not set_aside and os.path.exists(export_path):
                messages.append(
                    "No hierarchy baseline yet for what this export ships. "
                    "One will be recorded on the scene after this export."
                )
            return True, messages

        if match:
            SceneDataSidecar.clean_stale_diff(export_path, **self._sidecar_kwargs())
            return True, messages

        # Detect reparenting patterns for a cleaner summary
        reparented = SceneDataSidecar.detect_reparenting(missing, extra)

        # Stash for the post-export sidecar write: if the user proceeds,
        # the manifest records the diff they accepted.  Tagged with the
        # export path so a cancelled export's diff can never attach to a
        # different asset exported later in the same session.
        self._hierarchy_last_diff = {
            "export_path": export_path,
            "missing": missing,
            "extra": extra,
            "reparented": reparented,
        }

        diff_path = self._write_temp_diff_report(
            export_path, missing, extra, reparented, **self._sidecar_kwargs()
        )

        if reparented:
            for root, new_parent, count in reparented:
                messages.append(
                    f"Reparenting detected: '{root}' moved under '{new_parent}' "
                    f"({count} node(s) affected)"
                )
            # Report any remaining missing/extra not explained by reparenting
            explained_missing = set()
            explained_extra = set()
            for root, new_parent, _ in reparented:
                for p in missing:
                    if p.split("|")[0] == root:
                        explained_missing.add(p)
                        explained_extra.add(f"{new_parent}|{p}")
                explained_extra.add(new_parent)
            remaining_missing = [p for p in missing if p not in explained_missing]
            remaining_extra = [p for p in extra if p not in explained_extra]
        else:
            remaining_missing = missing
            remaining_extra = extra

        if remaining_missing:
            top_missing = ptk.HierarchyBaseline.top_level(remaining_missing)
            messages.append(
                f"{len(remaining_missing)} node(s) in previous export but missing now "
                f"({len(top_missing)} top-level):"
            )
            for p in top_missing[:20]:
                messages.append(f"  − {p}")
            if len(top_missing) > 20:
                messages.append(f"  … and {len(top_missing) - 20} more")

        if remaining_extra:
            top_extra = ptk.HierarchyBaseline.top_level(remaining_extra)
            messages.append(
                f"{len(remaining_extra)} new node(s) not in previous export "
                f"({len(top_extra)} top-level):"
            )
            for p in top_extra[:20]:
                messages.append(f"  + {p}")
            if len(top_extra) > 20:
                messages.append(f"  … and {len(top_extra) - 20} more")

        if diff_path:
            link = self.logger.log_link(
                "Open full diff report", "open", filepath=diff_path
            )
            messages.append(link)

        return False, messages
