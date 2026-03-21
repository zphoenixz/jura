import pytest

from app.models.person import Person


@pytest.mark.asyncio
async def test_people_list_empty(client):
    response = await client.get("/api/v1/people")
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_people_create_and_get(client, db_session):
    person = Person(display_name="Alice Smith", email="alice@example.com", squad="backend")
    db_session.add(person)
    await db_session.commit()

    response = await client.get("/api/v1/people")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["display_name"] == "Alice Smith"

    pid = response.json()["items"][0]["id"]
    response = await client.get(f"/api/v1/people/{pid}")
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_people_patch(client, db_session):
    person = Person(display_name="Test User", email="test@example.com")
    db_session.add(person)
    await db_session.commit()

    response = await client.get("/api/v1/people")
    pid = response.json()["items"][0]["id"]

    response = await client.patch(f"/api/v1/people/{pid}", json={"squad": "search", "role": "engineer"})
    assert response.status_code == 200
    assert response.json()["squad"] == "search"
    assert response.json()["role"] == "engineer"


@pytest.mark.asyncio
async def test_people_filter_by_name(client, db_session):
    db_session.add(Person(display_name="Alice Smith", email="alice@example.com"))
    db_session.add(Person(display_name="Bob Jones", email="bob@example.com"))
    await db_session.commit()

    response = await client.get("/api/v1/people?name=alice")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["display_name"] == "Alice Smith"
