"""피드백 부하 실험의 콜백을 JSONL로 저장한다.

FEEDBACK_CAPTURE_TOKEN과 FEEDBACK_CAPTURE_OUTPUT을 설정한 뒤 TLS를 적용해 실행한다.
대상 분석 서버의 APP_INTERNAL_CALLBACK_TOKEN 및 콜백 허용 호스트 설정과 일치해야 한다.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()
_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/capture")
async def capture(payload: dict[str, Any], x_internal_token: str | None = Header(default=None)) -> dict[str, str]:
    expected = os.environ.get("FEEDBACK_CAPTURE_TOKEN")
    output_name = os.environ.get("FEEDBACK_CAPTURE_OUTPUT")
    if not expected or not output_name:
        raise HTTPException(status_code=503, detail="collector 미설정")
    if x_internal_token is None or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="인증 실패")
    if not isinstance(payload.get("jobId"), str) or not isinstance(payload.get("sessionId"), str):
        raise HTTPException(status_code=422, detail="jobId/sessionId 누락")
    record = {"received_at_epoch": time.time(), "payload": payload}
    destination = Path(output_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with _lock:
        with destination.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "received"}
