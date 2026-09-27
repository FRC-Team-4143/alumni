"""Legion SSO gating on /admin: alumni-admin (full) vs alumni-manager (directory +
resend only) vs neither (403) vs unauthenticated (redirect to Legion)."""
from app.config import settings

from .conftest import make_sso_cookie


async def test_unauthenticated_redirects_to_legion(client):
    settings.legion_base_url = "https://legion.example.org"
    resp = await client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("https://legion.example.org/sso/authorize")


async def test_no_groups_is_forbidden(client):
    cookie = make_sso_cookie(groups=[])
    client.cookies.set("mw_sso", cookie)
    resp = await client.get("/admin")
    assert resp.status_code == 403


async def test_admin_reaches_every_section(client, make_alumnus, admin_cookie):
    member = await make_alumnus(name="Ada Alum")
    client.cookies.set("mw_sso", admin_cookie)

    for path in (
        "/admin", "/admin/alumni", f"/admin/alumni/{member.id}",
        "/admin/newsletters", "/admin/audit", "/admin/backup", "/admin/settings",
    ):
        resp = await client.get(path)
        assert resp.status_code == 200, path


async def test_manager_reaches_directory_but_not_settings(client, make_alumnus, manager_cookie):
    member = await make_alumnus(name="Ada Alum")
    client.cookies.set("mw_sso", manager_cookie)

    for path in ("/admin", "/admin/alumni", f"/admin/alumni/{member.id}", "/admin/newsletters"):
        resp = await client.get(path)
        assert resp.status_code == 200, path

    for path in ("/admin/audit", "/admin/backup", "/admin/settings"):
        resp = await client.get(path)
        assert resp.status_code == 403, path


async def test_link_identity_is_stepped_up_not_403d(client):
    cookie = make_sso_cookie(via="link")
    client.cookies.set("mw_sso", cookie)
    settings.legion_base_url = "https://legion.example.org"

    resp = await client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert "/sso/stepup" in resp.headers["location"]
