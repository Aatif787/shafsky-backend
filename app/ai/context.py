"""
Dynamic Conversation Context Builder.
Assembles live customer, booking, workflow, and timeline data from backend services.
"""

import uuid
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.ai.memory import ConversationMemory
from app.ai.tools import AiTools
from app.ai.schemas import ConversationSessionData


class AiContextBuilder:
    """Builds rich real-time operational context for AI prompts."""

    @classmethod
    def build_context(cls, db: Session, session_id: str, email: Optional[str] = None) -> Dict[str, Any]:
        """Fetches live backend state and assembles structured context."""
        session: ConversationSessionData = ConversationMemory.get_session(session_id)

        context: Dict[str, Any] = {
            "conversation_id": session.conversation_id,
            "current_state": session.current_state.value,
            "assigned_staff": session.assigned_staff,
            "booking_details": None,
            "customer_history_count": 0,
            "timeline_events_count": 0
        }

        # 1. Fetch live booking details if booking_id is linked
        if session.booking_id:
            bk_res = AiTools.check_booking(db, session.booking_id)
            if "error" not in bk_res:
                context["booking_details"] = bk_res

        # 2. Fetch live customer booking history count if email is provided
        target_email = email or "customer@shafsky.com"
        hist_res = AiTools.customer_history(db, target_email)
        if "error" not in hist_res:
            context["customer_history_count"] = hist_res.get("total_bookings", 0)

        return context

    @classmethod
    def format_context_string(cls, context: Dict[str, Any]) -> str:
        """Formats context dictionary into prompt-friendly markdown block."""
        parts = [
            f"- Conversation ID: {context['conversation_id']}",
            f"- Conversation State: {context['current_state']}",
            f"- Assigned Staff: {context['assigned_staff'] or 'AI Assistant'}",
            f"- Customer Total Bookings: {context['customer_history_count']}"
        ]

        bk = context.get("booking_details")
        if bk:
            parts.append(
                f"- Active Booking: {bk.get('booking_reference')} | Status: {bk.get('status')} | "
                f"Flight: {bk.get('airline')} {bk.get('flight_number')} | Workflow State: {bk.get('workflow_state')}"
            )

        return "\n".join(parts)
