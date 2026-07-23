import sys
import os
import time
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.schema import RefreshToken, Booking, UserAuth
from sqlalchemy import select, func

client = TestClient(app)

def print_separator(title):
    print("\n" + "=" * 80)
    print(f" ENDPOINT RUNTIME VERIFICATION: {title}")
    print("=" * 80)

# 1. GET /health
print_separator("GET /health")
start = time.time()
res = client.get("/health")
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: GET /health")
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200

# 2. GET /live
print_separator("GET /live")
start = time.time()
res = client.get("/live")
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: GET /live")
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200

# 3. GET /ready
print_separator("GET /ready")
start = time.time()
res = client.get("/ready")
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: GET /ready")
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200

# 4. GET /metrics
print_separator("GET /metrics")
start = time.time()
res = client.get("/metrics")
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: GET /metrics")
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Text Output (First 15 lines):")
lines = res.text.split("\n")[:15]
print("\n".join(lines))
assert res.status_code == 200

# 5. POST /api/auth/login
print_separator("POST /api/auth/login")
login_payload = {
    "email": "admin@shafskyaviation.com",
    "password": "ShafskyAdmin2026!"
}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "X-Device-ID": "runtime_verify_device_001"
}
start = time.time()
res = client.post("/api/auth/login", json=login_payload, headers=headers)
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: POST /api/auth/login")
print("   Payload:", json.dumps(login_payload))
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200
login_data = res.json()["data"]
access_token = login_data["accessToken"]
refresh_token_1 = login_data["refreshToken"]

# DB Verification for Auth
db = SessionLocal()
db_token = db.scalar(select(RefreshToken).order_by(RefreshToken.created_at.desc()))
print("5. Database Query Result (RefreshToken Table):")
print(f"   Stored User ID: {db_token.user_id}")
print(f"   Stored Token Hash: {db_token.token_hash[:20]}...")
print(f"   Stored Device ID: {db_token.device_id}")
print(f"   Stored Browser: {db_token.browser}")
print(f"   Raw Token Match Check (Should be False for security): {db_token.token_hash == refresh_token_1}")
db.close()

# 6. POST /api/auth/refresh
print_separator("POST /api/auth/refresh")
refresh_payload = {
    "refreshToken": refresh_token_1
}
start = time.time()
res = client.post("/api/auth/refresh", json=refresh_payload, headers=headers)
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: POST /api/auth/refresh")
print("   Payload:", json.dumps(refresh_payload))
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200

# 7. POST /api/bookings
print_separator("POST /api/bookings")
now_utc = datetime.now(timezone.utc)
dep_time = (now_utc + timedelta(days=2)).isoformat()
arr_time = (now_utc + timedelta(days=2, hours=3)).isoformat()

booking_payload = {
    "passenger_name": "Lord Randolph",
    "passenger_email": "randolph@shafsky.com",
    "passenger_phone": "+919876543210",
    "flight_num": "AI101",
    "origin_code": "DEL",
    "dest_code": "BOM",
    "departure_time": dep_time,
    "arrival_time": arr_time,
    "service_type": "MEET_AND_GREET",
    "selected_services": {"lounge_access": True},
    "total_amount": 15000.0,
    "currency": "INR",
    "notes": "VIP Airport Assist"
}
start = time.time()
res = client.post("/api/bookings", json=booking_payload, headers={"Authorization": f"Bearer {access_token}"})
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: POST /api/bookings")
print("   Payload:", json.dumps(booking_payload))
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code in [200, 201]
booking_ref = res.json()["data"]["bookingRef"]

# DB Verification for Booking
db = SessionLocal()
db_booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
print("5. Database Query Result (Booking Table):")
print(f"   Booking ID: {db_booking.id}")
print(f"   Booking Reference: {db_booking.booking_ref}")
print(f"   Status: {db_booking.status}")
print(f"   Flight Number: {db_booking.flight_num}")
print(f"   Total Amount: {db_booking.total_amount} INR")
db.close()

# 8. GET /api/admin/observability/dashboard
print_separator("GET /api/admin/observability/dashboard")
start = time.time()
res = client.get("/api/admin/observability/dashboard", headers={"Authorization": f"Bearer {access_token}"})
resp_time = round((time.time() - start) * 1000, 2)
print("1. Request: GET /api/admin/observability/dashboard")
print(f"2. Status Code: {res.status_code}")
print(f"3. Response Time: {resp_time} ms")
print("4. Response Payload:")
print(json.dumps(res.json(), indent=2))
assert res.status_code == 200

print("\n" + "=" * 80)
print(" ALL 8 REQUIRED PRODUCTION ENDPOINTS VERIFIED WITH 100% SUCCESS!")
print(" RELEASE DECISION: GO FOR PRODUCTION DEPLOYMENT")
print("=" * 80)
