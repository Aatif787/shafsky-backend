"""Schema and catalog invariants: Alembic head matches ORM; seed is idempotent."""

from __future__ import annotations

import importlib
from pathlib import Path

from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.models.journey_models import AirportService, Service, SupportedAirport
from app.seeds.seed_journey_data import run_seed


SQL_ONLY_TABLES = {"alembic_version", "idempotency_records"}


def _register_models() -> None:
    models_dir = Path(__file__).resolve().parents[1] / "app" / "models"
    for path in models_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        importlib.import_module(f"app.models.{path.stem}")


def test_orm_tables_exist_after_alembic_upgrade():
    _register_models()
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())
    missing = sorted(set(Base.metadata.tables) - db_tables)
    assert missing == [], f"ORM tables missing from migrated schema: {missing}"


def test_orm_columns_exist_on_migrated_tables():
    _register_models()
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())
    mismatches = []
    for name, table in Base.metadata.tables.items():
        if name not in db_tables:
            continue
        db_cols = {col["name"] for col in inspector.get_columns(name)}
        missing = sorted(set(table.columns.keys()) - db_cols)
        if missing:
            mismatches.append(f"{name}: {missing}")
    assert mismatches == [], "ORM columns missing from migrated tables: " + "; ".join(mismatches)


def test_sql_only_tables_are_intentional():
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())
    leftover = sorted((db_tables - set(Base.metadata.tables.keys())) - SQL_ONLY_TABLES)
    assert leftover == [], f"Unexpected extra tables without ORM models: {leftover}"


def test_seed_is_idempotent_and_catalog_invariants_hold():
    run_seed()
    run_seed()

    db = SessionLocal()
    try:
        airports = db.query(SupportedAirport).filter_by(is_active=True).all()
        iata_codes = [row.iata_code for row in airports]
        assert len(iata_codes) == len(set(iata_codes))
        assert "DXB" not in iata_codes
        required = {"DEL", "BOM", "TRV", "IXC", "IXR", "ATQ", "HYD"}
        assert required.issubset(set(iata_codes))

        del_row = db.query(SupportedAirport).filter_by(iata_code="DEL").one()
        assert del_row.city == "New Delhi"

        services = db.query(Service).all()
        assert {row.slug for row in services} >= {
            "platinum",
            "elite",
            "silver",
            "gold",
            "meet_greet",
            "porter",
            "buggy",
            "wheelchair",
            "transport",
        }

        bom = db.query(SupportedAirport).filter_by(iata_code="BOM").one()
        bom_transit = (
            db.query(AirportService)
            .filter_by(airport_id=bom.id, journey_type="TRANSIT", is_available=True)
            .count()
        )
        assert bom_transit >= 1

        dup_mappings = db.execute(
            text(
                """
                SELECT airport_id, service_id, journey_type, flight_type, terminal, COUNT(*)
                FROM airport_services
                GROUP BY 1, 2, 3, 4, 5
                HAVING COUNT(*) > 1
                """
            )
        ).fetchall()
        assert dup_mappings == []
    finally:
        db.close()
