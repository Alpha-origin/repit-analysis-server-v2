# 행동 관찰 평가

이 디렉터리는 운영 활성화 전에 실제 카메라 영상과 사람 주석을 비교하는 도구다.
영상·얼굴 crop·개인 식별정보를 저장소에 커밋하지 않는다. 평가 자료는 사용 권한과 보관·삭제 담당을 정한다.

## 모델 release 준비

공식 모델 파일을 운영자가 별도로 제공한다. 서비스가 모델을 자동 다운로드하지 않는다.

```sh
uv sync --frozen --extra behavior
uv run python evaluation/behavior/prepare_release.py /absolute/release/directory
```

기존 manifest는 덮어쓰지 않는다. 실제 파일 hash와 현재 고정 패키지/Python 버전을 기록하고 로드 검증한다.
저장소의 [예제 manifest](../../deploy/video/behavior-manifest.example.json)와 모델 출처·hash를 대조한다.

## 사람 주석 입력

JSON은 최상위 clips 배열이다. 각 clip은 다음 필드를 갖는다.

- clipId: 익명 클립 코드, personCode: 익명 참여자 코드, condition: 촬영 조건 코드.
- prepared: 기존 영상 검증 단계를 통과한 PreparedVideo JSON. snake_case 필드를 그대로 사용한다.
- annotations: 다섯 metric key 각각의 구간 배열. 각 구간은 startMs/endMs/observed.
- observed=true: 해당 지표 조건을 만족, false: 조건 밖, null: 사람도 판단 불가.
- 구간은 정렬·비중첩·영상 내부 범위여야 한다. 주석하지 않은 시간은 정확도 계산에서 제외된다.

예를 들어 headFacingRatio 주석의 일부는 다음과 같다.

```json
[
  {"startMs": 0, "endMs": 6000, "observed": true},
  {"startMs": 6000, "endMs": 12000, "observed": false},
  {"startMs": 12000, "endMs": 15000, "observed": null}
]
```

처리 시 원본 크기·SHA-256과 실제 stream/PTS를 검사한다. 잘못된 입력을 조용히 건너뛰지 않는다.
서비스와 같은 모델·전처리·표본화·중앙값 완화를 사용하며 결과 보고서에는 원본 경로와 이미지를 넣지 않는다.

```sh
uv run python evaluation/behavior/evaluate.py \
  --dataset /absolute/private/dataset.json \
  --model-manifest /absolute/release/manifest.json \
  --out /absolute/private/report.json
```

JSON과 같은 이름의 Markdown 표를 만든다. 지표별 시간 가중 TP/FP/FN/TN, 관찰 제외/누락,
관찰 가능한 정답 구간 중 실제 측정 범위, F1, 관찰 불가 제외율, 클립별 비율 절대오차를 제공한다.
F1은 실제 관찰한 라벨 구간 기준이므로 coverage·missedObservationMs를 반드시 함께 평가한다.
분모가 없으면 수치는 null이며 임의 100%로 채우지 않는다. 도구가 배포 통과를 자동 선언하지 않는다.

## 운영 전 인수 기준

최소6명·30개 클립·합계30분, 그중20% 이상 독립 이중 주석과 불일치 조정으로 시작한다.
정면·좌우/상하 방향·어깨 기울기·상체 이동·미소/말하기·안경·가림·작은 얼굴·저조도·다중 인물을 포함한다.
조정에 사용한 클립과 별도 holdout을 분리한다. 이 최소 표본이 모든 집단에 대한 정확도를 증명하지는 않는다.

- 표준 환경 얼굴 유무 F1≥0.90.
- 나머지 네 관찰 지표 각각 F1≥0.80와 클립 비율 절대오차 중앙값≤10%p.
- 관찰 가능 정답 구간의 측정 coverage≥0.80. 적은 성공 구간만 남겨 F1을 높이지 않는다.
- 사람이 판단 불가인 구간의90% 이상 제외. 조건별 성능·실패·coverage를 따로 기록한다.
- 실제 브라우저/카메라 영상과30분/60분 CPU 실행시간·RSS·출력크기 검증.
- 실제 API/S3/인증된 callback 및 callback 유실 GET 복구 검증.

현재 저장소 구현·자동 smoke만으로 위 사람 평가·운영 연동을 완료했다고 판단하지 않는다.
runtime·모델 hash·modelVersion·thresholdsVersion·저장소 commit·미검증 항목을 보고서에 함께 기록한다.
