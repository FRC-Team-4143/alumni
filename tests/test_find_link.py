"""
GET/POST /survey/find — self-service "email me my update link" recovery for someone
with no personal link at all (a plain BCC blast, or lost Slack access).
"""
from datetime import datetime, timedelta

from app.config import settings
from app.models import SurveyStatus


async def _complete_with_email(client, member, email="grad@example.com"):
    """Drive a real /survey submission so contact_email actually gets set, matching
    how it's populated in production (services/survey.mark_completed)."""
    from app.services.survey import make_survey_link

    token = make_survey_link(member.survey.id).split("token=")[1]
    resp = await client.post(
        "/survey",
        data={"token": token, "stay_in_touch": "yes", "contact_email": email},
    )
    assert resp.status_code == 303
    await client.get(resp.headers["location"])


async def test_find_form_renders(client):
    resp = await client.get("/survey/find")
    assert resp.status_code == 200
    assert "Email address" in resp.text


async def test_matching_email_sends_a_link(client, make_alumnus, fake_email, db):
    member = await make_alumnus(name="Grad Uate")
    await _complete_with_email(client, member, email="grad@example.com")

    resp = await client.post("/survey/find", data={"email": "GRAD@example.com"})
    assert resp.status_code == 200
    assert "Check your email" in resp.text

    assert len(fake_email.sent) == 1
    assert fake_email.sent[0]["to"] == "grad@example.com"
    assert "/survey?token=" in fake_email.sent[0]["body"]

    await db.refresh(member.survey)
    assert member.survey.self_service_sent_at is not None


async def test_unmatched_email_gets_the_same_generic_response(client, fake_email):
    resp = await client.post("/survey/find", data={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert "Check your email" in resp.text
    assert fake_email.sent == []


async def test_cooldown_blocks_a_second_send(client, make_alumnus, fake_email, db):
    member = await make_alumnus(name="Grad Uate")
    await _complete_with_email(client, member, email="grad@example.com")

    await client.post("/survey/find", data={"email": "grad@example.com"})
    assert len(fake_email.sent) == 1

    await client.post("/survey/find", data={"email": "grad@example.com"})
    assert len(fake_email.sent) == 1  # still 1 — within cooldown, silently skipped


async def test_cooldown_expired_allows_a_resend(client, make_alumnus, fake_email, db):
    member = await make_alumnus(name="Grad Uate")
    await _complete_with_email(client, member, email="grad@example.com")

    member.survey.self_service_sent_at = datetime.utcnow() - timedelta(
        seconds=settings.survey_find_cooldown_seconds + 1
    )
    await db.commit()

    await client.post("/survey/find", data={"email": "grad@example.com"})
    assert len(fake_email.sent) == 1


async def test_no_contact_email_on_file_sends_nothing(client, make_alumnus, fake_email):
    """A graduate who never completed the survey (or said no to staying in touch) has
    no contact_email to match on at all — the generic response still shows, but there's
    nothing to send."""
    await make_alumnus(name="Grad Uate", status=SurveyStatus.sent)

    resp = await client.post("/survey/find", data={"email": "grad@example.com"})
    assert resp.status_code == 200
    assert fake_email.sent == []
