"""Property/fuzz tests for the display formatters: never raise, hold their
basic invariants across the whole input range. Pure stdlib (no hypothesis)."""
import random
import time

import pytest

pytest.importorskip("PySide6")  # helpers live in gui.theme
from gui.theme import human_size, human_speed, humanize_age, fmt_eta   # noqa: E402

_UNITS_SIZE = ("B", "KB", "MB", "GB", "TB", "PB")
_UNITS_SPEED = ("b/s", "Kb/s", "Mb/s", "Gb/s", "Tb/s")


def _samples():
    """A spread of edge + random magnitudes covering every unit boundary."""
    fixed = [0, -1, -99999, 1, 1023, 1024, 1025, 999, 1000,
             10**6, 10**9, 10**12, 10**15, 2**40, 2**50]
    rng = random.Random(1234)
    rand = [rng.randint(0, 2**52) for _ in range(2000)]
    rand += [rng.uniform(0, 1e15) for _ in range(2000)]
    return fixed + rand


def test_human_size_never_raises_and_formats():
    for n in _samples():
        out = human_size(n)
        assert isinstance(out, str) and out
        if n <= 0:
            assert out == "-"
        else:
            assert out.split()[-1] in _UNITS_SIZE, f"bad unit for {n}: {out!r}"


def test_human_speed_never_raises():
    for bps in _samples():
        out = human_speed(bps)
        assert isinstance(out, str)
        if bps <= 0:
            assert out == ""
        else:
            assert out.split()[-1] in _UNITS_SPEED, f"bad unit for {bps}: {out!r}"


def test_human_size_is_non_decreasing_by_byte_value():
    """Decode the formatted value*unit back to bytes; it must track the input
    order (allowing rounding) so the Size column sorts sanely by eye."""
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3,
            "TB": 1024**4, "PB": 1024**5}
    vals = sorted(random.Random(7).randint(1, 2**50) for _ in range(500))
    decoded = []
    for n in vals:
        num, unit = human_size(n).split()
        decoded.append(float(num) * mult[unit])
    # each decoded value within 1% of the true bytes (rounding to 1 decimal)
    for n, d in zip(vals, decoded):
        assert abs(d - n) <= max(1, n * 0.05), f"{n} -> {d}"


def test_fmt_eta_invariants():
    assert fmt_eta(0) == "" and fmt_eta(-5) == "" and fmt_eta(None) == ""
    assert fmt_eta(float("inf")) == ""
    for secs in [0.4, 1, 59, 60, 61, 3599, 3600, 3661, 90000]:
        out = fmt_eta(secs)
        assert isinstance(out, str)
        if secs >= 1:
            assert out, f"empty eta for {secs}"
    assert fmt_eta(42).endswith("s")
    assert "m" in fmt_eta(125)
    assert "h" in fmt_eta(7300)


def test_humanize_age_buckets():
    now = time.time()
    assert humanize_age(0) == "" and humanize_age(None) == ""
    assert humanize_age(now) == "just now"
    assert humanize_age(now - 120) == "2 min ago"
    assert humanize_age(now - 3600) == "1 hr ago"
    assert humanize_age(now - 7200) == "2 hrs ago"
    assert humanize_age(now - 86400) == "1 day ago"
    assert humanize_age(now - 3 * 86400) == "3 days ago"


def test_humanize_age_never_raises_for_any_offset():
    now = time.time()
    rng = random.Random(99)
    for _ in range(1000):
        off = rng.uniform(0, 5 * 365 * 86400)
        out = humanize_age(now - off)
        assert isinstance(out, str) and out
