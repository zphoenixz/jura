import pytest


@pytest.mark.asyncio
async def test_store_and_retrieve_analysis(client):
    analysis = {
        "meta": {"week": "2026-04-06", "generated_at": "2026-04-10T12:00:00Z"},
        "compliance_snapshot": {"features": 85.0, "bugs": 92.0},
        "declared_epics": [{"identifier": "TEAM-100", "title": "[EPIC] Auth"}],
        "unparented": [{"identifier": "TEAM-200", "title": "Fix login"}],
    }
    response = await client.post("/api/v1/epics-police/analysis", json=analysis)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "stored_at" in data

    response = await client.get("/api/v1/epics-police/analysis")
    assert response.status_code == 200
    stored = response.json()
    assert stored["meta"]["week"] == "2026-04-06"
    assert stored["compliance_snapshot"]["features"] == 85.0
    assert stored["declared_epics"][0]["identifier"] == "TEAM-100"
    assert stored["unparented"][0]["identifier"] == "TEAM-200"


@pytest.mark.asyncio
async def test_get_analysis_404_when_empty(client):
    response = await client.get("/api/v1/epics-police/analysis")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_store_overwrites_previous(client):
    await client.post("/api/v1/epics-police/analysis", json={"version": 1})
    await client.post("/api/v1/epics-police/analysis", json={"version": 2})

    response = await client.get("/api/v1/epics-police/analysis")
    assert response.status_code == 200
    assert response.json()["version"] == 2


@pytest.mark.asyncio
async def test_ui_returns_html(client):
    response = await client.get("/epics-police")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_analysis_accessible_via_config_api(client):
    """Analysis stored via epics-police is also visible in the config API."""
    await client.post("/api/v1/epics-police/analysis", json={"test": True})

    response = await client.get("/api/v1/config/epics_police/latest_analysis")
    assert response.status_code == 200
    assert response.json()["value"]["test"] is True
