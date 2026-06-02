"""N125 — the only legal source of "now" inside ``brickvision_runtime``.

Per [`docs/16-identity-audit-replay.md`](../../../docs/16-identity-audit-replay.md)
§12.4 invariant 6 every Skill **must** call ``core.time.now()``
or ``core.time.now_ms()`` instead of ``datetime.now()`` /
``time.time()``. Direct stdlib clock calls inside skill code
break replay because the wall-clock pin can no longer be honoured.

The ``ReplayPinDeterminism()`` scorer (N127) AST-asserts skill
code uses this module exclusively.

Replay mode:
    Setting ``BV_TRACE_TIME=true`` (a process env var) makes
    every call append a synthetic Claim to an in-process buffer
    so the stage emitter can persist them under
    ``build:<id>:time:<seq>``. Replay then injects the historical
    sequence via ``pinned_now(...)``.

This module **does not import the stdlib ``time`` module at the top
level inside skill code** — it intentionally re-exports the
runtime helpers under their own names so the lint rule can
ratchet ``import time`` / ``import datetime`` inside
``skills/`` and ``src/brickvision_runtime/`` cleanly.
"""

from __future__ import annotations

import datetime as _stdlib_datetime
import os
import threading
import time as _stdlib_time
from collections.abc import Iterator
from contextlib import contextmanager


# Module-private state. ``threading.local`` so concurrent builds
# (rare; coordinator is single-process) keep their own pin
# without cross-leak.
_LOCAL = threading.local()


def _trace_enabled() -> bool:
    return os.environ.get("BV_TRACE_TIME", "").lower() in ("1", "true", "yes")


def _next_seq() -> int:
    seq = getattr(_LOCAL, "seq", 0) + 1
    _LOCAL.seq = seq
    return seq


def _record(now_dt: _stdlib_datetime.datetime) -> None:
    if not _trace_enabled():
        return
    buffer = getattr(_LOCAL, "trace_buffer", None)
    if buffer is None:
        buffer = []
        _LOCAL.trace_buffer = buffer
    buffer.append((_next_seq(), now_dt.isoformat()))


def now() -> _stdlib_datetime.datetime:
    """Return the current wall-clock time (or the mocked pin)."""

    pinned = getattr(_LOCAL, "pinned", None)
    if pinned is not None:
        _record(pinned)
        return pinned
    real = _stdlib_datetime.datetime.now(_stdlib_datetime.timezone.utc)
    _record(real)
    return real


def now_ms() -> int:
    """Convenience: epoch millis from the same source as ``now()``."""

    return int(now().timestamp() * 1000)


@contextmanager
def pinned_now(wallclock: _stdlib_datetime.datetime) -> Iterator[None]:
    """Pin ``now()`` to ``wallclock`` for the lifetime of the context.

    Used by replay (a real production code path) to honour the stored
    ``wallclock_pin``, and also by tests via ``monkeypatch``-style
    pinning per discipline rule 15. The pin is thread-local; nested
    ``pinned_now`` calls stack.

    The function was renamed from ``mock_now`` under N189 / discipline
    rule 15 because the original name suggested test-only behavior
    when in fact the same context manager is the production replay
    path's wall-clock pin.
    """

    prev = getattr(_LOCAL, "pinned", None)
    _LOCAL.pinned = wallclock
    try:
        yield
    finally:
        _LOCAL.pinned = prev


def trace_buffer_snapshot() -> list[tuple[int, str]]:
    """Return a *copy* of the currently-recorded time-trace.

    The buffer accumulates while ``BV_TRACE_TIME=true``; the
    coordinator persists it as ``build:<id>:time:<seq>`` Claims at
    every step boundary, then calls ``reset_trace_buffer()``.
    """

    buf = getattr(_LOCAL, "trace_buffer", None)
    return list(buf or ())


def reset_trace_buffer() -> None:
    _LOCAL.trace_buffer = []
    _LOCAL.seq = 0


# Test helpers exposed for parity with the unit-test surface.
def _wallclock_now_for_tests() -> float:
    """Direct ``time.time()`` accessor reserved for *tests* only.

    Skill code must never call this. Kept out of ``__all__`` so
    lint can flag any non-test access.
    """

    return _stdlib_time.time()


__all__ = [
    "now",
    "now_ms",
    "pinned_now",
    "reset_trace_buffer",
    "trace_buffer_snapshot",
]
