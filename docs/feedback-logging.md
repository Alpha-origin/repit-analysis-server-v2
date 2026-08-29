# 피드백 생성 및 로깅 흐름

`POST /feedback/solo`와 `POST /feedback/multi`는 요청을 `202 Accepted`로 받은 뒤 백그라운드에서
피드백을 생성하고, 결과를 `callbackUrl`로 전송한다. 모든 피드백 디스패처 로그는 `job_id`로
한 작업을 추적할 수 있고 `extra` 문맥은 로그 끝에 JSON으로 출력된다.

## 생성 과정

1. **요청 수락**: HTTP DTO를 내부 요청으로 변환하고 백그라운드 작업을 등록한다.
2. **답변 조립 (`answer_assembly`)**: 질문과 답변을 `questionId`로 연결하고, 꼬리 질문에는 부모
   질문·답변 문맥을 붙인다. 공백/누락 답변은 채점 대상에서 제외한다.
3. **면접관 연결 (`persona_mapping`, N:1만)**: 질문별 담당 면접관을 연결한다.
4. **LLM 채점 (`llm_grading`)**: 답변이 있는 모든 문항을 한 번의 Anthropic tool-use 호출로
   채점한다. N:1은 문항 피드백과 면접관별 평가도 함께 생성한다.
5. **결과 조립 (`result_building`)**: LLM 결과에 요청 원문의 질문·답변을 다시 연결하고,
   자주 사용한 단어와 답변/질문 개수를 서버에서 계산한 뒤 Pydantic 스키마로 검증한다.
6. **최종 피드백 기록 (`final_feedback`)**: 종합 평가, 문항별 생성 피드백, N:1 면접관별 평가를
   INFO 로그로 남긴다. 요청에서 복사한 `questionContent`, `intention`, `userAnswer`는 로그에서
   제외한다.
7. **콜백 전송 (`callback`)**: 성공 결과 또는 실패 정보를 `callbackUrl`로 전송하고 전송 성공
   여부와 전체 처리 시간을 기록한다.

## 주요 로그 이벤트

| 이벤트 | 의미 | 주요 필드 |
|---|---|---|
| `feedback_{solo|multi}.accepted` | 요청 수락 | `job_id`, `session_id`, 입력 개수 |
| `feedback_{solo|multi}.dispatch.start` | 백그라운드 처리 시작 | `job_id`, 입력 개수 |
| `feedback_{solo|multi}.dispatch.stage.start` | 단계 시작 | `job_id`, `stage` |
| `feedback_{solo|multi}.dispatch.stage.done` | 단계 완료 | `job_id`, `stage`, `duration_ms`, 결과 개수 |
| `feedback_{solo|multi}.dispatch.stage.failed` | 단계 실패 | `job_id`, `stage`, `status_code`, `error` |
| `feedback_{solo|multi}.dispatch.final_feedback` | 최종 생성 피드백 | `job_id`, `feedback` |
| `feedback_{solo|multi}.dispatch.callback.done` | 콜백 전송 종료 | `job_id`, `delivered`, `duration_ms` |
| `feedback_{solo|multi}.dispatch.done` | 전체 작업 종료 | `job_id`, `status`, `callback_delivered`, `duration_ms` |

예시 로그는 다음과 같은 형태다.

```text
2026-08-27 10:00:01 INFO app.core.commands.dispatch_feedback_solo feedback_solo.dispatch.stage.done {"answered_count": 5, "duration_ms": 1.23, "job_id": "...", "stage": "answer_assembly", "unanswered_count": 1}
2026-08-27 10:00:08 INFO app.core.commands.dispatch_feedback_solo feedback_solo.dispatch.final_feedback {"feedback": {"overall": {"totalScore": 82, "summary": "..."}, "feedbacks": [{"questionId": "q1", "comment": "..."}]}, "job_id": "..."}
```

실패한 단계는 `stage.failed` 이후 실패 콜백(`status=failed`) 전송 로그로 이어진다. 콜백 자체가
HTTP 오류로 실패하면 웹훅 어댑터가 재시도별 원인과 최종 폐기 여부를 추가로 기록한다.
