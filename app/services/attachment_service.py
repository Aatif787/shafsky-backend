"""
Attachment Service — Phase B.5 Shared Domain Services.

Upload metadata registration, document references, file categorization,
and role-based access control.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.shared_domain import Attachment

logger = logging.getLogger("shafsky.services.attachment")

# Access level hierarchy (lower index = more restrictive)
ACCESS_HIERARCHY = ["ADMIN", "STAFF", "CUSTOMER", "PUBLIC"]


class AttachmentService:

    @classmethod
    def register(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        filename: str,
        storage_path: str,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        category: str = "GENERAL",
        uploaded_by: Optional[str] = None,
        access_level: str = "STAFF",
    ) -> Attachment:
        """Registers attachment metadata for an entity."""
        attachment = Attachment(
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            filename=filename,
            storage_path=storage_path,
            file_size=file_size,
            mime_type=mime_type,
            category=category.strip().upper(),
            uploaded_by=uploaded_by,
            access_level=access_level.strip().upper(),
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        logger.info(f"Attachment {attachment.id} registered: {filename} for {entity_type}:{entity_id}")
        return attachment

    @classmethod
    def get_attachments(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        category_filter: Optional[str] = None,
    ) -> List[Attachment]:
        """Returns attachments for an entity with optional category filter."""
        et = entity_type.strip().upper()
        query = db.query(Attachment).filter(
            Attachment.entity_type == et,
            Attachment.entity_id == entity_id,
            Attachment.is_deleted == False,
        )

        if category_filter:
            query = query.filter(Attachment.category == category_filter.strip().upper())

        return query.order_by(Attachment.created_at.desc()).all()

    @classmethod
    def delete(
        cls,
        db: Session,
        attachment_id: uuid.UUID,
        deleted_by: Optional[str] = None,
    ) -> Attachment:
        """Soft-deletes an attachment record."""
        attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if not attachment:
            raise ValueError(f"Attachment '{attachment_id}' not found.")

        attachment.is_deleted = True
        attachment.deleted_by = deleted_by
        attachment.deleted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(attachment)
        logger.info(f"Attachment {attachment_id} soft-deleted by {deleted_by}")
        return attachment

    @classmethod
    def check_access(
        cls,
        db: Session,
        attachment_id: uuid.UUID,
        user_role: str,
    ) -> Dict[str, Any]:
        """
        Validates whether the given user_role has access to the attachment.
        Returns dict with 'allowed' boolean and 'reason' string.
        """
        attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
        if not attachment:
            return {"allowed": False, "reason": "Attachment not found."}

        if attachment.is_deleted:
            return {"allowed": False, "reason": "Attachment has been deleted."}

        required_level = attachment.access_level.upper()
        role_clean = user_role.strip().upper()

        # PUBLIC access — everyone allowed
        if required_level == "PUBLIC":
            return {"allowed": True, "reason": "Public access."}

        # Map roles to access tiers
        role_tier_map = {
            "SUPER_ADMIN": 0, "ADMIN": 0,
            "OPERATIONS_MANAGER": 1, "DUTY_OFFICER": 1,
            "MEET_AND_ASSIST_STAFF": 1, "CONCIERGE_TEAM": 1,
            "CUSTOMER_SUPPORT": 1, "DISPATCHER": 1, "DRIVER": 1, "FINANCE": 1,
            "STAFF": 1,
            "CUSTOMER": 2,
        }
        level_tier_map = {"ADMIN": 0, "STAFF": 1, "CUSTOMER": 2, "PUBLIC": 3}

        user_tier = role_tier_map.get(role_clean, 3)
        required_tier = level_tier_map.get(required_level, 0)

        if user_tier <= required_tier:
            return {"allowed": True, "reason": f"Role '{role_clean}' has sufficient access."}

        return {
            "allowed": False,
            "reason": f"Role '{role_clean}' does not meet required access level '{required_level}'.",
        }
