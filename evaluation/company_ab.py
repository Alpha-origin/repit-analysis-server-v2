"""기업 없음/A/B 재작성 결과를 비교한다 실제 Claude API를 쓰는 수동 실행 도구다."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import Field

from app.core.common.company.dto import CompanyContext
from app.core.common.dto import CamelModel
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion, TailoredQuestion
from app.core.common.question_tailor.rewrite import QuestionRewrite
from app.inbound.http.company_dto import CompanyContextRequest
from app.inbound.http.question_tailor.dto import CandidateProfileRequest, OriginalQuestionRequest
from app.main.config import load_anthropic_settings, load_question_tailor_settings
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl


class ComparisonInput(CamelModel):
    profile: CandidateProfileRequest
    questions: list[OriginalQuestionRequest] = Field(..., min_length=5, max_length=5)


def load_company_package(path: Path) -> CompanyContext:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1:
        raise ValueError(f"지원하지 않는 schemaVersion: {path}")
    return CompanyContextRequest.model_validate(
        {
            "companyId": raw["code"],
            "displayName": raw["displayName"],
            "category": raw["category"],
            "facts": [dict(fact, factId=f"f{index}") for index, fact in enumerate(raw["facts"], 1)],
            "interviewerBrief": raw["interviewerBrief"],
        }
    ).to_core()


def _cell(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("\n", "<br>")
    )


def build_report(
    questions: tuple[OriginalQuestion, ...],
    companies: list[CompanyContext],
    results: list[tuple[TailoredQuestion, ...] | None],
) -> str:
    labels = ["기업 없음", *(company.display_name for company in companies)]
    lines = [
        "# 기업 컨텍스트 재작성 비교",
        "",
        "| 원질문 | " + " | ".join(map(_cell, labels)) + " |",
        "| --- | --- | --- | --- |",
    ]
    maps = [None if result is None else {item.id: item.question for item in result} for result in results]
    for question in questions:
        cells = [_cell(question.question)]
        cells.extend(
            _cell(result[question.id]) if result is not None else "재작성 실패 · 원질문 폴백" for result in maps
        )
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "회사 정보는 질문의 관점에만 사용하며 원질문의 검증 목표를 유지한다", ""])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    data = ComparisonInput.model_validate_json(args.questions.read_text(encoding="utf-8"))
    profile = CandidateProfile.model_validate(data.profile.model_dump())
    if not profile.has_any:
        raise ValueError("지원자 사전 정보가 최소 1개 필요합니다")
    questions = tuple(OriginalQuestion.model_validate(item.model_dump()) for item in data.questions)
    if len({question.id for question in questions}) != len(questions):
        raise ValueError("원질문 id는 서로 달라야 합니다")
    companies = [load_company_package(path) for path in args.companies]
    anthropic = load_anthropic_settings()
    settings = load_question_tailor_settings()
    client = AnthropicTextClientImpl(anthropic.API_KEY)
    try:
        rewrite = QuestionRewrite(
            client,
            anthropic.TEXT_MODEL,
            settings.REWRITE_MAX_TOKENS,
            settings.QUESTION_MAX_CHARS,
            settings.SPOKEN_TEXT_MAX_CHARS,
        )
        results = [await rewrite.execute(profile, questions, company) for company in [None, *companies]]
    finally:
        await client.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(questions, companies, results), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--companies", type=Path, nargs=2, required=True, metavar=("A", "B"))
    parser.add_argument("--out", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
