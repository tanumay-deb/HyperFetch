"""One download speed limit, kept by two engines.

Settings -> Downloads -> Download Speed Limit used to reach only this app's own
connections: a token bucket every HTTP segment waits on. aria2, which does the
torrents, was never told, so with "5 Mb/s" set a torrent ran flat out.

aria2 has its own limit and now gets a part of the app's. Which part is
`utils.share_speed_limit`: each side is owed a share by how many downloads it
is running, and this app's side also takes what the torrents are not using.
"""
import time

import utils

L = 1_000_000


# ---- how the limit is shared out ---------------------------------------------
def test_no_limit_is_no_limit_for_either():
    assert utils.share_speed_limit(0, 2, 3, 500_000) == (0, 0)


def test_one_kind_alone_has_all_of_it():
    assert utils.share_speed_limit(L, 0, 2, 900_000) == (L, L)
    assert utils.share_speed_limit(L, 3, 0, 0) == (L, L)


def test_both_busy_split_it_by_how_many_each_runs():
    http, torrent = utils.share_speed_limit(L, 1, 3, torrent_rate=750_000)
    assert (http, torrent) == (250_000, 750_000)
    assert http + torrent == L


def test_what_torrents_leave_unused_goes_to_the_rest():
    """A torrent with no seeders is still "running". Holding half the limit
    back for it would cap an ordinary download at half, for nothing."""
    http, torrent = utils.share_speed_limit(L, 1, 1, torrent_rate=0)
    assert http == L
    assert torrent == 500_000, "aria2's part moves only when the mix does"


def test_a_slow_torrent_is_left_room_to_grow():
    http, _ = utils.share_speed_limit(L, 1, 1, torrent_rate=100_000)
    assert http == L - 120_000


def test_the_rest_is_never_squeezed_below_its_own_part():
    """aria2 takes its part in bursts, and reports the burst."""
    http, _ = utils.share_speed_limit(L, 1, 1, torrent_rate=5_000_000)
    assert http == 500_000


def test_a_part_is_never_zero_because_zero_means_no_limit():
    for n_http, n_torrent, rate in ((50, 1, 0), (1, 50, 20_000), (1, 1, 10 ** 9)):
        http, torrent = utils.share_speed_limit(20_000, n_http, n_torrent, rate)
        assert http > 0 and torrent > 0, (n_http, n_torrent, http, torrent)


# ---- the bucket lets a part of its limit through -------------------------------
def test_a_share_paces_below_the_limit():
    rl = utils.RateLimiter()
    rl.set_limit(400_000)
    rl.set_share(50_000)
    rl.wait(50_000)                     # what the bucket holds to begin with
    t0 = time.monotonic()
    rl.wait(50_000)                     # a second's worth at the share...
    took = time.monotonic() - t0
    assert 0.6 < took < 3, f"{took:.2f}s: not paced at the share"   # ...an eighth at the limit


def test_a_share_of_nothing_or_of_everything_is_the_whole_limit():
    rl = utils.RateLimiter()
    rl.set_limit(L)
    for share in (0, L, 2 * L):
        rl.set_share(share)
        assert rl.rate == L


def test_a_share_means_nothing_without_a_limit():
    rl = utils.RateLimiter()
    rl.set_share(50_000)
    t0 = time.monotonic()
    rl.wait(10_000_000)
    assert time.monotonic() - t0 < 0.05


def test_a_new_limit_starts_whole():
    rl = utils.RateLimiter()
    rl.set_limit(400_000)
    rl.set_share(10_000)
    rl.set_limit(800_000)
    assert rl.rate == 800_000


def test_the_same_limit_set_again_gives_no_fresh_burst():
    """The window applies the limit again every minute (its scheduled value may
    have come or gone). Each time refilled the bucket: a free second's worth."""
    rl = utils.RateLimiter()
    rl.set_limit(50_000)
    rl.wait(50_000)
    rl.set_limit(50_000)
    t0 = time.monotonic()
    rl.wait(25_000)
    assert time.monotonic() - t0 > 0.3, "the bucket was refilled"


def test_the_same_limit_set_again_keeps_the_share():
    rl = utils.RateLimiter()
    rl.set_limit(L)
    rl.set_share(250_000)
    rl.set_limit(L)
    assert rl.rate == 250_000
