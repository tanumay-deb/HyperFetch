"""Priority download queue with bounded concurrency and a scheduler.

Shared by the GUI and the Flask API server so browser-triggered downloads show
up in the same list. Uses a Condition (no busy-spin) and tracks every task in
``self.tasks`` for the GUI to render.
"""
import time
import heapq
import logging
import threading

import task as T
from downloader import Downloader

log = logging.getLogger("hyperfetch.queue")


class Queue:
    def __init__(self, name, max_concurrent=3):
        self.name = name
        self.max_concurrent = max_concurrent
        self.active = 0

class QueueManager:
    def __init__(self, queues=None, segments=8):
        self._heap = []                  # ready-to-run tasks (priority ordered)
        self.tasks = []                  # every task ever added, for the GUI
        self.queues = {}
        for q in (queues or [{"name": "Main", "max_concurrent": 3}]):
            self.queues[q["name"]] = Queue(q["name"], q["max_concurrent"])
        self.active = 0
        self.segments = segments

        self.cond = threading.Condition()
        self._stop = False

        threading.Thread(target=self._scheduler, daemon=True).start()

    # ------------------------------------------------------------- public
    def add_task(self, task: "T.DownloadTask", start=True):
        """Register a task. start=False keeps it listed but not scheduled
        (used for 'Download Later' and for state restored from disk)."""
        with self.cond:
            if task not in self.tasks:
                self.tasks.append(task)
            if start:
                task.status = T.QUEUED
                # newly queued / resumed tasks go to the end of the pending order
                task.priority = max((t.priority for t in self._heap), default=-1) + 1
                heapq.heappush(self._heap, task)
            self.cond.notify()
        return task

    def remove_finished(self):
        """Drop completed/cancelled/errored tasks from the visible list."""
        with self.cond:
            self.tasks = [t for t in self.tasks
                          if t.status not in (T.COMPLETED, T.CANCELLED, T.ERROR)]

    def clear_all(self):
        """Cancel everything (incl. in-flight) and empty the visible list."""
        with self.cond:
            for t in self.tasks:
                t.request_cancel()
            self._heap.clear()
            self.tasks = []
            self.cond.notify_all()

    def move(self, task, where):
        """Reorder a QUEUED task within the pending order.

        ``where`` is ``'top'`` | ``'up'`` | ``'down'`` | ``'bottom'``. Only
        affects tasks still waiting in the heap for a free slot; running,
        paused and finished tasks are untouched.
        """
        with self.cond:
            if task not in self._heap:
                return
            ordered = sorted(self._heap)          # current queue order
            i = ordered.index(task)
            n = len(ordered)
            j = {"top": 0, "bottom": n - 1,
                 "up": max(0, i - 1), "down": min(n - 1, i + 1)}.get(where, i)
            if j == i:
                return
            ordered.insert(j, ordered.pop(i))
            for rank, t in enumerate(ordered):    # 0..n-1 → new add appends after
                t.priority = rank
            heapq.heapify(self._heap)
            
            # Sync `self.tasks` so the visual order in the GUI updates
            queued_indices = [idx for idx, t in enumerate(self.tasks) if t in ordered]
            for idx, t in zip(queued_indices, ordered):
                self.tasks[idx] = t
                
            self.cond.notify()

    def get_task(self, task_id):
        """Look a task up by its id (used by the context menu)."""
        return next((t for t in self.tasks if t.id == task_id), None)

    def resume_task(self, task: "T.DownloadTask"):
        """Re-queue a paused/errored task; keeps its segments to resume from disk."""
        if task.status in (T.DOWNLOADING, T.QUEUED, T.COMPLETED):
            return
        task.clear_pause()
        self.add_task(task)

    def pause_task(self, task: "T.DownloadTask"):
        if task.status in (T.DOWNLOADING, T.QUEUED):
            task.request_pause()
            self._drop_from_heap(task)
            if task.status == T.QUEUED:
                task.status = T.PAUSED

    def cancel_task(self, task: "T.DownloadTask"):
        task.request_cancel()
        self._drop_from_heap(task)
        if task.status in (T.QUEUED, T.PAUSED, T.DOWNLOADING):
            task.status = T.CANCELLED

    def remove_task(self, task: "T.DownloadTask"):
        self.cancel_task(task)
        with self.cond:
            if task in self.tasks:
                self.tasks.remove(task)
            self.cond.notify_all()

    def force_start(self, task: "T.DownloadTask"):
        """Force start a task immediately, bypassing concurrency limits."""
        with self.cond:
            if task.status in (T.DOWNLOADING, T.COMPLETED, T.CANCELLED):
                return
            task.clear_pause()
            self._drop_from_heap(task)
            if task not in self.tasks:
                self.tasks.append(task)
            task.status = T.QUEUED # Transition state

            q = self.queues.get(task.queue_name)
            if not q:
                q = Queue(task.queue_name, 3)
                self.queues[task.queue_name] = q
            q.active += 1
            self.active += 1

            threading.Thread(target=self._execute, args=(task, q.name),
                             daemon=True).start()
            self.cond.notify_all()

    def move_to_queue(self, task, qname):
        """Re-assign a task to a different queue and wake the scheduler.
        The bare field write the GUI used to do never notified, so a QUEUED
        task moved into a queue with free capacity could sit idle (the
        scheduler parks on cond.wait with no timeout)."""
        with self.cond:
            task.queue_name = qname
            if qname not in self.queues:
                self.queues[qname] = Queue(qname, 3)
            self.cond.notify_all()

    def delete_queue(self, name):
        """Remove a queue and move its tasks to the Main queue."""
        with self.cond:
            if name in self.queues and name != "Main":
                del self.queues[name]
                for task in self.tasks:
                    if getattr(task, "queue_name", "Main") == name:
                        task.queue_name = "Main"
                self.cond.notify_all()

    def add_queue(self, name, max_concurrent=3):
        """Create a named queue. Returns True if created, False if it already
        exists or the name is blank."""
        name = (name or "").strip()
        if not name:
            return False
        with self.cond:
            if name in self.queues:
                return False
            self.queues[name] = Queue(name, max(1, int(max_concurrent)))
            self.cond.notify_all()
        return True

    def set_max_concurrent(self, qname, n):
        """Change a queue's concurrency cap at runtime and admit waiting tasks
        if the cap was raised. Without the notify a raised cap would only take
        effect on the next add/complete event."""
        with self.cond:
            q = self.queues.get(qname)
            if q:
                q.max_concurrent = max(1, int(n))
                self.cond.notify_all()

    def shutdown(self):
        with self.cond:
            self._stop = True
            self.cond.notify_all()

    def wait_active(self, timeout):
        """Block until every running worker has finished (or ``timeout`` seconds).
        Returns True if everything drained, False if the timeout fired.
        Used by the GUI's close handler so a graceful exit waits for in-flight
        writes/flushes instead of letting daemon threads die mid-write."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self.cond:
            while self.active > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.cond.wait(timeout=remaining)
            return True

    # ------------------------------------------------------------- internal
    def _drop_from_heap(self, task):
        with self.cond:
            if task in self._heap:
                self._heap.remove(task)
                heapq.heapify(self._heap)
            self.cond.notify()

    def _next_ready(self):
        """Return a runnable task or None."""
        if not self._heap:
            return None, None
            
        passed_over = []
        ready_task = None
        while self._heap:
            task = heapq.heappop(self._heap)
            q = self.queues.get(task.queue_name)
            if not q:
                q = Queue(task.queue_name, 3)
                self.queues[task.queue_name] = q
            if q.active < q.max_concurrent:
                ready_task = task
                break
            else:
                passed_over.append(task)
                
        for t in passed_over:
            heapq.heappush(self._heap, t)
            
        return ready_task, None

    def _scheduler(self):
        while True:
            with self.cond:
                while not self._stop:
                    task, wait = self._next_ready()
                    if task is not None:
                        break
                    self.cond.wait(timeout=wait)   # sleeps until notify or timeout
                if self._stop:
                    return
                q = self.queues.get(task.queue_name)
                if not q:
                    q = Queue(task.queue_name, 3)
                    self.queues[task.queue_name] = q
                q.active += 1
                self.active += 1
            # Bind the slot to the queue we charged it against (q.name), passed
            # explicitly — reading task.queue_name again in _execute would let a
            # mid-download "Move to Queue" decrement a DIFFERENT queue than the
            # one incremented here, leaking a slot in one and going negative in
            # the other.
            threading.Thread(target=self._execute, args=(task, q.name),
                             daemon=True).start()

    def _execute(self, task, started_queue):
        try:
            if not task.cancel_requested:
                log.info("start: %s (%s) queue=%s", task.filename, task.id[:8], started_queue)
                Downloader(task, segments=self.segments).run()
                log.info("end: %s status=%s%s", task.filename, task.status,
                         f" error={task.error}" if task.error else "")
        except Exception:
            log.exception("task crashed: %s", task.filename)
        finally:
            # Safety net: a task must NEVER linger in a running state. run() sets
            # DOWNLOADING up front and only reaches a terminal status at the end,
            # so if any engine crashed — or returned without setting one (e.g. an
            # unexpected error between the last byte and finalize) — the task would
            # otherwise sit in the Active group forever at 100% / 0 b/s with only a
            # Pause button and no way out. Force a terminal status here so the card
            # always leaves Active. Honor a pending pause/cancel; otherwise Error
            # (resumable from the bytes already on disk).
            if task.status in (T.DOWNLOADING, T.QUEUED, T.SCHEDULED):
                if task.cancel_requested:
                    task.status = T.CANCELLED
                elif task.pause_requested:
                    task.status = T.PAUSED
                else:
                    task.status = T.ERROR
                    task.error = task.error or "Download ended unexpectedly — Resume to retry"
                log.warning("forced terminal status for %s -> %s", task.filename, task.status)
            with self.cond:
                q = self.queues.get(started_queue)
                if q:
                    q.active -= 1
                self.active -= 1
                # notify_all (not notify) so a closeEvent's wait_active waiter
                # always wakes — a single notify can wake the scheduler instead,
                # leaving wait_active parked until its full timeout fires.
                self.cond.notify_all()
