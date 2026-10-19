# API 레퍼런스

FastAPI 의 자동 문서(`/docs`, `/redoc`, `/openapi.json`)는 꺼져 있다(`docs_url=None`).
이 문서가 유일한 API 레퍼런스이므로, 엔드포인트를 추가·변경하면 여기도 같이 고친다.

선택 기능 `POST /analysis/audio`, `GET /analysis/audio/jobs/{jobId}`의 계약은 [음성 분석 통신 문서](audio-api.md),
워커 실행 방법은 [음성 분석 문서](audio-analysis.md)에 있다. 음성 작업은 아래의 `BackgroundTasks` 규약 대신
영속 큐를 사용하며, 콜백은 선택이고 상태 조회를 지원한다. 요청·응답은 동일하게 camelCase다.

## 공통 규약

**비동기 콜백** — 아래의 기존 텍스트 작업형 엔드포인트는 같은 형태다.

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
예전 snake_case 호출자를 위한 하위 호환이다.
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

## POST /profile — 종합 데이터 생성

포트폴리오 PDF + GitHub 저장소를 **한 번만** 분석해 종합 데이터(profile)를 만든다. 원질문은 이 데이터만
보고 `/questions/cycle` 이 만든다. 입력 검증 → PDF 처리 → 저장소 트리 → 코드 탐색 루프 순서이고, 루프는
`submit_profile` 호출로 끝난다. 수십 초 이상 걸린다.

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

### 직무·문서 목록 요청 (문서 분석 1차)

```json
{
  "jobRole": "QA_ENGINEER",
  "documents": [{"type": "TEST_PLAN", "url": "https://example.com/plan.pdf", "title": "결제 테스트 계획"}],
  "githubUrls": [],
  "supplements": [],
  "callbackUrl": "https://api.example.com/callbacks/profile"
}
```

- `jobRole`이 없으면 위의 옛 요청 그대로 처리한다. 개발 직군의 기존 모델 입력과 profile v1은 유지한다.
- 코드 9개: `FRONTEND`, `BACKEND`, `WEB_PUBLISHER`, `DATA_ANALYST`, `DBA_INFRA`, `QA_ENGINEER`, `SECURITY`, `PRODUCT_MANAGER`, `IT_CONSULTANT`.
- `jobRole`이 있으면 `major`·`portfolioUrl`은 무시한다. `documents`는 IT 1~5개, 일반 직군 0~5개이며 실제 PDF 합계 50쪽 이하. 손상·암호화 PDF는 실패한다.
- 개발 직군 GitHub는 필수, 데이터·DBA·QA·보안은 선택, 기획·컨설턴트는 받지 않는다. 문서 종류와 필수 종류는 `core/common/job_spec` 기준이다.
- `supplements`는 기획·컨설턴트만 최대 3개. `title`(1~80자), `documentIndex`(선택, 문서의 0부터 시작하는 위치), `goal`·`role`·`decision`·`outcome`(각 500자 이하)을 받는다.
- 직무·종류·필수 자료·GitHub·보완 입력 조합이 잘못되면 접수 전 422다. PDF 실제 쪽수·암호화 여부는 다운로드 후 실패 콜백 `INVALID_MATERIAL`이다.
- 비개발은 텍스트·표·그림·벡터 도식을 읽으며, 개발 직군에 새 쪽 읽기를 적용하지 않는다. 여러 개발 문서는 기존 처리 결과를 머리줄과 함께 합치고 이미지 호출 15개 상한을 요청 전체에서 공유한다.
- 비개발·일반 성공은 `profile.schemaVersion=2`다. 아래 기존 성공 콜백 예시는 v1이다. v2 예시는 [문서 분석 계약](document-analysis-v1.md)에 있다.
- v2 `sources`는 문서별 `pageKinds`·`unreadPages`(읽기 실패/불완전)·`droppedPages`(읽은 뒤 모델 입력 예산으로 제외)를 구분한다. 원문·전사·추출 색인은 작업 중 메모리에만 둔다.
- v2 재료 `materials[]`는 `category/title/summary/evidence/sourceKind`다. 근거 경로는 `doc-N/pM`, `supp-N/goal|role|decision|outcome`, 저장소 경로다. 문서·보완 인용 `quote`는 원문 80자 이하이고 NFKC·소문자·공백/문장부호 제거 후 8자 이상이며 원문 색인에 있어야 한다. 읽기 요약과 관계 해석은 인용으로 인정하지 않는다.
- `sourceKind`는 서버가 `DOCUMENT > VISION > CODE > COVER_LETTER > SUPPLEMENT` 순서로 정한다. 카테고리 2종 이상이 필요하며, 비개발에는 문서·비전·코드 근거 카테고리 1종 이상도 필요하다. v2 `claimChecks`는 `claim/reason/evidence`로 불확실한 역할·성과를 남긴다.
- 이 문서 경로에 얼굴·영상 분석과 API·Web 자료 저장은 포함하지 않는다. 비개발 테일러·채점 직무 확장은 아래 계약을 따른다.

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
    "projectSummary": { "overview": "...", "repositories": [], "coreFeatures": [], "techStack": [] },
    "documentExtraction": {
      "status": "partial", "pageCount": 12,
      "recoveredPages": [3, 4], "partialPages": [8], "failedPages": [9],
      "skippedPages": [10], "noTextPages": [1],
      "warnings": [{ "page": 9, "code": "timeout" }]
    }
  }
}
```

- `profile` 은 API 서버가 저장했다가 `/questions/cycle` 에 **그대로** 되돌려준다.
- `claimChecks.status` 는 `confirmed` / `partial` / `unverified`. 근거 경로가 없어 질문의 맥락으로만 쓴다.
- `projectSummary` 는 tailor/multi 입력용이며 LLM 이 아니라 서버가 profile 에서 복사한다.
  `techStack` ← `techStack[].name`, `repositories` ← `repo`/`role`/`description`,
  `coreFeatures[].basedOn` ← `evidence[].path`(중복 제거). 근거 없는 기능이 빠지므로
  기능 목록이 짧을 수 있다(의도된 결과).

`result.documentExtraction`은 PDF 판독 범위를 나타낸다. 페이지 번호는 PDF 뷰어와 같은 **1부터 시작**한다.
`status`는 `complete`(검사 범위에 미해결 문제 없음), `partial`(미판독·미처리·정제 진단 존재),
`not_evaluated`(복구 기능 비활성)다. `complete`는 전사 정확성이나 지원자 주장 검증을 보증하지 않는다.
`recoveredPages`, `partialPages`, `failedPages`, `skippedPages`, `noTextPages`는 해당 상태의 페이지 목록이다.
그림만 있는 `no_text` 페이지는 실패로 세지 않으며 기존 그림 의미 분석을 계속 사용한다.
경고에는 `empty_text`, `broken_characters`, `native_extraction_error`, `short_text`, `partial_extraction`,
`cleanup_suspect`, `unreadable_regions`, `native_vision_conflict`, `ambiguous_reading_order`와 실패 원인이 들어간다.
실패 원인은 `api_error`, `timeout`, `invalid_response`, `truncated`, `render_error`, `skipped_budget`,
`feature_disabled`다. `vision_region_conflict`, `image_extraction_error`도 진단 코드로 올 수 있다.

API 서버는 이 보고서를 프로필과 함께 저장하고 재사용 시 판독 범위를 유지해야 한다.
새 성공 키와 아래 실패 코드를 허용하는 소비자 계약을 함께 배포한다. 보고서는 `profile` 외부 필드이므로
개발 `ApplicantProfile.schemaVersion`은 **1을 유지**하고, 문서 경로(비개발·일반)는 **2**를 사용한다. 이 저장소의 변경은 Analysis 서버 범위다.
API 서버의 DTO·저장·조회 반영은 별도로 필요하다.

PDF 텍스트 복구와 `documentExtraction` 보고서는 개발 경로의 첫 PDF(옛 요청의 `portfolioUrl`, 개발 `jobRole`
요청의 `documents[0]`)에만 적용한다. 개발 직군 문서가 여러 개면 2번째 이후 문서는 복구 없이 원문 추출·정제만
거치고, 보고서의 페이지 번호와 `pageCount`도 첫 문서 기준이다. 비개발(v2) 성공은 자체 쪽 읽기를 쓰고 복구를
적용하지 않으므로 `documentExtraction`이 기본값(`status: not_evaluated`, `pageCount: 0`)이며, 판독 범위는
`profile.sources[].unreadPages`·`droppedPages`가 나타낸다.

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

**실패 콜백**

```json
{ "jobId": "uuid", "status": "failed", "error": { "statusCode": 422, "message": "...", "code": "INSUFFICIENT_EVIDENCE" } }
```

| statusCode | code | 원인 |
|---|---|---|
| `422` | `INVALID_MATERIAL` | 잘못된 PDF·URL |
| `422` | `UNREADABLE_DOCUMENT` | 비개발 필수 종류 문서에서 읽은 내용이 없음. 종합 데이터 생성 모델을 호출하지 않음 |
| `422` | `INSUFFICIENT_EVIDENCE` | 코드에서 확인할 수 있는 근거 부족(이용 가능한 카테고리 2종 미만) |
| `403` | `REPO_NOT_PUBLIC` | private 저장소 |
| `500` | `INTERNAL` | LLM 호출 실패, 제출 형식 오류, API 인증·설정 또는 내부 오류 |
| `503` | `DOCUMENT_EXTRACTION_UNAVAILABLE` | 사용 가능한 PDF 내용이 없으며 복구 API 일시 오류·시간 초과 발생 |
| `413` | `DOCUMENT_EXTRACTION_LIMIT` | 사용 가능한 PDF 내용이 없으며 복구 호출·페이지·출력 예산 소진 |
| `422` | `DOCUMENT_UNREADABLE` | 사용 가능한 PDF 내용이 없으며 판독 불가·실제 빈 내용 |

PDF 복구를 활성화한 경우 병합 후 사용 가능한 본문이나 그림 분석 결과가 전혀 없으면 GitHub 탐색 전에
실패한다. 일부 페이지에라도 사용 가능한 내용이 있으면 `partial` 보고서와 함께 분석을 계속한다.
원인이 여러 개면 설정·알 수 없는 오류 → 일시 오류 → 예산 제한 → 판독 불가 순서로 분류한다.
문서 판독 실패 콜백은 `error.documentExtraction`에 같은 보고서 구조를 포함한다. 그 외 기존 실패에는
해당 필드를 넣지 않는다. 비활성 상태에는 새 문서 실패 정책을 적용하지 않는다.

`UNREADABLE_DOCUMENT`(비개발 필수 종류 문서)와 `DOCUMENT_UNREADABLE`(개발 경로 PDF 텍스트 복구 후 판독 불가)은
서로 다른 코드다. `UNREADABLE_DOCUMENT` 실패에는 `error.documentExtraction`을 넣지 않는다.

같은 `422`가 여러 원인을 뜻하므로 화면 안내는 `code`로 고른다. `message`는 표시용이며 분기에 쓰지 않는다.
`code` 이름은 API 서버 `/profile/latest` 의 `failureReason` 과 같다. 다른 작업형 엔드포인트의 실패 콜백에는 `code` 가 없다.

---

## 일반 직군 추가 계약 (2026-10-20)

`/profile`은 `GEN_STRATEGY`(경영기획·전략), `GEN_HR_ADMIN`(인사·총무), `GEN_FINANCE`(재무·회계),
`GEN_MARKETING`(마케팅·홍보), `GEN_SALES`(영업·고객관리), `GEN_DESIGN`(디자인)을 지원한다.

```json
{
  "jobRole": "GEN_MARKETING", "jobTitle": "퍼포먼스 마케터",
  "documents": [], "githubUrls": [], "supplements": [],
  "coverLetters": [{"question": "직무 역량과 경험", "answer": "고객 인터뷰를 통해 타깃을 정의하고 캠페인 성과를 측정했습니다."}],
  "callbackUrl": "https://api.example.com/callbacks/profile"
}
```

- `jobTitle`은 필수다. 앞뒤 공백 제거 후 1~30자, 제어 문자 없는 출력 가능한 문자열이다.
- 허용 PDF 종류는 `RESUME`, `CAREER_DESCRIPTION`, `PORTFOLIO`, `WORK_SAMPLE`(업무 산출물)이다.
  일반에는 필수 문서 종류가 없으며 **이력서 외 PDF 1개 또는 자소서 1문항 이상**이 필요하다.
- `coverLetters`는 생략 시 `[]`, 최대 5문항이며 항목의 `question`은 공백 제거 후 1~200자,
  `answer`는 1~2,000자다. 문항·답이 모두 있어야 한다. 자소서만 입력하면 PDF·비전 읽기는 호출하지 않는다.
- 일반 직군은 GitHub·보완 입력을 받지 않는다. IT 직군은 `jobTitle: null`·`coverLetters: []`만 허용하며
  `WORK_SAMPLE`을 허용하지 않는다. 위반은 접수 전에 기존 `422 INVALID_MATERIAL` 응답으로 거절한다.
- 문서가 있어도 읽지 못하면 `UNREADABLE_DOCUMENT`다. 자소서가 없을 때는 이력서 외 문서 하나에서
  직접 텍스트 50자 이상 또는 비전 전사가 있어야 한다. 자소서가 있으면 이 읽기 하한은 통과한다.
- 일반의 근거 카테고리는 2개 이상이어야 하며 자소서 근거만으로 충족할 수 있다.
  비개발의 문서·코드 근거 카테고리 1개 이상 조건은 유지한다.
- v2 `profile.jobTitle`은 일반일 때 포함된다. 비개발 v2에는 키를 추가하지 않는다.
  `sources[].kind`·`materials[].sourceKind`에 `COVER_LETTER`를 더하며 경로는 1부터 `cl-1`~`cl-5`다.
  자소서 **답**의 원문 8~80자 인용을 대조한다. 문항은 근거가 아니며 자기 서술을 검증된 사실로 취급하지 않는다.
- `/questions/cycle`은 일반 v2의 대분류 카테고리·`jobTitle`·`COVER_LETTER`를 사용한다.
  SOLO 3세트×5, MULTI 3세트×2·최근 질문 중복 제외 규칙은 같다. SOLO는 세트별 생성하며 잘린 제출을 버린다.
- `/questions/tailor`의 `profile.jobTitle`, `/questions/tailor/multi` 최상위 `jobTitle`은 선택(최대 30자)이다.
- `/feedback/solo`, `/feedback/multi` 최상위 `jobRole`·`jobTitle`은 선택이며 `jobTitle` 최대 30자다.
  비개발·일반 코드면 직무 문구를 사용한다. 일반의 정확성은 검증 가능한 직무 지식 주장 기준이며
  검증할 주장이 없으면 null이다. MULTI 정확성은 TECH 첫 자리 문항만 사용한다.
  평가 축·가중치·기업 평가 모양은 유지하며 디자인의 시각적 완성도를 평가하지 않는다.
- 개발 또는 직무 누락 요청의 프롬프트·도구 설명은 기존 그대로다. 유료 모델 실품질과 서버 실연동은 미검증이다.

## POST /questions/cycle — 원질문 사이클 생성

종합 데이터만 보고 한 모드의 원질문 사이클을 만든다. SOLO 는 5문항씩 세트 1 → 2 → 3을 순차 생성하고,
MULTI 는 6문항을 한 번에 생성한다. 각 호출은 구성 규칙 위반 또는 출력 잘림 시 기본 1회 재시도한다.
끝내 실패하면 실패 콜백 `500` 이며, 앞서 생성한 세트만 부분 성공으로 보내지 않는다.

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
- `schemaVersion=2`도 받는다. 비개발 직무 카테고리와 저장된 `materials`만 읽고 PDF를 다시 내려받지 않는다. `VISION`의 숫자·기여를 확정 전제로 확대하지 않고, `SUPPLEMENT`는 지원자의 설명으로 다룬다. 세트 수·문항 수·중복 제외 규칙은 v1과 같다.
- `excludeQuestions` 는 같은 모드의 최근 2사이클 질문 본문이다. 앞에서부터 30개(`EXCLUDE_MAX`)까지만 쓴다.
  새 질문이 이 중 하나와 **같은 문장**이면 구성 규칙 위반으로 보고 다시 생성한다. 공백·문장부호·대소문자·전각 차이는
  무시하고, 표현만 바꾼 질문은 잡지 않는다. `+`·`#` 같은 기호는 지우지 않으므로 `C++`와 `C`는 다른 문장이다.
- SOLO 의 다음 세트에는 이미 승인된 세트의 질문을 제외 목록에 추가한다. 이 내부 목록은 30개 제한으로 자르지 않는다.
  앞선 세트와 같은 문장을 내면 해당 세트만 재시도하며, 마지막에 전체 15문항의 구성 규칙도 다시 검사한다.
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

**실패 콜백** — `/profile` 과 같은 형태에서 `code` 만 없다.

| statusCode | 원인 |
|---|---|
| `422` | 지원하지 않는 `schemaVersion`, 근거 경로 집합이 비어 있음, 이용 가능한 카테고리 2종 미만. LLM 을 부르지 않는다 |
| `500` | 재시도 후에도 구성 규칙 위반, LLM 호출 실패, 내부 오류 |

---

## 기업 컨텍스트 공통 계약

신규 엔드포인트나 기업 저장소는 추가하지 않는다. API 서버가 면접 생성 시 고정한 기업 스냅샷을
`/questions/tailor`, `/questions/tailor/multi`, `/feedback/solo`, `/feedback/multi`에 전달한다.

| 요청 필드 | 규칙 |
|---|---|
| `company` | 선택 객체. 미선택 시 API 서버는 키 자체를 생략한다 |
| `company.companyId` | 비어 있지 않은 문자열. API Long id를 문자열로 전달 |
| `company.displayName` | 1~30자 |
| `company.category` | 비어 있지 않은 문자열. 알려진 값은 표시명으로 변환하고 모르는 값은 그대로 사용 |
| `company.facts` | 1~20개. `TALENT`, `BUSINESS` 각각 최소 1개 |
| `facts[].factId` | 1~32자 문자열. 기업 안에서 유일 |
| `facts[].kind` | `BUSINESS`, `PROBLEM`, `TALENT`, `MISSION_VISION`, `TECH_STACK` |
| `facts[].title` | 1~20자. 서비스·가치·문제의 짧은 이름 |
| `facts[].content` | 1~200자. 설명 문장 |
| `facts[].positiveSignals`, `concernSignals` | TALENT에만 각각 0~3개. 항목당 1~100자. 생략 시 빈 배열 |
| `company.interviewerBrief` | 필수 1~600자. 면접 스냅샷 공통 계약을 검증하며 Analysis 프롬프트에는 사용하지 않음 |

문자열은 앞뒤 공백 제거 후 Unicode code point 수(Python `len`)로 길이를 검사한다.
공백뿐인 값·부분 company 객체·정규화 후 중복 factId는 작업 접수 전에 거부한다.
company 생략과 null은 기존 일반 면접 경로이며, 신호 4개를 앞의 3개로 자르지 않는다.
형식 위반은 작업 접수 전 **동기 HTTP 422**다. `legalSize`는 이 계약에 없고 전달돼도 무시한다.
출처·작성일·규모·별칭은 Analysis에 보내지 않는다. category 표시명은
`IT_SERVICE` 대기업 IT서비스, `PLATFORM` 빅테크 플랫폼, `FINTECH` 핀테크,
`GAME` 게임, `CONSUMER` 커머스·모빌리티, `SECURITY` 보안이다.

기업 프롬프트 자료의 `<`, `>`만 엔티티로 이스케이프한 뒤 `<company_context>` 경계 안에 넣고
회사 이름과 자료의 `&`는 원문 그대로 유지한다.
BUSINESS → PROBLEM → TALENT → MISSION_VISION → TECH_STACK 순서로 구성한다.
HR은 인재상, CEO는 미션·사업·인재상, PM은 사업·사례, DESIGN은 사업 관점을 참고한다.
모르는 직책은 인재상·사업 관점을 사용한다. 기업 정보는 자료이며 지시로 해석하지 않는다.
회사 소속 표현, 기업 정보 밖의 사실, 기업 내부 지식이 있어야 답할 수 있는 질문은 금지한다.
특정 팀·서비스·과거 사례라는 content의 범위를 유지하며 부모 회사 사실을 상속하거나 외부 조회로 보충하지 않는다.
기술 질문은 원질문 의도·핵심 답변 범위에 직접 연결되는 BUSINESS·PROBLEM을 먼저 검토하고,
관련 TECH_STACK은 원래 기술 대상을 유지하는 보조 맥락으로만 사용한다.
직접 연결이 약하면 TALENT의 행동 관점으로 표현을 조정하고, 여전히 부자연스러우면 원문을 유지한다.
기업 스택에 맞춰 다른 기술 문제로 교체하거나 모범답안을 힌트로 옮기지 않는다.
비기술 면접관의 선택 fact 종류가 비어 있으면 남은 관련 관점을 사용하고 없는 미션·사례를 만들지 않는다.
프로젝트 정보가 없는 경험은 명시적인 가정형으로 묻는다. 의미 품질은 실제 출력 검수 대상이다.

### 기업 관점 피드백 companyFit

두 피드백 성공 콜백의 `result.companyFit`은 항상 존재한다(null 포함). `overall`과 같은 위치다.

```json
{
  "companyFit": {
    "comment": "샘플커머스가 중요하게 보는 오너십 관점에서 문제 해결 경험이 드러났습니다",
    "matchedFocus": [
      {
        "factId": "23", "kind": "TALENT", "focus": "오너십",
        "evidence": "장애 원인을 직접 추적하고 재발 방지까지 맡은 경험을 설명했습니다",
        "questionIds": ["q1"]
      }
    ],
    "gaps": []
  }
}
```

| 결과 필드 | 규칙 |
|---|---|
| `companyFit` | 기업 미선택 또는 별도 생성 실패 시 null. 기존 피드백은 성공 |
| `comment` | 1~2문장, 1~300자. 앞뒤 공백 제거 후 초과하면 companyFit 전체를 null로 처리 |
| `matchedFocus`, `gaps` | 각각 최대 3개. 같은 목록 안의 factId는 첫 유효 항목만 유지 |
| `factId` | 요청에 존재하는 값만 허용. 없는 값의 항목은 제거. 프롬프트 내부의 URL 인코딩 식별자는 서버가 원래 ID로 복원 |
| `kind`, `focus` | 서버가 요청 fact에서 복사. focus는 title. 모델이 이 필드를 출력하면 해당 항목 제거 |
| `evidence`, `suggestion` | 각각 잘 드러난 근거·보완 제안. 1~150자. 길이 초과 항목은 자르지 않고 제거 |
| `questionIds` | 답변이 있는 문항 ID만 순서 보존 중복 제거. 유효한 ID가 없으면 해당 항목 제거 |

**회사 정보는 점수에 영향을 주지 않는다. 채점 호출에는 별도의 회사 컨텍스트가 들어가지 않는다.**
문자열 길이는 앞뒤 공백을 제거한 뒤 검사한다. comment는 완결된 코멘트를 보존하기 위해
길이가 초과해도 자르지 않는다. evidence·suggestion은 초과한 항목만 제거한다.
문항 ID는 불투명 문자열로 비교하며 음수·문자열 형식을 숫자로 재해석하지 않는다.
원래 항목이 있었으나 근거 검증에서 전부 제거되면 companyFit 전체를 null로 처리한다.
처음부터 두 목록이 빈 정상 결과는 허용하며 총평은 관찰 한계를 중립적으로 설명하도록 지시한다.
도구 출력의 최상위 필드는 comment·matched_focus·gaps만, 항목은 fact_id·evidence 또는 suggestion·question_ids만 허용한다.
잘못된 도구·tool_use 이외 종료 이유·중복 도구 호출·잘림·형식 오류는 null로 처리한다.
interviewerBrief는 API가 고정한 스냅샷의 공통 계약이라 필수로 받는다.
Analysis는 facts로 질문·코멘트를 만들고 Chat은 같은 스냅샷의 요약으로 꼬리질문을 만든다.

기업 관점 코멘트는 채점과 병렬로 호출하며 점수·등급·합격 가능성을 출력하지 않는다.
회사 이름이 이미 질문·답변 본문에 등장하는 경우 그 원문은 기존 채점 입력에 그대로 유지된다.
긍정 행동·직접 말한 우려 행동·설명 부족·관찰 기회 없음을 구분하도록 지시한다.
설명이 없다는 이유로 실제 행동을 하지 않았다고 단정하지 않으며, concernSignals는 직접 나타난 행동에만 적용한다.
유효한 ID의 존재가 코멘트 의미의 사실성을 보장하지는 않으므로 실제 모델 출력은 별도로 검수한다.
기업 코멘트 실패는 null로 격리하지만 기존 채점 실패는 기존 실패 콜백으로 처리한다.
`COMPANY_FIT_TIMEOUT_SECONDS`는 기본 20초인 전체 논리 호출 상한이다.
fit 태스크 시작부터 SDK 내부 대기·재시도까지 포함하며 채점이 끝난 뒤부터 새로 세지 않는다.
시간 초과 시 기업 호출을 취소·정리하고 companyFit=null로 기본 피드백을 유지한다.
신규 네트워크 재시도는 추가하지 않는다. 모델·온도·토큰·모드별 답변 길이 설정은 기존 값을 사용한다.
신규 company_fit 로그에는 적용 여부·사유·항목 수·토큰·지연만 남기며 원문은 넣지 않는다.
채점이 실패하거나 요청이 취소되면 진행 중인 기업 호출도 취소하고 태스크 정리를 마친다.
matchedFocus와 gaps가 같은 factId를 각각 한 번씩 참조하는 것은 허용한다.
병렬 실행으로 순차 호출 지연을 줄이며 전체 지연은 실제 호출 시간에 따라 달라진다.
다대일 기업 코멘트에는 기술 면접관의 어조만 사용한다.

로그의 `company_fit_applied`, `matched_count`, `gap_count`로 적용 여부를 확인한다.
comment 길이 초과는 `company_fit.comment_too_long`과 `actual_length`, `max_length`로 기록하며
코멘트 원문은 남기지 않는다. 다른 형식 오류는 `company_fit.malformed_result`로 구분한다.
배포는 기업 컨텍스트를 받는 Analysis → Chat → API → Web 순서다.
기업 데이터 수집·DB 적재·스냅샷 생성은 이 서버의 범위 밖이다.

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
  "company": {
    "companyId": "3",
    "displayName": "샘플커머스",
    "category": "CONSUMER",
    "facts": [
      { "factId": "21", "kind": "BUSINESS", "title": "샘플마켓", "content": "판매자와 구매자를 연결하는 온라인 거래 서비스" },
      { "factId": "23", "kind": "TALENT", "title": "오너십", "content": "문제를 스스로 정의하고 결과까지 책임진다" }
    ],
    "interviewerBrief": "샘플커머스는 온라인 거래 서비스를 운영하며 문제를 스스로 정의하고 결과까지 책임지는 오너십을 중요하게 보는 가상의 기업이다"
  },
  "callbackUrl": "https://api.example.com/callbacks/feedback"
}
```

`company`는 선택 필드다. 기업을 선택하지 않았으면 키를 생략한다(null도 미선택으로 처리).
기업 필드와 검증 규칙은 [기업 컨텍스트 공통 계약](#기업-컨텍스트-공통-계약)을 따른다.
기업 정보는 채점 호출에 별도로 넣지 않는다. 별도 LLM 호출로 만든 `result.companyFit`을 성공 콜백에 추가하며 기업이 없거나 생성에 실패하면 null이다.

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
    "companyFit": null,
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

질문 풀(`/questions/cycle`)에서 꺼낸 원질문을, 지원자의 사전 정보에 맞게 **본문만** 다시 쓴다.
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
  "company": {
    "companyId": "3",
    "displayName": "샘플커머스",
    "category": "CONSUMER",
    "facts": [
      { "factId": "21", "kind": "BUSINESS", "title": "샘플마켓", "content": "판매자와 구매자를 연결하는 온라인 거래 서비스" },
      { "factId": "23", "kind": "TALENT", "title": "오너십", "content": "문제를 스스로 정의하고 결과까지 책임진다" }
    ],
    "interviewerBrief": "샘플커머스는 온라인 거래 서비스를 운영하며 문제를 스스로 정의하고 결과까지 책임지는 오너십을 중요하게 보는 가상의 기업이다"
  },
  "callbackUrl": "https://api.example.com/callbacks/tailor"
}
```

`company`는 선택 필드다. 기업을 선택하지 않았으면 키를 생략한다(null도 미선택으로 처리).
기업 필드와 검증 규칙은 [기업 컨텍스트 공통 계약](#기업-컨텍스트-공통-계약)을 따른다.
기업이 없으면 기존 시스템 프롬프트와 사용자 메시지가 바이트 단위로 같다.
기업이 있으면 질문의 상황·관점만 바꾼다. intention과 같은 핵심 답변으로 답할 수 있어야 한다. 기업만으로 사전 정보 검사를 대체하지 않는다.

- `questions` 는 1~10개, `id` 중복 불가(422).
- `intention`(선택)은 **보존해야 할 검증 포인트**다. 재작성된 질문으로도 같은 것을 확인할 수 있어야 한다.
  `expectedAnswer` 는 참고 답안으로만 프롬프트에 싣는다.
- `intention` 이 없으면(예전 원질문) 지금처럼 `expectedAnswer` 를 검증 포인트로 쓴다.
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
    "questions": [{ "id": 1, "question": "Redis를 선택한 이유는 무엇인가요?", "spokenText": "레디스를 선택한 이유는 무엇인가요?" }]
  }
}
```

바뀌는 것은 본문뿐이라 `category` / `intention` / `expectedAnswer` / `basedOn` 은 돌려주지 않는다.
호출자가 들고 있는 원본을 그대로 쓰면 된다.

`spokenText`는 같은 질문을 음성으로 읽기 위한 발음 문장이다. 영문·숫자·기호를 한국어 읽는 표기로
변환하며, 누락·공백·비문자열·길이 초과이면 `null`을 돌려준다. 발음 문장만 없다고 재작성에 실패하지는
않는다. 분석 서버는 앞뒤 공백과 길이만 확인하고, 최종 검증과 원문 TTS 폴백은 Chat 서버가 맡는다.

**`tailored: false` — 원질문 폴백**

LLM 호출 실패, 응답 파싱 실패, 일부 문항 누락이면 **실패 콜백을 보내지 않는다.**
원질문은 이미 유효한 산출물이라, 재작성이 안 됐다고 면접을 못 열게 만드는 편이 더 손해다.
이 경우 `questions` 에는 요청에 실려온 원문이 그대로 담기고 `tailored` 가 `false` 가 된다.
각 질문의 `spokenText`는 `null`이며, 발음 문장을 만들기 위한 추가 LLM 호출은 하지 않는다.

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
  "company": {
    "companyId": "3",
    "displayName": "샘플커머스",
    "category": "CONSUMER",
    "facts": [
      { "factId": "21", "kind": "BUSINESS", "title": "샘플마켓", "content": "판매자와 구매자를 연결하는 온라인 거래 서비스" },
      { "factId": "23", "kind": "TALENT", "title": "오너십", "content": "문제를 스스로 정의하고 결과까지 책임진다" }
    ],
    "interviewerBrief": "샘플커머스는 온라인 거래 서비스를 운영하며 문제를 스스로 정의하고 결과까지 책임지는 오너십을 중요하게 보는 가상의 기업이다"
  },
  "callbackUrl": "https://api.example.com/callbacks/tailor"
}
```

`company`는 선택 필드다. 기업을 선택하지 않았으면 키를 생략한다(null도 미선택으로 처리).
기업 필드와 검증 규칙은 [기업 컨텍스트 공통 계약](#기업-컨텍스트-공통-계약)을 따른다.
기업이 없으면 기존 시스템 프롬프트와 사용자 메시지가 바이트 단위로 같다.
기업이 있으면 기술 질문 재작성과 비기술 질문 생성에 모두 반영한다. 비기술 면접관마다 기업 관점 질문을 최소 1개 생성하도록 지시한다.

`questions[].intention` 은 선택이다. `projectSummary` 는 `/profile` 결과의 `projectSummary` 를 넣는다.

기술 질문 수는 `techPersona.questionCount`와 `questions` 개수가 같아야 한다. 생성된 결과는 기술 면접관 질문이 먼저 오고, 이후 `otherPersonas` 순서대로 온다. 기술 질문 재작성 실패와 신규 질문 생성 실패 모두 실패 콜백을 보낸다. 성공 결과에는 `questions`가 있으며 `tailored` 필드는 없다.

**성공 콜백의 `intention`** — 질문마다 항상 채워진다.

- 기술 질문: 입력 `intention` 을 그대로 돌려준다. 없으면(레거시) `expectedAnswer` 로 채운다. LLM 이 다시 쓰지 않는다.
- 비개발 질문: 생성할 때 함께 만든다. 이 값은 질문 풀에 없고 **이 콜백으로만** 전달되므로 API 서버가 받아 저장해야 한다.

기술·비개발 질문 모두 `spokenText`를 함께 돌려준다. SOLO와 같이 발음 문장만 누락되거나 유효하지
않으면 `null`로 성공한다. 신규 질문 본문이 서버 상한(250자)으로 잘리면 본문과 의미가 어긋나지 않도록
발음 문장도 `null`로 돌려준다. 요청 DTO와 질문 풀(`/questions/cycle`)은 변경하지 않는다.

```json
{
  "id": 6,
  "personaId": "102",
  "category": "motivation",
  "question": "...",
  "spokenText": null,
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
  "company": {
    "companyId": "3",
    "displayName": "샘플커머스",
    "category": "CONSUMER",
    "facts": [
      { "factId": "21", "kind": "BUSINESS", "title": "샘플마켓", "content": "판매자와 구매자를 연결하는 온라인 거래 서비스" },
      { "factId": "23", "kind": "TALENT", "title": "오너십", "content": "문제를 스스로 정의하고 결과까지 책임진다" }
    ],
    "interviewerBrief": "샘플커머스는 온라인 거래 서비스를 운영하며 문제를 스스로 정의하고 결과까지 책임지는 오너십을 중요하게 보는 가상의 기업이다"
  },
  "callbackUrl": "https://api.example.com/callbacks/feedback"
}
```

`company`는 선택 필드다. 기업을 선택하지 않았으면 키를 생략한다(null도 미선택으로 처리).
기업 필드와 검증 규칙은 [기업 컨텍스트 공통 계약](#기업-컨텍스트-공통-계약)을 따른다.
기업 정보는 채점 호출에 별도로 넣지 않는다. 별도 LLM 호출로 만든 `result.companyFit`을 성공 콜백에 추가하며 기업이 없거나 생성에 실패하면 null이다.

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

문서 분석 1차 전용 설정(기존 개발 호출 설정은 유지):

| 변수 | 기본값 |
|---|---|
| `INTERVIEW_QA_PAGE_READER_MODEL` | `claude-haiku-5-5` (M-1 실측 전 후보) |
| `INTERVIEW_QA_PAGE_READER_CONCURRENCY` / `PAGE_READER_BATCH_SIZE` / `PAGE_READER_MAX_TOKENS` | 5 / 50단위 / 4096 |
| `INTERVIEW_QA_PAGE_RENDER_LONG_EDGE_PX` / `PAGE_RERENDER_LONG_EDGE_PX` | 1568 / 2400 |
| `INTERVIEW_QA_PAGE_TILE_ASPECT` / `PAGE_TILE_MAX` | 3.0 / 8 |
| `INTERVIEW_QA_PAGE_TEXT_RICH_CHARS` / `PAGE_TEXT_POOR_CHARS` | 200 / 50 |
| `INTERVIEW_QA_PAGE_IMAGE_SOME` / `PAGE_IMAGE_FIGURE` / `PAGE_IMAGE_DOMINANT` | 0.15 / 0.3 / 0.5 |
| `INTERVIEW_QA_PAGE_DRAWINGS` / `PAGE_BROKEN_RATIO` | 30 / 0.3 |
| `PROFILE_DOC_INPUT_TOKEN_LIMIT` | 100000 |

표에서 접두사를 생략한 뒤쪽 변수에도 같은 `INTERVIEW_QA_` 접두사를 붙인다.
50단위는 배치 크기이며, 이를 넘는 영역도 다음 배치로 처리한다. SDK 재시도와 확대 재읽기 1회는 따로 기록한다.

전부 환경 변수로 덮어쓴다(`.env` 지원). 기본값과 각 값을 그렇게 정한 이유는
[`src/app/main/config.py`](../src/app/main/config.py) 주석에 있다.

| prefix | 대상 | 자주 건드리는 값 |
|---|---|---|
| `APP_` | 서비스 공통 | `LOGGING_LEVEL`(DEBUG 면 단계별 풀 페이로드 로깅) |
| `ANTHROPIC_` | LLM 자격증명·모델 | `API_KEY`(필수, 없으면 부팅 실패), `TEXT_MODEL`, `VISION_MODEL` |
| `INTERVIEW_QA_` | `/profile` 입력 처리(1~3단계)·파일 읽기 | 트리아지 임계값, 비전 호출 상한, 외부 I/O timeout, 웹훅 |
| `FEEDBACK_SOLO_` | `/feedback/solo` | `GRADING_MAX_TOKENS`, `ANSWER_MAX_CHARS` |
| `COMPANY_FIT_` | 기업 관점 코멘트 | `MODEL`(미설정 시 `ANTHROPIC_TEXT_MODEL`), `MAX_TOKENS`(1500), `TEMPERATURE`(0.2), `TIMEOUT_SECONDS`(20) |
| `FEEDBACK_MULTI_` | `/feedback/multi` | `GRADING_MAX_TOKENS`, `ANSWER_MAX_CHARS` |
| `QUESTION_TAILOR_` | `/questions/tailor` | `REWRITE_MAX_TOKENS`, `QUESTION_MAX_CHARS`, `SPOKEN_TEXT_MAX_CHARS` |
| `QUESTION_TAILOR_MULTI_` | `/questions/tailor/multi` | `GENERATE_MAX_TOKENS`, `TEXT_MAX_CHARS`, `SPOKEN_TEXT_MAX_CHARS` |
| `PROFILE_` | `/profile` 탐색 루프 | `MAX_TURNS`, `TOKEN_LIMIT`, `RESPONSE_MAX_TOKENS`(파일 읽기 상한은 `INTERVIEW_QA_` 값 공유) |
| `QUESTION_CYCLE_` | `/questions/cycle` | `MAX_TOKENS`, `EXPECTED_ANSWER_MAX_CHARS`, `RETRY`, `EXCLUDE_MAX`, `TEXT_MAX_CHARS` |

`COMPANY_FIT_TIMEOUT_SECONDS`는 유한한 양수, `COMPANY_FIT_MAX_TOKENS`는 양수, `COMPANY_FIT_TEMPERATURE`는 0~1이어야 한다.
잘못된 설정은 앱 기동 시 검증 오류로 거부한다.

웹훅 설정(`WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_RETRY_DELAY_SECONDS`)은 `INTERVIEW_QA_` prefix
아래 있지만 모든 작업형 엔드포인트의 콜백에 적용된다.

`QUESTION_CYCLE_MAX_TOKENS`(12,288) 는 각 LLM 호출의 출력 상한이다. SOLO 는 세트당 5문항으로 나눠
받으며, `stop_reason=max_tokens` 응답은 도구 입력을 파싱할 수 있어도 버린다. 잘린 도구 호출 이력은
재사용하지 않고 같은 세트의 새 대화로 재시도한다. `QUESTION_CYCLE_RETRY`는 사이클 전체가 아닌 각 호출에 적용된다.
기본 재시도 1회에서 SOLO 는 정상 3회·최대 6회, MULTI 는 정상 1회·최대 2회 호출한다.
SOLO 는 입력 종합 데이터를 세 번 보내므로 입력 토큰 비용과 전체 소요 시간을 배포 후 확인한다.

#### 질문 사이클 장애 로그

기본 로그 포맷에서도 다음 정보가 메시지에 표시된다.

- `question_cycle.generate.violations`: `job_id`, `mode`, `sets`, `attempt`, 위반 개수와 구체적인 규칙 위반 사유
- `question_cycle.generate.truncated`: 같은 작업·세트 정보와 `max_tokens`, `output_tokens`
- `question_cycle.generate.call_failed`: `job_id`와 클라이언트 오류 종류(`kind`)
- `question_cycle.dispatch.callback_dropped`: 전송 재시도를 소진한 콜백의 `job_id`와 생성 결과 상태

위반 사유는 최대 20개·각 300자로 제한한다. 중복 질문의 본문은 남기지 않고 URL 쿼리와 인증 정보를 가린다.
콜백 전송 실패는 완료(`dispatch.done`)로 기록하지 않는다. 현재 콜백은 기존처럼 1회 재시도 후 포기하며
영속 재전송 큐는 없다. DNS 장애는 별도로 실행 컨테이너의 DNS·외부 연결을 점검해야 한다.

### N:1 최대 구성 검증 범위

질문 재작성의 기본 출력 상한은 `QUESTION_TAILOR_REWRITE_MAX_TOKENS=3072`, 신규 생성은
`QUESTION_TAILOR_MULTI_GENERATE_MAX_TOKENS=4608`이다. 발음 문장 상한은
`QUESTION_TAILOR_SPOKEN_TEXT_MAX_CHARS=600`, `QUESTION_TAILOR_MULTI_SPOKEN_TEXT_MAX_CHARS=600`이다.
상한을 넘는 발음 문장은 자르지 않고 버린다. 기존 환경변수에 출력 토큰 상한을 지정했다면 배포 시 함께 올린다.
Analysis의 새 콜백 필드를 저장·전달할 수 있도록 Chat → API → Analysis 순서로 배포한다.

자동 테스트는 LLM 스텁으로 문항 배분·ID·누락·콜백 계약을 검증한다. 테일러 발음 샘플과
기술 5문항 + 비기술 3명 × 2문항의 실제 LLM 실측은 [발음 문장 검증 결과](tts-analysis-validation.md)에 있다.
반복·부하 실측과 토큰 상한 조정은 후속 작업이다. `/questions/tailor/multi`와 `/feedback/multi`에서 콜백 완료 시간, 호출별 출력 토큰,
`stop_reason`, 절단율 및 최종 실패율을 측정한다. 꼬리질문 최대 수는 채팅 서버 정책 확인 후 정한다.
기존 5인 기록을 잘라내거나 수정하는 데이터 마이그레이션은 수행하지 않는다.


음성 분석의 신규 S3 요청·별도 콜백 계약은 [audio-api.md](audio-api.md)를 참고한다.
음성 분석은 기존 텍스트 피드백 경로와 분리한다. 기업 컨텍스트 선택 필드와 companyFit은 위 계약을 따른다.


### PDF 텍스트 복구 설정

복구 단계는 native 원문 추출과 정제 사이에 실행하며 이미지 개수 분기와 독립적이다.
정상 native 본문은 유지하고 의심 페이지에만 미리보기·Vision 전사를 요청한다.
원문·채택 본문·출처·미판독 영역을 분리하고 불확실한 전사는 확정 본문에서 제외한다.
기존 그림 의미 분석은 유지한다. 복구 예산은 이미지 의미 분석의 호출 상한과 별도다.

아래 이름 앞에 모두 `INTERVIEW_QA_`를 붙인다.

| 이름 | 기본값 | 의미 |
|---|---|---|
| `PDF_TEXT_RECOVERY_ENABLED` | `false` | 선별 실문서 평가 후 활성화 |
| `PDF_TEXT_RECOVERY_MAX_PAGES` | `20` | 요청당 복구 후보 페이지 상한 |
| `PDF_TEXT_RECOVERY_MAX_CALLS` | `40` | 최초 전사와 확대 타일의 논리 client 호출 합계 |
| `PDF_TEXT_RECOVERY_CONCURRENCY` | `2` | 요청별 렌더·전사 동시 작업 상한 |
| `PDF_PROCESS_WORKERS` | `2` | 앱 인스턴스가 요청 간 공유하는 PDF 프로세스 수 |
| `PDF_TEXT_RECOVERY_MAX_TOKENS` | `8192` | 전사 응답 토큰 상한 |
| `PDF_TEXT_RECOVERY_CALL_TIMEOUT_SECONDS` | `30` | SDK 내부 재시도 포함 논리 호출 제한 |
| `PDF_TEXT_RECOVERY_TOTAL_TIMEOUT_SECONDS` | `120` | 복구 단계 전체 제한 |
| `PDF_TEXT_RECOVERY_RENDER_DPI` | `150` | 전체 페이지·타일 렌더 시작 DPI |
| `PDF_TEXT_RECOVERY_RENDER_MAX_PIXELS` | `12000000` | 렌더당 픽셀 수 상한 |
| `PDF_TEXT_RECOVERY_MAX_TILES_PER_PAGE` | `4` | 추가 확대 타일 수 상한 0~4 |

모델은 `ANTHROPIC_VISION_MODEL`, 전송 이미지의 긴 변은 기존
`INTERVIEW_QA_VISION_RESIZE_LONG_EDGE_PX`를 따른다. 렌더 픽셀 상한과 모델 입력 해상도는 별개다.
PDF 작업은 앱이 생성·종료하는 공유 프로세스 풀에서 실행하고 각 작업이 문서를 직접 열고 닫는다.
워커 수의 2배를 넘는 진행·대기 작업은 신규 제출을 거절한다. 손상된 풀은 한 번만 재생성한다.
요청 취소 후에도 실행 중인 프로세스가 끝날 때까지 슬롯을 유지하고 늦은 결과는 버린다.
전체 페이지 최초 전사에 예산을 우선 배정하고 남은 호출을 페이지별 타일에 나눠 배정한다.
별도 네트워크 재시도 루프는 없으며 SDK 재시도까지 호출 제한 시간이 감싼다.

자동 테스트는 제어 흐름·좌표·병합·콜백 계약을 검증한다. 배포 전 실제 한글·영문·스캔·혼합·표·다단
문서와 정답 전사를 사용해 CER, 숫자·고유명사 정확도, 탐지 누락·오탐, 부분 복구율, 토큰·지연,
PDF 프로세스 간 복사 비용과 기존 그림 의미 분석의 중복 호출 비용을 측정해야 한다.
정상 문자로 잘못 매핑된 글자와 일부 작은 문구·읽기 순서 문제는 초기 탐지 규칙이 놓칠 수 있다.
제목·연락처만 남은 경우의 유용성도 이 평가에 포함한다. 정제 후 급감은 진단만 남기며 재호출하지 않는다.
실문서 평가 전에는 기본 비활성 상태를 유지한다. 자동 테스트 통과는 OCR 품질 검증을 대신하지 않는다.
