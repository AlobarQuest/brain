import ipaddress
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


_STRING_KEYS = ("ipv4", "ipv6", "tailnet_node", "tailnet_ip")
_IP_VERSIONS = {"ipv4": 4, "ipv6": 6, "tailnet_ip": 4}
_MIN_NAME_LEN = 4
_NAME_MARKS = frozenset("0123456789.:-")


def _bad_address(field: str, value: object, reason: str) -> dict:
    return {
        "error": "invalid_addresses",
        "field": field,
        "value": value,
        "reason": reason,
        "allowed": list(ADDRESS_KEYS),
    }


def _undistinctive(name: str) -> bool:
    """A name matched as a substring must not be a short or plain word (`ubuntu`)."""
    return len(name) < _MIN_NAME_LEN or not _NAME_MARKS & set(name)


def _is_ip(value: str, version: int) -> bool:
    try:
        return ipaddress.ip_address(value).version == version
    except ValueError:
        return False


def _invalid_addresses(addresses: object) -> dict | None:
    if not isinstance(addresses, dict) or set(addresses) - set(ADDRESS_KEYS):
        return {"error": "invalid_addresses", "allowed": list(ADDRESS_KEYS)}
    for key in _STRING_KEYS:
        if key in addresses and not (isinstance(addresses[key], str) and addresses[key]):
            return _bad_address(key, addresses[key], "must be a non-empty string")
    for key, version in _IP_VERSIONS.items():
        if key in addresses and not _is_ip(addresses[key], version):
            return _bad_address(key, addresses[key], f"must be a valid IPv{version} address")
    hostnames = addresses.get("hostnames", [])
    if not isinstance(hostnames, list) or not all(isinstance(h, str) and h for h in hostnames):
        return _bad_address("hostnames", hostnames, "must be a list of non-empty strings")
    names = [("tailnet_node", addresses["tailnet_node"])] if "tailnet_node" in addresses else []
    names.extend(("hostnames", h) for h in hostnames)
    for field, name in names:
        if _undistinctive(name):
            return _bad_address(
                field,
                name,
                f"must be at least {_MIN_NAME_LEN} characters and contain a digit, '.', ':' or '-'",
            )
    return None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _invalid_previous(previous: list | None) -> dict | None:
    for index, entry in enumerate(previous or []):
        if not isinstance(entry, dict):
            return {
                "error": "invalid_previous",
                "index": index,
                "reason": "each previous entry must be an object",
            }
        if not isinstance(entry.get("provider"), str):
            return {
                "error": "invalid_previous",
                "index": index,
                "reason": "provider must be a string",
            }
        if (bad := _invalid_addresses(entry.get("addresses"))) is not None:
            return bad
        if _parse_date(entry.get("until")) is None:
            return {"error": "invalid_date", "field": "previous.until"}
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
        ipv4 and tailnet_ip (IPv4 addresses), ipv6 (an IPv6 address), tailnet_node (a string) and
        hostnames (a list of strings); all optional. tailnet_node and each hostname must be at least 4 characters and
        contain a digit, '.', ':' or '-'. current_since is an ISO date. When previous is omitted
        and ipv4/ipv6/tailnet_ip changed, the old host is appended to previous (until = today);
        pass previous to replace the list explicitly. Each previous entry is
        {addresses, provider, until}. Store no secrets."""
        if not require_approver():
            return {
                "error": "not_authorized",
                "hint": "set_environment requires the approver key",
            }
        if (bad := _invalid_addresses(addresses)) is not None:
            return bad
        if (bad := _invalid_previous(previous)) is not None:
            return bad
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
