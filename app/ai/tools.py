"""
AI Tool Wrappers around Existing Platform Backend Services.
Strictly delegates actions to verified service layers without SQL or direct DB queries.
"""

import uuid
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.services.airport_service import AirportService
from app.services.search_service import SearchService
from app.services.assignment_service import AssignmentService
from app.services.notes_service import NotesService
from app.services.timeline_service import TimelineService
from app.schemas.airport import AirportBookingCreate, PassengerCreate


class AiTools:
    """Encapsulates backend service wrapper tools callable by the AI engine."""

    @classmethod
    def create_booking(cls, db: Session, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Creates an Airport Meet & Assist booking via AirportService."""
        passengers_raw = payload_dict.get("passengers", [])
        pax_list = []
        for p in passengers_raw:
            pax_list.append(PassengerCreate(
                full_name=p.get("full_name", "Guest"),
                passenger_type=p.get("passenger_type", "ADULT"),
                passport_number=p.get("passport_number")
            ))

        create_dto = AirportBookingCreate(
            contact_name=payload_dict.get("contact_name", "Valued Customer"),
            contact_email=payload_dict.get("contact_email", "customer@shafsky.com"),
            contact_phone=payload_dict.get("contact_phone", "+919876543210"),
            service_code=payload_dict.get("service_code", "MEET_GREET"),
            flight_number=payload_dict.get("flight_number", "EK-501"),
            airline=payload_dict.get("airline", "Emirates"),
            departure_airport=payload_dict.get("departure_airport", "BOM"),
            arrival_airport=payload_dict.get("arrival_airport", "DXB"),
            depart_date=payload_dict.get("depart_date", "2026-09-15T10:00:00Z"),
            passengers=pax_list
        )

        booking = AirportService.create_booking(db, create_dto)
        return {
            "booking_id": str(booking.id),
            "booking_reference": booking.booking_reference,
            "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            "service_code": booking.service_code,
            "created_at": booking.created_at.isoformat()
        }

    @classmethod
    def check_booking(cls, db: Session, booking_id_str: str) -> Dict[str, Any]:
        """Retrieves booking details via AirportService."""
        try:
            bk_uuid = uuid.UUID(booking_id_str)
        except Exception:
            return {"error": f"Invalid UUID format: '{booking_id_str}'"}

        try:
            details = AirportService.get_booking_details(db, bk_uuid)
            booking = details["booking"]
            return {
                "booking_id": str(booking.id),
                "booking_reference": booking.booking_reference,
                "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
                "contact_name": booking.contact_name,
                "contact_email": booking.contact_email,
                "flight_number": booking.flight_number,
                "airline": booking.airline,
                "workflow_state": details.get("workflow_state"),
                "assignments_count": len(details.get("assignments", []))
            }
        except ValueError as err:
            return {"error": str(err)}

    @classmethod
    def search_customer(cls, db: Session, query_str: str) -> Dict[str, Any]:
        """Searches customer records via SearchService."""
        results = SearchService.global_search(db, query=query_str, limit=5)
        return {"query": query_str, "results": results}

    @classmethod
    def assign_staff(cls, db: Session, booking_id_str: str, staff_user_id_str: str, role_type: str = "CONCIERGE") -> Dict[str, Any]:
        """Assigns staff duty officer via AssignmentService."""
        try:
            bk_uuid = uuid.UUID(booking_id_str)
            st_uuid = uuid.UUID(staff_user_id_str)
        except Exception:
            return {"error": "Invalid UUID format for booking or staff user."}

        try:
            assignment = AssignmentService.assign_staff(
                db,
                booking_id=bk_uuid,
                staff_user_id=st_uuid,
                role_type=role_type
            )
            return {
                "assignment_id": str(assignment.id),
                "booking_id": str(assignment.booking_id),
                "staff_user_id": str(assignment.staff_user_id),
                "role_type": assignment.role_type
            }
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def create_note(cls, db: Session, entity_type: str, entity_id_str: str, content_str: str) -> Dict[str, Any]:
        """Adds an internal note via NotesService."""
        note = NotesService.create_note(
            db,
            entity_type=entity_type,
            entity_id=entity_id_str,
            content=content_str,
            is_internal=True
        )
        return {
            "note_id": str(note.id),
            "entity_type": note.entity_type,
            "entity_id": note.entity_id,
            "created_at": note.created_at.isoformat()
        }

    @classmethod
    def update_booking(cls, db: Session, booking_id_str: str, status_str: str) -> Dict[str, Any]:
        """Updates booking status via AirportService."""
        try:
            bk_uuid = uuid.UUID(booking_id_str)
            updated = AirportService.update_booking_status(db, bk_uuid, status_str)
            return {"booking_id": str(updated.id), "new_status": updated.status.value if hasattr(updated.status, "value") else str(updated.status)}
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def cancel_booking(cls, db: Session, booking_id_str: str, reason: str = "Customer request") -> Dict[str, Any]:
        """Cancels booking via AirportService."""
        try:
            bk_uuid = uuid.UUID(booking_id_str)
            cancelled = AirportService.cancel_booking(db, bk_uuid, reason=reason)
            return {"booking_id": str(cancelled.id), "status": "CANCELLED", "reason": reason}
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def booking_status(cls, db: Session, booking_id_str: str) -> Dict[str, Any]:
        """Alias for check_booking via AirportService."""
        return cls.check_booking(db, booking_id_str)

    @classmethod
    def customer_history(cls, db: Session, customer_email: str) -> Dict[str, Any]:
        """Lists customer bookings via AirportService."""
        try:
            bookings = AirportService.list_my_bookings(db, customer_email)
            return {
                "customer_email": customer_email,
                "total_bookings": len(bookings),
                "bookings": [{"id": str(b.id), "ref": b.booking_reference, "status": b.status.value if hasattr(b.status, "value") else str(b.status)} for b in bookings]
            }
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def search_airport(cls, db: Session, query_str: str) -> Dict[str, Any]:
        """Searches airport records via SearchService."""
        return SearchService.global_search(db, query=query_str, limit=5)

    @classmethod
    def calculate_price(cls, service_code: str, pax_count: int = 1) -> Dict[str, Any]:
        """Calculates estimated price for services."""
        base_rates = {"MEET_GREET": 2500, "FAST_TRACK": 1800, "VIP_ASSIST": 5000, "LOUNGE": 3000}
        rate = base_rates.get(service_code.upper(), 2500)
        subtotal = rate * pax_count
        tax = round(subtotal * 0.18, 2)
        total = subtotal + tax
        return {"service_code": service_code, "pax_count": pax_count, "subtotal": subtotal, "tax_18_pct": tax, "total": total}

    @classmethod
    def start_workflow(cls, db: Session, entity_type: str, entity_id_str: str) -> Dict[str, Any]:
        """Triggers workflow instance start via TimelineService / WorkflowEngine."""
        entry = TimelineService.add_entry(db, entity_type=entity_type, entity_id=entity_id_str, event_type="WORKFLOW_STARTED", title="Workflow Auto-Initiated by AI")
        return {"timeline_id": str(entry.id), "status": "WORKFLOW_INITIATED"}

    @classmethod
    def timeline(cls, db: Session, entity_type: str, entity_id_str: str, title: str, event_type: str = "AI_EVENT") -> Dict[str, Any]:
        """Appends timeline event via TimelineService."""
        entry = TimelineService.add_entry(db, entity_type=entity_type, entity_id=entity_id_str, event_type=event_type, title=title)
        return {"timeline_id": str(entry.id), "title": entry.title}

    @classmethod
    def send_notification(cls, recipient: str, channel: str, message: str) -> Dict[str, Any]:
        """Dispatches multi-channel notification via CommunicationService."""
        from app.services.communication_service import CommunicationService
        if channel.upper() == "EMAIL":
            res = CommunicationService.send_email(to_email=recipient, subject="Shafsky Aviation Alert", body_text=message)
        elif channel.upper() == "WHATSAPP":
            res = CommunicationService.send_whatsapp(to_phone=recipient, message=message)
        else:
            res = CommunicationService.send_sms(to_phone=recipient, message=message)
        return {"recipient": recipient, "channel": channel, "dispatch": res}

    @classmethod
    def add_timeline(cls, db: Session, entity_type: str, entity_id_str: str, title: str, event_type: str = "AI_NOTE") -> Dict[str, Any]:
        """Adds a timeline event entry via TimelineService."""
        entry = TimelineService.add_entry(
            db,
            entity_type=entity_type,
            entity_id=entity_id_str,
            event_type=event_type,
            title=title
        )
        return {
            "timeline_id": str(entry.id),
            "title": entry.title,
            "event_type": entry.event_type
        }
