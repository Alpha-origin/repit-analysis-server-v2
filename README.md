## Quick Start

```bash

uv sync

make dev-http

# 또는 

uv run uvicorn app.main.run:make_app --factory --reload
```

## Project Layout

```
src/app/
├── main/      # 부트스트랩, 설정, DI 컨테이너 구성
├── inbound/   # HTTP 라우터·미들웨어 (FastAPI 의존)
├── core/      # 비즈니스 로직 (commands / queries / common)
└── outbound/  # 외부 시스템 어댑터 (DB, 외부 API 등)
```

## Common Tasks

```bash
make test           # 단위·통합(no_infra) 테스트
make lint           # ruff + import-linter + mypy
make fix            # ruff 자동 수정 + 포맷
make check          # lint + test
make dev-http       # Docker로 핫리로드 기동
```
