# 영상 서비스 배포와 복구

이 디렉터리는 단일 Linux 호스트용 systemd 서비스·timer·로그 설정·Prometheus 경보를 제공한다.
실제 설치·배포 검증은 구현 이후에 진행하기로 했으며, 이 작업에서는 호스트 서비스를 설치하지 않았다.
API·io/cpu/callback 워커·정리는 같은 서비스 계정, 로컬 SQLite DB, artifact 볼륨, 보안 설정을 사용한다.
SQLite WAL을 네트워크 파일시스템에 두거나 서로 다른 호스트에서 공유하지 않는다.

## 준비

1. 서비스 계정 `repit`과 `/srv/repit/video`를 만들고 해당 계정 소유·0700으로 설정한다.
2. 저장소를 `/opt/repit/analysis`에 배치한다. Python 3.12와 uv를 준비하고 `uv sync --frozen`으로 설치한다.
   ffmpeg/ffprobe 패키지를 설치하고 버전을 배포 기록에 남긴다.
3. `video.env.example`을 `/etc/repit/video.env`로 복사해 root 소유·0600으로 설정한다.
   빈 토큰/API key, 실제 콜백·S3 호스트를 채운다. 서로 다른 VIDEO_API_TOKEN과 APP_INTERNAL_CALLBACK_TOKEN을 쓴다.
   실제 영상, 서명 URL, 토큰은 저장소에 추가하지 않는다.
4. unit 파일을 `/etc/systemd/system/`에 배치하고 `systemd-analyze verify`로 확인한 뒤 daemon-reload한다.
   기본 실행 경로를 바꿨다면 WorkingDirectory·ExecStart·PATH·로그 설정 경로를 함께 바꾼다.
5. API, `repit-video-worker@io`, `@cpu`, `@callback`, `repit-video-cleanup.timer`를 enable/start한다.
   timer는 1시간마다 cleanup을 실행한다. cleanup 자체를 상주시키는 구성과 동시에 사용하지 않는다.

API는 127.0.0.1:8000에 바인딩한다. 내부 게이트웨이에서만 HTTPS로 접근하게 한다.
운영 워커는 `--lane`을 하나씩 명시해야 한다. 기본 io 워커 하나는 작업을 순차 실행하며,
VIDEO_IO_CONCURRENCY는 프로세스 전체의 claim 상한이다. 실제 io 병렬 처리가 필요하면
별도 이름의 io 서비스 인스턴스를 추가하되 `--lane io`와 동일 설정을 사용한다.

## 자원과 모니터링

- MemoryMax는 API 1GiB, 워커 3GiB, cleanup 256MiB이며 자식 프로세스도 포함한다.
  ffprobe 기본 RSS 감시 2GiB와 Python 여유를 고려한 초기값이다. 모델이 결정되면 실제 사용량으로 조정한다.
  RSS 감시만으로 순간 초과를 차단할 수 없으므로 cgroup 제한을 유지한다.
- ReadWritePaths는 `/srv/repit/video`로 제한하고 모델 파일은 별도 읽기 전용 경로에 배치한다.
  셸 실행·모델 자동 다운로드는 제공하지 않는다.
- 디스크는 동시 다운로드뿐 아니라 24시간 보관량·이전 시도 파일·DB/WAL·백업도 고려한다.
  VIDEO_DISK_BUDGET_BYTES는 artifact 예약 예산이고 볼륨 전체 사용량 상한은 아니다.
  staged 예약은 보수적으로 계산하며, 실제 여유 공간도 확인한다.
- `/internal/video/metrics`는 VIDEO_METRICS_ENABLED를 켰을 때만 등록되며 VIDEO_API_TOKEN 인증이 필요하다.
  Prometheus scrape 설정에서 `http_headers.X-Internal-Token.files`에 토큰 파일을 지정하고
  `job_name: repit-video`, `metrics_path: /internal/video/metrics`를 사용한다.
  토큰 파일·Prometheus 설정에는 운영 접근 권한을 적용한다. `prometheus.yml.example`을 실제 게이트웨이에 맞춘다.
  헤더 파일 구성은 [Prometheus 공식 설정](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#http_config)을 따른다.
- `alerts.yml`을 운영 Prometheus에 연결한다. 큐 6시간 지연·콜백 실패·3GiB 미만 여유·정리 2시간 이상
  미성공·삭제 오류·scrape 실패를 확인한다. 기준값은 실제 처리량과 서비스 목표에 맞춘다.
- `logging.json`은 모든 핸들러에 비밀값 제거 필터를 둔다. 별도 log-config를 도입할 때도 같은 필터를 유지한다.
  앱 기동 후 새로 만든 핸들러는 자동으로 필터를 받지 않으므로 설정에 명시해야 한다.

## 업데이트와 보안 교체

1. 새 토큰을 수신측이 먼저 수용하도록 준비한다.
2. timer와 API·영상 워커를 중지하고, 작업 중인 프로세스/자식이 완전히 종료됐는지 확인한다.
   기존 텍스트·음성 기능도 같은 콜백 토큰을 쓰므로 해당 프로세스의 교체를 포함한다.
3. SQLite backup API로 업데이트 전 DB를 백업한다. 새 코드를 배치하고 의존성을 고정 설치한다.
4. 환경 파일을 바꾸고 API·모든 워커·정리를 다시 시작한다. DB schema v1→v2 migration은 기동 시 자동 수행된다.
5. health, 인증된 metrics, 실제 소형 영상, 콜백·GET 동일성, cleanup 시각을 확인한 뒤 수신측 토큰 검증을 강제한다.

허용/차단 호스트 변경도 전 프로세스 재시작이 필요하다. hot reload는 없다.
영상 라우터를 끄더라도 이미 접수된 작업을 끝내려면 워커·정리를 계속 실행해야 한다.

## 백업·복원·롤백

```bash
cd /opt/repit/analysis
.venv/bin/python -m app.main.video_backup --destination /srv/repit/video/backups/video-20261003.sqlite3
```

systemd 서비스 환경 파일과 동일한 설정을 명령 실행 환경에도 제공한다. dotenv 설정이므로
파일을 셸로 source하지 말고 배포 도구나 보호된 `.env`를 통해 로드한다.
백업 도구는 live WAL을 포함한 SQLite backup API를 사용하고 integrity_check를 확인한다.
기존 목적지는 덮어쓰지 않고 새 파일을 0600으로 만든다. raw DB 파일만 복사하지 않는다.

복원은 API·워커·cleanup이 모두 멈춘 상태에서 수행한다. 기존 DB·WAL·SHM을 별도 장애 조사 위치로 옮기고
검증된 DB를 원래 절대 경로에 배치해 소유·권한을 맞춘다. DB와 artifact 볼륨도 같은 복구 시점에 맞춘다.
DB 백업은 영상 파일 자체를 포함하지 않으며, 대응 파일이 없으면 처리 중 작업이 INTERNAL_ERROR로 종료될 수 있다.
소형 접수·GET·콜백으로 확인한 뒤 트래픽을 재개한다.

v1 프로그램은 v2 DB를 열 수 없다. 구버전으로 롤백하려면 배포 전 v1 백업을 복원해야 한다.
백업 이후 접수·전달한 작업과 실제 수신측의 상태를 대조하고, 결과 소실·중복 콜백에 대한 복구 기록을 남긴다.
콜백은 at-least-once이므로 수신측 멱등성은 복원 이후에도 필요하다.

백업 안의 결과 만료 시각은 갱신하지 않는다. 오래된 백업을 복원한 뒤에도 조회는 논리적 만료를 지키며,
cleanup으로 기한이 지난 데이터를 회수한다. 별도 백업 사본의 보존·삭제는 운영자 책임이다.
결과와 식별 기록의 90+90일 기한을 넘기지 않게 정하고, 실제 소스 파일·원 URL을 포함한 백업 접근도 제한한다.

## 이후 배포 검증

검증 환경에서 서비스 강제 종료/재시작, DB/WAL 복원, 메모리 강제 제한, 디스크 부족,
cleanup 적체/권한 실패, 각 경보 발동·복구, 토큰 교체를 확인한다.
실제 API 서버·S3 시나리오는 [외부 QA 안내](../../docs/video-external-qa.md)를 따른다.
