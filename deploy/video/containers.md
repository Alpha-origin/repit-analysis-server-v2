# 영상 CPU runtime 분리

기존 systemd 구성과 선택해서 사용한다. 같은 SQLite/artifact 디렉터리를 두 구성에서 동시에 실행하지 않는다.
기본 API/io/callback/cleanup 이미지는 behavior extra와 음성 overlay를 설치하지 않는다.
CPU 이미지만 `uv sync --frozen --no-dev --extra behavior`와 native 추론 라이브러리를 설치한다.
음성 워커의 환경·설치 버전은 이 빌드에 포함하지 않는다.

## 이미지 준비

저장소 루트에서 실행한다. Python 3.12 Debian Trixie 기반이며 이미지 digest·OS/architecture·
Python patch·ffmpeg/ffprobe·uv.lock SHA를 실제 배포 기록에 남긴다.
실제 호스트 architecture로 빌드·검사한다. Apple Silicon의 amd64 에뮬레이션은 AVX가 없어 추론 검증에 쓰지 않는다. 아래 명령은 운영 서비스 시작을 포함하지 않는다.

```sh
docker build -f deploy/video/Dockerfile --target runtime -t repit-video:runtime .
docker build -f deploy/video/Dockerfile --target cpu -t repit-video:cpu .
docker run --rm repit-video:cpu python -c 'import mediapipe; import av; import numpy'
docker run --rm repit-video:cpu repit-behavior-analyzer --help
```

## 모델 release 준비

공식 두 파일과 SHA-256은 [행동 분석 문서](../../docs/behavior-analysis.md)의 값을 사용한다.
다운로드는 운영자가 준비 단계에 수행하며 서비스 시작·작업 중에는 다운로드하지 않는다.
`VIDEO_MODEL_RELEASE_DIR`는 호스트의 절대 release 디렉터리다. 모델을 담은 디렉터리를 준비한 뒤 실행한다.
컨테이너에서 디렉터리에 쓸 권한을 가진 준비 담당자의 UID/GID로 manifest를 생성한다.

```sh
export VIDEO_MODEL_RELEASE_DIR=/absolute/models/mp-face-pose-v1
docker run --rm --user "$(id -u):$(id -g)"   --mount "type=bind,src=$VIDEO_MODEL_RELEASE_DIR,dst=/release"   repit-video:cpu python evaluation/behavior/prepare_release.py /release
```

공식 hash와 다른 파일·symlink는 거부하며 기존 manifest는 덮어쓰지 않는다.
Python minor·MediaPipe/PyAV/NumPy 버전을 실제 추론 이미지로 기록한다. 다른 환경에서 만든 예제 manifest를 복사하지 않는다.
준비 후 UID 10001이 디렉터리를 탐색하고 모델·manifest를 읽을 수 있게 권한을 확인한다.
Compose는 모델을 `/models/mp-face-pose-v1`에 읽기 전용으로 연결한다.

## 공통 환경과 기동

`video.env.example`을 저장소 밖의 보호된 파일로 복사해 실제 토큰·호스트를 채운다.
`VIDEO_ENV_FILE`에는 그 절대 경로를 지정한다. 이 파일을 셸로 source하지 않는다.
`APP_ENVIRONMENT=production`, `VIDEO_ENABLED=true`와 기존 인증·allowlist 설정이 필요하다.
API의 신규 분석 OFF와 Analysis 라우터 OFF는 다르며 기존 PENDING 복구 중에는 라우터를 유지한다.

```sh
export VIDEO_ENV_FILE=/absolute/protected/video.env
docker compose -f deploy/video/compose.yaml config --quiet
# 배포 승인을 받은 환경에서만 실행
docker compose -f deploy/video/compose.yaml up -d --build
```

환경 우선순위는 Compose `environment`가 `env_file`보다 높다. DB/artifact 절대 경로는 모든 서비스에서 고정하며,
CPU backend·실행 파일·manifest 경로·버전도 Compose에서 고정한다. 기본 서비스의 backend는 unconfigured다.
토큰·S3 source/callback allowlist·VIDEO_ENABLED·timeout·보관 설정은 보호된 env 파일에서 가져온다.
환경값이 출력되는 `config` 대신 `config --quiet`로 검사한다.

모든 서비스는 UID/GID 10001로 한 호스트의 `video-data` named volume을 공유한다.
새 named volume은 이미지의 `/srv/repit/video` 소유권을 상속한다. 기존 volume의 소유권은 별도 확인한다.
SQLite를 다른 호스트·NFS에 공유하지 않는다. API에는 모델 mount가 없으며 CPU lane만 모델을 검증한다.
기본 CPU 동시성은 기존 1이다. 워커당 3GiB 컨테이너 제한과 analyzer RSS 2GiB 감시·900초 제한을 함께 적용한다.
수치는 실측 완료를 뜻하지 않으며 30/60분 영상·연속 유입·음성 동시 사용으로 확인한다.
API는 호스트 loopback에만 열고 내부 HTTPS gateway로 연결한다. cleanup은 이 구성의 상주 프로세스 하나만 사용한다.

D5 원본 삭제 종결 정책은 미확정이다. 기존 cleanup의 기본 24시간을 보관 승인으로 해석하지 않는다.
실제 S3·인증 callback·GET 복구, 카메라 영상, 사람 주석·holdout 평가를 통과하기 전 운영 활성화 완료로 기록하지 않는다.
