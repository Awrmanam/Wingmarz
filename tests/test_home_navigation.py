import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import home_navigation as home


def run(coro):
    return asyncio.run(coro)


def test_inactive_panel_owner_is_still_an_admin_home_user(monkeypatch):
    monkeypatch.setattr(
        home.db,
        "get_admins_for_user",
        AsyncMock(return_value=[SimpleNamespace(is_active=False)]),
    )
    assert run(home.has_admin_account(42)) is True
    text = run(home._admin_home_text(42))
    assert "پنل فعالی ندارید" in text
    assert "تمدید/افزایش" in text


def test_home_copy_is_composed_from_independently_editable_sections(monkeypatch):
    monkeypatch.setitem(home.config.MESSAGES, "home_public_title", "TITLE")
    monkeypatch.setitem(home.config.MESSAGES, "home_public_body", "BODY")
    monkeypatch.setitem(home.config.MESSAGES, "home_public_footer", "FOOTER")
    assert home._public_home_text() == "TITLE\n\nBODY\n\nFOOTER"

    monkeypatch.setitem(home.config.MESSAGES, "home_public_body", "")
    assert home._public_home_text() == "TITLE\n\nFOOTER"


def test_user_without_panel_record_is_public(monkeypatch):
    monkeypatch.setattr(home.db, "get_admins_for_user", AsyncMock(return_value=[]))
    assert run(home.has_admin_account(42)) is False


def test_home_router_precedes_public_start_fallback():
    source = Path("handlers/__init__.py").read_text(encoding="utf-8")
    assert source.index("include_router(operations_router)") < source.index("include_router(home_navigation_router)")
    assert source.index("include_router(home_navigation_router)") < source.index("include_router(operations_public_router)")


def test_all_back_aliases_converge_on_role_aware_home(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(home, "render_home", render)
    state = SimpleNamespace(clear=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        message=object(),
        answer=AsyncMock(),
    )
    run(home.role_aware_back(callback, state))
    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(callback.message, 42, edit=True)
    callback.answer.assert_awaited_once()
