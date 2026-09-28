# Alumni — Codebase Guide

Post-graduation survey + "where are they now" directory for FRC teams 4143 (MARS/WARS)
and 4423 (MARS' Minions) — plus a public newsletter archive, so alumni.marswars.org
reads as a place to catch up on the team, not just a survey/admin tool with no home
page. FastAPI + SQLAlchemy (async) + Jinja2 + SQLite. Intentionally mirrors the sibling
apps' stack, dark theme, and conventions, but is a fully separate app with its own DB
and Docker service (**port 8003**). Nothing is imported across the projects —
integration with Legion is over HTTP only, in both directions (see below).

## Running

```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8003
```

Requires a `.env` (see `.env.example`). Key vars: `SLACK_BOT_TOKEN`,
`SLACK_SIGNING_SECRET`, `BASE_URL`, and the Legion integration — `SSO_SECRET` (must
equal Legion's, admin sign-in only), `LEGION_BASE_URL`, `LEGION_PUSH_API_KEY` (must
equal Legion's `ALUMNI_PUSH_API_KEY`), plus `SURVEY_LINK_SECRET` (independent of
`SSO_SECRET` — see "The survey link is not a Legion login" below). There is **no**
admin password; `/admin` is gated by Legion SSO + the `alumni-admin` (full) or
`alumni-manager` (directory + resend only) group.

No Legion runs locally, so there's nothing to mint an `mw_sso` cookie for `/admin` dev.
The gitignored `devlogin.py` helper mints one signed with this app's own `SSO_SECRET`
and redirects in — run it on another port (`uvicorn devlogin:app --port 8103`) and hit
`/login?code=code0001&groups=alumni-admin`. The `/survey` side needs no cookie at all —
mint a link directly: `python -c "from app.services.survey import make_survey_link;
print(make_survey_link(1))"` (survey id `1` must already exist).

## Testing

```bash
pytest
```

In-memory SQLite with async `pytest-asyncio`. **Do not mock the database** — tests hit a
real (in-memory) DB. `tests/conftest.py` provides a `FakeSlack` recorder (no outbound
Slack), `make_sso_cookie()` / `admin_cookie` / `manager_cookie` fixtures (admin side
only — the survey side uses `make_survey_link`, not a cookie), and `make_member` /
`make_alumnus` factories.

`_isolate_settings_from_dotenv` resets every setting to its class default so a
developer's real `.env` can't change a test's outcome, and rebuilds both signers
(`services/sso.py`'s and `services/survey.py`'s — they're built at *import* time from
their respective secrets) so cookies/links minted in a test verify against the same key
that test just set.

## Project Layout

```
app/
  main.py            # FastAPI app, router wiring, lifespan (init_db + scheduler), /health
  config.py          # Settings (pydantic-settings, reads .env)
  database.py        # Engine, session, init_db() + inspect-guarded migrations
  models.py          # ORM models: Member, AlumniSurvey, AppSetting, AuditLog
  utils.py           # Naive-UTC datetime helpers (utc_to_local, now_utc)
  templating.py      # Shared Jinja2 env (filters + auth-aware globals)
  routers/
    admin.py         # /admin — dashboard, directory, detail, resend, export, newsletters, settings, backup, audit
    portal.py        # /survey (GET+POST) + /survey/thanks + /survey/find — the public form, token-gated
    newsletters.py    # / , /newsletters, /newsletters/{id} — the public newsletter front page + archive
    graduates.py      # POST /api/graduates — inbound intake from Legion (X-API-Key gated)
    slack.py         # /alumni slash command only — no interactive components exist here
  services/
    intake.py         # Turns a Legion push (or a hand-add) into a Member + AlumniSurvey
    survey.py         # Survey link mint/verify, DM send/resend, completion + DM-edit-to-thanks,
                      #   self-service email recovery (/survey/find)
    newsletter.py     # "Who gets a newsletter EMAIL" query, used by the CSV export — the
                      #   graduate-outreach newsletter, unrelated to the public PDF archive below
    newsletter_pdfs.py # Public newsletter PDF storage (save/delete/list/latest) — deliberately
                      #   named apart from newsletter.py above; see that file's own note
    email_sender.py   # Plain SMTP sender — used only by survey.py's self-service recovery
    sso.py            # Verifies Legion's mw_sso cookie (verify-only) — admin side only
    slack_client.py   # AsyncWebClient wrapper (send_dm / post_to_channel / update_message)
    scheduler.py      # APScheduler: nightly backup, survey reminders
    backup.py         # SQLite snapshot backup + staged restore (VACUUM INTO)
    audit.py          # Append-only mutation log
    app_settings.py   # Generic key/value store (no roster-sync watermark — see below)
scripts/
  migrate_from_legion.py  # One-time import of pre-existing Legion alumni/survey data
```

## Domain model (`app/models.py`)

`Member` — one graduate: `member_code` (nullable — a hand-added historical alumnus may
have none), `name`, `kind`, `slack_user_id`, `team_number`/`subteam_label` (plain
values, point-in-time, never refreshed — there's no live Team/Subteam mirror to resolve
them against), `graduation_year`. One-to-one with `AlumniSurvey` — `status`
(`sent`/`completed`), the 4 core answers moved from Legion's old `GraduationSurvey`
(`destination`, `field_of_study`, `stay_in_touch`, `contact_email`), plus
`linkedin_url`/`current_city`/`willing_to_mentor`/`admin_notes` (extensions the old
survey had no room for), and resend bookkeeping (`sent_at`/`resent_count`/
`last_resent_at`/`self_service_sent_at`). `Newsletter` — one published PDF issue
(`title`, `filename`, `published_date`, `uploaded_by`) backing the public front page;
see "The public newsletter front page" below. Plus `AppSetting` and `AuditLog`.

## Key conventions

### There is no roster sync — this is the one app that's pushed to, not pulling
Every sibling app has a `services/legion_sync.py` that pulls `/api/members` on a
schedule. This app has none. Legion doesn't track graduation as ongoing state anymore
(see Legion's CLAUDE.md, "Members are unified" / "Graduation is a push-event, not
stored state") — there's nothing left to poll for. Instead, Legion's Yearly Grade
Increase action calls `POST /api/graduates` here, once, at the exact moment it archives
a senior (`app/services/alumni_push.py` on Legion's side; `routers/graduates.py` +
`services/intake.py` here). That single event is the entire data relationship between
the two apps in this direction — no watermark, no incremental pull, nothing recurring.
Idempotent on `member_code`, so a resent push (an admin retrying a delivery that failed,
per Legion's audit log) never creates a duplicate `Member`.

### The public newsletter front page (`routers/newsletters.py`, `services/newsletter_pdfs.py`)
Before this existed, `/` had no route at all — the domain's only public surfaces were
`/survey` (a bearer link) and `/admin` (Legion SSO). Now `GET /` shows the most recent
`Newsletter` (by `published_date`, an admin-set issue date — **not** upload time, so
backfilling an older issue or uploading a bit late doesn't misorder the archive) embedded
inline via a plain `<embed type="application/pdf">`, with `GET /newsletters` listing every
issue and `GET /newsletters/{id}` showing any one of them the same way. Fully public, no
auth — the whole point is that the domain reads as a place to catch up on the team, not
just an app with a login wall. `public/base.html` is a separate, wider base template from
`portal/base.html` (560px, deliberately cramped for a one-field survey form) since reading
a PDF wants real width; it carries a small nav (Newsletter / Archive / "Update my info" →
`/survey/find`) tying the two public surfaces together.

**Storage**: PDFs saved under `settings.newsletter_dir` (default `data/newsletters`) with
a random filename — never the client-supplied one — and served back out via the
`/newsletter-files` static mount (`main.py`), exactly mirroring Merces's
`services/uploads.py` store-photo pattern. `data/` (not `static/`) because `static/` is
baked into the Docker image and wiped on redeploy; `data/` is the persisted
`alumni-data` volume apps-infra already mounts, so no infra change was needed to make
uploads durable. Filenames are unguessable but unauthenticated — acceptable since a
newsletter is inherently public content once published, same reasoning as Merces's item
photos.

**Management** (`admin.py`'s "Newsletters" section, `/admin/newsletters`): upload (title
+ issue date + PDF) and delete, gated the same as the Alumni Directory —
`alumni-admin` **or** `alumni-manager` (added to `_manager_allowed`) — not admin-only,
since the user wanted both roles able to publish an issue without needing full admin.
No draft/unpublished state: every uploaded row is immediately live on the public site,
matching this app's small-trusted-team posture everywhere else (no approval workflow on
the alumni directory edits either). Deleting removes both the DB row and the underlying
file (`delete_newsletter_pdf`); every upload/delete is audit-logged like every other
admin mutation.

### The survey link is not a Legion login
Legion's magic links deliberately re-check `is_active` on every redemption ("archiving
someone kills every link already sitting in their DMs" — Legion's `routers/sso.py`,
`sso_link`). A graduate is archived the instant they graduate, so a Legion-minted link
would be dead on arrival for exactly the audience this survey is for. `services/
survey.py` mints and verifies its own bearer token instead, signed with
`SURVEY_LINK_SECRET` — a secret with nothing to do with Legion, so it keeps working for
someone Legion no longer authenticates at all. `/survey` trusts the token alone; it sets
no cookie and needs none. Only `/admin` — staff, who *are* active Legion members — uses
real Legion SSO (`services/sso.py`, copied near-verbatim from Virtus).

### The newsletter link is BYO email
The same per-person link the graduation DM carries doubles as a graduate's **permanent
update link** — resubmitting `/survey` just overwrites their previous answers, so it's
equally good for "tell us where you're headed" on day one and "anything changed?" two
years later. There is **no in-app newsletter sender** here, on purpose: Slack DMs are
the intake channel only (sent once, at graduation — `services/intake.py`), since
Slack access itself doesn't reliably survive someone leaving the org. Everything after
that is deliberately left to whatever an admin already uses to send a newsletter —
building and maintaining an email sender (SMTP relay or a transactional-email API,
deliverability, unsubscribe handling) was scoped out as real infrastructure this small
a team doesn't need to own.

**Note the link can't go in a plain BCC blast** — BCC sends one identical body to
everyone, so there's no way for each recipient's link to differ. The intended path is
a real personalized/mail-merge send (Mailchimp, a Gmail mail-merge add-on, a
`newsletter.csv` + script), which sends one individual email per row instead of one
shared one. If a blast goes out as a plain BCC anyway (or an admin just doesn't have a
mail-merge tool handy), point it at `/survey/find` instead of a personal link — see
"Self-service link recovery" below.

`GET /admin/alumni/newsletter.csv` (`routers/admin.py`, backed by
`services/newsletter.newsletter_recipients`) exports exactly the recipient list an
external tool needs: `name, contact_email, graduation_year, update_link` — one row per
alum who said **Yes** to staying in touch *and* left a contact email (no opt-in, no
email, no row; this is the one place `stay_in_touch` actually gates something,
everywhere else it's just a display field). The alumnus detail page also shows one
person's link directly (with a copy button) for a one-off email. Because a newsletter
might go out unpredictably — six months later, three years later — `SURVEY_LINK_TTL`
defaults to **0 (never expires)**: the actual revocation lever, if one is ever needed,
is rotating `SURVEY_LINK_SECRET`, which invalidates every outstanding link at once
rather than aging them out individually.

**Tried and reverted: syncing the list into a Google Sheet.** A `services/
sheets_sync.py` (via `gspread`, a service-account credential) once mirrored
`newsletter_recipients()` into a Google Sheet on a schedule, for a Gmail mail-merge
add-on or Mailchimp sheet-import to read live. Removed once it turned out not to feed
anything real: the actual send path landed on Mailchimp/Brevo-style CSV import, which
`newsletter.csv` already serves directly with no Sheet in between. It would only have
earned its place backing a tool that reads *live* from a Sheet (paid-tier YAMM, or
Gmail's native mail merge where available) — worth reconsidering only if that's
actually where a future send ends up, not as a default.

### Self-service link recovery (`/survey/find`) — the one place this app sends its own email
Everything above assumes each recipient's link differs (a mail-merge send, a resend from
`/admin`, the original graduation DM). `/survey/find` (`routers/portal.py`,
`services/survey.request_link_by_email`/`find_surveys_by_email`/`email_survey_link`)
exists for the two cases where that's not true: a plain identical BCC blast with no
personal link in it at all, and a graduate who's lost Slack access and so can't get a
fresh DM either way (`resend_survey_dm` silently does nothing without a `slack_user_id`).
It's linked from `portal/link_invalid.html` for exactly that reason.

The flow: someone types the email they gave us on `/survey` (only ever set if they
answered **Yes** to staying in touch — see the newsletter section above), and every
`AlumniSurvey.contact_email` match (case-insensitively; not unique/indexed, so a rare
coincidence sends to all matches, each to that one inbox the requester already controls)
gets a fresh copy of its own link, mailed via **SMTP** (`services/email_sender.py` —
plain `smtplib`, no vendor SDK, works with any provider: a real mailbox, Gmail with an
app password, or a transactional API's SMTP endpoint; blank `SMTP_HOST` disables it,
logged, not a hard failure). The router's response is **identical whether or not
anything matched or sent** — same anti-enumeration shape as Legion's SSO username check
— so this endpoint can't be used to fish for which emails are on file. A per-survey
cooldown (`SURVEY_FIND_COOLDOWN_SECONDS`, default 300s, tracked on
`AlumniSurvey.self_service_sent_at`) stops one person hammering send; that field is
**separate** from `resent_count`/`last_resent_at`, which the admin directory's "Resend"
button uses to mean "how many times has staff tried" — a self-service request isn't
staff acting, so it doesn't bump that counter.

This only helps someone who already completed the survey once with an email on file —
there's nowhere else an email address is captured, so a graduate who never answered (or
said no to staying in touch) has no way to self-serve recovery and still needs an admin
to hand-resend or hand-add them from `/admin`.

### No Slack interactive components
The old Legion survey was a Slack Block Kit modal. This one is a plain web form: the DM
just carries a link (`services/survey._intro_text`). That means Alumni registers no
`block_actions`/`view_submission` handlers at all, and needs **no entry in Legion's
`slack_dispatch.py`** — same as Virtus/Merces ("every action is a form on the web"). The
only inbound Slack surface is the `/alumni` slash command (`routers/slack.py`), which
looks the caller up in the **local** `members` table by `slack_user_id` (never calls
Legion) and replies with a fresh survey link.

### Admin-edited answers don't self-complete a survey
`POST /admin/alumni/{id}/survey` (an admin correcting/filling in answers from a phone
call, LinkedIn, etc.) never flips `status` to `completed` on its own — only the graduate
submitting `/survey` themselves does that (`services/survey.mark_completed`). Editing is
a correction to whatever's there, not a stand-in submission on the alum's behalf.

### Resending never fails loudly
`services/survey.resend_survey_dm` bumps `resent_count`/`last_resent_at` regardless of
whether the DM actually sent — so repeated attempts stay visible in the admin UI (how
many times has staff tried?) even when Slack is unreachable or the person has no
`slack_user_id` on file. The **Resend** button itself is disabled client-side when
there's no Slack ID, and the route double-checks server-side.

### Database migrations
No Alembic, matching every sibling app. One inspect-guarded `_migration_*(conn)` exists
so far (`_migration_add_self_service_sent_at`, for the self-service recovery flow's
cooldown column) — a no-op on a freshly created schema, which already has the column.
Add another the same way, called from `init_db()`, when a future change needs one (see
Legion's `database.py` for more examples of the pattern).

## Scheduled jobs (`scheduler.py`)

| Job | Trigger |
|-----|---------|
| Database backup | `BACKUP_DAY` at `BACKUP_TIME` (SQLite snapshot, rotates to `BACKUP_KEEP`) |
| Survey reminders | daily at 10:30 local, **off by default** (`SURVEY_REMINDER_DAYS=0`) — nudges anyone still `sent` (not `completed`) once that many days have passed since the DM (or the last reminder) |

No Legion sync job — see "There is no roster sync" above.

## UI conventions
**Shared design (read first):** the look shared by every MARS/WARS app — palette, admin
and portal shells, the sign-in card, tables, icons — is defined in
`apps-infra/design/README.md`. `static/css/marswars.css` and
`static/js/table-filter-sort.js` are vendored from `apps-infra/design/` — never edit
them here; change the canonical copy and run `apps-infra/design/sync.sh`. App-only
styles go in the base template's own `<style>` block, after the `marswars.css` link.

Single dark theme shared with the siblings (`#0a0a0a` bg, `#111111` panels, accent red
`#cc2200`, borders `#2a1a1a`). Admin pages extend `admin/base.html` (Bootstrap 5,
sidebar). The public `/survey` pages extend `portal/base.html` — deliberately a much
simpler shell than the siblings' portal bases (no session-aware nav, no sign-out link,
no `is_staff`/`stepup_url` checks) since there's no session on this side at all, just a
bearer token.

## Deployment
Deployed alongside the siblings from the `apps-infra` repo (Docker Compose + Nginx Proxy
Manager) on container port **8003**, public URL `alumni.marswars.org`. Go-live sequence
(deploy Alumni first, run the migration script if Legion has pre-existing data, *then*
merge Legion's alumni-tracking-removal branch) is documented in `apps-infra`'s README.
