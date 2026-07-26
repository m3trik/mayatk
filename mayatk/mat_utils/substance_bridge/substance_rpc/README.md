# substance_rpc — talk to a running Substance 3D Painter

The "reach into a live Painter" half of the Substance bridge. The parent
[`substance_bridge`](../) is a safe, file-based handoff (export FBX → launch a
templated Painter). This subpackage adds the RPC leg that powers the
**reimport / render / bake_lighting** templates: a Python client
([`client.py`](client.py)) that talks to a small HTTP server hosted *inside*
Painter by a plugin ([`plugin_src/substance_rpc/`](plugin_src/substance_rpc/)),
installed into Painter's plugins folder by [`installer.py`](installer.py).

Stock Painter binds **no** RPC port of its own — `--enable-remote-scripting`
is a no-op (verified 2026-05-18). This plugin is what makes any "send to the
running Painter" feature possible.

## First-run: enable the plugin once in Painter ⚠️

**This is the one manual step, and the usual reason a first reimport does
nothing.** The bridge installs the plugin automatically on every send, but
Painter only loads plugins at launch, and a newly discovered plugin may sit
**disabled** until you turn it on:

1. **Painter ▸ Python menu** — find **`substance_rpc`** in the list.
2. If it isn't checked, **tick it once**. Painter remembers the choice across
   restarts, so this is genuinely one-time.
3. No restart needed if you use **Python ▸ Reload Plugins Folder** after the
   first install; otherwise the plugin loads on Painter's next launch.

When it's live you'll see `[substance_rpc] listening on http://127.0.0.1:8090`
in Painter's log/console. After that, reimport is one click — Maya/Blender
re-exports the FBX over the recorded path and Painter reloads it in place.

If Painter was **already open** before the plugin's first install, that
instance has no plugin loaded at all — restart it (or Reload Plugins Folder).

## What you get without it

Reimport still re-exports the FBX to the exact file your Painter project was
created from (recorded per-scene), then logs the manual fallback: **Edit ▸
Project Configuration ▸ Select** that same file **▸ OK**. So the feature
degrades to "one manual reload," never a dead end.

## Ops (the plugin's RPC surface)

`{op, kwargs}` over HTTP (pythontk's [`RpcClient`](../../../../../pythontk/pythontk/net_utils/rpc/client.py) wire):

| Op | Does |
|:--|:--|
| `mesh.reload` | Reload the open project's mesh from a path (`substance_painter.project.reload_mesh`) — the reimport primitive. Async; poll `mesh.reload_status`. |
| `project.info` | `{is_open, file_path, mesh_path, needs_saving}` for the open project. |
| `js.evaluate` | Run a legacy-JS (`alg.*`) snippet via `substance_painter.js.evaluate` — routes the render/bake_lighting template bodies. |
| `system.eval` | Exec Python inside Painter; returns the script's `result` var. |
| `system.ping` / `list_ops` / `version` | Liveness / discovery / version. |

## Config

- **Port** — `8090`, override machine-wide via the `SUBSTANCE_RPC_PORT` env var
  (both the plugin and the client read it, so they stay in agreement).
- **Plugins folder** — resolved from `SUBSTANCE_PAINTER_PLUGINS_PATH`, else the
  known Documents folder (`<Documents>\Adobe\Adobe Substance 3D Painter\python\plugins`).

## Manual install / uninstall

Auto-install (on every bridge send) is idempotent, but you can drive it directly:

```python
# mayatk shown; under Blender use blendertk.mat_utils.substance_bridge.substance_rpc
from mayatk.mat_utils.substance_bridge.substance_rpc import Installer
Installer.install()        # symlink-first; copytree fallback
Installer.is_installed()   # -> bool
Installer.uninstall()      # -> bool (True if something was removed)
```

`Installer.install()` returns the destination path, or `None` if Painter's
plugins folder couldn't be resolved (set `SUBSTANCE_PAINTER_PLUGINS_PATH`).

## Layout

- [`client.py`](client.py) — `PainterRpcClient` (a `pythontk.RpcClient` subclass).
- [`installer.py`](installer.py) — `Installer` (Painter-folder resolution over pythontk's `PluginInstaller`).
- [`plugin_src/substance_rpc/`](plugin_src/substance_rpc/) — the Painter-hosted plugin
  (registry / server / main-thread marshaller / ops), self-contained so it needs
  nothing on Painter's `sys.path`. Vendored from the marmoset_rpc plugin pattern.
