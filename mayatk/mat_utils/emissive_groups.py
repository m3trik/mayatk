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
  ``emissive_registry`` on the ``data_internal`` node. Slots are assigned once
  and never reshuffled — downstream Unity scenes key against them. Removing
  a group *retires* its slot; :meth:`EmissiveGroups.compact_slots` is the
  explicit, binding-breaking reclaim.
- Export manifest: regenerated onto ``data_export.emissive_groups`` before
  every FBX export (``FbxUtils.PRODUCERS`` -> :meth:`EmissiveGroups.export_record`);
  Unity's
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
except ImportError:
    pass

import pythontk as ptk

# From this package:
from mayatk.node_utils.data_nodes import DataNodes


class _EmissiveGroupsInternal:
    """Implementation-detail base for :class:`EmissiveGroups`."""

    SET_PREFIX = "emissiveGroup_"
    COLOR_SET = "emissiveGroups"
    #: The ``data_export`` manifest's channel
    #: (``ptk.SceneRecords.EMISSIVE_GROUPS``); the ``data_internal``
    #: registry keeps its own (``ptk.SceneRecords.EMISSIVE_REGISTRY``).
    DATA_CHANNEL = ptk.SceneRecords.EMISSIVE_GROUPS.key

    # ------------------------------------------------------------------
    # Registry — slot bookkeeping lives in the shared engine; this class
    # only binds it to the scene carrier (``data_internal``).
    # ------------------------------------------------------------------

    @classmethod
    def _registry(cls) -> "ptk.RegionGroupRegistry":
        return ptk.RegionGroupRegistry(
            load=lambda: ptk.SceneRecords.EMISSIVE_REGISTRY.read_text(DataNodes),
            save=lambda text: ptk.SceneRecords.EMISSIVE_REGISTRY.write_text(
                DataNodes, text
            ),
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
        source = [
            str(s) for s in (source if isinstance(source, (list, tuple)) else [source])
        ]
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
        if ptk.SceneRecords.EMISSIVE_GROUPS.is_present(DataNodes):
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
    def add_group(cls, name: str, faces=None, default: float = 1.0) -> str:
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
            name = node[len(cls.SET_PREFIX) :]
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
                    and attr[len(cls.SET_PREFIX) :] not in known
                ):
                    warnings.append(
                        f"Carrier attr {attr!r} has no registry entry — a stale "
                        "keyable weight (removed or imported group); re-add the "
                        f"group or remove_keyable_weights"
                        f"([{attr[len(cls.SET_PREFIX) :]!r}])."
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
        :class:`ptk.RegionMaskPacker`; the manifest is published to the FBX
        carrier only -- no sidecar beside the mask.

        Parameters:
            output_path: Mask image path. Defaults to
                ``<scene project texture folder>/<scene>_EMask.png``
                (``DataNodes.scene_source_images_dir``: the project the scene
                is saved in, not merely the active one -- the folder
                blendertk writes for a file in that project).
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
            from mayatk.node_utils.data_nodes import DataNodes

            folder = DataNodes.scene_source_images_dir()
            if not folder:
                raise ValueError(
                    "No project to write the mask into; save the scene, set a "
                    "project, or pass output_path."
                )
            scene = DataNodes.scene_path()
            stem = os.path.splitext(os.path.basename(scene))[0] or "untitled"
            output_path = os.path.join(folder, f"{stem}_EMask.png")
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
        manifest = packer.write(output_path, write_manifest=False)

        registry.set_encoding(
            "channels", mask=os.path.basename(output_path), uv_channel=0
        )
        published = cls.refresh_export_metadata()
        cls.logger.info(f"Baked mask: {output_path}")
        return json.loads(published) if published else manifest.to_dict()

    # ------------------------------------------------------------------
    # Scene-record crossings (``DataNodes.OWNERS``)
    # ------------------------------------------------------------------

    @classmethod
    def transfer_out(cls, ctx: "ptk.TransferContext") -> Optional[dict]:
        """The emissive registry's hand-off payload: the registry, each
        group's membership as ``{group: {object: [face index, ...]}}`` and
        each member object's face count -- the far side has no set to read,
        and a face index names the same face only on a mesh whose face count
        survived the crossing.  Objects are spelled as the carrier writes them
        (``ctx.rename``) and scoped to ``ctx.objects``; ``None`` without
        groups.  Mirror of blendertk's, which reads the same payload."""
        registry = ptk.SceneRecords.EMISSIVE_REGISTRY.load(DataNodes)
        if not registry or not registry.get("groups"):
            return None
        spell = ctx.rename or str
        scope = (
            None
            if ctx.objects is None
            else set(cmds.ls([str(o) for o in ctx.objects], long=True) or [])
        )
        members: Dict[str, Dict[str, List[int]]] = {}
        faces: Dict[str, int] = {}
        for name in registry["groups"]:
            by_object: Dict[str, List[int]] = {}
            for comp in cls._member_faces(name, flatten=True):
                node, _, index = comp.partition(".f[")
                transform = cls._transform(node)
                if not transform or (scope is not None and transform not in scope):
                    continue
                spelled = spell(transform)
                by_object.setdefault(spelled, []).append(int(index.rstrip("]")))
                faces.setdefault(spelled, int(cmds.polyEvaluate(transform, face=True)))
            if by_object:
                members[name] = {obj: sorted(set(ix)) for obj, ix in by_object.items()}
        return {"registry": registry, "members": members, "faces": faces}

    @classmethod
    def transfer_in(cls, payload: dict, ctx: "ptk.TransferContext") -> None:
        """Land a received emissive-registry payload (:meth:`transfer_out`'s
        shape, from either DCC): the registry merges by its rule -- a group
        whose slot is taken here is re-slotted, noted -- and each group's
        faces join its set on the object ``ctx.rename`` resolves.  An object
        whose face count changed in the crossing keeps no membership (noted):
        its indices would name other faces."""
        registry = (payload or {}).get("registry")
        if registry:
            ptk.RecordTransfer.merge_record(
                DataNodes, ptk.SceneRecords.EMISSIVE_REGISTRY, registry, ctx
            )
        counts = (payload or {}).get("faces") or {}
        known = {g["name"] for g in cls._registry().groups()}
        for name, by_object in ((payload or {}).get("members") or {}).items():
            if name not in known:
                continue  # the merge left it out (no free slot), and said so
            comps: List[str] = []
            for spelled, indices in by_object.items():
                node = ctx.rename(spelled) if ctx.rename else spelled
                if not node or not cmds.objExists(node):
                    ctx.note(
                        f"Emissive group {name!r}: {spelled!r} did not arrive; "
                        "its faces were not added."
                    )
                    continue
                count = int(cmds.polyEvaluate(node, face=True))
                if counts.get(spelled) not in (None, count):
                    ctx.note(
                        f"Emissive group {name!r}: {spelled!r} has {count} faces "
                        f"here and had {counts[spelled]}; its membership was not "
                        "restored."
                    )
                    continue
                comps.extend(f"{node}.f[{int(i)}]" for i in indices)
            if comps:
                cls.add_group(name, comps)

    @classmethod
    def merge_carrier(cls, carriers, other, ctx) -> None:
        """Another scene's groups merged into the registry: a member set the
        import renamed (``emissiveGroup_glow1``: this scene has that group
        too) or left namespaced joins the canonical set -- groups merge by
        name, members included."""
        for set_node, canonical in cls._foreign_sets(other, ctx):
            if cmds.objExists(canonical):
                members = cmds.sets(set_node, q=True) or []
                # Only with members: `sets -add` given none falls back to the
                # live selection, which would join the group.
                if members:
                    cmds.sets(members, add=canonical)
                cmds.delete(set_node)
            else:
                cmds.rename(set_node, canonical)

    @classmethod
    def discard_carrier(cls, carriers, other, ctx) -> None:
        """Another scene's groups were dropped: delete the member sets it
        brought, which no registry entry describes any more."""
        for set_node, _canonical in cls._foreign_sets(other, ctx, renamed_only=False):
            cmds.delete(set_node)

    @classmethod
    def _foreign_sets(cls, other, ctx, renamed_only: bool = True):
        """``(set, canonical name)`` for each member set the other scene's
        registry names that the import brought in: renamed or namespaced ones
        (a clash with this scene's set), plus -- unless *renamed_only* -- those
        that arrived under their own name (this scene had none)."""
        registry = other.get(ptk.SceneRecords.EMISSIVE_REGISTRY) or {}
        for name in registry.get("groups") or {}:
            canonical = cls._set_node(name)
            arrived = ctx.spell(canonical)
            if not cmds.objExists(arrived) or cmds.nodeType(arrived) != "objectSet":
                continue
            if arrived != canonical or not renamed_only:
                yield arrived, canonical

    @staticmethod
    def _transform(node: str) -> Optional[str]:
        """*node*'s transform as a long name (a shape's parent), else ``None``."""
        found = cmds.ls(node, long=True) or []
        if not found:
            return None
        if cmds.nodeType(found[0]) == "transform":
            return found[0]
        parent = cmds.listRelatives(found[0], parent=True, fullPath=True) or []
        return parent[0] if parent else None

    # ------------------------------------------------------------------
    # Export carrier
    # ------------------------------------------------------------------

    @classmethod
    def export_record(cls, ctx: "ptk.ExportContext") -> Optional["ptk.Record"]:
        """The ``emissive_groups`` manifest record, or ``None`` when no group
        exists -- the ``ptk.SceneRecords.EMISSIVE_GROUPS`` producer
        (``FbxUtils.PRODUCERS``), read Unity-side by
        ``EmissiveGroupController``'s importer.  Pure: it reads the registry
        and never writes.

        Parameters:
            ctx: The export's decisions (unused: the manifest is a function of
                the registry alone).
        """
        manifest = cls._registry().manifest(color_set=cls.COLOR_SET)
        if manifest is None:
            return None
        return ptk.SceneRecords.EMISSIVE_GROUPS.make(manifest.to_dict())

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``emissive_groups`` channel on the ``data_export``
        carrier from the registry.

        The authoring-time publish of :meth:`export_record`, committed through
        ``FbxUtils.publish_authored`` (an export pipeline runs the producer
        itself: ``FbxUtils.PRODUCERS``).  Clears the channel when no groups
        exist (no empty carrier left behind).

        Returns:
            The published JSON string, or None when cleared.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        FbxUtils.publish_authored({ptk.SceneRecords.EMISSIVE_GROUPS: record})
        return record.text if record is not None else None
