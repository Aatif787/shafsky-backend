"""
Notes Service — Phase B.5 Shared Domain Services.

Internal and customer-visible notes with mentions and immutable edit history.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from app.models.shared_domain import Note, NoteRevision

logger = logging.getLogger("shafsky.services.notes")


class NotesService:

    @classmethod
    def create(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        content: str,
        visibility: str = "INTERNAL",
        author_id: Optional[str] = None,
        mentions: Optional[List[str]] = None,
    ) -> Note:
        """Creates a new note and records the initial revision."""
        note = Note(
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            content=content,
            visibility=visibility.strip().upper(),
            author_id=author_id,
            mentions=mentions or [],
        )
        db.add(note)
        db.flush()

        # Record initial revision
        revision = NoteRevision(
            note_id=note.id,
            content_snapshot=content,
            edited_by=author_id,
            revision_number=1,
        )
        db.add(revision)

        db.commit()
        db.refresh(note)
        logger.info(f"Note {note.id} created for {entity_type}:{entity_id} (visibility={visibility})")
        return note

    @classmethod
    def update(
        cls,
        db: Session,
        note_id: uuid.UUID,
        content: str,
        editor_id: Optional[str] = None,
        mentions: Optional[List[str]] = None,
    ) -> Note:
        """Updates note content and creates a new revision snapshot."""
        note = db.query(Note).filter(Note.id == note_id, Note.is_deleted == False).first()
        if not note:
            raise ValueError(f"Note '{note_id}' not found or has been deleted.")

        # Determine next revision number
        max_rev = (
            db.query(NoteRevision)
            .filter(NoteRevision.note_id == note_id)
            .count()
        )

        note.content = content
        if mentions is not None:
            note.mentions = mentions

        # Create immutable revision
        revision = NoteRevision(
            note_id=note.id,
            content_snapshot=content,
            edited_by=editor_id,
            revision_number=max_rev + 1,
        )
        db.add(revision)

        db.commit()
        db.refresh(note)
        logger.info(f"Note {note_id} updated to revision {max_rev + 1}")
        return note

    @classmethod
    def delete(
        cls,
        db: Session,
        note_id: uuid.UUID,
        deleted_by: Optional[str] = None,
    ) -> Note:
        """Soft-deletes a note."""
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise ValueError(f"Note '{note_id}' not found.")

        note.is_deleted = True
        note.deleted_by = deleted_by
        note.deleted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(note)
        logger.info(f"Note {note_id} soft-deleted by {deleted_by}")
        return note

    @classmethod
    def get_notes(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        visibility_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Returns paginated notes for an entity with optional visibility filtering."""
        et = entity_type.strip().upper()
        query = db.query(Note).filter(
            Note.entity_type == et,
            Note.entity_id == entity_id,
            Note.is_deleted == False,
        )

        if visibility_filter:
            query = query.filter(Note.visibility == visibility_filter.strip().upper())

        total = query.count()
        notes = query.order_by(desc(Note.created_at)).offset(offset).limit(limit).all()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": notes,
        }

    @classmethod
    def get_revisions(cls, db: Session, note_id: uuid.UUID) -> List[NoteRevision]:
        """Returns immutable edit history for a note."""
        return (
            db.query(NoteRevision)
            .filter(NoteRevision.note_id == note_id)
            .order_by(asc(NoteRevision.revision_number))
            .all()
        )
