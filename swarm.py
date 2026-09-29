"""How healthy a torrent's swarm is known to be.

Two sources say how many seeders a torrent has: what it saw itself the last
time it ran (``last_seeds``, from aria2's numSeeders), and what its trackers
said while it waited (``swarm_seeds``, from scrape.py). The newer of the two is
what is known, and a count older than a day is not known at all - a swarm is
not the same crowd a day later.

The queue starts torrents in the order of health_rank: known seeders, most
first; then unknown; then known to have none. And a torrent stuck without
seeders gives way only to one that is not known dead (queue_manager.py).
"""

# A running torrent with no seeders and no new bytes for this long gives its
# slot to a waiting torrent that is not known dead (torrent.py).
NO_SEED_YIELD = 300

# An observation older than this says nothing about the swarm today.
FRESH_FOR = 24 * 3600


def known_seeds(task, now):
    """The seeders a torrent is known to have, or None when nothing fresh is known."""
    best_at, best = 0.0, None
    for n, at in ((getattr(task, "last_seeds", None), getattr(task, "last_seeds_at", 0.0)),
                  (getattr(task, "swarm_seeds", None), getattr(task, "swarm_at", 0.0))):
        at = float(at or 0.0)
        if n is None or now - at > FRESH_FOR:
            continue
        if at > best_at:
            best_at, best = at, int(n)
    return best


def is_known_dead(task, now):
    return known_seeds(task, now) == 0


def health_rank(task, now):
    """Sort key, lower first: known seeders (most first), unknown, known none."""
    n = known_seeds(task, now)
    if n is None:
        return (1, 0)
    if n > 0:
        return (0, -n)
    return (2, 0)
