# !/usr/bin/python
# coding=utf-8
"""External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.

Drives :class:`pythontk.UvUnwrap` (Ministry of Flat / Boundary First
Flattening) from Maya. Reached through :meth:`mayatk.UvUtils.auto_unwrap`;
nothing here is called directly.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils


IMPORT_NAMESPACE = "UvUnwrapImport"


@dataclass
class AutoUnwrapResult:
    """Per-object outcome of an :meth:`auto_unwrap` run."""

    engine: str
    succeeded: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.succeeded)


class _AutoUnwrapInternal:
    """Round-trip mechanics for :meth:`mayatk.UvUtils.auto_unwrap`."""

    # Every engine call funnels through here so tests can substitute a stub
    # without needing the real executables.
    @staticmethod
    def _engine_unwrap(obj_in: str, engine: str, **params) -> str:
        return ptk.UvUnwrap.unwrap(obj_in, engine=engine, **params)

    @staticmethod
    def _check_engine(engine: str) -> str:
        """Resolve the executable up front, before the scene is touched."""
        return ptk.UvUnwrap.resolve_engine(engine, required=True)

    @staticmethod
    def _resolve_meshes(objects) -> List[str]:
        """Selection-or-argument to a list of unique mesh transforms.

        Instanced transforms collapse to one representative: they share a
        shape, so unwrapping it once updates every sibling.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        transforms = []
        # Resolved one input at a time: a single batched ``cmds.ls`` returns
        # scene order, which would silently reorder the caller's list (and the
        # per-object results reported back against it).
        for node in CoreUtils.as_strings(objects):
            shapes = (
                cmds.ls(
                    node, dag=True, type="mesh", noIntermediate=True, long=True
                )
                or []
            )
            for shape in shapes:
                parent = cmds.listRelatives(shape, parent=True, fullPath=True)
                transform = parent[0] if parent else shape
                if transform not in transforms:
                    transforms.append(transform)
        if not transforms:
            return []
        # filter_duplicate_instances answers "which of these share a shape?"
        # but doesn't promise input order; re-impose it so the reported
        # per-object results line up with what the caller passed.
        kept = set()
        for node in NodeUtils.filter_duplicate_instances(transforms) or []:
            kept.update(cmds.ls(node, long=True) or [node])
        return [t for t in transforms if t in kept]

    @staticmethod
    def _export_obj(node: str, path: str) -> None:
        """Write *node* to a real Wavefront OBJ.

        Unlike the RizomUV bridge (whose payload is always FBX), the external
        unwrappers only read OBJ, so this uses Maya's objExport translator.
        Materials are excluded — the engines ignore them and would otherwise
        leave a stray .mtl beside the payload.
        """
        cmds.loadPlugin("objExport", quiet=True)
        cmds.select(node, replace=True)
        cmds.file(
            path,
            force=True,
            type="OBJexport",
            exportSelected=True,
            # No groups: one mesh per payload, so group records buy nothing and
            # come back from the import as object-set and groupId nodes to
            # clean up. (BFF drops o/g records anyway.)
            options="groups=0;ptgroups=0;materials=0;smoothing=1;normals=1",
        )

    @staticmethod
    def _import_obj(path: str) -> Tuple[List[str], List[str]]:
        """Import *path* into a scratch namespace.

        Returns ``(mesh_transforms, created_nodes)``. The translator makes more
        than the mesh -- shading and groupId nodes that aren't its children --
        so the full list is what cleanup has to remove.
        """
        created = (
            cmds.file(
                path,
                i=True,
                type="OBJ",
                ignoreVersion=True,
                renameAll=True,
                namespace=IMPORT_NAMESPACE,
                mergeNamespacesOnClash=False,
                options="mo=0",
                returnNewNodes=True,
            )
            or []
        )
        meshes = cmds.ls(created, dag=True, type="mesh", noIntermediate=True) or []
        transforms = []
        for shape in meshes:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True)
            transform = parent[0] if parent else shape
            if transform not in transforms:
                transforms.append(transform)
        return transforms, created

    @classmethod
    def _cleanup_namespace(cls) -> None:
        if cmds.namespace(exists=IMPORT_NAMESPACE):
            try:
                cmds.namespace(
                    removeNamespace=IMPORT_NAMESPACE, mergeNamespaceWithRoot=True
                )
            except RuntimeError:
                pass

    @classmethod
    def run(
        cls,
        uv_utils,
        objects=None,
        method: str = "hard",
        map_size: int = 4096,
        pack: Optional[bool] = None,
        orient: bool = True,
        engine_params: Optional[Dict[str, Any]] = None,
    ) -> AutoUnwrapResult:
        """Unwrap each mesh through an external engine. See ``UvUtils.auto_unwrap``."""
        engine = ptk.UvUnwrap.resolve_method(method)
        meshes = cls._resolve_meshes(objects)
        if not meshes:
            raise ValueError("auto_unwrap: no polygon meshes given or selected.")

        # Resolve the executable before anything mutates the scene, so a
        # missing engine surfaces as a clean error rather than a half-run.
        cls._check_engine(engine)

        params = dict(engine_params or {})
        if engine == "mof":
            # Ministry of Flat derives its island gutter from this.
            params.setdefault("resolution", map_size)
        layout = cls._layout_mode(engine, pack)

        result = AutoUnwrapResult(engine=engine)
        selection = cmds.ls(selection=True, long=True) or []
        try:
            with CoreUtils.undo_chunk(f"Auto Unwrap ({engine})"):
                with ptk.TempArtifacts("uv_unwrap", policy="scoped") as tmp:
                    for mesh in meshes:
                        cls._unwrap_one(
                            uv_utils, mesh, engine, params, map_size, layout,
                            orient, tmp, result,
                        )
        finally:
            cls._cleanup_namespace()
            if selection:
                cmds.select(
                    [s for s in selection if cmds.objExists(s)], replace=True
                )
            else:
                cmds.select(clear=True)
        return result

    @staticmethod
    def _layout_mode(engine: str, pack: Optional[bool]) -> str:
        """What to do with the engine's UVs: ``"pack"`` / ``"fit"`` / ``"none"``.

        The default follows the engine: one that arranges its own islands keeps
        that arrangement and is only scaled into the tile (Ministry of Flat packs
        into a rectangle that overruns 0-1); one that only flattens gets the full
        layout pass.
        """
        if pack is not None:
            return "pack" if pack else "none"
        return "fit" if ptk.UvUnwrap.ENGINES[engine].packs_own_layout else "pack"

    @classmethod
    def _unwrap_one(
        cls, uv_utils, mesh, engine, params, map_size, layout, orient, tmp, result
    ) -> None:
        """Round-trip one mesh, recording success or an isolated failure."""
        snapshot = uv_utils.snapshot_uv_sets([mesh])
        created: List[str] = []
        try:
            payload = tmp.path(extension=".obj")
            cls._export_obj(mesh, payload)
            unwrapped = cls._engine_unwrap(payload, engine, **params)
            tmp.register(unwrapped)

            imported, created = cls._import_obj(unwrapped)
            if not imported:
                raise RuntimeError("engine output contained no mesh")
            if not (cmds.polyEvaluate(imported[0], uvcoord=True) or 0):
                raise RuntimeError("engine output contained no UVs")

            # Both engines return the input topology untouched, so a
            # component-space transfer maps UVs back exactly -- no spatial
            # sampling and no tolerance to tune.
            uv_utils.transfer_uvs(imported[0], mesh, match_by_similarity=False)
            if layout == "pack":
                uv_utils._pack_shells(mesh, map_size=map_size, orient=orient)
            elif layout == "fit":
                # Keep the engine's own island arrangement, just scale it into
                # the tile -- Ministry of Flat packs into a rectangle that
                # routinely overruns 0-1.
                uv_utils._fit_uvs_to_tile(mesh)
        except Exception as error:  # noqa: BLE001 - one bad mesh must not stop the rest
            uv_utils.restore_uv_snapshot(snapshot)
            result.failed.append((mesh, str(error)))
        else:
            uv_utils.discard_uv_snapshot(snapshot)
            result.succeeded.append(mesh)
        finally:
            # One at a time: deleting a transform takes its shape with it, so a
            # batched call would trip over nodes that no longer exist.
            for node in created:
                if cmds.objExists(node):
                    cmds.delete(node)
