# !/usr/bin/python
# coding=utf-8
"""Import a Blender scene (.blend) into Maya via a headless-Blender FBX round-trip.

The pull-direction sibling of :class:`BlenderBridge` (which pushes the Maya selection
to a fresh interactive Blender), and the mayatk mirror of blendertk's
``MayaSceneImport`` / ``btk.import_maya_scene`` (name + behavior, per the ecosystem
parity rule). A pull inverts the hand-off pipeline -- the input is a *path*, the
payload is produced *Blender-side*, and the caller needs the result -- so it
deliberately does NOT subclass :class:`pythontk.ScriptLaunchBridge`; the shared pieces
are the :class:`pythontk.AppSpec` discovery (borrowed from ``_blender_bridge._SPEC``),
the ``__KEY__`` template renderer, and pythontk's blocking
:func:`~pythontk.run_script_to_artifact` runner.

Flow: render ``templates/_import_scene.py`` -> run it under
``blender --background --factory-startup`` (fresh process every time -- the ecosystem
session-safety rule; factory startup also skips the user's addons/config) -> the
script opens the .blend and exports an FBX + a texture manifest -> ``cmds.file`` (FBX
plugin) brings it in -> materials whose textures FBX cannot carry are rebuilt
natively from the manifest via the :class:`~mayatk.mat_utils.game_shader.GameShader`
engine -> temp payload removed on success, kept + logged on failure
(``TempArtifacts`` scoped policy).

``import maya.cmds`` stays deferred (inside the import methods) so this surface
resolves without a running Maya. Requires a local Blender install (no license --
unlike the reverse direction, the conversion is free and fast).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

import pythontk as ptk
from pythontk.core_utils import script_template as _templates

from mayatk.env_utils.blender_bridge._blender_bridge import _SPEC, _TEMPLATE_DIR

_IMPORT_TEMPLATE = _TEMPLATE_DIR / "_import_scene.py"

# Blender scene format bpy.ops.wm.open_mainfile accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".blend",)

# Child-process argv for the conversion Blender: headless, factory settings (no
# user addons/config -- deterministic AND skips any startup toolkit the user's
# Blender autoloads), then our rendered script.
_LAUNCH_ARGS = ("--background", "--factory-startup", "--python")


def _fbx_safe_name(name: str) -> str:
    """*name* as Maya's FBX importer will spell it (illegal chars -> ``FBXASC###``).

    Blender allows ``.`` / spaces / leading digits in datablock names
    ("Material.001"); Maya's FBX plugin encodes each illegal character as
    ``FBXASC`` + its 3-digit ASCII code, a leading digit included (verified
    live against Maya 2025: ``dotted.001`` -> ``dottedFBXASC046001``,
    ``1digit`` -> ``FBXASC049digit``).
    """
    out = []
    for i, ch in enumerate(name):
        legal = ("a" <= ch <= "z" or "A" <= ch <= "Z" or ch == "_"
                 or (ch.isdigit() and i > 0))
        out.append(ch if legal else "FBXASC%03d" % ord(ch))
    return "".join(out)


def _maya_safe_name(name: str) -> str:
    """A readable legal Maya node name for a REBUILT network (illegal -> ``_``).

    Distinct from :func:`_fbx_safe_name`: that models the importer for
    *matching*; this is the cosmetic spelling for nodes we create ourselves.
    """
    safe = re.sub(r"[^0-9A-Za-z_]", "_", name) or "rebuilt_material"
    return ("_" + safe) if safe[0].isdigit() else safe


def _matches_fbx_name(candidate: str, want: str) -> bool:
    """True when *candidate* is *want* modulo Maya's clash-rename digit suffix."""
    if candidate == want:
        return True
    return candidate.startswith(want) and candidate[len(want):].isdigit()


class BlenderSceneImport(ptk.LoggingMixin):
    """Engine: convert a .blend to FBX via headless Blender, then import it.

    Scriptable and synchronous; async affordances belong to the calling UI layer.
    """

    def __init__(self, blender_path: Optional[str] = None, log_level: str = "INFO"):
        super().__init__()
        self.logger.setLevel(log_level)
        self._blender_path = blender_path

    # ------------------------------------------------------------------ discovery
    @property
    def blender_path(self) -> Optional[str]:
        """The Blender executable (explicit, or discovered via the bridge's AppSpec)."""
        if not self._blender_path:
            self._blender_path = _SPEC.app.resolve()
        return self._blender_path

    @blender_path.setter
    def blender_path(self, value: Optional[str]) -> None:
        self._blender_path = value

    def require_blender(self) -> str:
        """Return :attr:`blender_path` or raise the spec's not-found error."""
        blender_exe = self.blender_path
        if not blender_exe:
            raise FileNotFoundError(_SPEC.app.not_found_message)
        return blender_exe

    # ------------------------------------------------------------------ conversion
    def render_script(
        self, src_path: str, out_fbx: str, *, embed_textures: bool = False,
        include_animation: bool = True,
    ) -> str:
        """Render the Blender-side conversion script (exposed for tests/preview)."""
        context = {
            "SRC_PATH": str(src_path).replace("\\", "/"),
            "OUT_FBX": str(out_fbx).replace("\\", "/"),
            "EMBED_TEXTURES": repr(bool(embed_textures)),
            "INCLUDE_ANIMATION": repr(bool(include_animation)),
        }
        return _templates.render_template(_IMPORT_TEMPLATE, context)

    def convert(
        self, src_path: str, out_fbx: str, *, timeout: float = 600, **script_opts: Any
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_fbx* in a fresh headless Blender (blocking)."""
        src = os.path.abspath(os.path.expandvars(str(src_path)))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Blender scene not found: {src}")
        if not src.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported scene format: {src} (expected {SUPPORTED_EXTENSIONS})"
            )
        blender_exe = self.require_blender()
        self.logger.info(f"Converting {os.path.basename(src)} via {blender_exe} ...")
        result = self._run_script(
            blender_exe,
            self.render_script(src, out_fbx, **script_opts),
            artifact=out_fbx,
            timeout=timeout,
        )
        self.logger.info(
            f"Converted to FBX in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the Blender run without patching pythontk internals).
    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.run_script_to_artifact(
            app_exe,
            script_text,
            artifact=artifact,
            launch_args=lambda script_path: [*_LAUNCH_ARGS, script_path],
            timeout=timeout,
            env=env,
        )

    @staticmethod
    def _cache_key(src: str, script_opts: Dict[str, Any]) -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Blender-side options that shape the FBX, and the
        conversion template's own identity -- a template fix must invalidate
        stale cached payloads, or a retry after an upgrade replays the old bug."""
        stat = os.stat(src)
        tpl = os.stat(_IMPORT_TEMPLATE)
        blob = (
            f"{src}|{stat.st_mtime_ns}|{stat.st_size}|{sorted(script_opts.items())}"
            f"|{tpl.st_mtime_ns}|{tpl.st_size}"
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ import
    def import_scene(
        self,
        src_path: str,
        *,
        cleanup: bool = True,
        use_cache: bool = True,
        timeout: float = 600,
        fbx_options: Optional[Dict[str, Any]] = None,
        **script_opts: Any,
    ) -> List[str]:
        """Import the Blender scene at *src_path*; return the transforms created.

        Parameters:
            src_path: A ``.blend`` file.
            cleanup: Remove the intermediate FBX on success (kept on failure
                either way, with its path logged, for debugging). Not applied
                to cached payloads -- persistence is the cache's point.
            use_cache: Reuse a prior conversion of the identical scene
                (path + mtime + size + options key) -- a cache hit skips the
                Blender launch entirely. Cached payloads live in the temp dir
                under the detached-policy lifecycle (stale-swept after
                ``max_age_days``). Texture edits flow through even on a hit:
                the payload references textures on disk (``embed_textures``
                defaults off), so Maya always loads the current files.
            timeout: Max seconds for the Blender-side conversion.
            fbx_options: Forwarded to ``cmds.file`` for the FBX import.
            **script_opts: Blender-side knobs (``embed_textures`` / ``include_animation``).
        """
        src = os.path.abspath(os.path.expandvars(str(src_path)))
        use_cache = use_cache and os.path.isfile(src)
        cache_fbx = None
        if use_cache:
            store = ptk.TempArtifacts("blender_to_mtk_cache", policy="detached")
            cache_fbx = store.path(
                extension=".fbx", name=self._cache_key(src, script_opts)
            )

        tmp = None
        if cache_fbx and os.path.isfile(cache_fbx) and os.path.getsize(cache_fbx) > 0:
            out_fbx = cache_fbx
            self.logger.info(
                f"Conversion cache hit ({os.path.basename(cache_fbx)}) -- "
                "skipping the Blender launch."
            )
        else:
            # Conversion always targets scoped SCRATCH; a completed conversion
            # is then atomically promoted into the cache slot. A timeout-killed
            # partial write can therefore never poison the cache (the failure
            # stays in scratch, kept + logged for debugging), and concurrent
            # imports of the same scene can't interleave into one file.
            tmp = ptk.TempArtifacts("blender_to_mtk", policy="scoped")
            out_fbx = tmp.path(extension=".fbx")
            tmp.register(out_fbx + ".manifest.json")
            try:
                self.convert(src, out_fbx, timeout=timeout, **script_opts)
            except Exception:
                if os.path.isfile(out_fbx):
                    self.logger.warning(
                        f"Keeping intermediate FBX for debugging: {out_fbx}"
                    )
                raise
            if cache_fbx:
                os.replace(out_fbx, cache_fbx)
                if os.path.isfile(out_fbx + ".manifest.json"):
                    os.replace(out_fbx + ".manifest.json",
                               cache_fbx + ".manifest.json")
                elif os.path.isfile(cache_fbx + ".manifest.json"):
                    os.remove(cache_fbx + ".manifest.json")  # stale partial promote
                out_fbx = cache_fbx

        # Sidecar the template writes for the textures FBX cannot carry
        # (metallic/roughness/ao and the packed game-engine maps).
        manifest_path = out_fbx + ".manifest.json"
        try:
            new_nodes = self._import_fbx(out_fbx, fbx_options)
        except Exception:
            if tmp is not None and os.path.isfile(out_fbx):
                self.logger.warning(f"Keeping intermediate FBX for debugging: {out_fbx}")
            raise
        if os.path.isfile(manifest_path):
            # Structurally non-fatal: a bad sidecar must never abort an
            # import whose FBX already landed (materials just stay classic).
            try:
                self._apply_texture_manifest(manifest_path, new_nodes)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"Texture-manifest rebuild failed ({e}); keeping FBX materials."
                )
        if cleanup and tmp is not None:
            tmp.cleanup()
        imported = self._transforms(new_nodes)
        self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
        return imported

    # ------------------------------------------------------------------ Maya side
    @staticmethod
    def _transforms(nodes: List[str]) -> List[str]:
        """The transform subset of *nodes* (behavior parity: blendertk's
        ``import_scene`` returns Blender objects, i.e. transform-level items)."""
        import maya.cmds as cmds

        return cmds.ls(nodes, type="transform") or []

    def _import_fbx(
        self, fbx_path: str, fbx_options: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Import *fbx_path* into the current scene; return ALL new nodes
        (the manifest apply needs the shading engines, not just transforms)."""
        import maya.cmds as cmds

        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        options = dict(
            i=True,
            type="FBX",
            ignoreVersion=True,
            returnNewNodes=True,
        )
        options.update(fbx_options or {})
        return cmds.file(fbx_path, **options) or []

    def _apply_texture_manifest(self, manifest_path: str, new_nodes: List[str]) -> None:
        """Rebuild manifest materials natively from the conversion's sidecar.

        The FBX carries only the classic-model approximation (color / normal /
        emissive); the manifest carries each textured material's ORIGINAL image
        files, which the game-shader engine (:class:`GameShader`) wires into a
        standardSurface network -- including the packed game-engine maps FBX has
        no slot for (``Metallic_Smoothness``, ``MSAO``, ``ORM``), smoothness ->
        roughness inversion and channel splits included. Classification is by
        filename via the shared ``ptk.MapFactory`` SSoT, so conventionally named
        sets round-trip; an entry whose files classify to nothing keeps its
        FBX material (logged). Per-entry failures degrade, never abort the import.
        """
        import json

        import maya.cmds as cmds

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            self.logger.warning(f"Texture manifest unreadable ({e}); keeping FBX materials.")
            return
        if not isinstance(manifest, dict):
            self.logger.warning("Texture manifest malformed; keeping FBX materials.")
            return

        # Imported shading engines by their surface material's short name --
        # the Maya analogue of blendertk's slot scan. Restricting to NEW nodes
        # keeps a pre-existing same-named scene material out of the swap.
        sgs_by_material: Dict[str, List[str]] = {}
        for sg in cmds.ls(new_nodes, exactType="shadingEngine") or []:
            sources = cmds.listConnections(
                f"{sg}.surfaceShader", source=True, destination=False
            )
            if sources:
                short = sources[0].split("|")[-1].split(":")[-1]
                sgs_by_material.setdefault(short, []).append(sg)

        # Fallback matching only (see below): new transforms by FBX-safe short name.
        by_short: Dict[str, List[str]] = {}
        for node in cmds.ls(new_nodes, type="transform") or []:
            short = node.split("|")[-1].split(":")[-1]
            by_short.setdefault(_fbx_safe_name(short), []).append(node)

        entries = manifest.get("materials", [])
        # Every entry's exact FBX-spelled target. The clash-rename suffix match
        # below must never claim a name that is ANOTHER entry's exact target --
        # "M_test" (renamed by the importer) must not steal "M_test2"'s SGs.
        wants = {_fbx_safe_name(e.get("fbx_material") or "") for e in entries
                 if isinstance(e, dict)}
        # Nor a name that truly exists in the .blend at all: the importer only
        # renames on CLASH, so an exact .blend spelling seen among the imported
        # SGs is its own material -- an UNTEXTURED "Mat2" beside textured "Mat"
        # has no manifest entry, and without this it would read as "Mat renamed
        # to Mat2" and get repainted with Mat's rebuilt textures. Older
        # manifests lack the key; the entries-only guard above still applies.
        wants |= {
            _fbx_safe_name(n)
            for n in manifest.get("scene_materials", [])
            if isinstance(n, str)
        }

        def target_sgs(want: str) -> Dict[str, List[str]]:
            """Shading groups for *want*: exact importer spelling first; else
            tolerate Maya's rename-on-clash digit suffix (sibling-safe)."""
            if want in sgs_by_material:
                return {want: sgs_by_material[want]}
            return {
                short: sgs for short, sgs in sgs_by_material.items()
                if _matches_fbx_name(short, want) and short not in wants
            }

        for entry in entries:
            name = entry.get("name", "?")
            try:
                listed = entry.get("files", [])
                files = [f for f in listed if os.path.isfile(f)]
                if not files:
                    # Never silent: untextured materials with no explanation
                    # cost a debugging session (live production report on the
                    # mirror direction).
                    if listed:
                        self.logger.warning(
                            f"{name}: manifest texture file(s) missing on disk, "
                            f"e.g. {listed[0]} -- material stays untextured."
                        )
                    else:
                        self.logger.warning(
                            f"{name}: no texture paths resolved during conversion "
                            "-- the .blend's images may be packed into the file "
                            "or need relinking. Material stays untextured."
                        )
                    continue
                new_sg = self._rebuild_material(files, name)
                if new_sg is None:  # nothing classified -- keep the FBX material
                    self.logger.warning(
                        f"{name}: no texture classified by filename; keeping the "
                        "FBX-carried material."
                    )
                    continue

                # Primary: transfer members at the SHADING-GROUP level, keyed by
                # the FBX-spelled material name (the importer may rename-on-clash
                # with a digit suffix). Renderable sets are exclusive, so
                # forceElement moves per-face assignments intact -- the Maya
                # analogue of blendertk's slot-level swap.
                want = _fbx_safe_name(entry.get("fbx_material") or "")
                replaced, swapped = [], 0
                if want:
                    for short, sgs in target_sgs(want).items():
                        for old_sg in sgs:
                            members = cmds.sets(old_sg, query=True) or []
                            if members:
                                cmds.sets(members, forceElement=new_sg)
                                swapped += 1
                            old_mats = cmds.listConnections(
                                f"{old_sg}.surfaceShader", source=True,
                                destination=False,
                            ) or []
                            for old in old_mats:
                                if old not in replaced:
                                    replaced.append(old)
                if swapped:
                    self._purge_orphans(replaced)
                    self.logger.info(
                        f"Rebuilt material {name} from {len(files)} "
                        f"file(s) into {swapped} shading group(s)."
                    )
                    continue

                # Fallback (importer renamed the material): whole-object assign.
                targets = [
                    node for member in entry.get("objects", [])
                    for node in by_short.get(_fbx_safe_name(member), [])
                ]
                if not targets:
                    self._purge_rebuilt(new_sg)  # nothing to attach it to
                    self.logger.warning(f"{name}: no matching shading group or object found.")
                    continue
                cmds.sets(targets, forceElement=new_sg)
                self.logger.info(
                    f"Rebuilt material {name} from {len(files)} file(s) "
                    f"on {len(targets)} object(s) (object-level fallback)."
                )
            except Exception as e:
                self.logger.warning(f"Manifest entry {name} skipped: {e}")

    # Seam for tests (stub the GameShader build without live texture prep).
    @staticmethod
    def _rebuild_material(files: List[str], name: str) -> Optional[str]:
        """Build a native shader network from *files*; return its shading group.

        ``standardSurface`` (not Stingray) so the result renders in any viewport
        without the ShaderFX plugin. The packed-map config flags are enabled per
        detected map type -- they gate each packed map as a desired OUTPUT in
        ``MapFactory``'s prep (MSAO's flag is ``mask_map`` per the registry SSoT).
        """
        import maya.cmds as cmds

        from mayatk.mat_utils.game_shader import GameShader

        flags = {
            "Metallic_Smoothness": "metallic_smoothness",
            "MSAO": "mask_map",
            "ORM": "orm_map",
            "Albedo_Transparency": "albedo_transparency",
            "Emissive": "emissive",
            "Ambient_Occlusion": "ambient_occlusion",
            "Opacity": "opacity",
        }
        kwargs: Dict[str, Any] = {"shader_type": "standard_surface"}
        for path in files:
            map_type = ptk.MapFactory.resolve_map_type(path)
            if map_type in flags:
                kwargs[flags[map_type]] = True

        # Blender datablock names ("Material.001") are not legal Maya node
        # names -- sanitize for the created network (matching elsewhere uses
        # the manifest strings, so this is cosmetic only).
        node = GameShader(log_level="WARNING").create_network(
            files, name=_maya_safe_name(name), **kwargs
        )
        if not node:
            return None
        node = str(node)
        if cmds.nodeType(node) == "shadingEngine":
            return node
        sgs = cmds.listConnections(node, type="shadingEngine") or []
        return sgs[0] if sgs else None

    def _purge_orphans(self, materials: List[str]) -> None:
        """Remove replaced materials (their emptied shading groups and
        now-exclusive texture nodes included) once unused.

        Hygiene only -- every step is best-effort and must never break the
        import.
        """
        import maya.cmds as cmds

        for mat in materials:
            try:
                if not cmds.objExists(mat):
                    continue
                sgs = cmds.listConnections(mat, type="shadingEngine") or []
                if any(cmds.sets(sg, query=True) for sg in sgs):
                    continue  # still assigned somewhere -- keep it
                textures = [
                    n for n in (cmds.listHistory(mat) or [])
                    if n != mat
                    and cmds.nodeType(n) in ("file", "place2dTexture", "bump2d")
                ]
                cmds.delete(list(set(sgs)) + [mat])
                for node in textures:
                    if cmds.objExists(node) and not cmds.listConnections(
                        node, source=False, destination=True
                    ):
                        cmds.delete(node)
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Orphan purge skipped: {e}")

    def _purge_rebuilt(self, sg: str) -> None:
        """Delete an unattachable rebuilt network (SG + its surface material)."""
        import maya.cmds as cmds

        try:
            mats = cmds.listConnections(
                f"{sg}.surfaceShader", source=True, destination=False
            ) or []
            self._purge_orphans(mats)
            if cmds.objExists(sg):
                cmds.delete(sg)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Rebuilt-network purge skipped: {e}")


def import_blender_scene(src_path: str, **kwargs: Any) -> List[str]:
    """Import a Blender scene (.blend) into the current Maya scene.

    Convenience wrapper over :meth:`BlenderSceneImport.import_scene` -- launches a
    fresh headless Blender to convert the scene to FBX, imports the FBX, rebuilds
    manifest materials, and cleans up. Returns the transforms created. Requires a
    local Blender install.
    """
    return BlenderSceneImport().import_scene(src_path, **kwargs)


__all__ = ["BlenderSceneImport", "import_blender_scene"]


