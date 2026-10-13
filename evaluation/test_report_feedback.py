"""평가 보고서의 집계와 입력 연결을 외부 API 없이 검증한다."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.cases import DEFAULT_CASES, load_cases
from evaluation.report_feedback import grade_agreement, load_runs, p95, quality_comparison, report


def run_row(repeat: int, intent: int, *, status: str = "succeeded") -> dict:
    row = {
        "case_id": "case-1", "variant": "current", "repeat": repeat,
        "status": status, "duration_seconds": float(repeat),
    }
    if status == "succeeded":
        row.update({
            "total_score": 75, "question_levels": {
                "q1": {"intent": intent, "depth": 3, "specificity": 3, "accuracy": None}
            },
        })
    return row


class ReportFeedbackTests(unittest.TestCase):
    def test_frozen_cases_have_development_and_holdout_splits(self) -> None:
        self.assertEqual(len(load_cases(DEFAULT_CASES, "development")), 2)
        self.assertEqual(len(load_cases(DEFAULT_CASES, "holdout")), 8)

    def test_repeated_axis_agreement_and_p95(self) -> None:
        rows = [run_row(1, 4), run_row(2, 4), run_row(3, 2)]
        self.assertAlmostEqual(grade_agreement(rows), (2 / 3 + 1 + 1) / 3)
        self.assertEqual(p95([1, 2, 3, 4, 5]), 5)
        self.assertIsNone(grade_agreement([run_row(1, 4)]))

    def test_missing_human_and_quality_data_are_not_claimed(self) -> None:
        output = report([run_row(1, 4)], [], [])
        self.assertIn("전문 면접관과의 일치도를 주장할 수 없음", output)
        self.assertIn("프롬프트 품질 향상을 주장할 수 없음", output)
        self.assertIn("1/1", output)

    def test_quality_rating_must_reference_successful_unique_run(self) -> None:
        rating = {
            "case_id": "case-1", "variant": "current", "repeat": "1", "reviewer_id": "r1",
            "question_fit": "4", "groundedness": "3", "technical_correctness": "4",
            "actionability": "2",
        }
        self.assertIn("question_fit", "\n".join(quality_comparison([rating], [run_row(1, 4)])))
        with self.assertRaises(ValueError):
            quality_comparison([rating, rating], [run_row(1, 4)])
        with self.assertRaises(ValueError):
            quality_comparison([rating], [run_row(1, 4, status="failed")])

    def test_duplicate_run_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            record = json.dumps(run_row(1, 4), ensure_ascii=False)
            path.write_text(f"{record}\n{record}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_runs(path)


if __name__ == "__main__":
    unittest.main()
