"""ASGI shim: makes /mcp (no trailing slash) behave like /mcp/ for the mounted FastMCP app."""


class MCPPrefixAlias:
    """Serve /mcp with the same mounted app behavior as /mcp/."""

    def __init__(self, app, mount_path: str):
        self.app = app
        self.mount_path = mount_path.rstrip("/")

    async def __call__(self, scope, receive, send) -> None:
        alias_scope = dict(scope)
        alias_scope["app_root_path"] = alias_scope.get(
            "app_root_path", alias_scope.get("root_path", "")
        )
        alias_scope["root_path"] = f"{alias_scope.get('root_path', '')}{self.mount_path}"
        alias_scope["path"] = f"{scope['path'].rstrip('/')}/"

        raw_path = scope.get("raw_path")
        if raw_path is not None:
            alias_scope["raw_path"] = raw_path.rstrip(b"/") + b"/"

        await self.app(alias_scope, receive, send)
