import pytest

# ── Analysis endpoints ───────────────────────────────────────────


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


# ── Decision endpoints ───────────────────────────────────────────


def _make_decision(**overrides):
    """Build a decision payload with sensible defaults."""
    base = {
        "week_monday": "2026-04-06",
        "decided_at": "2026-04-10T14:30:00Z",
        "orphan_identifier": "TEAM-200",
        "orphan_labels": ["feature", "thunder"],
        "orphan_squad": "Payments",
        "suggested_parent_id": "TEAM-100",
        "suggested_confidence": 72,
        "suggested_signals": {
            "label_overlap": 28,
            "title_overlap": 15,
            "description_overlap": 10,
            "squad_match": 10,
            "notion_match": 0,
        },
        "match_source": "pass1",
        "decision": "accepted",
        "actual_parent_id": "TEAM-100",
        "inferred": False,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_post_and_get_decisions(client):
    """Store a batch of decisions and retrieve them."""
    decisions = [
        _make_decision(),
        _make_decision(
            orphan_identifier="TEAM-201",
            decision="rejected",
            actual_parent_id=None,
        ),
    ]
    response = await client.post(
        "/api/v1/epics-police/decisions", json={"decisions": decisions}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stored"] == 2

    response = await client.get("/api/v1/epics-police/decisions")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_get_decisions_empty(client):
    """No decisions returns empty list, not 404."""
    response = await client.get("/api/v1/epics-police/decisions")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_decisions_filter_by_week(client):
    """Filter decisions by week_monday."""
    await client.post(
        "/api/v1/epics-police/decisions",
        json={
            "decisions": [
                _make_decision(week_monday="2026-04-06"),
                _make_decision(week_monday="2026-03-30", orphan_identifier="TEAM-300"),
            ]
        },
    )

    response = await client.get("/api/v1/epics-police/decisions?week=2026-04-06")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["orphan_identifier"] == "TEAM-200"


@pytest.mark.asyncio
async def test_get_decisions_filter_by_type(client):
    """Filter decisions by decision type."""
    await client.post(
        "/api/v1/epics-police/decisions",
        json={
            "decisions": [
                _make_decision(decision="accepted"),
                _make_decision(orphan_identifier="TEAM-201", decision="rejected"),
                _make_decision(
                    orphan_identifier="TEAM-202",
                    decision="redirected",
                    actual_parent_id="TEAM-999",
                ),
            ]
        },
    )

    response = await client.get("/api/v1/epics-police/decisions?decision=rejected")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["decision"] == "rejected"


@pytest.mark.asyncio
async def test_decision_preserves_signals(client):
    """Signal breakdown survives the roundtrip."""
    signals = {
        "label_overlap": 35.0,
        "title_overlap": 0.0,
        "description_overlap": 20.0,
        "squad_match": 10.0,
        "notion_match": 5.0,
    }
    await client.post(
        "/api/v1/epics-police/decisions",
        json={"decisions": [_make_decision(suggested_signals=signals)]},
    )

    response = await client.get("/api/v1/epics-police/decisions")
    stored = response.json()["items"][0]
    assert stored["suggested_signals"]["label_overlap"] == 35.0
    assert stored["suggested_signals"]["squad_match"] == 10.0
    assert stored["suggested_confidence"] == 72
    assert stored["match_source"] == "pass1"


@pytest.mark.asyncio
async def test_decision_manual_no_suggestion(client):
    """Manual reparent with no suggestion context."""
    await client.post(
        "/api/v1/epics-police/decisions",
        json={
            "decisions": [
                _make_decision(
                    decision="manual",
                    suggested_parent_id=None,
                    suggested_confidence=None,
                    suggested_signals=None,
                    match_source=None,
                    actual_parent_id="TEAM-500",
                )
            ]
        },
    )

    response = await client.get("/api/v1/epics-police/decisions")
    stored = response.json()["items"][0]
    assert stored["decision"] == "manual"
    assert stored["suggested_parent_id"] is None
    assert stored["actual_parent_id"] == "TEAM-500"


# ── Learnings endpoints ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_learnings_defaults_when_no_decisions(client):
    """Learnings endpoint returns defaults when no decisions exist."""
    response = await client.get("/api/v1/epics-police/learnings")
    assert response.status_code == 200
    data = response.json()
    assert data["sufficient_data"] is False
    assert data["learned_weights"]["label_overlap"] == 35.0
    assert data["learned_weights"]["squad_match"] == 10.0
    assert data["learned_thresholds"]["pass1_lock"] == 70.0


@pytest.mark.asyncio
async def test_distill_empty(client):
    """Distill with no decisions returns defaults."""
    response = await client.post("/api/v1/epics-police/distill")
    assert response.status_code == 200
    data = response.json()
    assert data["total_decisions"] == 0
    assert data["sufficient_data"] is False
    assert data["weights_changed"] is False


@pytest.mark.asyncio
async def test_distill_with_decisions(client):
    """Distill after storing decisions produces updated learnings."""
    # Store a mix of accepts and rejects with different signal profiles
    decisions = [
        # Accepted: strong label + squad signal
        _make_decision(
            orphan_identifier=f"TEAM-{i}",
            decision="accepted",
            suggested_confidence=75,
            suggested_signals={
                "label_overlap": 30,
                "title_overlap": 5,
                "description_overlap": 5,
                "squad_match": 10,
                "notion_match": 0,
            },
        )
        for i in range(10)
    ] + [
        # Rejected: strong title signal but no squad
        _make_decision(
            orphan_identifier=f"TEAM-{100 + i}",
            decision="rejected",
            suggested_confidence=55,
            suggested_signals={
                "label_overlap": 5,
                "title_overlap": 25,
                "description_overlap": 15,
                "squad_match": 0,
                "notion_match": 0,
            },
        )
        for i in range(10)
    ]
    await client.post("/api/v1/epics-police/decisions", json={"decisions": decisions})

    response = await client.post("/api/v1/epics-police/distill")
    assert response.status_code == 200
    data = response.json()
    assert data["total_decisions"] == 20
    assert data["sufficient_data"] is True

    # Now fetch the stored learnings
    response = await client.get("/api/v1/epics-police/learnings")
    assert response.status_code == 200
    learnings = response.json()
    assert learnings["total_decisions"] == 20
    assert learnings["weeks_covered"] == 1
    assert learnings["sufficient_data"] is True

    # Label overlap should have gained weight (strong in accepts, weak in rejects)
    assert (
        learnings["learned_weights"]["label_overlap"]
        > learnings["learned_weights"]["title_overlap"]
    )

    # Squad match should have gained weight (present in accepts, absent in rejects)
    assert learnings["signal_effectiveness"]["squad_match"]["lift"] > 1.0

    # Confidence calibration should have data
    assert "40_59" in learnings["confidence_calibration"]
    assert "60_79" in learnings["confidence_calibration"]


@pytest.mark.asyncio
async def test_distill_confidence_calibration(client):
    """Verify precision is computed per confidence band."""
    decisions = [
        # High confidence accepts (80-100)
        _make_decision(
            orphan_identifier="TEAM-1", decision="accepted", suggested_confidence=85
        ),
        _make_decision(
            orphan_identifier="TEAM-2", decision="accepted", suggested_confidence=90
        ),
        _make_decision(
            orphan_identifier="TEAM-3", decision="accepted", suggested_confidence=88
        ),
        # High confidence reject (should lower precision)
        _make_decision(
            orphan_identifier="TEAM-4", decision="rejected", suggested_confidence=82
        ),
        # Low confidence rejects (40-59)
        _make_decision(
            orphan_identifier="TEAM-5", decision="rejected", suggested_confidence=45
        ),
        _make_decision(
            orphan_identifier="TEAM-6", decision="rejected", suggested_confidence=50
        ),
        _make_decision(
            orphan_identifier="TEAM-7", decision="rejected", suggested_confidence=42
        ),
    ]
    await client.post("/api/v1/epics-police/decisions", json={"decisions": decisions})
    await client.post("/api/v1/epics-police/distill")

    response = await client.get("/api/v1/epics-police/learnings")
    cal = response.json()["confidence_calibration"]

    # 80-100 band: 3 accepted, 1 rejected → precision 0.75
    assert cal["80_100"]["accepted"] == 3
    assert cal["80_100"]["rejected"] == 1
    assert cal["80_100"]["precision"] == 0.75

    # 40-59 band: 0 accepted, 3 rejected → precision 0.0
    assert cal["40_59"]["accepted"] == 0
    assert cal["40_59"]["rejected"] == 3
    assert cal["40_59"]["precision"] == 0.0


@pytest.mark.asyncio
async def test_distill_idempotent(client):
    """Running distill twice produces the same learnings."""
    decisions = [
        _make_decision(orphan_identifier=f"TEAM-{i}", decision="accepted")
        for i in range(5)
    ]
    await client.post("/api/v1/epics-police/decisions", json={"decisions": decisions})

    resp1 = await client.post("/api/v1/epics-police/distill")
    resp2 = await client.post("/api/v1/epics-police/distill")

    d1 = resp1.json()
    d2 = resp2.json()
    assert d1["learned_weights"] == d2["learned_weights"]
    assert d1["learned_thresholds"] == d2["learned_thresholds"]


@pytest.mark.asyncio
async def test_learnings_stored_in_config(client):
    """Distilled learnings are stored via config and accessible from learnings endpoint."""
    await client.post(
        "/api/v1/epics-police/decisions",
        json={"decisions": [_make_decision()]},
    )
    await client.post("/api/v1/epics-police/distill")

    # Should be accessible via the config API too
    response = await client.get("/api/v1/config/epics_police/learnings")
    assert response.status_code == 200
    config_val = response.json()["value"]
    assert config_val["total_decisions"] == 1

    # And via the dedicated learnings endpoint
    response = await client.get("/api/v1/epics-police/learnings")
    assert response.status_code == 200
    assert response.json()["total_decisions"] == 1


@pytest.mark.asyncio
async def test_decision_counts_by_type(client):
    """Decision counts are broken down by type in learnings."""
    await client.post(
        "/api/v1/epics-police/decisions",
        json={
            "decisions": [
                _make_decision(orphan_identifier="TEAM-1", decision="accepted"),
                _make_decision(orphan_identifier="TEAM-2", decision="accepted"),
                _make_decision(orphan_identifier="TEAM-3", decision="rejected"),
                _make_decision(
                    orphan_identifier="TEAM-4",
                    decision="redirected",
                    actual_parent_id="TEAM-999",
                ),
                _make_decision(
                    orphan_identifier="TEAM-5",
                    decision="manual",
                    suggested_parent_id=None,
                ),
            ]
        },
    )
    await client.post("/api/v1/epics-police/distill")

    response = await client.get("/api/v1/epics-police/learnings")
    counts = response.json()["decision_counts"]
    assert counts["accepted"] == 2
    assert counts["rejected"] == 1
    assert counts["redirected"] == 1
    assert counts["manual"] == 1
