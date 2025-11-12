# Minimal test demonstrating the repo’s testing pattern.
# Why this test exists:
# - Exercises FastAPI in-process using `fastapi.testclient`.
# - Acts as a template for future route tests (happy path + status/assertions).
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
