"""Create and restore encrypted, self-contained Wingmarz backups."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pyzipper

import config


APP_SETTING_NAMES = (
    "BOT_TOKEN", "MARZBAN_URL", "MARZBAN_USERNAME", "MARZBAN_PASSWORD",
    "PANEL_PROVIDER", "REBECCA_URL", "REBECCA_BEARER_TOKEN",
    "REBECCA_LOGIN_URL", "REBECCA_SERVICE_IDS", "SUDO_ADMINS",
    "DATABASE_PATH", "BACKUP_DIR", "MONITORING_INTERVAL", "WARNING_THRESHOLD",
    "AUTO_DELETE_EXPIRED_USERS", "API_TIMEOUT", "MAX_RETRIES",
    "BACKUP_AUTO_ENABLED", "BACKUP_INTERVAL_HOURS", "BACKUP_RETENTION_COUNT",
    "BACKUP_ZIP_PASSWORD", "BACKUP_RECIPIENTS", "BACKUP_INCLUDE_LOGS",
)


@dataclass(frozen=True)
class BackupArtifact:
    path: Path
    zip_password: str
    database_password: str | None
    database_engine: str
    created_at: datetime
    size_bytes: int


def _resolved(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _database_path() -> Path:
    configured = str(getattr(config, "DATABASE_PATH", "") or "").strip()
    candidates = []
    if configured:
        candidates.append(_resolved(Path(configured)))
    candidates.extend(
        _resolved(candidate)
        for candidate in (
            Path.cwd() / "data" / "bot_database.db",
            Path.cwd() / "bot_database.db",
            Path("/app/data/bot_database.db"),
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else _resolved(Path("bot_database.db"))


def _backup_dir(db_path: Path) -> Path:
    configured = str(getattr(config, "BACKUP_DIR", "") or "").strip()
    return _resolved(Path(configured)) if configured else _resolved(db_path.parent / "backups")


def _serialize_setting(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return str(value if value is not None else "")


def _quote_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _settings_text() -> str:
    lines = ["# Wingmarz runtime settings", "# Contains secrets. Keep this file private."]
    for name in APP_SETTING_NAMES:
        value = getattr(config, name, os.getenv(name, ""))
        lines.append(f"{name}={_quote_env(_serialize_setting(value))}")
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Database file does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
        with sqlite3.connect(destination) as check_db:
            result = check_db.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite backup integrity check failed: {result}")


def _copy_optional_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_runtime_data(source_dir: Path, destination_dir: Path, excluded: Iterable[Path]) -> None:
    if not source_dir.is_dir():
        return
    excluded_paths = {_resolved(path) for path in excluded}
    for source in source_dir.rglob("*"):
        resolved = _resolved(source)
        if any(resolved == item or item in resolved.parents for item in excluded_paths):
            continue
        if source.is_file():
            relative = source.relative_to(source_dir)
            target = destination_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _write_manifest(staging_dir: Path, db_source: Path, created_at: datetime) -> None:
    files = []
    for path in sorted(item for item in staging_dir.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(staging_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    manifest = {
        "format_version": 2,
        "application": "Wingmarz",
        "created_at_utc": created_at.isoformat(),
        "database": {
            "engine": "sqlite",
            "source_name": db_source.name,
            "snapshot_path": "database/bot_database.db",
            "password": None,
        },
        "files": files,
    }
    (staging_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _create_encrypted_zip(staging_dir: Path, destination: Path, password: str) -> None:
    with pyzipper.AESZipFile(
        destination, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for path in sorted(item for item in staging_dir.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(staging_dir).as_posix())


def _prune_backups(output_dir: Path, keep: int) -> None:
    keep = max(1, int(keep))
    backups = sorted(
        output_dir.glob("wingmarz-backup-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[keep:]:
        old_backup.unlink(missing_ok=True)


def _create_backup_sync() -> BackupArtifact:
    db_path = _database_path()
    output_dir = _backup_dir(db_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    timestamp = created_at.strftime("%Y%m%d-%H%M%S-%f")
    destination = output_dir / f"wingmarz-backup-{timestamp}.zip"
    configured_password = str(getattr(config, "BACKUP_ZIP_PASSWORD", "") or "").strip()
    password = configured_password or secrets.token_urlsafe(18)

    with tempfile.TemporaryDirectory(prefix="wingmarz-backup-") as temporary:
        staging = Path(temporary)
        snapshot = staging / "database" / "bot_database.db"
        _sqlite_snapshot(db_path, snapshot)

        settings_dir = staging / "configuration"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.env").write_text(_settings_text(), encoding="utf-8")
        _copy_optional_file(Path.cwd() / ".env", settings_dir / "original.env")
        for filename in ("config.py", "docker-compose.yml", "Dockerfile", "requirements.txt"):
            _copy_optional_file(Path.cwd() / filename, settings_dir / filename)

        data_dir = db_path.parent
        if data_dir.name.lower() in {"data", "storage"}:
            _copy_runtime_data(data_dir, staging / "runtime-data", excluded=(db_path, output_dir))

        if bool(getattr(config, "BACKUP_INCLUDE_LOGS", True)):
            _copy_optional_file(Path.cwd() / "bot.log", staging / "logs" / "bot.log")
            logs_dir = Path.cwd() / "logs"
            if logs_dir.is_dir():
                _copy_runtime_data(logs_dir, staging / "logs", excluded=())

        _write_manifest(staging, db_path, created_at)
        _create_encrypted_zip(staging, destination, password)

    _prune_backups(output_dir, getattr(config, "BACKUP_RETENTION_COUNT", 24))
    return BackupArtifact(
        path=destination,
        zip_password=password,
        database_password=None,
        database_engine="SQLite",
        created_at=created_at,
        size_bytes=destination.stat().st_size,
    )


async def create_backup_zip() -> BackupArtifact:
    """Create an AES-256 archive containing a consistent DB snapshot and app settings."""
    return await asyncio.to_thread(_create_backup_sync)


def _select_database_member(names: list[str]) -> str:
    preferred = "database/bot_database.db"
    if preferred in names:
        return preferred
    expected_name = Path(config.DATABASE_PATH).name
    candidates = [name for name in names if Path(name).name in {"bot_database.db", expected_name}]
    if not candidates:
        raise ValueError("Database file was not found in this backup")
    return candidates[0]


def _restore_database_sync(archive_path: Path, destination: Path, password: str | None) -> Path:
    destination = _resolved(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.restore-{secrets.token_hex(4)}")
    try:
        with pyzipper.AESZipFile(archive_path, "r") as archive:
            if password:
                archive.setpassword(password.encode("utf-8"))
            member = _select_database_member(archive.namelist())
            with archive.open(member) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        with sqlite3.connect(temporary) as restored:
            result = restored.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError(f"Restored SQLite database failed integrity check: {result}")
        previous = destination.with_suffix(destination.suffix + ".bak")
        if destination.exists():
            shutil.copy2(destination, previous)
        os.replace(temporary, destination)
        return previous
    finally:
        temporary.unlink(missing_ok=True)


async def restore_database_from_zip(
    archive_path: str | Path, destination: str | Path, password: str | None
) -> Path:
    """Restore a validated SQLite snapshot and keep the previous DB as ``.bak``."""
    return await asyncio.to_thread(
        _restore_database_sync, Path(archive_path), Path(destination), password
    )
