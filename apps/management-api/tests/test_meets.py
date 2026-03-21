import pytest
import respx
import httpx


MOCK_TRANSCRIPTS = {
    "data": {
        "transcripts": [
            {
                "id": "ff-1", "title": "Sprint Planning", "dateString": "2026-03-30T10:00:00",
                "duration": 45,
                "meeting_attendees": [
                    {"name": "Alice Smith", "email": "alice@example.com"},
                    {"name": "Bob Jones", "email": "bob@example.com"},
                ],
                "meeting_info": {"silent_meeting": False, "summary_status": "completed"},
                "summary": {"keywords": "sprint,planning", "overview": "Planned goals", "short_summary": "Sprint planning", "notes": "Notes", "action_items": "- Review PRs"},
            },
            {
                "id": "ff-2", "title": "1:1 Alice / Charlie", "dateString": "2026-03-31T14:00:00",
                "duration": 30,
                "meeting_attendees": [
                    {"name": "Alice Smith", "email": "alice@example.com"},
                    {"name": "Charlie Lee", "email": "charlie@example.com"},
                ],
                "meeting_info": {"silent_meeting": False, "summary_status": "completed"},
                "summary": {"keywords": "", "overview": "1:1", "short_summary": "1:1", "notes": "", "action_items": ""},
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_fetch_meets(client):
    with respx.mock:
        respx.post("https://api.fireflies.ai/graphql").mock(return_value=httpx.Response(200, json=MOCK_TRANSCRIPTS))
        await client.put("/api/v1/config/meets/excluded_names", json={"value": ["charlie"]})
        await client.put("/api/v1/config/meets/participant_email", json={"value": "alice@example.com"})
        response = await client.post("/api/v1/meets/fetch?week=2026-03-30")
        assert response.status_code == 200
        data = response.json()
        assert data["meetings"] == 1
        assert data["excluded"] == 1


@pytest.mark.asyncio
async def test_get_meets_with_attendees(client):
    with respx.mock:
        respx.post("https://api.fireflies.ai/graphql").mock(return_value=httpx.Response(200, json=MOCK_TRANSCRIPTS))
        await client.put("/api/v1/config/meets/excluded_names", json={"value": ["charlie"]})
        await client.put("/api/v1/config/meets/participant_email", json={"value": "alice@example.com"})
        await client.post("/api/v1/meets/fetch?week=2026-03-30")

    response = await client.get("/api/v1/meets?week=2026-03-30")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Sprint Planning"
    assert len(data["items"][0]["attendees"]) == 2
