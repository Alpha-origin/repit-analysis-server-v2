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
5. 실제 HTTP 피드백 경로에 동시 요청을 보내 접수 시간, 콜백 완료 시간, 실패율, 잘림률을 기록한다. 동일 입력을 사용해 저부하·고부하 결과의 피드백 품질도 표본 비교한다.

## 결과 제출 체크리스트

- 고정 데이터셋 버전·건수·유형 분포
- 모델 ID, 프롬프트 Git 커밋, 실행 시각, 반복 횟수, 파라미터
- 평가자 수·경력과 축별 일치율 또는 의견 차이, 근거 예시
- 프롬프트 전후 품질 평가표와 출력 원문 링크
- 반복 평가의 점수 범위·등급 일치율·형식 실패율
- 동시성별 요청 수, 접수·콜백 p50/p95, 성공률, 외부 모델 오류율, 출력 잘림률
- API 서버의 `scoreBreakdown` 저장·조회 및 프론트 표시 확인 화면

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

부하 실험은 외부 Anthropic API 호출과 콜백 전달을 실제로 발생시키므로 합성 데이터·격리된 환경에서만 실행한다. 수집기는 신뢰할 수 있는 TLS 인증서를 적용한 HTTPS 443 주소로 공개해야 한다. 분석 서버의 `APP_CALLBACK_ALLOWED_HOSTS`에 수집기 호스트를 등록하고, `APP_INTERNAL_CALLBACK_TOKEN`을 수집기의 `FEEDBACK_CAPTURE_TOKEN`과 동일하게 설정한다.

```sh
FEEDBACK_CAPTURE_TOKEN=YOUR_TOKEN FEEDBACK_CAPTURE_OUTPUT=/tmp/feedback-callbacks.jsonl uvicorn evaluation.callback_collector:app --host 127.0.0.1 --port 8080 --workers 1
python -m evaluation.load_feedback --base-url https://ANALYSIS_HOST --callback-url https://CALLBACK_HOST/capture --callback-file /tmp/feedback-callbacks.jsonl --requests 20 --concurrency 2 --output /tmp/feedback-load-low.json --dry-run
python -m evaluation.load_feedback --base-url https://ANALYSIS_HOST --callback-url https://CALLBACK_HOST/capture --callback-file /tmp/feedback-callbacks.jsonl --requests 20 --concurrency 2 --output /tmp/feedback-load-low.json
```

수집기 앞에는 신뢰할 수 있는 인증서가 있는 HTTPS 443 역방향 프록시를 두고 `/capture`를 위 로컬 포트로 전달한다. 낮은 동시성과 높은 동시성을 각각 여러 차례 실행해 완료 p50/p95와 실패·누락 콜백을 비교한다. 수집 JSONL의 성공 콜백에서 동일 사례의 피드백 내용을 표본 추출해 블라인드 품질 평가도 반복한다. 부하 실험의 요청 접수 시간만으로 전체 피드백 생성 시간을 주장하지 않는다. `scoreBreakdown` 콜백 존재 여부는 이 도구가 확인하지만, API 서버 저장·조회와 프론트 표시 여부는 해당 저장소/화면에서 별도로 검증해야 한다.
