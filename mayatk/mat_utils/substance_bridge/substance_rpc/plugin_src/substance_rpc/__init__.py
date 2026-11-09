# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter RPC plugin -- entry point.

Painter's Python plugin loader imports this package from
``.../Adobe Substance 3D Painter/python/plugins`` and calls :func:`start_plugin` /
:func:`close_plugin` on enable / disable (the plugin appears under Painter's
**Python** menu).

The generic half (op registry, main-thread marshalling, the HTTP routes
:class:`pythontk.RpcClient` speaks) is :mod:`._rpc_core` -- a **staged verbatim
copy** of ``pythontk.net_utils.rpc.plugin_core``, because an installed plugin has
no ``pythontk`` on Painter's ``sys.path``. Never edit it here; edit the pythontk
source and re-run ``m3trik/scripts/sync_rpc_core.py``.

So this package contributes only what is actually Painter-specific: the
:data:`PLUGIN` configuration below, and :mod:`.ops` -- the op implementations,
whose ``@register(...)`` calls fire when the package is imported.

Adding an op = drop a function with ``@register("ns.name")`` into any module
under ``ops/``. ``system.ping`` / ``system.list_ops`` / ``system.describe`` come
free from the core, so only Painter-specific ops live here.

The plugin **announces** itself (:class:`pythontk.RpcInstances`): it binds a
port the OS picks, writes an instance record (its port, a token every call
must carry, and the project Painter holds, by path and UUID, rewritten on
Painter's project events) and removes it when it stops. A client finds the
Painter that holds a project by that record, never by a fixed port, and any
number of Painters serve at once. A script reaches Painter only through
``script.start`` (:mod:`.ops.script_ops`). Its menu in Painter's menu bar,
**M3trik Scripts**, says what it serves and pauses it (:func:`add_menu`).

Importing this file binds no port and touches no Painter API -- slot discovery
(which imports every ``*.py`` under a host library) stays inert. The server starts only
via :func:`start_plugin`, and the core additionally gates on actually being hosted
by Painter, so even a stray ``start_plugin()`` call elsewhere is a no-op.
"""

from ._rpc_core import RpcPlugin  # noqa: F401 -- re-export for tests/tooling

#: The one plugin instance. Its registry and marshaller are the module-level
#: `register` / `run_on_main_thread` the op modules import.
PLUGIN = RpcPlugin(
    label="substance_rpc",
    host_module="substance_painter",
    env_prefix="SUBSTANCE_RPC",
    default_port=0,  # the OS picks; the instance record says which
    # An hour, not the core's minute: a script or a multi-texture-set 4K bake
    # runs for many minutes on the main thread, and an hour still catches a
    # deadlocked loop.
    main_thread_timeout=3600.0,
    announce=True,
)

registry = PLUGIN.registry
register = registry.register
run_on_main_thread = PLUGIN.marshaller.run

# Imported AFTER the names above are bound: the op modules import them from this
# partially-initialised package, which is what makes `@register` work at import time.
# Via `import_ops` rather than a plain `from . import ops`, so a host reload --
# which rebuilds PLUGIN with an empty registry but leaves the ops submodules
# cached -- still re-runs the `@register` decorators. A plain import is a cache
# hit there, and the server comes back serving only `system.*`.
ops = PLUGIN.import_ops(f"{__name__}.ops")  # noqa: E402

#: The name a person's install has, and scripts import the plugin by
#: (``from substance_rpc.ops import bake_ops``).
PACKAGE = "substance_rpc"


def _answer_to_the_plugins_name():
    """A copy under another name -- the launch root's ``substance_rpc_launched``,
    which a bridge launches Painter with -- becomes ``substance_rpc`` in that
    Painter: scripts reach it by that name, and Painter's later scan of the
    ``plugins/`` folders resolves the name to this copy, so a person's install
    there is neither listed nor started in that Painter, ticked or not. (A copy
    named ``substance_rpc`` in the launch root was shadowed by that install:
    Painter puts the person's folders first.) Painter imports every startup
    module before it starts any, its Python menu among them, so this runs first.
    """
    if __name__ == PACKAGE:
        return
    import sys  # noqa: PLC0415

    for name, module in list(sys.modules.items()):
        if name == __name__ or name.startswith(__name__ + "."):
            sys.modules[PACKAGE + name[len(__name__):]] = module


_answer_to_the_plugins_name()


def start_server(port=None, host=None):
    """Start the RPC server (idempotent). Returns the bound ``(host, port)``."""
    return PLUGIN.start(port=port, host=host)


def stop_server():
    """Shut the server down (close_plugin hook / tests / hot-reload)."""
    PLUGIN.stop()


def is_running():
    """True while the server is bound."""
    return PLUGIN.is_running()


def autostart():
    """Start on plugin load, gated to the Painter host. ``None`` when declined."""
    return PLUGIN.autostart()


#: Painter's events that change what the record says (its project, busy).
_STATE_EVENTS = (
    "ProjectOpened",
    "ProjectCreated",
    "ProjectSaved",
    "ProjectClosed",
    "BusyStatusChanged",
)
#: The connected handlers: Painter's dispatcher holds only weak references.
_handlers = []


def publish_state(_event=None):
    """Write what Painter holds into the instance record: the project (path,
    name, UUID, the mesh it was made from) and whether Painter is busy."""
    try:
        from .ops.project_ops import project_info  # noqa: PLC0415
        import substance_painter.project as project  # noqa: PLC0415

        from .ops.script_ops import is_paused  # noqa: PLC0415

        info = project_info()
        PLUGIN.set_state(
            project={k: info.get(k) for k in ("name", "uuid", "file_path", "mesh_path")} if info["is_open"] else None,
            busy=bool(project.is_busy()),
            paused=is_paused(),
        )
    except Exception as exc:  # noqa: BLE001 -- a stale record beats a raise in Painter's event loop
        print(f"[substance_rpc] state not published: {exc}")


def _connect_events():
    import substance_painter.event as event  # noqa: PLC0415

    for name in _STATE_EVENTS:
        kind = getattr(event, name, None)
        if kind is not None:
            event.DISPATCHER.connect(kind, publish_state)
            _handlers.append(kind)


def _disconnect_events():
    try:
        import substance_painter.event as event  # noqa: PLC0415
    except ImportError:
        return
    while _handlers:
        try:
            event.DISPATCHER.disconnect(_handlers.pop(), publish_state)
        except Exception:  # noqa: BLE001
            pass


def status_line():
    """What the plugin's menu says it is doing: serving which project, or paused."""
    from .ops.script_ops import is_paused  # noqa: PLC0415

    if not PLUGIN.is_running():
        return "Not serving"
    if is_paused():
        return "Paused: scripts sent to Painter are refused"
    try:
        from .ops.project_ops import project_info  # noqa: PLC0415

        info = project_info()
    except Exception:  # noqa: BLE001 -- a status line never raises
        info = {"is_open": False}
    return f"Serving {info.get('name') or 'its project'}" if info["is_open"] else "Serving, no project open"


#: The plugin's menu in Painter's menu bar, while it is added.
_MENU = {}


def add_menu():
    """Add the plugin's menu to Painter's menu bar: a status line (what it
    serves) and Pause scripts, so the connection is visible and switchable off
    in Painter itself."""
    import substance_painter.ui as ui  # noqa: PLC0415
    from PySide6 import QtWidgets  # noqa: PLC0415 -- Painter's own binding

    from .ops import script_ops  # noqa: PLC0415

    menu = QtWidgets.QMenu("M3trik Scripts")
    status = menu.addAction(status_line())
    status.setEnabled(False)
    pause = menu.addAction("Pause scripts")
    pause.setCheckable(True)
    pause.setChecked(script_ops.is_paused())
    pause.setToolTip("While ticked, scripts sent to this Painter are refused; one under way finishes.")
    pause.toggled.connect(script_ops.set_paused)
    menu.aboutToShow.connect(lambda: status.setText(status_line()))
    ui.add_menu(menu)
    _MENU.update(menu=menu, status=status, pause=pause)
    return menu


def _remove_menu():
    menu = _MENU.pop("menu", None)
    _MENU.clear()
    if menu is None:
        return
    try:
        import substance_painter.ui as ui  # noqa: PLC0415

        ui.delete_ui_element(menu)
    except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
        print(f"[substance_rpc] menu not removed: {exc}")


def start_plugin():
    """Painter lifecycle hook: start the RPC server (idempotent), keep the
    instance record current and add the plugin's menu."""
    address = PLUGIN.autostart_safely()
    if address is not None:
        print(f"[substance_rpc] plugin started; RPC on {address[0]}:{address[1]}")
        try:
            _connect_events()
        except Exception as exc:  # noqa: BLE001
            print(f"[substance_rpc] project events not followed: {exc}")
        publish_state()
        try:
            add_menu()
        except Exception as exc:  # noqa: BLE001 -- serving does not hang on a menu
            print(f"[substance_rpc] menu not added: {exc}")


def close_plugin():
    """Painter lifecycle hook: shut the RPC server down and remove its menu.

    Also drops the deferred project-setup listener: a disabled plugin must not
    keep rewriting the next project that opens. A pause ends with it: ticking
    the plugin on again is switching it back on.
    """
    PLUGIN.stop()
    _disconnect_events()
    _remove_menu()
    try:
        from .ops import script_ops  # noqa: PLC0415

        script_ops.set_paused(False)
    except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
        print(f"[substance_rpc] pause not reset: {exc}")
    try:
        from .ops import setup_ops

        setup_ops.teardown()
    except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
        print(f"[substance_rpc] setup teardown failed: {exc}")
