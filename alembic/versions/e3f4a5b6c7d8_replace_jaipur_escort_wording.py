"""Replace Jaipur departure escort wording with ASSIST GUEST UPTO BOARDING GATE ACCESS

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-18 15:30:00.000000
"""

import json
import re
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

JAI_ESCORT_GATE_PATTERN = re.compile(
    r"^(Escort to the Boarding Gate\.?|Escort to Boarding Gate\.?|ASSIST GUEST UPTO BORDING GATE ACCESS\.?)$",
    re.IGNORECASE,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "airport_services" in tables and "supported_airports" in tables:
        airport_services_table = sa.table(
            "airport_services",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("airport_id", postgresql.UUID(as_uuid=True)),
            sa.column("features", sa.JSON),
        )

        jai_row = bind.execute(sa.text("SELECT id FROM supported_airports WHERE iata_code = 'JAI'")).first()
        if not jai_row:
            return

        jai_id = jai_row[0]

        rows = bind.execute(
            sa.text(f"SELECT id, features FROM airport_services WHERE airport_id = '{jai_id}' AND features IS NOT NULL")
        ).fetchall()

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
                if JAI_ESCORT_GATE_PATTERN.match(stripped):
                    new_feats.append("ASSIST GUEST UPTO BOARDING GATE ACCESS")
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
    pass
