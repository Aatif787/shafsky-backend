"""
Service layer for Website Contact Management System.
Handles retrieval, normalization, persistence, and transactional updates of service contact numbers.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, cast
from sqlalchemy import Table, inspect, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.schema import WebsiteContactSetting
from app.schemas.contact_settings import (
    CANONICAL_SERVICES,
    CANONICAL_SERVICE_DICT,
    CANONICAL_SERVICE_KEYS,
    ContactSettingsUpdateRequest,
    ContactSlotItem,
    PublicContactSlot,
    PublicServiceContact,
    ServiceContactConfig,
)

logger = logging.getLogger("shafsky.contact_settings")


class ContactSettingsService:
    @classmethod
    def ensure_initialized(cls, db: Session) -> None:
        """
        Guarantees that all 6 canonical services with 2 slots each exist in the database.
        Idempotent: will not create duplicates or overwrite existing data.
        """
        try:
            bind = db.get_bind()
            inspector = inspect(bind)
            if "website_contact_settings" not in inspector.get_table_names():
                table_obj = cast(Table, WebsiteContactSetting.__table__)
                Base.metadata.create_all(bind=bind, tables=[table_obj], checkfirst=True)
        except Exception as e:
            logger.warning("Could not verify/create WebsiteContactSetting table: %s", e)

        existing_rows = db.scalars(select(WebsiteContactSetting)).all()
        existing_pairs = {(r.service_key, r.slot_index) for r in existing_rows}

        needs_commit = False
        for service_key, service_name in CANONICAL_SERVICES:
            for slot in (1, 2):
                if (service_key, slot) not in existing_pairs:
                    new_setting = WebsiteContactSetting(
                        id=uuid.uuid4(),
                        service_key=service_key,
                        service_name=service_name,
                        slot_index=slot,
                        country_code="",
                        phone_number="",
                        full_phone_number="",
                        is_active=True,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                    db.add(new_setting)
                    needs_commit = True

        if needs_commit:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning("Could not auto-seed WebsiteContactSetting: %s", e)

    @classmethod
    def get_admin_contact_settings(cls, db: Session) -> List[ServiceContactConfig]:
        """
        Returns all six services and both configured slots for authorized admins.
        Always returns exactly 6 services, each with slot 1 and slot 2 in order.
        """
        cls.ensure_initialized(db)

        rows = db.scalars(
            select(WebsiteContactSetting).order_by(
                WebsiteContactSetting.service_key, WebsiteContactSetting.slot_index
            )
        ).all()

        by_service: Dict[str, Dict[int, WebsiteContactSetting]] = {
            k: {} for k in CANONICAL_SERVICE_KEYS
        }
        for r in rows:
            if r.service_key in by_service:
                by_service[r.service_key][r.slot_index] = r

        result: List[ServiceContactConfig] = []
        for service_key, default_name in CANONICAL_SERVICES:
            slots_map = by_service.get(service_key, {})
            slots_list: List[ContactSlotItem] = []
            service_display_name = default_name

            for slot_idx in (1, 2):
                row = slots_map.get(slot_idx)
                if row:
                    service_display_name = row.service_name or default_name
                    slots_list.append(
                        ContactSlotItem(
                            slot_index=slot_idx,
                            country_code=row.country_code or "",
                            phone_number=row.phone_number or "",
                            full_phone_number=row.full_phone_number or "",
                            is_active=row.is_active,
                        )
                    )
                else:
                    slots_list.append(
                        ContactSlotItem(
                            slot_index=slot_idx,
                            country_code="",
                            phone_number="",
                            full_phone_number="",
                            is_active=True,
                        )
                    )

            result.append(
                ServiceContactConfig(
                    service_key=service_key,
                    service_name=service_display_name,
                    slots=slots_list,
                )
            )

        return result

    @classmethod
    def get_public_contact_settings(cls, db: Session) -> Dict[str, PublicServiceContact]:
        """
        Returns active, configured phone numbers for public consumption.
        Empty or disabled slots are excluded so the public website never receives broken links.
        """
        cls.ensure_initialized(db)

        rows = db.scalars(
            select(WebsiteContactSetting)
            .where(WebsiteContactSetting.is_active.is_(True))
            .order_by(WebsiteContactSetting.service_key, WebsiteContactSetting.slot_index)
        ).all()

        data: Dict[str, PublicServiceContact] = {}
        for service_key, default_name in CANONICAL_SERVICES:
            data[service_key] = PublicServiceContact(service_name=default_name, contacts=[])

        for r in rows:
            if r.service_key not in data:
                continue
            phone = (r.phone_number or "").strip()
            full_phone = (r.full_phone_number or "").strip()
            if phone and full_phone:
                data[r.service_key].contacts.append(
                    PublicContactSlot(
                        country_code=r.country_code or "",
                        phone_number=phone,
                        full_phone_number=full_phone,
                    )
                )

        return data

    @classmethod
    def update_contact_settings(
        cls, db: Session, payload: ContactSettingsUpdateRequest
    ) -> List[ServiceContactConfig]:
        """
        Atomically updates the requested service slots in a database transaction.
        Normalizes country codes and phone numbers.
        """
        cls.ensure_initialized(db)

        for s_update in payload.services:
            key = s_update.service_key
            for slot_in in s_update.slots:
                slot_idx = slot_in.slot_index

                row = db.scalar(
                    select(WebsiteContactSetting).where(
                        WebsiteContactSetting.service_key == key,
                        WebsiteContactSetting.slot_index == slot_idx,
                    )
                )
                if not row:
                    row = WebsiteContactSetting(
                        id=uuid.uuid4(),
                        service_key=key,
                        service_name=CANONICAL_SERVICE_DICT.get(key, key),
                        slot_index=slot_idx,
                    )
                    db.add(row)

                raw_phone = (slot_in.phone_number or "").strip()
                cleaned_phone = re.sub(r"[\s\-\(\)\.]", "", raw_phone)

                if cleaned_phone:
                    country_code = (slot_in.country_code or "").strip()
                    if not country_code:
                        country_code = "+91"
                    elif not country_code.startswith("+"):
                        country_code = f"+{country_code}"

                    # Normalize full_phone_number e.g. +919599087959
                    full_phone = f"{country_code}{cleaned_phone}"
                    row.country_code = country_code
                    row.phone_number = cleaned_phone
                    row.full_phone_number = full_phone
                else:
                    # Slot cleared
                    row.country_code = ""
                    row.phone_number = ""
                    row.full_phone_number = ""

                row.is_active = slot_in.is_active if slot_in.is_active is not None else True
                row.updated_at = datetime.now(timezone.utc)

        db.commit()
        return cls.get_admin_contact_settings(db)
