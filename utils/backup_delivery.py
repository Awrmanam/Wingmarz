"""Secure delivery of encrypted Wingmarz backups.

The archive contains .env and credential-bearing settings. Only the numeric
Telegram IDs of trusted owners may receive it, in a private chat; plaintext
recovery passwords and API keys are NEVER transmitted with the file.
"""
from __future__ import annotations

from html import escape

from aiogram.types import FSInputFile

import config
from authorization import is_owner
from utils.backup import BackupArtifact


def backup_caption(artifact: BackupArtifact, automatic: bool) -> str:
    kind = "خودکار" if automatic else "دستی"
    size_mb = artifact.size_bytes / (1024 * 1024)
    return (
        f"🗄️ <b>بکاپ رمزگذاری‌شده Wingmarz · {kind}</b>\n\n"
        f"📦 <code>{escape(artifact.path.name)}</code>\n"
        f"📏 حجم: {size_mb:.2f} MB\n"
        f"🕓 زمان: {artifact.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        "🔒 این آرشیو شامل اطلاعات محرمانه است. رمز آن فقط در تنظیمات امن سرور "
        "نگهداری می‌شود و هرگز در تلگرام ارسال نمی‌شود."
    )


async def send_backup_to_chat(bot, chat_id: int, artifact: BackupArtifact, *, automatic: bool) -> None:
    # A private user chat has the same positive ID as the Telegram user.
    # Never send the archive to a supergroup, forum topic, or third party.
    # Runtime managers are included in config.SUDO_ADMINS for legacy routes.
    # Encrypted archives include .env, panel credentials, and the customer DB;
    # only immutable server-configured owners may receive them.
    if not is_owner(int(chat_id)) or int(chat_id) <= 0:
        raise PermissionError("Backup recipients must be owner Telegram user IDs")
    await bot.send_document(
        chat_id=int(chat_id),
        document=FSInputFile(str(artifact.path)),
        caption=backup_caption(artifact, automatic),
        parse_mode="HTML",
    )
