"""How healthy a torrent's swarm is known to be (swarm.py).

Two sources say how many seeders a torrent has: what it saw itself the last
time it ran (last_seeds), and what its trackers said while it waited
(swarm_seeds, Part A). The newer one is what is known; a count older than a
day is not known at all. The queue starts torrents in this order: known
seeders (most first), then unknown, then known to have none.
"""
import task as T
import swarm

NOW = 1_000_000.0


def _t(last=None, last_at=0.0, scraped=None, scraped_at=0.0):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, "x")
    t.last_seeds, t.last_seeds_at = last, last_at
    t.swarm_seeds, t.swarm_at = scraped, scraped_at
    return t


def test_nothing_observed_is_unknown():
    assert swarm.known_seeds(_t(), NOW) is None


def test_the_newer_observation_wins():
    assert swarm.known_seeds(_t(last=5, last_at=NOW - 60, scraped=0, scraped_at=NOW - 600), NOW) == 5
    assert swarm.known_seeds(_t(last=5, last_at=NOW - 600, scraped=0, scraped_at=NOW - 60), NOW) == 0


def test_a_count_older_than_a_day_is_not_known():
    old = NOW - swarm.FRESH_FOR - 1
    assert swarm.known_seeds(_t(last=9, last_at=old), NOW) is None
    assert swarm.known_seeds(_t(last=9, last_at=old, scraped=2, scraped_at=NOW - 60), NOW) == 2


def test_known_dead():
    assert swarm.is_known_dead(_t(last=0, last_at=NOW - 60), NOW)
    assert not swarm.is_known_dead(_t(), NOW), "unknown is not dead"
    assert not swarm.is_known_dead(_t(last=1, last_at=NOW - 60), NOW)


def test_the_order_seeders_then_unknown_then_dead():
    many = _t(last=30, last_at=NOW - 60)
    few = _t(scraped=2, scraped_at=NOW - 60)
    unknown = _t()
    dead = _t(last=0, last_at=NOW - 60)
    ranked = sorted([dead, unknown, few, many], key=lambda t: swarm.health_rank(t, NOW))
    assert ranked == [many, few, unknown, dead]


def test_the_counts_survive_a_restart():
    t = _t(last=7, last_at=NOW - 60, scraped=12, scraped_at=NOW - 30)
    t.swarm_peers = 40
    back = T.DownloadTask.from_dict(t.to_dict())
    assert (back.last_seeds, back.last_seeds_at) == (7, NOW - 60)
    assert (back.swarm_seeds, back.swarm_peers, back.swarm_at) == (12, 40, NOW - 30)


def test_a_list_saved_before_this_existed_loads_as_unknown():
    d = T.DownloadTask("magnet:?xt=urn:btih:" + "b" * 40, "x").to_dict()
    for k in ("last_seeds", "last_seeds_at", "swarm_seeds", "swarm_peers", "swarm_at"):
        d.pop(k, None)
    back = T.DownloadTask.from_dict(d)
    assert swarm.known_seeds(back, NOW) is None
    assert back.swarm_peers is None
