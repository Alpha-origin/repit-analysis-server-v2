"""Child process that dies (os._exit) at a precise point. Used only by test_crash_recovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.core.common.video.identity import request_fingerprint
from app.core.common.video.terminal import failed_callback
from tests.video.helpers import FakeClock, admit_for, repository, video_request


def main(scenario: str, directory: str) -> None:
    root = Path(directory)
    clock = FakeClock(float(os.environ.get("CRASH_CLOCK", "1800000000")))
    repo = repository(root, clock)
    request = video_request()
    if scenario == "after-accept-commit":
        repo.submit(request, request_fingerprint(request), admit_for(root))
        os._exit(17)  # the 202 never leaves the process
    if scenario == "after-callback-reservation":
        claim = repo.claim_callback(1, 60)
        assert claim is not None
        os._exit(17)
    task = repo.claim_stage({"io": 1, "cpu": 1}, 60)
    if task is None:
        sys.exit(3)
    if scenario == "after-temp-write":
        reservation = repo.reserve_artifact(task, "source")
        assert reservation is not None
        Path(reservation.temp_path).write_bytes(b"partial-download")
        os._exit(17)
    if scenario == "after-rename-before-commit":
        reservation = repo.reserve_artifact(task, "source")
        assert reservation is not None
        Path(reservation.temp_path).write_bytes(b"complete-download")
        with repo._write() as connection:
            Path(reservation.temp_path).replace(reservation.final_path)
            connection.execute("UPDATE video_artifacts SET state='published' WHERE id=?", (reservation.id,))
            os._exit(17)  # dies inside the transaction: SQLite rolls it back
    if scenario == "inside-terminal-commit":
        for _ in range(4):
            repo.block_stage(task, "SOURCE_NOT_FOUND")
            task = repo.claim_stage({"io": 1, "cpu": 1}, 60)
            assert task is not None
        body = failed_callback(task.job_id, request)
        with repo._write() as connection:
            repo._terminalize(connection, task.job_id, body, clock.now())
            os._exit(17)
    sys.exit(4)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
