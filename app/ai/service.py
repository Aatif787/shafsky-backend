"""
AI Orchestration & Lifecycle Handoff Engine.
Manages conversation state machine, human staff handoffs, and tool execution with strict authorization.
"""

import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.ai.memory import ConversationMemory
from app.ai.tools import AiTools
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.knowledge import AiKnowledgeService
from app.ai.context import AiContextBuilder
from app.ai.schemas import (
    ChatRequest,
    ChatResponseData,
    ConversationState,
    TakeoverRequest,
    ResumeRequest
)

_llm_provider = OpenAIProvider()

STAFF_ROLES = {
    "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
    "MEET_AND_ASSIST_STAFF", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT", "STAFF"
}


class AiService:
    """Core AI orchestration & lifecycle handoff engine."""

    @classmethod
    def get_provider_health(cls) -> Dict[str, Any]:
        """Returns active LLM provider health status."""
        return _llm_provider.health_check()

    @classmethod
    def take_over_conversation(cls, db: Session, req: TakeoverRequest) -> Dict[str, Any]:
        """Transfers active conversation from AI to human staff officer."""
        session = ConversationMemory.get_session(req.conversation_id)
        session.current_state = ConversationState.HANDOFF_TO_HUMAN
        session.assigned_staff = req.staff_user_id
        ConversationMemory.save_session(session)

        # Log timeline event & internal note
        if session.booking_id:
            AiTools.create_note(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id_str=session.booking_id,
                content_str=f"Human Takeover by Officer '{req.staff_user_id}'. Notes: {req.notes or 'None'}",
                author_id=req.staff_user_id
            )
            AiTools.timeline(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id_str=session.booking_id,
                title=f"Staff Officer {req.staff_user_id} took over conversation.",
                event_type="HUMAN_TAKEOVER",
                actor_id=req.staff_user_id
            )

        return {
            "conversation_id": req.conversation_id,
            "status": "HANDOFF_TO_HUMAN",
            "assigned_staff": req.staff_user_id
        }

    @classmethod
    def resume_ai_conversation(cls, db: Session, req: ResumeRequest) -> Dict[str, Any]:
        """Resumes AI conversation management from human staff officer."""
        session = ConversationMemory.get_session(req.conversation_id)
        session.current_state = ConversationState.PROCESSING
        session.assigned_staff = None
        ConversationMemory.save_session(session)

        if session.booking_id:
            AiTools.timeline(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id_str=session.booking_id,
                title=f"AI Assistant resumed conversation. Reason: {req.reason or 'Staff released session'}",
                event_type="AI_RESUMED",
                actor_id="AI_ENGINE"
            )

        return {
            "conversation_id": req.conversation_id,
            "status": "PROCESSING",
            "ai_active": True
        }

    @classmethod
    def assign_staff_if_available(cls, db: Session, conversation_id: str, booking_id: Optional[str] = None) -> str:
        """Assigns an available duty officer for human handoff."""
        default_staff_id = "staff_duty_officer_01"
        session = ConversationMemory.get_session(conversation_id)
        session.assigned_staff = default_staff_id
        session.current_state = ConversationState.WAITING_FOR_STAFF
        ConversationMemory.save_session(session)

        if booking_id:
            AiTools.create_note(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id_str=booking_id,
                content_str=f"Auto-flagged for human staff review: Assigned to {default_staff_id}",
                author_id="AI_ENGINE"
            )

        return default_staff_id

    @classmethod
    def process_chat(
        cls,
        db: Session,
        request: ChatRequest,
        current_user: Optional[Dict[str, Any]] = None
    ) -> ChatResponseData:
        """Processes chat message with state machine, handoff checks, and tool authorization."""
        session_id = request.session_id
        user_msg = request.message.strip()
        msg_lower = user_msg.lower()

        # Extract authenticated identity
        user_role = (current_user.get("role") or "GUEST").upper() if current_user else "GUEST"
        user_email = (current_user.get("email") or "").strip().lower() if current_user else None
        user_id = str(current_user.get("sub") or "") if current_user else None
        is_staff = user_role in STAFF_ROLES
        actor_id = user_email or user_id or "ANONYMOUS_USER"

        session = ConversationMemory.get_session(session_id)
        if request.phone_number:
            session.phone_number = request.phone_number

        ConversationMemory.add_message(session_id, role="user", content=user_msg)

        # 1. Check if session is already handed off to human
        if session.current_state in [ConversationState.HANDOFF_TO_HUMAN, ConversationState.WAITING_FOR_STAFF]:
            reply = "Your conversation is currently assigned to a human concierge officer. A representative will respond shortly."
            ConversationMemory.add_message(session_id, role="assistant", content=reply)
            return ChatResponseData(
                session_id=session_id,
                reply=reply,
                channel=request.channel,
                current_state=session.current_state,
                assigned_staff=session.assigned_staff,
                handoff_triggered=True,
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        # 2. Check automatic handoff triggers
        handoff_reasons = ["human", "agent", "representative", "complaint", "vip", "escalate", "operator", "speak to someone"]
        is_handoff_requested = any(r in msg_lower for r in handoff_reasons)

        if is_handoff_requested or session.failed_intent_attempts >= 3:
            assigned_staff = cls.assign_staff_if_available(db, session_id, session.booking_id)
            session.current_state = ConversationState.HANDOFF_TO_HUMAN
            ConversationMemory.save_session(session)

            reply = f"I am transferring your request to our 24/7 senior operations desk. Duty Officer '{assigned_staff}' has been notified and will assist you immediately."
            ConversationMemory.add_message(session_id, role="assistant", content=reply)

            return ChatResponseData(
                session_id=session_id,
                reply=reply,
                channel=request.channel,
                current_state=ConversationState.HANDOFF_TO_HUMAN,
                assigned_staff=assigned_staff,
                handoff_triggered=True,
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        # 3. Assemble dynamic context & knowledge base
        context_data = AiContextBuilder.build_context(db, session_id, email=user_email)
        context_str = AiContextBuilder.format_context_string(context_data)
        knowledge_str = AiKnowledgeService.get_knowledge_summary()
        combined_system_prompt = f"{SYSTEM_PROMPT}\n\n[ENTERPRISE KNOWLEDGE]\n{knowledge_str}\n\n[LIVE CONVERSATION CONTEXT]\n{context_str}"

        executed_tools: List[str] = []
        reply_parts: List[str] = []

        # 4. State Machine & Intent Execution with Authorization Boundaries
        if "check booking" in msg_lower or "status of" in msg_lower or "booking id" in msg_lower or "booking ref" in msg_lower:
            session.current_state = ConversationState.PROCESSING
            # Extract possible candidate reference / UUID
            tokens = re.findall(r'[A-Za-z0-9\-]{5,}', user_msg)
            candidate_id = next((t for t in tokens if len(t) >= 6 and any(c.isdigit() for c in t)), None)
            if candidate_id:
                session.booking_id = candidate_id
                res = AiTools.check_booking(
                    db,
                    booking_id_or_ref=candidate_id,
                    requester_email=user_email,
                    is_staff=is_staff
                )
                executed_tools.append("check_booking")
                if "error" in res:
                    reply_parts.append(res.get("message", "Unable to retrieve booking details."))
                else:
                    session.current_state = ConversationState.WAITING_FOR_CUSTOMER
                    session.failed_intent_attempts = 0
                    reply_parts.append(
                        f"Booking Reference: **{res['booking_reference']}**\n"
                        f"Status: **{res['status']}**\n"
                        f"Passenger: {res.get('passenger_name', 'Guest')}\n"
                        f"Flight: {res.get('airline', 'N/A')} {res.get('flight_number', '')}\n"
                        f"Route: {res.get('departure_airport', res.get('origin_code', 'N/A'))} → {res.get('arrival_airport', res.get('dest_code', 'N/A'))}"
                    )
            else:
                session.current_state = ConversationState.COLLECTING_DETAILS
                reply_parts.append("Please provide your Booking Reference (e.g. SHF-APT-20260731-A1B2 or SC-10001) to check your status.")

        elif "cancel booking" in msg_lower or "cancel my request" in msg_lower:
            session.current_state = ConversationState.PROCESSING
            tokens = re.findall(r'[A-Za-z0-9\-]{5,}', user_msg)
            candidate_id = next((t for t in tokens if len(t) >= 6 and any(c.isdigit() for c in t)), None)
            if candidate_id:
                res = AiTools.cancel_booking(
                    db,
                    booking_id_or_ref=candidate_id,
                    actor_id=actor_id,
                    requester_email=user_email,
                    is_staff=is_staff
                )
                executed_tools.append("cancel_booking")
                if "error" in res:
                    reply_parts.append(res.get("message", f"Cancellation could not be completed: {res['error']}"))
                else:
                    session.current_state = ConversationState.COMPLETED
                    reply_parts.append(f"Booking **{res.get('booking_reference', candidate_id)}** has been successfully cancelled.")
            else:
                session.current_state = ConversationState.COLLECTING_DETAILS
                reply_parts.append("Please specify the exact Booking Reference you would like to cancel.")

        elif "price" in msg_lower or "cost" in msg_lower or "quote" in msg_lower or "rates" in msg_lower:
            svc_type = "MEET_GREET"
            if "fast track" in msg_lower:
                svc_type = "FAST_TRACK"
            elif "vip" in msg_lower:
                svc_type = "VIP_ASSIST"
            elif "lounge" in msg_lower:
                svc_type = "LOUNGE"

            res = AiTools.calculate_price(svc_type, pax_count=1)
            executed_tools.append("calculate_price")
            session.current_state = ConversationState.WAITING_FOR_CUSTOMER
            reply_parts.append(
                f"Estimated {svc_type.replace('_', ' ')} Service Rate: **INR {res['total']:,.2f}** "
                f"(Base: INR {res['subtotal']:,.2f}, 18% GST: INR {res['tax_18_pct']:,.2f})."
            )

        elif "my bookings" in msg_lower or "booking history" in msg_lower:
            res = AiTools.customer_history(
                db,
                customer_email=user_email or "",
                requester_email=user_email,
                is_staff=is_staff
            )
            executed_tools.append("customer_history")
            if "error" in res:
                reply_parts.append(res.get("message", "Please log in to view your bookings."))
            else:
                total = res.get("total_bookings", 0)
                session.current_state = ConversationState.WAITING_FOR_CUSTOMER
                if total == 0:
                    reply_parts.append("No active or historical bookings found associated with your account.")
                else:
                    b_list = "\n".join(f"- **{b['reference']}** ({b['type']}): {b['status']}" for b in res["bookings"][:5])
                    reply_parts.append(f"You have {total} booking(s) on file:\n{b_list}")

        elif "airport" in msg_lower and ("search" in msg_lower or "find" in msg_lower or "code" in msg_lower):
            words = user_msg.split()
            query = words[-1] if words else "BOM"
            res = AiTools.search_airport(db, query)
            executed_tools.append("search_airport")
            session.current_state = ConversationState.WAITING_FOR_CUSTOMER
            reply_parts.append(f"Airport lookup results for '{query}': Found {len(res.get('results', []))} matches.")

        elif any(staff_cmd in msg_lower for staff_cmd in ["assign staff", "dispatch driver", "create note", "notify team"]):
            if not is_staff:
                reply_parts.append("This operational function is restricted to authorized Shafsky staff personnel.")
            else:
                reply_parts.append("Staff instruction recognized. Please perform administrative assignment via the Staff Command Center.")

        elif "book" in msg_lower and ("meet" in msg_lower or "greet" in msg_lower or "flight" in msg_lower or "assist" in msg_lower):
            session.current_state = ConversationState.COLLECTING_DETAILS
            reply_parts.append("I can assist you with your Airport Meet & Greet booking! Please specify your flight number, date, airport, and passenger count.")

        else:
            session.failed_intent_attempts += 1
            session.current_state = ConversationState.NEW if session.failed_intent_attempts == 1 else ConversationState.COLLECTING_DETAILS
            reply_parts.append("Welcome to Shafsky Aviation Services. How may I assist you with your flight escort, airport lounge, or concierge booking today?")

        ConversationMemory.save_session(session)
        reply_text = "\n".join(reply_parts)
        ConversationMemory.add_message(session_id, role="assistant", content=reply_text)

        return ChatResponseData(
            session_id=session_id,
            reply=reply_text,
            channel=request.channel,
            current_state=session.current_state,
            assigned_staff=session.assigned_staff,
            handoff_triggered=False,
            tool_calls_executed=executed_tools,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
