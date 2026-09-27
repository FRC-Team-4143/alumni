"""
Test fixtures — a fresh in-memory SQLite database per test (a real DB, never mocked), an
httpx client wired to it, an mw_sso cookie minter (admin side), a survey-link minter
(the graduate-facing side), and a fake Slack recorder so no outbound Slack call ever
leaves the process.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AlumniSurvey, Member, MemberKind, SurveyStatus


# ── Settings isolation ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv():
    """The suite must not be sensitive to whatever's in the developer's `.env`. Reset
    every setting to its class default before each test and restore afterwards;
    `sso_secret`/`survey_link_secret` get fixed test values since a blank signing key
    would make every cookie/link indistinguishable. Copied from the sibling apps'
    conftest, which hit this first."""
    from app.config import Settings

    defaults = Settings(
        _env_file=None, sso_secret="test-sso-secret", survey_link_secret="test-survey-secret",
    )
    original = {name: getattr(settings, name) for name in Settings.model_fields}
    for name in Settings.model_fields:
        setattr(settings, name, getattr(defaults, name))
    _rebuild_signers()
    yield
    for name, value in original.items():
        setattr(settings, name, value)
    _rebuild_signers()


def _rebuild_signers() -> None:
    """`sso.py` and `survey.py` build their `URLSafeTimedSerializer`s at import time from
    the matching setting. Swapping the setting alone would leave them signing with the
    old key, so every cookie/link this suite mints would fail verification — rebuild
    them whenever the fixture changes the secret."""
    from app.services import sso as sso_service, survey as survey_service

    sso_service._sso_signer = URLSafeTimedSerializer(settings.sso_secret, salt="mw-sso")
    survey_service._link_signer = URLSafeTimedSerializer(
        settings.survey_link_secret, salt="alumni-survey-link"
    )


# ── Database ───────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


# ── Fake Slack ───────────────────────────────────────────────────────────────────

class FakeSlack:
    """Records every DM / channel post instead of hitting Slack."""
    def __init__(self):
        self.dms: list[dict] = []
        self.channel_posts: list[dict] = []
        self.updates: list[dict] = []

    async def conversations_open(self, users):
        return {"channel": {"id": f"D{users}"}}

    async def chat_postMessage(self, channel, text, blocks=None):
        record = {"channel": channel, "text": text, "blocks": blocks}
        (self.dms if channel.startswith("D") else self.channel_posts).append(record)
        return {"ts": "1.0"}

    async def chat_update(self, channel, ts, text, blocks=None):
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ts": ts}


@pytest.fixture(autouse=True)
def fake_slack():
    """Install a fake Slack client for every test; updates stay enabled so sends are
    actually attempted (and recorded)."""
    from app.services import slack_client
    fake = FakeSlack()
    slack_client._client = fake
    settings.updates_enabled = True
    yield fake
    slack_client._client = None


# ── Fake email ───────────────────────────────────────────────────────────────────

class FakeEmail:
    """Records every outbound email instead of hitting SMTP."""
    def __init__(self):
        self.sent: list[dict] = []

    async def __call__(self, to_address, subject, body):
        self.sent.append({"to": to_address, "subject": subject, "body": body})
        return True


@pytest.fixture(autouse=True)
def fake_email(monkeypatch):
    """Patch services/survey.py's imported `send_email` name directly (there's no
    long-lived client object to swap, unlike Slack's AsyncWebClient)."""
    from app.services import survey as survey_service
    fake = FakeEmail()
    monkeypatch.setattr(survey_service, "send_email", fake)
    yield fake


# ── HTTP client ────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(session_factory, db):
    async def _get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ── SSO cookie (admin side) ─────────────────────────────────────────────────────────

def make_sso_cookie(
    *, member_code="m0000001", name="Test Staff", role="mentor",
    groups=None, slack_user_id=None, team_number=4143, via=None,
):
    signer = URLSafeTimedSerializer(settings.sso_secret, salt="mw-sso")
    claims = {
        "member_code": member_code,
        "username": name.lower().replace(" ", "."),
        "name": name,
        "role": role,
        "team_number": team_number,
        "groups": groups or [],
        "slack_user_id": slack_user_id,
    }
    if via:
        claims["via"] = via
    return signer.dumps(claims)


@pytest.fixture
def admin_cookie():
    return make_sso_cookie(
        member_code="admin001", name="Ada Admin", role="mentor",
        groups=["alumni-admin"], slack_user_id="UADMIN",
    )


@pytest.fixture
def manager_cookie():
    return make_sso_cookie(
        member_code="mgr00001", name="Mel Manager", role="mentor",
        groups=["alumni-manager"], slack_user_id="UMGR",
    )


# ── Convenience factories ────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def make_member(db):
    """Bare Member row, no survey."""
    counter = {"n": 0}

    async def _make(name="Grad Uate", *, kind=MemberKind.student, slack_user_id=None,
                    member_code=None, team_number=4143, subteam_label=None,
                    graduation_year=2026):
        counter["n"] += 1
        m = Member(
            member_code=member_code if member_code is not None else f"code{counter['n']:04d}",
            name=name, kind=kind, slack_user_id=slack_user_id,
            team_number=team_number, subteam_label=subteam_label,
            graduation_year=graduation_year,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m

    return _make


@pytest_asyncio.fixture
async def make_alumnus(make_member, db):
    """A Member + AlumniSurvey pair, matching what services/intake.py creates."""
    async def _make(name="Grad Uate", *, status=SurveyStatus.sent, slack_user_id="U0GRAD", **kwargs):
        member = await make_member(name=name, slack_user_id=slack_user_id, **kwargs)
        survey = AlumniSurvey(member_id=member.id, status=status)
        db.add(survey)
        await db.commit()
        # Explicitly refresh the relationship (not just columns) so `member.survey` is
        # populated in-memory — a bare attribute access later, outside any `await
        # db.execute(...)`, would otherwise try to lazy-load and raise MissingGreenlet.
        await db.refresh(member, attribute_names=["survey"])
        return member

    return _make
