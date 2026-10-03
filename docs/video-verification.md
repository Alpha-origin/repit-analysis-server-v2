# 영상 F1~F4 검증 안내

HTML 인계 명세의 R4는 구현자가 아닌 검토자의 독립 검증을 요구한다.
현재 수행한 자동 검사·수동 QA는 구현 측 재검증이다. 별도 검토자의 검증 완료로 표시하지 않는다.
원본 `.omo/plans/video-analysis-api.md`가 현재 작업 트리에 없어 32개 항목과 IS-1~IS-8의 원문 대조는 남아 있다.

## F1 — 계획 준수

원본 계획을 확보한 검토자가 32개 항목별로 코드·테스트·수용 기준을 매핑한다.
현재 계약 대조용 주요 검사는 다음과 같다.

| 검토 항목 | 테스트 |
|---|---|
| 요청 DTO·인증 순서·공개 오류 | test_request, test_api, test_errors |
| 재전송·요청 지문·과거 버전 | test_identity, test_submit, test_operations |
| 접수 정책 고정·스키마 migration | test_snapshot, test_settings, test_schema, test_operations |
| DAG·종료 문서·분석기 미호출 | test_processor, test_finalization, test_analyzer_boundary |
| 실제 다운로드·컨테이너·회전·전체 디코딩 | test_download, test_probe, test_validation, test_real_media |
| 브라우저 녹화 | test_browser_media |
| 임대·동시성·강제 종료 | test_leases, test_concurrency, test_crash_recovery |
| 콜백 예산·실패 분류·결과 불변 | test_delivery, test_callback_attempt |
| 90+90일·24시간 보관 | test_retention, test_artifact_gc |
| 토큰 분리·현재 보안·비밀값 제거 | tests/security 전체 |
| 공유 디스크 예약·대기 종료·운영 레인 | test_operations |
| 실제 프로세스 분석 어댑터·시간 초과 | test_process_analyzer |
| WAL 포함 백업·기존 백업 덮어쓰기 방지 | test_backup |

모델은 미정이며 default 미연결 결과를 유지한다. 테스트용 가짜 분석기는 src에서 import하지 않는다.
음성 전용 처리 정책은 변경하지 않았고 공통 콜백 보안만 공유한다. 과거 policy-v1은 새 시간 제한의 기본값으로 읽힌다.

## F2 — 코드 품질

URL·헤더 처리와 리다이렉트/프록시 정책, BEGIN IMMEDIATE와 lease-token fencing,
manifest-before-write/파일 확정, v1→v2 migration, 자식 그룹 종료,
로그 필터·Uvicorn 접근 로그 형식, 불필요한 예외 처리와 공개 오류 매핑을 검토한다.
DB busy, disk full, 취소, stale lease에서 저장된 결과가 달라지지 않는지 확인한다.
모니터링 값은 현재 상태 gauge이므로 누적 event counter로 해석하지 않는다.

## F3 — 수동 QA

검토자는 새 Chrome 녹화와 새 evidence 디렉터리로 다음 명령을 실행한다.
Playwright는 QA용 임시 의존성으로만 쓰고 런타임 의존성에 추가하지 않는다.

```bash
uv run --no-project --with playwright==1.58.0 python tests/video/browser_capture.py \
  .omo/evidence/video-analysis-api/reviewer-browser --channel chrome
PYTHONPATH=src .venv/bin/python -m tests.video.qa_verify \
  --browser-dir .omo/evidence/video-analysis-api/reviewer-browser \
  --evidence-dir .omo/evidence/video-analysis-api/reviewer
```

qa_verify는 Ruff check/format, mypy, import-linter, 필수 미디어 전체 테스트,
실제 uvicorn HTTP·별도 워커 프로세스 QA default/callback-failure/expired-job을 수행한다.
verification.json과 각 로그, 세 시나리오 qa-summary.json의 본문·상태·비밀값 제거 여부를 확인한다.
이 QA의 S3·콜백 수신측은 대체된다. 실제 환경 검증은 video-external-qa.md 절차를 별도로 따른다.

## F4 — 이상 상태 근거

원본 IS-1~IS-8을 확보한 뒤 각 항목에 실행 명령과 증거를 대응한다.
다음은 이미 실행할 수 있는 검증 주제이며 원문의 IS 번호를 추정해 붙이지 않는다.

- 202 유실·동시 중복 접수·내용 충돌: test_submit, test_concurrency, default HTTP QA.
- 원본 만료·404·다운로드 오류: test_download, 외부 expired-source 시나리오.
- 파일 크기·형식·손상·fps/길이 초과: test_probe, test_validation, test_real_media.
- 작업 claim/파일 rename/DB commit 사이 강제 종료: test_crash_recovery.
- 현재 임대 소유자 교체·동시 claim: test_leases, test_concurrency.
- 콜백 일시/영구 실패·재시작·조회 복구: test_delivery, callback-failure HTTP QA.
- 90일/180일 경계·파일 회수 실패: test_retention, test_artifact_gc, expired-job HTTP QA.
- 디스크·시간·출력·RSS 제한·프로세스 취소: test_media_process, test_operations, test_process_analyzer.

실제 서비스 재기동·cgroup 메모리 제한·Prometheus 경보·토큰 교체·배포 복구 시험은 배포 이후 수행한다.

## 구현 측 재검증 기록

최종 로컬 증거 경로: `.omo/evidence/video-analysis-api/r4-final/`.
필수 미디어·새 Chrome VP8/VP9 샘플을 포함한 392개 테스트, 정적 검사, HTTP QA 3종을 통과했다.
증거는 Git에 추가하지 않고 실행 시점의 기준 커밋·변경 상태를 인계 상태 문서에 기록한다.
독립 검토 완료에는 검토자·기준 커밋·32개 항목 대응·IS-1~8 결과·미해결 항목을 남긴다.
