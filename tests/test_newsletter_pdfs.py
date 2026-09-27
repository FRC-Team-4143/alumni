"""Newsletter PDF validation + save/delete (services/newsletter_pdfs.py), isolated
from the HTTP layer via a monkeypatched settings.newsletter_dir pointed at a tmp_path.
Mirrors Merces's tests/test_uploads.py."""
from io import BytesIO

from starlette.datastructures import Headers, UploadFile

from app.config import settings
from app.services import newsletter_pdfs


def _make_upload(filename, content, content_type="application/pdf"):
    return UploadFile(
        file=BytesIO(content), filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def test_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    upload = _make_upload("virus.exe", b"whatever", "application/octet-stream")
    try:
        await newsletter_pdfs.save_newsletter_pdf(upload)
        assert False, "expected InvalidNewsletterError"
    except newsletter_pdfs.InvalidNewsletterError:
        pass
    assert list(tmp_path.iterdir()) == []


async def test_rejects_mismatched_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    upload = _make_upload("issue.pdf", b"fake", "text/plain")
    try:
        await newsletter_pdfs.save_newsletter_pdf(upload)
        assert False, "expected InvalidNewsletterError"
    except newsletter_pdfs.InvalidNewsletterError:
        pass


async def test_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_newsletter_mb", 1)
    upload = _make_upload("issue.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")
    try:
        await newsletter_pdfs.save_newsletter_pdf(upload)
        assert False, "expected InvalidNewsletterError"
    except newsletter_pdfs.InvalidNewsletterError:
        pass


async def test_rejects_empty_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    upload = _make_upload("issue.pdf", b"", "application/pdf")
    try:
        await newsletter_pdfs.save_newsletter_pdf(upload)
        assert False, "expected InvalidNewsletterError"
    except newsletter_pdfs.InvalidNewsletterError:
        pass


async def test_saves_with_a_random_filename_not_the_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    upload = _make_upload("../../etc/passwd.pdf", b"%PDF-fake-bytes", "application/pdf")
    filename = await newsletter_pdfs.save_newsletter_pdf(upload)
    assert filename != "../../etc/passwd.pdf"
    assert "/" not in filename
    assert filename.endswith(".pdf")
    assert (tmp_path / filename).read_bytes() == b"%PDF-fake-bytes"


async def test_delete_is_a_noop_when_already_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "newsletter_dir", str(tmp_path))
    newsletter_pdfs.delete_newsletter_pdf("does-not-exist.pdf")
    newsletter_pdfs.delete_newsletter_pdf("")
