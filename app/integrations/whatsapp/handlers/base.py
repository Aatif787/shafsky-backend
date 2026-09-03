"""
Base Flow Mixin providing shared classmethod type stubs for WhatsApp flow mixins.
Ensures static type checkers recognize cross-mixin methods inherited by WhatsAppBookingStateMachine.
"""

from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.whatsapp_models import WhatsAppConversation


class BaseFlowMixin:
    """Base mixin providing type declarations for cross-mixin and state-machine methods."""

    @classmethod
    def _transition_state(cls, db: Session, conv: WhatsAppConversation, new_state: str) -> None:
        pass

    @classmethod
    def _reset_conversation_fields(cls, conv: WhatsAppConversation, db: Optional[Session] = None) -> WhatsAppConversation:
        return conv

    @classmethod
    def _send_airport_services_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        return {}

    @classmethod
    def _send_service_menu(cls, db: Session, conv: WhatsAppConversation, category_name: str) -> Dict[str, Any]:
        return {}

    @classmethod
    def _get_authoritative_airport_packages(
        cls, db: Session, airport_id: Any, journey_type: str, flight_types: List[str], terminal: Optional[str] = None
    ) -> List[Any]:
        return []

    @classmethod
    def _store_wa_menu(cls, db: Session, conv: WhatsAppConversation, menu_items: List[Dict[str, Any]]) -> None:
        pass

    @classmethod
    def _get_wa_menu(cls, conv: WhatsAppConversation) -> List[Dict[str, Any]]:
        return []

    @classmethod
    def _set_wa_state_key(cls, db: Session, conv: WhatsAppConversation, key: str, value: Any) -> None:
        pass

    @classmethod
    def _commit_accepted_flight(
        cls, db: Session, conv: WhatsAppConversation, pending: Dict[str, Any], *, verification_status: str, extra_meta: Optional[Dict[str, Any]] = None
    ) -> None:
        pass

    @classmethod
    def _prompt_terminal_selection(cls, conv: WhatsAppConversation, airport: Any, terminals: List[str]) -> Dict[str, Any]:
        return {}

    @classmethod
    def _get_applicable_terminals(
        cls, db: Session, airport: Any, journey_type: str, travel_type: str
    ) -> List[str]:
        return []

    @classmethod
    def _align_scheduled_datetimes_to_date(cls, metadata: Dict[str, Any], travel_date_str: Any, tz_name: Any = None) -> Dict[str, Any]:
        return metadata

    @classmethod
    def _prompt_hotel_transport_submenu(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        return {}

    @classmethod
    def _prompt_journey_type(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        return {}
