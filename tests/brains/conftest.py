import pytest

import src.brains.app.tools.apps as apps_tools_module
import src.brains.app.tools.knowledge as knowledge_tools_module
import src.brains.code.services.proposals as code_proposals_module
import src.brains.code.tools.exemplars as code_exemplars_module
import src.brains.code.tools.lessons as code_lessons_module
import src.brains.code.tools.roads as code_roads_module
import src.brains.code.tools.rules as code_rules_module


@pytest.fixture(autouse=True)
def _no_infra_brain_address_book(monkeypatch):
    """App and Code Brain write tests never reach Infra Brain; the guard sees no book (unguarded)."""

    async def unreachable():
        return None

    monkeypatch.setattr(knowledge_tools_module, "fetch_address_book", unreachable)
    for module in (
        apps_tools_module,
        code_proposals_module,
        code_exemplars_module,
        code_lessons_module,
        code_roads_module,
        code_rules_module,
    ):
        monkeypatch.setattr(module, "fetch_address_book", unreachable)
