"""services/survey.read_survey_token — the update link's expiry semantics.
SURVEY_LINK_TTL <= 0 (the default) means the link never expires, since the same link
is meant to be reusable in a newsletter sent unpredictably far in the future."""
from app.config import settings
from app.services import survey as survey_mod
from app.services.survey import make_survey_link, read_survey_token


async def test_default_ttl_is_never_expires():
    assert settings.survey_link_ttl == 0


async def test_zero_ttl_passes_no_max_age_to_the_signer(monkeypatch):
    settings.survey_link_secret = "test-survey-secret"
    settings.survey_link_ttl = 0
    calls = []
    real_loads = survey_mod._link_signer.loads

    def _spy_loads(token, max_age=None):
        calls.append(max_age)
        return real_loads(token, max_age=max_age)

    monkeypatch.setattr(survey_mod._link_signer, "loads", _spy_loads)

    link = make_survey_link(1)
    token = link.split("token=")[1]
    assert read_survey_token(token) == 1
    assert calls == [None]


async def test_positive_ttl_is_passed_through_to_the_signer(monkeypatch):
    settings.survey_link_secret = "test-survey-secret"
    settings.survey_link_ttl = 3600
    calls = []
    real_loads = survey_mod._link_signer.loads

    def _spy_loads(token, max_age=None):
        calls.append(max_age)
        return real_loads(token, max_age=max_age)

    monkeypatch.setattr(survey_mod._link_signer, "loads", _spy_loads)

    link = make_survey_link(1)
    token = link.split("token=")[1]
    assert read_survey_token(token) == 1
    assert calls == [3600]


async def test_expired_positive_ttl_link_is_rejected(monkeypatch):
    """A token minted long enough ago that a *positive* TTL has elapsed is refused —
    confirms a deploy that opts into expiry actually gets it. itsdangerous reads the
    `time` module's `time.time()` at call time (a plain `import time`, not `from time
    import time`), so patching the module attribute here reaches it."""
    settings.survey_link_secret = "test-survey-secret"
    settings.survey_link_ttl = 10  # 10 seconds

    link = make_survey_link(1)
    token = link.split("token=")[1]

    import time
    real_time = time.time  # capture before patching, to advance the "clock" from now
    monkeypatch.setattr(time, "time", lambda: real_time() + 20)
    assert read_survey_token(token) is None
