from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": tolerate leftover keys in a deployed .env instead of failing to boot.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    # Legion SSO — /admin and /survey are gated by the shared `mw_sso` cookie. Alumni
    # only *verifies* the cookie (Legion mints it); `sso_secret` must equal Legion's
    # SSO_SECRET. There is no local admin password — the first admin is granted
    # `alumni-admin` in Legion's /admin/groups.
    sso_secret: str = ""
    sso_session_ttl: int = 43200  # 12h; must match Legion's cookie max_age
    sso_cookie_domain: str = ""   # e.g. ".marswars.org" so one login spans subdomains

    # Legion's own base URL, needed only for the admin SSO round trip (/sso/authorize,
    # /sso/stepup — see services/sso.py) — NOT for a roster pull, and NOT for the survey
    # link a graduate gets. Unlike every sibling app, Alumni runs no legion_sync job:
    # Legion holds no "alumni" state to poll for anymore (see Legion's CLAUDE.md,
    # "Members are unified"). A person arrives here exactly once, pushed by Legion's
    # Yearly Grade Increase action — see LEGION_PUSH_API_KEY below. And Legion's own
    # magic links can't reach a graduate at all: they're archived (is_active=False) the
    # instant they graduate, and Legion's `/sso/link` deliberately re-checks is_active
    # on every redemption ("archiving someone kills every link already sitting in their
    # DMs") — so the survey link below is Alumni's own bearer token, not a Legion one.
    legion_base_url: str = ""  # e.g. "https://legion.marswars.org"

    # The reverse of every sibling's LEGION_API_KEY: the shared secret Alumni itself
    # checks on inbound requests to POST /api/graduates, since Legion is the caller here
    # instead of the callee. Must equal Legion's own ALUMNI_PUSH_API_KEY. Blank = the
    # intake endpoint is disabled (fails closed with 503), matching Legion's own
    # require_api_key discipline.
    legion_push_api_key: str = ""

    # Signs the survey link every graduation DM carries, and the same person's durable
    # "update your info" link (`services/survey.py`) — a single-purpose bearer token
    # naming one AlumniSurvey, completely independent of Legion's mw_sso/SSO_SECRET
    # (deliberately: it has to keep working for someone Legion no longer authenticates
    # at all). Blank = link minting is disabled (logged, survey rows are still created
    # so nothing is lost, just not reachable until this is set). Generate with:
    # python -c "import secrets; print(secrets.token_hex(32))"
    survey_link_secret: str = ""
    # How long a survey link stays redeemable, in seconds. 0 (the default) means it
    # never expires — deliberately: the *same* link doubles as a graduate's permanent
    # "update your career/email" link, meant to be dropped into whatever newsletter
    # tool an admin sends through, unpredictably, maybe years apart (see
    # routers/admin.py's newsletter CSV export). It grants access to one low-stakes
    # personal form, not an admin panel, so there's little to gain from expiring it —
    # the actual revocation lever is rotating SURVEY_LINK_SECRET, which invalidates
    # every link at once. Set a positive value here only if a deploy specifically wants
    # links to age out.
    survey_link_ttl: int = 0

    database_url: str = "sqlite+aiosqlite:///./alumni.db"

    timezone: str = "America/New_York"

    # Public base URL used when Slack messages (the graduation survey DM, /alumni
    # command replies) link back to Alumni.
    base_url: str = "http://localhost:8003"

    # Days after the survey DM before a reminder DM goes out to anyone who hasn't
    # answered yet. 0 disables the reminder job entirely (the default — nudging alumni
    # is a nice-to-have, not something every deploy necessarily wants on).
    survey_reminder_days: int = 0

    # Database backups (SQLite only)
    backup_dir: str = "backups"
    backup_keep: int = 14  # number of snapshots to retain
    backup_time: str = "23:30"  # HH:MM 24h local time for the weekly snapshot
    backup_day: str = "sun"  # day of week for the weekly backup (mon-sun)

    # Global toggle for all automated updates (Slack messages, scheduled jobs)
    updates_enabled: bool = True

    # Outbound email — used only by the self-service "email me my update link" flow
    # (routers/portal.py's GET/POST /survey/find). Every other integration in this app
    # deliberately avoided owning an email sender (see CLAUDE.md's "BYO email" section)
    # since Slack DMs + an external mail-merge tool covered everything else, but a
    # plain identical BCC blast has no way to carry a personalized link, and a graduate
    # who's lost Slack access has no other channel back in. Plain SMTP works with any
    # provider (a real mailbox, Gmail with an app password, or a transactional API's
    # SMTP endpoint) — no vendor SDK. Blank host = the feature is disabled: /survey/find
    # still shows its generic response, nothing is sent (logged so it's not a silent gap).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_address: str = ""
    smtp_from_name: str = "MARS/WARS Alumni"

    # Minimum time between self-service "email me my link" sends for the same survey —
    # prevents one person from hammering send (and spamming their own inbox). Separate
    # from the admin Resend button's bookkeeping (resent_count/last_resent_at) on
    # purpose: "how many times has staff tried" and "did the alum request this
    # themselves" are different questions — see AlumniSurvey.self_service_sent_at.
    survey_find_cooldown_seconds: int = 300

    # Newsletter PDF storage — the public front page (routers/newsletters.py) and its
    # admin management (admin.py's "Newsletters" section). Deliberately under "data/"
    # (not "static/") — static/ is baked into the Docker image and wiped on redeploy;
    # data/ is a persisted volume (apps-infra/docker-compose.yml: alumni-data:/app/data),
    # matching Merces's services/uploads.py pattern.
    newsletter_dir: str = "data/newsletters"
    max_newsletter_mb: int = 20


settings = Settings()
