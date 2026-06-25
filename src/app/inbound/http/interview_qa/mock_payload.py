# ruff: noqa: E501

from __future__ import annotations

from typing import Any

MOCK_RESULT: dict[str, Any] = {
    "project_summary": {
        "overview": (
            "StartHub는 스타트업 생존율 향상을 위한 AI 기반 통합 지원 플랫폼으로, "
            "Kotlin/Spring Boot 백엔드와 FastAPI 마이크로서비스로 구성되어 있습니다. "
            "RAG 시스템을 통한 맞춤형 공고 추천, 사용자 활동 기반 AI 챗봇, "
            "Claude API 기반 비즈니스 모델 캔버스 생성, 경쟁사 분석 등의 LLM 기반 기능을 제공합니다. "
            "트래픽 증가에 대응하기 위해 Kotlin Coroutine과 WebFlux를 활용한 Non-blocking 아키텍처로 "
            "개선하여 동시 처리량을 10배 이상 향상시켰습니다."
        ),
        "repositories": [
            {
                "repo": "https://github.com/JinInSaDaeCheonMyeong/startHub-server",
                "role": "메인 백엔드 서버 (RESTAPI)",
                "description": (
                    "Spring Boot 기반 REST API 서버로 로그인, 공고 조회, 사용자 관리, "
                    "챗봇 세션, BMC 생성 등 핵심 비즈니스 로직 처리. Redis 캐싱으로 고트래픽 API 최적화, "
                    "Firebase FCM 푸시알림, Jsoup 기반 공고 스크래핑, 코루틴 기반 Non-blocking I/O 구현."
                ),
            },
            {
                "repo": "https://github.com/JinInSaDaeCheonMyeong/starthub-ai",
                "role": "AI 마이크로서비스 (FastAPI)",
                "description": (
                    "FastAPI 기반 AI 서버로 OpenAI 임베딩 모델, Pinecone 벡터DB 연동, "
                    "Claude MCP를 통한 자연어 공고 검색, 사용자 컨텍스트 기반 챗봇 응답 생성 담당."
                ),
            },
        ],
        "core_features": [
            {
                "name": "사용자 활동 기반 AI 챗봇",
                "description": (
                    "사용자의 관심사, 활동 기록을 벡터화하여 Claude API에 실시간 주입하는 방식의 맞춤형 챗봇. "
                    "UserContextService에서 사용자 정보를 벡터로 변환하고, ClaudeAIService에서 Claude API 호출 시 "
                    "컨텍스트를 포함하여 개인화된 응답 생성."
                ),
                "based_on": [
                    "포트폴리오 개요 - Claude API에 벡터화된 사용자의 활동 정보를 실시간 주입하여 사용자 맞춤형 챗봇을 구현",
                    "파일트리 - UserContextService.kt, ClaudeAIService.kt, AIChatbotUseCase.kt",
                ],
            },
            {
                "name": "RAG 기반 맞춤형 공고 추천 (Claude MCP)",
                "description": (
                    "Pinecone 벡터DB에 저장된 임베딩된 공고에 대해 Claude MCP를 활용하여 자연어 요청을 "
                    "JSON 형식 결과로 변환. 사용자의 자연어 검색 쿼리를 Claude가 해석하여 최적 조건의 공고를 추천."
                ),
                "based_on": [
                    "포트폴리오 인프라 구조 - Claude MCP로 자연어 요청에 대해 적절한 조건의 공고를 찾고, JSON 형식으로 데이터를 반환",
                    "포트폴리오 인프라 구조 - Pinecone을 Cloud Vector DB로 활용하여 임베딩된 공고를 저장 및 검색",
                ],
            },
            {
                "name": "Spring AI 기반비즈니스 모델 캔버스(BMC) 생성",
                "description": (
                    "Spring AI를 활용하여 LLM이 사용자 답변을 기반으로 자동 생성한 BMC 문서. "
                    "DocumentAIService에서 문서 생성 담당."
                ),
                "based_on": [
                    "포트폴리오 개요 - 비즈니스 모델 캔버스 생성",
                    "포트폴리오 주요 업무 - LLM 기반 기능 구현",
                    "파일 트리 - DocumentAIService.kt",
                ],
            },
            {
                "name": "경쟁사 분석 (LLM 기반)",
                "description": "Perplexity API를 활용한자동 경쟁사 분석. 트래픽 증가 시 Non-blocking 처리로 성능 향상.",
                "based_on": [
                    "포트폴리오 개요 - 경쟁사 분석 등의 기능으로 창업 전 과정을 지원",
                    "포트폴리오 트러블슈팅 - Perplexity API의 긴 응답 지연 동안 스레드가 I/O 대기를 하게됨",
                ],
            },
            {
                "name": "Non-blocking 아키텍처 (Kotlin Coroutine)",
                "description": (
                    "Spring @Async와 CompletableFuture 기반의 블로킹 구조를 Kotlin Coroutine의"
                    "CoroutineScope와 Deferred로 전환. .block()을 .awaitSingle()로, "
                    "CompletableFuture.get()을 Deferred.await()로 변경하여 I/O 대기 중 스레드를 양보. "
                    "kotlinx-coroutines-reactor로 WebFlux 통합."
                ),
                "based_on": [
                    "포트폴리오 트러블슈팅 - 해결 방법 1, 2번 항목",
                    "파일 트리 - AsyncConfig.kt, WebClientConfig.kt",
                ],
            },
        ],
        "tech_stack": [
            "Kotlin",
            "Spring Boot",
            "Spring WebFlux",
            "Kotlin Coroutine",
            "Spring AI",
            "FastAPI",
            "Python",
            "Claude API",
            "OpenAI API",
            "Perplexity API",
            "Claude MCP",
            "Pinecone (Vector DB)",
            "MySQL",
            "Redis",
            "Firebase FCM",
            "Google Cloud Platform",
            "Docker Compose",
            "Jsoup",
            "GitHub Actions",
            "Grafana",
            "K6",
        ],
    },
    "interview": [
        {
            "id": 1,
            "category": "troubleshooting",
            "question": (
                "포트폴리오에서 Non-blocking 전환으로 동시 처리량을 10배 이상 향상시켰다고 했는데, "
                "CompletableFuture.get()을 Deferred.await()로 바꾼 것의 핵심 차이가 정확히 무엇인가요? "
                "왜 이것만으로 스레드 풀 확장이 방지되는 건가요?"
            ),
            "expected_answer": (
                "CompletableFuture.get()은 블로킹 호출로, 결과를 얻을 때까지 스레드가 점유되며 다른 작업을 처리할 수 없습니다. "
                "반면 Deferred.await()는 Kotlin Coroutine 기반의 논블로킹 호출로, I/O 대기 중 스레드를 반환하여 "
                "다른코루틴이 해당 스레드에서 실행될 수 있습니다. 특히 Perplexity API 같은 외부 API 호출의 긴 응답 지연이 있을 때, "
                "CompletableFuture는 그 동안 스레드를 점유하므로 동시 요청이 증가하면 스레드 풀이 exponentially 확장되고 "
                "메모리 사용량이 증가합니다. 하지만 await()를 사용하면 같은 스레드 수로 훨씬 더 많은 동시 요청을 처리할 수 있습니다. "
                "이것이 메모리 사용량 90% 감소와 동시 처리량 10배향상을 가능하게 합니다."
            ),
            "based_on": [
                "포트폴리오 트러블슈팅 - 블로킹 기반의 스레드 풀 구조 문제점",
                "포트폴리오 트러블슈팅 - Deferred.await()로 전환 내용",
                "포트폴리오 인프라 구조 - Perplexity API 호출",
            ],
        },
        {
            "id": 2,
            "category": "tech_choice",
            "question": (
                "공고 추천 기능에서 Pinecone 벡터 DB를 선택한 이유는 무엇인가요? "
                "Redis와 같은 다른 캐싱 솔루션이 아니라 전문 벡터 DB를 택한 이유, 그리고 대안을 고려했다면 어떤 것들을 검토했나요?"
            ),
            "expected_answer": (
                "Pinecone은 벡터 임베딩 검색에 특화된 클라우드 기반 벡터 DB로, 의미론적 유사성 검색(semantic similarity search)을 "
                "효율적으로 수행할 수 있습니다. RAG 시스템에서 사용자의 자연어 쿼리를 임베딩하고 저장된 공고 임베딩과의 "
                "코사인 유사도를 계산하여 가장 관련성 높은 공고를 추천하는 것이 핵심인데, Redis는 단순"
                "키-값 저장소로 대규모 벡터 검색에 최적화되지 않았습니다. Pinecone을 선택한 이유는: "
                "(1) 관리형 서비스로 운영 부담 최소화, (2) 메타데이터 필터링 지원으로 공고 필터링 가능, "
                "(3) 프로덕션급 확장성. 대안으로는 Weaviate, Qdrant, Milvus 같은 오픈소스 벡터 DB나 "
                "AWS OpenSearch 같은 옵션을 검토했을 수 있으나, 초기 스타트업 단계에서 관리형 서비스의 편의성과 "
                "비용 효율성이 중요했을 것 같습니다."
            ),
            "based_on": [
                "포트폴리오 인프라 구조 - Pinecone을 Cloud Vector DB로 활용",
                "포트폴리오 개요 - RAG 시스템을 직접 구축",
                "포트폴리오 기술 스택 - Pinecone",
            ],
        },
        {
            "id": 3,
            "category": "implementation",
            "question": (
                "사용자 활동 기반 AI 챗봇에서 '벡터화된 사용자의 활동 정보를 실시간 주입한다'는 것이 정확히 어떤 방식인가요? "
                "매 채팅마다 사용자 정보를 다시 벡터화하는 건가요, 아니면 미리 저장된 벡터를 활용하나요?"
            ),
            "expected_answer": (
                "UserContextService에서 사용자의 활동 정보(관심분야, 이전 채팅 기록, 비즈니스 진행 상태 등)를 수집하여 "
                "OpenAI의 임베딩 모델을 통해 벡터로 변환합니다. 매 요청마다 새로 벡터화하는 방식이 더 타당한데, "
                "이는 사용자의 최신 활동 정보를 반영하기 위함입니다. 그 후 Claude API 호출 시 시스템 프롬프트나 컨텍스트에 "
                "이 벡터화된 정보(또는 벡터 기반으로 추출된 관련 정보)를 주입하여 Claude가 개인화된 응답을 생성하도록 합니다. "
                "실시간 주입의 의미는: (1) 각 채팅 세션의 누적 컨텍스트 반영, (2) 사용자의 최근 행동 변화 감지 및 반영, "
                "(3) 이전 대화 내용 기반의 연속성 유지. 성능 최적화를 위해 자주 변경되지 않는 사용자 프로필은 캐싱하되, "
                "활동 기록(최근 공고 조회, 채팅 이력 등)은 실시간 갱신할 가능성이 높습니다."
            ),
            "based_on": [
                "포트폴리오 인프라 구조 - Claude API에 벡터화된 사용자의 활동 정보를 실시간 주입",
                "포트폴리오 주요 업무 - 사용자 활동 기반 AI 챗봇",
                "파일 트리 - UserContextService.kt, AIChatbotUseCase.kt",
            ],
        },
        {
            "id": 4,
            "category": "integration",
            "question": (
                "메인 서버(Spring Boot)와 AI 서버(FastAPI)의 통신 구조를 설명해주세요. "
                "ChatbotRAGClient는 정확히 어떤 역할을 하며, 두 서버 간 요청/응답 흐름에서 지연이나 실패 처리는 어떻게 되나요?"
            ),
            "expected_answer": (
                "메인 서버의 ChatbotRAGClient는 FastAPI AI 서버와의 통신을 담당하는 HTTP 클라이언트입니다. "
                "사용자의 채팅 요청이 메인 서버(AIChatbotController/AIChatbotUseCase)에 도달하면, "
                "ClaudeAIService와 함께 ChatbotRAGClient가 AI 서버에 사용자 컨텍스트와 쿼리를 POST 요청으로 전송합니다. "
                "AI 서버는 RAG를 통해 관련 공고나 정보를 검색하고, 그 결과를 JSON 형식으로 반환합니다. "
                "Non-blocking 전환 후에는 WebClient를 사용하여 논블로킹 HTTP 통신을 구현했을 것으로 예상됩니다. "
                "지연 처리로는: (1) 타임아웃 설정(예: 30초),(2) 재시도 로직(exponential backoff), "
                "(3) Circuit breaker 패턴 가능성. 실패 처리로는 RateLimitService와 QuotaService 같은 클래스가 존재하는 것으로 보아, "
                "요청 제한 초과 시 RateLimitExceededException이나 QuotaExceededException을 던지고, "
                "이를 GlobalExceptionHandler에서 처리합니다. 또한 ChatSessionNotFoundException 같은 예외로 유효성 검사를 수행합니다."
            ),
            "based_on": [
                "포트폴리오 인프라 구조 - FastAPI 서버는 마이크로서비스로 구성",
                "파일 트리 - ChatbotRAGClient.kt, RateLimitService.kt, QuotaService.kt, GlobalExceptionHandler.kt",
                "포트폴리오 트러블슈팅 - WebFlux의 논블로킹 기반의 높은 동시성 처리",
            ],
        },
        {
            "id": 5,
            "category": "structure",
            "question": (
                "전체 아키텍처에서 Redis의 역할을 구체적으로 설명해주세요. "
                "로그인과 공고 조회를 캐싱한다는 것인데, 특히 사용자 맞춤 공고 추천 같이 개인화된 데이터는 "
                "Redis에 캐싱할 때 어떤 전략을 사용하나요?"
            ),
            "expected_answer": (
                "Redis는 이 아키텍처에서 두 가지 역할을 합니다: (1) 세션/토큰 저장소 - TokenRedisService에서 "
                "사용자 인증 토큰을 저장하고 검증. (2) 캐싱 레이어 - 고트래픽 API(로그인, 공고 조회 등)의 결과를 캐싱하여 DB 부하 감소. "
                "사용자 맞춤 공고 추천 같은 개인화 데이터의 경우, 단순 캐싱이 아니라 더 신중한 전략이 필요합니다. "
                "가능한 방식은: (1) 사용자별 캐시 키 활용 - `user:{userId}:recommendations` 형태로 저장, "
                "TTL 설정(예: 1시간)하여 일정 시간 후 재계산. (2) 인크리멘탈 갱신 - 사용자가 공고 조회/좋아요 시 해당 캐시 무효화. "
                "(3) 분류별 캐싱 - 공고 목록(자주 변경 안 함), 사용자 선호도(자주 변경), 추천 결과(매 요청 계산) 등을 구분. "
                "CacheConfig에서 이러한 캐시 설정을 정의했을 것으로 예상되며, Pinecone의 벡터 검색 결과는 "
                "개인화도 높고 빈번히 변경되므로 캐싱보다는 매번 계산하는 것이 옳을 수 있습니다."
            ),
            "based_on": [
                "포트폴리오 인프라 구조 - Redis로 Caching함, 트래픽이 높은 API(로그인, 공고 조회 등)",
                "파일 트리 - CacheConfig.kt, TokenRedisService.kt, UserCache.kt, UserCacheImpl.kt",
            ],
        },
    ],
}
