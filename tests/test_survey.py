"""GET/POST /survey — the public form, reached by its own bearer token (not Legion
SSO). See app/routers/portal.py and app/services/survey.py."""
from sqlalchemy import select

from app.models import AlumniSurvey
from app.services.survey import make_survey_link


async def _link_for(member) -> str:
    return make_survey_link(member.survey.id)


async def test_invalid_token_shows_link_invalid_page(client):
    resp = await client.get("/survey", params={"token": "not-a-real-token"})
    assert resp.status_code == 404
    assert "isn't valid" in resp.text


async def test_missing_token_shows_link_invalid_page(client):
    resp = await client.get("/survey")
    assert resp.status_code == 404


async def test_valid_token_renders_form(client, make_alumnus):
    member = await make_alumnus(name="Sam Senior")
    link = await _link_for(member)
    token = link.split("token=")[1]

    resp = await client.get("/survey", params={"token": token})
    assert resp.status_code == 200
    assert "Congrats on graduating, Sam" in resp.text


async def test_submit_yes_with_valid_email_persists_answers(client, db, make_alumnus, fake_slack):
    member = await make_alumnus(name="Sam Senior")
    token = (await _link_for(member)).split("token=")[1]

    resp = await client.post(
        "/survey",
        data={
            "token": token, "destination": "State University",
            "field_of_study": "Computer Science", "stay_in_touch": "yes",
            "contact_email": "sam@example.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/survey/thanks")

    member_id = member.id  # captured before expire_all() below invalidates the object
    db.expire_all()
    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member_id))).scalars().first()
    assert survey.status.value == "completed"
    assert survey.destination == "State University"
    assert survey.field_of_study == "Computer Science"
    assert survey.stay_in_touch is True
    assert survey.contact_email == "sam@example.com"
    assert survey.completed_at is not None


async def test_submit_yes_with_blank_email_returns_validation_error(client, make_alumnus):
    member = await make_alumnus(name="Sam Senior")
    token = (await _link_for(member)).split("token=")[1]

    resp = await client.post(
        "/survey",
        data={"token": token, "stay_in_touch": "yes", "contact_email": ""},
    )
    assert resp.status_code == 400
    assert "valid email" in resp.text


async def test_submit_no_clears_email_even_if_typed(client, db, make_alumnus):
    member = await make_alumnus(name="Sam Senior")
    token = (await _link_for(member)).split("token=")[1]

    resp = await client.post(
        "/survey",
        data={"token": token, "stay_in_touch": "no", "contact_email": "sam@example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    member_id = member.id  # captured before expire_all() below invalidates the object
    db.expire_all()
    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member_id))).scalars().first()
    assert survey.stay_in_touch is False
    assert survey.contact_email is None


async def test_submit_edits_dm_to_thanks(client, db, make_alumnus, fake_slack):
    member = await make_alumnus(name="Sam Senior", slack_user_id="U0SAM")
    # Give the survey a slack_message_ts, as intake would have — committed, since the
    # POST below re-queries it in a fresh session.
    member.survey.slack_message_ts = "1000.0001"
    await db.commit()

    token = (await _link_for(member)).split("token=")[1]
    resp = await client.post(
        "/survey", data={"token": token, "stay_in_touch": "no"}, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(fake_slack.updates) == 1
    assert "Thanks" in fake_slack.updates[0]["text"]


async def test_thanks_page_renders(client, make_alumnus):
    member = await make_alumnus(name="Sam Senior")
    token = (await _link_for(member)).split("token=")[1]
    resp = await client.get("/survey/thanks", params={"token": token})
    assert resp.status_code == 200
    assert "Thanks, Sam" in resp.text
