from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Environment
from src.core.address_guard import AddressBook, book_from_payload, payload_from_environments

# Current-side match strings: changing or removing one that had a value means the role moved
# hosts. Adding one that was absent is not a move, nor is a hostname edit.
_MATCH_KEYS = ("ipv4", "ipv6", "tailnet_ip")


def _moved(old: dict, new: dict) -> bool:
    return any(old.get(k) and old.get(k) != new.get(k) for k in _MATCH_KEYS)


class EnvironmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[Environment]:
        result = await self.session.execute(select(Environment).order_by(Environment.role))
        return list(result.scalars().all())

    async def address_book(self) -> AddressBook:
        """The retired/current address lookup, read in this repository's own session."""
        return book_from_payload(payload_from_environments(await self.list_all()))

    async def get(self, role: str) -> Environment | None:
        result = await self.session.execute(select(Environment).where(Environment.role == role))
        return result.scalar_one_or_none()

    async def upsert(
        self, role: str, fields: dict, previous: list[dict] | None, today: date
    ) -> tuple[Environment, bool]:
        """Create or update a role. An explicit ``previous`` replaces the stored list. When it is
        omitted and a match string that had a value changed or was removed, the old host is
        appended to ``previous`` (rotation). Returns the row and whether it rotated."""
        env = await self.get(role)
        if env is None:
            env = Environment(role=role, previous=previous or [], **fields)
            self.session.add(env)
            await self.session.flush()
            return env, False

        rotated = False
        if previous is not None:
            new_previous = previous
        elif _moved(env.addresses, fields["addresses"]):
            rotated = True
            new_previous = [
                *env.previous,
                {
                    "addresses": env.addresses,
                    "provider": env.provider,
                    "until": today.isoformat(),
                },
            ]
        else:
            new_previous = env.previous
        for key, value in fields.items():
            setattr(env, key, value)
        env.previous = new_previous
        await self.session.flush()
        await self.session.refresh(env)  # onupdate expires updated_at; reload before serializing
        return env, rotated
