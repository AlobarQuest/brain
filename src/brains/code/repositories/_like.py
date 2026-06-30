def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE special characters to prevent wildcard injection."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
