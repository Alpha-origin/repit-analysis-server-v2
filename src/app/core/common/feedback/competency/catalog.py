from enum import StrEnum

SCORING_VERSION = "competency-v1"
MAX_LEVEL = 4
LEVEL_POINTS = 25


class Competency(StrEnum):
    PROBLEM_SOLVING = "PROBLEM_SOLVING"
    KNOWLEDGE = "KNOWLEDGE"
    OWNERSHIP = "OWNERSHIP"
    COLLABORATION = "COLLABORATION"
    GROWTH = "GROWTH"
    MOTIVATION = "MOTIVATION"


WEIGHTS = dict(zip(tuple(Competency)[:-1], (30, 25, 25, 10, 10), strict=True))
LABELS = dict(
    zip(
        Competency,
        ("문제 해결·판단", "직무 지식", "실행·기여", "협업·소통", "성장·성찰", "지원 동기·회사 이해"),
        strict=True,
    )
)
VERDICT_BOUNDARIES = ((3.5, "STRONG"), (2.5, "SOLID"), (1.5, "BORDERLINE"))


def verdict(mean_level: float) -> str:
    return next((name for boundary, name in VERDICT_BOUNDARIES if mean_level >= boundary), "WEAK")
