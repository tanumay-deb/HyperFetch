"""Every icon the UI asks for must exist.

themed_icon returns an empty QIcon for a name it cannot find, which renders as
nothing at all — so a typo or a never-added asset is invisible rather than
obviously broken. Select All, Select None and the Files search box all shipped
iconless that way.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"build", "dist", "node_modules", ".git", "tests"}


def _available():
    return {p.stem for p in (ROOT / "assets" / "icons").glob("*.svg")}


def _referenced():
    refs = {}
    for f in ROOT.rglob("*.py"):
        if SKIP_DIRS & set(f.relative_to(ROOT).parts):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'themed_icon\(\s*["\']([\w-]+)["\']', text):
            refs.setdefault(m.group(1), set()).add(
                str(f.relative_to(ROOT)))
    return refs


def test_every_referenced_icon_exists():
    have = _available()
    missing = {n: sorted(f) for n, f in _referenced().items() if n not in have}
    assert not missing, f"icons referenced but absent from assets/icons: {missing}"


def test_the_icons_are_parseable_svg():
    """A file that exists but will not render is the same bug wearing a hat."""
    import xml.etree.ElementTree as ET
    bad = []
    for p in (ROOT / "assets" / "icons").glob("*.svg"):
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError as e:
            bad.append(f"{p.name}: {e}")
            continue
        if not root.tag.endswith("svg"):
            bad.append(f"{p.name}: root element is {root.tag}")
    assert not bad, bad
