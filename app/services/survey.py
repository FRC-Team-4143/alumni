"""
Post-graduation survey — DM + link mint/verify + completion. Moved here from Legion's
`services/graduation_survey.py`, with one structural change: the link is a plain web
form (`GET/POST /survey`), not a Slack modal, and it is **not** gated by Legion SSO.

Why not Legion SSO: Legion's magic links deliberately re-check `is_active` on every
redemption ("archiving someone kills every link already sitting in their DMs" — see
Legion's `routers/sso.py::sso_link`), and a graduate is archived the instant they
graduate. So a Legion-minted link would be dead on arrival for exactly the audience this
survey is for. Instead, this module mints and verifies its own single-purpose bearer
token naming one `AlumniSurvey`, signed with `settings.survey_link_secret` — a secret
that has nothing to do with Legion and so keeps working for someone Legion no longer
authenticates at all.

This same link does double duty: it's what the graduation DM carries, and it's also the
permanent "update your career/email" link an admin can hand-drop into whatever they
send a newsletter through later — there's no separate email-sending feature here (see
routers/admin.py's `newsletter.csv` export and CLAUDE.md's "The newsletter link is BYO
email" for why). That's why it's reusable within `SURVEY_LINK_TTL` and, by default,
never expires at all (0) — a link that might sit in someone's admin spreadsheet for a
year between newsletters has to still work when they finally use it.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import AlumniSurvey, Member, SurveyStatus
from app.services.email_sender import send_email
from app.services.slack_client import send_dm, update_message, get_slack_client

log = logging.getLogger(__name__)

_link_signer = URLSafeTimedSerializer(settings.survey_link_secret, salt="alumni-survey-link")


def make_survey_link(survey_id: int) -> Optional[str]:
    """A bearer link to fill out (or review) survey `survey_id`. None if
    SURVEY_LINK_SECRET isn't configured — the survey row still exists, it's just not
    reachable by link until an admin sets that up (logged so it's not a silent gap)."""
    if not settings.survey_link_secret:
        log.warning("Cannot mint a survey link: SURVEY_LINK_SECRET is not configured.")
        return None
    token = _link_signer.dumps({"survey_id": survey_id})
    return f"{settings.base_url}/survey?token={quote(token, safe='')}"


def read_survey_token(token: str) -> Optional[int]:
    """The `survey_id` a token names, or None if missing/invalid/expired.
    `survey_link_ttl <= 0` (the default) means the link never expires."""
    if not token:
        return None
    max_age = settings.survey_link_ttl if settings.survey_link_ttl > 0 else None
    try:
        payload = _link_signer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    return payload.get("survey_id")


def _intro_text(link: str) -> str:
    return (
        "🎓 Congrats on graduating! We'd love to hear what's next for you, and whether "
        f"you'd like to stay in touch for future updates & special events.\n<{link}|Fill "
        "out a quick survey>"
    )


async def send_survey_dm(member: Member, survey_id: int) -> Optional[str]:
    """DM the graduate the intro + survey link. Returns the message ts (so a submit can
    edit the DM to a "Thanks!") or None if it couldn't be sent (no Slack id, no bot
    token, no link secret configured, or a Slack API failure). Never raises — matching
    every other outbound integration in this codebase. Takes `survey_id` explicitly
    (rather than reading `member.survey.id`) so the caller doesn't need that
    relationship eager-loaded."""
    if not member.slack_user_id:
        return None
    link = make_survey_link(survey_id)
    if not link:
        return None
    return await send_dm(member.slack_user_id, _intro_text(link), automated=False)


async def resend_survey_dm(member: Member, survey: AlumniSurvey) -> bool:
    """Admin-triggered resend (the directory's "Resend survey" button). Updates
    resend bookkeeping regardless of delivery outcome, so repeated attempts are still
    visible in the admin UI even if Slack is unreachable."""
    ts = await send_survey_dm(member, survey.id)
    survey.resent_count += 1
    survey.last_resent_at = datetime.utcnow()
    if ts:
        survey.slack_message_ts = ts
        survey.sent_at = survey.sent_at or datetime.utcnow()
    return ts is not None


async def mark_completed(survey: AlumniSurvey, member: Member, **answers) -> None:
    """Save the submitted answers and edit the original DM to a thank-you, if any.
    `member` is passed explicitly (rather than read off `survey.member`) so the caller
    doesn't need that relationship eager-loaded."""
    for key, value in answers.items():
        setattr(survey, key, value)
    survey.status = SurveyStatus.completed
    survey.completed_at = datetime.utcnow()

    if survey.slack_message_ts and member.slack_user_id:
        try:
            client = get_slack_client()
            conv = await client.conversations_open(users=member.slack_user_id)
            await update_message(
                conv["channel"]["id"], survey.slack_message_ts,
                "✅ Thanks for filling out the survey!",
            )
        except Exception:
            log.exception("Failed to update graduation survey DM for %s", member.name)


def _find_link_email_body(member: Member, link: str) -> str:
    first = member.name.split(" ")[0]
    return (
        f"Hi {first},\n\n"
        "Here's your personal link to update your info with us:\n"
        f"{link}\n\n"
        "It's the same link from your original graduation message — filling it out "
        "again just overwrites your previous answers, so use it any time something "
        "changes.\n"
    )


async def find_surveys_by_email(db: AsyncSession, email: str) -> list[AlumniSurvey]:
    """Every AlumniSurvey whose on-file `contact_email` matches (case-insensitive),
    each with `member` eager-loaded. Used only by the self-service /survey/find flow —
    `contact_email` is only ever set once someone has already completed the survey and
    opted to stay in touch, so this can't be used to fish for graduates who never gave
    us one. Usually zero or one result, but not unique/indexed, so a coincidence (two
    alumni sharing a family email, say) returns both — each gets its own link mailed to
    that one inbox, which is fine since the requester already controls it either way."""
    email = email.strip().lower()
    if not email:
        return []
    result = await db.execute(
        select(AlumniSurvey)
        .options(selectinload(AlumniSurvey.member))
        .where(func.lower(AlumniSurvey.contact_email) == email)
    )
    return [s for s in result.scalars().all() if s.member is not None]


async def email_survey_link(survey: AlumniSurvey, member: Member) -> bool:
    """Self-service resend, by email instead of Slack DM — the counterpart to
    resend_survey_dm for someone who's lost track of their link (a plain BCC blast
    carries no personal link, and a graduate long gone from Slack has no other way
    back in). Bumps `self_service_sent_at` regardless of whether the send actually
    succeeded, matching resend_survey_dm's "never fails loudly" bookkeeping — see that
    field's docstring for why it's kept separate from the admin Resend button's own
    resent_count/last_resent_at."""
    if not survey.contact_email:
        return False
    link = make_survey_link(survey.id)
    if not link:
        return False
    survey.self_service_sent_at = datetime.utcnow()
    return await send_email(
        survey.contact_email, "Your Alumni update link", _find_link_email_body(member, link)
    )


async def request_link_by_email(db: AsyncSession, email: str) -> None:
    """The full /survey/find flow: email a fresh update link to every survey whose
    on-file contact_email matches, skipping any still within
    SURVEY_FIND_COOLDOWN_SECONDS of its last self-service send. Commits. Always
    completes the same way whether or not anything matched — the router shows one
    generic response either way, so this can't be used to enumerate which emails are
    on file (same anti-enumeration shape as Legion's SSO username check)."""
    now = datetime.utcnow()
    cooldown = timedelta(seconds=settings.survey_find_cooldown_seconds)
    for survey in await find_surveys_by_email(db, email):
        if survey.self_service_sent_at and now - survey.self_service_sent_at < cooldown:
            continue
        await email_survey_link(survey, survey.member)
    await db.commit()
