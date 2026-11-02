"""In-memory registry of live runs: numbers each run's events and lets SSE
streams wait for new ones. One Condition guards everything; every append wakes
all waiters. Nothing here calls out while the lock is held except validation,
which raises before any state is touched."""
from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from api import config
from api.schema import LIVE_EVENT


class RegistryFull(Exception):
    """max_concurrent runs are already running."""


@dataclass
class LiveRun:
    run_id: str
    status: str
    created_at: float
    finished_at: float | None
    events: list[dict] = field(default_factory=list)
    task: Any = None


class RunRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_concurrent: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ttl = ttl_seconds
        self._max = max_concurrent
        self._clock = clock
        self._runs: dict[str, LiveRun] = {}
        self._cond = threading.Condition()

    @classmethod
    def from_config(cls) -> "RunRegistry":
        return cls(
            ttl_seconds=config.LIVE_TTL_SECONDS,
            max_concurrent=config.LIVE_MAX_CONCURRENT,
        )

    def _evict(self) -> None:
        # Caller holds the lock. Running runs have finished_at None: never dropped.
        now = self._clock()
        for rid in [
            rid
            for rid, r in self._runs.items()
            if r.finished_at is not None and now - r.finished_at > self._ttl
        ]:
            del self._runs[rid]

    def start(self) -> str:
        with self._cond:
            self._evict()
            if sum(r.status == "running" for r in self._runs.values()) >= self._max:
                raise RegistryFull(f"{self._max} runs already running")
            run_id = uuid.uuid4().hex
            self._runs[run_id] = LiveRun(run_id, "running", self._clock(), None)
            return run_id

    def append(self, run_id: str, event: dict) -> int:
        with self._cond:
            self._evict()
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status != "running":
                raise ValueError(f"run {run_id} is already {run.status}")
            seq = len(run.events) + 1
            # Raises ValidationError before anything is stored; the with-block
            # releases the lock on the way out.
            validated = LIVE_EVENT.validate_python({**event, "seq": seq})
            run.events.append(LIVE_EVENT.dump_python(validated, mode="json"))
            if validated.type in ("done", "failed"):
                run.status = validated.type
                run.finished_at = self._clock()
            self._cond.notify_all()
            return seq

    def _after(self, run_id: str, seq: int) -> list[dict]:
        run = self._runs.get(run_id)
        return [] if run is None else [copy.deepcopy(e) for e in run.events if e["seq"] > seq]

    def events_after(self, run_id: str, seq: int) -> list[dict]:
        with self._cond:
            self._evict()
            return self._after(run_id, seq)

    def wait_after(self, run_id: str, seq: int, timeout: float) -> list[dict]:
        deadline = self._wall() + timeout
        with self._cond:
            while True:
                self._evict()
                run = self._runs.get(run_id)
                if run is None:
                    return []
                newer = self._after(run_id, seq)
                if newer or run.status != "running":
                    return newer
                remaining = deadline - self._wall()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    @staticmethod
    def _wall() -> float:
        # Waiting is real time even when tests inject a fake registry clock.
        return time.monotonic()

    def get(self, run_id: str) -> LiveRun | None:
        with self._cond:
            self._evict()
            run = self._runs.get(run_id)
            # A snapshot: the worker keeps mutating the stored run. task stays shared.
            return None if run is None else replace(run, events=copy.deepcopy(run.events))


registry = RunRegistry.from_config()
