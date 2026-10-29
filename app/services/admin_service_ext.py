import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select
from fastapi import HTTPException

logger = logging.getLogger("shafsky.admin")


class AdminAirportPricingMixin:
    """Mixin for Airport Services & Pricing Matrix Management on AdminService."""

    @classmethod
    def list_airport_services(
        cls,
        db: Session,
        airport_code: Optional[str] = None,
        journey_type: Optional[str] = None,
        flight_type: Optional[str] = None,
        is_available: Optional[bool] = None,
        limit: int = 300,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        from app.services.service_airport_rules import normalize_iata, normalize_journey_type, normalize_flight_type

        stmt = (
            select(AirportService, SupportedAirport, Service)
            .join(SupportedAirport, AirportService.airport_id == SupportedAirport.id)
            .join(Service, AirportService.service_id == Service.id)
            .where(AirportService.deleted_at.is_(None))
        )

        if airport_code:
            clean_code = normalize_iata(airport_code)
            stmt = stmt.where(SupportedAirport.iata_code == clean_code)

        if journey_type:
            clean_jt = normalize_journey_type(journey_type)
            stmt = stmt.where(AirportService.journey_type == clean_jt)

        if flight_type:
            clean_ft = normalize_flight_type(flight_type) or flight_type.strip().upper()
            stmt = stmt.where(AirportService.flight_type.in_([clean_ft, "ALL"]))

        if is_available is not None:
            stmt = stmt.where(AirportService.is_available == is_available)

        stmt = stmt.order_by(
            SupportedAirport.iata_code,
            AirportService.journey_type,
            AirportService.flight_type,
            Service.display_order,
            AirportService.price
        ).limit(limit).offset(offset)

        rows = db.execute(stmt).all()
        results = []
        for aps, apt, svc in rows:
            results.append({
                "id": str(aps.id),
                "airport_id": str(apt.id),
                "airport_code": apt.iata_code,
                "airport_name": apt.airport_name,
                "city": apt.city,
                "service_id": str(svc.id),
                "service_name": svc.name,
                "service_slug": svc.slug,
                "price": float(aps.price),
                "currency": aps.currency or "INR",
                "journey_type": aps.journey_type,
                "flight_type": aps.flight_type,
                "terminal": aps.terminal or "All",
                "is_available": aps.is_available,
                "features": aps.features if isinstance(aps.features, list) else [],
                "inclusions": aps.features if isinstance(aps.features, list) else [],
                "short_description": aps.short_description or "",
                "min_booking_notice_hours": aps.min_booking_notice_hours or 0,
                "updated_at": aps.updated_at.isoformat() if aps.updated_at else None,
            })
        return results

    @classmethod
    def update_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        updates: Dict[str, Any],
        admin_email: str
    ) -> Dict[str, Any]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        old_details = {
            "price": float(mapping.price) if mapping.price is not None else 0.0,
            "currency": mapping.currency,
            "is_available": mapping.is_available,
            "terminal": mapping.terminal,
            "journey_type": mapping.journey_type,
            "flight_type": mapping.flight_type,
            "features": list(mapping.features) if isinstance(mapping.features, list) else [],
            "inclusions": list(mapping.features) if isinstance(mapping.features, list) else [],
        }

        if "price" in updates and updates["price"] is not None:
            try:
                new_price = float(updates["price"])
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Price must be a valid number.")
            if new_price <= 0:
                raise HTTPException(status_code=400, detail="Price must be greater than 0.")
            if new_price > 50000000:
                raise HTTPException(status_code=400, detail="Price exceeds maximum allowed limit.")
            mapping.price = round(new_price, 2)

        if "currency" in updates and updates["currency"]:
            curr = str(updates["currency"]).strip().upper()
            if len(curr) != 3:
                raise HTTPException(status_code=400, detail="Currency must be a 3-letter code (e.g., INR).")
            mapping.currency = curr

        if "is_available" in updates and updates["is_available"] is not None:
            mapping.is_available = bool(updates["is_available"])

        if "terminal" in updates and updates["terminal"] is not None:
            mapping.terminal = str(updates["terminal"]).strip()

        # Inclusions / features handling (accept either field name)
        features_input = None
        if "features" in updates and updates["features"] is not None:
            features_input = updates["features"]
        elif "inclusions" in updates and updates["inclusions"] is not None:
            features_input = updates["inclusions"]

        if features_input is not None:
            if isinstance(features_input, list):
                cleaned_features = [
                    str(f).strip().lstrip("•-*").strip()
                    for f in features_input
                    if str(f).strip()
                ]
                setattr(mapping, "features", cleaned_features)
                flag_modified(mapping, "features")
            elif isinstance(features_input, str):
                raw_str = features_input.strip()
                if "\n" in raw_str:
                    lines = [line.strip().lstrip("•-*").strip() for line in raw_str.split("\n")]
                else:
                    lines = [f.strip().lstrip("•-*").strip() for f in raw_str.split(",")]
                cleaned_features = [line for line in lines if line]
                setattr(mapping, "features", cleaned_features)
                flag_modified(mapping, "features")

        if "short_description" in updates and updates["short_description"] is not None:
            mapping.short_description = str(updates["short_description"]).strip()

        if "min_booking_notice_hours" in updates and updates["min_booking_notice_hours"] is not None:
            try:
                mapping.min_booking_notice_hours = max(0, int(updates["min_booking_notice_hours"]))
            except (ValueError, TypeError):
                pass

        try:
            mapping.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to commit airport service update: %s", err)
            raise HTTPException(status_code=500, detail="Database transaction failed during update.")

        # Audit log
        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="UPDATE_AIRPORT_SERVICE_PRICE",
                resource_type="AirportService",
                resource_id=str(mapping.id),
                details={
                    "old": old_details,
                    "new": {
                        "price": float(mapping.price) if mapping.price is not None else 0.0,
                        "currency": mapping.currency,
                        "is_available": mapping.is_available,
                        "terminal": mapping.terminal,
                        "features": list(mapping.features) if isinstance(mapping.features, list) else [],
                        "inclusions": list(mapping.features) if isinstance(mapping.features, list) else [],
                    }
                }
            )
        except Exception as audit_err:
            db.rollback()
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        try:
            from app.routers.journey_router import clear_services_catalog_cache
            clear_services_catalog_cache()
        except Exception:
            pass

        apt = db.scalar(select(SupportedAirport).where(SupportedAirport.id == mapping.airport_id))
        svc = db.scalar(select(Service).where(Service.id == mapping.service_id))

        return {
            "id": str(mapping.id),
            "airport_id": str(apt.id) if apt else "",
            "airport_code": apt.iata_code if apt else "",
            "airport_name": apt.airport_name if apt else "",
            "city": apt.city if apt else "",
            "service_id": str(svc.id) if svc else "",
            "service_name": svc.name if svc else "",
            "service_slug": svc.slug if svc else "",
            "price": float(mapping.price) if mapping.price is not None else 0.0,
            "currency": mapping.currency or "INR",
            "journey_type": mapping.journey_type,
            "flight_type": mapping.flight_type,
            "terminal": mapping.terminal or "All",
            "is_available": mapping.is_available,
            "features": mapping.features if isinstance(mapping.features, list) else [],
            "inclusions": mapping.features if isinstance(mapping.features, list) else [],
            "short_description": mapping.short_description or "",
            "min_booking_notice_hours": mapping.min_booking_notice_hours or 0,
            "updated_at": mapping.updated_at.isoformat() if mapping.updated_at else None,
        }

    @classmethod
    def create_airport_service(
        cls,
        db: Session,
        payload: Dict[str, Any],
        admin_email: str
    ) -> Dict[str, Any]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        from app.services.service_airport_rules import normalize_iata, normalize_journey_type, normalize_flight_type

        airport_code = normalize_iata(payload.get("airport_code") or payload.get("airport"))
        if not airport_code:
            raise HTTPException(status_code=400, detail="Airport code is required.")

        apt = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == airport_code))
        if not apt:
            raise HTTPException(status_code=404, detail=f"Supported airport '{airport_code}' not found.")

        service_slug = (payload.get("service_slug") or payload.get("service") or "").strip().lower()
        if not service_slug:
            raise HTTPException(status_code=400, detail="Service slug is required.")

        svc = db.scalar(select(Service).where(Service.slug == service_slug))
        if not svc:
            raise HTTPException(status_code=404, detail=f"Service '{service_slug}' not found.")

        try:
            price = float(payload.get("price", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Valid price is required.")
        if price <= 0:
            raise HTTPException(status_code=400, detail="Price must be greater than 0.")

        journey_type = normalize_journey_type(payload.get("journey_type"))
        flight_type = normalize_flight_type(payload.get("flight_type")) or "DOMESTIC"
        terminal = (payload.get("terminal") or "All").strip()
        currency = (payload.get("currency") or "INR").strip().upper()

        existing = db.scalar(
            select(AirportService).where(
                AirportService.airport_id == apt.id,
                AirportService.service_id == svc.id,
                AirportService.journey_type == journey_type,
                AirportService.flight_type == flight_type,
                AirportService.terminal == terminal,
            )
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Mapping already exists for {airport_code} - {service_slug} ({journey_type} {flight_type} {terminal})."
            )

        features = payload.get("features", [])
        if isinstance(features, str):
            features = [f.strip() for f in features.split(",") if f.strip()]

        new_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=apt.id,
            service_id=svc.id,
            journey_type=journey_type,
            flight_type=flight_type,
            terminal=terminal,
            price=round(price, 2),
            currency=currency,
            is_available=bool(payload.get("is_available", True)),
            features=features,
            short_description=payload.get("short_description") or svc.description or "",
            min_booking_notice_hours=int(payload.get("min_booking_notice_hours", 0)),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        try:
            db.add(new_mapping)
            db.commit()
            db.refresh(new_mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to create airport service mapping: %s", err)
            raise HTTPException(status_code=500, detail="Database transaction failed during creation.")

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="CREATE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=str(new_mapping.id),
                details={"airport": airport_code, "service": service_slug, "price": price}
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {
            "id": str(new_mapping.id),
            "airport_code": apt.iata_code,
            "airport_name": apt.airport_name,
            "service_name": svc.name,
            "service_slug": svc.slug,
            "price": new_mapping.price,
            "currency": new_mapping.currency,
            "journey_type": new_mapping.journey_type,
            "flight_type": new_mapping.flight_type,
            "terminal": new_mapping.terminal,
            "is_available": new_mapping.is_available,
            "features": new_mapping.features,
            "short_description": new_mapping.short_description,
            "min_booking_notice_hours": new_mapping.min_booking_notice_hours,
            "updated_at": new_mapping.updated_at.isoformat(),
        }

    @classmethod
    def recycle_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        admin_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        from app.models.journey_models import AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        if mapping.deleted_at is not None:
            raise HTTPException(status_code=400, detail="Airport service is already in the recycle bin.")

        admin_email = admin_context.get("email") or admin_context.get("sub") or "admin@shafskyaviation.com"
        admin_role = str(admin_context.get("role") or "SUPER_ADMIN")
        raw_uid = admin_context.get("user_id") or admin_context.get("userId")
        user_uuid: Optional[uuid.UUID] = None
        if raw_uid:
            try:
                user_uuid = uuid.UUID(str(raw_uid))
            except ValueError:
                pass

        now = datetime.now(timezone.utc)
        try:
            mapping.deleted_at = now
            mapping.is_available = False
            mapping.deleted_by_user_id = user_uuid
            mapping.deleted_by_email = admin_email
            mapping.deleted_by_role = admin_role
            mapping.updated_at = now
            db.commit()
            db.refresh(mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to recycle airport service %s: %s", mapping_id, err)
            raise HTTPException(status_code=500, detail="Database transaction failed during recycling.")

        try:
            from app.routers.journey_router import clear_services_catalog_cache
            clear_services_catalog_cache()
        except Exception:
            pass

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="RECYCLE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=str(mapping.id),
                details={"deleted_at": now.isoformat(), "deleted_by": admin_email}
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {
            "success": True,
            "id": str(mapping.id),
            "status": "recycled",
            "message": "Service successfully moved to recycle bin."
        }

    @classmethod
    def restore_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        admin_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        from app.models.journey_models import AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        if mapping.deleted_at is None:
            raise HTTPException(status_code=400, detail="Airport service is not in the recycle bin.")

        admin_email = admin_context.get("email") or admin_context.get("sub") or "admin@shafskyaviation.com"
        now = datetime.now(timezone.utc)
        try:
            mapping.deleted_at = None
            mapping.deleted_by_user_id = None
            mapping.deleted_by_email = None
            mapping.deleted_by_role = None
            mapping.is_available = True
            mapping.updated_at = now
            db.commit()
            db.refresh(mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to restore airport service %s: %s", mapping_id, err)
            raise HTTPException(status_code=500, detail="Database transaction failed during restoration.")

        try:
            from app.routers.journey_router import clear_services_catalog_cache
            clear_services_catalog_cache()
        except Exception:
            pass

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="RESTORE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=str(mapping.id),
                details={"restored_by": admin_email}
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {
            "success": True,
            "id": str(mapping.id),
            "status": "restored",
            "message": "Service successfully restored to active catalog."
        }

    @classmethod
    def list_recycled_airport_services(
        cls,
        db: Session,
        airport_code: Optional[str] = None,
        limit: int = 300,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        from app.services.service_airport_rules import normalize_iata

        stmt = (
            select(AirportService, SupportedAirport, Service)
            .join(SupportedAirport, AirportService.airport_id == SupportedAirport.id)
            .join(Service, AirportService.service_id == Service.id)
            .where(AirportService.deleted_at.is_not(None))
        )

        if airport_code:
            clean_code = normalize_iata(airport_code)
            stmt = stmt.where(SupportedAirport.iata_code == clean_code)

        stmt = stmt.order_by(
            AirportService.deleted_at.desc(),
            SupportedAirport.iata_code,
            AirportService.journey_type,
        ).limit(limit).offset(offset)

        rows = db.execute(stmt).all()
        results = []
        for aps, apt, svc in rows:
            results.append({
                "id": str(aps.id),
                "airport_id": str(apt.id),
                "airport_code": apt.iata_code,
                "airport_name": apt.airport_name,
                "city": apt.city,
                "service_id": str(svc.id),
                "service_name": svc.name,
                "service_slug": svc.slug,
                "price": float(aps.price),
                "currency": aps.currency or "INR",
                "journey_type": aps.journey_type,
                "flight_type": aps.flight_type,
                "terminal": aps.terminal or "All",
                "is_available": aps.is_available,
                "deleted_at": aps.deleted_at.isoformat() if aps.deleted_at else None,
                "deleted_by_email": aps.deleted_by_email,
                "deleted_by_role": aps.deleted_by_role,
                "features": aps.features if isinstance(aps.features, list) else [],
                "short_description": aps.short_description or "",
                "min_booking_notice_hours": aps.min_booking_notice_hours or 0,
                "updated_at": aps.updated_at.isoformat() if aps.updated_at else None,
            })
        return results

    @classmethod
    def purge_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        admin_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        from app.models.journey_models import AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        if mapping.deleted_at is None:
            raise HTTPException(
                status_code=400,
                detail="Airport service must be moved to the recycle bin before permanent deletion."
            )

        admin_email = admin_context.get("email") or admin_context.get("sub") or "admin@shafskyaviation.com"

        try:
            db.delete(mapping)
            db.commit()
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to permanently purge airport service %s: %s", mapping_id, err)
            raise HTTPException(
                status_code=409,
                detail="Cannot permanently delete service due to database integrity constraints."
            )

        try:
            from app.routers.journey_router import clear_services_catalog_cache
            clear_services_catalog_cache()
        except Exception:
            pass

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="PURGE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=mapping_id
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {
            "success": True,
            "id": mapping_id,
            "status": "purged",
            "message": "Airport service permanently deleted from database."
        }

    @classmethod
    def delete_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        admin_email: str
    ) -> Dict[str, Any]:
        """Backward-compatible alias for recycling an airport service."""
        return cls.recycle_airport_service(
            db=db,
            mapping_id=mapping_id,
            admin_context={"email": admin_email, "role": "SUPER_ADMIN"}
        )

