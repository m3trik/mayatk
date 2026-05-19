# !/usr/bin/python
# coding=utf-8
"""One-shot batch pipeline for the marmoset_rpc bridge.

Thin DCC binding around :mod:`pythontk.net_utils.rpc.job` -- exposes
``Call`` / ``Result`` / ``run_batch`` with Toolbag's default port baked in.

Example::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (
        Call, run_batch,
    )

    results = run_batch([
        Call("system.version"),
        Call("scene.list_materials"),
    ])
    for r in results:
        print(r.op, r.ok, r.value if r.ok else r.error)
"""
from typing import List

# Re-export the generic Call/Result so callers don't need two imports.
from pythontk.net_utils.rpc.job import Call, Result
from pythontk.net_utils.rpc.job import run_batch as _generic_run_batch

from .connection import MarmosetConnection


def run_batch(
    calls: List[Call],
    host: str = "127.0.0.1",
    port: int = 8765,
    stop_on_error: bool = False,
) -> List[Result]:
    """Connect to a running Toolbag's marmoset_rpc plugin and fire calls.

    *stop_on_error*: short-circuit on the first failure. Default is to
    run every call regardless -- useful when each call is independent
    and you want a complete report.

    The bridge plugin must already be loaded inside a running Toolbag.
    Use :meth:`MarmosetConnection.ping` upstream if you want to verify
    that and fall back to :class:`MarmosetBridge` (fresh-launch) on miss.
    """
    return _generic_run_batch(
        calls=calls,
        client=MarmosetConnection(host=host, port=port),
        stop_on_error=stop_on_error,
    )


__all__ = ["Call", "Result", "run_batch"]
