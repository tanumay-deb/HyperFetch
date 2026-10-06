"""The code signing policy SignPath Foundation asks a project to publish.

An application for its free certificate was made on 2026-10-06. Its terms
(https://signpath.org/terms) want, on the project's own pages, a section headed
"Code signing policy" that carries their attribution line word for word, says
who commits and who approves, and links the privacy policy. The section also
says where things really stand: nothing is signed until they accept.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTRIBUTION = "Free code signing provided by SignPath.io, certificate by SignPath Foundation"


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _section():
    readme = _read("README.md")
    m = re.search(r"^## Code signing policy\s*$(.*?)(?=^## |\Z)", readme, re.M | re.S)
    assert m, "README.md has no section headed 'Code signing policy'"
    return m.group(1)


def test_it_carries_their_line_word_for_word():
    assert ATTRIBUTION in _section()


def test_it_says_who_commits_and_who_approves():
    text = _section()
    assert re.search(r"Committers and reviewers", text)
    assert re.search(r"Approvers", text)
    assert text.count("https://github.com/tanumay-deb") >= 2, "the roles name nobody"


def test_it_links_the_privacy_policy():
    assert "https://tanumay-deb.github.io/HyperFetch/privacy.html" in _section()


def test_it_does_not_claim_a_signature_that_is_not_there_yet():
    """Until SignPath accepts the project and a release is built with their
    step, the files are not signed, and the page has to say so."""
    assert re.search(r"not (yet )?signed", _section())


def test_the_landing_page_links_to_it_by_that_name():
    html = _read("docs", "index.html")
    assert re.search(r"<a\b[^>]*href=\"[^\"]*#code-signing-policy\"[^>]*>\s*Code signing policy\s*</a>",
                     html)
