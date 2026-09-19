"""Telegram delivery helpers for encrypted Wingmarz backups."""

from __future__ import annotations

from html import escape

from aiogram.types import FSInputFile

import config
from utils.backup import BackupArtifact


def _secret_lines(artifact: BackupArtifact) -> list[str]:
    lines = [
        "🔐 <b>اطلاعات بازیابی</b>",
        f"رمز فایل ZIP: <code>{escape(artifact.zip_password)}</code>",
        f"نوع دیتابیس: <code>{escape(artifact.database_engine)}</code>",
        "رمز دیتابیس: <code>ندارد (SQLite)</code>",
    ]
    runtime_secrets = (
        ("توکن ربات", getattr(config, "BOT_TOKEN", "")),
        ("نام کاربری مرزبان", getattr(config, "MARZBAN_USERNAME", "")),
        ("رمز مرزبان", getattr(config, "MARZBAN_PASSWORD", "")),
        ("توکن Rebecca", getattr(config, "REBECCA_BEARER_TOKEN", "")),
    )
    configured = [(label, str(value)) for label, value in runtime_secrets if str(value or "").strip()]
    if configured:
        lines.extend(("", "🔑 <b>دسترسی‌های فعلی</b>"))
        lines.extend(f"{label}: <code>{escape(value)}</code>" for label, value in configured)
    lines.extend(("", "⚠️ این پیام همه دسترسی‌های اصلی را دارد؛ آن را فوروارد نکنید."))
    return lines


def backup_caption(artifact: BackupArtifact, automatic: bool) -> str:
    kind = "خودکار" if automatic else "دستی"
    size_mb = artifact.size_bytes / (1024 * 1024)
    return (
        f"✅ <b>فول‌بکاپ {kind} Wingmarz</b>\n"
        f"📦 <code>{escape(artifact.path.name)}</code>\n"
        f"📏 {size_mb:.2f} MB\n"
        f"🕓 {artifact.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )


async def send_backup_to_chat(bot, chat_id: int, artifact: BackupArtifact, *, automatic: bool) -> None:
    document_message = await bot.send_document(
        chat_id=chat_id,
        document=FSInputFile(str(artifact.path)),
        caption=backup_caption(artifact, automatic),
        parse_mode="HTML",
    )
    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(_secret_lines(artifact)),
        parse_mode="HTML",
        reply_to_message_id=getattr(document_message, "message_id", None),
    )
