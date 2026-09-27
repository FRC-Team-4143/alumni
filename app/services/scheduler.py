"""
APScheduler jobs:
  1. Weekly SQLite backup.
  2. Survey reminders — an optional daily nudge to anyone who hasn't answered yet
     (off by default; see SURVEY_REMINDER_DAYS).

No Legion roster sync job exists here — unlike every sibling app, Alumni doesn't poll
Legion for anything. See config.py's `legion_base_url` docstring.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal

log = logging.getLogger(__name__)


async def job_nightly_backup() -> None:
    from app.services.backup import is_sqlite, nightly_backup
    if not is_sqlite():
        return
    try:
        nightly_backup()
    except Exception:  # never let a backup failure crash the scheduler
        log.exception("Backup failed")


async def job_survey_reminders() -> None:
    """Re-DM anyone whose survey is still `sent` (not `completed`) once it's been at
    least SURVEY_REMINDER_DAYS since it went out. 0 (the default) disables this
    entirely — nudging alumni is a nice-to-have, not something every deploy wants on."""
    if not settings.updates_enabled or settings.survey_reminder_days <= 0:
        return
    from app.models import AlumniSurvey, Member, SurveyStatus
    from app.services.survey import resend_survey_dm

    cutoff = datetime.utcnow() - timedelta(days=settings.survey_reminder_days)
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(AlumniSurvey, Member)
                    .join(Member, Member.id == AlumniSurvey.member_id)
                    .where(
                        AlumniSurvey.status == SurveyStatus.sent,
                        AlumniSurvey.sent_at.is_not(None),
                        AlumniSurvey.sent_at <= cutoff,
                        # Don't re-nudge every night once eligible — only right after
                        # crossing the threshold, or after a previous reminder aged out
                        # by the same interval again.
                        (AlumniSurvey.last_resent_at.is_(None))
                        | (AlumniSurvey.last_resent_at <= cutoff),
                    )
                )
            ).all()
            sent = 0
            for survey, member in rows:
                if await resend_survey_dm(member, survey):
                    sent += 1
            await db.commit()
        log.info("Survey reminders: %s sent", sent)
    except Exception:  # never let a reminder failure crash the scheduler
        log.exception("Scheduled survey reminders failed")


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """(Re)register all scheduled jobs from the current settings. Uses
    ``replace_existing=True`` so it is safe to call on a running scheduler."""
    bh, bm = settings.backup_time.split(":")
    scheduler.add_job(
        job_nightly_backup,
        CronTrigger(day_of_week=settings.backup_day, hour=int(bh), minute=int(bm), timezone=settings.timezone),
        id="nightly_backup",
        replace_existing=True,
    )

    # Late morning: a reminder that lands mid-day gets acted on, one at 3am gets buried.
    scheduler.add_job(
        job_survey_reminders,
        CronTrigger(hour=10, minute=30, timezone=settings.timezone),
        id="survey_reminders",
        replace_existing=True,
    )


def reschedule_all(scheduler) -> None:
    if scheduler is None:
        return
    register_jobs(scheduler)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    register_jobs(scheduler)
    return scheduler
