"""Retired-address guard: find a retired host's address in text bound for a brain write.

Matching is pure. `fetch_address_book` is the only I/O, for the brains that do not own the
registry. A caller fetches the environment payload
(`payload_from_environments` builds it from `Environment` rows), turns it into an
`AddressBook`, scans the texts it is about to store, and either holds the new record
as proposed (`hold_for_retired`), refuses (`refusal`), or merges `guard_fields` into
its response.

Matching is by fixed substring, ignoring case. Provider words ("Hetzner") are never match strings.
A string that is current for any role is never treated as retired.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.config import get_settings
from src.core.governance import CONFLICT_DUPLICATE, CONFLICT_RETIRED_ADDRESS, STATUS_PROPOSED

logger = logging.getLogger(__name__)

CACHE_TTL_S = 300
FAILURE_TTL_S = 30
_FETCH_TIMEOUT_S = 5.0
_clock = time.monotonic  # replaced in tests
_cache: tuple[float, AddressBook | None] | None = None  # (expires_at, book)

_CURRENT_KEYS = ("ipv4", "ipv6", "tailnet_ip")
_RETIRED_KEYS = (*_CURRENT_KEYS, "tailnet_node")
_NOTE_SEPARATOR = " | "


@dataclass(frozen=True)
class AddressBook:
    """Match string -> role, for current and retired addresses."""

    current: dict[str, str]
    retired: dict[str, str]


@dataclass(frozen=True)
class AddressHits:
    """`(match, role)` pairs found in scanned text."""

    retired: tuple[tuple[str, str], ...]
    current: tuple[tuple[str, str], ...]

    @property
    def any_retired(self) -> bool:
        return bool(self.retired)


def _values(addresses: dict[str, Any], keys: tuple[str, ...], with_hostnames: bool) -> list[str]:
    found = [addresses[k] for k in keys if addresses.get(k)]
    if with_hostnames:
        found.extend(h for h in addresses.get("hostnames") or [] if h)
    return found


def payload_from_environments(rows: Iterable[Any]) -> dict:
    """The route payload for `Environment`-shaped rows (`.role`, `.addresses`, `.previous`).

    A previous value still present in any row's current addresses (an unchanged tailnet node or
    hostname carried into `previous` by rotation) is live, so it is left out of `retired`."""
    rows = list(rows)
    live = {v.lower() for row in rows for v in _values(row.addresses or {}, _RETIRED_KEYS, True)}
    roles = []
    for row in rows:
        retired: list[str] = []
        for prior in row.previous or []:
            found = _values(prior.get("addresses") or {}, _RETIRED_KEYS, True)
            retired.extend(v for v in found if v.lower() not in live)
        roles.append(
            {
                "role": row.role,
                "current": _values(row.addresses or {}, _CURRENT_KEYS, False),
                "retired": retired,
            }
        )
    return {"roles": roles}


def book_from_payload(payload: dict) -> AddressBook:
    """Build the lookup book. A string current for any role, in any case, is dropped from the
    retired side."""
    current: dict[str, str] = {}
    retired: dict[str, str] = {}
    for entry in payload.get("roles", []):
        role = entry["role"]
        for match in entry.get("current", []):
            if match:
                current[match] = role
        for match in entry.get("retired", []):
            if match:
                retired.setdefault(match, role)
    live = {match.lower() for match in current}
    retired = {m: role for m, role in retired.items() if m.lower() not in live}
    return AddressBook(current=current, retired=retired)


def _found(texts: list[str], table: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Pairs whose match string occurs in a text; `texts` are already lowercased."""
    return tuple((m, role) for m, role in table.items() if any(m.lower() in t for t in texts))


def scan(texts: Iterable[str | None], book: AddressBook | None) -> AddressHits:
    if book is None:
        return AddressHits((), ())
    kept = [t.lower() for t in texts if t]
    return AddressHits(retired=_found(kept, book.retired), current=_found(kept, book.current))


def retired_note(hits: AddressHits) -> str:
    return "; ".join(f"names {m}, a retired address of {role}" for m, role in hits.retired)


def hold_for_retired(data: dict, hits: AddressHits) -> None:
    """Hold a new record's insert dict as proposed. Run after `finalize_governance`."""
    if not hits.any_retired:
        return
    data["status"] = STATUS_PROPOSED
    data.pop("reviewed_by", None)
    data.pop("reviewed_at", None)
    if data.get("conflict_kind") != CONFLICT_DUPLICATE:
        data["conflict_kind"] = CONFLICT_RETIRED_ADDRESS
    existing = data.get("conflict_note")
    data["conflict_note"] = (
        f"{existing}{_NOTE_SEPARATOR}{retired_note(hits)}" if existing else retired_note(hits)
    )


def _matches(pairs: tuple[tuple[str, str], ...]) -> list[dict]:
    return [{"match": m, "role": role} for m, role in pairs]


def guard_fields(hits: AddressHits, *, held: bool) -> dict:
    """Fields to merge into a tool response; empty when nothing matched.

    `held` is True only when the caller held the new record as proposed
    (`hold_for_retired`); the note claims a hold only then."""
    fields: dict[str, Any] = {}
    if hits.any_retired:
        fields["retired_address"] = _matches(hits.retired)
        fields["note"] = (
            f"{retired_note(hits)}; the record is held as proposed" if held else retired_note(hits)
        )
    if hits.current:
        match, role = hits.current[0]
        fields["hint"] = (
            f"{match} is the current address of {role}; "
            f"say '{role}' so the record survives a host move"
        )
    return fields


def hint_only(hits: AddressHits) -> dict:
    """Just the current-address hint, for a write that already refused or allowed a retired hit."""
    return {k: v for k, v in guard_fields(hits, held=False).items() if k == "hint"}


def refusal(hits: AddressHits) -> dict:
    return {
        "error": "retired_address",
        "matches": _matches(hits.retired),
        "hint": "pass allow_retired=True for dated history",
    }


def reset_address_cache() -> None:
    global _cache
    _cache = None


def _validate_payload(payload: Any) -> None:
    """Raise ValueError unless payload is `{"roles": [{"role": str, "current"?: [str], ...}]}`."""
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
        raise ValueError("payload has no 'roles' list")
    for entry in payload["roles"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("role"), str):
            raise ValueError("roles entry is not an object with a string 'role'")
        for key in ("current", "retired"):
            values = entry.get(key, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"roles entry '{key}' is not a list of strings")


async def _fetch_book() -> AddressBook:
    settings = get_settings()
    url = f"{settings.infra_brain_url.rstrip('/')}/api/environments/addresses"
    headers = {"User-Agent": f"brain-address-guard/1 ({settings.brain_type}-brain)"}
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    payload = response.json()
    _validate_payload(payload)
    return book_from_payload(payload)


async def fetch_address_book() -> AddressBook | None:
    """Infra Brain's address book, cached; None when unavailable (the write goes unguarded)."""
    global _cache
    if _cache is not None and _clock() < _cache[0]:
        return _cache[1]
    try:
        # httpx's timeout applies per phase; this caps the whole request.
        async with asyncio.timeout(_FETCH_TIMEOUT_S):
            book: AddressBook | None = await _fetch_book()
        ttl = CACHE_TTL_S
    except TimeoutError:
        logger.warning("address guard skipped: no answer within %ss", _FETCH_TIMEOUT_S)
        book, ttl = None, FAILURE_TTL_S
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, TypeError) as exc:
        logger.warning("address guard skipped: %s", exc)
        book, ttl = None, FAILURE_TTL_S
    _cache = (_clock() + ttl, book)
    return book
