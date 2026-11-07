from types import SimpleNamespace

import pytest

from src.core import address_guard
from src.core.address_guard import (
    AddressBook,
    AddressHits,
    book_from_payload,
    guard_fields,
    hold_for_retired,
    payload_from_environments,
    refusal,
    retired_note,
    scan,
)

PAYLOAD = {
    "roles": [
        {
            "role": "prod-vps",
            "current": ["40.160.102.182"],
            "retired": ["178.156.247.239", "100.112.188.96", "ubuntu-2gb-ash-1"],
        }
    ]
}


def book() -> AddressBook:
    return book_from_payload(PAYLOAD)


def test_scan_retired_ip():
    hits = scan(["deploy to 178.156.247.239 now"], book())
    assert hits.retired == (("178.156.247.239", "prod-vps"),)
    assert hits.any_retired
    assert hits.current == ()


def test_scan_retired_hostname():
    hits = scan(["ssh ubuntu-2gb-ash-1"], book())
    assert hits.retired == (("ubuntu-2gb-ash-1", "prod-vps"),)


def test_scan_current_ip_is_hint_only():
    hits = scan(["host 40.160.102.182"], book())
    assert hits.current == (("40.160.102.182", "prod-vps"),)
    assert not hits.any_retired
    assert "prod-vps" in guard_fields(hits, held=False)["hint"]


def test_provider_word_never_matches():
    assert scan(["the Hetzner box"], book()) == AddressHits((), ())


def test_scan_none_book_and_none_text():
    assert scan(["178.156.247.239"], None) == AddressHits((), ())
    assert scan([None, ""], book()) == AddressHits((), ())


def test_current_wins_over_retired():
    payload = {
        "roles": [
            {"role": "old", "current": [], "retired": ["10.0.0.1"]},
            {"role": "new", "current": ["10.0.0.1"], "retired": []},
        ]
    }
    hits = scan(["10.0.0.1"], book_from_payload(payload))
    assert hits.current == (("10.0.0.1", "new"),)
    assert hits.retired == ()


def test_hold_for_retired_demotes_approved():
    data = {"status": "approved", "reviewed_by": "x", "reviewed_at": "t"}
    hold_for_retired(data, scan(["178.156.247.239"], book()))
    assert data["status"] == "proposed"
    assert "reviewed_by" not in data and "reviewed_at" not in data
    assert data["conflict_kind"] == "retired_address"
    assert data["conflict_note"] == retired_note(scan(["178.156.247.239"], book()))


def test_hold_no_hits_changes_nothing():
    data = {"status": "approved", "reviewed_by": "x"}
    hold_for_retired(data, scan(["40.160.102.182"], book()))
    assert data == {"status": "approved", "reviewed_by": "x"}


def test_hold_keeps_duplicate_kind():
    data = {"status": "proposed", "conflict_kind": "duplicate", "conflict_note": "dup of 3"}
    hold_for_retired(data, scan(["178.156.247.239"], book()))
    assert data["conflict_kind"] == "duplicate"
    assert "dup of 3" in data["conflict_note"]
    assert "retired address of prod-vps" in data["conflict_note"]


def test_hold_replaces_overlap_kind():
    data = {"status": "approved", "conflict_kind": "overlap", "conflict_note": "overlaps 4"}
    hold_for_retired(data, scan(["178.156.247.239"], book()))
    assert data["conflict_kind"] == "retired_address"
    assert "overlaps 4" in data["conflict_note"]
    assert "retired address of prod-vps" in data["conflict_note"]


def test_guard_fields_note_claims_hold_only_when_held():
    hits = scan(["178.156.247.239"], book())
    assert "held" in guard_fields(hits, held=True)["note"]
    unheld = guard_fields(hits, held=False)["note"]
    assert "held" not in unheld
    assert unheld == "names 178.156.247.239, a retired address of prod-vps"


def test_guard_fields_and_refusal():
    hits = scan(["178.156.247.239"], book())
    fields = guard_fields(hits, held=True)
    assert fields["retired_address"] == [{"match": "178.156.247.239", "role": "prod-vps"}]
    assert fields["note"].endswith("the record is held as proposed")
    assert guard_fields(AddressHits((), ()), held=True) == {}
    r = refusal(hits)
    assert r["error"] == "retired_address"
    assert r["matches"] == fields["retired_address"]
    assert "allow_retired=True" in r["hint"]
    assert retired_note(hits) == "names 178.156.247.239, a retired address of prod-vps"


def test_payload_from_environments_shape():
    env = SimpleNamespace(
        role="prod-vps",
        addresses={
            "ipv4": "40.160.102.182",
            "tailnet_node": "ovh-vps1",
            "tailnet_ip": "100.92.138.19",
            "hostnames": ["web.example.com"],
        },
        previous=[
            {
                "addresses": {
                    "ipv4": "178.156.247.239",
                    "tailnet_node": "old-node",
                    "hostnames": ["old.example.com"],
                },
                "provider": "Hetzner",
                "until": "2026-10-23",
            }
        ],
    )
    (row,) = payload_from_environments([env])["roles"]
    assert set(row) == {"role", "current", "retired"}
    assert row["current"] == ["40.160.102.182", "100.92.138.19"]
    assert "web.example.com" not in row["current"] and "ovh-vps1" not in row["current"]
    assert set(row["retired"]) == {"178.156.247.239", "old-node", "old.example.com"}
    assert "Hetzner" not in row["retired"]


def _env(role, addresses, previous):
    return SimpleNamespace(role=role, addresses=addresses, previous=previous)


def test_payload_drops_retired_values_still_live_on_any_row():
    live = {"ipv4": "40.160.102.182", "tailnet_node": "ovh-vps1", "hostnames": ["web-1.example"]}
    prior = {
        "ipv4": "178.156.247.239",
        "tailnet_node": "ovh-vps1",
        "hostnames": ["web-1.example", "dev-box.example"],
    }
    rows = [
        _env("prod-vps", live, [{"addresses": prior, "provider": "x", "until": "2026-10-23"}]),
        _env("dev", {"tailnet_ip": "100.64.0.9", "hostnames": ["dev-box.example"]}, []),
    ]
    prod, dev = payload_from_environments(rows)["roles"]
    assert prod["retired"] == ["178.156.247.239"]
    assert prod["current"] == ["40.160.102.182"]
    assert dev["current"] == ["100.64.0.9"]


def test_payload_retires_prior_ipv6_and_tailnet_ip():
    prior = {"ipv6": "2001:db8::1", "tailnet_ip": "100.112.188.96"}
    row = _env(
        "prod-vps",
        {"ipv6": "2001:db8::2", "tailnet_ip": "100.92.138.19"},
        [{"addresses": prior, "provider": "x", "until": "2026-10-23"}],
    )
    (out,) = payload_from_environments([row])["roles"]
    assert out["current"] == ["2001:db8::2", "100.92.138.19"]
    assert sorted(out["retired"]) == ["100.112.188.96", "2001:db8::1"]


def test_payload_previous_without_addresses_and_none_previous():
    rows = [
        _env("a", {"ipv4": "10.0.0.1"}, [{"provider": "x", "until": "2026-01-01"}]),
        _env("b", {"ipv4": "10.0.0.2"}, None),
    ]
    a, b = payload_from_environments(rows)["roles"]
    assert a["retired"] == [] and b["retired"] == []


def test_scan_is_case_insensitive_and_reports_stored_match():
    mixed = book_from_payload(
        {
            "roles": [
                {
                    "role": "prod-vps",
                    "current": ["2001:db8::a"],
                    "retired": ["2001:db8::bad", "Old-Host-1.example"],
                }
            ]
        }
    )
    hits = scan(["ssh OLD-HOST-1.EXAMPLE via 2001:DB8::BAD and 2001:DB8::A"], mixed)
    assert hits.retired == (
        ("2001:db8::bad", "prod-vps"),
        ("Old-Host-1.example", "prod-vps"),
    )
    assert hits.current == (("2001:db8::a", "prod-vps"),)


def test_hint_only_keeps_just_the_hint():
    hits = scan(["178.156.247.239 and 40.160.102.182"], book())
    assert address_guard.hint_only(hits) == {"hint": guard_fields(hits, held=False)["hint"]}
    assert address_guard.hint_only(scan(["178.156.247.239"], book())) == {}


def test_current_wins_ignores_case_in_payload():
    row = _env(
        "prod-vps",
        {"ipv6": "2001:DB8::A"},
        [{"addresses": {"ipv6": "2001:db8::a"}, "provider": "x", "until": "2026-10-23"}],
    )
    (out,) = payload_from_environments([row])["roles"]
    assert out["retired"] == []


def test_current_wins_ignores_case_in_book():
    payload = {
        "roles": [{"role": "prod-vps", "current": ["2001:DB8::A"], "retired": ["2001:db8::a"]}]
    }
    mixed = book_from_payload(payload)
    assert mixed.retired == {}
    assert not scan(["on 2001:db8::a"], mixed).any_retired


BOUNDARY_BOOK = book_from_payload(
    {
        "roles": [
            {
                "role": "prod-vps",
                "current": [],
                "retired": [
                    "100.92.138.1",
                    "vps1.alobar.net",
                    "178.156.247.239",
                    "ubuntu-2gb-ash-1",
                    "2001:db8::1",
                ],
            }
        ]
    }
)


@pytest.mark.parametrize(
    "text",
    [
        "tailnet 100.92.138.19",
        "ssh ovh-vps1.alobar.net",
        "ssh ubuntu-2gb-ash-10",
        "see 178.156.247.2390",
        "v6 2001:db8::1:5",
        "host 178.156.247.239.example.com",
    ],
)
def test_scan_needs_a_boundary(text):
    assert scan([text], BOUNDARY_BOOK).retired == ()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("see 178.156.247.239.", "178.156.247.239"),
        ("http://178.156.247.239:8000/x", "178.156.247.239"),
        ("(178.156.247.239)", "178.156.247.239"),
        ("ssh ubuntu-2gb-ash-1 now", "ubuntu-2gb-ash-1"),
        ("SSH UBUNTU-2GB-ASH-1", "ubuntu-2gb-ash-1"),
        ("https://vps1.alobar.net:443.", "vps1.alobar.net"),
        ("root@100.92.138.1:22", "100.92.138.1"),
        ("[2001:db8::1]:8000", "2001:db8::1"),
    ],
)
def test_scan_matches_at_a_boundary(text, match):
    assert scan([text], BOUNDARY_BOOK).retired == ((match, "prod-vps"),)
