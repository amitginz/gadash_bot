import pytest
from unittest.mock import patch
from app import app
import gadash.sheets as sheets_mod

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["_csrf"]     = "test-csrf-token"
        yield c


CSRF_HEADER = {"X-CSRFToken": "test-csrf-token"}


class TestApiOfflineEndpoints:

    def test_api_status_endpoint_returns_json(self):
        with app.test_client() as client:
            res = client.get("/api/status")
            assert res.status_code == 200
            data = res.get_json()
            assert "online" in data
            assert "pending_count" in data

    @patch("gadash.sync._get_sheet")
    def test_api_sync_endpoint_requires_auth_and_syncs(self, mock_get_sheet, client):
        # Authenticated client fixture
        sheets_mod._offline_queue = []
        res = client.post("/api/sync", headers=CSRF_HEADER)
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"
        assert data["synced_count"] == 0
