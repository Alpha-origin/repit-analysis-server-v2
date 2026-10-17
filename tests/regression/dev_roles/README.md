# 개발 직군 모델 입력 회귀

기준: `origin/main 6ad7939` (2026-10-17 fetch). 비개발 문서 분석 이슈 #33의 J-0 선행 작업(#32).

`RecordingClient`로 실제 실행 객체의 모델 호출 경계를 기록한다. 고정 대상은 시스템·사용자 메시지, 도구 정의, `tool_choice`다. `/profile`은 첫 호출과 강제 종료 도구, 사이클은 전체·일부 카테고리의 SOLO/MULTI 및 재시도 입력, 재작성은 BACKEND/FRONTEND, 채점은 SOLO/MULTI를 검사한다. 기업 없음 프롬프트의 기존 #30 골든은 유지한다.

```bash
uv run pytest tests/regression -q
UPDATE_GOLDEN=1 uv run pytest tests/regression -q
```

골든은 의도한 개발 직군 변경에서만 갱신한다. 비개발 기능 추가 PR에서 골든이 바뀌면 이유를 설명해야 한다. 출력은 실제 모델 결과를 고정하지 않으며 유료 API를 호출하지 않는다. 시간·UUID는 모델 입력에 포함되지 않고, 재시도 도구 ID는 테스트에서 고정한다.

이 PR은 제품 코드·프롬프트를 바꾸지 않는다.
