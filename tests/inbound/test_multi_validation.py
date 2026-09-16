from typing import Any

import pytest

from tests.multi_helpers import feedback_body, multi_http, tailor_body, tailor_outputs


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", " tech "),
        ("role", " HR "),
        ("role", "   "),
        ("personaId", "p-0"),
    ],
)
async def test_tailor_rejects_invalid_other_persona(field: str, value: str) -> None:
    body = tailor_body()
    body["otherPersonas"][1][field] = value
    async with multi_http({}) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 422
    assert webhook.payloads == []


@pytest.mark.parametrize("role", ["HR", "", "   "])
async def test_tailor_requires_tech_slot(role: str) -> None:
    body = tailor_body()
    body["techPersona"]["role"] = role
    async with multi_http({}) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 422
    assert webhook.payloads == []


@pytest.mark.parametrize("role", ["PM", "DESIGN", "NEW_ROLE"])
async def test_tailor_keeps_extensible_roles(role: str) -> None:
    body = tailor_body(2)
    body["otherPersonas"][0]["role"] = role
    async with multi_http(tailor_outputs((2,))) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"


@pytest.mark.parametrize("count", [1, 6])
async def test_tailor_rejects_outside_compatibility_bounds(count: int) -> None:
    async with multi_http({}) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=tailor_body(count))
    assert response.status_code == 422
    assert webhook.payloads == []


@pytest.mark.parametrize(("field", "value"), [("role", " tech "), ("role", " "), ("personaId", "p-0")])
async def test_feedback_rejects_duplicate_or_blank_personas(field: str, value: str) -> None:
    body = feedback_body()
    body["personas"][1][field] = value
    async with multi_http({}) as (client, webhook):
        response = await client.post("/feedback/multi", json=body)
    assert response.status_code == 422
    assert webhook.payloads == []


@pytest.mark.parametrize("count", [0, 6])
async def test_feedback_rejects_outside_compatibility_bounds(count: int) -> None:
    async with multi_http({}) as (client, webhook):
        response = await client.post("/feedback/multi", json=feedback_body(count))
    assert response.status_code == 422
    assert webhook.payloads == []


async def test_feedback_rejects_unknown_question_persona() -> None:
    body = feedback_body()
    body["questions"][0]["personaId"] = "unknown"
    async with multi_http({}) as (client, webhook):
        response = await client.post("/feedback/multi", json=body)
    assert response.status_code == 422
    assert webhook.payloads == []


@pytest.mark.parametrize("count", [0, 3, 6])
async def test_tailor_rejects_tech_question_count_mismatch_or_bounds(count: int) -> None:
    body: dict[str, Any] = tailor_body()
    body["techPersona"]["questionCount"] = count
    async with multi_http({}) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 422
    assert webhook.payloads == []
