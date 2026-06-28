"""Search-query parsing for the download list.

Plain words match the filename/URL (case-insensitive, all must match). Tokens
add structured filters, AND-combined with the words and each other:

    status:downloading   status:paused   status:failed   status:active …
    category:video       cat:music
    size:>100mb          size:<1gb       size:>=500m
    date:today           date:yesterday  date:7d   date:week   date:30d
    ext:zip              ext:mp4

`filter_tasks(tasks, query)` returns the matching tasks; pure + unit-tested.
"""
import re
import time
import datetime

import task as T
import utils

# friendly status words -> the Status values they include
_STATUS_ALIASES = {
    "downloading": (T.DOWNLOADING,),
    "paused": (T.PAUSED,),
    "queued": (T.QUEUED,),
    "scheduled": (T.SCHEDULED,),
    "completed": (T.COMPLETED,),
    "done": (T.COMPLETED,),
    "error": (T.ERROR,),
    "failed": (T.ERROR, T.CANCELLED),
    "cancelled": (T.CANCELLED,),
    "active": (T.DOWNLOADING, T.QUEUED, T.SCHEDULED),
}

# decimal units (1000) + binary (1024); a bare "m"/"g" is treated as mb/gb
_UNITS = {
    "b": 1, "k": 1000, "m": 1000**2, "g": 1000**3, "t": 1000**4,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}


def _parse_size(expr):
    """'>100mb' / '<1gb' / '>=500m' / '2gb' -> (op, bytes), or None if unparseable."""
    m = re.match(r"^(>=|<=|>|<|=)?\s*([\d.]+)\s*([a-z]+)?$", expr.strip().lower())
    if not m:
        return None
    op = m.group(1) or ">="
    try:
        val = float(m.group(2))
    except ValueError:
        return None
    mult = _UNITS.get(m.group(3) or "b")
    if mult is None:
        return None
    return op, val * mult


_OPS = {
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    "=": lambda a, b: abs(a - b) <= max(1.0, b * 0.05),   # ~5% tolerance for "="
}


def _parse_date(expr):
    """A predicate over a task's `added` epoch, or None. Supports today/yesterday,
    week/month, and N-day / N-hour windows (e.g. 7d, 12h)."""
    e = (expr or "").strip().lower()
    now = time.time()
    if e in ("today", "yesterday"):
        d = datetime.date.today() - datetime.timedelta(days=1 if e == "yesterday" else 0)
        start = time.mktime(d.timetuple())
        return lambda a: start <= a < start + 86400
    if e in ("week", "7d"):
        return lambda a: a >= now - 7 * 86400
    if e in ("month", "30d"):
        return lambda a: a >= now - 30 * 86400
    m = re.match(r"^(\d+)([dh])$", e)
    if m:
        secs = int(m.group(1)) * (86400 if m.group(2) == "d" else 3600)
        return lambda a: a >= now - secs
    return None


def parse(query):
    """Split a query into (words, filters). filters keys: status, category, size,
    date (predicate), ext."""
    words, filters = [], {}
    for tok in (query or "").split():
        low = tok.lower()
        if low.startswith("status:"):
            filters["status"] = low[7:]
        elif low.startswith("category:") or low.startswith("cat:"):
            filters["category"] = low.split(":", 1)[1]
        elif low.startswith("ext:"):
            filters["ext"] = low[4:].lstrip(".")
        elif low.startswith("date:"):
            dp = _parse_date(low[5:])
            if dp:
                filters["date"] = dp
            else:
                words.append(tok)            # unrecognized date -> plain word
        elif low.startswith("size:") or low.startswith("size"):
            sp = _parse_size(low.split(":", 1)[1] if ":" in low else low[4:])
            if sp:
                filters["size"] = sp
            else:
                words.append(tok)            # not a real size token -> plain word
        else:
            words.append(tok)
    return [w.lower() for w in words], filters


def _matches(t, words, filters):
    hay = f"{t.filename or ''}\n{getattr(t, 'url', '') or ''}".lower()
    if any(w not in hay for w in words):
        return False
    st = filters.get("status")
    if st is not None:
        allowed = _STATUS_ALIASES.get(st)
        if not allowed or t.status not in allowed:
            return False
    cat = filters.get("category")
    if cat is not None and utils.category_for(t.filename or "").lower() != cat:
        return False
    size = filters.get("size")
    if size is not None:
        op, thresh = size
        if not _OPS[op](float(getattr(t, "total_size", 0) or 0), thresh):
            return False
    ext = filters.get("ext")
    if ext and not (t.filename or "").lower().endswith("." + ext):
        return False
    date_pred = filters.get("date")
    if date_pred is not None and not date_pred(float(getattr(t, "added", 0) or 0)):
        return False
    return True


def filter_tasks(tasks, query):
    """Tasks matching the query (empty query -> unchanged)."""
    if not (query or "").strip():
        return list(tasks)
    words, filters = parse(query)
    return [t for t in tasks if _matches(t, words, filters)]
