"""
End-to-End Verification of Service Enquiries (Round Trip, Ticketing, Transport)
Verifies:
1. Safe global airport search from airports.csv
2. Submission of Round Trip, Ticketing, and Transport enquiries via POST /api/bookings/enquiries
3. Persistence in DB with PENDING status, Rs 0 total amount (no payment initiated)
4. Admin Dashboard API visibility via GET /api/bookings/admin/list
5. Category filtering in Admin Dashboard
6. Complete enquiry details retrieval via GET /api/bookings/admin/{booking_ref}
"""

import sys
import os
sys.path.insert(0, os.path.abspath("."))

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from app.services.auth_service import AuthService

BASE_URL = "http://127.0.0.1:8003"


def create_admin_token() -> str:
    payload = {
        "sub": "admin_test_verifier",
        "email": "admin@shafsky.com",
        "role": "ADMIN",
        "iat": datetime.now(timezone.utc).timestamp(),
    }
    return AuthService.create_access_token(payload)


def api_post(endpoint: str, data: dict) -> dict:
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


from typing import Optional


def api_get(endpoint: str, token: Optional[str] = None) -> dict:
    url = f"{BASE_URL}{endpoint}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 60)
    print("STEP 1: Verify Global Airport Autocomplete from airports.csv")
    print("=" * 60)
    res_airports = api_get("/api/global-airports?q=")
    assert res_airports["success"] is True
    assert res_airports["source"] == "airports.csv"
    hubs = [a["code"] for a in res_airports["data"][:5]]
    print(f"Empty query default hubs: {hubs}")
    assert "DEL" in hubs or "LHR" in hubs or "DXB" in hubs

    res_london = api_get("/api/global-airports?q=london")
    london_codes = [a["code"] for a in res_london["data"][:5]]
    print(f"Search 'london' top results: {london_codes}")
    assert any(c in london_codes for c in ("LHR", "LGW", "LTN", "STN", "LCY"))

    res_del = api_get("/api/global-airports?q=DEL")
    assert res_del["data"][0]["code"] == "DEL"
    print("DEL exact match successfully returned:", res_del["data"][0]["name"])
    print("[PASS] Global airport autocomplete verified.\n")

    admin_token = create_admin_token()

    print("=" * 60)
    print("STEP 2: Submit Round Trip Enquiry")
    print("=" * 60)
    rt_payload = {
        "passengerName": "Vikram Malhotra",
        "passengerEmail": "vikram.malhotra@shafsky.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Round Trip",
        "serviceType": "Round Trip Flight Enquiry",
        "origin": "Indira Gandhi International Airport (DEL)",
        "destination": "London Heathrow Airport (LHR)",
        "serviceDate": "2026-11-20",
        "notes": "Wheelchair assistance for elderly passenger",
        "details": {
            "first_name": "Vikram",
            "last_name": "Malhotra",
            "service": "Round Trip",
            "origin": "Indira Gandhi International Airport (DEL)",
            "destination": "London Heathrow Airport (LHR)",
            "departure_date": "2026-11-20",
            "return_origin": "London Heathrow Airport (LHR)",
            "return_destination": "Indira Gandhi International Airport (DEL)",
            "return_date": "2026-11-30",
            "outbound_flight": "AI161",
            "return_flight": "AI162",
            "passenger_count": 2,
            "additional_requirements": "Wheelchair assistance for elderly passenger"
        }
    }
    rt_res = api_post("/api/bookings/enquiries", rt_payload)
    assert rt_res["success"] is True
    rt_ref = rt_res["data"]["bookingRef"]
    print(f"Round Trip created: ref={rt_ref}, status={rt_res['data']['status']}")
    assert rt_res["data"]["serviceCategory"] == "Round Trip"
    assert rt_res["data"]["status"] == "PENDING"
    print("[PASS] Round Trip enquiry submitted successfully.\n")

    print("=" * 60)
    print("STEP 3: Submit Ticketing Enquiry")
    print("=" * 60)
    tkt_payload = {
        "passengerName": "Ananya Sen",
        "passengerEmail": "ananya.sen@shafsky.com",
        "passengerPhone": "+919876543211",
        "serviceCategory": "Ticketing",
        "serviceType": "Air Ticketing Enquiry",
        "origin": "Chhatrapati Shivaji Maharaj International Airport (BOM)",
        "destination": "Dubai International Airport (DXB)",
        "serviceDate": "2026-12-05",
        "notes": "Premium economy aisle seat preference",
        "details": {
            "first_name": "Ananya",
            "last_name": "Sen",
            "service": "Ticketing",
            "origin": "Chhatrapati Shivaji Maharaj International Airport (BOM)",
            "destination": "Dubai International Airport (DXB)",
            "travel_date": "2026-12-05",
            "passenger_count": 1,
            "additional_requirements": "Premium economy aisle seat preference"
        }
    }
    tkt_res = api_post("/api/bookings/enquiries", tkt_payload)
    assert tkt_res["success"] is True
    tkt_ref = tkt_res["data"]["bookingRef"]
    print(f"Ticketing created: ref={tkt_ref}, status={tkt_res['data']['status']}")
    assert tkt_res["data"]["serviceCategory"] == "Ticketing"
    assert tkt_res["data"]["status"] == "PENDING"
    print("[PASS] Ticketing enquiry submitted successfully.\n")

    print("=" * 60)
    print("STEP 4: Submit Transport Enquiry")
    print("=" * 60)
    trn_payload = {
        "passengerName": "Rajesh Koothrappali",
        "passengerEmail": "rajesh.k@shafsky.com",
        "passengerPhone": "+919876543212",
        "serviceCategory": "Ground Transport",
        "serviceType": "Ground Transport Enquiry",
        "origin": "DEL Airport Terminal 3",
        "destination": "ITC Maurya, Diplomatic Enclave, New Delhi",
        "serviceDate": "2026-11-25 14:30",
        "notes": "English speaking chauffeur and airport paging board",
        "details": {
            "first_name": "Rajesh",
            "last_name": "Koothrappali",
            "service": "Transport",
            "pickup_location": "DEL Airport Terminal 3",
            "dropoff_location": "ITC Maurya, Diplomatic Enclave, New Delhi",
            "travel_date": "2026-11-25",
            "travel_time": "14:30",
            "vehicle_preference": "Luxury Sedan",
            "passenger_count": 3,
            "additional_requirements": "English speaking chauffeur and airport paging board"
        }
    }
    trn_res = api_post("/api/bookings/enquiries", trn_payload)
    assert trn_res["success"] is True
    trn_ref = trn_res["data"]["bookingRef"]
    print(f"Transport created: ref={trn_ref}, status={trn_res['data']['status']}")
    assert trn_res["data"]["serviceCategory"] == "Ground Transport"
    assert trn_res["data"]["status"] == "PENDING"
    print("[PASS] Transport enquiry submitted successfully.\n")

    print("=" * 60)
    print("STEP 5: Verify Visibility in Admin Dashboard List API")
    print("=" * 60)
    admin_list = api_get("/api/bookings/admin/list?page=1&pageSize=50", token=admin_token)
    assert admin_list["success"] is True
    items = admin_list["data"]["items"]
    items_by_ref = {b["bookingRef"]: b for b in items}

    for ref, expected_name, expected_cat in [
        (rt_ref, "Vikram Malhotra", "Round Trip"),
        (tkt_ref, "Ananya Sen", "Ticketing"),
        (trn_ref, "Rajesh Koothrappali", "Ground Transport"),
    ]:
        assert ref in items_by_ref, f"Booking ref {ref} missing from Admin list!"
        b = items_by_ref[ref]
        assert b["passengerName"] == expected_name
        assert b["serviceCategory"] == expected_cat
        assert b["status"] == "PENDING"
        assert float(b["totalAmount"]) == 0.0
        assert b["createdAt"] is not None
        print(f"Admin list found {ref}: {b['serviceCategory']} | {b['passengerName']} | {b['status']} | Date: {b['createdAt']}")

    print("[PASS] All enquiries visible in Admin Dashboard list.\n")

    print("=" * 60)
    print("STEP 6: Verify Service Category Filtering in Admin Dashboard")
    print("=" * 60)
    # Filter Round Trip
    rt_filtered = api_get("/api/bookings/admin/list?service_category=Round%20Trip", token=admin_token)
    rt_filtered_refs = {b["bookingRef"] for b in rt_filtered["data"]["items"]}
    assert rt_ref in rt_filtered_refs, "Round Trip enquiry missing from Round Trip filtered list"
    assert tkt_ref not in rt_filtered_refs, "Ticketing enquiry unexpectedly found in Round Trip filtered list"

    # Filter Ticketing
    tkt_filtered = api_get("/api/bookings/admin/list?service_category=Ticketing", token=admin_token)
    tkt_filtered_refs = {b["bookingRef"] for b in tkt_filtered["data"]["items"]}
    assert tkt_ref in tkt_filtered_refs, "Ticketing enquiry missing from Ticketing filtered list"
    assert rt_ref not in tkt_filtered_refs, "Round Trip enquiry unexpectedly found in Ticketing filtered list"

    # Filter Ground Transport
    trn_filtered = api_get("/api/bookings/admin/list?service_category=Ground%20Transport", token=admin_token)
    trn_filtered_refs = {b["bookingRef"] for b in trn_filtered["data"]["items"]}
    assert trn_ref in trn_filtered_refs, "Transport enquiry missing from Ground Transport filtered list"

    print("[PASS] Admin Dashboard serviceCategory filters work accurately.\n")

    print("=" * 60)
    print("STEP 7: Verify Admin Can View Complete Details for Each Enquiry")
    print("=" * 60)
    # Check Round Trip Detail
    rt_detail = api_get(f"/api/bookings/{rt_ref}", token=admin_token)
    assert rt_detail["success"] is True
    b_rt = rt_detail["data"]
    print("Round Trip complete detail:", json.dumps({
        "ref": b_rt["bookingRef"],
        "customer": b_rt["passengerName"],
        "email": b_rt["passengerEmail"],
        "phone": b_rt["passengerPhone"],
        "serviceCategory": b_rt["serviceCategory"],
        "serviceType": b_rt["serviceType"],
        "flightNum": b_rt["flightNum"],
        "route": f"{b_rt['originCode']} -> {b_rt['destCode']}",
        "pax": b_rt["metadataJson"]["passenger_count"],
        "notes": b_rt["notes"],
        "details": b_rt["metadataJson"]["details"]
    }, indent=2))
    assert b_rt["passengerName"] == "Vikram Malhotra"
    assert b_rt["passengerEmail"] == "vikram.malhotra@shafsky.com"
    assert b_rt["passengerPhone"] == "+919876543210"
    assert b_rt["flightNum"] == "AI161"
    assert b_rt["metadataJson"]["passenger_count"] == 2
    assert b_rt["metadataJson"]["details"]["return_date"] == "2026-11-30"
    assert b_rt["metadataJson"]["details"]["return_flight"] == "AI162"
    assert b_rt["notes"] == "Wheelchair assistance for elderly passenger"

    # Check Ticketing Detail
    tkt_detail = api_get(f"/api/bookings/{tkt_ref}", token=admin_token)
    assert tkt_detail["success"] is True
    b_tkt = tkt_detail["data"]
    print("Ticketing complete detail:", json.dumps({
        "ref": b_tkt["bookingRef"],
        "customer": b_tkt["passengerName"],
        "email": b_tkt["passengerEmail"],
        "phone": b_tkt["passengerPhone"],
        "serviceCategory": b_tkt["serviceCategory"],
        "route": f"{b_tkt['originCode']} -> {b_tkt['destCode']}",
        "pax": b_tkt["metadataJson"]["passenger_count"],
        "notes": b_tkt["notes"],
    }, indent=2))
    assert b_tkt["passengerName"] == "Ananya Sen"
    assert b_tkt["passengerEmail"] == "ananya.sen@shafsky.com"
    assert b_tkt["passengerPhone"] == "+919876543211"
    assert b_tkt["notes"] == "Premium economy aisle seat preference"

    # Check Transport Detail
    trn_detail = api_get(f"/api/bookings/{trn_ref}", token=admin_token)
    assert trn_detail["success"] is True
    b_trn = trn_detail["data"]
    print("Transport complete detail:", json.dumps({
        "ref": b_trn["bookingRef"],
        "customer": b_trn["passengerName"],
        "email": b_trn["passengerEmail"],
        "phone": b_trn["passengerPhone"],
        "serviceCategory": b_trn["serviceCategory"],
        "pickup": b_trn["originCode"],
        "dropoff": b_trn["destCode"],
        "vehicle": b_trn["metadataJson"]["details"]["vehicle_preference"],
        "pax": b_trn["metadataJson"]["passenger_count"],
        "notes": b_trn["notes"],
    }, indent=2))
    assert b_trn["passengerName"] == "Rajesh Koothrappali"
    assert b_trn["passengerEmail"] == "rajesh.k@shafsky.com"
    assert b_trn["passengerPhone"] == "+919876543212"
    assert b_trn["metadataJson"]["details"]["vehicle_preference"] == "Luxury Sedan"
    assert b_trn["notes"] == "English speaking chauffeur and airport paging board"

    print("[PASS] Admin can open and view complete details for all enquiries.\n")
    print("=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
