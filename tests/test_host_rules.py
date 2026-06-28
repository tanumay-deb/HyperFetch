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
