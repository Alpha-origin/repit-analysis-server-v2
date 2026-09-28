from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.core.common.dto import CamelModel

# CamelModel 은 feedback 전용이 아니라서 core/common/dto.py 로 옮겼다.
# 기존 import 경로(app.core.common.feedback.dto.CamelModel) 를 깨지 않으려고 여기서 재export 한다.
__all__ = [
    "AxisScore",
    "CamelModel",
    "FeedbackCallbackFailure",
    "FeedbackErrorDetail",
    "ScoreAxis",
    "ScoreBreakdownPayload",
]


# ===== 콜백 페이로드 (1:1 / N:1 공용) =====


class FeedbackErrorDetail(CamelModel):
    status_code: int  # 422(채점 대상 없음), 502(LLM 호출 실패), 500(내부 오류) 등
    message: str  # 사용자에게 노출 가능한 한글 메시지


class FeedbackCallbackFailure(CamelModel):
    job_id: str
    # 실패 페이로드에는 result 가 없어서, session_id 가 없으면 수신측이 어느 세션인지 알 수 없다.
    session_id: str
    status: Literal["failed"] = "failed"
    error: FeedbackErrorDetail


# ===== 종합 점수 산출 근거 =====
# 사용자에게 "각 축이 몇 점이라 종합 몇 점"을 보여주기 위한 필드. 현재는 1:1 콜백에만 싣는다.
# 계산 규칙은 core/common/feedback/scoring.py 에 있다.

# 표시 이름은 클라이언트가 정한다. 순서는 이 목록 순서로 고정한다.
ScoreAxis = Literal["INTENT", "DEPTH", "SPECIFICITY", "ACCURACY"]


class AxisScore(CamelModel):
    axis: ScoreAxis
    # 세션 전체에서 해당 없는 축(주로 ACCURACY)은 둘 다 null 이다.
    score: int | None = Field(..., ge=0, le=100)
    # 실제 적용된 가중치(%). 해당 없는 축이 빠지면 나머지 가중치 합이 100 이 아니다.
    weight: int | None = Field(..., ge=0, le=100)


class ScoreBreakdownPayload(CamelModel):
    scoring_version: str
    # 표시된 score·weight 로 Σ(score x weight) / Σ(weight) 를 반올림하면 totalScore 와 항상 같다.
    axes: list[AxisScore] = Field(..., min_length=4, max_length=4)
    # 종합 점수에는 들어가지 않는 별도 지표. 답변이 1개라 판단할 수 없으면 null.
    consistency_score: int | None = Field(..., ge=0, le=100)
