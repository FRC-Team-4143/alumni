"""POST /api/graduates — the one place Legion calls into this app. See
app/routers/graduates.py and app/services/intake.py."""
from sqlalchemy import select

from app.config import settings
from app.models import AlumniSurvey, Member


async def test_intake_requires_api_key(client, db):
    settings.legion_push_api_key = "the-real-key"
    resp = await client.post(
        "/api/graduates",
        json={"name": "New Grad", "graduation_year": 2026},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


async def test_intake_fails_closed_when_unconfigured(client, db):
    # legion_push_api_key is blank by default (reset by the autouse settings fixture).
    resp = await client.post("/api/graduates", json={"name": "New Grad", "graduation_year": 2026})
    assert resp.status_code == 503


async def test_intake_creates_member_and_survey_and_sends_dm(client, db, fake_slack):
    settings.legion_push_api_key = "the-real-key"
    settings.survey_link_secret = "test-survey-secret"

    resp = await client.post(
        "/api/graduates",
        json={
            "name": "Nova Grad", "graduation_year": 2026, "member_code": "leg00001",
            "slack_user_id": "U0NOVA", "team_number": 4143, "subteam_label": "Software",
        },
        headers={"X-API-Key": "the-real-key"},
    )
    assert resp.status_code == 200

    member = (await db.execute(select(Member).where(Member.name == "Nova Grad"))).scalars().first()
    assert member is not None
    assert member.member_code == "leg00001"
    assert member.graduation_year == 2026
    assert member.team_number == 4143
    assert member.subteam_label == "Software"

    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member.id))).scalars().first()
    assert survey is not None
    assert survey.status.value == "sent"
    assert survey.sent_at is not None

    assert len(fake_slack.dms) == 1
    assert "Congrats on graduating" in fake_slack.dms[0]["text"]
    assert "/survey?token=" in fake_slack.dms[0]["text"]


async def test_intake_without_slack_id_creates_survey_but_sends_nothing(client, db, fake_slack):
    settings.legion_push_api_key = "the-real-key"
    resp = await client.post(
        "/api/graduates",
        json={"name": "No Slack Grad", "graduation_year": 2026},
        headers={"X-API-Key": "the-real-key"},
    )
    assert resp.status_code == 200
    member = (await db.execute(select(Member).where(Member.name == "No Slack Grad"))).scalars().first()
    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member.id))).scalars().first()
    assert survey is not None
    assert survey.sent_at is None
    assert fake_slack.dms == []


async def test_intake_is_idempotent_on_member_code(client, db, fake_slack):
    settings.legion_push_api_key = "the-real-key"
    payload = {
        "name": "Repeat Grad", "graduation_year": 2026, "member_code": "leg00002",
        "slack_user_id": "U0REPEAT",
    }
    headers = {"X-API-Key": "the-real-key"}

    first = await client.post("/api/graduates", json=payload, headers=headers)
    second = await client.post("/api/graduates", json=payload, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["member_id"] == second.json()["member_id"]

    rows = (await db.execute(select(Member).where(Member.member_code == "leg00002"))).scalars().all()
    assert len(rows) == 1
    # Only one survey row, and only one DM sent, despite the repeat push.
    surveys = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == rows[0].id))).scalars().all()
    assert len(surveys) == 1
    assert len(fake_slack.dms) == 1
