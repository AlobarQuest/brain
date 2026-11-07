from types import SimpleNamespace

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
    assert "prod-vps" in guard_fields(hits)["hint"]


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


def test_guard_fields_and_refusal():
    hits = scan(["178.156.247.239"], book())
    fields = guard_fields(hits)
    assert fields["retired_address"] == [{"match": "178.156.247.239", "role": "prod-vps"}]
    assert "note" in fields
    assert guard_fields(AddressHits((), ())) == {}
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
