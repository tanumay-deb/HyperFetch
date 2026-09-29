"""The Windows notification when downloads are added.

It is for downloads that arrive while you are not looking at HyperFetch: from
the browser, the web client, another launch, or with the window in the tray.
When the window is in front, the new card appearing in the list is the news,
and a notification on top of it would only say it twice.

A burst - "Download all images" sends forty, one by one - is one notification:
it waits for a short quiet before speaking, and counts what came in meanwhile.
"""
import time

NAMES_SHOWN = 3


class AddedNotifier:
    """Watches the task list and says, now and then, what was added."""

    QUIET = 1.0       # seconds without a new download before the notification goes

    def __init__(self, clock=time.time):
        self._clock = clock
        self._seen = None          # task ids already known; None until the first tick
        self._batch = []           # names added since the last notification
        self._due = 0.0

    def tick(self, tasks, looking, enabled):
        """Call on every refresh. Returns (title, body) when a notification is
        due, else None. ``looking``: the window is in front; ``enabled``: the
        setting is on."""
        now = self._clock()
        if self._seen is None:
            # The list restored at startup is not news.
            self._seen = {t.id for t in tasks}
            return None
        new = [t for t in tasks if t.id not in self._seen]
        if new:
            self._seen.update(t.id for t in new)
            if enabled and not looking:
                self._batch.extend((t.filename or t.url or "download") for t in new)
                self._due = now + self.QUIET
        if not self._batch or now < self._due:
            return None
        names, self._batch = self._batch, []
        if len(names) == 1:
            return "Download added", names[0]
        more = "…" if len(names) > NAMES_SHOWN else ""
        return "%d downloads added" % len(names), ", ".join(names[:NAMES_SHOWN]) + more


class YieldNotifier:
    """Says when a torrent gave its slot back, and what started in its place.

    A torrent gives way after minutes with no peers, or with no seeders while a
    live torrent waits (torrent.py). Done silently, that looks like the app
    reordering your list behind your back, so it is said - in a notification,
    batched like AddedNotifier, naming any torrent that started downloading in
    the same moment.
    """

    QUIET = 1.0

    def __init__(self, clock=time.time):
        self._clock = clock
        self._announced = None     # task id -> the yielded_at already announced
        self._was = {}             # task id -> status at the last tick
        self._batch = []           # torrents that gave way, not yet announced
        self._started = []         # names that began downloading meanwhile
        self._due = 0.0

    def tick(self, tasks):
        """Call on every refresh. Returns (title, body) when a note is due."""
        import task as T
        from torrent import is_torrent_task
        now = self._clock()
        if self._announced is None:
            self._announced = {t.id: getattr(t, "yielded_at", 0.0) for t in tasks}
            self._was = {t.id: t.status for t in tasks}
            return None
        for t in tasks:
            at = float(getattr(t, "yielded_at", 0.0) or 0.0)
            if at and at != self._announced.get(t.id):
                self._announced[t.id] = at
                self._batch.append(t)
                self._due = now + self.QUIET
        # Only a torrent can take a torrent's slot, so only a torrent that began
        # downloading meanwhile is named as having started in its place.
        for t in tasks:
            if (self._batch and t.status == T.DOWNLOADING
                    and self._was.get(t.id) != T.DOWNLOADING and t not in self._batch
                    and is_torrent_task(t.url, t.filename)):
                self._started.append(t.filename or t.url)
        self._was = {t.id: t.status for t in tasks}
        if not self._batch or now < self._due:
            return None
        batch, started = self._batch, self._started
        self._batch, self._started = [], []
        instead = ""
        if started:
            instead = " Started " + ", ".join('"%s"' % n for n in started[:NAMES_SHOWN]) + " instead."
        if len(batch) == 1:
            t = batch[0]
            left = max(0, float(getattr(t, "retry_after", 0) or 0) - now)
            mins = max(1, int(round(left / 60.0)))
            why = "No seeders" if t.yield_reason == "no seeders" else "No peers"
            return ('Paused "%s"' % (t.filename or t.url),
                    "%s — trying again in %d min.%s" % (why, mins, instead))
        names = ", ".join('"%s" (%s)' % (t.filename or t.url, t.yield_reason or "no peers")
                          for t in batch[:NAMES_SHOWN])
        more = "…" if len(batch) > NAMES_SHOWN else ""
        return ("%d torrents paused" % len(batch),
                "%s%s — each will try again later.%s" % (names, more, instead))
