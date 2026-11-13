"""Navigation regressions: stale inline keyboards must not reopen retired menus."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from handlers import admin_handlers, home_navigation, operations, public_handlers, sudo_handlers


def run(coro):
    return asyncio.run(coro)


def callback(user_id=9001):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def state():
    return SimpleNamespace(clear=AsyncMock())


def test_all_legacy_back_handlers_render_canonical_role_aware_home(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(home_navigation, "render_home", render)
    monkeypatch.setattr(operations, "_deny", AsyncMock(return_value=False))
    monkeypatch.setattr(config, "SUDO_ADMINS", [9001])
    handlers = (
        admin_handlers.back_to_admin_main,
        public_handlers.public_back_main,
        operations.operational_back,
        sudo_handlers.back_to_main,
        home_navigation.role_aware_back,
    )
    for handler in handlers:
        cb = callback()
        st = state()
        run(handler(cb, st))
        st.clear.assert_awaited_once()
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_not_awaited()
    assert render.await_count == len(handlers)
    for call in render.await_args_list:
        assert call.args[1] == 9001
        assert call.kwargs == {"edit": True}


def test_legacy_public_start_uses_canonical_renderer(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(home_navigation, "render_home", render)
    monkeypatch.setattr(config, "SUDO_ADMINS", [9001])
    monkeypatch.setattr(public_handlers.db, "is_admin_authorized", AsyncMock(return_value=False))
    message = SimpleNamespace(from_user=SimpleNamespace(id=48), answer=AsyncMock())
    run(public_handlers.public_catch_all(message, "/start"))
    render.assert_awaited_once_with(message, 48, edit=False)
    message.answer.assert_not_awaited()


def test_legacy_reseller_start_uses_canonical_renderer(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(home_navigation, "render_home", render)
    monkeypatch.setattr(config, "SUDO_ADMINS", [9001])
    monkeypatch.setattr(config, "PANEL_PROVIDER", "marzban")
    message = SimpleNamespace(from_user=SimpleNamespace(id=48), answer=AsyncMock())
    run(admin_handlers.admin_start(message))
    render.assert_awaited_once_with(message, 48, edit=False)
    message.answer.assert_not_awaited()


def test_no_legacy_home_builders_in_active_legacy_handlers():
    """Synchronous helpers may exist for external imports, never be called by a handler."""
    for name in ("handlers/admin_handlers.py", "handlers/public_handlers.py", "handlers/sudo_handlers.py"):
        source = Path(name).read_text(encoding="utf-8")
        parsed = ast.parse(source)
        forbidden = {"get_admin_keyboard", "get_public_main_keyboard"}
        calls = [
            node.func.id for node in ast.walk(parsed)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden
        ]
        assert not calls, (name, calls)


def test_deprecated_keyboards_only_link_to_canonical_home():
    buttons = admin_handlers.get_admin_keyboard().inline_keyboard
    assert [b.callback_data for row in buttons for b in row] == ["back_to_admin_main"]
    buttons = public_handlers.get_public_main_keyboard().inline_keyboard
    assert [b.callback_data for row in buttons for b in row] == ["public_back_main"]
    assert "back_to_main" in Path("handlers/home_navigation.py").read_text(encoding="utf-8")
