"""The website (docs/, served by GitHub Pages) stays findable.

Search Console proves the owner by fetching the verification file from the
site and keeps re-checking it: remove it and the property is lost. The
sitemap is what it was told to crawl."""
import os
import xml.etree.ElementTree as ET

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")


def test_google_search_console_can_verify_the_site():
    name = "googleff1f3b2ed6aa9c93.html"
    with open(os.path.join(DOCS, name), encoding="utf-8") as f:
        assert f.read().strip() == "google-site-verification: " + name


def test_the_sitemap_lists_pages_that_exist():
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    base = "https://tanumay-deb.github.io/HyperFetch/"
    locs = [e.text for e in ET.parse(os.path.join(DOCS, "sitemap.xml")).getroot()
            .findall("s:url/s:loc", ns)]
    assert base in locs
    for loc in locs:
        assert loc.startswith(base), loc
        page = loc[len(base):] or "index.html"
        assert os.path.isfile(os.path.join(DOCS, page)), "the sitemap lists a page that is not there: " + loc
