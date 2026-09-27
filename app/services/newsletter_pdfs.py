"""
Newsletter PDF storage — backs both the public front page (routers/newsletters.py) and
its admin management (admin.py's "Newsletters" section). Files are saved under
settings.newsletter_dir with a random filename (never the client-supplied one — avoids
path traversal and collisions) and served back out via the /newsletter-files static
mount registered in main.py. Mirrors Merces's services/uploads.py pattern exactly,
swapping image validation for PDF.
"""
import os
import secrets

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Newsletter


class InvalidNewsletterError(RuntimeError):
    """Raised when an upload fails type/size validation."""


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


async def save_newsletter_pdf(file: UploadFile) -> str:
    """Validate and persist an uploaded PDF. Returns the stored filename (join with
    settings.newsletter_dir, or use the /newsletter-files/<filename> URL, to reach it)."""
    if _ext(file.filename or "") != ".pdf" or (file.content_type or "") != "application/pdf":
        raise InvalidNewsletterError("That file doesn't look like a PDF.")

    content = await file.read()
    max_bytes = settings.max_newsletter_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise InvalidNewsletterError(f"File is too large (max {settings.max_newsletter_mb} MB).")
    if not content:
        raise InvalidNewsletterError("That file is empty.")

    os.makedirs(settings.newsletter_dir, exist_ok=True)
    filename = f"{secrets.token_hex(16)}.pdf"
    with open(os.path.join(settings.newsletter_dir, filename), "wb") as f:
        f.write(content)
    return filename


def delete_newsletter_pdf(filename: str) -> None:
    """Remove a previously saved PDF. No-op if it's already gone."""
    if not filename:
        return
    try:
        os.remove(os.path.join(settings.newsletter_dir, filename))
    except FileNotFoundError:
        pass


async def latest_newsletter(db: AsyncSession) -> Newsletter | None:
    """The one the public front page shows — most recent by `published_date` (an
    admin-set issue date, not upload time; see the model's docstring)."""
    return (
        await db.execute(
            select(Newsletter)
            .order_by(Newsletter.published_date.desc(), Newsletter.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def list_newsletters(db: AsyncSession) -> list[Newsletter]:
    """Every issue, most recent first — the public archive and the admin table."""
    return (
        await db.execute(
            select(Newsletter).order_by(Newsletter.published_date.desc(), Newsletter.created_at.desc())
        )
    ).scalars().all()


async def get_newsletter(db: AsyncSession, newsletter_id: int) -> Newsletter | None:
    return (
        await db.execute(select(Newsletter).where(Newsletter.id == newsletter_id))
    ).scalars().first()
