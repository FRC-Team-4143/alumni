# Alumni

Post-graduation survey and "where are they now" directory for FRC teams 4143
(MARS/WARS) and 4423 (MARS' Minions). Sibling to **Legion** (shared roster + SSO),
**Tempus** (attendance), **Munus** (volunteer hours), **Merces** (rewards), and
**Virtus** (goals/reviews) — same stack, dark theme, and conventions, but a fully
separate app with its own DB and Docker service (**port 8003**).

## Why this exists

Legion is meant to be the roster of *active* students and mentors. Alumni tracking —
a terminal grade, a graduation year, a post-graduation survey — used to live there too,
even though none of it is about anyone currently on the team. This app took all of
that over, and Legion now stores nothing about a person once they graduate beyond the
fact that a graduation happened (a one-time push — see "How people arrive here" below).

## How people arrive here

Unlike every sibling app, **Alumni runs no roster sync.** Legion holds no ongoing
"alumni" state to poll for — it doesn't track graduation at all. Instead, Legion's
**Yearly Grade Increase** admin action pushes a one-time event, `POST /api/graduates`,
the moment it archives a graduating senior. That push creates the `Member` row here and
sends the survey DM right away. From that point on, this app is the sole owner of
everything about that person — Legion never hears from it again, and this app never
asks Legion for anything about them again either.

A person can also be added by hand from `/admin/alumni` (not yet built — see CLAUDE.md)
for historical alumni who left before this app existed, or anyone the yearly push missed.

## Sending a newsletter

There's no email sender built in — Slack DMs the graduation survey link once, at
intake, and after that it's on you. Each alum's survey link doesn't expire and
doubles as a permanent "update your info" link.

**Use a real personalized/mail-merge send, not a plain BCC** — BCC sends one identical
email body to every recipient, so there's no way for each person's link to be
different in it. `/admin/alumni/newsletter.csv` (name, email, graduation year, their
personal link — only alumni who said **Yes** to staying in touch and left an email)
is built for Mailchimp, a Gmail mail-merge add-on, or a small send script: each of
those sends one individual email per row with that row's link substituted in. (A
single person's link is also on their detail page, for a one-off email.)

## The survey link is not a Legion login

A graduate is archived (`is_active=False`) in Legion the instant they graduate, and
Legion's magic links **refuse to redeem for an inactive member** by design — archiving
someone is supposed to kill every link already sitting in their DMs. So the survey link
in the graduation DM is **not** a Legion SSO link at all: it's a bearer token this app
mints and verifies itself (`SURVEY_LINK_SECRET`, independent of `SSO_SECRET`), naming
one survey. `/admin` is still gated by Legion SSO as usual — that's for staff, who are
active members.

## Running

```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8003
```

Requires a `.env` file (see `.env.example`). Key vars: `SSO_SECRET` (must equal
Legion's, for `/admin` sign-in only), `LEGION_BASE_URL`, `LEGION_PUSH_API_KEY` (must
equal Legion's `ALUMNI_PUSH_API_KEY` — this is what Legion presents when it calls in),
`SURVEY_LINK_SECRET` (independent secret for the graduate-facing link), `SLACK_BOT_TOKEN`
+ `SLACK_SIGNING_SECRET`. There is no local admin password; the first admin is granted
`alumni-admin` in Legion's `/admin/groups`.

## Testing

```bash
pytest
```

In-memory SQLite with async fixtures via `pytest-asyncio`. Do not mock the database —
tests hit a real (in-memory) DB to catch query bugs.

## One-time data migration

If Legion had any existing alumni/survey data before this app existed, run
`scripts/migrate_from_legion.py` **before** Legion's `_migration_retire_alumni_tracking`
ships — see that script's docstring for the exact steps. Likely a no-op: Legion never
shipped an admin page to view survey answers, so production probably has ~0 rows.

## Deployment

Deployed alongside the siblings from the `apps-infra` repo (Docker Compose + Nginx Proxy
Manager) on container port **8003**, public URL `alumni.marswars.org`.

## CSV export

`/admin/alumni/export.csv` — one row per alum with their current survey answers.
