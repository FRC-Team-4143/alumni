import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db
from app.routers import admin, graduates, newsletters, portal, slack
from app.services.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown()


app = FastAPI(title="Alumni", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

# StaticFiles requires the directory to exist at mount time, which runs before lifespan
# — see Merces's main.py for the identical pattern this was copied from.
os.makedirs(settings.newsletter_dir, exist_ok=True)
app.mount("/newsletter-files", StaticFiles(directory=settings.newsletter_dir), name="newsletter_files")

app.include_router(newsletters.router)
app.include_router(portal.router)
app.include_router(admin.router)
app.include_router(slack.router)
app.include_router(graduates.router)


@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    """Unauthenticated liveness probe — Legion's admin dashboard polls this to show
    Alumni in its System Status panel."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — any DB error means "not healthy"
        return JSONResponse({"status": "error", "app": "alumni"}, status_code=503)
    return {"status": "ok", "app": "alumni"}
