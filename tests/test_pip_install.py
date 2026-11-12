"""A pip install carries every module of the app (2026-11-12).

pyproject.toml lists the flat layout's modules by hand, and eight were
missing - pairing, plain_errors, scrape and swarm among them, which the
window imports as it starts: a pip-installed HyperFetch could not start.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_top_level_module_is_listed():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    listed = set(re.findall(r'"(\w+)"', text.split("py-modules = [", 1)[1].split("]", 1)[0]))
    modules = {n[:-3] for n in os.listdir(ROOT) if n.endswith(".py")}
    assert modules - listed == set(), "not installed by pip"
    packages = re.search(r"^packages = \[(.*?)\]", text, re.M).group(1)
    for pkg in ("gui", "gui2", "gui2.dialogs"):
        assert '"%s"' % pkg in packages, pkg


def test_a_pip_install_ships_and_finds_the_web_page_and_the_licences(tmp_path, monkeypatch):
    import api_server
    import utils
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    for f in api_server.ALLOWED_UI_FILES:
        assert '"web/%s"' % f in text, f
    assert '"share/hyperfetch/licenses" = ["licenses/*.txt"]' in text
    share = tmp_path / "share" / "hyperfetch"
    for folder in ("web", "assets", "licenses"):
        (share / folder).mkdir(parents=True)
    site_packages = str(tmp_path / "site-packages")
    monkeypatch.setattr(utils.sys, "prefix", str(tmp_path))
    assert api_server.web_dir(root=site_packages) == str(share / "web")
    assert utils.resource_path("licenses", root=site_packages) == str(share / "licenses")
    assert utils.resource_path("assets", root=site_packages) == str(share / "assets")
    from gui import theme
    assert theme.resource_path is utils.resource_path


