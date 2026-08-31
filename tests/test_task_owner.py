"""Ownership on downloads, and the folder it decides.

The riskiest part of the whole multi-user change: `DownloadTask` and
`downloads.json` are shared with the desktop app and the extension, so the
tests here care as much about downloads that predate accounts as about the new
field itself.
"""
import json
import os

import pytest

import task as T
import utils


# ---- the field -------------------------------------------------------------
def test_a_new_download_belongs_to_nobody():
    """Empty is the safe default: unattributable means admin, never "whoever
    happens to be asking"."""
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    assert t.owner == ""


def test_the_owner_survives_a_save_and_load():
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    t.owner = "tanumay"
    assert T.DownloadTask.from_dict(t.to_dict()).owner == "tanumay"


def test_a_download_written_before_accounts_existed_still_loads():
    """The migration that matters. downloads.json on a real machine is full of
    these, and they must load rather than raise or quietly disappear."""
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    legacy = t.to_dict()
    del legacy["owner"]

    restored = T.DownloadTask.from_dict(legacy)
    assert restored.owner == "", "a legacy download must fall to admin"
    assert restored.url == "https://e.test/x"
    assert restored.filename == "x"


def test_a_null_owner_is_treated_as_admin():
    """Hand-edited or half-written state should not produce an owner of None
    that later gets joined into a path."""
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x")
    d = t.to_dict()
    d["owner"] = None
    assert T.DownloadTask.from_dict(d).owner == ""


def test_the_owner_is_not_stripped_as_sensitive():
    """strip_sensitive removes cookies and auth headers on the way to disk. It
    must not take the owner with it, or every download becomes admin's after a
    restart."""
    t = T.DownloadTask("https://e.test/x", "C:/dl/x", filename="x",
                       headers={"Cookie": "secret=1", "Referer": "https://e.test/"})
    t.owner = "tanumay"
    d = t.to_dict()
    assert d["owner"] == "tanumay"
    assert "Cookie" not in json.dumps(d), "a cookie reached the disk"


def test_a_whole_list_round_trips(tmp_path):
    """What the app actually does at startup: write every task, read them back."""
    tasks = []
    for i, owner in enumerate(["", "tanumay", "someone", ""]):
        t = T.DownloadTask(f"https://e.test/{i}", f"C:/dl/{i}", filename=str(i))
        t.owner = owner
        tasks.append(t)

    p = tmp_path / "downloads.json"
    p.write_text(json.dumps([t.to_dict() for t in tasks]), encoding="utf-8")
    back = [T.DownloadTask.from_dict(d)
            for d in json.loads(p.read_text(encoding="utf-8"))]
    assert [t.owner for t in back] == ["", "tanumay", "someone", ""]


# ---- the folder ------------------------------------------------------------
def test_no_owner_means_the_normal_download_folder(tmp_path):
    base = str(tmp_path)
    assert utils.user_download_dir(base, "") == base
    assert utils.user_download_dir(base, None) == base


def test_a_user_gets_their_own_folder(tmp_path):
    base = str(tmp_path)
    d = utils.user_download_dir(base, "tanumay")
    assert d == os.path.join(base, "tanumay")
    assert os.path.isdir(d), "the folder was not created"


def test_two_users_do_not_share_a_folder(tmp_path):
    base = str(tmp_path)
    assert utils.user_download_dir(base, "aaa") != utils.user_download_dir(base, "bbb")


@pytest.mark.parametrize("bad", [
    "../escape", "..", ".", "a/b", "a\\b", "C:/Windows", "con", "nul",
    "  ", "\\\\server\\share",
])
def test_a_username_that_would_escape_is_refused(tmp_path, bad):
    """Refused rather than rewritten. Quietly relocating one account's files
    into a neighbouring folder is the failure worth preventing, and it is worse
    than an error."""
    if not bad.strip():
        # blank is the admin case, handled above, not an escape
        assert utils.user_download_dir(str(tmp_path), bad) == str(tmp_path)
        return
    with pytest.raises(ValueError):
        utils.user_download_dir(str(tmp_path), bad)


def test_nothing_is_created_when_the_name_is_refused(tmp_path):
    before = set(os.listdir(str(tmp_path)))
    with pytest.raises(ValueError):
        utils.user_download_dir(str(tmp_path), "../escape")
    assert set(os.listdir(str(tmp_path))) == before


def test_the_folder_is_reused_not_recreated(tmp_path):
    base = str(tmp_path)
    d = utils.user_download_dir(base, "tanumay")
    open(os.path.join(d, "already-here.bin"), "wb").write(b"x")
    again = utils.user_download_dir(base, "tanumay")
    assert again == d
    assert os.path.isfile(os.path.join(again, "already-here.bin")), \
        "an existing user folder was clobbered"
