def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root(client):
    from app import main

    resp = client.get("/")
    assert resp.status_code == 200
    if main._frontend is None:  # frontend not built: backend info
        assert resp.json()["status"] == "running"
    else:  # built: "/" is the web app
        assert "text/html" in resp.headers["content-type"]
