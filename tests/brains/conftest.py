import pytest

import src.brains.app.tools.apps as apps_tools_module
import src.brains.app.tools.knowledge as knowledge_tools_module


@pytest.fixture(autouse=True)
def _no_infra_brain_address_book(monkeypatch):
    """App Brain write tests never reach Infra Brain; the guard sees no book (unguarded)."""

    async def unreachable():
        return None

    monkeypatch.setattr(knowledge_tools_module, "fetch_address_book", unreachable)
    monkeypatch.setattr(apps_tools_module, "fetch_address_book", unreachable)
