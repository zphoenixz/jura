import httpx
import pytest
import respx

from app.services.linear_progress import (
    EpicProgress,
    _fetch_children_page,
    _state_bucket,
    _walk_epic,
    fetch_epic_progress,
)


def test_state_bucket_completed_is_done():
    assert _state_bucket("completed") == "done"


def test_state_bucket_started_is_in_progress():
    assert _state_bucket("started") == "in_progress"


def test_state_bucket_backlog_unstarted_triage_are_todo():
    assert _state_bucket("backlog") == "todo"
    assert _state_bucket("unstarted") == "todo"
    assert _state_bucket("triage") == "todo"


def test_state_bucket_canceled_is_excluded():
    assert _state_bucket("canceled") is None


def test_state_bucket_unknown_is_excluded():
    assert _state_bucket("something-new") is None


def test_epic_progress_starts_empty():
    p = EpicProgress()
    assert p.points == {"done": 0, "in_progress": 0, "todo": 0, "total": 0}
    assert p.descendant_count == 0
    assert p.missing_estimates == 0


def test_epic_progress_add_node_estimated_completed():
    p = EpicProgress()
    p.add_node(estimate=5, state_type="completed")
    assert p.points == {"done": 5, "in_progress": 0, "todo": 0, "total": 5}
    assert p.descendant_count == 1
    assert p.missing_estimates == 0


def test_epic_progress_add_node_canceled_excluded_from_totals():
    p = EpicProgress()
    p.add_node(estimate=8, state_type="canceled")
    assert p.points == {"done": 0, "in_progress": 0, "todo": 0, "total": 0}
    assert p.descendant_count == 1
    assert p.missing_estimates == 0


def test_epic_progress_add_node_missing_estimate():
    p = EpicProgress()
    p.add_node(estimate=None, state_type="started")
    assert p.points == {"done": 0, "in_progress": 0, "todo": 0, "total": 0}
    assert p.descendant_count == 1
    assert p.missing_estimates == 1


def test_epic_progress_add_node_unknown_state_excluded():
    p = EpicProgress()
    p.add_node(estimate=3, state_type="some-future-state")
    assert p.points == {"done": 0, "in_progress": 0, "todo": 0, "total": 0}
    assert p.descendant_count == 1
    assert p.missing_estimates == 0


def test_epic_progress_total_equals_sum_of_buckets():
    p = EpicProgress()
    p.add_node(estimate=3, state_type="completed")
    p.add_node(estimate=5, state_type="started")
    p.add_node(estimate=2, state_type="backlog")
    p.add_node(estimate=4, state_type="canceled")
    assert p.points == {"done": 3, "in_progress": 5, "todo": 2, "total": 10}
    assert p.descendant_count == 4


@pytest.mark.asyncio
async def test_fetch_children_page_returns_nodes_and_pageinfo(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    payload = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "issue-uuid-1",
                            "identifier": "SC-1",
                            "estimate": 3,
                            "state": {"type": "completed"},
                            "children": {"nodes": []},
                        }
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        nodes, page_info = await _fetch_children_page("SC-100", after=None)
    assert len(nodes) == 1
    assert nodes[0]["identifier"] == "SC-1"
    assert page_info == {"hasNextPage": False, "endCursor": None}


@pytest.mark.asyncio
async def test_fetch_children_page_raises_on_graphql_errors(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    payload = {"errors": [{"message": "issue not found"}]}
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        with pytest.raises(RuntimeError, match="issue not found"):
            await _fetch_children_page("SC-NOPE", after=None)


@pytest.mark.asyncio
async def test_walk_epic_single_page_flat_children(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    payload = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "u1", "identifier": "SC-1", "estimate": 5,
                            "state": {"type": "completed"},
                            "children": {"nodes": []},
                        },
                        {
                            "id": "u2", "identifier": "SC-2", "estimate": 3,
                            "state": {"type": "started"},
                            "children": {"nodes": []},
                        },
                        {
                            "id": "u3", "identifier": "SC-3", "estimate": 2,
                            "state": {"type": "backlog"},
                            "children": {"nodes": []},
                        },
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        progress = await _walk_epic("SC-100")
    assert progress.points == {"done": 5, "in_progress": 3, "todo": 2, "total": 10}
    assert progress.descendant_count == 3
    assert progress.missing_estimates == 0


@pytest.mark.asyncio
async def test_walk_epic_paginates_children(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    page1 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    "nodes": [
                        {"id": "u1", "identifier": "SC-1", "estimate": 4,
                         "state": {"type": "completed"}, "children": {"nodes": []}},
                    ],
                }
            }
        }
    }
    page2 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u2", "identifier": "SC-2", "estimate": 6,
                         "state": {"type": "started"}, "children": {"nodes": []}},
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
        )
        progress = await _walk_epic("SC-100")
    assert progress.descendant_count == 2
    assert progress.points == {"done": 4, "in_progress": 6, "todo": 0, "total": 10}


@pytest.mark.asyncio
async def test_walk_epic_descends_into_grandchildren(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    # Top-level epic SC-100 has one child u-parent which itself has two children.
    epic_page = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "u-parent", "identifier": "SC-1", "estimate": 0,
                            "state": {"type": "started"},
                            "children": {"nodes": [{"id": "ignored-probe"}]},
                        }
                    ],
                }
            }
        }
    }
    parent_page = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u-c1", "identifier": "SC-2", "estimate": 5,
                         "state": {"type": "completed"}, "children": {"nodes": []}},
                        {"id": "u-c2", "identifier": "SC-3", "estimate": 3,
                         "state": {"type": "backlog"}, "children": {"nodes": []}},
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            side_effect=[httpx.Response(200, json=epic_page), httpx.Response(200, json=parent_page)]
        )
        progress = await _walk_epic("SC-100")
    # Parent itself (SC-1, started, estimate=0) + 2 leaves
    assert progress.descendant_count == 3
    assert progress.points == {"done": 5, "in_progress": 0, "todo": 3, "total": 8}


@pytest.mark.asyncio
async def test_walk_epic_visit_cap_raises(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    # Epic returns 3 leaves; cap visits at 2.
    payload = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": f"u{i}", "identifier": f"SC-{i}", "estimate": 1,
                         "state": {"type": "completed"}, "children": {"nodes": []}}
                        for i in range(3)
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        with pytest.raises(RuntimeError, match="Visit cap"):
            await _walk_epic("SC-100", visit_cap=2)


@pytest.mark.asyncio
async def test_walk_epic_depth_cap_stops_recursion(monkeypatch, caplog):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    # Epic → child (with grandchildren probe) but depth_cap=1 prevents fetching grandchildren.
    epic_page = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u-parent", "identifier": "SC-1", "estimate": 2,
                         "state": {"type": "started"},
                         "children": {"nodes": [{"id": "probe"}]}}
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=epic_page)
        )
        with caplog.at_level("WARNING"):
            progress = await _walk_epic("SC-100", depth_cap=1)
    assert progress.descendant_count == 1
    assert progress.points["in_progress"] == 2
    assert any("Depth cap" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_walk_epic_visited_set_prevents_double_count(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    # Two pages return the SAME node id. The second occurrence must be ignored.
    page1 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "nodes": [
                        {"id": "u1", "identifier": "SC-1", "estimate": 4,
                         "state": {"type": "completed"}, "children": {"nodes": []}},
                    ],
                }
            }
        }
    }
    page2 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u1", "identifier": "SC-1", "estimate": 4,
                         "state": {"type": "completed"}, "children": {"nodes": []}},
                    ],
                }
            }
        }
    }
    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
        )
        progress = await _walk_epic("SC-100")
    assert progress.descendant_count == 1
    assert progress.points["done"] == 4


@pytest.mark.asyncio
async def test_fetch_epic_progress_happy_batch(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    sc100 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u1", "identifier": "SC-1", "estimate": 5,
                         "state": {"type": "completed"}, "children": {"nodes": []}}
                    ],
                }
            }
        }
    }
    sc200 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u2", "identifier": "SC-2", "estimate": 3,
                         "state": {"type": "started"}, "children": {"nodes": []}}
                    ],
                }
            }
        }
    }
    with respx.mock:
        def handler(request):
            import json as _json
            body = _json.loads(request.content)
            ident = body["variables"]["id"]
            return httpx.Response(200, json=sc100 if ident == "SC-100" else sc200)
        respx.post("https://api.linear.app/graphql").mock(side_effect=handler)
        results, warnings = await fetch_epic_progress(["SC-100", "SC-200"])
    assert warnings == []
    assert results["SC-100"].points["done"] == 5
    assert results["SC-200"].points["in_progress"] == 3


@pytest.mark.asyncio
async def test_fetch_epic_progress_per_epic_failure_isolated(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    sc100 = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "u1", "identifier": "SC-1", "estimate": 5,
                         "state": {"type": "completed"}, "children": {"nodes": []}}
                    ],
                }
            }
        }
    }
    not_found = {"data": {"issue": None}}

    def handler(request):
        import json as _json
        body = _json.loads(request.content)
        ident = body["variables"]["id"]
        return httpx.Response(200, json=sc100 if ident == "SC-100" else not_found)

    with respx.mock:
        respx.post("https://api.linear.app/graphql").mock(side_effect=handler)
        results, warnings = await fetch_epic_progress(["SC-100", "SC-NOPE"])
    assert results["SC-100"] is not None
    assert results["SC-NOPE"] is None
    assert any("SC-NOPE" in w for w in warnings)


@pytest.mark.asyncio
async def test_fetch_epic_progress_dedupes_input(monkeypatch):
    monkeypatch.setattr("app.services.linear_progress.settings.linear_api_key", "test-key")
    payload = {
        "data": {
            "issue": {
                "children": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }
    }
    with respx.mock:
        route = respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json=payload)
        )
        results, warnings = await fetch_epic_progress(["SC-100", "SC-100", "SC-100"])
    assert list(results.keys()) == ["SC-100"]
    assert route.call_count == 1
