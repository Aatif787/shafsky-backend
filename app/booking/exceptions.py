"""
Booking Module Custom Exceptions.
"""

from fastapi import HTTPException

class ConcurrencyException(HTTPException):
    """
    Raised when an optimistic locking version collision is detected on booking update.
    Returns HTTP 409 Conflict.
    """
    def __init__(self, detail: str = "Concurrency conflict: The booking was updated by another transaction. Please reload and retry."):
        super().__init__(status_code=409, detail=detail)
