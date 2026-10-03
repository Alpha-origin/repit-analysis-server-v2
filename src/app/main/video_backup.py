"""Make and verify a private, consistent SQLite backup without copying live WAL files."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from app.main.config import load_callback_security_settings
from app.main.video_config import VideoSettings


def backup_database(source: Path, destination: Path) -> None:
    if not source.is_file() or source.resolve() == destination.resolve():
        raise ValueError("backup requires an existing source and a distinct destination")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation prevents overwriting an earlier backup and fixes permissions before SQLite opens it.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with (
            sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as original,
            sqlite3.connect(destination) as copy,
        ):
            original.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    load_callback_security_settings()
    settings = VideoSettings()
    backup_database(settings.database_path, args.destination)


if __name__ == "__main__":
    main()
