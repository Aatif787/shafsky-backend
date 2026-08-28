"""
Charter & Hotel/Transport Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Hotel & Ground Transport submenu
- Hotel booking parameters (City, Nights)
- Ground Transport parameters (Pickup, Dropoff)
- Private Jet & Helicopter Charter parameters (Origin, Destination)
"""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

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
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "charter_date_prompt", "success": True}
