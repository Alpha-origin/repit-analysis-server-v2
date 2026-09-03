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

## POST /generate — 면접 질문 생성

포트폴리오 PDF + GitHub 저장소를 분석해 면접 질문 5개를 만든다. 수십 초 이상 걸린다.

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
      "totalScore": 70,
      "intentAlignmentScore": 65,
      "reliabilityScore": 80,
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

- 3지표는 서로 다른 축이다. `totalScore`(전체 종합), `intentAlignmentScore`(물은 것에 답했는가),
  `reliabilityScore`(일관성 — 모순·근거 구체성). 루브릭은 두지 않아 같은 답변이 세션마다
  다른 점수를 받을 수 있다.
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

`/generate` 로 만들어 둔 원질문을, 지원자의 사전 정보에 맞게 **본문만** 다시 쓴다.
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
      "expectedAnswer": "캐시 계층 선택 근거와 대안 비교",
      "basedOn": ["order-api/src/cache.py"]
    }
  ],
  "callbackUrl": "https://api.example.com/callbacks/tailor"
}
```

- `questions` 는 `/generate` 산출물(`interview[]`)을 그대로 되돌려주면 된다. 1~10개, `id` 중복 불가(422).
- `expectedAnswer` 는 재작성 대상이 아니라 **보존해야 할 검증 포인트**다. 재작성된 질문으로도
  같은 것을 확인할 수 있어야 한다.
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

바뀌는 것은 본문뿐이라 `category` / `expectedAnswer` / `basedOn` 은 돌려주지 않는다.
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

**요청** (camelCase)

```json
{
  "interviewId": "iv-1",
  "userId": "u-1",
  "jobRole": "백엔드",
  "experienceLevel": "주니어",
  "techPersona": {
    "personaId": "tech-1",
    "role": "TECH",
    "style": "METICULOUS",
    "tone": "DIRECT",
    "questionCount": 1
  },
  "otherPersonas": [
    {
      "personaId": "hr-1",
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

기술 질문 수는 `techPersona.questionCount`와 `questions` 개수가 같아야 한다. 생성된 결과는 기술 면접관 질문이 먼저 오고, 이후 `otherPersonas` 순서대로 온다. 기술 질문 재작성 실패 시 `tailored: false`로 원문을 사용하며, 신규 질문 생성 실패 시 실패 콜백을 보낸다.

---

## POST /feedback/multi — N:1 면접 피드백

여러 면접관의 질문·답변을 한 번에 채점한다. 각 질문의 `personaId`로 담당 면접관을 연결하고, `personas[].style`은 성향, `personas[].tone`은 어조로 사용한다.

**요청** (camelCase)

```json
{
  "sessionId": "s-1",
  "interviewId": "iv-1",
  "userId": "u-1",
  "personas": [
    {
      "personaId": "tech-1",
      "role": "TECH",
      "style": "METICULOUS",
      "tone": "DIRECT"
    },
    {
      "personaId": "hr-1",
      "role": "HR",
      "style": "FRIENDLY",
      "tone": "GENTLE"
    }
  ],
  "questions": [
    {
      "questionId": "q1",
      "personaId": "tech-1",
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

웹훅 설정(`WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_RETRY_DELAY_SECONDS`)은 `INTERVIEW_QA_` prefix
아래 있지만 네 작업형 엔드포인트의 콜백에 모두 적용된다.
