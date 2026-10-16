"""Standardize airport service features and contact service names

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-16 21:00:00.000000
"""

import json
import re
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

AEROBRIDGE_PATTERN = re.compile(
    r'^(WELCOME GUEST FROM AEROBRIDGE\.?|WELCOME GUEST FROM THE AEROBRIDGE\.?|WELCOME AT THE AEROBRIDGE\.?|WELCOME GUEST FROM AEROBRIDGE \.?|WELCOME AT THE END OF THE AEROBRIDGE\.?|WELCOME IN THE END OF THE AEROBRIDGE\.?|WELCOME GUEST FROM END OF THE AEROBRIDGE\.?|Welcome at the End of the Aerobridge\.?|Welcome in the End of the Aerobridge\.?|Welcome at the Aerobridge)$',
    re.IGNORECASE,
)

CAR_PARKING_PATTERN = re.compile(
    r'^(Escort to the Car Parking Area|Escort to the Parking Area|DROP OFF TILL CAR PARKING AREA\.?|DROP OFF CAR PARKING AREA|DROP OFF TILL CAR PARKING\.?|ASSIST GUEST TILL THE PARKING AREA\.?|ASSIST GUEST TILL THE CAR PARKING AREA\.?)$',
    re.IGNORECASE,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Update website_contact_settings service_name
    if "website_contact_settings" in tables:
        try:
            bind.execute(
                sa.text(
                    "UPDATE website_contact_settings "
                    "SET service_name = 'Meet & Greet Lounge' "
                    "WHERE service_key = 'meet_greet_lounge' AND service_name = 'Meet & Greet & Lounge'"
                )
            )
        except Exception as e:
            print(f"[migration] Note updating website_contact_settings: {e}")

    # 2. Update airport_services features
    if "airport_services" in tables:
        airport_services_table = sa.table(
            "airport_services",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("features", sa.JSON),
        )

        rows = bind.execute(sa.text("SELECT id, features FROM airport_services WHERE features IS NOT NULL")).fetchall()
        for row in rows:
            row_id = row[0]
            feats = row[1]
            if not feats:
                continue

            if isinstance(feats, str):
                try:
                    feats_list = json.loads(feats)
                except Exception:
                    continue
            elif isinstance(feats, list):
                feats_list = feats
            else:
                continue

            modified = False
            new_feats = []
            for item in feats_list:
                if not isinstance(item, str):
                    new_feats.append(item)
                    continue

                stripped = item.strip()
                if AEROBRIDGE_PATTERN.match(stripped):
                    new_feats.append("WELCOME IN THE END OF THE AEROBRIDGE")
                    modified = True
                elif CAR_PARKING_PATTERN.match(stripped):
                    new_feats.append("ASSIST GUEST TILL THE CAR PARKING AREA")
                    modified = True
                else:
                    new_feats.append(item)

            if modified:
                bind.execute(
                    airport_services_table.update()
                    .where(airport_services_table.c.id == row_id)
                    .values(features=new_feats)
                )


def downgrade() -> None:
    # Non-destructive downgrade: no schema changes to roll back
    pass
