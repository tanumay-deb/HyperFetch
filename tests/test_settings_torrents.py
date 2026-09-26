"""Torrent settings live on a Torrents page of their own."""
import inspect
import re

from gui2.dialogs import settings, settings_pages

TORRENT_ROWS = ("Listen Port", "UPnP", "Seed after completing", "Stop seeding at ratio",
                "Stop seeding after", "Upload speed limit", "Preview while downloading",
                "Disk cache", "Pre-allocate disk space")


def test_the_torrent_settings_have_a_page_of_their_own():
    assert "Torrents" in settings._SECTIONS
    page = inspect.getsource(settings_pages.PageBuilderMixin._p_torrents)
    for label in TORRENT_ROWS:
        assert label in page, label


def test_and_are_no_longer_scattered_elsewhere():
    for name in ("_p_network", "_p_advanced"):
        other = inspect.getsource(getattr(settings_pages.PageBuilderMixin, name))
        for label in TORRENT_ROWS:
            assert label not in other, (name, label)


def test_names_icons_and_pages_line_up():
    """The nav list, its icons and the stacked pages are three parallel lists;
    one out of step shows every later page under the wrong name."""
    src = inspect.getsource(settings.SettingsDialogV2.__init__)
    stacked = re.findall(r"self\.stack\.addWidget\(self\._p_(\w+)\(", src)
    icons = re.search(r"icons = \[(.*?)\]", src, re.S).group(1).count('"') // 2
    assert len(stacked) == len(settings._SECTIONS) == icons
    assert stacked.index("torrents") == settings._SECTIONS.index("Torrents")


def test_the_provider_block_fallback_has_a_switch_on_the_network_page():
    network = inspect.getsource(settings_pages.PageBuilderMixin._p_network)
    assert "dns_auto" in network and "Get past provider blocks" in network
