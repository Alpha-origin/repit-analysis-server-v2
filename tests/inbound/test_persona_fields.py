from app.inbound.http.interview_feedback.multi.dto import FeedbackPersonaRequest
from app.inbound.http.interview_feedback.solo.dto import FeedbackRequest
from app.inbound.http.question_tailor.dto import CandidateProfileRequest
from app.inbound.http.question_tailor.multi.dto import TailorPersonaRequest


def test_persona_tone_fields_use_expected_wire_names() -> None:
    assert CandidateProfileRequest.model_fields["persona_tone"].alias == "personaTone"
    assert FeedbackRequest.model_fields["persona_tone"].alias == "personaTone"
    assert TailorPersonaRequest.model_fields["tone"].alias == "tone"
    assert FeedbackPersonaRequest.model_fields["tone"].alias == "tone"
