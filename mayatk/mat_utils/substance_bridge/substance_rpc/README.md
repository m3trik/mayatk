# substance_rpc — run scripts in Substance 3D Painter

The Painter half of the Substance bridge:
- [`plugin_src/substance_rpc/`](plugin_src/substance_rpc/) is a small HTTP server hosted *inside* Painter.
- [`client.py`](client.py) is the Python client that finds and drives one.
- [`installer.py`](installer.py) installs the plugin into Painter's plugins folder, or stages it for a Painter the bridge launches.

Stock Painter binds **no** RPC port of its own; `--enable-remote-scripting` is a no-op (verified 2026-05-18). This plugin is what lets a bridge run a script in Painter.

## Finding a Painter

Each Painter running the plugin binds a port the OS picks and writes an **instance record** (pythontk's `RpcInstances`, in the per-user cache). The record holds that port, a token every call must carry, and the project Painter holds: its path, name, UUID and the mesh it was made from. Painter's project events keep the record current, and it is removed when the plugin stops.

`PainterRpcClient.painters()` lists the records, `holding(path)` names the Painter with a project open, and `for_instance(record)` is a client of one. There is no fixed port, so any number of Painters serve at once.

The server refuses any request a web page could send: an `Origin` header, a non-JSON body, or a `Host` that is not loopback.

## Your Painter, by your hand

- **A Painter the bridge launches** loads the plugin from a launch root (`Installer.launch_root()`, on `SUBSTANCE_PAINTER_PLUGINS_PATH`). Nothing of yours changes.
- **The Painter you open** serves scripts once you install the plugin (*Install in Painter* on the panel's Utilities menu, `Installer.install()`). Painter lists it in its **Python** menu, ticked the first time unless you have set it either way; untick it there to switch it off. A Painter open before the install picks it up with **Python ▸ Reload Plugins Folder**.
- **A send** never installs the plugin. It only refreshes an install already there when the install is out of date (`Installer.refresh()`).

## Ops (the plugin's RPC surface)

`{op, kwargs}` over HTTP (pythontk's `RpcClient` wire), each call carrying the record's token:

| Op | Does |
|:--|:--|
| `script.start` | Queues a script (its text, `args`, the project it is meant for); compiled first, refused for another project; runs when Painter is idle, each piece one undo entry |
| `script.status` / `script.cancel` / `script.jobs` | A job's state, report, console and error; stop it (a bake under way stops); every kept job |
| `project.info` | `{is_open, name, uuid, file_path, mesh_path, needs_saving}` |
| `project.open` / `create` / `save` / `save_as` / `close` | Painter's project calls (`create` takes mesh maps, a resolution, the UDIM workflow) |
| `mesh.reload` / `mesh.reload_status` | Reload the open project's mesh (async; the Update Mesh script) |
| `project.set_resolution`, `bake.set_high_poly`, `textures.apply_mesh_maps`, `bake.pending_setup` | Project setup, applied now or held until a project opens |
| `bake.lighting_to_diffuse`, `bake.mesh_maps` | The bakes the built-in scripts call |
| `system.ping` / `list_ops` / `describe` / `identity` / `version` | Liveness, discovery, this instance, versions |

There is no op that evaluates a string: a script reaches Painter only through `script.start`. A script may also call any op module directly (`from substance_rpc.ops import bake_ops`).

## Use

```python
# from the host library's (or the standalone app's) substance_bridge.substance_rpc
for record in PainterRpcClient.painters():
    print(record["pid"], (record["state"].get("project") or {}).get("name"))
client = PainterRpcClient.for_instance(record)
job = client.run_script("args.report(str(args['N']))", path="probe.py", args={"N": 1})
print(client.wait(job)["report"])
```

**Vendored copy.** This whole folder ships in each host library's Substance bridge and in the standalone Substance Scripts app. The Maya library's copy is the source: edit it there, then mirror it. Drift fails the vendor guard (`test_vendor_sync.py`).
