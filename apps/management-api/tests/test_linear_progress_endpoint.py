import httpx
import pytest
import respx


def _children_response(estimate, state_type):
    return {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "u1", "identifier": "SC-1", "estimate": estimate,
                            "state": {"type": state_type},
                            "children": {"nodes": []},
                        }
                    ],
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_epic_progress_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.linear_progress.settings.linear_api_key", "test-key"
    )
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=_children_response(5, "completed"))
        )
        resp = await client.post(
            "/api/v1/linear/epics/progress", json={"identifiers": ["SC-100"]}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["warnings"] == []
    assert body["results"]["SC-100"]["points"] == {
        "done": 5, "in_progress": 0, "todo": 0, "total": 5
    }
    assert body["results"]["SC-100"]["descendant_count"] == 1


@pytest.mark.asyncio
async def test_epic_progress_mixed_batch_with_missing(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.linear_progress.settings.linear_api_key", "test-key"
    )

    def handler(request):
        import json as _json
        body = _json.loads(request.content)
        ident = body["variables"]["id"]
        if ident == "SC-100":
            return httpx.Response(200, json=_children_response(3, "started"))
        return httpx.Response(200, json={"data": {"issue": None}})

    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(side_effect=handler)
        resp = await client.post(
            "/api/v1/linear/epics/progress",
            json={"identifiers": ["SC-100", "SC-NOPE"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"]["SC-100"] is not None
    assert body["results"]["SC-NOPE"] is None
    assert any("SC-NOPE" in w for w in body["warnings"])


@pytest.mark.asyncio
async def test_epic_progress_empty_list_returns_422(client):
    resp = await client.post(
        "/api/v1/linear/epics/progress", json={"identifiers": []}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "empty_identifiers"


@pytest.mark.asyncio
async def test_epic_progress_oversize_list_returns_422(client):
    resp = await client.post(
        "/api/v1/linear/epics/progress",
        json={"identifiers": [f"SC-{i}" for i in range(101)]},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "too_many_identifiers"


@pytest.mark.asyncio
async def test_epic_progress_all_fail_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.linear_progress.settings.linear_api_key", "test-key"
    )
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json={"data": {"issue": None}})
        )
        resp = await client.post(
            "/api/v1/linear/epics/progress",
            json={"identifiers": ["SC-NOPE-1", "SC-NOPE-2"]},
        )
    assert resp.status_code == 502
    body = resp.json()["detail"]
    assert body["code"] == "linear_unavailable"
    assert len(body["warnings"]) == 2
