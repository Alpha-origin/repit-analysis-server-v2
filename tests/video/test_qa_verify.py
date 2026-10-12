from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.video import qa_verify


@pytest.mark.parametrize("state", ["clean", "unstaged", "staged"])
def test_verification_identifies_changes_relative_to_recorded_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    git = shutil.which("git")
    assert git is not None

    def run_git(*arguments: str) -> str:
        result = subprocess.run(  # noqa: S603 - resolved git and fixed commands in this test
            [git, *arguments], cwd=tmp_path, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    run_git("init")
    source = tmp_path / "source.py"
    source.write_text("original\n")
    run_git("add", "source.py")
    run_git(
        "-c",
        "user.name=QA",
        "-c",
        "user.email=qa@example.com",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "baseline",
    )
    commit = run_git("rev-parse", "HEAD")
    if state != "clean":
        source.write_text("changed\n")
    if state == "staged":
        run_git("add", "source.py")
    monkeypatch.setattr(qa_verify, "ROOT", tmp_path)
    monkeypatch.setattr(
        qa_verify,
        "verification_steps",
        lambda evidence: [("probe", [sys.executable, "-c", "print('verification probe')"])],
    )
    evidence = tmp_path / "evidence"
    report = qa_verify.verify(tmp_path, evidence)
    assert report["commit"] == commit
    assert report["trackedChanges"] is (state != "clean")
    assert report["result"] == "PASS"
    assert json.loads((evidence / "verification.json").read_text()) == report
    assert (evidence / "probe.log").read_text() == "verification probe\n"
