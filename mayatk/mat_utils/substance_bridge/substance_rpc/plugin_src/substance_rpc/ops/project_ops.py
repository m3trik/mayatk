# !/usr/bin/python
# coding=utf-8
"""Project-level ops: inspect the open project and reload its mesh.

``mesh.reload`` is the op behind the Maya/Blender bridges' *reimport*
template: the DCC re-exports the FBX over the path the project was
created from, then asks Painter to replay the mesh from disk.
Painter preserves paint strokes where UVs/material names still match.

``substance_painter.project.reload_mesh`` is asynchronous -- it kicks
off the reload and reports completion via callback. The op therefore
returns ``{"started": True}`` immediately (blocking the main thread on
the callback would deadlock the reload itself); poll
``mesh.reload_status`` for the outcome.
"""
import os

from .. import register


# Outcome of the most recent ``mesh.reload`` call. Module-level because
# the reload completes via callback after the op has already returned.
_last_reload = {"status": None, "mesh_path": None}


@register("project.info")
def project_info():
    """Return ``{is_open, file_path, mesh_path, needs_saving}`` (best-effort).

    Every accessor is guarded: an unsaved project has no ``file_path``,
    and older Painter APIs lack ``last_imported_mesh_path`` -- missing
    values come back as ``None`` rather than erroring the whole call.
    """
    import substance_painter.project as project  # noqa: PLC0415

    info = {
        "is_open": bool(project.is_open()),
        "file_path": None,
        "mesh_path": None,
        "needs_saving": None,
    }
    if not info["is_open"]:
        return info
    for key, getter in (
        ("file_path", "file_path"),
        ("mesh_path", "last_imported_mesh_path"),
        ("needs_saving", "needs_saving"),
    ):
        fn = getattr(project, getter, None)
        if fn is None:
            continue
        try:
            info[key] = fn()
        except Exception:  # noqa: BLE001 -- e.g. ProjectError on unsaved
            info[key] = None
    return info


@register("mesh.reload")
def mesh_reload(mesh_path="", preserve_strokes=True, import_cameras=False):
    """Reload the open project's mesh from *mesh_path* (async).

    Parameters mirror :class:`substance_painter.project.MeshReloadingSettings`.
    Returns ``{"started": True, "mesh_path": ...}`` once the reload has
    been kicked off; the outcome lands in ``mesh.reload_status``.

    Raises:
        ValueError: *mesh_path* is empty or not a file on disk.
        RuntimeError: no project is open in Painter.
    """
    import substance_painter.project as project  # noqa: PLC0415

    if not mesh_path or not os.path.isfile(mesh_path):
        raise ValueError(f"Mesh file not found on disk: {mesh_path!r}")
    if not project.is_open():
        raise RuntimeError(
            "No project is open in Painter; open the project this mesh "
            "belongs to before reimporting."
        )

    settings = project.MeshReloadingSettings(
        import_cameras=bool(import_cameras),
        preserve_strokes=bool(preserve_strokes),
    )

    _last_reload["status"] = "pending"
    _last_reload["mesh_path"] = mesh_path

    def _on_done(status):
        ok = status == project.ReloadMeshStatus.SUCCESS
        _last_reload["status"] = "success" if ok else "failure"
        print(
            f"[substance_rpc] mesh reload "
            f"{'succeeded' if ok else 'FAILED'}: {mesh_path}"
        )

    project.reload_mesh(mesh_path, settings, _on_done)
    return {"started": True, "mesh_path": mesh_path}


@register("mesh.reload_status")
def mesh_reload_status():
    """Outcome of the last ``mesh.reload``: pending / success / failure.

    ``status`` is ``None`` if no reload has been requested this session.
    """
    return dict(_last_reload)
