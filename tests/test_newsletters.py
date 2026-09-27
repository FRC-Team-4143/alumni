"""Public newsletter pages (/, /newsletters, /newsletters/{id}) and their admin
management (/admin/newsletters) — see routers/newsletters.py, admin.py's "Newsletters"
section, and services/newsletter_pdfs.py."""
from app.config import settings

PDF_BYTES = b"%PDF-1.4 fake newsletter bytes"


def _pdf_file(name="issue.pdf"):
    return {"file": (name, PDF_BYTES, "application/pdf")}


async def _upload(client, cookie, *, title="October Alumni Update", published_date="2026-09-01"):
    client.cookies.set("mw_sso", cookie)
    return await client.post(
        "/admin/newsletters",
        data={"title": title, "published_date": published_date},
        files=_pdf_file(),
    )


async def test_home_page_shows_empty_state_when_no_newsletters(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "No newsletters yet" in resp.text


async def test_archive_page_shows_empty_state_when_no_newsletters(client):
    resp = await client.get("/newsletters")
    assert resp.status_code == 200
    assert "No newsletters published yet" in resp.text


async def test_admin_upload_shows_up_on_home_and_archive(client, admin_cookie, tmp_path):
    settings.newsletter_dir = str(tmp_path)

    resp = await _upload(client, admin_cookie)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/newsletters?message=Newsletter%20uploaded."

    home = await client.get("/")
    assert "October Alumni Update" in home.text
    assert "/newsletter-files/" in home.text

    archive = await client.get("/newsletters")
    assert "October Alumni Update" in archive.text

    # The saved file is actually on disk under newsletter_dir, not the client's name.
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == PDF_BYTES


async def test_manager_can_upload_and_delete(client, manager_cookie, tmp_path):
    settings.newsletter_dir = str(tmp_path)

    resp = await _upload(client, manager_cookie)
    assert resp.status_code == 303

    page = await client.get("/admin/newsletters")
    assert "October Alumni Update" in page.text

    # Grab the id from the admin list to delete it.
    import re
    match = re.search(r"/admin/newsletters/(\d+)/delete", page.text)
    assert match, "expected a delete form on the admin newsletters page"
    newsletter_id = match.group(1)

    resp = await client.post(f"/admin/newsletters/{newsletter_id}/delete")
    assert resp.status_code == 303
    assert list(tmp_path.iterdir()) == []

    page = await client.get("/admin/newsletters")
    assert "October Alumni Update" not in page.text


async def test_latest_newsletter_shown_is_by_published_date_not_upload_order(client, admin_cookie, tmp_path):
    settings.newsletter_dir = str(tmp_path)

    # Upload the older issue *second* — published_date must still win over upload order.
    await _upload(client, admin_cookie, title="Winter 2025 Newsletter", published_date="2025-12-01")
    await _upload(client, admin_cookie, title="Spring 2025 Newsletter", published_date="2025-03-01")

    home = await client.get("/")
    assert "Winter 2025 Newsletter" in home.text
    assert "Spring 2025 Newsletter" not in home.text

    archive = await client.get("/newsletters")
    assert archive.text.index("Winter 2025") < archive.text.index("Spring 2025")


async def test_non_pdf_upload_is_rejected(client, admin_cookie, tmp_path):
    settings.newsletter_dir = str(tmp_path)
    client.cookies.set("mw_sso", admin_cookie)

    resp = await client.post(
        "/admin/newsletters",
        data={"title": "Not a PDF", "published_date": "2026-01-01"},
        files={"file": ("virus.exe", b"whatever", "application/octet-stream")},
    )
    assert resp.status_code == 303
    assert "doesn" in resp.headers["location"]  # percent-encoded "doesn't look like a PDF"
    assert list(tmp_path.iterdir()) == []


async def test_unknown_newsletter_id_redirects_to_archive(client):
    resp = await client.get("/newsletters/999", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/newsletters"


async def test_detail_page_renders_for_a_real_issue(client, admin_cookie, tmp_path):
    settings.newsletter_dir = str(tmp_path)
    await _upload(client, admin_cookie)

    # Find the id via the archive page rather than reaching into the DB directly, since
    # this test only has the HTTP client.
    archive = await client.get("/newsletters")
    import re
    match = re.search(r"/newsletters/(\d+)", archive.text)
    assert match

    resp = await client.get(f"/newsletters/{match.group(1)}")
    assert resp.status_code == 200
    assert "October Alumni Update" in resp.text
