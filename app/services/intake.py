"""
Turning a newly-graduated member into a local record: create the `Member` row, create
its `AlumniSurvey`, and send the survey DM — all in one step, all idempotent on
`member_code` so a retried/duplicated push from Legion never creates two records for
the same person.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AlumniSurvey, Member
from app.services.survey import send_survey_dm


async def intake_graduate(
    db: AsyncSession,
    *,
    name: str,
    graduation_year: int,
    member_code: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    team_number: Optional[int] = None,
    subteam_label: Optional[str] = None,
) -> Member:
    """Create (or find) the Member for a graduate and make sure they have a survey.

    De-dupes on `member_code` when one is given — a resent Legion push (e.g. an admin
    retrying a delivery that failed the first time, per the audit log) must not create a
    second record for the same person. A member with no `member_code` at all (a
    hand-added historical alumnus) is never de-duped this way, since there's nothing
    reliable to match on.
    """
    member = None
    is_new = False
    if member_code:
        member = (
            await db.execute(
                select(Member)
                .options(selectinload(Member.survey))
                .where(Member.member_code == member_code)
            )
        ).scalars().first()

    if member is None:
        is_new = True
        member = Member(
            member_code=member_code,
            name=name,
            slack_user_id=slack_user_id,
            team_number=team_number,
            subteam_label=subteam_label,
            graduation_year=graduation_year,
        )
        db.add(member)
        await db.flush()  # assigns member.id

    # A brand-new member obviously has no survey yet — skip the `member.survey` check
    # entirely for that branch. It's a scalar (uselist=False) relationship, so SQLAlchemy
    # can't assume "no row exists" just because nothing was assigned in Python; reading
    # it lazy-loads with a real SELECT even on an object we just created ourselves — and
    # a bare attribute access like that isn't wrapped in the greenlet context
    # AsyncSession needs, so it raises MissingGreenlet outside of an explicit `await
    # session.execute(...)`/`refresh(...)`. The de-dupe branch above sidesteps this by
    # eager-loading `survey` with `selectinload` up front instead.
    if is_new or member.survey is None:
        survey = AlumniSurvey(member_id=member.id)
        db.add(survey)
        await db.flush()  # assigns survey.id, needed by the link the DM carries
        ts = await send_survey_dm(member, survey.id)
        survey.slack_message_ts = ts
        survey.sent_at = datetime.utcnow() if ts else None

    await db.commit()
    await db.refresh(member)
    return member
