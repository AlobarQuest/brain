"""
Seed Code Brain with the road catalog + discipline rules from seed/data.json.

Roads are inserted before rules (rules FK road_slug -> roads.slug). Both are
race-safe idempotent upserts (ON CONFLICT DO NOTHING), so re-running is harmless
and only inserts rows that don't already exist.

Usage:
    python -m src.brains.code.seed                 # default (what start.sh runs)
    python -m src.brains.code.seed --skip-existing
    python -m src.brains.code.seed --force

`--skip-existing` and `--force` are accepted for parity with the other brains and
start.sh, but seeding is ALWAYS an idempotent ON CONFLICT DO NOTHING upsert, so
the two modes behave identically — there is no destructive overwrite path.
"""
import argparse
import asyncio
import json
from pathlib import Path

from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.repositories.rules import RuleRepository
from src.core.db import get_session_factory


async def seed() -> None:
    data = json.loads((Path(__file__).parent / "seed" / "data.json").read_text())
    roads = data.get("roads", [])
    rules = data.get("rules", [])

    async with get_session_factory()() as session:
        roads_repo = RoadRepository(session)
        rules_repo = RuleRepository(session)

        # Roads first — rules reference roads.slug.
        roads_inserted = sum([await roads_repo.add_if_not_exists(r) for r in roads])
        rules_inserted = sum([await rules_repo.add_if_not_exists(r) for r in rules])
        await session.commit()

    print(
        f"Seed complete — roads: {roads_inserted} inserted / {len(roads)} total, "
        f"rules: {rules_inserted} inserted / {len(rules)} total"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Code Brain database (idempotent upsert)")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--force", action="store_true")
    parser.parse_args()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
