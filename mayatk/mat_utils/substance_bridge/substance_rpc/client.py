# !/usr/bin/python
# coding=utf-8
"""HTTP RPC client for the Painter-side ``substance_rpc`` plugin.

Speaks :class:`pythontk.net_utils.rpc.RpcClient`'s ``{op, kwargs}`` wire
format against the HTTP server the plugin stands up inside Painter
(see ``plugin_src/substance_rpc``; installed via :mod:`.installer`).

Kept separate from the bridge's stdio/log machinery (see the parent
``substance_bridge/connection.py``) so the RPC concern can evolve
independently.

Painter-specific conveniences layered on the generic client:

* :meth:`wait_until_ready` -- poll until the plugin's server answers.
* :meth:`eval_js` -- run legacy-JS (``alg.*``) template bodies via the
  plugin's ``js.evaluate`` op (:func:`substance_painter.js.evaluate`).
* :meth:`eval_py` -- exec Python source inside Painter (``system.eval``).
* :meth:`reload_mesh` -- the reimport primitive (``mesh.reload``).
"""
import os
import time
from typing import Any, Optional

from pythontk.net_utils.rpc.client import RpcClient


# Default port the substance_rpc plugin binds. Both sides resolve the
# same SUBSTANCE_RPC_PORT env var (the plugin at server start, this
# client at import), so a machine-wide override keeps them in agreement.
DEFAULT_RPC_PORT = int(os.environ.get("SUBSTANCE_RPC_PORT", "8090"))


class PainterRpcClient(RpcClient):
    """RPC client bound to the substance_rpc plugin's defaults.

    Requires the ``substance_rpc`` plugin to be installed and enabled in
    the target Painter (see :class:`.installer.Installer`; the bridge
    installs it automatically on send). Without the plugin nothing
    listens -- stock Painter binds no RPC port and its
    ``--enable-remote-scripting`` flag is a no-op (verified 2026-05-18).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_RPC_PORT,
        timeout: float = 30.0,
    ):
        super().__init__(host=host, port=port, app_label="Substance Painter")
        #: Default per-call timeout for :meth:`invoke` and the eval helpers.
        self.timeout = timeout

    def wait_until_ready(
        self, timeout: float = 60.0, poll_interval: float = 0.5
    ) -> bool:
        """Poll ``/health`` until the plugin answers, or *timeout* expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping(timeout=0.5):
                return True
            time.sleep(poll_interval)
        return False

    def invoke(self, op: str, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """:meth:`RpcClient.invoke` with this client's default timeout."""
        return super().invoke(
            op, timeout=self.timeout if timeout is None else timeout, **kwargs
        )

    # -- Painter conveniences ---------------------------------------------

    def eval_js(self, script: str) -> Any:
        """Evaluate *script* in Painter's JS engine (``alg.*`` API surface)."""
        return self.invoke("js.evaluate", script=script)

    def eval_py(self, script: str) -> Any:
        """Exec *script* (Python) inside Painter; returns its ``result`` var."""
        return self.invoke("system.eval", script=script)

    def reload_mesh(
        self,
        mesh_path: str,
        preserve_strokes: bool = True,
        import_cameras: bool = False,
    ) -> Any:
        """Ask Painter to reload the open project's mesh from *mesh_path*.

        Async on the Painter side -- returns ``{"started": True, ...}``;
        poll :meth:`reload_status` for the outcome.
        """
        return self.invoke(
            "mesh.reload",
            mesh_path=mesh_path,
            preserve_strokes=preserve_strokes,
            import_cameras=import_cameras,
        )

    def reload_status(self) -> Any:
        """Outcome of the last reload: ``{"status": ..., "mesh_path": ...}``."""
        return self.invoke("mesh.reload_status")

    def project_info(self) -> Any:
        """``{is_open, file_path, mesh_path, needs_saving}`` for the open project."""
        return self.invoke("project.info")
