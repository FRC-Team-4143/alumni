"""
The public front page. Alumni.marswars.org is meant to double as a place anyone — not
just staff, not just an alum with a personal link — can read the team's latest
newsletter, rather than a domain with no home page at all (previously `/` had no route;
only `/survey` (bearer-token) and `/admin` (Legion SSO) existed). Read-only: uploading
and deleting newsletters lives in admin.py's "Newsletters" section, gated the same as
the Alumni Directory (alumni-admin or alumni-manager).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.newsletter_pdfs import get_newsletter, latest_newsletter, list_newsletters
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    newsletter = await latest_newsletter(db)
    return templates.TemplateResponse(
        "public/home.html", {"request": request, "newsletter": newsletter, "active_page": "home"}
    )


@router.get("/newsletters", response_class=HTMLResponse)
async def newsletter_archive(request: Request, db: AsyncSession = Depends(get_db)):
    rows = await list_newsletters(db)
    return templates.TemplateResponse(
        "public/newsletters.html", {"request": request, "newsletters": rows, "active_page": "archive"}
    )


@router.get("/newsletters/{newsletter_id}", response_class=HTMLResponse)
async def newsletter_detail(newsletter_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    newsletter = await get_newsletter(db, newsletter_id)
    if newsletter is None:
        return RedirectResponse("/newsletters", status_code=303)
    return templates.TemplateResponse(
        "public/newsletter_detail.html",
        {"request": request, "newsletter": newsletter, "active_page": "archive"},
    )
