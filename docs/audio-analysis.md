# 답변 음성 분석

질문별 음성 1~12개를 영속 작업으로 접수하고, 전처리 결과를 두 독립 분석 라인에서 공유한다.
영상 및 기존 `/feedback/solo`, `/feedback/multi`와 연결하지 않는다. 점수·합격 기준은 만들지 않는다.

## 실행 범위

- 전처리: 원본 확보 → 검사 → 표준화 → 품질/VAD/전사 → 기본 시간 검증 → 결과 확정.
- 시간·리듬: 앞뒤 비발화, 내부 멈춤, 침묵 포함·제외 속도, 고정 구간 속도 변동.
- 유창성·완결성: 기존 Anthropic 클라이언트를 통한 전사 근거의 간투사·반복·자기 수정·미완결 후보.
- 영속 실행: SQLite 작업 큐, 단계별 결과, 임대·heartbeat, 제한된 재시도, 독립 분석 및 콜백 재시도.

**음향적 말더듬기 검출은 구현 범위에 포함되지 않는다.** 반복 전사 후보는 반환하지만,
소리 늘임·막힘·ASR이 놓친 소리는 전용 검증 모델이 정해지지 않았으므로 `acoustic_stuttering.status=unavailable`이다.
이 때문에 유창성 결과와 세션은 정상 처리돼도 `partial`일 수 있다. 작업 완료와 평가 항목 완전성은 별개다.

## 저장·실행 형태

이번 구현은 **한 호스트의 영속 디스크 + SQLite**를 사용한다. API와 모든 워커는 같은
DB와 업로드·산출물 디렉터리를 공유해야 한다. 네트워크 파일시스템의 SQLite나 여러 호스트 배포를 대상으로 하지 않는다.
멀티호스트 확장 시 `AudioRepository`와 `AudioStageBackend` 경계를 통해 DB/큐/객체 스토리지를 교체한다.

신규 S3 통신 계약은 [audio-api.md](audio-api.md)를 따른다.
`POST /analysis/audio`는 S3 Presigned URL로 다운로드하고 파일 크기를 검증한다.

아래 로컬 파일 입력은 기존 호환 API(`/audio/analysis`)에 한한다.
원본은 업로더가 `AUDIO_SOURCE_ROOT` 아래에 완전히 업로드한 후 요청한다.
API에는 상대 객체 키와 SHA-256을 전달한다. 업로드 엔드포인트는 제공하지 않는다. 원격 다운로드는 신규 S3 API에서 지원한다.
절대 경로·경로 탈출·루트 밖 심볼릭 링크는 거부한다. 체크섬 불일치도 처리하지 않는다.

기본 저장 위치:

```text
var/audio/uploads/       # 업로더가 쓰는 불변 원본
var/audio/artifacts/     # 검증된 원본 사본과 WAV
var/audio/jobs.sqlite3   # 작업, 전사, 품질 특징, 단계·분석 결과
```

API는 기존 서버와 동일하게 내부 서비스용이며 인증 기능을 추가하지 않는다.
신뢰하는 백엔드·인증 게이트웨이 뒤에서 사용한다. 작업 DB에는 전사와 근거 텍스트가 저장되므로
운영 보관 기간에 맞춘 DB·파일 삭제 및 백업 관리는 배포 측에서 함께 설정해야 한다. 자동 보관 만료는 아직 없다.

## 설치

HTTP 서버의 기존 의존성과 `uv.lock`은 유지한다. 모델은 HTTP 프로세스에 로딩하지 않는다.
별도 오디오 워커 가상환경에 프로젝트와 선택 의존성을 설치한다.

```bash
uv venv .venv-audio --python 3.12
uv pip install --python .venv-audio/bin/python -e . -r requirements-audio.txt
```

FFmpeg와 FFprobe 실행 파일은 별도로 설치해 PATH에서 찾을 수 있어야 한다.
CPU/CUDA별 의존성과 모델 동작을 검증한 뒤 워커 환경을 별도 잠금 파일로 고정한다.
`requirements-audio.txt`는 설치 후보 범위이며 이번 환경에서 설치·GPU 호환성을 검증한 잠금 파일은 아니다.

- FFmpeg/FFprobe: 파일 검사·PCM 변환
- Silero VAD: ONNX 기반 발화 탐지
- faster-whisper: 전사 및 단어 시간
- 품질 계산: 표준 라이브러리 `wave`/`array` (추가 음향 라이브러리 불필요)

참고: [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Silero VAD](https://github.com/snakers4/silero-vad), [FFprobe](https://ffmpeg.org/ffprobe.html).

## 설정과 시작

```dotenv
AUDIO_ENABLED=true
AUDIO_SOURCE_ROOT=/absolute/path/audio/uploads
AUDIO_ARTIFACT_ROOT=/absolute/path/audio/artifacts
AUDIO_DATABASE_PATH=/absolute/path/audio/jobs.sqlite3
AUDIO_CALLBACK_HOSTS=["callbacks.example.com"]
AUDIO_CPU_CONCURRENCY=2
AUDIO_INFERENCE_CONCURRENCY=1
AUDIO_LLM_CONCURRENCY=2
AUDIO_LEASE_SECONDS=180
AUDIO_MAX_ATTEMPTS=3
AUDIO_POLICY__WHISPER_MODEL=/absolute/path/models/whisper-large-v3-immutable-version
AUDIO_POLICY__WHISPER_DEVICE=cpu
AUDIO_POLICY__WHISPER_COMPUTE_TYPE=int8
AUDIO_POLICY__WHISPER_LOCAL_ONLY=true
```

모델 파일은 미리 준비한다. 기본 `local_only=true`이므로 실행 중 자동 모델 다운로드를 하지 않는다.
검증된 버전의 로컬 모델 디렉터리를 사용한다. 같은 경로의 가중치를 덮어쓰지 말고 버전 경로를 변경한다.
GPU 사용 시 대상 환경에서 지원되는 device/compute type과 런타임 조합을 설정한다.

API는 기존 방식으로 시작하고, 워커를 별도로 시작한다.

```bash
uv run uvicorn app.main.run:make_app --factory
.venv-audio/bin/python -m app.main.audio_worker
```

한 워커 프로세스는 한 번에 단계 하나를 처리한다. 전사 모델은 프로세스 안에서 재사용한다.
자원별 독립 실행이 필요하면 다음처럼 워커 프로세스를 나눈다.

```bash
.venv-audio/bin/python -m app.main.audio_worker --lane cpu
.venv-audio/bin/python -m app.main.audio_worker --lane inference
.venv-audio/bin/python -m app.main.audio_worker --lane llm
.venv-audio/bin/python -m app.main.audio_worker --lane callback
```

모든 워커에 동일한 전역 동시성·임대·재시도 설정을 적용한다. SQLite 트랜잭션에서 실행 슬롯을 확보하므로
여러 프로세스가 같은 DB를 사용해도 lane별 제한을 공유한다. `--once`는 작업 한 개만 시도하고 종료한다.

## API

### POST /audio/analysis (기존 로컬 입력 호환용)

기본적으로 꺼져 있으며 `AUDIO_ENABLED=true`일 때 등록된다.

```json
{
  "requestId": "audio-request-001",
  "sessionId": "session-001",
  "answers": [
    {
      "answerId": "answer-001",
      "questionId": "question-001",
      "assetKey": "session-001/answer-001.webm",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "endReason": "user"
    }
  ],
  "callbackUrl": "https://callbacks.example.com/audio"
}
```

SHA-256 예시는 실제 파일의 해시로 바꿔야 한다. `endReason`은 `user`, `timeout`, `interrupted`, `unknown`이다.
`callbackUrl`은 생략 가능하며, 지정하면 HTTPS·서버 허용 호스트·기본 443 포트만 허용한다.
콜백 클라이언트는 리다이렉트를 따라가지 않는다.

응답은 `202`와 `jobId`, `sessionId`, `status=accepted`다.
같은 sessionId/requestId와 같은 요청·정책이면 같은 작업을 반환한다. 내용이나 정책이 다르면 `409`다.
새 설정으로 재분석하려면 새로운 requestId를 사용한다.

### GET /audio/jobs/{jobId}

전체 작업 진행률, 단계 상태·시도 횟수·오류 코드, 콜백 상태 및 최종 답변 목록을 반환한다.
완료 결과에는 로컬 파일 경로나 전체 원문 전사를 넣지 않는다. 유창성 후보의 근거 인용은 포함된다.
각 답변은 `preprocessingStatus`, `durationMs`, `timing`, `fluency`를 독립적으로 가진다.

콜백 페이로드는 조회 응답의 `result`와 같다. 콜백 실패는 저장된 분석 결과를 삭제하지 않는다.
콜백은 at-least-once이므로 수신 측은 jobId로 중복 수신을 처리해야 한다.

## 단계와 실패 정책

| 단계 | 선행 단계 | 실패 시 |
|---|---|---|
| source | 없음 | 해당 파일의 기반 처리 중단 |
| inspect | source | 표준화 중단 |
| normalize | source, inspect | 음성 기반 후속 단계 차단 |
| quality | normalize | VAD·전사는 계속 가능 |
| vad | normalize | 시간·리듬 제한, 전사는 계속 가능 |
| transcribe | normalize | 침묵 분석 가능, 전사 기반 분석 불가 |
| align | normalize, transcribe | 원문·기본 전사 유지, 속도 안정성 제한 |
| prepare | 앞 단계들이 종료 | 산출물 상태를 모아 ready/partial/unusable 확정 |
| timing | prepare | 유창성 결과에 영향 없음 |
| fluency | prepare | 시간·리듬 결과에 영향 없음 |
| aggregate | 각 답변의 두 분석 종료 | 부분 성공을 포함해 결과 취합 |
| callback | aggregate | 전송만 재시도 |

DB 한 트랜잭션에 작업과 의존성을 함께 저장한다. 별도 메모리 큐나 발행 단계가 없어 접수와 큐 등록 사이의 유실이 없다.
성공한 단계는 재실행하지 않는다. 재시도는 외부 서비스 오류·시간 초과 등 일시적 오류에만 적용한다.
반복 장애가 시도 제한을 넘으면 종료 상태로 전환되어 세션 전체가 영원히 대기하지 않는다.

워커 임대가 만료되면 작업을 회수한다. 이전 워커의 완료 저장은 임대 토큰으로 거부한다.
프로세스 장애 전 이미 수행한 계산이 다시 실행될 수 있으므로 exactly-once 계산을 보장하지는 않는다.
원본 복사·변환 파일은 임시 파일에 쓰고 완료 후 원자적으로 교체한다.

재사용 범위는 **동일 작업의 재시도·재접수**다. 새 requestId 사이의 단계 결과 캐시나
정책 일부 변경에 따른 세밀한 자동 재사용은 아직 제공하지 않는다.

## 측정 정의와 한계

- 출력 시간은 디코딩된 음성 시작 기준 ms, 구간은 `[start, end)`다. 무음을 제거하거나 속도를 변경하지 않는다.
- 입력은 단일 오디오 스트림 및 mono/stereo를 지원한다. stereo는 downmix하며 별도 화자 채널 분리는 하지 않는다.
- VAD는 패딩 없이 측정 구간을 만든다. 최소 발화·비발화 길이와 임계값은 정책에 저장된다.
- 저에너지·0값·클리핑 지표는 표준화된 파형의 관찰값이다. 원본의 녹음 장애나 실제 침묵을 확정하지 않는다.
- 말 속도는 전사된 **한글 음절 블록 수**를 이용한 추정이다. 간투사·반복이 전사에 있으면 포함한다.
  영어·숫자·발음으로 풀어 쓸 수 없는 표기는 세지 않고 한계를 표시한다. 음소 속도나 정확한 조음 속도와 다르다.
- 속도 분모는 첫 발화~마지막 발화 구간 길이 또는 VAD 발화 길이 합이다. 앞뒤 여백은 제외한다.
- 안정성은 기본 5초 완전 구간의 속도 표준편차·변동계수다. 단어 중간 시각으로 구간에 배정한다.
  최소 발화량 조건을 충족하는 구간이 두 개 미만이거나 단어 시간이 불완전하면 제공하지 않는다.
- WhisperX 강제 정렬은 현재 연결하지 않았다. 기본 ASR 시간의 유효성만 검사하고 누락·비정상 값을 보존한다.
- 유창성은 원문 인용과 문자 범위를 검증한다. 없는 근거를 반환하면 분석 실패로 처리한다.
- 마지막 문장이 미완결로 관찰돼도 사용자가 직접 종료한 것이 아니면 `uncertain`으로 반환한다.

## 검증

```bash
.venv/bin/pytest tests/audio -q
.venv/bin/mypy src
```

단위·통합 테스트는 가짜 모델로 12개 파일의 흐름, 부분 실패, 중복 요청, 임대 회수,
콜백 재시도, 경로 제한, 시간 계산, LLM 근거 검증을 확인한다.
실제 FFmpeg 테스트는 바이너리가 있을 때만 실행한다. 한국어 ASR/VAD 정확도와 처리 시간·GPU 메모리는
실제 모델·한국어 녹음으로 별도 검증해야 한다. 가짜 모델 테스트 통과를 실제 분석 정확도로 해석하지 않는다.
