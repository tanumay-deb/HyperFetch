"""Per-host rules: host matching + downloader segment override."""
import os

import pytest

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


# ---- a host as a link names it (2026-11-12) ----------------------------------
@pytest.mark.parametrize("typed, kept", [
    ("Example.COM", "example.com"),
    ("https://user:pw@dl.example.com:8080/x?y=1#z", "dl.example.com"),
    ("*.example.com", "example.com"),
    ("example.com.", "example.com"),
    ("files.example.com?x=1", "files.example.com"),
    ("[::1]:8080", "::1"),
    ("bücher.de", "xn--bcher-kva.de"),
    ("   ", ""),
])
def test_a_host_is_kept_as_a_link_names_it(typed, kept):
    """The dialog kept "*.example.com", a trailing dot or a "?query" as typed
    - rules that never match - and "[::1]:8080" as "[" (2026-11-12)."""
    assert utils.rule_host(typed) == kept


def test_rules_match_ipv6_addresses_and_international_names(monkeypatch):
    """Every IPv6 link's host read as "[", so one IPv6 rule (saved as "[")
    took them all; a rule typed as bücher.de never met its link's
    xn--bcher-kva.de (2026-11-12)."""
    rule = {"segments": 3, "ytdlp": False}
    monkeypatch.setattr(utils, "HOST_RULES", {"[": {"segments": 1}, "::1": rule,
                                              "bücher.de": rule, "*.example.com": rule})
    assert utils.host_rule("http://[2001:db8::1]/f") == {}, "the old '[' rule took every IPv6 host"
    assert utils.host_rule("http://[::1]:8080/f") == rule
    assert utils.host_rule("https://xn--bcher-kva.de/f") == rule
    assert utils.host_rule("https://bücher.de/f") == rule
    assert utils.host_rule("https://cdn.example.com/f") == rule


def test_the_dialog_keeps_a_host_as_a_link_names_it():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.dialogs.host_rules import HostRulesDialog
    QApplication.instance() or QApplication([])
    rules = {}
    dlg = HostRulesDialog(None, rules)
    for typed in ("*.Example.com", "[::1]:8080", "bücher.de", "files.example.org?x=1"):
        dlg.host_edit.setText(typed)
        dlg._add()
    assert sorted(rules) == ["::1", "example.com", "files.example.org", "xn--bcher-kva.de"]


def test_cancel_in_settings_undoes_an_edit_to_a_rule():
    """Settings' copy of the rules shared each rule with the saved ones: an
    edit in Per-host rules (its rows write into the rule) was live at once,
    and Cancel kept it (2026-11-12)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui2.dialogs.settings import SettingsDialogV2
    QApplication.instance() or QApplication([])
    saved = {"host_rules": {"a.com": {"segments": 4, "ytdlp": False}}}
    dlg = SettingsDialogV2(None, extras=saved, save_dir=".", max_concurrent=3, segments=8,
                           verify_tls=True, pair_token="T", theme="Dark", accent="purple",
                           sched_en=False, sched_start="00:00", sched_stop="06:00")
    try:
        dlg._host_rules["a.com"]["segments"] = 9         # as the rule's spin box writes
        assert saved["host_rules"]["a.com"]["segments"] == 4, "changed before Save"
    finally:
        dlg.deleteLater()
