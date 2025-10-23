from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_create_get_delete():
    # create (201 or 409 if already exists)
    r = client.post("/strings", json={"value": "unittest-string"})
    assert r.status_code in (201, 409)

    # get
    r2 = client.get("/strings/unittest-string")
    assert r2.status_code == 200
    data = r2.json()
    assert data["value"] == "unittest-string"
    assert "properties" in data

    # delete (204 or 404)
    r3 = client.delete("/strings/unittest-string")
    assert r3.status_code in (204, 404)
