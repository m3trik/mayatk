# !/usr/bin/python
# coding=utf-8
"""HTTP RPC client for the Painter-side ``substance_rpc`` plugin.

Speaks :class:`pythontk.RpcClient`'s ``{op, kwargs}`` wire format against the
server the plugin stands up inside Painter (``plugin_src/substance_rpc``).
A Painter is found by what it is, never by a port: each one serving the plugin
writes an instance record (:meth:`painters`: its port, its token, the project
it holds) and a client is made from one (:meth:`for_instance`).

Painter-specific conveniences layered on the generic client:

* :meth:`painters` / :meth:`holding` -- the running Painters, and the one that
  has a project open.
* :meth:`run_script` / :meth:`job` / :meth:`cancel` / :meth:`wait` -- a script
  run as a job (``script.*`` ops).
* :meth:`reload_mesh` -- the reimport primitive (``mesh.reload``).

**Vendored copy -- keep byte-identical.** This file ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``).
"""

import os
import time
from typing import Any, Dict, List, Optional

from pythontk.net_utils.rpc.client import RpcClient


class PainterRpcClient(RpcClient):
    """RPC client of one Painter serving the ``substance_rpc`` plugin."""

    #: The plugin's label: the folder its instance records are under.
    LABEL = "substance_rpc"
    #: A finished job's states.
    FINISHED = ("done", "failed", "cancelled")

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = 30.0,
        token: Optional[str] = None,
        app_label: str = "Substance Painter",
    ):
        super().__init__(host=host, port=port, app_label=app_label, token=token)
        #: Default per-call timeout for :meth:`invoke`.
        self.timeout = timeout

    # -- Finding a Painter -------------------------------------------------

    @classmethod
    def painters(cls) -> List[Dict[str, Any]]:
        """Every running Painter serving the plugin: its instance record
        (``pid``, ``port``, ``state``: ``project`` and ``busy``), oldest first."""
        return cls.instances(cls.LABEL)

    @staticmethod
    def _same_file(a: Optional[str], b: Optional[str]) -> bool:
        norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))  # noqa: E731
        return bool(a and b) and norm(a) == norm(b)

    @classmethod
    def holding(cls, project_path: str) -> Optional[Dict[str, Any]]:
        """The record of the Painter that has *project_path* (an ``.spp``) open,
        or ``None``."""
        for record in cls.painters():
            project = (record.get("state") or {}).get("project") or {}
            if cls._same_file(project.get("file_path"), project_path):
                return record
        return None

    @classmethod
    def for_instance(cls, record: Dict[str, Any], **kwargs: Any) -> "PainterRpcClient":
        """A client of the Painter *record* names, carrying its token."""
        kwargs.setdefault("app_label", "Substance Painter")
        return cls(host=record.get("host") or "127.0.0.1", port=record["port"], token=record.get("token"), **kwargs)

    def wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 0.5) -> bool:
        """Poll ``/health`` until the plugin answers, or *timeout* expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping(timeout=0.5):
                return True
            time.sleep(poll_interval)
        return False

    def invoke(self, op: str, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """:meth:`RpcClient.invoke` with this client's default timeout."""
        return super().invoke(op, timeout=self.timeout if timeout is None else timeout, **kwargs)

    # -- Scripts -------------------------------------------------------------

    def run_script(
        self,
        source: str,
        path: str = "",
        args: Optional[Dict[str, Any]] = None,
        expect: Optional[Dict[str, Any]] = None,
        label: str = "",
    ) -> str:
        """Queue *source* in Painter with *args*; the job's id. *expect* (``{uuid,
        path}``) is the project the run is meant for: Painter refuses it when it
        holds another. Raises ``RuntimeError`` when the script does not compile
        or is refused."""
        answer = self.invoke("script.start", source=source, path=path, args=args or {}, expect=expect, label=label)
        return answer["job"]

    def job(self, job: str) -> Optional[Dict[str, Any]]:
        """The job's status (``state``, ``report``, ``console``, ``progress``,
        ``error``), or ``None`` when this Painter does not know it."""
        return self.invoke("script.status", job=job)

    def cancel(self, job: str) -> Optional[Dict[str, Any]]:
        """Stop the job (a running one at its next ``args.check_cancel()``)."""
        return self.invoke("script.cancel", job=job)

    def wait(self, job: str, timeout: float = 3600.0, poll: float = 0.5, on_status=None) -> Dict[str, Any]:
        """Poll the job until it finishes; its last status. *on_status* is
        called with each status (a panel's progress). Raises ``TimeoutError``
        after *timeout* seconds (the job is left running) and ``ConnectionError``
        when Painter stops answering. A poll Painter is too busy to answer (a
        long synchronous step holds its main thread, where every op runs) is
        no failure while ``/health``, served off that thread, still answers."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                status = self.job(job)
            except ConnectionError:
                if time.monotonic() > deadline or not self.ping(timeout=2.0):
                    raise
                time.sleep(poll)
                continue
            if status is None:
                return {"job": job, "state": "failed", "error": "Painter no longer knows this job (it restarted)."}
            if on_status is not None:
                on_status(status)
            if status["state"] in self.FINISHED:
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(f"Painter's job {job} did not finish within {timeout:.0f} s.")
            time.sleep(poll)

    # -- Project -------------------------------------------------------------

    def reload_mesh(self, mesh_path: str, preserve_strokes: bool = True, import_cameras: bool = False) -> Any:
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
        """``{is_open, name, uuid, file_path, mesh_path, needs_saving}``."""
        return self.invoke("project.info")
