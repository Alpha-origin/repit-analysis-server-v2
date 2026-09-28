# 피드백 채점 방식 변경 — API 서버 연동 명세 (axis-v1)

- 대상: Repit API 서버 (`/feedback/solo`, `/feedback/multi` 콜백 수신측)
- 채점 방식 버전: `axis-v1`
- 작성일: 2026-09-28

## 0. 요약

| 항목 | 1:1 (`/feedback/solo`) | N:1 (`/feedback/multi`) |
|---|---|---|
| 요청 본문 | 변경 없음 | 변경 없음 |
| `result.overall.scoreBreakdown` | **추가** | **추가** (1:1 과 같은 구조) |
| `result.personas[].scoreBreakdown` | — | **추가** |
| `result.personas[].score` | — | **null 허용으로 변경** (담당 답변이 없을 때) ⚠ 호환성 주의 |
| 점수 필드의 의미 | 변경 (서버 계산값) | 변경 (서버 계산값) |
| 실패 콜백 | 형식 동일, 발생 조건 1건 추가 | 형식 동일, 발생 조건 1건 추가 |

`scoreBreakdown` 은 사용자에게 **"각 축이 몇 점이고, 그래서 점수가 몇 점인지"** 를 보여주기 위한 필드다.
`personas[].score` 의 null 허용을 제외하면 모두 필드 추가라 하위 호환된다.

---

## 1. API 서버 작업 요청

1. **콜백 DTO 에 `scoreBreakdown` 추가** — 1:1 `overall`, N:1 `overall`, N:1 `personas[]` 세 곳. 구조는 2장.
2. **N:1 `personas[].score` 를 nullable 로 변경** — 담당 답변이 없는 면접관은 `0` 대신 `null` 이 온다(3장).
   non-null 타입(`int`, `long`)으로 받고 있다면 역직렬화가 실패하거나 0으로 바뀔 수 있으니 `Integer`/`Long` 등으로 바꿔야 한다.
3. **저장** — 결과 화면을 다시 열 때 산출 과정을 보여줘야 하므로 `scoreBreakdown` 전체를 저장한다.
   `scoringVersion` 은 채점 방식이 바뀌었을 때 전후 점수를 구분하는 데 필요하니 함께 저장한다.
4. **프론트 전달** — 결과 조회 응답에 `scoreBreakdown` 을 그대로 내려준다. 화면 예시는 2.4, 3.3.
5. **배포 순서** — 6장. **2번(nullable) 때문에 API 서버를 먼저 배포해야 한다.**
6. **점수 비교 주의** — 5장의 의미 변경으로, 적용 전후 저장된 점수를 같은 기준으로 비교하면 안 된다.

---

## 2. `scoreBreakdown` 구조 (공통)

### 2.1 예시 — 1:1 성공 콜백

```json
{
  "jobId": "uuid",
  "sessionId": "s-1",
  "status": "succeeded",
  "result": {
    "overall": {
      "totalScore": 71,
      "intentAlignmentScore": 88,
      "reliabilityScore": 69,
      "scoreBreakdown": {
        "scoringVersion": "axis-v1",
        "axes": [
          { "axis": "INTENT", "score": 88, "weight": 35 },
          { "axis": "DEPTH", "score": 38, "weight": 25 },
          { "axis": "SPECIFICITY", "score": 63, "weight": 25 },
          { "axis": "ACCURACY", "score": 100, "weight": 15 }
        ],
        "consistencyScore": 75
      },
      "summary": "...",
      "strengths": [],
      "improvements": [],
      "frequentWords": [],
      "answeredCount": 2,
      "questionCount": 2
    },
    "feedbacks": []
  }
}
```

### 2.2 필드

| 경로 | 타입 | null | 설명 |
|---|---|---|---|
| `scoreBreakdown.scoringVersion` | string | 불가 | 채점 방식 버전. 현재 `axis-v1` |
| `scoreBreakdown.axes` | array | 불가 | 항상 4개 |
| `axes[].axis` | string enum | 불가 | `INTENT` / `DEPTH` / `SPECIFICITY` / `ACCURACY`. 항상 이 순서다 |
| `axes[].score` | integer (0~100) | 가능 | 축 점수. 해당 없는 축이면 null |
| `axes[].weight` | integer (0~100) | 가능 | 실제 적용된 가중치(%). `score` 가 null 이면 함께 null |
| `scoreBreakdown.consistencyScore` | integer (0~100) | 가능 | 답변 간 일관성. 점수 계산에 들어가지 않는 별도 지표. `overall` 에서 답변이 1개면 null, `personas[]` 에서는 항상 null |

`overall.scoreBreakdown` 은 1:1 · N:1 모두 **항상 있다**(null 아님).

**축 의미** (표시 이름은 프론트가 정한다)

| axis | 권장 표시 이름 | 보는 것 | 기본 가중치 |
|---|---|---|---|
| `INTENT` | 의도 충족 | 질문이 물은 것에 답했는가 | 35 |
| `DEPTH` | 깊이 | 판단의 이유, 대안 비교, 한계 인식 | 25 |
| `SPECIFICITY` | 구체성 | 실제로 한 행동·방법과 그 결과 | 25 |
| `ACCURACY` | 정확성 | 확립된 기술 개념과 맞는가 (기술 질문에만 적용) | 15 |

### 2.3 보장 사항

- **표시값으로 점수를 재계산할 수 있다.** null 이 아닌 축만으로
  `Σ(score × weight) / Σ(weight)` 를 계산해 반올림(0.5 는 올림)하면
  `overall` 은 `totalScore`, `personas[]` 는 그 면접관의 `score` 와 항상 같다.
- `ACCURACY` 가 null 이면 나머지 세 축의 `weight` 는 35/25/25 그대로 내려가고 합이 85 다.
  화면에 비율로 보여주려면 합으로 나눈다(약 41% / 29% / 29%).
- `intentAlignmentScore` 는 `overall` 의 `INTENT` 점수와 같다.
- `reliabilityScore` 는 `consistencyScore` 와 `SPECIFICITY` 점수의 평균(반올림)이다. `consistencyScore` 가 null 이면 `SPECIFICITY` 점수다.

### 2.4 화면 표시 예 — 종합 점수

```
종합 점수 71점
─────────────────────────────
의도 충족   88점 × 35%
깊이        38점 × 25%
구체성      63점 × 25%
정확성     100점 × 15%
─────────────────────────────
일관성 75점 (종합 점수와 별도)
```

- `ACCURACY` 가 null 이면 "해당 없음"으로 표시한다.
- `consistencyScore` 가 null 이면 "판단 불가"로 표시한다.

---

## 3. N:1 면접관별 결과 — `result.personas[]`

### 3.1 예시

```json
"personas": [
  {
    "personaId": "p-1",
    "role": "TECH",
    "score": 74,
    "scoreBreakdown": {
      "scoringVersion": "axis-v1",
      "axes": [
        { "axis": "INTENT", "score": 88, "weight": 35 },
        { "axis": "DEPTH", "score": 63, "weight": 25 },
        { "axis": "SPECIFICITY", "score": 63, "weight": 25 },
        { "axis": "ACCURACY", "score": 75, "weight": 15 }
      ],
      "consistencyScore": null
    },
    "comment": "...", "strengths": [], "improvements": []
  },
  {
    "personaId": "p-2",
    "role": "HR",
    "score": 70,
    "scoreBreakdown": {
      "scoringVersion": "axis-v1",
      "axes": [
        { "axis": "INTENT", "score": 75, "weight": 35 },
        { "axis": "DEPTH", "score": 63, "weight": 25 },
        { "axis": "SPECIFICITY", "score": 69, "weight": 25 },
        { "axis": "ACCURACY", "score": null, "weight": null }
      ],
      "consistencyScore": null
    },
    "comment": "...", "strengths": [], "improvements": []
  },
  {
    "personaId": "p-3",
    "role": "CEO",
    "score": null,
    "scoreBreakdown": null,
    "comment": "평가할 답변이 없었습니다.", "strengths": [], "improvements": []
  }
]
```

### 3.2 필드 변경

| 경로 | 이전 | 이후 |
|---|---|---|
| `personas[].score` | integer, 담당 답변이 없으면 0 | **integer \| null**, 담당 답변이 없으면 **null** |
| `personas[].scoreBreakdown` | (없음) | object \| null. 담당 답변이 없으면 null |

- 면접관 점수는 그 면접관이 담당한 답변에만 4장 공식을 적용한 값이다.
- `role` 이 `TECH` 가 아닌 면접관은 `ACCURACY` 가 항상 null 이다(정확성은 기술 질문에만 적용).
- 면접관별 `consistencyScore` 는 항상 null 이다. 담당 문항이 2~3개라 그 안에서 모순을 판단하지 않는다.

### 3.3 화면 표시 시 주의

- **종합 점수는 면접관 점수의 평균이 아니다.** 종합 점수는 전체 문항의 축 평균으로 계산하므로,
  면접관마다 담당 문항 수가 다르거나 비개발 면접관이 섞이면 면접관 점수들의 단순 평균과 달라진다.
  종합 점수의 근거는 `overall.scoreBreakdown` 으로 보여주고, 면접관 점수는 "면접관별 평가"로 따로 보여준다.
- `score` 가 null 인 면접관은 "평가할 답변 없음"으로 표시한다.

---

## 4. 점수 계산 규칙 (1:1 · N:1 공통)

LLM 은 답변한 문항마다 4개 축의 등급(0~4)과 세션 전체의 일관성 등급(0~4)만 매긴다. 점수는 분석 서버가 계산한다.
등급별 판정 기준은 `src/app/core/common/feedback/rubric.py` 에 있고, N:1 은 같은 기준을 면접관 직책 관점으로 해석한다
(예: HR 의 깊이 = 동기와 행동의 이유, CEO 의 깊이 = 우선순위와 가치 판단의 근거).

1. 등급 → 점수: 0/1/2/3/4 → 0/25/50/75/100
2. 축 점수 = 대상 문항들의 해당 축 평균을 정수로 반올림(0.5 는 올림)
3. 점수 = 2 의 정수 축 점수를 가중합한 뒤 정수로 반올림
4. 의도 충족이 0등급(동문서답·회피)인 문항은 나머지 축도 0으로 처리한다.
5. 정확성이 해당 없는 문항(기술 내용이 없는 질문, N:1 의 비 `TECH` 면접관 문항)은 정확성 평균에서만 빠진다.
   대상 문항 전체가 해당 없으면 정확성을 빼고 나머지 세 축으로 계산한다.
6. 미답변 문항은 계산에서 빠지고 `answeredCount` / `questionCount` 로만 드러난다(기존과 동일).

대상 문항은 `overall` 이면 답변한 전체 문항, `personas[]` 면 그 면접관이 담당한 답변한 문항이다.

**계산 예시** — 답변 2개, 등급 (의도, 깊이, 구체성, 정확성) = (4, 2, 3, 4), (3, 1, 2, 4)

| 축 | 계산 | 점수 |
|---|---|---|
| 의도 충족 | (100 + 75) / 2 = 87.5 | 88 |
| 깊이 | (50 + 25) / 2 = 37.5 | 38 |
| 구체성 | (75 + 50) / 2 = 62.5 | 63 |
| 정확성 | (100 + 100) / 2 | 100 |
| **점수** | (88×35 + 38×25 + 63×25 + 100×15) / 100 = 71.05 | **71** |

---

## 5. 기존 필드의 의미 변경

이전에는 LLM 이 아래 점수를 각각 0~100 으로 직접 매겼다. 이제 4장 규칙으로 서버가 계산한다.

| 필드 | 이전 | 이후 |
|---|---|---|
| `overall.totalScore` | LLM 이 면접 전체를 보고 매긴 점수 | 4축 가중합 |
| `overall.intentAlignmentScore` | LLM 이 매긴 "물은 것에 답했는가" 점수 | 의도 충족 축 점수 |
| `overall.reliabilityScore` | LLM 이 매긴 일관성 점수 | (일관성 + 구체성) / 2. 답변 1개면 구체성 |
| `personas[].score` (N:1) | LLM 이 매긴 면접관별 점수, 담당 답변 없으면 0 | 담당 답변에 4장 공식을 적용한 값, 담당 답변 없으면 null |

- 이전 LLM 산출값은 70~80점대에 몰리는 경향이 있었고, 새 계산값은 더 넓게 퍼질 수 있다.
  적용 전후 점수를 같은 기준으로 비교·통계 처리하면 안 된다.

**실패 콜백 조건 추가** — LLM 이 어떤 문항의 축 등급을 빠뜨리거나 0~4 범위를 벗어난 값을 내면
그 문항은 피드백 누락과 같이 보고 실패 콜백을 보낸다. 형식과 상태 코드는 기존과 같다.

| 엔드포인트 | statusCode | message |
|---|---|---|
| `/feedback/solo` | 500 | 일부 문항의 피드백이 생성되지 않았습니다. |
| `/feedback/multi` | 500 | 일부 채점 결과가 생성되지 않았습니다. |

세션 단위 일관성 등급이 빠지거나 잘못된 경우는 실패로 보지 않는다. `consistencyScore` 가 null 로 나간다.

---

## 6. 배포 순서와 호환성

1. **API 서버 먼저 배포** — `scoreBreakdown` 수신·저장, N:1 `personas[].score` nullable 처리.
2. **분석 서버 배포.**

- `scoreBreakdown` 추가만 놓고 보면, API 서버가 모르는 필드를 무시하는 설정(Spring Boot 기본 Jackson 설정)이면 분석 서버가 먼저 나가도 연동은 깨지지 않는다.
  다만 그동안의 결과에는 산출 과정이 저장되지 않는다.
- **N:1 `personas[].score` 의 null 은 이전 계약과 호환되지 않는다.** API 서버가 준비되기 전에 분석 서버가 먼저 나가면
  담당 답변이 없는 면접관이 있는 N:1 결과에서 역직렬화가 실패하거나 0으로 잘못 저장될 수 있다.
- 기술 면접관의 `role` 은 기존처럼 `TECH` 로 보낸다(분석 서버는 앞뒤 공백을 지우고 대문자로 비교한다).
  다른 문자열로 보내면 그 면접관 문항에 정확성 축이 적용되지 않는다.
