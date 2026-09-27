"""Basic liveness: the app boots and /health responds."""


async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app": "alumni"}
