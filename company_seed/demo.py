"""Network/key-free SAMPLE demonstration. No real company or LLM result is claimed."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx

from company_seed.collection import Collector
from company_seed.documents import CompanySeed, Document, Seed
from company_seed.extraction import Evidence, Extraction, GroundedFact
from company_seed.schema import Fact

COMPANY = CompanySeed(
    code="sample-pipeline",
    display_name="파이프라인 샘플",
    legal_name="주식회사 데모기업",
    category="PLATFORM",
    boundary_note="가상 SAMPLE 자료와 결정적 모델 대역. 실제 기업·실모델 성능이 아님.",
    allowed_hosts=["example.invalid"],
    sources=[Seed(url="https://example.invalid/company", source_type="ABOUT_PAGE", scope="가상 기업의 데모 문서")],
)
BODY = """<html><title>SAMPLE 파이프라인 데모</title><article>
<p>주식회사 데모기업은 가상의 예시 기업이다. 샘플 주문은 구매자와 판매자에게 주문 접수 기능을 제공하며,
샘플 지도는 이용자가 주변 장소를 탐색하도록 돕는 가상 서비스다.</p>
<p>데모기업의 협업 가치는 동료와 판단 근거를 공유하고 서로의 의견을 검토하는 것이다.
이 문서는 자동 수집과 근거 연결을 재현하기 위한 예제이며 실제 회사 정보를 담고 있지 않다.</p>
</article></html>"""


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /")
    return httpx.Response(200, text=BODY, headers={"content-type": "text/html"})


class DemoCollector(Collector):
    async def read_seed(
        self,
        seed: Seed,
        company: CompanySeed,
        today: date,
    ) -> tuple[Document | None, list[str], bool]:
        document, links, discovery = await super().read_seed(seed, company, today)
        if document:
            document.source.source_type = "SAMPLE"
            document.acquisition = "fixture"
        return document, links, discovery


class DemoExtractor:
    model = "fixture:sample-demo-v1"

    async def extract(self, company: CompanySeed, documents: list[Document]) -> Extraction:
        doc = documents[0]
        source = doc.source.source_key

        def evidence(quote: str) -> Evidence:
            passage = next(p for p in doc.passages if quote in p.text)
            return Evidence(source_key=source, passage_id=passage.passage_id, quote=quote)

        evidence_order = evidence("샘플 주문은 구매자와 판매자에게 주문 접수 기능을 제공하며,")
        evidence_map = evidence("샘플 지도는 이용자가 주변 장소를 탐색하도록 돕는 가상 서비스다.")
        evidence_talent = evidence("동료와 판단 근거를 공유하고 서로의 의견을 검토하는 것이다.")
        return Extraction(
            legal_name=company.legal_name or "주식회사 데모기업",
            facts=[
                GroundedFact(
                    fact=Fact(
                        kind="BUSINESS",
                        title="샘플 주문",
                        content="구매자와 판매자에게 주문 접수 기능을 제공한다.",
                        source_key=source,
                    ),
                    evidence=evidence_order,
                ),
                GroundedFact(
                    fact=Fact(
                        kind="BUSINESS",
                        title="샘플 지도",
                        content="이용자가 주변 장소를 탐색하도록 돕는다.",
                        source_key=source,
                    ),
                    evidence=evidence_map,
                ),
                GroundedFact(
                    fact=Fact(
                        kind="TALENT",
                        title="협업",
                        content="동료와 판단 근거를 공유하고 의견을 검토한다.",
                        source_key=source,
                    ),
                    evidence=evidence_talent,
                ),
            ],
            interviewer_brief="가상 SAMPLE 기업은 주문 접수와 주변 장소 탐색 서비스를 제공하며 협업을 강조한다.",
        )


async def run_demo(output: Path, *, resume: bool = False) -> int:
    from company_seed.pipeline import execute  # noqa: PLC0415 -- avoid orchestration import cycle

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await execute(
            "run",
            [COMPANY],
            output / "cache",
            output,
            DemoCollector(client, delay=0),
            DemoExtractor(),
            resume=resume,
            demo=True,
        )
