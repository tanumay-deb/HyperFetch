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
