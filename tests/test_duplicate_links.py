"""A link signed afresh on every click is still the same download.

The same zip from a GitHub release was added twice with nothing asked
(2026-11-03): GitHub sends the browser to a signed address whose signature,
token and expiry change on every click, the extension hands the app that
address, and duplicate detection compared whole links. Signed links are now
compared without their signing parts; a link that is not signed still has to
match as it is.
"""
from test_torrent_duplicates import _Box, window  # noqa: F401

import utils

GH = ("https://release-assets.githubusercontent.com/github-production-release-asset/"
      "1067001001/189f4c16-e1c8-4a85-b0e8-388f6836dd17?sp=r&sv=2018-11-09&sr=b"
      "&spr=https&se=%s&rscd=attachment%%3B+filename%%3Dhyperfetch-firefox-1.9.3.zip"
      "&rsct=application%%2Foctet-stream&skoid=96c2&sktid=398a&skt=%s&ske=%s&sks=b"
      "&skv=2018-11-09&sig=%s&jwt=%s"
      "&response-content-disposition=attachment%%3B%%20filename%%3Dhyperfetch-firefox-1.9.3.zip"
      "&response-content-type=application%%2Foctet-stream")
FIRST = GH % ("2026-11-03T09:28:46Z", "2026-11-03T08:28", "2026-11-04T08:28", "AAA", "e30.a")
AGAIN = GH % ("2026-11-03T09:34:42Z", "2026-11-03T08:34", "2026-11-04T08:34", "BBB", "e30.b")


def test_two_signings_of_one_file_are_the_same_link():
    assert utils.same_link(FIRST, AGAIN)
    assert not utils.same_link(FIRST, AGAIN.replace("189f4c16", "2a0e7c11")), "another file"
    assert not utils.same_link(FIRST, AGAIN.replace("firefox-1.9.3", "chrome-1.9.3"))


def test_other_signers_too():
    s3 = ("https://b.s3.amazonaws.com/a/f.zip?X-Amz-Algorithm=AWS4-HMAC-SHA256"
          "&X-Amz-Credential=AK%%2F%s%%2Fus%%2Fs3%%2Faws4_request&X-Amz-Date=%sT090000Z"
          "&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=%s")
    assert utils.same_link(s3 % ("20261103", "20261103", "ab"), s3 % ("20261104", "20261104", "cd"))
    cf = "https://d1.cloudfront.net/v/f.mp4?Expires=%d&Signature=%s&Key-Pair-Id=K1"
    assert utils.same_link(cf % (1, "a"), cf % (2, "b"))


def test_a_link_that_is_not_signed_is_compared_as_it_is():
    assert utils.same_link("https://a.example/f.zip", "https://a.example/f.zip")
    assert not utils.same_link("https://a.example/get.php?id=1", "https://a.example/get.php?id=2")
    assert not utils.same_link("https://a.example/f.zip?t=1", "https://a.example/f.zip?t=2")
    assert not utils.same_link("magnet:?xt=urn:btih:1", "magnet:?xt=urn:btih:2")


def test_a_signed_link_added_again_is_asked_about(window, tmp_path):
    window.save_dir = str(tmp_path)
    window.add(FIRST)
    assert len(window.added) == 1
    _Box.press = "Show Existing"
    window.add(AGAIN)
    assert len(window.added) == 1, "a second signing of the same file was added"
    assert any("already in the list" in s for said in _Box.shown for s in said)
    assert window.shown == [window.added[0]]
