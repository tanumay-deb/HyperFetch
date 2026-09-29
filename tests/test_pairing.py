"""Pairing a Firefox install by asking the person at this computer.

Chrome and Edge pair themselves: /pair serves the token to their store
listings' fixed ids. A Firefox install's address is a random moz-extension://
UUID, so /pair can never recognise it. Instead it asks (POST /pair/request,
with a code it also shows), the window asks the person, and an address they
allow gets the token from then on.

Anything can ask - every Firefox add-on has a moz-extension address, and a
local program can claim one - so these pin what makes allowing the gate.
"""
import json

import pytest

import pairing
from api_server import create_app
from test_api_server import _FakeQueue

FF = "moz-extension://2f1d6c3e-8a4b-4c1f-9d2e-7b6a5c4d3e21"
OTHER = "moz-extension://9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def requests_(tmp_path, clock):
    return pairing.PairRequests(str(tmp_path / "paired_browsers.json"), clock=clock)


# ---- the rules ---------------------------------------------------------------

def test_a_first_request_waits_and_is_asked_about_once(requests_):
    assert requests_.request(FF, "4821") == "pending"
    assert requests_.next_prompt() == (FF, "4821")
    assert requests_.next_prompt() is None
    # the extension keeps asking while its page is open: still one question
    assert requests_.request(FF, "4821") == "pending"
    assert requests_.next_prompt() is None


def test_an_allowed_install_is_approved_and_remembered(tmp_path, requests_, clock):
    requests_.request(FF, "4821")
    requests_.decide(FF, True)
    assert requests_.request(FF, "4821") == "approved"
    again = pairing.PairRequests(str(tmp_path / "paired_browsers.json"), clock=clock)
    assert again.request(FF, "0000") == "approved", "the approval did not survive a restart"
    assert again.count() == 1


def test_a_refused_install_is_not_asked_about_again_for_a_while(requests_, clock):
    requests_.request(FF, "4821")
    requests_.next_prompt()
    requests_.decide(FF, False)
    assert requests_.request(FF, "4821") == "denied"
    assert requests_.next_prompt() is None, "a refusal must not turn into a nagging loop"
    clock.t += pairing.DENY_COOLDOWN + 1
    assert requests_.request(FF, "4821") == "pending"
    assert requests_.next_prompt() == (FF, "4821")


@pytest.mark.parametrize("origin,code", [
    ("chrome-extension://abcdefghijklmnopabcdefghijklmnop", "4821"),   # Chrome pairs by store id
    ("https://evil.example", "4821"),
    ("moz-extension://not-a-uuid", "4821"),
    ("", "4821"),
    (FF, "12"),
    (FF, "abcd"),
    (FF, ""),
])
def test_only_a_firefox_address_with_a_four_digit_code_may_ask(requests_, origin, code):
    assert requests_.request(origin, code) == "invalid"
    assert requests_.next_prompt() is None


def test_a_flood_of_addresses_is_capped(requests_):
    origins = ["moz-extension://00000000-0000-4000-8000-%012d" % i
               for i in range(pairing.MAX_PENDING)]
    for o in origins:
        assert requests_.request(o, "1111") == "pending"
    assert requests_.request(OTHER, "2222") == "busy"


def test_an_abandoned_request_frees_its_place(requests_, clock):
    origins = ["moz-extension://00000000-0000-4000-8000-%012d" % i
               for i in range(pairing.MAX_PENDING)]
    for o in origins:
        requests_.request(o, "1111")
    clock.t += pairing.PENDING_TTL + 1          # nobody kept asking
    assert requests_.request(OTHER, "2222") == "pending"


def test_an_answer_to_an_expired_request_still_counts(requests_, clock):
    requests_.request(FF, "4821")
    requests_.next_prompt()
    clock.t += pairing.PENDING_TTL + 1          # the popup was closed meanwhile
    requests_.decide(FF, True)                  # the person allowed it anyway
    assert requests_.request(FF, "4821") == "approved"


def test_forgetting_removes_every_paired_install(tmp_path, requests_, clock):
    requests_.decide(FF, True)
    requests_.decide(OTHER, True)
    assert requests_.forget_all() == 2
    again = pairing.PairRequests(str(tmp_path / "paired_browsers.json"), clock=clock)
    assert again.count() == 0
    assert again.request(FF, "4821") == "pending"


def test_a_damaged_file_starts_empty(tmp_path, clock):
    p = tmp_path / "paired_browsers.json"
    p.write_text("{not json", encoding="utf-8")
    r = pairing.PairRequests(str(p), clock=clock)
    assert r.count() == 0
    p.write_text(json.dumps({"origins": [FF, "https://evil.example", 5]}), encoding="utf-8")
    r = pairing.PairRequests(str(p), clock=clock)
    assert r.count() == 1, "only well-formed Firefox addresses are read back"


# ---- over HTTP ---------------------------------------------------------------

def _client(requests_=None):
    app = create_app(_FakeQueue(), ".", pending=None, token="SECRET", pair_requests=requests_)
    return app.test_client()


def test_firefox_gets_the_token_once_the_person_allows(requests_):
    c = _client(requests_)
    pre = c.open("/pair/request", method="OPTIONS", headers={"Origin": FF})
    assert pre.status_code in (200, 204)
    assert pre.headers.get("Access-Control-Allow-Origin") == FF
    assert "content-type" in pre.headers.get("Access-Control-Allow-Headers", "").lower()

    r = c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF})
    assert r.status_code == 202 and r.get_json() == {"status": "pending"}
    assert r.headers.get("Access-Control-Allow-Origin") == FF
    assert requests_.next_prompt() == (FF, "4821")

    requests_.decide(FF, True)
    r = c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF})
    assert r.status_code == 200
    assert r.get_json() == {"status": "approved", "token": "SECRET"}


def test_a_refused_install_gets_no_token(requests_):
    c = _client(requests_)
    c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF})
    requests_.decide(FF, False)
    r = c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF})
    assert r.status_code == 403 and r.get_json() == {"status": "denied"}
    assert "SECRET" not in r.get_data(as_text=True)


@pytest.mark.parametrize("origin", [
    "chrome-extension://abcdefghijklmnopabcdefghijklmnop", "https://evil.example", None])
def test_only_firefox_may_ask(requests_, origin):
    c = _client(requests_)
    headers = {"Origin": origin} if origin else {}
    r = c.post("/pair/request", json={"code": "4821"}, headers=headers)
    assert r.status_code == 403
    assert "SECRET" not in r.get_data(as_text=True)
    assert requests_.next_prompt() is None


def test_nothing_from_the_network_may_ask(requests_):
    c = _client(requests_)
    r = c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF},
               environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 403
    assert requests_.next_prompt() is None


def test_with_no_window_to_ask_the_extension_is_told_so():
    """The headless server has nobody to ask. The extension reads that and
    offers the paste box instead of waiting for an answer that never comes."""
    c = _client(None)
    r = c.post("/pair/request", json={"code": "4821"}, headers={"Origin": FF})
    assert r.status_code == 404 and r.get_json() == {"status": "unavailable"}
    assert r.headers.get("Access-Control-Allow-Origin") == FF


def test_pair_still_serves_only_the_store_listings(requests_):
    """Approval goes through /pair/request alone; /pair keeps its fixed list."""
    requests_.decide(FF, True)
    r = _client(requests_).get("/pair", headers={"Origin": FF})
    assert r.status_code == 403
