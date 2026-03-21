from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person


async def resolve_person(
    db: AsyncSession,
    *,
    email: str | None = None,
    slack_user_id: str | None = None,
    linear_user_id: str | None = None,
    display_name: str | None = None,
    fireflies_name: str | None = None,
) -> Person:
    person = None

    if email and not person:
        result = await db.execute(select(Person).where(Person.email == email))
        person = result.scalar_one_or_none()

    if slack_user_id and not person:
        result = await db.execute(select(Person).where(Person.slack_user_id == slack_user_id))
        person = result.scalar_one_or_none()

    if linear_user_id and not person:
        result = await db.execute(select(Person).where(Person.linear_user_id == linear_user_id))
        person = result.scalar_one_or_none()

    if display_name and not person:
        first_name = display_name.strip().split()[0].lower() if display_name else ""
        if first_name:
            result = await db.execute(
                select(Person).where(func.lower(Person.display_name).like(f"{first_name}%")).limit(1)
            )
            person = result.scalar_one_or_none()

    if person is None:
        person = Person(
            display_name=display_name or email or slack_user_id or "Unknown",
            email=email,
            slack_user_id=slack_user_id,
            linear_user_id=linear_user_id,
            fireflies_name=fireflies_name,
        )
        db.add(person)
        await db.flush()
        return person

    changed = False
    if email and not person.email:
        person.email = email
        changed = True
    if slack_user_id and not person.slack_user_id:
        person.slack_user_id = slack_user_id
        changed = True
    if linear_user_id and not person.linear_user_id:
        # Check no other person has this linear_user_id (avoid unique constraint violation)
        existing = await db.execute(select(Person).where(Person.linear_user_id == linear_user_id))
        if not existing.scalar_one_or_none():
            person.linear_user_id = linear_user_id
            changed = True
    if fireflies_name and not person.fireflies_name:
        person.fireflies_name = fireflies_name
        changed = True
    if changed:
        await db.flush()

    return person


async def get_people(
    db: AsyncSession,
    *,
    squad: str | None = None,
    email: str | None = None,
    name: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[Person], int]:
    query = select(Person)
    count_query = select(func.count()).select_from(Person)

    if squad:
        query = query.where(Person.squad == squad)
        count_query = count_query.where(Person.squad == squad)
    if email:
        query = query.where(Person.email.ilike(f"%{email}%"))
        count_query = count_query.where(Person.email.ilike(f"%{email}%"))
    if name:
        query = query.where(Person.display_name.ilike(f"%{name}%"))
        count_query = count_query.where(Person.display_name.ilike(f"%{name}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(Person.display_name).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all()), total
