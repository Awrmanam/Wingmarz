from aiogram import Bot
from typing import Any, Optional

from .text_utils import convert_markdown_bold_to_html


def _is_explicit_html(parse_mode: Any) -> bool:
    """True only when this individual request explicitly selects HTML."""
    value = getattr(parse_mode, "value", parse_mode)
    return isinstance(value, str) and value.lower() == "html"


class BoldFixBot(Bot):
    async def send_message(self, chat_id: int | str, text: str, *args: Any, **kwargs: Any):
        if isinstance(text, str) and not _is_explicit_html(kwargs.get("parse_mode")):
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
        if isinstance(text, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            text = convert_markdown_bold_to_html(text)
        return await super().edit_message_text(text, chat_id, message_id, inline_message_id, *args, **kwargs)

    async def send_photo(self, chat_id: int | str, photo: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
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
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            caption = convert_markdown_bold_to_html(caption)
        return await super().edit_message_caption(chat_id, message_id, inline_message_id, caption, *args, **kwargs)

    async def send_document(self, chat_id: int | str, document: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_document(chat_id, document, *args, **kwargs)

    async def send_audio(self, chat_id: int | str, audio: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_audio(chat_id, audio, *args, **kwargs)

    async def send_video(self, chat_id: int | str, video: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_video(chat_id, video, *args, **kwargs)

    async def send_animation(self, chat_id: int | str, animation: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_animation(chat_id, animation, *args, **kwargs)

    async def send_voice(self, chat_id: int | str, voice: Any, *args: Any, **kwargs: Any):
        caption = kwargs.get("caption")
        if isinstance(caption, str) and not _is_explicit_html(kwargs.get("parse_mode")):
            kwargs["caption"] = convert_markdown_bold_to_html(caption)
        return await super().send_voice(chat_id, voice, *args, **kwargs)

    async def send_media_group(self, chat_id: int | str, media: Any, *args: Any, **kwargs: Any):
        try:
            # media is a list of InputMedia* with optional .caption
            for item in media or []:
                cap = getattr(item, "caption", None)
                if isinstance(cap, str) and not _is_explicit_html(getattr(item, "parse_mode", None)):
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
            if isinstance(cap, str) and not _is_explicit_html(getattr(media, "parse_mode", None)):
                setattr(media, "caption", convert_markdown_bold_to_html(cap))
        except Exception:
            pass
        return await super().edit_message_media(media, chat_id, message_id, inline_message_id, *args, **kwargs)

    async def __call__(self, method, *args: Any, **kwargs: Any):
        """Normalize outgoing formatting and resolve ``{emoji:key}`` placeholders.

        Message.* helpers reach the Bot through this method, so Premium Emoji
        placeholders work consistently for normal messages, edits and captions.
        """
        try:
            text = getattr(method, "text", None)
            if (isinstance(text, str) and not _is_explicit_html(getattr(method, "parse_mode", None))
                    and any(marker in text for marker in ("**", "__", "`", "*", "_"))):
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
            if (isinstance(caption, str) and not _is_explicit_html(getattr(method, "parse_mode", None))
                    and any(marker in caption for marker in ("**", "__", "`", "*", "_"))):
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
