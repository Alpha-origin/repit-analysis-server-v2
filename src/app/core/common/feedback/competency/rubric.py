from __future__ import annotations

from app.core.common.feedback.competency.catalog import LABELS
from app.core.common.feedback.competency.evidence import SLOTS
from app.core.common.feedback.rubric import AXIS_LEVEL_CRITERIA, general_axis_level_criteria


def competency_criteria(*, general: bool = False) -> str:
    legacy = general_axis_level_criteria() if general else AXIS_LEVEL_CRITERIA
    # cleandoc가 앞 공백을 제거하므로 구획 이름으로 자른다.
    response = legacy.split("intent —", 1)[1].split("depth —", 1)[0]
    accuracy = legacy.split("accuracy —", 1)[1].split("consistency —", 1)[0]
    consistency = legacy.split("consistency —", 1)[1]
    response = response.replace("intent", "response").replace(
        "depth와 specificity는 0, accuracy는 null이다.", "주 역량은 0이고 보조 역량은 집계하지 않는다."
    )
    accuracy = accuracy.replace("intent", "response")
    consistency = consistency.replace(
        "근거 부족은 specificity에서 판단한다.", "근거 부족은 해당 역량과 개선 조언으로만 다룬다."
    )
    slots = "\n".join(
        f"{comp.value.lower()} ({LABELS[comp]}): " + "; ".join(f"{slot}={desc}" for slot, desc in definitions.items())
        for comp, definitions in SLOTS.items()
    )
    return f"""[역량별 근거 기준]
판정 순서:
1. 질문과 의도의 명시적인 요구만 확인한다. 요구하지 않은 이유·대안·결과를 필수 요구로 추가하지 않는다.
2. 대응하는 실제 답변 문구를 확인한다. FOLLOW는 제공된 부모 답변도 현재 질문의 근거일 때만 함께 본다.
3. response는 요구 충족 여부로, 역량은 지정된 근거 슬롯으로 따로 판단한다.
4. 아래 기준을 높은 등급부터 대조한다. 인상이나 답변 길이로 보정하지 않는다.
5. strengths와 improvements를 실제 문구와 연결한다. 필수 누락과 추가 발전 조언을 구분한다.

모델은 역량 등급이나 점수를 제출하지 않는다. response와 knowledge.accuracy만 0~4로 판단한다.
주 역량 그룹은 반드시 객체이며 근거가 없으면 문자열 슬롯을 전부 비운다.
보조 역량은 근거가 있을 때만 객체이고 없으면 null이다. 겨냥하지 않은 역량은 null이다.
미분류는 질문과 의도를 보고 inferred_primary에 주 역량 하나를 고른다. 고를 수 없으면 null이다.
분류된 문항의 inferred_primary는 null이다. 비TECH 면접관 질문에는 KNOWLEDGE를 추론하지 않는다.
모든 슬롯은 가장 짧은 연속 원문 인용이다. 없으면 빈 문자열이다.
요약·말바꾸기·생략 기호를 넣지 않는다. 질문·의도·다른 문항·예시 답안을 인용하지 않는다.
{slots}

서버 계산 기준:
knowledge: mechanism+condition=4 mechanism=3 components=2 term_only=1 없음=0 accuracy로 상한 적용
problem_solving: reason+alternatives+limitation=4 reason와 둘 중 하나=3 reason=2 generic_reason=1 없음=0
대안 이름 나열은 alternatives가 아니다. 원인과 그 원인을 제거한 수정을 연결하면 reason이다.
ownership: action+method+result=4 action+method=3 action만 또는 team_only=2 stance=1 없음=0
주어 생략은 본인 행동으로 인정한다. 명시적 팀 행동만 있고 본인 행동이 없을 때만 team_only다.
측정 절차만으로 result를 채우지 않는다. 결과 수치를 지어내지 않는다.
collaboration: counterpart+issue+coordination+outcome=4 앞의 셋=3 조율 행동만 또는 상대와 이견만=2 generic=1 없음=0
growth: admission+lesson+change=4 lesson과 둘 중 하나=3 구체적인 lesson 또는 admission 또는 change=2 generic=1 없음=0
motivation: company_fact+personal_link=4 둘 중 하나=2 generic=1 없음=0
company_fact는 제시한 사실의 구체성을 보며 회사 정보와 사실 대조하지 않는다. companyFit과 분리한다.

response — {response}
솔직하게 모른다고 말하며 아는 범위나 추론을 설명하면 response 1이며 회피 0이 아니다.
knowledge.accuracy — {accuracy}
consistency — {consistency}
consistency_issues에는 실제로 양립 불가능한 두 문항의 진술만 question_ids와 각 원문 quotes로 제출한다.
꼬리질문에서 설명이나 수치 측정 방법을 말하지 못한 것 자체는 모순이 아니다.
문장력·길이·구어체·말버릇·영상·음성은 점수에 반영하지 않는다.
다른 문항과의 모순은 consistency_issues에서만 다루며 역량 점수를 임의로 낮추지 않는다.
"""
