"""Priority download queue with bounded concurrency and a scheduler.

Shared by the GUI and the Flask API server so browser-triggered downloads show
up in the same list. Uses a Condition (no busy-spin) and tracks every task in
``self.tasks`` for the GUI to render.
"""
import time
import heapq
import threading

import task as T
from downloader import Downloader


class QueueManager:
    def __init__(self, max_concurrent=3, segments=8):
        self._heap = []                  # ready-to-run tasks (priority ordered)
        self.tasks = []                  # every task ever added, for the GUI
        self.active = 0
        self.max_concurrent = max_concurrent
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
                heapq.heappush(self._heap, task)
            self.cond.notify()
        return task

    def remove_finished(self):
        """Drop completed/cancelled/errored tasks from the visible list."""
        with self.cond:
            self.tasks = [t for t in self.tasks
                          if t.status not in (T.COMPLETED, T.CANCELLED, T.ERROR)]

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

    def shutdown(self):
        with self.cond:
            self._stop = True
            self.cond.notify_all()

    # ------------------------------------------------------------- internal
    def _drop_from_heap(self, task):
        with self.cond:
            if task in self._heap:
                self._heap.remove(task)
                heapq.heapify(self._heap)
            self.cond.notify()

    def _next_ready(self):
        """Return a runnable task or None."""
        if not self._heap or self.active >= self.max_concurrent:
            return None, None
        task = heapq.heappop(self._heap)
        return task, None

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
                self.active += 1
            threading.Thread(target=self._execute, args=(task,), daemon=True).start()

    def _execute(self, task):
        try:
            if not task.cancel_requested:
                Downloader(task, segments=self.segments).run()
        finally:
            with self.cond:
                self.active -= 1
                self.cond.notify()
