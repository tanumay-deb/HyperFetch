"""Per-host rules: host matching + downloader segment override."""
import utils
import task as T
from downloader import Downloader


def test_host_rule_matching(monkeypatch):
    monkeypatch.setattr(utils, "HOST_RULES",
                        {"example.com": {"segments": 4}, "cdn.foo.net": {"ytdlp": True}})
    assert utils.host_rule("https://example.com/f.zip") == {"segments": 4}
    assert utils.host_rule("https://www.example.com/f.zip") == {"segments": 4}   # subdomain
    assert utils.host_rule("http://EXAMPLE.com:8080/x") == {"segments": 4}       # case + port
    assert utils.host_rule("https://cdn.foo.net/v") == {"ytdlp": True}
    assert utils.host_rule("https://other.com/x") == {}
    assert utils.host_rule("magnet:?xt=urn:btih:abc") == {}


def test_downloader_segment_override(monkeypatch):
    monkeypatch.setattr(utils, "MAX_CONNECTIONS", 0)
    monkeypatch.setattr(utils, "HOST_RULES", {"slowhost.com": {"segments": 2}})
    t = T.DownloadTask("https://slowhost.com/big.iso", "C:/dl/big.iso")
    assert Downloader(t, segments=8).num_segments == 2
    # a host with no rule is unaffected
    t2 = T.DownloadTask("https://fast.com/big.iso", "C:/dl/big.iso")
    assert Downloader(t2, segments=8).num_segments == 8


def test_segment_override_capped_by_max(monkeypatch):
    monkeypatch.setattr(utils, "MAX_CONNECTIONS", 4)
    monkeypatch.setattr(utils, "HOST_RULES", {"h.com": {"segments": 16}})
    t = T.DownloadTask("https://h.com/x", "C:/dl/x")
    assert Downloader(t, segments=8).num_segments == 4    # rule 16 capped by Max Connections


def test_the_most_specific_rule_wins_whatever_order_they_were_added(monkeypatch):
    """The first matching rule won: one for cdn.example.com added after one for
    example.com was never used, and the dialog, which lists them by name,
    showed nothing of the order that decided it (2026-11-11)."""
    site, cdn = {"segments": 2, "ytdlp": True}, {"segments": 16, "ytdlp": False}
    for rules in ({"example.com": site, "cdn.example.com": cdn},
                  {"cdn.example.com": cdn, "example.com": site}):
        monkeypatch.setattr(utils, "HOST_RULES", rules)
        assert utils.host_rule("https://cdn.example.com/f.bin") == cdn
        assert utils.host_rule("https://a.cdn.example.com/f.bin") == cdn
        assert utils.host_rule("https://www.example.com/f.bin") == site
        assert utils.host_rule("https://example.com/f.bin") == site
    levels = [("example.com", {"segments": 2}), ("b.example.com", {"segments": 3}),
              ("a.b.example.com", {"segments": 4})]
    for order in (levels, levels[1:] + levels[:1], levels[::-1]):
        monkeypatch.setattr(utils, "HOST_RULES", dict(order))
        got = {h: utils.host_rule("https://%s/x" % h).get("segments") for h in
               ("x.a.b.example.com", "a.b.example.com", "b.example.com", "c.example.com")}
        assert got == {"x.a.b.example.com": 4, "a.b.example.com": 4,
                       "b.example.com": 3, "c.example.com": 2}, order
    monkeypatch.setattr(utils, "HOST_RULES", {"ample.com": {"segments": 5}})
    assert utils.host_rule("https://example.com/x") == {}, "a name that only ends the same"


def test_a_rule_that_is_not_a_rule_is_none(monkeypatch):
    """A settings.json edited by hand: the longer host now matches first, and
    its value went to every caller - the name scout's among them, every 3 s."""
    import downloader
    monkeypatch.setattr(utils, "MAX_CONNECTIONS", 0)
    for rules in ({"example.com": {"segments": 2}, "cdn.example.com": 8},
                  {"cdn.example.com": 8, "example.com": {"segments": 2}}):
        monkeypatch.setattr(utils, "HOST_RULES", rules)
        assert utils.host_rule("https://cdn.example.com/f.bin") == {}
        assert downloader.connections_for("https://cdn.example.com/f.bin", 6) == 6
