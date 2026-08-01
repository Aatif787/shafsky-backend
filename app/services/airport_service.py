"""
Airport Meet & Assist Business Service — Phase C.1.

Integrates with Phase B Workflow Engine, Assignment Service,
Timeline Service, Attachment Service, and Flight Validation interface.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func

from app.models.airport import (
    AirportBooking,
    AirportPassenger,
    AirportFlightDetail,
    AirportServiceAddon,
)
from app.services.airport.flight_validator import default_flight_validator
from app.workflow.engine import WorkflowEngine
from app.services.assignment_service import AssignmentService
from app.services.timeline_service import TimelineService
from app.services.attachment_service import AttachmentService

logger = logging.getLogger("shafsky.services.airport")

# Configuration-Driven Service Pricing Catalog
AIRPORT_PRICING_CATALOG: Dict[str, float] = {
    "STANDARD_MEET_GREET": 150.00,
    "VIP_EXECUTIVE_ASSIST": 350.00,
    "VVIP_PRIVATE_CHARTER": 750.00,
    # Addon items
    "MEET_GREET": 150.00,
    "FAST_TRACK": 75.00,
    "BUGGY": 50.00,
    "LOUNGE": 100.00,
    "PORTER": 40.00,
    "VIP_ASSIST": 300.00,
}


class AirportService:

    @classmethod
    def generate_booking_reference(cls) -> str:
        """Generates a unique booking reference formatted like 'SHF-APT-20260731-A1B2'."""
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_tag = uuid.uuid4().hex[:4].upper()
        return f"SHF-APT-{date_str}-{rand_tag}"

    @classmethod
    def create_booking(
        cls,
        db: Session,
        customer_id: str,
        service_package: str,
        passengers_data: List[Dict[str, Any]],
        flight_detail_data: Dict[str, Any],
        addons_data: Optional[List[Dict[str, Any]]] = None,
        special_instructions: Optional[str] = None,
        actor_id: Optional[str] = None
    ) -> AirportBooking:
        """
        Creates an Airport Meet & Assist booking with passengers, flight info, addons,
        auto-initializes Workflow Instance, and records Timeline activity.
        """
        # 1. Validate Flight Information
        val_res = default_flight_validator.validate_flight_info(
            airline=flight_detail_data["airline"],
            flight_number=flight_detail_data["flight_number"],
            departure_airport=flight_detail_data["departure_airport"],
            arrival_airport=flight_detail_data["arrival_airport"],
            scheduled_time=flight_detail_data["scheduled_time"],
            terminal=flight_detail_data.get("terminal")
        )

        if not val_res.is_valid:
            raise ValueError(f"Flight detail validation failed: {val_res.error}")

        # 2. Calculate Pricing
        pkg_price = AIRPORT_PRICING_CATALOG.get(service_package.upper(), 150.00)
        total_price = pkg_price

        addon_objects_data = []
        for addon in (addons_data or []):
            code = addon["service_code"].upper()
            unit_p = AIRPORT_PRICING_CATALOG.get(code, 50.00)
            qty = addon.get("quantity", 1)
            tot_p = unit_p * qty
            total_price += tot_p
            addon_objects_data.append({
                "service_code": code,
                "quantity": qty,
                "unit_price": unit_p,
                "total_price": tot_p
            })

        # 3. Create Booking Record
        ref = cls.generate_booking_reference()
        booking = AirportBooking(
            booking_reference=ref,
            customer_id=customer_id,
            service_package=service_package.upper(),
            status="BOOKED",
            total_price=round(total_price, 2),
            currency="USD",
            special_instructions=special_instructions,
        )
        db.add(booking)
        db.flush()

        # 4. Create Passengers
        for idx, pax in enumerate(passengers_data):
            p_obj = AirportPassenger(
                booking_id=booking.id,
                full_name=pax["full_name"],
                gender=pax.get("gender"),
                dob=pax.get("dob"),
                nationality=pax.get("nationality"),
                passport_number=pax.get("passport_number"),
                contact_email=pax.get("contact_email"),
                contact_phone=pax.get("contact_phone"),
                is_primary=pax.get("is_primary", idx == 0)
            )
            db.add(p_obj)

        # 5. Create Flight Detail
        f_obj = AirportFlightDetail(
            booking_id=booking.id,
            airline=flight_detail_data["airline"],
            flight_number=flight_detail_data["flight_number"].strip().upper(),
            departure_airport=flight_detail_data["departure_airport"].strip().upper(),
            arrival_airport=flight_detail_data["arrival_airport"].strip().upper(),
            terminal=flight_detail_data.get("terminal"),
            scheduled_time=flight_detail_data["scheduled_time"],
            flight_type=flight_detail_data.get("flight_type", "ARRIVAL").upper()
        )
        db.add(f_obj)

        # 6. Create Service Addons
        for add in addon_objects_data:
            a_obj = AirportServiceAddon(
                booking_id=booking.id,
                service_code=add["service_code"],
                quantity=add["quantity"],
                unit_price=add["unit_price"],
                total_price=add["total_price"]
            )
            db.add(a_obj)

        db.flush()

        # 7. Initialize Workflow Instance (AIRPORT_MEET_AND_ASSIST)
        wf_instance = WorkflowEngine.create_instance(
            db,
            service_type="AIRPORT_MEET_AND_ASSIST",
            entity_id=str(booking.id),
            actor_id=actor_id or customer_id,
            initial_context={
                "booking_reference": ref,
                "customer_id": customer_id,
                "flight_number": f_obj.flight_number,
                "airport_code": f_obj.arrival_airport if f_obj.flight_type == "ARRIVAL" else f_obj.departure_airport,
                "passenger_count": len(passengers_data),
                "package": service_package
            }
        )

        booking.workflow_instance_id = wf_instance.id
        booking.status = wf_instance.current_state

        # 8. Record Activity in Timeline (Phase B.5 Integration)
        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking.id),
            event_type="BOOKING_CREATED",
            title=f"Airport Booking Created ({ref})",
            details={
                "booking_reference": ref,
                "service_package": service_package,
                "flight_number": f_obj.flight_number,
                "total_price": float(booking.total_price)
            },
            actor_id=actor_id or customer_id
        )

        db.commit()
        db.refresh(booking)
        logger.info(f"Created Airport Booking {booking.id} ({ref}) with workflow instance {wf_instance.id}")
        return booking

    @classmethod
    def get_booking(cls, db: Session, booking_id: uuid.UUID) -> AirportBooking:
        """Retrieves an AirportBooking by ID."""
        booking = db.query(AirportBooking).filter(AirportBooking.id == booking_id).first()
        if not booking:
            raise ValueError(f"Airport booking '{booking_id}' not found.")
        return booking

    @classmethod
    def get_booking_details(cls, db: Session, booking_id: uuid.UUID) -> Dict[str, Any]:
        """
        Aggregates booking, passengers, flight details, addons, active workflow state,
        staff assignments (AssignmentService), attachments (AttachmentService), and timeline.
        """
        booking = cls.get_booking(db, booking_id)

        # Retrieve active workflow instance state
        wf_state = None
        if booking.workflow_instance_id:
            try:
                history_data = WorkflowEngine.get_history(db, booking.workflow_instance_id)
                wf_state = history_data["instance"].current_state
            except Exception:
                wf_state = booking.status

        # Retrieve shared assignments
        assignments = AssignmentService.get_entity_assignments(db, "AIRPORT_BOOKING", str(booking_id))
        assignments_data = [
            {
                "id": str(a.id),
                "staff_id": str(a.staff_id),
                "role_type": a.role_type,
                "status": a.status,
                "assigned_by": a.assigned_by
            }
            for a in assignments
        ]

        # Retrieve shared attachments
        attachments = AttachmentService.get_attachments(db, "AIRPORT_BOOKING", str(booking_id))
        attachments_data = [
            {
                "id": str(att.id),
                "filename": att.filename,
                "category": att.category,
                "access_level": att.access_level,
                "storage_path": att.storage_path
            }
            for att in attachments
        ]

        return {
            "booking": booking,
            "workflow_state": wf_state or booking.status,
            "assignments": assignments_data,
            "attachments": attachments_data
        }

    @classmethod
    def list_bookings(
        cls,
        db: Session,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Paginated list of airport bookings with filters."""
        query = db.query(AirportBooking)
        if customer_id:
            query = query.filter(AirportBooking.customer_id == customer_id)
        if status:
            query = query.filter(func.upper(AirportBooking.status) == status.strip().upper())

        total = query.count()
        bookings = query.order_by(desc(AirportBooking.created_at)).offset(offset).limit(limit).all()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": bookings
        }

    @classmethod
    def update_booking(
        cls,
        db: Session,
        booking_id: uuid.UUID,
        service_package: Optional[str] = None,
        special_instructions: Optional[str] = None,
        actor_id: Optional[str] = None
    ) -> AirportBooking:
        """Updates booking attributes and records a timeline event."""
        booking = cls.get_booking(db, booking_id)

        if service_package:
            booking.service_package = service_package.upper()
            booking.total_price = AIRPORT_PRICING_CATALOG.get(service_package.upper(), float(booking.total_price))

        if special_instructions is not None:
            booking.special_instructions = special_instructions

        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking.id),
            event_type="BOOKING_UPDATED",
            title="Airport Booking Updated",
            details={"service_package": booking.service_package},
            actor_id=actor_id
        )

        db.commit()
        db.refresh(booking)
        logger.info(f"Updated Airport Booking {booking_id}")
        return booking

    @classmethod
    def execute_transition(
        cls,
        db: Session,
        booking_id: uuid.UUID,
        action: str,
        actor_id: str,
        actor_role: str = "CUSTOMER",
        payload: Optional[Dict[str, Any]] = None
    ) -> AirportBooking:
        """
        Executes a workflow state transition via WorkflowEngine and syncs AirportBooking.status.
        """
        booking = cls.get_booking(db, booking_id)

        if not booking.workflow_instance_id:
            raise ValueError(f"No active workflow instance associated with booking '{booking_id}'.")

        wf_instance = WorkflowEngine.execute_transition(
            db,
            instance_id=booking.workflow_instance_id,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=payload
        )

        booking.status = wf_instance.current_state

        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking.id),
            event_type=f"TRANSITION_{action.upper()}",
            title=f"Workflow Transition: {action.upper()}",
            details={"new_state": wf_instance.current_state, "action": action},
            actor_id=actor_id,
            actor_role=actor_role
        )

        db.commit()
        db.refresh(booking)
        logger.info(f"Executed transition '{action}' on Airport Booking {booking_id} -> new state: {booking.status}")
        return booking

    @classmethod
    def cancel_booking(
        cls,
        db: Session,
        booking_id: uuid.UUID,
        actor_id: str,
        reason: Optional[str] = None
    ) -> AirportBooking:
        """Cancels an airport booking using WorkflowEngine cancellation."""
        booking = cls.get_booking(db, booking_id)

        if booking.workflow_instance_id:
            try:
                WorkflowEngine.cancel_instance(db, booking.workflow_instance_id, actor_id=actor_id, reason=reason)
            except Exception as err:
                logger.warning(f"Workflow cancel error: {err}")

        booking.status = "CANCELLED"

        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking.id),
            event_type="BOOKING_CANCELLED",
            title="Airport Booking Cancelled",
            details={"reason": reason or "Cancelled by user/admin"},
            actor_id=actor_id
        )

        db.commit()
        db.refresh(booking)
        logger.info(f"Airport Booking {booking_id} cancelled by {actor_id}")
        return booking
