"""Repeat local F1/F2 gates and F3 real HTTP QA; preserve fresh evidence for a separate reviewer.

This script is verification automation, not an independent review or an external deployment test.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.core.common.security.redaction import redact_text

ROOT = Path(__file__).resolve().parents[2]


def verification_steps(evidence: Path) -> list[tuple[str, list[str]]]:
    python = sys.executable
    steps = [
        ("ruff-check", [python, "-m", "ruff", "check", "--no-fix", "src", "tests"]),
        ("ruff-format", [python, "-m", "ruff", "format", "--check", "src", "tests"]),
        ("mypy", [python, "-m", "mypy", "src", "tests"]),
        ("architecture", [str(ROOT / ".venv/bin/lint-imports")]),
        ("pytest", [python, "-m", "pytest", "tests", "-q", "--color=no"]),
    ]
    steps.extend(
        (
            f"http-{scenario}",
            [
                python,
                "-m",
                "tests.video.qa_scenario",
                "--scenario",
                scenario,
                "--require-real-media",
                "--evidence-dir",
                str(evidence / scenario),
            ],
        )
        for scenario in ("default", "callback-failure", "expired-job")
    )
    return steps


def scrub(output: str, environment: dict[str, str]) -> str:
    for name in ("VIDEO_API_TOKEN", "APP_INTERNAL_CALLBACK_TOKEN", "ANTHROPIC_API_KEY", "QA_RECEIVER_TOKEN"):
        secret = environment.get(name, "")
        if secret:
            output = output.replace(secret, "[redacted]")
    return redact_text(output)


def verify(browser_dir: Path, evidence: Path) -> dict[str, Any]:
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "VIDEO_REAL_MEDIA_REQUIRED": "1",
        "VIDEO_BROWSER_FIXTURES_DIR": str(browser_dir.resolve()),
    }
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required")
    commit = subprocess.run(  # noqa: S603 - git is resolved on PATH and arguments are fixed
        [git, "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    dirty = (
        subprocess.run(  # noqa: S603 - fixed read-only git command
            [git, "diff", "--quiet", "HEAD"], cwd=ROOT, check=False
        ).returncode
        != 0
    )
    report: dict[str, Any] = {
        "commit": commit.stdout.strip(),
        "trackedChanges": dirty,
        "python": sys.version.split()[0],
        "steps": [],
        "independentReview": "pending",
        "externalDeployment": "deferred",
    }
    for name, command in verification_steps(evidence):
        started = time.monotonic()
        try:
            result = subprocess.run(  # noqa: S603 - fixed commands from verification_steps
                command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=600, check=False
            )
            code, output = result.returncode, result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired):
            code, output = 1, "verification tool failed or exceeded its deadline"
        log = evidence / f"{name}.log"
        log.write_text(scrub(output, environment))
        log.chmod(0o600)
        report["steps"].append({"name": name, "exitCode": code, "seconds": round(time.monotonic() - started, 2)})
    report["result"] = "PASS" if all(item["exitCode"] == 0 for item in report["steps"]) else "FAIL"
    destination = evidence / "verification.json"
    destination.write_text(json.dumps(report, indent=2))
    destination.chmod(0o600)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.browser_dir, args.evidence_dir)
    print(json.dumps({"result": report["result"], "steps": report["steps"]}))  # noqa: T201
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
