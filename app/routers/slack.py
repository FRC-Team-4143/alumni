"""
Slack routes.

  POST /slack/command — the `/alumni` slash command: a one-tap link to your own survey.

Verified by the Slack signing secret, same HMAC scheme every sibling app uses. There are
**no interactive components** (no buttons, no modals) — the survey is a plain web form
reached by its own bearer link (see services/survey.py) — so Legion needs no
`slack_dispatch.py` entry for Alumni, matching Virtus/Merces.
"""
import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import Member
from app.services.survey import make_survey_link

router = APIRouter(prefix="/slack")


async def _verify_slack_signature(request: Request) -> bytes:
    if not settings.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack integration is not configured (no signing secret set).")

    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    try:
        if abs(time.time() - float(timestamp)) > 300:
            raise HTTPException(status_code=403, detail="Request too old")
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid timestamp")

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")
    return body


@router.post("/command")
async def slack_command(request: Request, db: AsyncSession = Depends(get_db)):
    await _verify_slack_signature(request)

    form = await request.form()
    command = form.get("command", "")
    user_id = form.get("user_id", "")

    if command != "/alumni":
        return JSONResponse({"response_type": "ephemeral", "text": "Unknown command."})

    member = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).where(Member.slack_user_id == user_id)
        )
    ).scalars().first()

    if member is None or member.survey is None:
        text = "🎓 Nothing here for you yet — Alumni only has a record once you've graduated."
        return JSONResponse({"response_type": "ephemeral", "text": text})

    link = make_survey_link(member.survey.id)
    if not link:
        text = "The survey link isn't available right now — ask an admin to check the Alumni app's configuration."
        return JSONResponse({"response_type": "ephemeral", "text": text})

    verb = "Review or update" if member.survey.status.value == "completed" else "Fill out"
    text = f"🎓 {verb} your survey: <{link}|open it here>"
    return JSONResponse({
        "response_type": "ephemeral",
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    })
