from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.epic import Epic, EpicSubPage
from app.schemas.epic import EpicInput


async def store_epics(db: AsyncSession, week_id, epics_data: list[EpicInput]) -> tuple[int, int]:
    await db.execute(delete(Epic).where(Epic.week_id == week_id))

    epic_count = 0
    sub_page_count = 0

    for e in epics_data:
        epic = Epic(
            week_id=week_id,
            notion_page_id=e.notion_page_id,
            title=e.title,
            status=e.status,
            team=e.team,
            pm_lead=e.pm_lead,
            sort_order=e.sort_order,
            dates=e.dates,
            content=e.content,
            properties=e.properties,
        )
        db.add(epic)
        await db.flush()
        epic_count += 1

        for sp in e.sub_pages:
            sub_page = EpicSubPage(
                epic_id=epic.id,
                notion_page_id=sp.notion_page_id,
                title=sp.title,
                content=sp.content,
            )
            db.add(sub_page)
            sub_page_count += 1

    await db.flush()
    return epic_count, sub_page_count
