# 역량 채점 계약 competency-v1

기존 계약과 결과는 `axis-v4`로 유지한다. 이 계약은
`FEEDBACK_SCORING_VERSION=competency-v1`일 때 SOLO와 MULTI에 함께 적용한다.
기본값은 `axis-v4`다.

## 요청

`POST /feedback/solo`, `POST /feedback/multi`의 각 `questions[]`에
선택 필드 `category: string | null`을 추가한다.

- 공백 제거와 소문자 변환 뒤 `^[a-z][a-z0-9_]{0,63}$` 형식만 사용한다.
- 형식이 잘못되거나 값이 없어도 422로 거부하지 않는다.
- FOLLOW에 유효한 카테고리가 없으면 부모 카테고리를 상속한다.
- 직무별 매핑을 먼저 선택한다. 같은 `performance`도 DBA와 경영기획에서 다르다.
- MULTI 비TECH는 비기술 매핑만 사용하며 직무 지식을 주·보조로 평가하지 않는다.
- 미분류는 모델이 주 역량 하나를 추론한다. 추론하지 못하면 응답 적중만 집계한다.

매핑과 슬롯의 단일 기준은 `src/app/core/common/feedback/competency/`다.

## 점수

| 축 순서 | 가중치 |
|---|---:|
| PROBLEM_SOLVING | 30 |
| KNOWLEDGE | 25 |
| OWNERSHIP | 25 |
| COLLABORATION | 10 |
| GROWTH | 10 |

주 역량은 항상 채점한다. 보조 역량은 검증된 근거로 계산한 등급이 1 이상일 때만 집계한다.
모델은 원문 슬롯을 제출하고 서버가 등급을 계산한다.
응답 적중과 직무 지식의 정확성만 모델이 등급을 정한다.

| response | 주 역량 상한 | 보조 역량 |
|---|---:|---|
| 0 | 0 | 제외 |
| 1 | 1 | 제외 |
| 2 | 3 | 검증된 근거 등급 그대로 |
| 3 또는 4 | 4 | 검증된 근거 등급 그대로 |

등급은 25를 곱해 점수로 바꾼다. 역량별 평균을 0.5 올림으로 표시 정수화한 뒤
관측된 축의 `Σ(score × weight) / Σ(weight)`를 같은 방식으로 반올림해 종합을 계산한다.

**0점과 null:** 주 역량에서 응답 0을 받은 문항도 관측 문항으로 집계하므로
그 역량은 0점이고 근거 문항 수는 증가한다. 채점 문항이 없는 역량만
`score`, `weight`, `verdict`가 null이고 `evidenceCount`가 0이다.
원안의 “응답이 모두 0이면 모든 축 null” 문장은 상세 계산 알고리즘과 달라 적용하지 않는다.
관측된 역량이 전혀 없으면 종합은 0이다.

판정은 반올림 전 평균 등급 기준이다:
3.5 이상 STRONG, 2.5 이상 SOLID, 1.5 이상 BORDERLINE, 나머지 WEAK.
초기 가중치와 판정 경계는 오프라인 검증 대상이다.

## 성공 콜백

`overall.scoreBreakdown`과 `personas[].scoreBreakdown`에 다음 필드를 보존한다.

| 필드 | 내용 |
|---|---|
| scoringVersion | competency-v1 |
| axes | 위 순서의 5개 항목 axis·score·weight·verdict·evidenceCount |
| consistencyScore | consistency 등급 × 25 답변 1개면 null |
| response | score·hitCount·gradedCount 응답 등급 평균 × 25 hit는 등급 3 이상 |
| motivation | score·verdict·evidenceCount 해당 문항이 없으면 null |
| credibility | score·issues 면접관별 결과에서는 null |

- `overall.totalScore`는 5역량 종합이다.
- `overall.intentAlignmentScore`는 `response.score`와 같다.
- `overall.reliabilityScore`는 `credibility.score`와 같고 답변 1개면 null이다.
- 응답 적중·동기·신뢰성은 종합 가중 평균에 넣지 않는다.
- 담당 답변이 없는 면접관은 `score`와 `scoreBreakdown` 모두 null이다.
- 담당 답변이 있어도 가중 역량이 관측되지 않으면 면접관 `score`만 null이다.
  이때 `scoreBreakdown`은 유지해 동기·응답 지표를 제공한다. 관측된 역량의 0점은 그대로 보낸다.
- 면접관별 `consistencyScore`와 `credibility`는 항상 null이다.

문항 피드백은 다음을 추가한다. 기존 v4 콜백에는 추가 필드를 넣지 않는다.

| 필드 | 내용 |
|---|---|
| responseLevel | 0~4 |
| axisScores | 이 문항에서 집계한 5역량 코드 → 0~100 동기는 제외 |
| competencies | competency·role·level·score·evidence |
| competencies[].role | PRIMARY 또는 SECONDARY 주 역량 먼저 |
| competencies[].evidence | slot·quote 원문 인용 |

동기 문항은 competencies에 MOTIVATION으로 포함하지만 axisScores에는 넣지 않는다.
신뢰성 issue는 kind·questionIds·quotes를 갖고 서로 다른 두 문항의 실제 답변을 인용한다.
kind는 ROLE·TECH_CHOICE·CAUSE·DETAIL·OTHER 중 하나다.
꼬리질문 설명 부족이나 측정 방법을 모르는 것 자체는 모순이 아니다.
신뢰성 경고 때문에 종합 점수나 판정을 제한하지 않는다.

## 검증 실패

응답 등급 누락·범위 오류, 주 역량 그룹 누락·원문 인용 불일치는
한 번 재시도한다. 그래도 누락되면 기존 형식의 500 실패 콜백을 보낸다.
출력 토큰 제한으로 잘린 응답은 재시도하지 않는다.
잘못된 보조 그룹은 그 그룹만, 잘못된 모순은 그 issue만 버린다.
로그에는 답변·인용 원문 대신 사유 코드·숫자와 집계만 남긴다.

## 배포와 범위

API 서버의 카테고리·새 필드 저장 → 웹의 버전별 표시 → 분석 서버 코드 배포
→ 오프라인 전환 기준 검토 → 설정 전환 → 2주 운영 관찰 순서다.
웹의 과거 면접 비교와 통계는 같은 scoringVersion끼리만 계산해야 한다.
롤백은 설정을 axis-v4로 되돌린다. 저장된 v1 결과를 변환하거나 삭제하지 않는다.

채팅 서버 변경, 기업별 가중치, 서류 대조, 실제 합불 수집은 이번 구현에서 제외한다.
검증 실행과 미완료 절차는 `evaluation/README.md`를 참고한다.
