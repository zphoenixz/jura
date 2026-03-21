from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.person import Person
from app.schemas.common import PaginatedResponse
from app.schemas.person import PersonPatch, PersonRead
from app.services.people_service import get_people

router = APIRouter(prefix="/api/v1/people", tags=["people"])


@router.get("")
async def list_people(
    squad: str | None = None,
    email: str | None = None,
    name: str | None = None,
    limit: int = 500,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    limit = min(limit, 5000)
    people, total = await get_people(db, squad=squad, email=email, name=name, limit=limit, offset=offset)
    return PaginatedResponse(
        items=[PersonRead.model_validate(p) for p in people],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{person_id}")
async def get_person(person_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Person).where(Person.id == person_id))
    person = result.scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail={"error": "Person not found", "code": "not_found"})
    return PersonRead.model_validate(person)


@router.patch("/{person_id}")
async def patch_person(person_id: UUID, body: PersonPatch, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Person).where(Person.id == person_id))
    person = result.scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail={"error": "Person not found", "code": "not_found"})
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(person, field, value)
    await db.commit()
    await db.refresh(person)
    return PersonRead.model_validate(person)
