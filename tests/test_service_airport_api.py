"""Live-shaped tests for service-airport resolution against the FastAPI app."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_resolve_arrival_london_delhi():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "ARRIVAL", "origin": "LHR", "destination": "DEL", "flight_type": "INTERNATIONAL"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["service_airport"] == "DEL"


def test_resolve_departure_delhi_london():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "DEPARTURE", "origin": "DEL", "destination": "LHR", "flight_type": "INTERNATIONAL"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["service_airport"] == "DEL"


def test_resolve_transit_dubai_delhi_london():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={
            "journey_type": "TRANSIT",
            "origin": "DXB",
            "destination": "LHR",
            "transit": "DEL",
            "flight_type": "INTERNATIONAL",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["service_airport"] == "DEL"


def test_reject_arrival_london_paris():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "ARRIVAL", "origin": "LHR", "destination": "CDG"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert body["is_supported"] is False


def test_reject_departure_paris_london():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "DEPARTURE", "origin": "CDG", "destination": "LHR"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False


def test_reject_transit_unsupported():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "TRANSIT", "origin": "DXB", "destination": "LHR", "transit": "CDG"},
    )
    assert res.status_code == 200
    assert res.json()["valid"] is False


def test_global_csv_search_includes_dubai_not_as_supported():
    res = client.get("/api/journey/global-airports", params={"q": "DXB"})
    assert res.status_code == 200
    body = res.json()
    assert body.get("source") == "airports.csv"
    codes = [row["code"] for row in body["data"]]
    assert "DXB" in codes
    assert all(row.get("is_supported") is not True for row in body["data"] if row["code"] == "DXB")


def test_global_csv_empty_query_returns_large_airports():
    res = client.get("/api/journey/global-airports", params={"q": ""})
    assert res.status_code == 200
    body = res.json()
    assert body.get("source") == "airports.csv"
    assert len(body["data"]) > 0
    assert all(len(row["code"]) == 3 for row in body["data"])
    assert all(row.get("is_supported") is not True for row in body["data"])


def test_supported_search_excludes_dubai_and_london():
    res = client.get("/api/airports/search", params={"q": "", "scope": "supported", "journey_type": "ARRIVAL"})
    assert res.status_code == 200
    body = res.json()
    assert body.get("source") == "supported_airports"
    codes = {row["code"] for row in body["data"]}
    assert "DEL" in codes
    assert "BOM" in codes
    assert "DXB" not in codes
    assert "LHR" not in codes


def test_journey_airports_is_shafsky_db_only():
    res = client.get("/api/journey/airports", params={"journey_type": "ARRIVAL"})
    assert res.status_code == 200
    codes = {row["iata_code"] for row in res.json()["data"]}
    assert "DEL" in codes
    assert "BOM" in codes
    assert "DXB" not in codes
    assert "LHR" not in codes
    assert all("code" in row and "name" in row for row in res.json()["data"])
    assert len(codes) >= 20


def test_global_csv_search_includes_heathrow():
    res = client.get("/api/journey/global-airports", params={"q": "LHR"})
    assert res.status_code == 200
    assert res.json().get("source") == "airports.csv"
    codes = [row["code"] for row in res.json()["data"]]
    assert "LHR" in codes


def test_supported_search_does_not_require_lhr():
    res = client.get("/api/airports/search", params={"q": "LHR", "scope": "supported"})
    assert res.status_code == 200
    codes = [row["code"] for row in res.json()["data"]]
    assert "LHR" not in codes


def test_get_airports_returns_supported_airports_table():
    res = client.get("/api/airports")
    assert res.status_code == 200
    body = res.json()
    codes = {row["code"] for row in body["data"]}
    assert "DEL" in codes
    assert "BOM" in codes
    assert "GAU" in codes
    assert "DXB" not in codes
    assert "LHR" not in codes
    delhi = next(row for row in body["data"] if row["code"] == "DEL")
    assert "Indira Gandhi" in delhi["name"]
    assert delhi["city"]


def test_resolve_arrival_dubai_delhi():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "ARRIVAL", "origin": "DXB", "destination": "DEL", "flight_type": "INTERNATIONAL"},
    )
    assert res.status_code == 200
    assert res.json()["valid"] is True
    assert res.json()["service_airport"] == "DEL"


def test_resolve_departure_unsupported_origin():
    res = client.post(
        "/api/journey/resolve-service-airport",
        json={"journey_type": "DEPARTURE", "origin": "DXB", "destination": "DEL"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert "not supported" in body["error"].lower()


def test_cors_allows_localhost_5174():
    from app.config import settings
    assert "http://localhost:5174" in settings.ALLOWED_ORIGINS


def test_delhi_arrival_catalog_packages_only():
    res = client.get(
        "/api/airport/services",
        params={
            "airport": "DEL",
            "journey_type": "arrival",
            "origin": "LHR",
            "destination": "DEL",
            "flight_type": "international",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["airport"]["code"] == "DEL"
    assert body.get("individualServices") == []
    assert body.get("individual_services") == []
    assert "packages" in body
    ids = [p.get("id") for p in body.get("packages") or []]
    assert ids
    assert "meet_greet" not in ids
    assert "buggy" not in ids
    assert "porter" not in ids
    assert "wheelchair" not in ids
    assert "transport" not in ids
    assert any(i in ids for i in ("silver", "gold", "elite", "platinum", "elite_plus"))
