"""One background thread that runs every heavy task of both apps, one at a time.

The pipelines are CPU and RAM bound (2 vCPU / 4 GB on the server): two at once would only be slower and
could run out of memory. Each app keeps its own job state; this only decides what runs next.
"""
from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class Task:
    app: str          # "shorts" | "anime"
    job_id: str
    kind: str         # "pipeline" | "render" | "export" ...
    fn: Callable[[], None] = field(repr=False)
    queued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class SerialRunner:
    def __init__(self) -> None:
        self._q: deque[Task] = deque()
        self._cv = threading.Condition()
        self.current: Task | None = None
        threading.Thread(target=self._loop, daemon=True, name="studio-runner").start()

    def submit(self, app: str, job_id: str, kind: str, fn: Callable[[], None]) -> None:
        with self._cv:
            self._q.append(Task(app, job_id, kind, fn))
            self._cv.notify()

    def snapshot(self) -> dict:
        with self._cv:
            cur = self.current
            return {
                "current": {"app": cur.app, "job_id": cur.job_id, "kind": cur.kind} if cur else None,
                "queued": [{"app": t.app, "job_id": t.job_id, "kind": t.kind, "queued_at": t.queued_at}
                           for t in self._q],
            }

    def busy_with(self, app: str, job_id: str) -> bool:
        with self._cv:
            return any(t.app == app and t.job_id == job_id for t in [*self._q, *([self.current] if self.current else [])])

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait()
                self.current = self._q.popleft()
            try:
                self.current.fn()
            except Exception:  # noqa: BLE001  a task reports its own errors; never kill the runner
                traceback.print_exc()
            finally:
                with self._cv:
                    self.current = None
