# API 레퍼런스

FastAPI 의 자동 문서(`/docs`, `/redoc`, `/openapi.json`)는 꺼져 있다(`docs_url=None`).
이 문서가 유일한 API 레퍼런스이므로, 엔드포인트를 추가·변경하면 여기도 같이 고친다.

## 공통 규약

**비동기 콜백** — 모든 작업형 엔드포인트는 같은 형태다.

1. 요청을 받으면 `202 Accepted` + `jobId` 를 즉시 반환한다.
2. 실제 작업은 `BackgroundTasks` 에서 fire-and-forget 으로 돈다.
3. 결과는 요청에 실려온 `callbackUrl` 로 POST 한다. 성공/실패 모두 콜백으로만 알린다.

작업이 시작된 뒤에는 HTTP 응답으로 실패를 알릴 방법이 없다. 그래서 디스패처는 예외를
전부 삼키고 실패 페이로드로 바꿔서 콜백한다. 콜백 수신 실패는 1회 재시도 후 포기한다
(`WEBHOOK_RETRY_DELAY_SECONDS`).

**와이어 포맷** — 모든 엔드포인트가 `camelCase` 다. 호출자가 Java(소켓/API 서버)로 통일됐다.

요청·응답·콜백 DTO 는 전부 `CamelModel`(`core/common/dto.py`) 을 상속하고
`model_dump(by_alias=True)` 로 덤프한다. `by_alias` 를 빠뜨리면 snake_case 로 새어나간다.

`CamelModel` 은 `populate_by_name=True` 라 **요청 body 는 snake_case 도 그대로 받는다**.
`/generate` 가 예전에 snake_case 였던 탓에 남아 있는 호출자를 위한 하위 호환이다.
반대로 응답·콜백은 camelCase 로만 나간다.

**동기 에러** — 요청 형식이 틀리면 FastAPI 가 `422` 를 돌려준다. 파이프라인 예외
(`PipelineError`)가 요청 처리 중에 올라오면 `{"message": "..."}` 로 매핑된다.
다만 작업형 엔드포인트에서는 이 예외가 백그라운드에서 발생하므로 실패 **콜백**이 된다.

---

## GET /health

```json
{ "status": "ok" }
```

---

## POST /generate — 면접 질문 생성 (레거시)

포트폴리오 PDF + GitHub 저장소를 분석해 면접 질문 5개를 만든다. 수십 초 이상 걸린다.

> `/profile` + `/questions/cycle` 로 바뀌는 중이다. API 서버 이전이 끝날 때까지만 유지하고 이후 삭제한다.

**요청** (camelCase, snake_case 도 허용)

```json
{
  "portfolioUrl": "https://example.com/portfolio.pdf",
  "githubUrls": ["https://github.com/owner/repo"],
  "callbackUrl": "https://api.example.com/callbacks/qa"
}
```

`githubUrls` 는 1개 이상, public 저장소만 허용한다(private 은 실패 콜백 403).

**응답 202**

```json
{ "jobId": "uuid", "status": "accepted", "message": "..." }
```

**성공 콜백**

```json
{
  "jobId": "uuid",
  "status": "succeeded",
  "result": {
    "projectSummary": { "overview": "...", "repositories": [], "coreFeatures": [], "techStack": [] },
    "interview": [
      {
        "id": 1,
        "category": "tech_choice",
        "question": "...",
        "expectedAnswer": "...",
        "basedOn": ["repo/src/file.py"]
      }
    ]
  }
}
```

`interview` 는 항상 5개다. `category` 는 `tech_choice` / `implementation` / `troubleshooting` /
`integration` / `structure` 중 하나. `basedOn` 은 질문의 근거 파일 경로(또는 `["file_tree"]`)이며
추측 질문 방지 장치라 절대 비지 않는다.

**실패 콜백**

```json
{ "jobId": "uuid", "status": "failed", "error": { "statusCode": 422, "message": "..." } }
```

`422`(잘못된 PDF), `403`(private 저장소), `500`(내부 오류).

---

## POST /generate-mock — 콜백 수신측 테스트용

요청 형식은 `/generate` 와 같다. 분석을 돌리지 않고 30초 뒤 고정 페이로드를 콜백으로 보낸다.
수신측이 비동기 흐름을 붙이는 동안 실제 파이프라인 비용을 쓰지 않으려고 둔 것이다.
`/generate` 와 함께 이전이 끝나면 삭제한다.

---

## POST /profile — 종합 데이터 생성

포트폴리오 PDF + GitHub 저장소를 **한 번만** 분석해 종합 데이터(profile)를 만든다. 원질문은 이 데이터만
보고 `/questions/cycle` 이 만든다. 검증·PDF 처리·저장소 트리·탐색 루프는 `/generate` 와 같고, 루프를 끝내는
도구만 `submit_profile` 로 다르다. 수십 초 이상 걸린다.

**요청** (camelCase)

```json
{
  "major": "컴퓨터공학",
  "portfolioUrl": "https://example.com/portfolio.pdf",
  "githubUrls": ["https://github.com/owner/repo"],
  "callbackUrl": "https://api.example.com/callbacks/profile"
}
```

`major` 는 선택이다. 자유 문자열이며 탐색 우선순위 지시에만 쓴다. `githubUrls` 는 1개 이상, public 만.

**응답 202**

```json
{ "jobId": "uuid", "status": "accepted", "message": "..." }
```

**성공 콜백**

```json
{
  "jobId": "uuid",
  "status": "succeeded",
  "result": {
    "profile": {
      "schemaVersion": 1,
      "major": "컴퓨터공학",
      "overview": "주문·재고 서비스",
      "techStack": [{ "name": "Redis", "evidence": [{ "path": "order-api/build.gradle", "note": "의존성 추가" }] }],
      "repositories": [{ "repo": "order-api", "role": "api_server", "description": "...", "techStack": ["Spring Boot"] }],
      "coreFeatures": [
        { "name": "재고 차감", "description": "...", "implementation": "...", "evidence": [{ "path": "...", "note": "..." }] }
      ],
      "troubleshootings": [
        { "title": "...", "portfolioClaim": "...", "resolutionInCode": "...", "evidence": [{ "path": "...", "note": "..." }] }
      ],
      "integrations": [{ "from": "order-api", "to": "결제 API", "method": "HTTP", "evidence": [{ "path": "...", "note": "..." }] }],
      "claimChecks": [{ "claim": "TPS 3배 향상", "status": "unverified", "note": "..." }],
      "structureNotes": [{ "repo": "order-api", "summary": "...", "keyPaths": ["order-api/src/main/java/order"] }]
    },
    "projectSummary": { "overview": "...", "repositories": [], "coreFeatures": [], "techStack": [] }
  }
}
```

- `profile` 은 API 서버가 저장했다가 `/questions/cycle` 에 **그대로** 되돌려준다.
- `claimChecks.status` 는 `confirmed` / `partial` / `unverified`. 근거 경로가 없어 질문의 맥락으로만 쓴다.
- `projectSummary` 는 tailor/multi 입력용이며 LLM 이 아니라 서버가 profile 에서 복사한다.
  `techStack` ← `techStack[].name`, `repositories` ← `repo`/`role`/`description`,
  `coreFeatures[].basedOn` ← `evidence[].path`(중복 제거). 근거 없는 기능이 빠지므로 `/generate` 때보다
  기능 목록이 짧을 수 있다(의도된 결과).

**근거 정리 (Stage 5')** — LLM 이 낸 근거를 실제 저장소 트리와 대조한다. `/questions/cycle` 에는 트리가
없으므로 파일이 실제로 있는지는 여기서만 보장한다.

1. 끝 슬래시를 지우고, `<저장소>/<하위 경로>` 처럼 2단계 이상인 경로만 인정한다.
2. 파일 경로는 트리에 있어야 하고, 디렉터리 경로는 그 경로로 시작하는 파일이 하나라도 있어야 한다.
3. 근거가 0개가 된 `techStack` / `coreFeatures` / `integrations` / `structureNotes` 항목은 지운다.
4. 근거가 0개가 된 `troubleshootings` 항목은 지우고, 그 주장을 `claimChecks` 에 `unverified` 로 옮긴다.
5. 이용 가능한 카테고리(아래 재료 표)가 **2종 미만이면 실패 콜백 `422`** 다. 근거가 하나도 안 남은 경우도
   여기에 걸린다.

**근거 경로 집합** = `techStack`·`coreFeatures`·`troubleshootings`·`integrations` 의 `evidence[].path` +
`structureNotes[].keyPaths`. 원질문의 `basedOn` 은 이 집합에서만 고른다.

**실패 콜백** — `/generate` 와 같은 형태다.

| statusCode | 원인 |
|---|---|
| `422` | 잘못된 PDF·URL, **또는** 코드에서 확인할 수 있는 근거 부족(이용 가능한 카테고리 2종 미만) |
| `403` | private 저장소 |
| `500` | LLM 호출 실패, 제출 형식 오류, 내부 오류 |

> 같은 `422` 가 두 원인을 뜻한다. 화면 안내를 나눠야 하면 `message` 로 구분한다
> ("코드에서 확인할 수 있는 근거가 부족합니다..."). 별도 코드로 나눌지는 미결정이다.

---

## POST /questions/cycle — 원질문 사이클 생성

종합 데이터만 보고 한 모드의 원질문 사이클을 만든다. LLM 1회 호출이며, 구성 규칙을 어기면 위반 내용을 붙여
1회 재시도하고 그래도 어기면 실패 콜백 `500` 이다.

| mode | 문항 | 세트 구성 |
|---|---|---|
| `SOLO` | 15 | 세트 3개 × 5문항. 세트마다 이용 가능한 카테고리를 하나씩 모두 담고, 남는 칸은 이용 가능한 카테고리로 채운다(`implementation`·`structure` 우선) |
| `MULTI` | 6 | 세트 3개 × 2문항. 세트 안 카테고리는 서로 다르고, 사이클 전체에 이용 가능한 카테고리가 모두 1번 이상 |

**요청** (camelCase)

```json
{
  "mode": "SOLO",
  "profile": { "schemaVersion": 1, "overview": "...", "techStack": [] },
  "excludeQuestions": ["지난 사이클 질문 본문"],
  "callbackUrl": "https://api.example.com/callbacks/question-cycle"
}
```

- `profile` 은 `/profile` 성공 콜백의 `result.profile` 을 그대로 넣는다.
- `excludeQuestions` 는 같은 모드의 최근 2사이클 질문 본문이다. 앞에서부터 30개(`EXCLUDE_MAX`)까지만 쓴다.
- `mode` 가 `SOLO`/`MULTI` 가 아니면 동기 `422` 다.

**이용 가능한 카테고리** — 재료 항목(근거가 있는 것)이 1개 이상인 카테고리다. 재료가 없는 카테고리는 쓰지 않고,
도구 스키마의 `category` enum 에서도 빠진다.

| 카테고리 | 재료 항목 |
|---|---|
| `tech_choice` | `techStack` |
| `implementation` | `coreFeatures` |
| `troubleshooting` | `troubleshootings` |
| `integration` | `integrations` |
| `structure` | `structureNotes` |

**응답 202**

```json
{ "jobId": "uuid", "status": "accepted", "message": "..." }
```

**성공 콜백** — `setNo`, 카테고리 순으로 정렬돼 있다.

```json
{
  "jobId": "uuid",
  "status": "succeeded",
  "result": {
    "mode": "SOLO",
    "questions": [
      {
        "setNo": 1,
        "category": "tech_choice",
        "question": "...",
        "intention": "재고 차감에 분산락을 고른 이유를 DB 락과 비교해 설명할 수 있는지",
        "expectedAnswer": "...",
        "basedOn": ["order-api/build.gradle"]
      }
    ]
  }
}
```

- `intention` 은 이 질문으로 확인하려는 것 한 문장이고 **채점 기준**이다.
- `expectedAnswer` 는 모범답안(400자 이내, 넘으면 자름)이다. 꼬리질문 생성과 참고용이다.
- `basedOn` 은 1개 이상이고 모두 근거 경로 집합 안에 있다(끝 슬래시 정리 후 비교, `file_tree` 같은 예외 없음).
- `question` 은 250자에서 자른다(질문 칸이 VARCHAR(255)).

**실패 콜백** — `/generate` 와 같은 형태다.

| statusCode | 원인 |
|---|---|
| `422` | 지원하지 않는 `schemaVersion`, 근거 경로 집합이 비어 있음, 이용 가능한 카테고리 2종 미만. LLM 을 부르지 않는다 |
| `500` | 재시도 후에도 구성 규칙 위반, LLM 호출 실패, 내부 오류 |

---

## POST /feedback/solo — 1:1 면접 피드백

끝난 면접의 질문·답변을 받아 채점한다. **무상태** — 세션을 조회하지 않고 요청 body 만 본다.

**요청** (camelCase)

```json
{
  "sessionId": "s-1",
  "interviewId": "iv-1",
  "userId": "u-1",
  "personaType": "REALISTIC",
  "personaTone": "DIRECT",
  "questions": [
    {
      "questionId": "q1",
      "parentId": null,
      "type": "ORIGINAL",
      "intention": "캐시 계층 선택 근거 확인",
      "content": "왜 Redis 를 썼나요?",
      "createdAt": "2026-08-19T10:00:00"
    }
  ],
  "answers": [
    { "answerId": "a1", "questionId": "q1", "content": "...", "createdAt": "2026-08-19T10:01:00" }
  ],
  "callbackUrl": "https://api.example.com/callbacks/feedback"
}
```

- `questions` 는 1~50개. `type` 은 `ORIGINAL` / `FOLLOW` 이고 `FOLLOW` 는 `parentId` 필수,
  `ORIGINAL` 은 `parentId` 가 있으면 안 된다(형식 단계에서 422).
- **채점 기준은 `intention` 뿐이다.** 이 파이프라인에는 모범답안이 존재하지 않는다.
- 답변이 없거나 공백인 문항은 채점에서 빠지고 개수 집계에만 반영된다.
  전 문항 미답변이면 실패 콜백 `422`.
- `personaType` 은 성향 지침에, `personaTone` 은 어조 지침에 반영된다. 둘 다 점수에는 영향을 주지 않는다.

**응답 202**

```json
{ "jobId": "uuid", "sessionId": "s-1", "status": "accepted", "message": "..." }
```

**성공 콜백**

```json
{
  "jobId": "uuid",
  "sessionId": "s-1",
  "status": "succeeded",
  "result": {
    "overall": {
      "totalScore": 71,
      "intentAlignmentScore": 88,
      "reliabilityScore": 69,
      "scoreBreakdown": {
        "scoringVersion": "axis-v1",
        "axes": [
          { "axis": "INTENT", "score": 88, "weight": 35 },
          { "axis": "DEPTH", "score": 38, "weight": 25 },
          { "axis": "SPECIFICITY", "score": 63, "weight": 25 },
          { "axis": "ACCURACY", "score": 100, "weight": 15 }
        ],
        "consistencyScore": 75
      },
      "summary": "...",
      "strengths": [],
      "improvements": [],
      "frequentWords": [{ "word": "캐시", "count": 3 }],
      "answeredCount": 1,
      "questionCount": 1
    },
    "feedbacks": [
      {
        "questionId": "q1",
        "questionContent": "...",
        "intention": "...",
        "userAnswer": "...",
        "modelAnswer": "...",
        "strengths": [],
        "improvements": [],
        "comment": "..."
      }
    ]
  }
}
```

- 3지표는 LLM 이 직접 매기지 않고 서버가 계산한다(채점 방식 `axis-v1`).
  LLM 은 답변한 문항마다 4축 등급(0~4)과 세션 단위 일관성 등급만 매긴다.
  - 등급 → 점수: 0/25/50/75/100. 축 점수는 답변한 문항들의 평균을 정수로 반올림한 값이다.
  - `totalScore` = 의도 충족 35% + 깊이 25% + 구체성 25% + 정확성 15%(축 점수 정수로 계산 후 반올림).
    기술 내용이 없는 문항은 정확성에서 빠지고, 세션 전체가 해당 없으면 나머지 축 비율로 다시 나눈다.
  - `intentAlignmentScore` = 의도 충족 축 점수.
  - `reliabilityScore` = (일관성 + 구체성) / 2. 답변이 1개라 일관성을 판단할 수 없으면 구체성 점수.
  - 미답변 문항은 점수 계산에서 빠지고 `answeredCount` / `questionCount` 로만 드러난다.
- `scoreBreakdown` 은 종합 점수의 산출 근거다. 사용자에게 "각 축이 몇 점이라 종합 몇 점"을 보여주는 용도다.
  - `axes` 는 항상 4개이고 `INTENT` → `DEPTH` → `SPECIFICITY` → `ACCURACY` 순서다.
  - 세션 전체에서 해당 없는 축은 `score` 와 `weight` 가 모두 null 이다(주로 `ACCURACY`).
  - 표시된 값으로 `Σ(score × weight) / Σ(weight)` 를 반올림(0.5 올림)하면 `totalScore` 와 항상 같다.
  - `consistencyScore` 는 종합 점수에 들어가지 않는 별도 지표이며, 답변이 1개면 null 이다.
  - N:1(`/feedback/multi`) 은 같은 구조를 `overall` 과 면접관별 `personas[]` 에 싣는다.
  - 필드 명세와 변경 전후 비교는 [feedback-scoring-contract.md](feedback-scoring-contract.md) 참고.
- `questionContent` / `intention` / `userAnswer` 는 요청 body 를 그대로 되돌려주는 값이다.
  LLM 이 생성하지 않는다.
- `modelAnswer` 는 채점 기준이 아니라 사용자에게 보여주는 예시 답안(40~100자)이다.
- `frequentWords` 는 LLM 이 아니라 서버가 센다(2회 이상 상위 10개).

**실패 콜백** — `sessionId` 가 함께 실린다(`result` 가 없어 수신측이 세션을 못 찾기 때문).

```json
{ "jobId": "uuid", "sessionId": "s-1", "status": "failed", "error": { "statusCode": 422, "message": "..." } }
```

`422`(채점 대상 없음), `502`(LLM 호출 실패), `500`(내부 오류). LLM 응답이 불완전하면
재시도 없이 즉시 실패 콜백이다.

---

## POST /questions/tailor — 면접 전 질문 재작성

질문 풀(`/questions/cycle`)이나 레거시 `/generate` 에서 꺼낸 원질문을, 지원자의 사전 정보에 맞게 **본문만** 다시 쓴다.
면접 시작 직전에 호출한다.

**요청** (camelCase)

```json
{
  "interviewId": "iv-1",
  "userId": "u-1",
  "profile": {
    "jobRole": "백엔드",
    "experienceLevel": "신입",
    "personaType": "REALISTIC",
    "personaTone": "DIRECT"
  },
  "questions": [
    {
      "id": 1,
      "category": "tech_choice",
      "question": "왜 Redis 를 썼나요?",
      "intention": "캐시 계층 선택 근거를 대안과 비교해 설명할 수 있는지",
      "expectedAnswer": "TTL 기반 캐시로 조회 부하를 줄였다",
      "basedOn": ["order-api/src/cache.py"]
    }
  ],
  "callbackUrl": "https://api.example.com/callbacks/tailor"
}
```

- `questions` 는 1~10개, `id` 중복 불가(422).
- `intention`(선택)은 **보존해야 할 검증 포인트**다. 재작성된 질문으로도 같은 것을 확인할 수 있어야 한다.
  `expectedAnswer` 는 참고 답안으로만 프롬프트에 싣는다.
- `intention` 이 없으면(레거시 `/generate` 원질문) 지금처럼 `expectedAnswer` 를 검증 포인트로 쓴다.
- `profile` 4축은 모두 선택이지만 **하나도 없으면 실패 콜백 `422`** 다. 재작성할 근거가 없다.
- 세션이 아직 없으므로 매칭 키는 `sessionId` 가 아니라 `interviewId` 다.

**응답 202**

```json
{ "jobId": "uuid", "interviewId": "iv-1", "status": "accepted", "message": "..." }
```

**성공 콜백**

```json
{
  "jobId": "uuid",
  "interviewId": "iv-1",
  "status": "succeeded",
  "result": {
    "tailored": true,
    "questions": [{ "id": 1, "question": "다시 쓴 질문" }]
  }
}
```

바뀌는 것은 본문뿐이라 `category` / `intention` / `expectedAnswer` / `basedOn` 은 돌려주지 않는다.
호출자가 들고 있는 원본을 그대로 쓰면 된다.

**`tailored: false` — 원질문 폴백**

LLM 호출 실패, 응답 파싱 실패, 일부 문항 누락이면 **실패 콜백을 보내지 않는다.**
원질문은 이미 유효한 산출물이라, 재작성이 안 됐다고 면접을 못 열게 만드는 편이 더 손해다.
이 경우 `questions` 에는 요청에 실려온 원문이 그대로 담기고 `tailored` 가 `false` 가 된다.

일부만 재작성됐을 때도 **전체를 원문으로 되돌린다.** 재작성분과 원문이 한 면접에 섞이면
어조가 들쭉날쭉해져서, 부분 폴백보다 전체 폴백이 예측 가능하다.

**실패 콜백** — 사전 정보가 아예 없거나(`422`) 내부 오류(`500`) 일 때만 발생한다.

```json
{ "jobId": "uuid", "interviewId": "iv-1", "status": "failed", "error": { "statusCode": 422, "message": "..." } }
```

---

## POST /questions/tailor/multi — N:1 면접 질문 구성

기술 면접관의 원질문은 재작성하고, `otherPersonas`의 질문은 프로젝트 요약을 근거로 새로 생성한다.
`style`은 성향 키, `tone`은 어조 키다.

신규 N:1 면접은 기술 1명 + 비기술 1~3명(전체 2~4명)이다. **신규 생성 제한은 API 서버에서 적용한다.**
기존 5인 면접의 구성·질문·답변·피드백은 변경하지 않으며, 준비 및 재시도를 위해 이 분석 API는
`otherPersonas` 4명까지 계속 수용한다. 요청에 신규/기존 구분 필드를 추가하지 않는다.
API 서버의 신규 생성 제한을 먼저 배포해야 한다.

`techPersona.role`은 TECH, `otherPersonas`는 TECH 이외의 서로 다른 역할이어야 한다.
역할 비교 시 공백·대소문자를 무시하며 공백뿐인 역할은 거부한다. 역할은 자유 문자열로 유지한다
(TECH/HR/CEO/PM/DESIGN 및 신규 직책 지원). 모든 `personaId`는 서로 달라야 한다.
`personaId` 는 **문자열**이다. API 서버의 Long ID 를 문자열로 바꿔 보낸다(예: `"101"`). 숫자(`101`)로 보내면 422 로 거부된다. 분석 서버는 값을 해석하지 않고 결과에 그대로 되돌려준다.
`questionCount`는 면접관별 1~5, 생략하면 2다. 총 문항 수는 기술 원질문 수 + 비기술 `questionCount` 합이다.
기본값이면 2/3/4인에 4/6/8문항이며, 신규 질문 ID는 `max(6, 원질문 최대 ID + 1)`부터 연속 부여한다.

**요청** (camelCase)

```json
{
  "interviewId": "iv-1",
  "userId": "u-1",
  "jobRole": "백엔드",
  "experienceLevel": "주니어",
  "techPersona": {
    "personaId": "101",
    "role": "TECH",
    "style": "METICULOUS",
    "tone": "DIRECT",
    "questionCount": 1
  },
  "otherPersonas": [
    {
      "personaId": "102",
      "role": "HR",
      "style": "FRIENDLY",
      "tone": "GENTLE",
      "questionCount": 1
    }
  ],
  "questions": [
    {
      "id": 1,
      "category": "tech_choice",
      "question": "왜 Redis를 사용했나요?",
      "intention": "캐시 저장소 선택 근거를 설명할 수 있는지",
      "expectedAnswer": "캐시 선택 근거",
      "basedOn": ["order-api/src/cache.py"]
    }
  ],
  "projectSummary": {
    "overview": "주문 처리 서비스",
    "repositories": [],
    "coreFeatures": [],
    "techStack": ["Redis"]
  },
  "callbackUrl": "https://api.example.com/callbacks/tailor"
}
```

`questions[].intention` 은 선택이다. `projectSummary` 는 `/profile` 결과의 `projectSummary` 를 넣는다.

기술 질문 수는 `techPersona.questionCount`와 `questions` 개수가 같아야 한다. 생성된 결과는 기술 면접관 질문이 먼저 오고, 이후 `otherPersonas` 순서대로 온다. 기술 질문 재작성 실패와 신규 질문 생성 실패 모두 실패 콜백을 보낸다. 성공 결과에는 `questions`가 있으며 `tailored` 필드는 없다.

**성공 콜백의 `intention`** — 질문마다 항상 채워진다.

- 기술 질문: 입력 `intention` 을 그대로 돌려준다. 없으면(레거시) `expectedAnswer` 로 채운다. LLM 이 다시 쓰지 않는다.
- 비개발 질문: 생성할 때 함께 만든다. 이 값은 질문 풀에 없고 **이 콜백으로만** 전달되므로 API 서버가 받아 저장해야 한다.

```json
{
  "id": 6,
  "personaId": "102",
  "category": "motivation",
  "question": "...",
  "intention": "재고 기능을 먼저 만든 판단 근거를 사용자 관점에서 설명할 수 있는지",
  "expectedAnswer": "...",
  "basedOn": ["재고 차감"]
}
```

---

## POST /feedback/multi — N:1 면접 피드백

신규 면접은 최대 4명이며, 기존 기록의 채점·재시도를 위해 `personas`는 최대 5명까지 수용한다.
최소 1명 허용은 기존 계약을 유지한다. `personaId`와 역할은 각각 중복할 수 없으며 역할 비교는
공백·대소문자를 무시한다. 질문의 `personaId`는 명단에 있어야 한다.
`personaId` 는 **문자열**이다. API 서버의 Long ID 를 문자열로 바꿔 보낸다(예: `"101"`). 숫자(`101`)로 보내면 422 로 거부된다. 분석 서버는 값을 해석하지 않고 결과에 그대로 되돌려준다.
담당 문항 또는 답변이 없는 면접관도 요청 `personas` 순서대로 결과에 포함된다.

여러 면접관의 질문·답변을 한 번에 채점한다. 각 질문의 `personaId`로 담당 면접관을 연결하고, `personas[].style`은 성향, `personas[].tone`은 어조로 사용한다.

**요청** (camelCase)

```json
{
  "sessionId": "s-1",
  "interviewId": "iv-1",
  "userId": "u-1",
  "personas": [
    {
      "personaId": "101",
      "role": "TECH",
      "style": "METICULOUS",
      "tone": "DIRECT"
    },
    {
      "personaId": "102",
      "role": "HR",
      "style": "FRIENDLY",
      "tone": "GENTLE"
    }
  ],
  "questions": [
    {
      "questionId": "q1",
      "personaId": "101",
      "parentId": null,
      "type": "ORIGINAL",
      "intention": "캐시 선택 근거 확인",
      "content": "왜 Redis를 사용했나요?",
      "createdAt": "2026-08-19T10:00:00"
    }
  ],
  "answers": [
    { "answerId": "a1", "questionId": "q1", "content": "...", "createdAt": "2026-08-19T10:01:00" }
  ],
  "callbackUrl": "https://api.example.com/callbacks/feedback"
}
```

성공 콜백은 `result.overall`, 면접관별 `result.personas`, 문항별 `result.feedbacks`를 포함한다. 성향은 담당 면접관의 평가 관점에만, 어조는 해당 면접관의 피드백 표현에만 영향을 주며 점수 기준은 동일하다.

점수 계산과 `overall.scoreBreakdown` 은 1:1 과 같다. 다만 `role` 이 `TECH` 가 아닌 면접관의 문항은 정확성 축에서 빠진다.

면접관별 결과는 아래 형태다.

```json
{
  "personaId": "102",
  "role": "HR",
  "score": 70,
  "scoreBreakdown": {
    "scoringVersion": "axis-v1",
    "axes": [
      { "axis": "INTENT", "score": 75, "weight": 35 },
      { "axis": "DEPTH", "score": 63, "weight": 25 },
      { "axis": "SPECIFICITY", "score": 69, "weight": 25 },
      { "axis": "ACCURACY", "score": null, "weight": null }
    ],
    "consistencyScore": null
  },
  "comment": "...",
  "strengths": [],
  "improvements": []
}
```

- `personas[].score` 는 그 면접관이 담당한 답변에만 같은 공식을 적용한 값이다.
- 담당 답변이 없으면 `score` 와 `scoreBreakdown` 이 모두 null 이다("0점"과 구분하기 위해서다).
- 면접관별 `scoreBreakdown.consistencyScore` 는 항상 null 이다. 일관성은 면접 전체 단위로만 `overall` 에서 판단한다.
- `TECH` 가 아닌 면접관은 `ACCURACY` 가 항상 null 이다.
- 종합 점수는 전체 문항의 축 평균으로 계산하므로, 면접관 점수들의 단순 평균과 다를 수 있다.

---

## 성향·어조 키

- 성향: `FRIENDLY`, `REALISTIC`, `METICULOUS`
- 어조: `GENTLE`, `DIRECT`, `PRESSURING`
- 분석 서버 선배포 기간에는 기존 성향 키 `NEUTRAL`과 `STRESS`도 각각 `REALISTIC`, `METICULOUS`로 호환한다.
- 알 수 없는 키는 기본 지침으로 처리하고 로그에 경고를 남긴다.

---

## 설정

전부 환경 변수로 덮어쓴다(`.env` 지원). 기본값과 각 값을 그렇게 정한 이유는
[`src/app/main/config.py`](../src/app/main/config.py) 주석에 있다.

| prefix | 대상 | 자주 건드리는 값 |
|---|---|---|
| `APP_` | 서비스 공통 | `LOGGING_LEVEL`(DEBUG 면 단계별 풀 페이로드 로깅) |
| `ANTHROPIC_` | LLM 자격증명·모델 | `API_KEY`(필수, 없으면 부팅 실패), `TEXT_MODEL`, `VISION_MODEL` |
| `INTERVIEW_QA_` | `/generate` 파이프라인 | 트리아지 임계값, 비전 호출 상한, 외부 I/O timeout, 웹훅 |
| `FEEDBACK_SOLO_` | `/feedback/solo` | `GRADING_MAX_TOKENS`, `ANSWER_MAX_CHARS` |
| `FEEDBACK_MULTI_` | `/feedback/multi` | `GRADING_MAX_TOKENS`, `ANSWER_MAX_CHARS` |
| `QUESTION_TAILOR_` | `/questions/tailor` | `REWRITE_MAX_TOKENS`, `QUESTION_MAX_CHARS` |
| `QUESTION_TAILOR_MULTI_` | `/questions/tailor/multi` | `GENERATE_MAX_TOKENS`, `TEXT_MAX_CHARS` |
| `PROFILE_` | `/profile` 탐색 루프 | `MAX_TURNS`, `TOKEN_LIMIT`, `RESPONSE_MAX_TOKENS`(파일 읽기 상한은 `INTERVIEW_QA_` 값 공유) |
| `QUESTION_CYCLE_` | `/questions/cycle` | `MAX_TOKENS`, `EXPECTED_ANSWER_MAX_CHARS`, `RETRY`, `EXCLUDE_MAX`, `TEXT_MAX_CHARS` |

웹훅 설정(`WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_RETRY_DELAY_SECONDS`)은 `INTERVIEW_QA_` prefix
아래 있지만 모든 작업형 엔드포인트의 콜백에 적용된다.

`QUESTION_CYCLE_MAX_TOKENS`(12,288) 는 SOLO 15문항 기준 추정값이다. 실측 후 확정하며, 넘치면 SOLO 를
2회 호출(세트 1~2, 세트 3)로 나누고 두 번째 호출의 제외 질문에 첫 결과를 넣는다.

### N:1 최대 구성 검증 범위

자동 테스트는 LLM 스텁으로 문항 배분·ID·누락·콜백 계약을 검증한다. 실제 LLM 실측과 토큰 상한 조정은
후속 작업이다. `/questions/tailor/multi`와 `/feedback/multi`에서 콜백 완료 시간, 호출별 출력 토큰,
`stop_reason`, 절단율 및 최종 실패율을 측정한다. 꼬리질문 최대 수는 채팅 서버 정책 확인 후 정한다.
기존 5인 기록을 잘라내거나 수정하는 데이터 마이그레이션은 수행하지 않는다.
