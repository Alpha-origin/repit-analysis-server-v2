# CPU 영상 행동 관찰 v1

전체 면접 영상을 200ms 간격으로 관찰한다. 실제 눈동자 시선·감정·성격·정직성·합격 가능성을 추론하거나
기존 면접 점수에 합산하지 않는다. 카메라 구도·밝기·가림·말하기·질문 청취 구간이 관찰치에 영향을 준다.

## 구현과 기본값

기존 영상 접수·다운로드·ffprobe 검사·전체 디코딩·SQLite 워커·콜백·보관을 재사용한다.
기본 `unconfigured` backend는 기존 unavailable 결과를 유지한다. 실제 분석은 `process`로 명시해서 연결한다.
새 HTTP endpoint와 API/Chat DB 의존성은 없다. 일반 API/텍스트 워커는 선택 모델 패키지를 import하지 않는다.

```sh
uv sync --frozen --extra behavior
```

잠근 추론 패키지: MediaPipe 1.1.1, PyAV 19.0.1, NumPy 2.5.4. release manifest에는 Python minor와 세 버전도 기록한다.
모델은 공식 Face Landmarker·Pose Landmarker Lite CPU Tasks다. 모델 파일은 저장소에 포함하지 않는다.
공식 배포 위치·모델 카드·사용 조건은 운영자가 확인하고 release 자료와 함께 보관한다.

## 명시적 모델 준비

운영자가 미리 준비한 읽기 전용 release 디렉터리의 두 파일을 사용한다.
기동·사용자 작업 처리 중 자동 다운로드하지 않는다. 파일 경로는 manifest 디렉터리 내부의 일반 파일이어야 한다.

| 파일 | 공식 출처 | SHA-256 |
|---|---|---|
| face_landmarker.task | [Face 모델](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task) | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| pose_landmarker_lite.task | [Pose Lite 모델](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task) | `59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a` |

파일을 실제 배포 환경에 제공한 뒤 해당 환경의 버전·hash를 기록한다. 기존 manifest는 덮어쓰지 않는다.

```sh
uv run python evaluation/behavior/prepare_release.py /srv/repit/models/mp-face-pose-v1
```

[manifest 예제](../deploy/video/behavior-manifest.example.json)는 Python 3.12 기준이다.
새로운 모델 artifact·전처리·임계값은 같은 release 이름으로 덮어쓰지 않는다.

```dotenv
VIDEO_ANALYZER__BACKEND=process
VIDEO_ANALYZER__EXECUTABLE=/srv/repit/venv/bin/repit-behavior-analyzer
VIDEO_ANALYZER__MODEL_PATH=/srv/repit/models/mp-face-pose-v1/manifest.json
VIDEO_ANALYZER__MODEL_VERSION=mp-face-pose-v1
VIDEO_ANALYZER__DATA_SCHEMA_VERSION=behavior-v1
```

CPU 워커는 claim 전에 manifest·hash·runtime·설정 버전을 대조한다. io/callback 워커에는 모델이 필요 없다.
보안·토큰·allowlist·레인·자원 제한은 [기존 운영 문서](video-analysis.md)를 따른다.
`--model-path`, `--model-version`, `--input-json`은 기존 process 어댑터 인자를 그대로 사용한다.

## 시간축과 관찰 지표

원본 PTS를 첫 표시 프레임 기준으로 정규화하고, 각 200ms slot의 첫 프레임만 처리한다.
비어 있는 slot을 채우거나 평균 fps로 시간을 만들지 않는다. 회전·SAR을 반영하며, 미지정 MP4 SAR 0/1은
기존 파일 검사와 동일하게 정사각 픽셀로 처리한다. RGB의 긴 변은 최대 640px이고 좌우 반전을 추가하지 않는다.
최근 3개 연속 유효 slot의 중앙값으로 연속값을 완화하며, 공백·대상 모호성·지표 누락에서 창을 초기화한다.

| key | 관찰 내용 | 분모·조건 |
|---|---|---|
| faceVisibleRatio | 단일 얼굴 관찰 비율 | 얼굴 0/1개를 판단한 시간. 2개 이상은 제외 |
| headFacingRatio | 머리 정면 방향 추정 | 단일 얼굴·유효 변환 행렬·충분한 bbox, abs(yaw/pitch) 각 20도 이하 |
| shoulderLevelRatio | 양쪽 어깨 수평 관찰 | 단일 얼굴/pose·어깨 visibility/presence ≥0.5·폭 ≥영상10%, 기울기8도 이하 |
| upperBodyStableRatio | 상체 중심 안정 구간 | 연속 어깨 중심 속도 ≤0.15 어깨폭/초, 첫 slot·400ms 초과 간격 제외 |
| smileShapeRatio | 미소 형태 관찰 | mouthSmileLeft/Right 평균 ≥0.5. 감정 확률·태도 점수가 아님 |

얼굴 방향은 변환 행렬의 3×3에서 SVD로 scale을 제거하고 proper rotation을 복원한다.
`R=Rz(roll) Ry(yaw) Rx(pitch)`, yaw=`atan2(-R20,hypot(R00,R10))`, pitch=`atan2(R21,R22)`다.
행렬 특이점·작은 얼굴(높이12%/면적1% 미만)은 방향 추정에서 제외한다.
어깨는 pixel 좌표로 `atan2(abs(dy),abs(dx))`, 중심 이동은 인접 두 어깨폭 평균과 실제 PTS 간격으로 정규화한다.
다중 인물의 첫 사람을 임의 선택하지 않는다. 원본·landmark·얼굴 crop을 출력·로그로 보관하지 않는다.

## 결과 계약

- callback 최상위 schemaVersion=`1` 유지.
- `result.analysis.data.schemaVersion=behavior-v1`, modelVersion=`mp-face-pose-v1`, thresholdsVersion=`behavior-policy-v1`.
- 기계 스키마: [behavior-data.schema.json](behavior-data.schema.json).
- 공유 예제: [정상](../tests/behavior/fixtures/behavior-ready.json), [부분](../tests/behavior/fixtures/behavior-partial.json),
  [얼굴 0%](../tests/behavior/fixtures/behavior-zero-face.json).

각 metric은 status·value·numeratorMs·denominatorMs·coverageRatio·unavailableReason을 갖는다.
비율은 분자/분모, coverage는 분모/전체길이이며 ROUND_HALF_UP으로 소수점6자리까지 반올림한다.
측정 불가는 value=null이고 유효 0%와 다르다. coverage는 정확도나 신뢰도 점수가 아니다.

전체 ≥10초, 지표 분모 ≥10초, coverage ≥30%이면 해당 지표 AVAILABLE이다.
모두 AVAILABLE·각 coverage ≥80%·전체 sampling coverage ≥90%·component 실패 없음이면 ready다.
한 지표 이상 AVAILABLE이면 partial, 전부 측정 불가면 unavailable/BEHAVIOR_NOT_MEASURABLE다.
partial은 종료 상태이며 error=null, 부족한 항목의 사유는 data 안에 있다.

사유는 짧은 전체 영상 → 필요한 단일 얼굴 없음 → 해당 모델 출력 없음 → 관찰 범위 부족 순서다.
코드는 INSUFFICIENT_DURATION, NO_SINGLE_FACE, HEAD_POSE_UNAVAILABLE, SHOULDERS_NOT_VISIBLE,
BLENDSHAPES_UNAVAILABLE, LOW_COVERAGE다. 동일 원본의 측정 부족은 retryable=false다.
모델 파일·실행·출력 오류를 품질 부족으로 바꾸지 않는다. 기존 INTERNAL_ERROR·PROCESSING_TIMEOUT을 따른다.

timeline은 폭 `max(10000,ceil(durationMs/60000)*1000)`의 최대60개 bin이다.
slot/bin 교집합 길이로 분할해 최상위 분자·분모 합과 일치시킨다.
2초 이상 연속 조건 밖인 구간은 이벤트로 표시하며, 미관찰을 건너뛰지 않는다.
종류는 FACE_NOT_VISIBLE·HEAD_TURNED·SHOULDER_TILTED·UPPER_BODY_MOVEMENT다.
AVAILABLE 지표의 이벤트만 start/type/end 순으로 앞60개를 반환한다. 다른 지표는 겹칠 수 있다.
절삭 시 eventsTruncated와 EVENTS_TRUNCATED 경고를 함께 넣는다.

요약은 고정 한국어 템플릿 최대3문장이다. LLM 호출·평균 종합점수·미소 증가 권고를 만들지 않는다.
원문 출력은 UTF-8 1MiB 이하이며 CLI stdout은 JSON 한 개다. SDK native 진단은 stderr로 분리한다.
process 어댑터는 모든 타입·시간 합·상태·ID·duration·버전을 검증하고 잘못된 출력을 INTERNAL_ERROR로 거부한다.

## 검증·운영 적용

```sh
uv run ruff check --no-fix src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run lint-imports
uv run pytest tests -q
```

기본 CI는 모델 패키지가 없어도 기존 경로를 검사한다. 별도 behavior CI는 extra 설치·명시적 테스트 모델 제공 후
실제 H.264/VP8/VP9 추론을 실행한다. 테스트 이미지를 반복한 클립은 실행·포장 smoke 검증이며 모델 정확도 증거가 아니다.

실제 모델 검사는 BEHAVIOR_MODEL_MANIFEST·BEHAVIOR_TEST_PORTRAIT 및 PATH의 analyzer를 사용한다.
BEHAVIOR_REAL_MODELS_REQUIRED=1이면 설정 누락을 성공/skip으로 처리하지 않는다.
인적 평가 도구·정답 형식과 남은 배포 gate는 [evaluation 안내](../evaluation/behavior/README.md)를 따른다.

운영 활성화 전 실제 카메라 Chrome/Safari 영상, 실제 API/S3/콜백, 원본 보관 정책, 30분/60분 처리시간·RSS,
사람 주석 평가를 별도로 확인한다. 기본 분석 timeout은900초·RSS 감시는2GiB이며 자동으로 늘리지 않는다.
모델 교체는 API 신규 분석을 끄고 기존 작업을 모두 끝낸 다음 같은 release의 모든 워커를 교체한다.
모델별 job snapshot 라우팅·무중단 다중 모델은 지원하지 않는다. 과거 결과를 새 모델 결과로 덮어쓰지 않는다.
