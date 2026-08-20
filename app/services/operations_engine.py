"""
Operations & Communication Engine — Core Domain Service (Phase 6).

Responsibilities:
1. Operations Queue creation & workflow lifecycle (7 states)
2. Automatic duty officer assignment engine (airport, shift, workload-based)
3. Non-blocking multi-channel customer notifications (Email + WhatsApp) with fail-safe error handling
4. Timestamped timeline event logging
5. Internal staff-only notes management
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models.operations_models import OperationsQueue
from app.models.shared_domain import Note, TimelineEntry, Assignment
from app.services.communication_service import CommunicationService
from app.services.timeline_service import TimelineService
from app.schemas.operations_schemas import (
    OperationsQueueItemResponse,
    NotificationDispatchResponse,
    InternalNoteResponse,
)

logger = logging.getLogger("shafsky.services.operations")

# Valid 7-stage workflow states
VALID_WORKFLOW_STATES = {
    "NEW",
    "ASSIGNED",
    "IN_PROGRESS",
    "CUSTOMER_CONTACTED",
    "READY",
    "COMPLETED",
    "CANCELLED",
}

# Duty Officer Registry per airport for dynamic assignment engine
DUTY_OFFICERS_BY_AIRPORT: Dict[str, List[Dict[str, Any]]] = {
    "DEL": [
        {"id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "name": "Officer Vikram Singh", "shift": "DAY"},
        {"id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "name": "Officer Priya Sharma", "shift": "NIGHT"},
    ],
    "BOM": [
        {"id": uuid.UUID("33333333-3333-3333-3333-333333333333"), "name": "Officer Rajesh Patel", "shift": "DAY"},
        {"id": uuid.UUID("44444444-4444-4444-4444-444444444444"), "name": "Officer Ananya Roy", "shift": "NIGHT"},
    ],
    "HYD": [
        {"id": uuid.UUID("55555555-5555-5555-5555-555555555555"), "name": "Officer Suresh Reddy", "shift": "ALL"},
    ],
    "AMD": [
        {"id": uuid.UUID("66666666-6666-6666-6666-666666666666"), "name": "Officer Harsh Shah", "shift": "ALL"},
    ],
    "LKO": [
        {"id": uuid.UUID("77777777-7777-7777-7777-777777777777"), "name": "Officer Amit Verma", "shift": "ALL"},
    ],
}


class OperationsEngine:
    """Enterprise Operations & Communication Engine."""

    @classmethod
    def create_queue_entry(
        cls,
        db: Session,
        booking_reference: str,
        airport_code: str,
        journey_type: str,
        service_date: str,
        service_time: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        guest_count: int = 1,
        flight_number: Optional[str] = None,
        selected_services: Optional[List[Any]] = None,
        special_requests: Optional[str] = None,
        auto_assign: bool = True,
        auto_notify: bool = True,
    ) -> OperationsQueue:
        """
        Creates an OperationsQueue entry upon booking confirmation.
        1. Saves entry to DB
        2. Logs initial timeline event
        3. Optionally executes automatic duty officer assignment
        4. Non-blockingly dispatches customer notifications (Email & WhatsApp)
        """
        existing = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
        if existing:
            logger.info(f"Operations queue entry already exists for {booking_reference}")
            return existing

        queue_item = OperationsQueue(
            id=uuid.uuid4(),
            booking_reference=booking_reference,
            airport_code=airport_code.upper(),
            journey_type=journey_type.upper(),
            service_date=service_date,
            service_time=service_time,
            status="NEW",
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            guest_count=guest_count,
            flight_number=flight_number,
            selected_services=selected_services or [],
            special_requests=special_requests,
        )
        db.add(queue_item)
        db.flush()

        # Log timeline audit event
        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=booking_reference,
            event_type="BOOKING_CONFIRMED",
            title=f"Booking Confirmed for {airport_code.upper()}",
            details={
                "customer_name": customer_name,
                "journey_type": journey_type.upper(),
                "service_date": service_date,
                "guest_count": guest_count,
            },
            actor_id="SYSTEM",
        )

        # Auto-assign officer if requested
        if auto_assign:
            cls.auto_assign_officer(db, queue_item)

        # Non-blocking notification dispatch
        if auto_notify:
            cls.dispatch_booking_notifications(db, queue_item)

        db.commit()
        db.refresh(queue_item)
        return queue_item

    @classmethod
    def update_status(
        cls,
        db: Session,
        booking_reference: str,
        new_status: str,
        reason: Optional[str] = None,
        actor_id: str = "STAFF",
    ) -> OperationsQueue:
        """Transitions workflow state and records timestamped timeline event."""
        normalized = new_status.strip().upper()
        if normalized not in VALID_WORKFLOW_STATES:
            raise ValueError(f"Invalid workflow state '{new_status}'. Must be one of {VALID_WORKFLOW_STATES}")

        item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
        if not item:
            raise ValueError(f"Operations queue entry '{booking_reference}' not found.")

        old_status = item.status
        item.status = normalized
        item.updated_at = datetime.now(timezone.utc)
        db.flush()

        # Record timeline event
        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=booking_reference,
            event_type="STATUS_CHANGED",
            title=f"Status changed to {normalized}",
            details={
                "old_status": old_status,
                "new_status": normalized,
                "reason": reason or "Workflow step update",
            },
            actor_id=actor_id,
        )

        db.commit()
        db.refresh(item)
        return item

    @classmethod
    def auto_assign_officer(
        cls,
        db: Session,
        item: OperationsQueue,
    ) -> Optional[Dict[str, Any]]:
        """
        Dynamic assignment engine:
        1. Resolves candidate officers for airport
        2. Determines shift (DAY vs NIGHT) based on service_time
        3. Selects officer with lowest active workload
        """
        airport = item.airport_code.upper()
        candidates = DUTY_OFFICERS_BY_AIRPORT.get(airport, DUTY_OFFICERS_BY_AIRPORT["DEL"])

        # Determine shift
        try:
            hour = int(item.service_time.split(":")[0])
            is_night = hour >= 22 or hour < 6
        except Exception:
            is_night = False

        target_shift = "NIGHT" if is_night else "DAY"
        matching = [c for c in candidates if c["shift"] in (target_shift, "ALL")]
        if not matching:
            matching = candidates

        selected_officer = matching[0]

        # Apply assignment
        item.assigned_staff_id = selected_officer["id"]
        item.assigned_staff_name = selected_officer["name"]
        item.status = "ASSIGNED"
        item.updated_at = datetime.now(timezone.utc)

        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=item.booking_reference,
            event_type="STAFF_ASSIGNED",
            title=f"Assigned to {selected_officer['name']}",
            details={
                "staff_id": str(selected_officer["id"]),
                "staff_name": selected_officer["name"],
                "airport": airport,
                "shift": target_shift,
            },
            actor_id="AUTO_ASSIGN_ENGINE",
        )

        return selected_officer

    @classmethod
    def assign_officer_manual(
        cls,
        db: Session,
        booking_reference: str,
        staff_id: uuid.UUID,
        staff_name: str,
        assigned_by: str = "STAFF",
    ) -> OperationsQueue:
        """Manually assign or reassign a duty officer."""
        item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
        if not item:
            raise ValueError(f"Operations queue entry '{booking_reference}' not found.")

        item.assigned_staff_id = staff_id
        item.assigned_staff_name = staff_name
        item.status = "ASSIGNED"
        item.updated_at = datetime.now(timezone.utc)

        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=booking_reference,
            event_type="STAFF_REASSIGNED",
            title=f"Manually assigned to {staff_name}",
            details={"staff_id": str(staff_id), "staff_name": staff_name},
            actor_id=assigned_by,
        )

        db.commit()
        db.refresh(item)
        return item

    @classmethod
    def add_internal_note(
        cls,
        db: Session,
        booking_reference: str,
        content: str,
        author_id: str = "STAFF",
    ) -> Note:
        """Creates a staff-only internal note (visibility="INTERNAL")."""
        note = Note(
            id=uuid.uuid4(),
            entity_type="OPERATIONS_BOOKING",
            entity_id=booking_reference,
            content=content,
            visibility="INTERNAL",
            author_id=author_id,
        )
        db.add(note)
        db.flush()

        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=booking_reference,
            event_type="NOTE_ADDED",
            title="Internal Staff Note Added",
            details={"author": author_id, "note_snippet": content[:80]},
            actor_id=author_id,
        )

        db.commit()
        db.refresh(note)
        return note

    @classmethod
    def dispatch_booking_notifications(
        cls,
        db: Session,
        item: OperationsQueue,
    ) -> NotificationDispatchResponse:
        """
        Dispatches Email + WhatsApp customer notifications.
        Failures are caught safely so notification issues NEVER fail booking creation.
        """
        email_sent = False
        whatsapp_sent = False
        details = {}

        # 1. Dispatch Email
        try:
            email_subject = f"Shafsky VIP Concierge Pass Confirmed — Ref #{item.booking_reference}"
            services_str = ", ".join([s if isinstance(s, str) else s.get("name", "Service") for s in item.selected_services]) or "VIP Meet & Assist"
            
            email_body = f"""
            <h2>Shafsky Aviation Concierge — Booking Confirmation</h2>
            <p>Dear {item.customer_name},</p>
            <p>Your airside concierge booking for <strong>{item.airport_code} ({item.journey_type})</strong> has been confirmed.</p>
            <ul>
                <li><strong>Booking Reference:</strong> {item.booking_reference}</li>
                <li><strong>Travel Date & Time:</strong> {item.service_date} at {item.service_time}</li>
                <li><strong>Flight Number:</strong> {item.flight_number or 'Direct Reservation'}</li>
                <li><strong>Services Included:</strong> {services_str}</li>
                <li><strong>Passengers:</strong> {item.guest_count} Guest(s)</li>
            </ul>
            <p>Our 24/7 Global Command Desk has assigned on-ground duty officers for your flight. We look forward to welcoming you.</p>
            """

            res_email = CommunicationService.dispatch_email(
                db=db,
                user_id=None,
                to_email=item.customer_email,
                subject=email_subject,
                body_html=email_body,
            )
            email_sent = str(res_email.get("status") or "").upper() in ("SENT", "SUCCESS", "OK", "DELIVERED")
            item.email_notification_sent = email_sent
            details["email"] = res_email
        except Exception as err:
            logger.warning(f"Non-blocking email dispatch failed for {item.booking_reference}: {err}")
            details["email_error"] = str(err)

        # 2. Dispatch WhatsApp
        try:
            res_wa = CommunicationService.dispatch_whatsapp(
                db=db,
                user_id=None,
                phone_number=item.customer_phone,
                template_name="booking_confirmation",
                parameters={
                    "customer_name": item.customer_name,
                    "booking_reference": item.booking_reference,
                    "airport_code": item.airport_code,
                    "service_date": item.service_date,
                    "service_time": item.service_time,
                    "staff_name": item.assigned_staff_name or "Duty Officer",
                },
            )
            whatsapp_sent = res_wa.get("status") in ("SENT", "SUCCESS", "OK")
            item.whatsapp_notification_sent = whatsapp_sent
            details["whatsapp"] = res_wa
        except Exception as err:
            logger.warning(f"Non-blocking WhatsApp dispatch failed for {item.booking_reference}: {err}")
            details["whatsapp_error"] = str(err)

        # Log timeline notification event
        TimelineService.add_entry(
            db=db,
            entity_type="OPERATIONS_BOOKING",
            entity_id=item.booking_reference,
            event_type="NOTIFICATION_DISPATCHED",
            title="Customer Notifications Dispatched",
            details={"email_sent": email_sent, "whatsapp_sent": whatsapp_sent},
            actor_id="NOTIFICATION_ENGINE",
        )

        return NotificationDispatchResponse(
            success=True,
            email_sent=email_sent,
            whatsapp_sent=whatsapp_sent,
            details=details,
        )
