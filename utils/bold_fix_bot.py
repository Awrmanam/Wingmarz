import re
from aiogram import Bot
from typing import Any, Optional

from .text_utils import convert_markdown_bold_to_html


_HTML_TAG_RE = re.compile(r"</?(?:b|strong|i|em|u|ins|s|strike|del|span|tg-spoiler|a|code|pre|blockquote|tg-emoji)(?:\s[^>]*)?>", re.IGNORECASE)
_MD_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*[^*\n]+\*(?!\*)")
_MD_ITALIC_US_RE = re.compile(r"(?<![\w_])_[^_\n]+_(?![\w_])")


def _is_explicit_html(parse_mode: Any) -> bool:
    """True only when this individual request explicitly selects HTML."""
    value = getattr(parse_mode, "value", parse_mode)
    return isinstance(value, str) and value.lower() == "html"


def _should_convert_markdown(text: Any, parse_mode: Any = None) -> bool:
    """Convert only actual Markdown, never an existing Telegram HTML message.

    The old runtime treated any underscore as Markdown. Usernames such as
    ``arman_panel`` therefore caused an otherwise valid HTML message to be
    escaped, exposing literal <b>/<code> tags in Telegram.
    """
    if not isinstance(text, str) or _is_explicit_html(parse_mode):
        return False
    if _HTML_TAG_RE.search(text):
        return False
    if "```" in text or "`" in text or "**" in text or "__" in text:
        return True
    return bool(_MD_ITALIC_STAR_RE.search(text) or _MD_ITALIC_US_RE.search(text))


class BoldFixBot(Bot):
    async def send_message(self, chat_id: int | str, text: str, *args: Any, **kwargs: Any):
        if _should_convert_markdown(text, kwargs.get("parse_mode")):
            text = convert_markdown_bold_to_html(text)
        return await super().send_message(chat_id, text, *args, **kwargs)

    async def edit_message_text(
        self,
        text: str,
        chat_id: Optional[int | str] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ):
        if _should_convert_markdown(text, kwargs.get("parse_mode")):
            text = convert_markdown_bold_to_html(text)
        return await super().edit_message_text(text, chat_id, message_id, inline_message_id, *args, **kwargs)

    async def send_photo(self, chat_id: int | str, photo: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_photo(chat_id, photo, *args, **kwargs)

    async def edit_message_caption(
        self,
        chat_id: Optional[int | str] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        caption: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ):
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            caption = convert_markdown_bold_to_html(caption)
        return await super().edit_message_caption(chat_id, message_id, inline_message_id, caption, *args, **kwargs)

    async def send_document(self, chat_id: int | str, document: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_document(chat_id, document, *args, **kwargs)

    async def send_audio(self, chat_id: int | str, audio: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_audio(chat_id, audio, *args, **kwargs)

    async def send_video(self, chat_id: int | str, video: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_video(chat_id, video, *args, **kwargs)

    async def send_animation(self, chat_id: int | str, animation: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_animation(chat_id, animation, *args, **kwargs)

    async def send_voice(self, chat_id: int | str, voice: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if _should_convert_markdown(caption, kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_voice(chat_id, voice, *args, **kwargs)

    async def send_media_group(self, chat_id: int | str, media: Any, *args: Any, **kwargs: Any):
        try:
            for item in media or []:
                cap = getattr(item, "caption", None)
                if _should_convert_markdown(cap, getattr(item, "parse_mode", None)):
                    setattr(item, "caption", convert_markdown_bold_to_html(cap))
        except Exception:
            pass
        return await super().send_media_group(chat_id, media, *args, **kwargs)

    async def edit_message_media(
        self,
        media: Any,
        chat_id: Optional[int | str] = None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ):
        try:
            cap = getattr(media, "caption", None)
            if _should_convert_markdown(cap, getattr(media, "parse_mode", None)):
                setattr(media, "caption", convert_markdown_bold_to_html(cap))
        except Exception:
            pass
        return await super().edit_message_media(media, chat_id, message_id, inline_message_id, *args, **kwargs)

    async def __call__(self, method, *args: Any, **kwargs: Any):
        """Normalize outgoing formatting and resolve ``{emoji:key}`` placeholders."""
        try:
            text = getattr(method, "text", None)
            if _should_convert_markdown(text, getattr(method, "parse_mode", None)):
                text = convert_markdown_bold_to_html(text)
            if isinstance(text, str) and "{emoji:" in text:
                from premium_ui_service import premium_ui_service
                text = await premium_ui_service.render_placeholders(text)
            if isinstance(text, str):
                setattr(method, "text", text)
        except Exception:
            pass
        try:
            caption = getattr(method, "caption", None)
            if _should_convert_markdown(caption, getattr(method, "parse_mode", None)):
                caption = convert_markdown_bold_to_html(caption)
            if isinstance(caption, str) and "{emoji:" in caption:
                from premium_ui_service import premium_ui_service
                caption = await premium_ui_service.render_placeholders(caption)
            if isinstance(caption, str):
                setattr(method, "caption", caption)
        except Exception:
            pass
        try:
            media = getattr(method, "media", None)
            if isinstance(media, (list, tuple)):
                from premium_ui_service import premium_ui_service
                for item in media:
                    cap = getattr(item, "caption", None)
                    if isinstance(cap, str) and "{emoji:" in cap:
                        setattr(item, "caption", await premium_ui_service.render_placeholders(cap))
        except Exception:
            pass
        return await super().__call__(method, *args, **kwargs)
