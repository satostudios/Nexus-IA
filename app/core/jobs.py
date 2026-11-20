"""Long-running work that continues after a command has been answered.

Writing a program takes minutes on a laptop CPU. The tool that starts it
answers at once ("Vou escrever o app… te aviso quando terminar"), and the work
runs here, one job at a time, while NEXUS keeps taking commands. The assistant
subscribes to progress (status line) and completion (spoken reply). Plain
callbacks, no Qt, so tools and tests can use it directly.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("nexus.jobs")


@dataclass
class JobResult:
    message: str
    success: bool = True
    extra: list[str] = field(default_factory=list)
    label: str = ""   # filled in from the job


class JobContext:
    def __init__(self, runner: "BackgroundJobs", label: str) -> None:
        self._runner = runner
        self.label = label
        self.cancel_event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def progress(self, text: str) -> None:
        self._runner._emit_progress(text)


class BackgroundJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: JobContext | None = None
        self._thread: threading.Thread | None = None
        self.on_progress: Callable[[str], None] = lambda _text: None
        self.on_finished: Callable[[JobResult], None] = lambda _result: None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._current is not None

    @property
    def label(self) -> str:
        with self._lock:
            return self._current.label if self._current else ""

    def start(self, label: str, work: Callable[[JobContext], JobResult]) -> bool:
        """Run ``work`` on its own thread. False if another job is still running."""
        with self._lock:
            if self._current is not None:
                return False
            ctx = JobContext(self, label)
            self._current = ctx
            self._thread = threading.Thread(target=self._run, args=(ctx, work), name=f"job-{label[:20]}",
                                            daemon=True)
            self._thread.start()
        log.info("job started: %s", label)
        return True

    def cancel(self) -> bool:
        with self._lock:
            ctx = self._current
        if ctx is None:
            return False
        ctx.cancel_event.set()
        log.info("job cancel requested: %s", ctx.label)
        return True

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _run(self, ctx: JobContext, work: Callable[[JobContext], JobResult]) -> None:
        try:
            result = work(ctx)
        except Exception:
            log.exception("job %s crashed", ctx.label)
            from app.i18n import tr

            result = JobResult(tr("Something went wrong doing that."), success=False)
        finally:
            with self._lock:
                self._current = None
        result.label = result.label or ctx.label
        log.info("job finished: %s -> %s", ctx.label, result.success)
        try:
            self.on_progress("")
            self.on_finished(result)
        except Exception:
            log.exception("job completion handler failed")

    def _emit_progress(self, text: str) -> None:
        try:
            self.on_progress(text)
        except Exception:
            log.debug("progress handler failed", exc_info=True)
