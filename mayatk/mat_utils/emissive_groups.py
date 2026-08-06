# !/usr/bin/python
# coding=utf-8
"""Emissive groups — named face sets that gate emissive regions at runtime.

Author groups of faces ("headlights", "panel_leds") whose emissive texels a
game engine can toggle or dim independently while sharing one all-on
emissive map. The runtime gate is ``emissive * dot(mask, weights)``; this
tool authors the mask side. Two encodings (pythontk's region-mask engine is
the shared model — see ``pythontk/.../engines/textures/region_masks.py``):

- **vertex-color** (default) — membership baked into the ``emissiveGroups``
  RGBA color set, one group per channel, written per-face-vertex so group
  boundaries stay hard. No textures; rides the FBX. Claims the mesh's one
  Unity color channel and caps at 4 groups.
- **channels** — membership rasterized into an ``_EMask`` RGBA texture via
  :class:`ptk.RegionMaskPacker`, for emissive detail painted sub-face or
  meshes whose color set is spoken for.

Scene data (kept minimal — nothing is created until the tool is used):

- Membership: one plain ``objectSet`` per group (``emissiveGroup_<name>``),
  no custom attrs.
- Group registry (slots / defaults / encoding): a single JSON channel
  ``emissive_groups`` on the ``data_internal`` node. Slots are assigned once
  and never reshuffled — downstream Unity scenes key against them. Removing
  a group *retires* its slot; :meth:`EmissiveGroups.compact_slots` is the
  explicit, binding-breaking reclaim.
- Export manifest: regenerated onto ``data_export.emissive_groups`` before
  every FBX export (``FbxUtils._KNOWN_PRODUCERS``); Unity's
  ``EmissiveGroupController`` importer reads it as an FBX user property.
- Keyable weights (opt-in): :meth:`EmissiveGroups.make_weights_keyable` adds
  one keyable 0-1 float per group (``emissiveGroup_<name>``) on the
  ``data_export`` carrier — the RenderOpacity attribute-mode idiom, but
  model-global rather than per-object. Keyed curves ride the FBX as animated
  custom properties; because every group's attr name is unique, Unity's
  root-flattened import stays unambiguous and the importer rebinds each
  curve to ``EmissiveGroupController.groups[i].weight`` (no visibility
  dual-key workaround needed). The registry records each group's attr in the
  manifest so the importer knows what to look for.

Constraints artists should know (also in the panel help): a face's group
membership tracks face *indices* — topology edits can silently shift it;
mirrored/stacked UV shells share texels in channels encoding, so such groups
toggle together; baked-GI bounce light does not react to runtime toggles.
"""

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.node_utils.data_nodes import DataNodes


class _EmissiveGroupsInternal:
    """Implementation-detail base for :class:`EmissiveGroups`."""

    SET_PREFIX = "emissiveGroup_"
    COLOR_SET = "emissiveGroups"
    DATA_CHANNEL = "emissive_groups"  # data_internal registry + data_export manifest

    # ------------------------------------------------------------------
    # Registry — slot bookkeeping lives in the shared engine; this class
    # only binds it to the scene carrier (``data_internal``).
    # ------------------------------------------------------------------

    @classmethod
    def _registry(cls) -> "ptk.RegionGroupRegistry":
        return ptk.RegionGroupRegistry(
            load=lambda: DataNodes.get_internal_string(cls.DATA_CHANNEL),
            save=lambda text: DataNodes.set_internal_string(cls.DATA_CHANNEL, text),
            logger=cls.logger,
        )

    # ------------------------------------------------------------------
    # Naming / membership helpers
    # ------------------------------------------------------------------

    @classmethod
    def _set_node(cls, name: str) -> str:
        return f"{cls.SET_PREFIX}{name}"

    @classmethod
    def _faces_from(cls, faces=None) -> List[str]:
        """Resolve *faces* (or the selection) to face components.

        Whole meshes are converted to their full face list; anything that
        isn't a poly face is dropped.
        """
        source = faces if faces is not None else (cmds.ls(sl=True) or [])
        source = [str(s) for s in (source if isinstance(source, (list, tuple)) else [source])]
        if not source:
            return []
        converted = cmds.polyListComponentConversion(source, toFace=True) or []
        return cmds.filterExpand(converted, sm=34, expand=False) or []

    @classmethod
    def _member_faces(cls, name: str, flatten: bool = False) -> List[str]:
        node = cls._set_node(name)
        if not cmds.objExists(node):
            return []
        members = cmds.sets(node, q=True) or []
        faces = cmds.filterExpand(members, sm=34, expand=flatten) or []
        return faces

    @classmethod
    def _member_meshes(cls, names) -> List[str]:
        meshes = []
        for name in names:
            for face in cls._member_faces(name):
                mesh = face.split(".")[0]
                if mesh not in meshes and cmds.objExists(mesh):
                    meshes.append(mesh)
        return meshes

    @classmethod
    def _refresh_export_if_published(cls) -> None:
        """Keep an already-published manifest current — never create one.

        Authoring (add / remove / weight edits) must not stamp a
        ``data_export`` channel into a scene that has never been baked or
        exported: the export preparer regenerates it at export time anyway,
        so creating it early is pure scene clutter. Once a manifest *does*
        exist, though, leaving it stale would ship wrong data.
        """
        if DataNodes.get_export_string(cls.DATA_CHANNEL) is not None:
            cls.refresh_export_metadata()

    # ------------------------------------------------------------------
    # Keyable-weight helpers (attrs live on the ``data_export`` carrier)
    # ------------------------------------------------------------------

    @classmethod
    def _weight_plug(cls, name: str) -> str:
        return f"{DataNodes.EXPORT}.{cls._set_node(name)}"

    @classmethod
    def _weight_attr_exists(cls, name: str) -> bool:
        return cmds.objExists(DataNodes.EXPORT) and cmds.attributeQuery(
            cls._set_node(name), node=DataNodes.EXPORT, exists=True
        )

    @classmethod
    def _delete_weight_attr(cls, name: str) -> bool:
        """Delete the carrier's weight attr (and its anim curves) for *name*."""
        if not cls._weight_attr_exists(name):
            return False
        plug = cls._weight_plug(name)
        # Anim curves first — deleteAttr errors on connected attrs.
        curves = cmds.listConnections(plug, type="animCurve") or []
        if curves:
            cmds.delete(curves)
        cmds.deleteAttr(plug)
        return True

    @classmethod
    def _sync_unkeyed_attr(cls, name: str, value: float) -> None:
        """Keep an *un-driven* keyable weight attr in step with the group
        default. ANY incoming connection — anim curve, expression,
        constraint — owns the value (and would make the setAttr raise), so
        the sync applies only to a free plug."""
        if not cls._weight_attr_exists(name):
            return
        plug = cls._weight_plug(name)
        if not cmds.connectionInfo(plug, isDestination=True):
            cmds.setAttr(plug, float(value))

    # ------------------------------------------------------------------
    # UV harvest (channels encoding)
    # ------------------------------------------------------------------

    @classmethod
    def _harvest_mesh_uv_triangles(cls, mesh_faces: List[str], uv_set: Optional[str]):
        """Triangulated UVs for one mesh's face components: (N, 3, 2) lists."""
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        for face in mesh_faces:
            sel.add(face)
        dag, comp = sel.getComponent(0)
        fn = om.MFnMesh(dag)
        uv_set = uv_set or fn.currentUVSetName()
        tris = []
        it = om.MItMeshPolygon(dag, comp)
        while not it.isDone():
            if not it.hasUVs():
                it.next()
                continue
            local = {it.vertexIndex(i): i for i in range(it.polygonVertexCount())}
            us, vs = it.getUVs(uv_set)
            for t in range(it.numTriangles()):
                _, vtx_ids = it.getTriangle(t)
                tris.append([(us[local[v]], vs[local[v]]) for v in vtx_ids])
            it.next()
        return tris


class EmissiveGroups(_EmissiveGroupsInternal, ptk.LoggingMixin, ptk.HelpMixin):
    """Author, bake, and export named emissive face-groups (see module doc).

    All state lives in the scene (member ``objectSet``s + the
    ``data_internal`` registry channel), so the class is stateless and every
    operation is a classmethod.
    """

    # ------------------------------------------------------------------
    # Authoring
    # ------------------------------------------------------------------

    @classmethod
    def add_group(
        cls, name: str, faces=None, default: float = 1.0
    ) -> str:
        """Create a group from faces (or the selection), or extend an existing one.

        Parameters:
            name: Group name (sanitized to Maya-safe characters).
            faces: Face components / meshes; defaults to the selection.
            default: Default gate weight consumers apply (1.0 = on).

        Returns:
            The membership ``objectSet`` name.
        """
        name = ptk.RegionGroupRegistry.sanitize(name)
        faces = cls._faces_from(faces)
        if not faces:
            raise ValueError("No poly faces in the input or selection.")
        slot, is_new = cls._registry().add(name, default)
        node = cls._set_node(name)
        if cmds.objExists(node):
            cmds.sets(faces, add=node)
        else:
            cmds.sets(faces, name=node)
        cls.logger.info(
            f"Added group {name!r} (slot {slot})."
            if is_new
            else f"Extended group {name!r} (+{len(faces)} component(s))."
        )
        cls._refresh_export_if_published()
        return node

    @classmethod
    def remove_group(cls, name: str) -> None:
        """Delete a group's set, registry entry, and any keyable weight attr;
        its slot is retired (never auto-reused) so existing engine bindings
        stay valid."""
        cls._registry().remove(name)
        node = cls._set_node(name)
        if cmds.objExists(node):
            cmds.delete(node)
        cls._delete_weight_attr(name)
        cls._refresh_export_if_published()
        cls.logger.info(f"Removed group {name!r}.")

    @classmethod
    def list_groups(cls) -> Dict[str, dict]:
        """``{name: {"slot", "default", "faces"(count), "missing"(set gone),
        "attr"(keyable weight attr or None)}}`` in slot order."""
        out = {}
        for entry in cls._registry().groups():
            name = entry["name"]
            node = cls._set_node(name)
            missing = not cmds.objExists(node)
            out[name] = {
                "slot": entry["slot"],
                "default": entry["default"],
                "faces": 0 if missing else len(cls._member_faces(name, flatten=True)),
                "missing": missing,
                "attr": entry.get("attr"),
            }
        return out

    @classmethod
    def select_group(cls, name: str) -> None:
        faces = cls._member_faces(name)
        if not faces:
            raise ValueError(f"Group {name!r} has no members.")
        cmds.select(faces, replace=True)

    @classmethod
    def set_default(cls, name: str, default: float) -> None:
        """Set the group's default gate weight (0-1; clamped). An un-keyed
        keyable weight attr follows the default; a keyed one is animation-owned
        and left alone."""
        value = cls._registry().set_default(name, default)
        cls._sync_unkeyed_attr(name, value)
        cls._refresh_export_if_published()

    # ------------------------------------------------------------------
    # Keyable weights (opt-in)
    # ------------------------------------------------------------------

    @classmethod
    def make_weights_keyable(cls, names=None) -> Dict[str, str]:
        """Add a keyable 0-1 float per group on the ``data_export`` carrier.

        Explicitly export-facing (this publishes the manifest, creating the
        carrier if needed): the attrs exist so their animation curves ride
        the FBX as animated custom properties, which Unity's importer rebinds
        to ``EmissiveGroupController.groups[i].weight``. Key them in the
        channel box / graph editor or via :meth:`key_weight`.

        Parameters:
            names: Groups to make keyable; None = every group.

        Returns:
            ``{group_name: carrier_plug}`` for the affected groups.
        """
        registry = cls._registry()
        known = {entry["name"]: entry for entry in registry.groups()}
        if not known:
            raise ValueError("No emissive groups.")
        names = list(known) if names is None else [str(n) for n in names]
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"Unknown group(s): {unknown}.")
        export = str(DataNodes.ensure_export())
        plugs = {}
        for name in names:
            attr = cls._set_node(name)
            if not cmds.attributeQuery(attr, node=export, exists=True):
                cmds.addAttr(
                    export,
                    longName=attr,
                    attributeType="float",
                    minValue=0.0,
                    maxValue=1.0,
                    defaultValue=float(known[name]["default"]),
                    keyable=True,
                )
            else:
                # Relink path (e.g. an FBX reimport restored the attr but a
                # reimported attr is not necessarily keyable).
                cmds.setAttr(f"{export}.{attr}", keyable=True)
            registry.set_attr(name, attr)
            plugs[name] = f"{export}.{attr}"
        cls.refresh_export_metadata()
        cls.logger.info(f"Keyable weight attr(s): {sorted(plugs.values())}")
        return plugs

    @classmethod
    def remove_keyable_weights(cls, names=None) -> List[str]:
        """Delete the keyable weight attrs — including their animation — and
        clear the manifest's attr records. The groups themselves (membership,
        slots, defaults) are untouched.

        Parameters:
            names: Groups to strip; None = every group.

        Returns:
            The group names that actually had an attr to remove.
        """
        registry = cls._registry()
        known = {entry["name"] for entry in registry.groups()}
        names = sorted(known) if names is None else [str(n) for n in names]
        removed = []
        for name in names:
            if cls._delete_weight_attr(name):
                removed.append(name)
            if name in known:
                registry.set_attr(name, None)
        cls._refresh_export_if_published()
        if removed:
            cls.logger.info(f"Removed keyable weight attr(s): {removed}")
        return removed

    @classmethod
    def key_weight(
        cls,
        name: str,
        value: Optional[float] = None,
        frame: Optional[float] = None,
        auto_keyable: bool = True,
    ) -> str:
        """Key one group's weight on its carrier attr.

        Parameters:
            name: Group name.
            value: Weight to key (clamped 0-1); None = the attr's current
                value (key-current-state).
            frame: Time to key at; None = the current time.
            auto_keyable: Make the group keyable first when it isn't yet
                (mirrors ``OpacityAttributeMode.key_fade``'s ``auto_create``).

        Returns:
            The keyed plug (``data_export.emissiveGroup_<name>``).
        """
        if not cls._weight_attr_exists(name):
            if not auto_keyable:
                raise ValueError(
                    f"Group {name!r} has no keyable weight; run "
                    f"make_weights_keyable([{name!r}])."
                )
            cls.make_weights_keyable([name])
        plug = cls._weight_plug(name)
        kwargs = {}
        if frame is not None:
            kwargs["time"] = frame
        if value is not None:
            kwargs["value"] = max(0.0, min(1.0, float(value)))
        cmds.setKeyframe(plug, **kwargs)
        return plug

    @classmethod
    def compact_slots(cls) -> List[int]:
        """Reclaim retired slots. Explicit and binding-breaking: any engine
        scene keyed against a previously-exported slot layout must be
        re-wired after the next bake/export.

        Returns:
            The slot indices reclaimed.
        """
        reclaimed = cls._registry().compact()
        if reclaimed:
            cls.logger.warning(
                f"Reclaimed retired slot(s) {reclaimed}; re-bake and re-export "
                "— existing engine bindings for those slots are now invalid."
            )
        return reclaimed

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @classmethod
    def validate(cls) -> List[str]:
        """Non-fatal authoring warnings (empty list = clean)."""
        groups = cls._registry().groups()
        known = {entry["name"] for entry in groups}
        warnings = []
        face_owner: Dict[str, str] = {}
        for entry in groups:
            name = entry["name"]
            node = cls._set_node(name)
            if not cmds.objExists(node):
                warnings.append(f"Group {name!r}: membership set {node!r} is missing.")
                continue
            faces = cls._member_faces(name, flatten=True)
            if not faces:
                warnings.append(f"Group {name!r} is empty.")
            for face in faces:
                if face in face_owner:
                    warnings.append(
                        f"Group {name!r} overlaps {face_owner[face]!r} (e.g. {face}); "
                        "shared faces glow when either group is on."
                    )
                    break
                face_owner[face] = name
        # Orphan sets: membership without a registry entry (e.g. imported).
        for node in cmds.ls(f"{cls.SET_PREFIX}*", type="objectSet") or []:
            name = node[len(cls.SET_PREFIX):]
            if name not in known:
                warnings.append(
                    f"Set {node!r} has no registry entry — re-add it with "
                    f"add_group({name!r}) to assign a slot."
                )
        # Orphan weight attrs: an FBX REimport restores the carrier's keyable
        # attrs but not the registry (data_internal never rides the FBX).
        if cmds.objExists(DataNodes.EXPORT):
            for attr in cmds.listAttr(DataNodes.EXPORT, userDefined=True) or []:
                if (
                    attr.startswith(cls.SET_PREFIX)
                    and attr[len(cls.SET_PREFIX):] not in known
                ):
                    warnings.append(
                        f"Carrier attr {attr!r} has no registry entry — a stale "
                        "keyable weight (removed or imported group); re-add the "
                        f"group or remove_keyable_weights"
                        f"([{attr[len(cls.SET_PREFIX):]!r}])."
                    )
        # Foreign color sets fight ours for Unity's single color channel.
        for mesh in cls._member_meshes(known):
            for cset in cmds.polyColorSet(mesh, q=True, allColorSets=True) or []:
                if cset != cls.COLOR_SET:
                    warnings.append(
                        f"{mesh}: foreign color set {cset!r} — Unity imports only "
                        "one color stream; vertex-color encoding may not survive."
                    )
        for msg in warnings:
            cls.logger.warning(msg)
        return warnings

    # ------------------------------------------------------------------
    # Bakes
    # ------------------------------------------------------------------

    @classmethod
    def bake_vertex_colors(cls, force: bool = False) -> dict:
        """Bake membership into the ``emissiveGroups`` RGBA color set.

        Written per-face-vertex (hard group boundaries — shared boundary
        vertices must not interpolate between groups). The whole mesh is
        zeroed first so re-bakes never leave stale membership behind.

        Parameters:
            force: Proceed even when member meshes carry foreign color sets
                (Unity imports a single color stream — first set wins).

        Returns:
            The published manifest dict (vertex-color encoding).
        """
        registry = cls._registry()
        groups = registry.groups()
        if not groups:
            raise ValueError("No emissive groups to bake.")
        meshes = cls._member_meshes(entry["name"] for entry in groups)
        if not meshes:
            raise ValueError("No member meshes found (empty groups?).")

        foreign = {
            mesh: [
                c
                for c in (cmds.polyColorSet(mesh, q=True, allColorSets=True) or [])
                if c != cls.COLOR_SET
            ]
            for mesh in meshes
        }
        foreign = {m: c for m, c in foreign.items() if c}
        if foreign and not force:
            raise ValueError(
                f"Foreign color set(s) present: {foreign} — Unity imports only "
                "one color stream. Remove them, use the channels encoding, or "
                "pass force=True."
            )

        # Channel weights per face: overlapping groups accumulate.
        face_vec: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        for entry in groups:
            for face in cls._member_faces(entry["name"], flatten=True):
                face_vec[face][entry["slot"]] = 1.0

        for mesh in meshes:
            existing = cmds.polyColorSet(mesh, q=True, allColorSets=True) or []
            if cls.COLOR_SET not in existing:
                cmds.polyColorSet(
                    mesh,
                    create=True,
                    colorSet=cls.COLOR_SET,
                    representation="RGBA",
                    clamped=True,
                )
            cmds.polyColorSet(mesh, currentColorSet=True, colorSet=cls.COLOR_SET)
            cmds.polyColorPerVertex(f"{mesh}.f[*]", rgb=(0.0, 0.0, 0.0), a=0.0)

        # One assignment per distinct channel combination (fast, few combos).
        by_vec: Dict[tuple, List[str]] = defaultdict(list)
        for face, vec in face_vec.items():
            by_vec[tuple(vec)].append(face)
        for vec, faces in by_vec.items():
            cmds.polyColorPerVertex(faces, rgb=vec[:3], a=vec[3])

        registry.set_encoding("vertex-color")
        manifest = cls.refresh_export_metadata()
        cls.logger.info(
            f"Baked {len(groups)} group(s) into color set {cls.COLOR_SET!r} on "
            f"{len(meshes)} mesh(es)."
        )
        return json.loads(manifest)

    @classmethod
    def bake_mask(
        cls,
        output_path: Optional[str] = None,
        resolution: int = 512,
        padding_px: int = 4,
        uv_set: Optional[str] = None,
    ) -> dict:
        """Rasterize membership into an ``_EMask`` RGBA texture (channels encoding).

        Harvests each group's UV triangles and packs them through
        :class:`ptk.RegionMaskPacker`; the manifest sidecar lands next to the
        mask and is also published to the FBX carrier.

        Parameters:
            output_path: Mask image path. Defaults to
                ``<workspace sourceimages>/<scene>_EMask.png``.
            resolution: Mask resolution (masks are chunky; 512 usually
                suffices — decoupled from the emissive map's resolution).
            padding_px: Edge padding; keep >= the emissive bake's padding.
            uv_set: UV set to harvest; defaults to each mesh's current
                (must be the set the engine samples as UV0).

        Returns:
            The published manifest dict (channels encoding).
        """
        registry = cls._registry()
        groups = registry.groups()
        if not groups:
            raise ValueError("No emissive groups to bake.")
        if output_path is None:
            root = cmds.workspace(q=True, rootDirectory=True) or ""
            rule = cmds.workspace(fileRuleEntry="sourceImages") or "sourceimages"
            scene = cmds.file(q=True, sceneName=True, shortName=True) or ""
            stem = os.path.splitext(scene)[0] or "untitled"
            output_path = os.path.join(root, rule, f"{stem}_EMask.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        packer = ptk.RegionMaskPacker(resolution=resolution, padding_px=padding_px)
        for entry in groups:
            faces = cls._member_faces(entry["name"])
            if not faces:
                cls.logger.warning(f"Group {entry['name']!r} is empty; skipped.")
                continue
            by_mesh = defaultdict(list)
            for face in faces:
                by_mesh[face.split(".")[0]].append(face)
            tris = []
            for mesh_faces in by_mesh.values():
                tris.extend(cls._harvest_mesh_uv_triangles(mesh_faces, uv_set))
            if not tris:
                cls.logger.warning(
                    f"Group {entry['name']!r} has no mapped UVs; skipped."
                )
                continue
            packer.add_group(
                entry["name"],
                tris,
                slot=entry["slot"],
                default=entry["default"],
                attr=entry.get("attr"),
            )
        packer.validate()
        manifest = packer.write(output_path)

        registry.set_encoding(
            "channels",
            mask=os.path.basename(output_path),
            resolution=int(resolution),
            uv_channel=0,
        )
        published = cls.refresh_export_metadata()
        cls.logger.info(f"Baked mask: {output_path}")
        return json.loads(published) if published else manifest.to_dict()

    # ------------------------------------------------------------------
    # Export carrier
    # ------------------------------------------------------------------

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``emissive_groups`` channel on the ``data_export``
        carrier from the registry.

        The canonical no-arg pre-export refresh — wired into
        ``FbxUtils._KNOWN_PRODUCERS`` so any FBX export ships a current
        manifest (read Unity-side by ``EmissiveGroupController``'s importer).
        Clears the channel when no groups exist (no empty carrier left
        behind).

        Returns:
            The published JSON string, or None when cleared.
        """
        manifest = cls._registry().manifest(color_set=cls.COLOR_SET)
        if manifest is None:
            DataNodes.set_export_string(cls.DATA_CHANNEL, "")
            return None
        payload = manifest.to_json()
        DataNodes.set_export_string(cls.DATA_CHANNEL, payload)
        return payload


class EmissiveGroupsSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``emissive_groups.ui`` panel.

    Composition over inheritance: a thin driver over :class:`EmissiveGroups`
    — no authoring or bake logic lives here. The table lists groups in slot
    order; the **Weight** column is scrub- and click-editable (Maya
    channel-box idiom) and writes each group's default gate weight. **Bake**
    (``tb000``) runs the encoding chosen in its option box: *Vertex Color*
    (rides the FBX) or *Mask Texture* (an ``_EMask`` image for sub-face
    emissive detail).
    """

    COLUMNS = ("Group", "Slot", "Weight", "Faces")
    WEIGHT_COL = 2
    #: pixels of horizontal scrub per full 0-1 weight sweep
    SCRUB_PX_PER_UNIT = 150.0

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.emissive_groups
        self._updating = False
        self._scrub_start = None
        self._scrub_value = None

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Compact Retired Slots",
            setObjectName="compact_slots",
            setToolTip=self.sb.tooltip.fmt(
                title="Compact Retired Slots",
                body="Reclaim the channel slots left behind by removed groups.",
                notes=[
                    "Breaks any existing engine binding against those slots — "
                    "re-bake and re-export afterward.",
                ],
            ),
        )
        widget.menu.add(
            "QPushButton",
            setText="Republish Export Data",
            setObjectName="republish_export",
            setToolTip=self.sb.tooltip.fmt(
                title="Republish Export Data",
                body="Rewrite the <code>emissive_groups</code> channel on the "
                "<code>data_export</code> node from the current registry.",
                notes=["Runs automatically before every FBX export."],
            ),
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Emissive Groups",
                body=(
                    "Author named face groups whose emissive regions the "
                    "game engine can toggle or dim independently, sharing "
                    "one all-on emissive map."
                ),
                steps=[
                    "Select faces (or meshes), name the group, press Add.",
                    "Repeat per independently-controlled region (max 4).",
                    "Set each group's default Weight in the table.",
                    "Bake (option box picks Vertex Color or Mask Texture).",
                    "Export FBX as usual — the manifest rides along.",
                ],
                sections=[
                    (
                        "Encodings",
                        [
                            "<b>Vertex Color</b> — rides the FBX; claims "
                            "the mesh's single engine color stream.",
                            "<b>Mask Texture</b> — an _EMask image; use it "
                            "for emissive detail painted inside a face.",
                        ],
                    )
                ],
                notes=[
                    "Regions in no group keep glowing as baked — only "
                    "group what you intend to control.",
                    "Topology edits can shift face membership; re-run "
                    "Validate after modeling changes.",
                    "Baked-GI bounce light ignores runtime toggles.",
                    "Table menu &gt; Make Weights Keyable adds keyable "
                    "0-1 weights on the data_export carrier. Keyed curves "
                    "drive the Unity controller from both DCCs (Maya: FBX "
                    "custom-property curves; Blender: Scene Exporter "
                    "curve proxies).",
                ],
            )
        )

    def txt000_init(self, widget) -> None:
        """Group-name field — clearable back to the auto-derived name."""
        widget.option_box.clear_option = True

    def tbl000_init(self, widget) -> None:
        """Table setup: one-time construction, then (re)wire signals and populate.

        The signal wiring runs unconditionally because the ``tbl000`` QWidget
        can outlive this slots instance — a reload builds a NEW slots object
        on the SAME persisted widget, which already carries
        ``is_initialized``; without the re-wire the handlers stay bound to
        the orphaned instance and silently no-op. The context-menu ITEMS
        stay in the one-time block since they mutate the persisting widget
        (building them twice duplicates the entries).
        """
        if not widget.is_initialized:
            widget.is_initialized = True
            widget.refresh_on_show = True
            widget.setColumnCount(len(self.COLUMNS))
            widget.setHorizontalHeaderLabels(list(self.COLUMNS))

            QHeaderView = self.sb.QtWidgets.QHeaderView
            header = widget.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for col in (1, self.WEIGHT_COL, 3):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

            widget.setSelectionBehavior(
                self.sb.QtWidgets.QAbstractItemView.SelectRows
            )
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.SingleSelection
            )
            # Weight edits like a Maya channel-box field: MMB-drag to scrub,
            # single click to type.
            widget.set_scrub_columns([self.WEIGHT_COL])
            widget.set_single_click_edit_columns([self.WEIGHT_COL])

            widget.menu.add("Separator", setTitle="Group")
            widget.menu.add(
                "QPushButton",
                setText="Select Members",
                setObjectName="select_members",
                setToolTip=self.sb.tooltip.fmt(
                title="Select Members",
                body="Select the faces belonging to the highlighted group.",
            ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Remove Group",
                setObjectName="remove_group",
                setToolTip=self.sb.tooltip.fmt(
                    title="Remove Group",
                    body="Delete the highlighted group.",
                    notes=[
                        "Its channel slot is <b>retired</b>, never reused, so every "
                        "existing engine binding stays valid.",
                    ],
                ),
            )
            widget.menu.add("Separator", setTitle="Weights")
            widget.menu.add(
                "QPushButton",
                setText="All On",
                setObjectName="weights_all_on",
                setToolTip=self.sb.tooltip.fmt(
                title="All On",
                body="Set every group's default weight to <b>1</b>.",
            ),
            )
            widget.menu.add(
                "QPushButton",
                setText="All Off",
                setObjectName="weights_all_off",
                setToolTip=self.sb.tooltip.fmt(
                title="All Off",
                body="Set every group's default weight to <b>0</b>.",
            ),
            )
            widget.menu.add("Separator", setTitle="Keyable")
            widget.menu.add(
                "QPushButton",
                setText="Make Weights Keyable",
                setObjectName="make_weights_keyable",
                setToolTip=self.sb.tooltip.fmt(
                    title="Make Weights Keyable",
                    body="Add one keyable 0–1 weight per group on the "
                    "<code>data_export</code> carrier, then publish the manifest.",
                    sections=[
                        (
                            "How the curves travel",
                            [
                                "<b>Maya</b> — shipped natively as FBX "
                                "custom-property curves.",
                                "<b>Blender</b> — shipped by the Scene Exporter as "
                                "transient curve proxies.",
                            ],
                        )
                    ],
                    notes=[
                        "This is the one authoring action that creates the export "
                        "carrier — it is opt-in for that reason.",
                    ],
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Key Weights @ Current Frame",
                setObjectName="key_weights",
                setToolTip=self.sb.tooltip.fmt(
                    title="Key Weights @ Current Frame",
                    body="Set a key on every keyable group's weight, at its current "
                    "value and the current frame.",
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Remove Keyable Weights",
                setObjectName="remove_keyable_weights",
                setToolTip=self.sb.tooltip.fmt(
                    title="Remove Keyable Weights",
                    body="Delete the keyable weight attributes, <b>including any "
                    "animation on them</b>.",
                    notes=["Groups, slots and default weights are left untouched."],
                ),
            )

        self._wire_table_signals(widget)
        self._refresh_table()

    #: Table signal -> handler name. Bound in :meth:`_wire_table_signals`.
    _TABLE_SIGNALS = {
        "cellChanged": "_on_cell_changed",
        "cellScrubStarted": "_on_scrub_started",
        "cellScrubMoved": "_on_scrub_moved",
        "cellScrubFinished": "_on_scrub_finished",
    }

    def _wire_table_signals(self, widget) -> None:
        """Bind table signals to THIS instance, replacing only OUR bindings.

        Idempotent (see :meth:`tbl000_init`), and deliberately NOT a blanket
        ``signal.disconnect()``: that would also tear out the widget's own
        internal connections — uitk's ``TableWidget`` wires ``cellChanged``
        to its ``_on_cell_edited`` in its constructor — and PySide warns
        ("Failed to disconnect (None) from signal ...") whenever a signal
        has nothing attached. The previous binding is tracked on the widget
        instead and disconnected by reference; holding that reference also
        keeps the receiving slots instance alive for as long as the widget
        points at it.
        """
        previous = getattr(widget, "_emissive_group_handlers", {})
        handlers = {
            name: getattr(self, attr) for name, attr in self._TABLE_SIGNALS.items()
        }
        for name, handler in handlers.items():
            signal = getattr(widget, name)
            prior = previous.get(name)
            if prior is not None:
                try:
                    signal.disconnect(prior)
                except (TypeError, RuntimeError):
                    pass  # already gone (widget or receiver torn down)
            signal.connect(handler)
        widget._emissive_group_handlers = handlers

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        table = self.ui.tbl000
        self._updating = True
        try:
            groups = EmissiveGroups.list_groups()
            table.setRowCount(len(groups))
            QtWidgets = self.sb.QtWidgets
            Qt = self.sb.QtCore.Qt
            flags_ro = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            for row, (name, data) in enumerate(groups.items()):
                label = f"{name} (missing)" if data["missing"] else name
                values = (
                    label,
                    str(data["slot"]),
                    f"{data['default']:g}",
                    str(data["faces"]),
                )
                for col, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    if col != self.WEIGHT_COL:
                        item.setFlags(flags_ro)
                    if col in (1, self.WEIGHT_COL, 3):
                        item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row, col, item)
        finally:
            self._updating = False

    def _group_at(self, row: int) -> Optional[str]:
        item = self.ui.tbl000.item(row, 0)
        return item.text().replace(" (missing)", "") if item else None

    def _selected_group(self) -> Optional[str]:
        row = self.ui.tbl000.currentRow()
        return self._group_at(row) if row >= 0 else None

    def _set_weight(self, row: int, weight: float) -> None:
        name = self._group_at(row)
        if not name:
            return
        EmissiveGroups.set_default(name, weight)
        self._refresh_table()

    def _on_cell_changed(self, row, col) -> None:
        if self._updating or col != self.WEIGHT_COL:
            return
        try:
            weight = float(self.ui.tbl000.item(row, col).text())
        except (TypeError, ValueError):
            self._refresh_table()  # revert an unparseable entry
            return
        self._set_weight(row, weight)

    # Scrub-edit (MMB drag over the Weight cell) --------------------------
    #
    # A drag emits a move per mouse event, so the scene write is deferred to
    # release: the moves only repaint the cell (each ``set_default`` writes
    # the registry node AND republishes the export manifest — doing that per
    # pixel would spam the undo queue and stall the drag).

    def _on_scrub_started(self, row, col) -> None:
        name = self._group_at(row)
        groups = EmissiveGroups.list_groups()
        self._scrub_start = groups[name]["default"] if name in groups else None
        self._scrub_value = None

    def _on_scrub_moved(self, row, col, dx, dy) -> None:
        if self._scrub_start is None:
            return
        weight = self._scrub_start + (dx / self.SCRUB_PX_PER_UNIT)
        self._scrub_value = max(0.0, min(1.0, weight))
        item = self.ui.tbl000.item(row, self.WEIGHT_COL)
        if item is None:
            return
        self._updating = True  # preview only — don't round-trip through the engine
        try:
            item.setText(f"{self._scrub_value:g}")
        finally:
            self._updating = False

    def _on_scrub_finished(self, row, col) -> None:
        if self._scrub_start is not None and self._scrub_value is not None:
            self._set_weight(row, self._scrub_value)
        self._scrub_start = None
        self._scrub_value = None

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def b000(self) -> None:
        """Add (or extend) a group from the selection."""
        name = self.ui.txt000.text().strip()
        if not name:
            name = f"group_{len(EmissiveGroups.list_groups())}"
        try:
            EmissiveGroups.add_group(name)
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.ui.txt000.clear()
        self._refresh_table()

    def b001(self) -> None:
        """Remove the selected group (retires its slot)."""
        self.remove_group()

    def b002(self) -> None:
        """Select the group's member faces."""
        self.select_members()

    def b003(self) -> None:
        """Validate authoring state."""
        warnings = EmissiveGroups.validate()
        self.sb.message_box(
            "<br>".join(warnings) if warnings else "Emissive groups: clean."
        )
        self._refresh_table()

    # Bake (option box) ----------------------------------------------------

    def tb000_init(self, widget) -> None:
        """Initialize Bake."""
        widget.option_box.menu.setTitle("Bake")
        # Qt class name + addItems on the returned widget — passing a uitk
        # class name here silently yields a QLabel.
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb000",
            setToolTip=self.sb.tooltip.fmt(
                title="Encoding",
                body="How group membership is carried to the engine.",
                bullets=[
                    "<b>Vertex Color</b> — membership rides the FBX in a color "
                    "set. No textures, but it claims the mesh's single engine "
                    "color stream.",
                    "<b>Mask Texture</b> — membership is rasterized into an "
                    "<code>_EMask</code> image. Use it for emissive detail "
                    "painted inside a face.",
                ],
                notes=[
                    "Either way a group occupies one RGBA channel, so a model "
                    "carries at most 4 of them.",
                ],
            ),
        )
        cmb.addItems(["Vertex Color", "Mask Texture"])
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Resolution: ",
            setObjectName="s000",
            set_limits=[64, 8192],
            setValue=512,
            setToolTip=self.sb.tooltip.fmt(
                title="Resolution",
                body="Pixel size of the baked <code>_EMask</code> image.",
                notes=[
                    "Masks are chunky — 512 usually suffices, independent of "
                    "the emissive map's own resolution.",
                    "<b>Mask Texture</b> encoding only.",
                ],
            ),
        )
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Padding: ",
            setObjectName="s001",
            set_limits=[0, 64],
            setValue=4,
            setToolTip=self.sb.tooltip.fmt(
                title="Padding",
                body="Edge padding, in pixels, bled outside each region.",
                notes=[
                    "Keep it at or above the emissive bake's own padding, or "
                    "seams darken under mipping.",
                    "<b>Mask Texture</b> encoding only.",
                ],
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Force Over Foreign Color Set",
            setObjectName="chk000",
            setToolTip=self.sb.tooltip.fmt(
                title="Force Over Foreign Color Set",
                body="Bake even when a mesh already carries an unrelated "
                "color set.",
                notes=[
                    "The engine imports a single color stream, so the groups may "
                    "not survive the import.",
                    "<b>Vertex Color</b> encoding only.",
                ],
            ),
        )

    def tb000(self, widget) -> None:
        """Bake membership and publish the export manifest."""
        menu = widget.option_box.menu
        try:
            if menu.cmb000.currentIndex() == 0:
                EmissiveGroups.bake_vertex_colors(force=menu.chk000.isChecked())
                message = "Vertex colors baked and manifest published."
            else:
                manifest = EmissiveGroups.bake_mask(
                    resolution=menu.s000.value(), padding_px=menu.s001.value()
                )
                message = f"Mask baked: {manifest.get('mask', '')}"
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.sb.message_box(message)

    # Table context menu ---------------------------------------------------

    def select_members(self) -> None:
        name = self._selected_group()
        if not name:
            self.sb.message_box("Select a group row first.")
            return
        try:
            EmissiveGroups.select_group(name)
        except ValueError as error:
            self.sb.message_box(str(error))

    def remove_group(self) -> None:
        name = self._selected_group()
        if not name:
            self.sb.message_box("Select a group row first.")
            return
        EmissiveGroups.remove_group(name)
        self._refresh_table()

    def weights_all_on(self) -> None:
        self._set_all_weights(1.0)

    def weights_all_off(self) -> None:
        self._set_all_weights(0.0)

    def _set_all_weights(self, weight: float) -> None:
        for name in EmissiveGroups.list_groups():
            EmissiveGroups.set_default(name, weight)
        self._refresh_table()

    def make_weights_keyable(self) -> None:
        try:
            plugs = EmissiveGroups.make_weights_keyable()
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.sb.message_box(
            f"Keyable weight(s) added for {len(plugs)} group(s) on the "
            "data_export carrier; manifest published."
        )

    def key_weights(self) -> None:
        keyable = [
            name
            for name, data in EmissiveGroups.list_groups().items()
            if data.get("attr")
        ]
        if not keyable:
            self.sb.message_box(
                "No keyable weights — run Make Weights Keyable first."
            )
            return
        for name in keyable:
            EmissiveGroups.key_weight(name)
        self.sb.message_box(f"Keyed {len(keyable)} weight(s) at the current frame.")

    def remove_keyable_weights(self) -> None:
        removed = EmissiveGroups.remove_keyable_weights()
        self.sb.message_box(
            f"Removed keyable weight(s): {', '.join(removed)}."
            if removed
            else "No keyable weights."
        )

    # Header menu ----------------------------------------------------------

    def compact_slots(self) -> None:
        reclaimed = EmissiveGroups.compact_slots()
        self.sb.message_box(
            f"Reclaimed slot(s): {reclaimed}. Re-bake and re-export."
            if reclaimed
            else "No retired slots."
        )
        self._refresh_table()

    def republish_export(self) -> None:
        payload = EmissiveGroups.refresh_export_metadata()
        self.sb.message_box(
            "Manifest republished." if payload else "No groups; carrier cleared."
        )


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("emissive_groups", reload=True)
    ui.show(pos="screen", app_exec=True)
