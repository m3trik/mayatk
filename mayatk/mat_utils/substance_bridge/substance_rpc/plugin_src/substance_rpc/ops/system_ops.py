# !/usr/bin/python
# coding=utf-8
"""Painter-specific system ops: version reporting and script evaluation.

Liveness and introspection (``system.ping`` / ``system.list_ops`` /
``system.describe``) are registered by the shared core -- they are part of the
:class:`pythontk.RpcClient` contract, so no plugin has to remember them and no
per-plugin copy can drift.

``substance_painter`` is imported lazily inside each op so this module stays
import-safe outside Painter (tests exercise the server + registry against these
ops with no DCC present).
"""
from .. import register


@register("system.version")
def version():
    """Return Painter + plugin API version info (best-effort)."""
    info = {"plugin": "substance_rpc"}
    try:
        import substance_painter  # noqa: PLC0415

        info["api_version"] = getattr(substance_painter, "__version__", None)
        try:
            import substance_painter.application  # noqa: PLC0415

            info["painter_version"] = substance_painter.application.version()
        except Exception:  # noqa: BLE001
            info["painter_version"] = None
    except ImportError:
        info["api_version"] = None
        info["painter_version"] = None
    return info


@register("system.eval")
def eval_python(script=""):
    """Exec *script* (Python source) inside Painter's interpreter.

    The namespace is pre-seeded with ``substance_painter`` when available. Assign
    to a variable named ``result`` in the script to return a value to the caller;
    otherwise ``None`` comes back.

    Loopback-only by server construction (bound to 127.0.0.1) -- the same trust
    model as Painter's own scripting console.
    """
    namespace = {}
    try:
        import substance_painter  # noqa: PLC0415

        namespace["substance_painter"] = substance_painter
    except ImportError:
        pass
    exec(compile(script, "<substance_rpc:eval>", "exec"), namespace)
    return namespace.get("result")


@register("js.evaluate")
def js_evaluate(script=""):
    """Evaluate *script* in Painter's JavaScript engine (``alg.*`` API).

    Thin shim over :func:`substance_painter.js.evaluate` so bridge templates
    written against the legacy JS surface keep working.
    """
    import substance_painter.js  # noqa: PLC0415

    return substance_painter.js.evaluate(script)
