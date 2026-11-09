# !/usr/bin/python
# coding=utf-8
"""One-shot batch pipeline for the marmoset_rpc bridge.

Thin DCC binding around :mod:`pythontk.net_utils.rpc.job` -- exposes
``Call`` / ``Result`` / ``run_batch`` against a running Toolbag found by its
instance record (or one named by *port*).

Example::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (
        Call, BatchJob,
    )

    results = BatchJob.run_batch([
        Call("system.version"),
        Call("scene.list_materials"),
    ])
    for r in results:
        print(r.op, r.ok, r.value if r.ok else r.error)
"""

from typing import List, Optional

# Re-export the generic Call/Result so callers don't need two imports.
from pythontk.net_utils.rpc.job import Call, Result
from pythontk.net_utils.rpc.job import RpcJob

from .connection import MarmosetConnection


class BatchJob:
    """BatchJob — module namespace."""

    @staticmethod
    def run_batch(
        calls: List[Call],
        host: str = "127.0.0.1",
        port: int = 0,
        stop_on_error: bool = False,
        token: Optional[str] = None,
    ) -> List[Result]:
        """Connect to a running Toolbag's marmoset_rpc plugin and fire calls.

        *stop_on_error*: short-circuit on the first failure. Default is to
        run every call regardless -- useful when each call is independent
        and you want a complete report.

        The bridge plugin must already be loaded inside a running Toolbag:
        without *port*, the first one serving it (its instance record); with
        *port* (and that instance's *token*), that one. Use
        :meth:`MarmosetConnection.attach` upstream to check, and fall back to
        :class:`MarmosetEngine` (fresh-launch) on a miss.
        """
        client = MarmosetConnection(host=host, port=port, token=token)
        if not port:
            client.attach()
        return RpcJob.run_batch(calls=calls, client=client, stop_on_error=stop_on_error)


__all__ = ["Call", "Result", "BatchJob"]
