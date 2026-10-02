import pytest

from app.core.common.applicant_profile.prompt import SYSTEM_PROMPT_PROFILE
from app.core.common.feedback.multi.prompt import SYSTEM_PROMPT as MULTI_FEEDBACK_PROMPT
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT as SOLO_FEEDBACK_PROMPT
from app.core.common.interview_qa.prompts import SYSTEM_PROMPT_STAGE4
from app.core.common.question_cycle.prompt import SYSTEM_PROMPT as QUESTION_CYCLE_PROMPT
from app.core.common.question_tailor.multi.prompt import SYSTEM_PROMPT as MULTI_TAILOR_PROMPT
from app.core.common.question_tailor.prompt import SYSTEM_PROMPT as TAILOR_PROMPT

# 소스 코드의 들여쓰기가 그대로 모델에 전달되면 토큰만 늘고 항목 계층이 흐려진다.
_PROMPTS = [
    SOLO_FEEDBACK_PROMPT,
    MULTI_FEEDBACK_PROMPT,
    SYSTEM_PROMPT_STAGE4,
    TAILOR_PROMPT,
    MULTI_TAILOR_PROMPT,
    SYSTEM_PROMPT_PROFILE,
    QUESTION_CYCLE_PROMPT,
]


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_has_no_source_indentation(prompt: str) -> None:
    assert prompt == prompt.strip()
    assert "\n    " not in prompt
    assert all(line == line.rstrip() for line in prompt.split("\n"))
