"""
AI Tool Wrappers around Existing Platform Backend Services.
Strictly delegates actions to verified service layers with role-based access control and input validation.
"""

import uuid
import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.airport import AirportBooking, AirportPassenger, AirportFlightDetail
from app.models.schema import Booking, BookingStatus
from app.services.airport_service import AirportService
from app.services.search_service import SearchService
from app.services.assignment_service import AssignmentService
from app.services.notes_service import NotesService
from app.services.timeline_service import TimelineService

logger = logging.getLogger("shafsky.ai.tools")

PUBLIC_SAFE_TOOLS = {"calculate_price", "search_airport"}
CUSTOMER_TOOLS = {"check_booking", "customer_history"}
STAFF_TOOLS = {
    "create_booking", "assign_staff", "create_note",
    "update_booking", "cancel_booking", "start_workflow",
    "send_notification", "timeline", "add_timeline", "search_customer"
}


class AiTools:
    """Encapsulates backend service wrapper tools callable by the AI engine with permission enforcement."""

    @classmethod
    def check_booking(
        cls,
        db: Session,
        booking_id_or_ref: str,
        requester_email: Optional[str] = None,
        is_staff: bool = False
    ) -> Dict[str, Any]:
        """
        Retrieves booking details with strict ownership verification.
        Non-staff callers must be authenticated and own the booking.
        """
        clean_ref = booking_id_or_ref.strip()

        # 1. Attempt AirportBooking lookup (UUID or Reference)
        airport_booking = None
        try:
            bk_uuid = uuid.UUID(clean_ref)
            airport_booking = db.query(AirportBooking).filter(AirportBooking.id == bk_uuid).first()
        except Exception:
            airport_booking = db.query(AirportBooking).filter(
                func.upper(AirportBooking.booking_reference) == clean_ref.upper()
            ).first()

        if airport_booking:
            # Check ownership
            if not is_staff:
                if not requester_email:
                    return {
                        "error": "AUTH_REQUIRED",
                        "message": "To protect customer privacy, please sign in to your Shafsky account to view booking details."
                    }
                owner_emails = [
                    p.contact_email.strip().lower() for p in airport_booking.passengers if p.contact_email
                ]
                req_lower = requester_email.strip().lower()
                if req_lower != airport_booking.customer_id.strip().lower() and req_lower not in owner_emails:
                    return {
                        "error": "ACCESS_DENIED",
                        "message": "Access denied. You do not have permission to view this booking."
                    }

            primary_pax = next((p for p in airport_booking.passengers if p.is_primary), airport_booking.passengers[0] if airport_booking.passengers else None)
            flight = airport_booking.flight_details[0] if airport_booking.flight_details else None

            # Mask PII for non-staff
            pax_name = primary_pax.full_name if primary_pax else "Passenger"
            if not is_staff and len(pax_name) > 3:
                parts = pax_name.split()
                pax_name = parts[0] + " " + (parts[-1][0] + "***" if len(parts) > 1 else "***")

            return {
                "booking_id": str(airport_booking.id),
                "booking_reference": airport_booking.booking_reference,
                "status": airport_booking.status,
                "service_package": airport_booking.service_package,
                "passenger_name": pax_name,
                "flight_number": flight.flight_number if flight else "N/A",
                "airline": flight.airline if flight else "N/A",
                "departure_airport": flight.departure_airport if flight else "N/A",
                "arrival_airport": flight.arrival_airport if flight else "N/A",
            }

        # 2. Attempt Standard Flight Booking lookup
        standard_booking = None
        try:
            bk_uuid = uuid.UUID(clean_ref)
            standard_booking = db.query(Booking).filter(Booking.id == bk_uuid).first()
        except Exception:
            standard_booking = db.query(Booking).filter(
                func.upper(Booking.booking_ref) == clean_ref.upper()
            ).first()

        if standard_booking:
            if not is_staff:
                if not requester_email:
                    return {
                        "error": "AUTH_REQUIRED",
                        "message": "To protect customer privacy, please sign in to your Shafsky account to view booking details."
                    }
                if requester_email.strip().lower() != standard_booking.passenger_email.strip().lower():
                    return {
                        "error": "ACCESS_DENIED",
                        "message": "Access denied. You do not have permission to view this booking."
                    }

            pax_name = standard_booking.passenger_name
            if not is_staff and len(pax_name) > 3:
                parts = pax_name.split()
                pax_name = parts[0] + " " + (parts[-1][0] + "***" if len(parts) > 1 else "***")

            return {
                "booking_id": str(standard_booking.id),
                "booking_reference": standard_booking.booking_ref,
                "status": standard_booking.status.value if hasattr(standard_booking.status, "value") else str(standard_booking.status),
                "service_category": standard_booking.service_category,
                "passenger_name": pax_name,
                "flight_number": standard_booking.flight_num or "N/A",
                "origin_code": standard_booking.origin_code or "N/A",
                "dest_code": standard_booking.dest_code or "N/A",
            }

        return {"error": "NOT_FOUND", "message": f"No booking found matching reference '{clean_ref}'."}

    @classmethod
    def customer_history(
        cls,
        db: Session,
        customer_email: str,
        requester_email: Optional[str] = None,
        is_staff: bool = False
    ) -> Dict[str, Any]:
        """
        Lists customer bookings with forced identity binding for non-staff.
        """
        if not is_staff:
            if not requester_email:
                return {"error": "AUTH_REQUIRED", "message": "Please log in to view your booking history."}
            target_email = requester_email.strip().lower()
        else:
            target_email = (customer_email or requester_email or "").strip().lower()

        if not target_email:
            return {"error": "INVALID_EMAIL", "message": "A valid customer email is required."}

        # Query standard flight bookings
        flight_bookings = db.query(Booking).filter(
            func.lower(Booking.passenger_email) == target_email,
            Booking.deleted_at.is_(None)
        ).order_by(Booking.created_at.desc()).limit(10).all()

        # Query airport bookings
        airport_bookings = db.query(AirportBooking).join(
            AirportPassenger, AirportBooking.id == AirportPassenger.booking_id
        ).filter(
            func.lower(AirportPassenger.contact_email) == target_email
        ).order_by(AirportBooking.created_at.desc()).limit(10).all()

        history = []
        for b in flight_bookings:
            history.append({
                "type": "FLIGHT",
                "id": str(b.id),
                "reference": b.booking_ref,
                "status": b.status.value if hasattr(b.status, "value") else str(b.status),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            })
        for b in airport_bookings:
            history.append({
                "type": "AIRPORT_ASSIST",
                "id": str(b.id),
                "reference": b.booking_reference,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            })

        return {
            "customer_email": target_email,
            "total_bookings": len(history),
            "bookings": history
        }

    @classmethod
    def cancel_booking(
        cls,
        db: Session,
        booking_id_or_ref: str,
        actor_id: str,
        requester_email: Optional[str] = None,
        is_staff: bool = False,
        reason: str = "Customer requested via assistant"
    ) -> Dict[str, Any]:
        """
        Cancels booking with strict identity check.
        Customers can only cancel their own bookings; guests cannot cancel.
        """
        if not is_staff and not requester_email:
            return {"error": "AUTH_REQUIRED", "message": "Please sign in to your Shafsky account to cancel your booking."}

        clean_ref = booking_id_or_ref.strip()

        # Check AirportBooking
        airport_booking = None
        try:
            bk_uuid = uuid.UUID(clean_ref)
            airport_booking = db.query(AirportBooking).filter(AirportBooking.id == bk_uuid).first()
        except Exception:
            airport_booking = db.query(AirportBooking).filter(
                func.upper(AirportBooking.booking_reference) == clean_ref.upper()
            ).first()

        if airport_booking:
            if not is_staff:
                owner_emails = [
                    p.contact_email.strip().lower() for p in airport_booking.passengers if p.contact_email
                ]
                req_lower = (requester_email or "").strip().lower()
                if req_lower != airport_booking.customer_id.strip().lower() and req_lower not in owner_emails:
                    return {"error": "ACCESS_DENIED", "message": "You can only cancel your own bookings."}

            try:
                cancelled = AirportService.cancel_booking(db, airport_booking.id, actor_id=actor_id, reason=reason)
                return {
                    "booking_id": str(cancelled.id),
                    "booking_reference": cancelled.booking_reference,
                    "status": "CANCELLED",
                    "reason": reason
                }
            except Exception as err:
                return {"error": str(err)}

        # Check Standard Booking
        standard_booking = None
        try:
            bk_uuid = uuid.UUID(clean_ref)
            standard_booking = db.query(Booking).filter(Booking.id == bk_uuid).first()
        except Exception:
            standard_booking = db.query(Booking).filter(
                func.upper(Booking.booking_ref) == clean_ref.upper()
            ).first()

        if standard_booking:
            if not is_staff:
                req_lower = (requester_email or "").strip().lower()
                if req_lower != standard_booking.passenger_email.strip().lower():
                    return {"error": "ACCESS_DENIED", "message": "You can only cancel your own bookings."}

            standard_booking.status = BookingStatus.CANCELLED
            standard_booking.notes = f"{standard_booking.notes or ''}\nCancelled: {reason} by {actor_id}".strip()
            db.commit()
            return {
                "booking_id": str(standard_booking.id),
                "booking_reference": standard_booking.booking_ref,
                "status": "CANCELLED",
                "reason": reason
            }

        return {"error": "NOT_FOUND", "message": f"No booking found matching '{clean_ref}'."}

    @classmethod
    def calculate_price(cls, service_code: str, pax_count: int = 1) -> Dict[str, Any]:
        """Calculates estimated price for services (Public safe tool)."""
        base_rates = {"MEET_GREET": 2500, "FAST_TRACK": 1800, "VIP_ASSIST": 5000, "LOUNGE": 3000}
        rate = base_rates.get(service_code.upper(), 2500)
        pax = max(1, min(pax_count, 20))
        subtotal = rate * pax
        tax = round(subtotal * 0.18, 2)
        total = round(subtotal + tax, 2)
        return {
            "service_code": service_code.upper(),
            "pax_count": pax,
            "unit_price": rate,
            "subtotal": subtotal,
            "tax_18_pct": tax,
            "total": total,
            "currency": "INR"
        }

    @classmethod
    def search_airport(cls, db: Session, query_str: str) -> Dict[str, Any]:
        """Searches airport records via SearchService (Public safe tool)."""
        return SearchService.search(db, query=query_str, limit=5)

    @classmethod
    def assign_staff(
        cls,
        db: Session,
        entity_type: str,
        entity_id_str: str,
        staff_user_id_str: str,
        role_type: str = "GENERAL",
        assigned_by: str = "AI_AGENT"
    ) -> Dict[str, Any]:
        """Assigns staff duty officer via AssignmentService (Staff only)."""
        try:
            st_uuid = uuid.UUID(staff_user_id_str)
        except Exception:
            return {"error": f"Invalid UUID format for staff user: {staff_user_id_str}"}

        try:
            assignment = AssignmentService.assign(
                db,
                entity_type=entity_type,
                entity_id=entity_id_str,
                staff_id=st_uuid,
                assigned_by=assigned_by,
                role_type=role_type
            )
            return {
                "assignment_id": str(assignment.id),
                "entity_type": assignment.entity_type,
                "entity_id": assignment.entity_id,
                "staff_id": str(assignment.staff_id),
                "role_type": assignment.role_type
            }
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def create_note(
        cls,
        db: Session,
        entity_type: str,
        entity_id_str: str,
        content_str: str,
        author_id: str = "AI_AGENT",
        visibility: str = "INTERNAL"
    ) -> Dict[str, Any]:
        """Adds an internal note via NotesService (Staff only)."""
        try:
            note = NotesService.create(
                db,
                entity_type=entity_type,
                entity_id=entity_id_str,
                content=content_str,
                visibility=visibility,
                author_id=author_id
            )
            return {
                "note_id": str(note.id),
                "entity_type": note.entity_type,
                "entity_id": note.entity_id,
                "created_at": note.created_at.isoformat()
            }
        except Exception as err:
            return {"error": str(err)}

    @classmethod
    def search_customer(cls, db: Session, query_str: str) -> Dict[str, Any]:
        """Searches customer records via SearchService (Staff only)."""
        results = SearchService.search(db, query=query_str, limit=5)
        return {"query": query_str, "results": results}

    @classmethod
    def timeline(
        cls,
        db: Session,
        entity_type: str,
        entity_id_str: str,
        title: str,
        event_type: str = "AI_EVENT",
        actor_id: str = "AI_AGENT"
    ) -> Dict[str, Any]:
        """Appends timeline event via TimelineService."""
        entry = TimelineService.add_entry(
            db,
            entity_type=entity_type,
            entity_id=entity_id_str,
            event_type=event_type,
            title=title,
            actor_id=actor_id
        )
        return {"timeline_id": str(entry.id), "title": entry.title}

    @classmethod
    def send_notification(cls, db: Session, recipient: str, channel: str, message: str) -> Dict[str, Any]:
        """Dispatches multi-channel notification via CommunicationService (Staff only)."""
        from app.services.communication_service import CommunicationService
        if channel.upper() == "EMAIL":
            res = CommunicationService.dispatch_email(db, user_id=None, to_email=recipient, subject="Shafsky Aviation Alert", body_html=f"<p>{message}</p>")
        elif channel.upper() == "WHATSAPP":
            res = CommunicationService.dispatch_whatsapp(db, user_id=None, phone_number=recipient, template_name="general_alert", parameters={"message": message})
        else:
            res = CommunicationService.dispatch_sms(db, user_id=None, phone_number=recipient, message=message)
        return {"recipient": recipient, "channel": channel, "dispatch": res}
