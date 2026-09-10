def test_projects_require_auth(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 401


def test_create_and_list_project(client, user_headers):
    resp = client.post(
        "/api/projects",
        json={
            "name": "Cypher",
            "path": "C:\\Users\\Silvan\\Documents\\Claude\\Cypher",
            "description": "Persönlicher lokaler AI-Assistent",
            "technologies": ["Python", "FastAPI", "React"],
            "repository": "https://github.com/peppigminze/Cypher",
        },
        headers=user_headers,
    )
    assert resp.status_code == 200
    project = resp.json()
    assert project["name"] == "Cypher"
    assert project["technologies"] == ["Python", "FastAPI", "React"]

    listed = client.get("/api/projects", headers=user_headers).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Cypher"


def test_duplicate_project_name_is_rejected(client, user_headers):
    client.post("/api/projects", json={"name": "AlwaysMC"}, headers=user_headers)
    resp = client.post("/api/projects", json={"name": "AlwaysMC"}, headers=user_headers)
    assert resp.status_code == 409


def test_update_project(client, user_headers):
    created = client.post("/api/projects", json={"name": "Minecraft Plugin"}, headers=user_headers).json()

    resp = client.patch(
        f"/api/projects/{created['id']}",
        json={"notes": "Nutzt Paper API", "technologies": ["Java"]},
        headers=user_headers,
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["notes"] == "Nutzt Paper API"
    assert updated["technologies"] == ["Java"]


def test_delete_project(client, user_headers):
    created = client.post("/api/projects", json={"name": "Löschbar"}, headers=user_headers).json()

    resp = client.delete(f"/api/projects/{created['id']}", headers=user_headers)
    assert resp.status_code == 200

    listed = client.get("/api/projects", headers=user_headers).json()
    assert listed == []


def test_update_nonexistent_project(client, user_headers):
    resp = client.patch("/api/projects/999999", json={"notes": "x"}, headers=user_headers)
    assert resp.status_code == 404
