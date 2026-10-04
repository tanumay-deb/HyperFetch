"""Small text on the privacy page and the extension's welcome page is readable.

WCAG AA asks 4.5:1 between text and what is behind it (3:1 only for large
text). Three places fell short: the privacy page's footer and the welcome
page's hint line were a grey at 2.9:1, and the welcome page's step numbers and
its button put 13-14px white on the brand gradient's lighter stop at 4.2:1.

The palette stays purple. The grey is the pages' own muted tone, and the small
white text sits on a deeper step of the same indigo-to-violet gradient; the
logo is the logo's own file (tests/test_brand_logo.py).
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AA = 4.5
HEX = r"#[0-9a-fA-F]{6}\b"


def _lum(color):
    channels = []
    for i in (1, 3, 5):
        c = int(color[i:i + 2], 16) / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _css(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        page = f.read()
    return re.search(r"<style>(.*?)</style>", page, re.S).group(1)


def _rule(css, selector):
    """The declarations of one rule, by its exact selector."""
    m = re.search(r"(?:^|\})\s*%s\s*\{([^}]*)\}" % re.escape(selector), css, re.S)
    assert m, "no rule for %r" % selector
    return m.group(1)


def _text_color(css, selector):
    return re.search(r"(?<![-\w])color:\s*(%s)" % HEX, _rule(css, selector)).group(1)


def _backgrounds(css, selector):
    return re.findall(HEX, re.search(r"background:([^;]*);", _rule(css, selector)).group(1))


def _var(css, name):
    """The value of one of a page's colour variables."""
    return re.search(r"--%s:\s*(%s)" % (re.escape(name), HEX), css).group(1)


def test_the_arithmetic_is_wcags():
    assert round(contrast("#ffffff", "#000000"), 1) == 21.0
    assert round(contrast("#777777", "#ffffff"), 2) == 4.48      # the textbook near miss


def test_the_privacy_pages_footer_is_readable():
    css = _css("docs", "privacy.html")
    text = _text_color(css, "footer")
    for behind in _backgrounds(css, "body"):                     # every stop of the page gradient
        assert contrast(text, behind) >= AA, "%s on %s" % (text, behind)


def test_the_welcome_pages_hint_is_readable():
    css = _css("chrome_ext", "welcome.html")
    text, (card,) = _text_color(css, ".hint"), _backgrounds(css, ".card")
    assert contrast(text, card) >= AA, "%s on %s" % (text, card)


def test_small_white_text_on_purple_is_readable():
    """The step numbers (13px) and the button (14px) are not large text."""
    css = _css("chrome_ext", "welcome.html")
    for selector in (".num", ".cta"):
        stops = _backgrounds(css, selector)
        assert len(stops) == 2, "expected the two stops of a gradient under %s" % selector
        for stop in stops:
            assert contrast("#ffffff", stop) >= AA, "white on %s under %s" % (stop, selector)


def test_the_sites_brand_line_is_readable():
    """The line above the site's headline is 12px, so it is small text. It is
    set in the page's muted tone rather than the fainter one the section labels
    use: a label may recede, the brand line is there to be read."""
    css = _css("docs", "index.html")
    tone = re.search(r"(?<![-\w])color:\s*var\(--([\w-]+)\)", _rule(css, ".tagline")).group(1)
    text, behind = _var(css, tone), _var(css, "ink")
    assert contrast(text, behind) >= AA, "%s on %s" % (text, behind)
