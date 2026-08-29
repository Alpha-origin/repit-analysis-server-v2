# analysis-question-server

포트폴리오 PDF 와 GitHub 저장소를 분석해 면접 질문을 만들고, 면접 전 질문 재작성과
면접 후 답변 피드백을 제공하는 FastAPI 서버.

모든 작업형 엔드포인트는 `202 Accepted` + `jobId` 를 즉시 돌려주고, 결과는 `callbackUrl`
로 POST 하는 비동기 콜백 방식이다. 서버는 무상태 — 세션을 저장하지 않고 요청 body 만 본다.

## Quick Start

```bash
uv sync
uv run uvicorn app.main.run:make_app --factory --reload
```

`.env` 에 `ANTHROPIC_API_KEY` 가 있어야 한다. 없으면 부팅 시 `ValidationError` 로 막힌다.

## API

| 엔드포인트 | 설명 |
|---|---|
| `POST /generate` | 포트폴리오·저장소 분석 → 면접 질문 5개 생성 |
| `POST /generate-mock` | 30초 뒤 고정 페이로드 콜백. 수신측 테스트용 |
| `POST /questions/tailor` | 면접 전, 원질문 본문을 지원자 사전 정보에 맞게 재작성 |
| `POST /questions/tailor/multi` | N:1 면접용 기술 질문 재작성·비개발 질문 생성 |
| `POST /feedback/solo` | 1:1 면접 답변 채점·피드백 |
| `POST /feedback/multi` | N:1 면접 답변·면접관별 채점과 피드백 |
| `GET /health` | 헬스체크 |

요청·콜백 페이로드와 설정 값은 [docs/api.md](docs/api.md), 피드백 생성 단계와 로그 이벤트는
[docs/feedback-logging.md](docs/feedback-logging.md) 에 있다.
FastAPI 자동 문서(`/docs`)에서도 요청·응답과 콜백 스키마를 확인할 수 있다.

## Project Layout

```
src/app/
├── main/      # 부트스트랩, 설정, DI 컨테이너 구성
├── inbound/   # HTTP 라우터·미들웨어 (FastAPI 의존)
├── core/      # 비즈니스 로직 (commands / queries / common)
│   ├── commands/            # 백그라운드 작업 진입점(디스패처)
│   └── common/
│       ├── interview_qa/    # /generate 파이프라인 (1~4단계)
│       ├── feedback/        # 면접 피드백 — solo(1:1)
│       └── question_tailor/ # 질문 재작성
└── outbound/  # 외부 시스템 어댑터 (Anthropic, GitHub, 웹훅 등)
```

의존 방향은 `main → inbound → outbound → core` 한 방향이고, `core` 는 바깥을 모른다.
`core/common` 은 `commands` / `queries` 를 import 하지 않는다. 계약은 pyproject 의
import-linter 설정에 있다.

## Common Tasks

```bash
uv run ruff check src        # 린트 (fix=true 라 자동 수정까지 함께 돈다)
uv run ruff format src       # 포맷
uv run mypy src              # 타입 검사 (strict)
uv run lint-imports          # 아키텍처 의존 방향 검사
```

- `uv run lint-imports` 는 아직 만들지 않은 `app.core.queries` 를 계약이 참조해서 실패한다.
- 테스트는 `uv run pytest` 로 실행한다.
