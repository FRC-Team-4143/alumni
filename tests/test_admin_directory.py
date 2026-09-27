"""/admin/alumni — the directory, detail page, resend button, CSV export, and answer
edits. Auth gating itself is covered separately in test_auth.py."""
from sqlalchemy import select

from app.models import AlumniSurvey


async def test_directory_lists_alumni(client, make_alumnus, admin_cookie):
    await make_alumnus(name="Ada Alum", graduation_year=2025)
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get("/admin/alumni")
    assert resp.status_code == 200
    assert "Ada Alum" in resp.text


async def test_directory_shows_empty_state(client, admin_cookie):
    client.cookies.set("mw_sso", admin_cookie)
    resp = await client.get("/admin/alumni")
    assert resp.status_code == 200
    assert "No alumni yet" in resp.text


async def test_detail_page_shows_survey_status(client, make_alumnus, admin_cookie):
    member = await make_alumnus(name="Ada Alum")
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get(f"/admin/alumni/{member.id}")
    assert resp.status_code == 200
    assert "Ada Alum" in resp.text
    assert "Pending" in resp.text


async def test_resend_survey_bumps_count_and_sends_dm(client, db, make_alumnus, admin_cookie, fake_slack):
    member = await make_alumnus(name="Ada Alum", slack_user_id="U0ADA")
    client.cookies.set("mw_sso", admin_cookie)

    member_id = member.id
    resp = await client.post(f"/admin/alumni/{member_id}/resend-survey", follow_redirects=False)
    assert resp.status_code == 303
    assert len(fake_slack.dms) == 1

    db.expire_all()
    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member_id))).scalars().first()
    assert survey.resent_count == 1
    assert survey.last_resent_at is not None


async def test_resend_without_slack_id_is_refused(client, make_alumnus, admin_cookie, fake_slack):
    from urllib.parse import unquote

    member = await make_alumnus(name="No Slack Alum", slack_user_id=None)
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.post(f"/admin/alumni/{member.id}/resend-survey", follow_redirects=False)
    assert resp.status_code == 303
    assert "Can't resend" in unquote(resp.headers["location"])
    assert fake_slack.dms == []


async def test_admin_can_edit_survey_answers(client, db, make_alumnus, admin_cookie):
    member = await make_alumnus(name="Ada Alum")
    client.cookies.set("mw_sso", admin_cookie)

    member_id = member.id
    resp = await client.post(
        f"/admin/alumni/{member_id}/survey",
        data={
            "destination": "Acme Corp", "field_of_study": "Engineer",
            "stay_in_touch": "yes", "contact_email": "ada@example.com",
            "linkedin_url": "https://linkedin.com/in/ada", "current_city": "Springfield",
            "willing_to_mentor": "yes", "admin_notes": "Met at reunion",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db.expire_all()
    survey = (await db.execute(select(AlumniSurvey).where(AlumniSurvey.member_id == member_id))).scalars().first()
    assert survey.destination == "Acme Corp"
    assert survey.linkedin_url == "https://linkedin.com/in/ada"
    assert survey.current_city == "Springfield"
    assert survey.willing_to_mentor is True
    assert survey.admin_notes == "Met at reunion"
    # An admin edit alone does not flip status to completed — only the alum's own
    # submission via /survey does that.
    assert survey.status.value == "sent"


async def test_export_csv_includes_survey_answers(client, make_alumnus, admin_cookie):
    await make_alumnus(name="Ada Alum", graduation_year=2025)
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get("/admin/alumni/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "Ada Alum" in resp.text


async def test_newsletter_csv_includes_only_opted_in_with_email(client, db, make_alumnus, admin_cookie):
    from app.config import settings
    settings.survey_link_secret = "test-survey-secret"

    opted_in = await make_alumnus(name="Opted In", graduation_year=2025)
    opted_in.survey.stay_in_touch = True
    opted_in.survey.contact_email = "opted-in@example.com"

    said_no = await make_alumnus(name="Said No", graduation_year=2025, slack_user_id="U0NO")
    said_no.survey.stay_in_touch = False
    said_no.survey.contact_email = "said-no@example.com"  # has an email but declined

    no_email = await make_alumnus(name="No Email", graduation_year=2025, slack_user_id="U0NOEMAIL")
    no_email.survey.stay_in_touch = True  # opted in, but left no email to reach them at

    await db.commit()
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get("/admin/alumni/newsletter.csv")
    assert resp.status_code == 200
    assert "Opted In" in resp.text
    assert "opted-in@example.com" in resp.text
    assert "/survey?token=" in resp.text  # the update_link column
    assert "Said No" not in resp.text
    assert "No Email" not in resp.text


async def test_detail_page_shows_update_link(client, make_alumnus, admin_cookie):
    from app.config import settings
    settings.survey_link_secret = "test-survey-secret"

    member = await make_alumnus(name="Ada Alum")
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get(f"/admin/alumni/{member.id}")
    assert resp.status_code == 200
    assert "/survey?token=" in resp.text


async def test_detail_page_warns_when_opted_out(client, db, make_alumnus, admin_cookie):
    from app.config import settings
    settings.survey_link_secret = "test-survey-secret"

    member = await make_alumnus(name="Ada Alum")
    member.survey.stay_in_touch = False
    await db.commit()
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.get(f"/admin/alumni/{member.id}")
    assert resp.status_code == 200
    assert "said" in resp.text.lower() and "no" in resp.text.lower()
