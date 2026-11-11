"""Super Admin WhatsApp notification routing API tests.

Called by pytest. No earlier test covers these endpoints. The user asked to
verify Super Admin access, 403 for ADMIN and OPERATIONS_MANAGER, 401 when
unauthenticated, recipient and route rules, env import, audit rows, rollback,
and unchanged environment dispatch.
"""

from __future__ import annotations

import inspect
import socket
import uuid
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.models.notification_routing import (
    ROUTING_MODE_ENV,
    NotificationRecipient,
    NotificationServiceRoute,
    NotificationSettings,
)
from app.models.schema import AuditLog, Role, UserAuth
from app.routers import notification_routing_router
from app.schemas.notification_routing import NOTIFICATION_SERVICE_KEYS, RecipientCreate
from app.services.auth_service import AuthService
from app.services.notification_routing_service import NotificationRoutingService
from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin
from app.services.whatsapp_department_service import WhatsAppDepartmentService

PREFIX = "/api/admin/whatsapp-notifications"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    UserAuth.__table__.create(engine)
    AuditLog.__table__.create(engine)
    NotificationRecipient.__table__.create(engine)
    NotificationServiceRoute.__table__.create(engine)
    NotificationSettings.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_user(db, role: Role, email: str) -> UserAuth:
    user = UserAuth(
        id=uuid.uuid4(),
        email=email,
        password_hash="test-hash",
        role=role,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _token(user: UserAuth, role: str) -> str:
    return AuthService.create_access_token(
        {
            "sub": str(user.id),
            "user_id": str(user.id),
            "email": user.email,
            "role": role,
        }
    )


@pytest.fixture()
def users(db):
    return {
        "super": _add_user(db, Role.SUPER_ADMIN, "super-routing@shafsky.test"),
        "admin": _add_user(db, Role.ADMIN, "admin-routing@shafsky.test"),
        "ops": _add_user(db, Role.OPERATIONS_MANAGER, "ops-routing@shafsky.test"),
    }


@pytest.fixture()
def tokens(users):
    return {
        "super": _token(users["super"], "SUPER_ADMIN"),
        "admin": _token(users["admin"], "ADMIN"),
        "ops": _token(users["ops"], "OPERATIONS_MANAGER"),
    }


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(notification_routing_router.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_recipient(client: TestClient, token: str, phone: str, label: str = "Desk") -> dict:
    response = client.post(
        f"{PREFIX}/recipients",
        json={"phone_number": phone, "label": label},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _protected_calls(recipient_id: str):
    return [
        ("get", f"{PREFIX}/config", None),
        ("get", f"{PREFIX}/recipients", None),
        ("post", f"{PREFIX}/recipients", {"phone_number": "9876543210", "label": "Blocked"}),
        ("patch", f"{PREFIX}/recipients/{recipient_id}", {"label": "Blocked"}),
        ("delete", f"{PREFIX}/recipients/{recipient_id}", None),
        ("put", f"{PREFIX}/recipients/{recipient_id}/services", {"service_keys": ["Lounge"]}),
        ("post", f"{PREFIX}/adopt-env-routing", None),
        ("get", f"{PREFIX}/services", None),
        ("get", f"{PREFIX}/routes", None),
        ("put", f"{PREFIX}/routes/Lounge", {"assignments": []}),
        ("get", f"{PREFIX}/settings", None),
        ("patch", f"{PREFIX}/settings", {"routing_mode": "database"}),
        ("post", f"{PREFIX}/import-env-recipients", None),
    ]


def test_routes_are_not_public():
    paths = [getattr(route, "path", "") for route in notification_routing_router.router.routes]
    assert paths
    assert all(path.startswith(PREFIX) for path in paths)
    assert "Railway" not in NOTIFICATION_SERVICE_KEYS
    assert "Hotel" in NOTIFICATION_SERVICE_KEYS
    assert "Hotels" in NOTIFICATION_SERVICE_KEYS
    assert "Travel Support" in NOTIFICATION_SERVICE_KEYS
    assert "Travel Services" in NOTIFICATION_SERVICE_KEYS


def test_unauthenticated_requests_are_rejected(client, tokens):
    recipient = _create_recipient(client, tokens["super"], "9876543210")
    for method, url, body in _protected_calls(recipient["id"]):
        kwargs = {}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_admin_and_operations_manager_are_forbidden(client, tokens):
    recipient = _create_recipient(client, tokens["super"], "9876543210", "Keep")
    before = client.get(f"{PREFIX}/recipients", headers=_auth(tokens["super"])).json()["data"]
    for role in ("admin", "ops"):
        for method, url, body in _protected_calls(recipient["id"]):
            kwargs = {"headers": _auth(tokens[role])}
            if body is not None:
                kwargs["json"] = body
            response = getattr(client, method)(url, **kwargs)
            assert response.status_code == 403, (role, method, url, response.text)
    after = client.get(f"{PREFIX}/recipients", headers=_auth(tokens["super"])).json()["data"]
    settings_row = client.get(f"{PREFIX}/settings", headers=_auth(tokens["super"])).json()["data"]
    assert [row["id"] for row in after] == [row["id"] for row in before]
    assert after[0]["label"] == "Keep"
    assert settings_row["routing_mode"] == "env"


def test_admin_can_choose_services_edit_and_delete_live_numbers(client, tokens, monkeypatch):
    from app.schemas.notification_routing import NOTIFICATION_SERVICE_KEYS

    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9822222222",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
    )

    adopted = client.post(f"{PREFIX}/adopt-env-routing", headers=_auth(tokens["super"]))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["data"]["routing_mode"] == "database"
    assert adopted.json()["data"]["recipients_created"] == 2
    assert adopted.json()["data"]["routes_created"] == 2 * len(NOTIFICATION_SERVICE_KEYS)

    recipients = client.get(f"{PREFIX}/recipients", headers=_auth(tokens["super"])).json()["data"]
    first = next(item for item in recipients if item["phone_number"] == "919811111111")
    second = next(item for item in recipients if item["phone_number"] == "919822222222")

    chosen = client.put(
        f"{PREFIX}/recipients/{first['id']}/services",
        json={"service_keys": ["Lounge", "Private Charter"]},
        headers=_auth(tokens["super"]),
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["data"]["service_keys"] == ["Lounge", "Private Charter"]

    routes = client.get(f"{PREFIX}/routes", headers=_auth(tokens["super"])).json()["data"]
    lounge = next(item for item in routes if item["service_key"] == "Lounge")
    hotel = next(item for item in routes if item["service_key"] == "Hotel")
    assert first["id"] in [item["recipient_id"] for item in lounge["assignments"]]
    assert first["id"] not in [item["recipient_id"] for item in hotel["assignments"]]
    assert second["id"] in [item["recipient_id"] for item in hotel["assignments"]]

    again = client.post(f"{PREFIX}/adopt-env-routing", headers=_auth(tokens["super"]))
    assert again.json()["data"]["adopted"] is False
    routes_again = client.get(f"{PREFIX}/routes", headers=_auth(tokens["super"])).json()["data"]
    hotel_again = next(item for item in routes_again if item["service_key"] == "Hotel")
    assert first["id"] not in [item["recipient_id"] for item in hotel_again["assignments"]]

    edited = client.patch(
        f"{PREFIX}/recipients/{first['id']}",
        json={"label": "Desk", "phone_number": "9833333333"},
        headers=_auth(tokens["super"]),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["phone_number"] == "919833333333"

    deleted = client.delete(f"{PREFIX}/recipients/{first['id']}", headers=_auth(tokens["super"]))
    assert deleted.status_code == 200, deleted.text
    remaining = [item["id"] for item in client.get(f"{PREFIX}/recipients", headers=_auth(tokens["super"])).json()["data"]]
    assert first["id"] not in remaining
    assert second["id"] in remaining


def test_settings_shows_the_backend_numbers_receiving_notifications(client, tokens, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9822222222",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="9833333333",
        ),
    )

    data = client.get(f"{PREFIX}/settings", headers=_auth(tokens["super"])).json()["data"]

    assert data["routing_mode"] == "env"
    assert data["live_recipients"] == [
        {"label": "Environment recipient 1", "phone_number": "919811111111"},
        {"label": "Environment recipient 2", "phone_number": "919822222222"},
    ]


def test_super_admin_can_manage_recipients_and_routes(client, tokens, db, users):
    created = _create_recipient(client, tokens["super"], "9876543210", "Desk")
    assert created["phone_number"] == "919876543210"
    assert created["is_active"] is True

    edited = client.patch(
        f"{PREFIX}/recipients/{created['id']}",
        json={"label": "Ops desk", "phone_number": "+91 98765 43211"},
        headers=_auth(tokens["super"]),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["label"] == "Ops desk"
    assert edited.json()["data"]["phone_number"] == "919876543211"

    disabled = client.patch(
        f"{PREFIX}/recipients/{created['id']}",
        json={"is_active": False},
        headers=_auth(tokens["super"]),
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["is_active"] is False
    enabled = client.patch(
        f"{PREFIX}/recipients/{created['id']}",
        json={"is_active": True},
        headers=_auth(tokens["super"]),
    )
    assert enabled.json()["data"]["is_active"] is True

    second = _create_recipient(client, tokens["super"], "9988776655", "Second")
    lounge = client.put(
        f"{PREFIX}/routes/{quote('Lounge', safe='')}",
        json={
            "assignments": [
                {"recipient_id": created["id"], "is_active": True},
                {"recipient_id": second["id"], "is_active": True},
            ]
        },
        headers=_auth(tokens["super"]),
    )
    assert lounge.status_code == 200, lounge.text
    assert len(lounge.json()["data"]["effective_recipients"]) == 2

    hotels = client.put(
        f"{PREFIX}/routes/{quote('Hotels', safe='')}",
        json={"assignments": [{"recipient_id": created["id"]}]},
        headers=_auth(tokens["super"]),
    )
    hotel = client.put(
        f"{PREFIX}/routes/{quote('Hotel', safe='')}",
        json={"assignments": [{"recipient_id": created["id"]}]},
        headers=_auth(tokens["super"]),
    )
    assert hotels.status_code == 200
    assert hotel.status_code == 200
    assert hotels.json()["data"]["service_key"] == "Hotels"
    assert hotel.json()["data"]["service_key"] == "Hotel"

    removed = client.put(
        f"{PREFIX}/routes/Lounge",
        json={"assignments": [{"recipient_id": second["id"]}]},
        headers=_auth(tokens["super"]),
    )
    assert removed.status_code == 200
    assert [item["recipient_id"] for item in removed.json()["data"]["assignments"]] == [second["id"]]

    services = client.get(f"{PREFIX}/services", headers=_auth(tokens["super"]))
    keys = [item["service_key"] for item in services.json()["data"]]
    assert keys == list(NOTIFICATION_SERVICE_KEYS)
    assert "Railway" not in keys

    cargo = client.put(
        f"{PREFIX}/routes/{quote('Cargo & Logistics', safe='')}",
        json={"assignments": [{"recipient_id": created["id"]}]},
        headers=_auth(tokens["super"]),
    )
    assert cargo.status_code == 200, cargo.text
    assert cargo.json()["data"]["service_key"] == "Cargo & Logistics"

    unknown = client.put(
        f"{PREFIX}/routes/{quote('Railway Station', safe='')}",
        json={"assignments": []},
        headers=_auth(tokens["super"]),
    )
    assert unknown.status_code == 400

    config = client.get(f"{PREFIX}/config", headers=_auth(tokens["super"]))
    assert config.status_code == 200
    assert config.json()["data"]["routing_mode"] == "env"

    logs = db.scalars(select(AuditLog).where(AuditLog.resource_type == "WHATSAPP_NOTIFY_CONFIG")).all()
    actions = {log.action for log in logs}
    assert "RECIPIENT_ADDED" in actions
    assert "RECIPIENT_EDITED" in actions
    assert "RECIPIENT_ENABLED" in actions
    assert "RECIPIENT_DISABLED" in actions
    assert "SERVICE_ROUTING_CHANGED" in actions
    assert all(log.actor_id == users["super"].id for log in logs)
    assert all(log.actor_email == users["super"].email for log in logs)
    assert all("access_token" not in str(log.details).lower() for log in logs)


def test_duplicate_phone_and_invalid_phone_are_rejected(client, tokens):
    _create_recipient(client, tokens["super"], "9876543210")
    duplicate = client.post(
        f"{PREFIX}/recipients",
        json={"phone_number": "+91 98765 43210"},
        headers=_auth(tokens["super"]),
    )
    assert duplicate.status_code == 409
    invalid = client.post(
        f"{PREFIX}/recipients",
        json={"phone_number": "12345"},
        headers=_auth(tokens["super"]),
    )
    assert invalid.status_code == 422
    bad_indian = client.post(
        f"{PREFIX}/recipients",
        json={"phone_number": "1111111111"},
        headers=_auth(tokens["super"]),
    )
    assert bad_indian.status_code == 422


def test_duplicate_assignment_in_one_service_is_rejected(client, tokens):
    recipient = _create_recipient(client, tokens["super"], "9876543210")
    response = client.put(
        f"{PREFIX}/routes/Ticketing",
        json={
            "assignments": [
                {"recipient_id": recipient["id"]},
                {"recipient_id": recipient["id"]},
            ]
        },
        headers=_auth(tokens["super"]),
    )
    assert response.status_code == 422


def test_disabled_recipient_is_not_effective_and_delete_removes_assignments(client, tokens, db):
    recipient = _create_recipient(client, tokens["super"], "9876543210")
    other = _create_recipient(client, tokens["super"], "9988776655")
    assigned = client.put(
        f"{PREFIX}/routes/Transport",
        json={
            "assignments": [
                {"recipient_id": recipient["id"], "is_active": True},
                {"recipient_id": other["id"], "is_active": False},
            ]
        },
        headers=_auth(tokens["super"]),
    )
    assert assigned.status_code == 200, assigned.text
    effective_ids = [item["id"] for item in assigned.json()["data"]["effective_recipients"]]
    assert effective_ids == [recipient["id"]]

    disabled = client.patch(
        f"{PREFIX}/recipients/{recipient['id']}",
        json={"is_active": False},
        headers=_auth(tokens["super"]),
    )
    assert disabled.status_code == 200
    current = client.get(f"{PREFIX}/routes", headers=_auth(tokens["super"])).json()["data"]
    transport = next(item for item in current if item["service_key"] == "Transport")
    assert transport["effective_recipients"] == []
    assert len(transport["assignments"]) == 2

    active_delete = client.delete(
        f"{PREFIX}/recipients/{other['id']}",
        headers=_auth(tokens["super"]),
    )
    assert active_delete.status_code == 200, active_delete.text
    remaining = db.scalars(select(NotificationRecipient).where(NotificationRecipient.id == uuid.UUID(other["id"]))).first()
    assert remaining is None
    after_delete = client.get(f"{PREFIX}/routes", headers=_auth(tokens["super"])).json()["data"]
    transport_after = next(item for item in after_delete if item["service_key"] == "Transport")
    assert [item["recipient_id"] for item in transport_after["assignments"]] == [recipient["id"]]
    delete_log = db.scalars(
        select(AuditLog).where(AuditLog.action == "RECIPIENT_DELETED")
    ).first()
    assert delete_log is not None
    assert delete_log.details["previous"]["phone_number"] == "919988776655"


def test_unknown_recipient_does_not_change_existing_routes(client, tokens, db):
    recipient = _create_recipient(client, tokens["super"], "9876543210")
    saved = client.put(
        f"{PREFIX}/routes/Lounge",
        json={"assignments": [{"recipient_id": recipient["id"]}]},
        headers=_auth(tokens["super"]),
    )
    assert saved.status_code == 200
    failed = client.put(
        f"{PREFIX}/routes/Lounge",
        json={
            "assignments": [
                {"recipient_id": recipient["id"]},
                {"recipient_id": str(uuid.uuid4())},
            ]
        },
        headers=_auth(tokens["super"]),
    )
    assert failed.status_code == 404
    routes = db.scalars(select(NotificationServiceRoute)).all()
    assert len(routes) == 1
    assert routes[0].recipient_id == uuid.UUID(recipient["id"])


def test_settings_default_to_env_and_import_is_idempotent(client, tokens, db, monkeypatch):
    initial = client.get(f"{PREFIX}/settings", headers=_auth(tokens["super"]))
    assert initial.status_code == 200
    assert initial.json()["data"]["routing_mode"] == ROUTING_MODE_ENV

    existing = _create_recipient(client, tokens["super"], "9599087959", "Desk")
    client.patch(
        f"{PREFIX}/recipients/{existing['id']}",
        json={"is_active": False},
        headers=_auth(tokens["super"]),
    )
    monkeypatch.setattr(settings, "WHATSAPP_BOOKING_NOTIFY_PHONE_1", "9876543210")
    monkeypatch.setattr(settings, "WHATSAPP_BOOKING_NOTIFY_PHONE_2", "+91 98765 43210")
    monkeypatch.setattr(settings, "WHATSAPP_BOOKING_NOTIFY_PHONE_3", "123")
    monkeypatch.setattr(settings, "WHATSAPP_BOOKING_NOTIFY_PHONE_4", "")
    monkeypatch.setattr(settings, "WHATSAPP_BOOKING_NOTIFY_PHONE_5", "9599087959")
    monkeypatch.setattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", "9000000001")

    imported = client.post(f"{PREFIX}/import-env-recipients", headers=_auth(tokens["super"]))
    assert imported.status_code == 200, imported.text
    body = imported.json()["data"]
    assert body["routing_mode"] == "env"
    assert body["routes_created"] == 0
    assert len(body["created"]) == 1
    assert body["created"][0]["phone_number"] == "919876543210"
    assert body["skipped_existing"] == ["919876543210", "919599087959"]
    assert body["skipped_invalid"] == ["WHATSAPP_BOOKING_NOTIFY_PHONE_3"]
    assert body["skipped_empty"] == ["WHATSAPP_BOOKING_NOTIFY_PHONE_4"]

    kept = db.get(NotificationRecipient, uuid.UUID(existing["id"]))
    assert kept.label == "Desk"
    assert kept.is_active is False
    assert db.scalar(select(NotificationServiceRoute).limit(1)) is None
    assert "919000000001" not in {row.phone_number for row in db.scalars(select(NotificationRecipient)).all()}

    again = client.post(f"{PREFIX}/import-env-recipients", headers=_auth(tokens["super"]))
    assert again.json()["data"]["created"] == []
    assert again.json()["data"]["routing_mode"] == "env"
    import_logs = db.scalars(
        select(AuditLog).where(AuditLog.action == "ENV_RECIPIENTS_IMPORTED")
    ).all()
    assert len(import_logs) == 2


def test_changing_stored_mode_does_not_change_live_dispatch(client, tokens, monkeypatch):
    switched = client.patch(
        f"{PREFIX}/settings",
        json={"routing_mode": "database"},
        headers=_auth(tokens["super"]),
    )
    assert switched.status_code == 200
    assert switched.json()["data"]["routing_mode"] == "database"
    restored = client.patch(
        f"{PREFIX}/settings",
        json={"routing_mode": "env"},
        headers=_auth(tokens["super"]),
    )
    assert restored.json()["data"]["routing_mode"] == "env"

    class _Settings:
        WHATSAPP_BOOKING_NOTIFY_PHONE_1 = "9876543210"
        WHATSAPP_BOOKING_NOTIFY_PHONE_2 = ""
        WHATSAPP_BOOKING_NOTIFY_PHONE_3 = "919876543210"
        WHATSAPP_BOOKING_NOTIFY_PHONE_4 = ""
        WHATSAPP_BOOKING_NOTIFY_PHONE_5 = ""
        WHATSAPP_OFFICER_NOTIFY_PHONE = "9000000000"

    monkeypatch.setattr("app.services.whatsapp_department_service.settings", _Settings())
    assert WhatsAppDepartmentService.get_recipient_phone_numbers() == ["919876543210"]
    source = inspect.getsource(WhatsAppDepartmentFormattingMixin.get_recipient_phone_numbers)
    assert "notification_recipients" not in source
    assert "routing_mode" not in source


def test_failed_commit_does_not_keep_a_recipient(db, users):
    actor = {
        "user_id": str(users["super"].id),
        "email": users["super"].email,
        "role": "SUPER_ADMIN",
    }

    def boom():
        raise IntegrityError("INSERT", {}, Exception("forced"))

    db.commit = boom
    with pytest.raises(HTTPException) as caught:
        NotificationRoutingService.add_recipient(
            db,
            RecipientCreate(phone_number="9876543210", label="Ops"),
            actor,
        )
    assert caught.value.status_code == 409
    db.rollback()
    assert db.scalars(select(NotificationRecipient)).all() == []
    assert db.scalars(select(AuditLog)).all() == []


def test_endpoints_create_missing_routing_tables(monkeypatch):
    import app.services.notification_routing_service as routing_module
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UserAuth.__table__.create(engine)
    AuditLog.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(routing_module, "_schema_ready", False)
    try:
        user = _add_user(session, Role.SUPER_ADMIN, "missing-tables@shafsky.test")
        app = FastAPI()
        app.include_router(notification_routing_router.router)
        app.dependency_overrides[get_db] = lambda: session
        with TestClient(app) as test_client:
            unauthenticated = test_client.get(f"{PREFIX}/recipients")
            assert unauthenticated.status_code == 401
            assert "notification_recipients" not in sa_inspect(engine).get_table_names()

            token = _token(user, "SUPER_ADMIN")
            recipients = test_client.get(f"{PREFIX}/recipients", headers=_auth(token))
            routing = test_client.get(f"{PREFIX}/settings", headers=_auth(token))

        assert recipients.status_code == 200
        assert recipients.json()["data"] == []
        assert routing.status_code == 200
        assert routing.json()["data"]["routing_mode"] == "env"
        assert {
            "notification_recipients",
            "notification_service_routes",
            "notification_settings",
        } <= set(sa_inspect(engine).get_table_names())
    finally:
        session.close()
        engine.dispose()


def test_postgres_missing_routing_tables_are_created_in_rolled_back_schema(monkeypatch):
    import app.services.notification_routing_service as routing_module
    from sqlalchemy import inspect as sa_inspect, text

    url = _local_postgres_test_url()
    if url is None:
        pytest.skip("Local Postgres test database is not running. Production was not migrated.")

    engine = create_engine(url)
    schema = f"routing_check_{uuid.uuid4().hex[:8]}"
    connection = engine.connect()
    transaction = connection.begin()
    monkeypatch.setattr(routing_module, "_schema_ready", False)
    try:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        session = sessionmaker(bind=connection)()
        NotificationRoutingService.ensure_schema(session)
        tables = set(sa_inspect(connection).get_table_names(schema=schema))
        assert {
            "notification_recipients",
            "notification_service_routes",
            "notification_settings",
        } <= tables
        session.close()
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_main_app_mounts_the_router():
    from app.main import app

    paths = app.openapi()["paths"]
    assert f"{PREFIX}/config" in paths
    assert f"{PREFIX}/import-env-recipients" in paths


def _local_postgres_test_url() -> str | None:
    url = "postgresql://shafsky:shafsky@127.0.0.1:55432/shafsky_test"
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost"} or "test" not in (parsed.path or ""):
        return None
    try:
        with socket.create_connection((host, parsed.port or 5432), timeout=0.3):
            return url
    except OSError:
        return None


def test_postgres_recipient_and_settings_roundtrip():
    url = _local_postgres_test_url()
    if url is None:
        pytest.skip("Local Postgres test database is not running. Production was not migrated.")

    engine = create_engine(url)
    UserAuth.__table__.create(engine, checkfirst=True)
    AuditLog.__table__.create(engine, checkfirst=True)
    NotificationRecipient.__table__.create(engine, checkfirst=True)
    NotificationServiceRoute.__table__.create(engine, checkfirst=True)
    NotificationSettings.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(bind=engine)
    session = factory()
    user_id = uuid.uuid4()
    recipient_id = None
    try:
        user = UserAuth(
            id=user_id,
            email=f"routing-{user_id.hex[:8]}@shafsky.test",
            password_hash="test-hash",
            role=Role.SUPER_ADMIN,
            is_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        actor = {"user_id": str(user_id), "email": user.email, "role": "SUPER_ADMIN"}
        created = NotificationRoutingService.add_recipient(
            session,
            RecipientCreate(phone_number="9000000001", label="Postgres"),
            actor,
        )
        recipient_id = created["id"]
        mode = NotificationRoutingService.get_settings(session)
        assert created["phone_number"] == "919000000001"
        assert mode["routing_mode"] == "env"
        duplicate = RecipientCreate(phone_number="919000000001")
        with pytest.raises(HTTPException) as caught:
            NotificationRoutingService.add_recipient(session, duplicate, actor)
        assert caught.value.status_code == 409
    finally:
        session.rollback()
        if recipient_id is not None:
            session.query(NotificationServiceRoute).filter(
                NotificationServiceRoute.recipient_id == recipient_id
            ).delete(synchronize_session=False)
            session.query(AuditLog).filter(AuditLog.resource_id == str(recipient_id)).delete(
                synchronize_session=False
            )
            session.query(NotificationRecipient).filter(NotificationRecipient.id == recipient_id).delete(
                synchronize_session=False
            )
        session.query(AuditLog).filter(AuditLog.actor_id == user_id).delete(synchronize_session=False)
        session.query(UserAuth).filter(UserAuth.id == user_id).delete(synchronize_session=False)
        session.commit()
        session.close()
        engine.dispose()
