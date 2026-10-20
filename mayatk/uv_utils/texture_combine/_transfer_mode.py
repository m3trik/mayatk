# !/usr/bin/python
# coding=utf-8
"""The Texture Combine panel's Transfer mode: UVs or textures, source -> targets.

The standalone home of what was the tentacle UV panel's Transfer option box
(Maya). One tool, two transfers that are ALTERNATIVES: the source's UV layout
onto the targets (``UvUtils.transfer_uvs`` -- exact for identical topology,
sampled by proximity otherwise), or the source's textures re-mapped into each
target's OWN layout (:class:`~mayatk.TextureTransfer`: exact texel
correspondence, no rays, no bleed). They do not compose -- the texture pass
keeps the target's UV set, so a source layout copied alongside its maps would
land in a set nothing references. *Auto* defers the pick to run time.

:class:`_TransferMode` is composed by
:class:`~mayatk.uv_utils.texture_combine.texture_combine_slots.TextureCombineSlots`
(which owns the widgets' slot names and the shared Output / Material rows) --
a controller, not a mixin.
"""

import html
import os
from typing import List, Tuple

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.uv_utils.texture_transfer import TextureTransfer


class _TransferMode:
    """Transfer mode's decisions and its run, over the panel's widgets."""

    TRANSFER_AUTO = "auto"
    TRANSFER_TEXTURES = "textures"
    TRANSFER_UVS = "uvs"
    SOURCE_AUTO = "auto"

    #: Maya's words for the controls (Blender's twin says "UV Map").
    TERMS = {
        "first": "First Selected Mesh",
        "first_who": "the first-selected object",
        "last": "All But Last Selected",
        "last_who": "the last-selected mesh",
        "pick_first": "<b>Select the source first (a mesh or a group), then the target(s).</b>",
        "pick_last": "<b>Select the source mesh(es) first, then the target last.</b>",
    }

    def __init__(self, slots):
        self.slots = slots
        self.sb = slots.sb
        self.sources: List[str] = []
        self.similarity = None
        self.select_action = self.clear_action = None

    @property
    def ui(self):
        return self.slots.ui

    # ------------------------------------------------------------ widgets
    def init_transfer(self, widget) -> None:
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Transfer",
                body="What travels from the source to the targets -- one or the "
                "other, since the texture pass deliberately keeps the target's "
                "own layout.",
                bullets=[
                    "<b>Auto</b> -- decided per run: Textures when any source "
                    "material carries a texture map (or, with <b>Include "
                    "Lightmaps</b> on, the source carries a committed lightmap), "
                    "UV Set when none do.",
                    "<b>Textures</b> -- re-map the source's textures into each "
                    "target's OWN layout by exact texel correspondence: a "
                    "repacked atlas, a material consolidation, or another UV set "
                    "on the same mesh. No rays, no cage, no bleed.",
                    "<b>UV Set</b> -- copy the source's current UV set onto each "
                    "target. Exact for identical topology, sampled by proximity "
                    "otherwise.",
                ],
                notes=["Textures need identical topology; UV sets do not."],
            )
        )
        for label, data in (
            ("Transfer: Auto", self.TRANSFER_AUTO),
            ("Transfer: Textures", self.TRANSFER_TEXTURES),
            ("Transfer: UV Set", self.TRANSFER_UVS),
        ):
            widget.addItem(label, data)
        widget.currentIndexChanged.connect(lambda *_: self.sync())

    def init_source(self, widget) -> None:
        t = self.TERMS
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Source",
                body="Which selected meshes the UVs / textures come FROM; the rest "
                "are <b>targets</b>.",
                bullets=[
                    "<b>Auto</b> -- read from the selection: a mesh combined from "
                    "all the others is the target; a selection the stored meshes "
                    "match is their target; one mesh moves between its own UV "
                    f"sets; otherwise {t['first_who']} is the source.",
                    f"<b>{t['first']}</b> -- {t['first_who']} is the source (a "
                    "group: its meshes, paired by name); <b>Targets</b> picks the rest.",
                    f"<b>{t['last']}</b> -- {t['last_who']} is the target and "
                    "every other selected mesh a source.",
                    "<b>Stored Meshes</b> -- the meshes captured with the <b>+</b> "
                    "icon, paired to the selected targets by name (then order).",
                    "<b>UV Sets On Same Mesh</b> -- textures only: read through "
                    "the Source UV Set, written for the Target UV Set.",
                ],
            )
        )
        for label, data in (
            ("Source: Auto", self.SOURCE_AUTO),
            (f"Source: {t['first']}", "first"),
            (f"Source: {t['last']}", "last"),
            ("Source: Stored Meshes", "stored"),
            ("Source: UV Sets On Same Mesh", "uvset"),
        ):
            widget.addItem(label, data)
        box = widget.option_box
        capture = box.add_action(
            callback=self.capture_source,
            icon="add",
            tooltip="Store the selected meshes as the source meshes.",
        )
        self.sb.tooltip.bind(capture.widget, self.source_tooltip)
        self.select_action = box.add_action(
            callback=self.select_source,
            icon="select",
            tooltip="Select the stored source meshes. Enabled while something is stored.",
        )
        self.clear_action = box.add_action(
            callback=self.clear_source,
            icon="clear",
            tooltip="Clear the stored source meshes (the geometry is untouched).",
        )
        widget.currentIndexChanged.connect(lambda *_: self.sync())

    def init_targets(self, widget) -> None:
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Targets",
                body="Which meshes receive the transfer when the source is the "
                f"<b>{self.TERMS['first']}</b>.",
                bullets=[
                    "<b>Rest of Selection</b> -- every other selected object, in "
                    "selection order.",
                    "<b>Similar in Selection</b> -- the selected objects "
                    "geometrically similar to the source.",
                    "<b>Similar in Scene</b> -- every similar mesh in the scene.",
                ],
                notes=[
                    "The Similar scopes find their targets by transferring UVs, "
                    "so they need <b>Transfer: UV Set</b>; their threshold is "
                    "this row's option box."
                ],
            )
        )
        for label, data in (
            ("Targets: Rest of Selection", "order"),
            ("Targets: Similar in Selection", "selection"),
            ("Targets: Similar in Scene", "scene"),
        ):
            widget.addItem(label, data)
        menu = widget.option_box.menu
        menu.setTitle("Similar Targets")
        self.similarity = menu.add(
            "QDoubleSpinBox",
            setObjectName="d_tt_similarity",
            setPrefix="Similarity: ",
            setValue=0.9,
            setMinimum=0.0,
            setMaximum=1.0,
            setSingleStep=0.05,
            setToolTip=self.sb.tooltip.fmt(
                title="Similarity",
                body="The minimum score (0-1) a mesh must reach to receive UVs, "
                "scored on bounding-box volume and vertex count.",
            ),
        )
        widget.currentIndexChanged.connect(lambda *_: self.sync())

    def init_resolution(self, widget) -> None:
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Resolution",
                body="Output size per target material.",
                bullets=["<b>Auto</b> -- the largest source map feeding it."],
            )
        )
        widget.addItem("Resolution: Auto", 0)
        for n in (512, 1024, 2048, 4096, 8192):
            widget.addItem(f"Resolution: {n}", n)

    def init_uv_sets(self, src, dst) -> None:
        src.setToolTip(
            self.sb.tooltip.fmt(
                title="Source UV Set",
                body="The UV set the textures are READ through. Blank = <b>Auto</b>.",
                bullets=[
                    "<b>Mesh sources</b> -- each source mesh's current UV set.",
                    "<b>UV Sets On Same Mesh</b> -- the set the mesh's textures are "
                    "uvLink-ed to: the layout the maps were painted for.",
                ],
            )
        )
        dst.setToolTip(
            self.sb.tooltip.fmt(
                title="Target UV Set",
                body="The UV set the maps are WRITTEN for. Blank = <b>Auto</b>.",
                bullets=[
                    "<b>Mesh sources</b> -- each target mesh's current UV set.",
                    "<b>UV Sets On Same Mesh</b> -- the first set other than the "
                    "source, so a two-set mesh needs neither named.",
                ],
            )
        )

    def init_lightmaps(self, widget) -> None:
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Include Lightmaps",
                body="Also carry each source mesh's committed lightmap (the "
                "Lightmap Baker's) onto its target, bound per object.",
                bullets=[
                    "<b>Same lightmap layout</b> -- bound to the source's own map "
                    "and atlas rect; nothing is written.",
                    "<b>Different layout</b> -- resampled into the target's "
                    "lightmap UV set and written as <i>&lt;name&gt;_Lightmap.exr</i>.",
                ],
                notes=["Mesh sources only: on one mesh the lightmap is untouched."],
            )
        )
        widget.toggled.connect(lambda *_: self.sync())

    # ------------------------------------------------------------ sources
    @staticmethod
    def meshes(objects) -> List[str]:
        """Mesh transforms under *objects* (groups expand; order kept)."""
        out = []
        for o in objects:
            for shape in (
                cmds.listRelatives(
                    o, allDescendents=True, type="mesh", fullPath=True, ni=True
                )
                or []
            ):
                xf = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
                if xf not in out:
                    out.append(xf)
        return out

    def source_tooltip(self) -> str:
        stored = self.sources
        missing = [s for s in stored if not cmds.objExists(s)]
        return self.sb.tooltip.stored_items(
            stored,
            title="Store Source Meshes",
            body="Store the current selection as the source meshes and switch to "
            "<i>Stored Meshes</i>. <i>Source: Auto</i> reads them too, when they "
            "match the selection.",
            formatter=lambda n: n.rsplit("|", 1)[-1],
            noun="stored source mesh(es)",
            empty_text="Nothing stored yet.",
            notes=[f"{len(missing)} no longer in the scene; skipped."]
            if missing
            else None,
        )

    def capture_source(self) -> None:
        sel = cmds.ls(selection=True, long=True, type="transform") or []
        self.sources = self.meshes(sel)
        if self.sources:
            index = self.ui.cmb_tt_source.findData("stored")
            if index >= 0:
                self.ui.cmb_tt_source.setCurrentIndex(index)
        self.sync()
        self.slots.status(
            f"Stored {len(self.sources)} source mesh(es)."
            if self.sources
            else "Nothing selected -- select the source meshes (or their group)."
        )

    def select_source(self) -> None:
        """Select the stored source meshes -- the capture, made visible.

        The same survivor filter the run applies, so what is selected is
        exactly what a run would read; a node deleted since the capture is
        reported rather than silently dropped.
        """
        stored = self.sources
        alive = [s for s in stored if cmds.objExists(s)]
        if not alive:
            return self.slots.status(
                "Nothing stored -- store the source meshes with the + icon first."
                if not stored
                else "None of the stored source meshes are in the scene -- re-capture them."
            )
        cmds.select(alive, replace=True)
        missing = len(stored) - len(alive)
        self.slots.status(
            f"Selected {len(alive)} stored source mesh(es)."
            + (f" {missing} no longer in the scene; skipped." if missing else "")
        )

    def clear_source(self) -> None:
        self.sources = []
        self.sync()

    # ------------------------------------------------------------ decisions
    @classmethod
    def passes(cls, source_mode, transfer_mode) -> Tuple[bool, bool]:
        """``(do_uvs, do_textures)`` for a Source / Transfer pair (Auto unresolved
        = both possible; the same-mesh source has no second mesh for UVs)."""
        mode = transfer_mode or cls.TRANSFER_UVS
        if mode == cls.TRANSFER_AUTO:
            return source_mode != "uvset", True
        textures = mode == cls.TRANSFER_TEXTURES
        return not textures and source_mode != "uvset", textures

    @classmethod
    def resolve_auto(cls, source_meshes, has_lightmap=None) -> str:
        """Auto's run-time pick: Textures once any source material carries a map
        (or a committed lightmap, with *has_lightmap*), else UV Set."""
        for mesh in source_meshes:
            if has_lightmap is not None:
                try:
                    if has_lightmap(mesh):
                        return cls.TRANSFER_TEXTURES
                except Exception:  # noqa: BLE001 - unreadable marker = none
                    pass
            try:
                materials, _ = TextureTransfer.face_materials(mesh)
            except Exception:  # noqa: BLE001
                continue
            for material in materials:
                try:
                    if TextureTransfer.material_maps(material):
                        return cls.TRANSFER_TEXTURES
                except Exception:  # noqa: BLE001
                    continue
        return cls.TRANSFER_UVS

    def auto_source(self, meshes, stored=()):
        """Source: Auto's reading of the selection: ``(mode, target)``."""
        if len(meshes) >= 3:
            try:
                found = TextureTransfer.find_combined(meshes)
            except ValueError:
                found = None
            if found:
                return "last", found[0]
        if stored and meshes and not set(stored) & set(meshes):
            try:
                pairs = TextureTransfer.pair_sources(list(meshes), list(stored))
                if all(
                    TextureTransfer.topology_matches(t, s)[0] for t, s in pairs.items()
                ):
                    return "stored", None
            except ValueError:
                pass
        if len(meshes) == 1:
            return "uvset", None
        return "first", None

    def source_note(self, mode) -> str:
        reading = {
            "uvset": "one mesh, so its textures move between its UV sets",
            "last": "a mesh combined from all the others, so it is the target",
            "stored": "the stored source meshes, which match the selection",
            "first": f"{self.TERMS['first_who']} as the source and the rest as targets",
        }[mode]
        return f"<br><br><i>Source: Auto</i> read {reading}."

    @staticmethod
    def auto_note(auto, do_textures, source_mode=None, lightmaps=False) -> str:
        if not (auto and do_textures):
            return ""
        if source_mode == "uvset":
            return (
                "<br><br><i>Transfer: Auto</i> chose Textures -- a second UV set of "
                "the same mesh carries no layout to transfer."
            )
        if lightmaps:
            return (
                "<br><br><i>Transfer: Auto</i> chose Textures -- the source carries "
                "texture maps or a committed lightmap."
            )
        return (
            "<br><br><i>Transfer: Auto</i> chose Textures -- the source's materials "
            "carry texture maps."
        )

    def pair(self, targets, sources, do_uvs):
        """``(pairs, error)`` for mesh sources (Stored / Last)."""
        try:
            grouped = TextureTransfer.pair_sources(targets, sources)
        except ValueError as e:
            return None, f"<b>Transfer:</b><br>{e}"
        if do_uvs and any(isinstance(s, tuple) for s in grouped.values()):
            return None, (
                "<b>The UV pass copies one source per target.</b><br>The target was "
                "combined from several sources, so only their textures can travel "
                "-- pick <b>Transfer: Textures</b>."
            )
        return [(s, t) for t, s in grouped.items()], None

    def gate(self, mode, scope, source, targets, do_uvs, do_textures, notes=("", "")):
        """``(pairs, targets, others, refusal)`` -- the run's refusals and pairs."""
        t = self.TERMS
        auto_note, source_note = notes
        if not (do_uvs or do_textures):
            return (
                None,
                targets,
                None,
                (
                    "<b>Nothing to transfer.</b><br>The <i>UV Sets On Same Mesh</i> "
                    "source transfers textures -- pick a <b>Transfer</b> mode that "
                    "includes them." + source_note
                ),
            )
        if mode == "uvset":
            if not targets:
                return (
                    None,
                    targets,
                    None,
                    (
                        "<b>Nothing selected.</b><br>Select the mesh(es) to transfer on."
                        + source_note
                    ),
                )
            return None, targets, None, None
        if mode == "first" and scope != "order":
            if not source or (scope == "selection" and not targets):
                return None, None, None, t["pick_first"] + source_note
            if not do_uvs:
                return (
                    None,
                    None,
                    None,
                    (
                        "<b>The Similar scopes need a Transfer mode that includes UV "
                        "Set</b> -- the UV pass is what finds their targets. Use Rest "
                        "of Selection to transfer textures alone." + auto_note
                    ),
                )
            return None, None, targets, None
        if mode == "stored" and not source:
            return (
                None,
                targets,
                None,
                (
                    "<b>No stored source meshes.</b><br>Store them with the <b>+</b> "
                    "icon on the Source row first."
                ),
            )
        if not (source and targets):
            hint = {"first": t["pick_first"], "last": t["pick_last"]}.get(
                mode, "<b>Nothing selected.</b><br>Select the target mesh(es)."
            )
            return None, targets, None, hint + source_note
        pairs, error = self.pair(targets, source, do_uvs)
        return pairs, targets, None, (error + auto_note if error else None)

    def lightmap_probe(self):
        if not self.ui.chk_tt_lightmaps.isChecked():
            return None
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        return lambda mesh: LightmapRecords.baked_objects([mesh])

    # ------------------------------------------------------------ enablement
    def sync(self) -> None:
        """Grey every row the current Source / Transfer pair makes meaningless."""
        ui = self.ui
        source, transfer = (
            getattr(ui, "cmb_tt_source", None),
            getattr(ui, "cmb_tt_transfer", None),
        )
        if source is None or transfer is None:
            return
        mode = source.currentData() or self.SOURCE_AUTO
        same_mesh = mode == "uvset"
        index = transfer.findData(self.TRANSFER_TEXTURES)
        if same_mesh and index >= 0 and transfer.currentIndex() != index:
            transfer.setCurrentIndex(index)  # re-enters sync; harmless
        transfer.setEnabled(not same_mesh)
        uvs, textures = self.passes(mode, transfer.currentData())
        stored = bool(self.sources)
        for action in (self.select_action, self.clear_action):
            if action is not None:
                action.widget.setEnabled(stored)
        scope = ui.cmb_tt_targets
        scope.setEnabled(mode == "first")
        if self.similarity is not None:
            self.similarity.setEnabled(
                mode == "first" and uvs and (scope.currentData() or "order") != "order"
            )
        for name in (
            "t_tt_src_uvset",
            "t_tt_dst_uvset",
            "cmb_tt_resolution",
            "chk_assign",
        ):
            getattr(ui, name).setEnabled(textures)
        ui.chk_tt_lightmaps.setEnabled(textures and not same_mesh)
        self.slots.sync_shared(textures=textures)

    # ------------------------------------------------------------ run
    @CoreUtils.undoable
    def run(self) -> None:
        """Transfer UVs OR textures -- one pass per run (one undo step)."""
        import mayatk as mtk

        ui = self.ui
        mode = ui.cmb_tt_source.currentData() or self.SOURCE_AUTO
        scope = ui.cmb_tt_targets.currentData() or "order"
        ordered = cmds.ls(orderedSelection=True, long=True, type="transform") or []
        if not ordered:
            ordered = cmds.ls(selection=True, long=True, type="transform") or []
        stored = [s for s in self.sources if cmds.objExists(s)]
        source_note = ""
        if mode == self.SOURCE_AUTO:
            meshes = self.meshes(ordered)
            mode, target = self.auto_source(meshes, stored)
            if target is not None:
                ordered = [m for m in meshes if m != target] + [target]
            scope = "order"
            source_note = self.source_note(mode)
        if mode == "first":
            source = self.meshes(ordered[:1])
            targets = [m for m in self.meshes(ordered[1:]) if m not in source]
        elif mode == "last":
            targets = self.meshes(ordered[-1:])
            source = [m for m in self.meshes(ordered[:-1]) if m not in targets]
        elif mode == "stored":
            source = stored
            targets = [m for m in self.meshes(ordered) if m not in source]
        else:  # uvset: every selected mesh moves between its own sets
            source, targets = None, self.meshes(ordered)

        transfer_mode = ui.cmb_tt_transfer.currentData()
        auto = transfer_mode == self.TRANSFER_AUTO
        probe = self.lightmap_probe()
        if auto and mode != "uvset":
            transfer_mode = self.resolve_auto(source or [], has_lightmap=probe)
        do_uvs, do_textures = self.passes(mode, transfer_mode)
        auto_note = self.auto_note(auto, do_textures, mode, lightmaps=probe is not None)
        pairs, targets, others, refusal = self.gate(
            mode,
            scope,
            source,
            targets,
            do_uvs,
            do_textures,
            notes=(auto_note + source_note, source_note),
        )
        if refusal:
            return self.sb.message_box(refusal)

        report = []
        label = "Working: Transfer UV Set" if do_uvs else "Working: Transfer Textures"
        with self.sb.progress(text=label) as tick:
            tick()
            if do_uvs:
                try:
                    if pairs is None:
                        targets = mtk.UvUtils.transfer_uvs_to_similar(
                            source[0],
                            others if scope == "selection" else None,
                            tolerance=self.similarity.value()
                            if self.similarity
                            else 0.9,
                        )
                        if not targets:
                            return self.sb.message_box(
                                "<b>No similar objects found.</b><br>Lower the "
                                "similarity threshold, or note that true instances "
                                "already share the source's UVs."
                            )
                        results = [(source[0], t, "topology") for t in targets]
                    else:
                        results = mtk.UvUtils.transfer_uvs(
                            [s for s, _ in pairs],
                            [t for _, t in pairs],
                            match_by_similarity=False,
                        )
                except ValueError as e:
                    return self.sb.message_box(f"<b>Transfer UV Set:</b><br>{e}")
                approximated = [r for r in results if r[2] != "topology"]
                report.append(
                    f"Transferred UVs to <b>{len(results)}</b> object(s)."
                    + (
                        f"<br><b>{len(approximated)}</b> did not match the source's "
                        "topology and were sampled by proximity -- check the result."
                        if approximated
                        else ""
                    )
                )
            if do_textures:
                tick(text="Working: Transfer Textures")
                report.append(self.texture_pass(targets, source))
        self.sb.message_box("<br><br>".join(report) + source_note)

    def derived_name(self, source, targets) -> str:
        nodes = list(source) if source else list(targets or [])
        name = ptk.StrUtils.common_name(nodes, strip=ptk.NamingConvention.all_affixes())
        return name or "transfer"

    def texture_pass(self, targets, source) -> str:
        """The texture pass (and, with Include Lightmaps, the lightmap pass)."""
        ui = self.ui
        lines = []
        produced = False
        out_name = ui.t_name.text().strip()
        typed = bool(out_name)
        if not typed:
            out_name = self.derived_name(source, targets)
        named = out_name
        prefix, suffix = self.slots.material_affix()
        in_place = ui.chk_assign.isChecked() and self.slots.overwrites_target()
        before = self.read_files(targets) if in_place else set()
        engine = TextureTransfer()
        # What Overwrite target leaves alone is said as a warning; the report
        # repeats it (the class's own ring buffer, read before it is dropped).
        left = engine.logger.enable_log_buffer(level="WARNING") if in_place else None
        try:
            results = engine.transfer(
                targets,
                source,
                source_uv_set=ui.t_tt_src_uvset.text().strip() or None,
                target_uv_set=ui.t_tt_dst_uvset.text().strip() or None,
                size=ui.cmb_tt_resolution.currentData() or None,
                supersample=ui.cmb_quality.currentData() or 2,
                padding=ui.s_padding.value(),
                output_name=out_name,
                output_dir=ui.t_output.text().strip() or None,
                assign=ui.chk_assign.isChecked(),
                assign_prefix=prefix,
                assign_suffix=suffix,
                **self.slots.assign_options(),
            )
        except ValueError as e:
            lines.append(f"<b>Transfer Textures:</b> {e}")
            results = None
        finally:
            warnings = [r.getMessage() for r in left.buffer] if left else []
            if left is not None:
                engine.logger.disable_log_buffer()
        if results is not None:
            n_maps = sum(len(v) for v in results.values())
            produced = bool(n_maps)
            folder = next(
                (os.path.dirname(p) for v in results.values() for p in v.values()), ""
            )
            lines.append(
                f"Transferred <b>{n_maps}</b> map(s) for <b>{len(results)}</b> "
                "material(s)"
                + (
                    f'<br><a href="action://open?path={folder}">{folder}</a>'
                    if folder
                    else ""
                )
            )
            if in_place:
                lines.append(self.overwrite_note(before, results, warnings))
            stems = {
                os.path.basename(p).rsplit("_", 1)[0]
                for v in results.values()
                for p in v.values()
            }
            # In place, a map keeps the name it had: no stem is the output's.
            if len(results) == 1 and len(stems) == 1 and not in_place:
                named = stems.pop()
        if source is not None and ui.chk_tt_lightmaps.isChecked():
            text, wrote = self.lightmap_pass(targets, source, named)
            lines.append(text)
            produced = produced or wrote
        if produced and not typed and not in_place:
            lines.append(
                f"Named <b>{named}</b> after the source -- type an <b>Output Name</b> "
                "to choose another."
            )
        elif produced and named != out_name and not in_place:
            lines.append(
                f"Named <b>{named}</b>: <b>{out_name}</b> is held outside this run "
                "(another object's material, or a map one reads)."
            )
        if produced and typed:
            self.slots.record_name(out_name)
        return "<br><br>".join(lines)

    #: Overwrite target's report lists this many of what it left alone; the
    #: Script Editor has the rest.
    NOTE_LIMIT = 6

    @staticmethod
    def read_files(targets) -> set:
        """Path keys of the maps the materials *targets* wear read now."""
        return {
            TextureTransfer._path_key(path)
            for target in targets or ()
            for mat in TextureTransfer.face_materials(target)[0]
            for path in TextureTransfer.material_maps(mat).values()
        }

    @classmethod
    def overwrite_note(cls, before, results, warnings) -> str:
        """Overwrite target's report: the files rewritten where they are (the
        targets' materials read them before the run), the new ones they read
        now, and the run's warnings (what was left alone, and why)."""
        written = [p for maps in results.values() for p in maps.values()]
        over = [p for p in written if TextureTransfer._path_key(p) in before]
        text = (
            f"Overwrote <b>{len(over)}</b> file(s) in place: "
            + ", ".join(os.path.basename(p) for p in over)
            if over
            else "No file was overwritten in place."
        )
        if len(written) > len(over):
            text += (
                f"<br>Wrote <b>{len(written) - len(over)}</b> new file(s) the "
                "targets' own materials now read."
            )
        if warnings:
            shown = warnings[: cls.NOTE_LIMIT]
            more = len(warnings) - len(shown)
            text += "<br>Warnings:<br>" + "<br>".join(
                f"&bull; {html.escape(w)}" for w in shown
            )
            if more:
                text += f"<br>... and {more} more (Script Editor)."
        return text

    def lightmap_pass(self, targets, source, out_name) -> Tuple[str, bool]:
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        ui = self.ui
        try:
            results = LightmapRecords.transfer_lightmaps(
                targets,
                source,
                output_dir=ui.t_output.text().strip() or None,
                output_name=out_name,
                supersample=ui.cmb_quality.currentData() or 2,
                padding=ui.s_padding.value(),
            )
        except ValueError as e:
            return f"<b>Transfer Lightmaps:</b> {e}", False
        if not results:
            return (
                "<b>No lightmaps transferred</b> -- no source carries a committed "
                "lightmap, or its map or lightmap UVs are gone (see the log).",
                False,
            )
        resampled = [r["path"] for r in results.values() if r["how"] == "resampled"]
        rebound = len(results) - len(resampled)
        parts = []
        if rebound:
            parts.append(f"<b>{rebound}</b> bound to the source's own map")
        if resampled:
            parts.append(f"<b>{len(resampled)}</b> resampled into the target's layout")
        folder = os.path.dirname(resampled[0]) if resampled else ""
        return (
            f"Transferred lightmaps to <b>{len(results)}</b> object(s): "
            + ", ".join(parts)
            + "."
            + (
                f'<br><a href="action://open?path={folder}">{folder}</a>'
                if folder
                else ""
            ),
            bool(resampled),
        )
