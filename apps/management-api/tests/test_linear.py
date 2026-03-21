import pytest
import respx
import httpx

MOCK_TEAM = {"data": {"teams": {"nodes": [{"id": "team-1", "name": "Engineering"}]}}}
MOCK_CYCLE = {"data": {"team": {"activeCycle": {"id": "cycle-1", "number": 42, "name": "Cycle 42", "startsAt": "2026-03-30", "endsAt": "2026-04-11"}}}}
# Historical cycle lookup returns same cycle via cycles-by-date query
MOCK_CYCLES_BY_DATE = {"data": {"team": {"cycles": {"nodes": [{"id": "cycle-1", "number": 42, "name": "Cycle 42", "startsAt": "2026-03-30", "endsAt": "2026-04-11"}]}}}}
MOCK_ISSUES = {
    "data": {"cycle": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
        {
            "id": "issue-1", "identifier": "AGT-100", "title": "Fix bug",
            "description": "Bug description", "priority": 2, "estimate": 3,
            "url": "https://linear.app/issue/AGT-100",
            "createdAt": "2026-03-30T10:00:00Z", "updatedAt": "2026-03-31T12:00:00Z",
            "state": {"name": "In Progress", "type": "started"},
            "assignee": {"id": "user-1", "name": "Alice Smith", "email": "alice@example.com"},
            "labels": {"nodes": [{"name": "Backend"}]},
            "parent": None, "children": {"nodes": []},
            "comments": {"nodes": [
                {"id": "c-1", "body": "Working on it", "createdAt": "2026-03-31T10:00:00Z", "user": {"id": "user-1", "name": "Alice Smith", "email": "alice@example.com"}},
            ]},
            "attachments": {"nodes": []},
        },
    ]}}}
}


@pytest.mark.asyncio
async def test_fetch_linear(client):
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(side_effect=[
            httpx.Response(200, json=MOCK_TEAM),
            httpx.Response(200, json=MOCK_CYCLES_BY_DATE),
            httpx.Response(200, json=MOCK_ISSUES),
        ])
        await client.put("/api/v1/config/linear/team_name", json={"value": "Engineering"})
        response = await client.post("/api/v1/linear/fetch?week=2026-03-30")
        assert response.status_code == 200
        data = response.json()
        assert data["tickets"] == 1
        assert data["comments"] == 1
        assert data["cycle_number"] == 42


@pytest.mark.asyncio
async def test_get_linear_with_comments(client):
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(side_effect=[
            httpx.Response(200, json=MOCK_TEAM),
            httpx.Response(200, json=MOCK_CYCLES_BY_DATE),
            httpx.Response(200, json=MOCK_ISSUES),
        ])
        await client.put("/api/v1/config/linear/team_name", json={"value": "Engineering"})
        await client.post("/api/v1/linear/fetch?week=2026-03-30")

    response = await client.get("/api/v1/linear?week=2026-03-30")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    ticket = data["items"][0]
    assert ticket["identifier"] == "AGT-100"
    assert ticket["status_type"] == "in_progress"
    assert len(ticket["comments"]) == 1


@pytest.mark.asyncio
async def test_get_linear_filter_status_type(client):
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(side_effect=[
            httpx.Response(200, json=MOCK_TEAM),
            httpx.Response(200, json=MOCK_CYCLES_BY_DATE),
            httpx.Response(200, json=MOCK_ISSUES),
        ])
        await client.put("/api/v1/config/linear/team_name", json={"value": "Engineering"})
        await client.post("/api/v1/linear/fetch?week=2026-03-30")

    response = await client.get("/api/v1/linear?week=2026-03-30&status_type=done")
    assert response.json()["total"] == 0
    response = await client.get("/api/v1/linear?week=2026-03-30&status_type=in_progress")
    assert response.json()["total"] == 1
