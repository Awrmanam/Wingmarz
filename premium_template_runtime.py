from __future__ import annotations

import re

import config
from premium_ui_service import premium_ui_service


_TOKEN_RE = re.compile(r"\{emoji:[a-z0-9_.-]{1,64}\}")


class PremiumTemplateString(str):
    """String whose .format() keeps {emoji:key} tokens untouched.

    Business templates such as order_approved_user still use Python ``.format``
    for username/password. Without this wrapper ``{emoji:wire}`` would be parsed
    as a Python format field. Tokens are protected, normal fields are formatted,
    then Premium Emoji tokens are restored for the outgoing Bot renderer.
    """

    def format(self, *args, **kwargs):
        tokens: list[str] = []

        def protect(match: re.Match[str]) -> str:
            index = len(tokens)
            tokens.append(match.group(0))
            return f"__WINGMARZ_EMOJI_TOKEN_{index}__"

        protected = _TOKEN_RE.sub(protect, str(self))
        rendered = protected.format(*args, **kwargs)
        for index, token in enumerate(tokens):
            rendered = rendered.replace(f"__WINGMARZ_EMOJI_TOKEN_{index}__", token)
        return PremiumTemplateString(rendered)

    def format_map(self, mapping):
        tokens: list[str] = []

        def protect(match: re.Match[str]) -> str:
            index = len(tokens)
            tokens.append(match.group(0))
            return f"__WINGMARZ_EMOJI_TOKEN_{index}__"

        protected = _TOKEN_RE.sub(protect, str(self))
        rendered = protected.format_map(mapping)
        for index, token in enumerate(tokens):
            rendered = rendered.replace(f"__WINGMARZ_EMOJI_TOKEN_{index}__", token)
        return PremiumTemplateString(rendered)


def _wrap_messages() -> None:
    for key, value in list(config.MESSAGES.items()):
        if not isinstance(value, PremiumTemplateString):
            config.MESSAGES[key] = PremiumTemplateString(value)


_original_apply = premium_ui_service.apply_message_overrides
_original_set = premium_ui_service.set_message
_original_reset = premium_ui_service.reset_message


async def apply_message_overrides_preserving_tokens() -> None:
    await _original_apply()
    _wrap_messages()


async def set_message_preserving_tokens(key: str, body: str) -> None:
    await _original_set(key, body)
    config.MESSAGES[key] = PremiumTemplateString(config.MESSAGES[key])


async def reset_message_preserving_tokens(key: str) -> None:
    await _original_reset(key)
    config.MESSAGES[key] = PremiumTemplateString(config.MESSAGES[key])


premium_ui_service.apply_message_overrides = apply_message_overrides_preserving_tokens
premium_ui_service.set_message = set_message_preserving_tokens
premium_ui_service.reset_message = reset_message_preserving_tokens
_wrap_messages()
