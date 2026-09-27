#!/usr/bin/env python3
"""
One-time migration: pull any existing alumni + their graduation-survey answers out of
Legion's SQLite file and into this app's database, BEFORE Legion's
`_migration_retire_alumni_tracking` ships (that migration rewrites `grade='alumni'` back
to `'senior'` and drops both `graduation_year` and the `graduation_surveys` table —
irreversibly, from Legion's side).

None of this data was ever on Legion's HTTP API (guardians and survey answers are
deliberately excluded from the wire shape, and `grade='alumni'`/`graduation_year` are
being removed from it as part of this same change) — so this reads Legion's raw SQLite
file directly rather than calling `/api/members`.

Usage:
    # Get a snapshot of Legion's live database first (it's running in another
    # container/process, so don't open its file while it might be mid-write):
    docker cp legion:/app/data/legion.db /tmp/legion-export.db
    # or, for a local dev Legion: cp /path/to/legion/legion.db /tmp/legion-export.db

    # Then, from this repo, against Alumni's own (already-running, already-synced-at-
    # least-never, since there IS no sync — just already created) database:
    python scripts/migrate_from_legion.py /tmp/legion-export.db ./alumni.db

Prints a summary (migrated / already-present / skipped-unmatched) and touches nothing
in the source file — it's opened read-only.

Before running this at all: `sqlite3 /tmp/legion-export.db "SELECT COUNT(*) FROM
graduation_surveys;"` — if that's 0 (likely, since Legion never shipped an admin page to
view survey answers), there's nothing to migrate and this script can be skipped entirely.
"""
import sqlite3
import sys
from datetime import datetime


def _connect_readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _find_local_member_id(dest: sqlite3.Connection, *, member_code, slack_user_id, name) -> int | None:
    """Same match cascade every legion_sync.py uses: member_code, then slack_user_id,
    then case-insensitive name."""
    if member_code:
        row = dest.execute(
            "SELECT id FROM members WHERE member_code = ?", (member_code,)
        ).fetchone()
        if row:
            return row[0]
    if slack_user_id:
        row = dest.execute(
            "SELECT id FROM members WHERE slack_user_id = ?", (slack_user_id,)
        ).fetchone()
        if row:
            return row[0]
    row = dest.execute(
        "SELECT id FROM members WHERE lower(name) = lower(?)", (name,)
    ).fetchone()
    return row[0] if row else None


def migrate(legion_db_path: str, alumni_db_path: str) -> None:
    src = _connect_readonly(legion_db_path)
    dest = sqlite3.connect(alumni_db_path)

    # Every existing graduation_surveys row, joined back to its member for identity —
    # this is Legion's DB, pre-migration, so the `graduation_surveys` table and
    # `members.graduation_year` still exist there.
    surveys = src.execute("""
        SELECT gs.status, gs.destination, gs.field_of_study, gs.stay_in_touch,
               gs.contact_email, gs.created_at, gs.completed_at,
               m.member_code, m.name, m.slack_user_id, m.team_id, m.graduation_year
        FROM graduation_surveys gs JOIN members m ON m.id = gs.member_id
    """).fetchall()

    # Team id -> number, so the migrated record's team_number is a plain int like the
    # rest of this app expects (Alumni has no Team table of its own).
    team_numbers = dict(src.execute("SELECT id, number FROM teams").fetchall())
    # Every remaining grade='alumni' member who has no graduation_surveys row at all
    # (nobody ever clicked "Fill out quick survey", or Legion's Yearly Grade Increase
    # ran before the survey existed) — still worth carrying over as a bare record.
    survey_member_names = {row[8] for row in surveys}
    bare_alumni = src.execute("""
        SELECT member_code, name, slack_user_id, team_id, graduation_year
        FROM members WHERE grade = 'alumni'
    """).fetchall()

    migrated = already_present = skipped = 0

    for (status, destination, field_of_study, stay_in_touch, contact_email,
         created_at, completed_at, member_code, name, slack_user_id, team_id,
         graduation_year) in surveys:
        local_id = _find_local_member_id(
            dest, member_code=member_code, slack_user_id=slack_user_id, name=name
        )
        if local_id is None:
            dest.execute(
                "INSERT INTO members (member_code, name, kind, slack_user_id, "
                "team_number, subteam_label, graduation_year, created_at) "
                "VALUES (?, ?, 'student', ?, ?, NULL, ?, ?)",
                (member_code, name, slack_user_id, team_numbers.get(team_id),
                 graduation_year, datetime.utcnow().isoformat()),
            )
            local_id = dest.execute("SELECT last_insert_rowid()").fetchone()[0]

        existing_survey = dest.execute(
            "SELECT id FROM alumni_surveys WHERE member_id = ?", (local_id,)
        ).fetchone()
        if existing_survey:
            dest.execute(
                "UPDATE alumni_surveys SET status=?, destination=?, field_of_study=?, "
                "stay_in_touch=?, contact_email=?, created_at=?, completed_at=? "
                "WHERE id = ?",
                (status, destination, field_of_study, stay_in_touch, contact_email,
                 created_at, completed_at, existing_survey[0]),
            )
            already_present += 1
        else:
            dest.execute(
                "INSERT INTO alumni_surveys (member_id, status, destination, "
                "field_of_study, stay_in_touch, contact_email, resent_count, "
                "created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (local_id, status, destination, field_of_study, stay_in_touch,
                 contact_email, created_at, completed_at),
            )
            migrated += 1

    for member_code, name, slack_user_id, team_id, graduation_year in bare_alumni:
        if name in survey_member_names:
            continue  # already handled above
        local_id = _find_local_member_id(
            dest, member_code=member_code, slack_user_id=slack_user_id, name=name
        )
        if local_id is not None:
            continue  # already migrated in an earlier run
        dest.execute(
            "INSERT INTO members (member_code, name, kind, slack_user_id, "
            "team_number, subteam_label, graduation_year, created_at) "
            "VALUES (?, ?, 'student', ?, ?, NULL, ?, ?)",
            (member_code, name, slack_user_id, team_numbers.get(team_id),
             graduation_year, datetime.utcnow().isoformat()),
        )
        migrated += 1

    dest.commit()
    src.close()
    dest.close()
    print(f"Migrated: {migrated}   Already present (updated): {already_present}   Skipped: {skipped}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    migrate(sys.argv[1], sys.argv[2])
