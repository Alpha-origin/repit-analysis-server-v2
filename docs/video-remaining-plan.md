# 영상 분석 R1~R7 구현·인계 상태

2026-10-03. 작업 브랜치: `feat/#13-video-analysis`, 추적 원격: `origin/feat/#13-video-analysis`.
기존 영상 구현 11개 커밋(`0a4d128`~`ef6c261`)의 해시와 순서를 보존했다.
원격 브랜치 `fe88027` 이후의 기존 기반 이력도 유지했다. 기반 커밋은 `80e7a39`다.
push·PR 생성은 수행하지 않았다.

제공된 로컬 HTML “영상 분석 인계 명세”를 읽고 R1의 정확한 여섯 시나리오와 R4 F1~F4를 반영했다.
사용자 결정: **배포 검증은 구현 이후로 연기**, **행동 모델은 미정**.
원본 `.omo/plans/video-analysis-api.md`는 없으므로 32개 항목·IS-1~IS-8의 원문 대조는 후속 검토에 남긴다.

## 항목별 상태

| 항목 | 구현·로컬 검증 | 남은 조건 |
|---|---|---|
| R1 실제 연동 | HTTPS 실환경 러너, 원문 6개 시나리오 manifest, 수신측 관찰·장애 복원, 비밀값 없는 증거 | 실제 API·S3·수신측 환경에서 실행. 사용자 요청에 따라 뒤로 미룸 |
| R2 운영 배포 | systemd API/레인 워커/정리 timer, cgroup 메모리 제한, 공용 환경 예제, 지표·경보·로그 설정, WAL 백업 도구·복구 문서 | Linux 서비스 기동·강제 종료·메모리/경보·복원·토큰 교체 검증 |
| R3 분석기 연결 | 로컬 process 어댑터 선택, 실행·모델 경로 기동 검사, 모델/데이터 버전 검사, 접수 정책의 시간 제한, 실제 자식 취소·회수 | 모델·행동 데이터 계약 선택, 실제 녹화로 ready/partial 검증. 미정인 동안 기본 미연결 결과 유지 |
| R4 독립 검증 | 자동 F1/F2 검사, 새 Chrome 녹화, F3 HTTP QA 3종, 검토자용 재실행 러너·F1/F4 근거 안내 | 구현자가 아닌 검토자, 원본 32개/IS-1~8 대조 |
| R5 결정 기록 | fps 슬라이딩 구간·WebM 선언값/디코딩값·기존 콜백 보안 계약 문서화, 기존 경계/브라우저/보안 회귀 유지 | 실제 수신측의 계약 대조는 R1에 포함 |
| R6 한계 보완 | SQLite 합산 디스크 예약, 예산 없는 대기·대기 종료 상한, v1 migration, 빈 예약/GC 회수, 명시적 운영 레인, 저장 버전 재전송, 인증된 지표 | 실제 처리량에 맞춘 디스크·메모리·경보 예산 조정. 다중 호스트는 계속 범위 밖 |
| R7 저장소 정리 | 포맷 26개, mypy 오류, 없는 queries 계약, README 수정, CI·선택적 pre-commit, 안전한 local main 동기화 | 원격 CI 실행은 push 이후. 훅은 사용자가 설치할 때 적용 |

## 구현 지도

- `core/common/video/identity.py`: v1 계산기를 고정하고 저장된 identity_version별 계산기로 재전송 판정.
  미지원 저장 버전은 503으로 중단한다. tombstone을 현 버전으로 일괄 변환하지 않는다.
- `outbound/adapters/video/sqlite_repository.py`: schema v2, 합산 예약·대기 재접수·운영 지표·정리 기록.
  예산은 실제 파일 회수 전까지 유지하고, 부족 대기는 재시도 횟수를 소비하지 않는다.
- `core/commands/process_video_task.py`: 공유 디스크 대기와 분석 timeout, 실행 중단 후 빈 예약 회수.
  과거 policy-v1의 새 시간 제한 기본값은 900초다.
- `outbound/adapters/video/process_analyzer.py`: 셸 없는 로컬 실행, stdout 상한, 버전 검증.
  모델을 자동 다운로드하거나 행동 점수를 만들어내지 않는다.
- `main/video_config.py`, `video_worker.py`: process 설정과 모델 기동 검사, production의 레인 하나 명시.
- `inbound/http/video/router.py`: VIDEO_API_TOKEN 인증의 `/internal/video/metrics`.
- `main/video_backup.py`: 기존 파일을 덮어쓰지 않는 0600 SQLite backup API·integrity_check.
- `deploy/video/`: 서비스·timer·cgroup·로그 필터·Prometheus 경보·설치/업데이트/복구 절차.
- `tests/video/qa_external.py`: 실제 서비스용 manifest 러너. 외부 설정 누락은 BLOCKED이며 성공으로 처리하지 않는다.
- `tests/video/qa_verify.py`: 정적 검사·필수 미디어 전체 테스트·HTTP QA 3종을 재실행하고 로컬 증거를 남긴다.

## 계약·운영 문서

- [API 계약](video-api.md)
- [설정·워커·보관·분석기 계약](video-analysis.md)
- [실환경 QA와 여섯 시나리오](video-external-qa.md)
- [실환경 manifest 예제](video-external-manifest.example.json)
- [배포·모니터링·백업·복구](../deploy/video/README.md)
- [R5 결정 기록](video-decisions.md)
- [F1~F4 검토 안내](video-verification.md)

## 검증과 재실행

검증 기록·실제 Chrome WebM은 `.omo/evidence/video-analysis-api/`에만 보관하고 Git에 추가하지 않는다.
구현 측 검증과 외부 환경·독립 검토 결과를 구분한다.

```bash
uv sync --frozen
uv run --no-project --with playwright==1.58.0 python tests/video/browser_capture.py \
  .omo/evidence/video-analysis-api/reviewer-browser --channel chrome
PYTHONPATH=src .venv/bin/python -m tests.video.qa_verify \
  --browser-dir .omo/evidence/video-analysis-api/reviewer-browser \
  --evidence-dir .omo/evidence/video-analysis-api/reviewer
```

qa_verify는 Ruff check/format, mypy, import-linter, 필수 실파일/브라우저 전체 pytest와
HTTP default/callback-failure/expired-job QA를 실행한다. ffmpeg·샘플 누락은 실패다.
원래 import-linter의 layers 계약과 common → commands 금지 계약은 유지한다.

기존 369개 테스트에 디스크 예약·대기 종료·migration·지문 버전·운영 레인·지표 인증,
실제 process 분석 취소·버전 오류, WAL 백업, 외부 러너 실패/기밀 제거 회귀를 추가했다.
최종 구현 측 검증은 **392개 테스트 통과**, Ruff check/format·mypy·import-linter 통과,
HTTP QA 3종 통과다. 실제 Chrome VP8/VP9 녹화를 필수 입력으로 사용했다.
증거는 `.omo/evidence/video-analysis-api/r4-final/`에 보관했다.
검사는 `2a860bc` 이후의 구현 변경을 커밋하기 전에 실행했으며,
`verification.json`의 `trackedChanges=true`는 이 실행 시점을 나타낸다.
검증된 변경을 담은 커밋 목록은 완료 보고에 기록한다.

## 후속 실제 검증 순서

1. Linux 스테이징에 배포 파일·공유 경로·토큰·수신측 설정을 적용하고 R2 기동/복구/경보를 검증한다.
2. 보호된 실환경 manifest에 실제 S3·수신측 관찰 경로를 넣고 R1 여섯 시나리오를 실행한다.
3. 별도 검토자가 원본 계획과 F1~F4를 대조하고 새 evidence로 반복 검증한다.
4. 모델/데이터 계약을 정한 뒤 로컬 process 어댑터에 연결하고 실제 ready/partial을 확인한다.
   GPU 모델이라면 전용 레인·메모리·장치 구성을 먼저 추가한 뒤 R1/R4 영향 범위를 재검증한다.

모델 미연결 상태의 배포는 파일 검사와 unavailable 결과 전달 범위로만 완료 판정한다.
