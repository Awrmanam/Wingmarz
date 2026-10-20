from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ForcedJoinState(str, Enum):
    JOINED = "joined"
    NOT_JOINED = "not_joined"
    ERROR = "error"


@dataclass(frozen=True)
class ForcedJoinCheck:
    state: ForcedJoinState
    chat_id: int | str
    title: str
    error: str | None = None


def normalize_chat_ref(raw: Any) -> int | str:
    """Normalize legacy forced-channel refs into Bot API-compatible identifiers."""
    if isinstance(raw, int):
        return raw
    value = str(raw or "").strip()
    if not value:
        raise ValueError("Chat ID کانال خالی است")

    # Public username.
    if value.startswith("@"):
        return value

    # Accept common t.me public URLs from older/manual setup.
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if value.lower().startswith(prefix):
            tail = value[len(prefix):].strip("/ ")
            if not tail or tail.startswith("+") or tail.startswith("joinchat/"):
                raise ValueError("برای کانال خصوصی باید Chat ID واقعی ذخیره شود، نه لینک دعوت")
            if "/" in tail:
                tail = tail.split("/", 1)[0]
            return f"@{tail}"

    if value.lstrip("-").isdigit():
        # Already a canonical Bot API supergroup/channel id.
        if value.startswith("-100"):
            return int(value)
        # Legacy UI often stored the numeric body without the -100 prefix.
        unsigned = value.lstrip("-")
        return int(f"-100{unsigned}")

    # Bare usernames are common user input mistakes; make them usable.
    if value.replace("_", "").isalnum():
        return f"@{value}"

    raise ValueError("Chat ID / username کانال قابل تشخیص نیست")


def member_status(member: Any) -> str:
    status = getattr(member, "status", "")
    if hasattr(status, "value"):
        return str(status.value).lower()
    if hasattr(status, "name"):
        return str(status.name).lower()
    return str(status).lower().replace("chatmemberstatus.", "")


async def check_membership(bot, channel: dict[str, Any], user_id: int) -> ForcedJoinCheck:
    title = str(channel.get("title") or channel.get("chat_id") or "کانال")
    try:
        chat_id = normalize_chat_ref(channel.get("chat_id"))
    except Exception as exc:
        return ForcedJoinCheck(
            ForcedJoinState.ERROR,
            str(channel.get("chat_id") or ""),
            title,
            f"invalid stored chat reference: {exc}",
        )

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=int(user_id))
    except Exception as exc:
        return ForcedJoinCheck(
            ForcedJoinState.ERROR,
            chat_id,
            title,
            f"{type(exc).__name__}: {exc}",
        )

    status = member_status(member)
    if status in {"member", "administrator", "creator", "owner"}:
        return ForcedJoinCheck(ForcedJoinState.JOINED, chat_id, title)

    return ForcedJoinCheck(ForcedJoinState.NOT_JOINED, chat_id, title)


async def inspect_channel_access(bot, raw_chat_id: Any) -> dict[str, Any]:
    """Validate selected channel and return canonical metadata for storage."""
    chat_id = normalize_chat_ref(raw_chat_id)
    chat = await bot.get_chat(chat_id)
    actual_id = int(chat.id)
    chat_type = str(getattr(getattr(chat, "type", None), "value", getattr(chat, "type", ""))).lower()
    if chat_type not in {"channel", "supergroup"}:
        raise ValueError("چت انتخاب‌شده کانال/سوپرگروه نیست")

    me = await bot.get_me()
    member = await bot.get_chat_member(actual_id, int(me.id))
    status = member_status(member)
    if status not in {"administrator", "creator", "owner"}:
        raise ValueError("بات داخل این کانال Admin نیست")

    username = getattr(chat, "username", None)
    title = str(getattr(chat, "title", None) or username or actual_id)
    invite_link = f"https://t.me/{username}" if username else None
    return {
        "chat_id": str(actual_id),
        "title": title,
        "invite_link": invite_link,
        "bot_status": status,
    }
