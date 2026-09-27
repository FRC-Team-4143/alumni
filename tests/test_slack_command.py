"""POST /slack/command — the /alumni slash command. No interactive components exist in
this app (see routers/slack.py's docstring), so this is the only inbound Slack surface."""
import hashlib
import hmac
import time

from app.config import settings


def _signed_headers(body: str, secret: str) -> dict:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _post_command(client, user_id: str, secret="test-signing-secret"):
    body = f"command=%2Falumni&user_id={user_id}"
    return await client.post("/slack/command", content=body, headers=_signed_headers(body, secret))


async def test_unconfigured_signing_secret_is_503(client):
    resp = await _post_command(client, "U0UNKNOWN")
    assert resp.status_code == 503


async def test_bad_signature_is_403(client):
    settings.slack_signing_secret = "test-signing-secret"
    resp = await _post_command(client, "U0UNKNOWN", secret="wrong-secret")
    assert resp.status_code == 403


async def test_unknown_slack_id_gets_friendly_reply(client):
    settings.slack_signing_secret = "test-signing-secret"
    resp = await _post_command(client, "U0UNKNOWN")
    assert resp.status_code == 200
    assert "Nothing here for you yet" in resp.json()["text"]


async def test_known_alum_gets_survey_link(client, make_alumnus):
    settings.slack_signing_secret = "test-signing-secret"
    settings.survey_link_secret = "test-survey-secret"
    await make_alumnus(name="Sam Senior", slack_user_id="U0SAM")

    resp = await _post_command(client, "U0SAM")
    assert resp.status_code == 200
    text = resp.json()["text"]
    assert "Fill out your survey" in text
    assert "/survey?token=" in text


async def test_completed_survey_gets_review_wording(client, make_alumnus):
    from app.models import SurveyStatus
    settings.slack_signing_secret = "test-signing-secret"
    settings.survey_link_secret = "test-survey-secret"
    await make_alumnus(name="Sam Senior", slack_user_id="U0SAM", status=SurveyStatus.completed)

    resp = await _post_command(client, "U0SAM")
    assert resp.status_code == 200
    assert "Review or update your survey" in resp.json()["text"]
