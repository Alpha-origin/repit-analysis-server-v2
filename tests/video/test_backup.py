from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.main.video_backup import backup_database
from tests.video.helpers import repository, submit


def test_backup_includes_live_wal_and_restores_private_database(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    destination = tmp_path / "backups" / "copy.sqlite3"
    with sqlite3.connect(repo.path) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("UPDATE video_tasks SET attempts=1")
        source.commit()
        backup_database(repo.path, destination)
    assert destination.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(destination) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT id FROM video_jobs").fetchone()[0] == job_id
        assert restored.execute("SELECT SUM(attempts) FROM video_tasks").fetchone()[0] == 5
    with pytest.raises(FileExistsError):
        backup_database(repo.path, destination)
    with pytest.raises(ValueError, match="distinct"):
        backup_database(repo.path, repo.path)
