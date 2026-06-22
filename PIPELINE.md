# Interview Q&A 파이프라인

포트폴리오 PDF + GitHub 저장소 URL 입력을 받아 면접 질문 5개와 모범답변을 만들고, 결과를 `callback_url`로 POST 하기까지의 전체 흐름 정리.

---

## 프로젝트 구조 (한눈에)

```
src/app/
├── main/                          # 부트스트랩·설정·DI 컨테이너
│   ├── config.py
│   ├── run.py
│   └── ioc/                       # core/outbound provider 등록
├── inbound/http/                  # FastAPI 라우터
│   ├── exception_handlers.py
│   └── interview_qa/
│       ├── router.py              # 실제 파이프라인 엔드포인트
│       ├── mock_router.py         # mock 응답용
│       ├── dto.py                 # HTTP 요청/응답 모델
│       └── mock_payload.py
├── core/                          # 도메인 로직 (FastAPI 비의존)
│   ├── commands/
│   │   └── dispatch_interview_qa.py   # 백그라운드 디스패처 (use case 진입점)
│   └── common/interview_qa/
│       ├── dto.py                 # 단계 간 DTO + 콜백 페이로드
│       ├── errors.py              # PipelineError
│       ├── constants.py           # 트리 SKIP_DIRS/EXTS/FILES
│       ├── prompts.py             # Stage 4 시스템 프롬프트
│       ├── tools.py               # Stage 4 read_files / generate_result tool 정의
│       ├── image_context.py       # 이미지 주변 텍스트 추출
│       ├── image_resize.py        # 비전 호출 전 PNG 리사이즈
│       ├── stage1_validation.py
│       ├── stage2_pdf_extract.py
│       ├── stage2_image_triage.py
│       ├── stage2_image_llm_triage.py
│       ├── stage2_image_structuring.py
│       ├── stage2_document_merge.py
│       ├── stage3_repo_tree.py
│       ├── stage4_llm_session.py
│       ├── stage4_file_reader.py
│       └── ports/                 # 외부 의존성 인터페이스(포트)
│           ├── pdf_fetcher.py
│           ├── github_metadata_client.py
│           ├── github_tarball_fetcher.py
│           ├── anthropic_text_client.py
│           └── webhook_client.py
└── outbound/adapters/             # 포트 구현(httpx, anthropic SDK)
    ├── httpx_pdf_fetcher.py
    ├── httpx_github_metadata_client.py
    ├── httpx_github_tarball_fetcher.py
    ├── httpx_webhook_client.py
    └── anthropic_text_client_impl.py
```

### 설계 컨셉

- `core`는 FastAPI / httpx 같은 외부 기술과 무관한 순수 도메인.
- 외부 호출(HTTP, LLM, GitHub)은 모두 `ports/*` 인터페이스로 정의하고 `outbound/adapters/*`가 구현. Stage 서비스는 포트만 알면 됨.
- 단계 서비스는 모두 `async def execute(...)` 하나의 시그니처로 통일.
- 단계 사이 자료형은 `core/common/interview_qa/dto.py`의 Pydantic 모델로 고정.

---

## 전체 흐름

### 1줄 요약

```
HTTP POST → 라우터가 BackgroundTasks 로 DispatchInterviewQa.execute 예약
         → Stage 1 ~ Stage 5 순차 실행
         → 성공/실패 페이로드를 callback_url 로 POST
```

### 단계별 흐름

```
JobRequest(portfolio_url, github_urls, callback_url)
  │
  ▼
[Stage 1]  입력 검증 (PDF 다운로드+fitz 검사, GitHub URL→API public 확인)
  │  Stage1Result(pdf_bytes, repos)
  ▼
[Stage 2-1] PDF 추출 + 노이즈 제거         (fitz, asyncio.to_thread)
  │  ParsedPortfolio(pages[text_blocks + image_blocks])
  ▼
[Stage 2-2] 규칙 기반 이미지 트리아지 + 분기 판정
  │  TriagedPortfolio(pages, branch=text_heavy|image_heavy, info_img_count)
  ▼
[Stage 2-3] (image_heavy 일 때만) LLM 2차 트리아지
  │  TriagedPortfolio (장식 이미지 추가 제거)
  ▼
[Stage 2-4] (image_heavy 일 때만) 비전 모델로 이미지 구조화
  │  StructuredPortfolio(pages, structured_images, branch)
  ▼
[Stage 2-5] 통합 문서 병합 (텍스트 + 이미지 구조화 결과 합치기)
  │  MergedDocument(portfolio_text, branch)
  ▼
[Stage 3]   GitHub tarball 다운로드 + 압축해제 + 역할 판정 + 트리 생성
  │  Stage3Result(repos[name, role, file_paths], tree_text, path_index)
  ▼
[Stage 4]   LLM 코드 탐색 세션 (read_files ↔ generate_result 루프)
  │  raw_result(dict)
  ▼
[Stage 5]   InterviewQaResult 로 Pydantic 검증 → CallbackSuccess 페이로드 조립
  │
  ▼
WebhookClient.send(callback_url, payload)
```

---

## DispatchInterviewQa (백그라운드 디스패처)

`src/app/core/commands/dispatch_interview_qa.py`

### 책임

- 라우터가 `BackgroundTasks.add_task(dispatch.execute, job_id, job_request)`로 예약.
- 1~5 단계를 순차 실행하고 결과를 콜백 URL로 POST.
- 어떤 예외도 외부로 전파하지 않음 (백그라운드라서). 모든 오류는 콜백 페이로드로 변환.

### 의존성 (생성자 주입)

- `WebhookClient`
- `Stage1Validation`, `Stage2PdfExtract`, `Stage2ImageTriage`, `Stage2ImageLlmTriage`, `Stage2ImageStructuring`, `Stage2DocumentMerge`, `Stage3RepoTree`, `Stage4LlmSession`

### `execute(job_id, job_request)`

1. `_build_payload` 실행.
2. 알 수 없는 내부 예외가 터지면 `CallbackFailure(500, "내부 오류로 작업을 완료하지 못했습니다.")`로 변환.
3. `WebhookClient.send(callback_url, payload)` 호출.

### `_build_payload(job_id, job_request)`

- Stage 1 → 2-1 → 2-2 → 2-3 → 2-4 → 2-5 순차 await.
- `tempfile.TemporaryDirectory(prefix="interview_qa_")` 컨텍스트 안에서 Stage 3 + Stage 4 실행. → Stage 4의 `read_files`가 디스크 파일을 열어야 하므로 두 단계가 한 컨텍스트에 묶임.
- 마지막에 `_build_success_payload`로 결과 dict → `InterviewQaResult` Pydantic 검증 → `CallbackSuccess` 직렬화.
- 도중 `PipelineError` 발생 시 → `CallbackFailure(status_code, message)`로 변환.

### `_build_success_payload`

- Stage 4가 돌려준 raw `dict`를 `InterviewQaResult.model_validate`로 한 번 더 검증.
- 검증 실패 → `PipelineError(500, "면접 질문 생성 결과가 형식을 충족하지 못했습니다.")`.
- 성공 시 `CallbackSuccess(job_id=..., result=...)` → `.model_dump()`으로 dict 반환.

---

## Stage 1 — 입력 검증

`stage1_validation.py` / 산출물: `Stage1Result(pdf_bytes, repos)`

### 처리

1. `PdfFetcher.fetch(portfolio_url)` → 바이트 다운로드.
2. `fitz.open(stream=...)`로 열어 `page_count >= 1` 확인.
3. 각 GitHub URL을 `urlparse`로 분해 → `owner / repo` 추출 (`.git` suffix 제거).
4. `GithubMetadataClient.get_repo(owner, repo)`로 메타 조회 → `None` 또는 `is_private=True`면 거부.

### 실패 매핑

| 상황 | 상태 코드 | 메시지 |
| --- | :---: | --- |
| PDF 다운로드 실패 / 0바이트 / fitz 파싱 실패 | 422 | 유효한 PDF 파일이 아닙니다. 진행할 수 없습니다. |
| GitHub URL 형식 오류 | 422 | GitHub 저장소 URL 형식이 올바르지 않습니다. |
| GitHub API 실패 / 404 / private | 403 | github 저장소 상태를 public으로 변경해주세요. |

### 의존 포트

`PdfFetcher`, `GithubMetadataClient`

---

## Stage 2-1 — PDF 추출 + 노이즈 제거

`stage2_pdf_extract.py` / 산출물: `ParsedPortfolio(pages)`

### 처리

- `asyncio.to_thread`로 fitz 호출(CPU-bound)을 워커 스레드에서 실행.
- 페이지마다:
  - `page.get_text("blocks")`로 텍스트 블록 + 좌표(bbox) 수집.
  - `page.get_images(full=True)` + `extract_image` + `get_image_rects`로 이미지 바이너리·크기·좌표·xref 수집.
- 모든 페이지 추출 후 노이즈 제거 적용.

### 노이즈 제거 규칙

| 종류 | 방식 |
| --- | --- |
| 머리말/꼬리말 | 전체 페이지의 `header_footer_min_ratio` 이상에 동일 등장하는 줄 제거 |
| 페이지 번호 | `^\d+$`, `- 3 -`, `Page 3`, `3 / 10` 패턴 |
| 목차 | 앞쪽 `toc_front_pages` 페이지에서 `..........5` 점선 패턴 |
| 이메일 | `[\w.+-]+@[\w-]+\.[\w.-]+` 라인 내 부분만 치환 |
| 전화 | `+82-10-xxxx-xxxx` 류 부분만 치환 |
| 다중 공백 | `[ \t]{2,}` → 단일 공백 |

> 원칙: **보수적으로 제거** (애매하면 보존). 정보 손실보다 노이즈 잔존이 안전.

---

## Stage 2-2 — 규칙 기반 이미지 트리아지 + 분기 판정

`stage2_image_triage.py` / 산출물: `TriagedPortfolio(pages, branch, info_img_count)`

### 장식 판정 규칙 (하나라도 걸리면 장식)

1. 같은 `xref`가 `repeated_min_ratio` 이상 페이지에 등장 → 머리/꼬리 로고.
2. width/height 메타가 비정상 (`<= 0`).
3. 짧은 변 < `min_px` (저해상도 아이콘).
4. (긴변/짧은변) > `max_aspect_ratio` (가로 띠/세로 선).
5. 페이지 면적 대비 bbox 비율 < `min_area_ratio` (우표 크기).

### 분기 판정

- 통과한 정보성 이미지 총 개수 ≥ `info_img_threshold` → `image_heavy`
- 미만 → `text_heavy` (이후 Stage 2-3, 2-4 건너뜀)

---

## Stage 2-3 — LLM 2차 이미지 트리아지 (image_heavy 전용)

`stage2_image_llm_triage.py` / 산출물: `TriagedPortfolio` (장식이 더 빠진 형태)

### 처리

- `branch != "image_heavy"`면 그대로 통과.
- 각 이미지에 대해 `(image_id=p{page}_x{xref}, page, size, bbox, surrounding_text)` 메타 수집.
  - `surrounding_text`는 `image_context.surrounding_text`로 이미지 위·아래 `context_vertical_px` 범위의 텍스트 모음 (없으면 페이지 상위 2블록 fallback).
- Claude 텍스트 모델에 한 번에 전체 후보를 보내고 `triage_judgment` tool 호출 강제.
- 응답의 `judgments[].verdict == "decorative"`인 image_id만 추가로 제거.
- LLM 호출 실패·파싱 실패 → 빈 dict → 결과적으로 **모두 informative 유지** (안전 우선).

### 입력 (LLM에 보내는 것)

텍스트만 (픽셀 바이너리 ✗). 메타데이터 + 주변 텍스트 기반 판정.

---

## Stage 2-4 — 비전 이미지 구조화 (image_heavy 전용)

`stage2_image_structuring.py` / 산출물: `StructuredPortfolio(pages, structured_images, branch)`

### 처리

- `branch != "image_heavy"`면 `structured_images=[]` 빈 결과 반환.
- 후보 (page, image) 쌍을 모은 뒤 `max_images_per_request` 초과분은 드롭.
- `asyncio.Semaphore(concurrency)`로 동시 호출 상한.
- 한 장당:
  1. `image_resize.resize_for_vision`으로 긴 변 `resize_long_edge_px` 이내로 PNG 재인코딩 (다이어그램 텍스트 가독성 위해 JPEG 회피).
  2. base64 + 주변 텍스트 + 페이지 번호를 비전 모델에 전송.
  3. `structure_image` tool 호출 강제 → `{image_type, summary, tech_signals}` 수집.
- 개별 실패는 그 한 장만 누락. 전체 흐름은 계속.

### image_type 분류

`architecture_diagram | erd | ui_screenshot | flow_diagram | chart | other`

---

## Stage 2-5 — 통합 문서 병합

`stage2_document_merge.py` / 산출물: `MergedDocument(portfolio_text, branch)`

### 처리

- 페이지 순서대로 텍스트 블록을 `\n\n`으로 연결.
- `image_heavy` 분기에 한해 각 페이지 텍스트 직후에 같은 `source_page`의 `ImageStructure`들을 끼워 넣음.
- 이미지 블록 포맷:
  ```
  [이미지: architecture_diagram, p.4]
  API Gateway 뒤에 주문/결제/재고 서비스가 분리된 MSA 구조, Kafka 사용
  기술 신호: MSA, Kafka, API Gateway
  ```
- 이후 단계는 원본 이미지를 다시 보지 않음. Stage 4 입력은 오직 `portfolio_text` 문자열.

---

## Stage 3 — 저장소 tarball + 역할 판정 + 트리

`stage3_repo_tree.py` / 산출물: `Stage3Result(repos, tree_text, path_index)`

### 처리 (저장소별 직렬)

1. `GithubTarballFetcher.fetch(owner, name, default_branch)`로 `tar.gz` 바이트 다운로드.
2. `{working_dir}/{repo_name}_extracted/`에 압축 해제. `tarfile.extractall(..., filter="data")`로 path traversal 차단.
3. 추출된 단일 루트(`owner-repo-sha/`)를 저장소 루트로 사용.
4. 역할 판정 (`_detect_role`):
   - **1차: 저장소 이름 토큰 매칭**
     - `ai/ml/infer/model` → `ai_server`
     - `api/server/backend` → `api_server`
     - `web/front/client/ui` → `frontend`
     - `infra/deploy/ops` → `infra`
   - **2차: 의존성 파일**
     - python(`requirements.txt`, `pyproject.toml`, `Pipfile`, `setup.py`, `setup.cfg`) 안에 `torch / transformers / tensorflow / langchain / openai` → `ai_server`
     - `build.gradle` / `build.gradle.kts` / `pom.xml` 존재 → `api_server` (JVM 백엔드)
     - `package.json`에 `react / vue / next / @angular/core` → `frontend`, 아니면 `api_server`
   - 못 정하면 `unknown`
5. `Path.walk`로 파일 경로 수집. `SKIP_DIRS / SKIP_EXTS / SKIP_FILES` (`constants.py`)로 빌드 산출물·바이너리·잠금파일 제외.

### 산출 데이터

- `repos: list[RepoTree]` — 저장소별 role + 정렬된 상대 경로 목록.
- `tree_text` — Stage 4 프롬프트에 들어갈 사람이 읽기 좋은 트리.
  ```
  [api_server] order-api
    src/main/...
    build.gradle

  [frontend] order-web
    ...
  ```
- `path_index: dict[str, str]` — `"order-api/src/main/.../File.java" → 디스크 절대경로`. Stage 4 `read_files`가 경로 검증 + 실제 파일 조회에 사용.

### 실패 정책

한 저장소의 tarball 다운로드 실패 → 그 저장소만 `role=unknown, file_paths=[]`로 진행, 다른 저장소는 계속.

---

## Stage 4 — LLM 코드 탐색 세션

`stage4_llm_session.py` + `stage4_file_reader.py` + `tools.py` + `prompts.py`
산출물: `dict` (generate_result tool input 그대로)

### 초기 사용자 메시지 (`prompts.build_initial_user_message`)

```
[포트폴리오]
{merged.portfolio_text}

[파일 트리]
{repos_tree.tree_text}

포트폴리오에서 (1) 핵심 기능과 (2) 트러블슈팅 서술을 먼저 찾아라.
그 둘에 해당하는 실제 코드를 파일 트리에서 골라 read_files로 열어 분석하라.
분석이 끝나면 generate_result로 결과를 제출하라.
```

### 시스템 프롬프트 (`prompts.SYSTEM_PROMPT_STAGE4`)

- 역할: 시니어 개발 면접관.
- 우선 분석 대상: 포트폴리오 핵심 기능 + 트러블슈팅 서술.
- 절차: read_files로 코드 열고 → 추가 필요 시 더 읽고 → generate_result로 종료.
- 제약: 트리에 존재하는 정확한 경로만, 코드 읽기 전 추측 금지, role 다른 레포 구분, 레포 간 연동 분석.
- 질문 생성 규칙: 정확히 5개, 카테고리(tech_choice / implementation / troubleshooting / integration / structure) 고루, 각 질문에 `based_on` 근거 필수, 일반론 질문 금지.

### 사용 도구 (`tools.STAGE4_TOOLS`)

- **`read_files`** — `{paths: string[]}` 형식. 분석할 소스 파일 본문 요청.
- **`generate_result`** — 최종 JSON 제출. 호출되면 세션 종료 신호. `input_schema`에 enum/min/max 박혀 있어 1차 검증 역할.

### 루프 (`Stage4LlmSession.execute`)

1. 초기 user 메시지를 `messages`에 push.
2. `for turn in range(max_turns):`
   - 누적 input 토큰 > `token_limit`이고 아직 경고 안 했으면 한 번만 "토큰 한도 도달, 즉시 generate_result 호출하라" user 메시지 push.
   - `client.call(messages, tools=STAGE4_TOOLS, tool_choice=None)` 호출.
   - `total_input_tokens += response.input_tokens`.
   - assistant 응답 블록을 `messages`에 append.
   - 첫 tool_use 블록 확인:
     - 없음 → "도구를 사용하라" 안내 후 다음 턴.
     - `generate_result` → input dict 반환, 종료.
     - `read_files` → `Stage4FileReader.read_files(paths, path_index, explored)` 결과를 `tool_result`로 push 후 다음 턴.
     - 알 수 없는 도구 → 안내 메시지 push.
3. 루프 소진 → `_force_generate`로 `tool_choice={"type":"tool", "name":"generate_result"}`로 강제 호출.
4. 강제 호출도 실패 → `PipelineError(500, "면접 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.")`.

### `Stage4FileReader.read_files`

- `paths`를 `max_files_per_call`까지만 처리, 초과분은 `over_limit_dropped`로 카운트.
- 분기:
  - `path in already_read` → `already_provided`에 추가 (본문 재전송 ✗, 토큰 절감).
  - `path not in path_index` → `not_found`에 추가.
  - 정상 → 파일 읽고 다음 정리:
    - `_strip_license_header` — 최상단 주석/docstring 블록에 `copyright/license/licensed/spdx-license` 키워드 있으면 통째로 제거.
    - `_compress_blank_lines` — 빈 줄 3개 이상 → 2개.
    - `_truncate_by_bytes` — UTF-8 기준 `max_file_bytes` 초과 시 잘라 "(이하 생략)" 표시.
  - 읽기 실패(`OSError/UnicodeDecodeError`) → `not_found`로 묶음.
- 반환:
  ```json
  {
    "files": {"<path>": {"content": "...", "truncated": false}},
    "not_found": ["..."],
    "already_provided": ["..."],
    "over_limit_dropped": 0
  }
  ```

---

## Stage 5 — 결과 검증 + 콜백 페이로드 조립

`DispatchInterviewQa._build_success_payload` + `dto.InterviewQaResult` + `dto.CallbackSuccess`

### 검증 항목 (Pydantic 강제)

- `project_summary`:
  - `overview: str`
  - `repositories: [{repo, role, description}]`
  - `core_features: [{name, description, based_on?: []}]`
  - `tech_stack: [str]`
- `interview`: 정확히 5개
  - `id: 1..5`
  - `category ∈ {tech_choice, implementation, troubleshooting, integration, structure}`
  - `question, expected_answer: str`
  - `based_on: [str]` (`min_length=1` — 추측 질문 방지)

> 검증 실패 → `PipelineError(500)` → `CallbackFailure`로 변환.

### 최종 콜백 페이로드

**성공**

```json
{
  "job_id": "...",
  "status": "succeeded",
  "result": {
    "project_summary": {
      "overview": "...",
      "repositories": [{"repo":"...", "role":"...", "description":"..."}],
      "core_features": [{"name":"...", "description":"...", "based_on":["..."]}],
      "tech_stack": ["..."]
    },
    "interview": [
      {
        "id": 1,
        "category": "tech_choice",
        "question": "...",
        "expected_answer": "...",
        "based_on": ["..."]
      }
    ]
  }
}
```

**실패**

```json
{
  "job_id": "...",
  "status": "failed",
  "error": {
    "status_code": 422,
    "message": "유효한 PDF 파일이 아닙니다. 진행할 수 없습니다."
  }
}
```

전송: `WebhookClient.send(job_request.callback_url, payload)`가 POST.

---

## 오류 코드 한눈에 보기

| 코드 | 상황 | 발생 단계 |
| :---: | --- | --- |
| 422 | PDF 다운로드 실패 / 빈 파일 / fitz 파싱 실패 | Stage 1 |
| 422 | GitHub URL 형식 오류 | Stage 1 |
| 403 | GitHub 저장소 404 / private / API 실패 | Stage 1 |
| 500 | Stage 4 LLM 호출 자체 실패 / 강제 generate_result 실패 | Stage 4 |
| 500 | generate_result 결과가 `InterviewQaResult` 검증을 통과 못함 | Stage 5 |
| 500 | 그 외 알 수 없는 예외 (catch-all) | DispatchInterviewQa.execute |

> 모든 실패는 `CallbackFailure` 페이로드로 `callback_url`에 POST.