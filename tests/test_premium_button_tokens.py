import asyncio
from types import SimpleNamespace

from premium_markup_runtime import _consume_button_emoji_token
from style_engine import style_engine


def test_known_button_token_becomes_icon_key_and_is_removed(monkeypatch):
    async def fake_get_emoji(key):
        if key == "wire":
            return SimpleNamespace(enabled=True, custom_emoji_id="123", fallback_unicode="🎮")
        return None

    monkeypatch.setattr(style_engine, "get_emoji", fake_get_emoji)
    text, key = asyncio.run(
        _consume_button_emoji_token("WireGuard {emoji:wire} - سفارش #14", None)
    )
    assert text == "WireGuard - سفارش #14"
    assert key == "wire"


def test_generic_fallback_is_removed_when_premium_token_resolves(monkeypatch):
    async def fake_get_emoji(key):
        return SimpleNamespace(enabled=True, custom_emoji_id="123", fallback_unicode="🎮")

    monkeypatch.setattr(style_engine, "get_emoji", fake_get_emoji)
    text, key = asyncio.run(
        _consume_button_emoji_token("📦 WireGuard {emoji:wire} · 180,000 ت", None)
    )
    assert text == "WireGuard · 180,000 ت"
    assert key == "wire"


def test_unknown_button_token_stays_visible(monkeypatch):
    async def fake_get_emoji(key):
        return None

    monkeypatch.setattr(style_engine, "get_emoji", fake_get_emoji)
    text, key = asyncio.run(_consume_button_emoji_token("WireGuard {emoji:missing}", None))
    assert text == "WireGuard {emoji:missing}"
    assert key is None
