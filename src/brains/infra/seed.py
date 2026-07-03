"""
Seed Infra Brain with initial data from seed/data.json.

Usage:
    python -m src.brains.infra.seed               # skip existing records (default)
    python -m src.brains.infra.seed --skip-existing
    python -m src.brains.infra.seed --force       # overwrite all records
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.core.db import get_session_factory
from src.brains.infra.models import Combo
from src.brains.infra.repositories.combos import ComboRepository
from src.brains.infra.repositories.lessons import LessonRepository
from src.brains.infra.repositories.rules import RuleRepository
from src.brains.infra.repositories.versions import VersionRepository
from src.core.governance import AUTHORITY_INFORMATIONAL, STATUS_APPROVED


def _seed_governance() -> dict:
    """Governance stamp for seeded rules/combos/lessons (curated data — lands
    approved/informational, not the server-default proposed, so it's visible
    on a fresh DB via the default read path)."""
    return {
        "status": STATUS_APPROVED,
        "authority": AUTHORITY_INFORMATIONAL,
        "proposed_by": "seed",
        "reviewed_by": "seed",
        "reviewed_at": datetime.now(timezone.utc),
    }


COOLIFY_CHECKS = [
    {
        "severity": "WARN",
        "category": "coolify",
        "rule": "Health checks must be enabled on all running Coolify applications.",
        "reason": "Coolify relies on health checks to route traffic and detect unhealthy containers. Disabled health checks prevent automatic recovery.",
        "source_app": None,
        "check": {
            "schema_version": 1,
            "resource": "coolify_application",
            "assert": {"field": "health_check_enabled", "op": "eq", "value": True},
            "when": {"field": "status", "op": "contains", "value": "running"},
            "remediation_key": "coolify.enable_healthcheck",
            "kind": "remediation",
        },
    },
    {
        "severity": "WARN",
        "category": "coolify",
        "rule": "All Coolify application FQDNs must use HTTPS (not HTTP).",
        "reason": "HTTP-only endpoints expose traffic in plaintext. All production apps must enforce HTTPS.",
        "source_app": None,
        "check": {
            "schema_version": 1,
            "resource": "coolify_application",
            "assert": {"field": "fqdn", "op": "not_starts_with", "value": "http://"},
            "when": {"field": "fqdn", "op": "non_empty"},
            "remediation_key": "coolify.force_https",
            "kind": "remediation",
        },
    },
    {
        "severity": "WARN",
        "category": "coolify",
        "rule": "All running Coolify databases must have backup configurations defined.",
        "reason": "Databases without backup configs have no recovery path. Nightly backups are required per infrastructure standards.",
        "source_app": None,
        "check": {
            "schema_version": 1,
            "resource": "coolify_database",
            "assert": {"field": "backup_configs", "op": "non_empty"},
            "when": {"field": "status", "op": "contains", "value": "running"},
            "kind": "question",
        },
    },
]


async def seed(skip_existing: bool = True) -> None:
    data_path = Path(__file__).parent / "seed" / "data.json"
    data = json.loads(data_path.read_text())

    async with get_session_factory()() as session:
        versions_repo = VersionRepository(session)
        rules_repo = RuleRepository(session)
        combos_repo = ComboRepository(session)
        lessons_repo = LessonRepository(session)

        # Versions — upsert (always update if --force, skip if --skip-existing)
        versions_loaded = 0
        for v in data.get("versions", []):
            existing = await versions_repo.get_by_package(v["package"])
            if existing and skip_existing:
                continue
            await versions_repo.upsert(v)
            versions_loaded += 1

        # Rules — race-safe upsert via ON CONFLICT DO NOTHING on unique rule text.
        # Seed data is curated: stamped approved/informational so it's visible on a
        # fresh DB via the default read path (server default would otherwise land
        # every seeded rule at status='proposed', invisible to read tools).
        rules_loaded = 0
        for r in data.get("rules", []):
            await rules_repo.add_if_not_exists({**r, **_seed_governance()})
            rules_loaded += 1

        # Coolify structured checks — idempotent via ON CONFLICT DO NOTHING
        coolify_loaded = 0
        for r in COOLIFY_CHECKS:
            await rules_repo.add_if_not_exists({**r, **_seed_governance()})
            coolify_loaded += 1

        # Combos — upsert by name
        combos_loaded = 0
        for c in data.get("combos", []):
            existing = await combos_repo.get_by_name(c["name"])
            if existing and skip_existing:
                continue
            if existing:
                for key, value in c.items():
                    if key != "name":
                        setattr(existing, key, value)
            else:
                session.add(Combo(**{**c, **_seed_governance()}))
            combos_loaded += 1

        # Lessons — race-safe upsert via ON CONFLICT DO NOTHING on unique title
        lessons_loaded = 0
        for l in data.get("lessons", []):
            await lessons_repo.add_if_not_exists({**l, **_seed_governance()})
            lessons_loaded += 1

        await session.commit()

    print(
        f"Seed complete — "
        f"versions: {versions_loaded}, "
        f"rules: {rules_loaded}, "
        f"coolify_checks: {coolify_loaded}, "
        f"combos: {combos_loaded}, "
        f"lessons: {lessons_loaded}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Infra Brain database")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skip-existing", action="store_true", default=True, help="Skip records that already exist (default)")
    group.add_argument("--force", action="store_true", help="Overwrite all existing records")
    args = parser.parse_args()

    skip_existing = not args.force
    asyncio.run(seed(skip_existing=skip_existing))


if __name__ == "__main__":
    main()
