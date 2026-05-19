# !/usr/bin/python
# coding=utf-8
"""Op registry for the marmoset_rpc plugin.

Pure-Python (no ``mset`` import) so the registry module is import-safe
for tests and tooling. Op modules under ``ops/`` import this and call
:func:`register` to add entries; the server module imports the registry
to dispatch incoming requests.

Each registered op carries the function plus its inspected signature, so
a ``describe`` call can return parameter names + defaults + the
docstring -- enough for an agent (or human) to discover the surface
without reading the source.
"""
import inspect


_OPS = {}


def register(name):
    """Decorator: register *fn* under *name*.

    Names are dot-namespaced (``"system.ping"``, ``"scene.list_materials"``)
    so the client can group related calls. Duplicate names raise so a
    typo can't silently shadow a real op.
    """
    def decorator(fn):
        if name in _OPS:
            raise ValueError(f"Op {name!r} is already registered.")
        _OPS[name] = fn
        return fn
    return decorator


def get(name):
    """Return the op function for *name*, or None."""
    return _OPS.get(name)


def all_ops():
    """Sorted list of every registered op name."""
    return sorted(_OPS.keys())


def describe(name=None):
    """Return a JSON-friendly description of one op or all ops.

    For each op: ``{"name", "doc", "params": [{"name", "default"}, ...]}``.
    ``default`` is ``"<required>"`` for positional-without-default params
    and stringified otherwise (so the result round-trips through JSON
    even when defaults are non-serialisable).
    """
    if name is not None:
        fn = _OPS.get(name)
        if fn is None:
            return None
        return _describe_one(name, fn)
    return [_describe_one(n, _OPS[n]) for n in sorted(_OPS)]


def _describe_one(name, fn):
    """Inspect *fn* and return its description dict."""
    try:
        sig = inspect.signature(fn)
        params = []
        for p in sig.parameters.values():
            if p.default is inspect.Parameter.empty:
                default = "<required>"
            else:
                default = repr(p.default)
            params.append({"name": p.name, "default": default})
    except (TypeError, ValueError):
        params = []
    return {
        "name": name,
        "doc": (inspect.getdoc(fn) or "").strip(),
        "params": params,
    }


def clear():
    """Reset the registry (test-only)."""
    _OPS.clear()
