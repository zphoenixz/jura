import pytest


@pytest.mark.asyncio
async def test_config_crud(client):
    response = await client.put("/api/v1/config/slack/test_key", json={"value": ["a", "b"]})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "slack"
    assert data["key"] == "test_key"
    assert data["value"] == ["a", "b"]

    response = await client.get("/api/v1/config/slack/test_key")
    assert response.status_code == 200
    assert response.json()["value"] == ["a", "b"]

    response = await client.put("/api/v1/config/slack/test_key", json={"value": ["c"]})
    assert response.status_code == 200
    assert response.json()["value"] == ["c"]

    response = await client.delete("/api/v1/config/slack/test_key")
    assert response.status_code == 200

    response = await client.get("/api/v1/config/slack/test_key")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_config_invalid_source(client):
    response = await client.put("/api/v1/config/invalid_source/key", json={"value": "x"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_config_by_source(client):
    await client.put("/api/v1/config/slack/key1", json={"value": 1})
    await client.put("/api/v1/config/slack/key2", json={"value": 2})
    await client.put("/api/v1/config/linear/key1", json={"value": 3})

    response = await client.get("/api/v1/config/slack")
    assert response.status_code == 200
    keys = [c["key"] for c in response.json()]
    assert "key1" in keys
    assert "key2" in keys
