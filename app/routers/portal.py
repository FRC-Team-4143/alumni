"""
The public survey form — the one page in this app a graduate themselves ever sees.

Deliberately **not** gated by Legion SSO: a graduate is archived (is_active=False) in
Legion the instant they graduate, and Legion's magic links refuse to redeem for an
inactive member by design ("archiving someone kills every link already sitting in their
DMs"). So this page is reached by, and trusts, its own bearer token instead — see
services/survey.py for why and how that token is minted/verified. It's a low-stakes
form (where you're headed, not an admin panel), so a long-lived link is an acceptable
trade for "this has to keep working for someone Legion no longer authenticates at all."
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import AlumniSurvey, Member
from app.services.survey import mark_completed, read_survey_token, request_link_by_email
from app.templating import templates

router = APIRouter()


async def _survey_from_token(db: AsyncSession, token: str) -> Optional[tuple[AlumniSurvey, Member]]:
    survey_id = read_survey_token(token)
    if survey_id is None:
        return None
    survey = (
        await db.execute(
            select(AlumniSurvey)
            .options(selectinload(AlumniSurvey.member))
            .where(AlumniSurvey.id == survey_id)
        )
    ).scalars().first()
    if survey is None or survey.member is None:
        return None
    return survey, survey.member


@router.get("/survey", response_class=HTMLResponse)
async def survey_form(request: Request, token: str = "", db: AsyncSession = Depends(get_db)):
    resolved = await _survey_from_token(db, token)
    if resolved is None:
        return templates.TemplateResponse(
            "portal/link_invalid.html", {"request": request}, status_code=404
        )
    survey, member = resolved
    return templates.TemplateResponse(
        "portal/survey.html",
        {"request": request, "survey": survey, "member": member, "token": token, "error": None},
    )


@router.post("/survey", response_class=HTMLResponse)
async def survey_submit(
    request: Request,
    token: str = Form(""),
    destination: str = Form(""),
    field_of_study: str = Form(""),
    stay_in_touch: str = Form(""),
    contact_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    resolved = await _survey_from_token(db, token)
    if resolved is None:
        return templates.TemplateResponse(
            "portal/link_invalid.html", {"request": request}, status_code=404
        )
    survey, member = resolved

    stay = stay_in_touch == "yes"
    email = contact_email.strip()
    # Mirrors the old Slack modal's server-side rule: the email field is always shown
    # (nothing here can conditionally hide it based on the yes/no answer without a page
    # reload anyway, so there's no upside to trying) but is only required when they said
    # yes to staying in touch.
    if stay and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        return templates.TemplateResponse(
            "portal/survey.html",
            {
                "request": request, "survey": survey, "member": member, "token": token,
                "error": "Please enter a valid email so we can stay in touch.",
                "form": {
                    "destination": destination, "field_of_study": field_of_study,
                    "stay_in_touch": stay_in_touch, "contact_email": contact_email,
                },
            },
            status_code=400,
        )

    await mark_completed(
        survey, member,
        destination=destination.strip() or None,
        field_of_study=field_of_study.strip() or None,
        stay_in_touch=stay,
        contact_email=email if stay and email else None,
    )
    await db.commit()
    return RedirectResponse(f"/survey/thanks?token={token}", status_code=303)


@router.get("/survey/thanks", response_class=HTMLResponse)
async def survey_thanks(request: Request, token: str = "", db: AsyncSession = Depends(get_db)):
    resolved = await _survey_from_token(db, token)
    member = resolved[1] if resolved else None
    return templates.TemplateResponse(
        "portal/survey_thanks.html", {"request": request, "member": member}
    )


@router.get("/survey/find", response_class=HTMLResponse)
async def survey_find_form(request: Request):
    """A generic entry point for someone who has no personal link at all — e.g. they
    read a plain identical BCC blast, which (unlike a mail-merge send) has nowhere to
    put a per-recipient link. See services/survey.request_link_by_email."""
    return templates.TemplateResponse("portal/find.html", {"request": request})


@router.post("/survey/find", response_class=HTMLResponse)
async def survey_find_submit(
    request: Request, email: str = Form(""), db: AsyncSession = Depends(get_db)
):
    await request_link_by_email(db, email)
    # Same response regardless of whether anything actually matched or sent — see
    # request_link_by_email's docstring for why (no email enumeration).
    return templates.TemplateResponse("portal/find_sent.html", {"request": request})
