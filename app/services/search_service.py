"""
Search Service — Phase B.5 Shared Domain Services.

Global multi-entity search with ILIKE pattern matching, filters, pagination, and sorting.
Searches across timeline_entries, notes, attachments, and assignments.
"""

import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, or_
from app.models.shared_domain import TimelineEntry, Note, Attachment, Assignment

logger = logging.getLogger("shafsky.services.search")


class SearchService:

    @classmethod
    def search(
        cls,
        db: Session,
        query: str,
        entity_types: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        """
        Global multi-entity search across shared domain tables.
        Returns unified result set with source, entity_type, entity_id, match_field, snippet.
        """
        pattern = f"%{query}%"
        results = []
        filters = filters or {}
        et_filter = [e.strip().upper() for e in entity_types] if entity_types else None

        # 1. Search Timeline Entries
        timeline_results = cls._search_timeline(db, pattern, et_filter, filters)
        results.extend(timeline_results)

        # 2. Search Notes
        notes_results = cls._search_notes(db, pattern, et_filter, filters)
        results.extend(notes_results)

        # 3. Search Attachments
        attachment_results = cls._search_attachments(db, pattern, et_filter, filters)
        results.extend(attachment_results)

        # 4. Search Assignments
        assignment_results = cls._search_assignments(db, pattern, et_filter, filters)
        results.extend(assignment_results)

        # Sort results
        reverse = sort_order.lower() == "desc"
        results.sort(
            key=lambda r: r.get("created_at") or "",
            reverse=reverse,
        )

        total = len(results)
        paged = results[offset: offset + limit]

        return {
            "query": query,
            "total": total,
            "limit": limit,
            "offset": offset,
            "results": paged,
        }

    @classmethod
    def _search_timeline(
        cls, db: Session, pattern: str, et_filter: Optional[List[str]], filters: Dict
    ) -> List[Dict]:
        """Search timeline_entries by title and details."""
        query = db.query(TimelineEntry).filter(
            or_(
                TimelineEntry.title.ilike(pattern),
                TimelineEntry.event_type.ilike(pattern),
            )
        )
        if et_filter:
            query = query.filter(TimelineEntry.entity_type.in_(et_filter))
        if filters.get("actor_id"):
            query = query.filter(TimelineEntry.actor_id == filters["actor_id"])

        rows = query.limit(100).all()
        return [
            {
                "source": "timeline",
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "match_field": "title",
                "snippet": r.title[:200],
                "relevance_score": 1.0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    @classmethod
    def _search_notes(
        cls, db: Session, pattern: str, et_filter: Optional[List[str]], filters: Dict
    ) -> List[Dict]:
        """Search notes by content."""
        query = db.query(Note).filter(
            Note.content.ilike(pattern),
            Note.is_deleted == False,
        )
        if et_filter:
            query = query.filter(Note.entity_type.in_(et_filter))

        rows = query.limit(100).all()
        return [
            {
                "source": "notes",
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "match_field": "content",
                "snippet": r.content[:200],
                "relevance_score": 0.9,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    @classmethod
    def _search_attachments(
        cls, db: Session, pattern: str, et_filter: Optional[List[str]], filters: Dict
    ) -> List[Dict]:
        """Search attachments by filename."""
        query = db.query(Attachment).filter(
            Attachment.filename.ilike(pattern),
            Attachment.is_deleted == False,
        )
        if et_filter:
            query = query.filter(Attachment.entity_type.in_(et_filter))

        rows = query.limit(100).all()
        return [
            {
                "source": "attachments",
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "match_field": "filename",
                "snippet": r.filename[:200],
                "relevance_score": 0.8,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    @classmethod
    def _search_assignments(
        cls, db: Session, pattern: str, et_filter: Optional[List[str]], filters: Dict
    ) -> List[Dict]:
        """Search assignments by notes and role_type."""
        query = db.query(Assignment).filter(
            or_(
                Assignment.notes.ilike(pattern),
                Assignment.role_type.ilike(pattern),
            )
        )
        if et_filter:
            query = query.filter(Assignment.entity_type.in_(et_filter))

        rows = query.limit(100).all()
        return [
            {
                "source": "assignments",
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "match_field": "notes",
                "snippet": (r.notes or r.role_type)[:200],
                "relevance_score": 0.7,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
