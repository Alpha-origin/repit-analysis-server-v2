# 피드백 품질 검증

이 디렉터리는 `axis-v1` 채점의 근거, 반복 안정성, 프롬프트 품질, 동시 이용 성능을 검증하기 위한 입력과 절차를 보관한다. 단위 테스트의 LLM 스텁 결과를 실제 모델 품질 측정값으로 해석하지 않는다.

## 고정 입력과 평가자

- `cases.json`은 공개 가능한 합성 질문·답변이다. 실제 사용자 답변은 동의와 비식별화 후 별도 보관한다.
- 현재 10건은 검증 파이프라인을 확인하기 위한 소규모 시작 세트다. 실제 서비스의 긴 면접·다양한 직무·다수 문항을 대표한다는 근거로 사용하지 않는다.
- 기술 선택, 구현 설명, 트러블슈팅, 짧지만 충분한 답변, 회피, 기술 오류, 답변 간 모순, 비기술 면접관 질문을 포함한다.
- 평가자는 질문과 의도, 답변만 보고 `rubric.py`의 축별 0~4등급 및 판단 이유를 기록한다. 가능하면 실제 개발 면접 경험자 두 명이 서로의 판단을 보지 않고 평가한다.
- 사람의 등급은 서비스의 실시간 채점 입력이 아니라 비교 기준이다. 평가자 경력, 사례 수, 의견 불일치의 조정 방법을 결과 문서에 적는다. 자문을 받지 못했다면 "전문 면접관과 일치"라고 주장하지 않는다.

## 검증 순서

1. 입력 사례와 평가 기준, 사용할 모델 버전을 고정한다. 프롬프트 수정에 사용한 사례와 최종 검증 사례를 구분한다.
2. 동일 사례를 이전·현재 프롬프트에 각각 넣고 출력 원문과 축별 등급을 저장한다. 프롬프트 외의 모델·도구 스키마·입력은 동일하게 유지한다.
3. 동일 사례를 각 버전에서 최소 5회 평가해 총점의 최솟값·최댓값·표준편차, 문항·축별 최빈 등급 비율의 평균, 실패율을 계산한다.
4. 평가자가 버전명을 모르는 상태에서 질문 의도 충족, 실제 답변에 없는 주장, 기술 오류 판단, 개선 조언의 구체성을 평가한다. 좋은 사례와 실패 사례를 함께 남긴다.
5. 실제 API 서버 피드백 경로에 동시 요청을 보내 접수 시간, 조회에서 확인된 완료 시간, 실패율, 잘림률을 기록한다. 동일 유형의 면접을 사용해 저부하·고부하 결과의 피드백 품질도 표본 비교한다.

## 결과 제출 체크리스트

- 고정 데이터셋 버전·건수·유형 분포
- 모델 ID, 프롬프트 Git 커밋, 실행 시각, 반복 횟수, 파라미터
- 평가자 수·경력과 축별 일치율 또는 의견 차이, 근거 예시
- 프롬프트 전후 품질 평가표와 출력 원문 링크
- 반복 평가의 점수 범위·등급 일치율·형식 실패율
- 동시성별 요청 수, 접수·조회상 완료 p50/p95, 성공률, 외부 모델 오류율, 출력 잘림률
- API 서버의 `scoreBreakdown` 저장·조회와 프론트 표시 확인 화면

빈 결과표를 성공 결과로 채우지 않는다. 실제 모델 호출과 평가자 검토, API·프론트 연동이 끝나야 세 피드백 항목의 검증이 완료된다.

## 실행 방법

프로젝트 의존성을 설치한 환경에서 저장소 루트를 작업 디렉터리로 사용한다. 아래 출력 경로에는 질문·답변과 생성 피드백이 남으므로 접근을 제한하고, 공개 문서에는 비식별화한 표본만 첨부한다.

```sh
python -m evaluation.run_feedback --split holdout --repeats 5 --output /tmp/feedback-current.jsonl --dry-run
python -m evaluation.run_feedback --split holdout --repeats 5 --variant current --variant previous=evaluation/previous-system-prompt.txt --output /tmp/feedback-comparison.jsonl
python -m evaluation.report_feedback --runs /tmp/feedback-comparison.jsonl --human /tmp/human-ratings.csv --quality /tmp/quality-ratings.csv --output /tmp/feedback-report.md
```

실제 반복 채점에는 `ANTHROPIC_API_KEY`가 필요하며 호출량에 따른 비용이 발생한다. `previous-system-prompt.txt`에는 비교할 이전 버전의 **전체 렌더링된 시스템 프롬프트**를 넣는다. 이전 프롬프트가 solo와 multi에서 다르면 각 모드를 별도 실행한다. `--model`과 `--max-tokens`는 실제 서비스 설정과 맞춘다. `--dry-run`은 입력 검증만 하며 품질 측정 결과가 아니다.

`human_ratings.template.csv`의 등급은 각 축마다 0~4, 비기술 문항의 정확성은 빈 칸으로 기록한다. `reason`에는 답변의 어느 문구를 근거로 판단했는지 적는다. `quality_ratings.template.csv`의 네 품질 항목도 0~4로 평가하고 `notes`에 근거를 적는다. 평가자는 프롬프트 버전을 가린 출력 원문을 보고 평가한 다음, 결과 분석 시에만 버전명을 복원한다. 두 템플릿은 헤더만 있으므로 복사하여 값을 채워 사용한다. 평가자의 경력·인원·독립 평가 여부를 보고서에 별도로 남긴다.

## 실제 서버 연동과 부하 실험

실제 흐름은 채팅 서버 `POST /api/interviews/result` → API 서버 `POST /feedback/solo` 또는 `/feedback/multi` → 분석 서버가 API 서버 `POST /api/feedbacks/callback` 호출 → 사용자가 API 서버 `GET /api/feedbacks?interviewId=...`로 조회하는 순서다. `/capture` API는 어느 운영 서버에도 없다. 분석 서버가 전달받는 callbackUrl은 API 서버 설정의 `CALLBACK_BASE_URL`과 `/api/feedbacks/callback`을 합친 값이다.

`load_feedback.py`는 API 서버의 **수동 피드백 요청 경로**인 인증된 `POST /api/feedbacks?interviewId=...`를 동시에 호출하고, 같은 API의 `GET`으로 `SUCCEEDED`/`FAILED`를 확인한다. 실제 사용자 흐름에서 채팅 서버가 면접을 종료하면 API 서버가 피드백을 자동 접수하므로, 이미 `PENDING`/`SUCCEEDED`인 면접 ID를 이 스크립트에 다시 넣으면 안 된다. 실험 전 격리된 환경에서 테스트 계정 소유의 면접 전문·답변이 있고 아직 피드백 기록이 없는 면접 ID를 요청 수만큼 준비해야 한다. 스크립트도 모든 ID에 기존 기록이 없는지 사전 조회하고, 있으면 요청 전 중단한다.

면접 ID를 한 줄에 하나씩 적은 파일을 준비하고, 테스트 계정의 `Authorization` 헤더 **전체 값**을 `API_AUTHORIZATION` 환경 변수에 설정한다. 토큰을 저장소나 결과 파일에 넣지 않는다.

```sh
python -m evaluation.load_feedback --base-url http://localhost:8080 --interview-ids /tmp/test-interview-ids.txt --concurrency 2 --output /tmp/feedback-load-low.json --dry-run
python -m evaluation.load_feedback --base-url http://localhost:8080 --interview-ids /tmp/test-interview-ids.txt --concurrency 2 --output /tmp/feedback-load-low.json
```

낮은 동시성과 높은 동시성을 각각 여러 차례 실행해 접수·완료 p50/p95, 실패·시간 초과를 비교한다. 완료 시간은 API 조회가 최종 상태를 처음 관찰한 때라 `--poll-interval`만큼 실제 콜백 시각보다 늦을 수 있다. 원문 품질 비교가 필요할 때만 `--include-feedback`을 사용하고 결과 파일 접근을 제한한다. `scoreBreakdown`은 API 조회 응답에서 확인한다. 프론트 화면 표시는 별도로 확인해야 한다. 채팅 서버의 `load-test/README.md`는 WebSocket 면접 부하 시나리오이며 API·Anthropic을 모의하므로 실제 피드백 생성 품질 측정값으로 섞지 않는다.
