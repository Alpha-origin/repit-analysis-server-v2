# 2026-10-21 행동 분석 연결 수정·검증 기록

기준: fetch 후 main `8b3302783a25e262a49768802f832a8eda3714d3`.
브랜치: `feature/#58-action-analyze`. 코드·실행 구성 결과 commit: `d6cb8fe5a09b52dcc16e8e3de15d3f4eb60c2041`.
로컬 준비·검증 결과이며 실제 운영 배포·S3 연동·사람 평가 완료를 뜻하지 않는다.

## 발견한 결함과 수정

- 기존 prepare_release는 임의 파일의 hash를 그대로 manifest에 넣었으며 load_manifest도 그 자기 선언만 검사했다.
  실제로 잘못된 두 파일이 mp-face-pose-v1로 통과하는 것을 수정 전에 재현했다.
- 준비와 워커 기동 모두 공식 두 SHA-256·출처를 고정 release와 대조한다. 파일과 manifest를 함께 바꿔도 거절한다.
- hash·경로·runtime metadata를 검사한 뒤에만 manifest를 생성한다. symlink·누락 파일을 거절하고 기존 release는 덮어쓰지 않는다.
- API/io/callback/cleanup과 CPU 이미지를 분리했다. CPU만 behavior extra와 native 추론 라이브러리를 설치한다.
  음성 overlay·음성 워커의 설치 버전은 변경하지 않았다.
- Compose는 한 호스트의 SQLite/artifact volume과 읽기 전용 모델을 연결한다. 경로·환경 우선순위는
  [컨테이너 절차](../deploy/video/containers.md)에 기록했다.
- Bookworm FFmpeg 5에서 기존 rotation/VFR 검사가 실패하여 FFmpeg 7을 제공하는 Trixie 기반으로 보완했다.

## 고정 모델과 실행 환경

공식 모델 source URL과 SHA-256은 [기존 행동 분석 문서](behavior-analysis.md)의 두 파일과 일치한다.
별도 새 release 디렉터리에서 CPU 이미지로 prepare_release를 실행했으며 기존 release를 덮어쓰지 않았다.

| 항목 | 실제 검증 환경 |
|---|---|
| Python | 3.12.15 |
| OS/architecture | Debian Trixie Linux / aarch64 |
| MediaPipe / PyAV / NumPy | 1.1.1 / 19.0.1 / 2.5.4 |
| ffmpeg / ffprobe | 7.1.5-0+deb13u1 |
| uv.lock SHA-256 | `942bc0d919882d86d213770bbd3486891bf443aa2cdb130250a5184fc4fdf359` |
| model / thresholds / data / outer schema | mp-face-pose-v1 / behavior-policy-v1 / behavior-v1 / 1 |

실제로 빌드하고 검사한 로컬 이미지 ID:

- runtime: `sha256:d2f44df8d14c918b3daf8169a6b896be13c633ed3972e230f7a1c6202e024c85`
- CPU: `sha256:bdde722291a5413f1b5f6cd79e8016d696b5b9712e29a29e86c5eca01cb402b3`

Linux amd64 에뮬레이션은 이 Apple Silicon 호스트에서 AVX 미지원으로 실제 추론을 실행하지 못했다.
성공한 실제 모델 검증은 호스트와 같은 Linux ARM64에서 수행했다. 배포할 x86 호스트의 별도 검증은 남아 있다.

## 실행한 검사

| 검사 | 결과 |
|---|---|
| frozen lock 별도 환경 설치 | 통과 |
| ruff check --no-fix src tests 및 prepare_release | 통과 |
| ruff format --check src tests 및 prepare_release | 통과 |
| mypy src tests | 384 source files 통과 |
| lint-imports | 2 contracts 통과 |
| macOS 전체 pytest 첫 검증 | 1351 통과 / 13 skip / 실패 0 |
| Linux 전체 pytest 최종 검증 | 1366 통과 / skip 0 / 실패 0 |
| UID 10001·읽기 전용 파일시스템 실제 모델 재검증 | 5 통과 / skip 0 |
| runtime/CPU 이미지 실제 build·native import·CLI help | 통과 |
| 기본 이미지의 MediaPipe/PyAV/NumPy/faster-whisper 미설치 | 확인 |
| io/cpu/callback --lane·--once 기동 | 공유 임시 volume·UID 10001·읽기 전용 root에서 통과 |
| Compose config --quiet | synthetic 토큰/호스트로 통과 |
| 공유 ready/partial/zero-face fixture | 전달 계약 JSON과 세 파일 동일 |

최종 Linux 검사는 BEHAVIOR_MODEL_MANIFEST·BEHAVIOR_TEST_PORTRAIT·analyzer PATH와
BEHAVIOR_REAL_MODELS_REQUIRED=1, VIDEO_REAL_MEDIA_REQUIRED=1을 명시했다.
검증 전용 이미지에만 개발 도구·Git·Chromium을 설치했다. 배포 이미지에는 추가하지 않았다.
macOS의 FFmpeg/브라우저 누락 skip은 최종 Linux에서 실제 도구·fixture를 준비해 모두 실행했다.

실제 모델은 H.264/VP8/VP9 portrait clip의 얼굴 관찰·ready/partial 계약,
1초 영상의 unavailable/BEHAVIOR_NOT_MEASURABLE/retryable=false,
12초 검은 영상의 partial/얼굴 0%와 나머지 항목 null·사유를 확인했다.
Chromium 145 MediaRecorder가 만든 canvas VP8/VP9를 실제 파일 검사·전체 decode에 통과시켰다.
이 반복 이미지·canvas 영상은 실행 smoke이며 사람 주석·실제 카메라 품질 평가를 대신하지 않는다.

SOLO/MULTI·기업 유무 조합에서 interviewStatus 누락/null/ABANDONED/IN_PROGRESS/알 수 없는 값/소문자/숫자/
boolean/list/object는 422이며 job 생성·LLM·콜백은 0회다. COMPLETED의 기존 성공·실패 흐름도 회귀에 포함된다.
영상·음성 HTTP DTO에 내용 채점 자격 필드를 추가하거나 API DB 조회를 만들지 않았다.
기존 멱등 접수·콜백 재시도·GET·crash recovery·일반 직군·기업·음성 경로는 전체 테스트에 포함된다.

## 미검증·미확정 인수 항목

- 실제 S3 presigned 다운로드 → 배포 API 인증 callback → GET 복구·202 응답 유실 복구.
  로컬 대역/회귀 결과를 실제 S3 성공으로 기록하지 않는다.
- Chrome/Edge/Safari의 실제 카메라, 저조도·다중 인물·작은 얼굴·어깨 잘림 등 촬영 조건.
- 6명/30클립/30분·독립 이중 주석/holdout, 지표별 F1·비율 오차·coverage 품질 gate.
- 30/60분 전체 추론의 단계별 지연·RSS·출력 크기, 연속 유입·음성 동시 처리량.
  기존 60분 입력 경계 회귀는 장시간 실제 추론 벤치마크가 아니다.
- D5 삭제 종결·실패/재시도 수명은 미확정이다. API 원본 삭제·재분석 금지와 Analysis 진행 작업/전사 삭제 연계는
  정책 확정 후 별도 구현·실제 연동 검증이 필요하다. 기본 24시간 cleanup을 보관 승인으로 해석하지 않는다.
- 배포 환경 토큰 일치·실제 볼륨 권한·서버 자원·API/Web의 신규 활성화 gate.

공동 운영 활성화 기준은 아직 충족했다고 선언하지 않는다. 운영 flag·서비스·데이터는 변경하지 않았다.
원본 영상·모델 파일·토큰·presigned URL은 Git에 추가하지 않았다. 임시 검증 volume은 검사 후 정리했다.
