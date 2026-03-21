import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"


@pytest.mark.asyncio
async def test_weeks_empty(client):
    response = await client.get("/api/v1/weeks")
    assert response.status_code == 200
    assert response.json() == []
