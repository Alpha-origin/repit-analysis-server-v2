# 개발 직군 모델 입력 회귀

명세: `02-dev-regression.md`. 현재 체크아웃의 소스·기존 테스트는 기준 `origin/main 6ad7939`와 같다.
현재 main의 입력을 그대로 기록했다. 제품 코드와 기존 골든은 수정하지 않는다.

기록용 클라이언트는 모델 호출 인자를 깊은 복사로 보존하며 실제 API를 호출하지 않는다.
`/profile`의 BACKEND·FRONTEND·전공 없음, 사이클의 SOLO·MULTI와 전체·일부 카테고리 및
재시도, 재작성의 BACKEND·FRONTEND, 다면 생성 도구, SOLO·MULTI 채점을 검증한다.

시스템·사용자 문자열은 UTF-8 바이트를 보존해 비교하고, 도구와 `tool_choice`는 정렬된 JSON으로
비교한다. 사이클 재시도는 이전 대화를 포함한 네 가지 모델 입력 전체를 별도 JSON에 기록한다.
파일명에는 모드·카테고리 조합을 표시한다. #30과 겹치는 재작성 시스템과 다면 생성 메시지는
새 골든에 중복하지 않으며, 시스템 선택은 기존 #30 골든을 재사용해 검증한다.

길이와 토큰 상한은 `main/config.py`의 기본값을 사용한다. 테스트에서 해당 설정의 환경 변수와
`.env` 영향을 제외한다. 모델 입력에 시각·UUID·난수는 없고 재시도 도구 ID는 고정 문자열이다.

```bash
uv run pytest tests/regression -q
UPDATE_GOLDEN=1 uv run pytest tests/regression -q
```

골든은 의도한 개발 직군 변경에서만 갱신한다. 비개발 기능 PR에서 바뀌면 diff의 이유를 설명한다.
일반 실행은 골든을 덮어쓰지 않으며, 차이는 `unified_diff`로 표시한다.
