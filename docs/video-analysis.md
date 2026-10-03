# 영상 분석 운영

API 계약은 [video-api.md](video-api.md). 이 문서는 설치·설정·실행·보안 배포·보관 운영을 다룬다.

## 범위와 전제

- **단일 호스트**: SQLite(WAL) 한 파일과 로컬 디스크 하나를 API·워커·정리 프로세스가 공유한다.
  네트워크 파일시스템 위 SQLite, 여러 호스트 확장은 지원하지 않는다.
- 행동 분석 모델은 아직 결정되지 않았다. 기본 서비스는 `UnconfiguredVideoAnalyzer`를 사용하며
  유효한 파일도 `unavailable / ANALYZER_NOT_CONFIGURED`로 끝난다. 모델 소유자가 제공한 로컬 실행 파일은
  `VIDEO_ANALYZER__BACKEND=process`로 연결할 수 있다(아래 분석기 계약 참고).
  향후 분석기는 `VideoAnalyzer` 포트(`PreparedVideo` → `AnalysisOutcome`)를 구현해 워커에 주입한다.
- 업로드·취소·재분석 한도·S3 원본 삭제·URL 자동 갱신은 하지 않는다. 영상을 잘라서 통과시키거나 오디오를 분석하지 않는다.
- API 서버의 "6시간 실패 표시"는 이 서버의 취소 신호가 아니다. 단계별 재시도 상한과 시간 제한으로 모든 작업이 종료 결과로 수렴한다.

## 설치

```bash
uv sync
# macOS
brew install ffmpeg
# Debian/Ubuntu
sudo apt-get install ffmpeg
ffprobe -version   # cpu 레인 워커는 ffprobe 가 없으면 작업을 가져가기 전에 기동 실패한다
```

모델·유료 추론 패키지는 필요 없다.

## 설정

API·모든 워커·정리 프로세스에 **같은 값**을 준다(특히 경로와 보안 설정).

```dotenv
# 공통 콜백 보안 (텍스트·음성·영상 콜백 모두 적용)
APP_ENVIRONMENT=production                 # development(기본) | test | production
APP_INTERNAL_CALLBACK_TOKEN=...            # production 필수. 콜백 X-Internal-Token
APP_CALLBACK_ALLOWED_HOSTS=api.example.com # 쉼표 구분, 정확한 호스트
APP_CALLBACK_BLOCKED_HOSTS=                # 긴급 차단. 허용보다 우선

# 영상
VIDEO_ENABLED=true                         # 라우터 등록만 제어(워커·정리는 항상 처리)
VIDEO_API_TOKEN=...                        # POST/GET 호출 인증. 콜백 토큰과 다른 값
VIDEO_DATABASE_PATH=/srv/repit/video/jobs.sqlite3
VIDEO_ARTIFACT_ROOT=/srv/repit/video/artifacts
VIDEO_POLICY__SOURCE_HOSTS=["example-bucket.s3.ap-northeast-2.amazonaws.com"]
VIDEO_BLOCKED_SOURCE_HOSTS=                # 긴급 차단할 S3 호스트(쉼표 구분)
VIDEO_IO_CONCURRENCY=2
VIDEO_CPU_CONCURRENCY=1
VIDEO_CALLBACK_CONCURRENCY=1
VIDEO_LEASE_SECONDS=180
```

| 키 | 기본값 | 설명 |
|---|---|---|
| `VIDEO_METRICS_ENABLED` | `false` | 인증된 `/internal/video/metrics` 등록 |
| `VIDEO_DISK_BUDGET_BYTES` | 20GiB | 공유 artifact 예약 예산. 보관·재시도 파일 포함 |
| `VIDEO_ANALYZER__BACKEND` | `unconfigured` | `process`는 로컬 실행 파일·모델 필요 |
| `VIDEO_STAGE_MAX_ATTEMPTS` / `VIDEO_STAGE_RETRY_DELAYS_SECONDS` | `3` / `[2,4]` | 단계별 시도 상한·실패 후 대기 |
| `VIDEO_CALLBACK_MAX_ATTEMPTS` / `VIDEO_CALLBACK_RETRY_DELAYS_SECONDS` | `6` / `[5,15,60,300,900]` | 콜백 시도 예산·간격 |
| `VIDEO_CALLBACK_DEADLINE_SECONDS` | `15` | 콜백 한 번의 전체 시간 상한 |
| `VIDEO_RESULT_RETENTION_DAYS` / `VIDEO_TOMBSTONE_RETENTION_DAYS` | `90` / `90` | 최소 90 |
| `VIDEO_ARTIFACT_RETENTION_HOURS` | `24` | 로컬 사본 보관 |
| `VIDEO_POLICY__MAX_BYTES` | `1000000000` | 신규 요청 크기 상한 |
| `VIDEO_POLICY__MAX_DURATION_MS` | `3600000` | 길이 상한 |
| `VIDEO_POLICY__MAX_LONG_EDGE` / `__MAX_SHORT_EDGE` / `__MAX_FPS` | `1920` / `1080` / `60` | 회전 반영 표시 크기·프레임률 |
| `VIDEO_POLICY__DOWNLOAD_TIMEOUT_SECONDS` / `__PROBE_TIMEOUT_SECONDS` / `__DECODE_TIMEOUT_SECONDS` | `900` / `30` / `3600` | 단계 시간 상한 |
| `VIDEO_POLICY__ANALYZE_TIMEOUT_SECONDS` / `__RESOURCE_WAIT_TIMEOUT_SECONDS` | `900` 각각 | 분석/공유 디스크 대기 시간 제한. 과거 정책 기본값도 900초 |
| `VIDEO_POLICY__MAX_PROCESS_RSS_BYTES` / `__MAX_JOB_DISK_BYTES` / `__MIN_FREE_DISK_BYTES` | 2GiB 각각 | 자원 상한 |

`VIDEO_ENABLED=true` 이면 `VIDEO_API_TOKEN` 이 없을 때 부팅에 실패한다. `APP_ENVIRONMENT=production` 이면
`APP_INTERNAL_CALLBACK_TOKEN` 없이는 API·음성 워커·영상 워커·정리 프로세스 모두 DB 를 열기 전에 실패한다.
비밀값은 오류 메시지·로그·작업 정책에 남지 않는다.

**접수 시점 정책 고정:** 크기·시간 제한, 단계 재시도, 콜백 예산·간격, 보관 기간은 접수할 때 작업에 복사된다.
설정을 바꿔도 이미 접수된 작업은 처음 값으로 끝까지 처리된다. 반대로 토큰·허용/차단 호스트는 작업에 저장하지 않고
네트워크 요청 직전에 **현재 프로세스 설정**을 읽는다.

## 실행

```bash
uv run uvicorn app.main.run:make_app --factory          # API
uv run python -m app.main.video_worker --lane io        # 다운로드·종료 문서
uv run python -m app.main.video_worker --lane cpu       # ffprobe 검사·전체 디코딩·분석
uv run python -m app.main.video_worker --lane callback  # 콜백 전송(ffprobe 불필요)
uv run python -m app.main.video_cleanup                 # 1시간마다 보관 정리(--interval, --batch)
```

- 레인을 프로세스로 나누는 것을 권장한다. 한 시간짜리 디코딩이 콜백 전송을 막지 않게 하기 위해서다.
  production에서는 레인 하나를 명시해야 하며, 생략·결합 실행은 DB claim 전에 기동 실패한다.
  development/test에서만 생략 시 모든 레인을 번갈아 처리한다. `--once`는 한 번만 시도하고 끝낸다.
- 동시성 상한은 SQLite 트랜잭션에서 공유되므로 같은 레인 프로세스를 여러 개 띄워도 전역 상한을 넘지 않는다.
- 단계: `source(io) → inspect(cpu) → validate(cpu) → analyze(cpu) → finalize(io)`. 성공한 단계는 다시 실행하지 않는다.
  앞 단계가 실패하면 뒤 단계는 차단되지만 `finalize` 는 항상 실행되어 종료 콜백 하나를 남긴다.
- 배포 서비스·정리 timer·경보 예제는 [deploy/video/README.md](../deploy/video/README.md)에 있다.
  `--once --batch 100 --max-passes 100`은 한 번에 최대 100회 batch를 처리한다. 자동 설치는 하지 않는다.
  늦게 돌아도 조회/접수 응답은 만료 시각대로 바뀐다(정리는 저장 공간 회수만 한다).

## 실제 파일 검사

- 헤더(최대 64KiB): WebM 은 EBML DocType 이 `webm` 이어야 한다(일반 MKV 거부). MP4 는 `ftyp` 브랜드가
  isom/iso2~6/mp41/mp42/avc1/dash 여야 하며 QuickTime(MOV)·3GP 는 거부한다.
- FFprobe(`-select_streams V`): 일반 영상 스트림이 정확히 1개, 코덱은 WebM=VP8/VP9, MP4=H.264. 오디오·커버 이미지는 무시한다.
- 크기: 회전(display matrix 우선, 없으면 rotate 태그)과 SAR 을 반영한 표시 사각형의 경계 상자로 1920×1080 이하.
- 전체 디코딩: 모든 프레임의 PTS 가 엄격히 증가해야 하고, **어느 1초 구간 `[pts, pts+1s)` 에도 60프레임 이하**,
  파일 전체 평균 ≤ 60fps. VFR·59.94·양자화된 60fps·브라우저 녹화의 순간적인 짧은 간격(2~4ms 지터)은 통과,
  1초 안에 61프레임 이상(지속 61fps 이상)은 거부.
  - 계획서의 "모든 인접 간격 + 1 tick ≥ 1/60초" 규칙은 실제 Chrome MediaRecorder 파일(평균 ~20fps,
    간헐적 2~4ms 간격)을 60fps 초과로 거부해서 1초 창 규칙으로 바꿨다(`tests/video/test_browser_media.py`). 길이는 첫 PTS 부터 마지막 프레임 끝까지이며,
  WebM 헤더의 Duration(있을 때)이 더 길면 그 값으로 판정한다. FFprobe 의 **비트레이트 추정 길이는 쓰지 않는다**
  (브라우저 MediaRecorder WebM 은 헤더에 길이가 없다).
- 디코더 경고가 하나라도 나오면 종료 코드가 0 이어도 손상으로 본다. FFprobe 는 셸 없이, 로컬 파일만,
  외부 참조 비활성(`-enable_drefs 0`), 스레드 1개로 실행한다.
- 자원: 시간 상한 초과 시 프로세스 그룹을 종료하고 회수한다. stdout(메타데이터 1MiB, 프레임 64MiB·한 줄 8KiB)·
  프레임 216,001개 상한을 적용한다. stderr는 본문 저장 없이 바이트 수만 센다.
  RSS 2GiB 감시는 1초 간격 표본 검사 후 종료하는 **감시 방식**이며
  OS 하드 제한이 아니다. 더 강한 격리가 필요하면 컨테이너·프로세스 메모리 제한을 함께 건다.

## 보안 설정 배포 순서

토큰을 새로 도입하거나 교체할 때:

1. **수신측(API 서버)이 새 토큰을 받아들이도록** 먼저 배포한다(기존·새 토큰 모두 허용 또는 아직 미검증).
2. 이 서버의 `APP_INTERNAL_CALLBACK_TOKEN` 을 바꾸고 **API·음성 워커·영상 워커·정리 프로세스를 모두 재시작**한다.
   hot-reload 는 없다. 하나라도 이전 프로세스가 남으면 이전 토큰으로 보낸다.
3. 수신측에서 토큰 검증을 강제한다.

호스트 차단·허용 목록 변경도 같은 방식(모든 프로세스 교체)으로 적용한다. 이미 접수된 작업에도 다음 네트워크 요청부터 적용된다.

- 이 백엔드를 공개 인터넷에 직접 노출하지 않는다. 내부망/게이트웨이 뒤에 두고 `VIDEO_API_TOKEN` 은 추가 방어선으로 쓴다.
- 로그에는 URL 쿼리·사용자정보·토큰을 지운 값만 남는다(`uvicorn.access` 상대 경로 쿼리 포함). 응답 본문·예외 원문·FFprobe stderr 는 기록하지 않는다.
- 원격 URL 다운로드·콜백은 리다이렉트를 따르지 않고, 프록시 환경변수를 상속하지 않으며, TLS 검증을 끄지 않는다.

## 저장과 보관

```
var/video/jobs.sqlite3
var/video/artifacts/<서버가 만든 jobId>/<stage>/<lease-token>.source
```

- 사용자 ID 를 경로에 쓰지 않는다. 디렉터리 0700, 파일 0600. 같은 내용이어도 작업끼리 파일을 공유하지 않는다.
- 파일을 쓰기 전에 임시·최종 경로를 DB 에 먼저 기록하고, 현재 임대 소유자일 때만 최종 경로로 이름을 바꾼다.
  이름 변경 후 DB 커밋이 실패해도 기록이 남아 있어 24시간 정리에서 회수된다. 알 수 없는 디렉터리는 지우지 않는다.
- 결과 90일 → 식별 기록 90일 → 삭제. 처리 중 작업은 대상이 아니다. 백업을 따로 보관한다면 백업 보존 기간이
  이 기한을 넘지 않도록 운영 정책에서 맞춘다(이 서버는 백업 사본을 지우지 못한다).

## 검증

```bash
PYTHONPATH=src .venv/bin/pytest tests/video tests/security -q
VIDEO_REAL_MEDIA_REQUIRED=1 PYTHONPATH=src .venv/bin/pytest tests/video/test_real_media.py -q   # ffmpeg 없으면 실패
VIDEO_BROWSER_FIXTURES_DIR=<dir> VIDEO_REAL_MEDIA_REQUIRED=1 PYTHONPATH=src .venv/bin/pytest tests/video/test_browser_media.py -q
PYTHONPATH=src .venv/bin/python -m tests.video.qa_scenario --evidence-dir .omo/evidence/video-analysis-api/manual --require-real-media
```

QA 러너는 실제 `make_app`(uvicorn, HTTP)과 별도 워커 프로세스, 실제 FFprobe 디코딩, 운영 기본 분석기를 사용한다.
외부 S3 와 콜백 수신측만 테스트용으로 대체하며, 실제 S3·API 서버 연동 검증을 대신하지 않는다.

## 공유 디스크 예약

다운로드 전에 SQLite 트랜잭션으로 예상 원본 바이트를 예약한다. 모든 미회수 manifest의 합이
`VIDEO_DISK_BUDGET_BYTES`를 넘을 수 없다. 실제 여유 공간에서는 staged 예약을 전액 차감한 뒤
예상 다운로드와 `VIDEO_POLICY__MIN_FREE_DISK_BYTES`를 확인한다(일부 이미 쓰인 바이트는 보수적으로 이중 계산).
다운로드 중에도 16MiB마다 여유 공간을 재확인한다.
공간 부족은 재시도 예산을 소모하지 않고 5초 뒤 대기한다. 접수 후
`VIDEO_POLICY__RESOURCE_WAIT_TIMEOUT_SECONDS`가 지나도 예약할 수 없으면 PROCESSING_TIMEOUT으로 종료한다.
임대 만료만으로 예약을 풀지 않는다. 이전 writer가 살아 있을 수 있기 때문이다.
실패 실행이 실제로 중단된 뒤 파일이 없으면 예약을 해제하고, 남은 파일은 종료 후 24시간 GC가 회수한다.
이 보수적 방식은 오래된 staged 파일 때문에 대기를 길게 만들 수 있지만 예산을 중복 배정하지 않는다.
DB v1→v2 migration은 기존 파일의 크기·원 요청 크기로 예약을 복원하고 원본 결과를 유지한다.
롤백하려면 모든 프로세스를 멈추고 v1 백업을 복원해야 한다. 구버전 프로그램은 v2 DB를 열지 못한다.

## 분석기 실행 계약

모델/행동 지표는 미정이다. 어댑터는 행동 점수를 생성하거나 모델을 다운로드하지 않는다.
기본 `VIDEO_ANALYZER__BACKEND=unconfigured`는 기존 미연결 결과를 유지한다.
process 연결에는 `VIDEO_ANALYZER__EXECUTABLE`, `VIDEO_ANALYZER__MODEL_PATH`의 기존 절대 경로와
`VIDEO_ANALYZER__MODEL_VERSION`, `VIDEO_ANALYZER__DATA_SCHEMA_VERSION`이 필요하다.
CPU 워커는 실행 권한·모델 읽기 권한을 DB를 열기 전에 검사한다.

실행 파일 인자: `--model-path <path> --model-version <version> --input-json <PreparedVideo JSON>`.
stdout은 1MiB 이내 AnalysisOutcome JSON 한 개다. 성공/부분 결과의 data는 객체이며
`schemaVersion`과 `modelVersion`이 설정과 일치해야 한다. 나머지 행동 데이터 계약은 모델 소유자가 정의한다.
상위 콜백 schemaVersion은 "1"을 유지한다. 잘못된 출력은 INTERNAL_ERROR,
분석 단계 시간 초과는 PROCESSING_TIMEOUT으로 단계 예산 내 재시도한다.
실행은 셸·자동 다운로드·상속 자격증명 없이 진행하며, 시간 초과·임대 상실 시 자식 그룹을 종료·회수한다.
현재 process 어댑터는 cpu 레인을 사용한다. GPU 모델을 고른다면 전용 레인을 별도로 추가한다.

## 운영 지표와 백업

`VIDEO_ENABLED=true`와 `VIDEO_METRICS_ENABLED=true`에서 GET `/internal/video/metrics`를 등록한다.
`X-Internal-Token: VIDEO_API_TOKEN` 인증 후 Prometheus 텍스트를 반환한다.
처리/단계/콜백 상태별 수, 가장 오래 처리 중인 작업의 시간, 디스크 예약·예산·여유,
재시도·시간 초과 상태 수, 정리 마지막 회차·성공 시각·파일 삭제 오류를 노출한다.
상태 수는 현재 DB의 gauge이며 보관 정리 후 감소한다. 사용자 ID·URL·jobId 라벨은 없다.

```bash
uv run python -m app.main.video_backup --destination /srv/repit/video/backups/video-20261002.sqlite3
```

SQLite backup API로 WAL까지 일관되게 백업하고 integrity_check를 검사한다.
기존 목적지는 덮어쓰지 않으며 파일 권한은 0600이다. 백업 보존은 운영자가 관리한다.
작업별 원본 만료 시각이 이미 백업 안에 있으므로 복원 후 조회의 논리적 만료도 유지된다.
원래 기한이 지난 민감 데이터는 복원 뒤 정리하고, 별도 백업 접근·보존 정책도 준수한다.
