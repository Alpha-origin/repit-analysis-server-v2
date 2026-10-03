"""Run the handoff's six scenarios against real HTTPS services, using a private manifest.

No S3 or receiver is substituted by this CLI. The operator supplies preuploaded immutable S3 URLs,
expired URLs, receiver fault-control/observation endpoints and legacy requests in the manifest.
Missing inputs fail with exit 2. Requests, tokens, exception text and signed URLs are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import string
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.common.security.redaction import redact_text
from app.core.common.security.url_policy import parse_https_url

SCENARIOS = {"valid", "replay", "expired-source", "size-mismatch", "callback-failure", "legacy-callbacks"}
LEGACY_PATHS = {
    "/generate",
    "/questions/tailor",
    "/questions/tailor/multi",
    "/feedback/solo",
    "/feedback/multi",
    "/analysis/audio",
}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    target: Literal["analysis", "receiver"]
    method: Literal["GET", "POST"] = "GET"
    path: str
    body: Any = None
    authenticated: bool = True
    expected_status: int = Field(ge=100, le=599)
    # Dot paths select response JSON. "$name.path" refers to a previously saved response.
    expected: dict[str, Any] = Field(min_length=1)
    minimum: dict[str, float] = Field(default_factory=dict)
    save_as: str | None = None
    poll_seconds: int = Field(default=0, ge=0, le=1200)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str
    steps: list[Step] = Field(min_length=1)
    cleanup: list[Step] = Field(default_factory=list)


class ExternalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    analysis_base_url: str
    receiver_base_url: str
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def complete(self) -> ExternalSuite:
        if len(self.scenarios) != len(SCENARIOS) or {item.name for item in self.scenarios} != SCENARIOS:
            raise ValueError("all six handoff scenarios are required, once each")
        for base in (self.analysis_base_url, self.receiver_base_url):
            parsed = parse_https_url(base)
            if parsed.query:
                raise ValueError("service base URLs cannot contain credentials or queries")
        legacy = next(item for item in self.scenarios if item.name == "legacy-callbacks")
        paths = {step.path for step in legacy.steps if step.method == "POST" and step.target == "analysis"}
        if not paths >= LEGACY_PATHS:
            raise ValueError("legacy scenario requires five text routes and the audio route")
        return self


class ExternalQaError(AssertionError):
    pass


def value_at(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def resolve(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return value_at(saved, value[1:])
    if isinstance(value, list):
        return [resolve(item, saved) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, saved) for key, item in value.items()}
    return value


def meets_minimum(value: Any, minimum: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= minimum


def safe_summary(value: Any, tokens: dict[str, str]) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    for token in tokens.values():
        if token:
            serialized = serialized.replace(json.dumps(token, ensure_ascii=False)[1:-1], "[redacted]")
    return json.loads(redact_text(serialized))


async def run_step(
    client: httpx.AsyncClient, suite: ExternalSuite, step: Step, saved: dict[str, Any], tokens: dict[str, str]
) -> dict[str, Any]:
    base = suite.analysis_base_url if step.target == "analysis" else suite.receiver_base_url
    # Paths can interpolate saved scalar values as {name.path}; never interpolate the host or token.
    path = "".join(
        literal + (str(value_at(saved, field)) if field else "")
        for literal, field, _, _ in string.Formatter().parse(step.path)
    )
    if not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc or "\\" in path:
        raise ExternalQaError("invalid step path")
    token = tokens.get(step.target, "")
    if step.authenticated and not token:
        raise ExternalQaError("missing authentication configuration")
    headers = {"X-Internal-Token": token} if step.authenticated else {}
    expected = resolve(step.expected, saved)
    deadline = time.monotonic() + step.poll_seconds
    while True:
        response = await client.request(
            step.method,
            base.rstrip("/") + path,
            json=resolve(step.body, saved) if step.method == "POST" else None,
            headers=headers,
        )
        data: Any = None
        try:
            data = response.json()
            matches = (
                response.status_code == step.expected_status
                and all(value_at(data, key) == value for key, value in expected.items())
                and all(meets_minimum(value_at(data, key), minimum) for key, minimum in step.minimum.items())
            )
        except (ValueError, KeyError, IndexError, TypeError):
            matches = False
        if matches:
            if step.save_as:
                saved[step.save_as] = data
            return {"target": step.target, "httpStatus": response.status_code, "response": safe_summary(data, tokens)}
        if time.monotonic() >= deadline or step.method != "GET":
            raise ExternalQaError("response did not match the required contract")
        await asyncio.sleep(0.5)


async def run_suite(suite: ExternalSuite, tokens: dict[str, str], client: httpx.AsyncClient) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    report: dict[str, Any] = {"result": "PASS", "runId": run_id, "externalServicesSubstituted": False, "scenarios": []}
    names = [
        "valid",
        "expired",
        "recovery",
        "mismatch",
        "callback503",
        "callback401",
        "generate",
        "tailor",
        "tailorMulti",
        "feedbackSolo",
        "feedbackMulti",
        "audio",
        "session",
    ]
    saved: dict[str, Any] = {"run": {name: f"e2e-{name}-{run_id}" for name in names}}
    for scenario in suite.scenarios:
        item: dict[str, Any] = {"name": scenario.name, "result": "PASS", "steps": []}
        try:
            for step in scenario.steps:
                item["steps"].append(await run_step(client, suite, step, saved, tokens))
        except (ExternalQaError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            item["result"] = "FAIL"
            report["result"] = "FAIL"
        finally:
            for step in scenario.cleanup:
                try:
                    await run_step(client, suite, step, saved, tokens)
                except (ExternalQaError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    item["result"] = "FAIL"
                    report["result"] = "FAIL"
                    item["cleanupFailed"] = True
        report["scenarios"].append(item)
    return report


async def execute(manifest: Path) -> dict[str, Any]:
    suite = ExternalSuite.model_validate_json(manifest.read_text())
    tokens = {
        "analysis": os.environ.get("VIDEO_API_TOKEN", ""),
        "receiver": os.environ.get("QA_RECEIVER_TOKEN", ""),
        "callback": os.environ.get("APP_INTERNAL_CALLBACK_TOKEN", ""),
    }
    if not tokens["analysis"] or not tokens["receiver"] or not tokens["callback"]:
        raise ExternalQaError("required tokens are missing")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        return await run_suite(suite, tokens, client)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(execute(args.manifest))
    except (OSError, ValidationError, ExternalQaError, ValueError):
        report = {"result": "BLOCKED", "reason": "invalid or missing private manifest/configuration"}
    args.evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = args.evidence_dir / "external-qa.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    output.chmod(0o600)
    print(json.dumps({"result": report["result"]}))  # noqa: T201
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
