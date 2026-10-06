"""The landing page and the README point at HyperFetch on the web.

The hosted site (hyperfetch-dl.duckdns.org) takes a link, a magnet or a torrent
from any browser, with nothing to install. Nothing in this repository linked to
it, so neither a visitor nor a search engine following the project's own pages
could find it. A plain link, one a crawler follows.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTED = "https://hyperfetch-dl.duckdns.org/"


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("page", ["docs/index.html", "README.md"])
def test_both_link_it(page):
    assert HOSTED in _read(*page.split("/")), f"{page} does not link the hosted site"


def test_the_landing_page_links_it_where_a_crawler_follows():
    html = _read("docs", "index.html")
    links = re.findall(r"<a\b[^>]*href=\"%s\"[^>]*>(.*?)</a>" % re.escape(HOSTED), html, re.S)
    assert links, "no <a href> to the hosted site"
    assert all(text.strip() for text in links), "a link with no words on it"
    tags = re.findall(r"<a\b[^>]*href=\"%s\"[^>]*>" % re.escape(HOSTED), html)
    assert not any("nofollow" in tag for tag in tags)


def test_it_is_in_the_footer_with_the_other_ways_out():
    html = _read("docs", "index.html")
    footer = html[html.index("<footer>"):html.index("</footer>")]
    assert HOSTED in footer
