import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MemberKind(str, enum.Enum):
    """Mirrors Legion's `role`. Almost every graduate here is a student — the person
    whose grade froze at `senior` the moment Legion archived them — but Legion also
    lets an alumnus-turned-mentor carry their old grade directly on a mentor row (see
    Legion's CLAUDE.md, "Members are unified"), so a hand-added historical record here
    can be either kind too."""
    student = "student"
    mentor = "mentor"


class Member(Base):
    """One graduate. Unlike every sibling app's roster mirror, this is NOT a live sync
    of Legion — Legion holds no ongoing "alumni" state to poll for (see Legion's
    CLAUDE.md, "Graduation is a push-event, not stored state"). A row here is created
    exactly once, either by Legion's one-time POST /api/graduates push (see
    routers/graduates.py) at the moment someone graduates, or by hand (an admin
    backfilling a historical alumnus Legion never pushed). `team_number`/
    `subteam_label` are a point-in-time snapshot, never refreshed — if Legion's subteam
    naming changes later, an old graduate's record simply keeps saying what it said the
    day they left, which is the correct historical answer, not a live one.
    """
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Legion's `member_code`, when known — nullable because a hand-added historical
    # alumnus may predate any reliable record of theirs. Not unique: Legion's push
    # includes no idempotency key of its own, so a resend (e.g. Legion retrying a
    # failed delivery by hand) could plausibly repeat one — routers/graduates.py
    # de-dupes on it when present, but the column itself stays permissive.
    member_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[MemberKind] = mapped_column(
        SAEnum(MemberKind), nullable=False, default=MemberKind.student
    )
    slack_user_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    team_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subteam_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    graduation_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    survey: Mapped[Optional["AlumniSurvey"]] = relationship(
        "AlumniSurvey", back_populates="member", uselist=False, cascade="all, delete-orphan"
    )


class SurveyStatus(str, enum.Enum):
    sent = "sent"            # DM (or admin-created placeholder) exists, not yet answered
    completed = "completed"


class AlumniSurvey(Base):
    """One post-graduation survey. Created the moment a `Member` row is intake'd with a
    `slack_user_id` on file (see services/survey.py); a member with no Slack ID still
    gets a row so they show up as "needs manual follow-up" in the directory rather than
    silently having no survey at all. Moved here from Legion's old `GraduationSurvey`
    (the same 4 core fields), but answered via a plain web form (`/survey`) instead of
    a Slack modal — see CLAUDE.md's "Survey mechanism" for why.
    """
    __tablename__ = "alumni_surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[SurveyStatus] = mapped_column(
        SAEnum(SurveyStatus), nullable=False, default=SurveyStatus.sent
    )

    # The DM's message ts, so a submit can edit it to "✅ Thanks" in place. No channel id
    # is stored alongside it — conversations_open is idempotent per user, so
    # services/survey.py just re-opens the DM by slack_user_id when it needs to edit.
    slack_message_ts: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # The 4 core answers, unchanged in shape from Legion's old GraduationSurvey.
    destination: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    field_of_study: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    stay_in_touch: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # "Where are they now" extensions the old survey had no room for. None of these are
    # required by the graduation flow itself — an admin fills them in over time from
    # LinkedIn, a reunion conversation, etc.
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    current_city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    willing_to_mentor: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Staff-only — never rendered on the /survey form, only on the admin detail page.
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_resent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Last time this alum used /survey/find to email themselves a fresh copy of their
    # link (see services/survey.email_survey_link). Deliberately separate from
    # resent_count/last_resent_at above, which the admin directory's "Resend" button
    # reads as "how many times has staff tried" — a self-service request isn't staff
    # acting, and conflating the two would make that count lie. Doubles as the cooldown
    # clock (SURVEY_FIND_COOLDOWN_SECONDS) so one person can't hammer send.
    self_service_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    member: Mapped["Member"] = relationship("Member", back_populates="survey")


class Newsletter(Base):
    """One published newsletter issue: a PDF plus the metadata to list/order it. The
    public front page (routers/newsletters.py) shows the one with the latest
    `published_date` and links to the rest as an archive — this is what makes the
    domain double as a newsletter site rather than just an app with no home page.
    Uploaded/deleted only from `/admin/newsletters` (alumni-admin or alumni-manager,
    same tier as the Alumni Directory); there's no draft state — every row here is
    immediately public, matching this app's small-trusted-team posture elsewhere."""
    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The stored filename under settings.newsletter_dir — a random token, never the
    # client-supplied name (avoids path traversal and collisions), served publicly via
    # the /newsletter-files static mount (see main.py). PDFs are inherently public
    # content here, so an unguessable-but-unauthenticated filename is an acceptable
    # trade, same as Merces's store-item photos.
    filename: Mapped[str] = mapped_column(String(64), nullable=False)
    # What issue this is, e.g. "Fall 2026" — an admin-set date, not the upload
    # timestamp, so backfilling an older issue (or uploading this quarter's a few days
    # late) still sorts correctly against the rest of the archive.
    published_date: Mapped[date] = mapped_column(Date, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class AppSetting(Base):
    """Small key/value store for runtime-configurable app settings. No roster-sync
    watermark lives here (unlike the sibling apps) — Alumni runs no such sync."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AuditLog(Base):
    """Append-only record of admin mutations."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # naive UTC
    actor: Mapped[str] = mapped_column(String(80), nullable=False, default="admin")
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "member.resend_survey"
    entity_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
