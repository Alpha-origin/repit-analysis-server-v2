from __future__ import annotations

CATEGORY_HINTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "DATA_ANALYST": (
        ("ANALYSIS_REPORT", ("hypothesis", "data_prep", "method", "interpretation", "impact")),
        ("COMPETITION_SUMMARY", ("method", "interpretation")),
    ),
    "DBA_INFRA": (
        ("INCIDENT_REPORT", ("incident", "operations")),
        ("TABLE_SPEC", ("design", "performance")),
        ("SYSTEM_ARCHITECTURE", ("design", "operations")),
    ),
    "QA_ENGINEER": (
        ("TEST_PLAN", ("quality_process", "release_judgment")),
        ("TEST_CASE", ("test_design",)),
        ("BUG_REPORT", ("defect_reporting",)),
    ),
    "SECURITY": (
        ("VULNERABILITY_REPORT", ("vuln_principle", "analysis_process", "mitigation")),
        ("CTF_WRITEUP", ("vuln_principle", "analysis_process")),
    ),
    "PRODUCT_MANAGER": (
        ("PRD", ("problem_definition", "prioritization", "metrics")),
        ("POLICY_DOC", ("problem_definition", "prioritization")),
        ("REQUIREMENT_SPEC", ("prioritization",)),
        ("SCREEN_SPEC", ("problem_definition",)),
        ("SUPPLEMENT", ("collaboration", "retrospective", "metrics")),
    ),
    "IT_CONSULTANT": (
        ("PROPOSAL", ("requirement_analysis", "solution_design", "client_communication")),
        ("PROJECT_REPORT", ("delivery", "outcome")),
        ("REQUIREMENT_SPEC", ("requirement_analysis",)),
        ("SUPPLEMENT", ("client_communication", "delivery", "outcome")),
    ),
}

# 01 §3.2 / 04 §2.1: prompts use the same labels, descriptions and document hints.
CATEGORY_DETAILS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "DATA_ANALYST": (
        ("hypothesis", "문제·가설", "풀려던 문제, 세운 가설, 지표 정의"),
        ("data_prep", "수집·전처리", "데이터 출처, 품질 문제, 정제·결측·이상치 처리 선택"),
        ("method", "분석 방법", "고른 분석·모델링 방법과 대안, 검증 방식"),
        ("interpretation", "해석·한계", "결과 해석, 통계적 한계, 편향"),
        ("impact", "의사결정 연결", "결과가 어떤 결정·행동으로 이어졌는지"),
    ),
    "DBA_INFRA": (
        ("design", "설계", "DB·인프라 구조 설계와 선택 이유"),
        ("performance", "성능 개선", "병목 진단과 튜닝(쿼리·인덱스·용량)"),
        ("operations", "운영·모니터링", "백업·복구, 모니터링, 운영 절차"),
        ("incident", "장애 대응", "장애 원인 분석, 조치, 재발 방지"),
        ("automation", "배포·자동화", "IaC, 배포 파이프라인, 반복 작업 자동화"),
    ),
    "QA_ENGINEER": (
        ("test_design", "테스트 설계", "범위·케이스 설계 기법(경계값·예외), 커버리지"),
        ("defect_reporting", "결함 보고", "재현 절차, 심각도 판단, 개발자와의 소통"),
        ("release_judgment", "출시 판단", "종료 기준, 위험 기반 출시 결정"),
        ("automation", "테스트 자동화", "자동화 대상 선정, 도구, 유지보수"),
        ("quality_process", "품질 프로세스", "테스트 계획, 품질 지표, 프로세스 개선"),
    ),
    "SECURITY": (
        ("vuln_principle", "취약점 원리", "취약점의 원리와 발생 조건"),
        ("analysis_process", "분석 과정", "탐색·재현·검증 절차와 도구"),
        ("mitigation", "대응·완화", "수정·완화 방안과 우선순위"),
        ("incident_response", "사고 대응", "탐지·대응 절차, 영향 범위 판단"),
        ("ethics_law", "윤리·법", "책임 있는 공개, 권한 범위, 관련 법·규정"),
    ),
    "PRODUCT_MANAGER": (
        ("problem_definition", "문제 정의", "사용자 문제와 목표 설정"),
        ("prioritization", "우선순위", "요구사항·기능 우선순위 기준"),
        ("metrics", "지표", "성과 지표 설계와 측정"),
        ("collaboration", "협업", "개발·디자인·이해관계자 조율"),
        ("retrospective", "회고", "결과 검증, 개선, 실패에서 배운 점"),
    ),
    "IT_CONSULTANT": (
        ("requirement_analysis", "요구사항 분석", "고객 과제·현황 분석과 요구 도출"),
        ("solution_design", "해결안 설계", "해결안 구성과 대안 비교"),
        ("client_communication", "고객 커뮤니케이션", "제안·설명·설득, 기대치 관리"),
        ("delivery", "수행·납품", "일정·범위·위험 관리, 실행"),
        ("outcome", "성과", "결과 검증, 고객 가치·효과"),
    ),
}


CATEGORY_DETAILS.update(
    {
        "GEN_STRATEGY": (
            ("market_analysis", "시장·환경 분석", "시장·경쟁·내부 현황을 어떤 자료로 어떻게 분석했는지"),
            ("business_plan", "사업 계획", "목표 설정, 계획의 근거, 검토한 대안"),
            ("budget_resource", "예산·자원 관리", "예산·인력 배분 기준과 조정"),
            ("execution", "실행 관리", "일정·이슈 관리, 부서 간 조율"),
            ("performance", "성과 관리", "지표 설정, 성과 점검과 후속 조치"),
        ),
        "GEN_HR_ADMIN": (
            ("recruiting", "채용·배치", "채용 기준·과정 설계, 배치 판단"),
            ("evaluation_reward", "평가·보상", "평가 제도 운영, 보상 기준"),
            ("training_culture", "교육·조직문화", "교육 기획, 조직문화 활동과 효과"),
            ("labor_policy", "노무·규정", "근로 관련 규정 적용, 분쟁·민원 대응"),
            ("admin_support", "총무·운영 지원", "자산·시설·계약 등 운영 지원 업무와 개선"),
        ),
        "GEN_FINANCE": (
            ("closing", "결산·회계 처리", "결산 절차, 회계 처리 판단"),
            ("tax_compliance", "세무·법규", "세무 신고, 관련 기준·법규 적용"),
            ("budget_cost", "예산·원가 관리", "예산 편성·통제, 원가 분석"),
            ("financial_analysis", "재무 분석·보고", "재무제표 분석, 보고서 작성과 해석"),
            ("treasury_risk", "자금·리스크 관리", "자금 운용, 리스크 식별과 대응"),
        ),
        "GEN_MARKETING": (
            ("market_customer", "시장·고객 분석", "타깃 정의, 고객·시장 조사 방법"),
            ("campaign_planning", "캠페인 기획", "목표·메시지·예산 설계와 근거"),
            ("channel_execution", "채널·콘텐츠 실행", "채널 선택, 콘텐츠 제작·운영"),
            ("performance_measure", "성과 측정", "지표 정의, 측정 방법, 결과 해석과 개선"),
            ("brand_comms", "브랜드·대외 커뮤니케이션", "브랜드 일관성, 언론·대외 대응"),
        ),
        "GEN_SALES": (
            ("prospecting", "고객 발굴", "잠재 고객을 찾고 우선순위를 정한 방법"),
            ("needs_proposal", "니즈 파악·제안", "고객 요구 파악, 제안 구성"),
            ("negotiation", "협상·계약", "조건 협상, 계약 과정의 판단"),
            ("account_management", "고객 관계 관리", "기존 고객 유지, 불만·이슈 대응"),
            ("sales_performance", "실적 관리", "목표 대비 실적 관리, 부진 원인과 조치"),
        ),
        "GEN_DESIGN": (
            ("research", "리서치·문제 정의", "사용자·시장 조사, 디자인 문제 정의"),
            ("concept", "콘셉트·아이디어", "콘셉트 도출 과정, 검토한 아이디어"),
            ("design_decision", "디자인 결정", "최종안을 고른 기준과 근거(미적 완성도가 아니라 판단)"),
            ("collaboration", "협업·피드백", "기획·개발·클라이언트와의 협업, 피드백 반영"),
            ("validation", "결과 검증", "사용성 검증, 결과 효과 확인"),
        ),
    }
)
