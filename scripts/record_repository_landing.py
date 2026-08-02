"""
Idempotent recorder for the default-branch landing of repositories that have NO
application, from scripts/repository_landing.json.

Every repository that feeds a registered app was backfilled by migration 0006.
This covers the rest — repositories the factory can target and nothing deploys
from — which no app record could have supplied. Replaces WS-P2.29's
scripts/backfill_default_branch_landing.py, whose per-app determinations now
live on the repository they were always a property of.

Writes through the brain's OWN MCP tool over HTTP — record_default_branch_landing
— not by connecting to the database. Three reasons:

  * it needs no database credential, only the brain access key it already needs
    to read anything at all;
  * it goes through the tool's validation and the DB CHECKs, so it cannot write
    a shape a normal caller could not; and
  * `determined_at` is stamped server-side, so no determination can be dated by
    the thing asserting it.

The brain's HTTP transport is stateless (json_response=True, stateless_http=True),
so a single tools/call POST works with no initialize handshake.

Usage
-----
  export APPBRAIN_URL=https://app-brain.devonwatkins.com     # default
  export APPBRAIN_ACCESS_KEY=...        # the write-capable key; never echoed

  python scripts/record_repository_landing.py --dry-run   # validate only
  python scripts/record_repository_landing.py             # apply

Idempotent: re-running rewrites the same values (refreshing determined_at) and
upserts on the canonical slug, so it cannot duplicate a repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brains.app.models import LANDING_VALUES  # noqa: E402
from src.brains.app.repositories.apps import canonical_repo_slug  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repository_landing.json")


def load_determination(path: str = DATA) -> list[dict]:
    """Read and validate the determination file. Raises on a bad vocabulary, a
    reference the fold could not match, or a row missing its evidence — writing
    an unattributed claim is the thing the provenance CHECK exists to refuse, and
    failing here is cheaper than failing per-row against production."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc["repositories"]
    seen = set()
    for row in rows:
        repo = row.get("github_repo")
        slug = canonical_repo_slug(repo)
        if not slug:
            raise ValueError(f"github_repo must be 'owner/repo': {repo!r}")
        if slug in seen:
            raise ValueError(f"duplicate repository: {slug}")
        seen.add(slug)
        if row.get("landing") not in LANDING_VALUES:
            raise ValueError(f"{repo}: landing must be one of {LANDING_VALUES}")
        if not (row.get("evidence") or "").strip():
            raise ValueError(f"{repo}: evidence is required")
    return rows


def call_tool(base_url: str, key: str, name: str, arguments: dict) -> dict:
    """POST one MCP tools/call and return the tool's parsed result."""
    import httpx

    resp = httpx.post(
        f"{base_url.rstrip('/')}/mcp/",
        headers={
            "x-brain-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{name}: {body['error']}")
    return json.loads(body["result"]["content"][0]["text"])


def main() -> None:
    p = argparse.ArgumentParser(description="Record app-less repositories' landing")
    p.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = p.parse_args()

    base_url = os.environ.get("APPBRAIN_URL", "https://app-brain.devonwatkins.com")
    key = os.environ.get("APPBRAIN_ACCESS_KEY")
    if not key and not args.dry_run:
        raise SystemExit("APPBRAIN_ACCESS_KEY env var is required (use --dry-run to validate only)")

    rows = load_determination()
    print(f"{'DRY RUN — ' if args.dry_run else ''}{len(rows)} repository(ies)\n")

    for row in rows:
        print(f"  [{row['landing']:<9}] {row['github_repo']}")
        if args.dry_run:
            continue
        result = call_tool(
            base_url,
            key,
            "record_default_branch_landing",
            {
                "github_repo": row["github_repo"],
                "landing": row["landing"],
                "evidence": row["evidence"],
            },
        )
        if result.get("error"):
            raise SystemExit(f"{row['github_repo']}: {result['error']}")

    if args.dry_run:
        print("\nDry run — no writes. Determination file is valid.")
    else:
        print(f"\nRecorded {len(rows)} repository determination(s).")


if __name__ == "__main__":
    main()
