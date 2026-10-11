"""Background analysis so hooks return immediately and never wait on the LLM."""
import logging
import queue
import threading

from . import engine

log = logging.getLogger("align.worker")


class Worker:
    def __init__(self):
        self.queue: queue.Queue[str] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="align-worker", daemon=True)

    def start(self) -> "Worker":
        # Recover events left pending by a previous run.
        for event_id in engine.pending_event_ids():
            self.queue.put(event_id)
        self.thread.start()
        return self

    def submit(self, event_id: str) -> None:
        self.queue.put(event_id)

    def _run(self) -> None:
        while True:
            event_id = self.queue.get()
            try:
                engine.analyze_event(event_id)
            except Exception as e:
                log.exception("analysis failed for %s", event_id)
                engine.mark_failed(event_id, f"{type(e).__name__}: {e}")
            finally:
                self.queue.task_done()
