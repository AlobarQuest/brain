"""
One-time, idempotent backfill of apps.default_branch_landing (migration 0005)
from the determination recorded in scripts/default_branch_landing.json.

Writes through the brain's OWN MCP tools over HTTP — record_default_branch_landing
and update_app — not by connecting to the database. Three reasons:

  * it needs no database credential, only the brain access key it already needs
    to read anything at all;
  * it goes through the tool's validation and the DB CHECKs, so the backfill
    cannot write a shape a normal caller could not; and
  * `determined_at` is stamped server-side, so no determination can be dated by
    the thing asserting it.

The brain's HTTP transport is stateless (json_response=True, stateless_http=True),
so a single tools/call POST works with no initialize handshake.

Usage
-----
  export APPBRAIN_URL=https://app-brain.devonwatkins.com     # default
  export APPBRAIN_ACCESS_KEY=...        # the write-capable key; never echoed

  python scripts/backfill_default_branch_landing.py --dry-run   # report only
  python scripts/backfill_default_branch_landing.py             # apply
  python scripts/backfill_default_branch_landing.py --apps orchestrator,change-manager

Idempotent: re-running rewrites the same values (refreshing determined_at) and
never inserts a row, so it cannot duplicate. github_repo is GAP-FILL ONLY — it is
written solely where the record currently holds null, matching
sync_deployment_from_coolify.py's policy of never case-churning an existing value.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brains.app.models import LANDING_VALUES  # noqa: E402
from src.brains.app.repositories.apps import canonical_repo_slug  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_branch_landing.json")


def load_determination(path: str = DATA) -> list[dict]:
    """Read and validate the determination file. Raises on a bad vocabulary or a
    row missing its evidence — a backfill that writes an unattributed claim is
    the thing the provenance CHECK exists to refuse, and failing here is cheaper
    than failing per-row against production."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    apps = doc["apps"]
    seen = set()
    for row in apps:
        slug = row.get("slug")
        if not slug or slug in seen:
            raise ValueError(f"missing or duplicate slug: {slug!r}")
        seen.add(slug)
        if row.get("landing") not in LANDING_VALUES:
            raise ValueError(f"{slug}: landing must be one of {LANDING_VALUES}")
        if not (row.get("evidence") or "").strip():
            raise ValueError(f"{slug}: evidence is required")
    return apps


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
    p = argparse.ArgumentParser(description="Backfill app-brain default_branch_landing")
    p.add_argument("--dry-run", action="store_true", help="report only, no writes")
    p.add_argument("--apps", help="comma-separated slugs to restrict to")
    args = p.parse_args()
    only = {s.strip() for s in args.apps.split(",")} if args.apps else None

    base_url = os.environ.get("APPBRAIN_URL", "https://app-brain.devonwatkins.com")
    key = os.environ.get("APPBRAIN_ACCESS_KEY")
    if not key and not args.dry_run:
        raise SystemExit("APPBRAIN_ACCESS_KEY env var is required (use --dry-run to validate only)")

    rows = load_determination()
    rows = [r for r in rows if not only or r["slug"] in only]
    print(
        f"{'DRY RUN — ' if args.dry_run else ''}{len(rows)} app(s) from {os.path.basename(DATA)}\n"
    )

    wrote = filled = 0
    for row in rows:
        slug, landing, repo = row["slug"], row["landing"], row.get("github_repo")
        note = f"  +github_repo={repo}" if repo else ""
        print(f"  [{landing:<9}] {slug}{note}")
        if args.dry_run:
            continue
        # github_repo first: without it the app is unreachable by the very key
        # the landing fact is served on, so a half-applied run should leave the
        # record findable rather than merely answered.
        if repo:
            current = call_tool(base_url, key, "get_app", {"slug": slug})
            if current.get("error"):
                raise SystemExit(f"{slug}: {current['error']}")
            if not current.get("github_repo"):
                call_tool(base_url, key, "update_app", {"slug": slug, "github_repo": repo})
                filled += 1
            elif canonical_repo_slug(current["github_repo"]) != canonical_repo_slug(repo):
                # FATAL, not a warning. Recording the fact anyway would store a
                # determination against an app keyed on a DIFFERENT repository
                # than the one it was determined from — served on a key nobody
                # will ask, while the key they do ask returns no_app_record. One
                # indented warning in 25 lines of output is not a control.
                raise SystemExit(
                    f"{slug}: determination was made for {repo!r} but App Brain holds "
                    f"{current['github_repo']!r}. Resolve the disagreement before recording."
                )
        result = call_tool(
            base_url,
            key,
            "record_default_branch_landing",
            {"slug": slug, "landing": landing, "evidence": row["evidence"]},
        )
        if result.get("error"):
            raise SystemExit(f"{slug}: {result['error']}")
        wrote += 1

    if args.dry_run:
        print("\nDry run — no writes. Determination file is valid.")
    else:
        print(f"\nRecorded {wrote} landing value(s); filled {filled} null github_repo(s).")


if __name__ == "__main__":
    main()
