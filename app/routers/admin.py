"""
Admin / manager UI — the alumni directory, survey answers, and the usual settings/
backup/audit trio every sibling app carries.

Gated by Legion SSO: `alumni-admin` (full) or `alumni-manager` (directory + resend —
not settings, backup, or audit). Note what's *not* here: filling out a survey. That's
the public `/survey` page (`routers/portal.py`), reached by its own bearer link rather
than any Legion group — see that router's docstring for why.

Every mutation records an audit row (services/audit.py) in the same transaction as the
change it describes.
"""
import csv
import io
import os
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import AlumniSurvey, AuditLog, Member, Newsletter, SurveyStatus
from app.services import audit
from app.services.backup import is_sqlite, list_backups, nightly_backup, stage_restore
from app.services.newsletter import newsletter_recipients
from app.services.newsletter_pdfs import (
    InvalidNewsletterError, delete_newsletter_pdf, get_newsletter, list_newsletters,
    save_newsletter_pdf,
)
from app.services.survey import make_survey_link, resend_survey_dm
from app.services.sso import (
    is_admin, is_link_identity, is_staff, logout_url, make_authorize_url, sso_identity,
    stepup_url,
)
from app.templating import templates

router = APIRouter(prefix="/admin")

_ADMIN_GROUP = "alumni-admin"
_MANAGER_GROUP = "alumni-manager"


# ── Auth guards ──────────────────────────────────────────────────────────────────

def _manager_allowed(path: str) -> bool:
    """The sections an `alumni-manager` may reach: the dashboard, directory, a member's
    detail page (including resending their survey), and newsletter management. Admin-
    only, matching every sibling app: settings, backup, audit."""
    p = path.rstrip("/")
    return (
        p == "/admin" or p == "/admin/alumni" or p.startswith("/admin/alumni/")
        or p == "/admin/newsletters" or p.startswith("/admin/newsletters/")
    )


_SECTION_LABELS = [
    ("/admin/alumni", "Alumni Directory"),
    ("/admin/newsletters", "Newsletters"),
    ("/admin/audit", "Audit Log"),
    ("/admin/backup", "Backup"),
    ("/admin/settings", "Settings"),
    ("/admin", "Dashboard"),
]


def _section_label(path: str) -> str:
    for prefix, label in _SECTION_LABELS:
        if path.startswith(prefix):
            return label
    return "this page"


def _require_auth(request: Request):
    """Gate every admin route via Legion SSO. Returns None when allowed, otherwise the
    response to return instead. See Virtus's admin.py for the identical pattern this
    was copied from."""
    identity = sso_identity(request)
    if identity is None:
        return RedirectResponse(make_authorize_url(request), status_code=303)
    if is_link_identity(identity):
        return RedirectResponse(
            stepup_url(request, return_to=request.url.path), status_code=303
        )
    groups = set(identity.get("groups") or [])
    if _ADMIN_GROUP in groups:
        return None
    if _MANAGER_GROUP in groups and _manager_allowed(request.url.path):
        return None
    return templates.TemplateResponse(
        "admin/forbidden.html",
        {"request": request, "name": identity.get("name", ""), "section": _section_label(request.url.path)},
        status_code=403,
    )


def _require_admin(request: Request):
    """Same as `_require_auth`, but full-admin only regardless of the path allowlist."""
    if redirect := _require_auth(request):
        return redirect
    if not is_admin(sso_identity(request)):
        return templates.TemplateResponse(
            "admin/forbidden.html",
            {"request": request, "section": _section_label(request.url.path)},
            status_code=403,
        )
    return None


# ── Shared helpers ───────────────────────────────────────────────────────────────

def _redirect(path: str, message: str) -> RedirectResponse:
    """Post/redirect/get with a flash message, percent-encoded whole — a raw emoji or
    curly quote in a `Location` header isn't latin-1 encodable and would crash the
    response."""
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}message={quote(message, safe='')}", status_code=303)


@router.get("/logout")
async def admin_logout(request: Request):
    return RedirectResponse(logout_url(request, return_to="/admin"), status_code=303)


# ── Dashboard ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect

    total = (await db.execute(select(func.count(Member.id)))).scalar_one()
    completed = (
        await db.execute(select(func.count(AlumniSurvey.id)).where(AlumniSurvey.status == SurveyStatus.completed))
    ).scalar_one()
    pending = (
        await db.execute(select(func.count(AlumniSurvey.id)).where(AlumniSurvey.status == SurveyStatus.sent))
    ).scalar_one()
    stay_in_touch = (
        await db.execute(select(func.count(AlumniSurvey.id)).where(AlumniSurvey.stay_in_touch.is_(True)))
    ).scalar_one()
    by_year = (
        await db.execute(
            select(Member.graduation_year, func.count(Member.id))
            .where(Member.graduation_year.is_not(None))
            .group_by(Member.graduation_year)
            .order_by(Member.graduation_year.desc())
        )
    ).all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request, "active_page": "dashboard",
            "total": total, "completed": completed, "pending": pending,
            "stay_in_touch": stay_in_touch, "by_year": by_year,
        },
    )


# ── Alumni directory (admin + manager) ──────────────────────────────────────────

@router.get("/alumni", response_class=HTMLResponse)
async def alumni_list(request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect

    members = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).order_by(Member.name)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "admin/alumni_list.html",
        {
            "request": request, "active_page": "alumni", "members": members,
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@router.get("/alumni/export.csv")
async def alumni_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect

    members = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).order_by(Member.name)
        )
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "name", "graduation_year", "team_number", "subteam", "slack_user_id",
        "survey_status", "destination", "field_of_study", "stay_in_touch",
        "contact_email", "linkedin_url", "current_city", "willing_to_mentor",
    ])
    for m in members:
        s = m.survey
        writer.writerow([
            m.name, m.graduation_year or "", m.team_number or "", m.subteam_label or "",
            m.slack_user_id or "",
            s.status.value if s else "", s.destination if s else "",
            s.field_of_study if s else "", s.stay_in_touch if s and s.stay_in_touch is not None else "",
            s.contact_email if s else "", s.linkedin_url if s else "",
            s.current_city if s else "", s.willing_to_mentor if s and s.willing_to_mentor is not None else "",
        ])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alumni.csv"},
    )


@router.get("/alumni/newsletter.csv")
async def alumni_newsletter_csv(request: Request, db: AsyncSession = Depends(get_db)):
    """The recipient list for a newsletter/update email an admin sends through
    whatever tool they actually use (Mailchimp, a mail-merge, plain BCC) — there's no
    in-app sender here on purpose (see CLAUDE.md, "The newsletter link is BYO email").
    Only people who said Yes to staying in touch AND left a contact email — no email,
    no way to reach them this way; no "yes", no invitation to be re-contacted. Each row
    carries that person's own durable `update_link` (survey_link_ttl=0 by default, so
    it's good for as long as this file sits around before someone uses it) — the same
    link the graduation DM sent, since resubmitting it just updates their answers."""
    if redirect := _require_auth(request):
        return redirect

    rows = await newsletter_recipients(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "contact_email", "graduation_year", "update_link"])
    for r in rows:
        writer.writerow([r["name"], r["contact_email"], r["graduation_year"], r["update_link"]])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alumni-newsletter-list.csv"},
    )


@router.get("/alumni/{member_id}", response_class=HTMLResponse)
async def alumnus_detail(member_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect
    member = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).where(Member.id == member_id)
        )
    ).scalars().first()
    if member is None:
        return RedirectResponse("/admin/alumni", status_code=303)
    return templates.TemplateResponse(
        "admin/alumnus.html",
        {
            "request": request, "active_page": "alumni", "member": member,
            "update_link": make_survey_link(member.survey.id) if member.survey else None,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/alumni/{member_id}/survey")
async def alumnus_edit_survey(
    member_id: int,
    request: Request,
    destination: Optional[str] = Form(None),
    field_of_study: Optional[str] = Form(None),
    stay_in_touch: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    current_city: Optional[str] = Form(None),
    willing_to_mentor: Optional[str] = Form(None),
    admin_notes: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Admin correction of a graduate's answers — a phone-call update, a typo fix, or
    filling in what an admin learned some other way (LinkedIn, a reunion). This does
    NOT flip status to completed on its own if it was still `sent` — that only happens
    when the graduate themselves submits `/survey` (see routers/portal.py); an admin
    editing fields here is a correction, not a stand-in submission."""
    if redirect := _require_auth(request):
        return redirect
    member = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).where(Member.id == member_id)
        )
    ).scalars().first()
    if member is None or member.survey is None:
        return RedirectResponse("/admin/alumni", status_code=303)

    survey = member.survey
    survey.destination = (destination or "").strip() or None
    survey.field_of_study = (field_of_study or "").strip() or None
    survey.stay_in_touch = stay_in_touch == "yes" if stay_in_touch in ("yes", "no") else survey.stay_in_touch
    survey.contact_email = (contact_email or "").strip() or None
    survey.linkedin_url = (linkedin_url or "").strip() or None
    survey.current_city = (current_city or "").strip() or None
    survey.willing_to_mentor = (
        willing_to_mentor == "yes" if willing_to_mentor in ("yes", "no") else survey.willing_to_mentor
    )
    survey.admin_notes = (admin_notes or "").strip() or None

    await audit.record(
        db, request, "member.edit_survey", f"Edited survey answers for {member.name}",
        entity_type="member", entity_id=member.id,
    )
    await db.commit()
    return _redirect(f"/admin/alumni/{member_id}", "Saved.")


@router.post("/alumni/{member_id}/resend-survey")
async def alumnus_resend_survey(member_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect
    member = (
        await db.execute(
            select(Member).options(selectinload(Member.survey)).where(Member.id == member_id)
        )
    ).scalars().first()
    if member is None or member.survey is None:
        return RedirectResponse("/admin/alumni", status_code=303)

    if not member.slack_user_id:
        return _redirect(f"/admin/alumni/{member_id}", "Can't resend — no Slack ID on file for this person.")

    ok = await resend_survey_dm(member, member.survey)
    await audit.record(
        db, request, "member.resend_survey",
        f"{'Resent' if ok else 'Attempted to resend'} the survey to {member.name}",
        entity_type="member", entity_id=member.id,
    )
    await db.commit()
    msg = "Survey resent." if ok else "Resend failed — see logs (Slack unreachable or not configured?)."
    return _redirect(f"/admin/alumni/{member_id}", msg)


# ── Newsletters (admin + manager) ────────────────────────────────────────────────

@router.get("/newsletters", response_class=HTMLResponse)
async def newsletters_page(request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect
    rows = await list_newsletters(db)
    return templates.TemplateResponse(
        "admin/newsletters.html",
        {
            "request": request, "active_page": "newsletters", "newsletters": rows,
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@router.post("/newsletters")
async def newsletters_upload(
    request: Request,
    title: str = Form(...),
    published_date: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if redirect := _require_auth(request):
        return redirect

    try:
        pub_date = date.fromisoformat(published_date)
    except ValueError:
        return _redirect("/admin/newsletters", "Invalid issue date.")
    if not title.strip():
        return _redirect("/admin/newsletters", "Title is required.")

    try:
        filename = await save_newsletter_pdf(file)
    except InvalidNewsletterError as e:
        return _redirect("/admin/newsletters", str(e))

    identity = sso_identity(request) or {}
    newsletter = Newsletter(
        title=title.strip(), filename=filename, published_date=pub_date,
        uploaded_by=identity.get("name") or identity.get("username") or "Unknown",
    )
    db.add(newsletter)
    await db.flush()
    await audit.record(
        db, request, "newsletter.upload", f"Uploaded newsletter \"{newsletter.title}\"",
        entity_type="newsletter", entity_id=newsletter.id,
    )
    await db.commit()
    return _redirect("/admin/newsletters", "Newsletter uploaded.")


@router.post("/newsletters/{newsletter_id}/delete")
async def newsletters_delete(newsletter_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_auth(request):
        return redirect
    newsletter = await get_newsletter(db, newsletter_id)
    if newsletter is None:
        return _redirect("/admin/newsletters", "Not found.")

    delete_newsletter_pdf(newsletter.filename)
    title = newsletter.title
    await db.delete(newsletter)
    await audit.record(
        db, request, "newsletter.delete", f"Deleted newsletter \"{title}\"",
        entity_type="newsletter", entity_id=newsletter_id,
    )
    await db.commit()
    return _redirect("/admin/newsletters", "Newsletter deleted.")


# ── Audit log (admin only) ───────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request, db: AsyncSession = Depends(get_db)):
    if redirect := _require_admin(request):
        return redirect
    rows = (
        await db.execute(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(200))
    ).scalars().all()
    return templates.TemplateResponse(
        "admin/audit.html", {"request": request, "active_page": "audit", "rows": rows}
    )


# ── Backup (admin only) ──────────────────────────────────────────────────────────

@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request):
    if redirect := _require_admin(request):
        return redirect
    return templates.TemplateResponse(
        "admin/backup.html",
        {"request": request, "active_page": "backup", "backups": list_backups(),
         "is_sqlite": is_sqlite(), "message": request.query_params.get("message")},
    )


@router.post("/backup/snapshot")
async def backup_snapshot(request: Request):
    if redirect := _require_admin(request):
        return redirect
    try:
        nightly_backup()
        msg = "Snapshot created"
    except Exception:
        msg = "Snapshot failed"
    return _redirect("/admin/backup", msg)


@router.post("/backup/restore")
async def backup_restore(request: Request, file: UploadFile = File(...)):
    if redirect := _require_admin(request):
        return redirect
    ok, message = stage_restore(await file.read())
    return _redirect("/admin/backup", message)


@router.get("/backup/download/{name}")
async def backup_download(name: str, request: Request):
    if redirect := _require_admin(request):
        return redirect
    # Guard against path traversal — only a bare filename in the backup dir.
    safe = os.path.basename(name)
    path = os.path.join(settings.backup_dir, safe)
    if safe != name or not os.path.isfile(path):
        return _redirect("/admin/backup", "Not found")
    return FileResponse(path, filename=safe, media_type="application/octet-stream")


# ── Settings (admin only, read-only view) ────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if redirect := _require_admin(request):
        return redirect
    return templates.TemplateResponse(
        "admin/settings.html",
        {"request": request, "active_page": "settings", "settings": settings},
    )
