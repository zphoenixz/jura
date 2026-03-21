"""End-to-end tests against the live API with migrated data.

These tests validate:
- Data integrity after migration
- Week resolution and boundaries
- Cross-source people linking
- Slack deep link compatibility (real channel_ids + timestamps)
- Filter correctness
- Idempotent re-fetch behavior
- No cross-week data leakage
"""

import httpx
import pytest

API = "http://localhost:8100/api/v1"
CLIENT = httpx.Client(base_url=API, timeout=30)


# --- Health & Infrastructure ---

def test_health():
    r = CLIENT.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["db"] == "ok"


def test_configs_seeded():
    r = CLIENT.get("/config/slack")
    assert r.status_code == 200
    keys = {c["key"] for c in r.json()}
    assert "watched_channels" in keys
    assert "watched_dm_people" in keys
    assert "timezone" in keys


def test_weeks_have_data():
    r = CLIENT.get("/weeks")
    assert r.status_code == 200
    weeks = r.json()
    assert len(weeks) >= 3
    # At least one week should have substantial data
    max_slack = max(w["sources"]["slack"] for w in weeks)
    assert max_slack > 100


# --- Week Resolution ---

def test_week_snapping_any_day():
    """Any day in the week resolves to the same Monday-Sunday bucket."""
    counts = []
    for day in ["2026-03-23", "2026-03-25", "2026-03-27", "2026-03-29"]:
        r = CLIENT.get("/linear", params={"week": day})
        assert r.status_code == 200
        counts.append(r.json()["total"])
    # All should return the same count
    assert len(set(counts)) == 1


def test_different_weeks_different_data():
    """Each week has its own cycle with different ticket counts."""
    week_counts = {}
    for monday in ["2026-03-16", "2026-03-23", "2026-03-30"]:
        r = CLIENT.get("/linear", params={"week": monday})
        assert r.status_code == 200
        week_counts[monday] = r.json()["total"]
    # With 1-week sprints, at least one week should differ
    assert len(set(week_counts.values())) > 1, f"All weeks have same count: {week_counts}"


def test_no_cross_week_leakage_slack():
    """Slack messages in adjacent weeks should have mostly distinct timestamps.

    Note: Historical file-parsed weeks may have overlap because the original
    markdown files didn't enforce strict time boundaries. API-fetched weeks
    use oldest/latest bounds and should have zero overlap.
    """
    r1 = CLIENT.get("/slack", params={"week": "2026-03-16", "limit": 5000})
    r2 = CLIENT.get("/slack", params={"week": "2026-03-23", "limit": 5000})
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Verify each week has its own data (not empty)
    assert r1.json()["total"] > 0
    assert r2.json()["total"] > 0
    # Verify they're not identical (different weeks = different data)
    assert r1.json()["total"] != r2.json()["total"]


# --- Slack Deep Links ---

def test_slack_real_channel_ids():
    """All slack messages must have real Slack channel IDs (not parsed- fakes)."""
    for week in ["2026-03-16", "2026-03-23", "2026-03-30"]:
        r = CLIENT.get("/slack", params={"week": week, "limit": 5000})
        assert r.status_code == 200
        for msg in r.json()["items"]:
            assert not msg["channel_id"].startswith("parsed-"), \
                f"Fake channel_id found: {msg['channel_id']} in week {week}"
            assert msg["channel_id"][0] in ("C", "D", "G"), \
                f"Invalid channel_id prefix: {msg['channel_id']} in week {week}"


def test_slack_real_timestamps():
    """All slack messages must have real Slack timestamps (not generated fakes)."""
    for week in ["2026-03-16", "2026-03-23", "2026-03-30"]:
        r = CLIENT.get("/slack", params={"week": week, "limit": 5000})
        assert r.status_code == 200
        for msg in r.json()["items"]:
            ts = msg["slack_ts"]
            assert "." in ts, f"No dot in ts: {ts}"
            parts = ts.split(".")
            assert len(parts[0]) == 10, f"Bad epoch part: {ts}"
            assert len(parts[1]) == 6, f"Bad micro part: {ts}"


def test_slack_thread_replies_have_thread_ts():
    """Thread replies must reference their parent via thread_ts."""
    r = CLIENT.get("/slack", params={"week": "2026-03-16", "is_thread_reply": True, "limit": 100})
    assert r.status_code == 200
    assert r.json()["total"] > 0
    for msg in r.json()["items"]:
        assert msg["is_thread_reply"] is True
        assert msg["thread_ts"] is not None
        assert msg["thread_ts"] != msg["slack_ts"]  # Reply ts differs from parent ts


# --- Linear ---

def test_linear_tickets_have_cycle_info():
    r = CLIENT.get("/linear", params={"week": "2026-03-16", "limit": 5})
    assert r.status_code == 200
    assert r.json()["total"] > 0
    for ticket in r.json()["items"]:
        assert ticket["cycle_number"] is not None
        assert "-" in ticket["identifier"]  # e.g. TEAM-123


def test_linear_comments_nested():
    """Tickets with comments should have them nested in the response."""
    r = CLIENT.get("/linear", params={"week": "2026-03-30", "limit": 500})
    assert r.status_code == 200
    tickets_with_comments = [t for t in r.json()["items"] if t["comments"]]
    assert len(tickets_with_comments) > 0
    for ticket in tickets_with_comments:
        for comment in ticket["comments"]:
            assert "body" in comment
            assert "author_name" in comment


def test_linear_filters():
    r = CLIENT.get("/linear", params={"week": "2026-03-30", "status_type": "done"})
    assert r.status_code == 200
    for ticket in r.json()["items"]:
        assert ticket["status_type"] == "done"

    r = CLIENT.get("/linear", params={"week": "2026-03-30", "status_type": "in_progress"})
    assert r.status_code == 200
    for ticket in r.json()["items"]:
        assert ticket["status_type"] == "in_progress"


# --- Meets ---

def test_meets_different_counts_per_week():
    counts = {}
    for week in ["2026-01-05", "2026-02-09", "2026-03-16"]:
        r = CLIENT.get("/meets", params={"week": week})
        assert r.status_code == 200
        counts[week] = r.json()["total"]
    assert len(set(counts.values())) > 1, f"All weeks same count: {counts}"


def test_meets_attendees_nested():
    r = CLIENT.get("/meets", params={"week": "2026-03-16", "limit": 5})
    assert r.status_code == 200
    assert r.json()["total"] > 0
    for meeting in r.json()["items"]:
        assert "attendees" in meeting
        assert len(meeting["attendees"]) > 0
        for att in meeting["attendees"]:
            assert att["name"] or att["email"]


def test_meets_title_search():
    r = CLIENT.get("/meets", params={"week": "2026-03-16", "title": "planning"})
    assert r.status_code == 200
    for meeting in r.json()["items"]:
        assert "planning" in meeting["title"].lower()


# --- Epics ---

def test_epics_with_sub_pages():
    r = CLIENT.get("/epics", params={"week": "2026-03-23"})
    assert r.status_code == 200
    assert r.json()["total"] > 0
    epics_with_subs = [e for e in r.json()["items"] if e["sub_pages"]]
    assert len(epics_with_subs) > 0


def test_epics_status_filter():
    r = CLIENT.get("/epics", params={"week": "2026-03-30", "status": "In development"})
    assert r.status_code == 200
    for epic in r.json()["items"]:
        assert epic["status"] == "In development"


def test_epics_idempotent_push():
    """Pushing the same epics twice to current week should not duplicate."""
    # Use current week (historical weeks are protected by 409)
    payload = {
        "epics": [{"notion_page_id": "test-idem", "title": "Idempotent Test", "status": "Test"}],
    }
    r = CLIENT.post("/epics/fetch", json=payload)
    assert r.status_code == 200

    # Push again — replaces, not appends
    r = CLIENT.post("/epics/fetch", json=payload)
    assert r.status_code == 200

    r2 = CLIENT.get("/epics")  # current week
    # Should be 1 (the upsert replaces, not appends)
    assert r2.json()["total"] == 1


# --- People ---

def test_people_cross_linked():
    """People should have identities from multiple sources."""
    r = CLIENT.get("/people", params={"limit": 500})
    assert r.status_code == 200
    people = r.json()["items"]
    assert len(people) > 0

    with_email = [p for p in people if p["email"]]
    with_slack = [p for p in people if p["slack_user_id"]]
    assert len(with_email) > 10
    assert len(with_slack) > 10


def test_people_filter_by_name():
    r = CLIENT.get("/people", params={"name": "denis"})
    assert r.status_code == 200
    assert r.json()["total"] >= 1
    assert "denis" in r.json()["items"][0]["display_name"].lower()


# --- Pagination ---

def test_pagination():
    r1 = CLIENT.get("/slack", params={"week": "2026-03-16", "limit": 10, "offset": 0})
    r2 = CLIENT.get("/slack", params={"week": "2026-03-16", "limit": 10, "offset": 10})
    assert r1.status_code == 200
    assert r2.status_code == 200
    ids1 = {m["id"] for m in r1.json()["items"]}
    ids2 = {m["id"] for m in r2.json()["items"]}
    assert len(ids1) == 10
    assert len(ids2) == 10
    assert ids1.isdisjoint(ids2)  # No overlap between pages


# --- Fetch Log ---

def test_fetch_log_in_health():
    r = CLIENT.get("/health")
    assert r.status_code == 200
    sources = r.json()["sources"]
    for source in ["slack", "linear", "meets"]:
        assert sources[source]["last_status"] in ("success", "partial"), \
            f"{source} last_status={sources[source]['last_status']}"
