"""
Charter & Hotel/Transport Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Hotel & Ground Transport submenu
- Hotel booking parameters (City, Nights)
- Ground Transport parameters (Pickup, Dropoff)
- Private Jet & Helicopter Charter parameters (Origin, Destination)
"""

import logging
import os
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime

from app.models.whatsapp_models import WhatsAppConversation
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import delivery as wa_delivery

logger = logging.getLogger(__name__)


class CharterHotelFlowMixin:
    """Mixin for Hotel, Ground Transport, and Private Charter conversation states."""

    @classmethod
    def _prompt_hotel_transport_submenu(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Submenu for Hotel & Transportation."""
        body_text = (
            "🏨🚗 *Hotel & Transportation Services*\n\n"
            "Please choose a service:"
        )
        buttons = [
            {"id": "btn_sub_hotel", "title": "Hotel Booking"},
            {"id": "btn_sub_transport", "title": "Transportation"}
        ]
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Hotel & Transport",
            fallback_text=body_text + "\n\nReply *1* for Hotel Booking or *2* for Transportation.",
            client=whatsapp_client,
        )
        return {"status": "hotel_transport_submenu_sent", "success": True}

    @classmethod
    def _state_hotel_transport_submenu(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles submenu choice between Hotel and Transportation."""
        norm = (input_id or user_text).strip().upper()
        if "HOTEL" in norm or norm == "1" or norm == "btn_sub_hotel":
            conv.selected_service_name = "Luxury Hotel Booking"
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "HOTEL_CITY")
            whatsapp_client.send_text_message(conv.phone_number, "🏨 *Hotel Booking*\n\nPlease enter your destination city or preferred hotel:")
            return {"status": "hotel_city_prompt_sent"}

        elif "TRANSPORT" in norm or norm == "2" or norm == "btn_sub_transport":
            conv.selected_service_name = "Premium Ground Transport"
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "TRANSPORT_PICKUP")
            whatsapp_client.send_text_message(conv.phone_number, "🚗 *Transportation*\n\nPlease enter your pickup location:")
            return {"status": "transport_pickup_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *1. Hotel Booking* or *2. Transportation*.")
        return {"status": "invalid_submenu_choice", "success": False}

    @classmethod
    def _state_hotel_city(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        city = user_text.strip()
        conv.selected_airport_city = city
        cls._transition_state(db, conv, "HOTEL_NIGHTS")
        whatsapp_client.send_text_message(conv.phone_number, f"City: *{city}*\n\nHow many nights will you be staying? (e.g. 2):")
        return {"status": "hotel_nights_prompt", "success": True}

    @classmethod
    def _state_hotel_nights(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        digits = "".join(filter(str.isdigit, user_text)) or "1"
        conv.additional_requirements = f"Hotel in {conv.selected_airport_city}, {digits} nights"
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Check-in Date in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "hotel_date_prompt", "success": True}

    @classmethod
    def _state_transport_pickup(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        pickup = user_text.strip()
        conv.selected_airport_city = pickup
        cls._transition_state(db, conv, "TRANSPORT_DROPOFF")
        whatsapp_client.send_text_message(conv.phone_number, f"Pickup: *{pickup}*\n\nPlease enter your drop-off destination:")
        return {"status": "transport_dropoff_prompt", "success": True}

    @classmethod
    def _state_transport_dropoff(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        dropoff = user_text.strip()
        conv.additional_requirements = f"Route: {conv.selected_airport_city} to {dropoff}"
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "transport_date_prompt", "success": True}

    @classmethod
    def _state_charter_origin(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        origin = user_text.strip()
        conv.selected_airport_city = origin
        cls._transition_state(db, conv, "CHARTER_DESTINATION")
        whatsapp_client.send_text_message(conv.phone_number, f"Departure: *{origin}*\n\nPlease enter your destination city / airport:")
        return {"status": "charter_destination_prompt", "success": True}

    @classmethod
    def _state_charter_destination(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        destination = user_text.strip()
        conv.additional_requirements = f"Private Charter: {conv.selected_airport_city} to {destination}"
        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        meta["charter_origin"] = conv.selected_airport_city or ""
        meta["charter_destination"] = destination
        conv.flight_details_json = meta
        flag_modified(conv, "flight_details_json")
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "charter_date_prompt", "success": True}

    @classmethod
    def _submit_charter_enquiry(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Private Charter is enquiry-only: register the request in the dedicated
        charter pipeline (PrivateCharterRequest, status REQUESTED) - no Booking row,
        no payment link, no fabricated pricing."""
        from app.integrations.whatsapp import copy as wa_copy
        from app.schemas.charter import CharterLegSchema, PassengerCountSchema, PrivateCharterRequestCreate
        from app.services.charter_service import CharterService

        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        origin = (metadata.get("charter_origin") or conv.selected_airport_city or "").strip() or "Not specified"
        destination = (metadata.get("charter_destination") or "").strip() or "Not specified"

        departure_date_iso = None
        if conv.booking_date:
            for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    departure_date_iso = datetime.strptime(conv.booking_date.strip(), fmt).date().isoformat()
                    break
                except ValueError:
                    continue
        if not departure_date_iso:
            cls._transition_state(db, conv, "DATE_SELECTION")
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please re-enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):",
            )
            return {"status": "charter_enquiry_date_retry", "success": False}

        digits = "".join(filter(str.isdigit, conv.customer_phone or conv.phone_number or ""))[-12:]

        try:
            payload = PrivateCharterRequestCreate(
                customer_name=(conv.customer_name or "Guest").strip(),
                phone=digits,
                email=conv.customer_email,
                preferred_contact_method="PHONE_WHATSAPP",
                trip_type="ONE_WAY",
                origin=origin,
                destination=destination,
                departure_date=departure_date_iso,
                itinerary=[CharterLegSchema(origin=origin, destination=destination, departure_date=departure_date_iso)],
                passengers=PassengerCountSchema(adults=max(1, conv.passenger_count or 1)),
                aircraft_preference="NO_PREFERENCE",
                special_requests=(conv.additional_requirements or "").strip()[:2000] or None,
            )
            req = CharterService.create_charter_request(db, payload)
        except Exception as submit_err:
            logger.error(f"[WhatsApp Charter] Enquiry submission failed: {submit_err}")
            whatsapp_client.send_text_message(
                conv.phone_number,
                f"*Shafsky Aviation Services*\n\n"
                f"We could not register your charter enquiry just now. Please call our "
                f"executive on *+91-9599087959* - your details are safe with us.",
            )
            return {"status": "charter_enquiry_failed", "success": False, "error": str(submit_err)}

        conv.booking_ref = req.request_reference
        conv.payment_status = "QUOTE_REQUESTED"
        cls._transition_state(db, conv, "AWAITING_QUOTE")
        db.commit()

        whatsapp_client.send_text_message(
            conv.phone_number,
            wa_copy.CHARTER_ENQUIRY_REGISTERED.format(request_reference=req.request_reference),
        )
        cls._notify_charter_desk(conv, req.request_reference)
        return {
            "status": "charter_enquiry_registered",
            "request_reference": req.request_reference,
            "success": True,
        }

    @classmethod
    def _notify_charter_desk(cls, conv: WhatsAppConversation, request_reference: str) -> None:
        """Stage 3: land the enquiry on the charter desk with full details."""
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        team_msg = (
            "🚩 *NEW CHARTER ENQUIRY (WhatsApp)*\n\n"
            f" *Reference*: {request_reference}\n"
            f" *Customer*: {conv.customer_name} ({conv.customer_phone})\n"
            f" *Email*: {conv.customer_email}\n"
            f" *Service*: {conv.selected_service_name}\n"
            f" *Route*: {metadata.get('charter_origin') or 'N/A'} to {metadata.get('charter_destination') or 'N/A'}\n"
            f" *Date*: {conv.booking_date}\n"
            f" *Passengers*: {conv.passenger_count}\n"
            f" *Notes*: {conv.additional_requirements or 'N/A'}\n"
            f" *Status*: REQUESTED - contact the customer within 30 minutes"
        )
        try:
            whatsapp_client.send_text_message(officer_phone, team_msg)
        except Exception as err:
            logger.warning("[WhatsApp Charter] Charter desk notify failed: %s", type(err).__name__)
