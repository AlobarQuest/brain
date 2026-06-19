"""x-brain-key authentication middleware.

Provides ``make_auth_middleware`` which returns a Starlette ``BaseHTTPMiddleware``
subclass that enforces a shared secret on all paths NOT covered by the
caller-supplied ``allowlist``.

Authentication: the secret must appear as the ``x-brain-key`` request header
OR as the ``?key=`` query parameter.  Comparison uses ``hmac.compare_digest``
to prevent timing attacks.  Absent or mismatched credentials produce a 401
JSON response.
"""
from __future__ import annotations

import hmac
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


def make_auth_middleware(
    access_key: str,
    allowlist: tuple[str, ...],
) -> type[BaseHTTPMiddleware]:
    """Return a configured auth-middleware class ready for ``app.add_middleware()``.

    Args:
        access_key: The expected secret value (compared via ``hmac.compare_digest``).
        allowlist:  Tuple of path prefixes that bypass authentication entirely.
                    A request whose ``path`` starts with *any* entry passes through.

    Returns:
        A ``BaseHTTPMiddleware`` subclass (not an instance) so that
        ``app.add_middleware(<class>)`` works without extra arguments.
    """

    class BrainKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            # Prefix-match against every allowlisted path.
            for prefix in allowlist:
                if request.url.path.startswith(prefix):
                    return await call_next(request)

            provided = request.headers.get("x-brain-key") or request.query_params.get("key")
            if not provided or not hmac.compare_digest(provided, access_key):
                return JSONResponse(
                    content={"error": "Invalid or missing access key"},
                    status_code=401,
                )

            return await call_next(request)

    return BrainKeyMiddleware
