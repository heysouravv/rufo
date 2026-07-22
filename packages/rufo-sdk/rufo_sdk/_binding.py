from __future__ import annotations

import inspect


def bind_args(fn, args: tuple, kwargs: dict) -> dict:
    """Best-effort mapping of a call's positional/keyword args to a name->value dict
    so policy conditions can reference `args['amount']` regardless of how the
    underlying tool function was invoked.
    """
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except TypeError:
        return {"_args": list(args), **kwargs}
