import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pyzipper
import pytest

import config
from utils.backup import BackupArtifact, create_backup_zip, restore_database_from_zip
from utils.backup_delivery import send_backup_to_chat


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        connection.commit()


def test_encrypted_backup_contains_consistent_database_and_settings(tmp_path, monkeypatch):
    database = tmp_path / "data" / "bot_database.db"
    output_dir = tmp_path / "data" / "backups"
    _create_database(database, "original")
    (tmp_path / "data" / "extra.json").write_text('{"ok": true}', encoding="utf-8")
    (tmp_path / "bot.log").write_text("backup test", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", str(database))
    monkeypatch.setattr(config, "BACKUP_DIR", str(output_dir))
    monkeypatch.setattr(config, "BACKUP_ZIP_PASSWORD", "fixed-test-password")
    monkeypatch.setattr(config, "BACKUP_RETENTION_COUNT", 3)
    monkeypatch.setattr(config, "BACKUP_INCLUDE_LOGS", True)
    monkeypatch.setattr(config, "BOT_TOKEN", "12345:secret-token")
    monkeypatch.setattr(config, "MARZBAN_PASSWORD", "panel-password")

    artifact = asyncio.run(create_backup_zip())

    assert artifact.path.is_file()
    assert artifact.zip_password == "fixed-test-password"
    assert b"secret-token" not in artifact.path.read_bytes()

    with pyzipper.AESZipFile(artifact.path) as archive:
        archive.setpassword(artifact.zip_password.encode())
        names = archive.namelist()
        assert "database/bot_database.db" in names
        assert "configuration/settings.env" in names
        assert "runtime-data/extra.json" in names
        assert "logs/bot.log" in names
        settings = archive.read("configuration/settings.env").decode()
        assert 'BOT_TOKEN="12345:secret-token"' in settings
        assert 'MARZBAN_PASSWORD="panel-password"' in settings
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(archive.read("database/bot_database.db"))

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("original",)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)

    with pyzipper.AESZipFile(artifact.path) as archive:
        archive.setpassword(b"wrong-password")
        with pytest.raises(RuntimeError):
            archive.read("database/bot_database.db")


def test_restore_validates_database_and_preserves_previous_copy(tmp_path, monkeypatch):
    source = tmp_path / "source" / "bot_database.db"
    destination = tmp_path / "restore" / "bot_database.db"
    output_dir = tmp_path / "backups"
    _create_database(source, "from-backup")
    _create_database(destination, "before-restore")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", str(source))
    monkeypatch.setattr(config, "BACKUP_DIR", str(output_dir))
    monkeypatch.setattr(config, "BACKUP_ZIP_PASSWORD", "restore-password")
    monkeypatch.setattr(config, "BACKUP_INCLUDE_LOGS", False)
    artifact = asyncio.run(create_backup_zip())

    previous = asyncio.run(
        restore_database_from_zip(artifact.path, destination, artifact.zip_password)
    )

    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("from-backup",)
    with sqlite3.connect(previous) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("before-restore",)


class _FakeBot:
    def __init__(self):
        self.documents = []
        self.messages = []

    async def send_document(self, **kwargs):
        self.documents.append(kwargs)
        return SimpleNamespace(message_id=77)

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def test_delivery_places_recovery_credentials_directly_below_archive(tmp_path, monkeypatch):
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"test")
    artifact = BackupArtifact(
        path=archive,
        zip_password="zip<&password",
        database_password=None,
        database_engine="SQLite",
        created_at=datetime.now(timezone.utc),
        size_bytes=archive.stat().st_size,
    )
    monkeypatch.setattr(config, "BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "MARZBAN_USERNAME", "admin")
    monkeypatch.setattr(config, "MARZBAN_PASSWORD", "panel-password")
    monkeypatch.setattr(config, "REBECCA_BEARER_TOKEN", "")
    bot = _FakeBot()

    asyncio.run(send_backup_to_chat(bot, 123, artifact, automatic=True))

    assert bot.documents[0]["chat_id"] == 123
    assert bot.messages[0]["reply_to_message_id"] == 77
    assert "zip&lt;&amp;password" in bot.messages[0]["text"]
    assert "ندارد (SQLite)" in bot.messages[0]["text"]
    assert "bot-token" in bot.messages[0]["text"]
    assert "panel-password" in bot.messages[0]["text"]
