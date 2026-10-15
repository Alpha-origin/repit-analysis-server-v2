from app.core.common.company.dto import CompanyFact
from app.core.common.company.prompt import build_company_block
from tests.company_helpers import company_context


def test_block_uses_fixed_kind_order_and_titles() -> None:
    company = company_context()
    company = company.model_copy(
        update={
            "facts": (
                *reversed(company.facts),
                CompanyFact(fact_id="tech", kind="TECH_STACK", title="서버", content="실시간 처리"),
                CompanyFact(fact_id="mission", kind="MISSION_VISION", title="연결", content="사람을 연결한다"),
            )
        }
    )
    lines = build_company_block(company)
    assert lines == [
        "[기업 정보] 샘플커머스 (커머스·모빌리티)",
        "- 주력 사업·서비스: 샘플마켓 — 온라인 거래 서비스",
        "- 공개 사례의 문제: 재고 초과 판매 — 동시 주문 처리 사례",
        "- 인재상·핵심 가치: 오너십 — 결과까지 책임진다",
        "- 미션·비전: 연결 — 사람을 연결한다",
        "- 기술 스택: 서버 — 실시간 처리",
    ]


def test_filter_ids_and_signals_are_opt_in() -> None:
    lines = build_company_block(company_context(), ("TALENT",), include_ids=True, include_signals=True)
    assert lines == [
        "[기업 정보] 샘플커머스 (커머스·모빌리티)",
        "- [f:t1] 인재상·핵심 가치: 오너십 — 결과까지 책임진다",
        "  - 드러나면 좋은 모습: 문제를 직접 정의한 경험",
        "  - 우려되는 모습: 책임을 돌린 경험",
    ]
    assert build_company_block(company_context(), ()) == [lines[0]]


def test_unknown_category_is_preserved() -> None:
    company = company_context().model_copy(update={"category": "NEW"})
    assert build_company_block(company)[0] == "[기업 정보] 샘플커머스 (NEW)"
