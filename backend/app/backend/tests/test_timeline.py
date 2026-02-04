"""Tests for the timeline API endpoint.

These tests require a database connection and will be skipped in CI when
running outside Docker Compose (where the 'db' hostname is not resolvable).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _db_available() -> bool:
    """Check if the database is reachable."""
    try:
        response = client.get("/api/timeline/dates")
        # If we get a response (even an error), the endpoint was reached
        return response.status_code != 500 or "OperationalError" not in response.text
    except Exception:
        return False


# Skip all tests in this module if DB is not available
pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="Database not available (run inside Docker Compose)",
)


def test_timeline_dates_endpoint_exists():
    """The /api/timeline/dates endpoint should return 200."""
    response = client.get("/api/timeline/dates")
    assert response.status_code == 200


def test_timeline_dates_structure():
    """The timeline response should have the expected structure."""
    response = client.get("/api/timeline/dates")
    data = response.json()

    assert "dates" in data
    assert "min_date" in data
    assert "max_date" in data
    assert isinstance(data["dates"], list)


def test_timeline_dates_entry_structure():
    """Each date entry should have the expected fields."""
    response = client.get("/api/timeline/dates")
    data = response.json()

    if len(data["dates"]) > 0:
        entry = data["dates"][0]
        assert "date" in entry
        assert "sample_count" in entry
        assert "total_detections" in entry
        assert isinstance(entry["sample_count"], int)
        assert isinstance(entry["total_detections"], int)
