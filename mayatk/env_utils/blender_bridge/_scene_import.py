# !/usr/bin/python
# coding=utf-8
"""Import a Blender scene (.blend) into Maya via a headless-Blender round-trip
(FBX intermediate by default; USD per call via ``via="usd"``).

FBX is the default because its instancing is carried by the format itself on both
sides -- no sidecar replay stands between a linked duplicate and a real Maya
instance. The USD route reaches parity by replaying a recorded grouping from the
conversion's v2 sidecar, and that replay is GUARANTEED-OR-FAIL: the import happens
in an isolation namespace (clash-proof name matching) that is deleted wholesale on
a failed replay and merged to the root on success, so a USD pull either preserves
the sharing exactly or fails loudly -- never a silently flattened scene. Pick USD
per call/panel for look-heavy scenes.

The pull-direction sibling of :class:`BlenderBridge` (which pushes the Maya selection
to a fresh interactive Blender), and the mayatk mirror of blendertk's
``MayaSceneImport`` (name + behavior, per the ecosystem parity rule). A pull inverts
the hand-off pipeline -- the input is a *path*, the payload is produced
*Blender-side*, and the caller needs the result -- so it deliberately does NOT
subclass :class:`pythontk.ScriptLaunchBridge`; the shared pieces are the
:class:`pythontk.AppSpec` discovery (borrowed from ``_blender_bridge._SPEC``),
the ``__KEY__`` template renderer, and pythontk's blocking
:func:`~pythontk.run_script_to_artifact` runner.

Flow: render the per-route conversion template (``_TEMPLATES``) -> run it under
``blender --background --factory-startup`` (fresh process every time -- the ecosystem
session-safety rule; factory startup also skips the user's addons/config) -> the
script opens the .blend and exports the intermediate. USD route: materials /
instancing / animation import natively (``UsdUtils``) -- no sidecar. FBX route: an
FBX + texture manifest; ``cmds.file`` (FBX plugin) brings it in and materials whose
textures FBX cannot carry are rebuilt natively from the manifest via the
:class:`~mayatk.mat_utils.game_shader.GameShader` engine. Either way the temp payload
is removed on success, kept + logged on failure (``TempArtifacts`` scoped policy).

``import maya.cmds`` stays deferred (inside the import methods) so this surface
resolves without a running Maya. Requires a local Blender install (no license --
unlike the reverse direction, the conversion is free and fast).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

import pythontk as ptk
from pythontk.core_utils import script_template as _templates

from mayatk.env_utils.blender_bridge._blender_bridge import _SPEC, _TEMPLATE_DIR

# Module-level: the rescue below runs inside a @staticmethod (kept static as a
# test stub seam), so there is no ``self.logger`` to reach for.
_logger = logging.getLogger(__name__)

_IMPORT_TEMPLATE = _TEMPLATE_DIR / "_import_scene.py"
_IMPORT_TEMPLATE_USD = _TEMPLATE_DIR / "_import_scene_usd.py"
_BAKE_TEMPLATE = _TEMPLATE_DIR / "_bake_scene.py"

# Conversion intermediates by route: "fbx" = classic material model + texture-
# manifest sidecar rebuilt via the GameShader engine; "usd" = native materials /
# animation through each DCC's USD runtime, plus a REQUIRED instance sidecar
# (v2) replayed guaranteed-or-fail — see the templates' docstrings.
_TEMPLATES = {"fbx": _IMPORT_TEMPLATE, "usd": _IMPORT_TEMPLATE_USD}

# Blender scene format bpy.ops.wm.open_mainfile accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".blend",)

# Sources bake_scene turns into a referenceable .ma. A .blend needs the headless-Blender
# conversion first; an .fbx is already the bake's own input, so it skips that hop.
BAKE_SOURCE_EXTENSIONS = (".blend", ".fbx")

# Sidecar written beside every bake naming the scene it came from. The Reference Manager
# lists SOURCE rows but references the BAKED file, so "is this row referenced?" can only
# be answered by walking back from a reference to its origin. On disk rather than in panel
# settings so the mapping survives a session change and is shared by every panel.
BAKE_SOURCE_SUFFIX = ".source.json"

# USD sources short-circuit the whole pipeline: both DCCs speak USD natively,
# so there is no conversion (and no Blender install) involved at all.
USD_EXTENSIONS = ptk.USD_EXTENSIONS

# Child-process argv for the conversion Blender: headless, factory settings (no
# user addons/config -- deterministic AND skips any startup toolkit the user's
# Blender autoloads), then our rendered script.
_LAUNCH_ARGS = ("--background", "--factory-startup", "--python")

# Child-process env for the bake mayapy: skip the startup baggage a headless one-shot
# baker never needs. userSetup.py is the big one on pipeline machines (it can bootstrap a
# whole toolkit); the CIP/CER/CLIC analytics trio adds network round-trips. Mirror of the
# blendertk side's conversion env.
_FAST_MAYA_ENV = {
    "MAYA_SKIP_USERSETUP_PY": "1",
    "MAYA_DISABLE_CIP": "1",
    "MAYA_DISABLE_CER": "1",
    "MAYA_DISABLE_CLIC_IPM": "1",
}


class _BlenderSceneImportInternal(object):
    """Internal helpers for BlenderSceneImport."""

    @staticmethod
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
            legal = (
                "a" <= ch <= "z"
                or "A" <= ch <= "Z"
                or ch == "_"
                or (ch.isdigit() and i > 0)
            )
            out.append(ch if legal else "FBXASC%03d" % ord(ch))
        return "".join(out)

    @staticmethod
    def _maya_safe_name(name: str) -> str:
        """A readable legal Maya node name for a REBUILT network (illegal -> ``_``).

        Distinct from :func:`_fbx_safe_name`: that models the importer for
        *matching*; this is the cosmetic spelling for nodes we create ourselves.
        """
        safe = re.sub(r"[^0-9A-Za-z_]", "_", name) or "rebuilt_material"
        return ("_" + safe) if safe[0].isdigit() else safe

    @staticmethod
    def _matches_fbx_name(candidate: str, want: str) -> bool:
        """True when *candidate* is *want* modulo Maya's clash-rename digit suffix."""
        if candidate == want:
            return True
        return candidate.startswith(want) and candidate[len(want) :].isdigit()

    @staticmethod
    def _claim_material_name(sg: str, desired: str) -> str:
        """Rename a rebuilt network to *desired* once that name is free.

        A rebuild is necessarily built while the FBX-carried material still owns
        the name, so Maya hands it the clash spelling ("M_x" -> "M_x1"); the FBX
        one is purged moments later and the name falls free. Reclaiming it keeps
        the hand-off non-destructive -- downstream (Unity, a shader library, an
        FBX round-trip) binds by material NAME, and the digit compounds on every
        re-send.

        Yields silently whenever the name is still taken: the object-level
        fallback runs exactly when the FBX material was never matched, so it may
        still be assigned elsewhere and keeps its claim. Cosmetic and
        best-effort; the caller's material is already correctly assigned.

        :return: The shading group's name, which the rename may have changed.
        """
        import maya.cmds as cmds

        if not desired:
            return sg
        shaders = (
            cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False)
            or []
        )
        if not shaders:
            return sg
        shader = shaders[0]
        old = shader.split("|")[-1].split(":")[-1]
        if old == desired or cmds.objExists(desired):
            return sg
        try:
            cmds.rename(shader, desired)
        except RuntimeError:
            return sg
        # Carry the shading group along so the pair stays legible ("M_xSG" for
        # "M_x"). Two conventions reach here: named after the CREATED shader
        # ("M_x1SG") or after the REQUESTED name plus Maya's own clash digits
        # ("M_xSG1"). Both resolve to "M_xSG"; any other spelling is left alone
        # rather than renamed on a guess.
        short_sg = sg.split("|")[-1].split(":")[-1]
        wanted = ""
        if short_sg.startswith(old):
            wanted = desired + short_sg[len(old) :]
        elif _BlenderSceneImportInternal._matches_fbx_name(short_sg, f"{desired}SG"):
            wanted = f"{desired}SG"
        if wanted and wanted != short_sg and not cmds.objExists(wanted):
            try:
                return cmds.rename(sg, wanted)
            except RuntimeError:
                pass
        return sg


class BlenderSceneImport(ptk.LoggingMixin, _BlenderSceneImportInternal):
    """Engine: convert a .blend to FBX via headless Blender, then import it.

    Scriptable and synchronous; async affordances belong to the calling UI layer.
    """

    def __init__(
        self,
        blender_path: Optional[str] = None,
        log_level: str = "INFO",
        mayapy_path: Optional[str] = None,
    ):
        super().__init__()
        self.logger.setLevel(log_level)
        self._blender_path = blender_path
        # Host interpreter for the FBX -> .ma bake (see the mayapy_path property).
        self._mayapy_path = mayapy_path

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

    # ------------------------------------------------------------------ discovery (browser API)
    @staticmethod
    def find_scenes(
        root_dir: str,
        recursive: bool = False,
        extensions: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Every importable Blender scene (``.blend``) under *root_dir* — sorted abs paths.

        The discovery half of the import: pairs with :meth:`import_scene` so a browser can
        list convertible Blender scenes with one call, using the SAME extension set the
        importer accepts. USD sources are import-capable too but are not *Blender scenes*, so
        they are intentionally excluded here. ``.blend1`` backups never match ``*.blend``.

        *extensions* narrows or widens that default — a browser listing *bakeable* rows
        passes :data:`BAKE_SOURCE_EXTENSIONS` (which adds ``.fbx``), or the subset the
        user has enabled.
        """
        if not (root_dir and os.path.isdir(root_dir)):
            return []
        inc = [f"*{ext}" for ext in (extensions or SUPPORTED_EXTENSIONS)]
        found = ptk.FileUtils.get_dir_contents(
            root_dir, content="filepath", recursive=recursive, inc_files=inc
        )
        return sorted(os.path.normpath(p) for p in found)

    # ------------------------------------------------------------------ conversion
    @staticmethod
    def _template(via: str):
        """The conversion template for *via*; raises on an unknown route."""
        try:
            return _TEMPLATES[via]
        except KeyError:
            raise ValueError(
                f"via must be one of {sorted(_TEMPLATES)}, got {via!r}"
            ) from None

    def render_script(
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        embed_textures: bool = False,
        include_animation: bool = True,
    ) -> str:
        """Render the Blender-side conversion script (exposed for tests/preview)."""
        context = {
            "SRC_PATH": str(src_path).replace("\\", "/"),
            "INCLUDE_ANIMATION": repr(bool(include_animation)),
        }
        if via == "usd":
            context["OUT_USD"] = str(out_path).replace("\\", "/")
            if embed_textures:
                self.logger.info(
                    "embed_textures has no USD-route equivalent (textures are "
                    "referenced on disk); ignored."
                )
        else:
            context["OUT_FBX"] = str(out_path).replace("\\", "/")
            context["EMBED_TEXTURES"] = repr(bool(embed_textures))
        return _templates.ScriptTemplate.render_template(self._template(via), context)

    def convert(
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        timeout: float = 600,
        **script_opts: Any,
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_path* in a fresh headless Blender (blocking)."""
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Blender scene not found: {src}")
        if not src.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported scene format: {src} (expected {SUPPORTED_EXTENSIONS})"
            )
        blender_exe = self.require_blender()
        self.logger.info(f"Converting {os.path.basename(src)} via {blender_exe} ...")
        # The conversion Blender is launched FROM Maya, so it inherits Maya's
        # environment -- an OCIO var pointing inside Maya's own install would
        # override Blender's color management with a Maya-authored config. Same
        # hand-off hazard the send path already handles; reuse the spec's helper
        # (a studio config outside Maya's tree passes through untouched).
        result = self._run_script(
            blender_exe,
            self.render_script(src, out_path, via=via, **script_opts),
            artifact=out_path,
            timeout=timeout,
            env=_SPEC.launch_env(),  # None when there is nothing to strip
        )
        self.logger.info(
            f"Converted to {via.upper()} in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the Blender run without patching pythontk internals).
    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe,
            script_text,
            artifact=artifact,
            launch_args=lambda script_path: [*_LAUNCH_ARGS, script_path],
            timeout=timeout,
            env=env,
        )

    @classmethod
    def _cache_key(cls, src: str, script_opts: Dict[str, Any], via: str = "usd") -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Blender-side options that shape the artifact, and
        the conversion template's own identity (per *via*) -- a template fix
        must invalidate stale cached payloads, or a retry after an upgrade
        replays the old bug."""
        return ptk.CachedArtifact.key(
            sorted(script_opts.items()), files=[src, cls._template(via)]
        )

    def _cached_conversion(
        self,
        src: str,
        *,
        via: str,
        use_cache: bool,
        timeout: float,
        script_opts: Dict[str, Any],
    ) -> "ptk.CachedArtifact.Result":
        """The cached FBX/USD conversion of *src*, produced on a miss.

        Shared by :meth:`import_scene` and :meth:`bake_scene`: both need the SAME
        intermediate, so a scene that was already imported bakes without a second
        Blender launch.
        """
        ext = ".usd" if via == "usd" else ".fbx"
        self._template(via)  # validate the route before any work
        return ptk.CachedArtifact("blender_to_mtk", extension=ext).get(
            self._cache_key(src, script_opts, via),
            lambda out: self.convert(src, out, via=via, timeout=timeout, **script_opts),
            sidecars=(".manifest.json",),
            use_cache=use_cache and os.path.isfile(src),
        )

    # ------------------------------------------------------------------ import
    def import_scene(
        self,
        src_path: str,
        *,
        via: str = "fbx",
        cleanup: bool = True,
        use_cache: bool = True,
        timeout: float = 600,
        fbx_options: Optional[Dict[str, Any]] = None,
        shader_type: str = "stingray",
        **script_opts: Any,
    ) -> List[str]:
        """Import the Blender scene at *src_path*; return the transforms created.

        Parameters:
            src_path: A ``.blend`` file — or a USD file
                (``.usd``/``.usda``/``.usdc``/``.usdz``), which short-circuits
                the round-trip entirely: Maya imports USD natively (mayaUsd),
                so no headless Blender, cache or manifest is involved
                (``via``/``cleanup``/``use_cache``/``timeout``/``fbx_options``
                are inert for USD sources).
            via: Conversion intermediate for ``.blend`` sources. ``"fbx"``
                (default) = the classic material model + texture-manifest
                sidecar rebuilt through the ``GameShader`` engine; instancing
                is carried by the FBX format itself, so linked duplicates
                arrive as real Maya instances with no replay in the path.
                ``"usd"`` = ``wm.usd_export`` → mayaUsd import: materials
                arrive as native UsdPreviewSurface conversions (metallic /
                roughness / normal textures included, no texture manifest)
                and animation survives. Instancing is a recorded grouping
                (the conversion's REQUIRED v2 sidecar) replayed on import,
                GUARANTEED-OR-FAIL: the import lands in an isolation
                namespace, and a failed replay rolls it back wholesale and
                raises instead of leaving a silently flattened scene.
            cleanup: Remove the intermediate artifact on success (kept on
                failure either way, with its path logged, for debugging). Not
                applied to cached payloads -- persistence is the cache's point.
            use_cache: Reuse a prior conversion of the identical scene
                (path + mtime + size + options + per-``via`` template key) --
                a cache hit skips the Blender launch entirely. Cached payloads
                live in the temp dir under the detached-policy lifecycle
                (stale-swept after ``max_age_days``). Texture edits flow
                through even on a hit: the payload references textures on
                disk (``embed_textures`` defaults off), so Maya always loads
                the current files.
            timeout: Max seconds for the Blender-side conversion.
            fbx_options: Forwarded to ``cmds.file`` for the FBX import
                (``via="fbx"`` only; the USD route imports with the native
                defaults).
            shader_type: Which shader the manifest rebuild targets --
                ``GameShader``'s own vocabulary (``stingray`` / ``open_pbr`` /
                ``standard_surface``). Same knob, same default and same
                fallback as the Blender-side send panel's "Rebuild Shader":
                pulling a .blend and being sent one run the identical rebuild,
                so they must not disagree. ``via="fbx"`` only -- the USD route
                imports native UsdPreviewSurface conversions with no manifest.
            **script_opts: Blender-side knobs (``embed_textures`` /
                ``include_animation``; ``embed_textures`` is FBX-route only).
        """
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if os.path.splitext(src)[1].lower() in USD_EXTENSIONS:
            # USD fast path: native import, no headless-Blender round-trip at all.
            from mayatk.env_utils.usd import UsdUtils

            if not os.path.isfile(src):
                raise FileNotFoundError(f"USD file not found: {src}")
            self.logger.info(
                f"USD source — importing natively (no Blender conversion): {src}"
            )
            imported = self._transforms(UsdUtils.import_scene(src))
            self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
            return imported

        got = self._cached_conversion(
            src, via=via, use_cache=use_cache, timeout=timeout, script_opts=script_opts
        )
        out_path, tmp = got.path, got.scratch

        # Both routes sidecar what their intermediate cannot carry. FBX: the
        # textures (metallic/roughness/ao and the packed game-engine maps).
        # USD: materials arrive natively, but instance RELATIONSHIPS do not
        # survive a flattened export -- they are replayed below as real Maya
        # instances (shared shapes under multiple transforms).
        manifest_path = out_path + ".manifest.json"
        if via == "usd" and not os.path.isfile(manifest_path):
            # The v2 conversion ALWAYS writes the sidecar (empty groups included)
            # and withholds the USD when it can't -- a missing manifest means a
            # stale or hand-damaged payload, and importing it could silently
            # flatten linked duplicates. Refuse before touching the scene.
            raise RuntimeError(
                f"USD conversion sidecar missing: {manifest_path}. Nothing was "
                "imported (a flat import could silently lose instancing); clear "
                "the conversion cache or re-pull via FBX."
            )
        try:
            if via == "usd":
                import maya.cmds as cmds

                from mayatk.env_utils.usd import UsdUtils

                # Isolation namespace: prim->node names stay exact for the
                # replay below no matter what the open scene already contains
                # (merged back to the root on success, clash renames and all).
                ns, n = "_usd_pull", 1
                while cmds.namespace(exists=ns):
                    ns, n = f"_usd_pull{n}", n + 1
                new_nodes = UsdUtils.import_scene(out_path, namespace=ns)
            else:
                new_nodes = self._import_fbx(out_path, fbx_options)
                # Empties -> correct node types (the importer makes every FBX
                # null a locator; see the method). The manifest's ``empties``
                # section, when the conversion wrote one, overrides the
                # children-based heuristic. USD needs no repair: Empties
                # travel as Xform prims and arrive as plain transforms.
                self._restore_empty_groups(new_nodes, manifest_path)
        except Exception:
            if via == "usd":
                # UsdUtils creates the isolation namespace BEFORE the file
                # command, so a failed import (corrupt payload) would leak an
                # empty namespace into the scene. Cleanup must never mask the
                # real error (ns/cmds may be unbound if the failure came first).
                try:
                    import maya.cmds as cmds

                    if cmds.namespace(exists=ns):
                        cmds.namespace(removeNamespace=ns, deleteNamespaceContent=True)
                except Exception:  # noqa: BLE001
                    pass
            if tmp is not None and os.path.isfile(out_path):
                self.logger.warning(
                    f"Keeping intermediate {via.upper()} for debugging: {out_path}"
                )
            raise
        if via == "usd":
            # Rebuild real Maya instances from Blender's linked duplicates.
            # GUARANTEED-OR-FAIL: a partially-shared scene renders correctly and
            # only betrays itself when an artist edits one "instance" and its
            # siblings don't follow -- so a failed replay rolls the whole import
            # back (the isolation namespace makes that atomic) and raises.
            try:
                self._apply_instance_manifest(manifest_path, new_nodes)
                imported = self._merge_import_namespace(ns, new_nodes)
            except Exception:
                cmds.namespace(removeNamespace=ns, deleteNamespaceContent=True)
                if tmp is not None and os.path.isfile(out_path):
                    self.logger.warning(
                        f"Keeping intermediate USD for debugging: {out_path}"
                    )
                raise
        else:
            if os.path.isfile(manifest_path):
                # Structurally non-fatal: a bad sidecar must never abort an
                # import whose FBX already landed (materials just stay classic).
                try:
                    self._apply_texture_manifest(
                        manifest_path, new_nodes, shader_type=shader_type
                    )
                except Exception as e:  # noqa: BLE001
                    self.logger.warning(
                        f"Texture-manifest rebuild failed ({e}); keeping FBX materials."
                    )
            imported = self._transforms(new_nodes)
        if cleanup and tmp is not None:
            tmp.cleanup()
        self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
        return imported

    # ------------------------------------------------------------------ bake (FBX -> .ma)
    @property
    def mayapy_path(self) -> Optional[str]:
        """The headless ``mayapy`` used for the bake — this host's own interpreter.

        Unlike :attr:`blender_path` (a foreign app, discovered through an ``AppSpec``),
        the bake runs the SAME Maya the panel runs in, so the interpreter ships beside
        the running binary: ``sys.executable``'s ``bin`` dir, else ``MAYA_LOCATION/bin``.
        """
        if not self._mayapy_path:
            candidates = []
            if sys.executable:
                candidates.append(os.path.dirname(sys.executable))
            location = os.environ.get("MAYA_LOCATION")
            if location:
                candidates.append(os.path.join(location, "bin"))
            name = "mayapy.exe" if os.name == "nt" else "mayapy"
            for directory in candidates:
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate):
                    self._mayapy_path = candidate
                    break
            else:
                self._mayapy_path = shutil.which("mayapy")
        return self._mayapy_path

    @mayapy_path.setter
    def mayapy_path(self, value: Optional[str]) -> None:
        self._mayapy_path = value

    def require_mayapy(self) -> str:
        """Return :attr:`mayapy_path` or raise an error naming what's missing."""
        mayapy = self.mayapy_path
        if not mayapy:
            raise FileNotFoundError(
                "No mayapy interpreter found for the bake (not beside sys.executable, "
                "not under MAYA_LOCATION/bin, not on PATH). Set "
                "BlenderSceneImport.mayapy_path."
            )
        return mayapy

    def render_bake_script(self, src_path: str, out_path: str) -> str:
        """Render the Maya-side intermediate->.ma bake script (exposed for
        tests/preview). *src_path* may be a USD or FBX intermediate -- the template
        dispatches on extension."""
        return _templates.ScriptTemplate.render_template(
            _BAKE_TEMPLATE,
            {
                "SRC_FILE": str(src_path).replace("\\", "/"),
                "OUT_MA": str(out_path).replace("\\", "/"),
                # The child is the same Maya version, so the parent's sys.path entries
                # are valid there -- this is what makes the shared manifest replay
                # (mayatk in the child) reliable rather than best-effort.
                "EXTRA_SYS_PATH": repr(list(sys.path)),
            },
        )

    def bake(self, src_path: str, out_path: str, *, timeout: float = 600) -> Any:
        """Bake the USD/FBX intermediate *src_path* into the .ma at *out_path* in a
        fresh ``mayapy`` (blocking)."""
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Bake source not found: {src}")
        mayapy = self.require_mayapy()
        self.logger.info(f"Baking {os.path.basename(src)} to .ma via {mayapy} ...")
        env = dict(os.environ)
        env.update(_FAST_MAYA_ENV)
        result = self._run_bake_script(
            mayapy,
            self.render_bake_script(src, out_path),
            artifact=out_path,
            timeout=timeout,
            env=env,
        )
        self.logger.info(
            f"Baked to .ma in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the mayapy run without patching pythontk internals).
    @staticmethod
    def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe, script_text, artifact=artifact, timeout=timeout, env=env
        )

    def bake_scene(
        self,
        src_path: str,
        *,
        via: str = "fbx",
        use_cache: bool = True,
        timeout: float = 600,
        **script_opts: Any,
    ) -> str:
        """Bake *src_path* to a cached ``.ma`` and return its path — the reference path.

        Maya references FBX natively, so this is not a capability gap the way the
        Blender side's is; it is deliberate **symmetry**. Both panels reference a cached
        *native* scene, so the referenced-file surface (edits, namespaces, reload,
        relocate, display overrides) behaves identically no matter which DCC the row came
        from. A ``.blend`` is converted to an FBX (default) or USD intermediate in a
        headless Blender (the cached intermediate :meth:`import_scene` already uses),
        then baked to a ``.ma`` in a headless ``mayapy``; an ``.fbx`` source skips
        straight to the bake.

        Both stages are cached independently, and the bake's key includes the
        intermediate's identity **and the bake template's**, so a template fix
        invalidates stale bakes (a retry after an upgrade must not replay the old bug).

        Parameters:
            src_path: A ``.blend`` / ``.fbx`` file.
            via: Conversion intermediate for ``.blend`` sources — ``"fbx"`` (default:
                format-native instancing + classic model / manifest replay) or
                ``"usd"`` (native materials / animation, instancing replayed
                guaranteed-or-fail from the conversion's required sidecar; see
                :meth:`import_scene`).
            use_cache: Reuse a prior conversion + bake of the identical source.
            timeout: Max seconds for EACH headless stage.
            **script_opts: Blender-side conversion knobs (``embed_textures`` /
                ``include_animation``); inert for an ``.fbx`` source.

        Returns:
            str: Path to the cached ``.ma`` — pass it to ``cmds.file(reference=True)``.
        """
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        ext = os.path.splitext(src)[1].lower()
        if ext not in BAKE_SOURCE_EXTENSIONS:
            raise ValueError(
                f"Unsupported bake source: {src} (expected {BAKE_SOURCE_EXTENSIONS})"
            )
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Scene not found: {src}")

        if ext == ".fbx":
            inter_path, conversion = src, None
        else:
            conversion = self._cached_conversion(
                src,
                via=via,
                use_cache=use_cache,
                timeout=timeout,
                script_opts=script_opts,
            )
            inter_path = conversion.path
            if via == "usd" and not os.path.isfile(inter_path + ".manifest.json"):
                # The v2 conversion always writes the sidecar; without it the
                # bake could silently cache a flattened .ma (see the bake
                # template's apply_instances).
                raise RuntimeError(
                    f"USD conversion sidecar missing: {inter_path}.manifest.json. "
                    "Refusing to bake (a flat bake could silently lose "
                    "instancing); clear the conversion cache or bake via FBX."
                )

        got = ptk.CachedArtifact("blender_bake_mtk", extension=".ma").get(
            ptk.CachedArtifact.key(files=[inter_path, _BAKE_TEMPLATE]),
            lambda out: self.bake(inter_path, out, timeout=timeout),
            use_cache=use_cache,
        )
        # The intermediate scratch is consumed once the bake has read it; the .ma
        # scratch is NOT cleaned up -- the caller references that file, so it must
        # outlive this call (an uncached bake therefore lives under the scoped store's
        # stale sweep).
        if conversion is not None and conversion.scratch is not None:
            conversion.scratch.cleanup()
        # Rewritten on a cache hit too: cheap, and it self-heals a sidecar lost to a
        # partial sweep (without it the panel silently forgets the row is referenced).
        self._write_bake_source(got.path, src)
        self.logger.info(f"Baked {src_path} -> {got.path}")
        return got.path

    def _write_bake_source(self, baked_path: str, src: str) -> None:
        """Record beside *baked_path* which foreign scene it was baked from."""
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "w", encoding="utf-8") as fh:
                json.dump({"source": os.path.abspath(src)}, fh)
        except OSError as e:  # cosmetic bookkeeping — never fail a completed bake
            self.logger.debug(f"Could not write the bake source sidecar: {e}")

    @staticmethod
    def bake_source(baked_path: str) -> Optional[str]:
        """The foreign scene *baked_path* was baked from, or None if it is not a bake.

        The inverse of :meth:`bake_scene` — lets a browser map a reference back to the
        source row the user actually sees.
        """
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "r", encoding="utf-8") as fh:
                return json.load(fh).get("source") or None
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------ Maya side
    @staticmethod
    def _transforms(nodes: List[str]) -> List[str]:
        """The transform subset of *nodes* (behavior parity: blendertk's
        ``import_scene`` returns Blender objects, i.e. transform-level items)."""
        import maya.cmds as cmds

        return cmds.ls(nodes, type="transform") or []

    def _merge_import_namespace(self, ns: str, new_nodes: List[str]) -> List[str]:
        """Dissolve the USD import's isolation namespace *ns* into the root and
        return the surviving transform paths.

        The merge renames on clash (Maya appends a numeric suffix), which is
        safe HERE -- after the instance replay -- because sharing and shading
        travel with the nodes, not their names. MDagPath handles track the
        renames; string paths captured before the merge would go stale.
        """
        import maya.api.OpenMaya as om
        import maya.cmds as cmds

        sel = om.MSelectionList()
        for node in self._transforms(new_nodes):
            sel.add(node)
        dag_paths = [sel.getDagPath(i) for i in range(sel.length())]
        cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
        return [p.fullPathName() for p in dag_paths]

    def _import_fbx(
        self, fbx_path: str, fbx_options: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Import *fbx_path* into the current scene; return ALL new nodes
        (the manifest apply needs the shading engines, not just transforms).

        Deterministic baseline first: the plugin's import options are global
        and sticky (the user's last interactive FBX import shapes this one),
        and the factory default mode "merge" can RETARGET animation onto
        same-named nodes already in the scene -- reset, then pin "add". The
        import mirror of the conversion template's FBXResetExport rule;
        best-effort, so a plugin build missing a command can't block the
        import.
        """
        import maya.cmds as cmds

        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.load_plugin()
        try:
            FbxUtils.reset_import()
        except RuntimeError as e:
            self.logger.debug(f"FBXResetImport unavailable: {e}")
        FbxUtils._apply_import_options({"FBXImportMode": "add"})
        options = dict(
            i=True,
            type="FBX",
            ignoreVersion=True,
            returnNewNodes=True,
        )
        options.update(fbx_options or {})
        return cmds.file(fbx_path, **options) or []

    @staticmethod
    def _manifest_empty_rules(
        manifest_path: Optional[str],
    ) -> Dict[str, Optional[bool]]:
        """``{fbx-spelled name: keep locator (None = heuristic)}`` from ``empties``.

        The sender records each exported Empty's ``display_type`` (and, for
        round-tripped scenes, a ``maya_node_type`` custom property). The
        decision this feeds: an explicit ``maya_node_type`` wins outright; a
        non-``PLAIN_AXES`` display type is a deliberate author marker and stays
        a locator even with children; ``PLAIN_AXES`` (Blender's default) maps
        to ``None`` -- the children-based heuristic. EVERY listed Empty gets an
        entry, unmarked ones included: the exact name pins the lookup, so the
        rename-on-clash digit-suffix match can never hand a real sibling
        ("grp2") another Empty's rule ("grp") -- the same sibling guard the
        materials applier's ``wants`` set enforces. Missing/old manifests
        return empty -- pure heuristic, the pre-manifest behavior.

        Kept in step by hand with the dependency-free copies in blendertk's
        ``maya_bridge/templates/import.py`` / ``_save_scene.py``.
        """
        import json

        rules: Dict[str, Optional[bool]] = {}
        if not manifest_path or not os.path.isfile(manifest_path):
            return rules
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return rules
        empties = data.get("empties") if isinstance(data, dict) else []
        for entry in empties or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = _BlenderSceneImportInternal._fbx_safe_name(entry["name"])
            node_type = str(entry.get("maya_node_type") or "").lower()
            if node_type:
                rules[name] = node_type == "locator"
            elif str(entry.get("display_type") or "PLAIN_AXES") != "PLAIN_AXES":
                rules[name] = True
            else:
                rules[name] = None
        return rules

    @staticmethod
    def _restore_empty_groups(
        new_nodes: List[str], manifest_path: Optional[str] = None
    ) -> int:
        """Turn imported Empties back into the CORRECT Maya node types.

        Blender Empties travel as FBX nulls, and Maya's FBX importer gives EVERY
        null a locator shape -- so a Blender scene's whole group hierarchy arrives
        as locators (live production report). A Blender Empty with children IS a
        Maya group: drop the locator shape and leave the plain transform. A
        CHILDLESS Empty stays a locator -- it marks a point, and a shapeless leaf
        transform would be invisible and unpickable. The manifest's ``empties``
        section (see :meth:`_manifest_empty_rules`) overrides the heuristic:
        an Empty the author marked as a locator keeps its shape even as a
        parent, and one marked as a group is stripped even as a leaf.

        Scoped to *new_nodes* so a pre-existing user locator is never touched.
        Skips transforms with any non-locator shape (not a null translation).
        Returns the number of shapes stripped. Kept in step by hand with the
        dependency-free copies in blendertk's ``maya_bridge/templates/``
        (the send direction's Maya-side scripts).
        """
        import maya.cmds as cmds

        rules = BlenderSceneImport._manifest_empty_rules(manifest_path)
        stripped = 0
        for shape in cmds.ls(new_nodes, exactType="locator", long=True) or []:
            transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
            if not transform:
                continue
            shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
            if len(shapes) != 1:
                continue
            short = transform.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            if short in rules:  # exact manifest name pins the decision
                keep = rules[short]
            else:  # tolerate Maya's rename-on-clash digit suffix
                keep = next(
                    (
                        k
                        for want, k in rules.items()
                        if _BlenderSceneImportInternal._matches_fbx_name(short, want)
                    ),
                    None,
                )
            if keep is None:
                keep = not cmds.listRelatives(
                    transform, children=True, type="transform", fullPath=True
                )
            if not keep:
                cmds.delete(shape)
                stripped += 1
        return stripped

    def _apply_instance_manifest(
        self, manifest_path: str, new_nodes: List[str]
    ) -> int:
        """Rebuild real Maya instances from Blender's linked-duplicate groups.

        Mirror of blendertk's method. The USD export is flat, so the sharing
        relationship travels in the sidecar and is replayed here: for each
        group, one transform's shape is instanced under the others (Maya's
        shared-shape model) and their own shapes are deleted. Without this a
        .blend whose props are linked duplicates would land as N independent
        shapes -- the FBX route preserves sharing natively, so the USD route
        must reach the same result to be a usable alternative.

        The v2 sidecar records SANITIZED prim names -- what Blender's exporter
        actually writes (probe-verified: ``Chair.001`` -> ``Chair_001``) -- and
        the matcher strips the import's isolation namespace, so names match 1:1
        no matter what the open scene already contained.

        Shading is re-applied per transform afterwards: instancing routes
        assignments through ``instObjGroups``, and the surviving shape carries
        the master's shader, so a follower with its own material would
        otherwise inherit the master's.

        GUARANTEED-OR-FAIL: any member the import can't account for raises.
        A partially-shared scene renders correctly and only betrays itself when
        an artist edits one "instance" and its siblings don't follow, so the
        conversion must fail loudly instead (callers roll the import back).

        Returns the number of transforms re-instanced.
        """
        import json

        from maya import cmds

        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        if not isinstance(data, dict) or data.get("version") != 2 or data.get(
            "format"
        ) != "names":
            raise RuntimeError(
                "Unsupported instance sidecar (expected a v2 'names' manifest, "
                f"got version={data.get('version') if isinstance(data, dict) else data!r}). "
                "Stale conversion cache? Clear it or re-pull via FBX."
            )
        groups = data.get("instances") or []
        if not groups:
            return 0

        # Imported transforms by leaf name (isolation namespace stripped) --
        # sanitized prim names import 1:1, and Blender's global name uniqueness
        # plus the exporter's collision gate make leaves unique; the ambiguity
        # check is defense in depth.
        by_name: Dict[str, str] = {}
        ambiguous: set = set()
        for node in self._transforms(new_nodes):
            leaf = node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            if leaf in by_name:
                ambiguous.add(leaf)
            else:
                by_name[leaf] = node

        wanted = [n for group in groups for n in group]
        problems = sorted({n for n in wanted if n in ambiguous})
        if problems:
            raise RuntimeError(
                "Instance sidecar names are ambiguous in the import (duplicate "
                "leaf names): " + ", ".join(problems)
            )
        problems = sorted({n for n in wanted if n not in by_name})
        if problems:
            raise RuntimeError(
                "Instance sidecar members not found in the import: "
                + ", ".join(problems)
            )

        def shading_groups(transform: str) -> List[str]:
            shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
            sgs: List[str] = []
            for shape in shapes:
                for sg in cmds.listConnections(shape, type="shadingEngine") or []:
                    if sg not in sgs:
                        sgs.append(sg)
            return sgs

        rebuilt = 0
        failures: List[str] = []
        for group in groups:
            if len(group) < 2:
                raise RuntimeError(
                    f"Malformed instance sidecar group (needs >= 2 members): {group}"
                )
            members = [by_name[n] for n in group]
            master, rest = members[0], members[1:]
            master_shapes = (
                cmds.listRelatives(master, shapes=True, fullPath=True) or []
            )
            if not master_shapes:
                raise RuntimeError(
                    f"Instance master has no shape to share: {master}"
                )
            shape = master_shapes[0]
            for transform in rest:
                try:
                    own = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
                    sgs = shading_groups(transform)
                    # Instance the master's shape under this transform, then drop
                    # the transform's own geometry.
                    cmds.parent(shape, transform, add=True, shape=True)
                    if own:
                        cmds.delete(own)
                    # Re-assign per-instance shading (instObjGroups), so a
                    # follower keeps its own material instead of the master's.
                    if sgs:
                        cmds.sets(transform, edit=True, forceElement=sgs[0])
                    rebuilt += 1
                except Exception as error:  # noqa: BLE001 -- collect, then fail loud
                    failures.append(f"{transform}: {error}")
        if failures:
            raise RuntimeError("Instance rebuild failed for: " + "; ".join(failures))
        if rebuilt:
            self.logger.info(
                f"Instances rebuilt: {rebuilt} transform(s) re-instanced across "
                f"{len(groups)} Blender linked-duplicate set(s)."
            )
        return rebuilt

    def _apply_texture_manifest(
        self,
        manifest_path: str,
        new_nodes: List[str],
        shader_type: str = "stingray",
    ) -> None:
        """Rebuild manifest materials natively from the conversion's sidecar.

        The FBX carries only the classic-model approximation (color / normal /
        emissive); the manifest carries each textured material's ORIGINAL image
        files, which the game-shader engine (:class:`GameShader`) wires into a
        native network -- including the packed game-engine maps FBX has
        no slot for (``Metallic_Smoothness``, ``MSAO``, ``ORM``), smoothness ->
        roughness inversion and channel splits included. Classification is by
        filename via the shared ``ptk.MapFactory`` SSoT, so conventionally named
        sets round-trip; an entry whose files classify to nothing keeps its
        FBX material (logged). Per-entry failures degrade, never abort the import.

        *shader_type* selects which shader the rebuild targets
        (``standard_surface`` / ``open_pbr`` / ``stingray`` -- ``GameShader``'s
        vocabulary); see :meth:`_rebuild_material` for the fallback when this
        Maya cannot build the requested one.
        """
        import json

        import maya.cmds as cmds

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            self.logger.warning(
                f"Texture manifest unreadable ({e}); keeping FBX materials."
            )
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
            by_short.setdefault(
                _BlenderSceneImportInternal._fbx_safe_name(short), []
            ).append(node)

        entries = manifest.get("materials", [])
        # Every entry's exact FBX-spelled target. The clash-rename suffix match
        # below must never claim a name that is ANOTHER entry's exact target --
        # "M_test" (renamed by the importer) must not steal "M_test2"'s SGs.
        wants = {
            _BlenderSceneImportInternal._fbx_safe_name(e.get("fbx_material") or "")
            for e in entries
            if isinstance(e, dict)
        }
        # Nor a name that truly exists in the .blend at all: the importer only
        # renames on CLASH, so an exact .blend spelling seen among the imported
        # SGs is its own material -- an UNTEXTURED "Mat2" beside textured "Mat"
        # has no manifest entry, and without this it would read as "Mat renamed
        # to Mat2" and get repainted with Mat's rebuilt textures. Older
        # manifests lack the key; the entries-only guard above still applies.
        wants |= {
            _BlenderSceneImportInternal._fbx_safe_name(n)
            for n in manifest.get("scene_materials", [])
            if isinstance(n, str)
        }

        def target_sgs(want: str) -> Dict[str, List[str]]:
            """Shading groups for *want*: exact importer spelling first; else
            tolerate Maya's rename-on-clash digit suffix (sibling-safe)."""
            if want in sgs_by_material:
                return {want: sgs_by_material[want]}
            return {
                short: sgs
                for short, sgs in sgs_by_material.items()
                if _BlenderSceneImportInternal._matches_fbx_name(short, want)
                and short not in wants
            }

        for entry in entries:
            name = entry.get("name", "?")
            # What the rebuilt network should END UP called: the source
            # material's own name, in the spelling _rebuild_material asks for.
            desired = (
                _BlenderSceneImportInternal._maya_safe_name(name)
                if entry.get("name")
                else ""
            )
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
                new_sg = self._rebuild_material(
                    files, name, entry.get("slots"), shader_type=shader_type
                )
                if new_sg is None:  # nothing classified -- keep the FBX material
                    self.logger.warning(
                        f"{name}: no texture classified by filename and no "
                        "authoritative slot in the manifest; keeping the "
                        "FBX-carried material."
                    )
                    continue

                # Primary: transfer members at the SHADING-GROUP level, keyed by
                # the FBX-spelled material name (the importer may rename-on-clash
                # with a digit suffix). Renderable sets are exclusive, so
                # forceElement moves per-face assignments intact -- the Maya
                # analogue of blendertk's slot-level swap.
                want = _BlenderSceneImportInternal._fbx_safe_name(
                    entry.get("fbx_material") or ""
                )
                replaced, swapped = [], 0
                if want:
                    for short, sgs in target_sgs(want).items():
                        for old_sg in sgs:
                            members = cmds.sets(old_sg, query=True) or []
                            if members:
                                cmds.sets(members, forceElement=new_sg)
                                swapped += 1
                            old_mats = (
                                cmds.listConnections(
                                    f"{old_sg}.surfaceShader",
                                    source=True,
                                    destination=False,
                                )
                                or []
                            )
                            for old in old_mats:
                                if old not in replaced:
                                    replaced.append(old)
                if swapped:
                    # Purge FIRST: the source material's name is only free once
                    # the FBX-carried one it clashed with is gone.
                    self._purge_orphans(replaced)
                    self._claim_material_name(new_sg, desired)
                    self.logger.info(
                        f"Rebuilt material {name} from {len(files)} "
                        f"file(s) into {swapped} shading group(s)."
                    )
                    continue

                # Fallback (importer renamed the material): whole-object assign.
                targets = [
                    node
                    for member in entry.get("objects", [])
                    for node in by_short.get(
                        _BlenderSceneImportInternal._fbx_safe_name(member), []
                    )
                ]
                if not targets:
                    self._purge_rebuilt(new_sg)  # nothing to attach it to
                    self.logger.warning(
                        f"{name}: no matching shading group or object found."
                    )
                    continue
                cmds.sets(targets, forceElement=new_sg)
                self._claim_material_name(new_sg, desired)
                self.logger.info(
                    f"Rebuilt material {name} from {len(files)} file(s) "
                    f"on {len(targets)} object(s) (object-level fallback)."
                )
            except Exception as e:
                self.logger.warning(f"Manifest entry {name} skipped: {e}")

    # Seam for tests (stub the GameShader build without live texture prep).
    @staticmethod
    def _rebuild_material(
        files: List[str],
        name: str,
        slots: Optional[Dict[str, str]] = None,
        shader_type: str = "stingray",
    ) -> Optional[str]:
        """Build a native shader network from *files*; return its shading group.

        *shader_type* is ``GameShader``'s own vocabulary -- ``stingray``
        (default), ``open_pbr`` or ``standard_surface`` -- so the bridge exposes
        the engine's existing shader support rather than a second one. Stingray
        is the default because these hand-offs feed a game engine, it matches
        ``GameShader``'s own default, and it is the only family that DECLARES
        its texture slots, so its maps survive the trip back out instead of
        being re-guessed from filenames. ``standardSurface`` remains the
        universal floor: a type this Maya cannot build (openPBR needs a recent
        2025+, Stingray needs the ShaderFX plugin) degrades to it with a NAMED
        warning rather than losing the material. The packed-map config flags
        are enabled per detected map type
        -- they gate each packed map as a desired OUTPUT in ``MapFactory``'s prep
        (MSAO's flag is ``mask_map`` per the registry SSoT).

        *slots* is the manifest's ``{logical channel: file}``, used ONLY to rescue
        images whose filename classifies to nothing -- a Blender texture named
        after a product would otherwise leave the material untextured even though
        the sending material knew which input it fed. Filename classification
        stays authoritative (only a filename reveals packing), and the rescue is
        applied through :meth:`MatManifest.restore`, which resolves each channel
        to the shader's real attribute via the ``ShaderAttributeMap`` SSoT rather
        than hand-wiring plugs here.

        One deliberate asymmetry: a classified map is staged through
        ``MapFactory.prepare_maps`` and ends up workspace-relative
        (``sourceimages/x.png``), while a rescued one keeps its ORIGINAL absolute
        path. Staging is driven by map type, which is precisely what a rescued
        file lacks -- so it is referenced where it lives rather than guessed into
        a conversion. Both render identically.
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
        kwargs: Dict[str, Any] = {"shader_type": shader_type or "stingray"}
        for path in files:
            map_type = ptk.MapFactory.resolve_map_type(path)
            if map_type in flags:
                kwargs[flags[map_type]] = True

        # An opacity only the MANIFEST knows about (a product-named cutout map,
        # which classifies to nothing) still has to be declared before the build:
        # StingrayPBS gets its `opacity` slot from the transparent ShaderFX graph,
        # chosen at creation, so a rescue afterwards would find no slot at all and
        # the cutout would arrive fully opaque.
        if (slots or {}).get("opacity"):
            kwargs["opacity"] = True

        # Blender datablock names ("Material.001") are not legal Maya node
        # names -- sanitize for the created network (matching elsewhere uses
        # the manifest strings, so this is cosmetic only).
        safe = _BlenderSceneImportInternal._maya_safe_name(name)

        # Channels whose file the filename taxonomy cannot place. Only these are
        # rescued, so a rescued slot can never displace a classified map.
        rescue = {
            channel: path
            for channel, path in (slots or {}).items()
            if path
            and os.path.isfile(path)
            and ptk.MapFactory.resolve_map_type(path) is None
        }

        engine = GameShader(log_level="WARNING")
        try:
            node = engine.create_network(files, name=safe, **kwargs)
        except RuntimeError as error:
            # openPBRSurface needs a recent Maya 2025+; StingrayPBS needs the
            # ShaderFX plugin. Probed by ATTEMPT rather than by capability query
            # -- StingrayPBS only registers as a node type once the plugin
            # loads, so a pre-flight check would report a false negative.
            if kwargs["shader_type"] == "standard_surface":
                raise
            _logger.warning(
                f"{name}: '{kwargs['shader_type']}' unavailable in this Maya "
                f"({error}); falling back to standardSurface."
            )
            kwargs["shader_type"] = "standard_surface"
            node = engine.create_network(files, name=safe, **kwargs)
        if not node and not rescue:
            return None

        if not node:
            # Nothing classified at all, but the manifest still knows where these
            # images belong -- stand up a bare surface of the REQUESTED type
            # (each setup_* builds its own shading group) rather than dropping
            # them. Falls back the same way the network build above does.
            builders = {
                "open_pbr": engine.setup_open_pbr_node,
                "stingray": engine.setup_stringray_node,
            }
            builder = builders.get(kwargs["shader_type"])
            node = None
            if builder is not None:
                try:
                    node = builder(safe, False)
                except RuntimeError as error:
                    _logger.warning(
                        f"{name}: bare '{kwargs['shader_type']}' unavailable "
                        f"({error}); falling back to standardSurface."
                    )
            if not node:
                node = engine.setup_standard_surface_node(safe, False)

        node = str(node)
        shader = node
        if cmds.nodeType(node) == "shadingEngine":
            sources = cmds.listConnections(
                f"{node}.surfaceShader", source=True, destination=False
            )
            shader = sources[0] if sources else None

        if rescue and shader:
            from mayatk.mat_utils.mat_manifest import MatManifest
            from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

            # MatManifest.restore connects with force=True, so a rescued channel
            # would OVERWRITE one create_network already wired from a properly
            # named file. Drop any channel whose attribute is already driven --
            # the same "never displace a classified map" rule the Blender-side
            # fallback enforces, made explicit on this side too.
            #
            # Also drops channels this shader has no input for. Blender can send
            # ``bump``/``height`` (it distinguishes a Bump node from a Normal Map
            # node); ``ShaderAttrs`` has no such field, and wiring a height map
            # straight into ``normalCamera`` without a bump2d would be wrong, so
            # those are left to the filename path rather than mis-wired.
            # The MAYA node type ("standardSurface"), which is what ShaderAttrs
            # is keyed by -- deliberately not reusing the ``shader_type``
            # parameter name, which carries GameShader's vocabulary
            # ("standard_surface") and would read as the same thing.
            node_type = cmds.nodeType(shader)
            for channel in list(rescue):
                mapped = ShaderAttributeMap.get_attr(node_type, channel)
                if not mapped:
                    rescue.pop(channel)
                    continue
                attr = f"{shader}.{mapped[0]}"
                if not cmds.objExists(attr) or cmds.listConnections(
                    attr, source=True, destination=False
                ):
                    rescue.pop(channel)

            try:
                restored = MatManifest.restore(
                    shader, {"materials": {name: rescue}}, source_mat_name=name
                )
                if restored:
                    _logger.info(
                        f"{name}: {restored} unclassifiable texture(s) wired from "
                        f"the manifest's shader slots ({', '.join(sorted(rescue))})."
                    )
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    f"{name}: slot-based texture rescue failed ({e}); the "
                    "classified maps are unaffected."
                )

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
                    n
                    for n in (cmds.listHistory(mat) or [])
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
            mats = (
                cmds.listConnections(
                    f"{sg}.surfaceShader", source=True, destination=False
                )
                or []
            )
            self._purge_orphans(mats)
            if cmds.objExists(sg):
                cmds.delete(sg)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Rebuilt-network purge skipped: {e}")


__all__ = ["BlenderSceneImport"]
