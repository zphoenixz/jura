import pytest
import respx
import httpx

MOCK_USERS = {"ok": True, "members": [
    {"id": "U1", "deleted": False, "is_bot": False, "profile": {"real_name": "Alice Smith", "email": "alice@example.com"}},
    {"id": "U2", "deleted": False, "is_bot": False, "profile": {"real_name": "Bob Jones", "email": "bob@example.com"}},
], "response_metadata": {"next_cursor": ""}}

MOCK_HISTORY = {"ok": True, "messages": [
    {"ts": "1711900800.000001", "user": "U1", "text": "Hello from Alice", "reply_count": 0},
    {"ts": "1711900800.000002", "user": "U2", "text": "Reply", "reply_count": 1},
], "response_metadata": {"next_cursor": ""}}

MOCK_REPLIES = {"ok": True, "messages": [
    {"ts": "1711900800.000002", "user": "U2", "text": "Reply"},
    {"ts": "1711900800.000003", "user": "U1", "text": "Thread reply"},
]}


@pytest.mark.asyncio
async def test_fetch_slack(client):
    with respx.mock:
        respx.post("https://slack.com/api/users.list").mock(return_value=httpx.Response(200, json=MOCK_USERS))
        respx.post("https://slack.com/api/conversations.history").mock(return_value=httpx.Response(200, json=MOCK_HISTORY))
        respx.get("https://slack.com/api/conversations.replies").mock(return_value=httpx.Response(200, json=MOCK_REPLIES))

        # Use fixed channel map (no patterns, no conversations.list)
        await client.put("/api/v1/config/slack/watched_channels", json={"value": {"dev-general": "C1"}})
        await client.put("/api/v1/config/slack/watched_dm_people", json={"value": []})
        await client.put("/api/v1/config/slack/timezone", json={"value": "UTC"})

        response = await client.post("/api/v1/slack/fetch?week=2026-03-30")
        assert response.status_code == 200
        data = response.json()
        assert data["messages"] >= 2


@pytest.mark.asyncio
async def test_get_slack_filter_dm(client):
    with respx.mock:
        respx.post("https://slack.com/api/users.list").mock(return_value=httpx.Response(200, json=MOCK_USERS))
        respx.post("https://slack.com/api/conversations.history").mock(return_value=httpx.Response(200, json=MOCK_HISTORY))
        respx.get("https://slack.com/api/conversations.replies").mock(return_value=httpx.Response(200, json=MOCK_REPLIES))

        await client.put("/api/v1/config/slack/watched_channels", json={"value": {"dev-general": "C1"}})
        await client.put("/api/v1/config/slack/watched_dm_people", json={"value": []})
        await client.put("/api/v1/config/slack/timezone", json={"value": "UTC"})
        await client.post("/api/v1/slack/fetch?week=2026-03-30")

    response = await client.get("/api/v1/slack?week=2026-03-30&is_dm=true")
    assert response.json()["total"] == 0
    response = await client.get("/api/v1/slack?week=2026-03-30&is_dm=false")
    assert response.json()["total"] >= 2
