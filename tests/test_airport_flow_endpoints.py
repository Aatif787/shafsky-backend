from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_flow_init_requires_valid_airport():
    resp = client.post("/api/airport/flow/init", json={"airport_code": "DEL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert body.get("airport_code") == "DEL"
    assert "allowed_service_types" in body


def test_flow_flight_info_missing_fields():
    # Missing date should return 400
    resp = client.post("/api/airport/flow/flight-info", json={
        "airport_code": "DEL",
        "service_type": "arrival",
        "flight_number": "EK-501"
    })
    assert resp.status_code == 400
