import logging
from datetime import date

from fastmcp import FastMCP

from src.brains.infra.models import Environment
from src.brains.infra.repositories.environments import EnvironmentRepository
from src.core.db import get_session_factory
from src.core.governance import require_approver

logger = logging.getLogger(__name__)

ADDRESS_KEYS = ("ipv4", "ipv6", "tailnet_node", "tailnet_ip", "hostnames")


def environment_dict(env: Environment) -> dict:
    return {
        "role": env.role,
        "description": env.description,
        "provider": env.provider,
        "location": env.location,
        "addresses": env.addresses,
        "current_since": env.current_since.isoformat(),
        "previous": env.previous,
        "updated_at": env.updated_at.isoformat() if env.updated_at else None,
        "updated_by": env.updated_by,
    }


def _invalid_addresses(addresses: object) -> dict | None:
    if not isinstance(addresses, dict) or set(addresses) - set(ADDRESS_KEYS):
        return {"error": "invalid_addresses", "allowed": list(ADDRESS_KEYS)}
    return None


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def register_environment_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_environments() -> dict:
        """List every environment role (e.g. prod-vps) with its current host addresses and the
        hosts it has moved off. Readable with any key."""
        async with get_session_factory()() as session:
            envs = await EnvironmentRepository(session).list_all()
            return {"environments": [environment_dict(e) for e in envs]}

    @mcp.tool()
    async def get_environment(role: str) -> dict:
        """Get one environment role by name, including its previous hosts."""
        async with get_session_factory()() as session:
            env = await EnvironmentRepository(session).get(role)
            if env is None:
                return {"error": "not_found", "role": role}
            return environment_dict(env)

    @mcp.tool()
    async def set_environment(
        role: str,
        description: str,
        provider: str,
        location: str,
        addresses: dict,
        current_since: str,
        previous: list[dict] | None = None,
        updated_by: str = "devon",
    ) -> dict:
        """Create or update an environment role. APPROVER KEY ONLY. addresses may hold only
        ipv4, ipv6, tailnet_node, tailnet_ip, hostnames (list); all optional. current_since is an
        ISO date. When previous is omitted and ipv4/ipv6/tailnet_ip changed, the old host is
        appended to previous (until = today); pass previous to replace the list explicitly. Each
        previous entry is {addresses, provider, until}. Store no secrets."""
        if not require_approver():
            return {
                "error": "not_authorized",
                "hint": "set_environment requires the approver key",
            }
        if (bad := _invalid_addresses(addresses)) is not None:
            return bad
        for entry in previous or []:
            if (bad := _invalid_addresses(entry.get("addresses"))) is not None:
                return bad
            if _parse_date(entry.get("until")) is None:
                return {"error": "invalid_date", "field": "previous.until"}
        since = _parse_date(current_since)
        if since is None:
            return {"error": "invalid_date", "field": "current_since"}
        fields = {
            "description": description,
            "provider": provider,
            "location": location,
            "addresses": addresses,
            "current_since": since,
            "updated_by": updated_by,
        }
        async with get_session_factory()() as session:
            env, rotated = await EnvironmentRepository(session).upsert(
                role, fields, previous, date.today()
            )
            await session.commit()
            logger.info("environment %s set by %s (rotated=%s)", role, updated_by, rotated)
            return {**environment_dict(env), "rotated": rotated}
