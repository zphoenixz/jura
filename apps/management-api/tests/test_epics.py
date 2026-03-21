import pytest


SAMPLE_PAYLOAD = {
    "week": "2026-03-30",
    "epics": [
        {
            "notion_page_id": "page-1",
            "title": "Agent Always-On",
            "status": "In development",
            "team": ["Agents Core"],
            "pm_lead": "Sam",
            "sort_order": 1,
            "content": "# Epic content here",
            "sub_pages": [
                {"notion_page_id": "sub-1", "title": "Sub Page 1", "content": "Sub content"},
            ],
        },
        {
            "notion_page_id": "page-2",
            "title": "Checkout v2",
            "status": "Prioritised",
            "team": ["Checkout"],
            "content": "",
        },
    ],
}


@pytest.mark.asyncio
async def test_push_epics(client):
    response = await client.post("/api/v1/epics/fetch", json=SAMPLE_PAYLOAD)
    assert response.status_code == 200
    data = response.json()
    assert data["epics"] == 2
    assert data["sub_pages"] == 1
    assert data["monday"] == "2026-03-30"


@pytest.mark.asyncio
async def test_get_epics(client):
    await client.post("/api/v1/epics/fetch", json=SAMPLE_PAYLOAD)
    response = await client.get("/api/v1/epics?week=2026-03-30")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    epic_with_subs = [e for e in data["items"] if e["title"] == "Agent Always-On"][0]
    assert len(epic_with_subs["sub_pages"]) == 1


@pytest.mark.asyncio
async def test_get_epics_filter_status(client):
    await client.post("/api/v1/epics/fetch", json=SAMPLE_PAYLOAD)
    response = await client.get("/api/v1/epics?week=2026-03-30&status=Prioritised")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Checkout v2"


@pytest.mark.asyncio
async def test_push_epics_idempotent(client):
    await client.post("/api/v1/epics/fetch", json=SAMPLE_PAYLOAD)
    await client.post("/api/v1/epics/fetch", json=SAMPLE_PAYLOAD)
    response = await client.get("/api/v1/epics?week=2026-03-30")
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_push_epics_missing_required(client):
    response = await client.post("/api/v1/epics/fetch", json={"epics": [{"title": "No page ID"}]})
    assert response.status_code == 422
