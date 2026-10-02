"""Time budgets for cloud calls: a chain of providers that never waits forever.

`race()` runs a chain of steps (stream -> upload -> second provider) with a
hedge: the next step starts as soon as the previous one fails, or early, in
parallel, when the previous one has run longer than its hedge delay. The
first accepted result wins; everything still running is abandoned (its
thread finishes in the background and its result is dropped). An overall
deadline turns "slow" into a failure the caller can handle.

`call_with_deadline()` is the one-step version (the cleanup chain uses it
per provider). Spec: docs/superpowers/specs/2026-10-02-provider-failover.md.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class DeadlineExceeded(TimeoutError):
    pass


class AllFailed(RuntimeError):
    """Every step failed (or the deadline passed). `errors` maps step name
    to what went wrong, in the order the steps ran."""

    def __init__(self, errors: dict[str, BaseException]) -> None:
        self.errors = errors
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "nothing to try"
        super().__init__(detail)


@dataclass
class Step:
    name: str
    run: Callable[[], Any]
    # Start this step this long after the previous step started, even if
    # that one is still running. None: only once the previous step failed.
    hedge_s: float | None = None
    # A result that isn't accepted counts as a failure (an empty stream).
    accept: Callable[[Any], bool] = field(default=lambda r: True)


def _spawn(name: str, fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name=f"failover-{name}", daemon=True).start()


def race(steps: list[Step], deadline_s: float, *,
         clock: Callable[[], float] = time.monotonic) -> tuple[str, Any]:
    """Run `steps` as a hedged chain; return (winning step name, result).
    Raises AllFailed when every step failed or `deadline_s` passed first."""
    results: queue.Queue = queue.Queue()
    errors: dict[str, BaseException] = {}
    t0 = clock()
    deadline = t0 + deadline_s
    started = 0
    running = 0
    last_start = t0

    def start() -> None:
        nonlocal started, running, last_start
        step = steps[started]
        started += 1
        running += 1
        last_start = clock()

        def work() -> None:
            try:
                results.put((step, True, step.run()))
            except BaseException as e:   # reported to the caller, not raised here
                results.put((step, False, e))
        _spawn(step.name, work)

    if not steps:
        raise AllFailed({})
    start()
    while True:
        nxt = steps[started] if started < len(steps) else None
        hedge_at = (last_start + nxt.hedge_s
                    if nxt is not None and nxt.hedge_s is not None else None)
        wake = deadline if hedge_at is None else min(deadline, hedge_at)
        try:
            step, ok, value = results.get(timeout=max(0.0, wake - clock()))
        except queue.Empty:
            now = clock()
            if now >= deadline:
                for s in steps[:started]:
                    errors.setdefault(s.name, DeadlineExceeded(
                        f"no answer within {deadline_s:.1f}s"))
                raise AllFailed(errors)
            if nxt is not None and hedge_at is not None and now >= hedge_at:
                start()
            continue
        running -= 1
        if ok:
            try:
                accepted = step.accept(value)
            except Exception as e:
                accepted, value = False, e
            if accepted:
                return step.name, value
            errors[step.name] = value if isinstance(value, BaseException) \
                else RuntimeError("no usable result")
        else:
            errors[step.name] = value
        if started < len(steps) and (running == 0 or step is steps[started - 1]):
            start()     # the newest step failed: don't wait for its hedge
        elif running == 0:
            raise AllFailed(errors)


def call_with_deadline(fn: Callable[[], Any], timeout_s: float, *,
                       name: str = "call") -> Any:
    """fn() on a worker thread; its result, its exception, or
    DeadlineExceeded after `timeout_s` (the call is then abandoned)."""
    done = threading.Event()
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e
        finally:
            done.set()
    _spawn(name, work)
    if not done.wait(timeout_s):
        raise DeadlineExceeded(f"{name}: no answer within {timeout_s:.1f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]
