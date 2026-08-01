"""
AI Orchestration & Lifecycle Handoff Engine.
Manages conversation state machine, human staff handoffs, and tool execution.
"""

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
            AiTools.create_note(db, "AIRPORT_BOOKING", session.booking_id, f"Human Takeover by Officer '{req.staff_user_id}'. Notes: {req.notes or 'None'}")
            AiTools.timeline(db, "AIRPORT_BOOKING", session.booking_id, f"Staff Officer {req.staff_user_id} took over conversation.", event_type="HUMAN_TAKEOVER")

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
            AiTools.timeline(db, "AIRPORT_BOOKING", session.booking_id, f"AI Assistant resumed conversation. Reason: {req.reason or 'Staff released session'}", event_type="AI_RESUMED")

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
            AiTools.create_note(db, "AIRPORT_BOOKING", booking_id, f"Auto-flagged for human staff review: Assigned to {default_staff_id}")

        return default_staff_id

    @classmethod
    def process_chat(cls, db: Session, request: ChatRequest) -> ChatResponseData:
        """Processes chat message with state machine and handoff checks."""
        session_id = request.session_id
        user_msg = request.message.strip()
        msg_lower = user_msg.lower()

        session = ConversationMemory.get_session(session_id)
        if request.phone_number:
            session.phone_number = request.phone_number

        ConversationMemory.add_message(session_id, role="user", content=user_msg)

        # 1. Check if session is already handed off to human
        if session.current_state in [ConversationState.HANDOFF_TO_HUMAN, ConversationState.WAITING_FOR_STAFF]:
            reply = "Your conversation is currently assigned to a human staff concierge officer. A representative will respond shortly."
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
        email = request.metadata.get("email") if request.metadata else None
        context_data = AiContextBuilder.build_context(db, session_id, email=email)
        context_str = AiContextBuilder.format_context_string(context_data)
        knowledge_str = AiKnowledgeService.get_knowledge_summary()
        combined_system_prompt = f"{SYSTEM_PROMPT}\n\n[ENTERPRISE KNOWLEDGE]\n{knowledge_str}\n\n[LIVE CONVERSATION CONTEXT]\n{context_str}"

        executed_tools: List[str] = []
        reply_parts: List[str] = []

        # 4. State Machine & Intent Execution
        if "check booking" in msg_lower or "status of" in msg_lower or "booking id" in msg_lower:
            session.current_state = ConversationState.PROCESSING
            words = user_msg.split()
            candidate_id = next((w for w in words if len(w) > 8), None)
            if candidate_id:
                session.booking_id = candidate_id
                res = AiTools.check_booking(db, candidate_id)
                executed_tools.append("check_booking")
                if "error" not in res:
                    session.current_state = ConversationState.WAITING_FOR_CUSTOMER
                    session.failed_intent_attempts = 0
                    reply_parts.append(
                        f"Booking **{res['booking_reference']}** status: **{res['status']}**. "
                        f"Flight: {res['airline']} ({res['flight_number']}). "
                        f"Workflow State: {res.get('workflow_state', 'N/A')}."
                    )
                else:
                    reply_parts.append(f"Could not retrieve booking details: {res['error']}")
            else:
                session.current_state = ConversationState.COLLECTING_DETAILS
                reply_parts.append("Please provide a valid Booking ID or Reference to check status.")

        elif "cancel booking" in msg_lower or "cancel my request" in msg_lower:
            session.current_state = ConversationState.PROCESSING
            words = user_msg.split()
            candidate_id = next((w for w in words if len(w) > 8), None)
            if candidate_id:
                res = AiTools.cancel_booking(db, candidate_id)
                executed_tools.append("cancel_booking")
                session.current_state = ConversationState.COMPLETED
                reply_parts.append(f"Booking {candidate_id} cancellation request processed successfully.")
            else:
                session.current_state = ConversationState.COLLECTING_DETAILS
                reply_parts.append("Please specify the booking ID to cancel.")

        elif "price" in msg_lower or "cost" in msg_lower or "quote" in msg_lower:
            res = AiTools.calculate_price("MEET_GREET", pax_count=1)
            executed_tools.append("calculate_price")
            session.current_state = ConversationState.WAITING_FOR_CUSTOMER
            reply_parts.append(f"Estimated Meet & Assist price: INR {res['total']} (Subtotal: {res['subtotal']}, 18% Tax: {res['tax_18_pct']}).")

        elif "my bookings" in msg_lower or "booking history" in msg_lower:
            email = request.metadata.get("email") if request.metadata else "customer@shafsky.com"
            res = AiTools.customer_history(db, email)
            executed_tools.append("customer_history")
            session.current_state = ConversationState.WAITING_FOR_CUSTOMER
            reply_parts.append(f"Found {res.get('total_bookings', 0)} booking(s) registered for {email}.")

        elif "book" in msg_lower and ("meet" in msg_lower or "greet" in msg_lower or "flight" in msg_lower):
            session.current_state = ConversationState.COLLECTING_DETAILS
            reply_parts.append("I can assist you with your Airport Meet & Assist booking. Please specify passenger name, contact email, flight number, and date.")

        else:
            session.failed_intent_attempts += 1
            session.current_state = ConversationState.NEW if session.failed_intent_attempts == 1 else ConversationState.COLLECTING_DETAILS
            reply_parts.append("Welcome to Shafsky Aviation Services. How may I assist you with your flight escort, lounge, or concierge booking today?")

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
