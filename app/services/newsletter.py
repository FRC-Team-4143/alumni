"""
Source of truth for "who gets a newsletter EMAIL" — read by the CSV export
(`routers/admin.py`'s `newsletter.csv`), kept as its own module rather than inlined in
the router in case a second consumer ever needs the same list.

Not to be confused with `services/newsletter_pdfs.py` (deliberately named apart despite
the similar name) — that one stores the actual public-facing newsletter PDFs shown on
the front page / archive. This module has nothing to do with those; it's the recipient
list for the outbound BYO-mail-merge send those PDFs might accompany.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AlumniSurvey, Member
from app.services.survey import make_survey_link


async def newsletter_recipients(db: AsyncSession) -> list[dict]:
    """Alumni who said Yes to staying in touch and left a contact email — the only
    people it's ever appropriate to include in a newsletter send. Each row carries
    that person's own durable update link (see services/survey.py)."""
    members = (
        await db.execute(
            select(Member)
            .join(AlumniSurvey, AlumniSurvey.member_id == Member.id)
            .options(selectinload(Member.survey))
            .where(AlumniSurvey.stay_in_touch.is_(True), AlumniSurvey.contact_email.is_not(None))
            .order_by(Member.name)
        )
    ).scalars().all()
    return [
        {
            "name": m.name,
            "contact_email": m.survey.contact_email,
            "graduation_year": m.graduation_year or "",
            "update_link": make_survey_link(m.survey.id) or "",
        }
        for m in members
    ]
