"""신규 N:1은 최대 4명. 기존 5인 면접은 구성 변경 없이 재처리한다.

신규 생성 제한은 면접 기록을 소유한 API 서버에서 적용한다. 분석 서버의
요청에는 신규/기존 구분이 없으므로 HTTP 수용 상한은 기존 기록까지 포함한다.
"""

MAX_NEW_PERSONAS = 4
MAX_NEW_OTHER_PERSONAS = MAX_NEW_PERSONAS - 1
MAX_COMPATIBLE_PERSONAS = 5
MAX_COMPATIBLE_OTHER_PERSONAS = MAX_COMPATIBLE_PERSONAS - 1


def normalized_role(role: str) -> str:
    normalized = role.strip().upper()
    if not normalized:
        raise ValueError("role 은 공백일 수 없습니다.")
    return normalized
