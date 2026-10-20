"""Measured run counters; fixture/live metrics never mix silently."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def summarize(file: Path) -> dict[str, object]:
    rows: list[dict[str, Any]] = json.loads(file.read_text(encoding="utf-8"))
    modes = {row.get("mode", "LIVE") for row in rows}
    counts = Counter(str(row["status"]) for row in rows)
    return {
        "mode": next(iter(modes)) if len(modes) == 1 else "MIXED",
        "companies": len(rows),
        "statuses": dict(counts),
        "documentVersions": sum(len(r.get("collection", {}).get("documents", [])) for r in rows),
        "generatedDrafts": sum("factCount" in r for r in rows),
        "draftFacts": sum(r.get("factCount", 0) for r in rows),
        "modelRequestsStarted": sum(r.get("modelRequestsStarted", 0) for r in rows),
        "recordedInputTokens": sum(r.get("usage", {}).get("inputTokens", 0) for r in rows),
        "recordedOutputTokens": sum(r.get("usage", {}).get("outputTokens", 0) for r in rows),
        "companyElapsedSeconds": sum(r.get("elapsedSeconds", 0) for r in rows),
        "note": "카운터는 실행 기록이다. SAMPLE_DEMO는 가상 예제이며 실제 수집·실모델 성능으로 해석하지 않는다. "
        "원문 구절 일치 검사는 의미 정확도 측정이 아니며 실제 검수 완료를 뜻하지 않는다.",
    }
