"""Regression: systemd Wingmarz must not open a Git-tracked log at import."""
from pathlib import Path


def test_bot_logs_to_journald_without_root_owned_file_dependency():
    source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
    assert "logging.StreamHandler(sys.stdout)" in source
    assert "logging.FileHandler(" not in source
