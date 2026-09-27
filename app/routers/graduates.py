"""
Inbound graduation-event intake — the one place another app calls INTO Alumni, rather
than the usual pull. Legion's Yearly Grade Increase action posts here, once, the moment
it archives a senior; see Legion's `services/alumni_push.py` for the sender side and
`services/intake.py` here for what happens with it.
"""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.intake import intake_graduate

router = APIRouter()


async def require_legion_key(x_api_key: str = Header(default="")) -> None:
    """Dependency: reject requests without a configured, matching API key. The reverse
    of Legion's own `require_api_key` — here Legion is the caller, so it presents the
    key instead of checking it."""
    if not settings.legion_push_api_key:
        raise HTTPException(status_code=503, detail="Intake is not configured (no push key set).")
    if not hmac.compare_digest(x_api_key, settings.legion_push_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


class GraduatePayload(BaseModel):
    name: str
    graduation_year: int
    member_code: str | None = None
    slack_user_id: str | None = None
    team_number: int | None = None
    subteam_label: str | None = None


@router.post("/api/graduates", dependencies=[Depends(require_legion_key)])
async def receive_graduate(payload: GraduatePayload, db: AsyncSession = Depends(get_db)):
    member = await intake_graduate(db, **payload.model_dump())
    return {"status": "ok", "member_id": member.id}
