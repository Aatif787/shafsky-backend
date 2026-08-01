"""
AI Conversation Lifecycle & Memory Management.
Stores conversation state and message history in Redis with in-memory fallback.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.core.redis import get_redis_client
from app.ai.schemas import ConversationState, ConversationSessionData

logger = logging.getLogger(__name__)

# Fallback in-memory store
_in_memory_sessions: Dict[str, Dict[str, Any]] = {}
MEMORY_EXPIRY_SECONDS = 86400  # 24 hours retention


class ConversationMemory:
    """Manages chat history and session lifecycle state."""

    @classmethod
    def get_session(cls, conversation_id: str) -> ConversationSessionData:
        """Retrieves active session state or initializes a new session."""
        redis = get_redis_client()
        key = f"ai:session_state:{conversation_id}"

        if redis:
            try:
                raw_data = redis.get(key)
                if raw_data:
                    data = json.loads(raw_data)
                    return ConversationSessionData(**data)
            except Exception as err:
                logger.warning(f"Redis session fetch error '{conversation_id}': {err}")

        # Fallback to in-memory store
        raw = _in_memory_sessions.get(conversation_id)
        if raw:
            return ConversationSessionData(**raw)

        # Initialize new default session
        new_session = ConversationSessionData(conversation_id=conversation_id)
        cls.save_session(new_session)
        return new_session

    @classmethod
    def save_session(cls, session: ConversationSessionData) -> None:
        """Persists session state in Redis or in-memory cache."""
        redis = get_redis_client()
        key = f"ai:session_state:{session.conversation_id}"
        dumped = session.model_dump(mode="json")

        if redis:
            try:
                redis.setex(key, MEMORY_EXPIRY_SECONDS, json.dumps(dumped))
                return
            except Exception as err:
                logger.warning(f"Redis session save error '{session.conversation_id}': {err}")

        _in_memory_sessions[session.conversation_id] = dumped

    @classmethod
    def get_history(cls, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent conversation messages."""
        session = cls.get_session(session_id)
        return session.last_messages[-limit:]

    @classmethod
    def add_message(cls, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Appends a message to session history and updates session state."""
        session = cls.get_session(session_id)
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        session.last_messages.append(msg)
        if len(session.last_messages) > 50:
            session.last_messages = session.last_messages[-50:]
        cls.save_session(session)
        return msg

    @classmethod
    def update_state(cls, conversation_id: str, new_state: ConversationState, assigned_staff: Optional[str] = None) -> ConversationSessionData:
        """Updates the conversation lifecycle state."""
        session = cls.get_session(conversation_id)
        session.current_state = new_state
        if assigned_staff:
            session.assigned_staff = assigned_staff
        cls.save_session(session)
        return session

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        """Clears conversation session memory."""
        redis = get_redis_client()
        key = f"ai:session_state:{session_id}"

        if redis:
            try:
                redis.delete(key)
            except Exception as err:
                logger.warning(f"Redis memory delete failed '{session_id}': {err}")

        _in_memory_sessions.pop(session_id, None)
